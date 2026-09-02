"""
Platrixa — FYJC Live Database Persistence
backend/fyjc_db_persistence.py

Minimum viable persistence layer connecting the live student request flow
to the FYJC PostgreSQL database (4 tables).

Design:
  * Every student interaction creates an fyjc_interactions record.
  * Every completed kernel result creates an fyjc_interpretations record.
  * fyjc_training_candidates records are created ONLY for cases eligible
    for the training/learning pipeline (VERIFIED or REVIEW_REQUIRED).
  * Duplicate persistence is prevented via session fingerprint dedup.
  * PostgreSQL failures are logged and never break the student UI.

Invariant:
  * This module NEVER modifies Kernel accounting logic.
  * This module NEVER writes to Module 4 tables.
  * This module NEVER modifies the training JSONL format.
  * This module NEVER blocks the student experience.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# In-memory fingerprint dedup: tracks which projections have already been
# persisted in this Streamlit session. Prevents duplicate writes when
# Streamlit reruns the script (every interaction triggers a rerun).
_persisted_fingerprints: set = set()


def _get_session():
    """Get a SQLAlchemy session from the existing engine.
    Returns None if the database is unavailable."""
    try:
        from backend.database.db import SessionLocal
        session = SessionLocal()
        return session
    except Exception as exc:
        logger.warning("FYJC persistence: cannot create DB session: %s", exc)
        return None


def _extract_understanding(projection: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the understanding dict from a projection."""
    return projection.get("understanding") or {}


def _extract_journal_accounts(
    result: Dict[str, Any],
) -> tuple:
    """Extract debit and credit account lists from the orchestrate result.
    Returns (debit_accounts, credit_accounts) as JSON-serializable lists."""
    debit_lines = result.get("debit_lines") or []
    credit_lines = result.get("credit_lines") or []
    debit_accounts = [
        {
            "account": line.get("account"),
            "amount": str(line.get("amount", "")),
            "side_hint": line.get("side_hint") or line.get("rule") or "",
        }
        for line in debit_lines
        if line.get("account")
    ]
    credit_accounts = [
        {
            "account": line.get("account"),
            "amount": str(line.get("amount", "")),
            "side_hint": line.get("side_hint") or line.get("rule") or "",
        }
        for line in credit_lines
        if line.get("account")
    ]
    return debit_accounts, credit_accounts


def _extract_calculations(result: Dict[str, Any]) -> list:
    """Extract calculation records from the orchestrate result."""
    journal = result.get("journal") or {}
    records = journal.get("calculation_records") or []
    return [
        {
            "id": r.get("calculation_id") or r.get("id", ""),
            "label": r.get("label", ""),
            "result": str(r.get("result", "")),
        }
        for r in records
    ]


def _is_training_eligible(status: str) -> bool:
    """Determine if a result is eligible for the training pipeline.
    Only VERIFIED and REVIEW_REQUIRED cases are candidates."""
    return status in ("VERIFIED", "REVIEW_REQUIRED")


def persist_fyjc_result(
    projection: Dict[str, Any],
    question: str,
    fingerprint: str,
) -> bool:
    """Persist a student request result to the FYJC PostgreSQL database.

    Creates:
      1. fyjc_interactions  — raw student input (write-once)
      2. fyjc_interpretations — kernel interpretation + verdict (one per attempt)
      3. fyjc_training_candidates — only for training-eligible cases

    Dedup: uses the question fingerprint to prevent duplicate writes when
    Streamlit reruns the script.

    Returns True on success, False on any failure (never raises).
    """
    # --- Dedup check ---
    if fingerprint in _persisted_fingerprints:
        return True  # already persisted this projection

    # --- Get database session ---
    session = _get_session()
    if session is None:
        return False

    try:
        from backend.database.models import (
            FYJCInteraction,
            FYJCInterpretation,
            FYJCTrainingCandidate,
            FYJC_STATUS_CANDIDATE,
        )

        # --- Extract data from projection ---
        understanding = _extract_understanding(projection)
        raw_result = projection.get("result") or {}
        status = projection.get("status", "")

        # --- 1. Create fyjc_interactions ---
        interaction = FYJCInteraction(
            session_id=fingerprint,
            raw_input=question,
            board=None,  # not available from current flow
        )
        session.add(interaction)
        session.flush()  # get interaction.id

        # --- 2. Create fyjc_interpretations ---
        debit_accounts, credit_accounts = _extract_journal_accounts(raw_result)
        calculations = _extract_calculations(raw_result)

        # Extract parties and amounts from understanding
        parties = understanding.get("parties") or []
        amounts_raw = understanding.get("amounts") or []
        amounts = [
            str(a.get("value") or a.get("original") or "")
            for a in amounts_raw
            if isinstance(a, dict)
        ] or [str(a) for a in amounts_raw if a]

        # Extract ambiguity flags
        ambiguity_flags = []
        concerns = understanding.get("concerns") or []
        if concerns:
            ambiguity_flags.extend([str(c) for c in concerns])

        interpretation = FYJCInterpretation(
            interaction_id=interaction.id,
            model_id="kernel-only",  # deterministic engine, no AI model
            transaction_type=understanding.get("transaction_type"),
            parties=parties if parties else None,
            amounts=amounts if amounts else None,
            payment_method=understanding.get("payment_method"),
            ambiguity_flags=ambiguity_flags if ambiguity_flags else None,
            field_confidences=None,  # deterministic engine has no confidence scores
            raw_model_output=None,  # no AI model, kernel output is structured
            parse_success=True,  # deterministic engine always succeeds
            kernel_status=status,
            reason_classification=raw_result.get("reason"),
            journal_balanced=raw_result.get("journal_balanced"),
            journal_narration=(raw_result.get("journal") or {}).get("narration"),
            debit_accounts=debit_accounts if debit_accounts else None,
            credit_accounts=credit_accounts if credit_accounts else None,
            calculations=calculations if calculations else None,
            latency_ms=None,  # deterministic is instant
        )
        session.add(interpretation)
        session.flush()  # get interpretation.id

        # --- 3. Create fyjc_training_candidates (only for eligible cases) ---
        if _is_training_eligible(status):
            import hashlib
            problem_id = hashlib.sha256(
                question.strip().lower().encode("utf-8")
            ).hexdigest()[:16]

            candidate = FYJCTrainingCandidate(
                interaction_id=interaction.id,
                interpretation_id=interpretation.id,
                problem_id=problem_id,
                content_hash=problem_id,  # same hash for dedup
                category=None,  # will be classified later
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
            session.add(candidate)

        # --- Commit ---
        session.commit()

        # --- Dedup registration ---
        _persisted_fingerprints.add(fingerprint)

        return True

    except Exception as exc:
        logger.warning("FYJC persistence failed (non-blocking): %s", exc)
        try:
            session.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            session.close()
        except Exception:
            pass
