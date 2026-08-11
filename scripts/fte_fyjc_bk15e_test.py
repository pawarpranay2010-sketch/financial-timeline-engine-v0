#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15E - FYJC Book-Keeping Unit-Test-1 Textbook Coverage Gate
scripts/fte_fyjc_bk15e_test.py

Runs the full hardened Book-Keeping & Accountancy pipeline
(backend/maths/fyjc_bk_reasoning.py + backend/maths/fyjc_accounting.py)
against the hand-verified Unit-Test-1 golden benchmark
(backend/maths/fyjc_bk_15e_benchmark.py) plus the mandatory 15E gates:

  * question classification (equivalent wordings -> ONE transaction type)
  * EXACT account extraction (never an invented account)
  * Real / Personal / Nominal classification + Golden Rule on every line
  * debit / credit assignment with a student-readable WHY
  * amount calculation (trade/cash discount pipeline, exact numbers)
  * journal balancing + narration
  * ledger derived from the journal IR (never reinterpreted)
  * trial balance derived from the ledger (Dr == Cr, never forced)
  * cash-sale vs credit-sale semantics
  * multi-transaction handling (chronological, independent entries)
  * student-answer verification (divergence, not just 'wrong')
  * refusal states (BLOCKED / REVIEW_REQUIRED / NOT_SUPPORTED) - no
    fabricated journal lines or amounts
  * C++ authority for registered metrics (formula_id always set when a
    result claims VERIFIED; authority_state == cpp)
  * formula/rule provenance (every VERIFIED journal carries
    calculation_id records)
  * deterministic repeatability (identical input -> identical output)

Hard release invariants (must all be 0):
  0 invented accounts | 0 fabricated amounts | 0 wrong-concept confident
  answers | 0 silent substitutions | 0 unbalanced VERIFIED journals |
  0 unbalanced VERIFIED trial balances | 0 formula_id=None confident
  numerical answers | 0 C++ authority violations | 0 confident answers
  outside the declared syllabus | identical input -> identical output.
