"""
FYJC Accounting AI — Migration Script

Imports the existing 100 candidate cases from platrixa_ai_candidate_cases.jsonl
into the 4 FYJC PostgreSQL tables.

This is a ONE-TIME migration.  Run after fyjc_init_db.py has created the tables.

What gets migrated:
  - Each candidate case → 1 fyjc_interactions + 1 fyjc_interpretations + 1 fyjc_training_candidates

What does NOT get migrated:
  - Evaluation datasets (specialist_*_eval.jsonl) — kept as version-controlled fixtures
  - p5a_evaluation_results.jsonl — kept as evaluation artifact
  - specialist_clean_training.jsonl — already exported; re-derived from DB after migration

Safety:
  - Does NOT delete or modify any existing JSON/JSONL files
  - Idempotent: re-running skips already-migrated records (by problem_id)
  - Reports exactly what was migrated and what was skipped

Run:
    python backend/database/fyjc_migrate.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.database.db import SessionLocal, engine
from backend.database.models import (
    FYJCInteraction,
    FYJCInterpretation,
    FYJCTrainingCandidate,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CANDIDATE_CASES_PATH = _PROJECT_ROOT / "platrixa_ai_candidate_cases.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_candidate_cases(path: Path) -> List[Dict[str, Any]]:
    """Load all candidate cases from JSONL."""
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  ⚠️  Skipping line {lineno}: invalid JSON ({e})")
    return cases


def _case_to_interaction(case: Dict[str, Any]) -> Dict[str, Any]:
    """Map candidate case JSONL fields → fyjc_interactions row."""
    return {
        "session_id": case.get("case_id", ""),
        "raw_input": case.get("input_text", ""),
        "board": "HSC",  # FYJC is Maharashtra HSC board
        "created_at": _parse_timestamp(case.get("timestamp")),
    }


def _case_to_interpretation(case: Dict[str, Any]) -> Dict[str, Any]:
    """Map candidate case JSONL fields → fyjc_interpretations row."""
    understanding = case.get("understanding") or {}
    verification = case.get("verification") or {}

    return {
        "model_id": "kernel-only",  # Pre-AI data — kernel produced these results
        "transaction_type": understanding.get("transaction_type"),
        "parties": understanding.get("parties"),
        "amounts": None,  # Not stored in candidate case JSONL
        "payment_method": None,  # Not stored in candidate case JSONL
        "ambiguity_flags": None,
        "field_confidences": None,
        "raw_model_output": None,
        "parse_success": True,
        "kernel_status": case.get("status"),
        "reason_classification": case.get("reason_classification"),
        "journal_balanced": case.get("journal_balanced"),
        "journal_narration": case.get("journal_narration"),
        "debit_accounts": case.get("debit_accounts"),
        "credit_accounts": case.get("credit_accounts"),
        "calculations": case.get("calculations"),
        "latency_ms": None,
        "created_at": _parse_timestamp(case.get("timestamp")),
    }


def _case_to_candidate(case: Dict[str, Any]) -> Dict[str, Any]:
    """Map candidate case JSONL fields → fyjc_training_candidates row."""
    return {
        "problem_id": case.get("case_id", ""),
        "content_hash": case.get("content_hash", None),  # May be None in candidate JSONL
        "category": case.get("category"),
        "subcategory": case.get("subcategory"),
        "status": _map_status(case.get("status", ""), case.get("case_id", "")),
        "evidence_count": 1,  # Each JSONL record is 1 evidence instance
        "validation_count": 1 if case.get("status") == "VERIFIED" else 0,
        "rejection_count": 1 if case.get("status") in ("BLOCKED", "NOT_SUPPORTED") else 0,
        "source_diversity": 1,
        "confidence": _compute_initial_confidence(case),
        "human_approved": False,
        "human_notes": None,
        "human_approved_at": None,
        "exported_to_jsonl": False,
        "export_batch_id": None,
        "exported_at": None,
        "created_at": _parse_timestamp(case.get("timestamp")),
        "version": 1,
    }


# Cases retired in P4.3.6 (duplicates with content_hash collision)
_RETIREMENT_CASES = {"C0079"}


def _map_status(raw_status: str, case_id: str = "") -> str:
    """Map candidate case status → FYJC lifecycle status.

    C0079 is retired because it has identical content_hash with C0038
    (removed in P4.3.6 reconciliation).
    """
    if case_id in _RETIREMENT_CASES:
        return "RETIRED"
    mapping = {
        "VERIFIED": "VALIDATED",
        "REVIEW_REQUIRED": "CANDIDATE",
        "BLOCKED": "REJECTED",
        "NOT_SUPPORTED": "REJECTED",
        "EXCEPTION": "REJECTED",
    }
    return mapping.get(raw_status, "CANDIDATE")


def _compute_initial_confidence(case: Dict[str, Any]) -> float:
    """Deterministic initial confidence from single evidence instance."""
    status = case.get("status", "")
    if status == "VERIFIED":
        return 0.5  # Single verified instance
    elif status == "REVIEW_REQUIRED":
        return 0.25
    else:
        return 0.0


def _parse_timestamp(ts: Optional[str]) -> datetime:
    """Parse ISO timestamp string → datetime.  Falls back to now()."""
    if not ts:
        return datetime.now(timezone.utc)
    try:
        # Handle both Z and +00:00 suffixes
        ts_clean = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_clean)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_candidate_cases(
    cases_path: Path = _CANDIDATE_CASES_PATH,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Import candidate cases from JSONL into FYJC PostgreSQL tables.

    Returns a summary dict with migrated/skipped/failed counts.
    """
    print(f"\n{'='*60}")
    print("FYJC Migration: Candidate Cases → PostgreSQL")
    print(f"{'='*60}")

    # 1. Load JSONL
    if not cases_path.exists():
        print(f"❌ File not found: {cases_path}")
        return {"error": "file_not_found"}

    cases = _load_candidate_cases(cases_path)
    print(f"\n📄 Loaded {len(cases)} candidate cases from {cases_path.name}")

    if not cases:
        print("   No records to migrate.")
        return {"loaded": 0, "migrated": 0, "skipped": 0, "failed": 0}

    # 2. Connect to database
    session = SessionLocal()
    migrated = 0
    skipped = 0
    failed = 0
    errors = []

    try:
        # Check existing problem_ids to avoid duplicates
        existing_ids = set()
        result = session.execute(
            text("SELECT problem_id FROM fyjc_training_candidates")
        )
        for row in result:
            existing_ids.add(row[0])
        print(f"   Existing FYJC candidates: {len(existing_ids)}")

        for i, case in enumerate(cases):
            problem_id = case.get("case_id", f"UNKNOWN_{i}")

            # Skip if already migrated
            if problem_id in existing_ids:
                skipped += 1
                continue

            if dry_run:
                print(f"   [DRY RUN] Would migrate: {problem_id}")
                migrated += 1
                continue

            try:
                # Create interaction
                interaction_data = _case_to_interaction(case)
                interaction = FYJCInteraction(**interaction_data)
                session.add(interaction)
                session.flush()  # Get interaction.id

                # Create interpretation
                interp_data = _case_to_interpretation(case)
                interp_data["interaction_id"] = interaction.id
                interpretation = FYJCInterpretation(**interp_data)
                session.add(interpretation)
                session.flush()  # Get interpretation.id

                # Create candidate
                candidate_data = _case_to_candidate(case)
                candidate_data["interaction_id"] = interaction.id
                candidate_data["interpretation_id"] = interpretation.id
                candidate = FYJCTrainingCandidate(**candidate_data)
                session.add(candidate)

                session.commit()
                migrated += 1
                existing_ids.add(problem_id)

            except IntegrityError as e:
                session.rollback()
                skipped += 1
                errors.append(f"{problem_id}: integrity error ({e})")
            except Exception as e:
                session.rollback()
                failed += 1
                errors.append(f"{problem_id}: {e}")

        # 3. Summary
        print(f"\n{'─'*60}")
        print(f"📊 Migration Summary:")
        print(f"   Loaded:     {len(cases)}")
        print(f"   Migrated:   {migrated}")
        print(f"   Skipped:    {skipped} (already existed)")
        print(f"   Failed:     {failed}")
        if errors:
            print(f"\n   Errors:")
            for err in errors[:10]:
                print(f"     - {err}")
        print(f"{'─'*60}\n")

        return {
            "loaded": len(cases),
            "migrated": migrated,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
        }

    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    result = migrate_candidate_cases(dry_run=dry_run)
    sys.exit(0 if result.get("failed", 0) == 0 else 1)
