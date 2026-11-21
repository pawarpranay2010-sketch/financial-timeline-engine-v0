"""Persistence for developer onboarding.

One small layer over the metering store. SQLAlchemy Core (no ORM session
identity map) keeps the SQL explicit and lets the SAME code run on PostgreSQL
in production and SQLite in tests — the schema file is portable by design.

The two credential tables (``platrixa_api_keys``, ``platrixa_tenant_quotas``)
are declared here as Core tables over their EXISTING production shape. They are
written in the same transaction as the application state, which is what makes
provisioning atomic and idempotent: either the tenant, its one key, its quota
row, its delivery row and the ACTIVE status all commit, or none of them do.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from backend.onboarding import config

_DDL_PATH = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "platrixa_developer_onboarding_schema.sql"
)

metadata = MetaData()

access_requests = Table(
    "platrixa_developer_access_requests",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), nullable=False),
    Column("name", String(120), nullable=False),
    Column("company", String(160), nullable=False),
    Column("building", String(1000), nullable=False),
    Column("expected_usage", String(40), nullable=False),
    Column("profile_url", String(300)),
    Column("status", String(32), nullable=False),
    Column("status_reason", String(64)),
    Column("email_verified_at", DateTime),
    Column("tenant_id", String(64)),
    Column("key_id", String(36)),
    Column("monthly_limit", Integer),
    Column("client_ip_hash", String(64)),
    Column("visitor_id", String(64)),
    Column("provisioning_attempts", Integer, nullable=False),
    Column("last_error", String(120)),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

verifications = Table(
    "platrixa_developer_email_verifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("application_id", String(36), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("consumed_at", DateTime),
    Column("attempts", Integer, nullable=False),
)

key_deliveries = Table(
    "platrixa_developer_key_deliveries",
    metadata,
    Column("key_id", String(36), primary_key=True),
    Column("application_id", String(36), nullable=False),
    Column("tenant_id", String(64), nullable=False),
    Column("key_prefix", String(20), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("key_nonce", String(64)),
    Column("reveal_token_hash", String(64), nullable=False),
    Column("secret_version", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("revealed_at", DateTime),
    Column("voided_at", DateTime),
)

# One opaque, unguessable reference per ACCEPTED SUBMISSION (not per email).
# It is the capability the developer uses with GET /v1/developer/access-status:
# knowing it proves the caller submitted that address. It is not the
# application id, not a tenant id and not a token, and it grants no other
# access.
submission_handles = Table(
    "platrixa_developer_submission_handles",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("application_id", String(36), nullable=False),
    Column("email", String(320), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("last_used_at", DateTime),
    Column("revoked_at", DateTime),
)

notifications = Table(
    "platrixa_developer_notifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("application_id", String(36)),
    Column("audience", String(16), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("recipient", String(320)),
    Column("subject", String(200), nullable=False),
    Column("body_text", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime),
    Column("last_error", String(80)),
    Column("dedupe_key", String(200), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("sent_at", DateTime),
    # Dispatcher claim metadata. A worker may only acknowledge a row while it
    # still owns the current claim token and the lease has not expired.
    Column("claimed_at", DateTime),
    Column("claim_token", String(36)),
    Column("lease_expires_at", DateTime),
)

audit = Table(
    "platrixa_developer_onboarding_audit",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("actor", String(128), nullable=False),
    Column("action", String(64), nullable=False),
    Column("application_id", String(36)),
    Column("tenant_id", String(64)),
    Column("detail", String(500)),
    Column("created_at", DateTime, nullable=False),
)

# Existing production tables (Phase 5G registry + Phase 16 admission rows).
api_keys = Table(
    "platrixa_api_keys",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("name", String(120), nullable=False),
    Column("key_prefix", String(20), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("environment", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("last_used_at", DateTime),
    Column("revoked_at", DateTime),
)

tenant_quotas = Table(
    "platrixa_tenant_quotas",
    metadata,
    Column("api_key_hash", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("monthly_limit", Integer, nullable=False),
    Column("current_month_usage", Integer, nullable=False),
    Column("usage_month", String(7), nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("last_request_at", DateTime),
)


def utcnow() -> datetime:
    """Naive UTC — the columns are TIMESTAMP (no tz) in both engines."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_ENGINES: dict = {}
_FACTORIES: dict = {}
_SCHEMA_READY: set = set()
_LOCK = threading.Lock()


def _normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite:///") and "+" not in url.split("://", 1)[1]:
        return url.replace("sqlite:///", "sqlite+pysqlite:///", 1)
    return url


