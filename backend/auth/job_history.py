"""Platrixa — tenant-scoped job history (the read model behind GET /v1/jobs).

WHY THIS EXISTS
---------------
Every other read on the asynchronous plane starts from an id the developer
already had to remember (``GET /v1/jobs/{job_id}``, ``GET /v1/results/{id}``).
A developer building their own application therefore had to persist every job
id themselves — and lost the entire history when their own store was reset.
This module answers "what work has this tenant submitted, newest first?" from
the durable job table, so a job-history screen needs no local bookkeeping.

ISOLATION (non-negotiable)
--------------------------
Every query is anchored on ``tenant_id = :t``, where the tenant comes from the
AUTHENTICATED data-plane key, never from a request value. No function here
accepts a caller-supplied tenant id, and an unknown/foreign job id is
indistinguishable from a nonexistent one because it is never selected.

A website visitor request carries a visitor scope (bound by the edge proxy).
When that scope is present the listing is narrowed to
``owner_type = 'website_visitor' AND visitor_id = :v`` — the SAME predicate
``async_jobs.get_job`` already enforces — so a visitor cannot enumerate other
visitors' job metadata inside the shared website tenant.

WHAT IS DELIBERATELY NOT SELECTED
---------------------------------
``request_json`` and ``result_json`` are never read: a document payload can be
megabytes and a result envelope is the job of ``GET /v1/results/{id}``. The
list answers only ``result_json IS NOT NULL`` (server-side, as a boolean), so
``result_available`` is a projection of stored truth rather than a guess.

PAGINATION
----------
Keyset (cursor) pagination over the STABLE order
``created_at DESC, job_id DESC`` — never OFFSET, so a page cannot skip or
duplicate a job while new work is being submitted. ``job_id`` is a random
24-hex token, which makes it a total-order tie-breaker even when several jobs
share one ``created_at`` (they do: jobs admitted in the same transaction get
the same ``now()``).

The cursor is an opaque, URL-safe base64 document containing the last row's
``created_at`` and ``job_id``. It carries no authority: it only positions the
next page inside the caller's own tenant, so forging one cannot widen access.
Cursors do not expire. A malformed cursor is rejected explicitly (400
``INVALID_CURSOR``) instead of being silently ignored, because silently
starting from the first page would make a paginating client loop forever.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("platrixa.api")

#: Bounded page sizes. The default keeps a job-history screen's first paint
#: small; the hard maximum bounds one request's row count and JSON size.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

#: A cursor is a tiny base64 JSON document; anything larger is malformed.
CURSOR_MAX_LENGTH = 512
CURSOR_VERSION = 1

#: ``async_jobs.new_job_id`` — "job_" + 24 hex characters.
JOB_ID_RE = re.compile(r"^job_[0-9a-f]{24}$")

#: The internal job states a developer may filter on. This is the complete
#: stored vocabulary (``backend/auth/async_jobs.py``), not a new taxonomy.
FILTERABLE_STATUSES = (
    "QUEUED",
    "PROCESSING",
    "RETRY_WAIT",
    "COMPLETED",
    "FAILED",
)
_PENDING_STATUSES = ("QUEUED", "PROCESSING", "RETRY_WAIT")
_TERMINAL_STATUSES = ("COMPLETED", "FAILED")


class JobHistoryError(Exception):
    """A request the listing refuses, with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CursorError(JobHistoryError):
    """The cursor is malformed or unreadable."""

    def __init__(self, message: str) -> None:
        super().__init__("INVALID_CURSOR", message)


class StatusFilterError(JobHistoryError):
    """The status filter is not one of the stored job states."""

    def __init__(self, message: str) -> None:
        super().__init__("INVALID_STATUS_FILTER", message)


# ---------------------------------------------------------------------------
# Cursor codec (opaque, self-describing, validated on the way in)
# ---------------------------------------------------------------------------


