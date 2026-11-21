"""Public Developer API onboarding routes (v1).

Surface split, mirroring the existing Phase 5G management plane:

  PUBLIC (no API key — the website surface is authenticated by the edge
  visitor assertion + CSRF origin check in api.main's middleware):
      POST /v1/developer/access-requests
      POST /v1/developer/verify-email
      POST /v1/developer/resend-verification
      GET  /v1/developer/access-status

  DEVELOPER (authenticated by the tenant's OWN data-plane key):
      POST /v1/developer/keys/reveal      one-time key retrieval
      POST /v1/developer/keys/rotate      revoke + replace
      GET  /v1/usage                      existing data-plane usage (reused)
      GET  /v1/developer/jobs             tenant-scoped job history

Tenant identity ALWAYS comes from the authenticated key, never from a query
parameter, body field or header. There is no route here that accepts a tenant
id from the caller.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from api.routes.developer import _metered_api_key_guard, _sanitize_request_id
from backend.auth import async_jobs as _async_jobs
from backend.auth import gate as metered_gate
from backend.onboarding import service

router = APIRouter(prefix="/v1/developer", tags=["developer-onboarding"])

API_VERSION = "v1"


def error_envelope(
    code: str,
    message: str,
    request_id: Optional[str] = None,
    field: Optional[str] = None,
) -> dict:
    error: dict = {"code": code, "message": message}
    if field:
        error["field"] = field
    if request_id:
        error["request_id"] = request_id
    return {"api_version": API_VERSION, "error": error}


def _request_id(request: Request) -> Optional[str]:
    return _sanitize_request_id(request.headers.get("x-request-id", ""))


def register_error_handlers(app) -> None:
    """Render onboarding errors as the deterministic /v1 envelope."""

    @app.exception_handler(service.OnboardingError)
    async def _onboarding_error_handler(request: Request, exc: service.OnboardingError):
        headers = {"X-Platrixa-Error": exc.code}
        if exc.http_status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=exc.http_status,
            headers=headers,
            content=error_envelope(exc.code, exc.message, _request_id(request), exc.field),
        )


def _tenant_from_key(request: Request) -> str:
    """Resolve the authenticated tenant. Fails closed if there is none."""
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, context = metered_gate.resolve_tenant(provided)
    if reason != metered_gate.REASON_OK or context is None:
        # Never disclose which of missing/unknown/inactive applied.
        raise service.OnboardingError("UNAUTHORIZED", "missing or invalid API key", 401)
    return context.tenant_id


@router.post(
    "/access-requests",
    status_code=202,
    summary="Submit a Developer API access request",
    description=(
        "Six-field public application. The response is deliberately identical "
        "whether the address is new or already has an open application, so the "
        "endpoint cannot be used to enumerate applicants. No key is issued here: "
        "work-email verification and eligibility always come first."
    ),
)
def submit_access_request(
    request: Request,
    payload: dict[str, Any] = Body(...),
):
    from backend.auth import async_jobs

    result = service.submit_access_request(
        payload,
        client_ip=__import__("api.rate_limit", fromlist=["_client_key"])._client_key(request),
        visitor_id=async_jobs.current_visitor_scope(),
    )
    return JSONResponse(status_code=202, content={"api_version": API_VERSION, **result})


@router.post(
    "/verify-email",
    summary="Consume a single-use work-email verification token",
)
def verify_email(request: Request, payload: dict[str, Any] = Body(...)):
    result = service.verify_email(payload.get("token"))
    return {"api_version": API_VERSION, **result}


@router.post(
    "/resend-verification",
    status_code=202,
    summary="Re-send a verification link (throttled, non-enumerating)",
)
def resend_verification(request: Request, payload: dict[str, Any] = Body(...)):
    result = service.resend_verification(payload.get("email"))
    return JSONResponse(status_code=202, content={"api_version": API_VERSION, **result})


@router.get(
    "/access-status",
    summary="Check one application's status (reference + applied address)",
)
def access_status(
    request: Request,
    application_id: Optional[str] = Query(default=None, max_length=36),
    email: str = Query(..., max_length=320),
    reference: Optional[str] = Query(default=None, max_length=64, alias="reference"),
):
    result = service.access_status(application_id, email, reference=reference)
    return {"api_version": API_VERSION, **result}


@router.post(
    "/keys/reveal",
    summary="Retrieve a newly issued key exactly once",
    description=(
        "The opaque one-time token comes from the onboarding email. It is single "
        "use: after the key is shown, the server can no longer derive it. An "
        "expired or already-used token returns 410 and instructs rotation."
    ),
)
def reveal_key(request: Request, payload: dict[str, Any] = Body(...)):
    result = service.reveal_key(payload.get("token"))
    return {"api_version": API_VERSION, **result}


@router.post(
    "/keys/rotate",
    dependencies=[Depends(_metered_api_key_guard)],
    summary="Rotate the authenticated tenant's key",
    description=(
        "Revokes the current key and issues its replacement in one transaction. "
        "The new raw key is delivered once to the verified address — it is never "
        "returned in this response."
    ),
)
def rotate_key(
    request: Request,
    payload: Optional[dict[str, Any]] = Body(default=None),
):
    # Defence in depth: tenant-scoped rotation must never run on the website
    # surface, even if the edge proxy is misconfigured.
    if _async_jobs.current_visitor_scope() is not None:
        raise service.OnboardingError(
            "FORBIDDEN", "this operation is not available on the public website surface", 403
        )
    tenant_id = _tenant_from_key(request)
    key_id = None
    if isinstance(payload, dict):
        raw_key_id = payload.get("key_id")
        if isinstance(raw_key_id, str) and raw_key_id.strip():
            key_id = raw_key_id.strip()[:36]
    result = service.rotate_tenant_key(tenant_id, key_id, actor=f"tenant:{tenant_id}")
    return {"api_version": API_VERSION, **result}


# NOTE (no duplication): usage is NOT re-implemented here. The existing
# data-plane `GET /v1/usage` (api/routes/observability.py) already answers
# "current quota bucket for the authenticated key's tenant, zero quota cost",
# and the existing management `GET /v1/developer/usage` answers the tenant
# aggregate for the owner. Adding a third usage route under the same path
# would shadow one of them, so this module reuses them verbatim.


@router.get(
    "/jobs",
    dependencies=[Depends(_metered_api_key_guard)],
    summary="Tenant-scoped job history",
)
def jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    tenant_id = _tenant_from_key(request)
    visitor_id = _async_jobs.current_visitor_scope()
    if visitor_id is not None:
        return {
            "api_version": API_VERSION,
            **service.visitor_jobs(tenant_id, visitor_id, limit=limit, offset=offset),
        }
    return {
        "api_version": API_VERSION,
        **service.tenant_jobs(tenant_id, limit=limit, offset=offset),
    }
