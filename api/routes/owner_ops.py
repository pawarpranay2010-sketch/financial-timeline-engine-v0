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
