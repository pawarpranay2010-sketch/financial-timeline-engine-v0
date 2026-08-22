#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-G - Content Compiler / Verified Question Bank gate
scripts/fte_fyjc_15g_content_test.py

Drives the REAL 15I-G modules (backend/maths/fyjc_content_compiler.py +
backend/maths/fyjc_question_bank.py) through the REAL Platrixa engine. Tests
A-R from the sprint spec:

  A. Valid question ingestion
  B. Invalid question rejection
  C. REVIEW_REQUIRED question rejection
  D. Unbalanced journal rejection
  E. Missing-account rejection
  F. Provenance preservation
  G. Raw-text preservation
  H. Metadata editing
  I. Approved-question retrieval
  J. Chapter filtering
  K. Concept filtering
  L. Difficulty filtering
  M. Transaction-type filtering
  N. Duplicate detection
  O. Variant linkage
  P. LLM-suggested metadata cannot bypass verification
  Q. Deterministic verification remains authoritative
  R. Existing 15E-15I behavior remains unchanged

Pure deterministic gate: no AI, no network. Uses an in-memory bank (no
store file written). Exit 0 = all checks pass.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "backend")

from backend.maths.fyjc_content_compiler import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_COMPILED,
    STATUS_DRAFT,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_VALIDATING,
    UNKNOWN,
    _journal_total,
    journal_lines_from_engine,
    normalize_question_text,
    question_fingerprint,
    verify_question,
)
from backend.maths.fyjc_question_bank import (  # noqa: E402
    QuestionBank,
)
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    reason_bk_question,
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


def bank():
    return QuestionBank(store_path=os.path.join(
        tempfile.gettempdir(), "fte_15ig_bank_test.json"))


def pipeline(b, raw, **kwargs):
    """create -> compile -> validate -> (approve when possible)."""
    qid = b.create_question(raw, **kwargs)
    b.compile_question(qid)
    b.validate_question(qid)
    try:
        b.approve_question(qid)
    except ValueError:
        pass
    return qid


print("=" * 78)
print("SPRINT 15I-G - CONTENT COMPILER / QUESTION BANK GATE")
print("=" * 78)

# ------------------------------------------------------------------ A
print("\n--- A. Valid question ingestion ---")
b = bank()
qid = pipeline(
    b, "Sold goods to Ram on credit ₹12,000.",
    source_type="teacher_authored", source_name="Teacher Demo")
q = b.get_question(qid)
check("A1 create returns DRAFT->APPROVED lifecycle", q["status"] == STATUS_APPROVED,
      q["status"])
check("A2 expected journal Ram Dr / Sales Cr",
      q["expected_journal"] == {"debit": [["Ram", 12000]],
                                "credit": [["Sales", 12000]]},
      str(q["expected_journal"]))
check("A3 transaction_count == 1", q["transaction_count"] == 1)
check("A4 transaction_types == SALE_GOODS_CREDIT",
      q["transaction_types"] == ["SALE_GOODS_CREDIT"],
      str(q["transaction_types"]))
check("A5 concept detected", q["concept"] == "Credit sale", q["concept"])
check("A6 balanced totals", q["verification"]["balanced"] is True)

# ------------------------------------------------------------------ B
print("\n--- B. Invalid question rejection ---")
b2 = bank()
bad = pipeline(
    b2, "Prepare Trading and Profit and Loss Account for the year ended "
        "31 March.",
    source_type="manual")
check("B1 NOT_SUPPORTED question -> REJECTED",
      b2.get_question(bad)["status"] == STATUS_REJECTED,
      b2.get_question(bad)["status"])
check("B2 validation errors recorded",
      len(b2.get_question(bad)["validation_errors"]) > 0)
check("B3 rejected material never approved",
      bad not in [q["question_id"] for q in b2.list_approved()])

# ------------------------------------------------------------------ C
print("\n--- C. REVIEW_REQUIRED question rejection ---")
b3 = bank()
rr = pipeline(
    b3, "Paid Rs.5,000.",
    source_type="student_typed")
check("C1 ambiguous question -> REVIEW_REQUIRED",
      b3.get_question(rr)["status"] == STATUS_REVIEW_REQUIRED,
      b3.get_question(rr)["status"])
check("C2 never approved",
      rr not in [q["question_id"] for q in b3.list_approved()])

# ------------------------------------------------------------------ D
print("\n--- D. Unbalanced journal rejection ---")
# D1: the invariant math itself refuses an unbalanced projection.
j = {"debit_lines": [{"account": "Ram", "amount": "12000"}],
     "credit_lines": [{"account": "Sales", "amount": "8000"}]}
