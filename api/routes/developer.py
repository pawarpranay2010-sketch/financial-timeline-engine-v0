"""Platrixa — versioned developer API route (Phase 13).

The hosted developer boundary for the deterministic Platrixa runtime:

    HTTP request
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

Hard architectural rules enforced here (Phase 13, section 0):

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
    no hook/rule field, so clients can never submit executable rule code.
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

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from api.routes.kernel import (
    _HTTP_STATUS_BY_KERNEL_STATUS,
    _safe_accounting,
    _safe_candidate,
)
from api.schemas import (
    DeveloperHealthResponse,
    DeveloperProcessResponse,
    DeveloperReadyResponse,
    KernelProcessRequest,
)

logger = logging.getLogger("platrixa.api")

router = APIRouter(tags=["developer"])

API_VERSION = "v1"

# App-level request-size guard lives in api.main.create_app() (Phase 13
# middleware) — enforced before body parsing, shared by all /v1 routes.
# The semantic input limit is already enforced by the request schema
# (raw_input <= 2000 chars).

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


# ---------------------------------------------------------------------------
# Dependency wiring (lazy + injectable, mirroring the Phase 7F pattern)
# ---------------------------------------------------------------------------


def _get_client():
    """
    Resolve the public Platrixa client (lazily; tests may inject a stub).

    Server-side configuration boundary:
      - provider selection: existing PLATRIXA_MODEL_* environment variables
        ("auto" defers to the Kernel's own selection point — no new vars);
      - rule pack: PLATRIXA_RULE_PACK_PATH (a server-side filesystem path
        to a trusted YAML pack). Clients cannot supply rule packs or
        Python hooks through the API.
    """
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


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Machine-readable error envelope: no stack traces, no internals."""
    error: dict[str, Any] = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    return JSONResponse(
        status_code=status_code, content={"api_version": API_VERSION, "error": error}
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
def ready_v1() -> DeveloperReadyResponse:
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
        client = _get_client()
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


@router.post("/v1/process", response_model=DeveloperProcessResponse)
def process_v1(payload: KernelProcessRequest, request: Request) -> JSONResponse:
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
        unexpected failure                            → 500 (global handler,
                                                         type name only)
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    try:
        client = _get_client()
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
