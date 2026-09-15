"""Idempotent DDL initialisation for the metered gate (Phase 16).

Applies backend/database/platrixa_tenant_quota_schema.sql to the
metering store, following the repository's existing init-script
convention (see fyjc_init_db.py). Safe to run repeatedly.

Run:
    PLATRIXA_METERING_DATABASE_URL=postgresql://... \
        python -m backend.auth.init_metering
"""

from __future__ import annotations

import sys
from pathlib import Path

_DDL_PATH = Path(__file__).resolve().parent.parent / "database" / "platrixa_tenant_quota_schema.sql"


def initialise_tenant_quota_table() -> bool:
    """Apply the quota-table DDL. Returns True on success."""
    import os

    from sqlalchemy import create_engine, text

    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL", "") or "").strip()
    if not url:
        print("❌ PLATRIXA_METERING_DATABASE_URL is not set; nothing to initialise.")
        return False
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    ddl = _DDL_PATH.read_text(encoding="utf-8")
    try:
        engine = create_engine(url, future=True)
        with engine.begin() as conn:
            conn.execute(text(ddl))
        from sqlalchemy import inspect

        tables = inspect(engine).get_table_names()
        ok = "platrixa_tenant_quotas" in tables
        print("✅ platrixa_tenant_quotas initialised." if ok else "❌ table missing after DDL")
        return ok
    except Exception as exc:
        print(f"❌ metering schema initialisation failed: {type(exc).__name__}")
        return False


if __name__ == "__main__":
    sys.exit(0 if initialise_tenant_quota_table() else 1)
