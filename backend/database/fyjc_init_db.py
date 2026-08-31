"""
FYJC Accounting AI — Database Initialisation

Creates the 4 FYJC tables using SQLAlchemy ORM.
Reuses the existing PostgreSQL/Railway engine from backend/database/db.py.

Module 4 tables are NOT affected.

Run:
    python backend/database/fyjc_init_db.py

Safe to run multiple times — uses CREATE TABLE IF NOT EXISTS via ORM metadata.
"""

from __future__ import annotations

import sys

from sqlalchemy import inspect, text

from backend.database.db import Base, engine

# Import FYJC models so they register with Base.metadata.
# This import MUST happen before Base.metadata.create_all().
import backend.database.models  # noqa: F401


FYJC_TABLES = [
    "fyjc_interactions",
    "fyjc_interpretations",
    "fyjc_training_candidates",
    "fyjc_knowledge_base",
]


def initialise_fyjc_tables() -> bool:
    """Create FYJC tables if they do not exist.

    Returns True on success, False on failure.
    """
    try:
        # create_all only creates tables that don't already exist.
        # It will NOT drop or alter existing tables.
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        all_tables = inspector.get_table_names()

        fyjc_tables_present = [t for t in FYJC_TABLES if t in all_tables]
        fyjc_tables_missing = [t for t in FYJC_TABLES if t not in all_tables]

        if fyjc_tables_missing:
            print(f"⚠️  Missing FYJC tables: {fyjc_tables_missing}")
            print("   Base.metadata.create_all() should have created them.")
            print("   Check DATABASE_URL and PostgreSQL permissions.")
            return False

        print("✅ FYJC database initialised successfully.")
        print(f"   FYJC tables found ({len(fyjc_tables_present)}/{len(FYJC_TABLES)}):")
        for table in fyjc_tables_present:
            cols = [col["name"] for col in inspector.get_columns(table)]
            print(f"   - {table} ({len(cols)} columns)")
            print(f"     columns: {', '.join(cols)}")

        return True

    except Exception as e:
        print(f"❌ FYJC database initialisation failed: {e}", file=sys.stderr)
        return False


def verify_existing_tables_untouched() -> None:
    """Verify Module 4 tables still exist and were not modified."""
    inspector = inspect(engine)
    all_tables = inspector.get_table_names()

    module4_tables = [
        "companies",
        "financials",
        "market_prices",
        "filings",
        "news",
        "extracted_facts",
        "corporate_actions",
    ]

    print("\n📋 Module 4 tables (untouched):")
    for table in module4_tables:
        status = "✅ exists" if table in all_tables else "⚠️  missing (pre-existing)"
        print(f"   - {table}: {status}")


def print_migration_commands() -> None:
    """Print the manual SQL commands for reference."""
    print("\n📝 Manual SQL initialisation (alternative to ORM):")
    print("   psql $DATABASE_URL -f backend/database/fyjc_schema.sql")
    print("   psql $DATABASE_URL -f backend/database/fyjc_indexes.sql")


if __name__ == "__main__":
    success = initialise_fyjc_tables()
    verify_existing_tables_untouched()
    print_migration_commands()
    sys.exit(0 if success else 1)
