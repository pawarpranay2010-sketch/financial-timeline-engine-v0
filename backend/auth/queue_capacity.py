"""Durable, PostgreSQL-backed pending-job capacity for the Platrixa queue.

WHY THIS MODULE EXISTS
----------------------
Phase 1B originally shipped a process-local, in-memory capacity tracker.
That design could not enforce anything: counters reset on every process
restart (Railway redeploys, crashes), were not shared between application
processes or instances, were incremented only when a worker CLAIMED a job
(so jobs sitting in PostgreSQL waiting for a worker counted against
nothing), and defaulted to ``None`` — meaning the limits were turned off
unless somebody remembered to set them.

This module replaces it. There is no in-memory counter, no Redis, and no
second source of truth. The authoritative job table
(``platrixa_async_jobs``) carries one durable boolean per job —
``capacity_held`` — and PENDING capacity is *derived* from it:

    pending = SELECT count(*) FROM platrixa_async_jobs
              WHERE capacity_held = TRUE

PENDING-JOB DEFINITION (exact)
------------------------------
A job consumes one pending slot from the moment it is INSERTED (status
QUEUED, inside the admission transaction) until it reaches a TERMINAL
state. The states and how each is accounted for:

===================================  ===============  ==================
state                                pending slot     transition
===================================  ===============  ==================
QUEUED (waiting for a worker)        HELD             insert
PROCESSING (claimed, working)        HELD             claim does NOT release
RETRY_WAIT (backoff, retryable)      HELD             retry does NOT release
COMPLETED (terminal)                 RELEASED         once
FAILED (terminal, permanent or       RELEASED         once
       retry budget exhausted)
lease expired, still PROCESSING      HELD             release happens at
                                                      reclaim/terminal
worker interruption / crash          HELD             recovery preserves it
cancellation                         n/a (not supported by the API;
                                     if added, it must set the same flag)
===================================  ===============  ==================

The transition PROCESSING → COMPLETED / FAILED writes
``capacity_held = FALSE`` in the SAME atomic UPDATE that writes the
terminal state, conditional on the row still being PROCESSING and on the
caller still owning the lease (the existing fencing predicates). Because
that UPDATE matches at most one row and requires ``status='PROCESSING'``,
a duplicate completion attempt releases nothing twice. A retryable
failure that stays inside the retry budget does NOT clear the flag —
the job is still pending.

MONTHLY QUOTA IS SEPARATE
-------------------------
``TenantQuota`` (backend/auth/gate.py) accounts monthly request units.
Pending queue capacity is a different budget entirely: saturating the
queue never corrupts, refunds, or borrows monthly usage.

ADMISSION ATOMICITY
-------------------
:func:`check_admission` must be called inside the SAME transaction as the
job INSERT, after :func:`acquire_admission_lock`. The lock is
``pg_advisory_xact_lock`` on a single fixed bigint key: it is
transaction-scoped (released automatically on COMMIT *and* ROLLBACK), it
is honoured by every PostgreSQL session on the cluster — every Railway
instance, every restarted process — and it serialises only the brief
admission window (two counts + one insert). A SELECT COUNT followed by an
independent INSERT therefore cannot race: two concurrent admissions are
totally ordered, so two requests can never both consume the same
remaining slot.

CONFIGURATION (validated at startup)
------------------------------------
    PLATRIXA_QUEUE_GLOBAL_LIMIT      default 100 pending jobs, cluster-wide
    PLATRIXA_QUEUE_PER_TENANT_LIMIT  default 10 pending jobs per tenant

Defaults are conservative engineering values chosen for the current
deployment (a single Android/Termux worker claiming one inference job at
a time) — NOT measured throughput. A single worker cannot drain a large
backlog, so a small bound is the honest one.

Validation is strict: a value that is not a positive integer, or a
per-tenant limit larger than the global limit, raises
:class:`QueueLimitConfigurationError` from :func:`validate_config`, which
``api.main.create_app`` calls. Protection is therefore never silently
disabled by a typo. Operators adjust the two variables and redeploy;
nothing else changes.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

#: Fixed, stable 64-bit key for the transaction-scoped admission lock.
#: Decimal form of 0x504C5851 ("PLXQ"); must never change per deployment,
#: otherwise two deployments sharing a store would not serialise.
ADMISSION_LOCK_KEY = 1347246673

GLOBAL_LIMIT_ENV_VAR = "PLATRIXA_QUEUE_GLOBAL_LIMIT"
PER_TENANT_LIMIT_ENV_VAR = "PLATRIXA_QUEUE_PER_TENANT_LIMIT"

#: Conservative engineering defaults (single-worker deployment).
DEFAULT_GLOBAL_PENDING_LIMIT = 100
DEFAULT_PER_TENANT_PENDING_LIMIT = 10

#: Rejection codes surfaced to the developer API (HTTP 503).
GLOBAL_QUEUE_FULL = "GLOBAL_QUEUE_FULL"
TENANT_QUEUE_FULL = "TENANT_QUEUE_FULL"

#: Job states that are terminal and therefore must NOT hold capacity.
TERMINAL_STATUSES = ("COMPLETED", "FAILED")

#: Job states that are pending and therefore must hold capacity.
PENDING_STATUSES = ("QUEUED", "PROCESSING", "RETRY_WAIT")


class QueueLimitConfigurationError(RuntimeError):
    """Raised at startup when queue-limit configuration is unusable."""


class QueueCapacityExceeded(Exception):
    """A job admission was refused because the pending queue is full.

    ``code`` is one of :data:`GLOBAL_QUEUE_FULL` / :data:`TENANT_QUEUE_FULL`
    and is safe to return to the caller (it names no other tenant).
    """

    def __init__(
        self,
        code: str,
        *,
        scope: str,
        limit: int,
        pending: int,
        tenant_id: Optional[str] = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.scope = scope
        self.limit = limit
        self.pending = pending
        #: The tenant's OWN id only — never another tenant's.
        self.tenant_id = tenant_id


def _parse_positive_int(name: str, raw: Optional[str], default: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    try:
        value = int(text, 10)
    except (TypeError, ValueError):
        raise QueueLimitConfigurationError(
            f"{name} must be a positive integer, got {text!r}"
        ) from None
    if value <= 0:
        raise QueueLimitConfigurationError(
            f"{name} must be a positive integer, got {value}"
        )
    return value


_cache_lock = threading.Lock()
_cache: Dict[Tuple[str, str], Tuple[int, int]] = {}

#: Per-process admission rejection counters (owner visibility only).
#: Deliberately NOT durable: these describe what THIS process refused, and
#: labelling them as such is more honest than merging counters that a second
#: instance would silently duplicate. Durable refusal evidence already exists
#: in ``platrixa_request_log`` via the route's 503 response.
_rejection_lock = threading.Lock()
_rejections: Dict[str, int] = {GLOBAL_QUEUE_FULL: 0, TENANT_QUEUE_FULL: 0}


def _note_rejection(code: str) -> None:
    with _rejection_lock:
        _rejections[code] = int(_rejections.get(code, 0)) + 1


def rejection_snapshot() -> Dict[str, int]:
    """Rejections observed by this process, keyed by the public code."""
    with _rejection_lock:
        return dict(_rejections)


def reset_rejections() -> None:
    """Tests only: clear the per-process counters."""
    with _rejection_lock:
        for key in _rejections:
            _rejections[key] = 0


def limits() -> Tuple[int, int]:
    """Return ``(global_pending_limit, per_tenant_pending_limit)``.

    Raises :class:`QueueLimitConfigurationError` for unusable values. The
    parsed pair is cached against the raw environment strings, so a test
    that changes the environment sees the new limits immediately while
    normal request handling does no repeated parsing.
    """
    raw_global = os.getenv(GLOBAL_LIMIT_ENV_VAR)
    raw_tenant = os.getenv(PER_TENANT_LIMIT_ENV_VAR)
    key = (raw_global or "", raw_tenant or "")
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    global_limit = _parse_positive_int(
        GLOBAL_LIMIT_ENV_VAR, raw_global, DEFAULT_GLOBAL_PENDING_LIMIT
    )
    per_tenant_limit = _parse_positive_int(
        PER_TENANT_LIMIT_ENV_VAR, raw_tenant, DEFAULT_PER_TENANT_PENDING_LIMIT
    )
    if per_tenant_limit > global_limit:
        raise QueueLimitConfigurationError(
            f"{PER_TENANT_LIMIT_ENV_VAR} ({per_tenant_limit}) must not exceed "
            f"{GLOBAL_LIMIT_ENV_VAR} ({global_limit})"
        )
    with _cache_lock:
        _cache[key] = (global_limit, per_tenant_limit)
    return global_limit, per_tenant_limit


def validate_config() -> Tuple[int, int]:
    """Fail-fast startup validation used by ``api.main.create_app``."""
    return limits()


def reset_cache() -> None:
    """Drop the parsed-limit cache (tests and process-config reloads)."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Transactional primitive
