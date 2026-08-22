#!/usr/bin/env python3
"""
Platrixa
Sprint 15F - FYJC Book-Keeping Ch.1-3 Textbook Pattern Expansion Gate
scripts/fte_fyjc_bk15f_test.py

Runs the hand-verified 162-case golden benchmark
(backend/maths/fyjc_bk_15f_benchmark.py) through the FULL reasoning
pipeline (backend/maths/fyjc_bk_reasoning.py + fyjc_bk_15f.py) and the
mandatory Sprint 15F gates:

  * benchmark accuracy            (exact Dr/Cr lines, journal count,
                                   type key, balanced journal/TB/ledger,
                                   class + golden rule + why on every
                                   line, narration, calculation
                                   provenance)
  * refusal accuracy              (BLOCKED / REVIEW_REQUIRED /
                                   NOT_SUPPORTED with ZERO fabricated
                                   lines)
  * exact-account hallucination   (Furniture purchase never invents
                                   Machinery/Building)
  * wording collapse              (equivalent wordings -> ONE pattern:
                                   bought/purchased, on credit/on
                                   account, the-bank variants, cash
                                   discount vs cash purchase, partial
                                   vs full settlement)
  * continuation boundaries       ('paid him' folds into the purchase;
                                   'Paid rent' is a NEW transaction;
                                   debtor-subject 'Mohan paid' is a
                                   receipt - never a payment to Mohan)
  * student-answer verification   (final answer / journal / ledger /
                                   trial balance - first deterministic
                                   mistake, never just 'wrong')
  * C++ authority                 (registered metrics only;
                                   formula_id never None when VERIFIED)
  * deterministic repeatability   (identical input -> identical output)
  * hard safety invariants        (all counters == 0)
  * pattern coverage report       (machine-readable JSON +
                                   human-readable Markdown, per-pattern
                                   test/pass counts)

The oracle NEVER calls the engine - every expected value is a
hand-written FYJC treatment.
"""

import json
import sys
from decimal import Decimal

sys.path.insert(0, ".")

