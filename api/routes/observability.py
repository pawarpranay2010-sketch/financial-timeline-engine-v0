"""Platrixa — developer observability routes (Phase 5H).

Usage + request-history read model over data that already exists
durably: the Phase 16 metering row, the Phase 5C idempotency snapshots,
and the Phase 5H append-only request log. Deliberately NOT an analytics
platform — three read-only views, no new truth, no new quota behavior.

Reads consume ZERO data-plane quota, never touch the Kernel, the model,
grounding, or idempotency records.

Two planes (Phase 5G model, reused verbatim):
  * data-plane key (X-Platrixa-API-Key) → GET /v1/usage  (own usage only)
  * management token (X-Platrixa-Management-Token) → per-key usage,
    tenant aggregate, request history, request detail — strictly bound
    to PLATRIXA_KEY_MANAGEMENT_TENANT_ID

Privacy: metadata-first. No raw financial payload in history lists; no
keys, hashes, or secrets anywhere; capability_id only when the engine
reported one (never invented).

Retention (documented, not faked):
  * usage counters: current month bucket (resets monthly — Phase 16)
  * request metadata: platrixa_request_log (operator-pruned; default 30d)
  * idempotent result snapshots: Phase 5C 72h — a detail view returns the
    canonical 5D envelope when the snapshot still exists, metadata-only
    otherwise, with the boundary stated in the response.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("platrixa.api")

router = APIRouter(tags=["developer"])

API_VERSION = "v1"


def _error_envelope(code: str, message: str, request_id: Optional[str] = None) -> dict:
    """The SAME deterministic /v1 error envelope as every other route."""
    from api.status import LABEL_BY_PUBLIC_STATUS, public_status_for_error_code

    api_status = public_status_for_error_code(code)
    error: dict = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    error["api_status"] = api_status
    error["api_status_label"] = LABEL_BY_PUBLIC_STATUS.get(api_status, "")
    error["retryable"] = api_status == "PROCESSING"
    return {"api_version": API_VERSION, "error": error}


def _error_response(
    status_code: int, code: str, message: str, request_id: Optional[str] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=_error_envelope(code, message, request_id)
    )


def _sanitize_request_id(value: str) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    return v if len(v) <= 128 and all(c.isalnum() or c in "._-" for c in v) else None


# ---------------------------------------------------------------------------
# Data plane — GET /v1/usage (own usage; consumes NOTHING)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/usage",
    responses={
        200: {"description": "Current usage bucket for the authenticated key's tenant"},
        401: {"description": "Missing/unknown/inactive key — existing envelope"},
        503: {"description": "Metering store unavailable (fail closed)"},
    },
)
def get_usage(request: Request):
    """Usage for the authenticated runtime key's tenant — zero quota cost.

    Pure projection of the Phase 16 metering row (resolve_tenant is a
    read-only lookup; reserve_unit is never called). remaining is the
    deterministic max(limit - used, 0).
    """
    from backend.auth import gate as metered_gate

    if not metered_gate._metering_configured():
        return _error_response(
            503,
            "USAGE_NOT_CONFIGURED",
            "this deployment has no metering store configured; usage is not available",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    provided = request.headers.get("x-platrixa-api-key", "")
    try:
        reason, ctx = metered_gate.resolve_tenant(provided)
    except metered_gate.MeteredGateError:
        return _error_response(
            503,
            "USAGE_UNAVAILABLE",
            "metering store unavailable; usage not available",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    if reason == metered_gate.REASON_MISSING_KEY or reason == metered_gate.REASON_UNKNOWN_KEY or reason == metered_gate.REASON_INACTIVE:
        raise HTTPException(
            status_code=401,
            detail="UNAUTHORIZED",
            headers={"X-Platrixa-Error": "UNAUTHORIZED"},
        )
    if reason != metered_gate.REASON_OK or ctx is None:
        return _error_response(
            503,
            "USAGE_UNAVAILABLE",
            "usage lookup failed",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )

    used = ctx.current_month_usage or 0
    limit = ctx.monthly_limit or 0
    # The durable last-request timestamp lives on the quota row; read it
    # without mutating anything.
    last_request_at = None
    try:
        from backend.auth.gate import _session_factory as _quota_factory
        from backend.auth.models import TenantQuota
        from backend.auth.tokens import hash_token
        from sqlalchemy import select

        with _quota_factory()() as session:
            row = session.execute(
                select(TenantQuota).where(
                    TenantQuota.api_key_hash == hash_token(provided)
                )
            ).scalar_one_or_none()
            if row is not None and row.usage_month == ctx.usage_month:
                last_request_at = (
                    row.last_request_at.isoformat() if row.last_request_at else None
                )
    except Exception:
        last_request_at = None  # advisory field — hidden, not guessed

    return {
        "api_version": API_VERSION,
        "tenant_id": ctx.tenant_id,
        "month": ctx.usage_month,
        "monthly_limit": limit,
        "used": used,
        "remaining": max(limit - used, 0),
        "last_request_at": last_request_at,
    }


# ---------------------------------------------------------------------------
# Management plane — authorization identical to Phase 5G
# ---------------------------------------------------------------------------


def _management_tenant(request: Request) -> str:
    from backend.auth import api_keys as key_store

    reason, tenant_id = key_store.authorize_management(
        request.headers.get("x-platrixa-management-token", "")
    )
    if reason == key_store.MGMT_OK:
        return tenant_id  # type: ignore[return-value]
    if reason == key_store.MGMT_NOT_CONFIGURED:
        raise HTTPException(
            status_code=400, detail="API_KEY_MANAGEMENT_NOT_CONFIGURED"
        )
    if reason == key_store.MGMT_MISCONFIGURED:
        logger.warning("key-management token set without a tenant binding")
        raise HTTPException(
            status_code=503, detail="API_KEY_MANAGEMENT_UNAVAILABLE"
        )
    raise HTTPException(status_code=403, detail="API_KEY_MANAGEMENT_UNAUTHORIZED")


# ---------------------------------------------------------------------------
# Management usage — per key + tenant aggregate
# ---------------------------------------------------------------------------


@router.get(
    "/v1/developer/api-keys/{key_id}/usage",
    responses={
        200: {"description": "Usage for one tenant-owned key"},
        403: {"description": "Management authorization failed"},
        404: {"description": "Key not found (or owned by another tenant)"},
        503: {"description": "Management/usage store unavailable (fail closed)"},
    },
)
def get_key_usage(request: Request, key_id: str):
    from backend.auth import api_keys as key_store

    tenant_id = _management_tenant(request)
    try:
        keys = key_store.list_api_keys(tenant_id)
    except key_store.ApiKeyStoreError:
        return _error_response(
            503,
            "USAGE_UNAVAILABLE",
            "usage store unavailable",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    match = next((k for k in keys if k["id"] == key_id), None)
    if match is None:
        return _error_response(
            404,
            "API_KEY_NOT_FOUND",
            "no such key for this tenant",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )

    from backend.auth.gate import _session_factory as _quota_factory  # reuse store wiring
    from backend.auth.models import TenantQuota, current_usage_month
    from sqlalchemy import select

    try:
        SessionLocal = _quota_factory()
        with SessionLocal() as session:
            rows = (
                session.execute(
                    select(TenantQuota).where(TenantQuota.tenant_id == tenant_id)
                )
                .scalars()
                .all()
            )
    except Exception:
        return _error_response(
            503,
            "USAGE_UNAVAILABLE",
            "usage store unavailable",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )

    month = current_usage_month()
    agg = {"monthly_limit": 0, "used": 0}
    for row in rows:
        if row.usage_month == month:
            agg["monthly_limit"] += row.monthly_limit
            agg["used"] += row.current_month_usage

    # The key's own row is identified by its hash — which we deliberately
    # do NOT expose. Only the masked prefix leaves the system.
    return {
        "api_version": API_VERSION,
        "key_id": key_id,
        "key_prefix": match["key_prefix"],
        "month": month,
        "tenant": {
            "monthly_limit": agg["monthly_limit"],
            "used": agg["used"],
            "remaining": max(agg["monthly_limit"] - agg["used"], 0),
        },
        "note": "usage is tracked per credential row; per-key split is available via the tenant aggregate keys list",
    }


@router.get(
    "/v1/developer/usage",
    responses={
        200: {"description": "Tenant-wide usage aggregate across active credentials"},
        403: {"description": "Management authorization failed"},
        503: {"description": "Usage store unavailable (fail closed)"},
    },
)
def get_tenant_usage(request: Request):
    from backend.auth import api_keys as key_store
    from backend.auth.gate import _session_factory as _quota_factory
    from backend.auth.models import TenantQuota, current_usage_month
    from sqlalchemy import select

    tenant_id = _management_tenant(request)
    try:
        SessionLocal = _quota_factory()
        with SessionLocal() as session:
            rows = (
                session.execute(
                    select(TenantQuota).where(TenantQuota.tenant_id == tenant_id)
                )
                .scalars()
                .all()
            )
    except Exception:
        return _error_response(
            503,
            "USAGE_UNAVAILABLE",
            "usage store unavailable",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )

    month = current_usage_month()
    active = [r for r in rows if r.is_active]
    used = sum(r.current_month_usage for r in active if r.usage_month == month)
    limit = sum(r.monthly_limit for r in active)
    return {
        "api_version": API_VERSION,
        "tenant_id": tenant_id,
        "month": month,
        "active_keys": len(active),
        "monthly_limit": limit,
        "used": used,
        "remaining": max(limit - used, 0),
    }


# ---------------------------------------------------------------------------
# Request history — index + detail (management plane)
# ---------------------------------------------------------------------------


@router.get(
    "/v1/developer/requests",
    responses={
        200: {"description": "Tenant-scoped request metadata history (deterministic order)"},
        400: {"description": "Invalid pagination parameters"},
        403: {"description": "Management authorization failed"},
        503: {"description": "History store unavailable (fail closed)"},
    },
)
def list_requests(
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    from backend.auth import request_log as rl

    tenant_id = _management_tenant(request)
    if limit < 1 or limit > rl.HISTORY_MAX_LIMIT or offset < 0:
        return _error_response(
            400,
            "INPUT_INVALID",
            f"limit must be 1-{rl.HISTORY_MAX_LIMIT}; offset must be >= 0",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    try:
        rows, total = rl.list_requests(tenant_id, limit=limit, offset=offset)
    except rl.RequestLogStoreError:
        return _error_response(
            503,
            "REQUEST_HISTORY_UNAVAILABLE",
            "request history store unavailable",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    requests_out = []
    for r in rows:
        created = r.get("created_at")
        requests_out.append(
            {
                "request_id": r.get("request_id"),
                "created_at": created.isoformat() if created else None,
                "endpoint": r.get("endpoint"),
                "key_prefix": r.get("key_prefix"),
                "http_status": r.get("http_status"),
                "api_status": r.get("api_status"),
                "reason_code": r.get("reason_code"),
                "capability_id": r.get("capability_id"),
                "duration_ms": r.get("duration_ms"),
            }
        )
    return {
        "api_version": API_VERSION,
        "total": total,
        "limit": limit,
        "offset": offset,
        "requests": requests_out,
    }


@router.get(
    "/v1/developer/requests/{request_id}",
    responses={
        200: {"description": "Request detail — metadata plus the 5D result when still retained"},
        403: {"description": "Management authorization failed"},
        404: {"description": "Request not found for this tenant"},
        503: {"description": "History store unavailable (fail closed)"},
    },
)
def get_request_detail(request: Request, request_id: str):
    from backend.auth import request_log as rl

    tenant_id = _management_tenant(request)
    rid = _sanitize_request_id(request_id)
    if not rid:
        return _error_response(
            404,
            rl.NOT_FOUND,
            "no such request for this tenant",
            None,
        )
    try:
        meta = rl.get_request(tenant_id, rid)
    except rl.RequestLogStoreError:
        return _error_response(
            503,
            "REQUEST_HISTORY_UNAVAILABLE",
            "request history store unavailable",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )
    if meta is None:
        return _error_response(
            404,
            rl.NOT_FOUND,
            "no such request for this tenant",
            _sanitize_request_id(request.headers.get("x-request-id", "")),
        )

    # Result retrieval — ONLY through the existing Phase 5C snapshot
    # (never fabricated, never extended). Look up by (tenant, request_id).
    result_envelope = None
    result_source = "not_retained"
    try:
        from backend.auth import idempotency as idem

        snapshot = idem.find_result_by_request_id(tenant_id, rid)
        if snapshot is not None:
            result_envelope = snapshot.get("envelope")
            result_source = "idempotency_snapshot"
    except Exception:
        result_envelope = None
        result_source = "not_retained"

    created = meta.get("created_at")
    detail = {
        "api_version": API_VERSION,
        "request": {
            "request_id": meta.get("request_id"),
            "created_at": created.isoformat() if created else None,
            "endpoint": meta.get("endpoint"),
            "key_prefix": meta.get("key_prefix"),
            "http_status": meta.get("http_status"),
            "api_status": meta.get("api_status"),
            "reason_code": meta.get("reason_code"),
            "capability_id": meta.get("capability_id"),
            "duration_ms": meta.get("duration_ms"),
        },
        "result": {
            "available": result_envelope is not None,
            "source": result_source,
            "retention": (
                "result snapshots follow the Phase 5C idempotency retention window; "
                "metadata persists per the request-log retention policy"
            ),
        },
    }
    if result_envelope is not None:
        detail["result"]["envelope"] = result_envelope
    return detail
