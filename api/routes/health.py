"""Stage 2 — health & status endpoints."""
from __future__ import annotations

from fastapi import APIRouter

import api.services as svc
from api.schemas import HealthResponse, ProvidersResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """
    Liveness + component status.

    Deliberately non-blocking: database reachability is probed with a
    short SELECT 1 and provider keys are reported as presence booleans
    only. No external calls, no heavy imports, no startup coupling.
    """
    return HealthResponse(
        status="ok",
        service="financial-timeline-engine-api",
        version="0.2.0",
        stage=2,
        uptime_seconds=svc.uptime_seconds(),
        database=svc.database_status(),
        redis=svc.redis_status(),
        providers=svc.provider_key_status(),
    )


@router.get("/providers/status", response_model=ProvidersResponse)
def providers_status() -> ProvidersResponse:
    """Masked provider key configuration status (names only, never values)."""
    status = svc.provider_key_status()
    providers = [
        {"name": name, "key_configured": info["key_configured"], "env_var": info["env_var"]}
        for group in status.values()
        for name, info in group.items()
    ]
    return ProvidersResponse(
        status="ok",
        providers=providers,
        financial_providers=status.get("financial", {}),
    )
