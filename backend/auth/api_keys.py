"""Developer API-key lifecycle (Phase 5G).

Durable credential registry beside the Phase 16 metered gate, on the
SAME metering PostgreSQL store. The runtime admission credential still
lives in ``platrixa_tenant_quotas`` (SHA-256 hash primary key +
``is_active``); this module adds the lifecycle metadata (name,
environment, status, timestamps) and the atomic operations behind the
developer key-management API:

    create → the raw key is returned EXACTLY ONCE, at creation
    list   → masked metadata only (prefix, never hash, never raw)
    revoke → durable, idempotent, tenant-scoped
    rotate → atomic credential switch inside one transaction

SECURITY MODEL (stated honestly):

  * Raw keys are NEVER stored, logged, or serialized — only
    ``hash_token(raw)`` (the same SHA-256 digest the runtime gate
    already uses) is persisted, in ``key_hash`` (UNIQUE).
  * Generation uses ``secrets.token_urlsafe(32)`` — a
    cryptographically secure random source. Never random(), never
    timestamps, never UUIDs-as-secrets, never counters.
  * Tenant scoping is enforced in EVERY query: the caller's tenant_id
    comes from the management credential binding, never from the
    request body or path beyond the key id.
  * Revocation and rotation atomically update BOTH this registry row
    AND the companion quota row (single transaction) — a revoked key
    stops authenticating the moment the transaction commits; rotation
    has no overlap window (old hash replaced in the same commit).
  * Data-plane ≠ management plane: ordinary runtime API keys can NEVER
    manage keys. Management requires a separate operator-issued token
    (PLATRIXA_KEY_MANAGEMENT_TOKEN) bound to exactly one tenant
    (PLATRIXA_KEY_MANAGEMENT_TENANT_ID). There is deliberately no
    RBAC — the honest smallest mechanism.
  * Every store failure raises ``ApiKeyStoreError`` → the HTTP layer
    fails closed (503). Nothing here can silently degrade to open.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import Column, DateTime, String

from backend.auth.tokens import hash_token

# ---------------------------------------------------------------------------
# Configuration + vocabulary
# ---------------------------------------------------------------------------

# Management credential variables (explicit configuration only — mirrors
# METERING_ENV_VAR semantics: no ambient fallback, ever).
MANAGEMENT_TOKEN_ENV_VAR = "PLATRIXA_KEY_MANAGEMENT_TOKEN"
MANAGEMENT_TENANT_ENV_VAR = "PLATRIXA_KEY_MANAGEMENT_TENANT_ID"

ENVIRONMENTS = ("test", "live")
DEFAULT_ENVIRONMENT = "test"

MAX_NAME_LEN = 120
# Bounded retries on the astronomically-unlikely hash collision.
_MAX_GENERATION_ATTEMPTS = 3

# Outcome codes (HTTP-agnostic; routes map them deterministically).
MGMT_OK = "OK"
MGMT_NOT_CONFIGURED = "NOT_CONFIGURED"
MGMT_MISCONFIGURED = "MISCONFIGURED"
MGMT_INVALID = "INVALID"

KEY_NOT_FOUND = "API_KEY_NOT_FOUND"
KEY_REVOKED = "API_KEY_REVOKED"


class ApiKeyStoreError(Exception):
    """Management store unavailable while performing a lifecycle operation.

    The HTTP layer MUST fail closed on this (503) — never degrade.
    """


# ---------------------------------------------------------------------------
# ORM model — LAZILY registered on the shared declarative Base.
#
# backend.database.db raises at import when DATABASE_URL is absent, so —
# exactly like backend/auth/gate.py and backend/auth/models.py consumers —
# this module must stay importable in a zero-config deployment (Phase 7F
# clean-import contract). The model class is created once, on first use.
# ---------------------------------------------------------------------------

_API_KEY_MODEL = None


def api_key_model():
    """The ``ApiKey`` ORM class, registered on first use (cached)."""
    global _API_KEY_MODEL
    if _API_KEY_MODEL is not None:
        return _API_KEY_MODEL

    from backend.database.db import Base

    class ApiKey(Base):
        """ORM model for ``platrixa_api_keys`` (Phase 5G registry).

        Stores ONLY hashed key material — see backend/auth/tokens.py for
        the hashing contract and the honest boundary statement.
        """

        __tablename__ = "platrixa_api_keys"

        # Public identifier (UUID) — deliberately NOT the key hash, so the
        # hash is never part of any URL, path, or listing response.
        id = Column(String(36), primary_key=True, comment="Public key identifier (UUID v4)")

        tenant_id = Column(
            String(64),
            nullable=False,
            index=True,
            comment="Owning tenant — every management query filters on this",
        )

        name = Column(
            String(120),
            nullable=False,
            comment="Developer-chosen label (1-120 chars after strip)",
        )

        # Masked identification prefix (e.g. "plx_test_a1B2c3D") — never
        # the full secret, never authenticating material.
        key_prefix = Column(
            String(20), nullable=False, comment="Masked identification prefix"
        )

        key_hash = Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
            comment="SHA-256 hex digest of the raw key (raw key NEVER stored)",
        )

        environment = Column(
            String(20),
            nullable=False,
            default=DEFAULT_ENVIRONMENT,
            comment="Closed set: test | live",
        )

        status = Column(
            String(20),
            nullable=False,
            default="ACTIVE",
            comment="Closed set: ACTIVE | REVOKED",
        )

        created_at = Column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )

        last_used_at = Column(
            DateTime(timezone=True),
            nullable=True,
            comment="Reserved for future hot-path sync; the API derives last-use from the quota row",
        )

        revoked_at = Column(DateTime(timezone=True), nullable=True)

        def to_safe_dict(self) -> dict:
            """Serialisable view WITHOUT key material (raw or hashed)."""
            return {
                "id": self.id,
                "name": self.name,
                "environment": self.environment,
                "key_prefix": self.key_prefix,
                "status": self.status,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            }

    _API_KEY_MODEL = ApiKey
    return _API_KEY_MODEL


# ---------------------------------------------------------------------------
# Key material generation (cryptographically secure, prefix-preserving)
# ---------------------------------------------------------------------------


def generate_raw_key(environment: str) -> Tuple[str, str, str]:
    """Generate ``(raw_key, key_prefix, key_hash)`` for one environment.

    Raw form: ``plx_<env>_<43-char url-safe secret>`` — 32 bytes of
    entropy from ``secrets`` (the OS CSPRNG). ``key_prefix`` carries the
    leading characters of the secret for masked identification only; it
    is never sufficient to reconstruct or authenticate the key.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError("unsupported environment")
    secret = secrets.token_urlsafe(32)
    raw_key = f"plx_{environment}_{secret}"
    key_prefix = raw_key[:16]  # e.g. "plx_test_a1B2c3D" — identification only
    return raw_key, key_prefix, hash_token(raw_key)


