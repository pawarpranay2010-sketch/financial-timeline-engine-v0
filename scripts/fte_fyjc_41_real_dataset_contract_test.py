#!/usr/bin/env python3
"""
Platrixa — StructuredInterpretation Contract Regression Test (Sprint 41)
=========================================================================

REQUIREMENT: Test against REAL FYJC datasets + malformed injection cases.
- All existing valid records MUST pass
- All malformed records MUST fail with explicit reason
- Missing datasets → test FAILURE (not skip)
- Unparseable AI outputs → test FAILURE (not pass)
- Report exact counts and every rejection

Datasets under test:
  1. training_data/specialist_clean_training.jsonl (VERIFIED records)
  2. training_data/specialist_ambiguity_eval.jsonl (REVIEW_REQUIRED records)
  3. training_data/specialist_robustness_eval.jsonl (BLOCKED records)
  4. training_data/specialist_unsupported_eval.jsonl (NOT_SUPPORTED records)
  5. training_data/p5a_evaluation_results.jsonl (evaluation results)

Malformed injection cases (MUST all reject):
  - unknown_field: receipt_number, vendor_id, etc.
  - type_errors: parties as string, amounts as string, etc.
  - json_errors: invalid JSON in output field
  - missing_contracts: output field missing/null

Exit code 0 = all tests pass (valid records accepted, malformed rejected)
Exit code 1 = regression detected (valid records rejected OR malformed accepted)
"""

import os
import sys
import json
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.getcwd())

from backend.maths.schema_verifier import (  # noqa: E402
    validate_structured_interpretation,
    ValidationStatus,
)

# Configuration
REAL_DATASETS = {
    "specialist_clean_training": "training_data/specialist_clean_training.jsonl",
    "specialist_ambiguity_eval": "training_data/specialist_ambiguity_eval.jsonl",
    "specialist_robustness_eval": "training_data/specialist_robustness_eval.jsonl",
    "specialist_unsupported_eval": "training_data/specialist_unsupported_eval.jsonl",
    "p5a_evaluation_results": "training_data/p5a_evaluation_results.jsonl",
}

STATS = {
    "datasets_found": 0,
    "datasets_missing": 0,
    "total_records": 0,
    "total_valid": 0,
    "total_invalid": 0,
    "by_dataset": defaultdict(lambda: {
        "found": False,
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "records": [],
    }),
    "by_status": Counter(),
    "rejections": [],  # Detailed rejection list
    "malformed_injection_tests": {
        "total": 0,
        "correctly_rejected": 0,
        "incorrectly_accepted": 0,
    },
}


def load_jsonl(path: str) -> list:
    """Load JSONL file, return list of (line_no, record) tuples."""
    if not os.path.exists(path):
        return None  # Signal missing file
    
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records.append((line_no, record))
            except json.JSONDecodeError as e:
                print(f"✗ JSON parse error in {path}:{line_no}: {e}")
                # Return partial list with error flag
                records.append((line_no, {"__parse_error__": str(e)}))
    return records


def extract_ai_output(record: dict, dataset_name: str) -> Optional[dict]:
    """
    Extract AI output from record depending on dataset format.
    
    Formats:
    - specialist_*: record["output"] is JSON string
    - p5a_evaluation_results: record["raw_model_output"] is JSON string
    
    Returns None if output is missing/null/unparseable.
    """
    if "__parse_error__" in record:
        return None

    output_str = None
    if "output" in record:
        output_str = record["output"]
    elif "raw_model_output" in record:
        output_str = record["raw_model_output"]
    else:
        return None  # No output field

    if output_str is None:
        return None  # Null output

    # Parse if string
    if isinstance(output_str, str):
        try:
            return json.loads(output_str)
        except json.JSONDecodeError as e:
            # Unparseable → rejection
            return None
    
    # Already a dict
    if isinstance(output_str, dict):
        return output_str
    
    return None  # Invalid type


