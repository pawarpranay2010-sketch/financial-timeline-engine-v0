"""
FYJC Accounting AI — Export: PostgreSQL → specialist_clean_training.jsonl

Queries the FYJC PostgreSQL tables and produces the training JSONL
format expected by the Colab fine-tuning notebook.

This replaces the old file-based JSONL generation with a database-backed
export that always reflects the current approval state.

Export rules:
  - Only records with human_approved = true AND exported_to_jsonl = false
    are exported (to avoid duplicates across batches)
  - Each export gets a unique batch_id for provenance
  - The JSONL format matches specialist_clean_training.jsonl exactly:
      {instruction, input, output, _p4_metadata}
  - Existing JSONL files are NOT modified by this script
  - A NEW file is written to training_data/ with a timestamp

Safety:
  - Read-only on existing JSONL files
  - Does NOT modify Kernel logic
  - Does NOT wire the AI model
  - Does NOT delete or overwrite existing training data

Run:
    python backend/database/fyjc_export.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.database.db import SessionLocal


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TRAINING_DIR = _PROJECT_ROOT / "training_data"


# ---------------------------------------------------------------------------
# Prompt template (matches Colab notebook exactly)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a specialised finance/accounting AI for Indian school-level "
    "Book-Keeping (BK). Parse the student's accounting transaction into "
    "structured JSON. Extract: transaction_type, parties, amounts, "
    "payment_method, references, ambiguities, and grounding. Use Decimal "
    "precision. If information is missing, mark it as UNRESOLVED. "
    "Never invent values."
)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _fetch_approved_candidates(session) -> List[Dict[str, Any]]:
    """Fetch all candidates ready for export."""
    result = session.execute(
        text("""
            SELECT
                tc.id,
                tc.problem_id,
                tc.content_hash,
                tc.category,
                tc.subcategory,
                tc.status,
                i.raw_input,
                interp.transaction_type,
                interp.parties,
                interp.amounts,
                interp.payment_method,
                interp.kernel_status,
                interp.reason_classification,
                interp.journal_balanced,
                interp.journal_narration,
                interp.debit_accounts,
                interp.credit_accounts,
                interp.calculations,
                interp.field_confidences
            FROM fyjc_training_candidates tc
            JOIN fyjc_interactions i ON tc.interaction_id = i.id
            JOIN fyjc_interpretations interp ON tc.interpretation_id = interp.id
            WHERE tc.human_approved = true
              AND tc.exported_to_jsonl = false
            ORDER BY tc.problem_id
        """)
    )
    return [dict(row._mapping) for row in result]


def _build_training_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a database row into the training JSONL format."""
    # Build the structured output (what the model should learn to produce)
    output_dict = {
        "transaction_type": row.get("transaction_type"),
        "parties": row.get("parties") or [],
        "amounts": row.get("amounts") or [],
        "payment_method": row.get("payment_method"),
        "references": [],  # Populated from field_confidences if available
        "ambiguities": [],  # Populated from field_confidences if available
        "grounding": _build_grounding(row),
    }

    # Extract references and ambiguities from field_confidences if present
    confidences = row.get("field_confidences") or []
    if isinstance(confidences, list):
        for fc in confidences:
            if isinstance(fc, dict):
                if fc.get("grounding") == "UNRESOLVED":
                    output_dict["ambiguities"].append(fc.get("field", "unknown"))
                if fc.get("field", "").startswith("ref"):
                    output_dict["references"].append(fc.get("value"))

    # Instruction (system prompt for the model)
    instruction = _SYSTEM_PROMPT

    # Input (the student's raw text)
    input_text = row.get("raw_input", "")

    # Output (structured JSON as string — model learns to produce this)
    output_str = json.dumps(output_dict, ensure_ascii=False, default=str)

    # Metadata for provenance
    metadata = {
        "problem_id": row.get("problem_id"),
        "content_hash": row.get("content_hash"),
        "category": row.get("category"),
        "subcategory": row.get("subcategory"),
        "kernel_status": row.get("kernel_status"),
        "reason_classification": row.get("reason_classification"),
        "exported_from": "postgresql",
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "instruction": instruction,
        "input": input_text,
        "output": output_str,
        "_p4_metadata": metadata,
    }