lines = journal_lines_from_engine(j)
check("D1 invariant detects Dr != Cr",
      _journal_total(lines["debit"]) != _journal_total(lines["credit"]),
      f"{_journal_total(lines['debit'])} vs {_journal_total(lines['credit'])}")
# D2: a candidate-supplied unbalanced expected journal is never approved.
b4 = bank()
qid4 = pipeline(
    b4, "Sold goods to Ram ₹12,000.",
    source_type="teacher_authored",
    expected={"debit": [["Ram", 12000]], "credit": [["Sales", 8000]]})
q4 = b4.get_question(qid4)
check("D2 unbalanced teacher expectation -> not APPROVED",
      q4["status"] != STATUS_APPROVED, q4["status"])
check("D3 disagreement surfaced in validation_errors",
      any("disagrees" in e for e in q4["validation_errors"]),
      str(q4["validation_errors"]))

# ------------------------------------------------------------------ E
print("\n--- E. Missing-account rejection ---")
# E1: engine refuses a subject-less candidate (no accounts resolvable).
vr = verify_question("Was paid ₹5,000.")
check("E1 verification fails for unresolved candidate",
      vr["verdict"] != "PASS", str(vr["errors"][:2]))
check("E2 no accounts resolved flagged",
      vr["accounts_resolved"] is False)
# E3: bank-level rejection of the same candidate.
b5 = bank()
eid = pipeline(b5, "Was paid ₹5,000.", source_type="ocr")
check("E3 unresolved question -> REJECTED",
      b5.get_question(eid)["status"] == STATUS_REJECTED,
      b5.get_question(eid)["status"])

# ------------------------------------------------------------------ F
print("\n--- F. Provenance preservation ---")
b6 = bank()
qid6 = pipeline(
    b6, "Bought machinery from Amar on credit Rs.1,50,000.",
    source_type="previous_year_paper", source_name="Maharashtra FYJC",
    source_reference="Q.3(b)")
src = b6.get_question(qid6)["source"]
check("F1 source_type preserved", src["source_type"] == "previous_year_paper",
      src["source_type"])
check("F2 source_name preserved", src["source_name"] == "Maharashtra FYJC")
check("F3 source_reference preserved", src["source_reference"] == "Q.3(b)")
check("F4 ingestion_timestamp present", bool(src["ingestion_timestamp"]))
check("F5 compiler_version present", src["compiler_version"] == "15I-G-1")
check("F6 verification_version present",
      src["verification_version"] == "15I-G-1")

# ------------------------------------------------------------------ G
print("\n--- G. Raw-text preservation ---")
raw_g = "  Sold  goods   to   Ram on credit ₹12,000.  "
b7 = bank()
qid7 = pipeline(b7, raw_g, source_type="manual")
q7 = b7.get_question(qid7)
check("G1 raw_text byte-identical", q7["raw_text"] == raw_g,
      repr(q7["raw_text"]))
check("G2 normalized differs (whitespace collapsed)",
      q7["normalized_text"] ==
      "Sold goods to Ram on credit Rs.12,000.",
      repr(q7["normalized_text"]))

# ------------------------------------------------------------------ H
print("\n--- H. Metadata editing ---")
b8 = bank()
qid8 = pipeline(
    b8, "Purchased goods from Rahul on credit Rs.10,000.",
    source_type="teacher_authored")
b8.set_metadata(qid8, {"chapter": "Ch.3 Journal", "difficulty": 2,
                       "board": "Maharashtra State Board"})
q8 = b8.get_question(qid8)
check("H1 teacher edit applied", q8["chapter"] == "Ch.3 Journal"
      and q8["difficulty"] == 2)
check("H2 teacher provenance recorded",
      q8["metadata_provenance"].get("chapter") == "teacher",
      str(q8["metadata_provenance"]))
check("H3 status preserved through metadata-only edit",
      q8["status"] == STATUS_APPROVED, q8["status"])
try:
    b8.set_metadata(qid8, {"raw_text": "hijacked"})
    check("H4 text edit blocked", False, "no ValueError raised")
except ValueError:
    check("H4 text edit blocked", True)
try:
    b8.set_metadata(qid8, {"expected_journal": {}})
    check("H5 verified-journal edit blocked", False, "no ValueError raised")
except ValueError:
    check("H5 verified-journal edit blocked", True)

# ------------------------------------------------------------------ I
print("\n--- I. Approved-question retrieval ---")
b9 = bank()
pipeline(b9, "Sold goods to Kavita for cash Rs.15,000.",
         source_type="manual")
pipeline(b9, "Was paid ₹5,000.", source_type="manual")  # rejected
approved = b9.list_approved()
check("I1 list_approved only APPROVED", approved
      and all(q["status"] == STATUS_APPROVED for q in approved))
