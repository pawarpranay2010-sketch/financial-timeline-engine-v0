"""Platrixa — bounded MULTI-TRANSACTION batch jobs over the durable job plane.

ONE JOB ≠ ONE TRANSACTION
-------------------------
The outbound inference plane was built around one job carrying one
``raw_input``. A developer integrating an accounting application does not
submit one journal line at a time: it imports a day's vouchers. This module
lets ONE durable job carry a bounded list of accounting transactions, while
keeping every property the single-transaction plane already guarantees:

  * the job id is unique per batch; every transaction carries a stable,
    caller-chosen ``transaction_id`` that is unique inside its batch;
  * the WHOLE request is validated before admission (shape, count, bytes,
    duplicate ids) — a malformed batch never creates a job or charges quota;
  * every transaction is validated INDEPENDENTLY by the deterministic kernel;
    a model response can never set VERIFIED;
  * a per-transaction result exists for every transaction, and one
    transaction failing does not turn the others' outcomes into failures;
  * ordered/stateful sequences carry an explicit state hash chain, and a
    dependent transaction never runs on an unapproved state — it is marked
    BLOCKED deterministically, without spending device inference on it.

TWO EXECUTION MODES
-------------------
``independent``
    Transactions are unrelated. The device processes them in order for
    scheduling reasons only; no transaction's result is an input to another.
    All candidates are submitted in ONE fenced result call, so partial
    outcomes are explicit rather than transactional.

``ordered``
    Transactions form an accounting sequence. The worker submits them ONE AT
    A TIME; the server validates each with the kernel, advances the state
    chain only when the transaction is VERIFIED, and returns the approved
    prior state for the next prompt. A transaction that is not VERIFIED
    HALTS the chain: every later transaction is marked ``BLOCKED`` with
    ``BLOCKED_BY_PRIOR_UNVERIFIED_STATE`` and never receives model time.

    Transactions inside one sequence are NEVER parallelised: the device
    processes them sequentially, and the server refuses a submission that
    skips ahead (``BATCH_ORDER_VIOLATION``).

STATE CHAIN
-----------
``state_hash`` is a SHA-256 over a canonical JSON document containing the
prior hash, the transaction's batch index and id, its engine terminal state,
and a digest of the deterministic accounting result. It is computed ONLY for
a VERIFIED transaction, from the kernel's own output — never from model text
and never from a caller-supplied value. ``prior_state_hash`` on the first
transaction is ``null``; every later transaction records the hash it built
on, so the chain is auditable end to end.

BOUNDED EXECUTION
-----------------
A batch cannot monopolise the phone: the transaction count, the request
bytes, the summed input characters and the device wall-clock budget are all
configurable (never hard-coded to one phone's measured speed), and the worker
must stop and report the untouched remainder as ``DEFERRED`` when the budget
is exhausted. Deferral is a partial outcome, not a failure, and carries
``BATCH_BUDGET_EXHAUSTED``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("platrixa.api")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

MODE_INDEPENDENT = "independent"
MODE_ORDERED = "ordered"
BATCH_MODES = (MODE_INDEPENDENT, MODE_ORDERED)
MODE_SINGLE = "single"

#: Per-transaction states. ``PENDING`` is the only non-final one.
TX_PENDING = "PENDING"
TX_VERIFIED = "VERIFIED"
TX_REVIEW_REQUIRED = "REVIEW_REQUIRED"
TX_UNSUPPORTED = "UNSUPPORTED"
TX_INVALID_INPUT = "INVALID_INPUT"
TX_FAILED = "FAILED"
TX_BLOCKED = "BLOCKED"
TX_DEFERRED = "DEFERRED"

#: States produced by an actual kernel evaluation of a submitted candidate.
TX_PROCESSED = (
    TX_VERIFIED,
    TX_REVIEW_REQUIRED,
    TX_UNSUPPORTED,
    TX_INVALID_INPUT,
    TX_FAILED,
)

#: Batch-level outcomes (orthogonal to the six-state API vocabulary).
BATCH_COMPLETE = "COMPLETE"
BATCH_PARTIAL = "PARTIAL"
BATCH_HALTED = "HALTED"

BLOCKED_CODE = "BLOCKED_BY_PRIOR_UNVERIFIED_STATE"
DEFERRED_CODE = "BATCH_BUDGET_EXHAUSTED"
NOT_PROCESSED_CODE = "BATCH_NOT_PROCESSED"
ORDER_VIOLATION_CODE = "BATCH_ORDER_VIOLATION"
BATCH_UNKNOWN_TRANSACTION = "BATCH_TRANSACTION_NOT_FOUND"

# ---------------------------------------------------------------------------
# Configuration (every bound is configurable — no invented phone capacity)
# ---------------------------------------------------------------------------

MAX_TRANSACTIONS_ENV_VAR = "PLATRIXA_BATCH_MAX_TRANSACTIONS"
MAX_BYTES_ENV_VAR = "PLATRIXA_BATCH_MAX_BYTES"
MAX_INPUT_CHARS_ENV_VAR = "PLATRIXA_BATCH_MAX_INPUT_CHARS"
BUDGET_SECONDS_ENV_VAR = "PLATRIXA_BATCH_MAX_SECONDS"

DEFAULT_MAX_TRANSACTIONS = 10
#: Absolute ceiling: configuration may lower the batch bound, never raise it
#: beyond this. It exists so one request can never ask one phone for an
#: unbounded unit of work.
HARD_MAX_TRANSACTIONS = 50
DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_MAX_INPUT_CHARS = 20_000
DEFAULT_BUDGET_SECONDS = 180.0

#: Same per-transaction input contract as POST /v1/process (1..2000 chars).
MAX_TRANSACTION_INPUT_CHARS = 2000
MAX_TRANSACTION_ID_LENGTH = 64
TRANSACTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

MAX_STATE_SUMMARY_CHARS = 1500


class BatchConfigurationError(RuntimeError):
    """Raised at startup when a batch bound is unusable."""


class BatchValidationError(Exception):
    """A batch request was rejected BEFORE admission.

    ``code`` is the machine-readable contract value; ``details`` carries only
    caller-supplied identifiers (never secrets, never another tenant's data).
    """

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class BatchTransaction:
    """One validated transaction inside a batch request."""

    transaction_id: str
    input: str
    index: int


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    try:
        value = int(text, 10)
    except (TypeError, ValueError):
        raise BatchConfigurationError(f"{name} must be an integer, got {text!r}") from None
    if value < minimum or value > maximum:
        raise BatchConfigurationError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise BatchConfigurationError(f"{name} must be a number, got {text!r}") from None
    if value != value or value < minimum or value > maximum:  # NaN-safe
        raise BatchConfigurationError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


_limits_lock = threading.Lock()
_limits_cache: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}


def limits() -> Dict[str, Any]:
    """The effective batch bounds (cached per environment snapshot)."""
    key = (
        os.getenv(MAX_TRANSACTIONS_ENV_VAR) or "",
        os.getenv(MAX_BYTES_ENV_VAR) or "",
        os.getenv(MAX_INPUT_CHARS_ENV_VAR) or "",
        os.getenv(BUDGET_SECONDS_ENV_VAR) or "",
    )
    with _limits_lock:
        cached = _limits_cache.get(key)
    if cached is not None:
        return dict(cached)

    value = {
        "max_transactions": _env_int(
            MAX_TRANSACTIONS_ENV_VAR,
            DEFAULT_MAX_TRANSACTIONS,
            minimum=1,
            maximum=HARD_MAX_TRANSACTIONS,
        ),
        "max_bytes": _env_int(
            MAX_BYTES_ENV_VAR, DEFAULT_MAX_BYTES, minimum=1024, maximum=4 * 1024 * 1024
        ),
        "max_input_chars": _env_int(
            MAX_INPUT_CHARS_ENV_VAR,
            DEFAULT_MAX_INPUT_CHARS,
            minimum=MAX_TRANSACTION_INPUT_CHARS,
            maximum=1_000_000,
        ),
        "budget_seconds": _env_float(
            BUDGET_SECONDS_ENV_VAR, DEFAULT_BUDGET_SECONDS, minimum=1.0, maximum=3600.0
        ),
    }
    with _limits_lock:
        _limits_cache[key] = dict(value)
    return value


def validate_config() -> Dict[str, Any]:
    """Fail-fast startup validation used by ``api.main.create_app``."""
    return limits()


def reset_cache() -> None:
    with _limits_lock:
        _limits_cache.clear()


# ---------------------------------------------------------------------------
# Request validation (BEFORE admission)
# ---------------------------------------------------------------------------


def is_batch_request(body: Dict[str, Any]) -> bool:
    """True when the request carries a ``transactions`` list.

    The presence of the key — not a separate route — is the switch, so the
    existing single-transaction body keeps its exact meaning and a client
    that never heard of batches is unaffected.
    """
    return isinstance(body, dict) and "transactions" in body


def validate_request(
    body: Dict[str, Any], *, body_bytes: Optional[int] = None
) -> Tuple[str, List[BatchTransaction]]:
    """Validate a whole batch and return ``(mode, transactions)``.

    Raises :class:`BatchValidationError` for every rejection, with a code the
    developer can branch on. Nothing is admitted, stored or charged by this
    function.
    """
    bounds = limits()

    if body_bytes is not None and int(body_bytes) > int(bounds["max_bytes"]):
        raise BatchValidationError(
            "BATCH_TOO_LARGE",
            f"batch request exceeds {bounds['max_bytes']} bytes",
            {"max_bytes": bounds["max_bytes"]},
        )

    if "raw_input" in body:
        # Ambiguous shape: the caller must choose one contract, not have the
        # server guess which field is authoritative.
        raise BatchValidationError(
            "BATCH_AMBIGUOUS",
            "send either raw_input (one transaction) or transactions (a batch), never both",
        )

    mode_raw = body.get("mode", MODE_INDEPENDENT)
    if not isinstance(mode_raw, str):
        raise BatchValidationError("BATCH_MODE_INVALID", "mode must be a string")
    mode = mode_raw.strip().lower()
    if mode not in BATCH_MODES:
        raise BatchValidationError(
            "BATCH_MODE_INVALID",
            "mode must be 'independent' or 'ordered'",
            {"allowed": list(BATCH_MODES)},
        )

    raw_transactions = body.get("transactions")
    if not isinstance(raw_transactions, list):
        raise BatchValidationError("BATCH_MALFORMED", "transactions must be a list")
    if not raw_transactions:
        raise BatchValidationError("BATCH_EMPTY", "a batch must contain at least one transaction")

    max_transactions = int(bounds["max_transactions"])
    if len(raw_transactions) > max_transactions:
        raise BatchValidationError(
            "BATCH_TOO_MANY_TRANSACTIONS",
            f"a batch may contain at most {max_transactions} transactions",
            {"max_transactions": max_transactions, "received": len(raw_transactions)},
        )

    transactions: List[BatchTransaction] = []
    seen: Dict[str, int] = {}
    duplicates: List[str] = []
    total_chars = 0

    for index, item in enumerate(raw_transactions):
        if not isinstance(item, dict):
            raise BatchValidationError(
                "BATCH_TRANSACTION_INVALID",
                f"transactions[{index}] must be an object",
                {"index": index},
            )

        tx_id = item.get("transaction_id")
        if not isinstance(tx_id, str) or not TRANSACTION_ID_RE.match(tx_id):
            raise BatchValidationError(
                "BATCH_TRANSACTION_INVALID",
                f"transactions[{index}].transaction_id must match "
                f"^[A-Za-z0-9._:-]{{1,{MAX_TRANSACTION_ID_LENGTH}}}$",
                {"index": index},
            )
        if tx_id in seen:
            if tx_id not in duplicates:
                duplicates.append(tx_id)
        else:
            seen[tx_id] = index

        tx_input = item.get("input")
        if not isinstance(tx_input, str) or not tx_input.strip():
            raise BatchValidationError(
                "BATCH_TRANSACTION_INVALID",
                f"transactions[{index}].input must be a non-empty string",
                {"index": index, "transaction_id": tx_id},
            )
        if len(tx_input) > MAX_TRANSACTION_INPUT_CHARS:
            raise BatchValidationError(
                "BATCH_TRANSACTION_INVALID",
                f"transactions[{index}].input exceeds {MAX_TRANSACTION_INPUT_CHARS} characters",
                {"index": index, "transaction_id": tx_id},
            )
        total_chars += len(tx_input)

        transactions.append(
            BatchTransaction(transaction_id=tx_id, input=tx_input, index=index)
        )

    if duplicates:
        # Deterministic, bounded: the FIRST occurrence is kept; every repeat is
        # named so the caller can fix its own payload.
        raise BatchValidationError(
            "BATCH_DUPLICATE_TRANSACTION_ID",
            "transaction_id must be unique within a batch",
            {"duplicate_transaction_ids": duplicates[:20]},
        )

    if total_chars > int(bounds["max_input_chars"]):
        raise BatchValidationError(
            "BATCH_INPUT_TOO_LARGE",
            f"the batch's total input exceeds {bounds['max_input_chars']} characters",
            {
                "max_input_chars": bounds["max_input_chars"],
                "received": total_chars,
            },
        )

    return mode, transactions


def job_payload(mode: str, transactions: List[BatchTransaction]) -> Dict[str, Any]:
    """The durable payload stored on the job row for a batch.

    The stored text is the SERVER's admission-time copy of the caller's input;
    every later validation reads the input from here, never from a submission.
    """
    return {
        "mode": mode,
        "transactions": [
            {"transaction_id": tx.transaction_id, "input": tx.input}
            for tx in transactions
        ],
    }


def stored_transactions(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index a stored batch payload by ``transaction_id`` (insertion ordered)."""
    out: Dict[str, Dict[str, Any]] = {}
    raw = payload.get("transactions")
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            tx_id = item.get("transaction_id")
            if isinstance(tx_id, str) and tx_id not in out:
                out[tx_id] = item
    return out


# ---------------------------------------------------------------------------
# Per-transaction rows
# ---------------------------------------------------------------------------


@dataclass
class BatchTransactionRecord:
    job_id: str
    tenant_id: str
    batch_index: int
    transaction_id: str
    status: str
    api_status: Optional[str] = None
    engine_status: Optional[str] = None
    error_code: Optional[str] = None
    prior_state_hash: Optional[str] = None
    state_hash: Optional[str] = None
    state_summary: Optional[str] = None
    result_json: Optional[str] = None
    http_status: Optional[int] = None


def _row_to_transaction(row: Any) -> BatchTransactionRecord:
    return BatchTransactionRecord(
        job_id=str(row["job_id"]),
        tenant_id=str(row["tenant_id"]),
        batch_index=int(row["batch_index"]),
        transaction_id=str(row["transaction_id"]),
        status=str(row["status"]),
        api_status=row["api_status"],
        engine_status=row["engine_status"],
        error_code=row["error_code"],
        prior_state_hash=row["prior_state_hash"],
        state_hash=row["state_hash"],
        state_summary=row["state_summary"],
        result_json=row["result_json"],
        http_status=row["http_status"],
    )


_TX_COLUMNS = (
    "job_id, tenant_id, batch_index, transaction_id, status, api_status, "
    "engine_status, error_code, prior_state_hash, state_hash, state_summary, "
    "result_json, http_status"
)

#: The single, atomic lease fence every per-transaction write carries. It is
#: the SAME rule the job commit enforces (owner + generation + unexpired
#: lease, judged by the SERVER's clock), so a stale worker cannot write a
#: transaction row any more than it can commit a result.
_FENCE_SQL = (
    " AND EXISTS (SELECT 1 FROM platrixa_async_jobs j "
    "WHERE j.job_id = :j AND j.status = 'PROCESSING' "
    "  AND j.lease_owner = :o AND j.lease_generation = :g "
    "  AND j.lease_expires_at IS NOT NULL "
    "  AND j.lease_expires_at > clock_timestamp())"
)


def _require_proof(owner: Optional[str], generation: Optional[int]) -> None:
    if not owner or generation is None:
        raise ValueError("a lease ownership proof (owner + generation) is required")


def rows_for_job(tenant_id: str, job_id: str) -> List[BatchTransactionRecord]:
    """Every transaction row for ONE tenant's job, in batch order."""
    from sqlalchemy import text

    from backend.auth import async_jobs

    SessionLocal = async_jobs._session_factory()
    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT " + _TX_COLUMNS + " FROM platrixa_batch_transactions "
                    "WHERE tenant_id = :t AND job_id = :j ORDER BY batch_index"
                ),
                {"t": tenant_id, "j": job_id},
            ).mappings().all()
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("batch transaction read failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc
    return [_row_to_transaction(row) for row in rows]


def get_transaction(
    tenant_id: str, job_id: str, transaction_id: str
) -> Optional[BatchTransactionRecord]:
    """ONE transaction row, tenant-scoped (a foreign id is indistinguishable
    from a nonexistent one)."""
    from sqlalchemy import text

    from backend.auth import async_jobs

    SessionLocal = async_jobs._session_factory()
    try:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT " + _TX_COLUMNS + " FROM platrixa_batch_transactions "
                    "WHERE tenant_id = :t AND job_id = :j AND transaction_id = :tx"
                ),
                {"t": tenant_id, "j": job_id, "tx": transaction_id},
            ).mappings().first()
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("batch transaction read failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc
    return _row_to_transaction(row) if row else None


def pending_rows(rows: List[BatchTransactionRecord]) -> List[BatchTransactionRecord]:
    return [row for row in rows if row.status == TX_PENDING]


def next_pending(rows: List[BatchTransactionRecord]) -> Optional[BatchTransactionRecord]:
    for row in rows:
        if row.status == TX_PENDING:
            return row
    return None


def last_state_hash(rows: List[BatchTransactionRecord]) -> Optional[str]:
    latest: Optional[str] = None
    for row in rows:
        if row.state_hash:
            latest = row.state_hash
    return latest


def record_transaction(
    *,
    tenant_id: str,
    job_id: str,
    transaction_id: str,
    status: str,
    api_status: Optional[str] = None,
    engine_status: Optional[str] = None,
    error_code: Optional[str] = None,
    prior_state_hash: Optional[str] = None,
    state_hash: Optional[str] = None,
    state_summary: Optional[str] = None,
    envelope: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
    lease_owner: str,
    lease_generation: int,
) -> bool:
    """Persist ONE transaction's outcome under the job's lease fence.

    Only a ``PENDING`` row is written: a processed outcome is never
    overwritten, so a duplicate submission, a reclaimed job or two racing
    writes cannot change what a transaction already means. Returns False when
    the fence refused the write (or the row was already final).
    """
    from sqlalchemy import text

    from backend.auth import async_jobs

    _require_proof(lease_owner, lease_generation)

    result_json = (
        json.dumps(envelope, ensure_ascii=False) if envelope is not None else None
    )
    SessionLocal = async_jobs._session_factory()
    try:
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_batch_transactions AS t SET "
                        "  status = :st, api_status = :api, engine_status = :eng, "
                        "  error_code = :code, prior_state_hash = :prior, "
                        "  state_hash = :hash, state_summary = :summary, "
                        "  result_json = :res, http_status = :http, updated_at = now() "
                        "WHERE t.job_id = :j AND t.transaction_id = :tx "
                        "  AND t.tenant_id = :t AND t.status = 'PENDING'"
                        + _FENCE_SQL
                    ),
                    {
                        "st": status,
                        "api": api_status,
                        "eng": engine_status,
                        "code": error_code,
                        "prior": prior_state_hash,
                        "hash": state_hash,
                        "summary": state_summary,
                        "res": result_json,
                        "http": http_status,
                        "j": job_id,
                        "tx": transaction_id,
                        "t": tenant_id,
                        "o": lease_owner,
                        "g": int(lease_generation),
                    },
                )
                return (result.rowcount or 0) == 1
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("batch transaction write failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc


def mark_pending(
    *,
    tenant_id: str,
    job_id: str,
    from_index: int,
    status: str,
    error_code: str,
    lease_owner: str,
    lease_generation: int,
) -> int:
    """Mark every still-PENDING transaction from ``from_index`` onward.

    Used for two deterministic, non-model outcomes: a halted ordered chain
    (``BLOCKED``) and a device budget that ran out (``DEFERRED``). Neither is
    a failure verdict invented by the transport — they name exactly what
    happened to work that was admitted and never evaluated.
    """
    from sqlalchemy import text

    from backend.auth import async_jobs

    _require_proof(lease_owner, lease_generation)

    SessionLocal = async_jobs._session_factory()
    try:
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_batch_transactions AS t SET "
                        "  status = :st, error_code = :code, updated_at = now() "
                        "WHERE t.job_id = :j AND t.tenant_id = :t "
                        "  AND t.batch_index >= :i AND t.status = 'PENDING'"
                        + _FENCE_SQL
                    ),
                    {
                        "st": status,
                        "code": error_code,
                        "j": job_id,
                        "t": tenant_id,
                        "i": int(from_index),
                        "o": lease_owner,
                        "g": int(lease_generation),
                    },
                )
                return int(result.rowcount or 0)
    except async_jobs.AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("batch transaction bulk write failed: %s", type(exc).__name__)
        raise async_jobs.AsyncStoreUnavailableError("async store unavailable") from exc


