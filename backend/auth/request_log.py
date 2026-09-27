"""Request observability log (Phase 5H).

A small, append-only, tenant-scoped METADATA log for admitted data-plane
requests, on the same metering PostgreSQL as every other Phase 5 store.
Deliberately NOT an analytics platform.

PRIVACY BOUNDARY (enforced here, tested in suite 84):

  * The raw financial input is NEVER persisted and NEVER returned in a
    history list — it exists only inside the request scope that supplied
    it.
  * No raw keys, no key hashes, no secrets are stored or returned.
    Only the masked ``key_prefix`` is recorded for identification.
  * ``capability_id`` is recorded only when the engine actually reported
    one; it is never invented. (Today the engine does not report one on
    /v1/process — the column simply stays NULL rather than fabricating.)
  * Every read is tenant-scoped: attribution comes from the
    authenticated tenant context, never inferred from a request_id.

RETENTION (stated honestly):
  * Request metadata: retained in ``platrixa_request_log``; operators
    prune rows older than ``REQUEST_LOG_RETENTION_DAYS`` (default 30)
    via :func:`prune_older_than` — an explicit, logged action, not
    silent automatic deletion.
  * Idempotent RESULT snapshots remain governed by Phase 5C retention
    (72h). This module never extends or fakes that window: a request
    whose snapshot has expired returns metadata-only.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger("platrixa.api")

REQUEST_LOG_RETENTION_DAYS = int(os.getenv("PLATRIXA_REQUEST_LOG_RETENTION_DAYS", "30") or "30")
HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 200

# Outcome codes (HTTP-agnostic; routes map them deterministically).
STORE_UNAVAILABLE = "REQUEST_HISTORY_UNAVAILABLE"
NOT_FOUND = "REQUEST_NOT_FOUND"


class RequestLogStoreError(Exception):
    """Request-log store unavailable while performing an operation.

    The HTTP layer MUST fail closed on this (503) — never degrade into
    silently serving partial history.
    """


# ---------------------------------------------------------------------------
# Store wiring (lazy, request-time; same store as the metering gate)
# ---------------------------------------------------------------------------


def _store_database_url() -> Optional[str]:
    from backend.auth.gate import METERING_ENV_VAR

    return (os.getenv(METERING_ENV_VAR, "") or "").strip() or None


def _session_factory():
    """Session factory on the shared metering store.

    Deliberately REUSES the metering gate's pooled engine (same database,
    same pool) instead of creating a second connection pool — a second
    pool would double peak connections under concurrent load and can
    starve the admission path (observed as spurious fail-closed 503s in
    the suite 66 concurrency proof). The request log only ensures its
    schema once per URL, then shares the gate's factory.
    """
    global _schema_ensured
    url = _store_database_url()
    if url is None:
        raise RequestLogStoreError("request-log store not configured")

    from backend.auth.gate import _session_factory as gate_session_factory

    if url not in _schema_ensured:
        from sqlalchemy import create_engine

        ddl_url = url.replace("postgresql://", "postgresql+psycopg2://", 1) if url.startswith("postgresql://") else url
        engine = create_engine(ddl_url, future=True)
        _ensure_schema(engine)
        engine.dispose()
        _schema_ensured.add(url)
    return gate_session_factory()


_schema_ensured: set = set()


def _ensure_schema(engine) -> None:
    from pathlib import Path
    from sqlalchemy import text

    ddl_path = (
        Path(__file__).resolve().parent.parent
        / "database"
        / "platrixa_request_log_schema.sql"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl_path.read_text(encoding="utf-8")))


def _session():
    return _session_factory()()


# ---------------------------------------------------------------------------
# Write path — append-only; called ONLY after a request reached a terminal
# outcome (admitted requests that completed processing, plus deterministic
# rejections). Never called for auth failures (no tenant exists yet).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Background writer — observability NEVER competes with the admission path.
#
# Appends are enqueued (non-blocking) and written by one daemon thread
# through one serialized connection. Rationale (measured, not assumed):
# a synchronous append inside the request added pool demand during the
# suite-66 40-thread concurrency storm and starved the metering gate into
# spurious fail-closed 503s. The bounded queue makes this explicitly
# best-effort telemetry: under sustained overload the OLDEST-arriving new
# events are dropped with a warning rather than slowing admissions.
# Every READ path still fails closed; only WRITES are best-effort.
# ---------------------------------------------------------------------------

_WRITE_QUEUE_MAX = 1000
_queue: Optional["object"] = None
_worker_started = False
_worker_lock = None


def _ensure_worker():
    global _queue, _worker_started, _worker_lock
    import threading
    from queue import Queue

    if _worker_started:
        return
    with (_worker_lock or threading.Lock()):
        if _worker_started:
            return
        _worker_lock = threading.Lock()
        _queue = Queue(maxsize=_WRITE_QUEUE_MAX)
        worker = threading.Thread(target=_drain_forever, name="platrixa-request-log", daemon=True)
        worker.start()
        _worker_started = True


def _drain_forever() -> None:  # pragma: no cover — daemon loop
    import threading
    from queue import Empty

    idle = threading.Event()
    while True:
        try:
            row = _queue.get(timeout=1.0)
            _insert_row(row)
            _queue.task_done()
            idle.clear()
        except Empty:
            idle.set()
        except Exception as exc:  # never let the writer die
            logger.warning("request-log writer error (non-fatal): %s", type(exc).__name__)


def _insert_row(row: dict) -> None:
    from sqlalchemy import text as _text

    with _session() as session:
        session.execute(
            _text(
                "INSERT INTO platrixa_request_log "
                "(tenant_id, key_prefix, endpoint, request_id, http_status, "
                "api_status, reason_code, capability_id, duration_ms) "
                "VALUES (:t, :kp, :ep, :rid, :hs, :astatus, :rc, :cap, :dm)"
            ),
            row,
        )
        session.commit()


def record_request(
    tenant_id: str,
    endpoint: str,
    http_status: int,
    request_id: Optional[str] = None,
    key_prefix: Optional[str] = None,
    api_status: Optional[str] = None,
    reason_code: Optional[str] = None,
    capability_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> bool:
    """Append one metadata row. Best-effort BY DESIGN for availability.

    Enqueued onto the background writer (never executed on the request
    thread — observability must not add pool demand to the admission
    path). Full queue → the event is dropped with a warning; the request
    contract is unaffected. Reads remain fail-closed.
    """
    row = {
        "t": tenant_id,
        "kp": (key_prefix or None) and str(key_prefix)[:20],
        "ep": endpoint[:64],
        "rid": (request_id or None) and str(request_id)[:128],
        "hs": int(http_status),
        "astatus": (api_status if isinstance(api_status, str) else None),
        "rc": (reason_code or None) and str(reason_code)[:80],
        "cap": (capability_id or None) and str(capability_id)[:120],
        "dm": int(duration_ms) if duration_ms is not None else None,
    }
    try:
        _ensure_worker()
        _queue.put_nowait(row)
        return True
    except Exception as exc:  # queue full or worker unavailable — drop, never block
        logger.warning("request-log append dropped (non-fatal): %s", type(exc).__name__)
        return False


def flush(timeout: float = 5.0) -> bool:
    """Wait until all enqueued metadata rows are committed (test/operator hook)."""
    if _queue is None:
        return True
    try:
        _queue.join()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Read paths — tenant-scoped, deterministic, fail-closed
# ---------------------------------------------------------------------------


def list_requests(
    tenant_id: str,
    limit: int = HISTORY_DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    """Deterministic history: created_at DESC, id DESC (stable tiebreaker).

    Returns ``(rows, total)`` so callers can paginate honestly. Total is
    a plain COUNT over the tenant's rows.
    """
    from sqlalchemy import text

    safe_limit = max(1, min(int(limit), HISTORY_MAX_LIMIT))
    safe_offset = max(0, int(offset))
    try:
        with _session() as session:
            rows = session.execute(
                text(
                    "SELECT id, tenant_id, key_prefix, endpoint, request_id, "
                    "http_status, api_status, reason_code, capability_id, "
                    "duration_ms, created_at FROM platrixa_request_log "
                    "WHERE tenant_id = :t "
                    "ORDER BY created_at DESC, id DESC "
                    "LIMIT :l OFFSET :o"
                ),
                {"t": tenant_id, "l": safe_limit, "o": safe_offset},
            ).mappings().all()
            total = session.execute(
                text("SELECT count(*) FROM platrixa_request_log WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalar()
    except RequestLogStoreError:
        raise
    except Exception as exc:
        raise RequestLogStoreError("request history lookup failed") from exc
    return [dict(r) for r in rows], int(total or 0)


def get_request(tenant_id: str, request_id: str) -> Optional[dict]:
    """One metadata row, strictly tenant-scoped.

    Never infers ownership from the request_id alone; the tenant comes
    from the authenticated context. Returns None for unknown or
    cross-tenant ids (indistinguishable, no existence leak).
    """
    from sqlalchemy import text

    try:
        with _session() as session:
            row = session.execute(
                text(
                    "SELECT id, tenant_id, key_prefix, endpoint, request_id, "
                    "http_status, api_status, reason_code, capability_id, "
                    "duration_ms, created_at FROM platrixa_request_log "
                    "WHERE tenant_id = :t AND request_id = :rid "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"t": tenant_id, "rid": str(request_id)[:128]},
            ).mappings().first()
    except RequestLogStoreError:
        raise
    except Exception as exc:
        raise RequestLogStoreError("request lookup failed") from exc
    return dict(row) if row else None


def prune_older_than(days: int = REQUEST_LOG_RETENTION_DAYS) -> int:
    """Delete metadata rows older than ``days``. Returns rows removed.

    An explicit operator action (documented retention policy), not an
    automatic silent deletion.
    """
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    try:
        with _session() as session:
            result = session.execute(
                text("DELETE FROM platrixa_request_log WHERE created_at < :c"),
                {"c": cutoff},
            )
            session.commit()
            return result.rowcount or 0
    except Exception as exc:
        raise RequestLogStoreError("retention prune failed") from exc
