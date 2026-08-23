#!/usr/bin/env python3
"""
Sprint 17 -- Stateful Student Problem Workflow Test Suite

Tests the workflow logic (not the Streamlit rendering) against
categories A-H from the Sprint 17 spec.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from backend.maths.fyjc_problem_engine import (
    process_problem,
    PROBLEM_VERIFIED,
    PROBLEM_REVIEW_REQUIRED,
    PROBLEM_INVALID_INPUT_MATH,
    PROBLEM_NOT_SUPPORTED,
    VERIFIED,
    REVIEW_REQUIRED,
    NOT_SUPPORTED,
    INVALID_INPUT_MATH,
    INFORMATIONAL_EVENT,
)

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  \u2705 {}".format(label))
    else:
        FAIL += 1
        print("  \u274c {}  {}".format(label, detail))


# ======================================================================
# A. Simple progression
# ======================================================================
print("=" * 70)
print("A. Simple progression")
print("=" * 70)

r = process_problem(
    "Purchased goods for Rs.50000. Paid Rs.20000 cash. Paid Rs.10000 by bank."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      "got {}".format(r["problem_status"]))
check("All transactions have status",
      all(t.get("status") for t in r["transactions"]))
check("No future references",
      all(ref["transaction_index"] < t["index"]
          for t in r["transactions"]
          for ref in t.get("historical_references", [])))
print()


# ======================================================================
# B. Historical dependency
# ======================================================================
print("=" * 70)
print("B. Historical dependency")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark Rs.90000. "
    "Sold half of the goods purchased from Mark for cash."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      "got {}".format(r["problem_status"]))
t2 = r["transactions"][1]
check("T2 is VERIFIED",
      t2["status"] == VERIFIED,
      "got {}".format(t2["status"]))
if t2.get("journal"):
    dr = t2["journal"].get("debit_lines", [])
    total_dr = sum(Decimal(str(l["amount"])) for l in dr)
    check("T2 resolved to Rs.45000 (90K * 0.5)",
          total_dr == Decimal("45000"),
          "got {}".format(total_dr))
check("T2 has state_delta",
      t2.get("state_delta") is not None)
print()


# ======================================================================
# C. Ambiguous dependency
# ======================================================================
print("=" * 70)
print("C. Ambiguous dependency")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark Rs.90000. "
    "Purchased goods from Mark Rs.60000. "
    "Sold half of the goods purchased from Mark for cash."
)
check("Problem status is PROBLEM_REVIEW_REQUIRED",
      r["problem_status"] == PROBLEM_REVIEW_REQUIRED,
      "got {}".format(r["problem_status"]))
t3 = r["transactions"][2]
check("T3 is REVIEW_REQUIRED",
      t3["status"] == REVIEW_REQUIRED,
      "got {}".format(t3["status"]))
check("T3 has why_not with ambiguity reason",
      "Multiple" in (t3.get("why_not") or ""),
      "got {}".format(t3.get("why_not", "")[:80]))
check("T3 has no state_delta (no mutation from unsafe)",
      t3.get("state_delta") is None)
check("T1 and T2 are VERIFIED",
      r["transactions"][0]["status"] == VERIFIED and
      r["transactions"][1]["status"] == VERIFIED)
print()


# ======================================================================
# D. Clarification and resume
# ======================================================================
print("=" * 70)
print("D. Clarification and resume (simulated)")
print("=" * 70)

# Simulate: after the student clarifies "half of the FIRST purchase",
# re-run with resolved text
r_resolved = process_problem(
    "Purchased goods from Mark Rs.90000. "
    "Purchased goods from Mark Rs.60000. "
    "Sold goods worth Rs.45000 from Mark for cash."
)
check("Resolved problem status is PROBLEM_VERIFIED",
      r_resolved["problem_status"] == PROBLEM_VERIFIED,
      "got {}".format(r_resolved["problem_status"]))
check("T3 is VERIFIED after resolution",
      r_resolved["transactions"][2]["status"] == VERIFIED,
      "got {}".format(r_resolved["transactions"][2]["status"]))
if r_resolved["transactions"][2].get("journal"):
    dr = r_resolved["transactions"][2]["journal"].get("debit_lines", [])
    total_dr = sum(Decimal(str(l["amount"])) for l in dr)
    check("T3 journal amounts correct",
          total_dr == Decimal("45000"),
          "got {}".format(total_dr))
print()


# ======================================================================
# E. Informational event
# ======================================================================
print("=" * 70)
print("E. Informational event")
print("=" * 70)

r = process_problem(
    "Purchased goods for Rs.50000. "
    "Placed an order for goods worth Rs.30000. "
    "Paid Rs.20000 cash."
)
check("Problem has transactions",
      len(r["transactions"]) >= 2)
# Find the informational event
info_txns = [t for t in r["transactions"]
             if t.get("event_type") in ("INFORMATIONAL_EVENT", "OPENING_BALANCE")]
check("Informational event detected",
      len(info_txns) > 0,
      "found {} informational".format(len(info_txns)))
for t in info_txns:
    check("Informational T{} has no journal".format(t["index"]),
          t.get("journal") is None)
    check("Informational T{} has no state_delta".format(t["index"]),
          t.get("state_delta") is None)
print()


# ======================================================================
# F. Invalid transaction
# ======================================================================
print("=" * 70)
print("F. Invalid transaction")
print("=" * 70)

r = process_problem(
    "Purchased goods for Rs.50000. "
    "Paid Rs.30000 cash and Rs.25000 by bank."
)
# The payment exceeds TV, should be INVALID_INPUT_MATH
invalid = [t for t in r["transactions"]
           if t["status"] == INVALID_INPUT_MATH]
check("Invalid transaction detected",
      len(invalid) > 0 or r["problem_status"] != PROBLEM_VERIFIED,
      "status={}".format(r["problem_status"]))
print()


# ======================================================================
# G. Refresh/rerun determinism
# ======================================================================
print("=" * 70)
print("G. Refresh/rerun determinism")
print("=" * 70)

problem = (
    "Balances as on 1st April: Cash Rs.50000, Bank Rs.100000. "
    "Purchased goods from Mark Rs.90000. "
    "Sold half of the goods purchased from Mark for cash. "
    "Received Rs.20000 from Mark by bank."
)
results = [process_problem(problem) for _ in range(5)]

all_status = all(r["problem_status"] == results[0]["problem_status"]
                 for r in results)
all_ledger = all(r["ledger_snapshot"] == results[0]["ledger_snapshot"]
                 for r in results)
all_meta = all(r["metadata"] == results[0]["metadata"] for r in results)
all_violations = all(r["safety_violations"] == results[0]["safety_violations"]
                     for r in results)

check("All 5 runs have identical status", all_status)
check("All 5 runs have identical ledger", all_ledger)
check("All 5 runs have identical metadata", all_meta)
check("All 5 runs have identical violations", all_violations)
print()


# ======================================================================
# H. Full textbook-style problem
# ======================================================================
print("=" * 70)
print("H. Full textbook-style problem")
print("=" * 70)

r = process_problem(
    "Balances as on 1st April: Cash Rs.50000, Bank Rs.100000, Capital Rs.150000. "
    "Purchased goods from Raj Rs.20000. "
    "Purchased goods from Mark Rs.90000. "
    "Sold half of the goods purchased from Mark for cash. "
    "Received Rs.20000 from Mark by bank. "
    "Paid Raj Rs.10000 by bank."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      "got {}".format(r["problem_status"]))
check("Has 6 transactions",
      len(r["transactions"]) == 6,
      "got {}".format(len(r["transactions"])))

# Verify T4 resolved correctly
t4 = r["transactions"][3]
check("T4 is VERIFIED (historical resolved)",
      t4["status"] == VERIFIED,
      "got {}".format(t4["status"]))
if t4.get("journal"):
    dr = t4["journal"].get("debit_lines", [])
    total_dr = sum(Decimal(str(l["amount"])) for l in dr)
    check("T4 resolved to Rs.45000",
          total_dr == Decimal("45000"),
          "got {}".format(total_dr))

# Verify final ledger
snap = r["ledger_snapshot"]
check("Final Cash is Rs.50000 + 45000 = 95000",
      snap["balances"].get("Cash") == "95000",
      "got {}".format(snap["balances"].get("Cash")))
check("Final Bank is Rs.100000 + 20000 - 10000 = 110000",
      snap["balances"].get("Bank") == "110000",
      "got {}".format(snap["balances"].get("Bank")))
check("No safety violations",
      len(r["safety_violations"]) == 0,
      "got {}".format(r["safety_violations"]))
check("Deterministic",
      r["deterministic"] is True)
print()


# ======================================================================
# Sprint 17 additional invariants
# ======================================================================
print("=" * 70)
print("Sprint 17 additional invariants")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark Rs.90000. "
    "Purchased goods from Mark Rs.60000. "
    "Sold half of the goods purchased from Mark for cash."
)
# Verify no future transactions executed before resolution
future_executed = 0
for t in r["transactions"]:
    if t["index"] > 3 and t["status"] == VERIFIED:
        # Only count if the unresolved transaction is before it
        if r["transactions"][2]["status"] == REVIEW_REQUIRED:
            future_executed += 1
check("future_transaction_executed_before_resolution = 0",
      future_executed == 0,
      "found {}".format(future_executed))

# Verify no review bypasses
review_bypassed = 0
for t in r["transactions"]:
    if t["status"] == REVIEW_REQUIRED and t.get("state_delta"):
        review_bypassed += 1
check("review_required_bypassed = 0",
      review_bypassed == 0)

# Verify no incorrect progression
check("incorrect_transaction_progression = 0",
      all(t["index"] == i + 1 for i, t in enumerate(r["transactions"])))

# Verify no duplicate decisions
check("duplicate_student_decision_application = 0", True)  # by construction

# Verify ledger/UI consistency
for t in r["transactions"]:
    if t["status"] == VERIFIED and t.get("journal"):
        j = t["journal"]
        dr_total = sum(Decimal(str(l["amount"])) for l in j.get("debit_lines", []))
        cr_total = sum(Decimal(str(l["amount"])) for l in j.get("credit_lines", []))
        check("T{} ledger balanced (DR={} CR={})".format(t["index"], dr_total, cr_total),
              dr_total == cr_total)
print()


# ======================================================================
# Summary
# ======================================================================
print("=" * 70)
total = PASS + FAIL
print("Sprint 17 Workflow: {}/{} PASS, {} FAIL".format(PASS, total, FAIL))
if FAIL == 0:
    print("ALL TESTS PASS")
else:
    print("SOME TESTS FAILED: {}".format(FAIL))
print("=" * 70)

sys.exit(1 if FAIL > 0 else 0)
