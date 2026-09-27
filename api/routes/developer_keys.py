"""Platrixa — developer API-key management routes (Phase 5G).

Key lifecycle over the SAME metering PostgreSQL store as the runtime
admission gate. This is the MANAGEMENT PLANE — deliberately separate
from the data plane:

    X-Platrixa-API-Key  → authenticates /v1/process (data plane).
                          It can NEVER manage keys.
    X-Platrixa-Management-Token → authorizes THESE endpoints only.
                          Operator-issued, bound to exactly one tenant
                          (PLATRIXA_KEY_MANAGEMENT_TOKEN +
                          PLATRIXA_KEY_MANAGEMENT_TENANT_ID).

Raw keys are shown EXACTLY ONCE (create / rotate response), stored only
as SHA-256 hashes, never logged, never echoed, never serialized into
errors. Revocation/rotation atomically update the registry row AND the
companion quota row, so a revoked key stops authenticating at commit.

Fail-closed mapping: store unavailability → 503; authorization failure
→ 403; unknown/cross-tenant key references → 404 (indistinguishable);
validation failures → 400 with the existing envelope and codes.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from api.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyListResponse,
    ApiKeyRevokedResponse,
)
from api.status import LABEL_BY_PUBLIC_STATUS, public_status_for_error_code

logger = logging.getLogger("platrixa.api")

router = APIRouter(prefix="/v1/developer", tags=["developer"])

API_VERSION = "v1"


def _error_envelope(code: str, message: str, request_id: Optional[str] = None) -> dict:
    """The SAME deterministic /v1 error envelope as every other route."""
    api_status = public_status_for_error_code(code)
    error: dict = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    error["api_status"] = api_status
    error["api_status_label"] = LABEL_BY_PUBLIC_STATUS.get(api_status, "")
    error["retryable"] = {
        "PROCESSING": True,
        "VERIFIED": False,
        "REVIEW_REQUIRED": False,
        "UNSUPPORTED": False,
        "INVALID_INPUT": False,
        "FAILED": False,
    }.get(api_status, False)
    return {"api_version": API_VERSION, "error": error}


def _error_response(
    status_code: int, code: str, message: str, request_id: Optional[str] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=_error_envelope(code, message, request_id)
    )


def _authorize(request: Request) -> str:
    """Authorize the management plane. Returns the bound tenant_id.

    Fails closed on every ambiguous state (mirrors the gate's mapping
    discipline):
      NOT_CONFIGURED → 400 (deployment disabled; client-side config)
      MISCONFIGURED  → 503 (token set but tenant binding missing)
      INVALID        → 403 (wrong/absent token — including a runtime
                       API key masquerading as a management token)
      store error    → 503
    """
    from backend.auth import api_keys as store

    token = request.headers.get("x-platrixa-management-token", "")
    reason, tenant_id = store.authorize_management(token)
    if reason == store.MGMT_OK:
        return tenant_id  # type: ignore[return-value]

    request_id = request.headers.get("x-request-id")
    if reason == store.MGMT_NOT_CONFIGURED:
        raise HTTPException(
            status_code=400,
            detail="API_KEY_MANAGEMENT_NOT_CONFIGURED",
            headers={"X-Platrixa-Error": "API_KEY_MANAGEMENT_NOT_CONFIGURED"},
        )
    if reason == store.MGMT_MISCONFIGURED:
        logger.warning("key-management token set without a tenant binding")
        raise HTTPException(
            status_code=503,
            detail="API_KEY_MANAGEMENT_UNAVAILABLE",
            headers={"X-Platrixa-Error": "API_KEY_MANAGEMENT_UNAVAILABLE"},
        )
    raise HTTPException(
        status_code=403,
        detail="API_KEY_MANAGEMENT_UNAUTHORIZED",
        headers={"X-Platrixa-Error": "API_KEY_MANAGEMENT_UNAUTHORIZED"},
    )


def _store_error_response(request: Request) -> JSONResponse:
    request_id = request.headers.get("x-request-id")
    return _error_response(
        503,
        "API_KEY_MANAGEMENT_UNAVAILABLE",
        "key management store unavailable; operation not performed",
        request_id,
    )


# ---------------------------------------------------------------------------
# POST /v1/developer/api-keys — create (raw key shown EXACTLY ONCE)
# ---------------------------------------------------------------------------


@router.post(
    "/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=201,
    responses={
        400: {"description": "Invalid input (name/environment) — existing envelope"},
        403: {"description": "Management authorization failed"},
        503: {"description": "Management store unavailable (fail closed)"},
    },
)
def create_api_key(
    request: Request,
    payload: ApiKeyCreateRequest,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    tenant_id = _authorize(request)
    from backend.auth import api_keys as store

    if store.validate_name(payload.name) is None:
        return _error_response(
            400,
            "API_KEY_NAME_INVALID",
            "key name must be 1-120 characters after trimming",
            request.headers.get("x-request-id"),
        )
    if payload.environment not in store.ENVIRONMENTS:
        return _error_response(
            400,
            "API_KEY_ENVIRONMENT_INVALID",
            "environment must be one of: test, live",
            request.headers.get("x-request-id"),
        )
    try:
        created = store.create_api_key(tenant_id, payload.name, payload.environment)
    except store.ApiKeyStoreError:
        logger.warning("api-key creation store failure (fail closed)")
        return _store_error_response(request)
    except ValueError:
        return _error_response(
            400, "API_KEY_NAME_INVALID", "invalid key name", request.headers.get("x-request-id")
        )
    return created


# ---------------------------------------------------------------------------
# GET /v1/developer/api-keys — list (masked metadata only, tenant-scoped)
# ---------------------------------------------------------------------------


@router.get(
    "/api-keys",
    response_model=ApiKeyListResponse,
    responses={
        403: {"description": "Management authorization failed"},
        503: {"description": "Management store unavailable (fail closed)"},
    },
)
def list_api_keys(
    request: Request,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    tenant_id = _authorize(request)
    from backend.auth import api_keys as store

    try:
        keys = store.list_api_keys(tenant_id)
    except store.ApiKeyStoreError:
        return _store_error_response(request)
    return {"keys": keys}


# ---------------------------------------------------------------------------
# POST /v1/developer/api-keys/{key_id}/revoke — durable, idempotent
# ---------------------------------------------------------------------------


@router.post(
    "/api-keys/{key_id}/revoke",
    response_model=ApiKeyRevokedResponse,
    responses={
        403: {"description": "Management authorization failed"},
        404: {"description": "Key not found (or owned by another tenant)"},
        503: {"description": "Management store unavailable (fail closed)"},
    },
)
def revoke_api_key(
    request: Request,
    key_id: str,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    tenant_id = _authorize(request)
    from backend.auth import api_keys as store

    try:
        view, code = store.revoke_api_key(tenant_id, key_id)
    except store.ApiKeyStoreError:
        return _store_error_response(request)
    if view is None:
        if code == store.KEY_REVOKED:
            return _error_response(
                404,
                "API_KEY_REVOKED",
                "key is revoked and cannot be rotated",
                request.headers.get("x-request-id"),
            )
        return _error_response(
            404,
            "API_KEY_NOT_FOUND",
            "no such key for this tenant",
            request.headers.get("x-request-id"),
        )
    return view


# ---------------------------------------------------------------------------
# POST /v1/developer/api-keys/{key_id}/rotate — new raw key shown ONCE
# ---------------------------------------------------------------------------


@router.post(
    "/api-keys/{key_id}/rotate",
    response_model=ApiKeyCreatedResponse,
    status_code=201,
    responses={
        403: {"description": "Management authorization failed"},
        404: {"description": "Key not found (or owned by another tenant)"},
        503: {"description": "Management store unavailable (fail closed)"},
    },
)
def rotate_api_key(
    request: Request,
    key_id: str,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    tenant_id = _authorize(request)
    from backend.auth import api_keys as store

    try:
        view, code = store.rotate_api_key(tenant_id, key_id)
    except store.ApiKeyStoreError:
        return _store_error_response(request)
    if view is None:
        if code == store.KEY_REVOKED:
            return _error_response(
                404,
                "API_KEY_REVOKED",
                "key is revoked and cannot be rotated",
                request.headers.get("x-request-id"),
            )
        return _error_response(
            404,
            "API_KEY_NOT_FOUND",
            "no such key for this tenant",
            request.headers.get("x-request-id"),
        )
    return view
