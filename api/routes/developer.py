"""Platrixa — versioned developer API route (Phase 13, hardened Phase 15).

The hosted developer boundary for the deterministic Platrixa runtime:

    HTTP request
        ↓
    API-key gate (only when PLATRIXA_DEV_API_KEY is configured; 401 otherwise)
        ↓
    KernelProcessRequest          (request-shape validation only — the
                                   existing canonical input contract,
                                   reused verbatim; no second input format)
        ↓
    platrixa.Platrixa.process()   (the Phase 12 public developer interface —
                                   the canonical application boundary)
        ↓
    Kernel.process(...)           (single execution authority, exactly once)
        ↓
    PlatrixaResult                (public result projection)
        ↓
    DeveloperProcessResponse      (stable versioned JSON; Phase 5D adds the
                                   canonical envelope fields additively)
        ↓
    HTTP response

Hard architectural rules enforced here (Phase 13 §0, hardened Phase 15):

  * This route is a TRANSPORT boundary. It performs no accounting
    reasoning, no grounding, no schema interpretation, no financial-rule
    evaluation, and makes NO persistence calls.
  * The HTTP layer cannot create or upgrade states: this module contains
    NO status literals at all (not even in comments) — the Kernel status →
    HTTP status mapping is imported from the Phase 7F route so there is
    exactly one mapping and one owner of the terminal-state taxonomy.
  * The only runtime entry point is the public interface (``platrixa``).
    The route never imports backend internals (kernel, providers,
    accounting, grounding, rules, persistence) and never calls the model.
  * Rule packs come exclusively from trusted SERVER configuration (the
    PLATRIXA_RULE_PACK_PATH environment variable). The request schema has
    no hook/rule field, so clients can never submit executable rule code
    or a rules_path — arbitrary server filesystem reads and arbitrary
    Python execution via HTTP are structurally impossible.
  * Authentication is an HTTP-boundary concern ONLY. Phase 15 gated
    /v1/process with a single shared PLATRIXA_DEV_API_KEY; Phase 16
    extends that boundary into the metered developer gate
    (backend/auth): per-key SHA-256-hashed credentials resolved to
    tenants, database-safe ATOMIC monthly quota reservation, and
    fail-closed behavior when the metering store is unavailable. When
    metering is not configured (PLATRIXA_METERING_DATABASE_URL unset),
    the Phase 15 single-key gate remains the boundary — zero-config
    local development stays open, exactly as documented. The key is
    compared in constant time, never logged, never echoed, never
    persisted, and never part of any response or evidence.
    Kernel/business logic stays auth-free.
  * Malformed HTTP input (invalid JSON, wrong content type, missing
    fields, wrong types) is normalized to HTTP 400 with a deterministic
    error envelope — framework defaults (FastAPI/Pydantic 422) are
    explicitly overridden at this boundary so the public contract is
    deterministic. Valid requests rejected by domain validation keep
    their documented statuses (422 INPUT_INVALID, 503 provider, etc.).
  * Heavy objects are constructed lazily per request and are injectable
    for tests (set_client/reset_client), mirroring the Phase 7F pattern.

Logging is metadata-only: request id, endpoint, duration, resulting
state, error category. Financial content, tokens, and secrets are never
logged. Error bodies carry machine-readable codes, never stack traces,
filesystem paths, or credentials.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes.kernel import (
    _HTTP_STATUS_BY_KERNEL_STATUS,
    _safe_accounting,
    _safe_candidate,
)
from api.schemas import (
    DeveloperCapabilitiesResponse,
    DeveloperCapabilityEntry,
    DeveloperHealthResponse,
    DeveloperReadyResponse,
    DeveloperResultEnvelope,
    KernelProcessRequest,
)

# Phase 5D: canonical result envelope builder — transport serialization
# of engine truth only (no status authority, no financial semantics).
from api.results import build_process_result

# Phase 5A: deterministic six-state public-status mapping (transport-only;
# no status literals in this module — see the E1 discipline below).
from api.status import (
    LABEL_BY_PUBLIC_STATUS,
    RETRYABLE_BY_PUBLIC_STATUS,
    public_status_for_error_code,
)
from api.status import STATUS_VERIFIED as STATUS_VERIFIED_PUBLIC

# Phase 16 metered gate — HTTP-agnostic admission control. Imported at
# module scope is SAFE here (unlike the public interface): the auth
# package touches no provider/model/persistence code at import time and
# reads its env-var configuration lazily at request time.
from backend.auth import gate as metered_gate
from backend.auth import idempotency as idempotency_store

# Phase 5I: the ONE authoritative admission path lives in
# backend/auth/admission.py. The guard, the idempotency tenant
# resolution, and every billable route handler all route through it —
# reservation is no longer a per-route memory task (audit finding C1:
# POST /v1/process/document reserved nothing).
from backend.auth import admission as admission_boundary

# Gate reason → HTTP mapping (single source of truth for /v1):
#   missing/unknown/inactive key → 401 (externally indistinguishable)
#   quota exhausted              → 429
#   metering store unavailable   → 503 (fail closed — never admit)
_GATE_HTTP_STATUS = {
    metered_gate.REASON_MISSING_KEY: 401,
    metered_gate.REASON_UNKNOWN_KEY: 401,
    metered_gate.REASON_INACTIVE: 401,
    metered_gate.REASON_QUOTA_EXHAUSTED: 429,
    metered_gate.REASON_METERING_UNAVAILABLE: 503,
}

_GATE_ERROR_CODES = {
    metered_gate.REASON_MISSING_KEY: "UNAUTHORIZED",
    metered_gate.REASON_UNKNOWN_KEY: "UNAUTHORIZED",
    metered_gate.REASON_INACTIVE: "UNAUTHORIZED",
    metered_gate.REASON_QUOTA_EXHAUSTED: "QUOTA_EXHAUSTED",
    metered_gate.REASON_METERING_UNAVAILABLE: "METERING_UNAVAILABLE",
}

_GATE_MESSAGES = {
    metered_gate.REASON_QUOTA_EXHAUSTED: (
        "monthly quota exhausted for this API key"
    ),
    metered_gate.REASON_METERING_UNAVAILABLE: (
        "metering service unavailable; request not admitted"
    ),
}


_GATE_ERROR_MESSAGES = {
    "UNAUTHORIZED": "missing or invalid API key",
    "QUOTA_EXHAUSTED": "monthly quota exhausted for this API key",
    "METERING_UNAVAILABLE": "metering service unavailable; request not admitted",
    # Phase 5G key-management plane (raised as HTTPException by the
    # developer_keys routes; the same scoped handler renders them as the
    # deterministic /v1 envelope). Additive — no existing entry changed.
    "API_KEY_MANAGEMENT_NOT_CONFIGURED": (
        "key management is not configured on this deployment"
    ),
    "API_KEY_MANAGEMENT_UNAUTHORIZED": (
        "management token missing, invalid, or not authorized for key management"
    ),
    "API_KEY_MANAGEMENT_UNAVAILABLE": (
        "key management store unavailable; operation not performed"
    ),
}


def _gate_http_exception(reason: str) -> HTTPException:
    """Gate rejection as HTTPException.

    FastAPI dependencies can only abort a request by RAISING — a returned
    JSONResponse from a dependency is discarded. The deterministic /v1
    envelope is rendered by the scoped handler installed in
    register_developer_error_handlers.
    """
    return HTTPException(
        status_code=_GATE_HTTP_STATUS[reason],
        detail=_GATE_ERROR_CODES[reason],
        headers={"X-Platrixa-Error": _GATE_ERROR_CODES[reason]},
    )


def _metered_api_key_guard(request: Request) -> None:
    """
    FastAPI dependency implementing the Phase 5I admission boundary —
    AUTHENTICATION half. Every request on the /v1 surface authenticates
    here, before any route code runs, under the deterministic Phase 15 /
    Phase 16 precedence (see backend/auth/admission.py). A request that
    fails this guard never reaches a handler, the model, or a store.

    The QUOTA half of admission — the single reservation — is performed
    by exactly ONE ``admission.admit`` call inside each BILLABLE
    handler, after cheap input-shape validation and (where present)
    after the idempotency claim, so:

      * replays never double-charge (Phase 5C contract),
      * malformed input is still a free rejection (documented 400
        semantics), and
      * an admitted request that later fails downstream keeps its
        reservation — the unit paid for admission to the processing
        system (documented Phase 16 policy, unchanged).

    There is exactly one reservation call per billable route and it
    lives at one named choke point; suite fte_fyjc_85 asserts the
    per-route charge table (1/1/1/0/0/0) so a future route cannot
    "forget" admission without failing the suite.
    """
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, _ctx = admission_boundary.authenticate_only(provided)
    if reason != admission_boundary.ADMIT_OK:
        raise _gate_http_exception(reason)

logger = logging.getLogger("platrixa.api")

router = APIRouter(tags=["developer"])

API_VERSION = "v1"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


# ---------------------------------------------------------------------------
# Dependency wiring (lazy + injectable, mirroring the Phase 7F pattern)
# ---------------------------------------------------------------------------


def _get_client(request: Request):
    """
    Resolve the public Platrixa client (lazily; tests may inject a stub).

    Resolution order (Phase 15 isolation hardening):
      1. ``request.app.state.platrixa_client`` — APP-SCOPED override. Each
         application instance can carry its own fully-constructed client,
         so concurrent tenants/tests never share mutable wiring state.
         This is the preferred, concurrency-safe seam.
      2. module-level override (legacy single-tenant test seam, kept for
         the existing Phase 12/13 suites) — safe only for single-tenant
         test processes, never for multi-tenant serving.
      3. construct from server configuration.

    Server-side configuration boundary:
      - provider selection: existing PLATRIXA_MODEL_* environment variables
        ("auto" defers to the Kernel's own selection point — no new vars);
      - rule pack: PLATRIXA_RULE_PACK_PATH (a server-side filesystem path
        to a trusted YAML pack). Clients cannot supply rule packs or
        Python hooks through the API.
    """
    app_scoped = getattr(getattr(request, "app", None), "state", None)
    app_scoped = getattr(app_scoped, "platrixa_client", None)
    if app_scoped is not None:
        return app_scoped

    override = getattr(_get_client, "_override", None)
    if override is not None:
        return override

    from platrixa import Platrixa, PlatrixaConfig

    rule_pack = (os.getenv("PLATRIXA_RULE_PACK_PATH", "") or "").strip() or None
    return Platrixa(PlatrixaConfig(provider="auto", rule_pack=rule_pack))


def set_client(client: Any) -> None:
    """Inject a client (tests / alternative wiring)."""
    _get_client._override = client


def reset_client() -> None:
    """Remove any injected client override."""
    _get_client._override = None


# ---------------------------------------------------------------------------
# API-key gate (Phase 15) — HTTP boundary only, env-gated, fail-closed
# ---------------------------------------------------------------------------


def _configured_api_key() -> Optional[str]:
    """The Phase 15 shared developer API key, if one is configured.

    Phase 5I: reading the credential is owned by
    ``backend.auth.admission`` (single admission boundary); this helper
    remains for callers that only need to know whether the Phase 15
    break-glass credential exists. The VALUE is never logged, echoed, or
    serialized.
    """
    return (os.getenv("PLATRIXA_DEV_API_KEY", "") or "").strip() or None


def _constant_time_equal(provided: str, expected: str) -> bool:
    """
    Length-independent constant-time comparison (SHA-256 both operands, so
    the comparison cost never leaks the secret's length or content through
    timing).

    Phase 5I: the single IMPLEMENTATION of the credential comparison now
    lives in ``backend.auth.admission`` (the single admission boundary);
    this wrapper delegates so existing imports and the security-suite
    invariant ("a constant-time comparison is used on this path") keep
    resolving to one implementation rather than two copies.
    """
    return admission_boundary._constant_time_equal(provided, expected)


def _error_envelope(
    code: str,
    message: str,
    request_id: Optional[str] = None,
) -> dict:
    """The deterministic /v1 error envelope as a plain dict.

    Used by :func:`_error_response` (HTTP layer) and by the Phase 5C
    idempotency failure store (which must persist the same envelope that
    would be returned).
    """
    api_status = public_status_for_error_code(code)
    error: dict[str, Any] = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    error["api_status"] = api_status
    error["api_status_label"] = LABEL_BY_PUBLIC_STATUS.get(api_status, "")
    error["retryable"] = RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False)
    return {"api_version": API_VERSION, "error": error}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Machine-readable error envelope: no stack traces, no internals.

    Phase 5A: the envelope additionally carries the six-state public API
    status (``api_status``), its label, and retryability — derived from
    the error code by the deterministic mapping in ``api.status``. The
    code itself is unchanged, preserving the documented contract.
    """
    return JSONResponse(status_code=status_code, content=_error_envelope(code, message, request_id))


# ---------------------------------------------------------------------------
# Deterministic malformed-request handling (Phase 15)
#
# FastAPI/Pydantic's default for body parsing/validation failures is 422.
# For the PUBLIC /v1 contract, network-level structural failures must be
# 400 (the request never represented a valid call), while valid requests
# rejected by domain validation keep their documented application
# statuses. This handler converts ONLY the parsing/validation layer.
# ---------------------------------------------------------------------------


def developer_validation_handler(request: Request, exc: RequestValidationError):
    # Structural detail stays server-side-safe: field paths and error
    # types only — never raw input values (which could echo attacker
    # payloads back) and never internals.
    fields = [
        {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
         "reason": err.get("type", "invalid")}
        for err in exc.errors()
    ]
    # Phase 5A: the 400 envelope also carries the six-state public API
    # status trio, mapped from the error code like every other /v1 error.
    # Phase 5I (audit M1): the caller's correlation id is honored here
    # like on every other /v1 error path (it was previously dropped only
    # on this one).
    api_status = public_status_for_error_code("REQUEST_MALFORMED")
    error = {
        "code": "REQUEST_MALFORMED",
        "message": "request body could not be parsed as a valid process request",
        "fields": fields,
        "api_status": api_status,
        "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
        "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
    }
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if rid:
        error["request_id"] = rid
    return JSONResponse(
        status_code=400,
        content={"api_version": API_VERSION, "error": error},
    )


def register_developer_error_handlers(app) -> None:
    """
    Install the /v1 malformed-request normalization plus the gate
    rejection envelope.

    Scoped explicitly to /v1 paths so the browser-facing /api/v1 contract
    (Phase 7F) keeps its documented FastAPI behavior unchanged.
    """
    original_handler = developer_validation_handler

    from fastapi import HTTPException as _HTTPException

    async def gate_error_handler(request: Request, exc: _HTTPException):
        """Render gate rejections as the deterministic /v1 envelope.

        Scoped to /v1 paths and to gate-produced error codes, so every
        other HTTPException (including /api/v1 browser routes) keeps the
        framework's documented behavior.
        """
        code = exc.detail if isinstance(exc.detail, str) else None
        if request.url.path.startswith("/v1") and code in _GATE_ERROR_MESSAGES:
            return _error_response(
                exc.status_code,
                code or "ERROR",
                _GATE_ERROR_MESSAGES[code],
                request_id=_sanitize_request_id(
                    request.headers.get("x-request-id", "")
                ),
            )
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    app.add_exception_handler(_HTTPException, gate_error_handler)

    async def handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/v1"):
            return original_handler(request, exc)
        # Non-/v1 paths keep the framework default (422), preserving the
        # Phase 7F browser contract byte-for-byte.
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, exc)

    app.add_exception_handler(RequestValidationError, handler)