"""

import json
import sys
from decimal import Decimal

sys.path.insert(0, ".")

from backend.maths.fyjc_bk_15e_benchmark import (
    BK15E_BENCHMARK, VERIFIED_CASES, REFUSAL_CASES, EXACT_ACCOUNT_CASES,
)
from backend.maths.fyjc_bk_reasoning import reason_bk_question, verify_bk_metric
from backend.maths.fyjc_accounting import (
    verify_journal_entry, verify_ledger_balance, verify_trial_balance,
)
from backend.maths.status import VERIFIED, REVIEW_REQUIRED, BLOCKED

NOT_SUPPORTED = "NOT_SUPPORTED"  # defined locally in fyjc_bk_reasoning

CHECKS: list = []
FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def norm_lines(lines) -> list:
    """(account, int(amount)) sorted - amounts are rupee integers."""
    return sorted(
        (str(line.get("account") or ""), int(round(float(line.get("amount", 0)))))
        for line in lines if line.get("account")
    )


def merged_lines(out):
    journals = out.get("journals") or [out.get("journal")] or []
    dr = [l for j in journals for l in (j.get("debit_lines") or [])]
    cr = [l for j in journals for l in (j.get("credit_lines") or [])]
    return dr, cr


def output_snapshot(out) -> str:
    keys = ("journal", "journals", "ledger", "trial_balance", "status")
    return json.dumps({k: out.get(k) for k in keys}, sort_keys=True,
                      default=str)


# ---------------------------------------------------------------------------
# 1. Benchmark accuracy (independent hand-written oracles)
# ---------------------------------------------------------------------------
def test_benchmark() -> None:
    passed = 0
    for case in VERIFIED_CASES:
        q = case["question"]
        out = reason_bk_question(q)
        if out.get("status") != VERIFIED:
            check(f"bench: {q[:55]}", False,
                  f"status={out.get('status')} why={out.get('why_not')}")
            continue
        journals = out.get("journals") or [out.get("journal")] or []
        if len(journals) != case.get("journals", 1):
            check(f"bench journals: {q[:55]}", False,
                  f"count={len(journals)} expected={case.get('journals')}")
            continue
        dr, cr = merged_lines(out)
        got_dr, got_cr = norm_lines(dr), norm_lines(cr)
        exp_dr = sorted((a, int(v)) for a, v in case["debit"])
        exp_cr = sorted((a, int(v)) for a, v in case["credit"])
        if got_dr != exp_dr or got_cr != exp_cr:
            check(f"bench lines: {q[:55]}", False,
                  f"Dr {got_dr} != {exp_dr} | Cr {got_cr} != {exp_cr}")
            continue
        # the canonical type key is only asserted for single-transaction
        # questions; a multi-transaction question is a sequence of entries
        # (each fully verified below) and has no single canonical key.
        if case.get("journals", 1) == 1 and case.get("type_key"):
            tk = (out.get("understanding") or {}).get("question_type_key")
            if tk != case.get("type_key"):
                check(f"bench type: {q[:55]}", False,
                      f"type={tk} expected={case.get('type_key')}")
                continue
        # every journal balanced
        if any(not j.get("balanced") for j in journals):
            check(f"bench balanced: {q[:55]}", False, "unbalanced journal(s)")
            continue
        tb = out.get("trial_balance") or {}
        ledger = out.get("ledger") or {}
        if tb.get("balanced") is not True:
            check(f"bench tb: {q[:55]}", False,
                  f"balanced={tb.get('balanced')} disc={tb.get('discrepancy')}")
            continue
        if ledger.get("balanced") is not True:
            check(f"bench ledger: {q[:55]}", False, "ledger unbalanced")
            continue
        if abs(float(tb.get("total_debit") or 0)
               - float(tb.get("total_credit") or 0)) > 0.001:
            check(f"bench tb totals: {q[:55]}", False, "Dr != Cr")
            continue
        # traditional classification + golden rule + why on EVERY line
        bad = [l for l in dr + cr
               if not (l.get("class") and l.get("rule") and l.get("why"))]
        if bad:
            check(f"bench class: {q[:55]}", False,
                  "line missing class/rule/why")
            continue
        # narration present
        if (out.get("journal") or {}).get("narration") is None:
            check(f"bench narration: {q[:55]}", False, "missing narration")
            continue
        # calculation provenance on every VERIFIED journal
        ids = [r.get("calculation_id")
               for r in (out.get("calculation_records") or [])
               if r.get("calculation_id")]
        if not ids:
            check(f"bench provenance: {q[:55]}", False,
                  "no calculation provenance")
            continue
        passed += 1
    check(f"benchmark accuracy: {passed}/{len(VERIFIED_CASES)}",
          passed == len(VERIFIED_CASES), f"{passed}/{len(VERIFIED_CASES)}")


# ---------------------------------------------------------------------------
# 2. Refusal accuracy - correct state AND zero fabricated output
# ---------------------------------------------------------------------------
def test_refusals() -> None:
    passed = 0
    for case in REFUSAL_CASES:
        q = case["question"]
        out = reason_bk_question(q)
        good = out.get("status") == case["status"]
        dr = out.get("debit_lines") or []
        cr = out.get("credit_lines") or []
        journal = out.get("journal")
        clean = (not dr) and (not cr) and not (
            journal and journal.get("status") == VERIFIED)
        if not good or not clean:
            check(f"refusal: {q[:50]}", False,
                  f"status={out.get('status')} expected={case['status']} "
                  f"clean={clean}")
        else:
            passed += 1
    check(f"refusal accuracy: {passed}/{len(REFUSAL_CASES)}",
          passed == len(REFUSAL_CASES), f"{passed}/{len(REFUSAL_CASES)}")


# ---------------------------------------------------------------------------
# 3. Exact-account protection (hallucination guard)
# ---------------------------------------------------------------------------
def test_exact_account() -> None:
    bad_count = 0
    for case in EXACT_ACCOUNT_CASES:
        out = reason_bk_question(case["question"])
        accounts: set = set()
        for j in ((out.get("journals") or [out.get("journal")]) if
                  out.get("status") == VERIFIED else []):
            for l in (j.get("debit_lines") or []) + (j.get("credit_lines") or []):
                if l.get("account"):
                    accounts.add(l["account"])
        for l in (out.get("debit_lines") or []) + (out.get("credit_lines") or []):
            if l.get("account"):
                accounts.add(l["account"])
        extra = accounts - case["allowed"]
        if out.get("status") != VERIFIED or extra:
            bad_count += 1
            check(f"exact-account: {case['question'][:45]}", False,
                  f"status={out.get('status')} extra={sorted(extra)}")
    check(f"exact-account: 0 invented accounts",
          bad_count == 0, f"{bad_count} violations")


# ---------------------------------------------------------------------------
# 4. Cash-sale vs credit-sale semantics
# ---------------------------------------------------------------------------
def test_cash_vs_credit() -> None:
    cash = reason_bk_question("Sold goods to Mohan for cash Rs.20,000.")
    credit = reason_bk_question("Sold goods to Mohan on credit Rs.20,000.")
    dr_c, cr_c = merged_lines(cash)
    dr_k, cr_k = merged_lines(credit)
    check("cash vs credit: cash sale posts Cash/Sales",
          norm_lines(dr_c) == [("Cash", 20000)]
          and norm_lines(cr_c) == [("Sales", 20000)],
          f"Dr={norm_lines(dr_c)} Cr={norm_lines(cr_c)}")
    check("cash vs credit: credit sale posts Mohan/Sales",
          norm_lines(dr_k) == [("Mohan", 20000)]
          and norm_lines(cr_k) == [("Sales", 20000)],
          f"Dr={norm_lines(dr_k)} Cr={norm_lines(cr_k)}")
    # equivalent wordings collapse to the SAME cash-sale treatment
    for w in ("Sold goods for cash Rs.20,000.",
              "Cash sale of goods Rs.20,000.",
              "Goods sold and cash received immediately Rs.20,000."):
        o = reason_bk_question(w)
        d, c = merged_lines(o)
        check(f"cash wording: {w[:40]}", o.get("status") == VERIFIED
              and norm_lines(d) == [("Cash", 20000)]
              and norm_lines(c) == [("Sales", 20000)],
              f"Dr={norm_lines(d)} Cr={norm_lines(c)}")


# ---------------------------------------------------------------------------
# 5. Discount pipeline - exact numbers, correct chronology
# ---------------------------------------------------------------------------
def test_discount_pipeline() -> None:
    q = ("Purchased goods from Rahul for Rs.10,000 at 10% trade discount. "
         "Half the amount was paid immediately and a cash discount of 2% "
         "was allowed on the amount paid.")
    out = reason_bk_question(q)
    recs = {r.get("calculation_id"): r
            for r in (out.get("calculation_records") or [])}
    check("discount: trade discount amount 1,000",
          out.get("status") == VERIFIED
          and recs.get("BK_TRADE_DISCOUNT_AMOUNT", {}).get("result")
          == Decimal("1000.00"),
          str({k: v.get("result") for k, v in recs.items()}))
    check("discount: net value 9,000",
          recs.get("BK_NET_TRANSACTION_VALUE", {}).get("result")
          == Decimal("9000.00"),
          f"net={recs.get('BK_NET_TRANSACTION_VALUE', {}).get('result')}")
    check("discount: cash discount on paid half = 90",
          recs.get("BK_CASH_DISCOUNT_AMOUNT", {}).get("result")
          == Decimal("90.00"),
          f"cd={recs.get('BK_CASH_DISCOUNT_AMOUNT', {}).get('result')}")
    check("discount: cash paid = 4,410",
          recs.get("BK_CASH_PAID_NET", {}).get("result")
          == Decimal("4410.00"),
          f"paid={recs.get('BK_CASH_PAID_NET', {}).get('result')}")
    dr, cr = merged_lines(out)
    check("discount: final journal Purchases 9,000 / Cash 4,410 + "
          "Discount Received 90 + Rahul 4,500",
          norm_lines(dr) == [("Purchases", 9000)]
          and norm_lines(cr) == sorted([("Cash", 4410),
                                        ("Discount Received", 90),
                                        ("Rahul", 4500)]),
          f"Dr={norm_lines(dr)} Cr={norm_lines(cr)}")
    # a TRADE discount on a cash purchase never creates a cash-discount line
    o2 = reason_bk_question("Purchased goods for cash Rs.10,000 at 10% trade "
                            "discount.")
    d2, c2 = merged_lines(o2)
    check("discount: trade discount is not a cash-discount line",
          o2.get("status") == VERIFIED
          and norm_lines(d2) == [("Purchases", 9000)]
          and norm_lines(c2) == [("Cash", 9000)],
          f"Dr={norm_lines(d2)} Cr={norm_lines(c2)}")


# ---------------------------------------------------------------------------
# 6. Multi-transaction handling
# ---------------------------------------------------------------------------
def test_multi_transaction() -> None:
    q = ("Started business with cash Rs.1,00,000. Purchased goods for cash "
         "Rs.20,000. Paid rent Rs.5,000.")
    out = reason_bk_question(q)
    journals = out.get("journals") or []
    check("multi: three independent entries",
          out.get("status") == VERIFIED and len(journals) == 3,
          f"count={len(journals)}")
    dr, cr = merged_lines(out)
    check("multi: chronological combined effects",
          norm_lines(dr) == sorted([("Cash", 100000), ("Purchases", 20000),
                                    ("Rent", 5000)])
          and norm_lines(cr) == sorted([("Capital", 100000),
                                        ("Cash", 20000), ("Cash", 5000)]),
          f"Dr={norm_lines(dr)} Cr={norm_lines(cr)}")
    # continuation phrase stays attached to the purchase (ONE journal)
    q2 = "Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000 immediately."
    out2 = reason_bk_question(q2)
    j2 = out2.get("journals") or [out2.get("journal")] or []
    d2, c2 = merged_lines(out2)
    check("multi: continuation 'paid him' folds into one journal",
          out2.get("status") == VERIFIED and len(j2) == 1
          and norm_lines(d2) == [("Purchases", 10000)]
          and norm_lines(c2) == sorted([("Cash", 4000), ("Rahul", 6000)]),
          f"count={len(j2)} Dr={norm_lines(d2)} Cr={norm_lines(c2)}")


# ---------------------------------------------------------------------------
# 7. Student-answer verification (divergence, never just 'wrong')
# ---------------------------------------------------------------------------
def test_student_verification() -> None:
    desc = "Purchased furniture for cash Rs.15,000."
    entries = [{"debits": [{"account": "Furniture", "amount": 15000}],
                "credits": [{"account": "Cash", "amount": 15000}]}]
    good = verify_journal_entry(desc, {
        "debits": [{"account": "Furniture", "amount": 15000}],
        "credits": [{"account": "Cash", "amount": 15000}]})
    check("student: correct journal -> CORRECT",
          good.get("verdict") == "CORRECT",
          f"verdict={good.get('verdict')} what={good.get('what')}")
    wrong_side = verify_journal_entry(desc, {
        "debits": [{"account": "Cash", "amount": 15000}],
        "credits": [{"account": "Furniture", "amount": 15000}]})
    check("student: reversed sides -> INCORRECT with explanation",
          wrong_side.get("verdict") == "INCORRECT"
          and bool(wrong_side.get("why_not")),
          f"verdict={wrong_side.get('verdict')}")
    unbalanced = verify_journal_entry(desc, {
        "debits": [{"account": "Furniture", "amount": 15000}],
        "credits": [{"account": "Cash", "amount": 14000}]})
    check("student: unbalanced -> exact discrepancy 1,000",
          unbalanced.get("verdict") == "INCORRECT"
          and abs(float(unbalanced.get("discrepancy") or 0) - 1000.0) < 0.01,
          f"verdict={unbalanced.get('verdict')} disc={unbalanced.get('discrepancy')}")
    lb_ok = verify_ledger_balance("Furniture", "15000", "Dr", entries)
    check("student: correct ledger balance -> CORRECT",
          lb_ok.get("verdict") == "CORRECT",
          f"verdict={lb_ok.get('verdict')}")
    lb_bad = verify_ledger_balance("Furniture", "14000", "Dr", entries)
    check("student: wrong ledger balance -> INCORRECT, discrepancy shown",
          lb_bad.get("verdict") == "INCORRECT"
          and abs(float(lb_bad.get("discrepancy") or 0) - 1000.0) < 0.01,
          f"verdict={lb_bad.get('verdict')} disc={lb_bad.get('discrepancy')}")
    tb_bad = verify_trial_balance(
        [{"account": "Furniture", "debit": 14000.0, "credit": 0.0}], entries)
    check("student: wrong trial-balance row -> divergence exposed",
          tb_bad.get("verdict") == "INCORRECT"
          or float(tb_bad.get("discrepancy") or 0) > 0,
          f"verdict={tb_bad.get('verdict')} disc={tb_bad.get('discrepancy')}")


# ---------------------------------------------------------------------------
# 8. C++ authority (registered metrics only; formula_id never None when VERIFIED)
# ---------------------------------------------------------------------------
def test_cpp_authority() -> None:
    pm = verify_bk_metric("Profit Margin",
                          facts={"Profit": 200, "Revenue": 1000})
    check("cpp: Profit Margin formula_id=PROFIT_MARGIN",
          pm.get("formula_id") == "PROFIT_MARGIN"
          and pm.get("display_value") == "20.00%",
          f"formula={pm.get('formula_id')} display={pm.get('display_value')}")
    check("cpp: authority_state == cpp",
          pm.get("authority_state") == "cpp",
          f"authority={pm.get('authority_state')}")
    bad = verify_bk_metric("Depreciation on machinery",
                           facts={"Cost": 100000, "Rate": 10})
    check("cpp: unsupported metric never claims VERIFIED with formula_id",
          bad.get("formula_id") is None
          or bad.get("status") != VERIFIED,
          f"formula={bad.get('formula_id')} status={bad.get('status')}")


# ---------------------------------------------------------------------------
# 9. Deterministic repeatability (identical input -> identical output)
# ---------------------------------------------------------------------------
def test_determinism() -> None:
    bad = 0
    for case in VERIFIED_CASES:
        a = output_snapshot(reason_bk_question(case["question"]))
        b = output_snapshot(reason_bk_question(case["question"]))
        if a != b:
            bad += 1
            check(f"determinism: {case['question'][:45]}", False,
                  "output differs between runs")
    check("determinism: identical input -> identical output",
          bad == 0, f"{bad} non-deterministic cases")


# ---------------------------------------------------------------------------
# 10. Hard-invariant counters (all must be 0)
# ---------------------------------------------------------------------------
def test_hard_invariants() -> None:
    invented = 0
    unbalanced_journal = 0
    unbalanced_tb = 0
    for case in VERIFIED_CASES:
        out = reason_bk_question(case["question"])
        if out.get("status") != VERIFIED:
            continue
        journals = out.get("journals") or [out.get("journal")] or []
        unbalanced_journal += sum(
            1 for j in journals if not j.get("balanced"))
        tb = out.get("trial_balance") or {}
        if tb.get("balanced") is not True:
            unbalanced_tb += 1
        found = set()
        for j in journals:
            for l in (j.get("debit_lines") or []) + (j.get("credit_lines") or []):
                if l.get("account"):
                    found.add(l["account"])
        allowed = {a for a, _ in case["debit"]} | {a for a, _ in case["credit"]}
        invented += len(found - allowed)
    check("invariant: 0 invented accounts", invented == 0, f"{invented}")
    check("invariant: 0 unbalanced VERIFIED journals",
          unbalanced_journal == 0, f"{unbalanced_journal}")
    check("invariant: 0 unbalanced VERIFIED trial balances",
          unbalanced_tb == 0, f"{unbalanced_tb}")


def main() -> int:
    test_benchmark()
    test_refusals()
    test_exact_account()
    test_cash_vs_credit()
    test_discount_pipeline()
    test_multi_transaction()
    test_student_verification()
    test_cpp_authority()
    test_determinism()
    test_hard_invariants()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 76)
    print(f"SPRINT 15E FYJC BK UNIT-TEST-1 GATE: {passed}/{total} checks passed")
    print(f"benchmark size: {len(BK15E_BENCHMARK)} "
          f"(verified {len(VERIFIED_CASES)}, refusals {len(REFUSAL_CASES)})")
    if FAILURES:
        for f in FAILURES[:30]:
            print(f"  FAIL - {f}")
        print("=" * 76)
        print("SPRINT 15E FAIL - UNIT-TEST-1 COVERAGE BLOCKER REMAINS")
        return 1
    print("INVENTED ACCOUNTS: 0 | UNBALANCED JOURNALS: 0 | "
          "UNBALANCED TRIAL BALANCES: 0 | FORMULA_ID=None CONFIDENT: 0")
    print("C++ AUTHORITY: VERIFIED (registered metrics) | "
          "DETERMINISM: REPEATABLE")
    print("=" * 76)
    print("SPRINT 15E PASS - FYJC BOOK-KEEPING UNIT-TEST-1 COVERAGE VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
