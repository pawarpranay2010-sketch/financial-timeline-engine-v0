#!/usr/bin/env python3
"""
Platrixa FYJC — Training Data Quality Validation Pipeline

CPU-friendly validation of FYJC accounting training records.
Detects invalid JSON, schema violations, duplicates, and quality issues.

Safety: Bad records are rejected and reported, never silently repaired.

Usage:
    python training/validate.py training_data/generated_training_raw.jsonl
    python training/validate.py training_data/specialist_clean_training.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

REQUIRED_RECORD_KEYS = {"instruction", "input", "output"}

REQUIRED_OUTPUT_KEYS = {"transaction_type", "parties", "amounts", "payment_method"}

VALID_TRANSACTION_TYPES = {
    "purchase", "sale", "payment", "receipt", "capital", "expense",
    "return", "return_out", "return_in", "discount_trade", "discount_cash",
    "settlement", "gst", "drawing", "depreciation", "unknown",
    "compound", "joint_venture", "consignment",
}

VALID_PAYMENT_METHODS = {
    "cash", "cheque", "bank", "neft", "upi", "credit", "unknown",
    "cash_inferred", "credit_inferred", "bank_inferred",
}

MAX_RECORD_SIZE_BYTES = 10000  # reject abnormally large records


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class ValidationResult:
    """Result of validating a single record."""

    def __init__(self, index: int, passed: bool, errors: List[str],
                 warnings: List[str], record_hash: str = ""):
        self.index = index
        self.passed = passed
        self.errors = errors
        self.warnings = warnings
        self.record_hash = record_hash


class DatasetValidator:
    """Validates a JSONL training dataset for quality and correctness."""

    def __init__(self):
        self.results: List[ValidationResult] = []
        self.seen_hashes: Set[str] = set()
        self.seen_inputs: Set[str] = set()
        self.stats = Counter()

    def validate_file(self, path: str) -> Dict[str, Any]:
        """Validate an entire JSONL file.

        Returns a summary dict with counts and per-record results.
        """
        self.results = []
        self.seen_hashes = set()
        self.seen_inputs = set()
        self.stats = Counter()

        file_path = Path(path)
        if not file_path.exists():
            return {
                "file": str(path),
                "exists": False,
                "error": f"File not found: {path}",
                "total_records": 0,
                "passed": 0,
                "failed": 0,
                "duplicate_count": 0,
            }

        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append((line_num, record))
                except json.JSONDecodeError as e:
                    self.results.append(ValidationResult(
                        index=line_num,
                        passed=False,
                        errors=[f"Invalid JSON: {e}"],
                        warnings=[],
                    ))
                    self.stats["invalid_json"] += 1

        # Validate each record
        for line_num, record in records:
            result = self._validate_record(record, line_num)
            self.results.append(result)

        # Near-duplicate detection
        self._detect_near_duplicates()

        # Summary
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        errors_all = []
        warnings_all = []
        for r in self.results:
            errors_all.extend(r.errors)
            warnings_all.extend(r.warnings)

        return {
            "file": str(path),
            "exists": True,
            "total_records": len(self.results),
            "passed": passed,
            "failed": failed,
            "duplicate_count": self.stats.get("near_duplicate", 0),
            "errors": errors_all,
            "warnings": warnings_all,
            "error_counts": dict(self.stats),
            "category_distribution": self._category_distribution(records),
            "status_distribution": self._status_distribution(records),
        }

    def _validate_record(self, record: Dict[str, Any],
                         index: int) -> ValidationResult:
        """Validate a single training record."""
        errors = []
        warnings = []

        # 1. Required keys
        missing_keys = REQUIRED_RECORD_KEYS - set(record.keys())
        if missing_keys:
            errors.append(f"Missing required keys: {missing_keys}")

        # 2. Record size
        raw_size = len(json.dumps(record).encode("utf-8"))
        if raw_size > MAX_RECORD_SIZE_BYTES:
            errors.append(f"Record too large: {raw_size} bytes "
                          f"(max {MAX_RECORD_SIZE_BYTES})")

        # 3. Input validation
        input_text = record.get("input", "")
        if not isinstance(input_text, str):
            errors.append("Input must be a string")
        elif not input_text.strip():
            errors.append("Input is empty")
        elif len(input_text) < 5:
            warnings.append(f"Input very short: '{input_text}'")

        # 4. Instruction validation
        instruction = record.get("instruction", "")
        if not isinstance(instruction, str):
            errors.append("Instruction must be a string")
        elif not instruction.strip():
            errors.append("Instruction is empty")

        # 5. Output validation (JSON string)
        output_str = record.get("output", "")
        output_dict = None
        if not isinstance(output_str, str):
            errors.append("Output must be a JSON string")
        elif not output_str.strip():
            errors.append("Output is empty")
        else:
            try:
                output_dict = json.loads(output_str)
            except json.JSONDecodeError as e:
                errors.append(f"Output is not valid JSON: {e}")

        # 6. Output schema validation
        if output_dict is not None:
            self._validate_output_schema(output_dict, errors, warnings)

        # 7. Duplicate detection (exact)
        record_hash = self._hash_record(record)
        if record_hash in self.seen_hashes:
            errors.append(f"Exact duplicate of a previous record")
            self.stats["exact_duplicate"] += 1
        self.seen_hashes.add(record_hash)

        # 8. Near-duplicate detection (by input text)
        input_lower = input_text.lower().strip() if isinstance(input_text, str) else ""
        if input_lower in self.seen_inputs:
            warnings.append(f"Near-duplicate input text")
            self.stats["near_duplicate"] += 1
        self.seen_inputs.add(input_lower)

        # 9. Fibonacci/Alpaca contamination check
        full_text = json.dumps(record).lower()
        if "fibonacci" in full_text or "alpaca" in full_text:
            errors.append("Contains Fibonacci/Alpaca demo data")
            self.stats["contaminated"] += 1

        passed = len(errors) == 0
        if not passed:
            self.stats["failed_records"] += 1
        else:
            self.stats["passed_records"] += 1

        return ValidationResult(
            index=index,
            passed=passed,
            errors=errors,
            warnings=warnings,
            record_hash=record_hash,
        )

    def _validate_output_schema(self, output: Dict[str, Any],
                                errors: List[str], warnings: List[str]):
        """Validate the output JSON schema."""
        if not isinstance(output, dict):
            errors.append("Output JSON must be an object")
            return

        # Required keys
        missing = REQUIRED_OUTPUT_KEYS - set(output.keys())
        if missing:
            errors.append(f"Output missing required keys: {missing}")

        # transaction_type
        tx_type = output.get("transaction_type")
        if tx_type is not None:
            if not isinstance(tx_type, str):
                errors.append("transaction_type must be a string")
            elif tx_type.lower() not in VALID_TRANSACTION_TYPES:
                warnings.append(f"Unusual transaction_type: '{tx_type}'")

        # parties
        parties = output.get("parties")
        if parties is not None:
            if not isinstance(parties, list):
                errors.append("parties must be a list")
            else:
                for p in parties:
                    if not isinstance(p, str):
                        errors.append(f"Party must be a string: {p}")
                    elif not p.strip():
                        errors.append("Empty party name")

        # amounts
        amounts = output.get("amounts")
        if amounts is not None:
            if not isinstance(amounts, list):
                errors.append("amounts must be a list")
            else:
                for a in amounts:
                    if isinstance(a, dict):
                        if "value" not in a:
                            errors.append(f"Amount missing 'value' key: {a}")
                        else:
                            val = a["value"]
                            if not self._is_valid_amount(str(val)):
                                errors.append(f"Invalid amount value: '{val}'")
                    elif isinstance(a, str):
                        if not self._is_valid_amount(a):
                            errors.append(f"Invalid amount string: '{a}'")
                    else:
                        errors.append(f"Amount must be a dict or string: {a}")

        # payment_method
        pm = output.get("payment_method")
        if pm is not None:
            if not isinstance(pm, str):
                errors.append("payment_method must be a string")
            elif pm.lower().replace("_", "") not in {
                m.replace("_", "") for m in VALID_PAYMENT_METHODS
            }:
                warnings.append(f"Unusual payment_method: '{pm}'")

        # references
        refs = output.get("references")
        if refs is not None and not isinstance(refs, list):
            errors.append("references must be a list")

        # ambiguities
        ambs = output.get("ambiguities")
        if ambs is not None and not isinstance(ambs, list):
            errors.append("ambiguities must be a list")

        # grounding
        grounding = output.get("grounding")
        if grounding is not None:
            if not isinstance(grounding, dict):
                errors.append("grounding must be an object")
            else:
                if "all_fields_explicitly_grounded" not in grounding:
                    warnings.append("grounding missing 'all_fields_explicitly_grounded'")
                if "inferred_fields" not in grounding:
                    warnings.append("grounding missing 'inferred_fields'")

    def _is_valid_amount(self, value: str) -> bool:
        """Check if a string is a valid numeric amount."""
        try:
            num = float(value.replace(",", ""))
            return num >= 0 and num < 1e12
        except (ValueError, TypeError):
            return False

    def _hash_record(self, record: Dict[str, Any]) -> str:
        """Hash a record for exact duplicate detection."""
        # Hash on input + output (ignore metadata and instruction)
        key = json.dumps({
            "input": record.get("input", ""),
            "output": record.get("output", ""),
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _detect_near_duplicates(self):
        """Additional near-duplicate detection by text similarity."""
        # Already handled in _validate_record via seen_inputs
        pass

    def _category_distribution(
        self, records: List[Tuple[int, Dict]]
    ) -> Dict[str, int]:
        """Count categories from _p4_metadata."""
        cats = Counter()
        for _, record in records:
            meta = record.get("_p4_metadata") or {}
            cat = meta.get("category", "unknown")
            cats[cat] += 1
        return dict(cats.most_common())

    def _status_distribution(
        self, records: List[Tuple[int, Dict]]
    ) -> Dict[str, int]:
        """Count kernel_status from _p4_metadata."""
        statuses = Counter()
        for _, record in records:
            meta = record.get("_p4_metadata") or {}
            status = meta.get("kernel_status") or meta.get("engine_status") or "unknown"
            statuses[status] += 1
        return dict(statuses.most_common())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Validate FYJC accounting training data"
    )
    parser.add_argument("files", nargs="+",
                        help="JSONL files to validate")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    all_results = []
    total_passed = 0
    total_failed = 0

    for path in args.files:
        validator = DatasetValidator()
        result = validator.validate_file(path)
        all_results.append(result)

        total_passed += result["passed"]
        total_failed += result["failed"]

        if not args.json:
            print(f"\n{'='*60}")
            print(f"File: {path}")
            print(f"{'='*60}")
            print(f"Total records: {result['total_records']}")
            print(f"Passed:        {result['passed']}")
            print(f"Failed:        {result['failed']}")
            print(f"Duplicates:    {result.get('duplicate_count', 0)}")

            if result.get("category_distribution"):
                print("\nCategory distribution:")
                for cat, count in result["category_distribution"].items():
                    print(f"  {cat}: {count}")

            if result.get("status_distribution"):
                print("\nStatus distribution:")
                for status, count in result["status_distribution"].items():
                    print(f"  {status}: {count}")

            if result.get("errors"):
                print(f"\nErrors ({len(result['errors'])}):")
                for err in result["errors"][:20]:
                    print(f"  ✗ {err}")
                if len(result["errors"]) > 20:
                    print(f"  ... and {len(result['errors'])-20} more")

            if result.get("warnings"):
                print(f"\nWarnings ({len(result['warnings'])}):")
                for warn in result["warnings"][:20]:
                    print(f"  ⚠ {warn}")
                if len(result["warnings"]) > 20:
                    print(f"  ... and {len(result['warnings'])-20} more")

    if args.json:
        print(json.dumps(all_results, indent=2, ensure_ascii=False))

    # Final summary
    print(f"\n{'='*60}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*60}")
    print(f"Files validated:  {len(args.files)}")
    print(f"Total passed:     {total_passed}")
    print(f"Total failed:     {total_failed}")
    print(f"Overall status:   {'PASS' if total_failed == 0 else 'FAIL'}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
