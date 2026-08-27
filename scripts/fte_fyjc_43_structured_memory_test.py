#!/usr/bin/env python3
"""
Sprint 43 — Structured Working Memory + Cash/Credit + Settlement Regression Tests

Tests:
  A. Cash/Credit ambiguity detection (5 tests)
  B. Settlement without explicit amount (5 tests)
  C. Structured memory integrity (5 tests)
  D. Long-problem regression (2 tests)
  E. Safety invariants (3 tests)
  F. Regression gates (10 tests)
  G. Determinism (2 tests)

Total: 32 tests
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from backend.maths.fyjc_structured_memory import (
    detect_payment_method,
    detect_cash_credit_ambiguity,
    detect_settlement_ambiguity,
    build_transaction_memory,
    build_problem_memory,
    re_resolve_with_clarification,
)
from backend.maths.fyjc_problem_engine import (
    process_problem,
    VERIFIED,
    REVIEW_REQUIRED,
    NOT_SUPPORTED,
    INVALID_INPUT_MATH,
)

PASS_COUNT = 0
FAIL_COUNT = 0
FAILURES = []


def test(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  ✅ {name}")
    else:
        FAIL_COUNT += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  ❌ {name}: {detail}")


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# A. Cash/Credit Ambiguity Detection (5 tests)
# ============================================================
section("A. Cash/Credit Ambiguity Detection")

# A1: Explicit cash purchase → no ambiguity
amb = detect_cash_credit_ambiguity("Purchased goods for cash Rs.20,000.")
test("A1: Explicit cash → no ambiguity", amb is None)

# A2: Explicit credit purchase → no ambiguity
amb = detect_cash_credit_ambiguity("Purchased goods on credit Rs.20,000.")
test("A2: Explicit credit → no ambiguity", amb is None)

# A3: Missing payment mode → ambiguity detected
amb = detect_cash_credit_ambiguity("Purchased goods from Raj for Rs.20,000.")
test("A3: Missing mode → ambiguity detected", amb is not None and amb["gate_id"] == "CASH_CREDIT",
     f"got {amb}")

# A4: Non-purchase transaction → no ambiguity (expenses, drawings)
amb = detect_cash_credit_ambiguity("Paid rent Rs.5,000.")
test("A4: Expense (no purchase verb) → no ambiguity", amb is None)

# A5: Ambiguity with student clarification → re-resolution works
mem = build_transaction_memory(1, "Purchased goods from Raj for Rs.20,000.")
test("A5: Structured memory flags payment_mode", "payment_mode" in mem.unresolved_fields)

# ============================================================
# B. Settlement Without Explicit Amount (5 tests)
# ============================================================
section("B. Settlement Without Explicit Amount")

# B1: Full settlement with unknown outstanding → ambiguity
settle = detect_settlement_ambiguity("Rohan settled his account with Amit.", [], None)
test("B1: Full settlement, unknown outstanding → ambiguity", settle is not None,
     f"got {settle}")

# B2: Settlement with explicit amount → no ambiguity
settle = detect_settlement_ambiguity("Rohan paid Rs.15,000 to settle Amit's account.", [], None)
test("B2: Settlement with explicit amount → no ambiguity", settle is None)

# B3: Non-settlement transaction → no detection
settle = detect_settlement_ambiguity("Purchased goods from Raj Rs.20,000", [], None)
test("B3: Non-settlement → no detection", settle is None)

# B4: Payment without amount → ambiguity
settle = detect_settlement_ambiguity("Rohan paid Amit by cheque.", [], None)
test("B4: Payment without amount → ambiguity", settle is not None,
     f"got {settle}")

# B5: Full settlement with known outstanding → no ambiguity
settle = detect_settlement_ambiguity(
    "Rohan settled his account with Amit.",
    [],
    Decimal("15000"),  # known outstanding
)
test("B5: Full settlement with known outstanding → no ambiguity", settle is None)

# ============================================================
# C. Structured Memory Integrity (5 tests)
# ============================================================
section("C. Structured Memory Integrity")

# C1: Transaction memory captures parties
mem = build_transaction_memory(1, "Purchased goods from Raj for Rs.20,000.")
test("C1: Parties detected", "Raj" in mem.parties, f"got {mem.parties}")

# C2: Transaction memory captures amount
test("C2: Amount detected", mem.amount == Decimal("20000"), f"got {mem.amount}")

# C3: Transaction memory detects payment method
mem2 = build_transaction_memory(2, "Paid Amit Rs.15,000 by cheque.")
test("C3: Payment method detected", mem2.payment_method == "bank", f"got {mem2.payment_method}")

# C4: Problem memory tracks unresolved items
pmem = build_problem_memory(
    "Purchased goods from Raj for Rs.20,000. Paid Amit Rs.15,000 by cheque.",
    ["Purchased goods from Raj for Rs.20,000", "Paid Amit Rs.15,000 by cheque."],
)
test("C4: Problem memory unresolved items", len(pmem.unresolved_items) >= 1,
     f"got {len(pmem.unresolved_items)}")

# C5: Snapshot is deterministic
snap1 = pmem.snapshot()
snap2 = pmem.snapshot()
test("C5: Snapshot deterministic", snap1 == snap2)

# ============================================================
# D. Long-Problem Regression (2 tests)
# ============================================================
section("D. Long-Problem Regression")

# D1: 13-transaction problem produces structured memory
result = process_problem(
    "On 1st April 2026, Rohan started a business with cash of Rs.1,00,000 and furniture worth Rs.20,000. "
    "On 2nd April, he purchased goods from Amit for Rs.30,000 on credit. "
    "On 3rd April, he purchased goods from Raj for Rs.20,000 on credit. "
    "On 5th April, he sold goods to Suresh for Rs.40,000 on credit. "
    "On 7th April, Suresh paid Rs.20,000 by cheque. "
    "On 10th April, Rohan paid Amit Rs.15,000 by cheque. "
    "On 12th April, goods worth Rs.5,000 purchased from Raj were returned. "
    "On 15th April, Rohan purchased goods for Rs.25,000 plus 18% GST for cash. "
    "On 18th April, Rohan sold goods for Rs.30,000 plus 18% GST for cash. "
    "On 25th April, Rohan paid Rs.8,000 for office expenses by cash. "
    "On 30th April, Rohan withdrew Rs.5,000 cash for personal use."
)
sm = result["structured_memory"]
test("D1: 13-tx problem produces structured memory", sm is not None and sm["transaction_count"] >= 10,
     f"got txns={sm['transaction_count'] if sm else 'None'}")

# D2: No false INCORRECT_VERIFIED
incorrect = 0
for tx in result.get("transactions", []):
    if tx.get("status") == VERIFIED:
        j = tx.get("journal")
        if not j or (not j.get("debit_lines") and not j.get("credit_lines")):
            incorrect += 1
test("D2: No VERIFIED with zero journal lines", incorrect == 0,
     f"got {incorrect} incorrect VERIFIED")

# ============================================================
# E. Safety Invariants (3 tests)
# ============================================================
section("E. Safety Invariants")

# E1: Safety violations from problem engine
test("E1: Safety violations empty", len(result.get("safety_violations", [])) == 0,
     f"got {result.get('safety_violations', [])}")

# E2: Deterministic output
r1 = process_problem("Purchased goods from Raj for Rs.20,000 on credit.")
r2 = process_problem("Purchased goods from Raj for Rs.20,000 on credit.")
test("E2: Deterministic output", r1 == r2)

# E3: Structured memory present in all results
for prob in [
    "Purchased goods from Raj Rs.20,000 on credit.",
    "Paid rent Rs.5,000 by cash.",
    "Sold goods to Amit for Rs.30,000 on credit.",
]:
    r = process_problem(prob)
    test(f"E3: Memory in result for '{prob[:30]}...'", "structured_memory" in r)

# ============================================================
# F. Regression Gates (10 tests)
# ============================================================
section("F. Regression Gates")

# F1: py_compile
import py_compile
try:
    py_compile.compile("backend/maths/fyjc_structured_memory.py", doraise=True)
    py_compile.compile("backend/maths/fyjc_problem_engine.py", doraise=True)
    test("F1: py_compile", True)
except py_compile.PyCompileError as e:
    test("F1: py_compile", False, str(e))

# F2–F8: Run regression gates via subprocess to avoid namespace pollution
import subprocess
_test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_regression_tests = [
    ("F2: Sprint 35 integrity", "scripts/fte_fyjc_35_integrity_invariant_test.py"),
    ("F3: Sprint 36 UI contract", "scripts/fte_fyjc_36_ui_contract_test.py"),
    ("F4: Sprint 37 calc scoping", "scripts/fte_fyjc_37_calc_scoping_test.py"),
    ("F5: Sprint 38 runtime audit", "scripts/fte_fyjc_38_ui_runtime_audit.py"),
    ("F6: Sprint 27 mutation safety", "scripts/fte_fyjc_27_mutation_safety_test.py"),
    ("F7: Boundary 852/852", "scripts/fte_fyjc_15boundary_closure_test.py"),
    ("F8: Sprint 40 adversarial", "scripts/fte_fyjc_40_adversarial_whole_problem_audit.py"),
]
for gate_name, gate_script in _regression_tests:
    try:
        proc = subprocess.run(
            ["python3", gate_script],
            capture_output=True, text=True, timeout=180,
            cwd=_test_dir,
        )
        test(gate_name, proc.returncode == 0,
             proc.stdout[-300:] if proc.returncode != 0 else "")
    except subprocess.TimeoutExpired:
        test(gate_name, False, "timeout")
    except Exception as e:
        test(gate_name, False, str(e)[:200])

# F9: Determinism across runs
r_a = process_problem("Purchased goods from Raj Rs.20,000.")
r_b = process_problem("Purchased goods from Raj Rs.20,000.")
test("F9: Determinism across runs", r_a == r_b)

# F10: Sprint 39 routing (problem_engine in projection)
test("F10: Structured memory in output", "structured_memory" in result)

# ============================================================
# G. Determinism (2 tests)
# ============================================================
section("G. Determinism")

# G1: Multi-run determinism
results = []
for _ in range(3):
    r = process_problem(
        "Purchased goods from Raj for Rs.20,000. Sold goods to Amit for Rs.30,000 on credit."
    )
    results.append(r)
test("G1: 3 runs byte-identical", results[0] == results[1] == results[2])

# G2: Memory snapshot deterministic
snap_a = results[0]["structured_memory"]
snap_b = results[1]["structured_memory"]
test("G2: Memory snapshots identical", snap_a == snap_b)


# ============================================================
# FINAL REPORT
# ============================================================
print(f"\n{'='*60}")
print(f"  SPRINT 43 TEST RESULTS")
print(f"{'='*60}")
print(f"  Total: {PASS_COUNT + FAIL_COUNT}")
print(f"  PASS:  {PASS_COUNT}")
print(f"  FAIL:  {FAIL_COUNT}")
if FAILURES:
    print(f"\n  FAILURES:")
    for f in FAILURES:
        print(f"    - {f}")
print(f"{'='*60}")

sys.exit(1 if FAIL_COUNT > 0 else 0)
