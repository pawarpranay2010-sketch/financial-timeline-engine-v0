#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15 - Stage 1: FYJC Real-Question Internal Benchmark Gate
scripts/fte_fyjc_pilot_test.py

Runs all 40 pilot questions (20 Maths + 20 Book-Keeping) through the REAL
student journey (run_fyjc_student_flow), compares the engine's output against
the INDEPENDENT golden benchmark in backend/maths/fyjc_pilot_dataset.py, and
produces the Sprint 15 accuracy matrix, safety-invariant report and verdict.

The benchmark is an oracle: it never calls the solver. Every expected value
is a hand-verified constant. Mismatches are CLASSIFIED (extraction failure /
interpretation failure / accounting reasoning failure / formula gap /
C++ calculation failure / unsafe confident answer / correct refusal), never
silently patched - that is Stage 4.

Exit code: 0 = PASS or CONDITIONAL PASS; 1 = safety invariant violated
(FAIL - not ready for student use).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_pilot_dataset import (  # noqa: E402
    FYJC_PILOT_MATHS,
    FYJC_PILOT_BK,
    pilot_summary,
)
from backend.maths.fyjc_student_flow import run_fyjc_student_flow
from backend.maths.fyjc_accounting import (
    build_trial_balance,
    classify_transaction,
    post_ledger,
    verify_journal_entry,
    verify_ledger_balance,
    verify_trial_balance,
)

PASS_VERDICT = "PASS — READY FOR SMALL FYJC EXPANSION"
CONDITIONAL_VERDICT = "CONDITIONAL PASS — FIX SPECIFIC BLOCKERS"
FAIL_VERDICT = "FAIL — NOT READY FOR STUDENT USE"

EMPTY_DISPLAY = (None, "", "—")

# ---------------------------------------------------------------------------
# small helpers (display comparison only - never computes a result)
# ---------------------------------------------------------------------------


def _num(display: Any) -> Optional[float]:
    """Extract a plain number from a display string like '20.00%' or '—'."""
    if display in EMPTY_DISPLAY:
        return None
    s = str(display).replace("%", "").replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _display_matches(actual: Any, expected: Any) -> bool:
    """Numeric-tolerant display comparison (0.01 tolerance)."""
    na, nb = _num(actual), _num(expected)
    if na is not None and nb is not None:
        return abs(na - nb) < 0.01
    return str(actual).strip() == str(expected).strip()


def _steps_text(flow: Dict[str, Any]) -> str:
    return " ".join(
        str(step.get("body")) for step in (flow.get("steps") or [])
    )


def _has_display(display: Any) -> bool:
    return display not in EMPTY_DISPLAY


# ---------------------------------------------------------------------------
# maths journey runner + evaluator
# ---------------------------------------------------------------------------