# ---------------------------------------------------------------------------


def acquire_admission_lock(session: Any) -> None:
    """Take the cluster-wide, transaction-scoped admission lock.

    Must be called FIRST inside the admission transaction, before counting.
    The lock is released by PostgreSQL when the transaction ends, whatever
    the outcome (commit, rollback, connection loss).
    """
    from sqlalchemy import text

    session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADMISSION_LOCK_KEY}
    )


def pending_counts(session: Any, tenant_id: str) -> Tuple[int, int]:
    """Return ``(global_pending, tenant_pending)`` from the durable table.

    Caller must already hold :func:`acquire_admission_lock` inside the
    transaction that will insert the job.
    """
    from sqlalchemy import text

    global_pending = int(
        session.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE"
            )
        ).scalar_one()
    )
    tenant_pending = int(
        session.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t"
            ),
            {"t": tenant_id},
        ).scalar_one()
    )
    return global_pending, tenant_pending


def check_admission(session: Any, tenant_id: str) -> Tuple[int, int]:
    """Enforce both limits inside the open admission transaction.

    Returns ``(global_pending, tenant_pending)`` when the admission is
    allowed; raises :class:`QueueCapacityExceeded` otherwise. The caller's
    transaction is expected to roll back on the exception, so a rejected
    admission reserves nothing.
    """
    global_limit, per_tenant_limit = limits()
    global_pending, tenant_pending = pending_counts(session, tenant_id)

    if global_pending >= global_limit:
        _note_rejection(GLOBAL_QUEUE_FULL)
        raise QueueCapacityExceeded(
            GLOBAL_QUEUE_FULL,
            scope="global",
            limit=global_limit,
            pending=global_pending,
        )
    if tenant_pending >= per_tenant_limit:
        _note_rejection(TENANT_QUEUE_FULL)
        raise QueueCapacityExceeded(
            TENANT_QUEUE_FULL,
            scope="tenant",
            limit=per_tenant_limit,
            pending=tenant_pending,
            tenant_id=tenant_id,
        )
    return global_pending, tenant_pending


