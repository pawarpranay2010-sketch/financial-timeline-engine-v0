"""Durable async document jobs + webhook endpoint registry (Phase 5E).

Storage lives in the SAME metering PostgreSQL store as the Phase 16 gate
and the Phase 5C idempotency records — one durable store for the whole
developer API, no second infrastructure.

Job lifecycle (all states are real engine outcomes; completion is never
equated to VERIFIED):

    POST /v1/documents            GET /v1/jobs/{job_id}
        ↓ 202 (job created,         ↓ QUEUED / PROCESSING / COMPLETED
          quota reserved)             / FAILED (verbatim engine state)
        ↓
    in-process worker: claim_next_job (FOR UPDATE SKIP LOCKED + lease)
        ↓
    EXISTING document pipeline (DocumentProcessor → Kernel) — reused,
    never replaced; the worker adds no financial logic
        ↓
    COMPLETED (5D envelope stored verbatim, retrievable via
    GET /v1/results/{result_id}) or FAILED (error envelope + retryable).

Durability contract (honest):
  * Jobs live in PostgreSQL — a process restart never erases a submitted
    job. QUEUED jobs are picked up again; a job whose worker died
    mid-flight is recovered after its lease expires and reprocessed.
  * Delivery of results is via polling; no distributed queue exists in
    this deployment, so webhook delivery is a best-effort in-process
    foundation (single attempt, signed, documented) — NOT an
    at-least-once production guarantee.
  * Exactly-once execution is NOT promised (same as Phase 5C).

Security boundary (mirrors backend.auth.gate / idempotency):
  * Job rows are tenant-scoped; every read path filters by tenant_id, so
    no cross-tenant read exists.
  * Webhook secrets are stored SHA-256-hashed only; the plaintext secret
    is returned exactly once at registration and never logged or stored.
  * No financial logic in this module — it never interprets a document.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("platrixa.api")

# Worker lease: a PROCESSING job whose lease expired is considered
# orphaned (worker crashed) and is claimed again for reprocessing.
JOB_LEASE_SECONDS = 120

# Phase 5K retry policy (5K §10). THESE ARE ENGINEERING DEFAULTS, NOT
# MEASURED PRODUCTION VALUES — they must be tuned against real traffic
# before being treated as capacity numbers.
#
#   initial delay  : RETRY_BASE_DELAY_SECONDS
#   backoff        : exponential, RETRY_BACKOFF_FACTOR per attempt
#   maximum delay  : RETRY_MAX_DELAY_SECONDS (ceiling)
#   max attempts   : RETRY_MAX_ATTEMPTS (per job, stored in the row)
#
# Deliberately conservative: a deterministic user-input failure is NEVER
# retried, so the budget only ever applies to infrastructure conditions.
RETRY_BASE_DELAY_SECONDS = 5
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY_SECONDS = 300
RETRY_MAX_ATTEMPTS = 3

# Lease renewal cadence: renew at a third of the lease, so two
# consecutive renewal failures are still absorbed before expiry.
LEASE_RENEW_FRACTION = 3.0


def retry_backoff_seconds(attempt: int) -> int:
    """Deterministic bounded exponential backoff for a 1-based attempt."""
    try:
        n = max(1, int(attempt))
    except (TypeError, ValueError):
        n = 1
    delay = RETRY_BASE_DELAY_SECONDS * (RETRY_BACKOFF_FACTOR ** (n - 1))
    return int(min(delay, RETRY_MAX_DELAY_SECONDS))


def classify_failure(*, error_code: Optional[str], api_status: Optional[str]) -> bool:
    """Is this failure worth retrying? (5K §9)

    Deterministic and explicit — NEVER inferred from an HTTP status alone.
    The rule is: a failure is retryable ONLY when the outcome is still
    unknown because an infrastructure dependency was unavailable. A
    deterministic user-input outcome (invalid input, unsupported
    capability, grounding/schema rejection, authentication failure) is
    permanently NOT retryable, because retrying it can only ever produce
    the same answer.

    Reuses the existing Platrixa status/reason-code vocabulary
    (``api.status``) rather than inventing a parallel scheme.
    """
    from api.status import STATUS_INVALID_INPUT, STATUS_UNSUPPORTED

    # Permanent: the request itself is the problem.
    if api_status in (STATUS_INVALID_INPUT, STATUS_UNSUPPORTED):
        return False
    if error_code in PERMANENT_ERROR_CODES:
        return False
    # Infrastructure: the outcome is genuinely unknown right now.
    if error_code in RETRYABLE_ERROR_CODES:
        return True
    # Unknown code: fail safe toward NOT retrying, so a new deterministic
    # rejection can never spin in a retry loop.
    return False


# Error codes that mean "infrastructure was unavailable" — the job's
# outcome is unknown and a later attempt may genuinely differ.
RETRYABLE_ERROR_CODES = frozenset({
    "PROVIDER_UNAVAILABLE",
    "MODEL_UNAVAILABLE",
    "METERING_UNAVAILABLE",
    "ASYNC_UNAVAILABLE",
    "INTERNAL_ERROR",
    "RESULT_PENDING",
})

# Error codes that are permanently the caller's fault.
PERMANENT_ERROR_CODES = frozenset({
    "INPUT_INVALID",
    "INPUT_MISSING",
    "INPUT_AMBIGUOUS",
    "REQUEST_MALFORMED",
    "REQUEST_TOO_LARGE",
    "FILE_EMPTY",
    "FILE_TYPE_UNSUPPORTED",
    "FILE_TOO_LARGE",
    "FILE_NAME_MISSING",
    "CONTENT_TYPE_UNSUPPORTED",
    "UNAUTHORIZED",
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "BATCH_EMPTY",
    "BATCH_TOO_LARGE",
    "BATCH_ITEM_INVALID",
    "BATCH_ITEM_ID_INVALID",
    "BATCH_DUPLICATE_ITEM_ID",
    "JOB_NOT_FOUND",
    "RESULT_NOT_FOUND",
})

_WORKER_ID: Optional[str] = None


def _default_worker_id() -> str:
    """Stable per-process worker identity used as the lease owner."""
    global _WORKER_ID
    if _WORKER_ID is None:
        _WORKER_ID = f"w_{secrets.token_hex(6)}"
    return _WORKER_ID


def worker_id() -> str:
    """This process's worker identity (stable for the process lifetime)."""
    return _default_worker_id()

