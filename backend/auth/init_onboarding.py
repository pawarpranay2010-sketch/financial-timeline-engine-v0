"""Idempotent DDL initialisation for self-service developer onboarding.

Applies backend/database/platrixa_developer_onboarding_schema.sql to the
onboarding/metering store, following the repository's existing init-script
convention (init_metering.py, init_visitor_schema.py). Safe to re-run; the DDL
contains no destructive statement.

Modes:
  * default: apply the full DDL (CREATE TABLE/INDEX IF NOT EXISTS) plus
    additive column migrations for older databases.
  * --verify: read-only compatibility check (no DDL, no writes). Returns
    non-zero when the schema is missing or older than this build.

The two credential tables it depends on (``platrixa_api_keys``,
``platrixa_tenant_quotas``) are created by their own init modules and are NOT
redefined here. Run order for a fresh store:

    python -m backend.auth.init_metering       # quota table
    python -m backend.auth.init_onboarding     # onboarding tables

Run:
    PLATRIXA_METERING_DATABASE_URL=postgresql://... \\
        python -m backend.auth.init_onboarding [--verify]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_DDL_PATH = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "platrixa_developer_onboarding_schema.sql"
)

_EXPECTED_TABLES = (
    "platrixa_developer_access_requests",
    "platrixa_developer_email_verifications",
    "platrixa_developer_key_deliveries",
    "platrixa_developer_notifications",
    "platrixa_developer_onboarding_audit",
)

# Additive column migrations for databases created by an earlier build. Each
# entry is (table, column, sql_type). Applied only when the column is absent.
_ADDITIVE_COLUMNS = (
    ("platrixa_developer_key_deliveries", "secret_version", "INTEGER NOT NULL DEFAULT 1"),
    ("platrixa_developer_notifications", "claim_token", "VARCHAR(128)"),
    ("platrixa_developer_notifications", "claimed_at", "TIMESTAMP"),
    ("platrixa_developer_notifications", "lease_expires_at", "TIMESTAMP"),
    ("platrixa_developer_submission_handles", "last_used_at", "TIMESTAMP"),
    ("platrixa_developer_submission_handles", "revoked_at", "TIMESTAMP"),
)


def _db_url() -> str:
    return (
        (os.getenv("PLATRIXA_ONBOARDING_DATABASE_URL", "") or "").strip()
        or (os.getenv("PLATRIXA_METERING_DATABASE_URL", "") or "").strip()
    )


def _normalise_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def verify_onboarding_schema() -> bool:
    """Read-only compatibility check. No DDL, no writes."""
    import os as _os

    url = _db_url()
    if not url:
        print("❌ PLATRIXA_METERING_DATABASE_URL is not set; nothing to verify.")
        return False
    try:
        from backend.onboarding.store import verify_schema

        verify_schema(url, fresh=True)
        print("✅ onboarding schema verified.")
        return True
    except Exception as exc:
        print(f"❌ onboarding schema verification failed: {type(exc).__name__}: {exc}")
        return False


def initialise_onboarding_schema(*, verify_only: bool = False) -> bool:
    """Apply the onboarding DDL. Returns True on success."""
    url = _db_url()
    if not url:
        print("❌ PLATRIXA_METERING_DATABASE_URL is not set; nothing to initialise.")
        return False
    if verify_only:
        return verify_onboarding_schema()

    url = _normalise_url(url)
    from sqlalchemy import create_engine, inspect, text

    from backend.onboarding.store import split_sql

    statements = split_sql(_DDL_PATH.read_text(encoding="utf-8"))
    try:
        engine = create_engine(url, future=True)
        with engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
            # Additive column migrations for older databases.
            inspector = inspect(engine)
            for table, column, sql_type in _ADDITIVE_COLUMNS:
                if table not in inspector.get_table_names():
                    continue
                present = {c["name"] for c in inspector.get_columns(table)}
                if column in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"))
        tables = set(inspect(engine).get_table_names())
        missing = [table for table in _EXPECTED_TABLES if table not in tables]
        if missing:
            print(f"❌ tables missing after DDL: {', '.join(missing)}")
            return False
        credentials = {"platrixa_tenant_quotas", "platrixa_api_keys"} & tables
        print(f"✅ onboarding schema initialised ({len(_EXPECTED_TABLES)} tables).")
        if "platrixa_api_keys" not in credentials:
            print(
                "⚠️  platrixa_api_keys is absent: run the Phase 5G key-registry "
                "initialisation before provisioning."
            )
        return True
    except Exception as exc:
        print(f"❌ onboarding schema initialisation failed: {type(exc).__name__}")
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa onboarding schema init")
    parser.add_argument("--verify", action="store_true", help="Read-only compatibility check")
    args = parser.parse_args(argv)
    return 0 if initialise_onboarding_schema(verify_only=args.verify) else 1


if __name__ == "__main__":
    sys.exit(main())