def get_engine(url: Optional[str] = None) -> Engine:
    """Cached engine for the configured (or explicitly supplied) store."""
    target = _normalize_url(url or config.database_url())
    if not target:
        raise RuntimeError("onboarding store is not configured")
    with _LOCK:
        engine = _ENGINES.get(target)
        if engine is not None:
            return engine
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if target.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        engine = create_engine(target, **kwargs)
        if target.startswith("sqlite"):

            @event.listens_for(engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        _ENGINES[target] = engine
        _FACTORIES[target] = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        return engine


def reset_cache() -> None:
    """Test hook: forget engines/factories/schema flags."""
    with _LOCK:
        for engine in _ENGINES.values():
            engine.dispose()
        _ENGINES.clear()
        _FACTORIES.clear()
        _SCHEMA_READY.clear()


@contextmanager
def session(url: Optional[str] = None) -> Iterator[Session]:
    """One transaction. Commit on clean exit, roll back on any exception."""
    target = _normalize_url(url or config.database_url())
    with _LOCK:
        factory = _FACTORIES.get(target)
    if factory is None:
        get_engine(url)
        with _LOCK:
            factory = _FACTORIES[target]
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def split_sql(ddl: str) -> list[str]:
    """Statements in a comment-free SQL file.

    Full-line ``--`` comments are removed BEFORE splitting: comment prose
    contains semicolons ("no Alembic; version-controlled DDL"), and splitting
    first used to produce a fragment starting mid-sentence.
    """
    body = "\n".join(
        line for line in ddl.splitlines() if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in body.split(";") if statement.strip()]


class SchemaNotReady(RuntimeError):
    """The onboarding schema is absent or older than this build.

    Raised by RUNTIME code instead of attempting DDL: a service account must
    not need schema-owner privileges. Apply the migration deliberately with
    ``python -m backend.auth.init_onboarding``.
    """

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__("onboarding schema is not ready: " + ", ".join(self.missing))


# Tables/columns runtime code requires to operate at all. Kept here (not only
# in the DDL) so a partially-migrated database fails with an explicit,
# actionable error instead of a random 500 mid-transaction.
_REQUIRED_COLUMNS = {
    "platrixa_developer_access_requests": (
        "id", "email", "status", "email_verified_at", "tenant_id", "key_id",
        "monthly_limit", "client_ip_hash", "visitor_id", "created_at",
        "updated_at", "expires_at",
    ),
    "platrixa_developer_email_verifications": (
        "id", "application_id", "token_hash", "expires_at", "consumed_at",
    ),
    "platrixa_developer_key_deliveries": (
        "key_id", "application_id", "tenant_id", "key_prefix", "key_hash",
        "key_nonce", "reveal_token_hash", "secret_version", "expires_at",
        "revealed_at", "voided_at",
    ),
    "platrixa_developer_notifications": (
        "id", "application_id", "audience", "kind", "recipient", "subject",
        "body_text", "status", "attempts", "next_attempt_at", "last_error",
        "dedupe_key", "created_at", "sent_at", "claimed_at", "claim_token",
        "lease_expires_at",
    ),
    "platrixa_developer_onboarding_audit": (
        "id", "actor", "action", "created_at",
    ),
    "platrixa_developer_submission_handles": (
        "id", "application_id", "email", "created_at", "last_used_at",
        "revoked_at",
    ),
}


def verify_schema(url: Optional[str] = None, *, fresh: bool = False) -> None:
    """Read-only compatibility check. No DDL, no writes, no elevated rights.

    ``fresh=True`` bypasses the per-process memo. The init script's --verify
    mode uses it so the answer always comes from the live catalogue rather
    than a check performed earlier in the same process.
    """
    from sqlalchemy import inspect

    engine = get_engine(url)
    key = str(engine.url)
    if not fresh:
        with _LOCK:
            if key in _SCHEMA_READY:
                return
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table, columns in _REQUIRED_COLUMNS.items():
        if table not in tables:
            missing.append(table)
            continue
        present = {column["name"] for column in inspector.get_columns(table)}
        missing.extend(f"{table}.{column}" for column in columns if column not in present)
    if missing:
        # A negative result invalidates any earlier positive memo.
        with _LOCK:
            _SCHEMA_READY.discard(key)
        raise SchemaNotReady(missing)
    with _LOCK:
        _SCHEMA_READY.add(key)


# Runtime entry point (explicit name used by the services).
def require_schema(url: Optional[str] = None) -> None:
    verify_schema(url)


def ensure_schema(url: Optional[str] = None) -> None:
    """Apply the idempotent DDL once per engine (safe to re-run).

    MIGRATION ONLY. Runtime code calls ``require_schema`` instead, so the
    application role never needs DDL privileges. The init script and tests
    call this deliberately.
    """
    engine = get_engine(url)
    key = str(engine.url)
    with _LOCK:
        if key in _SCHEMA_READY:
            return
    statements = split_sql(_DDL_PATH.read_text(encoding="utf-8"))
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
    with _LOCK:
        _SCHEMA_READY.add(key)


def lock_row(db: Session, table: Table, pk_value: str):
    """Row lock when the engine supports it (PostgreSQL), plain read otherwise.

    SQLite serialises writers itself, so ``FOR UPDATE`` is unnecessary there and
    would be a syntax error.
    """
    query = select(table).where(table.c.id == pk_value)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    return db.execute(query).mappings().first()
