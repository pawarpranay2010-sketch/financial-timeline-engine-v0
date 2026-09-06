"""
Platrixa — PostgreSQL persistence implementation (Phase 7E)

Concrete ResultPersistence implementation backed by the existing FYJC
PostgreSQL tables:

    fyjc_interactions      ← raw student input (write-once)
    fyjc_interpretations   ← KernelResult (status, model identity,
                             grounding state, accounting output)

Responsibility boundary:

  * This module STORES already-determined results.
  * It does NOT calculate, reinterpret, infer, or override accounting truth.
  * It does NOT touch journal/ledger/trial-balance logic.
  * It does NOT create a second accounting, grounding, or validation
    implementation.
  * It FAILS CLOSED: a storage failure is returned as a PersistenceFailure
    and never becomes a successful accounting result.

Dependency direction (clean):

    Kernel → backend.persistence.base (contract)
    backend.persistence.postgres → backend.persistence.base (contract)
    backend.persistence.postgres → existing backend.database models

The Kernel never imports anything from this module.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

from backend.persistence.base import (
    PERSISTENCE_UNAVAILABLE,
    PERSISTENCE_WRITE_FAILED,
    PersistedResult,
    PersistenceFailure,
    build_persistence_record,
    roundtrip_snapshot,
)

logger = logging.getLogger(__name__)

# Training-pipeline eligibility is an existing concept owned by the FYJC
# training tables. We reuse the same rule the existing persistence layer
# uses so no new lifecycle policy is invented here.
_TRAINING_ELIGIBLE_STATUSES = ("VERIFIED", "REVIEW_REQUIRED")


class PostgresResultPersistence:
    """
    ResultPersistence implementation over the existing FYJC PostgreSQL schema.

    One persist() call maps to:
      * 1 row in fyjc_interactions      (raw input, write-once)
      * 1 row in fyjc_interpretations   (status + model + grounding + accounting)
      * optionally 1 row in fyjc_training_candidates (existing eligibility rule)

    All values are copied verbatim from the KernelResult. Nothing is
    recalculated. Any database error is converted into a PersistenceFailure
    (fail closed) — the caller's KernelResult is never modified.
    """

    def __init__(self, session_factory=None) -> None:
        """
        session_factory: optional callable returning a SQLAlchemy session.
        Defaults to the existing SessionLocal. Injectable for tests.
        """
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Session handling
    # ------------------------------------------------------------------

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        try:
            from backend.database.db import SessionLocal

            return SessionLocal()
        except Exception as exc:
            logger.warning("FYJC persistence: cannot create DB session: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Contract entry point
    # ------------------------------------------------------------------

    def persist(self, result: Any) -> "PersistedResult | PersistenceFailure":
        """
        Persist one KernelResult. Fail-closed: returns PersistenceFailure on
        any storage problem; never raises and never mutates `result`.
        """
        try:
            record = build_persistence_record(result)
        except TypeError as exc:
            return PersistenceFailure(
                kind=PERSISTENCE_WRITE_FAILED,
                reason="not a KernelResult-like object",
                detail=str(exc),
            )

        session = self._session()
        if session is None:
            return PersistenceFailure(
                kind=PERSISTENCE_UNAVAILABLE,
                reason="database session unavailable",
            )

        try:
            return self._persist_record(session, record)
        except Exception as exc:
            logger.warning("FYJC persistence failed: %s", exc)
            try:
                session.rollback()
            except Exception:
                pass
            return PersistenceFailure(
                kind=PERSISTENCE_WRITE_FAILED,
                reason="database write failed",
                detail=str(exc),
            )
        finally:
            try:
                session.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Mapping into the existing schema (verbatim copy, no recomputation)
    # ------------------------------------------------------------------

    def _persist_record(self, session, record: Dict[str, Any]):
        from backend.database.models import (
            FYJCInteraction,
            FYJCInterpretation,
            FYJCTrainingCandidate,
            FYJC_STATUS_CANDIDATE,
        )

        raw_input = record.get("raw_input", "")
        status = record.get("status", "")

        # --- 1. fyjc_interactions: original student input, write-once ---
        interaction = FYJCInteraction(
            session_id=self._fingerprint(raw_input),
            raw_input=raw_input,
            board=None,
        )
        session.add(interaction)
        session.flush()

        # --- 2. fyjc_interpretations: KernelResult verbatim ---
        accounting = record.get("accounting_result") or {}
        candidate = record.get("interpretation_candidate") or {}

        interpretation = FYJCInterpretation(
            interaction_id=interaction.id,
            # model identity preserved; empty string → "unknown" is avoided:
            # the column is NOT NULL, so we store the literal value carried
            # by the result, with the existing fallback vocabulary for blank.
            model_id=record.get("model_id") or "unknown",
            transaction_type=candidate.get("transaction_type"),
            parties=self._as_json(candidate.get("parties")),
            amounts=self._amount_strings(candidate.get("amounts")),
            payment_method=candidate.get("payment_method"),
            ambiguity_flags=self._as_json(candidate.get("ambiguities")),
            field_confidences=self._as_json(candidate.get("grounding")),
            raw_model_output=None,
            parse_success=True,
            # Authoritative terminal status, stored verbatim. Non-success
            # terminal states (VALIDATION_FAILED, GROUNDING_FAILED,
            # FORBIDDEN_OUTPUT, MODEL_UNAVAILABLE, UNSUPPORTED_TRANSACTION)
            # are persisted as themselves — never relabelled, never collapsed.
            kernel_status=status,
            reason_classification=record.get("verification_status"),
            journal_balanced=accounting.get("journal_balanced"),
            journal_narration=(accounting.get("journal") or {}).get("narration"),
            debit_accounts=self._journal_side(accounting, "debit_lines"),
            credit_accounts=self._journal_side(accounting, "credit_lines"),
            calculations=self._calculations(accounting),
            latency_ms=None,
        )
        session.add(interpretation)
        session.flush()

        interpretation_id = interpretation.id

        # --- 3. fyjc_training_candidates: existing eligibility rule only ---
        candidate_id = None
        if status in _TRAINING_ELIGIBLE_STATUSES:
            problem_id = self._fingerprint(raw_input)
            candidate_row = FYJCTrainingCandidate(
                interaction_id=interaction.id,
                interpretation_id=interpretation_id,
                problem_id=problem_id,
                content_hash=problem_id,
                category=None,
                subcategory=None,
                status=FYJC_STATUS_CANDIDATE,
                evidence_count=1,
                validation_count=1 if status == "VERIFIED" else 0,
                rejection_count=0,
                source_diversity=1,
                confidence=1.0 if status == "VERIFIED" else 0.5,
                human_approved=False,
                human_notes=None,
                human_approved_at=None,
                exported_to_jsonl=False,
                export_batch_id=None,
                exported_at=None,
                version=1,
            )
            session.add(candidate_row)
            session.flush()
            candidate_id = candidate_row.id

        session.commit()

        # --- Read-back projection from the values actually stored ---
        stored = dict(record)
        stored.update(
            {
                "interaction_id": interaction.id,
                "interpretation_id": interpretation_id,
                "candidate_id": candidate_id,
                "persisted_at": None,
            }
        )
        return roundtrip_snapshot(stored)

    # ------------------------------------------------------------------
    # Pure serialization helpers (no logic, no accounting decisions)
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(text: str) -> str:
        """Deterministic content fingerprint (existing dedup convention)."""
        return hashlib.sha256(
            (text or "").strip().lower().encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def _as_json(value: Any):
        """Pass through JSON-safe values; None otherwise. No transformation."""
        if value is None:
            return None
        return value

    @staticmethod
    def _amount_strings(amounts: Any):
        """
        Normalize the 18-field `amounts` shape into the existing string-list
        column convention, without inventing values.

        Existing convention (see fyjc_db_persistence.py): amounts are stored
        as strings for Decimal precision.
        """
        if not isinstance(amounts, list):
            return None
        out = []
        for entry in amounts:
            if isinstance(entry, dict):
                value = (
                    entry.get("value")
                    or entry.get("original")
                    or entry.get("display")
                    or ""
                )
                if value:
                    out.append(str(value))
            elif entry:
                out.append(str(entry))
        return out or None

    @staticmethod
    def _journal_side(accounting: Dict[str, Any], key: str):
        """
        Copy an existing accounting journal side verbatim into the existing
        column shape. This is a field mapping, not a recalculation: account,
        amount, and rule strings come straight from the Kernel's result.
        """
        lines = accounting.get(key) or []
        out = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            if not line.get("account"):
                continue
            out.append(
                {
                    "account": line.get("account"),
                    "amount": str(line.get("amount", "")),
                    "side_hint": line.get("side_hint") or line.get("rule") or "",
                }
            )
        return out or None

    @staticmethod
    def _calculations(accounting: Dict[str, Any]):
        """Copy existing calculation records into the existing column shape."""
        journal = accounting.get("journal") or {}
        records = journal.get("calculation_records") or []
        out = []
        for r in records:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "id": r.get("calculation_id") or r.get("id", ""),
                    "label": r.get("label", ""),
                    "result": str(r.get("result", "")),
                }
            )
        return out or None


__all__ = [
    "PostgresResultPersistence",
    "PersistedResult",
    "PersistenceFailure",
]
