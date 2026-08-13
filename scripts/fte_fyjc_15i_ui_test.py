#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-I - Student Practice UI + Teacher Dashboard Integration Gate
scripts/fte_fyjc_15i_ui_test.py

Verifies the Sprint 15I-I UI/integration layer against the REAL 15I-H
engines and the REAL 15I-G bank (Sprint 15I-I spec section 12).

  A. Student flow    - session start, approved selection, question
                       display, correct / incorrect / amount / account /
                       direction / review-required / unsupported answers,
                       retry, next, end, summary, history persistence
  B. Mastery         - correct updates mastery, incorrect updates ledger,
                       REVIEW_REQUIRED and NOT_SUPPORTED never become
                       correct/incorrect, repeated mistakes persist
  C. Teacher         - question/student/mistake filtering, mastery +
                       provenance display data, approved integrity
  D. Safety          - canonical journal cannot be overridden from the UI
                       path, teacher expected journal cannot override
                       FT-E, unapproved content never reaches practice,
                       no LLM path, no silent repair, immutable attempts
  E. UI render smoke - the real Streamlit UI modules render and drive a
                       practice session through AppTest with temp storage

The UI is presentation-only: every check below verifies that the verdict /
mistake / mastery data comes from the 15I-H engines, never from the UI.
Answers in tests are derived from the canonical journal of whichever
approved question the deterministic selection actually returns.
"""

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_question_bank import (  # noqa: E402
    QuestionBank,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_VALIDATING,
)
from backend.maths.fyjc_practice_engine import (  # noqa: E402
    PracticeEngine,
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_REVIEW_REQUIRED,
    OUTCOME_NOT_SUPPORTED,
    SESSION_ACTIVE,
    SESSION_COMPLETED,
    MODE_NORMAL,
)
from backend.maths.fyjc_mistake_ledger import (  # noqa: E402
    MISTAKE_OPEN,
    MISTAKE_IMPROVING,
    MISTAKE_RESOLVED,
    MISTAKE_CATEGORIES,
)
from backend.maths.fyjc_mastery_engine import (  # noqa: E402
    MASTERY_LEARNING,
)

FAIL = []
OK_COUNT = [0]


def check(name, ok, detail=""):
    if ok:
        OK_COUNT[0] += 1
        print(f"OK  [{name}]")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL[{name}] {detail}")


RUN_ID = uuid.uuid4().hex[:10]
TMP = os.path.join(tempfile.gettempdir(), f"fte_15ii_{RUN_ID}")
os.makedirs(TMP, exist_ok=True)


def _tmp(name):
    return os.path.join(TMP, name)


# ---------------------------------------------------------------------------
# Fixture: a real approved Question Bank + deterministic engine
# ---------------------------------------------------------------------------

BANK_QUESTIONS = [
    ("q_credit_sale", "Sold goods to Ram on credit ₹12,000.", 2, "Ch.3 Journal"),
    ("q_cash_sale", "Sold goods for cash Rs.25,000.", 1, "Ch.3 Journal"),
    ("q_credit_purchase", "Purchased goods from Rahul on credit Rs.10,000.", 1, "Ch.3 Journal"),
    ("q_rent", "Paid rent Rs.6,000.", 1, "Ch.3 Journal"),
    ("q_bank_deposit", "Deposited cash into bank Rs.10,000.", 1, "Ch.2 Basic Accounting Terms"),
    ("q_cash_discount", "Purchased goods from Rahul for Rs.10,000, half the "
                        "amount paid immediately with 2% cash discount.", 3, "Ch.3 Journal"),
    ("q_multi", "Started business with cash Rs.1,00,000. Purchased goods for "
                "cash Rs.20,000. Paid rent Rs.5,000.", 3, "Ch.3 Journal"),
    ("q_meena", "Sold goods to Meena for cash Rs.12,000.", 2, "Ch.3 Journal"),
    ("q_machinery", "Bought machinery from Amar on credit Rs.1,50,000.", 2, "Ch.3 Journal"),
]


def make_bank(store_path=None) -> QuestionBank:
    b = QuestionBank(store_path=store_path or _tmp("bank.json"))
    qids = {}
    for key, text, diff, chapter in BANK_QUESTIONS:
        qid = b.create_question(text, source_type="manual",
                                source_reference=key)
        b.compile_question(qid)
        b.validate_question(qid)
        b.approve_question(qid)
        b.set_metadata(qid, {"difficulty": diff, "chapter": chapter})
        qids[key] = qid
    # Rejected content (must never reach a student).
    bad = b.create_question(
        "Prepare Trading and Profit and Loss Account for the year ended "
        "31 March.", source_type="manual")
    b.compile_question(bad)
    b.validate_question(bad)
    qids["q_rejected"] = bad
    assert b.get_question(bad)["status"] == STATUS_REJECTED
    # The bank is a caller-saves store; persist the fixture so a fresh
    # QuestionBank (e.g. the UI's) reads exactly these questions.
    b.save()
    return b, qids


def make_engine(bank, store_path=None):
    return PracticeEngine(bank, store_path or _tmp("store.json"),
                          rng_seed=20260813)


def submit(engine, sid, q, d_accts, d_amts, c_accts, c_amts, raw=""):
    return engine.submit_answer(sid, q, d_accts, d_amts, c_accts, c_amts,
                                raw_response=raw)


# ---------------------------------------------------------------------------
# Canonical-derived answer builders (prove normalization + structure diff)
# ---------------------------------------------------------------------------

_WRONG_ACCOUNTS = ["Sales", "Rent", "Cash", "Ram", "Rahul", "Meena",
                   "Amar", "Bank"]


def _canon_lines(qrec, side):
    return [[acc, amt] for acc, amt in
            ((qrec.get("expected_journal") or {}).get(side) or [])]


def correct_entry(qrec):
    """The verified answer, with case-folded accounts to prove that the
    engine's normalization makes 'cash' == 'Cash'."""
    d = [[str(acc).lower(), amt] for acc, amt in _canon_lines(qrec, "debit")]
    c = [[str(acc).lower(), amt] for acc, amt in _canon_lines(qrec, "credit")]
    return [x[0] for x in d], [x[1] for x in d], \
        [x[0] for x in c], [x[1] for x in c]


