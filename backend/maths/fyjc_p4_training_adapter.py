"""
P4 Training-Format Adapter
==========================

Converts P4 canonical JSONL (input/output/metadata) into notebook-compatible
JSONL (instruction/input/output) for Qwen2.5-1.5B-Instruct-style fine-tuning.

IMPORTANT:
- This adapter does NOT modify P4 canonical data.
- This adapter does NOT modify the accounting kernel.
- This adapter does NOT automatically start training.
- This adapter is a read-only transformer over exported P4 data.

P4 Canonical JSONL:
    {"input": "...", "output": {...}, "metadata": {...}}

Notebook-Compatible JSONL:
    {"instruction": "...", "input": "...", "output": "<stringified JSON>"}
"""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Instruction templates
# ---------------------------------------------------------------------------

INSTRUCTION_PARSE_TRANSACTION = (
    "You are a specialised finance/accounting AI for FYJC Book-Keeping. "
    "Parse the following student accounting transaction and produce a "
    "structured interpretation. Output must include: transaction_type, "
    "parties (list), amounts (list), payment_method, references (list), "
    "ambiguities (list), and optional journal details if available. "
    "Do NOT calculate amounts. Do NOT create journal entries. "
    "Only extract and interpret what the student wrote."
)

INSTRUCTION_AMBIGUITY_DETECTION = (
    "You are a specialised finance/accounting AI for FYJC Book-Keeping. "
    "The following transaction has ambiguity. Identify what is ambiguous "
    "and what information is missing. Output the ambiguity type, the "
    "missing fields, and what clarification question should be asked."
)

INSTRUCTION_CLASSIFY_TRANSACTION = (
    "You are a specialised finance/accounting AI for FYJC Book-Keeping. "
    "Classify the following accounting transaction. Identify: "
    "transaction_type (cash_purchase, credit_purchase, cash_sale, credit_sale, "
    "expense_payment, settlement, return, etc.), parties involved, amounts, "
    "and payment method if stated."
)


# ---------------------------------------------------------------------------
# Training Format Adapter
# ---------------------------------------------------------------------------