# The documented webhook signature scheme (Stripe-style):
#   Platrixa-Signature: t=<unix_seconds>,v1=<hex hmac-sha256>
SIGNATURE_TOLERANCE_SECONDS = 300  # verifier-side replay window

# Event vocabulary (closed; deterministic mapping from terminal status).
EVENT_PROCESSING = "document.processing"
EVENT_COMPLETED = "document.completed"
EVENT_REVIEW_REQUIRED = "document.review_required"
EVENT_UNSUPPORTED = "document.unsupported"
EVENT_FAILED = "document.failed"
ALL_EVENTS = (
    EVENT_PROCESSING,
    EVENT_COMPLETED,
    EVENT_REVIEW_REQUIRED,
    EVENT_UNSUPPORTED,
    EVENT_FAILED,
)

EVENT_BY_API_STATUS = {
    "VERIFIED": EVENT_COMPLETED,
    "REVIEW_REQUIRED": EVENT_REVIEW_REQUIRED,
    "UNSUPPORTED": EVENT_UNSUPPORTED,
    "INVALID_INPUT": EVENT_FAILED,
    "FAILED": EVENT_FAILED,
}


class AsyncStoreUnavailableError(Exception):
    """Async job/webhook store unavailable while operating (fail closed)."""


# Webhook signing secrets are sealed at rest (the worker must recover the
# plaintext to sign deliveries, so a one-way hash is not enough). The
# sealing key comes from server configuration ONLY:
#   PLATRIXA_WEBHOOK_SIGNING_KEY  (required to register endpoints)
# Scheme: SHA-256-CTR stream seal — keystream blocks are
#   SHA256(key || nonce || counter_be32)
# XORed with the plaintext; nonce is per-record CSPRNG. This is a
# stdlib-only authenticated-confidentiality-*partial* design (integrity
# is provided by the DB trust boundary, confidentiality by the seal);
# documented honestly in docs/HOSTED_API.md.
WEBHOOK_SEALING_ENV_VAR = "PLATRIXA_WEBHOOK_SIGNING_KEY"


def webhook_signing_configured() -> bool:
    return bool((os.getenv(WEBHOOK_SEALING_ENV_VAR, "") or "").strip())


def _seal_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """LEGACY v1 keystream — RETAINED FOR REFERENCE ONLY.

    Security hardening (audit M-03, 2026-09-29): this SHA256 counter-mode
    XOR keystream is UNAUTHENTICATED. It is malleable — flipping bit n of
    the ciphertext flips bit n of the plaintext — and unsealing detects
    nothing. It is no longer used to protect webhook signing secrets.

    Kept ONLY so the v1 refusal path is readable and testable. Do not use
    it for new data.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(
            key + nonce + counter.to_bytes(4, "big")
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


_SEAL_VERSION = "v2"


def _seal_key() -> bytes:
    """Derive a 32-byte AES key from the configured signing key.

    The configured value is an arbitrary operator string, not a 32-byte
    key, so it is stretched with HKDF-SHA256 into a fixed-length key. HKDF
    is a standard primitive from the ``cryptography`` library; this
    repository does not implement a custom KDF.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = (os.getenv(WEBHOOK_SEALING_ENV_VAR, "") or "").strip()
    if not raw:
        raise AsyncStoreUnavailableError("webhook signing key not configured")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"platrixa-webhook-secret-seal-v2",
    ).derive(raw.encode("utf-8"))


