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

import hashlib
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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


# ---------------------------------------------------------------------------
# Management authorization — one shared dependency for every management route
# ---------------------------------------------------------------------------

# Every header that makes up a COMPLETE gateway assertion. A partial set is an
# error, never a downgrade: authentication must not silently fall back to the
# static token when a caller forgets (or an attacker strips) one header.
_ASSERTION_HEADERS = (
    "x-platrixa-assertion-signature",
    "x-platrixa-assertion-issuer",
    "x-platrixa-assertion-audience",
    "x-platrixa-assertion-tenant-id",
    "x-platrixa-assertion-method",
    "x-platrixa-assertion-path",
    "x-platrixa-assertion-query-string",
    "x-platrixa-assertion-content-type",
    "x-platrixa-assertion-body-hash",
    "x-platrixa-assertion-timestamp",
    "x-platrixa-assertion-nonce",
)


def _management_denied() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="API_KEY_MANAGEMENT_UNAUTHORIZED",
        headers={"X-Platrixa-Error": "API_KEY_MANAGEMENT_UNAUTHORIZED"},
    )


def canonical_management_path(raw_path: str) -> Optional[str]:
    """The one canonical path form shared by edge signing and ASGI checking.

    Rules (identical to the operator gateway's ``canonicalizePath``):
      * must start with ``/``
      * no ``..`` path component
      * no control characters
      * trailing slashes preserved
      * percent-encoding preserved EXACTLY as received — ``%2F`` is never
        decoded into a separator, so a signature over one representation can
        never be replayed as another.
    """
    if not raw_path or not raw_path.startswith("/"):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_path):
        return None
    if ".." in raw_path.split("/"):
        return None
    return raw_path


async def require_management(request: Request) -> str:
    """Authorize one MANAGEMENT request. Returns the bound tenant_id.

    Both credentials are mandatory on every management endpoint:

      1. the static management token (``X-Platrixa-Management-Token``), and
      2. a COMPLETE signed gateway assertion.

    The assertion is bound to the ACTUAL request: its declared method, path,
    query string, content-type and body hash must equal what this server
    received, and the signature is verified over those received values —
    never over the caller's claim about them.

    Fails closed on every ambiguous state:
      NOT_CONFIGURED -> 400, MISCONFIGURED -> 503, INVALID -> 403,
      nonce store unavailable -> 503.
    """
    from backend.auth import api_keys as store

    token = request.headers.get("x-platrixa-management-token", "")
    reason, tenant_id = store.authorize_management(token)
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
    if reason != store.MGMT_OK or not tenant_id:
        raise _management_denied()

    # Complete-or-nothing: any missing assertion header is a hard rejection.
    for header in _ASSERTION_HEADERS:
        if request.headers.get(header) is None:
            raise _management_denied()

    # --- Bind to the ACTUAL request -------------------------------------
    # raw_path/query_string are the undecoded ASGI values, so percent-encoding
    # survives intact and matches exactly what the edge signed.
    raw_path = request.scope.get("raw_path")
    path = canonical_management_path(
        raw_path.decode("latin-1") if raw_path else request.url.path
    )
    query_string = request.scope.get("query_string", b"").decode("latin-1")
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    body_hash = hashlib.sha256(body).hexdigest()

    if path is None:
        raise _management_denied()

    actual_method = request.method.upper()
    actual = {
        "method": actual_method,
        "path": path,
        "query_string": query_string,
        "content_type": content_type,
        "body_hash": body_hash,
    }
    declared = {
        "method": request.headers.get("x-platrixa-assertion-method", ""),
        "path": request.headers.get("x-platrixa-assertion-path", ""),
        "query_string": request.headers.get("x-platrixa-assertion-query-string", ""),
        "content_type": request.headers.get("x-platrixa-assertion-content-type", ""),
        "body_hash": request.headers.get("x-platrixa-assertion-body-hash", ""),
    }
    if declared != actual:
        raise _management_denied()

    # Tenant binding is checked before the HMAC so a wrong-tenant assertion
    # never consumes a nonce.
    declared_tenant = request.headers.get("x-platrixa-assertion-tenant-id", "")
    if declared_tenant != tenant_id:
        logger.warning(
            "Management tenant mismatch: authorize_management=%s, assertion=%s",
            tenant_id,
            declared_tenant,
        )
        raise _management_denied()

    result, assertion_tenant = store.validate_management_gateway_assertion(
        secret=os.getenv("PLATRIXA_MANAGEMENT_ASSERTION_SECRET"),
        signature=request.headers.get("x-platrixa-assertion-signature"),
        issuer=request.headers.get("x-platrixa-assertion-issuer"),
        audience=request.headers.get("x-platrixa-assertion-audience"),
        tenant_id=declared_tenant,
        method=actual_method,
        path=path,
        query_string=query_string,
        content_type=content_type,
        body_hash=body_hash,
        timestamp=request.headers.get("x-platrixa-assertion-timestamp"),
        nonce=request.headers.get("x-platrixa-assertion-nonce"),
    )
    if result == store.MGMT_NONCE_UNAVAILABLE:
        # Fail closed: without durable replay protection nothing is accepted.
        raise HTTPException(
            status_code=503,
            detail="API_KEY_MANAGEMENT_UNAVAILABLE",
            headers={"X-Platrixa-Error": "API_KEY_MANAGEMENT_UNAVAILABLE"},
        )
    if result != store.MGMT_OK:
        raise _management_denied()
    if assertion_tenant != tenant_id:
        raise _management_denied()
    return tenant_id


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
    tenant_id: str = Depends(require_management),
):
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
    tenant_id: str = Depends(require_management),
):
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
    tenant_id: str = Depends(require_management),
):
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
    tenant_id: str = Depends(require_management),
):
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
