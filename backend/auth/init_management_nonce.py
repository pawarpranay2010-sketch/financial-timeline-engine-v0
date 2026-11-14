"""Idempotent DDL initialisation for the management assertion nonce store.

Applies backend/database/platrixa_management_nonce_schema.sql to the metering
store, following the same convention as init_visitor_nonce.py. Safe to run
repeatedly.

Run:
    PLATRIXA_METERING_DATABASE_URL=postgresql://... \
        python -m backend.auth.init_management_nonce
"""

from __future__ import annotations

import sys
from pathlib import Path

_DDL_PATH = Path(__file__).resolve().parent.parent / "database" / "platrixa_management_nonce_schema.sql"


def initialise_management_nonce_schema() -> bool:
    """Apply the management nonce DDL. Returns True on success."""
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
        ok = "management_assertion_nonces" in tables
        print("✅ management_assertion_nonces initialised." if ok else "❌ table missing after DDL")
        return ok
    except Exception as exc:
        print(f"❌ management nonce schema initialisation failed: {type(exc).__name__}")
        return False


if __name__ == "__main__":
    sys.exit(0 if initialise_management_nonce_schema() else 1)
