#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-C - FYJC Student UI session & refresh persistence gate
scripts/fte_fyjc_student_session_test.py

Tests the pure session layer (backend.fyjc_student_session) that drives
the FYJC Study / Verify page's refresh/rerun behavior. The layer is
non-Streamlit, so every transition is tested deterministically on a
plain dict:

    rerun/refresh  == reconcile(state) called again on the same mapping
    analyse        == set K_FLOW + K_FLOW_FP for the current question
    verify         == set K_VERDICT + K_VERDICT_FP
    reset          == reset_session(state)

Test matrix (sprint requirements 9 and 10):
    A. Fresh visit -> ENTRY
    B. Enter question -> rerun -> question remains
    C. Analyse -> rerun -> result remains
    D. Student answer -> rerun -> verification remains
    E. Edit question -> rerun -> edited question remains
    F. Change question after result -> old result invalidated
    G. Start over -> everything clears -> ENTRY
    H. Refresh after analysis -> same question + same result
    I. Refresh after verification -> same question + same verification
    J. Refresh after failed analysis -> no fake result
    K. Uploaded-file refresh -> text preserved / binary re-upload asked
    L. Different question cannot display previous result
    M. Multiple reruns -> deterministic

Hard invariants: no stale result, no result/question mismatch, reset
clears, refresh != reset, verification belongs to the current question,
failed analysis never becomes success, no fabricated file persistence,
existing widget keys unchanged.

Exit code 0 = 15I-C PASS.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.fyjc_student_session import (  # noqa: E402
    K_ACCT_FP, K_ACCT_VERIFY, K_ANALYSIS_ERROR, K_CORRECTED, K_DOC_NAME,
    K_DOC_TEXT, K_EDIT, K_FLOW, K_FLOW_FP, K_MANUAL_FACTS,
    K_MANUAL_FACTS_FP, K_MODE, K_PHOTO_UPLOAD, K_DOC_UPLOAD, K_QUESTION,
    K_UPLOAD_KIND, K_VERDICT, K_VERDICT_FP,
    STAGE_EDITING, STAGE_ENTRY, STAGE_INPUT_READY, STAGE_RESULT,
    STAGE_VERIFYING,
    derive_stage,
    effective_question,
    question_fingerprint,
    reconcile,
    reset_session,
    upload_recovery_note,
)

CHECKS = []
FAILURES = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def analyse(state, question):
    """Simulate a completed analysis for `question`."""
    state[K_FLOW] = {"flow": "maths", "status": "VERIFIED",
                     "display": "20.00%"}
    state[K_FLOW_FP] = question_fingerprint(question)


def verify(state, question):
    """Simulate a completed student-answer verification for `question`."""
    state[K_VERDICT] = {"verdict": "CORRECT", "student_display": "20"}
    state[K_VERDICT_FP] = question_fingerprint(question)


# ---------------------------------------------------------------------------
# A - M matrix
# ---------------------------------------------------------------------------


def test_a_fresh_visit():
    state = {}
    stage = reconcile(state)
    check("A.fresh visit -> ENTRY", stage == STAGE_ENTRY, stage)
    check("A.fresh visit has no start-over state",
          not any(state.get(k) for k in (K_QUESTION, K_FLOW, K_VERDICT)), "")


def test_b_question_remains_on_rerun():
    state = {K_QUESTION: "Purchased goods from Rahul on credit for Rs.10,000."}
    reconcile(state)
    q1 = effective_question(state)
    reconcile(state)  # rerun
    check("B.question remains after rerun",
          effective_question(state) == q1, effective_question(state))
    check("B.input-ready stage after question only",
          derive_stage(state) == STAGE_INPUT_READY, derive_stage(state))


def test_c_result_remains_on_rerun():
    state = {K_QUESTION: "Calculate the Profit Margin."}
    reconcile(state)
    analyse(state, effective_question(state))
    reconcile(state)
    check("C.result present after analyse", state.get(K_FLOW) is not None, "")
    reconcile(state)  # rerun
    check("C.result remains after rerun", state.get(K_FLOW) is not None, "")
    check("C.stage is RESULT", derive_stage(state) == STAGE_RESULT,
          derive_stage(state))


def test_d_verification_remains_on_rerun():
    state = {K_QUESTION: "Calculate the Profit Margin."}
    reconcile(state)
    analyse(state, effective_question(state))
    verify(state, effective_question(state))
    reconcile(state)
    reconcile(state)  # rerun
    check("D.verification remains after rerun",
          state.get(K_VERDICT) == {"verdict": "CORRECT",
                                   "student_display": "20"}, str(state.get(K_VERDICT)))
    check("D.stage is VERIFYING", derive_stage(state) == STAGE_VERIFYING,
          derive_stage(state))