def test_real_dataset(dataset_name: str, path: str) -> bool:
    """
    Test all records in a JSONL dataset.
    
    Returns True if all records pass, False if any fail.
    """
    print(f"\n{'='*70}")
    print(f"Testing: {dataset_name}")
    print(f"Path:    {path}")
    print(f"{'='*70}")

    records = load_jsonl(path)
    
    # Missing dataset → FAILURE
    if records is None:
        print(f"✗ MISSING: {path}")
        STATS["datasets_missing"] += 1
        STATS["by_dataset"][dataset_name]["found"] = False
        return False
    
    STATS["datasets_found"] += 1
    STATS["by_dataset"][dataset_name]["found"] = True
    
    if not records:
        print(f"⚠️  Empty dataset: {path}")
        return True
    
    dataset_stats = STATS["by_dataset"][dataset_name]
    dataset_stats["total"] = len(records)
    
    all_passed = True

    for line_no, record in records:
        # Skip parse errors in the JSONL itself (already reported)
        if "__parse_error__" in record:
            all_passed = False
            continue

        ai_output = extract_ai_output(record, dataset_name)
        
        # Missing/unparseable output → rejection
        if ai_output is None:
            problem_id = (record.get("_p4_metadata", {}).get("problem_id")
                          or record.get("problem_id", f"line_{line_no}"))
            dataset_stats["invalid"] += 1
            all_passed = False
            STATS["rejections"].append({
                "dataset": dataset_name,
                "problem_id": problem_id,
                "line": line_no,
                "reason": "output_field_missing_or_unparseable",
            })
            print(f"✗ [{problem_id}] output field missing/unparseable")
            continue

        # Validate
        report = validate_structured_interpretation(ai_output)

        problem_id = (record.get("_p4_metadata", {}).get("problem_id")
                      or record.get("problem_id", f"line_{line_no}"))
        engine_status = (record.get("_p4_metadata", {}).get("engine_status")
                         or record.get("kernel_status", "?"))

        STATS["by_status"][engine_status] += 1

        if report.valid:
            dataset_stats["valid"] += 1
            # Only print errors/rejections, not every valid record
        else:
            dataset_stats["invalid"] += 1
            all_passed = False
            error_summary = "; ".join(f"{e.field}:{e.issue}" for e in report.errors)
            print(f"✗ [{problem_id}] {report.status.value}: {error_summary}")
            STATS["rejections"].append({
                "dataset": dataset_name,
                "problem_id": problem_id,
                "line": line_no,
                "engine_status": engine_status,
                "validation_status": report.status.value,
                "errors": [e.to_dict() for e in report.errors],
            })

        dataset_stats["records"].append({
            "problem_id": problem_id,
            "line": line_no,
            "engine_status": engine_status,
            "validation_status": report.status.value,
            "valid": report.valid,
        })

    # Summary
    print(f"\n{dataset_name} Summary:")
    print(f"  Total records:    {dataset_stats['total']}")
    print(f"  ✓ Valid:          {dataset_stats['valid']}")
    print(f"  ✗ Invalid:        {dataset_stats['invalid']}")
    print(f"  By engine status: {dict(Counter(r['engine_status'] for r in dataset_stats['records']))}")

    STATS["total_records"] += dataset_stats["total"]
    STATS["total_valid"] += dataset_stats["valid"]
    STATS["total_invalid"] += dataset_stats["invalid"]

    return all_passed


