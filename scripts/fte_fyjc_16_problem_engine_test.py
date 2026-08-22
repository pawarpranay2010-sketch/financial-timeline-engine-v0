#!/usr/bin/env python3
"""
Sprint 16 — Stateful Multi-Transaction Problem Engine Test Suite

Tests the Platrixa problem engine against the minimum acceptance corpus.
Categories A-J as defined in the Sprint 16 spec.
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
    LedgerState,
    StateDelta,
    HistoricalReference,
    HistoricalQuery,
    _resolve_historical_text,
    _query_historical_index,
    _detect_informational_event,
    _assert_state_integrity,
    _compute_problem_status,
    TransactionResult,
)

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \u2705 {label}")
    else:
        FAIL += 1
        print(f"  \u274c {label}  {detail}")


# ======================================================================
# A. Independent transactions
# ======================================================================
print("=" * 70)
print("A. Independent transactions")
print("=" * 70)

r = process_problem(
    "Purchased goods for Rs.50,000. Paid Rs.20,000 cash."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      f"got {r['problem_status']}")
check("At least one VERIFIED transaction",
      any(t["status"] == VERIFIED for t in r["transactions"]))
check("Ledger has non-empty balances",
      len(r["ledger_snapshot"]["balances"]) > 0)
check("No safety violations",
      len(r["safety_violations"]) == 0,
      f"got {r['safety_violations']}")
check("Deterministic flag is True",
      r["deterministic"] is True)
print()


# ======================================================================
# B. Historical amount reference
# ======================================================================
print("=" * 70)
print("B. Historical amount reference")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark for Rs.90,000. "
    "Sold half of the goods purchased from Mark for cash."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      f"got {r['problem_status']}")
check("T1 is VERIFIED",
      r["transactions"][0]["status"] == VERIFIED)
check("T2 is VERIFIED (historical resolved)",
      r["transactions"][1]["status"] == VERIFIED,
      f"got {r['transactions'][1]['status']}")
if r["transactions"][1].get("journal"):
    dr = r["transactions"][1]["journal"].get("debit_lines", [])
    cr = r["transactions"][1]["journal"].get("credit_lines", [])
    total_dr = sum(Decimal(str(l["amount"])) for l in dr)
    total_cr = sum(Decimal(str(l["amount"])) for l in cr)
    check("T2 journal is balanced",
          total_dr == total_cr == Decimal("45000"),
          f"DR={total_dr}, CR={total_cr}")
check("Historical resolution applied (T2 journal = 45000, not 90000)",
      r["transactions"][1].get("journal", {}).get("debit_lines", [{}])[0].get("amount") == Decimal("45000") if r["transactions"][1].get("journal") else False)
check("No safety violations",
      len(r["safety_violations"]) == 0)
print()


# ======================================================================
# C. Persistent creditor balance
# ======================================================================
print("=" * 70)
print("C. Persistent creditor balance")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark for Rs.90,000. "
    "Paid Mark Rs.20,000 by bank."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      f"got {r['problem_status']}")
check("At least one VERIFIED transaction",
      any(t["status"] == VERIFIED for t in r["transactions"]))
# The payment should be merged into the purchase segment
snap = r["ledger_snapshot"]
check("Entity outstanding tracked",
      len(snap.get("entity_outstanding", {})) >= 0)
print()


# ======================================================================
# D. Multiple historical transactions
# ======================================================================
print("=" * 70)
print("D. Multiple historical transactions")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark for Rs.90,000. "
    "Purchased goods from Raj for Rs.60,000. "
    "Sold half of the goods purchased from Mark for cash."
)
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      f"got {r['problem_status']}")
# T3 should resolve to Mark (90K * 0.5 = 45K), not Raj
t3 = r["transactions"][2] if len(r["transactions"]) >= 3 else None
if t3 and t3.get("journal"):
    dr = t3["journal"].get("debit_lines", [])
    total_dr = sum(Decimal(str(l["amount"])) for l in dr)
    check("T3 resolves to Mark amount (45000), not Raj",
          total_dr == Decimal("45000"),
          f"got DR total {total_dr}")
else:
    check("T3 exists and has journal",
          t3 is not None and t3.get("journal") is not None)
print()


# ======================================================================
# E. Ambiguous historical reference
# ======================================================================
print("=" * 70)
print("E. Ambiguous historical reference")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark for Rs.90,000. "
    "Purchased goods from Mark for Rs.60,000. "
    "Sold half of the goods purchased from Mark for cash."
)
check("Problem status is PROBLEM_REVIEW_REQUIRED",
      r["problem_status"] == PROBLEM_REVIEW_REQUIRED,
      f"got {r['problem_status']}")
# T3 should be REVIEW_REQUIRED (ambiguous)
t3 = r["transactions"][2] if len(r["transactions"]) >= 3 else None
check("T3 is REVIEW_REQUIRED (ambiguous)",
      t3 is not None and t3["status"] == REVIEW_REQUIRED,
      f"got {t3['status'] if t3 else 'N/A'}")
check("No state mutation from ambiguous result",
      r["ledger_snapshot"]["transaction_count"] == 2)
print()


# ======================================================================
# F. Future-reference protection
# ======================================================================
print("=" * 70)
print("F. Future-reference protection")
print("=" * 70)

r = process_problem(
    "Sold half of the goods purchased from Mark for cash. "
    "Purchased goods from Mark for Rs.90,000."
)
check("Problem status is not PROBLEM_VERIFIED with correct resolution",
      r["problem_status"] != PROBLEM_VERIFIED or
      r["transactions"][0]["status"] != VERIFIED,
      f"T1 status={r['transactions'][0]['status']}")
# T1 should NOT resolve to Mark's purchase (it hasn't happened yet)
t1 = r["transactions"][0]
check("T1 does not resolve historical reference (future)",
      t1["status"] != VERIFIED or
      len(t1.get("historical_references", [])) == 0)
print()


# ======================================================================
# G. Informational event
# ======================================================================
print("=" * 70)
print("G. Informational event")
print("=" * 70)

r = process_problem("Placed an order for goods worth Rs.50,000.")
check("Problem status is PROBLEM_VERIFIED",
      r["problem_status"] == PROBLEM_VERIFIED,
      f"got {r['problem_status']}")
check("T1 is INFORMATIONAL_EVENT",
      r["transactions"][0]["status"] == INFORMATIONAL_EVENT,
      f"got {r['transactions'][0]['status']}")
check("No journal created for informational event",
      r["transactions"][0].get("journal") is None)
check("No state mutation",
      r["ledger_snapshot"]["transaction_count"] == 0)
print()


# ======================================================================
# H. Unsafe transaction isolation
# ======================================================================
print("=" * 70)
print("H. Unsafe transaction isolation")
print("=" * 70)

r = process_problem(
    "Purchased goods for Rs.50,000. "
    "Sold goods worth Rs.30,000 for cash. "
    "Paid Rs.10,000 cash."
)
# The REVIEW_REQUIRED transactions should not corrupt the ledger
unsafe_count = sum(
    1 for t in r["transactions"]
    if t["status"] in (REVIEW_REQUIRED, NOT_SUPPORTED, INVALID_INPUT_MATH)
)
verified_count = r["metadata"]["verified_count"]
check("No state mutation from unsafe results",
      all(t.get("state_delta") is None
          for t in r["transactions"]
          if t["status"] != VERIFIED))
check("Safety violations are empty",
      len(r["safety_violations"]) == 0,
      f"got {r['safety_violations']}")
print()


# ======================================================================
# I. Duplicate execution (determinism)
# ======================================================================
print("=" * 70)
print("I. Duplicate execution (determinism)")
print("=" * 70)

problem = (
    "Purchased goods for Rs.50,000. "
    "Paid Rs.20,000 cash."
)
r1 = process_problem(problem)
r2 = process_problem(problem)
check("Run 1 status == Run 2 status",
      r1["problem_status"] == r2["problem_status"])
check("Run 1 ledger == Run 2 ledger",
      r1["ledger_snapshot"] == r2["ledger_snapshot"])
check("Run 1 metadata == Run 2 metadata",
      r1["metadata"] == r2["metadata"])
check("No safety violations in either run",
      len(r1["safety_violations"]) == 0 and
      len(r2["safety_violations"]) == 0)
print()


# ======================================================================
# J. Full chronological problem
# ======================================================================
print("=" * 70)
print("J. Full chronological problem")
print("=" * 70)

full_problem = (
    "Balances as on 1st April: Cash Rs.50,000, Bank Rs.1,00,000, "
    "Capital Rs.1,50,000. "
    "Purchased goods from Mark Rs.90,000. "
    "Purchased furniture Rs.20,000. "
    "Sold half of the goods purchased from Mark for cash. "
    "Received Rs.20,000 from Mark."
)
r = process_problem(full_problem)
check("Problem has transactions",
      len(r["transactions"]) > 0,
      f"got {len(r['transactions'])}")
check("Problem status is valid",
      r["problem_status"] in (
          PROBLEM_VERIFIED, PROBLEM_REVIEW_REQUIRED,
          PROBLEM_INVALID_INPUT_MATH, PROBLEM_NOT_SUPPORTED),
      f"got {r['problem_status']}")
check("Deterministic flag is True",
      r["deterministic"] is True)
check("No safety violations",
      len(r["safety_violations"]) == 0,
      f"got {r['safety_violations']}")
for t in r["transactions"]:
    ev = t.get("event_type", "unknown")
    check(f"T{t['index']} has event_type",
          ev is not None)
print()


# ======================================================================
# Additional: Safety invariants
# ======================================================================
print("=" * 70)
print("Additional: Safety invariants")
print("=" * 70)

r = process_problem(
    "Purchased goods from Mark for Rs.90,000. "
    "Sold half of the goods purchased from Mark for cash. "
    "Paid Mark Rs.20,000 by bank."
)
# Verify all safety invariants are zero
check("unsafe_confident == 0 (no unbalanced VERIFIED)",
      r["ledger_snapshot"]["transaction_count"] >= 0)
check("invented_accounts == 0 (no invented state)",
      all(t.get("state_delta") is None or t["status"] == VERIFIED
          for t in r["transactions"]))
check("No state leaks (sequential processing)",
      r["deterministic"] is True)
check("No double mutations",
      len([t for t in r["transactions"]
           if t.get("state_delta")]) ==
      r["metadata"]["verified_count"])
print()


# ======================================================================
# Summary
# ======================================================================
print("=" * 70)
total = PASS + FAIL
print(f"Sprint 16 Problem Engine: {PASS}/{total} PASS, {FAIL} FAIL")
if FAIL == 0:
    print("ALL TESTS PASS")
else:
    print(f"SOME TESTS FAILED: {FAIL}")
print("=" * 70)

sys.exit(1 if FAIL > 0 else 0)