def seal_webhook_secret(plain: str) -> str:
    """
    Seal a webhook signing secret for at-rest storage.

    Uses AES-256-GCM (an AEAD): the ciphertext carries an authentication
    tag, so any modification is detected BEFORE the plaintext is accepted.
    Format: ``v2:<base64url(nonce || ciphertext || tag)>``.

    Replaces the unauthenticated XOR scheme of audit M-03, where a one-bit
    ciphertext edit yielded a well-formed but wrong signing secret.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _seal_key()
    nonce = secrets.token_bytes(12)          # 96-bit nonce, unique per seal
    blob = nonce + AESGCM(key).encrypt(nonce, plain.encode("utf-8"), None)
    return f"{_SEAL_VERSION}:{base64.urlsafe_b64encode(blob).decode('ascii')}"


def unseal_webhook_secret(sealed: str) -> str:
    """
    Recover a sealed webhook signing secret (worker-side signing).

    FAILS CLOSED on a malformed blob, an unsupported version, or a failed
    AEAD authentication tag (tampered ciphertext).

    MIGRATION NOTE (audit M-03): legacy ``v1`` blobs used an unauthenticated
    XOR scheme and therefore cannot be verified. They are REFUSED rather
    than accepted, because accepting them would silently restore the
    malleability this change removes. A v1 webhook secret must be
    re-registered (POST /v1/webhook-endpoints) to obtain a v2 seal. The
    consequence is bounded: webhook DELIVERY is best-effort by design and a
    refused seal is logged, never raised into the job path.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _seal_key()
    try:
        version, payload = sealed.split(":", 1)
    except Exception as exc:
        raise AsyncStoreUnavailableError("webhook secret seal corrupt") from exc

    if version != _SEAL_VERSION:
        if version == "v1":
            raise AsyncStoreUnavailableError(
                "legacy v1 webhook secret seal cannot be authenticated; "
                "re-register the webhook endpoint to migrate to v2"
            )
        raise AsyncStoreUnavailableError("unsupported seal version")

    try:
        blob = base64.urlsafe_b64decode(payload.encode("ascii"))
    except Exception as exc:
        raise AsyncStoreUnavailableError("webhook secret seal corrupt") from exc

    if len(blob) < 12:
        raise AsyncStoreUnavailableError("webhook secret seal corrupt")

    nonce, ct_and_tag = blob[:12], blob[12:]
    try:
        return AESGCM(key).decrypt(nonce, ct_and_tag, None).decode("utf-8")
    except InvalidTag as exc:
        raise AsyncStoreUnavailableError(
            "webhook secret seal failed authentication — ciphertext was "
            "modified or was sealed under a different key"
        ) from exc
    except Exception as exc:
        raise AsyncStoreUnavailableError("webhook secret seal corrupt") from exc


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------





_DDL = """
CREATE TABLE IF NOT EXISTS platrixa_async_jobs (
    job_id        VARCHAR(64)  NOT NULL,
    tenant_id     VARCHAR(64)  NOT NULL,
    result_id     VARCHAR(64)  NOT NULL,
    request_hash  VARCHAR(64)  NOT NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'QUEUED',
    request_json  TEXT         NOT NULL,
    result_json   TEXT,
    http_status   INTEGER,
    error_json    TEXT,
    retryable     BOOLEAN      NOT NULL DEFAULT false,
    request_id    VARCHAR(128),
    lease_expires_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id)
);
CREATE INDEX IF NOT EXISTS idx_async_jobs_tenant ON platrixa_async_jobs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_async_jobs_status ON platrixa_async_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_async_jobs_result ON platrixa_async_jobs (result_id);
CREATE TABLE IF NOT EXISTS platrixa_webhook_endpoints (
    webhook_id    VARCHAR(64) NOT NULL,
    tenant_id     VARCHAR(64) NOT NULL,
    url           TEXT        NOT NULL,
    events        TEXT        NOT NULL,
    secret_sealed TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (webhook_id)
);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_tenant ON platrixa_webhook_endpoints (tenant_id);
CREATE TABLE IF NOT EXISTS platrixa_webhook_deliveries (
    delivery_id  VARCHAR(80) NOT NULL,
    job_id       VARCHAR(64) NOT NULL,
    tenant_id    VARCHAR(64) NOT NULL,
    webhook_id   VARCHAR(64) NOT NULL,
    event        VARCHAR(48) NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER    NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    last_attempt_at  TIMESTAMPTZ,
    last_http_status INTEGER,
    last_error     TEXT,
    delivered_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (delivery_id)
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_pending
    ON platrixa_webhook_deliveries (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_job
    ON platrixa_webhook_deliveries (job_id);
"""

# Phase 5K additive migration. The _DDL above is the fresh-install shape;
# this runs on stores created before 5K and is idempotent, so an upgraded
# deployment converges to the same schema as a fresh one.
#
#   lease_owner      — worker identity that currently owns the job
#   lease_generation — monotonic fencing token, bumped on EVERY claim.
#                     A commit is accepted only when both still match, so
#                     a worker that lost the lease can never publish.
#   attempt_count    — bounded retry budget (5K §10)
#   max_attempts     — the budget itself, stored per job
#   next_attempt_at  — backoff gate; a job is not claimable before this
#
# The result uniqueness constraint (5K §11) is created separately in
# _ensure_result_uniqueness, because building a unique index over an
# existing table fails if duplicates are already present.
_MIGRATION_5K = [
    "ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS "
    "lease_owner VARCHAR(128)",
    "ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS "
    "lease_generation BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS "
    "attempt_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS "
    "max_attempts INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS "
    "next_attempt_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_async_jobs_claimable ON "
    "platrixa_async_jobs (status, next_attempt_at, created_at)",
]

def async_configured() -> bool:
    """Async documents require the durable store (the metering store)."""
    from backend.auth.gate import METERING_ENV_VAR

    return bool((os.getenv(METERING_ENV_VAR, "") or "").strip())


METERING_ENV_VAR = "PLATRIXA_METERING_DATABASE_URL"


