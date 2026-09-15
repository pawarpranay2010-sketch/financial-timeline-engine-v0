"""SQLAlchemy ORM row for the Phase 18 ExecutionEvidence store.

Registered on the SAME declarative Base as every other Platrixa model
(backend/database/db.py) — the existing persistence boundary, not a second
subsystem. Mirrors backend/database/platrixa_execution_evidence_schema.sql
exactly (the DDL is authoritative for production; this model is the ORM
view used by backend/semantics/persistence.py).

Import-safety note: importing backend.database.db requires DATABASE_URL
(pre-existing behavior), so this module is imported LAZILY by the store —
never at package import time.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB

from backend.database.db import Base
# The store's model is registered on the app's existing declarative Base
# (the single shared persistence boundary).


class ExecutionEvidenceRow(Base):
    """One immutable, chain-linked ExecutionEvidence record."""

    __tablename__ = "platrixa_execution_evidence"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(128), nullable=False, index=True)
    evidence_schema_version = Column(String(32), nullable=False)
    input_hash = Column(String(64), nullable=False)
    candidate_interpretation_hash = Column(String(64), nullable=False)
    grounded_interpretation_hash = Column(String(64), nullable=False)
    model_identity = Column(JSONB, nullable=False, default=dict)
    adapter_identity = Column(JSONB, nullable=False, default=dict)
    schema_version = Column(String(64), nullable=False, default="")
    prompt_version = Column(String(64), nullable=False, default="")
    grounding_version = Column(String(64), nullable=False, default="")
    accounting_version = Column(String(92), nullable=False, default="")
    rule_pack_hash = Column(String(64), nullable=False, default="")
    rule_evidence = Column(JSONB, nullable=False, default=list)
    final_state = Column(String(32), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    chain_digest = Column(String(64), nullable=False)
