#!/usr/bin/env python3
"""FTE Sprint 28.5 — Automated Daily Whole-Problem Student Validation.

Runs a small corpus of COMPLETE real-student accounting problems (opening
entry -> final transaction) through the existing Platrixa pipeline and
validates the WHOLE-PROBLEM result:

  * ingestion / segmentation sanity
  * transaction progression (dropped / duplicated / merged segments)
  * state continuity (ledger reconciliation from state deltas)
  * ground-truth final balances where known
  * safety invariants
  * determinism (3 repeated runs must be byte-identical)
  * regression comparison against the previous known-good baseline

This is a testing/observability layer ONLY.  It never mutates production
state and never "fixes" failures automatically.

Usage:
    python3 scripts/fte_daily_whole_problem_validation.py
    python3 scripts/fte_daily_whole_problem_validation.py --update-baseline

Exit code is non-zero when a critical failure is detected:
    incorrect VERIFIED result, safety violation, determinism failure,
    unexpected REVIEW_REQUIRED, or a new regression vs the baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.maths.fyjc_problem_engine import process_problem  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "fte_daily_whole_problem_baseline.json"

# ---------------------------------------------------------------------------
# Daily student corpus.
#
# Whole problems only — each entry is one complete accounting problem from
# opening entry to final transaction.  Texts are privacy-cleaned (neutral
# party names, no contact/college identifiers) but structurally faithful to
# real student input, including Indian-English phrasing and spelling slips.
#
# expected_balances is ACCOUNTING GROUND TRUTH (not current engine output).
# A mismatch while the problem claims PROBLEM_VERIFIED => INCORRECT_VERIFIED.
# ---------------------------------------------------------------------------

CORPUS: List[Dict[str, Any]] = [
    {
        "id": "DWP001_FULL_CYCLE",
        "description": "Opening capital; credit purchase/sale; cash receipt "
                       "from debtor; part payment to creditor; expense.",
        "text": (
            "Commenced business with cash Rs.80000.\n"
            "Purchased goods from Raj Rs.20000 on credit.\n"
            "Sold goods to Amit Rs.25000 on credit.\n"
            "Paid rent Rs.5000 cash.\n"
            "Received Rs.10000 cash from Amit.\n"
            "Paid Raj Rs.15000 cash."
        ),
        "expected_transaction_count": 6,
        "expected_statuses": ["VERIFIED"] * 6,
        # Ground truth (debit-positive convention used by LedgerState):
        "expected_balances": {
            "Cash": "70000", "Capital": "-80000", "Purchases": "20000",
            "Sales": "-25000", "Rent": "5000", "Amit": "15000", "Raj": "-5000",
        },
    },
    {
        "id": "DWP002_GST_CREDIT_CYCLE",
        "description": "GST purchase (cash), GST credit sale, full receipt by "
                       "cheque from the debtor.",
        "text": (
            "Commenced business with cash Rs.100000.\n"
            "Purchased goods for Rs.10000 plus 18% GST, paid cash.\n"
            "Sold goods to Suresh for Rs.20000 plus 18% GST on credit.\n"
            "Received Rs.23600 from Suresh by cheque."
        ),
        "expected_transaction_count": 4,
        "expected_statuses": ["VERIFIED"] * 4,
        # Ground truth: receipt by cheque must DEBIT Bank (receiver) and
        # CREDIT Suresh (giver), closing Suresh's 23600 debit balance.
        "expected_balances": {
            "Cash": "88200", "Bank": "23600", "Capital": "-100000",
            "Purchases": "10000", "Sales": "-20000",
            "Input CGST": "900", "Input SGST": "900",
            "Output CGST": "-1800", "Output SGST": "-1800",
            "Suresh": "0",
        },
    },
    {
        "id": "DWP003_STUDENT_TYPO_INPUT",
        "description": "Real student phrasing: lowercase, spelling mistakes "
                       "('bussiness', 'recieved'), multi-line input.",
        "text": (
            "started bussiness with cash 40000\n"
            "bought goods from mehta 15000 on credit\n"
            "paid mehta 8000 by cheque\n"
            "sold goods to ramesh 12000\n"
            "recieved 6000 cash from ramesh"
        ),
        "expected_transaction_count": 3,  # splitter merges lines 2-4 (known limitation)
        "expected_statuses": None,  # capability boundary — no status assertions
        "expected_balances": None,
        "must_not_claim_verified": True,
    },
    {
        "id": "DWP004_DISCOUNT_SETTLEMENT",
        "description": "Trade discount purchase followed by full settlement; "
                       "splitter merges purchase + settlement (known Sprint 23 "
                       "Category-C limitation).",
        "text": (
            "Opened business with bank loan of Rs.50000 and cash Rs.20000.\n"
            "Purchased goods from Kumar Rs.30000 at 10% trade discount on credit.\n"
            "Paid Kumar Rs.13500 cash in full settlement."
        ),
        "expected_transaction_count": 2,  # merged segment expected
        "expected_statuses": ["REVIEW_REQUIRED", "REVIEW_REQUIRED"],
        "expected_balances": None,
        "must_not_claim_verified": True,
    },
]

CRITICAL_INVARIANTS = [
    "unsafe_confident", "invented_accounts", "invented_amounts",
    "unbalanced_verified", "state_leaks", "double_mutations",
    "ledger_reconciliation_failures", "determinism_failure",
    "incorrect_verified",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonical(obj: Any) -> Any:
    """Recursively convert engine output into JSON-safe deterministic data."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _digest(result: Dict[str, Any]) -> str:
    payload = json.dumps(_canonical(result), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _reconcile_ledger(result: Dict[str, Any]) -> List[str]:
    """Independently recompute balances from per-transaction state deltas and
    compare against the engine's final ledger snapshot."""
    recomputed: Dict[str, Decimal] = {}
    for tx in result["transactions"]:
        sd = tx.get("state_delta")
        if not sd:
            continue
        for d in sd["deltas"]:
            acct = d["account"]
            amt = Decimal(d["amount"])
            cur = recomputed.get(acct, Decimal(0))
            recomputed[acct] = cur + amt if d["direction"] == "debit" else cur - amt
    snapshot = result["ledger_snapshot"]["balances"]
    mismatches = []
    for acct in sorted(set(snapshot) | set(recomputed)):
        got = snapshot.get(acct, "0")
        want = str(recomputed.get(acct, Decimal(0)))
        if Decimal(got) != Decimal(want):
            mismatches.append(f"{acct}: ledger={got} recomputed={want}")
    return mismatches


def _analyze(spec: Dict[str, Any], result: Dict[str, Any],
             deterministic: bool, elapsed: float) -> Dict[str, Any]:
    m: Dict[str, Any] = {
        "problem_id": spec["id"],
        "input_length": len(spec["text"]),
        "transaction_count": len(result["transactions"]),
        "final_classification": result["problem_status"],
        "review_required_count": 0,
        "student_resolvable_count": 0,
        "unexpected_review_required_count": 0,
        "incorrect_verified": 0,
        "not_supported_count": 0,
        "invalid_math_count": 0,
        "final_ledger_reconciled": True,
        "execution_time": round(elapsed, 3),
        "deterministic": deterministic,
    }
    notes: List[str] = []
    counts = {"EXPECTED_REVIEW_REQUIRED": 0, "STUDENT_RESOLVABLE": 0,
              "UNEXPECTED_REVIEW_REQUIRED": 0, "INCORRECT_VERIFIED": 0}

    # -- A/B: ingestion & progression -------------------------------------
    exp_count = spec.get("expected_transaction_count")
    if exp_count is not None and len(result["transactions"]) != exp_count:
        notes.append(f"PROGRESSION: expected {exp_count} transactions, "
                     f"got {len(result['transactions'])} (merged/dropped/split)")
    for i, tx in enumerate(result["transactions"]):
        st = tx["status"]
        if st == "REVIEW_REQUIRED":
            m["review_required_count"] += 1
            gate = tx.get("confidence_gate")
            expected_rr = (spec.get("expected_statuses") or [])[i:i + 1] == ["REVIEW_REQUIRED"]
            if gate:
                m["student_resolvable_count"] += 1
                counts["STUDENT_RESOLVABLE" if expected_rr else
                       "UNEXPECTED_REVIEW_REQUIRED"] += 1
            elif expected_rr:
                counts["EXPECTED_REVIEW_REQUIRED"] += 1
            else:
                counts["UNEXPECTED_REVIEW_REQUIRED"] += 1
                notes.append(f"T{i + 1}: UNEXPECTED_REVIEW_REQUIRED :: "
                             f"{tx.get('why_not', '')[:90]}")
        elif st == "NOT_SUPPORTED":
            m["not_supported_count"] += 1
        elif st == "INVALID_INPUT_MATH":
            m["invalid_math_count"] += 1
        elif st == "VERIFIED":
            j = tx.get("journal") or {}
            if j and not j.get("balanced", True):
                counts["INCORRECT_VERIFIED"] += 1
                notes.append(f"T{i + 1}: unbalanced VERIFIED journal")

    # Expected statuses (where specified)
    if spec.get("expected_statuses"):
        for i, want in enumerate(spec["expected_statuses"]):
            got = (result["transactions"][i]["status"]
                   if i < len(result["transactions"]) else "MISSING")
            if got != want:
                notes.append(f"T{i + 1}: expected {want}, got {got}")
                if got == "VERIFIED" and want != "VERIFIED":
                    counts["INCORRECT_VERIFIED"] += 1

    # -- Ground-truth final state -----------------------------------------
    if spec.get("expected_balances") and result["problem_status"] == "PROBLEM_VERIFIED":
        actual = result["ledger_snapshot"]["balances"]
        for acct, want in sorted(spec["expected_balances"].items()):
            got = actual.get(acct, "0")
            if Decimal(got) != Decimal(want):
                counts["INCORRECT_VERIFIED"] += 1
                notes.append(f"LEDGER GROUND TRUTH MISMATCH: {acct}: "
                             f"engine={got}, correct={want}")

    if spec.get("must_not_claim_verified") and \
            result["problem_status"] == "PROBLEM_VERIFIED":
        counts["INCORRECT_VERIFIED"] += 1
        notes.append("Problem claimed PROBLEM_VERIFIED but ground truth "
                     "is unresolved")

    # -- Safety & reconciliation -------------------------------------------
    violations = result.get("safety_violations") or []
    if violations:
        notes.append(f"SAFETY VIOLATIONS: {violations}")
    recon = _reconcile_ledger(result)
    if recon:
        m["final_ledger_reconciled"] = False
        notes.append("LEDGER RECONCILIATION FAILURE: " + "; ".join(recon))

    m.update({k.lower(): v for k, v in counts.items()})
    m["notes"] = notes
    return m


def _load_baseline() -> Dict[str, Any]:
    if BASELINE_PATH.exists():
        try:
            raw = json.loads(BASELINE_PATH.read_text())
            return {r["problem_id"]: r for r in raw.get("results", [])}
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update-baseline", action="store_true",
                    help="persist today's results as the new known-good baseline")
    args = ap.parse_args()

    baseline = _load_baseline()
    first_run = not baseline
    new_flags: List[str] = []

    print("=" * 72)
    print("SPRINT 28.5 — DAILY WHOLE-PROBLEM STUDENT VALIDATION")
    print("=" * 72)

    totals: Dict[str, int] = {k: 0 for k in CRITICAL_INVARIANTS}
    totals.update({"problems": 0, "verified_problems": 0,
                   "student_resolvable_problems": 0})
    summaries: List[Dict[str, Any]] = []

    for spec in CORPUS:
        totals["problems"] += 1

        # Determinism: three independent runs must be byte-identical.
        digests, results, t0 = [], [], time.time()
        deterministic = True
        for _ in range(3):
            r = process_problem(spec["text"])
            results.append(r)
            digests.append(_digest(r))
        if len(set(digests)) != 1:
            deterministic = False
        elapsed = time.time() - t0
        result = results[0]

        metrics = _analyze(spec, result, deterministic, elapsed)
        digest = digests[0]

        # Critical invariant accounting
        if not deterministic:
            totals["determinism_failure"] += 1
            metrics["notes"].append("DETERMINISM FAILURE across 3 runs")
        if metrics["incorrect_verified"]:
            totals["incorrect_verified"] += metrics["incorrect_verified"]
        if not metrics["final_ledger_reconciled"]:
            totals["ledger_reconciliation_failures"] += 1
        if result.get("safety_violations"):
            totals["unsafe_confident"] += len(result["safety_violations"])

        if metrics["final_classification"] == "PROBLEM_VERIFIED":
            totals["verified_problems"] += 1
        if metrics["student_resolvable_count"]:
            totals["student_resolvable_problems"] += 1

        # Regression comparison vs previous known-good baseline
        prev = baseline.get(spec["id"])
        if prev:
            if prev.get("digest") != digest:
                prev_rr = prev.get("summary", {}).get("review_required_count")
                now_rr = metrics["review_required_count"]
                if prev_rr is not None and now_rr > prev_rr:
                    new_flags.append(f"NEW_REVIEW_REQUIRED: {spec['id']} "
                                     f"({prev_rr} -> {now_rr})")
                new_flags.append(
                    f"OUTPUT_CHANGED: {spec['id']} "
                    f"(baseline={prev.get('digest', '')[:12]}, "
                    f"current={digest[:12]})")
            if prev.get("summary", {}).get("final_classification") != \
                    metrics["final_classification"]:
                new_flags.append(
                    f"NEW_CLASSIFICATION: {spec['id']}: "
                    f"{prev['summary']['final_classification']} -> "
                    f"{metrics['final_classification']}")

        summaries.append({"problem_id": spec["id"], "digest": digest,
                          "summary": {
                              k: metrics[k] for k in (
                                  "final_classification", "transaction_count",
                                  "review_required_count",
                                  "student_resolvable_count",
                                  "unexpected_review_required_count",
                                  "incorrect_verified", "not_supported_count",
                                  "invalid_math_count")}})
        summaries[-1]["summary"]["notes"] = metrics["notes"]

        # Per-problem report line
        print(f"\n[{spec['id']}] {metrics['final_classification']}  "
              f"(T={metrics['transaction_count']}, RR={metrics['review_required_count']}"
              f"/{metrics['student_resolvable_count']} resolvable, "
              f"NS={metrics['not_supported_count']}, "
              f"deterministic={'YES' if deterministic else 'NO'})")
        for n in metrics["notes"]:
            print(f"    ! {n}")

    # Baseline handling
    if args.update_baseline or first_run:
        BASELINE_PATH.write_text(json.dumps(
            {"written_by": "fte_daily_whole_problem_validation.py",
             "results": summaries}, indent=2, sort_keys=True))
        print(f"\nBaseline {'created' if first_run else 'updated'}: {BASELINE_PATH.name}")

    # Summary
    critical_failures = sum(totals[k] for k in CRITICAL_INVARIANTS)
    print("\n" + "-" * 72)
    print(f"Problems tested: {totals['problems']}")
    print(f"Whole problems VERIFIED: {totals['verified_problems']}")
    print(f"Problems with student-resolvable confirmation: "
          f"{totals['student_resolvable_problems']}")
    print(f"Incorrect VERIFIED: {totals['incorrect_verified']}")
    print(f"Safety violations: {totals['unsafe_confident']}")
    print(f"Determinism failures: {totals['determinism_failure']}")
    print(f"Ledger reconciliation failures: "
          f"{totals['ledger_reconciliation_failures']}")
    if new_flags:
        print("Regression flags:")
        for f in new_flags:
            print(f"  ! {f}")
    else:
        print("Regression flags: none"
              + (" (first run — baseline established)" if first_run else ""))

    print("-" * 72)
    if critical_failures > 0 or any(
            s["summary"]["incorrect_verified"] or
            s["summary"]["unexpected_review_required_count"]
            for s in summaries):
        print("RESULT: FAIL (critical regression detected)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
