"""Stage 2 — FastAPI application factory.

Startup contract (per Stage 2 requirements):
  - Binds port 5000 quickly — NO heavy imports at module scope.
  - No expensive document processing at startup.
  - No live AI-provider connectivity checks at startup.
  - No blocking database initialization at startup.
All Phase 6 components are imported lazily inside api/services on demand.

Architecture:
  Browser → frontend/ (static, served at /) → api/ (FastAPI, /api/v1/*)
  → Phase 6 intelligence/extraction pipeline → PostgreSQL → AI/financial providers
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api import __version__
from api.status import (
    LABEL_BY_PUBLIC_STATUS,
    RETRYABLE_BY_PUBLIC_STATUS,
    public_status_for_error_code,
)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately empty: nothing blocking happens at startup.
    # The Phase 6 pipeline, database, Redis, and providers initialize
    # lazily on first request.
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Platrixa API",
        version=__version__,
        description=(
            "Deterministic financial semantic validation infrastructure for "
            "AI-powered accounting and finance software. Schema-validated "
            "semantic interpretation is routed through deterministic "
            "authorities; served to the browser, backed by PostgreSQL."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Stage 2: standalone frontend on any origin
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        return JSONResponse(
            status_code=500,
            content={"detail": f"Unhandled error: {type(exc).__name__}"},
        )

    # Phase 13: app-level body-size cap for the versioned developer API.
    # Enforced BEFORE request-body parsing so oversized payloads are
    # rejected with 413 instead of being read and schema-validated.
    _MAX_DEV_BODY_BYTES = 64 * 1024

    @app.middleware("http")
    async def _developer_body_size_guard(request, call_next):
        if request.url.path.startswith("/v1/"):
            content_length = request.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > _MAX_DEV_BODY_BYTES:
                # Phase 5A: the 413 envelope carries the six-state public
                # API status trio, mapped from the error code (same mapping
                # as every other /v1 error envelope).
                api_status = public_status_for_error_code("REQUEST_TOO_LARGE")
                return JSONResponse(
                    status_code=413,
                    content={
                        "api_version": "v1",
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "request body too large",
                            "api_status": api_status,
                            "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
                            "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
                        },
                    },
                )
        return await call_next(request)

    # API routes first, so /api/v1/* is never shadowed by the static mount.
    from api.routes import developer, health, intelligence, kernel, market

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(intelligence.router, prefix="/api/v1")
    # Phase 7F: authoritative Kernel boundary (lazy heavy imports inside).
    app.include_router(kernel.router, prefix="/api/v1")
    # Phase 13: versioned developer API — transport boundary over the
    # public developer interface (platrixa), mounted at top-level /v1 so
    # the public contract is versioned independently of /api/v1.
    app.include_router(developer.router)
    # Phase 15: deterministic malformed-request normalization for /v1 only
    # (400 instead of the framework-default 422 for parsing/validation
    # failures). Browser-facing /api/v1 keeps its documented behavior.
    developer.register_developer_error_handlers(app)

    # Standalone frontend: served at / (landing + app UI)
    if _FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "5000"))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)
