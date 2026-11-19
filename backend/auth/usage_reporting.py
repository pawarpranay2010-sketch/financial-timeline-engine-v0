"""Platrixa — honest usage counters for developer and owner reporting.

THE PROBLEM THIS MODULE EXISTS TO FIX
-------------------------------------
Two independent meters exist (see ``backend/auth/transaction_quota.py``):

  1. **jobs admitted** — one unit per admitted API request
     (``platrixa_tenant_quotas.current_month_usage``);
  2. **accounting transactions admitted** — N units for a batch of N
     (``platrixa_tenant_transaction_quotas.used``).

A usage endpoint that returns a single number called ``used`` invites a
developer to read a 5-transaction batch as "5 jobs", or a 5-transaction batch
as "1 transaction". Both are wrong. This module produces counters that are
explicit about (a) the UNIT being counted and (b) the WINDOW it covers, so a
client never has to guess.

UNITS
-----
``jobs``
    Rows in ``platrixa_async_jobs`` for the tenant. One row = one admitted
    API job, however many transactions it carries.
``transactions``
    Admitted accounting transactions — the SAME unit the transaction quota
    charges: ``sum(transaction_count)`` over the tenant's jobs in the window
    (a single-transaction job contributes 1, an independent/ordered batch
    contributes N). Only BATCH jobs materialize per-transaction rows in
    ``platrixa_batch_transactions``, so the per-state breakdown covers those
    rows and says so (``tracked_rows`` / ``coverage``) instead of pretending a
    single job's transaction has its own row.

WINDOWS
-------
Counters are scoped to one UTC calendar month by ``created_at`` (the
admission instant), matching the quota bucket's ``YYYY-MM`` semantics. They
are NOT lifetime totals: a tenant that was admitted 100 jobs last month and 3
this month reports 3. Nothing is inferred or extrapolated — every counter is a
``COUNT(*) FILTER`` over stored rows in the window.

The quota meters themselves are read separately by the routes (they are the
authoritative remaining-allowance figures). This module never mutates them.

HONEST LIMITATION (stated, not worked around)
---------------------------------------------
Job rows do not record WHICH credential admitted them, so a per-KEY job or
transaction count cannot be produced from stored data. Only the quota row is
per-credential. The routes therefore label the quota block with the scope
("this credential" vs "tenant") instead of presenting a per-key quota beside
tenant-wide counters as if they described the same population.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("platrixa.api")

#: Transaction states that represent an EVALUATED transaction (the kernel ran).
_EVALUATED_STATES = (
    "VERIFIED",
    "REVIEW_REQUIRED",
    "UNSUPPORTED",
    "INVALID_INPUT",
    "FAILED",
)

#: Admitted but never evaluated (still charged — see transaction_quota.py).
_NOT_EVALUATED_STATES = ("BLOCKED", "DEFERRED", "PENDING")

TRANSACTIONS_SCOPE_NOTE = (
    "blocked, deferred and pending transactions were admitted (and charged) "
    "but produced no evaluated outcome; they are never counted as jobs"
)


def month_window(month: Optional[str] = None) -> Dict[str, str]:
    """The UTC calendar-month window for a ``YYYY-MM`` bucket.

    Returns ISO-8601 half-open bounds ``[start, end)`` plus the bucket name and
    the boundary rule, so a client can display "resets at" without guessing.
    """
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        year, month_number = (int(part) for part in str(month).split("-"))
        start = datetime(year, month_number, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise ValueError(f"month must be a YYYY-MM string, got {month!r}") from None
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(start.year, start.month + 1, 1, tzinfo=timezone.utc)
    return {
        "month": start.strftime("%Y-%m"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "basis": "UTC calendar month, by admission time (created_at)",
    }


def _month_bounds(month: Optional[str]) -> tuple[datetime, datetime]:
    window = month_window(month)
    return (
        datetime.fromisoformat(window["start"]),
        datetime.fromisoformat(window["end"]),
    )


def tenant_usage_counts(
    tenant_id: str, *, month: Optional[str] = None
) -> Dict[str, Any]:
    """Job and transaction counters for ONE tenant in ONE month window.

    Raises ``async_jobs.AsyncStoreUnavailableError`` when the store is
    unreachable (the routes map that to their documented fail-closed 503).
    """
    from sqlalchemy import text

    from backend.auth import async_jobs

    if not tenant_id:
        raise ValueError("tenant_id is required")

    window = month_window(month)
    start, end = _month_bounds(window["month"])
    params = {"t": tenant_id, "start": start, "end": end}

    job_sql = text(
        "SELECT "
        "  count(*) FILTER (WHERE kind = 'inference') AS jobs_submitted, "
        "  count(*) FILTER (WHERE kind = 'inference' AND status = 'COMPLETED') AS jobs_completed, "
        "  count(*) FILTER (WHERE kind = 'inference' AND status = 'FAILED') AS jobs_failed, "
        "  count(*) FILTER (WHERE kind = 'inference' AND status = 'RETRY_WAIT') AS jobs_retry_wait, "
        "  count(*) FILTER (WHERE kind = 'inference' "
        "        AND status IN ('QUEUED','PROCESSING','RETRY_WAIT')) AS jobs_pending, "
        "  count(*) FILTER (WHERE kind = 'document') AS document_jobs_submitted, "
        "  coalesce(sum(transaction_count) FILTER (WHERE kind = 'inference'), 0) "
        "        AS transactions_admitted_by_jobs "
        "FROM platrixa_async_jobs "
        "WHERE tenant_id = :t AND created_at >= :start AND created_at < :end"
    )

    # A per-transaction breakdown, joined to the job row only for the tenant
    # predicate. ``t.created_at`` is the transaction's own admission instant.
    tx_sql = text(
        "SELECT t.status AS status, count(*) AS n "
        "FROM platrixa_batch_transactions t "
        "WHERE t.tenant_id = :t AND t.created_at >= :start AND t.created_at < :end "
        "GROUP BY t.status"
    )

    try:
        SessionLocal = async_jobs._session_factory()
        with SessionLocal() as session:
            job_row = session.execute(job_sql, params).mappings().first() or {}
            tx_rows = session.execute(tx_sql, params).mappings().all()
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("usage counter read failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc

    by_state: Dict[str, int] = {}
    for row in tx_rows:
        by_state[str(row["status"]).upper()] = int(row["n"])

    per_state = {
        state.lower(): int(by_state.get(state, 0)) for state in _EVALUATED_STATES
    }
    for state in _NOT_EVALUATED_STATES:
        per_state[state.lower()] = int(by_state.get(state, 0))

    evaluated = sum(int(by_state.get(state, 0)) for state in _EVALUATED_STATES)
    tracked_rows = sum(int(value) for value in by_state.values())
    admitted = int(job_row.get("transactions_admitted_by_jobs") or 0)
    untracked = max(admitted - tracked_rows, 0)

    transactions = {
        # The authoritative count: identical to what the transaction quota
        # charges, because both are derived from the job's transaction_count.
        "admitted": admitted,
        # Per-transaction rows that actually exist (batch jobs only).
        "tracked_rows": tracked_rows,
        "evaluated_tracked": evaluated,
        **per_state,
        "unit": "admitted accounting transactions (N per N-transaction batch)",
        "coverage": {
            "admitted_transactions": admitted,
            "per_transaction_rows": tracked_rows,
            "admitted_without_a_per_transaction_row": untracked,
            "note": (
                "single-transaction jobs are counted from their job row "
                "(transaction_count = 1) and do not materialize a row in "
                "platrixa_batch_transactions; their outcome is the job status. "
                "The per-state counts cover batch rows only and are never "
                "extrapolated to the admitted total."
            ),
        },
        "states_note": TRANSACTIONS_SCOPE_NOTE,
    }

    out: Dict[str, Any] = {
        "scope": "tenant",
        "tenant_id": tenant_id,
        "window": window,
        "jobs": {
            "submitted": int(job_row.get("jobs_submitted") or 0),
            "completed": int(job_row.get("jobs_completed") or 0),
            "failed": int(job_row.get("jobs_failed") or 0),
            "pending": int(job_row.get("jobs_pending") or 0),
            "retry_wait": int(job_row.get("jobs_retry_wait") or 0),
            "document_jobs_submitted": int(
                job_row.get("document_jobs_submitted") or 0
            ),
            "unit": "admitted API jobs (1 per job, regardless of batch size)",
        },
        "transactions": transactions,
        "units": {
            "jobs": "one unit per admitted API job",
            "transactions": "one unit per admitted accounting transaction",
            "relationship": "1 job = transaction_count transaction units",
        },
    }
    if tracked_rows > admitted:
        # More per-transaction rows than the jobs admit. That cannot be produced
        # by the admission path, so report the anomaly rather than picking a
        # number; no figure is adjusted.
        out["transactions"]["reconciliation"] = {
            "consistent": False,
            "note": (
                "more per-transaction rows than admitted transactions for this "
                "window; neither figure has been adjusted"
            ),
            "per_transaction_rows": tracked_rows,
            "admitted": admitted,
        }
    else:
        out["transactions"]["reconciliation"] = {
            "consistent": True,
            "unmaterialized_rows_explained": untracked,
        }

    return out


PER_KEY_LIMITATION = (
    "job and transaction counts are stored per TENANT, not per credential; "
    "the quota block is the only per-credential figure"
)


__all__ = [
    "month_window",
    "tenant_usage_counts",
    "PER_KEY_LIMITATION",
    "TRANSACTIONS_SCOPE_NOTE",
]