def _session_factory():
    """Sessionmaker over the metering store with the schema ensured once.

    Phase 5I (audit C3): this store previously kept its OWN engine cache
    with the same lookup-raw/store-normalized defect ``gate.py`` already
    documented and fixed (2026-09-29 incident: 132 engines under the
    40-thread suite, connection-cap exhaustion, spurious fail-closed
    503s). The async store now delegates to the gate's single canonical,
    locked, normalize-first cache — one engine per process per store
    URL, never a third implementation.
    """
    url = (os.getenv(METERING_ENV_VAR, "") or "").strip()
    if not url:
        raise AsyncStoreUnavailableError("async store not configured")

    from backend.auth.gate import _session_factory as _canonical_factory

    if url not in _schema_ensured:
        from sqlalchemy import create_engine, text

        ddl_url = (
            url.replace("postgresql://", "postgresql+psycopg2://", 1)
            if url.startswith("postgresql://")
            else url
        )
        try:
            engine = create_engine(ddl_url, future=True)
            with engine.begin() as conn:
                conn.execute(text(_DDL))
                for stmt in _MIGRATION_5K:
                    conn.execute(text(stmt))
                _ensure_result_uniqueness(conn)
        except Exception as exc:
            raise AsyncStoreUnavailableError("async store unavailable") from exc
        finally:
            try:
                engine.dispose()
            except Exception:
                pass
        _schema_ensured.add(url)
    try:
        return _canonical_factory()
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc


_schema_ensured: set = set()


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Durable webhook delivery (Phase 5K §13, §14)
#
# Delivery rows reference the canonical persisted job/result — they never
# store a copy of the document or of the financial payload, only delivery
# metadata plus the event identity.
# ---------------------------------------------------------------------------

DELIVERY_PENDING = "PENDING"
DELIVERY_DELIVERED = "DELIVERED"
DELIVERY_RETRYING = "RETRYING"
DELIVERY_FAILED = "FAILED"

# Webhook retry budget — engineering defaults, not measured values.
WEBHOOK_MAX_ATTEMPTS = 5
WEBHOOK_BASE_DELAY_SECONDS = 10
WEBHOOK_BACKOFF_FACTOR = 2
WEBHOOK_MAX_DELAY_SECONDS = 3600
# A Retry-After larger than this is ignored in favour of the computed
# backoff, so a hostile or buggy endpoint cannot park a delivery forever.
WEBHOOK_MAX_RETRY_AFTER_SECONDS = 300


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    job_id: str
    tenant_id: str
    webhook_id: str
    event: str
    status: str
    attempt_count: int
    next_attempt_at: Optional[datetime]
    last_attempt_at: Optional[datetime]
    last_http_status: Optional[int]
    last_error: Optional[str]
    delivered_at: Optional[datetime]


def _delivery_row(row: Any) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=row["delivery_id"],
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        webhook_id=row["webhook_id"],
        event=row["event"],
        status=row["status"],
        attempt_count=int(row["attempt_count"] or 0),
        next_attempt_at=row["next_attempt_at"],
        last_attempt_at=row["last_attempt_at"],
        last_http_status=row["last_http_status"],
        last_error=row["last_error"],
        delivered_at=row["delivered_at"],
    )


def new_delivery_id() -> str:
    return "dlv_" + secrets.token_hex(12)


def webhook_backoff_seconds(attempt: int) -> int:
    try:
        n = max(1, int(attempt))
    except (TypeError, ValueError):
        n = 1
    d = WEBHOOK_BASE_DELAY_SECONDS * (WEBHOOK_BACKOFF_FACTOR ** (n - 1))
    return int(min(d, WEBHOOK_MAX_DELAY_SECONDS))


