"""Stage 2 — health & status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

import api.services as svc
from api.routes._admission import require_api_v1_credential
from api.schemas import HealthResponse, ProvidersResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """
    Liveness + component status.

    Deliberately non-blocking: database reachability is probed with a
    short SELECT 1. No external calls, no heavy imports, no startup
    coupling.

    This route is PUBLIC by design — ``render.yaml`` declares it as the
    service ``healthCheckPath``, and the production UI polls it (it only
    inspects the HTTP status). Therefore it must not disclose sensitive
    internals.

    Security hardening (audit H-01/M-04, 2026-09-29): the provider key
    INVENTORY (environment-variable names plus a per-key ``configured``
    flag) was previously served here anonymously, which is exactly the
    reconnaissance material that ``/api/v1/providers/status`` now protects
    behind a credential. The inventory is therefore REMOVED from the
    public response; the field is kept (schema-compatible, returns an
    empty object) and the same data remains available to an authenticated
    caller via ``GET /api/v1/providers/status``.
    """
    return HealthResponse(
        status="ok",
        service="financial-timeline-engine-api",
        version="0.2.0",
        stage=2,
        uptime_seconds=svc.uptime_seconds(),
        database=svc.database_status(),
        redis=svc.redis_status(),
        providers={},
    )


@router.get(
    "/providers/status",
    response_model=ProvidersResponse,
    dependencies=[Depends(require_api_v1_credential)],
)
def providers_status() -> ProvidersResponse:
    """Masked provider key configuration status (names only, never values).

    Security hardening (audit H-01): this route enumerates the deployment's
    provider environment-variable names and whether each key is configured.
    That is reconnaissance material for a credential attack, so it now
    requires a credential.

    ``GET /api/v1/health`` deliberately stays public — it is the Render
    ``healthCheckPath`` (render.yaml) and gating it would mark the service
    unhealthy.
    """
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
