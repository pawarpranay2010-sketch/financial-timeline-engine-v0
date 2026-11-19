"""Platrixa — owner-only operational visibility.

WHY THIS EXISTS
---------------
The queue's own reporting primitives (``queue_capacity.pending_summary``,
``queue_capacity.reconcile``) existed as library functions with **no route**
in front of them, and there was no server-side worker presence of any kind:
the only "heartbeat" in the system is a job lease renewal. An operator could
therefore not answer, from outside the process, "how deep is the queue?",
"is the phone claiming?", or "did capacity accounting diverge?".

This router exposes exactly that information, and nothing else:

  * pending/processing/retry counts and both configured bounds;
  * ``reconcile()`` divergence classes (read-only — never repairs);
  * per-worker last activity, DERIVED from lease ownership on PROCESSING
    rows. Deliberately no new heartbeat table: a lease-derived signal cannot
    disagree with the lease the server already enforces, and a worker that
    stops claiming disappears on its own when its lease lapses;
  * admission rejections observed by THIS process;
  * the effective per-tenant rate-limit configuration.

SECURITY
--------
Mounted under ``/v1/owner`` and authorized by the SAME management credential
as key management (``_authorize``): a developer API key can never reach it.
No response contains another tenant's id, transaction text, prompt, candidate
payload or key material. Rejection counters are per process and are labelled
as such — a second instance's counters are not silently merged.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from api.routes.developer_keys import _authorize, _store_error_response

logger = logging.getLogger("platrixa.api")

router = APIRouter(prefix="/v1/owner", tags=["owner"])

API_VERSION = "v1"


def _worker_activity() -> List[Dict[str, Any]]:
    """Lease-derived worker presence: who holds work, and since when.

    A worker appears here only while it owns at least one PROCESSING job.
    ``last_seen`` is the row's ``updated_at``, which every claim, renewal and
    terminal write touches — so this is the server's own record of the
    worker's activity, not a client-supplied claim.
    """
    from sqlalchemy import text

    from backend.auth import async_jobs

    SessionLocal = async_jobs._session_factory()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT lease_owner AS worker_id, count(*) AS processing, "
                "       max(updated_at) AS last_seen, "
                "       min(lease_expires_at) AS next_expiry "
                "FROM platrixa_async_jobs "
                "WHERE status = 'PROCESSING' AND lease_owner IS NOT NULL "
                "GROUP BY lease_owner ORDER BY lease_owner"
            )
        ).mappings().all()
    return [
        {
            "worker_id": str(r["worker_id"]),
            "processing": int(r["processing"]),
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            "next_lease_expiry": r["next_expiry"].isoformat() if r["next_expiry"] else None,
        }
        for r in rows
    ]


@router.get(
    "/queue/status",
    responses={
        403: {"description": "Management authorization failed"},
        503: {"description": "Store unavailable (fail closed)"},
    },
)
def queue_status(
    request: Request,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    """Owner-only snapshot of queue depth, capacity, reconciliation and workers."""
    from backend.auth import queue_capacity, tenant_rate_limit

    _authorize(request)

    try:
        summary = queue_capacity.pending_summary()
        reconciliation = queue_capacity.reconcile()
        workers = _worker_activity()
        rejections = queue_capacity.rejection_snapshot()
    except Exception as exc:
        logger.warning("owner queue status unavailable: %s", type(exc).__name__)
        return _store_error_response(request)

    rate_limit, rate_window = tenant_rate_limit.limits()

    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "queue": summary,
            "reconciliation": reconciliation,
            "workers": workers,
            "admission": {
                "rejections": rejections,
                "scope": "this API process only",
            },
            "rate_limit": {
                "tenant_limit": rate_limit,
                "window_seconds": rate_window,
                "enabled": rate_limit > 0,
            },
        },
    )


@router.get(
    "/usage",
    responses={
        403: {"description": "Management authorization failed"},
        503: {"description": "Store unavailable (fail closed)"},
    },
)
def usage_report(
    request: Request,
    x_platrixa_management_token: Optional[str] = Header(default=None),
):
    """Owner-only per-tenant usage report for the private beta.

    Answers, for every provisioned tenant: which keys are active/revoked, how
    many jobs and accounting transactions were submitted, how deep their backlog
    is, the oldest pending instant, processing/retry counts, failed and
    review-required outcomes, blocked/deferred batch transactions, both quotas
    (jobs and transactions) and durable throttling events.

    Developer-facing routes never expose this; it requires the SAME management
    credential as key management and contains no transaction text, candidate
    payload, prompt or key material — only counts and timestamps.
    """
    from sqlalchemy import text

    from backend.auth import (
        async_jobs,
        batch_jobs,
        queue_capacity,
        tenant_rate_limit,
        transaction_quota,
    )
    from backend.auth import api_keys as key_store

    _authorize(request)

    try:
        SessionLocal = async_jobs._session_factory()
        keys_table = key_store.api_key_model().__tablename__
        with SessionLocal() as session:
            job_rows = session.execute(
                text(
                    """SELECT tenant_id,
                       count(*) FILTER (WHERE kind = 'inference') AS jobs,
                       coalesce(sum(transaction_count) FILTER (WHERE kind = 'inference'), 0) AS transactions,
                       count(*) FILTER (WHERE kind = 'inference' AND status = 'COMPLETED') AS completed,
                       count(*) FILTER (WHERE kind = 'inference' AND status = 'FAILED') AS failed,
                       count(*) FILTER (WHERE kind = 'inference' AND status = 'PROCESSING') AS processing,
                       count(*) FILTER (WHERE kind = 'inference' AND status = 'RETRY_WAIT') AS retry_wait,
                       count(*) FILTER (WHERE kind = 'inference' AND status IN ('QUEUED','PROCESSING','RETRY_WAIT')) AS pending,
                       min(created_at) FILTER (WHERE kind = 'inference' AND status IN ('QUEUED','PROCESSING','RETRY_WAIT')) AS oldest_pending_at
                    FROM platrixa_async_jobs GROUP BY tenant_id ORDER BY tenant_id"""
                )
            ).mappings().all()
            batch_rows = session.execute(
                text(
                    """SELECT tenant_id,
                       count(*) FILTER (WHERE status = 'VERIFIED') AS verified,
                       count(*) FILTER (WHERE status = 'REVIEW_REQUIRED') AS review_required,
                       count(*) FILTER (WHERE status = 'UNSUPPORTED') AS unsupported,
                       count(*) FILTER (WHERE status = 'FAILED') AS failed,
                       count(*) FILTER (WHERE status = 'BLOCKED') AS blocked,
                       count(*) FILTER (WHERE status = 'DEFERRED') AS deferred,
                       count(*) FILTER (WHERE status = 'PENDING') AS pending
                    FROM platrixa_batch_transactions GROUP BY tenant_id ORDER BY tenant_id"""
                )
            ).mappings().all()
            key_rows = session.execute(
                text(
                    "SELECT tenant_id, "
                    " count(*) FILTER (WHERE status = 'ACTIVE') AS active, "
                    " count(*) FILTER (WHERE status = 'REVOKED') AS revoked "
                    f"FROM {keys_table} GROUP BY tenant_id ORDER BY tenant_id"
                )
            ).mappings().all()
            job_quota_rows = session.execute(
                text(
                    "SELECT tenant_id, monthly_limit, current_month_usage, usage_month, is_active "
                    "FROM platrixa_tenant_quotas ORDER BY tenant_id"
                )
            ).mappings().all()
        rate_events = tenant_rate_limit.event_counts()
        tx_quotas = {row["tenant_id"]: row for row in transaction_quota.usage_all()}
        summary = queue_capacity.pending_summary()
    except Exception as exc:
        logger.warning("owner usage report unavailable: %s", type(exc).__name__)
        return _store_error_response(request)

    rate_limit, rate_window = tenant_rate_limit.limits()
    bounds = batch_jobs.limits()

    tenants: Dict[str, Dict[str, Any]] = {}

    def _slot(tenant_id: str) -> Dict[str, Any]:
        return tenants.setdefault(
            tenant_id,
            {
                "tenant_id": tenant_id,
                "keys": {"active": 0, "revoked": 0},
                "jobs": {
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "processing": 0,
                    "retry_wait": 0,
                    "pending": 0,
                    "oldest_pending_at": None,
                },
                "transactions": {
                    "admitted_by_jobs": 0,
                    "verified": 0,
                    "review_required": 0,
                    "unsupported": 0,
                    "failed": 0,
                    "blocked": 0,
                    "deferred": 0,
                    "pending": 0,
                },
                "quota": {
                    "jobs": None,
                    "transactions": None,
                    "window": "current UTC month (usage_month on each quota row)",
                },
                "rate_limit": {
                    "rejected": 0,
                    "last_rejected_at": None,
                    "limit": rate_limit,
                    "window_seconds": rate_window,
                },
            },
        )

    for row in job_rows:
        slot = _slot(str(row["tenant_id"]))
        # Every counter here is inference-only and ALL-TIME (rows still present
        # in the store). The quota block below is a MONTH bucket. The two are
        # labelled separately so a lifetime job count is never compared with a
        # monthly allowance as if they were the same window.
        slot["jobs"] = {
            "window": "all_time",
            "unit": "jobs (one per admitted job, regardless of transaction_count)",
            "total": int(row["jobs"]),
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "processing": int(row["processing"]),
            "retry_wait": int(row["retry_wait"]),
            "pending": int(row["pending"]),
            "oldest_pending_at": row["oldest_pending_at"].isoformat()
            if row["oldest_pending_at"]
            else None,
        }
        slot["transactions"]["admitted_by_jobs"] = int(row["transactions"])

    for row in batch_rows:
        slot = _slot(str(row["tenant_id"]))
        for key in (
            "verified",
            "review_required",
            "unsupported",
            "failed",
            "blocked",
            "deferred",
            "pending",
        ):
            slot["transactions"][key] = int(row[key])

    for row in key_rows:
        slot = _slot(str(row["tenant_id"]))
        slot["keys"] = {"active": int(row["active"]), "revoked": int(row["revoked"])}

    for row in job_quota_rows:
        slot = _slot(str(row["tenant_id"]))
        slot["quota"]["jobs"] = {
            "unit": "admitted job units",
            "monthly_limit": int(row["monthly_limit"]),
            "used": int(row["current_month_usage"]),
            "usage_month": row["usage_month"],
            "active": bool(row["is_active"]),
        }

    for tenant_id, quota in tx_quotas.items():
        _slot(tenant_id)["quota"]["transactions"] = {
            "unit": "admitted accounting transactions",
            "monthly_limit": int(quota["monthly_limit"]),
            "used": int(quota["used"]),
            "remaining": int(quota["remaining"]),
            "usage_month": quota.get("usage_month"),
        }

    for tenant_id, events in rate_events.items():
        slot = _slot(tenant_id)
        slot["rate_limit"]["rejected"] = int(events["rejected"])
        slot["rate_limit"]["last_rejected_at"] = events["last_rejected_at"]

    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "tenants": [tenants[key] for key in sorted(tenants)],
            "tenant_count": len(tenants),
            "batch": {
                "max_transactions": bounds["max_transactions"],
                "max_bytes": bounds["max_bytes"],
                "max_input_chars": bounds["max_input_chars"],
                "budget_seconds": bounds["budget_seconds"],
            },
            "quota_units": {
                "jobs": "admitted API jobs",
                "transactions": "admitted accounting transactions",
            },
            "reporting_windows": {
                "jobs": (
                    "all_time counts of rows still present in "
                    "platrixa_async_jobs (kind='inference')"
                ),
                "transactions": (
                    "all_time per-transaction states still present in "
                    "platrixa_batch_transactions"
                ),
                "quota": (
                    "current UTC calendar month, from each tenant's quota rows "
                    "(jobs and transactions are separate meters)"
                ),
            },
            "queue": summary,
        },
    )
