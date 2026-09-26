"""Durable idempotency for the developer API (Phase 5C).

Tenant-scoped, replay-safe request handling for mutating developer
endpoints. Design properties (verified by
``scripts/fte_fyjc_79_phase5c_idempotency_test.py``):

  * Durable: records live in the metering PostgreSQL store (the same
    store the Phase 16 gate uses). No in-memory, file, or Redis-only
    state participates in the replay guarantee.
  * Tenant-scoped: the uniqueness key is (tenant_id, key_hash).
    Tenant A and Tenant B using the SAME key string are separate
    namespaces — one tenant can never retrieve, conflict with, or even
    prove the existence of another tenant's record via key reuse.
  * Replay decision = key + fingerprint: SHA-256 of a canonical
    serialization of everything that materially affects processing
    (tenant identity, endpoint scope, canonical request body).
    Server-generated request IDs and transport metadata are excluded
    by construction.
  * Atomic: the claim is one ``INSERT ... ON CONFLICT DO NOTHING``
    under the composite PRIMARY KEY (tenant_id, key_hash); PostgreSQL
    arbitrates concurrent duplicates, so exactly one request becomes
    the canonical processing attempt. Losers read the winner's record
    inside their own transaction and replay or conflict accordingly.
  * Honest about guarantees: this provides durable idempotent REPLAY
    semantics. It does NOT promise exactly-once engine execution.

Security boundary (mirrors backend.auth.gate):
  * Raw keys are NEVER stored or logged. The stored identifier is a
    SHA-256 hex digest of the key; logs carry an 8-hex prefix only.
  * Stored payloads are response envelopes already returned to the same
    tenant — no new secrets enter the store, and rows are tenant-keyed,
    so no cross-tenant read path exists.

This module contains no financial logic: it never calls the Kernel,
model provider, or authorities. It is an API reliability layer only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("platrixa.api")

# Replayable window for a terminal (COMPLETED/FAILED) record. After it
# passes, the stored envelope is deleted on next sight (audit metadata
# — tenant, key hash, fingerprint, state timeline — persists), and the
# key becomes reusable for a NEW processing attempt. Documented in
# docs/HOSTED_API.md; only applies when metering is configured.
IDEMPOTENCY_RETENTION_HOURS = 72

# Zero-config (no metering store) requests share one anonymous tenant
# namespace. There is exactly one local deployment identity, so this
# preserves replay safety without inventing tenant data.
ANONYMOUS_TENANT_ID = "anonymous"

MIN_KEY_LENGTH = 16
MAX_KEY_LENGTH = 200
_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")

# Machine-readable outcomes surfaced by the HTTP layer.
KEY_OK = "OK"
KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"
KEY_TOO_LONG = "IDEMPOTENCY_KEY_TOO_LONG"
CONFLICT_CODE = "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
IN_PROGRESS_CODE = "IDEMPOTENCY_REQUEST_IN_PROGRESS"

# Human-readable contract for key validation errors (safe to return).
KEY_FORMAT_MESSAGE = (
    "Idempotency-Key must be 16-200 characters of [A-Za-z0-9._~-] "
    "with no surrounding whitespace"
)

# Failure codes DETERMINISTIC in the engine/input domain: a replay
# returns the stored failure (retrying the identical request would
# deterministically fail the same way). Transient infrastructure codes
# RELEASE the claim so a retry can genuinely retry.
DETERMINISTIC_FAILURE_CODES = frozenset({"INPUT_INVALID"})


class IdempotencyUnavailableError(Exception):
    """Idempotency store unavailable while operating (fail closed)."""


# ---------------------------------------------------------------------------
# Canonical fingerprint
# ---------------------------------------------------------------------------


def canonical_fingerprint(tenant_id: str, endpoint: str, body: Dict[str, Any]) -> str:
    """SHA-256 of the canonical request fingerprint.

    Included: tenant identity, endpoint scope, and the canonical request
    body (sorted keys, compact separators, UTF-8, ``ensure_ascii=False``).
    Excluded: server-generated request IDs, timestamps, and transport
    metadata that cannot affect processing.
    """
    payload = {"tenant_id": tenant_id, "endpoint": endpoint, "body": body}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_key_hash(raw_key: str) -> str:
    """Deterministic SHA-256 hex digest of the raw Idempotency-Key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def redact_key(raw_key: Optional[str]) -> str:
    """8-hex prefix of the key digest for safe logging (never the raw key)."""
    if not raw_key:
        return ""
    return idempotency_key_hash(raw_key)[:8]


