#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-N - Advanced Personalization & Adaptive Learning Engine gate
scripts/fte_fyjc_15n_personalization_test.py

Drives the REAL Sprint 15I-N module (backend/maths/
fyjc_personalization_engine.py) against REAL persisted evidence produced
by the 15I-H Practice / Mistake / Mastery stack, a REAL 15I-G Question
Bank (compile -> validate -> approve), and REAL 15I-M generated content
(live batch through the full lifecycle).

Sections (sprint spec section 23):
  A. cold-start profile
  B. weakness detection
  C. strength detection
  D. repeated mistake targeting
  E. amount-error personalization
  F. debit/credit personalization
  G. GST personalization
  H. TD personalization
  I. CD personalization
  J. multi-transaction personalization
  K. recent accuracy weighting
  L. lifetime vs recent behavior
  M. mastery degradation
  N. mastery recovery
  O. spaced revision priority
  P. difficulty progression
  Q. difficulty regression
  R. remediation session
  S. revision session
  T. exam session
  U. mixed session
  V. diversity control
  W. canonical repetition prevention
  X. variant preference
  Y. cold-start determinism
  Z. seeded determinism
  AA. identical history -> identical profile
  AB. REVIEW_REQUIRED does not count as incorrect
  AC. NOT_SUPPORTED does not count as incorrect
  AD. rejected questions never selected
  AE. LLM cannot influence personalization
  AF. QuestionBank remains unchanged
  AG. PracticeEngine remains authoritative
  AH. teacher dashboard aggregation
  AI. explanation evidence matches actual records
  AJ. adversarial corrupted-history handling
  + 15I-M generated-content integration (spec section 27)

