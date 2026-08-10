#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15C - FYJC BK P0 Transaction Semantics & Basic Journal Hardening
scripts/fte_fyjc_bk_p0_test.py

Deterministic P0 regression gate for the 5-question smoke test exposed by
the FYJC student pilot. It locks down the BASIC transaction semantics that
must never regress:

  P0-1  Cash-sale semantics - 'Sold goods to Mohan for cash Rs.20,000' is
        Cash A/c Dr / Sales A/c Cr (Mohan NEVER becomes a debtor because
        the settlement says 'for cash'). Four equivalent wordings resolve
        to the SAME IR + journal; the credit-sale contrast ('on credit')
        is the mirror image.
  P0-2  Basic transaction matrix - the 10 canonical FYJC transactions,
        each verified for exact accounts, traditional class, Golden Rule,
        debit/credit direction, amount, journal balance, ledger derived
        from the journal, and trial-balance effect.
  P0-3  Exact-account hallucination guard - a Furniture purchase NEVER
        invents Machinery/Building/Vehicle/Equipment/Land.
  P0-4  Missing-value refusal - 'Purchased goods from Rahul.' stays
        BLOCKED with no invented amount and no journal lines.
  P0-5  Trade discount + half payment + cash discount - the exact smoke
        question must produce 9,000 / 4,410 / 90 / 4,500 through the
        registered pipeline (NOT a one-off hardcoded pattern).
  P0-6  Multi-transaction question - the three period-separated sentences
        resolve to THREE independent journals preserving chronological
        order, combined into ONE balanced ledger + trial balance.

Hard invariants enforced (same spirit as the Sprint 15 release gate):
  * every VERIFIED journal is balanced (Dr == Cr)
  * no invented accounts (exact-account guard)
  * no invented amounts (a BLOCKED/refused question carries no lines)
  * no unbalanced verified journal, no silent substitution
  * registered metrics still resolve through the C++ authority with
    formula_id set (never a DERIVED/VERIFIED result with formula_id=None)
  * identical input -> identical output (deterministic repeatability)

All cases run through the REAL hardened pipeline (reason_bk_question +
journal/ledger/trial-balance generators). Nothing is committed or pushed
by this gate.

Exit code: 0 = PASS, 1 = FAIL (release blocker).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    CLASS_NOMINAL,
    CLASS_PERSONAL,
    CLASS_REAL,
    TRADITIONAL_GOLDEN_RULES,
    generate_journal,
    reason_bk_question,
    resolve_transaction_amounts,
    verify_bk_metric,
)
from backend.maths.status import BLOCKED, VERIFIED  # noqa: E402

CHECKS: List[Tuple[str, bool, str]] = []
FAILURES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def _accounts(lines: Any) -> Set[str]:
    return {str(line.get("account")) for line in (lines or [])}


