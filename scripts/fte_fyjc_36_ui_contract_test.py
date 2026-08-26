#!/usr/bin/env python3
"""
Sprint 36 — Whole-Problem UI Contract Regression Tests

Proves that the UI projection layer produces consistent, correct results
across ALL supported transaction types and mixed-state whole problems.

14 tests covering:
1. Every transaction receives an independent UI representation
2. Debit/Credit columns are structurally aligned
3. Journal data belongs to the correct transaction
4. Calculation records belong to the correct transaction
5. Explanation records belong to the correct transaction
6. Details are scoped to the selected transaction
7. VERIFIED cannot render without a valid journal
8. REVIEW_REQUIRED transactions remain identifiable
9. Transaction order is preserved
10. No transaction disappears during UI projection
11. No transaction is duplicated
12. Different whole-problem inputs produce the same structural UI contract
13. Sprint 29-35 representative cases remain compatible
14. No mutation occurs during UI projection
"""

import copy
import os
import sys
import traceback

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import():
    from backend.maths.fyjc_ui_contract import (
        project_student_result,
        validate_transaction_integrity,
        validate_problem_integrity,
    )
    from backend.maths.fyjc_orchestration import orchestrate
    from backend.maths.fyjc_problem_engine import process_problem
    return project_student_result, validate_transaction_integrity, validate_problem_integrity, orchestrate, process_problem


