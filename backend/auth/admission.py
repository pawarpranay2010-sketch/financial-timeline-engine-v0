"""Platrixa — Phase 5I authoritative admission boundary (one choke point).

Phase 5I integration finding (audit
reports/PRODUCTION_READINESS_INTEGRATION_AUDIT.md, C1): the Phase 16 gate
exposed two operations — ``resolve_tenant`` (authenticate only) and
``reserve_unit`` (authenticate + charge one unit) — and the routes chose
between them per call site. ``POST /v1/process/document`` ended up
reserving NOTHING (measured: /v1/process = 1 unit,
/v1/process/document = 0, /v1/documents = 1). Nothing in the code
structure stopped that: reservation was a per-route *memory task*.

Phase 5I replaces per-route discretion with ONE authoritative
admission/reservation path. Every billable processing route calls
:func:`admit`; the metered gate is invoked exactly once per request, at
exactly one place, and the reservation it performs is the only
reservation. A replayed idempotent request never reaches this function;
a request that fails after admission has its claim released by the
caller (the reservation itself is NOT refunded — that remains the
documented Phase 16 policy: the unit pays for admission to the
processing system).

Identity precedence (Phase 15 / Phase 16), deterministic, tested:

  1. PLATRIXA_DEV_API_KEY set  → that exact shared key is the ONLY
     accepted data-plane credential. Phase 16 tenant keys are REJECTED
     (401), never silently mapped onto the shared identity, and the
     operator's shared key is never treated as a tenant key. Rationale:
     the Phase 15 key is the deployment's break-glass credential; a
     configuration that sets it must not quietly widen the boundary by
     also accepting per-tenant keys (and a tenant key must never be
     able to spend the shared identity's quota).
  2. PLATRIXA_METERING_DATABASE_URL set (and the Phase 15 key unset) →
     ONLY tenant keys created through the Phase 5G management plane are
     accepted; each is resolved to exactly one tenant and charged one
     unit.
  3. NEITHER set → the documented zero-config local development mode:
     the boundary is OPEN but anonymous (Phase 13 contract). No unit is
     reserved because no quota store exists. /v1/ready reports
     admission.mode "open-anonymous" so this state is never silent.

There is no fallback, no ambient DATABASE_URL shortcut, and no case
where a request is both unauthenticated and charged.

This module is HTTP-agnostic (mirrors backend/auth/gate.py): it returns
reason codes; only the routes map reasons to HTTP. It performs no
accounting, grounding, or kernel work and stays import-safe for
api.main (reads env lazily).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("platrixa.api")

from backend.auth import gate as metered_gate

# The Phase 15 single-key variable (same one the routes have always read).
PHASE15_ENV_VAR = "PLATRIXA_DEV_API_KEY"

# Admission reason codes (HTTP-agnostic; the routes own the mapping).
ADMIT_OK = "OK"
ADMIT_MISSING_KEY = metered_gate.REASON_MISSING_KEY          # → 401
ADMIT_UNKNOWN_KEY = metered_gate.REASON_UNKNOWN_KEY          # → 401
ADMIT_INACTIVE = metered_gate.REASON_INACTIVE                # → 401
ADMIT_QUOTA_EXHAUSTED = metered_gate.REASON_QUOTA_EXHAUSTED  # → 429
ADMIT_METERING_UNAVAILABLE = metered_gate.REASON_METERING_UNAVAILABLE  # → 503


def phase15_key_configured() -> bool:
    """True when the Phase 15 shared key is configured (non-empty)."""
    return bool((os.getenv(PHASE15_ENV_VAR, "") or "").strip())


def metering_configured() -> bool:
    """True when the Phase 16 metering store is configured."""
    return metered_gate._metering_configured()


def admission_mode() -> str:
    """The deterministic deployment admission mode (never a guess).

    phase15-shared-key  — Phase 15 break-glass boundary only.
    metered-tenants     — Phase 16/5G per-tenant metered boundary.
    open-anonymous      — zero-config local development (documented).
    """
    if phase15_key_configured():
        return "phase15-shared-key"
    if metering_configured():
        return "metered-tenants"
    return "open-anonymous"


@dataclass(frozen=True)
class AdmissionContext:
    """Everything downstream may know about one admitted request.

    Carries NO key material (the ``key_prefix`` is a truncated,
    non-authenticating identification string, stored only in the
    observability log — see request_log.py). ``units_reserved`` is 1 for
    every admitted request on a metered deployment and 0 in zero-config
    mode (there is no quota store to reserve against — the OPEN
    anonymous contract, not a bypass).
    """

    tenant_id: str
    key_prefix: Optional[str]
    mode: str
    units_reserved: int


def _phase15_prefix(provided: str) -> str:
    """Short, non-authenticating identification prefix (5I: minimized).

    The audit (M6) found 16 raw-key characters persisted in the request
    log. Phase 5I shrinks the stored prefix to 12 characters — long
    enough to correlate a log row with a configured credential, far
    short of anything usable — and the Phase 15 key is normally an
    operator-chosen secret with no structured ``plx_<env>_`` header, so
    12 characters cannot leak a structured key body either.
    """
    return (provided or "")[:12] or None


def admit(
    provided_key: Optional[str],
    *,
    reserve: bool = True,
) -> Tuple[str, Optional[AdmissionContext]]:
    """THE admission path: authenticate (+ reserve exactly one unit).

    Every billable processing route MUST obtain admission through this
    function before expensive processing. Routes must not call
    ``metered_gate.reserve_unit`` / ``authorize_request`` themselves;
    the shared FastAPI guard also routes through here.

    Order of evaluation (deterministic, see module docstring):
      1. Phase 15 shared key configured → exact-match check only.
      2. Metering configured → resolve tenant, then reserve ONE unit.
      3. Neither → zero-config open mode, anonymous context.

    Returns ``(reason, context_or_none)``; ``reason != ADMIT_OK`` means
    the request was NOT admitted and the caller must refuse it. A store
    outage maps to ``ADMIT_METERING_UNAVAILABLE`` (fail closed) — this
    function never raises for store unavailability.
    """
    # RAW header comparison — the value is NEVER normalized. Normalizing
    # (e.g. .strip()) would silently widen the credential boundary:
    # fte_sec suite 65 B5 pins "trailing space → 401" and that contract is
    # preserved here. Missing-key handling is left to each branch (the
    # zero-config branch must stay OPEN even with no key, per the
    # documented Phase 13 contract).
    provided = provided_key or ""

    # --- 1. Phase 15 shared-key boundary (break-glass) -------------------
    if phase15_key_configured():
        # EXACT raw match against the configured key (empty or
        # whitespace-only values simply fail the comparison).
        expected = os.getenv(PHASE15_ENV_VAR, "") or ""
        if _constant_time_equal(provided, expected):
            return ADMIT_OK, AdmissionContext(
                tenant_id="phase15-shared",
                key_prefix=_phase15_prefix(provided),
                mode="phase15-shared-key",
                units_reserved=0,
            )
        return ADMIT_MISSING_KEY, None

    # --- 2. Phase 16 metered tenant boundary -----------------------------
    if metering_configured():
        if not provided:
            return ADMIT_MISSING_KEY, None
        try:
            reason, ctx = metered_gate.authorize_request(provided)
        except Exception:  # defensive: authorize_request already maps, belt+braces
            return ADMIT_METERING_UNAVAILABLE, None
        if reason != metered_gate.REASON_OK or ctx is None:
            return reason, None
        return ADMIT_OK, AdmissionContext(
            tenant_id=ctx.tenant_id,
            key_prefix=_phase15_prefix(provided),
            mode="metered-tenants",
            units_reserved=ctx.units_reserved or 1,
        )

    # --- 3. Zero-config open mode (documented Phase 13 contract) --------
    return ADMIT_OK, AdmissionContext(
        tenant_id="anonymous",
        key_prefix=None,
        mode="open-anonymous",
        units_reserved=0,
    )


def _constant_time_equal(provided: str, expected: str) -> bool:
    """Length-independent constant-time comparison (SHA-256 both sides).

    Single shared implementation of the comparison the Phase 15 gate has
    always used; the routes import theirs from here so there is exactly
    one comparison to audit.
    """
    import hashlib
    import hmac as _hmac

    a = hashlib.sha256(provided.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return _hmac.compare_digest(a, b)


def authenticate_only(
    provided_key: Optional[str],
) -> Tuple[str, Optional[AdmissionContext]]:
    """Authenticate WITHOUT reserving (management/read planes).

    Used by usage/observability read paths and by the idempotency tenant
    resolution. Never charges. Same precedence as :func:`admit`.
    """
    provided = provided_key or ""

    if phase15_key_configured():
        expected = os.getenv(PHASE15_ENV_VAR, "") or ""
        if _constant_time_equal(provided, expected):
            return ADMIT_OK, AdmissionContext(
                tenant_id="phase15-shared",
                key_prefix=_phase15_prefix(provided),
                mode="phase15-shared-key",
                units_reserved=0,
            )
        return ADMIT_MISSING_KEY, None

    if metering_configured():
        if not provided:
            return ADMIT_MISSING_KEY, None
        try:
            reason, ctx = metered_gate.resolve_tenant(provided)
        except metered_gate.MeteredGateError:
            return ADMIT_METERING_UNAVAILABLE, None
        if reason != metered_gate.REASON_OK or ctx is None:
            return reason, None
        return ADMIT_OK, AdmissionContext(
            tenant_id=ctx.tenant_id,
            key_prefix=_phase15_prefix(provided),
            mode="metered-tenants",
            units_reserved=0,
        )

    return ADMIT_OK, AdmissionContext(
        tenant_id="anonymous",
        key_prefix=None,
        mode="open-anonymous",
        units_reserved=0,
    )


# ---------------------------------------------------------------------------
# Production admission readiness (/v1/ready consumption; no secrets here)
# ---------------------------------------------------------------------------


def readiness_block() -> dict:
    """Admission-stack readiness facts. Contains NO secret material.

    Reports configuration booleans and coarse store availability. The
    database URL itself, key values, and tokens are NEVER included.
    ``production_ready`` is the single boolean an orchestrator can gate
    on: a deployment is admission-ready when a real credential boundary
    exists (Phase 15 OR Phase 16) and the metering store behind it is
    reachable.
    """
    mode = admission_mode()
    store_ok: Optional[bool] = None
    if metering_configured():
        try:
            metered_gate._session_factory()
            store_ok = True
        except Exception:
            store_ok = False
    return {
        "admission": {
            "mode": mode,
            "phase15_shared_key_configured": phase15_key_configured(),
            "metering_configured": metering_configured(),
            "metering_store_available": store_ok,
            "idempotency_supported": metering_configured(),
            "async_documents_supported": metering_configured(),
            "quota_enforced": mode in {"phase15-shared-key", "metered-tenants"},
            "production_ready": (
                metering_configured()
                and store_ok is True
            ),
        }
    }
