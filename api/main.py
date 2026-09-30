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
        # SECURITY (audit M-04, 2026-09-29): the previous body named the
        # concrete exception class, which aids targeted reconnaissance. The
        # full traceback is logged server-side; the client gets a fixed,
        # non-identifying message. The route is visible to the caller via
        # the path, so the class name adds nothing but signal.
        import logging

        logging.getLogger("platrixa.api").exception(
            "unhandled error on %s: %s", request.url.path, type(exc).__name__
        )
        # Phase 5I (audit M1): unhandled failures now speak the same
        # deterministic envelope as every other /v1 error. FAILED is the
        # fail-closed public state; retryable is false (an unchanged
        # retry of a deterministic pipeline is not expected to differ);
        # the server-side correlation id is echoed so the operator can
        # match the logged traceback without exposing any internals.
        from api.status import (
            LABEL_BY_PUBLIC_STATUS,
            RETRYABLE_BY_PUBLIC_STATUS,
            STATUS_FAILED,
        )

        rid = (request.headers.get("x-request-id") or "").strip()
        import re as _re

        if not _re.fullmatch(r"[A-Za-z0-9._-]{1,128}", rid or ""):
            rid = None
        return JSONResponse(
            status_code=500,
            content={
                "api_version": "v1",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "internal error",
                    "request_id": rid,
                    "api_status": STATUS_FAILED,
                    "api_status_label": LABEL_BY_PUBLIC_STATUS.get(STATUS_FAILED, ""),
                    "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(STATUS_FAILED, False),
                },
            },
        )

    # Phase 13: app-level body-size cap for the versioned developer API.
    # Enforced BEFORE request-body parsing so oversized payloads are
    # rejected with 413 instead of being read and schema-validated.
    # Phase 5E: /v1/documents carries base64 document payloads for the
    # durable async store, so it gets its own (larger, still bounded) cap.
    _MAX_DEV_BODY_BYTES = 64 * 1024
    _MAX_ASYNC_DOCUMENT_BODY_BYTES = 16 * 1024 * 1024 + 64 * 1024

    def _rate_key(request) -> str:
        """Transport peer address; never a client-supplied identifier."""
        from api.rate_limit import _client_key

        return _client_key(request)

    from api.rate_limit import EXEMPT_EXACT as _RATE_LIMIT_EXEMPT

    @app.middleware("http")
    async def _request_rate_limit(request, call_next):
        """
        Security hardening (audit H-02, 2026-09-29): bound the number of
        requests an anonymous client can issue.

        Runs BEFORE routing and before any route body, so a throttled
        request never reaches a provider call, the agentic loop, the model,
        or the database. It does NOT replace the tenant quota in
        backend/auth/gate.py, which remains the authoritative per-tenant
        control on the metered /v1 surface.
        """
        path = request.url.path
        if path in _RATE_LIMIT_EXEMPT or not (
            path.startswith("/v1/") or path.startswith("/api/v1/")
        ):
            return await call_next(request)

        from api.rate_limit import check as _rl_check

        allowed, remaining, retry_after, _window = _rl_check(path, _rate_key(request))
        if not allowed:
            # Phase 5I (audit M1): the throttle response now uses the same
            # deterministic error envelope as every /v1 and /api/v1 error
            # (api_status trio + request_id). Retryability is honest: the
            # limiter is a transient condition.
            from api.status import (
                LABEL_BY_PUBLIC_STATUS,
                RETRYABLE_BY_PUBLIC_STATUS,
                public_status_for_error_code,
            )

            rid = (request.headers.get("x-request-id") or "").strip()
            import re as _re

            if not _re.fullmatch(r"[A-Za-z0-9._-]{1,128}", rid or ""):
                rid = None
            api_status = public_status_for_error_code("RATE_LIMITED")
            error = {
                "code": "RATE_LIMITED",
                "message": "too many requests; retry later",
                "api_status": api_status,
                "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
                "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
            }
            if rid:
                error["request_id"] = rid
            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": str(max(1, retry_after)),
                    "X-Platrixa-Error": "RATE_LIMITED",
                    "X-RateLimit-Remaining": "0",
                },
                content={"api_version": "v1", "error": error},
            )
        response = await call_next(request)
        if remaining >= 0:
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    @app.middleware("http")
    async def _developer_body_size_guard(request, call_next):
        if request.url.path.startswith("/v1/"):
            max_body = (
                _MAX_ASYNC_DOCUMENT_BODY_BYTES
                if request.url.path == "/v1/documents"
                else _MAX_DEV_BODY_BYTES
            )
            content_length = request.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > max_body:
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
    # Phase 5E: async document jobs, results, and webhook endpoints —
    # same admission boundary, same 5D result contract, durable job store
    # over the metering PostgreSQL (activated only when configured).
    from api.routes import async_api

    app.include_router(async_api.router)
    # Phase 5G: developer API-key lifecycle (management plane — separate
    # credential from the data-plane key; same metering PostgreSQL,
    # same error envelope, raw keys shown exactly once).
    from api.routes import developer_keys

    app.include_router(developer_keys.router)
    # Phase 5H: developer usage + request observability (read-only).
    # Zero data-plane quota for reads; two-plane authorization identical
    # to Phase 5G; retention semantics documented, never faked.
    from api.routes import observability

    app.include_router(observability.router)
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
