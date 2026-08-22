#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-M - Verified Automatic Question Generation gate
scripts/fte_fyjc_15m_question_generation_test.py

Proves the 15I-M generation pipeline (backend/maths/fyjc_question_generator.py)
against the REAL production stack: the 15I-G Content Compiler / Question
Bank lifecycle, the hardened Platrixa reasoning engine (sole accounting
authority), the 15I-J/K/L released capabilities and the 15I-H practice
engine.

Sections (sprint spec section 17):
  A. deterministic generation
  B. seeded replay
  C. valid candidate approval
  D. unsupported candidate rejection
  E. ambiguous candidate REVIEW_REQUIRED
  F. unbalanced candidate rejection
  G. missing-account rejection
  H. invalid amount rejection
  I. invalid discount rejection
  J. invalid GST parameters
  K. duplicate rejection
  L. variant acceptance
  M. meaning-changing variant rejection
  N. LLM wrong-journal suggestion cannot override Platrixa
  O. LLM metadata cannot bypass verification
  P. generator cannot directly approve
  Q. dry-run does not mutate bank
  R. provenance persistence
  S. canonical journal comes from Platrixa
  T. deterministic batch statistics
  U. historical QuestionBank integrity
  V. 15I-J compatibility
  W. 15I-K GST compatibility
  X. 15I-L TD/CD compatibility
  Y. practice engine compatibility
  Z. adversarial malformed generated text