def validate_key(raw_key: Optional[str]) -> Tuple[bool, str]:
    """Validate an Idempotency-Key per the documented contract.

    A missing header (None) is valid — idempotency is optional. An
    invalid/oversized key is a 400-class transport rejection that never
    reaches processing and never consumes quota.

    Returns ``(ok, code)`` with code in {KEY_OK, KEY_INVALID, KEY_TOO_LONG}.
    """
    if raw_key is None:
        return True, KEY_OK
    key = raw_key.strip()
    if not key or key != raw_key:
        # empty, whitespace-only, or surrounding whitespace → malformed
        return False, KEY_INVALID
    if len(key) > MAX_KEY_LENGTH:
        logger.warning(
            "idempotency key rejected (too long): prefix=%s len=%d",
            redact_key(key),
            len(key),
        )
        return False, KEY_TOO_LONG
    if len(key) < MIN_KEY_LENGTH or not _KEY_PATTERN.fullmatch(key):
        logger.warning("idempotency key rejected (invalid format): prefix=%s", redact_key(key))
        return False, KEY_INVALID
    return True, KEY_OK


# ---------------------------------------------------------------------------
# Store wiring (dedicated lazy engine over the metering store, schema ensured)
# ---------------------------------------------------------------------------

_session_factory_cache: dict = {}
_schema_ensured: set = set()

_DDL = """
CREATE TABLE IF NOT EXISTS platrixa_idempotency_keys (
    key_hash     VARCHAR(64)  NOT NULL,
    tenant_id    VARCHAR(64)  NOT NULL,
    request_hash VARCHAR(64)  NOT NULL,
    endpoint     VARCHAR(64)  NOT NULL,
    state        VARCHAR(16)  NOT NULL DEFAULT 'PROCESSING',
    request_id   VARCHAR(128),
    result_json  TEXT,
    result_status INTEGER,
    failure_json TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, key_hash)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_tenant ON platrixa_idempotency_keys (tenant_id);
CREATE INDEX IF NOT EXISTS idx_idempotency_expires
    ON platrixa_idempotency_keys (expires_at) WHERE expires_at IS NOT NULL;
"""


def _metering_database_url() -> Optional[str]:
    from backend.auth.gate import METERING_ENV_VAR

    return (os.getenv(METERING_ENV_VAR, "") or "").strip() or None


def _session_factory():
    """Sessionmaker over the metering store with the schema ensured once."""
    url = _metering_database_url()
    if not url:
        raise IdempotencyUnavailableError("metering store not configured")
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
        raise IdempotencyUnavailableError("idempotency store unavailable") from exc
    _session_factory_cache[url] = factory
    _schema_ensured.add(url)
    return factory


# ---------------------------------------------------------------------------
# Claim / replay / complete / fail / release
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimOutcome:
    """Result of an atomic claim attempt.

    claimed      — this request IS the canonical processing attempt.
    replay       — a terminal record exists; ``envelope``/``http_status``
                   reproduce the original response verbatim.
    conflict     — same (tenant, key), different fingerprint.
    processing   — the canonical attempt is still in flight.
    expired      — a terminal record aged out of retention; the key was
                   reclaimed here for a NEW processing attempt.
    """

    claimed: bool
    replay: bool = False
    conflict: bool = False
    processing: bool = False
    expired: bool = False
    envelope: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    request_id: Optional[str] = None