class P4TrainingAdapter:
    """Read-only adapter that transforms P4 canonical JSONL into
    notebook-compatible JSONL for instruction-tuning.

    The canonical P4 dataset is never modified.
    """

    @staticmethod
    def _select_instruction(record: Dict[str, Any]) -> str:
        """Select the appropriate instruction based on record category."""
        category = P4TrainingAdapter._extract_category(record)
        engine_status = P4TrainingAdapter._extract_status(record)

        if engine_status == "REVIEW_REQUIRED":
            return INSTRUCTION_AMBIGUITY_DETECTION
        if category in ("CASH_CREDIT", "PARTIAL_PAYMENT", "SETTLEMENT"):
            return INSTRUCTION_PARSE_TRANSACTION
        if category in ("PRONOUN_RESOLUTION", "HISTORICAL_REFERENCE"):
            return INSTRUCTION_CLASSIFY_TRANSACTION
        return INSTRUCTION_PARSE_TRANSACTION

    @staticmethod
    def _build_output_string(record: Dict[str, Any]) -> str:
        """Stringify the nested output dict for notebook-compatible format."""
        output = P4TrainingAdapter._extract_output(record)
        if not output:
            return "{}"
        return json.dumps(output, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _extract_status(record: Dict[str, Any]) -> str:
        """Extract engine status from either P4 canonical or flat schema."""
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict) and "engine_status" in metadata:
            return metadata.get("engine_status", "")
        # Flat schema (candidate cases from hard-case discovery)
        return record.get("status", record.get("journal_status", ""))

    @staticmethod
    def _extract_category(record: Dict[str, Any]) -> str:
        """Extract category from either schema."""
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict) and "category" in metadata:
            return metadata.get("category", "OTHER")
        return record.get("category", "OTHER")

    @staticmethod
    def _extract_input(record: Dict[str, Any]) -> str:
        """Extract input text from either schema."""
        if "input" in record and isinstance(record["input"], str):
            return record["input"]
        return record.get("input_text", "")

    @staticmethod
    def _extract_output(record: Dict[str, Any]) -> Dict[str, Any]:
        """Extract output interpretation from either schema."""
        # P4 canonical: has "output" key with nested dict
        if "output" in record and isinstance(record["output"], dict):
            return record["output"]
        # Flat schema: build output from top-level fields
        output = {}
        if record.get("journal_narration"):
            output["journal_narration"] = record["journal_narration"]
        if record.get("debit_accounts"):
            output["debit_accounts"] = record["debit_accounts"]
        if record.get("credit_accounts"):
            output["credit_accounts"] = record["credit_accounts"]
        if record.get("understanding"):
            output["understanding"] = record["understanding"]
        if record.get("calculations"):
            output["calculations"] = record["calculations"]
        return output

    @staticmethod
    def _validate_record(record: Dict[str, Any]) -> bool:
        """Only export records that passed P4 validation."""
        engine_status = P4TrainingAdapter._extract_status(record)

        # Only export VERIFIED or REVIEW_REQUIRED records
        # BLOCKED records have no useful interpretation target
        # REVIEW_REQUIRED records are valuable for ambiguity-detection training
        if engine_status not in ("VERIFIED", "REVIEW_REQUIRED"):
            return False

        # Must have at least some output content
        output = P4TrainingAdapter._extract_output(record)
        if not output:
            return False

        # Must have input text
        input_text = P4TrainingAdapter._extract_input(record)
        if not input_text or not input_text.strip():
            return False

        return True

    @staticmethod
    def adapt_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Adapt a single P4 canonical record to notebook-compatible format.

        Returns None if the record should not be exported.
        The original P4 record is never modified.
        """
        if not P4TrainingAdapter._validate_record(record):
            return None

        instruction = P4TrainingAdapter._select_instruction(record)
        input_text = P4TrainingAdapter._extract_input(record)
        output_string = P4TrainingAdapter._build_output_string(record)

        # Deterministic ID from content
        content_hash = hashlib.sha256(
            f"{input_text}:{output_string}".encode("utf-8")
        ).hexdigest()[:12]

        metadata = record.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        adapted = {
            "instruction": instruction,
            "input": input_text,
            "output": output_string,
            "_p4_metadata": {
                "problem_id": metadata.get("problem_id", record.get("case_id", "")),
                "category": P4TrainingAdapter._extract_category(record),
                "engine_status": P4TrainingAdapter._extract_status(record),
                "confidence": metadata.get("confidence", record.get("confidence", "0")),
                "evidence_count": metadata.get("evidence_count", record.get("evidence_count", 0)),
                "content_hash": content_hash,
                "adapted_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        return adapted

    @staticmethod
    def adapt_jsonl(
        input_path: str,
        output_path: str,
        sort_by: str = "content_hash",
    ) -> Tuple[int, int, Dict[str, int]]:
        """Adapt a P4 canonical JSONL file to notebook-compatible format.

        Args:
            input_path: Path to P4 canonical JSONL.
            output_path: Path to write adapted JSONL.
            sort_by: Deterministic sort key ('content_hash' or 'problem_id').

        Returns:
            (total_input, total_exported, status_counts)
        """
        records: List[Dict[str, Any]] = []
        with open(input_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))

        total_input = len(records)
        status_counts: Dict[str, int] = {}

        adapted: List[Dict[str, Any]] = []
        for record in records:
            engine_status = P4TrainingAdapter._extract_status(record) or "UNKNOWN"
            status_counts[engine_status] = status_counts.get(engine_status, 0) + 1

            result = P4TrainingAdapter.adapt_record(record)
            if result is not None:
                adapted.append(result)

        # Deterministic ordering
        if sort_by == "content_hash":
            adapted.sort(key=lambda x: x.get("_p4_metadata", {}).get("content_hash", ""))
        elif sort_by == "problem_id":
            adapted.sort(key=lambda x: x.get("_p4_metadata", {}).get("problem_id", ""))

        # Write
        with open(output_path, "w", encoding="utf-8") as f:
            for record in adapted:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        return total_input, len(adapted), status_counts

    @staticmethod
    def adapt_from_records(
        records: List[Dict[str, Any]],
        output_path: str,
        sort_by: str = "content_hash",
    ) -> Tuple[int, int]:
        """Adapt an in-memory list of P4 records to notebook-compatible JSONL.

        Returns:
            (total_input, total_exported)
        """
        adapted: List[Dict[str, Any]] = []
        for record in records:
            result = P4TrainingAdapter.adapt_record(record)
            if result is not None:
                adapted.append(result)

        if sort_by == "content_hash":
            adapted.sort(key=lambda x: x.get("_p4_metadata", {}).get("content_hash", ""))
        elif sort_by == "problem_id":
            adapted.sort(key=lambda x: x.get("_p4_metadata", {}).get("problem_id", ""))

        with open(output_path, "w", encoding="utf-8") as f:
            for record in adapted:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        return len(records), len(adapted)


# ---------------------------------------------------------------------------
# Analysis: Should the training target change?
# ---------------------------------------------------------------------------

def analyze_training_target(input_path: str) -> Dict[str, Any]:
    """Analyze the P4 dataset to determine whether the training target
    should be changed from journal-generation to structured language
    interpretation.

    This is a read-only analysis — no files are modified.
    """
    records: List[Dict[str, Any]] = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    total = len(records)
    has_journal = 0
    has_interpretation = 0
    has_both = 0
    has_neither = 0
    categories: Dict[str, int] = {}
    statuses: Dict[str, int] = {}

    for rec in records:
        output = P4TrainingAdapter._extract_output(rec)
        cat = P4TrainingAdapter._extract_category(rec)
        status = P4TrainingAdapter._extract_status(rec) or "UNKNOWN"

        categories[cat] = categories.get(cat, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1

        has_j = bool(output.get("journal_narration") or output.get("debit_accounts"))
        has_i = bool(output.get("transaction_type") and output.get("parties") is not None)

        if has_j:
            has_journal += 1
        if has_i:
            has_interpretation += 1
        if has_j and has_i:
            has_both += 1
        if not has_j and not has_i:
            has_neither += 1

    return {
        "total_records": total,
        "has_journal_target": has_journal,
        "has_interpretation_target": has_interpretation,
        "has_both": has_both,
        "has_neither": has_neither,
        "categories": categories,
        "statuses": statuses,
        "recommendation": (
            "The current P4 output contains BOTH journal-generation targets "
            "(journal_narration, debit_accounts, credit_accounts) AND structured "
            "interpretation targets (transaction_type, parties, amounts). "
            "For a language-understanding model, the PRIMARY training target "
            "should be structured interpretation (what the student meant), "
            "NOT journal generation (what the kernel computed). The journal "
            "is the kernel's job. The AI's job is to understand language. "
            "The adapter preserves both fields but the training loss should "
            "focus on the interpretation fields. The journal fields serve as "
            "context/grounding, not as the primary prediction target."
        ),
    }