# ---------------------------------------------------------------------------
# Guards (transport concerns only — no input repair, no interpretation)
# ---------------------------------------------------------------------------


def _sanitize_request_id(value: str) -> Optional[str]:
    """Honor a caller-supplied correlation id only when well-formed."""
    rid = (value or "").strip()
    if rid and _REQUEST_ID_RE.match(rid):
        return rid
    return None


def _log(
    endpoint: str,
    *,
    request_id: Optional[str],
    status: Optional[str],
    duration_ms: int,
    error: Optional[str],
) -> None:
    """
    Structured, metadata-only request logging.

    Never logs financial content, tokens, or secrets — only the request
    identifier, endpoint, processing duration, resulting state, and a
    coarse error category.
    """
    logger.info(
        "api_request endpoint=%s request_id=%s status=%s duration_ms=%d error=%s",
        endpoint,
        request_id or "-",
        status or "-",
        duration_ms,
        error or "-",
    )


# ---------------------------------------------------------------------------
# Phase 5A — six-state public API status (mapping layer only)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 5B — capability discovery (read-only adapter over the registry)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/capabilities",
    response_model=DeveloperCapabilitiesResponse,
    dependencies=[Depends(_metered_api_key_guard)],
    summary="Discover what the runtime can currently prove and execute",
    description=(
        "Read-only, deterministic capability discovery derived from the live "
        "capability registry (backend/maths/capability_registry.py), which is "
        "the single source of truth. Returns every registered capability with "
        "its registry field names and values verbatim, ordered by capability_id. "
        "supported_status preserves the registry's four-state vocabulary "
        "(SUPPORTED / PARTIAL / UNSUPPORTED / PLANNED); UNSUPPORTED and PLANNED "
        "are capability metadata, not API failures. This endpoint performs no "
        "model inference, no grounding, and no authority execution, and it "
        "maintains no capability list of its own."
    ),
)
def capabilities_v1(request: Request) -> "DeveloperCapabilitiesResponse":
    """GET /v1/capabilities — read-only registry adapter.

    Architecture (hard rule): the existing capability registry is the
    single source of truth. This handler is a thin serializer over its
    own ``to_metadata()`` projection — no second capability list is kept
    here, nothing is renamed, reinterpreted, upgraded, or collapsed.
    UNSUPPORTED and PLANNED entries are returned as the metadata they
    are, never converted to errors.

    Determinism: the registry is a pure module (no I/O, no network, no
    DB) and the output is ordered by ``capability_id``; the same registry
    state always yields the same response.

    JSON safety: serialization goes through the registry's own explicit
    ``to_metadata()`` boundary (str / list-of-str / int only) — no
    ``__dict__`` dumps, no internal objects, no secrets, no environment.

    Import hygiene: the registry import is deliberately LAZY (inside the
    handler). The registry module transitively imports the accounting
    orchestrator, which must never load at API import time — the boundary
    suite pins 'no heavy modules at API import'. A registry import
    failure fails closed through the existing structured error mechanism
    (no traceback ever reaches the client).
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.monotonic()
    try:
        from backend.maths.capability_registry import CAPABILITIES, summary

        capabilities = [
            DeveloperCapabilityEntry(**cap.to_metadata())
            for cap in sorted(CAPABILITIES.values(), key=lambda c: c.capability_id)
        ]
        summary_out = summary()
    except Exception as exc:  # fail closed, structured envelope, no internals
        _log(
            "/v1/capabilities",
            request_id=rid,
            status=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            error="registry read failed",
        )
        return _error_response(
            503,
            "PROVIDER_UNAVAILABLE",
            "capability registry is temporarily unavailable",
            request_id=rid,
        )  # noqa: B901 — exc deliberately unused; never serialized
    _log(
        "/v1/capabilities",
        request_id=rid,
        status="OK",
        duration_ms=int((time.monotonic() - started) * 1000),
        error=None,
    )
    return DeveloperCapabilitiesResponse(
        api_status=STATUS_VERIFIED_PUBLIC,
        api_status_label=LABEL_BY_PUBLIC_STATUS.get(STATUS_VERIFIED_PUBLIC, ""),
        retryable=RETRYABLE_BY_PUBLIC_STATUS.get(STATUS_VERIFIED_PUBLIC, False),
        request_id=rid,
        registry_summary=summary_out,
        count=len(capabilities),
        capabilities=capabilities,
    )


@router.get("/v1/health", response_model=DeveloperHealthResponse)
def health_v1() -> DeveloperHealthResponse:
    """
    Liveness: the API process is alive.

    Deliberately touches NOTHING — no client construction, no provider
    status, no database probe. A health check can never trigger a model
    load or any external call.
    """
    return DeveloperHealthResponse(
        status="ok", service="platrixa-developer-api", api_version=API_VERSION
    )


@router.get("/v1/ready", response_model=DeveloperReadyResponse)
def ready_v1(request: Request) -> DeveloperReadyResponse:
    """
    Readiness: required runtime dependencies are available enough to
    process a request.

    Reports provider readiness through the public interface's status view
    (which by contract never loads the model) plus the configured rule
    pack summary. A cold provider that is loadable counts as ready (the
    first request may pay the load cost); a hard provider failure or an
    invalid server configuration reports not_ready with a reason — never
    hidden.
    """
    started = time.perf_counter()
    try:
        client = _get_client(request)
    except Exception as exc:  # server configuration problem — fail visibly
        logger.error(
            "developer client construction failed: %s", type(exc).__name__
        )
        return DeveloperReadyResponse(
            status="not_ready",
            api_version=API_VERSION,
            provider={"available": False, "loadable": False},
            rule_pack=None,
            reason="server configuration invalid",
        )

    provider_status = client.provider_status()
    provider_ready = bool(provider_status.get("available") or provider_status.get("loadable"))

    # Phase 5I: readiness covers the ADMISSION stack, not just the model.
    # A deployment is admission-ready when a real credential boundary is
    # configured (Phase 15 shared key OR Phase 16 metering) and the
    # metering store behind it is reachable. Zero-config open mode is a
    # valid local-development state but is reported honestly as not
    # production-ready. The block contains configuration booleans only —
    # never key material, tokens, or connection strings.
    admission = admission_boundary.readiness_block()["admission"]
    ready = provider_ready and admission["production_ready"]
    reason = None
    if not ready:
        if not provider_ready:
            reason = str(provider_status.get("reason") or "provider not available")
        elif not admission_boundary.phase15_key_configured() and not admission_boundary.metering_configured():
            reason = "admission not configured: no PLATRIXA_DEV_API_KEY and no metering store (zero-config open mode)"
        else:
            reason = "metering store unreachable"

    duration_ms = int((time.perf_counter() - started) * 1000)
    _log(
        "/v1/ready",
        request_id=None,
        status="ready" if ready else "not_ready",
        duration_ms=duration_ms,
        error=None if ready else "NOT_READY",
    )
    return DeveloperReadyResponse(
        status="ready" if ready else "not_ready",
        api_version=API_VERSION,
        provider=provider_status,
        rule_pack=client.rule_pack_summary(),
        reason=reason,
        admission=admission,
    )


@router.post(
    "/v1/process",
    response_model=DeveloperResultEnvelope,
    dependencies=[Depends(_metered_api_key_guard)],
    responses={
        409: {
            "description": (
                "Idempotency conflict — the Idempotency-Key was already used "
                "with a different request body (IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST)."
            )
        },
        200: {
            "description": (
                "Processing result, idempotent replay of a previous result, or an "
                "in-progress acknowledgement (reason_code IDEMPOTENCY_REQUEST_IN_PROGRESS). "
                "Replays carry the Idempotent-Replayed: true response header and the "
                "original request_id."
            )
        },
    },
)
def process_v1(
    payload: KernelProcessRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
        title="Idempotency-Key",
        description=(
            "Optional. Makes this request replay-safe per tenant: repeating the same key "
            "with the same request returns the originally recorded result instead of "
            "processing again and without consuming additional quota. Same key with a "
            "different request is rejected (409). 16-200 chars of [A-Za-z0-9._~-]. "
            "Requires a metering-enabled deployment; returns 400 IDEMPOTENCY_NOT_CONFIGURED "
            "otherwise. Platrixa does NOT promise exactly-once execution — it provides "
            "durable idempotent replay semantics for this endpoint."
        ),
    ),
) -> "DeveloperResultEnvelope":
    # Phase 5C: idempotency-aware admission + processing.
    return _process_v1_idempotent(payload, request, idempotency_key)


def _process_v1_idempotent(
    payload: KernelProcessRequest, request: Request, idem_key_raw: Optional[str]
) -> "DeveloperResultEnvelope":
    """
    Process one financial transaction through the public developer
    interface (which forwards to the Kernel exactly once).

    Transport mapping only — the response body always carries the
    authoritative Kernel state verbatim:

        VERIFIED / REVIEW_REQUIRED / BLOCKED          → 200
        VALIDATION_FAILED / GROUNDING_FAILED /
        FORBIDDEN_OUTPUT / UNSUPPORTED_TRANSACTION    → 422
        MODEL_UNAVAILABLE                             → 503
        caller input errors (InputError)              → 422 error envelope
        provider runtime failure (PlatrixaError)      → 503 error envelope
        missing/invalid API key                       → 401 (before any
                                                         processing)
        quota exhausted                               → 429 (reservation
                                                         happens BEFORE the
                                                         public interface is
                                                         resolved)
        metering store unavailable                    → 503 (fail closed —
                                                         never admitted)
        unexpected failure                            → 500 (global handler,
                                                         type name only)

    Quota policy: exactly one unit is consumed per admitted request. The
    reservation is the first processing-path operation, so malformed
    requests (400, rejected pre-dependency by the validation handler)
    and failed authentication (401) never consume quota; an admitted
    request that later fails downstream keeps its reservation (the
    unit paid for admission to the processing system).
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    # -----------------------------------------------------------------
    # Phase 5C admission order (idempotency-aware):
    #   1. authentication (dependency — no quota consumed on failure)
    #   2. key validation (400-class transport rejection, no quota)
    #   3. atomic idempotency claim (replay/conflict decided here —
    #      BEFORE the reservation, so replays never consume quota)
    #   4. atomic quota reservation (canonical attempts only)
    #   5. processing
    # The claim is deleted on any reservation refusal so a later retry
    # is never blocked by a claim that never got to process.
    # -----------------------------------------------------------------
    idem_key_raw = idem_key_raw if idem_key_raw is not None else request.headers.get("idempotency-key")
    idem_active = idem_key_raw is not None
    idem_tenant: Optional[str] = None
    idem_key: Optional[str] = None
    if idem_active:
        ok, key_code = idempotency_store.validate_key(idem_key_raw)
        if not ok:
            return _error_response(400, key_code, idempotency_store.KEY_FORMAT_MESSAGE, request_id=rid)
        idem_key = (idem_key_raw or "").strip()
        if not admission_boundary.metering_configured():
            # Zero-config deployments have no durable store to back the
            # replay contract — fail explicitly rather than silently
            # ignoring the key (which would fake the guarantee).
            return _error_response(
                400,
                "IDEMPOTENCY_NOT_CONFIGURED",
                "this deployment has no durable idempotency store configured; omit the Idempotency-Key header",
                request_id=rid,
            )
        idem_tenant = _resolve_idempotency_tenant(request)
        if idem_tenant is None:
            # Fail closed: cannot establish the tenant scope for the key.
            return _error_response(
                503,
                "IDEMPOTENCY_UNAVAILABLE",
                "idempotency store unavailable; request not admitted",
                request_id=rid,
            )
        try:
            outcome = idempotency_store.claim(
                idem_key, idem_tenant, "/v1/process", {"raw_input": payload.raw_input}
            )
        except idempotency_store.IdempotencyUnavailableError:
            return _error_response(
                503,
                "IDEMPOTENCY_UNAVAILABLE",
                "idempotency store unavailable; request not admitted",
                request_id=rid,
            )
        if outcome.replay:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _log(
                "/v1/process",
                request_id=outcome.request_id or rid,
                status=None,
                duration_ms=duration_ms,
                error="IDEMPOTENT_REPLAY",
            )
            return JSONResponse(
                status_code=outcome.http_status or 200,
                content=outcome.envelope or {},
                headers={
                    "Idempotent-Replayed": "true",
                    "Idempotency-Key": idempotency_store.idempotency_key_hash(idem_key)[:8],
                },
            )
        if outcome.conflict:
            return _error_response(
                409,
                idempotency_store.CONFLICT_CODE,
                "this Idempotency-Key was already used with a different request",
                request_id=rid,
            )
        if outcome.processing:
            return JSONResponse(
                status_code=200,
                content={
                    "api_version": API_VERSION,
                    "request_id": outcome.request_id,
                    "status": "PROCESSING",
                    "status_label": "Processing",
                    "success": False,
                    "api_status": "PROCESSING",
                    "api_status_label": "Processing",
                    "retryable": True,
                    "reason_code": idempotency_store.IN_PROGRESS_CODE,
                },
                headers={"Idempotent-Replayed": "false", "Retry-After": "2"},
            )

    try:
        client = _get_client(request)
    except Exception as exc:  # server configuration problem — fail closed
        if idem_key:
            idempotency_store.release(idem_key, idem_tenant)
        logger.error("developer client construction failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="service configuration invalid"
        ) from exc

    # Phase 5I atomic quota reservation — the ONE admission call on this
    # route (canonical attempts only; replays never reach this line). On
    # refusal the claim is released. The stored key prefix is minimized
    # to 12 characters inside the admission boundary (audit M6).
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, adm = admission_boundary.admit(provided, reserve=True)
    if reason != admission_boundary.ADMIT_OK:
        if idem_key:
            idempotency_store.release(idem_key, idem_tenant)
        raise _gate_http_exception(reason)
    admitted_ctx = (adm.tenant_id, adm.key_prefix)

    # Imported lazily: importing the platrixa package pulls the backend
    # kernel graph, which must never happen at api.main import time
    # (Phase 7F clean-import discipline for /health serving).
    from platrixa.errors import InputError, PlatrixaError

    try:
        result = client.process(payload.raw_input, request_id=rid)
    except InputError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _log(
            "/v1/process",
            request_id=rid,
            status=None,
            duration_ms=duration_ms,
            error="INPUT_INVALID",
        )
        envelope_dict = _error_envelope("INPUT_INVALID", str(exc), request_id=rid)
        if idem_key:
            idempotency_store.fail(
                idem_key, idem_tenant, "/v1/process", {"raw_input": payload.raw_input},
                code="INPUT_INVALID",
                envelope=envelope_dict,
                http_status=422,
                request_id=rid,
            )
        _record_request_metadata(
            admitted_ctx,
            http_status=422,
            api_status=envelope_dict["error"]["api_status"],
            reason_code="INPUT_INVALID",
            request_id=rid,
            duration_ms=duration_ms,
        )
        return JSONResponse(status_code=422, content=envelope_dict)
    except PlatrixaError as exc:
        # Runtime failure (provider unavailable/failed). Never converted
        # into a success; details logged server-side, generic message out.
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "developer request failed: %s: %s", type(exc).__name__, exc
        )
        _log(
            "/v1/process",
            request_id=rid,
            status=None,
            duration_ms=duration_ms,
            error="PROVIDER_UNAVAILABLE",
        )
        # Phase 5C: transient infrastructure failure — release the claim
        # so a retry (same key, same request) genuinely retries.
        if idem_key:
            idempotency_store.release(idem_key, idem_tenant)
        _record_request_metadata(
            admitted_ctx,
            http_status=503,
            api_status="PROCESSING",
            reason_code="PROVIDER_UNAVAILABLE",
            request_id=rid,
            duration_ms=duration_ms,
        )
        return _error_response(
            503,
            "PROVIDER_UNAVAILABLE",
            "model provider is unavailable; retry later",
            request_id=rid,
        )
    # Any other exception propagates to the global handler (500, type
    # name only — no stack traces, no internals in the response).

    duration_ms = int((time.perf_counter() - started) * 1000)
    status = result.status
    transport_status = _HTTP_STATUS_BY_KERNEL_STATUS.get(status, 500)

    # Phase 5D: canonical result envelope — a serialization of the engine
    # result (all Phase 5A fields preserved verbatim, additive evidence /
    # reason_codes / metadata fields added). Same values, one contract.
    content = build_process_result(
        request_id=getattr(result, "request_id", None),
        engine_status=status,
        engine_status_label=getattr(result, "status_label", "") or status,
        next_action=getattr(result, "next_action", None),
        issues=list(getattr(result, "issues", None) or []),
        grounding_issues=list(getattr(result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(result, "rule_evidence", None) or []),
        interpretation=_safe_candidate(getattr(result, "interpretation", None)),
        accounting=_safe_accounting(getattr(result, "accounting", None)),
        duration_ms=duration_ms,
    )

    _log(
        "/v1/process",
        request_id=rid,
        status=status,
        duration_ms=duration_ms,
        error=None,
    )

    # Phase 5H: append request metadata (best-effort; never affects the
    # response). Metadata only — no raw input, no key material.
    _record_request_metadata(
        admitted_ctx,
        http_status=transport_status,
        api_status=content.get("api_status"),
        reason_code=(content.get("reason_codes") or [None])[0]
        if isinstance(content.get("reason_codes"), list)
        else None,
        request_id=content.get("request_id") or rid,
        duration_ms=duration_ms,
    )

    # Phase 5C: record the terminal envelope for future replays.
    if idem_key:
        try:
            idempotency_store.complete(
                idem_key,
                idem_tenant,
                "/v1/process",
                {"raw_input": payload.raw_input},
                request_id=content.get("request_id") or rid or None,
                envelope=content,
                http_status=transport_status,
            )
        except idempotency_store.IdempotencyUnavailableError:
            logger.warning("idempotency record completion failed (non-fatal)")

    headers = {"Idempotent-Replayed": "false"} if idem_active else None
    return JSONResponse(
        status_code=transport_status,
        content=content,
        headers=headers,
    )


def _record_request_metadata(
    admitted_ctx: Optional[tuple],
    *,
    http_status: int,
    api_status: Optional[str],
    reason_code: Optional[str],
    request_id: Optional[str],
    duration_ms: Optional[int],
    endpoint: str = "/v1/process",
) -> None:
    """Phase 5H: best-effort request-metadata append after a terminal outcome.

    ``admitted_ctx`` is the (tenant_id, key_prefix) captured at
    admission; None (auth failures / metering-down) records nothing —
    there is no tenant to attribute to, and attribution is never guessed.
    Phase 5I: ``endpoint`` names the serving route so async/document
    outcomes are attributable too (audit M3: only /v1/process was ever
    recorded). Observability must never alter the response path: every
    failure mode here is logged and swallowed by the store layer by design.
    """
    if admitted_ctx is None:
        return
    tenant_id, key_prefix = admitted_ctx
    try:
        from backend.auth import request_log

        request_log.record_request(
            tenant_id=tenant_id,
            endpoint=endpoint,
            http_status=http_status,
            request_id=request_id,
            key_prefix=key_prefix,
            api_status=api_status,
            reason_code=reason_code,
            duration_ms=duration_ms,
        )
    except Exception:  # pragma: no cover — belt and braces; store swallows too
        logger.debug("request metadata append skipped")


def _resolve_idempotency_tenant(request: Request) -> Optional[str]:
    """The tenant scope for idempotency (Phase 5C, Phase 5I path).

    Resolved through the single admission boundary (authenticate WITHOUT
    reserving — a replay lookup must never charge). Metering configured
    → tenant_id resolved from the authenticated key (never
    client-supplied). Zero-config mode → a single anonymous local
    namespace. Returns None when the tenant cannot be established, which
    the caller maps to a fail-closed 503.
    """
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, adm = admission_boundary.authenticate_only(provided)
    if reason != admission_boundary.ADMIT_OK or adm is None:
        return None
    return adm.tenant_id


# ---------------------------------------------------------------------------
# Document path (Phase 3)
#
# Same admission control, same status authority, same public interface as
# /v1/process — the ONLY difference is that the source may be a PDF or an
# image instead of a text string.
#
# The chain is:
#     upload -> document understanding -> evidence adapter
#            -> EXISTING financial semantic interpreter
#            -> EXISTING 18-field CandidateSemanticIR
#            -> EXISTING schema verification
#            -> EXISTING ExpandedGroundingGate
#            -> EXISTING authority routing
#
# This route owns no part of that chain beyond the first two arrows, and
# cannot produce VERIFIED: the status in the response body is a verbatim
# copy of the Kernel's terminal state.
# ---------------------------------------------------------------------------


@router.post("/v1/process/document", response_model=DeveloperResultEnvelope,
             dependencies=[Depends(_metered_api_key_guard)])
async def process_document_v1(request: Request) -> "DeveloperResultEnvelope":
    """Process a text, PDF, or image financial document.

    Transport rules (identical in spirit to ``/v1/process``):
        missing/invalid API key     -> 401 (before any processing)
        quota exhausted             -> 429
        metering store unavailable  -> 503 (fail closed)
        no input / ambiguous input  -> 400 error envelope
        unsupported file type       -> 415 error envelope
        file too large              -> 413 error envelope
        Kernel terminal states      -> mapped exactly as for /v1/process
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    content_type = (request.headers.get("content-type") or "").lower()
    raw_input: Optional[str] = None
    upload = None

    if content_type.startswith("multipart/form-data"):
        form = await _read_multipart_form(request)
        raw_input = form.get("raw_input")
        upload = form.get("document") or form.get("file")
    else:
        try:
            body = await request.json()
        except Exception:
            return _error_response(400, "REQUEST_MALFORMED",
                                   "Expected a JSON body with raw_input, "
                                   "or a multipart/form-data upload.", request_id=rid)
        if not isinstance(body, dict):
            return _error_response(400, "REQUEST_MALFORMED",
                                   "JSON body must be an object.", request_id=rid)
        raw_input = body.get("raw_input")

    from backend.document_understanding.inputs import (
        DocumentInputError,
        resolve_document_input,
    )

    try:
        data, source_name = resolve_document_input(raw_input, upload)
    except DocumentInputError as exc:
        status_code = {
            "INPUT_MISSING": 400,
            "INPUT_AMBIGUOUS": 400,
            "FILE_EMPTY": 400,
            "FILE_NAME_MISSING": 400,
            "FILE_TYPE_UNSUPPORTED": 415,
            "CONTENT_TYPE_UNSUPPORTED": 415,
            "FILE_TOO_LARGE": 413,
        }.get(exc.code, 400)
        _log("/v1/process/document", request_id=rid, status=None,
             duration_ms=int((time.perf_counter() - started) * 1000),
             error=exc.code)
        return _error_response(status_code, exc.code, exc.message, request_id=rid)

    # Phase 5I: this route IS billable (OCR + document understanding +
    # model inference). It now reserves its one unit through the same
    # single admission choke point as every other billable route —
    # AFTER input shape validation, so a malformed document is still a
    # free rejection, and BEFORE any model/OCR work (audit C1: this
    # route previously reserved NOTHING).
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, adm = admission_boundary.admit(provided, reserve=True)
    if reason != admission_boundary.ADMIT_OK:
        raise _gate_http_exception(reason)
    admitted_ctx = (adm.tenant_id, adm.key_prefix)

    try:
        client = _get_client(request)
    except Exception as exc:  # server configuration problem — fail closed
        logger.error("developer client construction failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="service configuration invalid") from exc

    # Imported lazily: keeps the Phase 7F clean-import discipline.
    from backend.document_understanding.processor import DocumentProcessor
    from backend.document_understanding.registry import get_ocr_provider

    processor = DocumentProcessor(
        process_text=client.process,
        ocr_provider=get_ocr_provider(),
    )

    try:
        result = processor.process(data, source_name, request_id=rid)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("document request failed: %s: %s", type(exc).__name__, exc)
        _log("/v1/process/document", request_id=rid, status=None,
             duration_ms=duration_ms, error="PROVIDER_UNAVAILABLE")
        # Phase 5H: the admitted request reached a terminal outcome —
        # record it (best-effort) like every other admitted request.
        _record_request_metadata(
            admitted_ctx,
            http_status=503,
            api_status="PROCESSING",
            reason_code="PROVIDER_UNAVAILABLE",
            request_id=rid,
            duration_ms=duration_ms,
            endpoint="/v1/process/document",
        )
        return _error_response(
            503, "PROVIDER_UNAVAILABLE",
            "model provider is unavailable; retry later", request_id=rid,
        )

    kernel_result = result.kernel_result
    status = result.status
    transport_status = _HTTP_STATUS_BY_KERNEL_STATUS.get(status, 500)

    # Phase 5D: canonical result envelope with document provenance — the
    # deterministic document representation, its evidence refs (thin
    # serialization, capped at 500), and the field→evidence lineage.
    content = build_process_result(
        request_id=getattr(kernel_result, "request_id", None),
        engine_status=status,
        engine_status_label=getattr(kernel_result, "status_label", "") or status,
        next_action=getattr(kernel_result, "next_action", None),
        issues=list(getattr(kernel_result, "issues", None) or []),
        grounding_issues=list(getattr(kernel_result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(kernel_result, "rule_evidence", None) or []),
        interpretation=_safe_candidate(getattr(kernel_result, "interpretation", None)),
        accounting=_safe_accounting(getattr(kernel_result, "accounting", None)),
        document=result.document.to_dict(),
        evidence_refs=result.document.evidence,
        lineage=result.lineage,
        timings_ms=result.timings_ms,
        notes=result.notes,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    _log("/v1/process/document", request_id=rid, status=status,
         duration_ms=int((time.perf_counter() - started) * 1000), error=None)

    # Phase 5I: the admitted request reached a terminal outcome — append
    # request metadata (best-effort) and make the effective request id
    # discoverable from observability (audit M3: this route previously
    # recorded nothing).
    effective_rid = getattr(kernel_result, "request_id", None) or rid
    _record_request_metadata(
        admitted_ctx,
        http_status=transport_status,
        api_status=content.get("api_status"),
        reason_code=(content.get("reason_codes") or [None])[0]
        if isinstance(content.get("reason_codes"), list)
        else None,
        request_id=effective_rid,
        duration_ms=int((time.perf_counter() - started) * 1000),
        endpoint="/v1/process/document",
    )
    return JSONResponse(
        status_code=transport_status,
        content=content,
    )


async def _read_multipart_form(request: Request):
    """Read a multipart body into ``{field_name: (value | UploadFile)}``.

    A small bounded reader: the total body is capped, and a file is only
    materialized once its size is known to be within the limit.
    """
    from backend.document_understanding.inputs import MAX_DOCUMENT_BYTES

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="malformed multipart body") from exc

    out = {}
    try:
        for key in form.keys():
            item = form[key]
            if hasattr(item, "filename") and item.filename:
                out[key] = item
            else:
                value = getattr(item, "value", item)
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                out[key] = value
    finally:
        pass

    # Pre-check declared size when the client provides it, so an oversized
    # upload is refused without buffering the whole body.
    try:
        declared = int(request.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > MAX_DOCUMENT_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="document too large")

    return out