def wrong_account_entry(qrec):
    """Same amounts, one debit account swapped for a non-canonical one."""
    d = _canon_lines(qrec, "debit")
    c = _canon_lines(qrec, "credit")
    canon_accounts = {str(a).lower() for a, _ in d + c}
    swap = next(a for a in _WRONG_ACCOUNTS
                if a.lower() not in canon_accounts)
    d = [[swap if i == 0 else acc, amt] for i, (acc, amt) in enumerate(d)]
    return [x[0] for x in d], [x[1] for x in d], \
        [x[0] for x in c], [x[1] for x in c]


def amount_error_entry(qrec):
    """Right accounts, every amount shifted by +1000 (still balanced, so
    the diff resolves to AMOUNT_ERROR, not a balancing error)."""
    d = _canon_lines(qrec, "debit")
    c = _canon_lines(qrec, "credit")
    d = [[acc, amt + 1000] for acc, amt in d]
    c = [[acc, amt + 1000] for acc, amt in c]
    return [x[0] for x in d], [x[1] for x in d], \
        [x[0] for x in c], [x[1] for x in c]


def reversed_entry(qrec):
    """The whole entry with debit/credit sides swapped."""
    d = _canon_lines(qrec, "debit")
    c = _canon_lines(qrec, "credit")
    return [x[0] for x in c], [x[1] for x in c], \
        [x[0] for x in d], [x[1] for x in d]


# ---------------------------------------------------------------------------
# A. Student flow
# ---------------------------------------------------------------------------

