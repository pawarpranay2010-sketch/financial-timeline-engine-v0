"""Durable, PostgreSQL-backed per-tenant request-rate limiting.

WHY THIS MODULE EXISTS
----------------------
The only request-rate limiter in the application before this module was
``api/rate_limit.py``: a **per-client-IP, in-process, in-memory** bucket.
That is honest for one uvicorn process and a single peer address, but it
has two properties that are wrong for a multi-tenant private beta:

  * it cannot distinguish tenants — two developers behind one NAT share a
    bucket, and one abusive tenant is throttled only after every tenant
    behind the same address is;
  * it is per process. Railway currently runs ONE uvicorn process, so the
    limit is coherent today; the moment a second instance (or ``--workers``)
    exists, every limit silently multiplies by the process count.

This module adds one durable per-tenant control, in the same metering
PostgreSQL store as the queue and the quota. It is a **fixed-window
counter**, because a token bucket needs a read-modify-write that is easy to
get subtly wrong under concurrency, while a single UPSERT is atomic by
construction:

    INSERT ... ON CONFLICT (tenant_id) DO UPDATE
      SET used = CASE WHEN window_start = <this window> THEN used + 1 ELSE 1 END

The row lock taken by the ON CONFLICT update serialises concurrent requests
for the same tenant, so N simultaneous callers observe N distinct values of
``used`` — the counter is exact, not approximate.

WHAT THIS IS NOT
----------------
* It does not replace the monthly quota (``backend/auth/gate.py``), the
  pending-job bound (``backend/auth/queue_capacity.py``), or the per-IP
  limiter. It bounds request RATE; those bound usage, backlog and volume.
* It is not a security boundary on its own. A valid tenant key is still
  required before a tenant id exists to bucket against.

CONFIGURATION
-------------
    PLATRIXA_TENANT_RATE_LIMIT             requests per window (default 120)
    PLATRIXA_TENANT_RATE_WINDOW_SECONDS    window length      (default 60)

``PLATRIXA_TENANT_RATE_LIMIT=0`` disables the control explicitly. Any other
non-positive or non-integer value is rejected at startup by
:func:`validate_config`, so a typo cannot silently disable protection.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("platrixa.api")

TENANT_RATE_LIMIT_ENV_VAR = "PLATRIXA_TENANT_RATE_LIMIT"
TENANT_RATE_WINDOW_ENV_VAR = "PLATRIXA_TENANT_RATE_WINDOW_SECONDS"

DEFAULT_TENANT_RATE_LIMIT = 120
DEFAULT_TENANT_RATE_WINDOW_SECONDS = 60

#: Rejection code surfaced to the developer API (HTTP 429).
TENANT_RATE_LIMITED = "TENANT_RATE_LIMITED"

_DDL = """
CREATE TABLE IF NOT EXISTS platrixa_tenant_rate_limits (
    tenant_id    VARCHAR(64) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    used         INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id)
);
CREATE TABLE IF NOT EXISTS platrixa_tenant_rate_limit_events (
    tenant_id    VARCHAR(64) NOT NULL,
    rejected     BIGINT      NOT NULL DEFAULT 0,
    last_rejected_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id)
);
"""


class TenantRateLimitConfigurationError(RuntimeError):
    """Raised at startup when the rate-limit configuration is unusable."""


class TenantRateLimited(Exception):
    """One tenant exceeded its configured request rate for this window.

    ``retry_after_seconds`` is derived from the DATABASE's window boundary,
    never from a local clock, so it cannot disagree with the enforcement
    decision.
    """

    def __init__(self, *, limit: int, used: int, retry_after_seconds: int) -> None:
        super().__init__(TENANT_RATE_LIMITED)
        self.code = TENANT_RATE_LIMITED
        self.limit = limit
        self.used = used
        self.retry_after_seconds = max(1, int(retry_after_seconds))


def _parse_non_negative_int(name: str, raw: Optional[str], default: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    try:
        value = int(text, 10)
    except (TypeError, ValueError):
        raise TenantRateLimitConfigurationError(
            f"{name} must be a non-negative integer, got {text!r}"
        ) from None
    if value < 0:
        raise TenantRateLimitConfigurationError(
            f"{name} must be a non-negative integer, got {value}"
        )
    return value


_cache_lock = threading.Lock()
_cache: Dict[Tuple[str, str], Tuple[int, int]] = {}


def limits() -> Tuple[int, int]:
    """Return ``(request_limit, window_seconds)``; ``(0, w)`` means disabled."""
    raw_limit = os.getenv(TENANT_RATE_LIMIT_ENV_VAR)
    raw_window = os.getenv(TENANT_RATE_WINDOW_ENV_VAR)
    key = (raw_limit or "", raw_window or "")
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    limit = _parse_non_negative_int(
        TENANT_RATE_LIMIT_ENV_VAR, raw_limit, DEFAULT_TENANT_RATE_LIMIT
    )
    window = _parse_non_negative_int(
        TENANT_RATE_WINDOW_ENV_VAR, raw_window, DEFAULT_TENANT_RATE_WINDOW_SECONDS
    )
    if window <= 0:
        raise TenantRateLimitConfigurationError(
            f"{TENANT_RATE_WINDOW_ENV_VAR} must be a positive integer, got {window}"
        )
    with _cache_lock:
        _cache[key] = (limit, window)
    return limit, window


def validate_config() -> Tuple[int, int]:
    """Fail-fast startup validation used by ``api.main.create_app``."""
    return limits()


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def enabled() -> bool:
    limit, _window = limits()
    return limit > 0


# ---------------------------------------------------------------------------
# Durable primitive
# ---------------------------------------------------------------------------

_schema_ensured: set = set()
_schema_lock = threading.Lock()


def _session_factory():
    """The canonical metering sessionmaker (raises when unconfigured)."""
    from backend.auth import async_jobs

    return async_jobs._session_factory()


def _ensure_schema(url: str) -> None:
    from sqlalchemy import create_engine, text

    ddl_url = (
        url.replace("postgresql://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql://")
        else url
    )
    engine = create_engine(ddl_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text(_DDL))
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
    with _schema_lock:
        _schema_ensured.add(url)


def consume(tenant_id: str) -> Dict[str, Any]:
    """Consume ONE request unit for ``tenant_id``; raise when over the limit.

    Returns ``{"limit", "window_seconds", "used", "remaining"}`` when the
    request is allowed. Raises :class:`TenantRateLimited` otherwise, and any
    store failure propagates as ``AsyncStoreUnavailableError`` so the caller
    fails closed rather than admitting unbounded traffic.
    """
    from sqlalchemy import text

    limit, window = limits()
    if limit <= 0:
        return {
            "limit": 0,
            "window_seconds": window,
            "used": 0,
            "remaining": 0,
            "disabled": True,
        }

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    # Fixed windows anchored to the epoch, computed by the DATABASE clock so
    # every API process agrees on the boundary.
    sql = text(
        "INSERT INTO platrixa_tenant_rate_limits (tenant_id, window_start, used) "
        "VALUES (:t, to_timestamp(floor(extract(epoch from now()) / :w) * :w), 1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "  used = CASE WHEN platrixa_tenant_rate_limits.window_start = EXCLUDED.window_start "
        "              THEN platrixa_tenant_rate_limits.used + 1 ELSE 1 END, "
        "  window_start = EXCLUDED.window_start "
        "RETURNING used, window_start, "
        "  to_timestamp(floor(extract(epoch from now()) / :w) * :w) AS window_floor"
    )
    try:
        with SessionLocal() as session:
            with session.begin():
                row = session.execute(
                    sql, {"t": tenant_id, "w": int(window)}
                ).mappings().first()
                if row is None:  # pragma: no cover - defensive
                    raise AsyncStoreUnavailableError("rate-limit store unavailable")
                used = int(row["used"])
                retry_after = window
                if used > limit:
                    # Durable rejection counter for owner visibility: "how often
                    # was this tenant throttled" is answered from the store, not
                    # from a per-process counter that a restart would erase.
                    session.execute(
                        text(
                            "INSERT INTO platrixa_tenant_rate_limit_events "
                            "  (tenant_id, rejected, last_rejected_at) "
                            "VALUES (:t, 1, now()) "
                            "ON CONFLICT (tenant_id) DO UPDATE SET "
                            "  rejected = platrixa_tenant_rate_limit_events.rejected + 1, "
                            "  last_rejected_at = now()"
                        ),
                        {"t": tenant_id},
                    )
                    retry = session.execute(
                        text(
                            "SELECT GREATEST(1, ceil(extract(epoch from "
                            "(to_timestamp(floor(extract(epoch from now()) / :w) * :w) "
                            "+ make_interval(secs => :w)) - now())))::int AS r"
                        ),
                        {"w": int(window)},
                    ).scalar_one()
                    retry_after = int(retry or window)
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("tenant rate-limit store failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    if used > limit:
        raise TenantRateLimited(
            limit=limit, used=used, retry_after_seconds=retry_after
        )
    return {
        "limit": limit,
        "window_seconds": window,
        "used": used,
        "remaining": max(0, limit - used),
    }


def event_counts() -> dict:
    """Cumulative throttling events per tenant (owner report, read-only)."""
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT tenant_id, rejected, last_rejected_at "
                    "FROM platrixa_tenant_rate_limit_events ORDER BY tenant_id"
                )
            ).mappings().all()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("rate-limit event read failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    return {
        str(row["tenant_id"]): {
            "rejected": int(row["rejected"]),
            "last_rejected_at": row["last_rejected_at"].isoformat()
            if row["last_rejected_at"]
            else None,
        }
        for row in rows
    }


def ensure_schema_for_current_store() -> None:
    """Create the counter table on the configured store (idempotent)."""
    from backend.auth.gate import METERING_ENV_VAR

    url = (os.getenv(METERING_ENV_VAR, "") or "").strip()
    if not url or url in _schema_ensured:
        return
    _ensure_schema(url)


__all__ = [
    "TENANT_RATE_LIMIT_ENV_VAR",
    "TENANT_RATE_WINDOW_ENV_VAR",
    "DEFAULT_TENANT_RATE_LIMIT",
    "DEFAULT_TENANT_RATE_WINDOW_SECONDS",
    "TENANT_RATE_LIMITED",
    "TenantRateLimitConfigurationError",
    "TenantRateLimited",
    "limits",
    "validate_config",
    "reset_cache",
    "enabled",
    "consume",
    "event_counts",
    "ensure_schema_for_current_store",
]