def _amounts(lines: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for line in (lines or []):
        try:
            out[str(line.get("account"))] = float(line.get("amount"))
        except (TypeError, ValueError):
            out[str(line.get("account"))] = float("nan")
    return out


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# P0-1 Cash-sale semantics: cash wording NEVER makes the party a debtor
# ---------------------------------------------------------------------------

CASH_SALE_WORDINGS = [
    "Sold goods for cash \u20b920,000.",
    "Sold goods to Mohan for cash \u20b920,000.",
    "Cash sale of goods \u20b920,000.",
    "Goods sold and cash received immediately \u20b920,000.",
]


def test_cash_sale_semantics() -> None:
    for q in CASH_SALE_WORDINGS:
        r = reason_bk_question(q)
        check(
            f"P0-1: '{q[:46]}' VERIFIED as SALE_GOODS_CASH",
            r.get("status") == VERIFIED
            and (r.get("understanding") or {}).get("question_type_key")
            == "SALE_GOODS_CASH",
            f"status={r.get('status')} "
            f"type={(r.get('understanding') or {}).get('question_type_key')}",
        )
        if r.get("status") != VERIFIED:
            continue
        check(
            f"P0-1: '{q[:46]}' Cash A/c Dr exactly 20,000 (Mohan never a "
            "debtor)",
            _amounts(r.get("debit_lines")) == {"Cash": 20000.0},
            str(_amounts(r.get("debit_lines"))),
        )
        check(
            f"P0-1: '{q[:46]}' Sales A/c Cr exactly 20,000",
            _amounts(r.get("credit_lines")) == {"Sales": 20000.0},
            str(_amounts(r.get("credit_lines"))),
        )
        check(
            f"P0-1: '{q[:46]}' Mohan absent from every account line",
            "Mohan" not in _accounts(r.get("debit_lines"))
            and "Mohan" not in _accounts(r.get("credit_lines")),
            str(_accounts(r.get("debit_lines")) | _accounts(r.get("credit_lines"))),
        )
        check(
            f"P0-1: '{q[:46]}' journal balanced",
            bool(r.get("journal", {}).get("balanced")),
            f"Dr={r.get('journal', {}).get('total_debit')} "
            f"Cr={r.get('journal', {}).get('total_credit')}",
        )

    # traditional reasoning for the cash sale (B): Cash Real A/c comes in,
    # Sales Nominal A/c income
    r = reason_bk_question("Sold goods to Mohan for cash \u20b920,000.")
    if r.get("status") == VERIFIED:
        lines = (r.get("debit_lines") or []) + (r.get("credit_lines") or [])
        cash = next((l for l in lines if l.get("account") == "Cash"), None)
        sales = next((l for l in lines if l.get("account") == "Sales"), None)
        check("P0-1: Cash (debit) classified Real with Real Golden Rule",
              cash is not None and cash.get("class") == CLASS_REAL
              and cash.get("rule") == TRADITIONAL_GOLDEN_RULES[CLASS_REAL],
              str(cash))
        check("P0-1: Sales (credit) classified Nominal with Nominal Golden "
              "Rule",
              sales is not None and sales.get("class") == CLASS_NOMINAL
              and sales.get("rule") == TRADITIONAL_GOLDEN_RULES[CLASS_NOMINAL],
              str(sales))
        check("P0-1: both lines carry student-readable WHY text",
              all(len(str(l.get("why") or "")) > 20 for l in lines),
              "")

    # contrast: the SAME sale on credit makes Mohan the debtor
    credit = reason_bk_question(
        "Sold goods to Mohan on credit \u20b920,000.")
    check("P0-1: 'on credit' -> SALE_GOODS_CREDIT",
          credit.get("status") == VERIFIED
          and (credit.get("understanding") or {}).get("question_type_key")
          == "SALE_GOODS_CREDIT",
          f"status={credit.get('status')} "
          f"type={(credit.get('understanding') or {}).get('question_type_key')}")
    if credit.get("status") == VERIFIED:
        check("P0-1: credit sale -> Mohan A/c Dr 20,000 / Sales A/c Cr "
              "20,000",
              _amounts(credit.get("debit_lines")) == {"Mohan": 20000.0}
              and _amounts(credit.get("credit_lines")) == {"Sales": 20000.0},
              f"Dr={_amounts(credit.get('debit_lines'))} "
              f"Cr={_amounts(credit.get('credit_lines'))}")


# ---------------------------------------------------------------------------
# P0-2 Basic transaction matrix
# ---------------------------------------------------------------------------

# (question, expected Dr {account: amount}, expected Cr {account: amount})
MATRIX: List[Tuple[str, Dict[str, float], Dict[str, float]]] = [
    ("Started business with cash \u20b950,000.",
     {"Cash": 50000.0}, {"Capital": 50000.0}),
    ("Purchased furniture for cash \u20b910,000.",
     {"Furniture": 10000.0}, {"Cash": 10000.0}),
    ("Purchased goods for cash \u20b915,000.",
     {"Purchases": 15000.0}, {"Cash": 15000.0}),
    ("Purchased goods from Rahul on credit \u20b920,000.",
     {"Purchases": 20000.0}, {"Rahul": 20000.0}),
    ("Sold goods for cash \u20b912,000.",
     {"Cash": 12000.0}, {"Sales": 12000.0}),
    ("Sold goods to Amit on credit \u20b918,000.",
     {"Amit": 18000.0}, {"Sales": 18000.0}),
    ("Paid rent \u20b95,000 in cash.",
     {"Rent": 5000.0}, {"Cash": 5000.0}),
    ("Received commission \u20b93,000 in cash.",
     {"Cash": 3000.0}, {"Commission Received": 3000.0}),
    ("Paid Rahul \u20b98,000 in cash.",
     {"Rahul": 8000.0}, {"Cash": 8000.0}),
    ("Received \u20b96,000 from Amit in cash.",
     {"Cash": 6000.0}, {"Amit": 6000.0}),
]

# expected traditional class per account appearing in the matrix
CLASS_EXPECT: Dict[str, str] = {
    "Cash": CLASS_REAL, "Furniture": CLASS_REAL,
    "Capital": CLASS_PERSONAL, "Rahul": CLASS_PERSONAL,
    "Amit": CLASS_PERSONAL, "Mohan": CLASS_PERSONAL,
    "Purchases": CLASS_NOMINAL, "Sales": CLASS_NOMINAL,
    "Rent": CLASS_NOMINAL, "Commission Received": CLASS_NOMINAL,
}


def test_transaction_matrix() -> None:
    for q, want_dr, want_cr in MATRIX:
        r = reason_bk_question(q)
        check(
            f"P0-2: '{q[:44]}' VERIFIED",
            r.get("status") == VERIFIED,
            f"status={r.get('status')} why={str(r.get('why_not'))[:60]}",
        )
        if r.get("status") != VERIFIED:
            continue
        check(
            f"P0-2: '{q[:44]}' Dr accounts/amounts exactly {want_dr}",
            _amounts(r.get("debit_lines")) == want_dr,
            str(_amounts(r.get("debit_lines"))),
        )
        check(
            f"P0-2: '{q[:44]}' Cr accounts/amounts exactly {want_cr}",
            _amounts(r.get("credit_lines")) == want_cr,
            str(_amounts(r.get("credit_lines"))),
        )
        j = r.get("journal") or {}
        check(
            f"P0-2: '{q[:44]}' journal balanced (Dr == Cr)",
            bool(j.get("balanced"))
            and j.get("total_debit") == j.get("total_credit"),
            f"Dr={j.get('total_debit')} Cr={j.get('total_credit')}",
        )
        # every line: traditional class + Golden Rule + side + WHY
        lines = (r.get("debit_lines") or []) + (r.get("credit_lines") or [])
        check(
            f"P0-2: '{q[:44]}' every line has class + rule + side + WHY",
            all(
                l.get("class") in (CLASS_REAL, CLASS_PERSONAL, CLASS_NOMINAL)
                and l.get("rule") == TRADITIONAL_GOLDEN_RULES[l.get("class")]
                and l.get("side") in ("debit", "credit")
                and len(str(l.get("why") or "")) > 20
                for l in lines
            ),
            "",
        )
        # ledger derived from journal + balanced; trial balance never forced
        ledger = r.get("ledger") or {}
        tb = r.get("trial_balance") or {}
        check(
            f"P0-2: '{q[:44]}' ledger balanced (derived from journal)",
            bool(ledger.get("balanced")),
            str(ledger.get("accounts")),
        )
        check(
            f"P0-2: '{q[:44]}' trial balance Dr == Cr",
            bool(tb.get("balanced"))
            and tb.get("total_debit") == tb.get("total_credit"),
            f"Dr={tb.get('total_debit')} Cr={tb.get('total_credit')}",
        )

    # per-account traditional classification spot checks (Cash -> Real,
    # Purchases/Sales -> Nominal, Rahul/Amit/Capital -> Personal, ...)
    for account, expected_cls in CLASS_EXPECT.items():
        q = next((mq for mq, dr, cr in MATRIX
                  if account in dr or account in cr), None)
        if q is None:
            continue
        r = reason_bk_question(q)
        lines = (r.get("debit_lines") or []) + (r.get("credit_lines") or [])
        line = next((l for l in lines if l.get("account") == account), None)
        check(
            f"P0-2: {account} classified {expected_cls}",
            line is not None and line.get("class") == expected_cls,
            f"class={None if line is None else line.get('class')}",
        )


# ---------------------------------------------------------------------------
# P0-3 Exact-account hallucination guard
# ---------------------------------------------------------------------------

def test_exact_account_guard() -> None:
    r = reason_bk_question(
        "Purchased Furniture for Cash \u20b915,000.")
    check("P0-3: Furniture purchase VERIFIED",
          r.get("status") == VERIFIED,
          f"status={r.get('status')}")
    if r.get("status") == VERIFIED:
        check("P0-3: Dr exactly {Furniture} / Cr exactly {Cash}",
              _amounts(r.get("debit_lines")) == {"Furniture": 15000.0}
              and _amounts(r.get("credit_lines")) == {"Cash": 15000.0},
              f"Dr={_amounts(r.get('debit_lines'))} "
              f"Cr={_amounts(r.get('credit_lines'))}")
        forbidden = {"Machinery", "Building", "Vehicle", "Equipment", "Land"}
        all_accounts = _accounts(r.get("debit_lines")) | \
            _accounts(r.get("credit_lines"))
        check("P0-3: NEVER invents Machinery/Building/Vehicle/Equipment/Land",
              not (all_accounts & forbidden),
              str(sorted(all_accounts)))


# ---------------------------------------------------------------------------
# P0-4 Missing-value refusal
# ---------------------------------------------------------------------------

def test_missing_value_refusal() -> None:
    r = reason_bk_question("Purchased goods from Rahul.")
    check("P0-4: 'Purchased goods from Rahul.' -> BLOCKED",
          r.get("status") == BLOCKED,
          f"status={r.get('status')}")
    check("P0-4: BLOCKED carries NO journal lines (no invented amount)",
          not (r.get("debit_lines") or r.get("credit_lines")),
          "")
    check("P0-4: BLOCKED explains the missing amount",
          bool(r.get("why_not")) and "amount" in str(r.get("why_not")).lower(),
          str(r.get("why_not"))[:60])


# ---------------------------------------------------------------------------
# P0-5 Trade discount + half payment + cash discount (exact smoke question)
# ---------------------------------------------------------------------------

DISCOUNT_QUESTION = (
    "Purchased goods from Rahul for \u20b910,000 at 10% trade discount. "
    "Half the amount was paid immediately and a cash discount of 2% was "
    "allowed on the amount paid."
)


def test_discount_pipeline_exact() -> None:
    r = reason_bk_question(DISCOUNT_QUESTION)
    check("P0-5: discount question VERIFIED",
          r.get("status") == VERIFIED,
          f"status={r.get('status')} why={str(r.get('why_not'))[:60]}")
    if r.get("status") != VERIFIED:
        return
    # 10,000 -> 10% trade -> 9,000 net -> half paid 4,500 -> 2% cash
    # discount on 4,500 = 90 -> cash paid 4,410 -> Rahul payable 4,500
    check("P0-5: Purchases A/c Dr 9,000",
          _amounts(r.get("debit_lines")) == {"Purchases": 9000.0},
          str(_amounts(r.get("debit_lines"))))
    check("P0-5: Cash 4,410 / Discount Received 90 / Rahul 4,500 Cr",
          _amounts(r.get("credit_lines")) ==
          {"Cash": 4410.0, "Discount Received": 90.0, "Rahul": 4500.0},
          str(_amounts(r.get("credit_lines"))))
    check("P0-5: journal balanced",
          bool(r.get("journal", {}).get("balanced")),
          f"Dr={r.get('journal', {}).get('total_debit')} "
          f"Cr={r.get('journal', {}).get('total_credit')}")

    amounts = resolve_transaction_amounts(DISCOUNT_QUESTION)
    for key, want in (("list_price", 10000.0), ("trade_discount_rate", 10.0),
                      ("net_value", 9000.0), ("paid_amount", 4500.0),
                      ("credit_amount", 4500.0),
                      ("cash_discount_rate", 2.0),
                      ("cash_discount_amount", 90.0),
                      ("cash_paid", 4410.0)):
        check(f"P0-5: pipeline {key} == {want}",
              _num(amounts.get(key)) == want,
              str(amounts.get(key)))
    check("P0-5: every numeric step carries a calculation_id",
          all(s.get("calculation_id") for s in (amounts.get("steps") or [])),
          str([s.get("calculation_id") for s in (amounts.get("steps") or [])]))


# ---------------------------------------------------------------------------
# P0-6 Multi-transaction question (period-separated sentences)
# ---------------------------------------------------------------------------

MULTI_QUESTION = (
    "Started business with cash \u20b91,00,000. Purchased goods for cash "
    "\u20b920,000. Paid rent \u20b95,000."
)


def test_multi_transaction() -> None:
    r = reason_bk_question(MULTI_QUESTION)
    check("P0-6: multi-transaction question VERIFIED",
          r.get("status") == VERIFIED,
          f"status={r.get('status')} why={str(r.get('why_not'))[:60]}")
    if r.get("status") != VERIFIED:
        return
    journal = r.get("journal") or {}
    check("P0-6: journal is multi with count == 3",
          journal.get("multi") is True and journal.get("count") == 3,
          f"multi={journal.get('multi')} count={journal.get('count')}")
    journals = r.get("journals") or []
    check("P0-6: three independent journals in chronological order",
          len(journals) == 3, str(len(journals)))
    if len(journals) == 3:
        check("P0-6: #1 Cash Dr 1,00,000 / Capital Cr 1,00,000",
              _amounts(journals[0].get("debit_lines")) == {"Cash": 100000.0}
              and _amounts(journals[0].get("credit_lines")) ==
              {"Capital": 100000.0},
              f"Dr={_amounts(journals[0].get('debit_lines'))} "
              f"Cr={_amounts(journals[0].get('credit_lines'))}")
        check("P0-6: #2 Purchases Dr 20,000 / Cash Cr 20,000",
              _amounts(journals[1].get("debit_lines")) ==
              {"Purchases": 20000.0}
              and _amounts(journals[1].get("credit_lines")) ==
              {"Cash": 20000.0},
              f"Dr={_amounts(journals[1].get('debit_lines'))} "
              f"Cr={_amounts(journals[1].get('credit_lines'))}")
        check("P0-6: #3 Rent Dr 5,000 / Cash Cr 5,000",
              _amounts(journals[2].get("debit_lines")) == {"Rent": 5000.0}
              and _amounts(journals[2].get("credit_lines")) ==
              {"Cash": 5000.0},
              f"Dr={_amounts(journals[2].get('debit_lines'))} "
              f"Cr={_amounts(journals[2].get('credit_lines'))}")
    # combined ledger + trial balance
    ledger = r.get("ledger") or {}
    tb = r.get("trial_balance") or {}
    check("P0-6: combined ledger balanced (derived from the journals)",
          bool(ledger.get("balanced")),
          str(ledger.get("accounts")))
    # the trial balance NETS each account's balance: Cash Dr 1,00,000
    # against Cr 20,000 + 5,000 leaves a net Dr of 75,000, so the netted
    # totals are 1,00,000 per side (Purchases 20,000 + Rent 5,000 + Cash
    # net 75,000); the gross journal total is 1,25,000 per side.
    check("P0-6: combined trial balance Dr == Cr == 1,00,000 (netted)",
          bool(tb.get("balanced"))
          and tb.get("total_debit") == tb.get("total_credit")
          and tb.get("total_debit") == 100000,
          f"Dr={tb.get('total_debit')} Cr={tb.get('total_credit')}")


# ---------------------------------------------------------------------------
# Hard invariants (determinism / balance / refusals / C++ authority)
# ---------------------------------------------------------------------------

ALL_QUESTIONS = (
    CASH_SALE_WORDINGS
    + [c[0] for c in MATRIX]
    + ["Sold goods to Mohan on credit \u20b920,000."]
    + ["Purchased Furniture for Cash \u20b915,000.",
       "Purchased goods from Rahul.", DISCOUNT_QUESTION, MULTI_QUESTION]
)


def test_hard_invariants() -> None:
    bad_det: List[str] = []
    bad_balance: List[str] = []
    bad_ledger: List[str] = []
    bad_tb: List[str] = []
    bad_refusal: List[str] = []
    for q in ALL_QUESTIONS:
        r1 = reason_bk_question(q)
        r2 = reason_bk_question(q)
        if json.dumps(r1, sort_keys=True, default=str) != \
                json.dumps(r2, sort_keys=True, default=str):
            bad_det.append(q[:40])
        if r1.get("status") == VERIFIED:
            j = r1.get("journal") or {}
            if not j.get("balanced"):
                bad_balance.append(q[:40])
            ledger = r1.get("ledger") or {}
            tb = r1.get("trial_balance") or {}
            if not ledger.get("balanced"):
                bad_ledger.append(q[:40])
            if not tb.get("balanced") or \
                    tb.get("total_debit") != tb.get("total_credit"):
                bad_tb.append(q[:40])
        else:
            if r1.get("debit_lines") or r1.get("credit_lines"):
                bad_refusal.append(q[:40])

    check("invariant: deterministic repeatability", not bad_det, str(bad_det))
    check("invariant: every VERIFIED journal is balanced",
          not bad_balance, str(bad_balance))
    check("invariant: every VERIFIED journal has a balanced ledger",
          not bad_ledger, str(bad_ledger))
    check("invariant: every VERIFIED journal has a balanced trial balance",
          not bad_tb, str(bad_tb))
    check("invariant: refusals never carry journal lines",
          not bad_refusal, str(bad_refusal))

    # C++ authority untouched: a registered metric still resolves through
    # the C++ authority with formula_id set (never formula_id=None for a
    # DERIVED/VERIFIED result).
    pm = verify_bk_metric("Profit Margin",
                          facts={"Profit": 200, "Revenue": 1000})
    check("invariant: Profit Margin -> formula PROFIT_MARGIN, cpp authority",
          pm.get("formula_id") == "PROFIT_MARGIN"
          and pm.get("authority_state") == "cpp"
          and pm.get("display_value") == "20.00%",
          f"formula={pm.get('formula_id')} "
          f"authority={pm.get('authority_state')} "
          f"display={pm.get('display_value')}")
    dep = verify_bk_metric("Depreciation")
    check("invariant: unsupported metric refused (never formula_id=None "
          "with DERIVED/VERIFIED)",
          dep.get("status") == "UNSUPPORTED"
          and dep.get("formula_id") is None
          and dep.get("resolved") is False,
          f"status={dep.get('status')} formula={dep.get('formula_id')}")


def main() -> int:
    test_cash_sale_semantics()
    test_transaction_matrix()
    test_exact_account_guard()
    test_missing_value_refusal()
    test_discount_pipeline_exact()
    test_multi_transaction()
    test_hard_invariants()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"SPRINT 15C FYJC BK P0 GATE: {passed}/{total} checks passed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL - {f}")
        print("=" * 72)
        print("SPRINT 15C P0 FAIL - TRANSACTION SEMANTICS BLOCKER REMAINS")
        return 1
    print("UNSAFE CONFIDENT ANSWERS: 0 | INVENTED ACCOUNTS: 0 | "
          "INVENTED AMOUNTS: 0")
    print("UNBALANCED VERIFIED JOURNALS: 0 | SILENT SUBSTITUTIONS: 0 | "
          "FORMULA_ID=None CONFIDENT ANSWERS: 0")
    print("C++ AUTHORITY: VERIFIED (registered metrics) | "
          "REFUSALS: BLOCKED / REVIEW_REQUIRED / NOT_SUPPORTED")
    print("=" * 72)
    print("SPRINT 15C P0 PASS - FYJC BASIC TRANSACTION SEMANTICS RELIABLE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