check("I2 rejected question absent from approved",
      all(q["raw_text"] != "Was paid ₹5,000." for q in approved))

# ------------------------------------------------------------------ J
print("\n--- J. Chapter filtering ---")
b10 = bank()
pipeline(b10, "Journalise the following transactions: Purchased goods "
              "for cash Rs.10,000; sold goods to Anil on credit "
              "Rs.15,000.", source_type="previous_year_paper")
pipeline(b10, "Sold goods to Meena for cash Rs.12,000.",
         source_type="manual")
ch3 = b10.filter_by_chapter("Ch.3 Journal")
check("J1 journal wording -> Ch.3", any(
    "Journalise" in q["raw_text"] for q in ch3))
check("J2 chapter filter excludes others",
      all(q["chapter"] == "Ch.3 Journal" for q in ch3))

# ------------------------------------------------------------------ K
print("\n--- K. Concept filtering ---")
b11 = bank()
pipeline(b11, "Sold goods to Ram on credit ₹12,000.", source_type="manual")
pipeline(b11, "Bought goods from Rahul on credit Rs.10,000.",
         source_type="manual")
credit_sales = b11.filter_by_concept("Credit sale")
check("K1 concept filter finds credit sale",
      any("Ram" in q["raw_text"] for q in credit_sales))
check("K2 concept filter excludes purchase",
      all(q["concept"] == "Credit sale" for q in credit_sales))

# ------------------------------------------------------------------ L
print("\n--- L. Difficulty filtering ---")
b12 = bank()
q12 = pipeline(b12, "Started business with cash Rs.1,00,000.",
               source_type="manual")
b12.set_metadata(q12, {"difficulty": 1})
pipeline(b12, "Purchased goods from Rahim Rs.20,000 at 10% trade "
              "discount; paid half immediately; paid the balance after "
              "a month.", source_type="manual")
b12.set_metadata(b12.list_questions()[-1]["question_id"],
                 {"difficulty": 3})
easy = b12.filter_by_difficulty(1)
hard = b12.filter_by_difficulty(3)
check("L1 difficulty=1 filter", len(easy) == 1
      and easy[0]["question_id"] == q12)
check("L2 difficulty=3 filter", len(hard) == 1)

# ------------------------------------------------------------------ M
print("\n--- M. Transaction-type filtering ---")
b13 = bank()
pipeline(b13, "Sold goods to Ram on credit ₹12,000.", source_type="manual")
pipeline(b13, "Sold goods for cash Rs.25,000.", source_type="manual")
credit = b13.filter_by_transaction_type("SALE_GOODS_CREDIT")
check("M1 type filter credit sale",
      any("on credit" in q["raw_text"] for q in credit)
      and all("SALE_GOODS_CREDIT" in (q["transaction_types"] or [])
              for q in credit))
check("M2 cash sale excluded", all("for cash" not in q["raw_text"]
                                   for q in credit))

# ------------------------------------------------------------------ N
print("\n--- N. Duplicate detection ---")
b14 = bank()
q1 = pipeline(b14, "Sold goods to Ram on credit ₹12,000.",
              source_type="manual", source_reference="S1")
q2 = pipeline(b14, "Sold goods to Ram on credit ₹12,000.",
              source_type="manual", source_reference="S2")
check("N1 different-source same wording is flagged",
      b14.get_question(q2)["duplicate_of"] == q1,
      str(b14.get_question(q2)["duplicate_of"]))
check("N2 flagged duplicate is still stored (no false merge)",
      q1 in b14._questions and q2 in b14._questions)
try:
    b14.create_question("Sold goods to Ram on credit ₹12,000.",
                        source_type="manual", source_reference="S1")
    check("N3 exact same text+source blocked", False, "no ValueError")
except ValueError:
    check("N3 exact same text+source blocked", True)

# ------------------------------------------------------------------ O
print("\n--- O. Variant linkage ---")
b15 = bank()
canon = pipeline(b15, "Sold goods to Ram on credit ₹12,000.",
                 source_type="manual")
vid = b15.link_variant(
    canon, "Goods sold to Ram on credit Rs.12,000.",
    source_type="generated", source_reference="variant-1")
v = b15.get_question(vid)
check("O1 wording variant approved", v["status"] == STATUS_APPROVED,
      v["status"])
check("O2 variant points to canonical",
      v["canonical_id"] == canon)
check("O3 canonical records the variant",
      vid in b15.get_question(canon)["variants"])
vid2 = b15.link_variant(
    canon, "Sold goods to Ram ₹12,000. Received ₹5,000 from him.",
    source_type="generated", source_reference="variant-2")