def test_e_edit_state_remains_on_rerun():
    state = {K_QUESTION: "Purchased goods for cash Rs.10,000.", K_EDIT: True}
    reconcile(state)
    state["fte_fyjc_question_edit"] = "Purchased goods for cash Rs.12,000."
    reconcile(state)  # rerun - the edited widget text must stay put
    check("E.edit flag remains after rerun", state.get(K_EDIT) is True, "")
    check("E.edited text remains after rerun",
          state.get("fte_fyjc_question_edit")
          == "Purchased goods for cash Rs.12,000.",
          str(state.get("fte_fyjc_question_edit")))
    check("E.stage is EDITING", derive_stage(state) == STAGE_EDITING,
          derive_stage(state))


def test_f_question_change_invalidates_result():
    state = {K_QUESTION: "Q1: Purchased goods from Rahul on credit."}
    reconcile(state)
    analyse(state, effective_question(state))
    reconcile(state)
    check("F.result present for Q1", state.get(K_FLOW) is not None, "")
    state[K_QUESTION] = "Q2: Sold goods to Mohan for cash."
    reconcile(state)  # the entry widget changed the question -> stale result
    check("F.old result invalidated", state.get(K_FLOW) is None,
          str(state.get(K_FLOW)))
    check("F.no stale fingerprint", state.get(K_FLOW_FP) is None, "")
    check("F.back to INPUT_READY",
          derive_stage(state) == STAGE_INPUT_READY, derive_stage(state))


def test_g_start_over_clears_everything():
    state = {
        K_MODE: "✍️ Enter Question",
        K_QUESTION: "Q1",
        K_CORRECTED: "corrected",
        K_DOC_TEXT: "doc text",
        K_DOC_NAME: "q.pdf",
        K_UPLOAD_KIND: "document",
        K_FLOW: {"flow": "maths"},
        K_EDIT: True,
        K_MANUAL_FACTS: {"Equity": "1000"},
        K_VERDICT: {"verdict": "CORRECT"},
        K_ACCT_VERIFY: {"journal": {}},
        K_ANALYSIS_ERROR: {"message": "boom", "fp": "abc"},
        K_FLOW_FP: "a", K_VERDICT_FP: "b", K_ACCT_FP: "c",
        K_MANUAL_FACTS_FP: "d",
        K_PHOTO_UPLOAD: "not-a-real-file",
        K_DOC_UPLOAD: "not-a-real-file",
    }
    reset_session(state)
    leftovers = {k: v for k, v in state.items()
                 if k in (K_MODE, K_QUESTION, K_CORRECTED, K_DOC_TEXT,
                          K_DOC_NAME, K_UPLOAD_KIND, K_FLOW, K_EDIT,
                          K_MANUAL_FACTS, K_VERDICT, K_ACCT_VERIFY,
                          K_ANALYSIS_ERROR, K_FLOW_FP, K_VERDICT_FP, K_ACCT_FP,
                          K_MANUAL_FACTS_FP, K_PHOTO_UPLOAD, K_DOC_UPLOAD)}
    check("G.reset clears every managed key", not leftovers, str(leftovers))
    check("G.reset -> ENTRY", derive_stage(state) == STAGE_ENTRY,
          derive_stage(state))
    check("G.reset clears the input mode", K_MODE not in state, "")


def test_h_refresh_after_analysis():
    state = {K_QUESTION: "Purchased goods from Rahul on credit for Rs.10,000."}
    reconcile(state)
    analyse(state, effective_question(state))
    reconcile(state)
    q_before = effective_question(state)
    flow_before = state.get(K_FLOW)
    fp_before = state.get(K_FLOW_FP)
    reconcile(state)  # refresh: same session, script reruns
    reconcile(state)
    check("H.same question after refresh",
          effective_question(state) == q_before, "")
    check("H.same result after refresh",
          state.get(K_FLOW) is flow_before
          and state.get(K_FLOW_FP) == fp_before, "")
    check("H.stage stays RESULT", derive_stage(state) == STAGE_RESULT,
          derive_stage(state))


def test_i_refresh_after_verification():
    state = {K_QUESTION: "Calculate the Profit Margin."}
    reconcile(state)
    analyse(state, effective_question(state))
    verify(state, effective_question(state))
    reconcile(state)
    verdict_before = state.get(K_VERDICT)
    reconcile(state)  # refresh
    reconcile(state)
    check("I.verification survives refresh",
          state.get(K_VERDICT) == verdict_before, str(state.get(K_VERDICT)))
    check("I.stage stays VERIFYING",
          derive_stage(state) == STAGE_VERIFYING, derive_stage(state))


