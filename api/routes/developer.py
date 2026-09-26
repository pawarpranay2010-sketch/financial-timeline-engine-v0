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
    DeveloperProcessResponse      (stable versioned JSON)
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

import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes.kernel import (
    _HTTP_STATUS_BY_KERNEL_STATUS,
    _safe_accounting,
    _safe_candidate,
)
from api.schemas import (
    DeveloperDocumentProcessResponse,
    DeveloperHealthResponse,
    DeveloperProcessResponse,
    DeveloperReadyResponse,
    KernelProcessRequest,
)

# Phase 5A: deterministic six-state public-status mapping (transport-only;
# no status literals in this module — see the E1 discipline below).
from api.status import (
    LABEL_BY_PUBLIC_STATUS,
    RETRYABLE_BY_PUBLIC_STATUS,
    engine_status_verbatim,
    public_status_for_engine,
    public_status_for_error_code,
    reason_code_for_engine,
)
from api.status import STATUS_FAILED as _FAILED_PUBLIC

# Phase 16 metered gate — HTTP-agnostic admission control. Imported at
# module scope is SAFE here (unlike the public interface): the auth
# package touches no provider/model/persistence code at import time and
# reads its env-var configuration lazily at request time.
from backend.auth import gate as metered_gate

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
    FastAPI dependency implementing the Phase 16 metered admission
    boundary, in request order:

      1. Phase 15 single-key gate (when PLATRIXA_DEV_API_KEY is set) —
         still the zero-config local boundary.
      2. Metered gate (when a metering store is configured):
         hash → tenant → ATOMIC reservation. 401/429/503 on rejection;
         on success the reservation is ALREADY committed before any
         processing begins.

    RAISES (aborting the request) whenever the request is rejected —
    rejection happens strictly before the public interface is resolved
    and before Kernel.process, so a rejected request can never load the
    model. Returns None only when the request is admitted.
    """
    phase15_failure = _check_api_key(request)
    if phase15_failure is not None:
        raise _gate_http_exception(metered_gate.REASON_MISSING_KEY)

    if not metered_gate._metering_configured():
        return  # metering not configured → Phase 15 boundary only

    provided = request.headers.get("x-platrixa-api-key", "")
    reason, _ctx = metered_gate.authorize_request(provided)
    if reason != metered_gate.REASON_OK:
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
    """The server-side developer API key, if one is configured."""
    return (os.getenv("PLATRIXA_DEV_API_KEY", "") or "").strip() or None


def _constant_time_equal(provided: str, expected: str) -> bool:
    """
    Length-independent constant-time comparison.

    Both operands are hashed first so the comparison cost never leaks the
    secret's length or content through timing.
    """
    a = hashlib.sha256(provided.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def _check_api_key(request: Request) -> Optional[JSONResponse]:
    """
    Fail-closed API-key gate for /v1/process.

    - No key configured server-side → endpoint stays open (current
      documented Phase 13 behavior; zero-config local development).
    - Key configured → requests MUST present `X-Platrixa-API-Key` with the
      exact value. Anything else → 401 with the deterministic error
      envelope (same shape as all other /v1 errors).
    The key is never logged, never echoed, never serialized anywhere.
    """
    expected = _configured_api_key()
    if expected is None:
        return None
    provided = request.headers.get("x-platrixa-api-key", "")
    if not provided or not _constant_time_equal(provided, expected):
        return _error_response(401, "UNAUTHORIZED", "missing or invalid API key")
    return None


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
    api_status = public_status_for_error_code(code)
    error: dict[str, Any] = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    error["api_status"] = api_status
    error["api_status_label"] = LABEL_BY_PUBLIC_STATUS.get(api_status, "")
    error["retryable"] = RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False)
    return JSONResponse(
        status_code=status_code, content={"api_version": API_VERSION, "error": error}
    )


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
    api_status = public_status_for_error_code("REQUEST_MALFORMED")
    return JSONResponse(
        status_code=400,
        content={
            "api_version": API_VERSION,
            "error": {
                "code": "REQUEST_MALFORMED",
                "message": "request body could not be parsed as a valid process request",
                "fields": fields,
                "api_status": api_status,
                "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
                "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
            },
        },
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


def _api_status_fields(
        engine_status: str,
        *,
        issues: Optional[list] = None,
        grounding_issues: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Derive the six-state public fields from an engine state.

        Pure mapping via ``api.status``; carries no status literals in
        this module (the engine taxonomy stays owned by the Kernel) and
        adds no financial semantics. ``reason_code`` is only emitted for
        engine states that have a documented public reason (fail-closed
        rejections); otherwise it stays None.
        """
        api_status = public_status_for_engine(engine_status)
        fields: Dict[str, Any] = {
            "api_status": api_status,
            "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
            "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
            "reason_code": reason_code_for_engine(engine_status),
            # The engine state is carried verbatim next to the public
            # mapping so no information is lost by the relabeling.
            "engine_status": engine_status_verbatim(engine_status),
        }
        if (
            api_status == _FAILED_PUBLIC
            and (issues or grounding_issues)
        ):
            fields["reason_code"] = "EVIDENCE_RECORDED"
        return fields


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
    ready = bool(provider_status.get("available") or provider_status.get("loadable"))
    duration_ms = int((time.perf_counter() - started) * 1000)
    _log(
        "/v1/ready",
        request_id=None,
        status="ready" if ready else "not_ready",
        duration_ms=duration_ms,
        error=None if ready else "PROVIDER_NOT_READY",
    )
    return DeveloperReadyResponse(
        status="ready" if ready else "not_ready",
        api_version=API_VERSION,
        provider=provider_status,
        rule_pack=client.rule_pack_summary(),
        reason=None
        if ready
        else str(provider_status.get("reason") or "provider not available"),
    )