def encode_cursor(created_at: datetime, job_id: str) -> str:
    """Encode the last row's position as an opaque URL-safe cursor."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    payload = {
        "v": CURSOR_VERSION,
        "c": created_at.isoformat(),
        "j": job_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Tuple[datetime, str]:
    """Decode a cursor, or raise :class:`CursorError`.

    Validation is deliberately strict (version, timestamp, job-id shape) so a
    truncated or hand-edited cursor fails loudly rather than paging from an
    arbitrary position.
    """
    text_value = (cursor or "").strip()
    if not text_value:
        raise CursorError("cursor is empty")
    if len(text_value) > CURSOR_MAX_LENGTH:
        raise CursorError("cursor is too long")
    padded = text_value + "=" * (-len(text_value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise CursorError("cursor is not valid base64url") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CursorError("cursor payload is not valid JSON") from None
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise CursorError("cursor version is not supported")
    created_raw = payload.get("c")
    job_id = payload.get("j")
    if not isinstance(created_raw, str) or not isinstance(job_id, str):
        raise CursorError("cursor is missing its position")
    if not JOB_ID_RE.match(job_id):
        raise CursorError("cursor job id is malformed")
    try:
        created_at = datetime.fromisoformat(created_raw)
    except ValueError:
        raise CursorError("cursor timestamp is malformed") from None
    if created_at.tzinfo is None:
        raise CursorError("cursor timestamp must be timezone-aware")
    return created_at, job_id


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _reason_code(row: Any) -> Optional[str]:
    """The stored error code of a failed job — never a stored payload.

    ``error_json`` is the deterministic error envelope (a few hundred bytes at
    most). Only ``error.code`` leaves this function.
    """
    raw = row["error_json"]
    if not raw:
        return None
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(envelope, dict):
        return None
    error = envelope.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return str(code) if isinstance(code, str) else None


def _status_label(status: str, reason_code: Optional[str]) -> str:
    """The public six-state label for a job state (transport projection only)."""
    from api.status import (
        LABEL_BY_PUBLIC_STATUS,
        STATUS_FAILED as STATUS_FAILED_PUBLIC,
        STATUS_PROCESSING,
        public_status_for_error_code,
    )

    if status in _PENDING_STATUSES:
        return LABEL_BY_PUBLIC_STATUS.get(STATUS_PROCESSING, "Processing")
    if status == "FAILED":
        mapped = (
            public_status_for_error_code(reason_code)
            if reason_code
            else STATUS_FAILED_PUBLIC
        )
        return LABEL_BY_PUBLIC_STATUS.get(mapped, "Failed")
    # COMPLETED: the authoritative api_status lives in the result envelope
    # (VERIFIED / REVIEW_REQUIRED / UNSUPPORTED / ...). This list does not
    # re-read result payloads, so it makes no claim beyond "completed".
    return "Completed"


def _api_status(status: str, reason_code: Optional[str]) -> Optional[str]:
    """The public six-state status, or None when only the envelope knows it."""
    from api.status import (
        STATUS_FAILED as STATUS_FAILED_PUBLIC,
        STATUS_PROCESSING,
        public_status_for_error_code,
    )

    if status in _PENDING_STATUSES:
        return STATUS_PROCESSING
    if status == "FAILED":
        return public_status_for_error_code(reason_code) if reason_code else STATUS_FAILED_PUBLIC
    return None


def _job_entry(row: Any) -> Dict[str, Any]:
    """One job-history entry: metadata only, bounded, no payloads."""
    status = str(row["status"])
    reason_code = _reason_code(row)
    result_available = bool(row["result_available"])
    result_url = f"/v1/results/{row['result_id']}" if result_available else None
    batch_mode = str(row["batch_mode"] or "single")
    transaction_count = int(row["transaction_count"] or 1)
    completed_at = _iso(row["updated_at"]) if status in _TERMINAL_STATUSES else None

    entry: Dict[str, Any] = {
        "job_id": str(row["job_id"]),
        "status": status,
        "status_label": _status_label(status, reason_code),
        "api_status": _api_status(status, reason_code),
        "retryable": status in _PENDING_STATUSES
        or (status == "FAILED" and bool(row["retryable"])),
        "kind": str(row["kind"] or "inference"),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "completed_at": completed_at,
        "transaction_count": transaction_count,
        "batch": (
            {"mode": batch_mode, "transaction_count": transaction_count}
            if batch_mode != "single"
            else None
        ),
        "result_available": result_available,
        "result_url": result_url,
        "reason_code": reason_code if status == "FAILED" else None,
        "request_id": row["request_id"],
    }
    return entry


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------

_SELECT = (
    "SELECT job_id, status, kind, batch_mode, transaction_count, result_id, "
    "       (result_json IS NOT NULL) AS result_available, "
    "       retryable, error_json, request_id, created_at, updated_at "
    "FROM platrixa_async_jobs "
)


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    value = str(status).strip().upper()
    if not value:
        return None
    if value not in FILTERABLE_STATUSES:
        raise StatusFilterError(
            "status must be one of " + ", ".join(FILTERABLE_STATUSES)
        )
    return value


def list_jobs(
    tenant_id: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    visitor_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """One page of the tenant's jobs, newest first.

    Returns ``(entries, has_more)``. Raises :class:`CursorError`,
    :class:`StatusFilterError` or
    ``async_jobs.AsyncStoreUnavailableError``.

    ``limit`` is validated by the caller (route) against
    :data:`MAX_PAGE_SIZE`; this function fetches ``limit + 1`` rows so
    ``has_more`` is known without a second COUNT query.
    """
    from sqlalchemy import text

    from backend.auth import async_jobs

    if not tenant_id:
        raise ValueError("tenant_id is required")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError("limit out of bounds")
    normalized_status = _normalize_status(status)

    clauses = ["tenant_id = :t"]
    params: Dict[str, Any] = {"t": tenant_id, "lim": int(limit) + 1}

    if normalized_status is not None:
        clauses.append("status = :status")
        params["status"] = normalized_status
    if kind is not None:
        clauses.append("kind = :kind")
        params["kind"] = kind
    if visitor_id is not None:
        # Mirrors async_jobs.get_job: a visitor scope narrows to that visitor.
        clauses.append("owner_type = 'website_visitor' AND visitor_id = :v")
        params["v"] = visitor_id
    if cursor is not None:
        created_at, job_id = decode_cursor(cursor)
        clauses.append("(created_at, job_id) < (:c_ts, :c_job)")
        params["c_ts"] = created_at
        params["c_job"] = job_id

    query = (
        _SELECT
        + "WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, job_id DESC LIMIT :lim"
    )

    try:
        SessionLocal = async_jobs._session_factory()
        with SessionLocal() as session:
            rows = session.execute(text(query), params).mappings().all()
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("job history read failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    return [_job_entry(row) for row in page], has_more


def next_cursor_for(entries: List[Dict[str, Any]]) -> Optional[str]:
    """The cursor a client echoes to fetch the following page."""
    if not entries:
        return None
    last = entries[-1]
    created_raw = last.get("created_at")
    job_id = last.get("job_id")
    if not isinstance(created_raw, str) or not isinstance(job_id, str):
        return None
    try:
        created_at = datetime.fromisoformat(created_raw)
    except ValueError:
        return None
    return encode_cursor(created_at, job_id)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "FILTERABLE_STATUSES",
    "JobHistoryError",
    "CursorError",
    "StatusFilterError",
    "encode_cursor",
    "decode_cursor",
    "list_jobs",
    "next_cursor_for",
]