def test_j_refresh_after_failed_analysis():
    fp = question_fingerprint("Q with a problem")
    state = {
        K_QUESTION: "Q with a problem",
        K_ANALYSIS_ERROR: {"message": "boom", "fp": fp},
    }
    reconcile(state)
    reconcile(state)  # refresh
    check("J.no fake result after failed analysis",
          state.get(K_FLOW) is None, str(state.get(K_FLOW)))
    check("J.error is preserved for the same question",
          state.get(K_ANALYSIS_ERROR) is not None, "")
    check("J.recoverable INPUT_READY",
          derive_stage(state) == STAGE_INPUT_READY, derive_stage(state))
    # question changes -> the error is stale too
    state[K_QUESTION] = "A different question"
    reconcile(state)
    check("J.stale error dropped on question change",
          state.get(K_ANALYSIS_ERROR) is None, "")


def test_k_upload_refresh():
    doc_text = ("Current Assets: Rs.5,00,000\n"
                "Current Liabilities: Rs.2,50,000")
    state = {K_UPLOAD_KIND: "document", K_DOC_NAME: "q.pdf",
             K_DOC_TEXT: doc_text}
    reconcile(state)
    reconcile(state)  # refresh
    check("K.extracted text preserved after refresh",
          effective_question(state) == doc_text,
          effective_question(state)[:60])
    check("K.document note says text is preserved",
          "preserved" in upload_recovery_note(state, "document"), "")

    # Photo-only session: the binary never survives a refresh.
    state = {K_UPLOAD_KIND: "image", K_DOC_NAME: "photo.jpg"}
    reconcile(state)
    check("K.photo-only never fabricates a question",
          effective_question(state) == "", effective_question(state))
    note = upload_recovery_note(state, "image")
    check("K.re-upload is requested for a lost photo", bool(note), note)

    # PDF with no readable text: re-upload or paste is requested.
    state = {K_UPLOAD_KIND: "document", K_DOC_NAME: "scan.pdf"}
    note = upload_recovery_note(state, "document")
    check("K.re-upload requested when only the binary existed",
          bool(note), note)
    check("K.no note for an unrelated mode",
          upload_recovery_note(state, "image") == "", "")


def test_l_different_question_never_shows_old_result():
    state = {K_QUESTION: "Q1"}
    reconcile(state)
    analyse(state, effective_question(state))
    verify(state, effective_question(state))
    state[K_ACCT_VERIFY] = {"journal": {"verdict": "INCORRECT"}}
    state[K_ACCT_FP] = question_fingerprint("Q1")
    state[K_MANUAL_FACTS] = {"Equity": "1000"}
    state[K_MANUAL_FACTS_FP] = question_fingerprint("Q1")
    reconcile(state)
    check("L.all artifacts present for Q1",
          state.get(K_FLOW) is not None
          and state.get(K_VERDICT) is not None
          and state.get(K_ACCT_VERIFY) is not None
          and state.get(K_MANUAL_FACTS) is not None, "")
    state[K_QUESTION] = "Q2 - entirely different question"
    reconcile(state)
    check("L.flow dropped for Q2", state.get(K_FLOW) is None, "")
    check("L.verdict dropped for Q2", state.get(K_VERDICT) is None, "")
    check("L.accounting checks dropped for Q2",
          state.get(K_ACCT_VERIFY) is None, "")
    check("L.manual facts dropped for Q2",
          state.get(K_MANUAL_FACTS) is None, "")
    check("L.stage INPUT_READY for Q2",
          derive_stage(state) == STAGE_INPUT_READY, derive_stage(state))


def test_m_multiple_reruns_deterministic():
    state = {K_QUESTION: "Purchased goods from Rahul on credit for Rs.10,000."}
    reconcile(state)
    analyse(state, effective_question(state))
    verify(state, effective_question(state))
    snapshots = []
    for _ in range(5):
        reconcile(state)
        snapshots.append(dict(state))
    check("M.five reruns produce identical state",
          all(s == snapshots[0] for s in snapshots[1:]), "")


# ---------------------------------------------------------------------------
# Hard invariants
# ---------------------------------------------------------------------------


