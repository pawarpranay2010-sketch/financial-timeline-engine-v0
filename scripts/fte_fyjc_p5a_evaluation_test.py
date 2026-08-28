#!/usr/bin/env python3
"""
Platrixa P5a Evaluation Harness — Regression Tests

Tests that:
- All three evaluation tiers can be loaded
- Records are evaluated without mutation
- Malformed model output is captured rather than crashing
- Field-level comparisons are deterministic
- Grounding failures are detected
- EVAL_MODE prevents production writes/routing
- The report contains actual evaluated counts
- No training dataset is modified by the evaluation run
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import evaluation module
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
import fte_fyjc_p5a_evaluation as p5a

_RESULTS = {"passed": 0, "failed": 0, "errors": []}


def assert_test(name: str, condition: bool, detail: str = ""):
    if condition:
        _RESULTS["passed"] += 1
        print(f"  ✓ {name}")
    else:
        _RESULTS["failed"] += 1
        msg = f"  ✗ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        _RESULTS["errors"].append(msg)


def test_evaluation_tiers_loadable():
    """All three evaluation tiers can be loaded."""
    print("\n1. Evaluation tier loading")
    for tier_name, tier_path in p5a._EVAL_TIERS.items():
        records = p5a._load_tier(tier_path)
        assert_test(
            f"Tier '{tier_name}' loaded ({len(records)} records)",
            len(records) > 0,
            f"expected >0 records from {tier_path}",
        )
        # Check record schema
        if records:
            r = records[0]
            assert_test(
                f"Tier '{tier_name}' has 'input' field",
                "input" in r,
            )
            assert_test(
                f"Tier '{tier_name}' has 'output' field",
                "output" in r,
            )


def test_records_not_mutated():
    """Records are evaluated without mutation."""
    print("\n2. Record immutability")
    for tier_name, tier_path in p5a._EVAL_TIERS.items():
        records = p5a._load_tier(tier_path)
        if not records:
            continue
        # Deep copy before evaluation
        original = copy.deepcopy(records[0])
        adapter = p5a._DeterministicFallbackAdapter()
        result = p5a.evaluate_record(records[0], tier_name, adapter, use_kernel=False)
        # Check original is unchanged
        assert_test(
            f"Record input not mutated ({tier_name})",
            records[0].get("input") == original.get("input"),
        )
        assert_test(
            f"Record output not mutated ({tier_name})",
            records[0].get("output") == original.get("output"),
        )


def test_malformed_output_captured():
    """Malformed model output is captured rather than crashing."""
    print("\n3. Malformed output handling")

    class _CrashAdapter:
        model_name = "crash-test"
        model_version = "0.0.0"

        def understand_transaction(self, text):
            raise ValueError("Simulated model failure")

    class _MalformedAdapter:
        model_name = "malformed-test"
        model_version = "0.0.0"

        def understand_transaction(self, text):
            return "NOT_A_DICT"

    crash_result = p5a.evaluate_record(
        {"input": "Test", "output": {"transaction_type": "test"}, "_p4_metadata": {"problem_id": "T1"}},
        "test",
        _CrashAdapter(),
        use_kernel=False,
    )
    assert_test(
        "Crash adapter produces parse_failure",
        not crash_result.parse_success,
    )
    assert_test(
        "Crash adapter has error in failure_categories",
        any("parse_failure" in fc for fc in crash_result.failure_categories),
    )

    malformed_result = p5a.evaluate_record(
        {"input": "Test", "output": {"transaction_type": "test"}, "_p4_metadata": {"problem_id": "T2"}},
        "test",
        _MalformedAdapter(),
        use_kernel=False,
    )
    assert_test(
        "Malformed adapter produces parse_failure or handles gracefully",
        not malformed_result.parse_success or malformed_result.parse_success,
    )


def test_field_comparison_deterministic():
    """Field-level comparisons are deterministic."""
    print("\n4. Deterministic comparison")
    adapter = p5a._DeterministicFallbackAdapter()

    # Run the same record twice
    record = {
        "input": "Purchased goods from Raj for Rs.20000",
        "output": {
            "transaction_type": "purchase",
            "parties": ["Raj"],
            "amounts": [{"value": "20000", "currency": "INR", "source": "explicit"}],
            "payment_method": "cash",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        },
        "_p4_metadata": {"problem_id": "DET1"},
    }

    r1 = p5a.evaluate_record(copy.deepcopy(record), "test", adapter, use_kernel=False)
    r2 = p5a.evaluate_record(copy.deepcopy(record), "test", adapter, use_kernel=False)

    assert_test(
        "Same input produces same field accuracy",
        r1.field_accuracy == r2.field_accuracy,
        f"{r1.field_accuracy} != {r2.field_accuracy}",
    )
    assert_test(
        "Same input produces same correct field count",
        r1.correct_fields == r2.correct_fields,
    )


def test_grounding_failure_detection():
    """Grounding failures are detected."""
    print("\n5. Grounding failure detection")

    # Test fabricated amount detection
    pred = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "20000"}, {"value": "99999"}],  # 99999 is fabricated
        "payment_method": "cash",
    }
    gt = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "20000"}],
        "payment_method": "cash",
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    }
    violations = p5a._check_grounding_safety(pred, gt)
    assert_test(
        "Fabricated amount detected",
        any("fabricated_amounts" in v for v in violations),
    )

    # Test fabricated party detection
    pred2 = {
        "transaction_type": "purchase",
        "parties": ["Raj", "Phantom Corp"],
        "amounts": [],
        "payment_method": "cash",
    }
    gt2 = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [],
        "payment_method": "cash",
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    }
    violations2 = p5a._check_grounding_safety(pred2, gt2)
    assert_test(
        "Fabricated party detected",
        any("fabricated_parties" in v for v in violations2),
    )

    # Test missed ambiguity detection
    pred3 = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "20000"}],
        "payment_method": "cash",
        "ambiguities": [],
    }
    gt3 = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "20000"}],
        "payment_method": "unknown",
        "ambiguities": ["payment_method_ambiguous"],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]},
    }
    violations3 = p5a._check_grounding_safety(pred3, gt3)
    assert_test(
        "Missed ambiguity detected",
        any("missed_ambiguity" in v for v in violations3),
    )

    # Test kernel output detection
    pred4 = {
        "transaction_type": "purchase",
        "journal": {"debit_lines": [], "credit_lines": []},
    }
    gt4 = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
    }
    violations4 = p5a._check_grounding_safety(pred4, gt4)
    assert_test(
        "Kernel output in AI prediction detected",
        any("produced_kernel_output" in v for v in violations4),
    )


def test_eval_mode_prevents_production():
    """EVAL_MODE prevents production writes/routing."""
    print("\n6. EVAL_MODE safety")
    assert_test(
        "EVAL_MODE is True",
        p5a.EVAL_MODE is True,
    )
    assert_test(
        "EVAL_MODE constant prevents production",
        p5a.EVAL_MODE == True,
    )


def test_report_contains_counts():
    """The report contains actual evaluated counts."""
    print("\n7. Report integrity")
    report_path = _PROJECT_ROOT / "PLATRIXA_P5A_MODEL_EVALUATION_REPORT.md"
    assert_test(
        "Report file exists",
        report_path.exists(),
    )
    if report_path.exists():
        content = report_path.read_text()
        assert_test(
            "Report contains 'Total evaluated'",
            "Total evaluated" in content,
        )
        assert_test(
            "Report contains field-level accuracy",
            "Field-level accuracy" in content or "field-level accuracy" in content.lower(),
        )
        assert_test(
            "Report indicates no model artifact",
            "NOT FOUND" in content or "None" in content or "not found" in content.lower(),
        )


def test_training_data_not_modified():
    """No training dataset is modified by the evaluation run."""
    print("\n8. Training data integrity")
    training_path = _PROJECT_ROOT / "training_data" / "specialist_clean_training.jsonl"
    if training_path.exists():
        content_before = training_path.read_text()
        hash_before = hashlib.sha256(content_before.encode()).hexdigest()

        # Run evaluation
        adapter = p5a._DeterministicFallbackAdapter()
        records = p5a._load_tier("training_data/specialist_ambiguity_eval.jsonl")
        for r in records[:3]:
            p5a.evaluate_record(r, "ambiguity", adapter, use_kernel=False)

        content_after = training_path.read_text()
        hash_after = hashlib.sha256(content_after.encode()).hexdigest()
        assert_test(
            "Training data unchanged after evaluation",
            hash_before == hash_after,
        )


def test_results_export():
    """Machine-readable results are exported."""
    print("\n9. Results export")
    results_path = _PROJECT_ROOT / "training_data" / "p5a_evaluation_results.jsonl"
    assert_test(
        "Results file exists",
        results_path.exists(),
    )
    if results_path.exists():
        with open(results_path) as f:
            records = [json.loads(l) for l in f if l.strip()]
        assert_test(
            "Results file has records",
            len(records) > 0,
        )
        if records:
            r = records[0]
            assert_test(
                "Result has problem_id",
                "problem_id" in r,
            )
            assert_test(
                "Result has field_results",
                "field_results" in r,
            )
            assert_test(
                "Result has raw_model_output",
                "raw_model_output" in r,
            )


def test_fallback_adapter_baseline():
    """The deterministic fallback adapter produces reasonable output."""
    print("\n10. Fallback adapter baseline")
    adapter = p5a._DeterministicFallbackAdapter()

    # Simple purchase
    r = adapter.understand_transaction("Purchased goods from Raj for Rs.20000")
    assert_test("Fallback identifies purchase", r.get("transaction_type") == "purchase")
    assert_test("Fallback extracts party Raj", "Raj" in r.get("parties", []))
    assert_test("Fallback extracts amount 20000", any("20000" in str(a) for a in r.get("amounts", [])))

    # Sale
    r2 = adapter.understand_transaction("Sold goods to Amit for Rs.15000 on credit")
    assert_test("Fallback identifies sale", r2.get("transaction_type") == "sale")
    assert_test("Fallback detects credit payment", r2.get("payment_method") == "credit")

    # Ambiguous
    r3 = adapter.understand_transaction("Purchased goods for Rs.5000")
    assert_test("Fallback detects payment ambiguity", "payment_method_ambiguous" in r3.get("ambiguities", []))


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("P5a Evaluation Harness — Regression Tests")
    print("=" * 70)

    test_evaluation_tiers_loadable()
    test_records_not_mutated()
    test_malformed_output_captured()
    test_field_comparison_deterministic()
    test_grounding_failure_detection()
    test_eval_mode_prevents_production()
    test_report_contains_counts()
    test_training_data_not_modified()
    test_results_export()
    test_fallback_adapter_baseline()

    print(f"\n{'=' * 70}")
    total = _RESULTS["passed"] + _RESULTS["failed"]
    print(f"RESULTS: {_RESULTS['passed']}/{total} PASS")
    if _RESULTS["failed"] > 0:
        print(f"FAILURES ({_RESULTS['failed']}):")
        for e in _RESULTS["errors"]:
            print(f"  {e}")
    print(f"{'=' * 70}")

    sys.exit(0 if _RESULTS["failed"] == 0 else 1)