def record_delivery(
    job_id: str,
    tenant_id: str,
    webhook_id: str,
    event: str,
) -> Optional[DeliveryRecord]:
    """Create (idempotently) one PENDING delivery row.

    The delivery id is DERIVED from (job, webhook, event) rather than
    random, so a retried job — or a duplicated terminal transition —
    cannot create a second delivery for the same subscriber. This is what
    makes at-least-once delivery safe to deduplicate downstream.
    """
    from sqlalchemy import text

    delivery_id = hashlib.sha256(
        f"{job_id}:{webhook_id}:{event}".encode("utf-8")
    ).hexdigest()[:48]
    delivery_id = "dlv_" + delivery_id
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "INSERT INTO platrixa_webhook_deliveries "
                        "(delivery_id, job_id, tenant_id, webhook_id, event, status, "
                        " attempt_count, next_attempt_at, created_at, updated_at) "
                        "VALUES (:d, :j, :t, :w, :e, :st, 0, now(), now(), now()) "
                        "ON CONFLICT (delivery_id) DO NOTHING"
                    ),
                    {"d": delivery_id, "j": job_id, "t": tenant_id,
                     "w": webhook_id, "e": event, "st": DELIVERY_PENDING},
                )
                row = session.execute(
                    text(
                        "SELECT delivery_id, job_id, tenant_id, webhook_id, event, status, "
                        "attempt_count, next_attempt_at, last_attempt_at, last_http_status, "
                        "last_error, delivered_at FROM platrixa_webhook_deliveries "
                        "WHERE delivery_id = :d"
                    ),
                    {"d": delivery_id},
                ).mappings().first()
        return _delivery_row(row) if row else None
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("webhook delivery record failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def mark_delivery_attempt(
    delivery_id: str,
    *,
    http_status: Optional[int],
    error: Optional[str],
    retryable: bool,
    max_attempts: int = WEBHOOK_MAX_ATTEMPTS,
) -> str:
    """Record one delivery attempt and decide the next state.

    Returns the resulting status. ``retryable`` comes from
    :func:`classify_webhook_failure` — never from the HTTP status alone at
    the call site. A permanent 4xx becomes terminal FAILED immediately;
    a retryable failure becomes RETRYING with a bounded backoff until the
    budget is spent.
    """
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                row = session.execute(
                    text(
                        "SELECT attempt_count FROM platrixa_webhook_deliveries "
                        "WHERE delivery_id = :d FOR UPDATE"
                    ),
                    {"d": delivery_id},
                ).mappings().first()
                if row is None:
                    return DELIVERY_FAILED
                attempt = int(row["attempt_count"] or 0) + 1
                will_retry = bool(retryable) and attempt < max_attempts
                new_status = (
                    DELIVERY_RETRYING if will_retry
                    else (DELIVERY_DELIVERED if (not retryable and http_status and 200 <= http_status < 300)
                          else DELIVERY_FAILED)
                )
                delay = webhook_backoff_seconds(attempt) if will_retry else None
                session.execute(
                    text(
                        "UPDATE platrixa_webhook_deliveries SET "
                        "status = :st, attempt_count = :n, last_attempt_at = now(), "
                        "last_http_status = :hs, last_error = :err, "
                        "next_attempt_at = CASE WHEN :wr THEN now() + make_interval(secs => :d) "
                        "            ELSE NULL END, "
                        "delivered_at = CASE WHEN :st2 = 'DELIVERED' THEN now() ELSE NULL END, "
                        "updated_at = now() WHERE delivery_id = :did"
                    ),
                    {"st": new_status, "n": attempt, "hs": http_status, "err": error,
                     "wr": will_retry, "d": delay, "st2": new_status, "did": delivery_id},
                )
                return new_status
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("webhook delivery attempt update failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def due_deliveries(limit: int = 20) -> List[DeliveryRecord]:
    """Claimable webhook deliveries whose backoff has elapsed (worker)."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT delivery_id, job_id, tenant_id, webhook_id, event, status, "
                    "attempt_count, next_attempt_at, last_attempt_at, last_http_status, "
                    "last_error, delivered_at FROM platrixa_webhook_deliveries "
                    "WHERE status IN (:p, :r) "
                    "  AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
                    "ORDER BY created_at LIMIT :lim"
                ),
                {"p": DELIVERY_PENDING, "r": DELIVERY_RETRYING, "lim": int(limit)},
            ).mappings().fetchall()
        return [_delivery_row(r) for r in rows]
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("webhook due-delivery read failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def delivery_status_counts() -> Dict[str, int]:
    """Diagnostic counters for webhook delivery (operations + tests)."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT status, COUNT(*) AS n FROM platrixa_webhook_deliveries "
                    "GROUP BY status"
                )
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}
    except Exception:
        return {}


def classify_webhook_failure(http_status: Optional[int], error: Optional[str] = None) -> bool:
    """Should this webhook delivery be retried? (5K §14)

    Explicitly NOT a bare HTTP-status check:
      * 2xx            -> delivered, not a failure
      * 408 / 429      -> retry (transient / rate limited)
      * any other 4xx  -> PERMANENT: a bad URL, auth or payload will not
                          fix itself, so retrying is pointless
      * 5xx            -> retry (upstream is having a bad time)
      * network error  -> retry
    """
    if error:
        return True
    if http_status is None:
        return True
    if 200 <= http_status < 300:
        return False
    if http_status in (408, 429):
        return True
    if 400 <= http_status < 500:
        return False
    return True  # 5xx and anything unexpected


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    tenant_id: str
    result_id: str
    status: str  # QUEUED / PROCESSING / RETRY_WAIT / COMPLETED / FAILED
    http_status: Optional[int]
    result_json: Optional[str]
    error_json: Optional[str]
    retryable: bool
    request_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    # Phase 5K ownership + retry fields. Defaults keep every existing
    # construction site (and the 5E suite's fixtures) working unchanged.
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    lease_generation: int = 0
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: Optional[datetime] = None


# Phase 5K job state machine. QUEUED/PROCESSING/COMPLETED/FAILED are the
# pre-existing names and are reused unchanged; RETRY_WAIT is the only
# addition, and it exists because a retryable failure must be deferred
# (backoff), not lost.
#
#   QUEUED ──claim──> PROCESSING ──ok──> COMPLETED          (terminal)
#                       │
#                       ├──permanent failure──> FAILED       (terminal)
#                       ├──retryable, budget left──> RETRY_WAIT ──backoff──> QUEUED
#                       └──retryable, budget spent──> FAILED   (terminal)
#   PROCESSING ──lease expiry──> (claimable again: crash recovery)
#
# Invariants enforced in SQL, not in application code:
#   * a terminal job is never re-claimed (claim filters on status)
#   * only the current lease owner may transition the job
#   * the fencing generation changes on every claim
STATUS_QUEUED = "QUEUED"
STATUS_PROCESSING = "PROCESSING"
STATUS_RETRY_WAIT = "RETRY_WAIT"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)