def test_a_student_flow():
    bank, qids = make_bank()
    engine = make_engine(bank)
    sid = engine.create_session("stu_a", mode=MODE_NORMAL,
                                question_count=4)
    s = engine.get_session(sid)
    check("A.1 session created", s["status"] == SESSION_ACTIVE
          and s["student_id"] == "stu_a", str(s))

    q1 = engine.select_next(sid)
    q1rec = bank.get_question(q1)
    check("A.2 approved question selected",
          q1rec["status"] == STATUS_APPROVED, q1)
    check("A.3 question displayed (raw_text present)",
          bool(q1rec.get("raw_text")), q1rec.get("raw_text"))

    # Correct answer (case-folded on purpose).
    ok = submit(engine, sid, q1, *correct_entry(q1rec),
                raw="Debit Cash 12000; Credit Ram 12000")
    check("A.4 correct answer", ok["outcome"] == OUTCOME_CORRECT,
          str(ok.get("outcome")))
    check("A.4 correct answer has no mistake",
          ok.get("mistake_id") is None and ok.get("mistake_category") is None,
          str(ok))

    # Retry: a second correct attempt is a NEW attempt, same outcome.
    retry = submit(engine, sid, q1, *correct_entry(q1rec),
                   raw="Debit Cash 12000; Credit Ram 12000")
    check("A.5 retry produces new attempt",
          retry["attempt_id"] != ok["attempt_id"]
          and retry["outcome"] == OUTCOME_CORRECT, "")

    # Next question: with a clean 100% concept, the pool excludes q1.
    q2 = engine.select_next(sid)
    check("A.6 next question is different and approved",
          q2 != q1 and bank.get_question(q2)["status"] == STATUS_APPROVED,
          f"{q2} vs {q1}")

    # Incorrect answer: wrong account selection.
    q2rec = bank.get_question(q2)
    bad = submit(engine, sid, q2, *wrong_account_entry(q2rec),
                 raw="Debit Cash 12000; Credit Sales 12000")
    check("A.7 incorrect answer",
          bad["outcome"] == OUTCOME_INCORRECT
          and bad["mistake_category"] in ("ACCOUNT_SELECTION",
                                          "TRANSACTION_CLASSIFICATION"),
          str(bad.get("outcome")) + " " + str(bad.get("mistake_category")))

    # Amount error: right accounts, wrong amounts (still balanced).
    amt = submit(engine, sid, q2, *amount_error_entry(q2rec),
                 raw="Debit Cash 13000; Credit Ram 13000")
    check("A.8 amount error",
          amt["outcome"] == OUTCOME_INCORRECT
          and amt["mistake_category"] == "AMOUNT_ERROR",
          str(amt.get("mistake_category")))

    # Debit/credit direction error.
    dirn = submit(engine, sid, q2, *reversed_entry(q2rec),
                  raw="Debit Ram 12000; Credit Cash 12000")
    check("A.9 debit/credit direction error",
          dirn["outcome"] == OUTCOME_INCORRECT
          and dirn["mistake_category"] in (
              "DEBIT_CREDIT_DIRECTION", "PARTY_ROLE_ERROR"),
          str(dirn.get("mistake_category")))

    # REVIEW_REQUIRED answer: empty journal with ambiguous text.
    rr = submit(engine, sid, q2, [], [], [], [], raw="")
    check("A.10 review-required answer",
          rr["outcome"] == OUTCOME_REVIEW_REQUIRED,
          str(rr.get("outcome")))

    # NOT_SUPPORTED answer: explicit unsupported hint.
    ns = submit(engine, sid, q2, [], [], [], [],
                raw="I don't know this")
    check("A.11 unsupported answer",
          ns["outcome"] == OUTCOME_NOT_SUPPORTED,
          str(ns.get("outcome")))

    # End session.
    done = engine.complete_session(sid)
    check("A.12 end session",
          done["status"] == SESSION_COMPLETED and done["ended_at"], "")

    # Session summary data (as rendered from get_session + dashboard).
    dash = engine.student_dashboard("stu_a")
    check("A.13 session summary metrics",
          dash["total_attempts"] >= 7 and dash["correct"] >= 2
          and dash["review_required"] >= 1 and dash["not_supported"] >= 1,
          str(dash))

    # History persistence: reload a fresh engine from the same store file.
    engine2 = make_engine(bank, store_path=_tmp("store.json"))
    s2 = engine2.get_session(sid)
    check("A.14 history persists across reload",
          s2["completed_count"] == done["completed_count"]
          and len(engine2.store.attempts) == len(engine.store.attempts),
          f"{s2['completed_count']} / {done['completed_count']}")
    check("A.14 raw responses preserved verbatim",
          engine2.store.attempts[retry["attempt_id"]]["raw_response"]
          == "Debit Cash 12000; Credit Ram 12000", "")


