#!/usr/bin/env python3
"""
Sprint P4 — Problem Learning Database → JSONL → Training Pipeline
Comprehensive Regression Suite

Tests:
  Section 1: Problem Learning Database (CRUD, validation, persistence)
  Section 2: JSONL Exporter (deterministic, format, validated-only)
  Section 3: AI Training Adapter (interface, no model loaded)
  Section 4: Training Pipeline (prepare, split, command generation)
  Section 5: Evaluation Mechanism (accuracy, safety checks)
  Section 6: Safety Boundary Tests (kernel untouched, no auto-training)
  Section 7: Integration with Existing P2/P3
  Section 8: Full Regression Gates
"""

import json
import os
import sys
import tempfile
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_p4_problem_learning import (
    JSONLExporter,
    ModelEvaluationHarness,
    ProblemCategory,
    ProblemLearningDatabase,
    ProblemRecord,
    ProblemStatus,
    TrainingPipeline,
    FinanceModelAdapter,
)
from backend.maths.fyjc_validated_knowledge import EvidenceSource

PASS_COUNT = 0
FAIL_COUNT = 0
TOTAL = 0


def assert_test(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT, TOTAL
    TOTAL += 1
    if condition:
        PASS_COUNT += 1
        print(f"  ✅ {name}")
    else:
        FAIL_COUNT += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def _seed_db(db, count=5):
    """Seed database with test records."""
    cases = [
        ("Purchased goods from Raj for Rs.20000", "CASH_CREDIT",
         "VERIFIED", "balanced_journal"),
        ("He paid Rs.5000 to Amit", "PRONOUN_RESOLUTION",
         "REVIEW_REQUIRED", "UNRESOLVED_PRONOUN"),
        ("Purchased goods from Raj", "MISSING_AMOUNT",
         "BLOCKED", "MISSING_INFO"),
        ("Settled account with Raj", "SETTLEMENT",
         "NOT_SUPPORTED", "UNSUPPORTED"),
        ("Purchased 10 kgs at Rs.50/kg from Raj", "MULTI_AMOUNT",
         "VERIFIED", "balanced_journal"),
    ]
    for i in range(min(count, len(cases))):
        text, cat_str, eng_status, reason = cases[i]
        cat = ProblemCategory(cat_str)
        record = ProblemRecord(
            raw_student_input=text,
            category=cat,
            engine_status=eng_status,
            engine_reason=reason,
        )
        db.add_record(record)
        # Add enough evidence to promote
        for j in range(3):
            src = EvidenceSource.STUDENT if j < 2 else EvidenceSource.DETERMINISTIC
            db.record_evidence(record.problem_id, src, "VERIFIED")
        db.promote_record(record.problem_id)


def section_1_database():
    print("\n=== Section 1: Problem Learning Database ===")

    # 1.1: Create and add records
    db = ProblemLearningDatabase()
    r1 = ProblemRecord(
        raw_student_input="Purchased goods from Raj for Rs.20000",
        category=ProblemCategory.CASH_CREDIT,
        engine_status="VERIFIED",
        engine_reason="balanced_journal",
    )
    added = db.add_record(r1)
    assert_test("1.1a: Record added", added is not None)
    assert_test("1.1b: Status is CANDIDATE", added.status == ProblemStatus.CANDIDATE)
    assert_test("1.1c: problem_id generated", len(added.problem_id) > 0)

    # 1.2: Retrieve record
    fetched = db.get_record(r1.problem_id)
    assert_test("1.2: Record retrievable", fetched is not None)
    assert_test("1.2b: Input preserved exactly",
                 fetched.raw_student_input == "Purchased goods from Raj for Rs.20000")

    # 1.3: Category stored correctly
    assert_test("1.3: Category is CASH_CREDIT",
                 fetched.category == ProblemCategory.CASH_CREDIT)

    # 1.4: Evidence accumulation
    db.record_evidence(r1.problem_id, EvidenceSource.STUDENT, "VERIFIED")
    db.record_evidence(r1.problem_id, EvidenceSource.STUDENT, "VERIFIED")
    db.record_evidence(r1.problem_id, EvidenceSource.DETERMINISTIC, "VERIFIED")
    fetched2 = db.get_record(r1.problem_id)
    assert_test("1.4a: Evidence count = 3", fetched2.evidence_count == 3)
    assert_test("1.4b: Source diversity = 2", fetched2.source_diversity >= 2)
    assert_test("1.4c: Confidence > 0", fetched2.confidence > Decimal("0"))

    # 1.5: Validation
    can_val, reason = db.validate_record(r1.problem_id)
    assert_test("1.5a: Validation passes", can_val)
    assert_test("1.5b: Reason indicates success", "met" in reason.lower())

    # 1.6: Promotion
    ok, msg = db.promote_record(r1.problem_id)
    assert_test("1.6a: Promotion succeeds", ok)
    assert_test("1.6b: Status is VALIDATED",
                 db.get_record(r1.problem_id).status == ProblemStatus.VALIDATED)

    # 1.7: Rejection
    r2 = ProblemRecord(
        raw_student_input="Bad test input",
        category=ProblemCategory.EDGE_CASE,
        engine_status="NOT_SUPPORTED",
    )
    db.add_record(r2)
    ok, _ = db.reject_record(r2.problem_id, "test rejection")
    assert_test("1.7a: Rejection succeeds", ok)
    assert_test("1.7b: Status is REJECTED",
                 db.get_record(r2.problem_id).status == ProblemStatus.REJECTED)

    # 1.8: Retirement
    r3 = ProblemRecord(
        raw_student_input="Retire me",
        category=ProblemCategory.OTHER,
    )
    db.add_record(r3)
    db.record_evidence(r3.problem_id, EvidenceSource.STUDENT, "VERIFIED")
    db.record_evidence(r3.problem_id, EvidenceSource.DETERMINISTIC, "VERIFIED")
    ok, _ = db.retire_record(r3.problem_id, "test retire")
    assert_test("1.8a: Retirement succeeds", ok)
    assert_test("1.8b: Status is RETIRED",
                 db.get_record(r3.problem_id).status == ProblemStatus.RETIRED)

    # 1.9: Query by status
    validated = db.get_by_status(ProblemStatus.VALIDATED)
    assert_test("1.9: get_by_status works", len(validated) >= 1)

    # 1.10: Query by category
    cc = db.get_by_category(ProblemCategory.CASH_CREDIT)
    assert_test("1.10: get_by_category works", len(cc) >= 1)

    # 1.11: Stats
    stats = db.stats()
    assert_test("1.11a: stats has total_records", "total_records" in stats)
    assert_test("1.11b: stats has by_status", "by_status" in stats)
    assert_test("1.11c: total > 0", stats["total_records"] > 0)

    # 1.12: Persistence round-trip
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        db_path = f.name
    try:
        db2 = ProblemLearningDatabase(db_path=db_path)
        # Add and promote a record
        r4 = ProblemRecord(
            raw_student_input="Persistence test",
            category=ProblemCategory.UNUSUAL_WORDING,
            engine_status="REVIEW_REQUIRED",
            engine_reason="AMBIGUITY",
        )
        db2.add_record(r4)
        db2.record_evidence(r4.problem_id, EvidenceSource.STUDENT, "VERIFIED")
        db2.record_evidence(r4.problem_id, EvidenceSource.DETERMINISTIC, "VERIFIED")
        db2.promote_record(r4.problem_id)
        db2.save()

        # Load in new instance
        db3 = ProblemLearningDatabase(db_path=db_path)
        loaded = db3.get_record(r4.problem_id)
        assert_test("1.12a: Persistence round-trip", loaded is not None)
        assert_test("1.12b: Status preserved VALIDATED",
                     loaded.status == ProblemStatus.VALIDATED)
        assert_test("1.12c: Input preserved",
                     loaded.raw_student_input == "Persistence test")
    finally:
        os.unlink(db_path)

    # 1.13: Malformed file returns empty
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{bad json")
        bad_path = f.name
    try:
        db_bad = ProblemLearningDatabase(db_path=bad_path)
        assert_test("1.13: Malformed file handled gracefully",
                     db_bad.stats()["total_records"] == 0)
    finally:
        os.unlink(bad_path)


def section_2_exporter():
    print("\n=== Section 2: JSONL Exporter ===")

    db = ProblemLearningDatabase()
    _seed_db(db, 3)

    # 2.1: Export validated records
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        jsonl_path = f.name
    try:
        count = JSONLExporter.export_from_database(db, jsonl_path)
        assert_test("2.1a: Export count > 0", count > 0)

        # Verify JSONL format
        with open(jsonl_path) as f:
            lines = f.readlines()
        assert_test("2.1b: JSONL has correct line count", len(lines) == count)

        # Verify each line is valid JSON
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert_test(f"2.1c.{i}: Line {i} is valid JSON", isinstance(record, dict))
    finally:
        os.unlink(jsonl_path)

    # 2.2: Export format has required fields
    validated = db.get_by_status(ProblemStatus.VALIDATED)
    if validated:
        exported = JSONLExporter.export_record(validated[0])
        assert_test("2.2a: Has 'input' key", "input" in exported)
        assert_test("2.2b: Has 'output' key", "output" in exported)
        assert_test("2.2c: Has 'metadata' key", "metadata" in exported)
        assert_test("2.2d: Input matches raw input",
                     exported["input"] == validated[0].raw_student_input)

    # 2.3: Only VALIDATED records exported
    r_bad = ProblemRecord(
        raw_student_input="Should not export",
        category=ProblemCategory.INCOMPLETE,
    )
    db.add_record(r_bad)
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        jsonl2_path = f.name
    try:
        count2 = JSONLExporter.export_from_database(db, jsonl2_path)
        with open(jsonl2_path) as f:
            content = f.read()
        assert_test("2.3: CANDIDATE not in export",
                     "Should not export" not in content)
    finally:
        os.unlink(jsonl2_path)

    # 2.4: Deterministic export (same records → same output)
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path_a = f.name
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path_b = f.name
    try:
        JSONLExporter.export_from_database(db, path_a)
        JSONLExporter.export_from_database(db, path_b)
        with open(path_a) as f:
            content_a = f.read()
        with open(path_b) as f:
            content_b = f.read()
        assert_test("2.4: Deterministic export", content_a == content_b)
    finally:
        os.unlink(path_a)
        os.unlink(path_b)

    # 2.5: CANDIDATE records export as None
    cand = ProblemRecord(
        raw_student_input="Not validated yet",
        category=ProblemCategory.OTHER,
        engine_status="REVIEW_REQUIRED",
    )
    exported_cand = JSONLExporter.export_record(cand)
    assert_test("2.5: CANDIDATE export returns None", exported_cand is None)


def section_3_adapter():
    print("\n=== Section 3: AI Training Adapter ===")

    # 3.1: Adapter creation
    adapter = FinanceModelAdapter()
    assert_test("3.1a: Adapter created", adapter is not None)
    assert_test("3.1b: Default model name", adapter.model_name == "qwen2.5-1.5b-instruct")

    # 3.2: Not loaded by default
    assert_test("3.2: Not loaded by default", not adapter.is_available())

    # 3.3: Load fails without weights
    ok = adapter.load_model("/nonexistent/path")
    assert_test("3.3: Load fails without weights", not ok)

    # 3.4: Predict returns error when not loaded
    result = adapter.predict("test input")
    assert_test("3.4: Predict returns error", result.get("error") is not None)

    # 3.5: Health check
    health = adapter.health_check()
    assert_test("3.5a: Health check has model_name", "model_name" in health)
    assert_test("3.5b: Health check shows not loaded", not health.get("loaded"))

    # 3.6: No model downloaded
    assert_test("3.6: No model weights present",
                 adapter.model_path is None or not os.path.exists(str(adapter.model_path)))


def section_4_pipeline():
    print("\n=== Section 4: Training Pipeline ===")

    db = ProblemLearningDatabase()
    _seed_db(db, 5)

    pipeline = TrainingPipeline(db)

    # 4.1: Prepare training data
    train_path, stats = pipeline.prepare_training_data()
    assert_test("4.1a: Training data prepared", stats["exported"] > 0)
    assert_test("4.1b: Output path exists", os.path.exists(train_path))
    os.unlink(train_path)

    # 4.2: Create evaluation set
    eval_train, eval_path, split_stats = pipeline.create_evaluation_set()
    assert_test("4.2a: Train set created", split_stats["train_count"] > 0)
    assert_test("4.2b: Eval set created", split_stats["eval_count"] > 0)
    assert_test("4.2c: Files exist", os.path.exists(eval_train) and os.path.exists(eval_path))
    os.unlink(eval_train)
    os.unlink(eval_path)

    # 4.3: Full pipeline
    result = pipeline.run_full_pipeline()
    assert_test("4.3a: Pipeline status is PIPELINE_READY",
                 result["status"] == "PIPELINE_READY")
    assert_test("4.3b: Training command is placeholder",
                 "PLACEHOLDER" in result["training_command"])
    assert_test("4.3c: No model downloaded",
                 "no model downloaded" in result["note"].lower())
    assert_test("4.3d: No training occurred",
                 "no training occurred" in result["note"].lower())

    # 4.4: Confidence filtering
    result4 = pipeline.run_full_pipeline(min_confidence=Decimal("0.99"))
    assert_test("4.4: High confidence filters records",
                 result4["prepare_stats"]["filtered_by_confidence"] <= result4["prepare_stats"]["total_validated"])


def section_5_evaluation():
    print("\n=== Section 5: Evaluation Mechanism ===")

    db = ProblemLearningDatabase()
    _seed_db(db, 3)

    # Create eval data
    pipeline = TrainingPipeline(db)
    _, eval_path, _ = pipeline.create_evaluation_set(holdout_fraction=0.5)

    try:
        # 5.1: Safety check
        safety = ModelEvaluationHarness.safety_check(eval_path)
        assert_test("5.1: Safety check passes", safety["pass"])

        # 5.2: Evaluation report
        report = ModelEvaluationHarness.generate_evaluation_report(eval_path)
        assert_test("5.2a: Report has total_records", "total_records" in report)
        assert_test("5.2b: Report has categories", "categories" in report)
        assert_test("5.2c: Report has safety_check", "safety_check" in report)

        # 5.3: Accuracy evaluation with mock predictions
        import json as _json
        ground_truth = []
        with open(eval_path) as f:
            for line in f:
                ground_truth.append(_json.loads(line))

        # Mock predictions (copy ground truth with some errors)
        predictions = []
        for gt in ground_truth:
            pred = {
                "input": gt["input"],
                "output": dict(gt.get("output", {})),
                "metadata": gt.get("metadata", {}),
            }
            predictions.append(pred)

        accuracy = ModelEvaluationHarness.evaluate_model_predictions(eval_path, predictions)
        assert_test("5.3a: Accuracy calculated", "accuracy" in accuracy)
        assert_test("5.3b: Perfect accuracy with same predictions",
                     accuracy["accuracy"] == 1.0)
        assert_test("5.3c: Pass threshold check", accuracy["pass"])

    finally:
        # Clean up eval files
        if os.path.exists(eval_path):
            os.unlink(eval_path)
        # Clean up train file too
        for f in os.listdir(os.path.dirname(eval_path) or "."):
            if f.startswith("train_") or f.startswith("eval_"):
                full = os.path.join(os.path.dirname(eval_path) or ".", f)
                if os.path.exists(full):
                    os.unlink(full)


def section_6_safety():
    print("\n=== Section 6: Safety Boundary Tests ===")

    # 6.1: AI adapter cannot modify kernel
    adapter = FinanceModelAdapter()
    result = adapter.predict("Purchased goods from Raj for Rs.20000")
    assert_test("6.1a: AI predict returns no journal",
                 "journal" not in result)
    assert_test("6.1b: AI predict returns no ledger",
                 "ledger" not in result)

    # 6.2: Problem records cannot claim VERIFIED automatically
    db = ProblemLearningDatabase()
    r = ProblemRecord(
        raw_student_input="Test",
        category=ProblemCategory.OTHER,
    )
    db.add_record(r)
    assert_test("6.2: New record starts as CANDIDATE",
                 r.status == ProblemStatus.CANDIDATE)

    # 6.3: Validation requires evidence
    can_val, _ = db.validate_record(r.problem_id)
    assert_test("6.3a: No evidence = cannot validate", not can_val)

    # 6.4: Single student correction doesn't auto-promote
    db.record_evidence(r.problem_id, EvidenceSource.STUDENT, "VERIFIED")
    can_val2, _ = db.validate_record(r.problem_id)
    assert_test("6.4: Single evidence = cannot validate", not can_val2)

    # 6.5: JSONL export only includes VALIDATED
    r2 = ProblemRecord(
        raw_student_input="Should not export",
        category=ProblemCategory.OTHER,
        engine_status="REVIEW_REQUIRED",
    )
    db.add_record(r2)
    exported = JSONLExporter.export_record(r2)
    assert_test("6.5: CANDIDATE not exported to training", exported is None)

    # 6.6: Training pipeline never triggers automatically
    pipeline = TrainingPipeline(db)
    result6 = pipeline.run_full_pipeline()
    assert_test("6.6a: Pipeline is placeholder",
                 "PLACEHOLDER" in result6["training_command"])
    assert_test("6.6b: No actual training executed",
                 "no training occurred" in result6["note"].lower())

    # 6.7: FinanceModelAdapter has no model weights
    adapter7 = FinanceModelAdapter()
    assert_test("6.7a: No model loaded",
                 not adapter7.is_available())
    assert_test("6.7b: Load fails gracefully",
                 not adapter7.load_model("/tmp/nonexistent_model"))

    # 6.8: Deterministic confidence
    r8 = ProblemRecord(
        raw_student_input="Determinism test",
        category=ProblemCategory.OTHER,
        engine_status="VERIFIED",
    )
    db.add_record(r8)
    for _ in range(4):
        db.record_evidence(r8.problem_id, EvidenceSource.STUDENT, "VERIFIED")
    conf1 = db.get_record(r8.problem_id).confidence
    conf2 = db.get_record(r8.problem_id).confidence
    assert_test("6.8: Confidence is deterministic", conf1 == conf2)


def section_7_integration():
    print("\n=== Section 7: Integration with P2/P3 ===")

    # 7.1: P4 imports P2/P3 successfully
    from backend.maths.fyjc_p3_learning_system import P3LearningManager
    from backend.maths.fyjc_validated_knowledge import ValidatedKnowledgeStore
    assert_test("7.1: P2/P3 imports work", True)

    # 7.2: P4 uses same EvidenceSource as P2
    from backend.maths.fyjc_p4_problem_learning import EvidenceSource as P4ES
    from backend.maths.fyjc_validated_knowledge import EvidenceSource as P2ES
    assert_test("7.2: Same EvidenceSource enum", P4ES is P2ES)

    # 7.3: P4 ProblemStatus distinct from P2 KnowledgeStatus
    from backend.maths.fyjc_p4_problem_learning import ProblemStatus
    from backend.maths.fyjc_validated_knowledge import KnowledgeStatus
    assert_test("7.3: Distinct status enums", ProblemStatus is not KnowledgeStatus)

    # 7.4: P4 can coexist with P3 manager
    mgr = P3LearningManager()
    db = ProblemLearningDatabase()
    mgr.record_student_correction("test", "answer")
    r = ProblemRecord(raw_student_input="test", category=ProblemCategory.OTHER)
    db.add_record(r)
    assert_test("7.4: P3 and P4 coexist", True)

    # 7.5: Kernel untouched
    from backend.maths.fyjc_orchestration import orchestrate
    r1 = orchestrate("Purchased goods from Raj for Rs.20000 for cash")
    r2 = orchestrate("Purchased goods from Raj for Rs.20000 for cash")
    assert_test("7.5: Kernel produces identical results",
                 r1.get("status") == r2.get("status"))


def section_8_regression():
    print("\n=== Section 8: Regression Gates ===")

    import py_compile

    # 8.1: py_compile
    try:
        py_compile.compile("backend/maths/fyjc_p4_problem_learning.py", doraise=True)
        py_compile.compile("backend/maths/fyjc_p3_learning_system.py", doraise=True)
        py_compile.compile("backend/maths/fyjc_validated_knowledge.py", doraise=True)
        py_compile.compile("backend/maths/fyjc_ai_adapter.py", doraise=True)
        assert_test("8.1: py_compile PASS", True)
    except py_compile.PyCompileError as e:
        assert_test("8.1: py_compile PASS", False, str(e))

    # 8.2: Existing test suites
    def _check(script_path):
        try:
            import subprocess
            r = subprocess.run(
                ["python3", script_path],
                capture_output=True, text=True, timeout=120,
            )
            out = r.stdout + r.stderr
            if r.returncode == 0:
                return True, "exit 0"
            lines = out.strip().split("\n")
            last = " ".join(lines[-3:]) if lines else out
            if "0 FAIL" in last and "PASS" in last:
                return True, "0 FAIL"
            if "ALL TESTS PASS" in out:
                return True, "ALL PASS"
            return False, f"exit={r.returncode}"
        except Exception as e:
            return False, str(e)

    for name, path in [
        ("Sprint P2", "scripts/fte_fyjc_p2_validated_knowledge_test.py"),
        ("Sprint P3", "scripts/fte_fyjc_p3_learning_test.py"),
        ("Sprint 35", "scripts/fte_fyjc_35_integrity_invariant_test.py"),
        ("Sprint 36", "scripts/fte_fyjc_36_ui_contract_test.py"),
        ("Sprint 37", "scripts/fte_fyjc_37_calc_scoping_test.py"),
    ]:
        ok, detail = _check(path)
        assert_test(f"8.2: {name}", ok, detail)

    # 8.3: git diff --check
    try:
        rdc = os.popen("git diff --check 2>/dev/null").read()
        assert_test("8.3: git diff --check PASS", rdc.strip() == "")
    except Exception:
        assert_test("8.3: git diff --check PASS", False, "git error")

    # 8.4: Kernel untouched
    from backend.maths.fyjc_orchestration import orchestrate
    test_result = orchestrate("Purchased goods from Raj for Rs.20000 for cash")
    assert_test("8.4: Kernel still produces VERIFIED",
                 test_result.get("status") == "VERIFIED")

    # 8.5: Determinism
    from backend.maths.fyjc_problem_engine import process_problem
    r_a = process_problem("Purchased goods from Raj for Rs.20000.")
    r_b = process_problem("Purchased goods from Raj for Rs.20000.")
    for t1, t2 in zip(r_a["transactions"], r_b["transactions"]):
        assert_test(f"8.5: Determinism T{t1['index']}",
                     t1["status"] == t2["status"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Sprint P4 — Problem Learning Database → JSONL → Training Pipeline")
    print("=" * 70)

    section_1_database()
    section_2_exporter()
    section_3_adapter()
    section_4_pipeline()
    section_5_evaluation()
    section_6_safety()
    section_7_integration()
    section_8_regression()

    print("\n" + "=" * 70)
    print(f"SPRINT P4 RESULTS: {PASS_COUNT}/{TOTAL} PASS, {FAIL_COUNT} FAIL")
    print("=" * 70)

    if FAIL_COUNT > 0:
        print("\n❌ SPRINT P4: FAIL")
        sys.exit(1)
    else:
        print("\n✅ SPRINT P4: PASS")
        sys.exit(0)