def _ensure_result_uniqueness(conn) -> None:
    """5K §11: one canonical result per result_id.

    Enforced at the DATABASE, not in the worker: the worker already
    serialises, but a store-level constraint is the only thing that
    actually makes a duplicate result impossible when two workers race.
    Built defensively — a pre-existing duplicate is reported and the
    constraint is skipped rather than aborting startup.
    """
    from sqlalchemy import text

    exists = conn.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = 'uq_async_jobs_result_id' "
        "AND table_name = 'platrixa_async_jobs'"
    )).first()
    if exists:
        return
    dupes = conn.execute(text(
        "SELECT result_id FROM platrixa_async_jobs "
        "GROUP BY result_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if dupes:
        logger.warning(
            "5K: duplicate result_id present; unique constraint not created"
        )
        return
    conn.execute(text(
        "ALTER TABLE platrixa_async_jobs "
        "ADD CONSTRAINT uq_async_jobs_result_id UNIQUE (result_id)"
    ))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id() -> str:
    return "job_" + secrets.token_hex(12)


def new_result_id() -> str:
    return "res_" + secrets.token_hex(12)


def _row_to_record(row: Any) -> JobRecord:
    def _get(key, default=None):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    return JobRecord(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        result_id=row["result_id"],
        status=row["status"],
        http_status=row["http_status"],
        result_json=row["result_json"],
        error_json=row["error_json"],
        retryable=bool(row["retryable"]),
        request_id=row["request_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        lease_owner=_get("lease_owner"),
        lease_expires_at=_get("lease_expires_at"),
        lease_generation=int(_get("lease_generation") or 0),
        attempt_count=int(_get("attempt_count") or 0),
        max_attempts=int(_get("max_attempts") or 0) or 3,
        next_attempt_at=_get("next_attempt_at"),
    )


# Every SELECT that feeds _row_to_record must project the 5K columns, or
# the record silently loses its ownership fields and a commit would be
# rejected. Kept as one constant so the projection cannot drift.
_JOB_COLUMNS = (
    "job_id, tenant_id, result_id, status, http_status, result_json, "
    "error_json, retryable, request_id, created_at, updated_at, "
    "lease_owner, lease_expires_at, lease_generation, attempt_count, "
    "max_attempts, next_attempt_at"
)


def create_job(
    tenant_id: str,
    request_payload: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> JobRecord:
    """Insert one QUEUED job. Raises AsyncStoreUnavailableError if down."""
    from sqlalchemy import text

    job_id = new_job_id()
    result_id = new_result_id()
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "INSERT INTO platrixa_async_jobs "
                        "(job_id, tenant_id, result_id, request_hash, status, request_json, request_id, created_at, updated_at) "
                        "VALUES (:j, :t, :r, :rh, 'QUEUED', :req, :rid, now(), now())"
                    ),
                    {
                        "j": job_id,
                        "t": tenant_id,
                        "r": result_id,
                        "rh": hashlib.sha256(job_id.encode()).hexdigest(),
                        "req": json.dumps(request_payload, ensure_ascii=False),
                        "rid": request_id,
                    },
                )
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job create failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    return get_job(tenant_id, job_id)  # type: ignore[return-value]


def get_job(tenant_id: str, job_id: str) -> Optional[JobRecord]:
    """Tenant-scoped job read (no cross-tenant path exists)."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    try:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT " + _JOB_COLUMNS +
                    " FROM platrixa_async_jobs WHERE tenant_id = :t AND job_id = :j"
                ),
                {"t": tenant_id, "j": job_id},
            ).mappings().first()
        return _row_to_record(row) if row else None
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job read failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def get_result_record(tenant_id: str, result_id: str) -> Optional[JobRecord]:
    """Tenant-scoped result lookup (the completed job holding result_id)."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    try:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT " + _JOB_COLUMNS +
                    " FROM platrixa_async_jobs WHERE tenant_id = :t AND result_id = :r"
                ),
                {"t": tenant_id, "r": result_id},
            ).mappings().first()
        return _row_to_record(row) if row else None
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async result read failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def claim_next_job(
    lease_seconds: int = JOB_LEASE_SECONDS,
    owner: Optional[str] = None,
) -> Optional[JobRecord]:
    """Atomically claim the next runnable job (worker side).

    One UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED):
    PostgreSQL arbitrates among concurrent workers, so each job is
    claimed by at most one worker at a time. A PROCESSING job whose
    lease expired (crashed worker) is claimable again — the restart/
    durability recovery path. Returns None when nothing is runnable.

    Phase 5K: the claim also STAMPS OWNERSHIP — ``lease_owner`` and a
    monotonically increasing ``lease_generation`` (fencing token) — and
    consumes one unit of the retry budget. A worker must present both
    values back on every later commit; that is what makes a stale
    worker's write impossible (§7).

    Claimable means: QUEUED, or RETRY_WAIT whose backoff has elapsed, or
    PROCESSING with an absent/expired lease (crash recovery) — and
    always with retry budget remaining. Terminal jobs are never claimed.
    """
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    owner = owner or _default_worker_id()
    try:
        with SessionLocal() as session:
            with session.begin():
                rows = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs j SET status = 'PROCESSING', "
                        "lease_owner = :owner, "
                        "lease_generation = j.lease_generation + 1, "
                        "attempt_count = j.attempt_count + 1, "
                        "lease_expires_at = now() + make_interval(secs => :lease), "
                        "updated_at = now() "
                        "WHERE j.job_id IN ("
                        "  SELECT job_id FROM platrixa_async_jobs "
                        "  WHERE (status = 'QUEUED' "
                        "         OR status = 'RETRY_WAIT' "
                        "         OR (status = 'PROCESSING' "
                        "             AND (lease_expires_at IS NULL OR lease_expires_at < now()))) "
                        "    AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
                        "    AND attempt_count < max_attempts "
                        "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                        ") RETURNING j.job_id, j.tenant_id, j.result_id, j.status, j.http_status, "
                        "j.result_json, j.error_json, j.retryable, j.request_id, j.created_at, "
                        "j.updated_at, j.lease_owner, j.lease_expires_at, j.lease_generation, "
                        "j.attempt_count, j.max_attempts, j.next_attempt_at"
                    ),
                    {"lease": lease_seconds, "owner": owner},
                ).mappings().fetchall()
                if not rows:
                    return None
                return _row_to_record(rows[0])
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job claim failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def complete_job(
    job_id: str,
    *,
    envelope: Dict[str, Any],
    http_status: int,
    request_id: Optional[str] = None,
    lease_owner: Optional[str] = None,
    lease_generation: Optional[int] = None,
) -> bool:
    """Store the terminal 5D envelope for a COMPLETED job — FENCED.

    Phase 5I: ``request_id`` aligns the stored request id with the ENGINE
    request id produced inside the worker, so the submission path, the
    job/result read paths, and the observability log all report ONE id.

    Phase 5K (§7, the critical fix): the commit is conditional on the
    caller STILL OWNING the lease — ``lease_owner`` and
    ``lease_generation`` must both still match the row. Returns False
    (and changes nothing) when the caller has been fenced out, which is
    how a stale worker that lost its lease is prevented from overwriting
    the authoritative result of the worker that reclaimed the job.

    ``lease_owner``/``lease_generation`` are REQUIRED. A commit with no
    ownership proof is refused rather than silently allowed: the
    pre-5K ``status='PROCESSING'``-only predicate is exactly the
    unfenced write this phase removes.
    """
    from sqlalchemy import text

    if not lease_owner or lease_generation is None:
        logger.warning(
            "async job complete refused: no lease ownership proof (job=%s)",
            job_id[:14],
        )
        return False
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET status = 'COMPLETED', "
                        "result_json = :res, http_status = :st, request_id = :rid, "
                        "lease_expires_at = NULL, lease_owner = NULL, "
                        "next_attempt_at = NULL, updated_at = now() "
                        "WHERE job_id = :j AND status = 'PROCESSING' "
                        "  AND lease_owner = :owner AND lease_generation = :gen"
                    ),
                    {"res": json.dumps(envelope, ensure_ascii=False), "st": http_status,
                     "rid": (request_id or None), "j": job_id,
                     "owner": lease_owner, "gen": int(lease_generation)},
                )
                accepted = (result.rowcount or 0) == 1
                if not accepted:
                    logger.warning(
                        "async job complete REJECTED (stale worker) job=%s",
                        job_id[:14],
                    )
                return accepted
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job complete failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def fail_job(
    job_id: str,
    *,
    envelope: Dict[str, Any],
    http_status: int,
    retryable: bool,
    lease_owner: Optional[str] = None,
    lease_generation: Optional[int] = None,
) -> bool:
    """Record a job failure — FENCED, and retry-aware (5K §9/§10).

    A retryable failure with budget remaining moves the job to RETRY_WAIT
    with an exponential backoff gate, so it is re-claimed later instead
    of being lost. When the budget is exhausted — or the failure is
    permanent — the job becomes terminal FAILED.

    Terminal FAILED is written for a retryable failure only once the
    budget is spent, so a provider outage can no longer exhaust a job on
    its first occurrence (the pre-5K behaviour).

    Like :func:`complete_job`, the write requires valid lease ownership;
    a fenced-out worker cannot fail a job another worker now owns.
    """
    from sqlalchemy import text

    if not lease_owner or lease_generation is None:
        logger.warning(
            "async job fail refused: no lease ownership proof (job=%s)", job_id[:14]
        )
        return False
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET "
                        "status = CASE WHEN :rt AND attempt_count < max_attempts "
                        "             THEN 'RETRY_WAIT' ELSE 'FAILED' END, "
                        "error_json = :res, http_status = :st, retryable = :rt, "
                        "next_attempt_at = CASE WHEN :rt AND attempt_count < max_attempts "
                        "             THEN now() + make_interval(secs => :delay) "
                        "             ELSE NULL END, "
                        "lease_expires_at = NULL, lease_owner = NULL, updated_at = now() "
                        "WHERE job_id = :j AND status = 'PROCESSING' "
                        "  AND lease_owner = :owner AND lease_generation = :gen"
                    ),
                    {"res": json.dumps(envelope, ensure_ascii=False), "st": http_status,
                     "rt": bool(retryable), "j": job_id, "owner": lease_owner,
                     "gen": int(lease_generation),
                     "delay": retry_backoff_seconds(1)},
                )
                accepted = (result.rowcount or 0) == 1
                if not accepted:
                    logger.warning(
                        "async job fail REJECTED (stale worker) job=%s", job_id[:14]
                    )
                return accepted
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job fail failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def renew_lease(
    job_id: str,
    *,
    lease_owner: str,
    lease_generation: int,
    lease_seconds: int = JOB_LEASE_SECONDS,
) -> bool:
    """Extend the lease ONLY while this worker still owns it (5K §6).

    Conditional on ``lease_owner`` AND ``lease_generation`` still
    matching, so a worker that has already been fenced out cannot extend
    a lease it no longer holds (renewal failure must never silently
    restore stale ownership). Returns False when renewal is refused —
    the caller must then treat its work as no longer authoritative.
    """
    from sqlalchemy import text

    if not lease_owner or lease_generation is None:
        return False
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET "
                        "lease_expires_at = now() + make_interval(secs => :lease), "
                        "updated_at = now() "
                        "WHERE job_id = :j AND status = 'PROCESSING' "
                        "  AND lease_owner = :owner AND lease_generation = :gen"
                    ),
                    {"lease": int(lease_seconds), "j": job_id,
                     "owner": lease_owner, "gen": int(lease_generation)},
                )
                return (result.rowcount or 0) == 1
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async lease renewal failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def count_jobs_by_status() -> Dict[str, int]:
    """Diagnostic counters (used by tests / operations)."""
    from sqlalchemy import text

    SessionLocal = _session_factory()
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT status, COUNT(*) AS n FROM platrixa_async_jobs GROUP BY status")
        ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