check("O4 meaning-changing variant never approved",
      b15.get_question(vid2)["status"] != STATUS_APPROVED,
      b15.get_question(vid2)["status"])

# ------------------------------------------------------------------ P
print("\n--- P. LLM suggestions cannot bypass verification ---")
b16 = bank()
qid16 = pipeline(
    b16, "Purchased machinery from Seshadri ₹50,000.",
    source_type="manual")
before = b16.get_question(qid16)
b16.apply_llm_suggestions(
    qid16, {"chapter": "Ch.3 Journal", "difficulty": 3,
            "concept": "Asset purchase (credit)"})
after = b16.get_question(qid16)
check("P1 LLM suggestion applied to unknown fields",
      after["difficulty"] == 3
      and after["metadata_provenance"].get("difficulty") == "llm_suggested",
      str(after["metadata_provenance"]))
check("P2 suggestion cannot touch raw_text/expected_journal/status",
      after["raw_text"] == before["raw_text"]
      and after["expected_journal"] == before["expected_journal"]
      and after["status"] == before["status"])
try:
    b16.apply_llm_suggestions(
        qid16, {"raw_text": "manipulated",
                "expected_journal": {"debit": [], "credit": []}})
    check("P3 LLM cannot suggest raw_text/expected_journal", False,
          "no ValueError")
except ValueError:
    check("P3 LLM cannot suggest raw_text/expected_journal", True)
# Approval still requires deterministic verification even after edits.
try:
    b16.reject_question(qid16)
    b16.set_metadata(qid16, {"difficulty": 3})
    b16.approve_question(qid16)
    check("P4 rejected question cannot be force-approved", False,
          "approve succeeded after reject")
except ValueError:
    check("P4 rejected question cannot be force-approved", True)

# ------------------------------------------------------------------ Q
print("\n--- Q. Deterministic verification remains authoritative ---")
b17 = bank()
qid17 = b17.create_question(
    "Sold goods to Ram on credit ₹12,000.", source_type="manual")
b17.compile_question(qid17)
b17.validate_question(qid17)
v1 = b17.get_question(qid17)["verification"]
b17.validate_question(qid17)
v2 = b17.get_question(qid17)["verification"]
b17.approve_question(qid17)
check("Q1 repeated validation is identical",
      v1 == v2, "verification records differ")
# Teacher-supplied expected journal that contradicts the engine can never
# override the engine result.
b18 = bank()
qid18 = b18.create_question(
    "Sold goods to Ram on credit ₹12,000.",
    source_type="teacher_authored",
    expected={"debit": [["Ram", 12000]], "credit": [["Cash", 12000]]})
b18.compile_question(qid18)
b18.validate_question(qid18)
q18 = b18.get_question(qid18)
check("Q2 engine journal wins over teacher expectation",
      q18["expected_journal"]["credit"] == [["Sales", 12000]],
      str(q18["expected_journal"]))
check("Q3 contradiction blocks approval",
      q18["status"] != STATUS_APPROVED, q18["status"])

# ------------------------------------------------------------------ R
print("\n--- R. Existing 15E-15I behavior unchanged ---")
pins = {
    "Mohan was paid ₹5,000.":
        (["Mohan"], ["Cash"], "VERIFIED"),
    "Was paid ₹5,000.":
        ([], [], "NOT_SUPPORTED"),
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
    ok = (res.get("status") == exp_status and dr == exp_dr and cr == exp_cr)
    check(f"R pin {text[:42]!r}", ok,
          f"status={res.get('status')} dr={dr} cr={cr}")

# ------------------------------------------------------------------
print("\n--- Seed demonstration (provenance + pipeline on real corpus) ---")
try:
    from backend.maths import fyjc_bk_15e_benchmark as bench_15e
    b19 = bank()
    stats = b19.seed_from_benchmark(bench_15e)
    print(f"  seeded 15E: {stats}")
    check("S1 seed runs pipeline end-to-end",
          stats["created"] > 0 and stats["approved"] > 0, str(stats))
    seeded = b19.list_approved()
    check("S2 seeded questions carry provenance",
          all(q["source"]["source_name"].endswith("fyjc_bk_15e_benchmark")
              for q in seeded[:5]),
          str([q["source"]["source_name"] for q in seeded[:3]]))
    check("S3 seeded questions carry verified journals",
          all(q["expected_journal"] for q in seeded[:5]))
except Exception as exc:  # noqa: BLE001
    check("S1 seed runs pipeline end-to-end", False, repr(exc))

print("\n" + "=" * 78)
print(f"SUMMARY: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("SPRINT 15I-G PASS - CONTENT COMPILER / QUESTION BANK VERIFIED")
sys.exit(0)
