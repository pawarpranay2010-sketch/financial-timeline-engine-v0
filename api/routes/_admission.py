"""
Platrixa — Admission control for the /api/v1 surface (security hardening)
=========================================================================

WHY THIS EXISTS
---------------
The 2026-09-29 security audit (H-01) found that the entire ``/api/v1/*``
surface was reachable without any credential. Several of those routes are
state-changing (``/db/init``), fan out to paid third-party providers
(``/market/{ticker}``), run an agentic retrieval loop
(``/intelligence/analyze``) or disclose deployment fingerprinting
(``/providers/status``).

This module adds a FastAPI dependency that REUSES the existing Phase 15 /
Phase 16 authentication architecture. It invents no new credential system.

WHAT IS DELIBERATELY LEFT PUBLIC
--------------------------------
Two ``/api/v1`` routes stay reachable without a credential, because they
are part of the shipped deployment contract and cannot be changed without
breaking production:

  * ``GET  /api/v1/health``  — ``render.yaml`` sets this as the service
    ``healthCheckPath``. Requiring a credential would make Render mark the
    service unhealthy.
  * ``POST /api/v1/kernel/process`` — the legacy production UI
    (``frontend/app.js``) posts here with no credential. This is the
    documented public entry point and is protected by request-rate limiting
    instead (see ``api/rate_limit.py``).

ADMISSION ORDERING
------------------
The dependency runs BEFORE the route body, so a rejected request never
reaches the provider call, the agentic loop, or the database.

FAIL-CLOSED
-----------
If no authentication mechanism is configured at all (neither
``PLATRIXA_DEV_API_KEY`` nor a metering store), the protected routes are
REFUSED rather than served. An unconfigured deployment must not silently
expose state-changing and provider-backed endpoints.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request

from backend.auth import gate as metered_gate

logger = logging.getLogger("platrixa.api")

# Phase 15 single-key variable (same one developer.py honours).
DEV_API_KEY_ENV_VAR = "PLATRIXA_DEV_API_KEY"

_401_BODY = {
    "api_version": "v1",
    "error": {
        "code": "UNAUTHORIZED",
        "message": "valid X-Platrixa-API-Key required",
    },
}
_503_BODY = {
    "api_version": "v1",
    "error": {
        "code": "METERING_UNAVAILABLE",
        "message": "this endpoint requires authentication and no "
                   "authentication mechanism is configured",
    },
}


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=_401_BODY["error"]["message"],
        headers={
            "X-Platrixa-Error": "UNAUTHORIZED",
            "WWW-Authenticate": "ApiKey",
        },
    )


def _no_mechanism() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=_503_BODY["error"]["message"],
        headers={"X-Platrixa-Error": "METERING_UNAVAILABLE"},
    )


def require_api_v1_credential(request: Request) -> None:
    """
    FastAPI dependency protecting sensitive ``/api/v1`` routes.

    Resolution order, reusing the existing architecture:

      1. ``PLATRIXA_DEV_API_KEY`` set  -> require that key (Phase 15).
      2. Metering store configured     -> require a valid tenant API key
                                          (Phase 16 ``resolve_tenant``,
                                          constant-time, fail-closed).
      3. Neither configured            -> 503, never served.

    NOTE: unlike ``developer._metered_api_key_guard``, step 3 is REFUSED
    rather than allowed. Those guards protect the metered ``/v1`` developer
    contract, whose documented behaviour is "open when metering is not
    configured". The routes guarded here have no such contract — they are
    not part of the public developer API — so failing open would silently
    re-expose them on an unconfigured host.

    This dependency only AUTHENTICATES. It does not reserve quota and does
    not replace the tenant quota in ``backend/auth/gate.py``.
    """
    dev_key = (os.getenv(DEV_API_KEY_ENV_VAR, "") or "").strip()

    if dev_key:
        provided = request.headers.get("x-platrixa-api-key", "") or ""
        import hmac

        if not hmac.compare_digest(
            provided.strip().encode("utf-8"), dev_key.encode("utf-8")
        ):
            raise _unauthorized()
        return

    if metered_gate._metering_configured():
        provided: Optional[str] = request.headers.get("x-platrixa-api-key", "")
        try:
            reason, _ctx = metered_gate.resolve_tenant(provided)
        except metered_gate.MeteredGateError:
            logger.warning(
                "api/v1 admission: metering store unavailable (%s)",
                "MeteredGateError",
            )
            raise _no_mechanism()
        if reason != metered_gate.REASON_OK:
            raise _unauthorized()
        return

    # Fail closed: nothing is configured, so nothing is served.
    logger.warning(
        "api/v1 admission refused: neither %s nor %s is configured",
        DEV_API_KEY_ENV_VAR,
        metered_gate.METERING_ENV_VAR,
    )
    raise _no_mechanism()


__all__ = ["require_api_v1_credential", "DEV_API_KEY_ENV_VAR"]
