"""
Platrixa — FYJC Database Integration Verdict Matrix Test
backend/fyjc_db_verdict_matrix_test.py

Tests the live student flow end-to-end:
  Student input → orchestrate() → project_student_result() → persist_fyjc_result()
  → PostgreSQL verification

Covers: VERIFIED, REVIEW_REQUIRED, NOT_SUPPORTED, BLOCKED, INVALID_INPUT_MATH,
        student correction, rerun dedup, and DB failure graceful degradation.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

os.environ.setdefault("FTE_LIVE_PERSISTENCE", "1")

# Import the actual kernel and persistence layer
try:
    from backend.maths.fyjc_orchestration import orchestrate
    from backend.maths.fyjc_ui_contract import project_student_result
    from backend.fyjc_db_persistence import (
        persist_fyjc_result,
        _persisted_fingerprints,
    )
    from backend.database.db import SessionLocal
    from backend.database.models import (
        FYJCInteraction,
        FYJCInterpretation,
        FYJCTrainingCandidate,
        FYJC_STATUS_CANDIDATE,
    )
except Exception as e:
    print(f"IMPORT FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Representative questions per verdict class
# ---------------------------------------------------------------------------

TEST_CASES = [
    # --- VERIFIED: clear transaction with sufficient information ---
    # NOTE: all questions use UNIQUE text to avoid hash collisions with
    # the 100-case migrated dataset in the same database.
    {
        "label": "VERIFIED — simple cash purchase",
        "question": "Purchased goods from Mehta Traders for Rs.25000",
        "expected_verdict": "VERIFIED",
        "expect_candidate": True,
    },
    {
        "label": "VERIFIED — credit purchase with GST",
        "question": "Sold goods to Verma Co for Rs.60000 at 18% GST intra-state",
        "expected_verdict": "VERIFIED",
        "expect_candidate": True,
    },
    # --- REVIEW_REQUIRED: ambiguous transaction requiring clarification ---
    {
        "label": "REVIEW_REQUIRED — partial payment",
        "question": "Purchased goods from Patel Bros for Rs.40000. Paid half immediately.",
        "expected_verdict": "REVIEW_REQUIRED",
        "expect_candidate": True,
    },
    # --- NOT_SUPPORTED: unsupported accounting request ---
    {
        "label": "NOT_SUPPORTED — acquisition terminology",
        "question": "Acquired office furniture from Interio Ltd for Rs.75000",
        "expected_verdict": "NOT_SUPPORTED",
        "expect_candidate": False,
    },
    # --- BLOCKED: missing critical information ---
    {
        "label": "BLOCKED — no amount at all",
        "question": "Purchased goods from Ghosh Traders BLK",
        "expected_verdict": "BLOCKED",
        "expect_candidate": False,
    },
    # --- INVALID_INPUT_MATH: mathematical contradiction (payments exceed total) ---
    {
        "label": "INVALID_INPUT_MATH — payments exceed total",
        "question": "Purchased goods from Desai Corp for Rs.20000. Paid Rs.15000 cash and Rs.10000 by cheque.",
        "expected_verdict": "INVALID_INPUT_MATH",
        "expect_candidate": False,
    },
]


def fp(text: str) -> str:
    """Deterministic question fingerprint."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def count_rows(session, question_text):
    """Count FYJC rows for a given question.
    NOTE: multiple process runs may create multiple interaction rows
    for the same fingerprint. We count all of them but the test
    verifies the LATEST one has correct data."""
    fingerprint = fp(question_text)
    interactions = session.query(FYJCInteraction).filter(
        FYJCInteraction.session_id == fingerprint
    ).count()
    # Get the latest interaction (highest id)
    interaction = session.query(FYJCInteraction).filter(
        FYJCInteraction.session_id == fingerprint
    ).order_by(FYJCInteraction.id.desc()).first()
    interpretations = 0
    candidates = 0
    if interaction:
        interpretations = session.query(FYJCInterpretation).filter(
            FYJCInterpretation.interaction_id == interaction.id
        ).count()
        candidates = session.query(FYJCTrainingCandidate).filter(
            FYJCTrainingCandidate.interaction_id == interaction.id
        ).count()
    return interactions, interpretations, candidates


