#!/usr/bin/env python3
"""
Sprint 38 — Automated Runtime Diagnostic
scripts/fte_fyjc_38_ui_runtime_audit.py

Classification: DIAGNOSTIC ONLY — no production behavior changes.

Probes the exact rendering path from _compute_projection through
project_student_result to _is_multi_tx_problem and identifies
whether the whole-problem UI or single-transaction UI is reached.
"""

from __future__ import annotations
import sys
import os
import subprocess

sys.path.insert(0, os.getcwd())

PASS = 0
FAIL = 0
results: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAIL += 1
    else:
        PASS += 1
    results.append(f"  {'✅' if condition else '❌'} [{status}] {label}"
                   + (f" — {detail}" if detail else ""))


def main() -> None:
    global PASS, FAIL

    # ─── 1. LOCAL COMMIT ────────────────────────────────────────────
    local_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    local_branch = subprocess.run(
        ["git", "branch", "--show-current"], capture_output=True, text=True
    ).stdout.strip()
    remote_sha = subprocess.run(
        ["git", "rev-parse", f"origin/{local_branch}"],
        capture_output=True, text=True,
    ).stdout.strip()

    print(f"\nLOCAL_COMMIT:   {local_sha}")
    print(f"LOCAL_BRANCH:   {local_branch}")
    print(f"REMOTE_COMMIT:  {remote_sha}")
    check("Local and remote commits match", local_sha == remote_sha,
          f"local={local_sha[:8]} remote={remote_sha[:8]}")

    # ─── 2. MODULE IMPORT AUDIT ─────────────────────────────────────
    import importlib
    mod = importlib.import_module("backend.fyjc_student_ui")
    module_path = mod.__file__
    print(f"MODULE_PATH:    {module_path}")

    # Check for duplicates
    import glob
    dupes = glob.glob("**/fyjc_student_ui.py", recursive=True)
    check("No duplicate fyjc_student_ui.py", len(dupes) == 1,
          f"found {len(dupes)}: {dupes}")

    # ─── 3. RENDERER PATH AUDIT ─────────────────────────────────────
    import inspect
    import backend.fyjc_student_ui as ui

    # Check _render_problem_workflow
    src_workflow = inspect.getsource(ui._render_problem_workflow)
    has_expanders = "st.expander" in src_workflow and "for i, tx" in src_workflow
    has_all_at_once = "for i, tx in enumerate(txns)" in src_workflow
    has_old_nav = ("st.button" in src_workflow and
                   ("Previous" in src_workflow or "_advance_to_next_tx" in src_workflow))
    print(f"HAS_EXPANDER_RENDERING:   {has_expanders}")
    print(f"HAS_ALL_AT_ONCE:          {has_all_at_once}")
    print(f"HAS_OLD_STEP_BY_STEP_NAV: {has_old_nav}")
    check("Sprint 36/37 renderer exists in source", has_expanders and has_all_at_once)
    check("No old step-by-step navigation in renderer", not has_old_nav)

    # Check _relevant_calc_records
    has_relevant_calc = hasattr(ui, "_relevant_calc_records")
    check("Sprint 37 _relevant_calc_records exists", has_relevant_calc)

    # ─── 4. PROJECTION PATH AUDIT ───────────────────────────────────
    from backend.maths.fyjc_ui_contract import project_student_result

    # Build a synthetic multi-transaction result
    synthetic_result = {
        "status": "VERIFIED",
        "status_label": "Test",
        "debit_lines": [{"account": "Purchases", "amount": 10000}],
        "credit_lines": [{"account": "Rahul", "amount": 10000}],
    }
    synthetic_result["problem_engine"] = {
        "problem_status": "PROBLEM_VERIFIED",
        "transactions": [{"index": 1, "status": "VERIFIED"}],
        "ledger_snapshot": {},
        "deterministic": True,
    }

    projection = project_student_result(synthetic_result, "test")
    has_pe_in_proj = "problem_engine" in projection
    print(f"\nPROBLEM_ENGINE_SURVIVES_PROJECTION: {has_pe_in_proj}")
    check("project_student_result preserves problem_engine key",
          has_pe_in_proj,
          "CRITICAL: if False, whole-problem UI can never be reached")

    # Check _is_multi_tx_problem
    is_multi = ui._is_multi_tx_problem(projection)
    print(f"IS_MULTI_TX_PROBLEM: {is_multi}")
    check("_is_multi_tx_problem returns True when problem_engine is present",
          is_multi)

    # ─── 5. FULL PIPELINE TEST (13-transaction problem) ─────────────
    from backend.maths.fyjc_problem_engine import process_problem

    problem = (
        "On 1st April 2026, Rohan started a business with cash of Rs.1,00,000 "
        "and furniture worth Rs.20,000.\n"
        "On 2nd April, he purchased goods from Amit for Rs.30,000 on credit.\n"
        "On 3rd April, he purchased goods from Raj for Rs.20,000 on credit.\n"
        "On 5th April, he sold goods to Suresh for Rs.40,000 on credit.\n"
        "On 7th April, Suresh paid Rs.20,000 by cheque.\n"
        "On 10th April, Rohan paid Amit Rs.15,000 by cheque.\n"
        "On 12th April, goods worth Rs.5,000 purchased from Raj were returned.\n"
        "On 15th April, Rohan purchased goods for Rs.25,000 plus 18% GST for cash.\n"
        "On 18th April, Rohan sold goods for Rs.30,000 plus 18% GST for cash.\n"
        "On 20th April, Rohan paid the remaining amount due to Amit by cheque "
        "and settled his account in full.\n"
        "On 22nd April, Rohan received Rs.10,000 from Suresh by cheque.\n"
        "On 25th April, Rohan paid Rs.8,000 for office expenses by cash.\n"
        "On 30th April, Rohan withdrew Rs.5,000 cash for personal use."
    )

    engine_result = process_problem(problem)
    tx_count = len(engine_result["transactions"])
    print(f"\nENGINE_TX_COUNT: {tx_count}")
    check("Engine produces 13 transactions", tx_count == 13, f"got {tx_count}")

    # Replicate _compute_projection logic
    verified = [t for t in engine_result["transactions"]
                if t["status"] == "VERIFIED"]
    if verified:
        primary = verified[-1]
        _jnl = primary.get("journal") or {}
        single_result = {
            "status": primary["status"],
            "journal": _jnl,
            "why_not": None,
            "next_action": primary.get("next_action"),
            "debit_lines": _jnl.get("debit_lines", []),
            "credit_lines": _jnl.get("credit_lines", []),
            "calculation_records": _jnl.get("calculation_records", []),
        }
    else:
        primary = engine_result["transactions"][0]
        _jnl = primary.get("journal") or {}
        single_result = {
            "status": primary["status"],
            "journal": _jnl,
            "why_not": primary.get("why_not"),
            "next_action": primary.get("next_action"),
            "debit_lines": _jnl.get("debit_lines", []),
            "credit_lines": _jnl.get("credit_lines", []),
            "calculation_records": _jnl.get("calculation_records", []),
        }

    single_result["problem_engine"] = {
        "problem_status": engine_result["problem_status"],
        "transactions": engine_result["transactions"],
        "ledger_snapshot": engine_result["ledger_snapshot"],
        "deterministic": engine_result["deterministic"],
    }

    full_projection = project_student_result(single_result, problem)
    pe_in_full = "problem_engine" in full_projection
    is_multi_full = ui._is_multi_tx_problem(full_projection)
    print(f"FULL_PIPELINE_PROBLEM_ENGINE: {pe_in_full}")
    print(f"FULL_PIPELINE_IS_MULTI_TX:    {is_multi_full}")
    check("Full pipeline preserves problem_engine", pe_in_full)
    check("Full pipeline routes to whole-problem renderer", is_multi_full)

    # ─── 6. INTEGRITY INVARIANT ─────────────────────────────────────
    from backend.maths.fyjc_ui_contract import validate_problem_integrity
    integrity = validate_problem_integrity(engine_result["transactions"])
    violations = integrity.get("integrity_violations", 0)
    print(f"\nINTEGRITY_VIOLATIONS: {violations}")
    check("No integrity violations in engine output", violations == 0)

    # ─── 7. SPRINT 37 FILTER ───────────────────────────────────────
    drawings_tx = None
    for tx in engine_result["transactions"]:
        if "withdrew" in tx.get("text", "").lower():
            drawings_tx = tx
            break
    if drawings_tx:
        jnl = drawings_tx.get("journal") or {}
        filtered = ui._relevant_calc_records(
            jnl.get("calculation_records", []),
            jnl.get("debit_lines", []),
            jnl.get("credit_lines", []),
        )
        print(f"\nDRAWINGS_CALC_BEFORE: {len(jnl.get('calculation_records', []))}")
        print(f"DRAWINGS_CALC_AFTER:  {len(filtered)}")
        check("Sprint 37 filter suppresses irrelevant calc records for drawings",
              len(filtered) < len(jnl.get("calculation_records", [])))

    # ─── SUMMARY ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SPRINT 38 DIAGNOSTIC SUMMARY")
    print("=" * 60)
    for line in results:
        print(line)
    print(f"\nTOTAL: {PASS} PASS / {FAIL} FAIL")

    # ─── DECISION TREE ──────────────────────────────────────────────
    if not pe_in_full:
        print("\nROOT CAUSE: C — Execution-path mismatch")
        print("project_student_result() strips problem_engine key.")
        print("Sprint 36/37 code exists but the application routes")
        print("through the old single-transaction renderer.")
    else:
        print("\nROOT CAUSE: Not C — problem_engine survives projection.")
        if not is_multi_full:
            print("ROOT CAUSE: F — Cannot reproduce")
        else:
            print("ROOT CAUSE: Rendering path is correct.")
            print("Issue may be Streamlit-specific (D).")

    print(f"\nDEPLOYED_COMMIT:   {remote_sha}")
    print(f"SPRINT36_RENDERER_REACHED: {is_multi_full}")
    print(f"OLD_RENDERER_REACHED: {not is_multi_full}")

    sys.exit(FAIL)


if __name__ == "__main__":
    main()
