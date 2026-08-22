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
            "Standalone web backend for the Platrixa — "
            "Agentic RAG intelligence, extraction 2.0, and market data "
            "served to the browser, backed by PostgreSQL."
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

    # API routes first, so /api/v1/* is never shadowed by the static mount.
    from api.routes import health, intelligence, market

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(intelligence.router, prefix="/api/v1")

    # Standalone frontend: served at / (landing + app UI)
    if _FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "5000"))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)
