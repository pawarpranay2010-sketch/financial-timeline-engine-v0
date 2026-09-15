"""Platrixa metered developer gate (Phase 16).

Admission control for the hosted developer API:

    HTTP request
        ↓
    X-Platrixa-API-Key header
        ↓
    hash (SHA-256) → tenant row lookup        (401 on unknown/inactive)
        ↓
    ATOMIC quota reservation                  (429 when exhausted)
        ↓
    TenantContext → the existing public interface → Kernel

Hard boundary rules:

  * This module NEVER calls the Kernel, the model provider, accounting,
    grounding, or the RuleEngine. It is an admission-control boundary
    and contains no financial logic of any kind.
  * The raw API key exists ONLY inside the request scope that supplied
    it. It is hashed immediately, never persisted, never logged, never
    serialized into a TenantContext, a response, or an error message.
  * Authentication and quota enforcement happen BEFORE the public
    interface is resolved and before Kernel.process is invoked — a
    rejected request must never trigger a model load or inference.
  * The reservation is ONE database UPDATE whose WHERE clause carries
    the full admission predicate (active key, current-bucket usage
    below limit, or a stale-bucket row eligible for rollover).
    PostgreSQL row-level locking on the primary key serializes
    concurrent UPDATEs for the same key, so the count of accepted
    reservations can never exceed ``monthly_limit``. There is no
    read-increment-write race and no compensation logic.
  * Month semantics: ``usage_month`` is a deterministic UTC "YYYY-MM"
    bucket. A stale bucket transitions into the new bucket INSIDE the
    reservation UPDATE itself (usage resets to 1 in the same atomic
    statement) — no scheduler, and August usage can never be counted
    as September usage.
  * Activation is EXPLICIT: metering runs only when
    PLATRIXA_METERING_DATABASE_URL is set. There is deliberately NO
    DATABASE_URL fallback — an implicit fallback would silently convert
    the documented zero-config local contract into fail-closed 401s on
    any host with an ambient DATABASE_URL, and could brick an
    unconfigured deployment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import and_, case, or_, update

logger = logging.getLogger("platrixa.api")

# Metering is activated explicitly via PLATRIXA_METERING_DATABASE_URL.
# Rationale (audit-backed): backend.database.db raises at import when
# DATABASE_URL is absent, so importing it at module scope would break
# the Phase 7F clean-import contract for /health. Reading the variable
# at request time keeps the API importable without any database. A
# dedicated variable also lets the metering store differ from the
# application store, and keeps zero-config local development open (the
# gate simply does not activate — documented behavior).
METERING_ENV_VAR = "PLATRIXA_METERING_DATABASE_URL"

# HTTP semantics are the route's concern; the gate reports outcomes as
# reason codes so this module stays HTTP-agnostic and directly testable.
REASON_OK = "OK"
REASON_MISSING_KEY = "MISSING_KEY"  # → 401
REASON_UNKNOWN_KEY = "UNKNOWN_KEY"  # → 401 (externally indistinguishable)
REASON_INACTIVE = "INACTIVE"  # → 401
REASON_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"  # → 429
REASON_METERING_UNAVAILABLE = "METERING_UNAVAILABLE"  # → 503 (fail closed)


class MeteredGateError(Exception):
    """Metering store unavailable while enforcing admission (fail closed)."""


@dataclass(frozen=True)
class TenantContext:
    """Minimal read-only context handed to downstream code.

    Carries NO key material — raw key or hash. ``units_reserved`` is 1
    for every admitted request (the reservation happened before this
    object was constructed) and 0 whenever admission failed, so the
    auditability questions ("was this request authenticated?", "was one
    quota unit reserved?") are answerable from this object alone.
    """

    tenant_id: str
    monthly_limit: int
    current_month_usage: int
    usage_month: str
    units_reserved: int = 0

    def to_safe_dict(self) -> dict:
        """Serialisable audit view — never contains key material."""
        return {
            "tenant_id": self.tenant_id,
            "monthly_limit": self.monthly_limit,
            "current_month_usage": self.current_month_usage,
            "usage_month": self.usage_month,
            "units_reserved": self.units_reserved,
        }


# ---------------------------------------------------------------------------
# Store wiring (lazy, request-time; never at API import)
# ---------------------------------------------------------------------------


def _metering_database_url() -> Optional[str]:
    """The metering store URL, if metering is configured (else None).

    Metering activates ONLY on its dedicated variable,
    PLATRIXA_METERING_DATABASE_URL. There is deliberately NO fallback to
    DATABASE_URL: an implicit fallback would silently flip the documented
    Phase 15 contract ("zero-config local development stays open") into
    fail-closed 401s on any host whose .env happens to define DATABASE_URL,
    and could brick an unconfigured deployment (every request 401s against
    a tenant table that was never seeded). Explicit over implicit: the
    operator who wants metering sets the variable, runs init_metering, and
    seeds tenants — one deliberate act, fully documented.

    Reading ``os.environ`` directly (instead of importing
    backend.database.db, which raises when DATABASE_URL is unset) preserves
    the Phase 7F clean-import contract.
    """
    return (os.getenv(METERING_ENV_VAR, "") or "").strip() or None


def _session_factory():
    """A sessionmaker bound to the METERING store (not the app store).

    A dedicated engine is built lazily per unique URL and cached, so the
    metering gate works even when backend.database.db is not importable
    (no DATABASE_URL) and never touches the application pool.
    """
    global _session_factory_cache
    url = _metering_database_url()
    if url is None:
        raise MeteredGateError("metering store not configured")
    cached = _session_factory_cache.get(url)
    if cached is not None:
        return cached
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url, pool_pre_ping=True, future=True)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _session_factory_cache[url] = factory
    return factory


_session_factory_cache: dict = {}


def _metering_configured() -> bool:
    return _metering_database_url() is not None


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def resolve_tenant(
    provided_key: Optional[str],
) -> Tuple[str, Optional[TenantContext]]:
    """Authenticate an API key WITHOUT consuming quota.

    Returns ``(reason, context_or_none)``. Pure lookup — used to answer
    "is this key known and active?" and by :func:`reserve_unit` to
    distinguish authentication failure from quota exhaustion.
    Raises :class:`MeteredGateError` when the metering store is
    unavailable (callers must fail closed).
    """
    from backend.auth.models import TenantQuota, current_usage_month
    from backend.auth.tokens import hash_token

    if not provided_key or not provided_key.strip():
        return REASON_MISSING_KEY, None
    if not _metering_configured():
        # No metering store configured → no credentials exist to match.
        return REASON_UNKNOWN_KEY, None

    key_hash = hash_token(provided_key)
    try:
        SessionLocal = _session_factory()
    except Exception as exc:
        raise MeteredGateError("metering store unavailable") from exc

    try:
        with SessionLocal() as session:
            row = session.get(TenantQuota, key_hash)
            if row is None:
                return REASON_UNKNOWN_KEY, None
            if not row.is_active:
                return REASON_INACTIVE, None
            month = current_usage_month()
            usage = row.current_month_usage if row.usage_month == month else 0
            return REASON_OK, TenantContext(
                tenant_id=row.tenant_id,
                monthly_limit=row.monthly_limit,
                current_month_usage=usage,
                usage_month=month,
                units_reserved=0,
            )
    except MeteredGateError:
        raise
    except Exception as exc:
        raise MeteredGateError("metering lookup failed") from exc


def reserve_unit(provided_key: Optional[str]) -> Tuple[str, Optional[TenantContext]]:
    """Authenticate AND atomically reserve one unit of monthly quota.

    The reservation is a single UPDATE statement. Its WHERE clause holds
    the complete admission predicate:

        hash matches  AND  is_active  AND
        ( same-month bucket AND usage < limit
          OR  stale month bucket (rollover — resets to 1) )

    PostgreSQL serializes concurrent UPDATEs on the same primary-key
    row, therefore:

        concurrent accepted reservations <= monthly_limit

    under arbitrary concurrency — no read-modify-write race, no
    retries, no post-hoc compensation. A quota-exhausted or unknown key
    matches zero rows and NOTHING is written (authentication failures
    and quota failures consume zero quota).

    Returns ``(reason, context_or_none)``; raises
    :class:`MeteredGateError` when the store is unavailable.
    """
    from backend.auth.models import TenantQuota, current_usage_month
    from backend.auth.tokens import hash_token

    if not provided_key or not provided_key.strip():
        return REASON_MISSING_KEY, None
    if not _metering_configured():
        return REASON_UNKNOWN_KEY, None

    key_hash = hash_token(provided_key)
    month = current_usage_month()
    now = datetime.now(timezone.utc)

    try:
        SessionLocal = _session_factory()
    except Exception as exc:
        raise MeteredGateError("metering store unavailable") from exc

    try:
        with SessionLocal() as session:
            result = session.execute(
                update(TenantQuota)
                .where(
                    TenantQuota.api_key_hash == key_hash,
                    TenantQuota.is_active.is_(True),
                    or_(
                        and_(
                            TenantQuota.usage_month == month,
                            TenantQuota.current_month_usage
                            < TenantQuota.monthly_limit,
                        ),
                        # Stale bucket: rollover is always admissible —
                        # the same statement resets usage into the new
                        # bucket, so the limit applies to the NEW month.
                        TenantQuota.usage_month != month,
                    ),
                )
                .values(
                    current_month_usage=case(
                        (TenantQuota.usage_month != month, 1),
                        else_=TenantQuota.current_month_usage + 1,
                    ),
                    usage_month=month,
                    last_request_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            reserved = result.rowcount or 0

        if reserved == 1:
            # Re-read the committed row for the exact audit counters.
            reason, ctx = resolve_tenant(provided_key)
            if reason == REASON_OK and ctx is not None:
                return REASON_OK, TenantContext(
                    tenant_id=ctx.tenant_id,
                    monthly_limit=ctx.monthly_limit,
                    current_month_usage=ctx.current_month_usage,
                    usage_month=ctx.usage_month,
                    units_reserved=1,
                )
            # Practically unreachable (row existed a moment ago); treat
            # defensively as a store problem — fail closed.
            raise MeteredGateError("post-reservation readback failed")

        # Zero rows updated: distinguish why, via a pure lookup that
        # consumes nothing. (This lookup is safe, not a retry — the
        # reservation attempt already happened exactly once.)
        reason, _ = resolve_tenant(provided_key)
        if reason in (REASON_UNKNOWN_KEY, REASON_INACTIVE, REASON_MISSING_KEY):
            return reason, None
        return REASON_QUOTA_EXHAUSTED, None
    except MeteredGateError:
        raise
    except Exception as exc:
        raise MeteredGateError("metering reservation failed") from exc


def authorize_request(
    provided_key: Optional[str],
) -> Tuple[str, Optional[TenantContext]]:
    """Full admission decision, mapping store failure to fail-closed.

    Never raises for store unavailability: an unavailable metering
    dependency returns ``REASON_METERING_UNAVAILABLE`` so the HTTP layer
    can refuse the request (503) instead of admitting it. The critical
    property: metering failure ⇒ request NOT admitted.
    """
    try:
        return reserve_unit(provided_key)
    except MeteredGateError as exc:
        logger.warning("metering gate unavailable: %s", type(exc).__name__)
        return REASON_METERING_UNAVAILABLE, None
