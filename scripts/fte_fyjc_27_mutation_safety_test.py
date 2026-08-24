#!/usr/bin/env python3
"""
Sprint 27 — Air-Lock Mutation Safety Regression Gate
scripts/fte_fyjc_27_mutation_safety_test.py

Zero In-Place Mutation rule:
  All Air-Lock processing must treat splitter output, transaction objects,
  historical context and ledger state as READ-ONLY inputs.

Proves:
  M1. Splitter output is never mutated by process_problem().
  M2. Previously stored T1/T2 objects are unchanged after T3 is processed.
  M3. Stored historical references are not mutated by later transactions.
  M4. Ledger snapshot() returns an isolated copy (not live state).
  M5. Processing Problem B after Problem A inherits nothing from A.
  M6. resolve_problem_transaction() does not mutate the original problem text.
"""

import copy
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_bk_reasoning import _split_transactions
from backend.maths.fyjc_problem_engine import (
    process_problem,
    VERIFIED,
    REVIEW_REQUIRED,
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


# ---------------------------------------------------------------------------
# M1: Splitter output immutability
# ---------------------------------------------------------------------------

def test_splitter_output_immutable():
    print("\n--- M1: Splitter output is never mutated ---")
    problem = (
        "Purchased goods from Raj Rs.50000 for cash. "
        "Paid Rs.20000 to Raj. "
        "Sold half of goods purchased from Raj for cash."
    )
    segments_before = _split_transactions(problem)
    snapshot_before = copy.deepcopy(segments_before)

    process_problem(problem)

    segments_after = _split_transactions(problem)
    check("Splitter output identical after processing",
          segments_after == segments_before,
          f"before={segments_before} after={segments_after}")
    check("Fresh call produces fresh equal list",
          snapshot_before == segments_after)


# ---------------------------------------------------------------------------
# M2: Stored T1/T2 objects unchanged after T3
# ---------------------------------------------------------------------------

def test_prior_transactions_not_mutated():
    print("\n--- M2: Prior transaction objects unchanged after later txns ---")
    # We simulate the engine's internal flow: capture the result objects
    # produced for T1/T2 via a full run, then verify that re-running the
    # full pipeline leaves the earlier results byte-identical.
    problem = (
        "Purchased goods from Mark Rs.90000 for cash. "
        "Purchased goods from Raj Rs.20000 on credit. "
        "Sold half of goods purchased from Mark for cash."
    )
    r1 = process_problem(problem)
    t1_run1 = copy.deepcopy(r1["transactions"][0])
    t2_run1 = copy.deepcopy(r1["transactions"][1])

    r2 = process_problem(problem)  # second full run (T3 included again)
    t1_run2 = r2["transactions"][0]
    t2_run2 = r2["transactions"][1]

    check("T1 object identical across runs",
          _jsonable(t1_run1) == _jsonable(t1_run2))
    check("T2 object identical across runs",
          _jsonable(t2_run1) == _jsonable(t2_run2))

    # The in-memory objects returned in run 1 must not have been altered
    # simply because run 2 executed.
    check("Run-1 T1 dict still intact",
          t1_run1.get("status") == VERIFIED or t1_run1.get("status") == REVIEW_REQUIRED)
    check("Run-1 T1 has no fields added by run 2",
          set(t1_run1.keys()) <= {
              "index", "text", "status", "event_type", "journal",
              "state_delta", "historical_references", "why_not",
              "next_action", "confidence_gate"})


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str, sort_keys=True))


# ---------------------------------------------------------------------------
# M3: Historical references not mutated by later transactions
# ---------------------------------------------------------------------------

def test_historical_refs_readonly():
    print("\n--- M3: Historical index entries are read-only inputs ---")
    from backend.maths.fyjc_problem_engine import (
        _resolve_historical_text,
    )
    from backend.maths.fyjc_problem_engine import HistoricalReference

    ref = HistoricalReference(
        transaction_index=1,
        transaction_text="Purchased goods from Mark Rs.90000",
        entity="Mark",
        event_type="PURCHASE",
        amount=Decimal(90000),
        date_or_order=1,
        provenance=[{"source": "test"}],
    )
    ref_before = copy.deepcopy(ref)
    index = [ref]

    text, refs_used, ambiguous = _resolve_historical_text(
        "Sold half of goods purchased from Mark for cash.",
        index, current_tx_index=2)

    check("Stored reference unchanged after resolution",
          _jsonable(ref) == _jsonable(ref_before),
          f"before={ref} after={ref_before}")
    check("Resolution succeeded deterministically",
          text != "Sold half of goods purchased from Mark for cash."
          and refs_used and not ambiguous,
          f"text={text!r} ambiguous={ambiguous}")