# ---------------------------------------------------------------------------
# Webhook endpoint registry (secret hashed; plaintext shown exactly once)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookRecord:
    webhook_id: str
    tenant_id: str
    url: str
    events: List[str]
    secret_sealed: str
    created_at: Optional[datetime]


def register_webhook(
    tenant_id: str,
    url: str,
    events: List[str],
    *,
    secret: Optional[str] = None,
) -> tuple[WebhookRecord, str]:
    """Register a webhook endpoint. Returns (record, plaintext_secret).

    The caller may supply the signing secret; otherwise one is generated
    (CSPRNG). The plaintext secret is returned EXACTLY ONCE here; only
    its SHA-256 hash is stored.
    """
    from sqlalchemy import text

    plain_secret = secret or ("whsec_" + secrets.token_urlsafe(32))
    webhook_id = "wh_" + secrets.token_hex(12)
    sealed = seal_webhook_secret(plain_secret)
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "INSERT INTO platrixa_webhook_endpoints "
                        "(webhook_id, tenant_id, url, events, secret_sealed, created_at) "
                        "VALUES (:w, :t, :u, :e, :s, now())"
                    ),
                    {
                        "w": webhook_id,
                        "t": tenant_id,
                        "u": url,
                        "e": json.dumps(list(events)),
                        "s": sealed,
                    },
                )
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("webhook register failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    record = WebhookRecord(
        webhook_id=webhook_id,
        tenant_id=tenant_id,
        url=url,
        events=list(events),
        secret_sealed=sealed,
        created_at=_now(),
    )
    return record, plain_secret


