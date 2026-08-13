#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-H - Student Practice / Mistake Ledger / Mastery Engine gate
scripts/fte_fyjc_15h_student_learning_test.py

Drives the REAL 15I-H modules (fyjc_practice_engine.py +
fyjc_mistake_ledger.py + fyjc_mastery_engine.py) against a REAL 15I-G
Question Bank built through the FULL compile -> validate -> approve
pipeline. Tests A-Z + 20 adversarial cases from the sprint spec.

Deterministic gate: fixed RNG seed, fixed clock, isolated temp storage,
no AI, no network. Exit 0 = all checks pass.
"""

import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "backend")

from backend.maths.fyjc_mastery_engine import (  # noqa: E402
    MASTERY_DEVELOPING,
    MASTERY_LEARNING,
    MASTERY_MASTERED,
    MASTERY_REVIEW,
    MASTERY_UNSEEN,
)
from backend.maths.fyjc_mistake_ledger import (  # noqa: E402
    MISTAKE_IMPROVING,
    MISTAKE_OPEN,
    MISTAKE_RESOLVED,
)
from backend.maths.fyjc_practice_engine import (  # noqa: E402
    MODE_CHAPTER,
    MODE_MISTAKE_RETRY,
    MODE_NORMAL,
    MODE_WEAKNESS,
    MODE_REVISION,
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_NOT_SUPPORTED,
    OUTCOME_REVIEW_REQUIRED,
    SESSION_ACTIVE,
    SESSION_COMPLETED,
    PracticeEngine,
)
from backend.maths.fyjc_question_bank import QuestionBank  # noqa: E402
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Fixtures: a real approved Question Bank + deterministic engine
# ---------------------------------------------------------------------------

FIXED_CLOCK = [1700000000.0]


def clock():
    return FIXED_CLOCK[0]


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


RUN_ID = uuid.uuid4().hex[:10]


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), f"fte_15ih_{RUN_ID}_{name}")


def make_bank() -> QuestionBank:
    b = QuestionBank(store_path=_tmp("bank.json"))
    qids = {}
    for key, text, diff, chapter in BANK_QUESTIONS:
        qid = b.create_question(text, source_type="manual",
                                source_reference=key)
        b.compile_question(qid)
        b.validate_question(qid)
        b.approve_question(qid)
        b.set_metadata(qid, {"difficulty": diff, "chapter": chapter})
        qids[key] = qid
    # Rejected content (must never be selected).
    bad = b.create_question(
        "Prepare Trading and Profit and Loss Account for the year ended "
        "31 March.", source_type="manual")
    b.compile_question(bad)
    b.validate_question(bad)
    qids["q_rejected"] = bad
    assert b.get_question(bad)["status"] == "REJECTED"
    # Verified variant of the credit-sale canonical.
    vid = b.link_variant(qids["q_credit_sale"],
                         "Goods sold to Ram on credit Rs.12,000.",
                         source_type="generated", source_reference="v1")
    qids["q_credit_sale_variant"] = vid
    return b, qids


def make_engine(bank, seed=42, tag=""):
    return PracticeEngine(
        bank,
        store_path=_tmp(f"store_{seed}_{tag}.json"),
        rng_seed=seed, now_fn=clock)


def submit(eng, sid, qid, dr_a, dr_m, cr_a, cr_m, raw=""):
    return eng.submit_answer(sid, qid, dr_a, dr_m, cr_a, cr_m,
                             raw_response=raw)


def CANON_ENTRY(bank, qid):
    """The canonical journal of an approved question as submit arguments."""
    j = canonical(bank, qid)
    return ([acc for acc, _ in j["debit"]],
            [amt for _, amt in j["debit"]],
            [acc for acc, _ in j["credit"]],
            [amt for _, amt in j["credit"]])


def canonical(bank, qid):
    return bank.get_question(qid)["expected_journal"]


print("=" * 78)
print("SPRINT 15I-H - STUDENT PRACTICE / MISTAKE LEDGER / MASTERY GATE")
print("=" * 78)

bank, Q = make_bank()

# ---------------------------------------------------------------- A
print("\n--- A. session creation ---")
eng = make_engine(bank)
sid = eng.create_session("student_a", MODE_NORMAL)
s = eng.get_session(sid)
check("A1 session created ACTIVE", s["status"] == SESSION_ACTIVE, s["status"])
check("A2 required fields present",
      all(k in s for k in ("session_id", "student_id", "mode",
                           "started_at", "question_ids", "current_index",
                           "attempts", "completed_count", "correct_count",
                           "incorrect_count", "review_required_count",
                           "status")))
check("A3 unique session ids",
      eng.create_session("student_a", MODE_NORMAL) != sid)

# ---------------------------------------------------------------- B
print("\n--- B. verified question selection ---")
qid = eng.select_next(sid)
q = bank.get_question(qid)
check("B1 selection is APPROVED", q["status"] == "APPROVED", q["status"])
check("B2 selection recorded in session",
      qid in eng.get_session(sid)["question_ids"])

# ---------------------------------------------------------------- C
print("\n--- C. rejected question exclusion ---")
approved_ids = {q["question_id"] for q in bank.list_approved()}
check("C1 rejected question never in approved set",
      Q["q_rejected"] not in approved_ids)
selected = {eng.select_next(eng.create_session("student_c", MODE_NORMAL))
            for _ in range(15)}
check("C2 repeated selection never returns rejected content",
      Q["q_rejected"] not in selected)
try:
    submit(eng, sid, Q["q_rejected"], ["Cash"], [100], ["Sales"], [100])
    check("C3 submitting to rejected question blocked", False,
          "no ValueError")
except ValueError:
    check("C3 submitting to rejected question blocked", True)

# ---------------------------------------------------------------- D
print("\n--- D. answer submission ---")
s2 = eng.create_session("student_d", MODE_NORMAL)
a = submit(eng, s2, Q["q_credit_sale"], ["Ram"], [12000], ["Sales"], [12000])
ss = eng.get_session(s2)
check("D1 attempt recorded", a["attempt_id"] in eng.store.attempts)
check("D2 session counters updated",
      ss["attempts"] == 1 and ss["correct_count"] == 1
      and ss["completed_count"] == 1, str(ss))

# ---------------------------------------------------------------- E
print("\n--- E. correct answer ---")
check("E1 correct -> CORRECT / VERIFIED",
      a["outcome"] == OUTCOME_CORRECT
      and a["verification_status"] == "VERIFIED",
      f"{a['outcome']} / {a['verification_status']}")
check("E2 no mistake for correct answer", a["mistake_id"] is None)

# ---------------------------------------------------------------- F
print("\n--- F. incorrect answer ---")
s3 = eng.create_session("student_f", MODE_NORMAL)
f = submit(eng, s3, Q["q_credit_sale"], ["Ram"], [10000], ["Sales"], [10000])
check("F1 wrong amount -> INCORRECT", f["outcome"] == OUTCOME_INCORRECT,
      f["outcome"])
check("F2 category AMOUNT_ERROR", f["mistake_category"] == "AMOUNT_ERROR",
      f["mistake_category"])
check("F3 verified journal captured", f["verified_journal"] is not None)

# ---------------------------------------------------------------- G
print("\n--- G. REVIEW_REQUIRED answer ---")
s4 = eng.create_session("student_g", MODE_NORMAL)
g = submit(eng, s4, Q["q_credit_sale"], [], [], [], [], raw="")
check("G1 ambiguous -> REVIEW_REQUIRED",
      g["outcome"] == OUTCOME_REVIEW_REQUIRED
      and g["verification_status"] == "REVIEW_REQUIRED",
      f"{g['outcome']} / {g['verification_status']}")
check("G2 category AMBIGUOUS_RESPONSE",
      g["mistake_category"] == "AMBIGUOUS_RESPONSE")

# ---------------------------------------------------------------- H
print("\n--- H. NOT_SUPPORTED answer ---")
s5 = eng.create_session("student_h", MODE_NORMAL)
h = submit(eng, s5, Q["q_credit_sale"], [], [], [], [],
           raw="I don't know this")
check("H1 unsupported -> NOT_SUPPORTED", h["outcome"] == OUTCOME_NOT_SUPPORTED,
      h["outcome"])
check("H2 category UNSUPPORTED_RESPONSE",
      h["mistake_category"] == "UNSUPPORTED_RESPONSE")

# ---------------------------------------------------------------- I
print("\n--- I. mistake creation ---")
check("I1 mistake recorded on INCORRECT", f["mistake_id"] is not None)
m = eng.ledger.get(f["mistake_id"])
check("I2 mistake fields present",
      all(k in m for k in ("mistake_id", "student_id", "session_id",
                           "question_id", "attempt_id", "concept",
                           "concept_key", "transaction_type", "difficulty",
                           "mistake_category",
                           "expected_journal_reference", "student_response",
                           "created_at", "resolved_at", "occurrence_count",
                           "status")))
check("I3 status OPEN", m["status"] == MISTAKE_OPEN, m["status"])
check("I4 occurrence 1", m["occurrence_count"] == 1)

# ---------------------------------------------------------------- J
print("\n--- J. mistake classification ---")
s6 = eng.create_session("student_j", MODE_NORMAL)

def classify_case(name, qkey, dr_a, dr_m, cr_a, cr_m, expected):
    at = submit(eng, s6, Q[qkey], dr_a, dr_m, cr_a, cr_m)
    check(f"J {name} -> {expected}",
          at["mistake_category"] == expected,
          f"got {at['mistake_category']} outcome={at['outcome']}")

classify_case("reversed rent", "q_rent",
              ["Cash"], [6000], ["Rent"], [6000],
              "DEBIT_CREDIT_DIRECTION")
classify_case("reversed party", "q_machinery",
              ["Amar"], [150000], ["Machinery"], [150000],
              "PARTY_ROLE_ERROR")
classify_case("omitted account", "q_credit_sale",
              ["Ram"], [12000], ["Cash"], [12000],
              "ACCOUNT_SELECTION")
classify_case("invented account", "q_credit_sale",
              ["Ram"], [12000], ["Bank"], [12000],
              "ACCOUNT_SELECTION")
classify_case("wrong family", "q_credit_sale",
              ["Purchases"], [12000], ["Cash"], [12000],
              "TRANSACTION_CLASSIFICATION")
classify_case("unbalanced", "q_rent",
              ["Rent"], [6000], ["Cash"], [5000],
              "LEDGER_BALANCING_ERROR")
classify_case("multi-tx partial", "q_multi",
              ["Cash"], [100000], ["Capital"], [100000],
              "MULTI_TRANSACTION_ERROR")
classify_case("cash discount omitted", "q_cash_discount",
              ["Purchases"], [10000], ["Cash", "Rahul"],
              [5000, 5000], "CASH_DISCOUNT_ERROR")

# ---------------------------------------------------------------- K
print("\n--- K. repeated mistake counting ---")
s7 = eng.create_session("student_k", MODE_NORMAL)
mid = None
for _ in range(5):
    at = submit(eng, s7, Q["q_credit_sale"], ["Ram"], [9999], ["Sales"], [9999])
    mid = at["mistake_id"]
m = eng.ledger.get(mid)
check("K1 same mistake id across repeats", at["mistake_id"] == mid)
check("K2 occurrence_count == 5", m["occurrence_count"] == 5,
      m["occurrence_count"])
check("K3 still OPEN", m["status"] == MISTAKE_OPEN)

# ---------------------------------------------------------------- L
print("\n--- L. mistake resolution ---")
s8 = eng.create_session("student_l", MODE_NORMAL)
submit(eng, s8, Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])  # correct
submit(eng, s8, Q["q_rent"], ["Rent"], [6000], ["Cash"], [5000])  # mistake
m1 = eng.ledger.open_mistakes(student_id="student_l")
mid_l = m1[0]["mistake_id"]
submit(eng, s8, Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])  # correct 1
check("L1 first correct -> IMPROVING",
      eng.ledger.get(mid_l)["status"] == MISTAKE_IMPROVING,
      eng.ledger.get(mid_l)["status"])
submit(eng, s8, Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])  # correct 2
check("L2 second correct -> RESOLVED",
      eng.ledger.get(mid_l)["status"] == MISTAKE_RESOLVED,
      eng.ledger.get(mid_l)["status"])
check("L3 historical mistake never deleted", eng.ledger.get(mid_l) is not None)

# ---------------------------------------------------------------- M
print("\n--- M. mastery creation ---")
rec = eng.mastery.get("student_d", "SALE_GOODS_CREDIT")
check("M1 mastery record created", rec["attempts"] == 1, str(rec["attempts"]))
check("M2 initial state LEARNING", rec["mastery_state"] == MASTERY_LEARNING,
      rec["mastery_state"])

# ---------------------------------------------------------------- N
print("\n--- N. mastery transition ---")
sn = eng.create_session("student_n", MODE_NORMAL)
for _ in range(3):
    submit(eng, sn, Q["q_meena"], ["Cash"], [12000], ["Sales"], [12000])
submit(eng, sn, Q["q_meena"], ["Cash"], [12000], ["Sales"], [11000])  # wrong
submit(eng, sn, Q["q_meena"], ["Cash"], [12000], ["Sales"], [12000])
rec = eng.mastery.get("student_n", "SALE_GOODS_CASH")
check("N1 DEVELOPING after improvement", rec["mastery_state"] == MASTERY_DEVELOPING,
      rec["mastery_state"])
for _ in range(3):
    submit(eng, sn, Q["q_meena"], ["Cash"], [12000], ["Sales"], [12000])
rec = eng.mastery.get("student_n", "SALE_GOODS_CASH")
check("N2 MASTERED after sustained accuracy",
      rec["mastery_state"] == MASTERY_MASTERED,
      f"{rec['mastery_state']} acc={rec['accuracy']} recent={rec['recent_accuracy']}")
check("N3 transition evidence recorded",
      any(t["to"] == MASTERY_MASTERED for t in rec["transitions"]),
      str([(t["from"], t["to"]) for t in rec["transitions"]]))

# ---------------------------------------------------------------- O
print("\n--- O. recent-performance degradation ---")
for _ in range(3):
    submit(eng, sn, Q["q_meena"], ["Cash"], [12000], ["Sales"], [9000])
rec = eng.mastery.get("student_n", "SALE_GOODS_CASH")
check("O1 MASTERED degrades to REVIEW",
      rec["mastery_state"] == MASTERY_REVIEW, rec["mastery_state"])

# ---------------------------------------------------------------- P
print("\n--- P. weakness selection ---")
sp = eng.create_session("student_p", MODE_WEAKNESS)
submit(eng, sp, Q["q_rent"], ["Rent"], [6000], ["Cash"], [5000])  # weak EXPENSE_PAID
nxt = eng.select_next(sp)
nq = bank.get_question(nxt)
check("P1 weakness mode targets weak concept",
      nq["concept_key"] == "EXPENSE_PAID", nq["concept_key"])

# ---------------------------------------------------------------- Q
print("\n--- Q. chapter selection ---")
sq = eng.create_session("student_q", MODE_CHAPTER, chapter="Ch.3 Journal")
sel = {eng.select_next(sq) for _ in range(8)}
check("Q1 chapter-scoped selection",
      all(bank.get_question(q)["chapter"] == "Ch.3 Journal" for q in sel),
      str([bank.get_question(q)["chapter"] for q in sel]))
try:
    submit(eng, sq, Q["q_bank_deposit"], ["Bank"], [10000], ["Cash"], [10000])
    check("Q2 out-of-chapter submission blocked", False, "no ValueError")
except ValueError:
    check("Q2 out-of-chapter submission blocked", True)

# ---------------------------------------------------------------- R
print("\n--- R. difficulty progression ---")
sr = eng.create_session("student_r", MODE_NORMAL)
for qkey in ("q_cash_sale", "q_credit_purchase", "q_rent"):
    submit(eng, sr, Q[qkey], *CANON_ENTRY(bank, Q[qkey]))
nxt = eng.select_next(sr)
check("R1 after 3 successes targets MEDIUM",
      bank.get_question(nxt)["difficulty"] == 2,
      f"got difficulty {bank.get_question(nxt)['difficulty']}")

# ---------------------------------------------------------------- S
print("\n--- S. anti-repetition ---")
ss_ = eng.create_session("student_s", MODE_NORMAL)
first = eng.select_next(ss_)
submit(eng, ss_, first, *CANON_ENTRY(bank, first))
second = eng.select_next(ss_)
check("S1 no immediate repeat in NORMAL mode", second != first,
      f"{first} -> {second}")

# ---------------------------------------------------------------- T
print("\n--- T. verified variant selection ---")
st = eng.create_session("student_t", MODE_NORMAL)
submit(eng, st, Q["q_credit_sale"], ["Ram"], [12000], ["Sales"], [12000])
nxt = eng.select_next(st)
check("T1 variant preferred after canonical answered",
      nxt == Q["q_credit_sale_variant"], f"got {nxt}")

# ---------------------------------------------------------------- U
print("\n--- U. LLM suggestion cannot bypass verification ---")
su = eng.create_session("student_u", MODE_NORMAL)
before = canonical(bank, Q["q_credit_sale"])
llm_wrong = submit(eng, su, Q["q_credit_sale"],
                   ["Sales"], [12000], ["Ram"], [12000])
check("U1 LLM-suggested wrong journal -> INCORRECT",
      llm_wrong["outcome"] == OUTCOME_INCORRECT, llm_wrong["outcome"])
check("U2 canonical journal untouched",
      canonical(bank, Q["q_credit_sale"]) == before)

# ---------------------------------------------------------------- V
print("\n--- V. canonical journal cannot be overwritten ---")
after = canonical(bank, Q["q_credit_sale"])
check("V1 canonical identical after full battery", after == before)

# ---------------------------------------------------------------- W
print("\n--- W. raw student response preservation ---")
sw = eng.create_session("student_w", MODE_NORMAL)
raw = "  Ram  Dr   12,000   (my   answer)  "
aw = submit(eng, sw, Q["q_credit_sale"], ["Ram"], [12000], ["Sales"], [12000],
            raw=raw)
check("W1 raw_response verbatim", aw["raw_response"] == raw,
      repr(aw["raw_response"]))
check("W2 normalized_response separate",
      aw["normalized_response"] == "Ram Dr 12,000 (my answer)")

# ---------------------------------------------------------------- X
print("\n--- X. session replay determinism ---")
def run_replay():
    e = make_engine(bank, seed=777, tag=uuid.uuid4().hex[:6])
    sid = e.create_session("student_x", MODE_NORMAL)
    seq = [("q_credit_sale", ["Ram"], [12000], ["Sales"], [12000]),
           ("q_rent", ["Cash"], [6000], ["Rent"], [6000]),
           ("q_cash_discount", ["Purchases"], [10000],
            ["Cash", "Discount Received", "Rahul"], [4900, 100, 5000]),
           ("q_credit_sale", ["Ram"], [9999], ["Sales"], [9999])]
    for qkey, dr_a, dr_m, cr_a, cr_m in seq:
        submit(e, sid, Q[qkey], dr_a, dr_m, cr_a, cr_m)
    submit(e, sid, Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])
    return e.replay_aggregates("student_x")

r1 = run_replay()
r2 = run_replay()
check("X1 replay produces identical aggregates", r1 == r2,
      f"{r1} != {r2}")

# ---------------------------------------------------------------- Y
print("\n--- Y. historical Question Bank integrity ---")
check("Y1 approved count unchanged",
      len(bank.list_approved()) == 10, len(bank.list_approved()))
check("Y2 sample journals unchanged",
      canonical(bank, Q["q_rent"]) == {"debit": [["Rent", 6000]],
                                       "credit": [["Cash", 6000]]})

# ---------------------------------------------------------------- Z
print("\n--- Z. 15E-15I regression compatibility ---")
pins = {
    "Mohan was paid ₹5,000.": (["Mohan"], ["Cash"], "VERIFIED"),
    "Was paid ₹5,000.": ([], [], "NOT_SUPPORTED"),
    "Sold goods to Ram ₹12,000. Received further cash ₹5,000.":
        ([], [], "REVIEW_REQUIRED"),
    "Opened an account with Bank of India ₹20,000.":
        (["Bank"], ["Cash"], "VERIFIED"),
    "Bought goods from Rahul ₹12,000. Paid him ₹5,000.":
        (["Purchases"], ["Cash", "Rahul"], "VERIFIED"),
}
for text, (exp_dr, exp_cr, exp_status) in pins.items():
    res = reason_bk_question(text)
    j = res.get("journal") or {}
    dr = sorted(l.get("account") for l in (j.get("debit_lines") or []))
    cr = sorted(l.get("account") for l in (j.get("credit_lines") or []))
    check(f"Z pin {text[:40]!r}",
          res.get("status") == exp_status and dr == exp_dr and cr == exp_cr,
          f"status={res.get('status')} dr={dr} cr={cr}")

# ---------------------------------------------------------------- ADVERSARIAL
print("\n--- ADVERSARIAL (20) ---")

# 1-6 covered above; explicit asserts for the full matrix.
adv = eng
sa = eng.create_session("adv", MODE_NORMAL)
adv_checks = [
    ("A1 reversed Dr/Cr -> direction/party", "q_rent",
     ["Cash"], [6000], ["Rent"], [6000],
     ("DEBIT_CREDIT_DIRECTION", OUTCOME_INCORRECT)),
    ("A2 right accounts wrong amount", "q_credit_sale",
     ["Ram"], [11000], ["Sales"], [11000],
     ("AMOUNT_ERROR", OUTCOME_INCORRECT)),
    ("A3 omitted account", "q_credit_sale",
     ["Ram"], [12000], ["Cash"], [12000],
     ("ACCOUNT_SELECTION", OUTCOME_INCORRECT)),
    ("A4 invented account", "q_credit_sale",
     ["Ram"], [12000], ["Bank"], [12000],
     ("ACCOUNT_SELECTION", OUTCOME_INCORRECT)),
    ("A5 ambiguous journal", "q_credit_sale",
     [], [], [], [],
     ("AMBIGUOUS_RESPONSE", OUTCOME_REVIEW_REQUIRED)),
    ("A6 unsupported language", "q_credit_sale",
     [], [], [], [],
     ("UNSUPPORTED_RESPONSE", OUTCOME_NOT_SUPPORTED)),
]
for name, qkey, dr_a, dr_m, cr_a, cr_m, (cat, out) in adv_checks:
    aa = submit(eng, sa, Q[qkey], dr_a, dr_m, cr_a, cr_m,
                raw="I don't know" if out == OUTCOME_NOT_SUPPORTED else "")
    check(name, aa["outcome"] == out and aa["mistake_category"] == cat,
          f"outcome={aa['outcome']} cat={aa['mistake_category']}")

# 7 repeated 5x / 8 mistake then success (K/L already)
# 9 MASTERED -> REVIEW (O already)
# 10 LLM wrong canonical (U already)
# 11 teacher expected conflict with FT-E: canonical wins.
st2 = eng.create_session("adv_teacher", MODE_NORMAL)
t = submit(eng, st2, Q["q_credit_sale"], ["Ram"], [12000], ["Cash"], [12000])
check("A11 teacher-style expectation cannot override FT-E canonical",
      t["outcome"] == OUTCOME_INCORRECT
      and t["mistake_category"] == "ACCOUNT_SELECTION",
      f"{t['outcome']} {t['mistake_category']}")

# 12 question invalid after modification: text edits blocked by bank;
#    submitting to an invalidated question is refused.
try:
    bank.set_metadata(Q["q_rent"], {"raw_text": "Changed."})
    check("A12 text modification blocked", False, "no ValueError")
except ValueError:
    check("A12 text modification blocked", True)

# 13 duplicate selection (S already) - assert fallback repeats only when empty.
solo = eng.create_session("adv_solo", MODE_CHAPTER, chapter="Ch.2 Basic Accounting Terms")
q_a = eng.select_next(solo)
q_b = eng.select_next(solo)
check("A13 sole question repeats when no other candidate",
      q_a == q_b and bank.get_question(q_a)["chapter"] == "Ch.2 Basic Accounting Terms")

# 14 meaning-changing variant refused at the bank (never approved, so
# it can never be selected or verified in practice).
bad_vid = bank.link_variant(
    Q["q_credit_sale"],
    "Sold goods to Ram ₹12,000. Received ₹5,000 from him.",
    source_type="generated", source_reference="bad")
bv = bank.get_question(bad_vid)
check("A14 meaning-changing variant never approved",
      bv["status"] != "APPROVED", bv["status"])
check("A14b variant excluded from approved content",
      bad_vid not in {q["question_id"] for q in bank.list_approved()})

# 15 REVIEW_REQUIRED cannot push mastery to MASTERED.
s15 = eng.create_session("adv15", MODE_NORMAL)
for _ in range(5):
    submit(eng, s15, Q["q_rent"], [], [], [], [], raw="")
rec = eng.mastery.get("adv15", "EXPENSE_PAID")
check("A15 REVIEW_REQUIRED responses do not create mastery",
      rec["attempts"] == 5 and rec["correct"] == 0 and rec["incorrect"] == 0
      and rec["review_required"] == 5
      and rec["mastery_state"] == MASTERY_LEARNING,
      f"{rec['mastery_state']} c={rec['correct']} i={rec['incorrect']} r={rec['review_required']}")

# 16 corrupted / partial session.
try:
    submit(eng, "S-does-not-exist", Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])
    check("A16 unknown session rejected", False, "no KeyError")
except KeyError:
    check("A16 unknown session rejected", True)
done = eng.create_session("adv16", MODE_NORMAL)
eng.complete_session(done)
try:
    submit(eng, done, Q["q_rent"], ["Rent"], [6000], ["Cash"], [6000])
    check("A17 completed session rejects submissions", False, "no ValueError")
except ValueError:
    check("A17 completed session rejects submissions", True)

# 18 rejected content (C already) - bank contains rejected + selection excludes.
# 19/20 out-of-scope submissions.
s19 = eng.create_session("adv19", MODE_CHAPTER, chapter="Ch.3 Journal")
try:
    submit(eng, s19, Q["q_bank_deposit"], ["Bank"], [10000], ["Cash"], [10000])
    check("A19 out-of-chapter submission blocked", False, "no ValueError")
except ValueError:
    check("A19 out-of-chapter submission blocked", True)
s20 = eng.create_session("adv20", MODE_NORMAL, difficulty=1)
try:
    submit(eng, s20, Q["q_credit_sale"], ["Ram"], [12000], ["Sales"], [12000])
    check("A20 out-of-difficulty submission blocked", False, "no ValueError")
except ValueError:
    check("A20 out-of-difficulty submission blocked", True)

# 17 replay (X already) - re-assert with the adversarial store.
r3 = eng.replay_aggregates("adv")
check("A17b replay aggregate shape stable", isinstance(r3["attempts"], list)
      and len(r3["attempts"]) == len(adv_checks))

# ------------------------------------------------------------------
print("\n" + "=" * 78)
print(f"SUMMARY: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("SPRINT 15I-H PASS - STUDENT LEARNING LAYER VERIFIED")
sys.exit(0)