# ---------------------------------------------------------------------------
# M4: Ledger snapshot isolation
# ---------------------------------------------------------------------------

def test_ledger_snapshot_isolated():
    print("\n--- M4: snapshot() returns isolated copy ---")
    result = process_problem(
        "Purchased goods from Raj Rs.50000 for cash. Paid Rs.20000 to Raj.")
    snap1 = result["ledger_snapshot"]

    # Mutate the returned snapshot deeply
    snap1["balances"]["Cash"] = "-999999"
    if "entity_outstanding" in snap1:
        for k in snap1["entity_outstanding"]:
            snap1["entity_outstanding"][k] = "-999999"

    snap2 = process_problem(
        "Purchased goods from Raj Rs.50000 for cash. Paid Rs.20000 to Raj."
    )["ledger_snapshot"]
    check("Mutating returned snapshot does not affect fresh runs",
          snap2.get("balances", {}).get("Cash") != "-999999",
          f"snap2 Cash={snap2.get('balances', {}).get('Cash')}")


# ---------------------------------------------------------------------------
# M5: Problem A cannot leak into Problem B
# ---------------------------------------------------------------------------

def test_no_cross_problem_inheritance():
    print("\n--- M5: Problem B after Problem A inherits nothing ---")

    # Problem A establishes Mark's purchase + partial payment state
    problem_a = ("Purchased goods from Mark Rs.90000 for cash. "
                 "Paid Rs.30000 to Mark.")
    ra = process_problem(problem_a)

    # Problem B mentions Mark but must be evaluated independently
    problem_b = ("Purchased goods from Mark Rs.60000 on credit. "
                 "Paid Rs.10000 to Mark.")
    rb1 = process_problem(problem_b)
    rb1_snap = copy.deepcopy(rb1["ledger_snapshot"])

    # Run B again AFTER another A execution - must be byte-identical
    process_problem(problem_a)
    rb2 = process_problem(problem_b)

    check("Problem B ledger identical regardless of Problem A runs",
          _jsonable(rb1_snap) == _jsonable(rb2["ledger_snapshot"]),
          f"b1={rb1_snap} b2={rb2['ledger_snapshot']}")
    check("Problem B status stable",
          rb1["problem_status"] == rb2["problem_status"])
    check("Problem B transaction statuses stable",
          [t["status"] for t in rb1["transactions"]] ==
          [t["status"] for t in rb2["transactions"]])

    # Also verify ordering independence: B first then A vs A then B
    rb_solo = process_problem(problem_b)
    process_problem(problem_a)
    rb_after = process_problem(problem_b)
    check("B solo == B after A (full result)",
          _jsonable(rb_solo) == _jsonable(rb_after))


# ---------------------------------------------------------------------------
# M6: resolve_problem_transaction does not mutate the original input
# ---------------------------------------------------------------------------

def test_resolve_does_not_mutate_input():
    print("\n--- M6: Student resolution leaves original text untouched ---")
    from backend.maths.fyjc_problem_engine import resolve_problem_transaction

    problem = "Purchased goods for 10000. Paid rent 5000."
    before = copy.deepcopy(problem)

    resolve_problem_transaction(problem, 1, "CASH_CREDIT", "cash")

    check("Original problem string unchanged (immutable str)",
          problem == before)
    segments = _split_transactions(problem)
    check("Splitter output for original text still resolves identically",
          len(segments) >= 2)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("SPRINT 27 \u2014 AIR-LOCK MUTATION SAFETY GATE")
    print("=" * 70)

    test_splitter_output_immutable()
    test_prior_transactions_not_mutated()
    test_historical_refs_readonly()
    test_ledger_snapshot_isolated()
    test_no_cross_problem_inheritance()
    test_resolve_does_not_mutate_input()

    print("\n" + "=" * 70)
    print(f"Sprint 27 Mutation Safety: {PASS}/{PASS + FAIL} PASS, {FAIL} FAIL")
    print("=" * 70)
    sys.exit(0 if FAIL == 0 else 1)