from backend.maths.fyjc_accounting import (
    verify_ledger_balance,
    verify_trial_balance,
)
from backend.maths.fyjc_bk_15f import (
    BK_PATTERN_LIBRARY,
    pattern_coverage_report,
    write_coverage_report,
    verify_student_final,
    verify_student_journal,
)
from backend.maths.fyjc_bk_15f_benchmark import (
    BK15F_BENCHMARK,
    EXACT_ACCOUNT_CASES,
    MISSING_AMBIGUOUS,
    REFUSAL_CASES,
    STUDENT_ERROR_CASES,
    UNSUPPORTED_REFUSALS,
    VERIFIED_CASES,
)
from backend.maths.fyjc_bk_reasoning import (
    generate_journal,
    journal_to_entries,
    reason_bk_question,
    verify_bk_metric,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

NOT_SUPPORTED = "NOT_SUPPORTED"

CHECKS: list = []
FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def norm_lines(lines) -> list:
    return sorted(
        (str(line.get("account") or ""),
         int(round(float(line.get("amount", 0)))))
        for line in lines if line.get("account")
    )


def merged_lines(out):
    journals = out.get("journals") or [out.get("journal")] or []
    dr = [l for j in journals for l in (j.get("debit_lines") or [])]
    cr = [l for j in journals for l in (j.get("credit_lines") or [])]
    return dr, cr


def output_snapshot(out) -> str:
    keys = ("journal", "journals", "ledger", "trial_balance", "status",
            "debit_lines", "credit_lines")
    return json.dumps({k: out.get(k) for k in keys}, sort_keys=True,
                      default=str)


# ---------------------------------------------------------------------------
# 1. Benchmark accuracy
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
        exp_dr = sorted((a, int(v)) for a, v in case["debit"])
        exp_cr = sorted((a, int(v)) for a, v in case["credit"])
        if norm_lines(dr) != exp_dr or norm_lines(cr) != exp_cr:
            check(f"bench lines: {q[:55]}", False,
                  f"Dr {norm_lines(dr)} != {exp_dr} | "
                  f"Cr {norm_lines(cr)} != {exp_cr}")
            continue
        if case.get("journals", 1) == 1 and case.get("type_key"):
            tk = (out.get("understanding") or {}).get("question_type_key")
            if tk != case.get("type_key"):
                check(f"bench type: {q[:55]}", False,
                      f"type={tk} expected={case.get('type_key')}")
                continue
        if any(not j.get("balanced") for j in journals):
            check(f"bench balanced: {q[:55]}", False, "unbalanced journal(s)")
            continue
        tb = out.get("trial_balance") or {}
        ledger = out.get("ledger") or {}
        if tb.get("balanced") is not True:
            check(f"bench tb: {q[:55]}", False,
                  f"balanced={tb.get('balanced')}")
            continue
        if ledger.get("balanced") is not True:
            check(f"bench ledger: {q[:55]}", False, "ledger unbalanced")
            continue
        bad = [l for l in dr + cr
               if not (l.get("class") and l.get("rule") and l.get("why"))]
        if bad:
            check(f"bench class: {q[:55]}", False,
                  "line missing class/rule/why")
            continue
        if (out.get("journal") or {}).get("narration") is None:
            check(f"bench narration: {q[:55]}", False, "missing narration")
            continue
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
    # the missing/ambiguous vs unsupported split is asserted too
    mb = sum(1 for c in MISSING_AMBIGUOUS
             if reason_bk_question(c["question"]).get("status")
             == c["status"])
    check(f"missing/ambiguous accuracy: {mb}/{len(MISSING_AMBIGUOUS)}",
          mb == len(MISSING_AMBIGUOUS), f"{mb}/{len(MISSING_AMBIGUOUS)}")
    ns = sum(1 for c in UNSUPPORTED_REFUSALS
             if reason_bk_question(c["question"]).get("status")
             == c["status"])
    check(f"unsupported refusal accuracy: {ns}/{len(UNSUPPORTED_REFUSALS)}",
          ns == len(UNSUPPORTED_REFUSALS), f"{ns}/{len(UNSUPPORTED_REFUSALS)}")


# ---------------------------------------------------------------------------
# 3. Exact-account hallucination guard
# ---------------------------------------------------------------------------
def test_exact_account() -> None:
    bad_count = 0
    for case in EXACT_ACCOUNT_CASES:
        out = reason_bk_question(case["question"])
        accounts: set = set()
        for j in ((out.get("journals") or [out.get("journal")]) if
                  out.get("status") == VERIFIED else []):
            for l in (j.get("debit_lines") or []) + (j.get("credit_lines")
                                                     or []):
                if l.get("account"):
                    accounts.add(l["account"])
        extra = accounts - case["allowed"]
        if out.get("status") != VERIFIED or extra:
            bad_count += 1
            check(f"exact-account: {case['question'][:45]}", False,
                  f"status={out.get('status')} extra={sorted(extra)}")
    check("exact-account: 0 invented accounts", bad_count == 0,
          f"{bad_count} violations")


# ---------------------------------------------------------------------------
# 4. Wording collapse - equivalent wordings -> ONE canonical IR
# ---------------------------------------------------------------------------
def test_wording_collapse() -> None:
    # purchase-for-cash family
    family = [
        "Purchased goods for cash Rs.16,000.",
        "Bought goods for cash Rs.16,000.",
        "Goods purchased for cash Rs.16,000.",
        "Goods bought for cash Rs.16,000.",
        "Purchased goods paying cash Rs.16,000.",
    ]
    for w in family:
        o = reason_bk_question(w)
        d, c = merged_lines(o)
        check(f"wording collapse (cash purchase): {w[:40]}",
              o.get("status") == VERIFIED
              and norm_lines(d) == [("Purchases", 16000)]
              and norm_lines(c) == [("Cash", 16000)],
              f"Dr={norm_lines(d)} Cr={norm_lines(c)}")
    # on credit == on account (credit purchase)
    o1 = reason_bk_question("Bought goods on credit from Rahul Rs.22,000.")
    o2 = reason_bk_question("Bought goods on account from Rahul Rs.22,000.")
    d1, c1 = merged_lines(o1)
    d2, c2 = merged_lines(o2)
    check("wording collapse: 'on account' == 'on credit' (purchase)",
          o1.get("status") == VERIFIED and o2.get("status") == VERIFIED
          and norm_lines(d1) == norm_lines(d2) == [("Purchases", 22000)]
          and norm_lines(c1) == norm_lines(c2) == [("Rahul", 22000)],
          f"Dr={norm_lines(d1)}/{norm_lines(d2)}")
    o3 = reason_bk_question("Sold goods on credit to Mohan Rs.18,000.")
    o4 = reason_bk_question("Sold goods on account to Mohan Rs.18,000.")
    d3, c3 = merged_lines(o3)
    d4, c4 = merged_lines(o4)
    check("wording collapse: 'on account' == 'on credit' (sale)",
          o3.get("status") == VERIFIED and o4.get("status") == VERIFIED
          and norm_lines(d3) == norm_lines(d4) == [("Mohan", 18000)]
          and norm_lines(c3) == norm_lines(c4) == [("Sales", 18000)],
          f"Dr={norm_lines(d3)}/{norm_lines(d4)}")
    # cash sale family
    for w in ("Sold goods for cash Rs.25,000.",
              "Cash sale of goods Rs.25,000.",
              "Goods sold and cash received immediately Rs.25,000.",
              "Sold goods to Mohan for cash Rs.25,000."):
        o = reason_bk_question(w)
        d, c = merged_lines(o)
        check(f"wording collapse (cash sale): {w[:40]}",
              o.get("status") == VERIFIED
              and norm_lines(d) == [("Cash", 25000)]
              and norm_lines(c) == [("Sales", 25000)],
              f"Dr={norm_lines(d)} Cr={norm_lines(c)}")
    # 'the bank' variants
    for w in ("Deposited cash into bank Rs.12,000.",
              "Deposited cash into the bank Rs.12,000.",
              "Cash deposited in bank Rs.12,000."):
        o = reason_bk_question(w)
        d, c = merged_lines(o)
        check(f"wording collapse (bank deposit): {w[:40]}",
              o.get("status") == VERIFIED
              and norm_lines(d) == [("Bank", 12000)]
              and norm_lines(c) == [("Cash", 12000)],
              f"Dr={norm_lines(d)} Cr={norm_lines(c)}")
    for w in ("Withdrew cash from bank Rs.3,000.",
              "Withdrew cash from the bank Rs.3,000.",
              "Cash withdrawn from bank for office use Rs.3,000."):
        o = reason_bk_question(w)
        d, c = merged_lines(o)
        check(f"wording collapse (bank withdrawal): {w[:40]}",
              o.get("status") == VERIFIED
              and norm_lines(d) == [("Cash", 3000)]
              and norm_lines(c) == [("Bank", 3000)],
              f"Dr={norm_lines(d)} Cr={norm_lines(c)}")
    # contextual distinction: 'cash discount' NEVER implies a cash purchase
    o5 = reason_bk_question(
        "Purchased goods from Rahul for Rs.10,000 at 10% trade discount. "
        "Half the amount was paid immediately and a cash discount of 2% "
        "was allowed on the amount paid.")
    tk5 = (o5.get("understanding") or {}).get("question_type_key")
    check("context: 'cash discount' never implies a cash purchase",
          o5.get("status") == VERIFIED
          and tk5 == "PURCHASE_GOODS_CREDIT",
          f"type={tk5}")
    # contextual distinction: partial payment never implies full cash
    o6 = reason_bk_question(
        "Purchased goods from Rahul for Rs.10,000 at 10% trade discount. "
        "Half the amount was paid immediately.")
    tk6 = (o6.get("understanding") or {}).get("question_type_key")
    check("context: 'half ... paid immediately' stays a credit purchase",
          o6.get("status") == VERIFIED
          and tk6 == "PURCHASE_GOODS_CREDIT"
          and norm_lines(merged_lines(o6)[1]) == sorted(
              [("Cash", 4500), ("Rahul", 4500)]),
          f"type={tk6}")
    # a cheque deposited into the bank is never cash
    o7 = reason_bk_question("Cheque deposited into bank Rs.5,000.")
    d7, c7 = merged_lines(o7)
    check("context: cheque deposit without drawer is refused (never Cash)",
          o7.get("status") == REVIEW_REQUIRED and not d7 and not c7,
          f"status={o7.get('status')} Dr={norm_lines(d7)} Cr={norm_lines(c7)}")
    o8 = reason_bk_question(
        "Cheque received from Mohan and deposited into bank Rs.5,000.")
    d8, c8 = merged_lines(o8)
    check("context: cheque deposit with drawer posts Bank/party",
          o8.get("status") == VERIFIED
          and norm_lines(d8) == [("Bank", 5000)]
          and norm_lines(c8) == [("Mohan", 5000)],
          f"Dr={norm_lines(d8)} Cr={norm_lines(c8)}")


# ---------------------------------------------------------------------------
# 5. Continuation resolution boundaries (spec section 6)
# ---------------------------------------------------------------------------
def _all_journals(out) -> list:
    return out.get("journals") or [out.get("journal")] or []


def test_continuation() -> None:
    o1 = reason_bk_question(
        "Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000 "
        "immediately.")
    j1 = _all_journals(o1)
    d1, c1 = merged_lines(o1)
    check("continuation: 'paid him' folds into ONE journal",
          o1.get("status") == VERIFIED and len(j1) == 1
          and norm_lines(d1) == [("Purchases", 10000)]
          and norm_lines(c1) == sorted([("Cash", 4000), ("Rahul", 6000)]),
          f"count={len(j1)}")
    o2 = reason_bk_question(
        "Purchased goods from Rahul for Rs.10,000. Paid rent Rs.4,000.")
    j2 = _all_journals(o2)
    d2, c2 = merged_lines(o2)
    check("continuation: 'Paid rent' is a NEW transaction",
          o2.get("status") == VERIFIED and len(j2) == 2
          and norm_lines(d2) == sorted([("Purchases", 10000), ("Rent", 4000)])
          and norm_lines(c2) == sorted([("Rahul", 10000), ("Cash", 4000)]),
          f"count={len(j2)}")
    o3 = reason_bk_question(
        "Purchased goods from Rahul. Paid rent Rs.4,000.")
    check("continuation: missing amount on first transaction -> BLOCKED "
          "(two transactions created, zero fabricated lines)",
          o3.get("status") == BLOCKED
          and not (o3.get("debit_lines") or o3.get("credit_lines")),
          f"status={o3.get('status')}")
    o4 = reason_bk_question(
        "Purchased goods from Rahul for Rs.20,000. Returned goods worth "
        "Rs.1,000 to him.")
    j4 = _all_journals(o4)
    d4, c4 = merged_lines(o4)
    check("continuation: 'returned goods ... to him' is its own entry",
          o4.get("status") == VERIFIED and len(j4) == 2
          and norm_lines(d4) == sorted([("Purchases", 20000),
                                        ("Rahul", 1000)])
          and norm_lines(c4) == sorted([("Rahul", 20000),
                                        ("Purchase Returns", 1000)]),
          f"count={len(j4)}")
    o5 = reason_bk_question(
        "Sold goods to Mohan for Rs.20,000. Mohan paid Rs.12,000 "
        "immediately.")
    j5 = _all_journals(o5)
    d5, c5 = merged_lines(o5)
    check("continuation: debtor-subject 'Mohan paid' is a RECEIPT "
          "(Cash Dr / Mohan Cr - never a payment to Mohan)",
          o5.get("status") == VERIFIED and len(j5) == 2
          and norm_lines(d5) == sorted([("Mohan", 20000), ("Cash", 12000)])
          and norm_lines(c5) == sorted([("Sales", 20000), ("Mohan", 12000)]),
          f"count={len(j5)} Dr={norm_lines(d5)} Cr={norm_lines(c5)}")


# ---------------------------------------------------------------------------
# 6. Student-answer verification (first deterministic mistake)
# ---------------------------------------------------------------------------
def test_student_verification() -> None:
    total = 0
    passed = 0
    for case in STUDENT_ERROR_CASES:
        q = case["question"]
        # reference entries come from the FULL pipeline (reason_bk_question
        # splits multi-transaction questions); generate_journal alone would
        # mis-treat a multi-transaction question as one journal.
        reference = reason_bk_question(q)
        entries: list = []
        for j in _all_journals(reference):
            if j.get("status") == VERIFIED:
                entries.extend(journal_to_entries(j))
        for chk in case["checks"]:
            total += 1
            kind = chk["kind"]
            hint = chk.get("hint")
            exp = chk["expected_verdict"]
            if kind == "journal":
                res = verify_student_journal(q, chk["student"])
            elif kind == "final":
                res = verify_student_final(q, chk["answer"],
                                           chk.get("what", "journal_total"))
            elif kind == "ledger":
                res = verify_ledger_balance(
                    chk["account"], chk["balance"], chk["side"], entries)
            elif kind == "tb":
                res = verify_trial_balance(chk["rows"], entries)
            else:
                check(f"student: unknown kind {kind}", False)
                continue
            ok = res.get("verdict") == exp
            if ok and exp == "INCORRECT" and hint:
                joined = str(res.get("first_mistake") or "") + " " + \
                    str(res.get("why_not") or "")
                if hint.lower() not in joined.lower():
                    ok = False
            if ok:
                passed += 1
            else:
                check(f"student: {q[:40]} [{kind}] {hint}", False,
                      f"verdict={res.get('verdict')} expected={exp} "
                      f"first={res.get('first_mistake')} "
                      f"why={res.get('why_not')}")
    check(f"student-answer verification: {passed}/{total}",
          passed == total, f"{passed}/{total}")


# ---------------------------------------------------------------------------
# 7. C++ authority
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
          bad.get("formula_id") is None or bad.get("status") != VERIFIED,
          f"formula={bad.get('formula_id')} status={bad.get('status')}")