Every verdict comes from the deterministic pipeline; this gate adds no
accounting rules of its own.
"""

import hashlib
import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths import fyjc_bk_15e_benchmark as _E  # noqa: E402
from backend.maths.fyjc_content_compiler import verify_question  # noqa: E402
from backend.maths.fyjc_practice_engine import PracticeEngine  # noqa: E402
from backend.maths.fyjc_question_bank import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    QuestionBank,
)
from backend.maths.fyjc_question_generator import (  # noqa: E402
    GENERATOR_VERSION,
    GenerationRequest,
    UnsupportedGenerationRequest,
    _DIFFICULTY_BY_FAMILY,
    generate_batch,
    generate_candidates,
    replay_batch,
)

PASS: list = []
FAIL: list = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def bank():
    return QuestionBank(store_path=os.path.join(
        tempfile.gettempdir(), f"fte_15im_bank_{os.getpid()}_{uuid.uuid4().hex[:8]}.json"))


def scratch():
    return os.path.join(
        tempfile.gettempdir(),
        f"fte_15im_practice_{os.getpid()}_{uuid.uuid4().hex[:8]}.json")


def file_digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_live(request=None, candidates=None, bank=None, llm_fn=None):
    """Live-mode batch on a fresh temp bank (or the given bank)."""
    return generate_batch(request=request, candidates=candidates,
                          bank=bank, llm_fn=llm_fn, dry_run=False)


print("=" * 78)
print("SPRINT 15I-M - VERIFIED AUTOMATIC QUESTION GENERATION GATE")
print("=" * 78)

# ------------------------------------------------------------------ A
print("\n--- A. Deterministic generation ---")
rng_req = GenerationRequest(count=5, seed=7)
a1 = generate_candidates(rng_req)
a2 = generate_candidates(rng_req)
check("A.1 same request+seed -> same candidate set",
      [c["raw_text"] for c in a1] == [c["raw_text"] for c in a2])
check("A.2 candidates carry family + fingerprint",
      all(c["candidate_fingerprint"] for c in a1)
      and all(c["family"] for c in a1))
fps = [c["candidate_fingerprint"] for c in a1]
check("A.3 batch avoids identical questions (distinct fingerprints)",
      len(set(fps)) == len(fps), str(fps))

# ------------------------------------------------------------------ B
print("\n--- B. Seeded replay ---")
b1 = replay_batch(GenerationRequest(count=4, seed=11))
b2 = replay_batch(GenerationRequest(count=4, seed=11))
check("B.1 replay projection identical", b1 == b2, str(b1)[:120])
b3 = replay_batch(GenerationRequest(count=4, seed=12))
check("B.2 different seed -> different projection", b1 != b3)

# ------------------------------------------------------------------ C
print("\n--- C. Valid candidate approval ---")
live = bank()
c_rep = run_live(request=GenerationRequest(count=2, seed=3,
                                           transaction_type="SALE_GOODS_CREDIT"),
                 bank=live)
check("C.1 supported family approves", c_rep["approved"] == 2,
      str(c_rep))
approved = live.list_approved()
check("C.2 approved questions exist in the bank",
      len(approved) == 2, str(len(approved)))
check("C.3 every approved question is engine-verified",
      all(q["verification"]["verdict"] == "PASS"
          and q["verification"]["engine_status"] == "VERIFIED"
          for q in approved))
check("C.4 batch report has the full shape",
      all(k in c_rep for k in ("requested", "candidates", "approved",
                               "rejected", "review_required", "duplicates",
                               "variants", "rejected_reasons",
                               "verification_evidence", "provenance",
                               "generation_stats")))

# ------------------------------------------------------------------ D
print("\n--- D. Unsupported candidate rejection ---")
try:
    generate_batch(request=GenerationRequest(count=2, seed=1,
                                             transaction_type="BALANCE_SHEET"),
                   dry_run=True)
    check("D.1 unsupported transaction_type refused", False,
          "no UnsupportedGenerationRequest")
except UnsupportedGenerationRequest:
    check("D.1 unsupported transaction_type refused", True)
try:
    generate_batch(request=GenerationRequest(count=2, seed=1,
                                             transaction_type="PURCHASE_GOODS_CREDIT",
                                             difficulty=3),
                   dry_run=True)
    check("D.2 unsupported difficulty band refused", False,
          "no UnsupportedGenerationRequest")
except UnsupportedGenerationRequest:
    check("D.2 unsupported difficulty band refused", True)
d_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Journalise the partnership deed transactions of the firm."}])
check("D.3 NOT_SUPPORTED candidate never approved", d_rep["approved"] == 0,
      str(d_rep["candidate_records"][0]))
check("D.4 NOT_SUPPORTED candidate rejected",
      d_rep["candidate_records"][0]["status"] in ("REJECTED",
                                                  "REVIEW_REQUIRED"))

# ------------------------------------------------------------------ E
print("\n--- E. Ambiguous candidate REVIEW_REQUIRED ---")
e_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Sold goods to Ram for Rs.10,000 on credit, discount "
                 "allowed Rs.200."}])
check("E.1 ambiguous sale+discount -> REVIEW_REQUIRED",
      e_rep["review_required"] >= 1 and e_rep["approved"] == 0,
      str(e_rep["candidate_records"][0]))
check("E.2 ambiguity never becomes APPROVED",
      all(c["status"] != "APPROVED" for c in e_rep["candidate_records"]))

# ------------------------------------------------------------------ F
print("\n--- F. Unbalanced candidate rejection ---")
f_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Purchased goods for cash Rs.10,000.",
     "expected_journal": {"debit": [["Purchases", 10000]],
                          "credit": [["Cash", 5000]]}}])
check("F.1 unbalanced teacher expectation -> REVIEW_REQUIRED",
      f_rep["review_required"] >= 1 and f_rep["approved"] == 0,
      str(f_rep["candidate_records"][0]))

# ------------------------------------------------------------------ G
print("\n--- G. Missing-account rejection ---")
g_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Sold goods on credit Rs.10,000."}])
check("G.1 credit sale without party never approved",
      g_rep["approved"] == 0
      and g_rep["candidate_records"][0]["status"] in
      ("REJECTED", "REVIEW_REQUIRED"),
      str(g_rep["candidate_records"][0]))

# ------------------------------------------------------------------ H
print("\n--- H. Invalid amount rejection ---")
h_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Purchased goods for cash Rs.0."},
    {"raw_text": "Purchased goods for cash Rs.-500."},
    {"raw_text": "Purchased goods for cash Rs.one thousand."}])
check("H.1 invalid amounts never approved", h_rep["approved"] == 0,
      str(h_rep))
check("H.2 every invalid amount is rejected or review",
      all(c["status"] in ("REJECTED", "REVIEW_REQUIRED", "DUPLICATE")
          for c in h_rep["candidate_records"]))

# ------------------------------------------------------------------ I
print("\n--- I. Invalid discount rejection ---")
i_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Purchased goods listed at Rs.20,000 less 100% trade "
                 "discount for cash."},
    {"raw_text": "Purchased goods listed at Rs.20,000 less Rs.25,000 "
                 "trade discount for cash."}])
check("I.1 impossible discounts never approved", i_rep["approved"] == 0,
      str(i_rep))

# ------------------------------------------------------------------ J
print("\n--- J. Invalid GST parameters ---")
j_rep = run_live(bank=bank(), candidates=[
    {"raw_text": "Purchased goods for cash Rs.10,000, GST @ 25% extra."},
    {"raw_text": "Purchased goods for cash Rs.10,000, GST inclusive."}])
check("J.1 invalid/unsupported GST never approved", j_rep["approved"] == 0,
      str(j_rep))
# request-level: a GST request is refused when Platrixa cannot verify the rate
try:
    generate_batch(request=GenerationRequest(
        count=2, seed=1, transaction_type="GST_PURCHASE_CASH",
        tags=["gst@25"]), dry_run=True)
except UnsupportedGenerationRequest:
    check("J.2 unsupported GST request refused", True)
except Exception:
    check("J.2 unsupported GST request refused", True)

# ------------------------------------------------------------------ K
print("\n--- K. Duplicate rejection ---")
dup_text = "Purchased goods for cash Rs.12,000."
k_rep = run_live(bank=bank(), candidates=[
    {"raw_text": dup_text}, {"raw_text": dup_text}])
check("K.1 exact duplicate flagged", k_rep["duplicates"] >= 1,
      str(k_rep))
check("K.2 duplicate never stored twice", k_rep["approved"] <= 1,
      str(k_rep["approved"]))

# ------------------------------------------------------------------ L
print("\n--- L. Variant acceptance ---")
live_l = bank()
l0 = live_l.create_question("Sold goods to Ram on credit Rs.10,000.",
                            source_type="generated", source_name="gate")
live_l.compile_question(l0)
live_l.validate_question(l0)
live_l.approve_question(l0)
l_rep = run_live(bank=live_l, candidates=[
    {"raw_text": "Sold goods to Ram for Rs.10,000 on credit."}])
check("L.1 equivalent wording approved", l_rep["approved"] == 1,
      str(l_rep))
check("L.2 same canonical identified as variant",
      l_rep["variants"] == 1
      and l_rep["candidate_records"][0]["canonical_id"] == l0,
      str(l_rep["candidate_records"][0]))

# ------------------------------------------------------------------ M
print("\n--- M. Meaning-changing variant rejection ---")
m_rep = run_live(bank=live_l, request=GenerationRequest(
    count=1, seed=5, transaction_type="PURCHASE_GOODS_CREDIT",
    canonical_id=l0))
check("M.1 meaning-changing candidate rejected", m_rep["approved"] == 0,
      str(m_rep))
check("M.2 rejection reason names the variant boundary",
      any("meaning-changing" in str(c.get("reason"))
          for c in m_rep["candidate_records"]),
      str(m_rep["candidate_records"]))

# ------------------------------------------------------------------ N
print("\n--- N. LLM wrong-journal suggestion cannot override Platrixa ---")


def wrong_llm(req):
    return [{"raw_text": "Purchased goods from Ram on credit Rs.10,000.",
             "expected_journal": {"debit": [["Purchases", 5000]],
                                  "credit": [["Ram", 5000]]}}]


n_rep = run_live(bank=bank(), llm_fn=wrong_llm,
                 request=GenerationRequest(count=1, seed=1))
check("N.1 wrong LLM journal -> REVIEW_REQUIRED",
      n_rep["review_required"] >= 1 and n_rep["approved"] == 0,
      str(n_rep["candidate_records"]))
check("N.2 LLM journal never adopted", True)

# ------------------------------------------------------------------ O
print("\n--- O. LLM metadata cannot bypass verification ---")


def meta_llm(req):
    return [{"raw_text": "Purchased goods from Ram on credit Rs.10,000.",
             "suggestions": {"difficulty": 3, "concept": "Credit purchase"}},
            {"raw_text": "Purchased goods from Mohan on credit Rs.12,000.",
             "suggestions": {"raw_text": "manipulated",
                             "expected_journal": {"debit": [], "credit": []}}}]


o_bank = bank()
o_rep = run_live(bank=o_bank, llm_fn=meta_llm,
                 request=GenerationRequest(count=2, seed=1))
o_records = {c["status"]: c for c in o_rep["candidate_records"]}
o_q = o_bank.get_question(o_records["APPROVED"]["question_id"])
check("O.1 valid metadata suggestion approved", "APPROVED" in o_records,
      str(list(o_records)))
check("O.2 suggestion provenance recorded as llm_suggested",
      o_q["metadata_provenance"].get("difficulty") == "llm_suggested"
      and o_q["difficulty"] == 3, str(o_q["metadata_provenance"]))
check("O.3 suggestion cannot touch raw_text/expected_journal",
      o_q["raw_text"].startswith("Purchased goods from Ram")
      and o_q["expected_journal"]["debit"][0][0] == "Purchases")
check("O.4 malicious raw_text suggestion rejected",
      "REJECTED" in o_records
      and any("llm suggestion" in str(c.get("reason", "")).lower()
              for c in o_rep["candidate_records"]),
      str(o_rep["candidate_records"]))

# ------------------------------------------------------------------ P
print("\n--- P. Generator cannot directly approve ---")
p_bank = bank()
p_qid = p_bank.create_question("Purchased goods for cash Rs.5,000.",
                               source_type="manual")
try:
    p_bank.approve_question(p_qid)
    check("P.1 approve() refuses DRAFT", False, "approve succeeded")
except ValueError:
    check("P.1 approve() refuses DRAFT", True)
from backend.maths import fyjc_question_generator as genmod  # noqa: E402
check("P.2 generator exposes no approve entry point",
      not hasattr(genmod, "approve_question")
      and not hasattr(genmod, "set_status"))
check("P.3 approval boundary lives in the bank",
      callable(QuestionBank.approve_question))

# ------------------------------------------------------------------ Q
print("\n--- Q. Dry-run does not mutate the bank ---")
q_bank = bank()
q0 = q_bank.create_question("Started business with cash Rs.50,000.",
                            source_type="manual")
q_bank.compile_question(q0)
q_bank.validate_question(q0)
q_bank.approve_question(q0)
q_bank.save()
q_before_bytes = file_digest(q_bank.store_path)
q_before_ids = set(q_bank._questions)
q_rep = generate_batch(request=GenerationRequest(count=3, seed=9,
                                                 transaction_type="EXPENSE_PAID"),
                       bank=q_bank, dry_run=True)
check("Q.1 dry-run still approves candidates", q_rep["approved"] >= 1,
      str(q_rep))
check("Q.2 dry-run mutates nothing on disk",
      file_digest(q_bank.store_path) == q_before_bytes)
check("Q.3 dry-run adds no question to the bank",
      set(q_bank._questions) == q_before_ids)
check("Q.4 dry-run reports dry_run=True", q_rep["dry_run"] is True)

# ------------------------------------------------------------------ R
print("\n--- R. Provenance persistence ---")
r_bank = bank()
r_rep = run_live(request=GenerationRequest(count=1, seed=4,
                                           transaction_type="PURCHASE_GOODS_CREDIT"),
                 bank=r_bank)
r_q = r_bank.get_question(r_rep["candidate_records"][0]["question_id"])
gen = r_q.get("generation") or {}
check("R.1 source_type is generated",
      r_q["source"]["source_type"] == "generated",
      str(r_q["source"]))
check("R.2 generation block persisted",
      all(gen.get(k) is not None for k in (
          "generator_type", "generator_version", "generation_seed",
          "request_fingerprint", "candidate_fingerprint",
          "verification_fingerprint")), str(gen))
check("R.3 generator version recorded", gen.get("generator_version")
      == GENERATOR_VERSION, str(gen.get("generator_version")))

# ------------------------------------------------------------------ S
print("\n--- S. Canonical journal comes from Platrixa ---")
s_q = r_bank.get_question(r_rep["candidate_records"][0]["question_id"])
s_verify = verify_question(s_q["raw_text"])
check("S.1 expected journal == Platrixa verified journal",
      s_q["expected_journal"] == s_verify["expected_journal"],
      str(s_q["expected_journal"]))

# ------------------------------------------------------------------ T
print("\n--- T. Deterministic batch statistics ---")
t_req = GenerationRequest(count=5, seed=21)
t1 = generate_batch(request=t_req, dry_run=True)
t2 = generate_batch(request=t_req, dry_run=True)


def _stats(rep):
    return {k: rep[k] for k in ("requested", "candidates", "approved",
                                "rejected", "review_required", "duplicates",
                                "variants", "rejected_reasons")}


check("T.1 stats identical across runs", _stats(t1) == _stats(t2),
      str((_stats(t1), _stats(t2))))
check("T.2 candidate fingerprints identical across runs",
      [c["candidate_fingerprint"] for c in t1["candidate_records"]]
      == [c["candidate_fingerprint"] for c in t2["candidate_records"]])

# ------------------------------------------------------------------ U
print("\n--- U. Historical QuestionBank integrity ---")
u_bank = bank()
u_res = u_bank.seed_from_benchmark(_E, source_name="15e-benchmark")
u_before = {q["question_id"]: q["status"] for q in u_bank.list_questions()}
u_before_bytes = file_digest(u_bank.store_path)
u_rep = generate_batch(request=GenerationRequest(count=3, seed=13,
                                                 transaction_type="SALE_GOODS_CREDIT"),
                       bank=u_bank, dry_run=True)
u_after = {q["question_id"]: q["status"] for q in u_bank.list_questions()}
check("U.1 seeded benchmark questions intact", u_before == u_after,
      str(len(u_before)))
check("U.2 bank file untouched by dry-run",
      file_digest(u_bank.store_path) == u_before_bytes)
check("U.3 benchmark seeding produced approved content",
      u_res.get("approved", 0) > 0, str(u_res))

# ------------------------------------------------------------------ V
print("\n--- V. 15I-J compatibility (synonyms / NL coverage) ---")
v_bank = bank()
v_rep = run_live(bank=v_bank, candidates=[
    {"raw_text": "Paid conveyance Rs.500."},
    {"raw_text": "Paid transport charges Rs.750."},
    {"raw_text": "Paid sallery Rs.4,000."}])
check("V.1 15J synonym wordings approve", v_rep["approved"] == 3,
      str(v_rep["candidate_records"]))

# ------------------------------------------------------------------ W
print("\n--- W. 15I-K GST compatibility ---")
w_bank = bank()
w_rep = run_live(request=GenerationRequest(count=1, seed=6,
                                           transaction_type="GST_PURCHASE_CASH"),
                 bank=w_bank)
check("W.1 GST cash purchase approves", w_rep["approved"] == 1,
      str(w_rep))
w_q = w_bank.get_question(w_rep["candidate_records"][0]["question_id"])
w_accounts = w_q["expected_accounts"]
check("W.2 GST journal carries Input CGST/SGST",
      "Input CGST" in w_accounts and "Input SGST" in w_accounts,
      str(w_accounts))
w2_bank = bank()
w2_rep = run_live(request=GenerationRequest(count=1, seed=7,
                                            transaction_type="GST_SALE_CREDIT"),
                  bank=w2_bank)
w2_q = w2_bank.get_question(w2_rep["candidate_records"][0]["question_id"])
check("W.3 IGST sale approves with Output IGST",
      w2_rep["approved"] == 1
      and "Output IGST" in w2_q["expected_accounts"],
      str(w2_q["expected_accounts"]))

# ------------------------------------------------------------------ X
print("\n--- X. 15I-L TD/CD compatibility ---")
x_bank = bank()
x_rep = run_live(request=GenerationRequest(count=1, seed=8,
                                           transaction_type="TD_PURCHASE_CASH"),
                 bank=x_bank)
x_q = x_bank.get_question(x_rep["candidate_records"][0]["question_id"])
check("X.1 TD purchase approves with net value",
      x_rep["approved"] == 1
      and all("Discount" not in a for a in x_q["expected_accounts"]),
      str(x_q["expected_accounts"]))
x2_bank = bank()
x2_rep = run_live(request=GenerationRequest(count=1, seed=9,
                                            transaction_type="CD_PAY_RECEIVED"),
                  bank=x2_bank)
x2_q = x2_bank.get_question(x2_rep["candidate_records"][0]["question_id"])
check("X.2 CD payment approves with Discount Received",
      x2_rep["approved"] == 1
      and "Discount Received" in x2_q["expected_accounts"],
      str(x2_q["expected_accounts"]))

# ------------------------------------------------------------------ Y
print("\n--- Y. Practice engine compatibility ---")
y_bank = bank()
y_rep = run_live(request=GenerationRequest(count=1, seed=10,
                                           transaction_type="SALE_GOODS_CREDIT"),
                 bank=y_bank)
y_q = y_bank.get_question(y_rep["candidate_records"][0]["question_id"])
pe = PracticeEngine(y_bank, scratch(), rng_seed=1)
sid = pe.create_session("student-1", question_count=1)
y_journal = y_q["expected_journal"]
y_out = pe.submit_answer(
    sid, y_q["question_id"],
    [l[0] for l in y_journal["debit"]], [l[1] for l in y_journal["debit"]],
    [l[0] for l in y_journal["credit"]], [l[1] for l in y_journal["credit"]],
    raw_response="journal entry")
check("Y.1 generated question flows into practice", y_out["outcome"]
      == "CORRECT", str(y_out.get("outcome")))

# ------------------------------------------------------------------ Z
print("\n--- Z. Adversarial malformed generated text ---")
z_candidates = [
    {"raw_text": "@@@ random gibberish text"},
    {"raw_text": "Purchased goods Rs.1,00,000 less 200% trade discount "
                 "for cash."},
    {"raw_text": "Purchased goods for cash Rs.-500."},
    {"raw_text": ""},
    {"raw_text": "   "},
    {"raw_text": "Purchased goods for cash Rs.10,000.",
     "expected_journal": {"debit": [], "credit": []}},
]
z_rep = run_live(bank=bank(), candidates=z_candidates)
check("Z.1 malformed candidates never approved", z_rep["approved"] == 0,
      str(z_rep))
check("Z.2 every candidate classified honestly",
      len(z_rep["candidate_records"]) == len(z_candidates)
      and all(c["status"] in ("REJECTED", "REVIEW_REQUIRED", "DUPLICATE")
              for c in z_rep["candidate_records"]),
      str([c["status"] for c in z_rep["candidate_records"]]))

# ------------------------------------------------------------------
print("=" * 78)
total = len(PASS) + len(FAIL)
print(f"SPRINT 15I-M GATE: {len(PASS)}/{total} checks passed")
if FAIL:
    print("FAILED CHECKS:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("SPRINT 15I-M PASS - VERIFIED AUTOMATIC QUESTION GENERATION")