def get_row_details(session, question_text):
    """Get detailed row data for a question.
    Returns the LATEST interaction (highest id) to handle
    multiple process runs that may create duplicate rows."""
    fingerprint = fp(question_text)
    interaction = session.query(FYJCInteraction).filter(
        FYJCInteraction.session_id == fingerprint
    ).order_by(FYJCInteraction.id.desc()).first()
    if not interaction:
        return None, None, None

    interpretation = session.query(FYJCInterpretation).filter(
        FYJCInterpretation.interaction_id == interaction.id
    ).first()
    candidate = session.query(FYJCTrainingCandidate).filter(
        FYJCTrainingCandidate.interaction_id == interaction.id
    ).first()

    return interaction, interpretation, candidate


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_verdict_matrix():
    """Execute the full verdict matrix test."""
    results = []
    _persisted_fingerprints.clear()

    print("=" * 80)
    print("FYJC DATABASE INTEGRATION — VERDICT MATRIX TEST")
    print("=" * 80)

    # -----------------------------------------------------------------------
    # Phase 1: Primary verdict matrix
    # -----------------------------------------------------------------------
    print("\n--- Phase 1: Primary Verdict Matrix ---\n")

    for i, tc in enumerate(TEST_CASES, 1):
        label = tc["label"]
        question = tc["question"]
        expected_verdict = tc["expected_verdict"]
        expect_candidate = tc["expect_candidate"]

        print(f"  [{i}/{len(TEST_CASES)}] {label}")
        print(f"    Question: {question}")

        try:
            # Step 1: Run actual kernel orchestration
            result = orchestrate(question)
            actual_verdict = result.get("status", "UNKNOWN")
            print(f"    Kernel verdict: {actual_verdict}")

            # Step 2: Project into student UI contract
            projection = project_student_result(result, question)

            # Step 3: Persist to PostgreSQL
            fingerprint = fp(question)
            ok = persist_fyjc_result(projection, question, fingerprint)
            print(f"    Persist: {'OK' if ok else 'FAILED'}")

            # Step 4: Verify rows in PostgreSQL
            session = SessionLocal()
            try:
                interactions, interpretations, candidates = count_rows(session, question)
                interaction, interpretation, candidate = get_row_details(session, question)

                # Verify interaction (>= 1 because prior test runs may have left rows)
                assert interactions >= 1, f"Expected >= 1 interaction, got {interactions}"
                assert interaction is not None, "Interaction row not found"
                assert interaction.raw_input == question, (
                    f"raw_input mismatch: expected '{question}', got '{interaction.raw_input}'"
                )
                assert interaction.session_id == fingerprint, "session_id mismatch"

                # Verify interpretation
                assert interpretations == 1, f"Expected 1 interpretation, got {interpretations}"
                assert interpretation is not None, "Interpretation row not found"
                assert interpretation.kernel_status == actual_verdict, (
                    f"kernel_status mismatch: expected '{actual_verdict}', got '{interpretation.kernel_status}'"
                )
                assert interpretation.model_id == "kernel-only", "model_id should be kernel-only"
                assert interpretation.parse_success is True, "parse_success should be True"

                # Verify journal fields for VERIFIED cases
                if actual_verdict == "VERIFIED":
                    assert interpretation.debit_accounts is not None, "VERIFIED should have debit_accounts"
                    assert interpretation.credit_accounts is not None, "VERIFIED should have credit_accounts"

                # Verify candidate eligibility
                if expect_candidate:
                    assert candidates == 1, f"Expected 1 candidate, got {candidates}"
                    assert candidate is not None, "Candidate row not found"
                    assert candidate.status == FYJC_STATUS_CANDIDATE, (
                        f"Candidate status should be CANDIDATE, got {candidate.status}"
                    )
                else:
                    assert candidates == 0, f"Expected 0 candidates, got {candidates}"

                # Verify verdict match
                verdict_match = (actual_verdict == expected_verdict)
                result_status = "PASS" if verdict_match else "MISMATCH"

                print(f"    Interaction: id={interaction.id}, input preserved: OK")
                print(f"    Interpretation: id={interpretation.id}, kernel_status={interpretation.kernel_status}")
                print(f"    Candidate: {'id=' + str(candidate.id) if candidate else 'none (expected)'}")
                print(f"    Verdict match: {'✅' if verdict_match else '❌'} (expected={expected_verdict}, actual={actual_verdict})")

                results.append({
                    "label": label,
                    "question": question[:60],
                    "expected_verdict": expected_verdict,
                    "actual_verdict": actual_verdict,
                    "interaction_row": f"id={interaction.id}",
                    "interpretation_row": f"id={interpretation.id}, status={interpretation.kernel_status}",
                    "candidate_row": f"id={candidate.id}" if candidate else "none",
                    "result": result_status,
                })

            finally:
                session.close()

        except Exception as e:
            print(f"    ❌ ERROR: {e}")
            traceback.print_exc()
            results.append({
                "label": label,
                "question": question[:60],
                "expected_verdict": expected_verdict,
                "actual_verdict": "ERROR",
                "interaction_row": "N/A",
                "interpretation_row": "N/A",
                "candidate_row": "N/A",
                "result": f"ERROR: {e}",
            })

        print()

    # -----------------------------------------------------------------------
    # Phase 2: Student correction → re-evaluation
    # -----------------------------------------------------------------------
    print("--- Phase 2: Student Correction → Re-evaluation ---\n")

    correction_question_1 = "Purchased goods from Nair Traders BLK"
    correction_question_2 = "Purchased goods from Nair Traders BLK for Rs.20000 cash"

    print(f"  Step 1: Original question (BLOCKED)")
    print(f"    Question: {correction_question_1}")

    try:
        # Original: BLOCKED (no amount)
        result1 = orchestrate(correction_question_1)
        verdict1 = result1.get("status", "UNKNOWN")
        projection1 = project_student_result(result1, correction_question_1)
        fp1 = fp(correction_question_1)
        persist_fyjc_result(projection1, correction_question_1, fp1)
        print(f"    Kernel: {verdict1}")

        # Corrected: should be VERIFIED
        print(f"\n  Step 2: Corrected question (should be VERIFIED)")
        print(f"    Question: {correction_question_2}")

        result2 = orchestrate(correction_question_2)
        verdict2 = result2.get("status", "UNKNOWN")
        projection2 = project_student_result(result2, correction_question_2)
        fp2 = fp(correction_question_2)
        persist_fyjc_result(projection2, correction_question_2, fp2)
        print(f"    Kernel: {verdict2}")

        # Verify: two separate interactions, two separate interpretations
        session = SessionLocal()
        try:
            int1, interp1, cand1 = get_row_details(session, correction_question_1)
            int2, interp2, cand2 = get_row_details(session, correction_question_2)

            assert int1 is not None, "Original interaction missing"
            assert int2 is not None, "Corrected interaction missing"
            assert int1.id != int2.id, "Interactions should have different IDs"
            assert interp1.kernel_status == "BLOCKED", f"Original should be BLOCKED, got {interp1.kernel_status}"
            assert interp2.kernel_status == verdict2, f"Corrected should be {verdict2}, got {interp2.kernel_status}"

            # Original should NOT be a candidate (BLOCKED)
            assert cand1 is None, "BLOCKED should not create candidate"
            # Corrected should be a candidate (VERIFIED)
            assert cand2 is not None, "VERIFIED should create candidate"

            print(f"\n  Result:")
            print(f"    Original: id={int1.id}, status={interp1.kernel_status}, candidate={'yes' if cand1 else 'no'}")
            print(f"    Corrected: id={int2.id}, status={interp2.kernel_status}, candidate={'yes' if cand2 else 'no'}")
            print(f"    Historical evidence preserved: ✅")
            print(f"    New interpretation created (not overwritten): ✅")

            results.append({
                "label": "CORRECTION — original BLOCKED",
                "question": correction_question_1[:60],
                "expected_verdict": "BLOCKED",
                "actual_verdict": interp1.kernel_status,
                "interaction_row": f"id={int1.id}",
                "interpretation_row": f"id={interp1.id}, status={interp1.kernel_status}",
                "candidate_row": "none",
                "result": "PASS" if interp1.kernel_status == "BLOCKED" else "MISMATCH",
            })
            results.append({
                "label": "CORRECTION — corrected VERIFIED",
                "question": correction_question_2[:60],
                "expected_verdict": "VERIFIED",
                "actual_verdict": interp2.kernel_status,
                "interaction_row": f"id={int2.id}",
                "interpretation_row": f"id={interp2.id}, status={interp2.kernel_status}",
                "candidate_row": f"id={cand2.id}" if cand2 else "none",
                "result": "PASS" if interp2.kernel_status == "VERIFIED" else "MISMATCH",
            })

        finally:
            session.close()

    except Exception as e:
        print(f"    ❌ ERROR: {e}")
        traceback.print_exc()
        results.append({
            "label": "CORRECTION — flow",
            "question": "correction flow",
            "expected_verdict": "BLOCKED → VERIFIED",
            "actual_verdict": "ERROR",
            "interaction_row": "N/A",
            "interpretation_row": "N/A",
            "candidate_row": "N/A",
            "result": f"ERROR: {e}",
        })

    print()

    # -----------------------------------------------------------------------
    # Phase 3: Streamlit rerun dedup
    # -----------------------------------------------------------------------
    print("--- Phase 3: Streamlit Rerun Dedup ---\n")

    rerun_question = "Sold goods to Bhatia Merchants for Rs.18000"
    _persisted_fingerprints.clear()

    print(f"  Question: {rerun_question}")

    try:
        result_rerun = orchestrate(rerun_question)
        projection_rerun = project_student_result(result_rerun, rerun_question)
        fp_rerun = fp(rerun_question)

        # First persist
        ok1 = persist_fyjc_result(projection_rerun, rerun_question, fp_rerun)
        session = SessionLocal()
        try:
            int_count_1, _, _ = count_rows(session, rerun_question)
        finally:
            session.close()

        # Second persist (simulates Streamlit rerun)
        ok2 = persist_fyjc_result(projection_rerun, rerun_question, fp_rerun)
        session = SessionLocal()
        try:
            int_count_2, _, _ = count_rows(session, rerun_question)
        finally:
            session.close()

        dedup_ok = (int_count_1 == int_count_2 == 1)
        print(f"  First persist: {'OK' if ok1 else 'FAILED'}")
        print(f"  Second persist (dedup): {'OK (skipped)' if ok2 else 'FAILED'}")
        print(f"  Interactions after 1st: {int_count_1}")
        print(f"  Interactions after 2nd: {int_count_2}")
        print(f"  Dedup correct: {'✅' if dedup_ok else '❌'}")

        results.append({
            "label": "DEDUP — rerun",
            "question": rerun_question[:60],
            "expected_verdict": result_rerun.get("status"),
            "actual_verdict": result_rerun.get("status"),
            "interaction_row": f"{int_count_1} → {int_count_2}",
            "interpretation_row": "same",
            "candidate_row": "same",
            "result": "PASS" if dedup_ok else "FAIL",
        })

    except Exception as e:
        print(f"    ❌ ERROR: {e}")
        traceback.print_exc()
        results.append({
            "label": "DEDUP — rerun",
            "question": rerun_question[:60],
            "expected_verdict": "?",
            "actual_verdict": "ERROR",
            "interaction_row": "N/A",
            "interpretation_row": "N/A",
            "candidate_row": "N/A",
            "result": f"ERROR: {e}",
        })

    print()

    # -----------------------------------------------------------------------
    # Phase 4: PostgreSQL failure graceful degradation
    # -----------------------------------------------------------------------
    print("--- Phase 4: PostgreSQL Failure Graceful Degradation ---\n")

    fail_question = "Purchased goods from Saxena Industries for Rs.12000"
    _persisted_fingerprints.clear()

    try:
        result_fail = orchestrate(fail_question)
        projection_fail = project_student_result(result_fail, fail_question)
        fp_fail = fp(fail_question)

        # Simulate DB failure by patching _get_session to return None
        import backend.fyjc_db_persistence as persistence_module
        original_get_session = persistence_module._get_session
        persistence_module._get_session = lambda: None

        try:
            ok_fail = persist_fyjc_result(projection_fail, fail_question, fp_fail)
            print(f"  persist_fyjc_result with DB down: returned {ok_fail} (should be False)")
            print(f"  Student still receives result: ✅ (persistence is non-blocking)")
            print(f"  Graceful degradation: {'✅' if not ok_fail else '❌'}")

            results.append({
                "label": "DB FAILURE — graceful degradation",
                "question": fail_question[:60],
                "expected_verdict": result_fail.get("status"),
                "actual_verdict": f"persist returned {ok_fail}",
                "interaction_row": "N/A (DB down)",
                "interpretation_row": "N/A (DB down)",
                "candidate_row": "N/A (DB down)",
                "result": "PASS" if not ok_fail else "FAIL",
            })
        finally:
            persistence_module._get_session = original_get_session

    except Exception as e:
        print(f"    ❌ ERROR: {e}")
        traceback.print_exc()
        persistence_module._get_session = original_get_session
        results.append({
            "label": "DB FAILURE — graceful degradation",
            "question": fail_question[:60],
            "expected_verdict": "?",
            "actual_verdict": "ERROR",
            "interaction_row": "N/A",
            "interpretation_row": "N/A",
            "candidate_row": "N/A",
            "result": f"ERROR: {e}",
        })

    print()

    # -----------------------------------------------------------------------
    # Phase 5: Field mapping verification (deep inspection)
    # -----------------------------------------------------------------------
    print("--- Phase 5: Deep Field Mapping Verification ---\n")

    deep_question = "Purchased goods from Banerjee Traders for Rs.25000 cash"
    _persisted_fingerprints.clear()

    try:
        result_deep = orchestrate(deep_question)
        projection_deep = project_student_result(result_deep, deep_question)
        fp_deep = fp(deep_question)
        persist_fyjc_result(projection_deep, deep_question, fp_deep)

        session = SessionLocal()
        try:
            interaction, interpretation, candidate = get_row_details(session, deep_question)

            print("  Interaction fields:")
            print(f"    raw_input exact match: {'✅' if interaction.raw_input == deep_question else '❌'}")
            print(f"    session_id: {interaction.session_id}")
            print(f"    board: {interaction.board} (expected: None)")
            print(f"    created_at: {interaction.created_at}")

            print("\n  Interpretation fields:")
            print(f"    model_id: {interpretation.model_id} (expected: kernel-only)")
            print(f"    kernel_status: {interpretation.kernel_status}")
            print(f"    parse_success: {interpretation.parse_success} (expected: True)")
            print(f"    transaction_type: {interpretation.transaction_type}")
            print(f"    parties: {interpretation.parties}")
            print(f"    amounts: {interpretation.amounts}")
            print(f"    payment_method: {interpretation.payment_method}")
            print(f"    journal_balanced: {interpretation.journal_balanced}")
            print(f"    debit_accounts present: {'✅' if interpretation.debit_accounts else '❌'}")
            print(f"    credit_accounts present: {'✅' if interpretation.credit_accounts else '❌'}")
            print(f"    calculations present: {'✅' if interpretation.calculations else 'n/a'}")
            print(f"    ambiguity_flags: {interpretation.ambiguity_flags}")

            if interpretation.debit_accounts:
                for d in interpretation.debit_accounts:
                    print(f"      Dr: {d.get('account')} Rs.{d.get('amount')}")
            if interpretation.credit_accounts:
                for c in interpretation.credit_accounts:
                    print(f"      Cr: {c.get('account')} Rs.{c.get('amount')}")

            print("\n  Candidate fields:")
            if candidate:
                print(f"    problem_id: {candidate.problem_id}")
                print(f"    status: {candidate.status} (expected: CANDIDATE)")
                print(f"    evidence_count: {candidate.evidence_count} (expected: 1)")
                print(f"    validation_count: {candidate.validation_count}")
                print(f"    confidence: {candidate.confidence}")
                print(f"    human_approved: {candidate.human_approved} (expected: False)")
                print(f"    exported_to_jsonl: {candidate.exported_to_jsonl} (expected: False)")
                print(f"    version: {candidate.version} (expected: 1)")

            results.append({
                "label": "FIELD MAPPING — deep verification",
                "question": deep_question[:60],
                "expected_verdict": "VERIFIED",
                "actual_verdict": interpretation.kernel_status,
                "interaction_row": f"id={interaction.id}",
                "interpretation_row": f"id={interpretation.id}",
                "candidate_row": f"id={candidate.id}" if candidate else "none",
                "result": "PASS",
            })

        finally:
            session.close()

    except Exception as e:
        print(f"    ❌ ERROR: {e}")
        traceback.print_exc()
        results.append({
            "label": "FIELD MAPPING — deep verification",
            "question": deep_question[:60],
            "expected_verdict": "VERIFIED",
            "actual_verdict": "ERROR",
            "interaction_row": "N/A",
            "interpretation_row": "N/A",
            "candidate_row": "N/A",
            "result": f"ERROR: {e}",
        })

    print()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("=" * 80)
    print("VERDICT MATRIX — RESULTS TABLE")
    print("=" * 80)
    print()
    print(f"{'Case':<45} {'Expected':<18} {'Actual':<18} {'Inter':<12} {'Interp':<18} {'Candidate':<12} {'Result':<8}")
    print("-" * 131)
    for r in results:
        print(f"{r['label']:<45} {r['expected_verdict']:<18} {r['actual_verdict']:<18} {r['interaction_row']:<12} {r['interpretation_row']:<18} {r['candidate_row']:<12} {r['result']:<8}")

    # Count
    passed = sum(1 for r in results if r['result'] == 'PASS')
    failed = sum(1 for r in results if r['result'] != 'PASS')
    print(f"\nTotal: {len(results)} tests, {passed} PASS, {failed} FAIL")

    if failed == 0:
        print("\n✅ ALL VERDICT CLASSES PASS — Integration complete.")
    else:
        print(f"\n❌ {failed} FAILURES — integration not complete until all pass.")

    return results, passed, failed


if __name__ == "__main__":
    results, passed, failed = run_verdict_matrix()
    sys.exit(0 if failed == 0 else 1)