def claim(
    raw_key: str,
    tenant_id: str,
    endpoint: str,
    body: Dict[str, Any],
) -> ClaimOutcome:
    """Atomically claim (tenant, key) for one canonical processing attempt.

    Concurrency: the INSERT ... ON CONFLICT DO NOTHING under the
    composite PK makes PostgreSQL the arbiter — concurrent duplicates
    yield exactly one ``claimed`` outcome; every other request observes
    either PROCESSING (winner still running) or the stored terminal
    envelope. Expired terminal records are reclaimed inside the same
    locked transaction (delete + re-insert), so expiry never produces
    two live attempts either.
    """
    from sqlalchemy import text

    key_hash = idempotency_key_hash(raw_key)
    request_hash = canonical_fingerprint(tenant_id, endpoint, body)

    try:
        SessionLocal = _session_factory()
    except IdempotencyUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise IdempotencyUnavailableError("idempotency store unavailable") from exc

    try:
        with SessionLocal() as session:
            with session.begin():
                inserted = session.execute(
                    text(
                        "INSERT INTO platrixa_idempotency_keys "
                        "(key_hash, tenant_id, request_hash, endpoint, state, created_at, updated_at) "
                        "VALUES (:kh, :t, :rh, :ep, 'PROCESSING', now(), now()) "
                        "ON CONFLICT (tenant_id, key_hash) DO NOTHING "
                        "RETURNING key_hash"
                    ),
                    {"kh": key_hash, "t": tenant_id, "rh": request_hash, "ep": endpoint},
                )
                if inserted.first() is not None:
                    logger.info(
                        "idempotency claim: prefix=%s tenant=%s endpoint=%s",
                        key_hash[:8], tenant_id, endpoint,
                    )
                    return ClaimOutcome(claimed=True)

                # Row exists. Lock it for the duration of the decision.
                row = session.execute(
                    text(
                        "SELECT state, request_hash, request_id, result_json, "
                        "result_status, failure_json, expires_at "
                        "FROM platrixa_idempotency_keys "
                        "WHERE tenant_id = :t AND key_hash = :kh "
                        "FOR UPDATE"
                    ),
                    {"t": tenant_id, "kh": key_hash},
                ).mappings().first()

                if row is None:  # pragma: no cover - raced expiry reclaim
                    return ClaimOutcome(claimed=True)

                if row["request_hash"] != request_hash:
                    logger.info(
                        "idempotency conflict: prefix=%s tenant=%s", key_hash[:8], tenant_id
                    )
                    return ClaimOutcome(claimed=False, conflict=True)

                if row["state"] == "PROCESSING":
                    return ClaimOutcome(
                        claimed=False,
                        processing=True,
                        request_id=row["request_id"],
                    )

                # Terminal record: replay within retention, reclaim after.
                expires_at = row["expires_at"]
                if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                    session.execute(
                        text(
                            "DELETE FROM platrixa_idempotency_keys "
                            "WHERE tenant_id = :t AND key_hash = :kh"
                        ),
                        {"t": tenant_id, "kh": key_hash},
                    )
                    session.execute(
                        text(
                            "INSERT INTO platrixa_idempotency_keys "
                            "(key_hash, tenant_id, request_hash, endpoint, state, created_at, updated_at) "
                            "VALUES (:kh, :t, :rh, :ep, 'PROCESSING', now(), now())"
                        ),
                        {"kh": key_hash, "t": tenant_id, "rh": request_hash, "ep": endpoint},
                    )
                    logger.info(
                        "idempotency reclaim after retention: prefix=%s tenant=%s",
                        key_hash[:8], tenant_id,
                    )
                    return ClaimOutcome(claimed=True, expired=True)

                envelope_json = row["result_json"] if row["state"] == "COMPLETED" else row["failure_json"]
                envelope = json.loads(envelope_json) if envelope_json else None
                return ClaimOutcome(
                    claimed=False,
                    replay=True,
                    envelope=envelope,
                    http_status=row["result_status"],
                    request_id=row["request_id"],
                )
    except IdempotencyUnavailableError:
        raise
    except Exception as exc:
        logger.warning("idempotency claim failed: %s", type(exc).__name__)
        raise IdempotencyUnavailableError("idempotency store unavailable") from exc


