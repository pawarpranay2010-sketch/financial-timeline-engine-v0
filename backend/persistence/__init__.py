"""
Platrixa — Persistence boundary package (Phase 7E)

Public surface:

    ResultPersistence          — storage-side contract (Protocol)
    PersistedResult            — read-back projection of a stored record
    PersistenceFailure         — storage-side failure (fail-closed)
    PERSISTENCE_UNAVAILABLE    — failure kind: store unreachable
    PERSISTENCE_WRITE_FAILED   — failure kind: write rejected/failed
    InMemoryResultPersistence  — reference implementation (tests/dev)
    PostgresResultPersistence  — existing-FYJC-schema implementation

The Kernel depends only on `base` (the contract). It never imports the
PostgreSQL implementation, keeping the dependency direction clean:

    Kernel → backend.persistence.base (contract)
    backend.persistence.postgres → backend.persistence.base
"""

from backend.persistence.base import (
    PERSISTENCE_UNAVAILABLE,
    PERSISTENCE_WRITE_FAILED,
    InMemoryResultPersistence,
    PersistedResult,
    PersistenceFailure,
    ResultPersistence,
    build_persistence_record,
    roundtrip_snapshot,
)

def __getattr__(name: str):
    """
    Lazily expose the concrete PostgreSQL implementation.

    backend.persistence.postgres imports the existing database models, whose
    engine module requires DATABASE_URL at import time. Importing it eagerly
    here would make `import backend.persistence` fail in environments without
    a configured database (including contract-only consumers such as the
    Kernel and its tests). The lazy hook keeps the contract import-safe while
    still making `from backend.persistence import PostgresResultPersistence`
    work where the database is actually used.
    """
    if name == "PostgresResultPersistence":
        from backend.persistence.postgres import PostgresResultPersistence

        return PostgresResultPersistence
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "PERSISTENCE_UNAVAILABLE",
    "PERSISTENCE_WRITE_FAILED",
    "PersistedResult",
    "PersistenceFailure",
    "ResultPersistence",
    "InMemoryResultPersistence",
    "PostgresResultPersistence",
    "build_persistence_record",
    "roundtrip_snapshot",
]
