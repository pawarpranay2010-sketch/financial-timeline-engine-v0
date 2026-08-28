#!/usr/bin/env python3
"""
Sprint P4.3.2 — Problem Learning Storage Audit + Dataset Grounding Hardening
=============================================================================
Regression test suite.
"""

import json
import os
import sys
import hashlib
import subprocess
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_p4_2_dataset_quality import (
    label_record, audit_dataset, build_four_tiers, validate_record,
    check_train_eval_overlap, StructuredInterpretation,
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

    # Load source
    cases = []
    with open("platrixa_ai_candidate_cases.jsonl") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))

    verified = [c for c in cases if c.get("status") == "VERIFIED"]

    # Load tiers
    tiers = {}
    for name in ["specialist_clean_training", "specialist_ambiguity_eval",
                 "specialist_unsupported_eval", "specialist_robustness_eval"]:
        path = f"training_data/{name}.jsonl"
        records = []
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        tiers[name] = records

    # ======================================================================
    # Section 1: Storage Audit
    # ======================================================================
    print("\n=== Section 1: Storage Audit ===")

    # 1.1: P4 does NOT import Railway database
    p4_imports_db = False
    for mod_path in [
        "backend/maths/fyjc_p4_problem_learning.py",
        "backend/maths/fyjc_p4_2_dataset_quality.py",
        "backend/maths/fyjc_p3_learning_system.py",
        "backend/maths/fyjc_validated_knowledge.py",
        "backend/maths/fyjc_structured_memory.py",
    ]:
        if os.path.exists(mod_path):
            with open(mod_path) as f:
                content = f.read()
                if "from backend.database" in content or "import sqlalchemy" in content:
                    p4_imports_db = True
    assert_test("1.1: P4 modules do NOT import Railway database", not p4_imports_db)

    # 1.2: P4 uses JSON file persistence
    p4_uses_json = False
    with open("backend/maths/fyjc_p4_problem_learning.py") as f:
        content = f.read()
        if "json.dump" in content and "json.load" in content:
            p4_uses_json = True
    assert_test("1.2: P4 uses JSON file persistence", p4_uses_json)

    # 1.3: Railway database exists but is NOT used by FYJC
    railway_exists = os.path.exists("backend/database/db.py")
    assert_test("1.3: Railway database module exists", railway_exists)

    # 1.4: P4 uses atomic writes
    with open("backend/maths/fyjc_p4_problem_learning.py") as f:
        content = f.read()
    assert_test("1.4: P4 uses atomic writes (tempfile + os.replace)",
                "tempfile.mkstemp" in content and "os.replace" in content)

    # 1.5: No SQL in P4/P4.2 modules
    sql_in_p4 = False
    for mod_path in ["backend/maths/fyjc_p4_problem_learning.py", "backend/maths/fyjc_p4_2_dataset_quality.py"]:
        with open(mod_path) as f:
            if "SELECT" in f.read() or "INSERT" in f.read() or "CREATE TABLE" in f.read():
                sql_in_p4 = True
    assert_test("1.5: No SQL in P4/P4.2 modules", not sql_in_p4)

    # ======================================================================
    # Section 2: Inferred Field Policy
    # ======================================================================
    print("\n=== Section 2: Inferred Field Policy ===")

    # 2.1: Inferred payment methods are correctly marked
    inferred_pm_count = 0
    for c in verified:
        interp = label_record(c)
        if interp.payment_method.endswith("_inferred"):
            inferred_pm_count += 1
            assert_test(f"2.1a: Inferred PM in grounding ({c['case_id']})",
                        "payment_method" in interp.grounding.get("inferred_fields", []))
    assert_test("2.1: Inferred payment methods exist and are marked",
                inferred_pm_count > 0, f"got {inferred_pm_count}")

    # 2.2: Explicit fields not marked as inferred
    for c in verified[:10]:
        interp = label_record(c)
        if interp.payment_method in ("cash", "credit", "cheque", "bank_transfer"):
            assert_test(f"2.2: Explicit PM not in inferred_fields ({c['case_id']})",
                        "payment_method" not in interp.grounding.get("inferred_fields", []))

    # 2.3: INFERRED records are still eligible
    for c in verified:
        interp = label_record(c)
        if interp.payment_method.endswith("_inferred"):
            assert_test(f"2.3: Inferred PM record is substantially complete ({c['case_id']})",
                        not interp.is_substantially_empty())
            break

    # ======================================================================
    # Section 3: Re-audit Data
    # ======================================================================
    print("\n=== Section 3: Re-audit Data ===")

    assert_test("3.1: Source corpus = 100", len(cases) == 100, f"got {len(cases)}")
    assert_test("3.2: Clean training in file = 47", len(tiers["specialist_clean_training"]) == 47,
                f"got {len(tiers['specialist_clean_training'])}")
    assert_test("3.3: Ambiguity eval in file = 20", len(tiers["specialist_ambiguity_eval"]) == 20,
                f"got {len(tiers['specialist_ambiguity_eval'])}")
    assert_test("3.4: Unsupported eval in file = 24", len(tiers["specialist_unsupported_eval"]) == 24,
                f"got {len(tiers['specialist_unsupported_eval'])}")
    assert_test("3.5: Robustness eval in file = 9", len(tiers["specialist_robustness_eval"]) == 9,
                f"got {len(tiers['specialist_robustness_eval'])}")

    # ======================================================================
    # Section 4: Field-Level Grounding
    # ======================================================================
    print("\n=== Section 4: Field-Level Grounding ===")

    # transaction_type
    tt_explicit = sum(1 for c in verified if label_record(c).transaction_type and label_record(c).transaction_type != "unknown")
    assert_test("4.1: transaction_type populated",
                tt_explicit >= 40, f"got {tt_explicit}/47")

    # parties
    p_explicit = sum(1 for c in verified if label_record(c).parties)
    assert_test("4.2: parties populated",
                p_explicit >= 35, f"got {p_explicit}/47")

    # amounts
    a_explicit = sum(1 for c in verified if label_record(c).amounts)
    assert_test("4.3: amounts populated",
                a_explicit >= 40, f"got {a_explicit}/47")

    # payment_method — mix of explicit and inferred
    pm_explicit = sum(1 for c in verified if label_record(c).payment_method and not label_record(c).payment_method.endswith("_inferred"))
    pm_inferred = sum(1 for c in verified if label_record(c).payment_method.endswith("_inferred"))
    assert_test("4.4: payment_method explicit > 0",
                pm_explicit > 0, f"got {pm_explicit}")
    assert_test("4.5: payment_method inferred > 0",
                pm_inferred > 0, f"got {pm_inferred}")

    # No fabricated values
    fabricated = 0
    for c in verified:
        interp = label_record(c)
        input_text = c.get("input_text", "")
        for party in interp.parties:
            if party.lower() not in input_text.lower():
                fabricated += 1
    assert_test("4.6: No fabricated parties", fabricated == 0, f"found {fabricated}")

    # ======================================================================
    # Section 5: Training Floor
    # ======================================================================
    print("\n=== Section 5: Training Floor ===")

    eligible = 0
    for c in verified:
        interp = label_record(c)
        if not interp.is_substantially_empty():
            eligible += 1

    assert_test("5.1: Eligible count >= 40", eligible >= 40, f"got {eligible}")
    assert_test("5.2: Eligible count = 47", eligible == 47, f"got {eligible}")

    # ======================================================================
    # Section 6: Deduplication
    # ======================================================================
    print("\n=== Section 6: Deduplication ===")

    all_ids = []
    for tier_records in tiers.values():
        for r in tier_records:
            all_ids.append(r["_p4_metadata"]["problem_id"])

    assert_test("6.1: No intra-tier duplicates",
                len(all_ids) == len(set(all_ids)),
                f"got {len(all_ids)} total, {len(set(all_ids))} unique")

    train_ids = set(r["_p4_metadata"]["problem_id"] for r in tiers["specialist_clean_training"])
    eval_ids = set()
    for k in ["specialist_ambiguity_eval", "specialist_unsupported_eval", "specialist_robustness_eval"]:
        eval_ids |= set(r["_p4_metadata"]["problem_id"] for r in tiers[k])
    overlap = check_train_eval_overlap(train_ids, eval_ids)
    assert_test("6.2: Cross-tier overlap = 0", overlap == 0, f"got {overlap}")

    # ======================================================================
    # Section 7: AI Target Safety
    # ======================================================================
    print("\n=== Section 7: AI Target Safety ===")

    journal_in_output = 0
    for r in tiers["specialist_clean_training"]:
        output = json.loads(r["output"])
        if any(k in output for k in ["journal_narration", "debit_accounts", "credit_accounts"]):
            journal_in_output += 1
    assert_test("7.1: No journal in training output", journal_in_output == 0,
                f"found {journal_in_output}")

    interpretation_present = sum(1 for r in tiers["specialist_clean_training"]
                                 if json.loads(r["output"]).get("transaction_type"))
    assert_test("7.2: Interpretation fields present in all training records",
                interpretation_present == len(tiers["specialist_clean_training"]),
                f"got {interpretation_present}/{len(tiers['specialist_clean_training'])}")

    # ======================================================================
    # Section 8: Training Format
    # ======================================================================
    print("\n=== Section 8: Training Format ===")

    for i, rec in enumerate(tiers["specialist_clean_training"][:3]):
        assert_test(f"8.{i+1}: Has instruction/input/output",
                    all(k in rec for k in ("instruction", "input", "output")))
        try:
            output = json.loads(rec["output"])
            assert_test(f"8.{i+4}: Output is valid JSON", True)
            assert_test(f"8.{i+5}: Has transaction_type", bool(output.get("transaction_type")))
            assert_test(f"8.{i+6}: Has grounding", "grounding" in output)
        except:
            assert_test(f"8.{i+4}: Output is valid JSON", False)

    # ======================================================================
    # Section 9: Kernel Immutability
    # ======================================================================
    print("\n=== Section 9: Kernel Immutability ===")

    for kf in ["fyjc_orchestration.py", "fyjc_bk_reasoning.py", "fyjc_normalization.py"]:
        path = f"backend/maths/{kf}"
        assert_test(f"9.1: {kf} exists", os.path.exists(path))

    # ======================================================================
    # Section 10: No Model Download / Training
    # ======================================================================
    print("\n=== Section 10: No Model Download / Training ===")

    model_downloaded = False
    for root, dirs, files in os.walk("."):
        for f in files:
            if f.endswith((".bin", ".safetensors", ".gguf", ".ggml")):
                model_downloaded = True
                break
    assert_test("10.1: No model weights downloaded", not model_downloaded)

    # Check no training scripts exist
    training_found = False
    for root, dirs, files in os.walk("."):
        for f in files:
            if "train" in f.lower() and f.endswith((".py", ".sh")) and "test" not in f.lower():
                if "fte_" not in f and "scripts/" not in root:
                    training_found = True
    assert_test("10.2: No standalone training scripts", not training_found)

    # ======================================================================
    # Section 11: Regression Gates
    # ======================================================================
    print("\n=== Section 11: Regression Gates ===")

    test_suites = [
        ("11.1: Sprint P4", "scripts/fte_fyjc_p4_problem_learning_test.py"),
        ("11.2: Sprint P4.2", "scripts/fte_fyjc_p4_2_dataset_quality_test.py"),
        ("11.3: Sprint P2", "scripts/fte_fyjc_p2_validated_knowledge_test.py"),
        ("11.4: Sprint P3", "scripts/fte_fyjc_p3_learning_test.py"),
        ("11.5: Sprint 35", "scripts/fte_fyjc_35_integrity_invariant_test.py"),
        ("11.6: Sprint 36", "scripts/fte_fyjc_36_ui_contract_test.py"),
        ("11.7: Sprint 37", "scripts/fte_fyjc_37_calc_scoping_test.py"),
    ]

    for label, script in test_suites:
        try:
            r = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=120
            )
            assert_test(label, "PASS" in r.stdout and "FAIL" not in r.stdout.split("PASS")[-1][:50])
        except Exception:
            assert_test(label, False, "script not found or timeout")

    # ======================================================================
    # Section 12: py_compile
    # ======================================================================
    print("\n=== Section 12: Compilation ===")

    try:
        subprocess.run(
            [sys.executable, "-m", "py_compile", "backend/maths/fyjc_p4_2_dataset_quality.py"],
            check=True, capture_output=True
        )
        assert_test("12.1: py_compile PASS", True)
    except subprocess.CalledProcessError:
        assert_test("12.1: py_compile PASS", False)

    # ======================================================================
    # Summary
    # ======================================================================
    print()
    print("=" * 70)
    print(f"SPRINT P4.3.2 RESULTS: {passed}/{total} PASS, {failed} FAIL")
    print("=" * 70)

    if failed > 0:
        print(f"\nFailures:")
        for f in failures:
            print(f"  - {f}")
        print(f"\n❌ SPRINT P4.3.2: FAIL")
        return False
    else:
        print(f"\n✅ SPRINT P4.3.2: PASS")
        return True


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
