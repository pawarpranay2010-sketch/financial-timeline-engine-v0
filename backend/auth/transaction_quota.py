"""Durable, PostgreSQL-backed per-tenant ACCOUNTING-TRANSACTION quota.

WHY A SECOND QUOTA EXISTS
-------------------------
One API job can carry a bounded batch of accounting transactions
(``backend/auth/batch_jobs.py``). The existing monthly quota in
``gate.py`` counts ADMITTED JOBS — one unit per request. If that were the
only meter, a developer could submit one job containing the maximum batch
and consume a single unit for N transactions' worth of device inference
and deterministic kernel work.

So there are two independent, deterministic units:

  1. **jobs admitted**      — ``platrixa_tenant_quotas`` (gate.py, unchanged)
  2. **transactions admitted** — ``platrixa_tenant_transaction_quotas`` (here)

CHARGING POLICY (deterministic, documented, no refunds)
-------------------------------------------------------
A transaction is charged EXACTLY ONCE, at admission, when the batch request
is validated and the job row is created:

  * an admitted transaction is charged even if it later fails, is
    review-required, is unsupported, is blocked by an earlier ordered
    transaction, or is deferred because the device budget ran out — the
    unit bought admission to a bounded batch, and device inference/kernel
    time was scheduled for it;
  * a request rejected BEFORE admission (malformed body, duplicate ids,
    over the batch limit, bad credentials, job/transaction quota already
    exhausted, queue full, store unavailable) charges nothing;
  * a replay of the same ``Idempotency-Key`` with the same body charges
    nothing (the replay path never reaches either reservation);
  * a worker retry of a job that was already admitted charges nothing (the
    charge is attached to the job, not to the attempt).

The only decrement in this module is :func:`release`, used solely when the
job row could not be created after the charge was taken (queue capacity or
store failure), so the caller is never billed for a job that does not
exist.

CONFIGURATION
-------------
    PLATRIXA_TENANT_TX_MONTHLY_LIMIT    transactions per tenant per month
                                        (default 1000)

A per-tenant override is stored on the tenant's own row
(:func:`set_limit`), which is what owner provisioning writes. A missing or
malformed environment value falls back to the default; an explicitly
non-integer value is a configuration error at startup
(:func:`validate_config`).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("platrixa.api")

TX_MONTHLY_LIMIT_ENV_VAR = "PLATRIXA_TENANT_TX_MONTHLY_LIMIT"
DEFAULT_MONTHLY_TX_LIMIT = 1000

#: Rejection code surfaced to the developer API (HTTP 429).
TRANSACTION_QUOTA_EXCEEDED = "TRANSACTION_QUOTA_EXCEEDED"

_DDL = """
CREATE TABLE IF NOT EXISTS platrixa_tenant_transaction_quotas (
    tenant_id     VARCHAR(64) NOT NULL,
    monthly_limit INTEGER     NOT NULL DEFAULT 1000,
    used          INTEGER     NOT NULL DEFAULT 0,
    usage_month   VARCHAR(7)  NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id)
);
"""


class TransactionQuotaConfigurationError(RuntimeError):
    """Raised at startup when the transaction-quota configuration is unusable."""


class TransactionQuotaExceeded(Exception):
    """One tenant has insufficient transaction units for this request."""

    def __init__(self, *, limit: int, used: int, requested: int) -> None:
        super().__init__(TRANSACTION_QUOTA_EXCEEDED)
        self.code = TRANSACTION_QUOTA_EXCEEDED
        self.limit = int(limit)
        self.used = int(used)
        self.requested = int(requested)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def _parse_limit(raw: Optional[str]) -> int:
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MONTHLY_TX_LIMIT
    text = str(raw).strip()
    try:
        value = int(text, 10)
    except (TypeError, ValueError):
        raise TransactionQuotaConfigurationError(
            f"{TX_MONTHLY_LIMIT_ENV_VAR} must be a non-negative integer, got {text!r}"
        ) from None
    if value < 0:
        raise TransactionQuotaConfigurationError(
            f"{TX_MONTHLY_LIMIT_ENV_VAR} must be a non-negative integer, got {value}"
        )
    return value


_limit_lock = threading.Lock()
_limit_cache: Dict[str, int] = {}


def default_limit() -> int:
    """The configured default monthly transaction allowance."""
    raw = os.getenv(TX_MONTHLY_LIMIT_ENV_VAR)
    key = raw or ""
    with _limit_lock:
        cached = _limit_cache.get(key)
    if cached is not None:
        return cached
    value = _parse_limit(raw)
    with _limit_lock:
        _limit_cache[key] = value
    return value


def validate_config() -> int:
    """Fail-fast startup validation used by ``api.main.create_app``."""
    return default_limit()


def reset_cache() -> None:
    with _limit_lock:
        _limit_cache.clear()


# ---------------------------------------------------------------------------
# Durable primitives
# ---------------------------------------------------------------------------

_schema_ensured: set = set()
_schema_lock = threading.Lock()


def _session_factory():
    """The canonical metering sessionmaker (raises when unconfigured)."""
    from backend.auth import async_jobs

    return async_jobs._session_factory()


def ensure_schema_for_current_store() -> None:
    """Create the counter table on the configured store (idempotent)."""
    from sqlalchemy import create_engine, text

    from backend.auth.gate import METERING_ENV_VAR

    url = (os.getenv(METERING_ENV_VAR, "") or "").strip()
    if not url or url in _schema_ensured:
        return
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


def reserve(tenant_id: str, units: int) -> Dict[str, Any]:
    """Reserve ``units`` transaction units atomically for ``tenant_id``.

    Returns ``{"limit", "used", "remaining", "usage_month"}`` when the
    reservation succeeded. Raises :class:`TransactionQuotaExceeded` when the
    tenant's remaining allowance is too small (nothing is charged), and any
    store failure propagates as ``AsyncStoreUnavailableError`` so the caller
    fails closed instead of admitting unaccounted work.
    """
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    count = int(units)
    if count <= 0:
        raise ValueError("transaction quota units must be a positive integer")

    limit = default_limit()

    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    # One statement decides AND applies the reservation: the INSERT ... SELECT
    # admits a new tenant row only when the whole batch fits, and the conflict
    # arm either rolls the month over or adds the units only while the tenant
    # stays inside its own stored limit. A refused reservation writes nothing.
    sql = text(
        "INSERT INTO platrixa_tenant_transaction_quotas "
        "  (tenant_id, monthly_limit, used, usage_month, updated_at) "
        "SELECT :t, :limit, :n, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM'), now() "
        "WHERE :n <= :limit "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "  used = CASE "
        "    WHEN platrixa_tenant_transaction_quotas.usage_month = EXCLUDED.usage_month "
        "    THEN platrixa_tenant_transaction_quotas.used + :n "
        "    ELSE :n END, "
        "  usage_month = EXCLUDED.usage_month, "
        "  updated_at = now() "
        "WHERE (platrixa_tenant_transaction_quotas.usage_month <> EXCLUDED.usage_month "
        "       OR platrixa_tenant_transaction_quotas.used + :n "
        "          <= platrixa_tenant_transaction_quotas.monthly_limit) "
        "RETURNING used, monthly_limit, usage_month"
    )
    try:
        with SessionLocal() as session:
            with session.begin():
                row = session.execute(
                    sql, {"t": tenant_id, "limit": int(limit), "n": count}
                ).mappings().first()
                if row is not None:
                    used = int(row["used"])
                    tenant_limit = int(row["monthly_limit"])
                    return {
                        "limit": tenant_limit,
                        "used": used,
                        "remaining": max(0, tenant_limit - used),
                        "usage_month": str(row["usage_month"]),
                        "requested": count,
                    }
                current = session.execute(
                    text(
                        "SELECT used, monthly_limit FROM platrixa_tenant_transaction_quotas "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).mappings().first()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("transaction quota store failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    used = int(current["used"]) if current is not None else 0
    tenant_limit = int(current["monthly_limit"]) if current is not None else int(limit)
    raise TransactionQuotaExceeded(limit=tenant_limit, used=used, requested=count)


def release(tenant_id: str, units: int) -> int:
    """Give back ``units`` reserved units for the CURRENT month bucket.

    Used only when a job could not be created after its charge was taken. It
    never crosses a month boundary (a stale bucket is left untouched) and
    never goes below zero.
    """
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    count = int(units)
    if count <= 0:
        return 0
    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_tenant_transaction_quotas "
                        "SET used = GREATEST(0, used - :n), updated_at = now() "
                        "WHERE tenant_id = :t "
                        "  AND usage_month = to_char(now() AT TIME ZONE 'utc', 'YYYY-MM')"
                    ),
                    {"t": tenant_id, "n": count},
                )
                return int(result.rowcount or 0)
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("transaction quota release failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def set_limit(tenant_id: str, limit: Optional[int]) -> int:
    """Set the tenant's stored monthly transaction allowance.

    ``None`` keeps the configured default. An existing row keeps its current
    usage; only the ceiling changes. Returns the effective limit.
    """
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    effective = default_limit() if limit is None else int(limit)
    if effective < 0:
        raise ValueError("transaction limit must be non-negative")
    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "INSERT INTO platrixa_tenant_transaction_quotas "
                        "  (tenant_id, monthly_limit, used, usage_month, updated_at) "
                        "VALUES (:t, :limit, 0, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM'), now()) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET "
                        "  monthly_limit = EXCLUDED.monthly_limit, updated_at = now()"
                    ),
                    {"t": tenant_id, "limit": effective},
                )
        return effective
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("transaction quota limit write failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def usage(tenant_id: str) -> Dict[str, Any]:
    """Current-bucket usage for ONE tenant (read-only, never writes)."""
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT tenant_id, monthly_limit, used, usage_month "
                    "FROM platrixa_tenant_transaction_quotas WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            ).mappings().first()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("transaction quota read failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    if row is None:
        return {
            "tenant_id": tenant_id,
            "monthly_limit": default_limit(),
            "used": 0,
            "remaining": default_limit(),
            "usage_month": None,
        }
    used = int(row["used"])
    limit = int(row["monthly_limit"])
    return {
        "tenant_id": row["tenant_id"],
        "monthly_limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "usage_month": row["usage_month"],
    }


def usage_all() -> List[Dict[str, Any]]:
    """Current-bucket usage for every tenant that has a row (owner report)."""
    from sqlalchemy import text

    from backend.auth.async_jobs import AsyncStoreUnavailableError

    try:
        ensure_schema_for_current_store()
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT tenant_id, monthly_limit, used, usage_month "
                    "FROM platrixa_tenant_transaction_quotas ORDER BY tenant_id"
                )
            ).mappings().all()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("transaction quota read failure: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc

    out: List[Dict[str, Any]] = []
    for row in rows:
        used = int(row["used"])
        limit = int(row["monthly_limit"])
        out.append(
            {
                "tenant_id": row["tenant_id"],
                "monthly_limit": limit,
                "used": used,
                "remaining": max(0, limit - used),
                "usage_month": row["usage_month"],
            }
        )
    return out


__all__ = [
    "TX_MONTHLY_LIMIT_ENV_VAR",
    "DEFAULT_MONTHLY_TX_LIMIT",
    "TRANSACTION_QUOTA_EXCEEDED",
    "TransactionQuotaConfigurationError",
    "TransactionQuotaExceeded",
    "default_limit",
    "validate_config",
    "reset_cache",
    "ensure_schema_for_current_store",
    "reserve",
    "release",
    "set_limit",
    "usage",
    "usage_all",
]