# ---------------------------------------------------------------------------
# Durable, read-only reporting / reconciliation
# ---------------------------------------------------------------------------


def _store_session_factory():
    """The canonical async-store sessionmaker (raises when unconfigured)."""
    from backend.auth import async_jobs

    return async_jobs._session_factory()


def pending_summary(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only pending-workload snapshot (owner reporting).

    Never mutates state. Returns limits, live pending counts, and the
    per-status breakdown of pending jobs. ``status_counts`` covers PENDING
    states only, so a tenant's terminal history never leaks into this view
    beyond the aggregate job counts below.
    """
    from sqlalchemy import text

    global_limit, per_tenant_limit = limits()
    SessionLocal = _store_session_factory()
    params: Dict[str, Any] = {}
    scope = ""
    if tenant_id is not None:
        scope = " AND tenant_id = :t"
        params["t"] = tenant_id

    with SessionLocal() as session:
        global_pending = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs "
                    "WHERE capacity_held = TRUE"
                )
            ).scalar_one()
        )
        tenant_pending = global_pending
        if tenant_id is not None:
            tenant_pending = int(
                session.execute(
                    text(
                        "SELECT count(*) FROM platrixa_async_jobs "
                        "WHERE capacity_held = TRUE AND tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).scalar_one()
            )
        rows = session.execute(
            text(
                "SELECT status, count(*) AS n FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE" + scope + " GROUP BY status"
            ),
            params,
        ).mappings().all()

    return {
        "global_limit": global_limit,
        "per_tenant_limit": per_tenant_limit,
        "pending_total": global_pending,
        "pending_for_tenant": tenant_pending,
        "status_counts": {str(r["status"]): int(r["n"]) for r in rows},
    }


def reconcile() -> Dict[str, Any]:
    """Detect durable accounting divergence between the flag and the state.

    Two divergence classes are possible and both are reported:

      * ``terminal_still_held`` — a COMPLETED/FAILED row that still holds a
        pending slot. The reservation is orphaned: capacity silently
        shrinks until reconciled.
      * ``pending_already_released`` — a QUEUED/PROCESSING/RETRY_WAIT row
        that does not hold a slot. The job is running without accounting
        for capacity: the bound can be exceeded.

    No repair is performed implicitly. :func:`repair` is the explicit,
    operator-invoked fix (also used by tests that deliberately corrupt a
    row to prove detection works).
    """
    from sqlalchemy import text

    SessionLocal = _store_session_factory()
    terminal_list = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
    pending_list = ", ".join(f"'{s}'" for s in PENDING_STATUSES)
    with SessionLocal() as session:
        terminal_still_held = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs "
                    f"WHERE status IN ({terminal_list}) AND capacity_held = TRUE"
                )
            ).scalar_one()
        )
        pending_already_released = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs "
                    f"WHERE status IN ({pending_list}) AND capacity_held = FALSE"
                )
            ).scalar_one()
        )
        pending_rows = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs "
                    "WHERE capacity_held = TRUE"
                )
            ).scalar_one()
        )
    return {
        "pending_rows": pending_rows,
        "terminal_still_held": terminal_still_held,
        "pending_already_released": pending_already_released,
        "consistent": terminal_still_held == 0 and pending_already_released == 0,
    }


def repair() -> Dict[str, int]:
    """Reconcile the flag to the authoritative job status (explicit call).

    Idempotent. Returns the number of rows corrected in each direction.
    """
    from sqlalchemy import text

    SessionLocal = _store_session_factory()
    terminal_list = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
    pending_list = ", ".join(f"'{s}'" for s in PENDING_STATUSES)
    with SessionLocal() as session:
        with session.begin():
            released = session.execute(
                text(
                    "UPDATE platrixa_async_jobs SET capacity_held = FALSE "
                    f"WHERE status IN ({terminal_list}) AND capacity_held = TRUE"
                )
            ).rowcount
            held = session.execute(
                text(
                    "UPDATE platrixa_async_jobs SET capacity_held = TRUE "
                    f"WHERE status IN ({pending_list}) AND capacity_held = FALSE"
                )
            ).rowcount
    return {
        "terminal_released": int(released or 0),
        "pending_reheld": int(held or 0),
    }


__all__ = [
    "ADMISSION_LOCK_KEY",
    "GLOBAL_LIMIT_ENV_VAR",
    "PER_TENANT_LIMIT_ENV_VAR",
    "DEFAULT_GLOBAL_PENDING_LIMIT",
    "DEFAULT_PER_TENANT_PENDING_LIMIT",
    "GLOBAL_QUEUE_FULL",
    "TENANT_QUEUE_FULL",
    "TERMINAL_STATUSES",
    "PENDING_STATUSES",
    "QueueLimitConfigurationError",
    "QueueCapacityExceeded",
    "limits",
    "validate_config",
    "reset_cache",
    "acquire_admission_lock",
    "pending_counts",
    "check_admission",
    "pending_summary",
    "reconcile",
    "repair",
    "rejection_snapshot",
    "reset_rejections",
]
