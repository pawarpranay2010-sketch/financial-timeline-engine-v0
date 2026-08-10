#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15 - Stage 4: FYJC Requested-Concept Routing Regression Gate
scripts/fte_fyjc_routing_regression_test.py

Blocker-specific regression gate for the Sprint 15 Stage 4 routing fixes:

    Blocker A - reverse/missing-figure questions must resolve the
                REQUESTED concept from the question's intent, never from
                a supplied value or nearby financial terminology.
    Blocker B - concept-specific dependency routing: ROE requires
                Net Profit + Equity, EPS requires Net Profit + Shares
                Outstanding (the registered formula's own dependencies).

Hard invariants verified here (the 0%-unsafe release gate):
  * unsafe confident answers        = 0
  * C++ authority violations        = 0  (every resolved output is cpp)
  * wrong-concept confident answers = 0
  * fabricated values               = 0  (refusals carry no display)
  * silent substitutions            = 0
  * a numerical result NEVER has status DERIVED/VERIFIED with
    formula_id = None               (no fact-echo labelled as calculated)
  * identical inputs -> identical output (deterministic)

All cases run through the REAL student journey (run_fyjc_student_flow).
C++ remains the sole mathematical authority; nothing here is committed
or pushed - this is a verification gate.

Exit code: 0 = PASS, 1 = FAIL (release blocker).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_student_flow import (  # noqa: E402
    build_understanding,
    run_fyjc_student_flow,
)

CHECKS: List[tuple] = []
FAILURES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def _has_display(display: Any) -> bool:
    """Refusals never carry a display; None/''/'—' all mean 'no value'."""
    return str(display or "").strip() not in ("", "—", "None")


def run(q: str, **kw) -> Dict[str, Any]:
    f = run_fyjc_student_flow(q, **kw)
    o = f.get("outcome") or {}
    f["_outcome_display"] = o.get("display_value")
    f["_outcome_formula"] = o.get("formula_id")
    f["_outcome_missing"] = o.get("missing")
    return f


# ---------------------------------------------------------------------------
# Blocker A - reverse / missing-figure routing (the requested concept wins)
# ---------------------------------------------------------------------------

REVERSE_CASES = [
    {
        "id": "R1.Find Expenses",
        "question": "Find the missing figure: Expenses.\nRevenue: 1,000\nProfit: 200",
        "metric": "expenses",
        "answer": 800,
        "display": "800.00",
        "formula": "PROFIT",
    },
    {
        "id": "R2.Find Profit",
        "question": "Find the Profit.\nProfit Margin: 20\nRevenue: 1,000",
        "metric": "profit",
        "answer": 200,
        "display": "200.00",
        "formula": "PROFIT_MARGIN",
    },
    {
        "id": "R3.Find Revenue",
        "question": "Find Revenue.\nGross Profit: 4,000\nCost of Sales: 6,000",
        "metric": "revenue",
        "answer": 10000,
        "display": "10000.00",
        "formula": "GROSS_PROFIT",
    },
    {
        "id": "R4.Find Profit Margin",
        "question": "Calculate the Profit Margin.\nProfit: 200\nRevenue: 1,000",
        "metric": "profit margin",
        "answer": 20,
        "display": "20.00%",
        "formula": "PROFIT_MARGIN",
    },
]

# Blocker B - concept-specific dependency routing
DEPENDENCY_CASES = [
    {
        "id": "D1.Reverse ROE",
        "question": "Find Equity.\nROE: 20\nNet Profit: 200",
        "metric": "equity",
        "answer": 1000,
        "display": "1000.00",
        "formula": "ROE",
    },
    {
        "id": "D2.Reverse EPS",
        "question": "Find Shares Outstanding.\nEPS: 2\nNet Profit: 200",
        "metric": "shares outstanding",
        "answer": 100,
        "display": "100.00",
        "formula": "EPS",
    },
    {
        "id": "D3.ROE missing Equity",
        "question": "Calculate ROE.\nNet Profit: 200",
        "metric": "roe",
        "status": "BLOCKED",
        "missing_in": ["Equity"],
        "not_missing_in": ["Revenue", "Expenses"],
    },
    {
        "id": "D4.EPS missing Shares",
        "question": "Calculate EPS.\nNet Profit: 200",
        "metric": "eps",
        "status": "BLOCKED",
        "missing_in": ["Shares Outstanding"],
        "not_missing_in": ["Revenue", "Expenses"],
    },
]

# Unsupported inverse + ambiguous wording
REFUSAL_CASES = [
    {
        "id": "U1.Unsupported inverse",
        "question": "Find Revenue.\nGross Margin: 40\nGross Profit: 4,000",
        "metric": "revenue",
        "status": "BLOCKED",
    },
    {
        "id": "A1.Ambiguous: Profit and Loss",
        "question": "Find Profit and Loss.",
        "status": "REVIEW_REQUIRED",
        "uncertain": True,
    },
    {
        "id": "A2.Ambiguous: ratio of Profit to Revenue",
        "question": "What is the ratio of Profit to Revenue?",
        "status": "REVIEW_REQUIRED",
        "uncertain": True,
    },
    {
        "id": "A3.Ambiguous: several figures in facts only",
        "question": "Revenue: 1,000\nProfit: 200",
        "status": "REVIEW_REQUIRED",
        "uncertain": True,
    },
]

# Echo gate - a supplied target is never presented as a calculated answer
ECHO_CASES = [
    {
        "id": "E1.supplied target, no derivation -> BLOCKED",
        "question": "Find the Profit.\nProfit: 200",
        "status": "BLOCKED",
    },
    {
        "id": "E2.supplied target, derivation agrees -> DERIVED w/ formula",
        "question": "Find the Profit Margin.\nProfit Margin: 20\nProfit: 200\nRevenue: 1,000",
        "status": "DERIVED",
        "display": "20.00%",
        "formula": "PROFIT_MARGIN",
        "answer": 20,
    },
    {
        "id": "E3.supplied target, derivation conflicts -> REVIEW_REQUIRED",
        "question": "Find the Profit.\nProfit: 500\nRevenue: 1,000\nExpenses: 800",
        "status": "REVIEW_REQUIRED",
    },
]


def test_reverse_cases() -> None:
    for case in REVERSE_CASES:
        f = run(case["question"], student_answer=case["answer"])
        check(
            f"{case['id']} routes to the requested concept",
            f.get("metric") == case["metric"], f"metric={f.get('metric')}",
        )
        check(
            f"{case['id']} resolves through a registered formula",
            f.get("status") == "DERIVED"
            and f.get("verdict") == "CORRECT"
            and f.get("_outcome_display") == case["display"]
            and f.get("_outcome_formula") == case["formula"],
            f"status={f.get('status')} verdict={f.get('verdict')} "
            f"display={f.get('_outcome_display')} formula={f.get('_outcome_formula')}",
        )
        check(
            f"{case['id']} is C++-authoritative",
            f.get("authority_state") == "cpp",
            str(f.get("authority_state")),
        )
        # student-facing 'Requested:' is the canonical concept
        u = f.get("understanding") or {}
        check(
            f"{case['id']} understanding names the requested concept",
            case["metric"].title() in str(u.get("interpretation")),
            str(u.get("interpretation")),
        )


def test_dependency_cases() -> None:
    for case in DEPENDENCY_CASES:
        f = run(case["question"])
        if case.get("status") == "BLOCKED":
            check(
                f"{case['id']} refuses BLOCKED",
                f.get("status") == "BLOCKED" and not f.get("resolved"),
                f"status={f.get('status')}",
            )
            missing = " ".join(str(x) for x in (f.get("_outcome_missing") or []))
            check(
                f"{case['id']} names the formula's own missing dependency",
                all(m in missing for m in case["missing_in"])
                and not any(m in missing for m in case["not_missing_in"]),
                f"missing={missing}",
            )
            check(
                f"{case['id']} carries no fabricated display",
                not _has_display(f.get("_outcome_display")),
                str(f.get("_outcome_display")),
            )
        else:
            check(
                f"{case['id']} resolves via {case['formula']}",
                f.get("status") == "DERIVED"
                and f.get("_outcome_display") == case["display"]
                and f.get("_outcome_formula") == case["formula"],
                f"status={f.get('status')} display={f.get('_outcome_display')}",
            )


def test_refusal_cases() -> None:
    for case in REFUSAL_CASES:
        f = run(case["question"])
        check(
            f"{case['id']} -> {case['status']}",
            f.get("status") == case["status"],
            f"status={f.get('status')} flow={f.get('flow')}",
        )
        check(
            f"{case['id']} carries no confident display",
            not _has_display(f.get("_outcome_display")),
            str(f.get("_outcome_display")),
        )
        if case.get("uncertain"):
            u = f.get("understanding") or {}
            check(
                f"{case['id']} flagged requested_uncertain",
                bool(u.get("requested_uncertain"))
                and f.get("why_not"),
                str(u.get("requested_uncertain")),
            )


def test_echo_gate() -> None:
    for case in ECHO_CASES:
        f = run(case["question"], student_answer=case.get("answer"))
        check(
            f"{case['id']}",
            f.get("status") == case["status"],
            f"status={f.get('status')} display={f.get('_outcome_display')}",
        )
        if case.get("formula"):
            check(
                f"{case['id']} carries the registered formula id",
                f.get("_outcome_formula") == case["formula"],
                str(f.get("_outcome_formula")),
            )


def test_hard_invariants() -> None:
    """The 0%-unsafe release gate across every case in this gate."""
    all_cases = (
        REVERSE_CASES + DEPENDENCY_CASES + REFUSAL_CASES + ECHO_CASES
    )
    bad_echo: List[str] = []       # DERIVED/VERIFIED with formula_id None
    bad_authority: List[str] = []  # resolved without cpp authority
    bad_display: List[str] = []    # refusal carrying a confident display
    bad_det: List[str] = []        # non-deterministic

    for case in all_cases:
        kw = {"student_answer": case.get("answer")}
        f1 = run(case["question"], **kw)
        f2 = run(case["question"], **kw)
        if json.dumps(f1, sort_keys=True, default=str) != \
                json.dumps(f2, sort_keys=True, default=str):
            bad_det.append(case["id"])
        status = f1.get("status")
        if status in ("DERIVED", "VERIFIED"):
            if not f1.get("_outcome_formula"):
                bad_echo.append(case["id"])
            if f1.get("authority_state") != "cpp":
                bad_authority.append(case["id"])
        elif status in ("BLOCKED", "UNSUPPORTED", "REVIEW_REQUIRED"):
            disp = str(f1.get("_outcome_display"))
            if status in ("BLOCKED", "UNSUPPORTED") \
                    and _has_display(f1.get("_outcome_display")):
                bad_display.append(f"{case['id']}({disp})")

    check("invariant: no DERIVED/VERIFIED result with formula_id=None",
          not bad_echo, str(bad_echo))
    check("invariant: every resolved result is C++-authoritative",
          not bad_authority, str(bad_authority))
    check("invariant: BLOCKED/UNSUPPORTED never carry a display",
          not bad_display, str(bad_display))
    check("invariant: deterministic repeatability", not bad_det, str(bad_det))

    # understanding surface: 'Requested:' is explicit and canonical
    u = build_understanding(
        "Find the missing figure: Expenses.\nRevenue: 1,000\nProfit: 200"
    )
    check("understanding shows Requested: Expenses",
          u.get("requested") == "expenses"
          and "Expenses" in str(u.get("interpretation")),
          str(u.get("requested")))


def main() -> int:
    test_reverse_cases()
    test_dependency_cases()
    test_refusal_cases()
    test_echo_gate()
    test_hard_invariants()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"SPRINT 15 STAGE 4 ROUTING GATE: {passed}/{total} checks passed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL - {f}")
        print("=" * 72)
        print("STAGE 4 FAIL - ROUTING RELEASE BLOCKER REMAINS")
        return 1
    print("UNSAFE CONFIDENT ANSWERS: 0 | C++ AUTHORITY VIOLATIONS: 0 | "
          "FABRICATED VALUES: 0 | SILENT SUBSTITUTIONS: 0")
    print("=" * 72)
    print("STAGE 4 PASS - REQUESTED-CONCEPT ROUTING VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