def validate_name(name: object) -> Optional[str]:
    """Normalized key name, or None when invalid (fail closed)."""
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    if not normalized or len(normalized) > MAX_NAME_LEN:
        return None
    return normalized


# ---------------------------------------------------------------------------
# Store wiring (lazy, request-time; same store as the metering gate)
# ---------------------------------------------------------------------------


def _store_database_url() -> Optional[str]:
    from backend.auth.gate import METERING_ENV_VAR

    return (os.getenv(METERING_ENV_VAR, "") or "").strip() or None


def _session_factory():
    """Sessionmaker on the metering store, schema self-ensured once per URL."""
    global _session_factory_cache
    url = _store_database_url()
    if url is None:
        raise ApiKeyStoreError("key-management store not configured")
    cached = _session_factory_cache.get(url)
    if cached is not None:
        return cached

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url, pool_pre_ping=True, future=True)
    _ensure_schema(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _session_factory_cache[url] = factory
    return factory


_session_factory_cache: dict = {}


def _ensure_schema(engine) -> None:
    """Apply the idempotent registry DDL (same convention as init_metering)."""
    from pathlib import Path
    from sqlalchemy import text

    ddl_path = (
        Path(__file__).resolve().parent.parent
        / "database"
        / "platrixa_api_keys_schema.sql"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl_path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Management authorization — deliberately separate from the data plane
# ---------------------------------------------------------------------------


def authorize_management(provided_token: Optional[str]) -> Tuple[str, Optional[str]]:
    """Authorize a key-MANAGEMENT request. Returns ``(reason, tenant_id)``.

    This is the entire authorization model, kept honest and small:

      * No token configured  → NOT_CONFIGURED (deployment disabled it).
      * Token set, tenant binding missing → MISCONFIGURED (fail closed;
        an unbound token must never manage keys for an ambiguous tenant).
      * Token mismatch (including a runtime API key presented as a
        management token) → INVALID. Comparison is constant-time over
        SHA-256 digests so timing never leaks the token.

    Runtime API keys carry no management power — they are not consulted
    here at all.
    """
    import hashlib
    import hmac

    expected = (os.getenv(MANAGEMENT_TOKEN_ENV_VAR, "") or "").strip()
    bound_tenant = (os.getenv(MANAGEMENT_TENANT_ENV_VAR, "") or "").strip()

    if not expected:
        return MGMT_NOT_CONFIGURED, None
    if not bound_tenant:
        return MGMT_MISCONFIGURED, None
    if not provided_token or not provided_token.strip():
        return MGMT_INVALID, None

    a = hashlib.sha256(provided_token.strip().encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    if not hmac.compare_digest(a, b):
        return MGMT_INVALID, None
    return MGMT_OK, bound_tenant


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _session():
    SessionLocal = _session_factory()
    return SessionLocal()


def _looks_like_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _scoped_row(session, tenant_id: str, key_id: str, for_update: bool):
    """Tenant-scoped row fetch. Cross-tenant and missing lookups are
    deliberately indistinguishable (both → KEY_NOT_FOUND, no existence
    leak)."""
    from sqlalchemy import select

    model = api_key_model()
    stmt = select(model).where(model.id == key_id, model.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def _last_used_for(session, key_hash: str) -> Optional[datetime]:
    """Derive last-use from the companion quota row (read-only, honest)."""
    from sqlalchemy import select

    from backend.auth.models import TenantQuota

    return session.execute(
        select(TenantQuota.last_request_at).where(TenantQuota.api_key_hash == key_hash)
    ).scalar_one_or_none()


def _safe_view(row: ApiKey, last_used: Optional[datetime]) -> dict:
    view = row.to_safe_dict()
    view["last_used_at"] = last_used.isoformat() if last_used else None
    return view


# ---------------------------------------------------------------------------
# Lifecycle operations (tenant-scoped, transactional)
# ---------------------------------------------------------------------------


def create_api_key(tenant_id: str, name: str, environment: str) -> dict:
    """Create a key: registry row + companion quota row, one transaction.

    The returned dict contains ``raw_key`` EXACTLY ONCE. The database
    receives only the hash. On the (astronomically unlikely) event of a
    ``key_hash`` collision, generation is retried with fresh randomness
    — uniqueness is enforced by the database, not hoped for.
    """
    from sqlalchemy.exc import IntegrityError

    from backend.auth.models import TenantQuota, current_usage_month

    normalized = validate_name(name)
    if normalized is None:
        raise ValueError("invalid key name")
    if environment not in ENVIRONMENTS:
        raise ValueError("invalid environment")

    last_error: Optional[Exception] = None
    for _attempt in range(_MAX_GENERATION_ATTEMPTS):
        raw_key, key_prefix, key_hash = generate_raw_key(environment)
        now = datetime.now(timezone.utc)
        key_id = str(uuid.uuid4())
        try:
            with _session() as session:
                session.add(
                    api_key_model()(
                        id=key_id,
                        tenant_id=tenant_id,
                        name=normalized,
                        key_prefix=key_prefix,
                        key_hash=key_hash,
                        environment=environment,
                        status="ACTIVE",
                        created_at=now,
                    )
                )
                # Companion admission credential — the same default
                # monthly limit as the model/dev-seed convention.
                session.add(
                    TenantQuota(
                        api_key_hash=key_hash,
                        tenant_id=tenant_id,
                        monthly_limit=100,
                        current_month_usage=0,
                        usage_month=current_usage_month(),
                        is_active=True,
                    )
                )
                session.commit()
            return {
                "id": key_id,
                "name": normalized,
                "environment": environment,
                "key": raw_key,
                "key_prefix": key_prefix,
                "status": "ACTIVE",
                "created_at": now.isoformat(),
            }
        except IntegrityError as exc:
            # Hash collision (2^-256) or a concurrent duplicate — retry
            # with freshly generated randomness; never overwrite.
            last_error = exc
    raise ApiKeyStoreError("key generation failed after retries") from last_error


def list_api_keys(tenant_id: str) -> List[dict]:
    """All keys for ONE tenant, created_at DESC / id DESC (deterministic).

    Returns masked metadata only. ``last_used_at`` is derived read-only
    from the companion quota row's real ``last_request_at`` — never
    invented, and never written by this read path.
    """
    from sqlalchemy import select

    from backend.auth.models import TenantQuota

    model = api_key_model()
    try:
        with _session() as session:
            rows = (
                session.execute(
                    select(model)
                    .where(model.tenant_id == tenant_id)
                    .order_by(model.created_at.desc(), model.id.desc())
                )
                .scalars()
                .all()
            )
            hashes = [row.key_hash for row in rows]
            usage_map: dict = {}
            if hashes:
                usage_rows = session.execute(
                    select(TenantQuota.api_key_hash, TenantQuota.last_request_at).where(
                        TenantQuota.api_key_hash.in_(hashes)
                    )
                ).all()
                usage_map = {h: ts for h, ts in usage_rows}
    except ApiKeyStoreError:
        raise
    except Exception as exc:
        raise ApiKeyStoreError("key-management lookup failed") from exc

    return [
        _safe_view(row, usage_map.get(row.key_hash))
        for row in rows
    ]


def revoke_api_key(tenant_id: str, key_id: str) -> Tuple[Optional[dict], str]:
    """Revoke durably and idempotently. Returns ``(view_or_none, code)``.

    One transaction flips BOTH the registry status and the companion
    quota row's ``is_active`` — revocation takes effect atomically at
    commit; there is no window where a revoked key still authenticates.
    Repeating a revoke returns the same deterministic REVOKED view.
    """
    from backend.auth.models import TenantQuota
    from sqlalchemy import update

    if not _looks_like_uuid(key_id):
        return None, KEY_NOT_FOUND

    try:
        with _session() as session:
            row = _scoped_row(session, tenant_id, key_id, for_update=True)
            if row is None:
                return None, KEY_NOT_FOUND
            if row.status == "REVOKED":
                session.rollback()  # idempotent no-op
                return _safe_view(row, _last_used_for(session, row.key_hash)), "OK"

            row.status = "REVOKED"
            row.revoked_at = datetime.now(timezone.utc)
            quota_result = session.execute(
                update(TenantQuota)
                .where(TenantQuota.api_key_hash == row.key_hash)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
            if (quota_result.rowcount or 0) != 1:
                # A registry row without its companion would leave a
                # credential that still authenticates — abort everything.
                session.rollback()
                raise ApiKeyStoreError("companion quota row missing; revocation aborted")
            session.commit()
            return _safe_view(row, None), "OK"
    except ApiKeyStoreError:
        raise
    except Exception as exc:
        raise ApiKeyStoreError("revocation failed") from exc


def rotate_api_key(tenant_id: str, key_id: str) -> Tuple[Optional[dict], str]:
    """Atomically replace a key's credential. ``(view_or_none, code)``.

    Single transaction: SELECT ... FOR UPDATE the tenant-scoped row →
    generate a fresh cryptographically random secret → replace
    ``key_hash``/``key_prefix`` → repoint the companion quota row at the
    new hash (usage reset — a rotated credential starts a fresh month
    bucket, the honest semantics of a new credential) → commit. The old
    raw key stops authenticating at commit; the new raw key is returned
    EXACTLY ONCE; the old secret is unrecoverable (only its hash ever
    existed, and that hash row is gone).
    """
    from backend.auth.models import TenantQuota, current_usage_month
    from sqlalchemy import update

    if not _looks_like_uuid(key_id):
        return None, KEY_NOT_FOUND

    try:
        with _session() as session:
            row = _scoped_row(session, tenant_id, key_id, for_update=True)
            if row is None:
                return None, KEY_NOT_FOUND
            if row.status == "REVOKED":
                return None, KEY_REVOKED

            environment = row.environment
            raw_key, key_prefix, key_hash = generate_raw_key(environment)
            old_hash = row.key_hash
            row.key_hash = key_hash
            row.key_prefix = key_prefix
            row.status = "ACTIVE"
            row.revoked_at = None

            quota_result = session.execute(
                update(TenantQuota)
                .where(TenantQuota.api_key_hash == old_hash)
                .values(
                    api_key_hash=key_hash,
                    is_active=True,
                    current_month_usage=0,
                    usage_month=current_usage_month(),
                )
                .execution_options(synchronize_session=False)
            )
            if (quota_result.rowcount or 0) != 1:
                session.rollback()
                raise ApiKeyStoreError("companion quota row missing; rotation aborted")
            session.commit()
            return {
                "id": row.id,
                "name": row.name,
                "environment": environment,
                "key": raw_key,
                "key_prefix": key_prefix,
                "status": "ACTIVE",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }, "OK"
    except ApiKeyStoreError:
        raise
    except Exception as exc:
        raise ApiKeyStoreError("rotation failed") from exc