# ---------------------------------------------------------------------------
# Deterministic state chain
# ---------------------------------------------------------------------------


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def state_hash_for(
    *,
    prior_state_hash: Optional[str],
    index: int,
    transaction_id: str,
    envelope: Dict[str, Any],
) -> Optional[str]:
    """The next state hash, or None when this transaction did not advance state.

    ONLY a VERIFIED transaction advances the chain. The digest is built from
    the deterministic accounting output and the engine's own terminal state —
    never from model text — so an unverified candidate can never become
    trusted accounting state.
    """
    if envelope.get("api_status") != TX_VERIFIED:
        return None
    return _canonical_digest(
        {
            "prior_state_hash": prior_state_hash,
            "batch_index": index,
            "transaction_id": transaction_id,
            "engine_status": envelope.get("status"),
            "api_status": envelope.get("api_status"),
            "accounting_digest": _canonical_digest(envelope.get("accounting")),
        }
    )


def state_summary_for(
    *,
    transaction_id: str,
    state_hash: str,
    envelope: Dict[str, Any],
) -> str:
    """A bounded, deterministic context string for the NEXT prompt.

    It carries the approved accounting output and the hash it belongs to. The
    text is server-generated from kernel output; the next transaction still
    has to be validated on its own, so including it cannot upgrade anything.
    """
    accounting = envelope.get("accounting")
    summary = {
        "approved_transaction_id": transaction_id,
        "state_hash": state_hash,
        "engine_status": envelope.get("status"),
        "accounting": accounting,
    }
    encoded = json.dumps(
        summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    if len(encoded) <= MAX_STATE_SUMMARY_CHARS:
        return encoded

    # Too large for a prompt: keep the CONTEXT VALID JSON. A truncated string
    # would be unparseable, and the next link would receive no approved state at
    # all. The full accounting is still committed by ``state_hash`` above, so
    # the digest is a faithful, bounded stand-in.
    summary = {
        "approved_transaction_id": transaction_id,
        "state_hash": state_hash,
        "engine_status": envelope.get("status"),
        "accounting_digest": _canonical_digest(accounting),
        "note": "accounting omitted for size; state_hash commits to it",
    }
    encoded = json.dumps(
        summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    if len(encoded) <= MAX_STATE_SUMMARY_CHARS:
        return encoded
    return json.dumps(
        {
            "approved_transaction_id": transaction_id[:64],
            "state_hash": state_hash,
            "accounting_digest": _canonical_digest(accounting),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# Result projection
# ---------------------------------------------------------------------------


def transaction_summary(record: BatchTransactionRecord, result_id: str) -> Dict[str, Any]:
    """One compact per-transaction entry (the full envelope is fetched by URL)."""
    from api.status import LABEL_BY_PUBLIC_STATUS

    url: Optional[str] = None
    if record.result_json:
        url = f"/v1/results/{result_id}/transactions/{record.transaction_id}"
    api_status = record.api_status
    return {
        "batch_index": record.batch_index,
        "transaction_id": record.transaction_id,
        "status": record.status,
        "api_status": api_status,
        "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status or "", "") or None,
        "engine_status": record.engine_status,
        "error_code": record.error_code,
        "http_status": record.http_status,
        "prior_state_hash": record.prior_state_hash,
        "state_hash": record.state_hash,
        "result_url": url,
    }


def _aggregate(rows: List[BatchTransactionRecord]) -> Tuple[str, Optional[str], List[str]]:
    """Deterministic batch aggregate: ``(api_status, engine_status, reasons)``.

    Rule (documented and order-stable): the aggregate is VERIFIED only when
    EVERY processed transaction is VERIFIED; otherwise it is the FIRST
    non-VERIFIED transaction in batch order, carried verbatim. When nothing
    was processed at all (every transaction deferred), the batch reports
    FAILED with ``BATCH_NOT_PROCESSED`` and is retryable — it produced no
    outcome to report.
    """
    processed = [row for row in rows if row.status in TX_PROCESSED]
    reasons: List[str] = []
    if not processed:
        return TX_FAILED, None, [NOT_PROCESSED_CODE]
    for row in processed:
        if row.status != TX_VERIFIED:
            if row.error_code:
                reasons.append(row.error_code)
            return row.status, row.engine_status, reasons
    return TX_VERIFIED, processed[-1].engine_status if processed else None, reasons


def build_envelope(
    *,
    job_id: str,
    result_id: str,
    request_id: Optional[str],
    mode: str,
    rows: List[BatchTransactionRecord],
    created_at: Optional[Any] = None,
    completed_at: Optional[Any] = None,
) -> Dict[str, Any]:
    """The batch result envelope stored on the job and served by the API."""
    from api.status import LABEL_BY_PUBLIC_STATUS, RETRYABLE_BY_PUBLIC_STATUS

    api_status, engine_status, reasons = _aggregate(rows)
    counts = {
        "total": len(rows),
        "processed": 0,
        TX_VERIFIED.lower(): 0,
        TX_REVIEW_REQUIRED.lower(): 0,
        TX_UNSUPPORTED.lower(): 0,
        TX_INVALID_INPUT.lower(): 0,
        TX_FAILED.lower(): 0,
        TX_BLOCKED.lower(): 0,
        TX_DEFERRED.lower(): 0,
        TX_PENDING.lower(): 0,
    }
    for row in rows:
        if row.status in TX_PROCESSED:
            counts["processed"] += 1
        key = row.status.lower()
        if key in counts:
            counts[key] += 1

    if counts[TX_DEFERRED.lower()]:
        batch_status = BATCH_PARTIAL
    elif counts[TX_BLOCKED.lower()]:
        batch_status = BATCH_HALTED
    else:
        batch_status = BATCH_COMPLETE

    processed_rows = [row for row in rows if row.status in TX_PROCESSED]
    success = (
        batch_status == BATCH_COMPLETE
        and len(processed_rows) == len(rows)
        and all(row.status == TX_VERIFIED for row in processed_rows)
    )
    retryable = bool(RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False))
    if api_status == TX_FAILED and NOT_PROCESSED_CODE in reasons:
        # Nothing was evaluated: resubmitting the same batch is meaningful.
        retryable = True

    chain = [
        {
            "batch_index": row.batch_index,
            "transaction_id": row.transaction_id,
            "prior_state_hash": row.prior_state_hash,
            "state_hash": row.state_hash,
        }
        for row in rows
        if row.state_hash
    ]

    return {
        "api_version": "v1",
        "request_id": request_id,
        "job_id": job_id,
        "batch_id": job_id,
        "result_id": result_id,
        "mode": mode,
        "batch_status": batch_status,
        "status": engine_status or api_status,
        "api_status": api_status,
        "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
        "engine_status": engine_status,
        "success": success,
        "retryable": retryable,
        "reason_codes": reasons,
        "total_transactions": len(rows),
        "processed_transactions": counts["processed"],
        "verified_count": counts[TX_VERIFIED.lower()],
        "review_required_count": counts[TX_REVIEW_REQUIRED.lower()],
        "unsupported_count": counts[TX_UNSUPPORTED.lower()],
        "failed_count": counts[TX_FAILED.lower()],
        "blocked_count": counts[TX_BLOCKED.lower()],
        "deferred_count": counts[TX_DEFERRED.lower()],
        "counts": counts,
        "state_chain": chain,
        "final_state_hash": last_state_hash(rows),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "completed_at": (
            completed_at.isoformat()
            if hasattr(completed_at, "isoformat")
            else (completed_at or datetime.now(timezone.utc).isoformat())
        ),
        "transactions": [transaction_summary(row, result_id) for row in rows],
        "quota": {"transactions_admitted": len(rows)},
        "metadata": {"authority": "deterministic-kernel", "mode": mode},
    }


def finalize(
    record: Any, *, lease_owner: str, lease_generation: int
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Build the batch envelope and commit the job in ONE fenced statement.

    ``record`` is the claimed ``async_jobs.JobRecord`` (its tenant/job/result
    ids are server-derived). Returns ``(accepted, envelope)``; ``accepted``
    False means the lease was lost or expired at the commit point, and
    NOTHING was stored.
    """
    from backend.auth import async_jobs

    rows = rows_for_job(record.tenant_id, record.job_id)
    envelope = build_envelope(
        job_id=record.job_id,
        result_id=record.result_id,
        request_id=record.request_id,
        mode=str(getattr(record, "batch_mode", MODE_INDEPENDENT) or MODE_INDEPENDENT),
        rows=rows,
        created_at=record.created_at,
        completed_at=datetime.now(timezone.utc),
    )
    accepted = async_jobs.complete_job(
        record.job_id,
        envelope=envelope,
        http_status=200,
        request_id=envelope.get("request_id") or record.request_id,
        lease_owner=lease_owner,
        lease_generation=lease_generation,
    )
    return accepted, (envelope if accepted else None)


__all__ = [
    "MODE_INDEPENDENT",
    "MODE_ORDERED",
    "MODE_SINGLE",
    "BATCH_MODES",
    "TX_PENDING",
    "TX_VERIFIED",
    "TX_REVIEW_REQUIRED",
    "TX_UNSUPPORTED",
    "TX_INVALID_INPUT",
    "TX_FAILED",
    "TX_BLOCKED",
    "TX_DEFERRED",
    "TX_PROCESSED",
    "BATCH_COMPLETE",
    "BATCH_PARTIAL",
    "BATCH_HALTED",
    "BLOCKED_CODE",
    "DEFERRED_CODE",
    "NOT_PROCESSED_CODE",
    "ORDER_VIOLATION_CODE",
    "BATCH_UNKNOWN_TRANSACTION",
    "BatchConfigurationError",
    "BatchValidationError",
    "BatchTransaction",
    "BatchTransactionRecord",
    "limits",
    "validate_config",
    "reset_cache",
    "is_batch_request",
    "validate_request",
    "job_payload",
    "stored_transactions",
    "rows_for_job",
    "get_transaction",
    "pending_rows",
    "next_pending",
    "last_state_hash",
    "record_transaction",
    "mark_pending",
    "state_hash_for",
    "state_summary_for",
    "transaction_summary",
    "build_envelope",
    "finalize",
]
