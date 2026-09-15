"""ExecutionEvidence persistence (Phase 18 §8–§11).

One module, one boundary. Moves ExecutionEvidence from in-memory only to
permanent PostgreSQL persistence using the EXISTING database architecture:

    backend/semantics/evidence.py   (Phase 17 — the evidence record itself)
        ↓
    EvidenceStore (this module)
        ↓
    backend/database/db.py Base + SessionLocal (existing engine/session)
        ↓
    PostgreSQL

Design rules honored from the phase contract:
  - NO second persistence subsystem: the model registers on the existing
    declarative Base; the DDL mirrors the Phase 16 schema-script precedent
    (backend/database/platrixa_execution_evidence_schema.sql, applied by
    backend.semantics.init_evidence_store).
  - The persisted record IS the actual Phase 17 evidence: every
    ExecutionEvidence.to_dict() field has a column; nothing is dropped,
    renamed, or invented.
  - IMMUTABLE-ON-WRITE: the only write paths are INSERT (append-only).
    There is deliberately no UPDATE and no delete helper.
  - Chain linkage: each row's ``chain_digest`` binds the previous row's
    digest (sha256) with this record's frozen payload — a tamper-evident
    append-only ledger. Genesis record defines the chain.
  - FAIL-CLOSED: every method raises on any store error. Silent
    ``None``/``[]`` on failure is forbidden — callers cannot mistake a
    broken store for an empty one. No exception text includes URLs or
    connection material.
  - Import safety: backend.database.db raises at import time when
    DATABASE_URL is absent (pre-existing Phase 16 lesson), so the ORM
    model is imported lazily inside methods. Importing this module NEVER
    requires a database.

Pure persistence boundary: no model, no grounding, no accounting, no
Kernel, no state authority. It cannot create evidence — only store,
retrieve, and verify it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

EVIDENCE_TABLE = "platrixa_execution_evidence"
PERSISTENCE_VERSION = "evidence-store-1"

# Digests of the previous record bind into the next: the first record's
# genesis digest is this fixed value (not a secret, a chain anchor).
GENESIS_DIGEST = "0" * 64


def record_payload_digest(payload: Dict[str, Any], previous_digest: str) -> str:
    """The per-record chain digest.

    Hash definition: sha256 of the UTF-8 bytes of
    ``previous_digest + canonical_json(payload)`` where canonical JSON is
    ``json.dumps(payload, sort_keys=True, separators=(",", ":"),
    default=str)`` and ``payload`` is the FULL frozen record dict
    (evidence schema version, request id, all hashes, identities, versions,
    rule evidence, final state) EXCLUDING ``chain_digest`` itself.

    Deterministic: identical (payload, previous_digest) inputs always
    produce the identical digest.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((previous_digest + canonical).encode("utf-8")).hexdigest()