def test_malformed_injection():
    """
    Test that validator REJECTS obviously malformed outputs.
    
    All injection cases MUST be rejected. If any pass, that's a regression.
    Returns True if all correctly rejected, False otherwise.
    """
    print(f"\n{'='*70}")
    print("Malformed Injection Tests (ALL should FAIL validation)")
    print(f"{'='*70}")

    injection_cases = [
        {
            "name": "unknown_field_receipt_number",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
                "receipt_number": "RCP-12345",  # ← NOT IN CONTRACT
            },
            "expect_invalid": True,
            "expect_status": "UNKNOWN_FIELD",
        },
        {
            "name": "unknown_field_vendor_id",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
                "vendor_id": "V-001",  # ← NOT IN CONTRACT
            },
            "expect_invalid": True,
            "expect_status": "UNKNOWN_FIELD",
        },
        {
            "name": "type_error_parties_string_not_list",
            "output": {
                "transaction_type": "purchase",
                "parties": "Vendor",  # ← Should be list[str]
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "type_error_parties_list_of_numbers",
            "output": {
                "transaction_type": "purchase",
                "parties": [123, 456],  # ← Should be list[str]
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "type_error_amounts_string_not_list",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": "1000",  # ← Should be list[dict]
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "type_error_amounts_list_of_strings",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": ["1000", "500"],  # ← Should be list[dict]
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": {},
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "type_error_references_string_not_list",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": "some_reference",  # ← Should be list[str]
                "ambiguities": [],
                "grounding": {},
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "type_error_grounding_list_not_dict",
            "output": {
                "transaction_type": "purchase",
                "parties": ["Vendor"],
                "amounts": [{"value": "1000"}],
                "payment_method": "cash",
                "references": [],
                "ambiguities": [],
                "grounding": ["EXPLICIT"],  # ← Should be dict
            },
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
        {
            "name": "malformed_json_invalid_syntax",
            "output": "{not valid json",  # ← Not parseable JSON
            "expect_invalid": True,
            "expect_status": "MALFORMED_JSON",
        },
        {
            "name": "malformed_not_dict_is_list",
            "output": ["transaction_type", "purchase"],  # ← Not a dict
            "expect_invalid": True,
            "expect_status": "TYPE_ERROR",
        },
    ]

    all_correct = True

    for case in injection_cases:
        STATS["malformed_injection_tests"]["total"] += 1
        report = validate_structured_interpretation(case["output"])

        if case["expect_invalid"]:
            if not report.valid:
                # Correct: malformed output was rejected
                if case.get("expect_status") and report.status.value == case["expect_status"]:
                    print(f"✓ {case['name']:50} rejected as {report.status.value}")
                    STATS["malformed_injection_tests"]["correctly_rejected"] += 1
                else:
                    # Wrong status but still rejected (acceptable, but logged)
                    print(f"⚠ {case['name']:50} rejected as {report.status.value} (expected {case.get('expect_status')})")
                    STATS["malformed_injection_tests"]["correctly_rejected"] += 1
            else:
                # REGRESSION: malformed output was incorrectly accepted
                print(f"✗ {case['name']:50} INCORRECTLY ACCEPTED")
                STATS["malformed_injection_tests"]["incorrectly_accepted"] += 1
                all_correct = False

    print(f"\nInjection Test Summary:")
    print(f"  Total:                    {STATS['malformed_injection_tests']['total']}")
    print(f"  ✓ Correctly rejected:     {STATS['malformed_injection_tests']['correctly_rejected']}")
    print(f"  ✗ Incorrectly accepted:   {STATS['malformed_injection_tests']['incorrectly_accepted']}")

    return all_correct


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("StructuredInterpretation Contract Regression Test (Sprint 41)")
    print("=" * 70)

    all_tests_passed = True

    # Test each real dataset
    print("\n" + "=" * 70)
    print("PHASE 1: Real Dataset Validation")
    print("=" * 70)
    
    for dataset_name, path in REAL_DATASETS.items():
        dataset_ok = test_real_dataset(dataset_name, path)
        if not dataset_ok:
            all_tests_passed = False

    # Test malformed injection
    print("\n" + "=" * 70)
    print("PHASE 2: Malformed Injection Tests")
    print("=" * 70)
    
    injection_ok = test_malformed_injection()
    if not injection_ok:
        all_tests_passed = False

    # Overall summary
    print(f"\n{'='*70}")
    print("REGRESSION TEST SUMMARY")
    print(f"{'='*70}")

    print(f"\nDatasets:")
    print(f"  Found:                  {STATS['datasets_found']}/{len(REAL_DATASETS)}")
    print(f"  Missing (FAILURE):      {STATS['datasets_missing']}/{len(REAL_DATASETS)}")

    print(f"\nReal Data Validation:")
    print(f"  Total records tested:   {STATS['total_records']}")
    print(f"  ✓ Valid:                {STATS['total_valid']}")
    print(f"  ✗ Invalid:              {STATS['total_invalid']}")

    print(f"\nDataset Breakdown:")
    for dataset_name, stats in STATS["by_dataset"].items():
        if stats["total"] > 0:
            pct = (stats["valid"] / stats["total"] * 100) if stats["total"] > 0 else 0
            status = "✅" if stats["invalid"] == 0 else "❌"
            print(f"  {status} {dataset_name:40} {stats['valid']:4}/{stats['total']:4} "
                  f"({pct:5.1f}%)")
        else:
            if stats["found"]:
                print(f"  ⚠️  {dataset_name:40} (empty)")
            else:
                print(f"  ❌ {dataset_name:40} (MISSING)")

    print(f"\nEngine Status Distribution (all records):")
    for status, count in sorted(STATS["by_status"].items()):
        print(f"  {status:20} {count:4}")

    print(f"\nMalformed Injection Tests:")
    print(f"  Total:                  {STATS['malformed_injection_tests']['total']}")
    print(f"  ✓ Correctly rejected:   {STATS['malformed_injection_tests']['correctly_rejected']}")
    print(f"  ✗ Incorrectly accepted: {STATS['malformed_injection_tests']['incorrectly_accepted']}")

    print(f"\nRejections ({len(STATS['rejections'])} total):")
    if STATS["rejections"]:
        for i, rejection in enumerate(STATS["rejections"][:20], 1):
            dataset = rejection["dataset"]
            problem_id = rejection.get("problem_id", "?")
            reason = rejection.get("reason") or rejection.get("validation_status", "unknown")
            print(f"  {i:2}. [{dataset:35}] {problem_id:20} → {reason}")
        if len(STATS["rejections"]) > 20:
            print(f"  ... and {len(STATS['rejections']) - 20} more")

    # Exit code
    exit_code = 0
    print(f"\n{'='*70}")

    if STATS['datasets_missing'] > 0:
        print(f"❌ REGRESSION: {STATS['datasets_missing']} dataset(s) missing")
        exit_code = 1
    elif STATS['total_invalid'] > 0:
        print(f"❌ REGRESSION: {STATS['total_invalid']} valid record(s) rejected")
        exit_code = 1
    elif STATS['malformed_injection_tests']['incorrectly_accepted'] > 0:
        print(f"❌ REGRESSION: {STATS['malformed_injection_tests']['incorrectly_accepted']} malformed record(s) accepted")
        exit_code = 1
    else:
        print(f"✅ REGRESSION TEST PASSED")
        print(f"   • All {STATS['datasets_found']} datasets found and valid")
        print(f"   • All {STATS['total_valid']} real records accepted")
        print(f"   • All {STATS['malformed_injection_tests']['total']} injection cases correctly rejected")

    print(f"{'='*70}\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