def run_maths(case: Dict[str, Any]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    if case.get("facts"):
        kw["facts"] = case["facts"]
    if case.get("documents"):
        kw["documents"] = case["documents"]
    kw["student_answer"] = case.get("student_answer")
    f = run_fyjc_student_flow(case["question"], **kw)
    o = f.get("outcome") or {}
    return {
        "status": f.get("status"),
        "verdict": f.get("verdict"),
        "display": o.get("display_value"),
        "metric": f.get("metric"),
        "missing": o.get("missing"),
        "authority": o.get("authority_state"),
        "formula": o.get("formula_id"),
        "steps": len(f.get("steps") or []),
        "steps_text": _steps_text(f),
    }


def evaluate_maths(case: Dict[str, Any]) -> Dict[str, Any]:
    """Compare one maths case against its independent expectation."""
    r = run_maths(case)
    exp = case["expected"]
    exp_status = exp.get("status")
    exp_metric = case.get("metric")  # independent metric, case-level
    is_refusal = exp_status in ("BLOCKED", "REVIEW_REQUIRED", "UNSUPPORTED")
    row: Dict[str, Any] = {
        "id": case["id"],
        "subject": "Maths",
        "topic": case.get("topic", ""),
        "difficulty": case.get("difficulty"),
        "source": case.get("source_kind"),
        "expected": exp_status,
        "actual": r["status"],
        "engine_metric": r["metric"],
        "display": r["display"],
        "authority": r["authority"],
        "formula": r["formula"],
        "steps": r["steps"],
        "match": False,
        "failure": None,
        "unsafe": False,
    }
    if is_refusal:
        row["match"] = r["status"] == exp_status
        if not row["match"]:
            row["failure"] = (
                f"refusal mismatch (expected {exp_status}, got "
                f"{r['status']})"
            )
        # unsafe = the engine answered confidently when it should refuse
        row["unsafe"] = (
            r["status"] not in ("BLOCKED", "REVIEW_REQUIRED", "UNSUPPORTED")
            and _has_display(r["display"])
        )
        return row

    # resolved expectation (DERIVED + verdict + display)
    metric_ok = r["metric"] == exp_metric
    verdict_ok = r["verdict"] == exp.get("verdict")
    display_ok = _display_matches(r["display"], exp.get("display"))
    resolved = r["status"] in ("DERIVED", "VERIFIED")

    if metric_ok and verdict_ok and display_ok:
        row["match"] = True
        return row

    if resolved and _has_display(r["display"]):
        # the student sees a confident numeric answer
        if display_ok and not metric_ok:
            row["failure"] = (
                f"interpretation failure (metric {r['metric']!r} vs "
                f"{exp_metric!r})"
            )
        else:
            row["failure"] = (
                f"UNSAFE confident answer (metric {r['metric']!r} -> "
                f"{r['display']}, expected {exp_metric!r} = "
                f"{exp.get('display')})"
            )
            row["unsafe"] = True
        return row

    if not metric_ok:
        row["failure"] = (
            f"interpretation + extraction failure (metric {r['metric']!r} "
            f"vs {exp_metric!r})"
        )
    else:
        row["failure"] = "extraction failure (values not read from input)"
    return row


# ---------------------------------------------------------------------------
# book-keeping runner + evaluator
# ---------------------------------------------------------------------------


def build_entries(transactions: List[str]) -> Tuple[Optional[List[Dict]], Optional[Dict]]:
    """Journal the transactions through the engine and return ledger entries."""
    entries: List[Dict[str, Any]] = []
    for tx in transactions or []:
        c = classify_transaction(tx, None)
        if c.get("status") != "VERIFIED":
            return None, {"tx": tx, "status": c.get("status"),
                          "why": c.get("why_not")}
        entries.append({
            "debits": [
                {"account": line.get("account"), "amount": line.get("amount")}
                for line in c.get("debit_lines") or []
            ],
            "credits": [
                {"account": line.get("account"), "amount": line.get("amount")}
                for line in c.get("credit_lines") or []
            ],
        })
    return entries, None


def _accounts_in_steps(steps_text: str, accounts) -> bool:
    return all(str(a).lower() in steps_text.lower() for a in accounts)


def evaluate_bk(case: Dict[str, Any]) -> Dict[str, Any]:
    exp = case["expected"]
    kind = case.get("kind", "transaction")
    row: Dict[str, Any] = {
        "id": case["id"],
        "subject": "Book-Keeping",
        "topic": case.get("topic", ""),
        "difficulty": case.get("difficulty"),
        "source": case.get("source_kind"),
        "expected": exp.get("status") or exp.get("verdict"),
        "actual": "",
        "match": False,
        "failure": None,
        "unsafe": False,
    }

    if kind == "transaction":
        f = run_fyjc_student_flow(case["question"])
        row["actual"] = f.get("status")
        steps = _steps_text(f)
        status_ok = f.get("status") == exp.get("status")
        accounts_ok = (
            _accounts_in_steps(steps, exp.get("debit") or [])
            and _accounts_in_steps(steps, exp.get("credit") or [])
        )
        row["match"] = status_ok and accounts_ok
        if not row["match"]:
            row["failure"] = (
                f"accounting reasoning failure (status {f.get('status')}, "
                f"accounts in steps: {accounts_ok})"
            )
        return row

    if kind in ("ledger", "trial_balance"):
        entries, err = build_entries(case.get("transactions") or [])
        if err:
            row["actual"] = err["status"]
            row["failure"] = (
                f"accounting reasoning failure (transaction not classified: "
                f"{err['tx'][:40]}...)"
            )
            return row
        if kind == "ledger":
            pl = post_ledger(entries)
            row["actual"] = "balanced" if pl.get("balanced") else "unbalanced"
            checks = []
            for acct, want in (exp.get("balances") or {}).items():
                got = pl.get("accounts", {}).get(acct)
                if not got:
                    checks.append(f"{acct}:missing")
                    continue
                ok_amt = abs(float(got.get("balance")) - want["balance"]) < 0.01
                ok_side = got.get("balance_side") == want["side"]
                if not (ok_amt and ok_side):
                    checks.append(
                        f"{acct}:got {got.get('balance')}{got.get('balance_side')}"
                    )
            ok_totals = (
                abs(pl.get("total_debit", 0) - exp["total_debit"]) < 0.01
                and abs(pl.get("total_credit", 0) - exp["total_credit"]) < 0.01
            )
            row["match"] = (
                bool(pl.get("balanced")) and not checks and ok_totals
            )
            if not row["match"]:
                row["failure"] = (
                    f"ledger mismatch (checks={checks}, totals_ok={ok_totals})"
                )
        else:
            tb = build_trial_balance(entries)
            row["actual"] = "balanced" if tb.get("balanced") else "unbalanced"
            ok_totals = (
                abs(tb.get("total_debit", 0) - exp["total_debit"]) < 0.01
                and abs(tb.get("total_credit", 0) - exp["total_credit"]) < 0.01
            )
            row["match"] = bool(tb.get("balanced")) and ok_totals
            if not row["match"]:
                row["failure"] = (
                    f"trial balance mismatch (Dr {tb.get('total_debit')} / "
                    f"Cr {tb.get('total_credit')}, totals_ok={ok_totals})"
                )
        return row

    if kind == "verify_tb":
        entries, err = build_entries(case.get("transactions") or [])
        if err:
            row["failure"] = "accounting reasoning failure (transactions)"
            return row
        rows = []
        for line in (case.get("student_tb") or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3:
                rows.append({
                    "account": parts[0],
                    "debit": float(parts[1] or 0),
                    "credit": float(parts[2] or 0),
                })
        vr = verify_trial_balance(rows, entries)
        row["actual"] = vr.get("verdict")
        row["match"] = vr.get("verdict") == exp.get("verdict")
        if not row["match"]:
            row["failure"] = f"verify_tb verdict {vr.get('verdict')}"
        return row

    if kind == "verify_journal":
        entry = {
            "debits": [
                {"account": a, "amount": amt}
                for a, amt in case.get("student_debits") or []
            ],
            "credits": [
                {"account": a, "amount": amt}
                for a, amt in case.get("student_credits") or []
            ],
        }
        vr = verify_journal_entry(case.get("description"), entry)
        row["actual"] = vr.get("verdict")
        row["match"] = vr.get("verdict") == exp.get("verdict")
        if not row["match"]:
            row["failure"] = f"verify_journal verdict {vr.get('verdict')}"
        return row

    if kind == "verify_ledger":
        entries, err = build_entries(case.get("transactions") or [])
        if err:
            row["failure"] = "accounting reasoning failure (transactions)"
            return row
        vr = verify_ledger_balance(
            case.get("student_account"), case.get("student_balance"),
            case.get("student_side"), entries)
        row["actual"] = vr.get("verdict")
        row["match"] = vr.get("verdict") == exp.get("verdict")
        if not row["match"]:
            row["failure"] = f"verify_ledger verdict {vr.get('verdict')}"
        return row

    row["failure"] = f"unknown kind {kind}"
    return row


# ---------------------------------------------------------------------------
# safety invariants (12) - these must ALWAYS hold
# ---------------------------------------------------------------------------


def check_safety(matrices: Dict[str, List[Dict]], maths_runs) -> List[Dict]:
    inv: List[Dict[str, Any]] = []

    # 1+2. C++ remains the mathematical authority / no Python fallback:
    #      every resolved output carries authority_state == cpp. (A
    #      'fact-echo' resolve may have no formula_id - no arithmetic ran -
    #      but the value still comes from the C++-authoritative graph.)
    bad = [
        m["id"] for m in matrices["maths"]
        if m["actual"] in ("DERIVED", "VERIFIED")
        and m.get("authority") != "cpp"
    ]
    inv.append({
        "name": "1+2. C++ authority / no Python fallback",
        "ok": not bad,
        "detail": f"resolved outputs without cpp authority state: {bad}",
    })

    # 3. No unsupported formula executed: a resolved output either ran a
    #    registered formula (uppercase ID) or re-used a stated input
    #    (formula_id None) - never an invented formula.
    bad = [
        m["id"] for m in matrices["maths"]
        if m["actual"] in ("DERIVED", "VERIFIED")
        and m.get("formula") and not str(m.get("formula")).isupper()
    ]
    inv.append({
        "name": "3. No unsupported formula executed",
        "ok": not bad, "detail": f"non-registered formula ids: {bad}",
    })

    # 1. No fabricated values: BLOCKED / UNSUPPORTED never carry a display
    #    (REVIEW_REQUIRED may carry a 'computed but never presented as
    #    verified' value - it is refused as an answer)
    bad = [
        m["id"] for m in matrices["maths"]
        if m["actual"] in ("BLOCKED", "UNSUPPORTED")
        and _has_display(m["display"])
    ]
    inv.append({
        "name": "1. No fabricated values on BLOCKED / UNSUPPORTED",
        "ok": not bad,
        "detail": f"refusals carrying a display: {bad}",
    })

    # 4+9+10. Conflicting evidence -> REVIEW_REQUIRED, never merged
    p19 = next(m for m in matrices["maths"] if m["id"] == "P19")
    inv.append({
        "name": "4/9/10. Conflicting facts -> REVIEW_REQUIRED, never merged",
        "ok": p19["actual"] == "REVIEW_REQUIRED" and not p19["unsafe"],
        "detail": f"P19 -> {p19['actual']}",
    })

    # 5. Unsupported question types stay UNSUPPORTED (P12..P17)
    bad = [
        m["id"] for m in matrices["maths"]
        if m["id"] in ("P12", "P13", "P14", "P15", "P16", "P17")
        and m["actual"] != "UNSUPPORTED"
    ]
    inv.append({
        "name": "5. Unsupported topics stay UNSUPPORTED",
        "ok": not bad, "detail": f"not refused: {bad}",
    })

    # 6. Missing inputs -> BLOCKED
    p18 = next(m for m in matrices["maths"] if m["id"] == "P18")
    b17 = next(m for m in matrices["bk"] if m["id"] == "B17")
    inv.append({
        "name": "6. Missing inputs -> BLOCKED",
        "ok": p18["actual"] == "BLOCKED" and b17["actual"] == "BLOCKED",
        "detail": f"P18 -> {p18['actual']}, B17 -> {b17['actual']}",
    })

    # 7. Zero denominator -> BLOCKED (no division by zero, no guess)
    p20 = next(m for m in matrices["maths"] if m["id"] == "P20")
    inv.append({
        "name": "7. Zero denominator -> BLOCKED",
        "ok": p20["actual"] == "BLOCKED" and not p20["unsafe"],
        "detail": f"P20 -> {p20['actual']}",
    })

    # 8. REVIEW_REQUIRED stays REVIEW_REQUIRED (B18 ambiguous, B19 discount)
    b18 = next(m for m in matrices["bk"] if m["id"] == "B18")
    b19 = next(m for m in matrices["bk"] if m["id"] == "B19")
    inv.append({
        "name": "8. Ambiguous transactions stay REVIEW_REQUIRED",
        "ok": b18["actual"] == "REVIEW_REQUIRED"
        and b19["actual"] == "REVIEW_REQUIRED",
        "detail": f"B18 -> {b18['actual']}, B19 -> {b19['actual']}",
    })

    # 11. Determinism: identical input -> identical output
    det = _check_determinism(maths_runs)
    inv.append({
        "name": "11. Deterministic repeatability",
        "ok": det["ok"], "detail": det["detail"],
    })

    # 10/12. Student-visible reasoning matches the execution path: every
    # resolved maths flow emits the 6-step student journey
    # (Given / Required / Formula / Substitution / C++ / Final Answer).
    bad = [
        m["id"] for m in matrices["maths"]
        if m["actual"] in ("DERIVED", "VERIFIED") and m.get("steps") != 6
    ]
    inv.append({
        "name": "10/12. Student-visible reasoning (6-step journey) matches",
        "ok": not bad, "detail": f"flows missing the 6-step journey: {bad}",
    })

    return inv


def _check_determinism(maths_runs) -> Dict[str, Any]:
    """Re-run every resolved-supported maths case and compare fingerprints."""
    bad = []
    for case in FYJC_PILOT_MATHS:
        exp = case.get("expected") or {}
        if exp.get("status") in ("BLOCKED", "REVIEW_REQUIRED", "UNSUPPORTED"):
            continue
        f1 = run_maths(case)
        f2 = run_maths(case)
        fp1 = (f1["status"], f1["verdict"], f1["display"], f1["metric"])
        fp2 = (f2["status"], f2["verdict"], f2["display"], f2["metric"])
        if fp1 != fp2:
            bad.append(case["id"])
    return {
        "ok": not bad,
        "detail": f"non-deterministic cases: {bad}" if bad else "all identical",
    }


# ---------------------------------------------------------------------------
# metrics + report
# ---------------------------------------------------------------------------


def compute_metrics(matrices: Dict[str, List[Dict]]) -> Dict[str, Any]:
    ms = matrices["maths"]
    bs = matrices["bk"]

    supported_expected = [m for m in ms if m["expected"] not in (
        "BLOCKED", "REVIEW_REQUIRED", "UNSUPPORTED")]
    supported_ok = [m for m in supported_expected if m["match"]]
    refusal_expected = [m for m in ms if m["expected"] in (
        "BLOCKED", "REVIEW_REQUIRED", "UNSUPPORTED")]
    refusal_ok = [m for m in refusal_expected if m["match"]]

    unsafe = [m for m in ms if m["unsafe"]] + [m for m in bs if m["unsafe"]]
    bk_ok = [m for m in bs if m["match"]]

    return {
        "maths_supported_accuracy": (
            len(supported_ok), len(supported_expected)),
        "maths_correct_refusal_rate": (
            len(refusal_ok), len(refusal_expected)),
        "bookkeeping_accuracy": (len(bk_ok), len(bs)),
        "unsafe_confident_answers": len(unsafe),
        "unsafe_ids": [m["id"] for m in unsafe],
        "cpp_match_rate": (
            sum(1 for m in ms
                if m["actual"] in ("DERIVED", "VERIFIED")
                and m.get("authority") == "cpp"),
            sum(1 for m in ms if m["actual"] in ("DERIVED", "VERIFIED")),
        ),
    }


def print_matrix(matrices: Dict[str, List[Dict]]) -> None:
    print("\n=== ACCURACY MATRIX ===")
    hdr = (f"{'ID':<5}{'Subject':<14}{'Topic':<26}{'Src':<8}{'Exp':<16}"
           f"{'Actual':<16}{'Match':<6}Failure")
    print(hdr)
    print("-" * len(hdr))
    for m in matrices["maths"] + matrices["bk"]:
        failure = (m["failure"] or "")[:60]
        print(f"{m['id']:<5}{m['subject']:<14}{(m['topic'] or '')[:25]:<26}"
              f"{m['source']:<8}{str(m['expected']):<16}{str(m['actual']):<16}"
              f"{'OK' if m['match'] else '--':<6}{failure}")


def main() -> int:
    matrices = {"maths": [], "bk": []}
    for case in FYJC_PILOT_MATHS:
        matrices["maths"].append(evaluate_maths(case))
    for case in FYJC_PILOT_BK:
        matrices["bk"].append(evaluate_bk(case))

    print_matrix(matrices)
    invs = check_safety(matrices, None)
    metrics = compute_metrics(matrices)

    print("\n=== SAFETY INVARIANTS (must always hold) ===")
    for inv in invs:
        print(f"  [{'PASS' if inv['ok'] else 'FAIL'}] {inv['name']} — "
              f"{inv['detail']}")

    print("\n=== SPRINT 15 METRICS (Stage 1) ===")
    for key, val in metrics.items():
        print(f"  {key}: {val}")
    print("\nDataset:", pilot_summary())

    inv_failed = [i for i in invs if not i["ok"]]
    unsafe_count = metrics["unsafe_confident_answers"]
    supported, supported_total = metrics["maths_supported_accuracy"]
    ref_ok, ref_total = metrics["maths_correct_refusal_rate"]
    bk_ok, bk_total = metrics["bookkeeping_accuracy"]

    if inv_failed:
        verdict = FAIL_VERDICT
        exit_code = 1
        blockers = [i["name"] for i in inv_failed]
    elif unsafe_count > 0:
        verdict = CONDITIONAL_VERDICT
        exit_code = 0
        blockers = [
            f"UNSAFE confident answer on {', '.join(metrics['unsafe_ids'])}"
            " (wrong metric answered confidently)",
            "ROE/EPS/missing-figure/reverse questions route to the wrong "
            "metric (P05, P06, P07, P08, P18) - classifier must resolve the "
            "requested metric before solving",
        ]
    elif supported < supported_total or ref_ok < ref_total or bk_ok < bk_total:
        verdict = CONDITIONAL_VERDICT
        exit_code = 0
        blockers = ["supported-question accuracy below 100% - see matrix"]
    else:
        verdict = PASS_VERDICT
        exit_code = 0
        blockers = []

    print(f"\n=== VERDICT: {verdict} ===")
    if blockers:
        print("Blockers:")
        for b in blockers:
            print(f"  - {b}")

    # summary line for gate runners
    print(
        f"\nSUMMARY: maths {supported}/{supported_total} supported, "
        f"refusal {ref_ok}/{ref_total}, bk {bk_ok}/{bk_total}, "
        f"unsafe {unsafe_count}, invariants "
        f"{len(invs) - len(inv_failed)}/{len(invs)}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