# ---------------------------------------------------------------------------
# B. Mastery
# ---------------------------------------------------------------------------

def test_b_mastery():
    bank, qids = make_bank(_tmp("bank_b.json"))
    engine = make_engine(bank, _tmp("store_b.json"))
    sid = engine.create_session("stu_b", mode=MODE_NORMAL)
    q1 = engine.select_next(sid)
    q1rec = bank.get_question(q1)
    concept = q1rec["concept_key"]

    # Correct attempt -> mastery evidence grows (no mistake record).
    submit(engine, sid, q1, *correct_entry(q1rec),
           raw="Debit Cash 12000; Credit Ram 12000")
    rec = engine.mastery.get("stu_b", concept)
    check("B.1 correct updates mastery",
          rec["attempts"] == 1 and rec["correct"] == 1
          and rec["incorrect"] == 0, str(rec))

    # Incorrect attempt -> mistake ledger record created.
    submit(engine, sid, q1, *wrong_account_entry(q1rec),
           raw="Debit Cash 12000; Credit Sales 12000")
    rec = engine.mastery.get("stu_b", concept)
    check("B.2 incorrect updates ledger + mastery",
          rec["incorrect"] == 1 and rec["mistake_count"] >= 1
          and len(engine.ledger.records()) >= 1, str(rec))

    # REVIEW_REQUIRED must NOT become correct/incorrect or change state.
    before = engine.mastery.get("stu_b", concept)
    submit(engine, sid, q1, [], [], [], [], raw="")
    after = engine.mastery.get("stu_b", concept)
    check("B.3 REVIEW_REQUIRED is mastery-neutral",
          after["correct"] == before["correct"]
          and after["incorrect"] == before["incorrect"]
          and after["mastery_state"] == before["mastery_state"]
          and after["review_required"] == before["review_required"] + 1,
          f"{before} -> {after}")

    # NOT_SUPPORTED must NOT become a mastery failure.
    before = engine.mastery.get("stu_b", concept)
    submit(engine, sid, q1, [], [], [], [], raw="not sure")
    after = engine.mastery.get("stu_b", concept)
    check("B.4 NOT_SUPPORTED is mastery-neutral",
          after["correct"] == before["correct"]
          and after["incorrect"] == before["incorrect"]
          and after["mastery_state"] == before["mastery_state"]
          and after["unsupported"] == before["unsupported"] + 1,
          f"{before} -> {after}")

    # Repeated mistakes persist (occurrence counting).
    for _ in range(3):
        submit(engine, sid, q1, *wrong_account_entry(q1rec),
               raw="Debit Cash 12000; Credit Sales 12000")
    open_mistakes = engine.ledger.open_mistakes(student_id="stu_b")
    top = sorted(open_mistakes, key=lambda m: -m["occurrence_count"])[0]
    check("B.5 repeated mistakes persist",
          top["occurrence_count"] >= 4 and top["status"] == MISTAKE_OPEN,
          str(top))

    # Correct streak resolves the mistake (ledger, not the UI).
    for _ in range(2):
        submit(engine, sid, q1, *correct_entry(q1rec),
               raw="Debit Cash 12000; Credit Ram 12000")
    rec_after = engine.ledger.records().get(top["mistake_id"]) or {}
    check("B.5 correct streak resolves mistake",
          rec_after.get("status") in (MISTAKE_RESOLVED, MISTAKE_IMPROVING),
          str(rec_after.get("status")))