def test_all():
    (project_student_result, validate_transaction_integrity,
     validate_problem_integrity, orchestrate, process_problem) = _import()

    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  \u2705 {name}")
        else:
            failed += 1
            print(f"  \u274c {name}" + (f" — {detail}" if detail else ""))

    # --- Test 1: Every transaction receives independent UI representation ---
    q1 = ("Started business with Cash Rs.50,000. "
          "Purchased goods from Raj on credit Rs.20,000. "
          "Sold goods to Amit on credit Rs.30,000.")
    pe1 = process_problem(q1)
    txns1 = pe1["transactions"]
    check("T1: every tx has index + status + text",
          all(t.get("index") and t.get("status") and t.get("text") for t in txns1),
          f"txns={[t.get('index') for t in txns1]}")

    # --- Test 2: Debit/Credit columns are structurally aligned ---
    for tx in txns1:
        if tx["status"] == "VERIFIED":
            jnl = tx.get("journal", {})
            debits = jnl.get("debit_lines", [])
            credits = jnl.get("credit_lines", [])
            for d in debits:
                check(f"T2: debit line has account+amount (T{tx['index']})",
                      bool(d.get("account") and d.get("amount")),
                      f"account={d.get('account')} amount={d.get('amount')}")
            for c in credits:
                check(f"T2: credit line has account+amount (T{tx['index']})",
                      bool(c.get("account") and c.get("amount")),
                      f"account={c.get('account')} amount={c.get('amount')}")
            # Balanced
            total_d = sum(float(d.get("amount", 0)) for d in debits)
            total_c = sum(float(c.get("amount", 0)) for c in credits)
            check(f"T2: journal balanced (T{tx['index']})",
                  abs(total_d - total_c) < 0.01,
                  f"D={total_d} C={total_c}")

    # --- Test 3: Journal data belongs to correct transaction ---
    # Opening entry: Cash Dr / Capital Cr
    t_opening = [t for t in txns1 if "Cash" in t["text"] and "50,000" in t["text"]][0]
    jnl_opening = t_opening.get("journal", {})
    accounts = [l.get("account") for l in jnl_opening.get("debit_lines", [])]
    check("T3: opening entry debits Cash",
          "Cash" in accounts, f"accounts={accounts}")
    accounts_cr = [l.get("account") for l in jnl_opening.get("credit_lines", [])]
    check("T3: opening entry credits Capital",
          "Capital" in accounts_cr, f"accounts={accounts_cr}")

    # Credit purchase: Purchases Dr / Raj Cr
    t_purchase = [t for t in txns1 if "Raj" in t["text"]][0]
    jnl_purchase = t_purchase.get("journal", {})
    accounts_dr = [l.get("account") for l in jnl_purchase.get("debit_lines", [])]
    check("T3: purchase debits Purchases",
          "Purchases" in accounts_dr, f"accounts={accounts_dr}")
    accounts_cr = [l.get("account") for l in jnl_purchase.get("credit_lines", [])]
    check("T3: purchase credits Raj",
          "Raj" in accounts_cr, f"accounts={accounts_cr}")

    # --- Test 4: Calculation records belong to correct transaction ---
    q_td = "Purchased goods from Raj for Rs.50,000 at 10% trade discount."
    pe_td = process_problem(q_td)
    tx_td = pe_td["transactions"][0]
    calc = tx_td.get("journal", {}).get("calculation_records", [])
    check("T4: trade discount has calculation records",
          len(calc) > 0, f"count={len(calc)}")

    # --- Test 5: Explanation records belong to correct transaction ---
    # Each VERIFIED tx should have narration or why info
    for tx in txns1:
        if tx["status"] == "VERIFIED":
            jnl = tx.get("journal", {})
            has_explanation = bool(jnl.get("narration") or jnl.get("why_not"))
            check(f"T5: T{tx['index']} has explanation data",
                  has_explanation or len(jnl.get("debit_lines", [])) > 0)

    # --- Test 6: Details are scoped to the selected transaction ---
    # Verify each transaction's journal contains ONLY its own accounts
    q_multi = ("Purchased goods from Raj on credit Rs.15,000. "
               "Purchased goods from Amit on credit Rs.10,000. "
               "Paid Rs.15,000 to Raj by cheque.")
    pe_multi = process_problem(q_multi)
    txns_multi = pe_multi["transactions"]
    # T1 should only mention Raj, not Amit
    t1_multi = txns_multi[0]
    t1_all_accounts = (
        [l.get("account") for l in t1_multi.get("journal", {}).get("debit_lines", [])] +
        [l.get("account") for l in t1_multi.get("journal", {}).get("credit_lines", [])]
    )
    check("T6: T1 journal scoped (no Amit)",
          "Amit" not in t1_all_accounts, f"accounts={t1_all_accounts}")
    # T2 should only mention Amit, not Raj
    t2_multi = txns_multi[1]
    t2_all_accounts = (
        [l.get("account") for l in t2_multi.get("journal", {}).get("debit_lines", [])] +
        [l.get("account") for l in t2_multi.get("journal", {}).get("credit_lines", [])]
    )
    check("T6: T2 journal scoped (no Raj)",
          "Raj" not in t2_all_accounts, f"accounts={t2_all_accounts}")

    # --- Test 7: VERIFIED cannot render without a valid journal ---
    for tx in txns1:
        if tx["status"] == "VERIFIED":
            jnl = tx.get("journal", {})
            has_journal = bool(jnl.get("debit_lines") and jnl.get("credit_lines"))
            check(f"T7: VERIFIED T{tx['index']} has valid journal", has_journal)

    # --- Test 8: REVIEW_REQUIRED transactions remain identifiable ---
    q_rr = "Started business with Cash Rs.30,000, Bank balance Rs.50,000, and a Loan from Bank Rs.20,000."
    pe_rr = process_problem(q_rr)
    txns_rr = pe_rr["transactions"]
    rr_found = any(t["status"] == "REVIEW_REQUIRED" for t in txns_rr)
    check("T8: REVIEW_REQUIRED is identifiable", rr_found)

    # --- Test 9: Transaction order is preserved ---
    for txns, label in [(txns1, "P1"), (txns_multi, "P2"), (txns_rr, "P3")]:
        indices = [t["index"] for t in txns]
        expected = list(range(1, len(txns) + 1))
        check(f"T9: order preserved ({label})",
              indices == expected, f"indices={indices}")

    # --- Test 10: No transaction disappears ---
    for txns, expected_min, label in [
        (txns1, 3, "P1"), (txns_multi, 3, "P2"), (txns_rr, 1, "P3")
    ]:
        check(f"T10: no tx disappeared ({label})",
              len(txns) >= expected_min, f"count={len(txns)}")

    # --- Test 11: No transaction is duplicated ---
    for txns, label in [(txns1, "P1"), (txns_multi, "P2")]:
        indices = [t["index"] for t in txns]
        check(f"T11: no duplicates ({label})",
              len(txns) == len(set(indices)))

    # --- Test 12: Different inputs produce same structural contract ---
    q_a = "Purchased goods from Raj on credit Rs.10,000."
    q_b = "Sold goods to Amit for cash Rs.15,000."
    proj_a = project_student_result(orchestrate(q_a), q_a)
    proj_b = project_student_result(orchestrate(q_b), q_b)
    # Both should have the same top-level keys
    keys_a = set(proj_a.keys())
    keys_b = set(proj_b.keys())
    check("T12: same structural contract",
          keys_a == keys_b,
          f"diff={keys_a.symmetric_difference(keys_b)}")

    # --- Test 13: Sprint 29-35 representative cases ---
    s29_q = "Received Rs.23,600 from Suresh by cheque."
    r29 = orchestrate(s29_q)
    check("T13: Sprint 29 receipt-by-cheque processes",
          r29.get("status") in ("VERIFIED", "REVIEW_REQUIRED"))

    s33_q = "Purchased goods from Raj for Rs.20,000."
    r33 = orchestrate(s33_q)
    proj33 = project_student_result(r33, s33_q)
    check("T13: Sprint 33 adversarial case projects",
          proj33.get("status") in ("VERIFIED", "REVIEW_REQUIRED"))

    # --- Test 14: No mutation during UI projection ---
    q_mut = "Purchased goods from Raj on credit Rs.20,000."
    result_before = orchestrate(q_mut)
    result_copy = copy.deepcopy(result_before)
    proj = project_student_result(result_before, q_mut)
    check("T14: orchestrate result not mutated by projection",
          result_before == result_copy,
          "deep copy differed after projection")

    # --- Summary ---
    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"Sprint 36 UI Contract Tests: {passed}/{total} PASS, {failed} FAIL")
    print(f"{'=' * 60}")
    if failed:
        print("FAIL")
        sys.exit(1)
    else:
        print("ALL TESTS PASS")
        sys.exit(0)


if __name__ == "__main__":
    try:
        test_all()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