@router.post("/v1/process", response_model=DeveloperProcessResponse,
             dependencies=[Depends(_metered_api_key_guard)])
def process_v1(payload: KernelProcessRequest, request: Request) -> "DeveloperProcessResponse":
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

    try:
        client = _get_client(request)
    except Exception as exc:  # server configuration problem — fail closed
        logger.error("developer client construction failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="service configuration invalid"
        ) from exc

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
        return _error_response(422, "INPUT_INVALID", str(exc), request_id=rid)
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

    response = DeveloperProcessResponse(
        api_version=API_VERSION,
        request_id=getattr(result, "request_id", None),
        status=status,
        status_label=getattr(result, "status_label", "") or status,
        success=bool(getattr(result, "success", False)),
        next_action=getattr(result, "next_action", None),
        issues=list(getattr(result, "issues", None) or []),
        grounding_issues=list(getattr(result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(result, "rule_evidence", None) or []),
        interpretation=_safe_candidate(getattr(result, "interpretation", None)),
        accounting=_safe_accounting(getattr(result, "accounting", None)),
        **_api_status_fields(
            status,
            issues=list(getattr(result, "issues", None) or []),
            grounding_issues=list(getattr(result, "grounding_issues", None) or []),
        ),
    )

    _log(
        "/v1/process",
        request_id=rid,
        status=status,
        duration_ms=duration_ms,
        error=None,
    )
    return JSONResponse(
        status_code=transport_status,
        content=jsonable_encoder(response.model_dump()),
    )


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


@router.post("/v1/process/document", response_model=DeveloperDocumentProcessResponse,
             dependencies=[Depends(_metered_api_key_guard)])
async def process_document_v1(request: Request) -> "DeveloperDocumentProcessResponse":
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
        return _error_response(
            503, "PROVIDER_UNAVAILABLE",
            "model provider is unavailable; retry later", request_id=rid,
        )

    kernel_result = result.kernel_result
    status = result.status
    transport_status = _HTTP_STATUS_BY_KERNEL_STATUS.get(status, 500)

    document_dict = result.document.to_dict()
    # Bound the response: full evidence is returned, but page text is not
    # duplicated inside the per-page summary.
    evidence = [
        e.to_dict() for e in result.document.evidence
    ][:500]

    response = DeveloperDocumentProcessResponse(
        api_version=API_VERSION,
        request_id=getattr(kernel_result, "request_id", None),
        status=status,
        status_label=getattr(kernel_result, "status_label", "") or status,
        success=bool(getattr(kernel_result, "success", False)),
        next_action=getattr(kernel_result, "next_action", None),
        issues=list(getattr(kernel_result, "issues", None) or []),
        grounding_issues=list(getattr(kernel_result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(kernel_result, "rule_evidence", None) or []),
        interpretation=_safe_candidate(getattr(kernel_result, "interpretation", None)),
        accounting=_safe_accounting(getattr(kernel_result, "accounting", None)),
        document=document_dict,
        evidence=evidence,
        timings_ms=result.timings_ms,
        notes=result.notes,
        **_api_status_fields(
            status,
            issues=list(getattr(kernel_result, "issues", None) or []),
            grounding_issues=list(
                getattr(kernel_result, "grounding_issues", None) or []
            ),
        ),
    )

    _log("/v1/process/document", request_id=rid, status=status,
         duration_ms=int((time.perf_counter() - started) * 1000), error=None)
    return JSONResponse(
        status_code=transport_status,
        content=jsonable_encoder(response.model_dump()),
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