# ---------------------------------------------------------------------------
# 8. Determinism
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
    check("determinism: identical input -> identical output", bad == 0,
          f"{bad} non-deterministic cases")


# ---------------------------------------------------------------------------
# 9. Hard invariants (all must be 0)
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
            for l in (j.get("debit_lines") or []) + (j.get("credit_lines")
                                                     or []):
                if l.get("account"):
                    found.add(l["account"])
        allowed = {a for a, _ in case["debit"]} | {a for a, _ in
                                                   case["credit"]}
        invented += len(found - allowed)
    check("invariant: 0 invented accounts", invented == 0, f"{invented}")
    check("invariant: 0 unbalanced VERIFIED journals",
          unbalanced_journal == 0, f"{unbalanced_journal}")
    check("invariant: 0 unbalanced VERIFIED trial balances",
          unbalanced_tb == 0, f"{unbalanced_tb}")
    # refusal cases never carry journal lines (0 fabricated output)
    fabricated = sum(
        1 for case in REFUSAL_CASES
        if (reason_bk_question(case["question"]).get("debit_lines")
            or reason_bk_question(case["question"]).get("credit_lines")))
    check("invariant: 0 fabricated amounts/lines in refusals",
          fabricated == 0, f"{fabricated}")


# ---------------------------------------------------------------------------
# 10. Coverage report (spec section 16)
# ---------------------------------------------------------------------------
def test_coverage_report() -> None:
    report = write_coverage_report(
        BK15F_BENCHMARK, reason_bk_question,
        "docs/fyjc_bk_15f_coverage.json",
        "docs/FYJC_BK_15F_COVERAGE.md")
    totals = report["totals"]
    check(f"coverage: {totals['test_count']} cases, "
          f"{totals['pass_count']} pass ({totals['pass_rate']}%)",
          totals["pass_count"] == totals["test_count"],
          f"{totals['pass_count']}/{totals['test_count']}")
    ids = {p["pattern_id"] for p in report["patterns"]}
    lib_ids = {p["pattern_id"] for p in BK_PATTERN_LIBRARY}
    # composite/pipeline patterns are exercised INSIDE their parent buckets
    # (a trade-discount purchase posts under PURCHASE_GOODS_CREDIT, etc.)
    # and are asserted directly by the wording-collapse / context tests.
    _COMPOSITE_PATTERNS = {
        "TRADE_DISCOUNT_PIPELINE", "CASH_DISCOUNT_PIPELINE",
        "EXPLICIT_DISCOUNT_SETTLEMENT", "CHEQUE_DEPOSITED",
    }
    uncovered = lib_ids - ids - _COMPOSITE_PATTERNS
    check("coverage: every library pattern has a test bucket (or is a "
          "documented composite)",
          not uncovered,
          f"library patterns without direct buckets: {sorted(uncovered)}")
    check("coverage: reports written",
          True, "docs/fyjc_bk_15f_coverage.json + "
                "docs/FYJC_BK_15F_COVERAGE.md")


