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
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(
            key + nonce + counter.to_bytes(4, "big")
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def seal_webhook_secret(plain: str) -> str:
    """Seal a webhook signing secret for at-rest storage."""
    key = (os.getenv(WEBHOOK_SEALING_ENV_VAR, "") or "").strip()
    if not key:
        raise AsyncStoreUnavailableError("webhook signing key not configured")
    nonce = secrets.token_bytes(16)
    data = plain.encode("utf-8")
    stream = _seal_keystream(key.encode("utf-8"), nonce, len(data))
    ct = bytes(a ^ b for a, b in zip(data, stream))
    return f"v1:{nonce.hex()}:{ct.hex()}"


def unseal_webhook_secret(sealed: str) -> str:
    """Recover a sealed webhook signing secret (worker-side signing)."""
    key = (os.getenv(WEBHOOK_SEALING_ENV_VAR, "") or "").strip()
    if not key:
        raise AsyncStoreUnavailableError("webhook signing key not configured")
    try:
        version, nonce_hex, ct_hex = sealed.split(":", 2)
        if version != "v1":
            raise ValueError("unsupported seal version")
        nonce = bytes.fromhex(nonce_hex)
        ct = bytes.fromhex(ct_hex)
    except Exception as exc:
        raise AsyncStoreUnavailableError("webhook secret seal corrupt") from exc
    stream = _seal_keystream(key.encode("utf-8"), nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")


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
"""


def async_configured() -> bool:
    """Async documents require the durable store (the metering store)."""
    from backend.auth.gate import METERING_ENV_VAR

    return bool((os.getenv(METERING_ENV_VAR, "") or "").strip())


def _session_factory():
    """Sessionmaker over the metering store with the schema ensured once."""
    from backend.auth.gate import METERING_ENV_VAR

    url = (os.getenv(METERING_ENV_VAR, "") or "").strip()
    if not url:
        raise AsyncStoreUnavailableError("async store not configured")
    cached = _session_factory_cache.get(url)
    if cached is not None:
        return cached

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.begin() as conn:
            conn.execute(text(_DDL))
        factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    _session_factory_cache[url] = factory
    return factory


_session_factory_cache: dict = {}


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    tenant_id: str
    result_id: str
    status: str  # QUEUED / PROCESSING / COMPLETED / FAILED
    http_status: Optional[int]
    result_json: Optional[str]
    error_json: Optional[str]
    retryable: bool
    request_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id() -> str:
    return "job_" + secrets.token_hex(12)


def new_result_id() -> str:
    return "res_" + secrets.token_hex(12)


def _row_to_record(row: Any) -> JobRecord:
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
                    "SELECT job_id, tenant_id, result_id, status, http_status, result_json, "
                    "error_json, retryable, request_id, created_at, updated_at "
                    "FROM platrixa_async_jobs WHERE tenant_id = :t AND job_id = :j"
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
                    "SELECT job_id, tenant_id, result_id, status, http_status, result_json, "
                    "error_json, retryable, request_id, created_at, updated_at "
                    "FROM platrixa_async_jobs WHERE tenant_id = :t AND result_id = :r"
                ),
                {"t": tenant_id, "r": result_id},
            ).mappings().first()
        return _row_to_record(row) if row else None
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async result read failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def claim_next_job(lease_seconds: int = JOB_LEASE_SECONDS) -> Optional[JobRecord]:
    """Atomically claim the next runnable job (worker side).

    One UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED):
    PostgreSQL arbitrates among concurrent workers, so each job is
    claimed by at most one worker at a time. A PROCESSING job whose
    lease expired (crashed worker) is claimable again — the restart/
    durability recovery path. Returns None when nothing is runnable.
    """
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        raise AsyncStoreUnavailableError("async store unavailable") from exc
    try:
        with SessionLocal() as session:
            with session.begin():
                rows = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs j SET status = 'PROCESSING', "
                        "lease_expires_at = now() + make_interval(secs => :lease), updated_at = now() "
                        "WHERE j.job_id IN ("
                        "  SELECT job_id FROM platrixa_async_jobs "
                        "  WHERE status = 'QUEUED' "
                        "     OR (status = 'PROCESSING' AND (lease_expires_at IS NULL OR lease_expires_at < now())) "
                        "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                        ") RETURNING j.job_id, j.tenant_id, j.result_id, j.status, j.http_status, "
                        "j.result_json, j.error_json, j.retryable, j.request_id, j.created_at, j.updated_at"
                    ),
                    {"lease": lease_seconds},
                ).mappings().fetchall()
                if not rows:
                    return None
                return _row_to_record(rows[0])
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job claim failed: %s", type(exc).__name__)
        raise AsyncStoreUnavailableError("async store unavailable") from exc


def complete_job(job_id: str, *, envelope: Dict[str, Any], http_status: int) -> bool:
    """Store the terminal 5D envelope for a COMPLETED job."""
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET status = 'COMPLETED', "
                        "result_json = :res, http_status = :st, lease_expires_at = NULL, updated_at = now() "
                        "WHERE job_id = :j AND status = 'PROCESSING'"
                    ),
                    {"res": json.dumps(envelope, ensure_ascii=False), "st": http_status, "j": job_id},
                )
                return (result.rowcount or 0) == 1
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
) -> bool:
    """Store the terminal error envelope for a FAILED job.

    ``retryable`` is the honest, documented signal: true only when the
    failure was an infrastructure condition (provider down, worker
    restart) such that resubmitting the same document may succeed.
    """
    from sqlalchemy import text

    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET status = 'FAILED', "
                        "error_json = :res, http_status = :st, retryable = :rt, "
                        "lease_expires_at = NULL, updated_at = now() "
                        "WHERE job_id = :j AND status = 'PROCESSING'"
                    ),
                    {"res": json.dumps(envelope, ensure_ascii=False), "st": http_status,
                     "rt": retryable, "j": job_id},
                )
                return (result.rowcount or 0) == 1
    except AsyncStoreUnavailableError:
        raise
    except Exception as exc:
        logger.warning("async job fail failed: %s", type(exc).__name__)
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