def test_hard_invariants():
    # fingerprint: stable, deterministic, and question-sensitive (never a
    # timestamp or random id).
    fp1 = question_fingerprint("Purchased goods for cash Rs.10,000.")
    fp2 = question_fingerprint("Purchased goods for cash Rs.10,000.")
    fp3 = question_fingerprint("  purchased goods for cash rs.10,000.  ")
    fp4 = question_fingerprint("Purchased goods for cash Rs.12,000.")
    check("I.fingerprint is deterministic", fp1 == fp2, "")
    check("I.fingerprint is normalisation-stable", fp1 == fp3, "")
    check("I.fingerprint differs across questions", fp1 != fp4, "")
    check("I.fingerprint is 16 hex chars", len(fp1) == 16, len(fp1))

    # refresh != reset: reconcile twice preserves; reset_session clears.
    state = {K_QUESTION: "Q", K_FLOW: {"flow": "maths"},
             K_FLOW_FP: question_fingerprint("Q")}
    reconcile(state)
    reconcile(state)
    check("I.rerun preserves the session", state.get(K_FLOW) is not None, "")
    reset_session(state)
    check("I.reset clears what refresh preserved",
          state.get(K_FLOW) is None and state.get(K_QUESTION) is None, "")

    # verification belongs to the current question.
    state = {K_QUESTION: "Q1"}
    reconcile(state)
    verify(state, "Q1")
    reconcile(state)
    check("I.verification kept for the same question",
          state.get(K_VERDICT) is not None, "")
    state[K_QUESTION] = "Q2"
    reconcile(state)
    check("I.verification invalidated on question change",
          state.get(K_VERDICT) is None, "")

    # reconcile is idempotent.
    state = {K_QUESTION: "Q", K_FLOW: {"flow": "maths"},
             K_FLOW_FP: question_fingerprint("Q"),
             K_VERDICT: {"verdict": "CORRECT"},
             K_VERDICT_FP: question_fingerprint("Q")}
    reconcile(state)
    before = dict(state)
    reconcile(state)
    check("I.reconcile is idempotent", state == before, "")

    # effective-question precedence: correction > typed > document text.
    state = {K_QUESTION: "typed", K_CORRECTED: "corrected",
             K_DOC_TEXT: "document"}
    check("I.correction wins over typed text",
          effective_question(state) == "corrected", effective_question(state))
    state.pop(K_CORRECTED)
    check("I.typed text wins over document text",
          effective_question(state) == "typed", effective_question(state))
    state.pop(K_QUESTION)
    check("I.document text is the fallback",
          effective_question(state) == "document", effective_question(state))

    # no fabricated persistence of unavailable files.
    state = {K_UPLOAD_KIND: "image"}
    reconcile(state)
    check("I.no fabricated question from a lost upload",
          effective_question(state) == "", "")

    # stage precedence: EDITING > VERIFYING > RESULT > INPUT_READY > ENTRY.
    state = {K_QUESTION: "Q", K_FLOW: {"x": 1}, K_VERDICT: {"y": 2},
             K_EDIT: True}
    check("I.EDITING beats VERIFYING", derive_stage(state) == STAGE_EDITING, "")
    state.pop(K_EDIT)
    check("I.VERIFYING beats RESULT",
          derive_stage(state) == STAGE_VERIFYING, derive_stage(state))
    state.pop(K_VERDICT)
    check("I.RESULT stage", derive_stage(state) == STAGE_RESULT, "")
    state.pop(K_FLOW)
    check("I.INPUT_READY stage", derive_stage(state) == STAGE_INPUT_READY, "")
    state.pop(K_QUESTION)
    check("I.ENTRY stage", derive_stage(state) == STAGE_ENTRY, "")


def test_widget_key_contract():
    # The AppTest and Sprint 14 gate pin these widget keys by name.
    check("C.K_MODE key contract", K_MODE == "fte_fyjc_mode", K_MODE)
    check("C.K_QUESTION key contract", K_QUESTION == "fte_fyjc_question",
          K_QUESTION)
    ui_path = os.path.join(os.path.dirname(__file__), "..",
                           "backend", "fyjc_student_ui.py")
    with open(ui_path, encoding="utf-8") as fh:
        ui_src = fh.read()
    for key in ("fte_fyjc_go", "fte_fyjc_reset", "fte_fyjc_verify_answer",
                "fte_fyjc_verify_btn", "fte_fyjc_edit_btn",
                "fte_fyjc_question_edit", "fte_fyjc_reanalyse",
                "fte_fyjc_photo", "fte_fyjc_doc"):
        check(f"C.UI keeps widget key {key}", f'"{key}"' in ui_src, "")


def verdict():
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"RESULT: {passed}/{total} checks passed")
    if FAILURES:
        for f in FAILURES[:50]:
            print(f"  - {f}")
        print("=" * 72)
        print("SPRINT 15I-C FAIL - SESSION PERSISTENCE NOT VERIFIED")
        return 1
    print("=" * 72)
    print("SPRINT 15I-C PASS - STUDENT SESSION & REFRESH PERSISTENCE VERIFIED")
    return 0


def main():
    test_a_fresh_visit()
    test_b_question_remains_on_rerun()
    test_c_result_remains_on_rerun()
    test_d_verification_remains_on_rerun()
    test_e_edit_state_remains_on_rerun()
    test_f_question_change_invalidates_result()
    test_g_start_over_clears_everything()
    test_h_refresh_after_analysis()
    test_i_refresh_after_verification()
    test_j_refresh_after_failed_analysis()
    test_k_upload_refresh()
    test_l_different_question_never_shows_old_result()
    test_m_multiple_reruns_deterministic()
    test_hard_invariants()
    test_widget_key_contract()
    return verdict()


if __name__ == "__main__":
    sys.exit(main())