def webhooks_for_event(tenant_id: str, event: str) -> List[WebhookRecord]:
    """Endpoints of one tenant subscribed to ``event`` (worker-side)."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT webhook_id, tenant_id, url, events, secret_sealed, created_at "
                    "FROM platrixa_webhook_endpoints WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            ).mappings().all()
        out: List[WebhookRecord] = []
        for row in rows:
            try:
                subscribed = json.loads(row["events"])
            except Exception:
                subscribed = []
            if event in subscribed:
                out.append(
                    WebhookRecord(
                        webhook_id=row["webhook_id"],
                        tenant_id=row["tenant_id"],
                        url=row["url"],
                        events=subscribed,
                        secret_sealed=row["secret_sealed"],
                        created_at=row["created_at"],
                    )
                )
        return out
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("webhook lookup failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


# ---------------------------------------------------------------------------
# Signing / verification helpers (pure functions; documented scheme)
# ---------------------------------------------------------------------------


def event_id(job_id: str, event: str) -> str:
    """Deterministic event identity: same job + same event → same id.

    Redeliveries of the same event carry the same id, so consumers can
    deduplicate. (Delivery itself is at-most-once in this deployment.)
    """
    digest = hashlib.sha256(f"{job_id}:{event}".encode("utf-8")).hexdigest()
    return f"evt_{digest[:24]}"


def sign_event(secret: str, timestamp: int, payload_json: str) -> str:
    """HMAC-SHA256 signature over ``f"{timestamp}.{payload_json}"``."""
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{payload_json}".encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


def verify_event_signature(
    secret: str,
    signature_header: str,
    payload_json: str,
    *,
    now_ts: Optional[int] = None,
) -> bool:
    """Consumer-side verification helper (mirrors the documented scheme).

    Expects ``Platrixa-Signature: t=<unix>,v1=<hex>``. Replay protection:
    the timestamp must be within SIGNATURE_TOLERANCE_SECONDS of now and
    the HMAC must match. Constant-time comparison.
    """
    try:
        parts = dict(
            piece.split("=", 1) for piece in signature_header.split(",") if "=" in piece
        )
        ts = int(parts.get("t", ""))
        sig = parts.get("v1", "")
    except Exception:
        return False
    current = now_ts if now_ts is not None else int(_now().timestamp())
    if abs(current - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = sign_event(secret, ts, payload_json)
    return hmac.compare_digest(expected, sig)


def build_event_payload(
    *,
    event: str,
    event_id_value: str,
    job_id: str,
    result_id: str,
    request_id: Optional[str],
    api_status: str,
    created_at: Optional[datetime],
    updated_at: Optional[datetime],
) -> Dict[str, Any]:
    """The signed webhook payload (metadata only — never raw documents)."""
    return {
        "id": event_id_value,
        "type": event,
        "created_at": (updated_at or created_at or _now()).isoformat() if (updated_at or created_at) else None,
        "data": {
            "job_id": job_id,
            "result_id": result_id,
            "request_id": request_id,
            "api_status": api_status,
        },
    }