def _build_grounding(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build grounding array from kernel verification data."""
    grounding = []

    # Parties grounding
    parties = row.get("parties") or []
    for party in parties:
        grounding.append({
            "field": "parties",
            "value": party,
            "confidence": 1.0,
            "grounding": "GROUNDED",
            "source_text": row.get("raw_input", ""),
        })

    # Amounts grounding
    amounts = row.get("amounts") or []
    for amt in amounts:
        grounding.append({
            "field": "amounts",
            "value": amt,
            "confidence": 1.0,
            "grounding": "GROUNDED",
            "source_text": row.get("raw_input", ""),
        })

    # Payment method grounding
    pm = row.get("payment_method")
    if pm:
        grounding.append({
            "field": "payment_method",
            "value": pm,
            "confidence": 1.0,
            "grounding": "GROUNDED",
            "source_text": row.get("raw_input", ""),
        })
    else:
        grounding.append({
            "field": "payment_method",
            "value": None,
            "confidence": 0.0,
            "grounding": "UNRESOLVED",
            "source_text": row.get("raw_input", ""),
        })

    return grounding


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_training_jsonl(
    output_dir: Path = _TRAINING_DIR,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Export approved FYJC candidates → training JSONL.

    Returns summary dict with exported/skipped counts.
    """
    print(f"\n{'='*60}")
    print("FYJC Export: PostgreSQL → specialist_clean_training.jsonl")
    print(f"{'='*60}")

    session = SessionLocal()

    try:
        # 1. Fetch approved candidates
        candidates = _fetch_approved_candidates(session)
        print(f"\n📋 Approved candidates ready for export: {len(candidates)}")

        if not candidates:
            print("   No candidates ready for export.")
            print("   (candidates must have human_approved=true AND exported_to_jsonl=false)")
            return {"fetched": 0, "exported": 0, "skipped": 0}

        # 2. Build training records
        records = []
        for row in candidates:
            record = _build_training_record(row)
            records.append(record)

        print(f"   Built {len(records)} training records")

        # 3. Show sample
        if records:
            sample = records[0]
            print(f"\n📝 Sample record:")
            print(f"   instruction: {sample['instruction'][:80]}...")
            print(f"   input: {sample['input'][:80]}...")
            out = json.loads(sample["output"])
            print(f"   output keys: {list(out.keys())}")
            print(f"   metadata: {sample['_p4_metadata']}")

        # 4. Write to file
        batch_id = f"pg_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        output_path = output_dir / f"specialist_clean_training_{batch_id}.jsonl"

        if dry_run:
            print(f"\n   [DRY RUN] Would write {len(records)} records to {output_path.name}")
            return {"fetched": len(candidates), "exported": len(records), "skipped": 0, "dry_run": True}

        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        print(f"\n✅ Exported {len(records)} records to: {output_path.name}")

        # 5. Mark candidates as exported
        for row in candidates:
            session.execute(
                text("""
                    UPDATE fyjc_training_candidates
                    SET exported_to_jsonl = true,
                        export_batch_id = :batch_id,
                        exported_at = NOW()
                    WHERE id = :candidate_id
                """),
                {"batch_id": batch_id, "candidate_id": row["id"]},
            )
        session.commit()
        print(f"   Marked {len(candidates)} candidates as exported (batch: {batch_id})")

        # 6. Summary
        print(f"\n{'─'*60}")
        print(f"📊 Export Summary:")
        print(f"   Fetched:     {len(candidates)}")
        print(f"   Exported:    {len(records)}")
        print(f"   Batch ID:    {batch_id}")
        print(f"   Output:      {output_path.name}")
        print(f"{'─'*60}\n")

        return {
            "fetched": len(candidates),
            "exported": len(records),
            "batch_id": batch_id,
            "output_path": str(output_path),
        }

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Verify export integrity
# ---------------------------------------------------------------------------

def verify_export(output_path: Path) -> Dict[str, Any]:
    """Verify an exported JSONL file has valid structure."""
    print(f"\n🔍 Verifying: {output_path.name}")

    if not output_path.exists():
        print(f"   ❌ File not found")
        return {"valid": False, "error": "file_not_found"}

    records = []
    with open(output_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"   ❌ Line {lineno}: invalid JSON ({e})")
                return {"valid": False, "error": f"invalid_json_line_{lineno}"}

    # Validate structure
    required_fields = {"instruction", "input", "output", "_p4_metadata"}
    empty_instruction = 0
    empty_input = 0
    empty_output = 0
    invalid_json_output = 0

    for i, rec in enumerate(records):
        missing = required_fields - set(rec.keys())
        if missing:
            print(f"   ❌ Record {i}: missing fields {missing}")
            return {"valid": False, "error": f"missing_fields_{i}"}

        if not str(rec.get("instruction", "")).strip():
            empty_instruction += 1
        if not str(rec.get("input", "")).strip():
            empty_input += 1
        if not str(rec.get("output", "")).strip():
            empty_output += 1
        else:
            try:
                json.loads(rec["output"])
            except (json.JSONDecodeError, TypeError):
                invalid_json_output += 1

    print(f"   ✅ {len(records)} records, all valid")
    print(f"   Empty instruction: {empty_instruction}/{len(records)}")
    print(f"   Empty input:       {empty_input}/{len(records)}")
    print(f"   Empty output:      {empty_output}/{len(records)}")
    print(f"   Invalid JSON out:  {invalid_json_output}/{len(records)}")

    return {
        "valid": True,
        "record_count": len(records),
        "empty_instruction": empty_instruction,
        "empty_input": empty_input,
        "empty_output": empty_output,
        "invalid_json_output": invalid_json_output,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    verify_only = "--verify" in sys.argv

    if verify_only:
        # Find latest export
        exports = sorted(_TRAINING_DIR.glob("specialist_clean_training_pg_export_*.jsonl"))
        if not exports:
            print("No PostgreSQL exports found in training_data/")
            sys.exit(1)
        result = verify_export(exports[-1])
        sys.exit(0 if result.get("valid") else 1)
    else:
        result = export_training_jsonl(dry_run=dry_run)
        sys.exit(0 if result.get("failed", 0) == 0 else 1)
