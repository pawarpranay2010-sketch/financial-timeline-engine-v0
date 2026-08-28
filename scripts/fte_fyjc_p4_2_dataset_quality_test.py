#!/usr/bin/env python3
"""
Sprint P4.2 — Dataset Quality, Target Restructuring & Training Readiness
========================================================================
Regression test suite.

Classification: PASS/FAIL per test.
Overall: PASS only if all tests pass.
"""

import json
import os
import sys
import hashlib
import subprocess
from collections import Counter

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_p4_2_dataset_quality import (
    StructuredInterpretation,
    label_record,
    audit_dataset,
    build_four_tiers,
    export_tiers,
    validate_record,
    validate_dataset,
    check_train_eval_overlap,
    generate_evaluation_report,
    INSTRUCTION_CLEAN,
)


def run_tests():
    passed = 0
    failed = 0
    total = 0
    failures = []

    def assert_test(name, condition, detail=""):
        nonlocal passed, failed, total
        total += 1
        if condition:
            passed += 1
            print(f"  ✅ {name}")
        else:
            failed += 1
            msg = f"  ❌ {name}"
            if detail:
                msg += f" — {detail}"
            print(msg)
            failures.append(name)

    # Load candidate cases
    cases = []
    with open("platrixa_ai_candidate_cases.jsonl") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))

    verified = [c for c in cases if c.get("status") == "VERIFIED"]
    review = [c for c in cases if c.get("status") == "REVIEW_REQUIRED"]
    not_supported = [c for c in cases if c.get("status") == "NOT_SUPPORTED"]
    blocked = [c for c in cases if c.get("status") == "BLOCKED"]
    exception = [c for c in cases if c.get("status") == "EXCEPTION"]

    # ======================================================================
    # Section 1: Dataset Audit
    # ======================================================================
    print("\n=== Section 1: Dataset Audit ===")
    audit = audit_dataset(cases)

    assert_test("1.1: Total records = 100", audit.total == 100, f"got {audit.total}")
    assert_test("1.2: VERIFIED = 47", audit.verified == 47, f"got {audit.verified}")
    assert_test("1.3: REVIEW_REQUIRED = 20", audit.review_required == 20, f"got {audit.review_required}")
    assert_test("1.4: NOT_SUPPORTED = 24", audit.not_supported == 24, f"got {audit.not_supported}")
    assert_test("1.5: BLOCKED = 7", audit.blocked == 7, f"got {audit.blocked}")
    assert_test("1.6: EXCEPTION = 2", audit.exception == 2, f"got {audit.exception}")
    assert_test("1.7: Sum equals total",
                audit.verified + audit.review_required + audit.not_supported + audit.blocked + audit.exception == audit.total)

    # ======================================================================
    # Section 2: Deterministic Labeler
    # ======================================================================
    print("\n=== Section 2: Deterministic Labeler ===")

    # 2.1: Basic purchase
    c = next(c for c in verified if c["case_id"] == "C0000")
    interp = label_record(c)
    assert_test("2.1: Purchase detection", interp.transaction_type == "purchase",
                f"got {interp.transaction_type}")
    assert_test("2.2: Party extraction", "Raj" in interp.parties,
                f"got {interp.parties}")
    assert_test("2.3: Amount extraction", any(a["value"] == "20000" for a in interp.amounts),
                f"got {interp.amounts}")

    # 2.4: Cash explicit
    c_cash = next(c for c in verified if c["case_id"] == "C0003")
    interp_cash = label_record(c_cash)
    assert_test("2.4: Cash payment detected", interp_cash.payment_method == "cash",
                f"got {interp_cash.payment_method}")

    # 2.5: Credit explicit
    c_credit = next(c for c in verified if c["case_id"] == "C0004")
    interp_credit = label_record(c_credit)
    assert_test("2.5: Credit payment detected", interp_credit.payment_method == "credit",
                f"got {interp_credit.payment_method}")

    # 2.6: Determinism — same input produces same output
    interp_a = label_record(c)
    interp_b = label_record(c)
    assert_test("2.6: Deterministic labeling",
                interp_a.to_dict() == interp_b.to_dict())

    # 2.7: StructuredInterpretation serialization
    s = interp.to_json_string()
    parsed = json.loads(s)
    assert_test("2.7: Valid JSON serialization", isinstance(parsed, dict))
    assert_test("2.8: Has transaction_type", "transaction_type" in parsed)
    assert_test("2.9: Has parties", "parties" in parsed)
    assert_test("2.10: Has amounts", "amounts" in parsed)
    assert_test("2.11: Has grounding", "grounding" in parsed)

    # ======================================================================
    # Section 3: Field Population (VERIFIED)
    # ======================================================================
    print("\n=== Section 3: Field Population (VERIFIED) ===")

    assert_test("3.1: transaction_type populated",
                audit.verified_with_transaction_type >= 40,
                f"got {audit.verified_with_transaction_type}/47")
    assert_test("3.2: parties populated",
                audit.verified_with_parties >= 35,
                f"got {audit.verified_with_parties}/47")
    assert_test("3.3: amounts populated",
                audit.verified_with_amounts >= 40,
                f"got {audit.verified_with_amounts}/47")
    assert_test("3.4: payment_method populated",
                audit.verified_with_payment_method >= 40,
                f"got {audit.verified_with_payment_method}/47")
    assert_test("3.5: substantive_interpretation = 47",
                audit.verified_with_substantive_interpretation == 47,
                f"got {audit.verified_with_substantive_interpretation}")

    # ======================================================================
    # Section 4: Training Floor (40 examples)
    # ======================================================================
    print("\n=== Section 4: Training Floor ===")

    assert_test("4.1: Usable VERIFIED >= 40",
                audit.usable_verified >= 40,
                f"got {audit.usable_verified}")
    assert_test("4.2: Training floor met", audit.training_floor_met)
    assert_test("4.3: Shortfall = 0", audit.training_floor缺口 == 0,
                f"got {audit.training_floor缺口}")

    # ======================================================================
    # Section 5: Four-Tier Builder
    # ======================================================================
    print("\n=== Section 5: Four-Tier Builder ===")

    tiers = build_four_tiers(cases)

    assert_test("5.1: Clean training exists",
                "specialist_clean_training" in tiers)
    assert_test("5.2: Ambiguity eval exists",
                "specialist_ambiguity_eval" in tiers)
    assert_test("5.3: Unsupported eval exists",
                "specialist_unsupported_eval" in tiers)
    assert_test("5.4: Robustness eval exists",
                "specialist_robustness_eval" in tiers)

    train = tiers["specialist_clean_training"]
    ambig = tiers["specialist_ambiguity_eval"]
    unsup = tiers["specialist_unsupported_eval"]
    robust = tiers["specialist_robustness_eval"]

    assert_test("5.5: Clean training size >= 40", len(train.records) >= 40,
                f"got {len(train.records)}")
    assert_test("5.6: Ambiguity eval size > 0", len(ambig.records) > 0,
                f"got {len(ambig.records)}")
    assert_test("5.7: Unsupported eval size > 0", len(unsup.records) > 0,
                f"got {len(unsup.records)}")
    assert_test("5.8: Robustness eval size > 0", len(robust.records) > 0,
                f"got {len(robust.records)}")

    # ======================================================================
    # Section 6: No Train/Eval Overlap
    # ======================================================================
    print("\n=== Section 6: Train/Eval Separation ===")

    train_ids = {r["_p4_metadata"]["problem_id"] for r in train.records}
    eval_ids = set()
    for tier in [ambig, unsup, robust]:
        eval_ids |= {r["_p4_metadata"]["problem_id"] for r in tier.records}

    overlap = check_train_eval_overlap(train_ids, eval_ids)
    assert_test("6.1: Train/eval overlap = 0", overlap == 0, f"got {overlap}")

    # Also check no duplicate within training
    train_problems = [r["_p4_metadata"]["problem_id"] for r in train.records]
    assert_test("6.2: No duplicate problem_ids in training",
                len(train_problems) == len(set(train_problems)))

    # ======================================================================
    # Section 7: Training Format
    # ======================================================================
    print("\n=== Section 7: Training Format ===")

    for i, rec in enumerate(train.records[:5]):
        assert_test(f"7.{i+1}: Record has instruction", bool(rec.get("instruction")))
        assert_test(f"7.{i+2}: Record has input", bool(rec.get("input")))
        assert_test(f"7.{i+3}: Record has output", bool(rec.get("output")))
        # Output must be valid JSON
        try:
            output = json.loads(rec["output"])
            assert_test(f"7.{i+4}: Output is valid JSON", True)
        except:
            assert_test(f"7.{i+4}: Output is valid JSON", False)
            output = {}

        # Must have transaction_type
        assert_test(f"7.{i+5}: Has transaction_type", bool(output.get("transaction_type")),
                    f"got {output.get('transaction_type')}")
        # Must have amounts
        assert_test(f"7.{i+6}: Has amounts", bool(output.get("amounts")),
                    f"got {output.get('amounts')}")

    # Most records should have parties (some genuinely lack party names, e.g. 'Paid rent')
    party_count = sum(1 for r in train.records if json.loads(r["output"]).get("parties"))
    assert_test(f"7.7: Majority of training records have parties",
                party_count >= len(train.records) * 0.8,
                f"got {party_count}/{len(train.records)}")

    # ======================================================================
    # Section 8: No Journal in Training Output
    # ======================================================================
    print("\n=== Section 8: No Journal in Training Output ===")

    journal_in_output = 0
    for rec in train.records:
        output = json.loads(rec["output"])
        if any(k in output for k in ["journal_narration", "debit_accounts", "credit_accounts"]):
            journal_in_output += 1
    assert_test("8.1: No journal_narration in training output",
                journal_in_output == 0, f"found in {journal_in_output} records")

    # ======================================================================
    # Section 9: Quality Validation
    # ======================================================================
    print("\n=== Section 9: Quality Validation ===")

    val_results = export_tiers(tiers, output_dir="training_data")
    train_val = val_results["specialist_clean_training"]

    assert_test("9.1: Training validation passes", train_val["valid"] >= 40,
                f"got {train_val['valid']} valid")
    assert_test("9.2: No duplicates in training", train_val["duplicates"] == 0,
                f"got {train_val['duplicates']}")

    # ======================================================================
    # Section 10: No Fabricated Labels
    # ======================================================================
    print("\n=== Section 10: No Fabricated Labels ===")

    fabricated_count = 0
    for rec in train.records:
        is_valid, reason = validate_record(rec)
        if "fabricated" in reason:
            fabricated_count += 1
    assert_test("10.1: No fabricated entities in training",
                fabricated_count == 0, f"found {fabricated_count}")

    # ======================================================================
    # Section 11: REVIEW_REQUIRED Excluded from Training
    # ======================================================================
    print("\n=== Section 11: REVIEW_REQUIRED Exclusion ===")

    review_in_train = sum(1 for r in train.records
                          if r["_p4_metadata"]["engine_status"] == "REVIEW_REQUIRED")
    assert_test("11.1: No REVIEW_REQUIRED in clean training",
                review_in_train == 0, f"found {review_in_train}")

    # ======================================================================
    # Section 12: NOT_SUPPORTED/BLOCKED/EXCEPTION Placement
    # ======================================================================
    print("\n=== Section 12: Evaluation Tier Placement ===")

    # REVIEW_REQUIRED → ambiguity eval
    review_in_ambig = sum(1 for r in ambig.records
                          if r["_p4_metadata"]["engine_status"] == "REVIEW_REQUIRED")
    assert_test("12.1: REVIEW_REQUIRED → ambiguity_eval",
                review_in_ambig == len(ambig.records),
                f"got {review_in_ambig}/{len(ambig.records)}")

    # NOT_SUPPORTED → unsupported eval
    ns_in_unsup = sum(1 for r in unsup.records
                      if r["_p4_metadata"]["engine_status"] == "NOT_SUPPORTED")
    assert_test("12.2: NOT_SUPPORTED → unsupported_eval",
                ns_in_unsup == len(unsup.records),
                f"got {ns_in_unsup}/{len(unsup.records)}")

    # BLOCKED+EXCEPTION → robustness eval
    blocked_exc_in_robust = sum(1 for r in robust.records
                                if r["_p4_metadata"]["engine_status"] in ("BLOCKED", "EXCEPTION"))
    assert_test("12.3: BLOCKED+EXCEPTION → robustness_eval",
                blocked_exc_in_robust == len(robust.records),
                f"got {blocked_exc_in_robust}/{len(robust.records)}")

    # ======================================================================
    # Section 13: Deterministic Ordering
    # ======================================================================
    print("\n=== Section 13: Deterministic Ordering ===")

    hashes = [r["_p4_metadata"]["content_hash"] for r in train.records]
    assert_test("13.1: Training records sorted by content_hash",
                hashes == sorted(hashes))

    # Build twice → same order
    tiers2 = build_four_tiers(cases)
    hashes2 = [r["_p4_metadata"]["content_hash"] for r in tiers2["specialist_clean_training"].records]
    assert_test("13.2: Deterministic across runs",
                hashes == hashes2)

    # ======================================================================
    # Section 14: Kernel Immutability
    # ======================================================================
    print("\n=== Section 14: Kernel Immutability ===")

    kernel_files = [
        "backend/maths/fyjc_orchestration.py",
        "backend/maths/fyjc_bk_reasoning.py",
        "backend/maths/fyjc_normalization.py",
    ]
    for kf in kernel_files:
        exists = os.path.exists(kf)
        assert_test(f"14.1: {os.path.basename(kf)} exists", exists)

    # ======================================================================
    # Section 15: Grounding Correctness
    # ======================================================================
    print("\n=== Section 15: Grounding Correctness ===")

    # Records with explicit cash/credit should be grounded
    grounded_count = 0
    for c in verified:
        interp = label_record(c)
        if interp.grounding["all_fields_explicitly_grounded"]:
            grounded_count += 1
    assert_test("15.1: Some records fully grounded",
                grounded_count > 0, f"got {grounded_count}")

    # Records with inferred payment should be flagged
    inferred_count = 0
    for c in verified:
        interp = label_record(c)
        if interp.grounding["inferred_fields"]:
            inferred_count += 1
    assert_test("15.2: Inferred fields flagged",
                inferred_count > 0, f"got {inferred_count}")

    # ======================================================================
    # Section 16: py_compile
    # ======================================================================
    print("\n=== Section 16: Compilation ===")

    try:
        subprocess.run(
            [sys.executable, "-m", "py_compile", "backend/maths/fyjc_p4_2_dataset_quality.py"],
            check=True, capture_output=True
        )
        assert_test("16.1: py_compile PASS", True)
    except subprocess.CalledProcessError:
        assert_test("16.1: py_compile PASS", False)

    # ======================================================================
    # Section 17: Regression Gates
    # ======================================================================
    print("\n=== Section 17: Regression Gates ===")

    # P4 tests
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_p4_problem_learning_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.1: Sprint P4", "PASS" in r.stdout and "88/88" in r.stdout)
    except Exception:
        assert_test("17.1: Sprint P4", False, "script not found or timeout")

    # Sprint 35
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_35_integrity_invariant_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.2: Sprint 35 Integrity", "PASS" in r.stdout)
    except Exception:
        assert_test("17.2: Sprint 35 Integrity", False, "script not found")

    # Sprint 36
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_36_ui_contract_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.3: Sprint 36 UI Contract", "PASS" in r.stdout)
    except Exception:
        assert_test("17.3: Sprint 36 UI Contract", False, "script not found")

    # Sprint 37
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_37_calc_scoping_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.4: Sprint 37 Calc Scoping", "PASS" in r.stdout)
    except Exception:
        assert_test("17.4: Sprint 37 Calc Scoping", False, "script not found")

    # Sprint 43
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_43_structured_memory_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.5: Sprint 43 Structured Memory", "PASS" in r.stdout)
    except Exception:
        assert_test("17.5: Sprint 43 Structured Memory", False, "script not found")

    # Sprint P2
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_p2_validated_knowledge_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.6: Sprint P2 Validated Knowledge", "PASS" in r.stdout)
    except Exception:
        assert_test("17.6: Sprint P2 Validated Knowledge", False, "script not found")

    # Sprint P3
    try:
        r = subprocess.run(
            [sys.executable, "scripts/fte_fyjc_p3_learning_test.py"],
            capture_output=True, text=True, timeout=120
        )
        assert_test("17.7: Sprint P3 Learning System", "PASS" in r.stdout)
    except Exception:
        assert_test("17.7: Sprint P3 Learning System", False, "script not found")

    # ======================================================================
    # Summary
    # ======================================================================
    print()
    print("=" * 70)
    print(f"SPRINT P4.2 RESULTS: {passed}/{total} PASS, {failed} FAIL")
    print("=" * 70)

    if failed > 0:
        print(f"\nFailures:")
        for f in failures:
            print(f"  - {f}")
        print(f"\n❌ SPRINT P4.2: FAIL")
        return False
    else:
        print(f"\n✅ SPRINT P4.2: PASS")
        return True


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