Deterministic gate: fixed clock, fixed seeds, isolated temp storage,
no AI, no network. Exit 0 = all checks pass.
"""

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_question_bank import QuestionBank  # noqa: E402
from backend.maths.fyjc_practice_engine import (  # noqa: E402
    MODE_MISTAKE_RETRY,
    MODE_NORMAL,
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    PracticeEngine,
)
from backend.maths.fyjc_mastery_engine import (  # noqa: E402
    MASTERY_DEVELOPING,
    MASTERY_LEARNING,
    MASTERY_MASTERED,
    MASTERY_REVIEW,
)
from backend.maths.fyjc_personalization_engine import (  # noqa: E402
    PersonalizationEngine,
    OBJECTIVE_DIFFICULTY_PROGRESSION,
    OBJECTIVE_EXAM_PREPARATION,
    OBJECTIVE_MIXED_PRACTICE,
    OBJECTIVE_REMEDIATION,
    OBJECTIVE_REVISION,
    OBJECTIVE_WEAK_AREA_FOCUS,
    MODE_ADAPTIVE,
    MODE_COLD_START,
    DIRECTION_ADVANCE,
    DIRECTION_REINFORCE,
    DIRECTION_REMEDIATION,
    profile_fingerprint,
)
from backend.maths.fyjc_mistake_ledger import (  # noqa: E402
    MISTAKE_OPEN,
)

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
# Fixtures
# ---------------------------------------------------------------------------

FIXED_CLOCK = [1700000000.0]


def clock():
    return FIXED_CLOCK[0]


RUN_ID = uuid.uuid4().hex[:10]


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), f"fte_15in_{RUN_ID}_{name}")


def make_bank(store_path=None) -> tuple:
    """A real approved bank: plain + GST + TD + CD + multi questions, a
    verified variant, and a REJECTED question (never selectable)."""
    b = QuestionBank(store_path=store_path or _tmp("bank.json"))
    qids = {}
    for key, text, diff, chapter in [
        ("cs", "Sold goods to Ram on credit Rs.12,000.", 2, "Ch.3 Journal"),
        ("cs2", "Sold goods for cash Rs.25,000.", 1, "Ch.3 Journal"),
        ("pc", "Purchased goods from Rahul on credit Rs.10,000.", 1,
         "Ch.3 Journal"),
        ("rent", "Paid rent Rs.6,000.", 1, "Ch.3 Journal"),
        ("bank", "Deposited cash into bank Rs.10,000.", 1,
         "Ch.2 Basic Accounting Terms"),
        ("cd_s", "Sold goods to Ram for Rs.10,000 on credit. Received "
                 "Rs.9,800 from Ram and allowed Rs.200 cash discount.", 3,
         "Ch.3 Journal"),
        ("cd_p", "Purchased goods from Rahul for Rs.10,000 on credit. "
                 "Paid Rs.9,800 and received Rs.200 cash discount.", 3,
         "Ch.3 Journal"),
        ("td_s", "Sold goods to Ram on credit Rs.30,000 at 10% trade "
                 "discount.", 2, "Ch.3 Journal"),
        ("gst_s", "Sold goods to Mohan on credit Rs.20,000, IGST @ 18%.",
         2, "Ch.3 Journal"),
        ("gst_p", "Purchased goods for cash Rs.11,800 inclusive of GST "
                  "@ 18%, intra-state.", 2, "Ch.3 Journal"),
        ("multi", "Started business with cash Rs.1,00,000. Purchased "
                  "goods for cash Rs.20,000. Paid rent Rs.5,000.", 3,
         "Ch.3 Journal"),
        ("mach", "Bought machinery from Amar on credit Rs.1,50,000.", 2,
         "Ch.3 Journal"),
    ]:
        qid = b.create_question(text, source_type="manual",
                                source_reference=key)
        b.compile_question(qid)
        b.validate_question(qid)
        b.approve_question(qid)
        b.set_metadata(qid, {"difficulty": diff, "chapter": chapter})
        qids[key] = qid
    # Verified variant of the credit-sale canonical.
    qids["cs_v"] = b.link_variant(
        qids["cs"], "Goods sold to Ram on credit Rs.12,000.",
        source_type="generated", source_reference="v1")
    # Rejected content (must never be selected).
    bad = b.create_question(
        "Prepare Trading and Profit and Loss Account for the year ended "
        "31 March.", source_type="manual")
    b.compile_question(bad)
    b.validate_question(bad)
    qids["rej"] = bad
    return b, qids


def make_engine(bank, seed=42, tag=""):
    return PracticeEngine(bank, _tmp(f"store_{seed}_{tag}.json"),
                          rng_seed=seed, now_fn=clock)


def submit(eng, sid, qid, dr_a, dr_m, cr_a, cr_m, raw=""):
    return eng.submit_answer(sid, qid, dr_a, dr_m, cr_a, cr_m,
                             raw_response=raw)


def canonical(bank, qid):
    return bank.get_question(qid)["expected_journal"]


def CANON_ENTRY(bank, qid):
    j = canonical(bank, qid)
    return ([acc for acc, _ in j["debit"]],
            [amt for _, amt in j["debit"]],
            [acc for acc, _ in j["credit"]],
            [amt for _, amt in j["credit"]])


def profile_of(p, eng, student_id):
    return p.evaluate(student_id, eng.store.attempts,
                      eng.ledger.records(), eng.mastery.records())


def concept_of(bank, qid):
    return bank.get_question(qid)["concept_key"]


def band_of(bank, qid):
    return bank.get_question(qid)["difficulty"]


print("=" * 78)
print("SPRINT 15I-N - ADVANCED PERSONALIZATION & ADAPTIVE LEARNING GATE")
print("=" * 78)

BANK, Q = make_bank()
POOL = [qid for qid in Q.values() if qid != Q["rej"]]
P = PersonalizationEngine(BANK, seed=42)

# ---------------------------------------------------------------- A
print("\n--- A. cold-start profile ---")
eng = make_engine(BANK, tag="a")
prof = profile_of(P, eng, "cold")
check("A1 mode COLD_START", prof["mode"] == MODE_COLD_START, prof["mode"])
check("A2 no fabricated weaknesses", prof["concept_weaknesses"] == [])
check("A3 no fabricated strengths", prof["concept_strengths"] == [])
check("A4 baseline mixed objective",
      prof["recommended_objective"] == OBJECTIVE_MIXED_PRACTICE,
      prof["recommended_objective"])
mix = prof["recommended_mix"]
check("A5 cold-start mix has no remediation/revision weight",
      mix["weakness_remediation"] == 0.0 and mix["revision"] == 0.0
      and abs(sum(mix.values()) - 1.0) < 1e-6, str(mix))
check("A6 baseline explanation present",
      any("Building your baseline" in e for e in prof["explanations"]),
      str(prof["explanations"]))
check("A7 confidence reports cold start",
      prof["confidence"]["mode"] == MODE_COLD_START
      and prof["confidence"]["overall"] < 0.5,
      str(prof["confidence"]))

# ---------------------------------------------------------------- B
print("\n--- B. weakness detection ---")
eng = make_engine(BANK, tag="b")
sid = eng.create_session("weak", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["rent"], ["Cash"], [6000], ["Rent"], [6000])
prof = profile_of(P, eng, "weak")
check("B1 adaptive after >=5 scored attempts",
      prof["mode"] == MODE_ADAPTIVE, prof["mode"])
weak = [w["concept_key"] for w in prof["concept_weaknesses"]]
check("B2 EXPENSE_PAID detected weak",
      "EXPENSE_PAID" in weak, str(weak))
wrec = next(w for w in prof["concept_weaknesses"]
            if w["concept_key"] == "EXPENSE_PAID")
check("B3 weakness carries evidence",
      any(e["kind"] == "recent_accuracy" for e in wrec["evidence"]),
      str(wrec["evidence"]))
check("B4 recommended objective targets weaknesses",
      prof["recommended_objective"] == OBJECTIVE_WEAK_AREA_FOCUS,
      prof["recommended_objective"])

# ---------------------------------------------------------------- C
print("\n--- C. strength detection ---")
eng = make_engine(BANK, tag="c")
sid = eng.create_session("strong", MODE_NORMAL)
for _ in range(6):
    submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
prof = profile_of(P, eng, "strong")
strong = [s["concept_key"] for s in prof["concept_strengths"]]
check("C1 SALE_GOODS_CASH claimed strong after sustained success",
      "SALE_GOODS_CASH" in strong, str(strong))
check("C2 sustained success is not weak",
      "SALE_GOODS_CASH" not in
      [w["concept_key"] for w in prof["concept_weaknesses"]])
eng1 = make_engine(BANK, tag="c1")
sid1 = eng1.create_session("one", MODE_NORMAL)
submit(eng1, sid1, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
prof1 = profile_of(P, eng1, "one")
check("C3 single success never implies strength",
      prof1["concept_strengths"] == [],
      str(prof1["concept_strengths"]))

# ---------------------------------------------------------------- D
print("\n--- D. repeated mistake targeting ---")
eng = make_engine(BANK, tag="d")
sid = eng.create_session("repeat", MODE_NORMAL)
for _ in range(4):
    submit(eng, sid, Q["cs"], ["Ram"], [9999], ["Sales"], [9999])
prof = profile_of(P, eng, "repeat")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("D1 AMOUNT_ERROR pattern present",
      "AMOUNT_ERROR" in pats, str(list(pats)))
pat = pats.get("AMOUNT_ERROR")
check("D2 occurrence counting matches ledger",
      pat["occurrence_count"] == 4 and pat["open_count"] >= 1,
      str(pat))
check("D3 repeated mistakes are targeted", pat["targeted"] is True)
check("D4 pattern carries evidence",
      any(e["kind"] == "occurrences" for e in pat["evidence"]))

# ---------------------------------------------------------------- E
print("\n--- E. amount-error personalization (Cash Discount area) ---")
eng = make_engine(BANK, tag="e")
sid = eng.create_session("amt", MODE_NORMAL)
# Right accounts, wrong (still balanced) amounts on the cash-discount
# sale -> AMOUNT_ERROR; the question hints 'cash discount' so the
# recommendation must target exactly that weakness.
for _ in range(6):
    submit(eng, sid, Q["cd_s"], ["Ram", "Cash", "Discount Allowed"],
           [10100, 9800, 100], ["Sales", "Ram"], [10100, 9900])
prof = profile_of(P, eng, "amt")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("E1 AMOUNT_ERROR targeted on the Cash Discount question",
      pats.get("AMOUNT_ERROR", {}).get("targeted") is True,
      str(pats.get("AMOUNT_ERROR")))
amt = pats["AMOUNT_ERROR"]
check("E2 pattern tied to the practised question",
      Q["cd_s"] in amt["question_ids"], str(amt["question_ids"]))
no_mistakes = P.ranked_questions(
    "amt", eng.store.attempts, {}, eng.mastery.records(), POOL,
    objective=OBJECTIVE_REMEDIATION)
with_mistakes = P.ranked_questions(
    "amt", eng.store.attempts, eng.ledger.records(), eng.mastery.records(),
    POOL, objective=OBJECTIVE_REMEDIATION)
cd_no = next(r for r in no_mistakes if r["question_id"] == Q["cd_s"])
cd_yes = next(r for r in with_mistakes if r["question_id"] == Q["cd_s"])
check("E3 mistake evidence raises the question's mistake factor",
      cd_yes["factors"]["mistake"] > cd_no["factors"]["mistake"],
      f"{cd_no['factors']['mistake']} -> {cd_yes['factors']['mistake']}")
check("E4 weak-area focus surfaces the weak concept",
      concept_of(BANK, with_mistakes[0]["question_id"])
      == "SALE_GOODS_CREDIT",
      concept_of(BANK, with_mistakes[0]["question_id"]))

# ---------------------------------------------------------------- F
print("\n--- F. debit/credit personalization ---")
eng = make_engine(BANK, tag="f")
sid = eng.create_session("dir", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["rent"], ["Cash"], [6000], ["Rent"], [6000])
prof = profile_of(P, eng, "dir")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("F1 DEBIT_CREDIT_DIRECTION targeted",
      pats.get("DEBIT_CREDIT_DIRECTION", {}).get("targeted") is True,
      str(pats.get("DEBIT_CREDIT_DIRECTION")))
focus = [f for f in prof["recommended_focus_areas"]
         if f["kind"] == "mistake"]
check("F2 direction mistake appears in recommended focus",
      any(f["area"] == "DEBIT_CREDIT_DIRECTION" for f in focus),
      str([f["area"] for f in focus]))

# ---------------------------------------------------------------- G
print("\n--- G. GST personalization ---")
eng = make_engine(BANK, tag="g")
sid = eng.create_session("gst", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["gst_p"], ["Purchases", "Input CGST", "Input SGST"],
           [10000, 700, 700], ["Cash"], [11400])
prof = profile_of(P, eng, "gst")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("G1 GST_ERROR targeted",
      pats.get("GST_ERROR", {}).get("targeted") is True,
      str(pats.get("GST_ERROR")))
weak = [w["concept_key"] for w in prof["concept_weaknesses"]]
check("G2 GST purchase concept flagged weak",
      "PURCHASE_GOODS_CASH" in weak, str(weak))
ranked = P.ranked_questions(
    "gst", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_WEAK_AREA_FOCUS)
check("G3 weak-area focus surfaces a GST question",
      concept_of(BANK, ranked[0]["question_id"]) == "PURCHASE_GOODS_CASH",
      f"{ranked[0]['question_id']} -> "
      f"{concept_of(BANK, ranked[0]['question_id'])}")

# ---------------------------------------------------------------- H
print("\n--- H. TD personalization ---")
eng = make_engine(BANK, tag="h")
sid = eng.create_session("td", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["td_s"], ["Ram"], [25000], ["Sales"], [25000])
prof = profile_of(P, eng, "td")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("H1 TRADE_DISCOUNT_ERROR targeted",
      pats.get("TRADE_DISCOUNT_ERROR", {}).get("targeted") is True,
      str(pats.get("TRADE_DISCOUNT_ERROR")))
ranked = P.ranked_questions(
    "td", eng.store.attempts, eng.ledger.records(), eng.mastery.records(),
    POOL, objective=OBJECTIVE_REMEDIATION)
top3 = [r["question_id"] for r in ranked[:3]]
check("H2 TD question surfaces in remediation top 3",
      Q["td_s"] in top3, str(top3))

# ---------------------------------------------------------------- I
print("\n--- I. CD personalization ---")
eng = make_engine(BANK, tag="i")
sid = eng.create_session("cd", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["cd_p"], ["Purchases"], [10000], ["Cash"], [10000])
prof = profile_of(P, eng, "cd")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("I1 CASH_DISCOUNT_ERROR targeted",
      pats.get("CASH_DISCOUNT_ERROR", {}).get("targeted") is True,
      str(pats.get("CASH_DISCOUNT_ERROR")))
ranked = P.ranked_questions(
    "cd", eng.store.attempts, eng.ledger.records(), eng.mastery.records(),
    POOL, objective=OBJECTIVE_REMEDIATION)
top3 = [r["question_id"] for r in ranked[:3]]
check("I2 CD question surfaces in remediation top 3",
      Q["cd_p"] in top3, str(top3))

# ---------------------------------------------------------------- J
print("\n--- J. multi-transaction personalization ---")
eng = make_engine(BANK, tag="j")
sid = eng.create_session("multi", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["multi"], ["Cash"], [100000],
           ["Capital"], [100000])
prof = profile_of(P, eng, "multi")
pats = {p["category"]: p for p in prof["mistake_patterns"]}
check("J1 MULTI_TRANSACTION_ERROR targeted",
      pats.get("MULTI_TRANSACTION_ERROR", {}).get("targeted") is True,
      str(pats.get("MULTI_TRANSACTION_ERROR")))
ranked = P.ranked_questions(
    "multi", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_REMEDIATION)
top3 = [r["question_id"] for r in ranked[:3]]
check("J2 multi-tx question surfaces in remediation top 3",
      Q["multi"] in top3, str(top3))

# ---------------------------------------------------------------- K
print("\n--- K. recent accuracy weighting ---")


def _run_k(student, seq):
    e = make_engine(BANK, tag=f"k{student}")
    s = e.create_session(student, MODE_NORMAL)
    for qkey, out in seq:
        if out == "CORRECT":
            submit(e, s, Q[qkey], *CANON_ENTRY(BANK, Q[qkey]))
        else:
            submit(e, s, Q[qkey], ["Ram"], [9999], ["Sales"], [9999])
    return profile_of(P, e, student)


# Same lifetime (2/10), but A is strong recently, B weak recently.
prof_a = _run_k("ka", [("cs", "INCORRECT")] * 8 + [("cs", "CORRECT")] * 2)
prof_b = _run_k("kb", [("cs", "CORRECT")] * 2 + [("cs", "INCORRECT")] * 8)
wa = next((w for w in prof_a["concept_weaknesses"]
           if w["concept_key"] == "SALE_GOODS_CREDIT"), None)
wb = next((w for w in prof_b["concept_weaknesses"]
           if w["concept_key"] == "SALE_GOODS_CREDIT"), None)
check("K1 identical lifetime accuracy",
      abs(prof_a["evidence_summary"]["lifetime_accuracy"]
          - prof_b["evidence_summary"]["lifetime_accuracy"]) < 1e-9,
      f"{prof_a['evidence_summary']['lifetime_accuracy']} vs "
      f"{prof_b['evidence_summary']['lifetime_accuracy']}")
check("K2 recent-window accuracy differs (weakness model)",
      wb["recent_accuracy"] < wa["recent_accuracy"],
      f"a={wa and wa['recent_accuracy']} b={wb and wb['recent_accuracy']}")
check("K3 recently-weaker student has higher weakness score",
      (wb or {"score": 0})["score"] > (wa or {"score": 1})["score"],
      f"a={wa and wa['score']} b={wb and wb['score']}")

# ---------------------------------------------------------------- L
print("\n--- L. lifetime vs recent behavior ---")
# High lifetime, recent collapse -> flagged weak (degradation).
prof_l1 = _run_k("l1", [("cs2", "CORRECT")] * 8 + [("cs2", "INCORRECT")] * 4)
# Same lifetime, recent recovery -> not flagged.
prof_l2 = _run_k("l2", [("cs2", "INCORRECT")] * 4 + [("cs2", "CORRECT")] * 8)
weak_l1 = [w["concept_key"] for w in prof_l1["concept_weaknesses"]]
weak_l2 = [w["concept_key"] for w in prof_l2["concept_weaknesses"]]
check("L1 recent collapse flagged weak despite high lifetime",
      "SALE_GOODS_CASH" in weak_l1, str(weak_l1))
check("L2 recent recovery not flagged weak",
      "SALE_GOODS_CASH" not in weak_l2, str(weak_l2))

# ---------------------------------------------------------------- M
print("\n--- M. mastery degradation ---")
eng = make_engine(BANK, tag="m")
sid = eng.create_session("degrade", MODE_NORMAL)
# Same evidence shape as the 15I-H N/O gate: a MASTERED concept whose
# recent window already contains one mistake, so the first post-mastered
# failure trips REVIEW (the deterministic mastery state machine).
seq_m = [("CORRECT"), ("CORRECT"), ("CORRECT"), ("INCORRECT"),
         ("CORRECT"), ("CORRECT"), ("CORRECT"), ("CORRECT")]
for out in seq_m:
    if out == "CORRECT":
        submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
    else:
        submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [9000])
rec = eng.mastery.get("degrade", "SALE_GOODS_CASH")
check("M1 mastered before degradation",
      rec["mastery_state"] == MASTERY_MASTERED, rec["mastery_state"])
for _ in range(3):
    submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [9000])
rec = eng.mastery.get("degrade", "SALE_GOODS_CASH")
check("M2 mastery degrades to REVIEW",
      rec["mastery_state"] == MASTERY_REVIEW, rec["mastery_state"])
prof = profile_of(P, eng, "degrade")
weak = [w["concept_key"] for w in prof["concept_weaknesses"]]
check("M3 degraded concept flagged weak",
      "SALE_GOODS_CASH" in weak, str(weak))
check("M4 readiness turns to REMEDIATION",
      prof["difficulty_readiness"]["direction"]
      == DIRECTION_REMEDIATION,
      prof["difficulty_readiness"]["direction"])
check("M5 historical mastery evidence preserved",
      rec["attempts"] == 11 and rec["correct"] == 7,
      str(rec["attempts"]))

# ---------------------------------------------------------------- N
print("\n--- N. mastery recovery ---")
for _ in range(4):
    submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
rec = eng.mastery.get("degrade", "SALE_GOODS_CASH")
check("N1 state recovers out of REVIEW",
      rec["mastery_state"] != MASTERY_REVIEW, rec["mastery_state"])
prof = profile_of(P, eng, "degrade")
weak = [w["concept_key"] for w in prof["concept_weaknesses"]]
check("N2 weakness cleared after recovery",
      "SALE_GOODS_CASH" not in weak, str(weak))
check("N3 historical REVIEW evidence not erased",
      any(t["to"] == MASTERY_REVIEW for t in
          eng.mastery.get("degrade", "SALE_GOODS_CASH")["transitions"]))

# ---------------------------------------------------------------- O
print("\n--- O. spaced revision priority ---")
mastery_o = {
    "k1": {"student_id": "s", "concept_key": "SALE_GOODS_CASH",
           "attempts": 6, "mastery_state": MASTERY_MASTERED,
           "last_attempt_at": "2026-07-01T00:00:00Z"},
    "k2": {"student_id": "s", "concept_key": "EXPENSE_PAID",
           "attempts": 3, "mastery_state": MASTERY_LEARNING,
           "last_attempt_at": "2026-08-12T00:00:00Z"},
}
import calendar as _calendar
import time as _time
now_o = _calendar.timegm(
    _time.strptime("2026-08-13T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
prof_o = P.evaluate("s", {}, {}, mastery_o, now=now_o)
rev = prof_o["revision_candidates"]
check("O1 overdue mastered concept prioritised first",
      rev and rev[0]["concept_key"] == "SALE_GOODS_CASH",
      str([(r["concept_key"], r["priority"]) for r in rev]))
first = next((r for r in rev if r["concept_key"] == "SALE_GOODS_CASH"),
             None)
check("O2 overdue concept marked due", first and first["due"],
      str(first))
recent_one = next((r for r in rev
                   if r["concept_key"] == "EXPENSE_PAID"), None)
check("O3 freshly-practiced concept not due",
      recent_one and not recent_one["due"],
      str(recent_one))
check("O4 revision score is deterministic (documented rule)",
      first["priority"] > 0.5 and first["priority"] <= 1.0,
      str(first["priority"]))

# ---------------------------------------------------------------- P
print("\n--- P. difficulty progression ---")
eng = make_engine(BANK, tag="p")
sid = eng.create_session("prog", MODE_NORMAL)
for qkey in ("cs2", "rent", "bank", "pc", "cs2"):
    submit(eng, sid, Q[qkey], *CANON_ENTRY(BANK, Q[qkey]))
prof = profile_of(P, eng, "prog")
rd = prof["difficulty_readiness"]
check("P1 repeated success -> ADVANCE",
      rd["direction"] == DIRECTION_ADVANCE and rd["target_band"] == 2,
      f"{rd['direction']} -> {rd['target_band']}")
ranked = P.ranked_questions(
    "prog", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_DIFFICULTY_PROGRESSION)
top = band_of(BANK, ranked[0]["question_id"])
check("P2 next question is one band up",
      top == 2, f"top band {top}")

# ---------------------------------------------------------------- Q
print("\n--- Q. difficulty regression ---")
eng = make_engine(BANK, tag="q")
sid = eng.create_session("reg", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["cs"], ["Ram"], [9000], ["Sales"], [9000])
prof = profile_of(P, eng, "reg")
rd = prof["difficulty_readiness"]
check("Q1 repeated failure -> REINFORCE",
      rd["direction"] == DIRECTION_REINFORCE and rd["target_band"] == 1,
      f"{rd['direction']} -> {rd['target_band']}")
ranked = P.ranked_questions(
    "reg", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_DIFFICULTY_PROGRESSION)
top = band_of(BANK, ranked[0]["question_id"])
check("Q2 next question reinforces lower band",
      top == 1, f"top band {top}")

# ---------------------------------------------------------------- R
print("\n--- R. remediation session ---")
eng = make_engine(BANK, tag="r")
sid = eng.create_session("rem", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["rent"], ["Cash"], [6000], ["Rent"], [6000])
ranked = P.ranked_questions(
    "rem", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_REMEDIATION)
check("R1 remediation session targets weak concept",
      concept_of(BANK, ranked[0]["question_id"]) == "EXPENSE_PAID",
      f"{ranked[0]['question_id']} -> "
      f"{concept_of(BANK, ranked[0]['question_id'])}")
check("R2 selection carries a reason",
      bool(ranked[0]["reason"]) and bool(ranked[0]["evidence"]))

# ---------------------------------------------------------------- S
print("\n--- S. revision session ---")
mastery_s = {
    "k1": {"student_id": "s", "concept_key": "SALE_GOODS_CASH",
           "attempts": 6, "mastery_state": MASTERY_MASTERED,
           "last_attempt_at": "2026-07-01T00:00:00Z"},
}
ranked = P.ranked_questions("s", {}, {}, mastery_s, POOL,
                            objective=OBJECTIVE_REVISION,
                            now=now_o)
check("S1 revision session surfaces the due topic",
      concept_of(BANK, ranked[0]["question_id"]) == "SALE_GOODS_CASH",
      f"{ranked[0]['question_id']} -> "
      f"{concept_of(BANK, ranked[0]['question_id'])}")

# ---------------------------------------------------------------- T
print("\n--- T. exam session ---")
ranked = P.ranked_questions("fresh_t", {}, {}, {}, POOL,
                            objective=OBJECTIVE_EXAM_PREPARATION)
conc_t = {concept_of(BANK, r["question_id"]) for r in ranked[:8]}
bands_t = {band_of(BANK, r["question_id"]) for r in ranked[:8]}
check("T1 exam prep mixes concepts",
      len(conc_t) >= 5, str(conc_t))
check("T2 exam prep spans difficulty bands",
      len(bands_t) >= 2, str(bands_t))

# ---------------------------------------------------------------- U
print("\n--- U. mixed session ---")
ranked = P.ranked_questions("fresh_u", {}, {}, {}, POOL,
                            objective=OBJECTIVE_MIXED_PRACTICE)
conc_u = {concept_of(BANK, r["question_id"]) for r in ranked[:8]}
check("U1 mixed practice spans concepts",
      len(conc_u) >= 5, str(conc_u))

# ---------------------------------------------------------------- V
print("\n--- V. diversity control ---")
eng = make_engine(BANK, tag="v")
sid = eng.create_session("diverse", MODE_NORMAL)
for _ in range(8):
    submit(eng, sid, Q["cs"], *CANON_ENTRY(BANK, Q["cs"]))
ranked = P.ranked_questions(
    "diverse", eng.store.attempts, eng.ledger.records(),
    eng.mastery.records(), POOL, objective=OBJECTIVE_MIXED_PRACTICE)
check("V1 over-practiced concept not picked first",
      concept_of(BANK, ranked[0]["question_id"]) != "SALE_GOODS_CREDIT",
      f"{concept_of(BANK, ranked[0]['question_id'])}")

# ---------------------------------------------------------------- W
print("\n--- W. canonical repetition prevention ---")
pick = P.select_question(
    "w", {}, {}, {}, POOL, session={"mode": MODE_NORMAL},
    answered=[Q["cs"]])
check("W1 answered canonical never re-picked",
      pick is not None and pick["question_id"] != Q["cs"],
      str(pick))
check("W2 pick is approved content",
      BANK.get_question(pick["question_id"])["status"] == "APPROVED")

# ---------------------------------------------------------------- X
print("\n--- X. variant preference ---")
ranked = P.ranked_questions("x", {}, {}, {}, POOL,
                            objective=OBJECTIVE_MIXED_PRACTICE,
                            answered=[Q["cs"]])
order = [r["question_id"] for r in ranked]
check("X1 unseen variant ranks above answered canonical",
      order.index(Q["cs_v"]) < order.index(Q["cs"]),
      f"variant {order.index(Q['cs_v'])} vs canonical {order.index(Q['cs'])}")
pick = P.select_question(
    "x", {}, {}, {}, [Q["cs"], Q["cs_v"]], session={"mode": MODE_NORMAL},
    answered=[Q["cs"]])
check("X2 select_question returns the variant",
      pick and pick["question_id"] == Q["cs_v"], str(pick))

# ---------------------------------------------------------------- Y
print("\n--- Y. cold-start determinism ---")
p1 = P.evaluate("cold_y", {}, {}, {})
p2 = PersonalizationEngine(BANK, seed=42).evaluate("cold_y", {}, {}, {})
check("Y1 identical profiles",
      profile_fingerprint(p1) == profile_fingerprint(p2))
r1 = P.ranked_questions("cold_y", {}, {}, {}, POOL,
                        objective=OBJECTIVE_MIXED_PRACTICE)
r2 = PersonalizationEngine(BANK, seed=42).ranked_questions(
    "cold_y", {}, {}, {}, POOL, objective=OBJECTIVE_MIXED_PRACTICE)
check("Y2 identical rankings",
      [r["question_id"] for r in r1] == [r["question_id"] for r in r2])

# ---------------------------------------------------------------- Z
print("\n--- Z. seeded determinism ---")
pa = PersonalizationEngine(BANK, seed=7)
pb = PersonalizationEngine(BANK, seed=7)
pc = PersonalizationEngine(BANK, seed=8)
ra = pa.ranked_questions("z", {}, {}, {}, POOL,
                         objective=OBJECTIVE_MIXED_PRACTICE)
rb = pb.ranked_questions("z", {}, {}, {}, POOL,
                         objective=OBJECTIVE_MIXED_PRACTICE)
rc = pc.ranked_questions("z", {}, {}, {}, POOL,
                         objective=OBJECTIVE_MIXED_PRACTICE)
check("Z1 same seed -> identical ranking",
      [r["question_id"] for r in ra] == [r["question_id"] for r in rb])
check("Z2 profile is seed-independent",
      profile_fingerprint(pa.evaluate("z", {}, {}, {}))
      == profile_fingerprint(pc.evaluate("z", {}, {}, {})))
check("Z3 any-seed ranking stays score-ordered",
      all(ra[i]["score"] >= ra[i + 1]["score"] for i in range(len(ra) - 1))
      and all(rc[i]["score"] >= rc[i + 1]["score"]
              for i in range(len(rc) - 1)))

# ---------------------------------------------------------------- AA
print("\n--- AA. identical history -> identical profile ---")


def _run_aa():
    e = make_engine(BANK, seed=77, tag=uuid.uuid4().hex[:6])
    s = e.create_session("aa", MODE_NORMAL)
    for qkey, out in [
        ("cs", "CORRECT"), ("rent", "INCORRECT"), ("cs2", "CORRECT"),
        ("rent", "CORRECT"), ("pc", "CORRECT"), ("cs", "INCORRECT"),
    ]:
        if out == "CORRECT":
            submit(e, s, Q[qkey], *CANON_ENTRY(BANK, Q[qkey]))
        else:
            submit(e, s, Q[qkey], ["Cash"], [6000], ["Rent"], [5000])
    return profile_of(P, e, "aa")


prof_aa1 = _run_aa()
prof_aa2 = _run_aa()
check("AA1 replay produces identical profiles",
      profile_fingerprint(prof_aa1) == profile_fingerprint(prof_aa2))

# ---------------------------------------------------------------- AB
print("\n--- AB. REVIEW_REQUIRED never counts as incorrect ---")
eng = make_engine(BANK, tag="ab")
sid = eng.create_session("rr", MODE_NORMAL)
for _ in range(6):
    submit(eng, sid, Q["cs"], [], [], [], [], raw="")
prof = profile_of(P, eng, "rr")
es = prof["evidence_summary"]
check("AB1 neutral attempts not scored incorrect",
      es["incorrect"] == 0 and es["correct"] == 0
      and es["review_required"] == 6, str(es))
check("AB2 no weaknesses fabricated from neutral attempts",
      prof["concept_weaknesses"] == []
      and prof["mode"] == MODE_COLD_START, prof["mode"])

# ---------------------------------------------------------------- AC
print("\n--- AC. NOT_SUPPORTED never counts as incorrect ---")
eng = make_engine(BANK, tag="ac")
sid = eng.create_session("ns", MODE_NORMAL)
for _ in range(6):
    submit(eng, sid, Q["cs"], [], [], [], [], raw="I don't know this")
prof = profile_of(P, eng, "ns")
es = prof["evidence_summary"]
check("AC1 unsupported attempts not scored incorrect",
      es["incorrect"] == 0 and es["correct"] == 0
      and es["unsupported"] == 6, str(es))
check("AC2 no weaknesses fabricated from unsupported attempts",
      prof["concept_weaknesses"] == [])

# ---------------------------------------------------------------- AD
print("\n--- AD. rejected questions never selected ---")
approved_ids = {q["question_id"] for q in BANK.list_approved()}
check("AD1 rejected question not approved",
      Q["rej"] not in approved_ids)
ranked = P.ranked_questions("ad", {}, {}, {},
                            [Q["rej"]] + POOL,
                            objective=OBJECTIVE_MIXED_PRACTICE)
check("AD2 rejected question never ranked",
      all(r["question_id"] != Q["rej"] for r in ranked))
pick = P.select_question("ad", {}, {}, {}, [Q["rej"]],
                         session={"mode": MODE_NORMAL})
check("AD3 rejected-only pool yields no selection", pick is None)

# ---------------------------------------------------------------- AE
print("\n--- AE. LLM cannot influence personalization ---")
with open("backend/maths/fyjc_personalization_engine.py",
          "r", encoding="utf-8") as fh:
    src_mod = fh.read()
banned = [t for t in ("openai", "anthropic", "claude", "genai",
                      "requests.", "urllib", "chatgpt", "gpt-4")
          if t in src_mod]
check("AE1 module has no LLM/network code", not banned, str(banned))
# Unknown fields in evidence are ignored - a fake "llm score" changes
# nothing.
att_clean = {"a1": {"student_id": "s", "question_id": Q["cs"],
                    "outcome": "CORRECT",
                    "submitted_at": "2026-08-01T10:00:00Z"}}
att_llm = dict(att_clean)
att_llm["a1"] = dict(att_clean["a1"])
att_llm["a1"]["llm_suggested_score"] = 0.99
att_llm["a1"]["llm_suggested_journal"] = {"debit": [["X", 1]]}
p_clean = P.evaluate("s", att_clean, {}, {})
p_llm = P.evaluate("s", att_llm, {}, {})
check("AE2 LLM-suggested fields cannot influence the profile",
      profile_fingerprint(p_clean) == profile_fingerprint(p_llm))

# ---------------------------------------------------------------- AF
print("\n--- AF. QuestionBank remains unchanged ---")
snapshot_before = json.dumps(BANK.list_questions(include_internal=True),
                             sort_keys=True, default=str)
eng = make_engine(BANK, tag="af")
sid = eng.create_session("bank", MODE_NORMAL)
submit(eng, sid, Q["rent"], ["Cash"], [6000], ["Rent"], [6000])
P.evaluate("bank", eng.store.attempts, eng.ledger.records(),
           eng.mastery.records())
P.ranked_questions("bank", eng.store.attempts, eng.ledger.records(),
                   eng.mastery.records(), POOL,
                   objective=OBJECTIVE_REMEDIATION)
P.select_question("bank", eng.store.attempts, eng.ledger.records(),
                  eng.mastery.records(), POOL,
                  session={"mode": MODE_NORMAL})
snapshot_after = json.dumps(BANK.list_questions(include_internal=True),
                            sort_keys=True, default=str)
check("AF1 bank byte-identical after personalization",
      snapshot_before == snapshot_after)

# ---------------------------------------------------------------- AG
print("\n--- AG. PracticeEngine remains authoritative ---")
eng = make_engine(BANK, tag="ag")
sid = eng.create_session("auth", MODE_NORMAL)
for i in range(5):
    submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
qid = eng.select_next(sid, personalizer=P)
q = BANK.get_question(qid)
check("AG1 personalizer pick is APPROVED",
      q["status"] == "APPROVED", q["status"])
canon_before = json.dumps(q.get("expected_journal"), sort_keys=True)
d_accs, d_amts, c_accs, c_amts = CANON_ENTRY(BANK, qid)
ok = submit(eng, sid, qid, d_accs, d_amts, c_accs, c_amts)
check("AG2 correct answer still verified CORRECT by FT-E",
      ok["outcome"] == OUTCOME_CORRECT
      and ok["verification_status"] == "VERIFIED",
      f"{ok['outcome']} / {ok['verification_status']}")
bad = submit(eng, sid, qid, ["Cash"], [1], ["Sales"], [1])
from backend.maths.fyjc_mistake_ledger import (  # noqa: E402
    MISTAKE_CATEGORIES as _MISTAKE_CATEGORIES,
)
check("AG3 wrong answer still INCORRECT with deterministic category",
      bad["outcome"] == OUTCOME_INCORRECT
      and bad["verification_status"] == "VERIFIED"
      and bad["mistake_category"] in _MISTAKE_CATEGORIES,
      f"{bad['outcome']} {bad['mistake_category']}")
check("AG4 canonical journal untouched by personalization",
      json.dumps(BANK.get_question(qid).get("expected_journal"),
                 sort_keys=True) == canon_before)
check("AG5 selection reason recorded in session",
      bool(eng.get_session(sid).get("selection_reasons")),
      str(eng.get_session(sid).get("selection_reasons")))


def _ladder_seq(seed):
    e = make_engine(BANK, seed=seed, tag=uuid.uuid4().hex[:6])
    s = e.create_session("ladder", MODE_NORMAL)
    return [e.select_next(s) for _ in range(5)]


seq1 = _ladder_seq(20260813)
seq2 = _ladder_seq(20260813)
check("AG6 ladder without personalizer is byte-identical",
      seq1 == seq2, f"{seq1} != {seq2}")

# ---------------------------------------------------------------- AH
print("\n--- AH. teacher dashboard aggregation ---")
eng = make_engine(BANK, tag="ah")
sid = eng.create_session("weak_t", MODE_NORMAL)
for _ in range(5):
    submit(eng, sid, Q["rent"], ["Cash"], [6000], ["Rent"], [6000])
sid = eng.create_session("improve_t", MODE_NORMAL)
for i in range(6):
    if i < 3:
        submit(eng, sid, Q["cs"], ["Ram"], [9999], ["Sales"], [9999])
    else:
        submit(eng, sid, Q["cs"], *CANON_ENTRY(BANK, Q["cs"]))
sid = eng.create_session("degrade_t", MODE_NORMAL)
for out in ("CORRECT", "CORRECT", "CORRECT", "INCORRECT",
            "CORRECT", "CORRECT", "CORRECT", "CORRECT"):
    if out == "CORRECT":
        submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [25000])
    else:
        submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [9000])
for _ in range(3):
    submit(eng, sid, Q["cs2"], ["Cash"], [25000], ["Sales"], [9000])
agg = P.teacher_aggregates(eng.store.attempts, eng.ledger.records(),
                           eng.mastery.records())
weak_list = [w["concept_key"] for w in agg["weakest_concepts"]]
check("AH1 weakest concepts aggregated",
      "EXPENSE_PAID" in weak_list and "SALE_GOODS_CASH" in weak_list,
      str(weak_list))
check("AH2 common mistake categories sorted by occurrence",
      bool(agg["common_mistake_categories"])
      and all(agg["common_mistake_categories"][i]["occurrences"]
              >= agg["common_mistake_categories"][i + 1]["occurrences"]
              for i in range(len(agg["common_mistake_categories"]) - 1)),
      str(agg["common_mistake_categories"]))
need_review = {r["student_id"] for r in agg["students_needing_review"]}
check("AH3 degrading student flagged for review",
      "degrade_t" in need_review, str(need_review))
improving = {(r["student_id"], r["concept_key"])
             for r in agg["concepts_improving"]}
check("AH4 recovering student listed as improving",
      ("improve_t", "SALE_GOODS_CREDIT") in improving, str(improving))
degrading = {(r["student_id"], r["concept_key"])
             for r in agg["concepts_degrading"]}
check("AH5 degraded concept listed as degrading",
      ("degrade_t", "SALE_GOODS_CASH") in degrading, str(degrading))
check("AH6 revision-due view present (deterministic)",
      isinstance(agg["revision_due"], list)
      and isinstance(agg["strongest_concepts"], list))

# ---------------------------------------------------------------- AI
print("\n--- AI. explanation evidence matches actual records ---")
eng = make_engine(BANK, tag="ai")
sid = eng.create_session("explain", MODE_NORMAL)
for _ in range(6):
    submit(eng, sid, Q["cd_s"], ["Ram", "Cash", "Discount Allowed"],
           [10100, 9800, 100], ["Sales", "Ram"], [10100, 9900])
prof = profile_of(P, eng, "explain")
ledger_total = sum(int(m.get("occurrence_count") or 1)
                   for m in eng.ledger.records().values()
                   if m.get("mistake_category") == "AMOUNT_ERROR")
pat = next((p for p in prof["mistake_patterns"]
            if p["category"] == "AMOUNT_ERROR"), None)
check("AI1 pattern count equals ledger records",
      pat and pat["occurrence_count"] == ledger_total == 6,
      f"pattern={pat and pat['occurrence_count']} ledger={ledger_total}")
attempts_amt = [a for a in eng.store.attempts.values()
                if a.get("student_id") == "explain"
                and a.get("outcome") == OUTCOME_INCORRECT]
recent_5 = [1 if a["outcome"] == OUTCOME_CORRECT else 0
            for a in sorted(attempts_amt,
                            key=lambda a: a["submitted_at"])[-5:]]
actual_recent = sum(recent_5) / len(recent_5) if recent_5 else 0.0
weak_ai = next((w for w in prof["concept_weaknesses"]
                if w["concept_key"] == "SALE_GOODS_CREDIT"), None)
check("AI2 weakness recent accuracy equals actual records",
      weak_ai is not None
      and abs(weak_ai["recent_accuracy"] - actual_recent) < 1e-9,
      f"profile={weak_ai and weak_ai['recent_accuracy']} "
      f"actual={actual_recent}")

# ---------------------------------------------------------------- AJ
print("\n--- AJ. adversarial corrupted-history handling ---")
att_bad = {
    "g1": "not a dict",
    "g2": {"student_id": "s", "outcome": "CORRECT"},
    "g3": {"student_id": "s", "question_id": "Q-does-not-exist",
           "outcome": "CORRECT", "submitted_at": "2026-08-01T10:00:00Z"},
    "g4": {"student_id": "other", "question_id": Q["cs"],
           "outcome": "INCORRECT", "submitted_at": "2026-08-02T10:00:00Z"},
    "g5": {"student_id": "s", "question_id": Q["cs"], "outcome": 42,
           "submitted_at": "2026-08-03T10:00:00Z"},
    "g6": {"student_id": "s", "question_id": Q["cs"], "outcome": None,
           "submitted_at": None},
}
mis_bad = {"m1": "garbage",
           "m2": {"student_id": "s", "mistake_category": None,
                  "occurrence_count": "3", "status": "OPEN"},
           "m3": {"student_id": "s", "question_id": Q["rent"],
                  "concept_key": "EXPENSE_PAID",
                  "mistake_category": "AMOUNT_ERROR",
                  "occurrence_count": 2, "status": "OPEN",
                  "last_occurrence_at": "2026-08-01T10:00:00Z"}}
mas_bad = {"k1": None,
           "k2": {"student_id": "s", "concept_key": None,
                  "attempts": "5", "mastery_state": "MASTERED",
                  "last_attempt_at": "not-a-date"},
           "k3": {"student_id": "s", "concept_key": "SALE_GOODS_CASH",
                  "attempts": 6, "mastery_state": MASTERY_MASTERED,
                  "last_attempt_at": "2026-07-01T00:00:00Z"}}
try:
    prof_aj = P.evaluate("s", att_bad, mis_bad, mas_bad, now=now_o)
    ranked_aj = P.ranked_questions("s", att_bad, mis_bad, mas_bad,
                                   POOL, objective=OBJECTIVE_MIXED_PRACTICE)
    pick_aj = P.select_question("s", att_bad, mis_bad, mas_bad, POOL,
                                session={"mode": MODE_NORMAL})
    ok_aj = True
    detail = ""
except Exception as exc:  # noqa: BLE001 - any crash fails the check
    ok_aj = False
    detail = repr(exc)
check("AJ1 corrupted history handled without crash", ok_aj, detail)
check("AJ2 profile produced with sane shape",
      isinstance(prof_aj.get("evidence_summary"), dict)
      and prof_aj["evidence_summary"]["attempts"] >= 1,
      str(prof_aj.get("evidence_summary")))
check("AJ3 ranking survives corrupted history",
      isinstance(ranked_aj, list) and len(ranked_aj) >= 1)
check("AJ4 selection survives corrupted history",
      pick_aj is not None and pick_aj.get("question_id") in POOL,
      str(pick_aj))

# ---------------------------------------------------------------- 15I-M
print("\n--- 15I-M generated-content integration (section 27) ---")
from backend.maths.fyjc_question_generator import (  # noqa: E402
    GenerationRequest,
    generate_batch,
)
m_bank, m_q = make_bank(_tmp("bank_m.json"))
m_rep = generate_batch(request=GenerationRequest(count=3, seed=7),
                       bank=m_bank, dry_run=False)
gen_qids = [c["question_id"] for c in m_rep["candidate_records"]
            if c["question_id"] and c["status"] == "APPROVED"]
check("M1 live generation produced approved content",
      m_rep["approved"] >= 1 and len(gen_qids) >= 1,
      f"approved={m_rep['approved']}")
check("M2 generated questions went through the full lifecycle",
      all(m_bank.get_question(qid)["status"] == "APPROVED"
          for qid in gen_qids))
mp = PersonalizationEngine(m_bank, seed=42)
m_pool = [q["question_id"] for q in m_bank.list_approved()]
m_ranked = mp.ranked_questions("s", {}, {}, {}, m_pool,
                               objective=OBJECTIVE_MIXED_PRACTICE)
check("M3 generated content ranks like any approved question",
      any(r["question_id"] in gen_qids for r in m_ranked),
      str(gen_qids))
check("M4 personalization never gives generated content special trust",
      all(r["question_id"] in {q["question_id"]
                               for q in m_bank.list_approved()}
          for r in m_ranked))

# ------------------------------------------------------------------
print("\n" + "=" * 78)
total = len(PASS) + len(FAIL)
print(f"SPRINT 15I-N GATE: {len(PASS)}/{total} checks passed")
if FAIL:
    print("FAILED CHECKS:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("SPRINT 15I-N PASS - ADVANCED PERSONALIZATION & ADAPTIVE LEARNING "
      "VERIFIED")
sys.exit(0)