# ---------------------------------------------------------------------------
# C. Teacher dashboard data
# ---------------------------------------------------------------------------

def test_c_teacher():
    bank, qids = make_bank(_tmp("bank_c.json"))
    engine = make_engine(bank, _tmp("store_c.json"))
    sid = engine.create_session("stu_c", mode=MODE_NORMAL)
    q1 = engine.select_next(sid)
    q1rec = bank.get_question(q1)
    submit(engine, sid, q1, *correct_entry(q1rec),
           raw="Debit Cash 12000; Credit Ram 12000")
    submit(engine, sid, q1, *wrong_account_entry(q1rec),
           raw="Debit Cash 12000; Credit Sales 12000")

    # C.21 question filtering (bank APIs used by the dashboard).
    by_ch = bank.filter_by_chapter("Ch.3 Journal")
    check("C.21 chapter filter", len(by_ch) >= 7, str(len(by_ch)))
    approved = bank.list_approved()
    check("C.21 approved-only filter",
          all(q["status"] == STATUS_APPROVED for q in approved), "")

    # C.22 student filtering (dashboard aggregates per student).
    dash = engine.student_dashboard("stu_c")
    dash_other = engine.student_dashboard("nobody")
    check("C.22 student filtering",
          dash["total_attempts"] == 2 and dash_other["total_attempts"] == 0,
          str(dash["total_attempts"]))

    # C.23 mistake filtering (ledger records filterable by fields shown).
    records = list(engine.ledger.records().values())
    check("C.23 mistake records carry filterable fields",
          len(records) == 1
          and records[0]["student_id"] == "stu_c"
          and records[0]["mistake_category"] in MISTAKE_CATEGORIES,
          str(records))

    # C.24 mastery display data.
    summary = engine.mastery.summary("stu_c")
    check("C.24 mastery display",
          summary["attempts"] == 2 and summary["concepts_seen"] >= 1,
          str(summary))

    # C.25 provenance display.
    qrec = bank.get_question(q1)
    src = qrec.get("source") or {}
    check("C.25 provenance present",
          src.get("source_type") == "manual"
          and src.get("source_reference"),
          str(src))

    # C.26 approved-question integrity (canonical journal + verification).
    check("C.26 canonical journal present on approved",
          bool(qrec.get("expected_journal")), "")
    check("C.26 verification metadata retained",
          qrec.get("validation_status") is not None,
          str(qrec.get("validation_status")))


# ---------------------------------------------------------------------------
# D. Safety boundaries
# ---------------------------------------------------------------------------