def complete(
    raw_key: str,
    tenant_id: str,
    endpoint: str,
    body: Dict[str, Any],
    *,
    request_id: Optional[str],
    envelope: Dict[str, Any],
    http_status: int,
) -> bool:
    """Record the terminal success envelope for the canonical attempt."""
    from sqlalchemy import text

    expires_at = datetime.now(timezone.utc) + timedelta(hours=IDEMPOTENCY_RETENTION_HOURS)
    try:
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                result = session.execute(
                    text(
                        "UPDATE platrixa_idempotency_keys "
                        "SET state = 'COMPLETED', request_id = :rid, result_json = :res, "
                        "result_status = :st, expires_at = :exp, updated_at = now() "
                        "WHERE tenant_id = :t AND key_hash = :kh AND state = 'PROCESSING'"
                    ),
                    {
                        "rid": request_id,
                        "res": json.dumps(envelope, ensure_ascii=False),
                        "st": http_status,
                        "exp": expires_at,
                        "t": tenant_id,
                        "kh": idempotency_key_hash(raw_key),
                    },
                )
                done = (result.rowcount or 0) == 1
        if done:
            logger.info(
                "idempotency complete: prefix=%s tenant=%s endpoint=%s",
                idempotency_key_hash(raw_key)[:8], tenant_id, endpoint,
            )
        return done
    except IdempotencyUnavailableError:
        raise
    except Exception as exc:
        logger.warning("idempotency complete failed: %s", type(exc).__name__)
        raise IdempotencyUnavailableError("idempotency store unavailable") from exc


def fail(
    raw_key: str,
    tenant_id: str,
    endpoint: str,
    body: Dict[str, Any],
    *,
    code: str,
    envelope: Dict[str, Any],
    http_status: int,
    request_id: Optional[str] = None,
) -> str:
    """Record a failure for the canonical attempt.

    Deterministic failure codes (in the input/engine domain) are STORED:
    replaying the identical request returns the identical failure instead
    of rerunning it. Transient infrastructure codes RELEASE the claim so
    the retry genuinely retries.

    Returns the action taken: "stored" or "released".
    """
    if code in DETERMINISTIC_FAILURE_CODES:
        from sqlalchemy import text

        expires_at = datetime.now(timezone.utc) + timedelta(hours=IDEMPOTENCY_RETENTION_HOURS)
        SessionLocal = _session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "UPDATE platrixa_idempotency_keys "
                        "SET state = 'FAILED', request_id = :rid, failure_json = :res, "
                        "result_status = :st, expires_at = :exp, updated_at = now() "
                        "WHERE tenant_id = :t AND key_hash = :kh AND state = 'PROCESSING'"
                    ),
                    {
                        "rid": request_id,
                        "res": json.dumps(envelope, ensure_ascii=False),
                        "st": http_status,
                        "exp": expires_at,
                        "t": tenant_id,
                        "kh": idempotency_key_hash(raw_key),
                    },
                )
        logger.info(
            "idempotency fail (stored): prefix=%s tenant=%s code=%s",
            idempotency_key_hash(raw_key)[:8], tenant_id, code,
        )
        return "stored"

    release(raw_key, tenant_id)
    logger.info(
        "idempotency fail (released): prefix=%s tenant=%s code=%s",
        idempotency_key_hash(raw_key)[:8], tenant_id, code,
    )
    return "released"


def release(raw_key: str, tenant_id: str) -> None:
    """Remove a PROCESSING record (transient failure / reservation refusal)."""
    from sqlalchemy import text

    SessionLocal = _session_factory()
    with SessionLocal() as session:
        with session.begin():
            session.execute(
                text(
                    "DELETE FROM platrixa_idempotency_keys "
                    "WHERE tenant_id = :t AND key_hash = :kh"
                ),
                {"t": tenant_id, "kh": idempotency_key_hash(raw_key)},
            )
