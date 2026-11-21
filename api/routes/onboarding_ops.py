"""Operational endpoints for self-service developer onboarding.

These are NOT part of the public website surface and are NOT reachable with a
data-plane key. They are gated by a shared dispatch token
(``PLATRIXA_ONBOARDING_DISPATCH_TOKEN``) and exist so an external scheduler
(cron, systemd timer, Kubernetes CronJob) can drive the durable notification
dispatcher without granting management-assertion privileges.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from backend.onboarding import config, notifications

router = APIRouter(prefix="/v1/internal/onboarding", tags=["onboarding-ops"])


def _token_ok(provided: Optional[str]) -> bool:
    expected = config.dispatch_token()
    if not expected:
        return False
    import hmac

    return hmac.compare_digest(str(provided or ""), expected)


@router.post("/dispatch", summary="Flush due onboarding notifications")
def dispatch(
    limit: int = Query(default=50, ge=1, le=500),
    x_platrixa_dispatch_token: Optional[str] = Header(default=None),
):
    if not config.dispatch_token():
        raise HTTPException(status_code=404, detail="not found")
    if not _token_ok(x_platrixa_dispatch_token):
        raise HTTPException(status_code=403, detail="invalid dispatch token")
    return {"api_version": "v1", **notifications.dispatch_due(limit)}


@router.get("/health", summary="Onboarding dispatcher health")
def health(x_platrixa_dispatch_token: Optional[str] = Header(default=None)):
    if not config.dispatch_token():
        raise HTTPException(status_code=404, detail="not found")
    if not _token_ok(x_platrixa_dispatch_token):
        raise HTTPException(status_code=403, detail="invalid dispatch token")
    return {"api_version": "v1", "dispatch_configured": True}
