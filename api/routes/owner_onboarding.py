"""Private owner onboarding dashboard API.

Authentication is the EXISTING management plane: a complete signed gateway
assertion bound to this deployment's management tenant
(``backend.auth.api_keys.validate_management_gateway_assertion`` via
``api.routes.developer_keys.require_management``). No second auth system, no
owner token in a browser, and no path here is reachable with an ordinary
developer API key — a data-plane key cannot produce an assertion.

Every route is an explicit owner action and writes an audit row.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from api.routes.developer_onboarding import error_envelope
from api.routes.developer_keys import require_management
from api.routes.developer import _sanitize_request_id
from backend.onboarding import notifications, service

router = APIRouter(
    prefix="/v1/owner/onboarding",
    tags=["owner-onboarding"],
    dependencies=[Depends(require_management)],
)


def _actor(request: Request, owner_tenant: str) -> str:
    # The management tenant binding is the authoritative actor identity.
    return f"owner:{owner_tenant}"


@router.get("/overview", summary="Onboarding dashboard counts")
def overview(request: Request, owner_tenant: str = Depends(require_management)):
    return {"api_version": "v1", **service.owner_overview()}


@router.get("/applications", summary="Paginated application list")
def applications(
    request: Request,
    status: Optional[str] = Query(default=None, max_length=32),
    tenant_id: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    owner_tenant: str = Depends(require_management),
):
    return {
        "api_version": "v1",
        **service.owner_list_applications(
            status=status, tenant_id=tenant_id, limit=limit, offset=offset
        ),
    }


@router.get("/applications/{application_id}", summary="One application in detail")
def application_detail(request: Request, application_id: str, owner_tenant: str = Depends(require_management)):
    return {"api_version": "v1", **service.owner_application_detail(application_id)}


@router.post("/applications/{application_id}/advance", summary="Re-run eligibility")
def advance(request: Request, application_id: str, owner_tenant: str = Depends(require_management)):
    result = service.advance_application(application_id, actor=_actor(request, owner_tenant))
    return {"api_version": "v1", **result}


@router.post("/applications/{application_id}/provision", summary="Retry provisioning")
def provision(request: Request, application_id: str, owner_tenant: str = Depends(require_management)):
    result = service.provision_application(application_id, actor=_actor(request, owner_tenant))
    return {"api_version": "v1", **result}


@router.post("/applications/{application_id}/suspend", summary="Suspend developer access")
def suspend(
    request: Request,
    application_id: str,
    payload: Optional[dict[str, Any]] = Body(default=None),
    owner_tenant: str = Depends(require_management),
):
    reason = (payload or {}).get("reason") if isinstance(payload, dict) else None
    result = service.owner_set_status(
        application_id, "suspend", actor=_actor(request, owner_tenant),
        reason=reason if isinstance(reason, str) else None,
    )
    return {"api_version": "v1", **result}


@router.post("/applications/{application_id}/restore", summary="Restore suspended access")
def restore(request: Request, application_id: str, owner_tenant: str = Depends(require_management)):
    result = service.owner_set_status(
        application_id, "restore", actor=_actor(request, owner_tenant)
    )
    return {"api_version": "v1", **result}


@router.post("/applications/{application_id}/reject", summary="Reject an application")
def reject(
    request: Request,
    application_id: str,
    payload: Optional[dict[str, Any]] = Body(default=None),
    owner_tenant: str = Depends(require_management),
):
    reason = (payload or {}).get("reason") if isinstance(payload, dict) else None
    result = service.owner_set_status(
        application_id, "reject", actor=_actor(request, owner_tenant),
        reason=reason if isinstance(reason, str) else None,
    )
    return {"api_version": "v1", **result}


@router.post("/tenants/{tenant_id}/quota", summary="Set a tenant's monthly quota")
def set_quota(
    request: Request,
    tenant_id: str,
    payload: dict[str, Any] = Body(...),
    owner_tenant: str = Depends(require_management),
):
    limit = payload.get("monthly_limit")
    result = service.owner_set_quota(tenant_id, limit, actor=_actor(request, owner_tenant))
    return {"api_version": "v1", **result}


@router.post("/tenants/{tenant_id}/keys/{key_id}/revoke", summary="Revoke a tenant key")
def revoke_key(request: Request, tenant_id: str, key_id: str, owner_tenant: str = Depends(require_management)):
    result = service.owner_revoke_key(tenant_id, key_id, actor=_actor(request, owner_tenant))
    return {"api_version": "v1", **result}


@router.post("/tenants/{tenant_id}/keys/rotate", summary="Rotate a tenant key")
def rotate_key(request: Request, tenant_id: str, owner_tenant: str = Depends(require_management)):
    result = service.rotate_tenant_key(tenant_id, None, actor=_actor(request, owner_tenant))
    return {"api_version": "v1", **result}


@router.get("/notifications", summary="Notification delivery states")
def notification_list(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_tenant: str = Depends(require_management),
):
    return {
        "api_version": "v1",
        **service.owner_notifications_list(limit=limit, offset=offset),
    }


@router.post("/notifications/dispatch", summary="Flush due notifications now")
def dispatch_notifications(request: Request, owner_tenant: str = Depends(require_management)):
    return {"api_version": "v1", **notifications.dispatch_due(50)}


@router.post("/maintenance/expire", summary="Expire abandoned applications")
def expire(request: Request, owner_tenant: str = Depends(require_management)):
    return {
        "api_version": "v1",
        **service.expire_abandoned(actor=_actor(request, owner_tenant)),
    }