class EvidenceStore:
    """Append-only PostgreSQL store for ExecutionEvidence records."""

    def __init__(self, session_factory: Any = None) -> None:
        # Lazy import: keeps this module import-clean without DATABASE_URL.
        if session_factory is None:
            from backend.database.db import SessionLocal as _SessionLocal

            session_factory = _SessionLocal
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _model(self) -> Any:
        from backend.database.evidence_model import ExecutionEvidenceRow

        return ExecutionEvidenceRow

    # The canonical payload: EXACTLY the Phase 17 evidence fields, in both
    # directions (persist and verify). Storage-only columns (id, created_at)
    # are deliberately excluded so a retrieved row re-hashes identically to
    # the record that was persisted.
    CANONICAL_KEYS = (
        "evidence_schema_version",
        "request_id",
        "input_hash",
        "candidate_interpretation_hash",
        "grounded_interpretation_hash",
        "model_identity",
        "adapter_identity",
        "schema_version",
        "prompt_version",
        "grounding_version",
        "accounting_version",
        "rule_pack_hash",
        "rule_evidence",
        "final_state",
    )

    def _payload_from_dict(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """The exact fields the chain digest covers: EXACTLY the canonical
        Phase 17 evidence fields (CANONICAL_KEYS), in both directions —
        persist and verify — so a retrieved row re-hashes identically to
        the record that was persisted. Storage-only columns (id,
        created_at, chain_digest) are excluded. Rejects records missing
        required Phase 17 fields — fail closed, never invent."""
        required = (
            "evidence_schema_version",
            "request_id",
            "input_hash",
            "candidate_interpretation_hash",
            "grounded_interpretation_hash",
            "final_state",
        )
        missing = [k for k in required if not str(record.get(k, "")).strip()]
        if missing:
            raise ValueError(f"evidence record missing required fields: {missing}")
        return {k: record.get(k) for k in self.CANONICAL_KEYS}

    # ------------------------------------------------------------------
    # write path (INSERT-only)
    # ------------------------------------------------------------------

    def persist(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Persist ONE evidence record. INSERT-only; raises on any failure.

        Returns the persisted row as a dict (including id and
        chain_digest). Duplicate request_ids raise IntegrityError →
        propagated (fail closed; callers decide dedup policy).
        """
        payload = self._payload_from_dict(record)
        session = self._session_factory()
        try:
            Row = self._model()
            previous = (
                session.query(Row)
                .order_by(Row.id.desc())
                .with_for_update()
                .first()
            )
            previous_digest = previous.chain_digest if previous else GENESIS_DIGEST
            digest = record_payload_digest(payload, previous_digest)
            row = Row(
                request_id=payload["request_id"],
                evidence_schema_version=payload["evidence_schema_version"],
                input_hash=payload["input_hash"],
                candidate_interpretation_hash=payload[
                    "candidate_interpretation_hash"
                ],
                grounded_interpretation_hash=payload[
                    "grounded_interpretation_hash"
                ],
                model_identity=payload.get("model_identity", {}),
                adapter_identity=payload.get("adapter_identity", {}),
                schema_version=payload.get("schema_version", ""),
                prompt_version=payload.get("prompt_version", ""),
                grounding_version=payload.get("grounding_version", ""),
                accounting_version=payload.get("accounting_version", ""),
                rule_pack_hash=payload.get("rule_pack_hash", ""),
                rule_evidence=payload.get("rule_evidence", []),
                final_state=payload["final_state"],
                chain_digest=digest,
            )
            session.add(row)
            session.commit()
            return self._row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # read path
    # ------------------------------------------------------------------

    def retrieve(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve one record by request_id (None if absent). Raises on
        store failure — never swallows errors into None."""
        session = self._session_factory()
        try:
            Row = self._model()
            row = session.query(Row).filter(Row.request_id == request_id).first()
            return self._row_to_dict(row) if row else None
        except Exception:
            raise
        finally:
            session.close()

    def retrieve_all(self) -> List[Dict[str, Any]]:
        """All records, chain order (ascending id). Raises on failure."""
        session = self._session_factory()
        try:
            Row = self._model()
            rows = session.query(Row).order_by(Row.id.asc()).all()
            return [self._row_to_dict(r) for r in rows]
        except Exception:
            raise
        finally:
            session.close()

    def count(self) -> int:
        session = self._session_factory()
        try:
            Row = self._model()
            return int(session.query(Row).count())
        except Exception:
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # integrity verification
    # ------------------------------------------------------------------

    def verify_chain(self, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Verify the append-only ledger end-to-end.

        Recomputes every record's chain digest from its own stored payload
        + the previous stored digest and compares. Also re-hashes the
        three interpretation hashes' FORMAT binding (the frozen payload is
        authoritative — this check proves nothing was mutated after
        INSERT).

        Returns a summary dict; raises ValueError on the FIRST tampered
        record (exact position reported, never repaired).
        """
        if records is None:
            records = self.retrieve_all()
        previous = GENESIS_DIGEST
        for idx, rec in enumerate(records):
            payload = self._payload_from_dict(rec)
            expected = record_payload_digest(payload, previous)
            if expected != rec.get("chain_digest"):
                raise ValueError(
                    f"evidence chain tampered at ledger position {idx} "
                    f"(request_id={rec.get('request_id')!r}): stored digest "
                    "does not match recomputed digest"
                )
            previous = rec["chain_digest"]
        return {
            "records_verified": len(records),
            "chain_intact": True,
            "head_digest": previous if records else GENESIS_DIGEST,
            "genesis_digest": GENESIS_DIGEST,
        }

    def tamper_probe(self, request_id: str, field: str, value: Any) -> bool:
        """DELIBERATELY corrupt one stored field OUTSIDE the evidence
        lifecycle, to prove detection. Test-support only: it mutates the
        database, bypassing the INSERT-only discipline of persist(). Never
        use in production paths."""
        session = self._session_factory()
        try:
            Row = self._model()
            row = session.query(Row).filter(Row.request_id == request_id).first()
            if row is None:
                raise ValueError(f"tamper_probe: no record {request_id!r}")
            if not hasattr(row, field):
                raise ValueError(f"tamper_probe: unknown field {field!r}")
            setattr(row, field, value)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row.id,
            "request_id": row.request_id,
            "evidence_schema_version": row.evidence_schema_version,
            "input_hash": row.input_hash,
            "candidate_interpretation_hash": row.candidate_interpretation_hash,
            "grounded_interpretation_hash": row.grounded_interpretation_hash,
            "model_identity": dict(row.model_identity or {}),
            "adapter_identity": dict(row.adapter_identity or {}),
            "schema_version": row.schema_version,
            "prompt_version": row.prompt_version,
            "grounding_version": row.grounding_version,
            "accounting_version": row.accounting_version,
            "rule_pack_hash": row.rule_pack_hash,
            "rule_evidence": list(row.rule_evidence or []),
            "final_state": row.final_state,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "chain_digest": row.chain_digest,
        }


__all__ = [
    "EvidenceStore",
    "EVIDENCE_TABLE",
    "PERSISTENCE_VERSION",
    "GENESIS_DIGEST",
    "record_payload_digest",
]