def test_d_safety():
    bank, qids = make_bank(_tmp("bank_d.json"))
    engine = make_engine(bank, _tmp("store_d.json"))

    # D.27 the UI path cannot override the canonical journal.
    q1 = qids["q_credit_sale"]
    canonical_before = json.dumps(
        bank.get_question(q1).get("expected_journal"), sort_keys=True)
    try:
        bank.set_metadata(q1, {"expected_journal": {"debit": [["X", 1]]}})
        raised = False
    except ValueError:
        raised = True
    check("D.27 canonical journal not teacher-editable", raised, "")
    canonical_after = json.dumps(
        bank.get_question(q1).get("expected_journal"), sort_keys=True)
    check("D.27 canonical journal unchanged",
          canonical_before == canonical_after, "")

    # D.28 teacher expected journal cannot override FT-E: a wrong
    # teacher_expected_journal forces REVIEW_REQUIRED, never APPROVED.
    wrong = bank.create_question(
        "Sold goods to Ram on credit ₹9,000.", source_type="manual",
        source_reference="teacher-wrong",
        expected={"debit": [["Sales", 9000]], "credit": [["Ram", 9000]]})
    bank.compile_question(wrong)
    bank.validate_question(wrong)
    st_wrong = bank.get_question(wrong)["status"]
    approved = False
    if st_wrong == STATUS_VALIDATING:
        try:
            bank.approve_question(wrong)
            approved = True
        except ValueError:
            approved = False
    check("D.28 teacher journal cannot override FT-E",
          st_wrong == STATUS_REVIEW_REQUIRED and not approved,
          f"status={st_wrong} approved={approved}")

    # D.29 unapproved question can never reach practice.
    sid = engine.create_session("stu_d", mode=MODE_NORMAL)
    q1a = engine.select_next(sid)
    try:
        engine.submit_answer(sid, qids["q_rejected"],
                             ["Cash"], [1], ["Sales"], [1],
                             raw_response="x")
        raised = False
    except ValueError:
        raised = True
    check("D.29 rejected question cannot be submitted", raised, "")
    check("D.29 selection only yields approved",
          bank.get_question(q1a)["status"] == STATUS_APPROVED, "")

    # D.30 no LLM path can influence correctness: the UI + engine modules
    # contain no provider calls, and the verdict is a pure function of the
    # canonical journal + student entry.
    for path in ("backend/fyjc_practice_ui.py",
                 "backend/maths/fyjc_practice_engine.py"):
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        banned = [t for t in ("genai", "openai", "anthropic", "claude",
                              "chatgpt", "gpt-4", "gpt4")
                  if t in src]
        check(f"D.30 no LLM calls in {os.path.basename(path)}",
              not banned, str(banned))
    q1rec = bank.get_question(q1a)
    d_accs, d_amts, c_accs, c_amts = correct_entry(q1rec)
    correct = engine.submit_answer(
        sid, q1a, d_accs, d_amts, c_accs, c_amts,
        raw_response="Debit Cash 12000; Credit Ram 12000")
    check("D.30 verdict deterministic from engine",
          correct["outcome"] == OUTCOME_CORRECT
          and correct["verification_status"] == "VERIFIED", "")

    # D.31 no silent answer repair: the raw response is stored verbatim.
    weird = "  Debit   Cash   12000 ; Credit  Ram  12000  "
    attempt = engine.submit_answer(sid, q1a, d_accs, d_amts, c_accs,
                                   c_amts, raw_response=weird)
    check("D.31 raw response preserved verbatim",
          attempt["raw_response"] == weird, repr(attempt["raw_response"]))

    # D.32 historical attempts remain unchanged after later activity.
    snap = json.loads(json.dumps(engine.store.attempts, default=str))
    sid2 = engine.create_session("stu_d", mode=MODE_NORMAL)
    q2 = engine.select_next(sid2)
    q2rec = bank.get_question(q2)
    d2, a2, c2, b2 = correct_entry(q2rec)
    engine.submit_answer(sid2, q2, d2, a2, c2, b2,
                         raw_response="Debit Cash 12000; Credit Ram 12000")
    snap2 = json.loads(json.dumps(engine.store.attempts, default=str))
    unchanged = all(snap2[a] == snap[a] for a in snap)
    check("D.32 historical attempts immutable",
          unchanged and len(snap2) == len(snap) + 1,
          "attempts mutated")


# ---------------------------------------------------------------------------
# E. UI render smoke (AppTest over the real Streamlit modules)
# ---------------------------------------------------------------------------

def _practice_entry():
    import backend.fyjc_practice_ui as ui
    ui.render_practice_section(demo=True)


