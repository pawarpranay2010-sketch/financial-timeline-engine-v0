"""Idempotent DDL initialisation for the ExecutionEvidence store (Phase 18).

Applies backend/database/platrixa_execution_evidence_schema.sql, following
the repository's init-script convention (init_metering.py, Phase 16).
Safe to run repeatedly.

Run:
    DATABASE_URL=postgresql://... python -m backend.semantics.init_evidence_store
"""

from __future__ import annotations

import sys
from pathlib import Path

_DDL_PATH = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "platrixa_execution_evidence_schema.sql"
)


def initialise_evidence_table() -> bool:
    """Apply the evidence-table DDL. Returns True on success."""
    import os

    from sqlalchemy import create_engine, text

    url = (os.getenv("DATABASE_URL", "") or "").strip()
    if not url:
        print("❌ DATABASE_URL is not set; nothing to initialise.")
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
        ok = "platrixa_execution_evidence" in tables
        print(
            "✅ platrixa_execution_evidence initialised."
            if ok
            else "❌ table missing after DDL"
        )
        return ok
    except Exception as exc:
        print(f"❌ evidence schema initialisation failed: {type(exc).__name__}")
        return False


if __name__ == "__main__":
    sys.exit(0 if initialise_evidence_table() else 1)