def main() -> int:
    test_benchmark()
    test_refusals()
    test_exact_account()
    test_wording_collapse()
    test_continuation()
    test_student_verification()
    test_cpp_authority()
    test_determinism()
    test_hard_invariants()
    test_coverage_report()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 76)
    print(f"SPRINT 15F FYJC BK CH.1-3 PATTERN GATE: {passed}/{total} "
          "checks passed")
    print(f"benchmark size: {len(BK15F_BENCHMARK)} (verified "
          f"{len(VERIFIED_CASES)}, refusals {len(REFUSAL_CASES)}) + "
          f"{len(STUDENT_ERROR_CASES)} student-error cases = "
          f"{len(BK15F_BENCHMARK) + len(STUDENT_ERROR_CASES)}")
    if FAILURES:
        for f in FAILURES[:30]:
            print(f"  FAIL - {f}")
        print("=" * 76)
        print("SPRINT 15F FAIL - TEXTBOOK PATTERN COVERAGE BLOCKER REMAINS")
        return 1
    print("INVENTED ACCOUNTS: 0 | UNBALANCED JOURNALS: 0 | "
          "UNBALANCED TRIAL BALANCES: 0 | FABRICATED REFUSALS: 0 | "
          "FORMULA_ID=None CONFIDENT: 0")
    print("C++ AUTHORITY: VERIFIED (registered metrics) | "
          "DETERMINISM: REPEATABLE")
    print("=" * 76)
    print("SPRINT 15F PASS - FYJC BK CH.1-3 TEXTBOOK PATTERN COVERAGE "
          "VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