def _teacher_entry():
    import backend.fyjc_practice_ui as ui
    ui.render_teacher_section(demo=True)


def test_e_ui_smoke():
    from streamlit.testing.v1 import AppTest

    # Temp bank + store, seeded with the fixture; env overrides are read by
    # backend.fyjc_practice_ui at import time.
    bank_path = _tmp("ui_bank.json")
    store_path = _tmp("ui_store.json")
    make_bank(bank_path)
    os.environ["FTE_FYJC_BANK_PATH"] = bank_path
    os.environ["FTE_FYJC_PRACTICE_STORE_PATH"] = store_path

    at = AppTest.from_function(_practice_entry, default_timeout=120)
    at.run()
    check("E.1 practice page renders",
          not at.exception, [e.stack_trace for e in at.exception])

    # Start a session for the default student. Form-submit buttons are
    # keyed as FormSubmitter:<key>-<label> in AppTest, so look up by label.
    at.text_input(key="fte_pi_student").set_value("stu_ui")
    start_btn = next(b for b in at.button
                     if b.label == "Start practice session")
    start_btn.click().run()
    check("E.2 session started + question screen",
          not at.exception, [e.stack_trace for e in at.exception])
    texts = " ".join(m.value for m in at.markdown)
    check("E.2 approved question text displayed",
          any(t in texts for t in ("Sold goods to Ram", "Started business",
                                   "Purchased goods from Rahul",
                                   "Sold goods for cash", "Paid rent",
                                   "Deposited cash", "Bought machinery")),
          texts[:200])

    # Submit the correct journal for the first question: derive it from
    # the canonical journal of whichever question the UI actually served
    # (the seeded selection is deterministic but question-specific).
    with open(store_path, "r", encoding="utf-8") as fh:
        store = json.load(fh)
    sessions = [s for s in store.get("sessions", {}).values()
                if s.get("student_id") == "stu_ui"]
    qids_ui = sessions[0].get("question_ids") if sessions else []
    check("E.3 question served to student", bool(qids_ui), str(sessions))
    bank_ui = QuestionBank(store_path=bank_path)
    qrec_ui = bank_ui.get_question(qids_ui[0])
    d_accs, d_amts, c_accs, c_amts = correct_entry(qrec_ui)
    at.text_input(key="fte_pi_d1a").set_value(str(d_accs[0]))
    at.text_input(key="fte_pi_d1v").set_value(str(d_amts[0]))
    at.text_input(key="fte_pi_c1a").set_value(str(c_accs[0]))
    at.text_input(key="fte_pi_c1v").set_value(str(c_amts[0]))
    at.button(key="fte_pi_submit").click().run()
    check("E.3 submit renders verdict",
          not at.exception, [e.stack_trace for e in at.exception])
    texts = " ".join(m.value for m in at.markdown)
    check("E.3 CORRECT verdict rendered",
          "CORRECT" in texts, texts[:300])

    # Retry / Next / End controls render.
    check("E.4 retry/next/end controls present",
          any(b.label == "Retry" for b in at.button)
          and any(b.label == "Next question" for b in at.button)
          and any(b.label == "End session" for b in at.button),
          str([b.label for b in at.button]))

    # Teacher dashboard renders.
    at2 = AppTest.from_function(_teacher_entry, default_timeout=120)
    at2.run()
    check("E.5 teacher dashboard renders",
          not at2.exception, [e.stack_trace for e in at2.exception])
    ttexts = " ".join(m.value for m in at2.markdown)
    check("E.5 teacher content present",
          "Question Bank" in ttexts or "Student learning" in ttexts,
          ttexts[:200])


# ---------------------------------------------------------------------------

def main():
    test_a_student_flow()
    test_b_mastery()
    test_c_teacher()
    test_d_safety()
    test_e_ui_smoke()
    print(f"\n15I-I gate: {OK_COUNT[0]} checks passed, "
          f"{len(FAIL)} failed")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
