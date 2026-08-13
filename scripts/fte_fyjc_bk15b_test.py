#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15B - FYJC Book-Keeping Question Understanding & Reasoning Gate
scripts/fte_fyjc_bk15b_test.py

Deterministic regression gate for the hardened Book-Keeping & Accountancy
reasoning pipeline (backend/maths/fyjc_bk_reasoning.py):

    photo / PDF / typed question
      -> normalised transaction type      (wording variants collapse to ONE)
      -> EXACT account identification     (Furniture purchase NEVER invents
                                           Machinery/Building - hallucination
                                           guard)
      -> traditional Real/Personal/Nominal classification + Golden Rule
      -> debit/credit decision with student-readable WHY
      -> journal entry                    (date/particulars/Dr/Cr/narration,
                                           Dr == Cr always)
      -> ledger reasoning                 (derived from the journal IR only)
      -> trial balance reasoning          (Dr == Cr, never forced)
      -> trade / cash discount + partial-payment pipeline (exact numbers)
      -> C++ authority for registered metrics (authority_state == cpp,
                                           formula_id always set when resolved)
      -> refusal boundaries               (BLOCKED / REVIEW_REQUIRED /
                                           NOT_SUPPORTED - no invented
                                           accounts, amounts or treatments)

Hard invariants verified here (same spirit as the Sprint 15 release gate):
  * a VERIFIED journal is ALWAYS balanced (total_debit == total_credit)
  * a VERIFIED journal NEVER uses an invented account - the exact-account
    hallucination guard is tested for Furniture/Machinery/Building
  * a refusal NEVER carries a confident display or journal lines
  * every journal number carries a calculation_id provenance record
  * a registered metric resolved through verify_bk_metric ALWAYS returns
    authority_state == cpp with a non-empty formula_id; an unsupported
    metric is refused (UNSUPPORTED / formula_id None) - never a
    DERIVED/VERIFIED result with formula_id == None
  * identical input -> identical output (deterministic repeatability)

All cases run through the REAL hardened pipeline (reason_bk_question +
journal/ledger/trial-balance generators). Nothing here is committed or
pushed - this is a verification gate.

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
    NOT_SUPPORTED,
    build_bk_understanding,
    classify_bk_type,
    generate_journal,
    generate_ledger,
    generate_trial_balance,
    golden_rule_for,
    reason_bk_question,
    resolve_transaction_amounts,
    traditional_class_for,
    verify_bk_metric,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED  # noqa: E402

CHECKS: List[Tuple[str, bool, str]] = []
FAILURES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def _accounts(lines: Any) -> Set[str]:
    return {str(line.get("account")) for line in (lines or [])}


def _amounts(lines: Any) -> Dict[str, float]:
    """{account: numeric amount} - Decimal formatting (9000 vs 9000.00)
    is irrelevant to the accounting result, so amounts are compared as
    numbers."""
    out: Dict[str, float] = {}
    for line in (lines or []):
        try:
            out[str(line.get("account"))] = float(line.get("amount"))
        except (TypeError, ValueError):
            out[str(line.get("account"))] = float("nan")
    return out


def _num(value: Any) -> Optional[float]:
    """None-aware numeric normaliser for amount-field comparisons."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. Real-question understanding - wording variants -> ONE canonical type
# ---------------------------------------------------------------------------

# (question, expected_type_key, expected Dr accounts, expected Cr accounts)
ROUTING_CASES: List[Tuple[str, str, Set[str], Set[str]]] = [
    # --- goods purchases: cash / credit / cheque / named party ------------
    ("Purchased goods for cash Rs.10,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Cash"}),
    ("Bought goods for cash Rs.5,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Cash"}),
    ("Goods purchased for cash Rs.4,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Cash"}),
    # 'for cash' decides the MODE even with a named party
    ("Purchased goods from Amit for cash Rs.10,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Cash"}),
    ("Purchased goods in cash Rs.6,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Cash"}),
    ("Purchased stock by cheque Rs.8,000.", "PURCHASE_GOODS_CASH",
     {"Purchases"}, {"Bank"}),
    ("Purchased goods on credit from Rahul Rs.9,000.", "PURCHASE_GOODS_CREDIT",
     {"Purchases"}, {"Rahul"}),
    ("Goods purchased on credit from Rahul Rs.9,000.", "PURCHASE_GOODS_CREDIT",
     {"Purchases"}, {"Rahul"}),
    ("Bought goods from Amit on credit Rs.7,000.", "PURCHASE_GOODS_CREDIT",
     {"Purchases"}, {"Amit"}),
    ("Purchased goods from Rahul on credit for Rs.10,000.",
     "PURCHASE_GOODS_CREDIT", {"Purchases"}, {"Rahul"}),
    # --- goods sales: cash / credit / named party -------------------------
    ("Sold goods for cash Rs.15,000.", "SALE_GOODS_CASH",
     {"Cash"}, {"Sales"}),
    ("Sold goods to Mohan for cash Rs.15,000.", "SALE_GOODS_CASH",
     {"Cash"}, {"Sales"}),
    ("Cash sale Rs.3,000.", "SALE_GOODS_CASH", {"Cash"}, {"Sales"}),
    ("Sold stock for cash Rs.2,000.", "SALE_GOODS_CASH",
     {"Cash"}, {"Sales"}),
    ("Sold goods on credit to Mohan Rs.15,000.", "SALE_GOODS_CREDIT",
     {"Mohan"}, {"Sales"}),
    ("Sold goods to Mohan Rs.15,000 on credit.", "SALE_GOODS_CREDIT",
     {"Mohan"}, {"Sales"}),
    ("Goods sold to Mohan on credit Rs.6,000.", "SALE_GOODS_CREDIT",
     {"Mohan"}, {"Sales"}),
    # --- capital / drawings / business start ------------------------------
    ("Started business with cash Rs.50,000.", "START_BUSINESS",
     {"Cash"}, {"Capital"}),
    ("Brought in additional capital of Rs.20,000 by cheque.",
     "CAPITAL_INTRODUCED", {"Bank"}, {"Capital"}),
    ("Withdrew cash Rs.2,000 for personal use.", "DRAWINGS_CASH",
     {"Drawings"}, {"Cash"}),
    ("Goods taken for personal use Rs.1,000.", "GOODS_PERSONAL_USE",
     {"Drawings"}, {"Purchases"}),
    # --- expenses ----------------------------------------------------------
    ("Paid rent Rs.3,000.", "EXPENSE_PAID", {"Rent"}, {"Cash"}),
    ("Paid salaries Rs.4,000.", "EXPENSE_PAID", {"Salaries"}, {"Cash"}),
    ("Paid wages Rs.2,500.", "EXPENSE_PAID", {"Wages"}, {"Cash"}),
    ("Paid insurance premium Rs.1,200.", "EXPENSE_PAID",
     {"Insurance"}, {"Cash"}),
    ("Paid advertisement expenses Rs.1,500.", "EXPENSE_PAID",
     {"Advertisement"}, {"Cash"}),
    ("Paid electricity bill Rs.1,800.", "EXPENSE_PAID",
     {"Electricity"}, {"Cash"}),
    ("Purchased stationery for cash Rs.500.", "EXPENSE_PAID",
     {"Stationery"}, {"Cash"}),
    ("Paid telephone bill Rs.900.", "EXPENSE_PAID",
     {"Telephone Expenses"}, {"Cash"}),
    # --- incomes ------------------------------------------------------------
    ("Received commission Rs.1,200.", "INCOME_RECEIVED",
     {"Cash"}, {"Commission Received"}),
    ("Received rent Rs.2,000.", "INCOME_RECEIVED",
     {"Cash"}, {"Rent Received"}),
    ("Received dividend Rs.1,000.", "INCOME_RECEIVED",
     {"Cash"}, {"Dividend Received"}),
    ("Received interest Rs.800.", "INCOME_RECEIVED",
     {"Cash"}, {"Interest Received"}),
    # --- bank / cheque ------------------------------------------------------
    ("Deposited cash into bank Rs.5,000.", "CASH_INTO_BANK",
     {"Bank"}, {"Cash"}),
    ("Withdrew cash from bank Rs.3,000.", "CASH_FROM_BANK",
     {"Cash"}, {"Bank"}),
    ("Paid by cheque to Ramesh Rs.4,000.", "CHEQUE_PAID",
     {"Ramesh"}, {"Bank"}),
    ("Received a cheque from Suresh Rs.6,000.", "CHEQUE_RECEIVED",
     {"Bank"}, {"Suresh"}),
    # --- parties / returns / loans / interest -------------------------------
    ("Paid to Amit Rs.9,000.", "PAID_TO", {"Amit"}, {"Cash"}),
    ("Received from Mohan Rs.10,000.", "RECEIVED_FROM",
     {"Cash"}, {"Mohan"}),
    ("Returned goods to Amit Rs.500.", "PURCHASE_RETURN",
     {"Amit"}, {"Purchase Returns"}),
    ("Goods returned by Mohan Rs.700.", "SALES_RETURN",
     {"Sales Returns"}, {"Mohan"}),
    ("Took a loan from the bank Rs.10,000.", "LOAN_TAKEN",
     {"Bank"}, {"Loan"}),
    ("Repaid the loan Rs.4,000.", "LOAN_REPAID", {"Loan"}, {"Cash"}),
    ("Interest on capital allowed Rs.600.", "INTEREST_ON_CAPITAL",
     {"Interest on Capital"}, {"Capital"}),
    ("Interest on drawings charged Rs.200.", "INTEREST_ON_DRAWINGS",
     {"Drawings"}, {"Interest on Drawings"}),
    ("Goods distributed as free samples Rs.500.", "FREE_SAMPLES",
     {"Advertisement"}, {"Purchases"}),
]


def test_question_routing() -> None:
    for q, key, dr, cr in ROUTING_CASES:
        reason = reason_bk_question(q)
        check(
            f"routing: {q[:52]} -> {key} VERIFIED",
            reason.get("status") == VERIFIED,
            f"status={reason.get('status')} "
            f"why={str(reason.get('why_not'))[:60]}",
        )
        if reason.get("status") != VERIFIED:
            continue
        u = reason.get("understanding") or {}
        check(
            f"routing: {q[:52]} -> canonical type {key}",
            u.get("question_type_key") == key,
            f"type={u.get('question_type_key')}",
        )
        check(
            f"routing: {q[:52]} Dr exactly {sorted(dr)}",
            _accounts(reason.get("debit_lines")) == dr,
            str(_accounts(reason.get("debit_lines"))),
        )
        check(
            f"routing: {q[:52]} Cr exactly {sorted(cr)}",
            _accounts(reason.get("credit_lines")) == cr,
            str(_accounts(reason.get("credit_lines"))),
        )
        check(
            f"routing: {q[:52]} journal balanced",
            bool(reason.get("journal", {}).get("balanced")),
            f"Dr={reason.get('journal', {}).get('total_debit')} "
            f"Cr={reason.get('journal', {}).get('total_credit')}",
        )


# ---------------------------------------------------------------------------
# 2. EXACT account identification (hallucination guard - section 2)
# ---------------------------------------------------------------------------

EXACT_ASSET_CASES = [
    ("Purchased Furniture for Cash Rs.15,000.", {"Furniture"}, {"Cash"},
     {"Machinery", "Building", "Vehicle", "Equipment", "Land"}),
    ("Purchased Machinery on credit from Suresh Rs.50,000.",
     {"Machinery"}, {"Suresh"},
     {"Furniture", "Building", "Vehicle", "Equipment", "Land"}),
    ("Bought Office Equipment for cash Rs.12,000.",
     {"Office Equipment"}, {"Cash"},
     {"Furniture", "Machinery", "Building", "Vehicle"}),
    ("Sold Building for Cash Rs.60,000.", {"Cash"}, {"Building"},
     {"Furniture", "Machinery", "Vehicle", "Land"}),
    ("Sold old Machinery for cash Rs.20,000.", {"Cash"}, {"Machinery"},
     {"Furniture", "Building", "Vehicle", "Land"}),
    ("Purchased a computer for cash Rs.35,000.", {"Equipment"}, {"Cash"},
     {"Furniture", "Machinery", "Building"}),
]


def test_exact_account_identification() -> None:
    for q, dr, cr, forbidden in EXACT_ASSET_CASES:
        reason = reason_bk_question(q)
        check(
            f"exact-account: {q[:48]} VERIFIED",
            reason.get("status") == VERIFIED,
            f"status={reason.get('status')} "
            f"why={str(reason.get('why_not'))[:60]}",
        )
        if reason.get("status") != VERIFIED:
            continue
        check(
            f"exact-account: {q[:48]} Dr exactly {sorted(dr)}",
            _accounts(reason.get("debit_lines")) == dr,
            str(_accounts(reason.get("debit_lines"))),
        )
        check(
            f"exact-account: {q[:48]} Cr exactly {sorted(cr)}",
            _accounts(reason.get("credit_lines")) == cr,
            str(_accounts(reason.get("credit_lines"))),
        )
        all_accounts = _accounts(reason.get("debit_lines")) | \
            _accounts(reason.get("credit_lines"))
        check(
            f"exact-account: {q[:48]} NEVER invents "
            f"{sorted(forbidden)}",
            not (all_accounts & forbidden),
            str(sorted(all_accounts)),
        )
    # a question naming TWO assets is ambiguous - never guessed
    multi = reason_bk_question(
        "Purchased machinery and furniture for cash Rs.60,000.")
    check("exact-account: two assets -> REVIEW_REQUIRED (never guessed)",
          multi.get("status") == REVIEW_REQUIRED,
          f"status={multi.get('status')}")


# ---------------------------------------------------------------------------
# 3. Traditional FYJC classification + Golden Rule + WHY (section 3)
# ---------------------------------------------------------------------------

CLASS_EXPECTATIONS = [
    # (question, account, side, expected class)
    ("Purchased Furniture for Cash Rs.15,000.", "Furniture", "debit",
     CLASS_REAL),
    ("Purchased Furniture for Cash Rs.15,000.", "Cash", "credit", CLASS_REAL),
    ("Purchased goods on credit from Rahul Rs.9,000.", "Rahul", "credit",
     CLASS_PERSONAL),
    ("Sold goods on credit to Mohan Rs.15,000.", "Mohan", "debit",
     CLASS_PERSONAL),
    ("Purchased goods for cash Rs.10,000.", "Purchases", "debit",
     CLASS_NOMINAL),
    ("Sold goods for cash Rs.15,000.", "Sales", "credit", CLASS_NOMINAL),
    ("Started business with cash Rs.50,000.", "Capital", "credit",
     CLASS_PERSONAL),
]


def test_traditional_classification() -> None:
    for q, account, side, expected_cls in CLASS_EXPECTATIONS:
        journal = generate_journal(q)
        lines = (journal.get("debit_lines") or []) \
            + (journal.get("credit_lines") or [])
        line = next((l for l in lines
                     if l.get("account") == account and l.get("side") == side),
                    None)
        check(
            f"class: {account} ({side}) in '{q[:40]}' -> {expected_cls}",
            line is not None and line.get("class") == expected_cls,
            f"line={None if line is None else line.get('class')}",
        )
        if line is None:
            continue
        check(
            f"class: {account} carries the traditional Golden Rule text",
            line.get("rule") == TRADITIONAL_GOLDEN_RULES[expected_cls],
            str(line.get("rule")),
        )
        why = str(line.get("why") or "")
        check(
            f"class: {account} WHY is student-readable and names the class",
            expected_cls in why and len(why) > 20,
            why,
        )
    # direct helpers agree with the per-line classes
    check("golden_rule_for(Cash) == Real rule",
          golden_rule_for("Cash") == TRADITIONAL_GOLDEN_RULES[CLASS_REAL])
    check("golden_rule_for(Rahul) == Personal rule",
          golden_rule_for("Rahul") == TRADITIONAL_GOLDEN_RULES[CLASS_PERSONAL])
    check("traditional_class_for(Purchases) == Nominal",
          traditional_class_for("Purchases") == CLASS_NOMINAL)


# ---------------------------------------------------------------------------
# 4. Journal / Ledger / Trial Balance reasoning (sections 4-6)
# ---------------------------------------------------------------------------

LEDGER_CASES = [
    "Purchased goods for cash Rs.10,000.",
    "Sold goods on credit to Mohan Rs.15,000.",
    "Paid rent Rs.3,000.",
    "Started business with cash Rs.50,000 and furniture Rs.20,000.",
    "Received from Mohan Rs.9,800, discount allowed Rs.200.",
]


def test_journal_ledger_trial_balance() -> None:
    for q in LEDGER_CASES:
        journal = generate_journal(q)
        check(
            f"journal: '{q[:44]}' VERIFIED and balanced",
            journal.get("status") == VERIFIED
            and journal.get("total_debit") == journal.get("total_credit"),
            f"status={journal.get('status')} "
            f"Dr={journal.get('total_debit')} "
            f"Cr={journal.get('total_credit')}",
        )
        if journal.get("status") != VERIFIED:
            continue
        check(
            f"journal: '{q[:44]}' every line has a positive amount",
            all((l.get("amount") or 0) > 0
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])),
            "",
        )
        check(
            f"journal: '{q[:44]}' carries calculation provenance",
            any(s.get("calculation_id") == "BK_LIST_PRICE"
                for s in (journal.get("calculation_records") or [])),
            str([s.get("calculation_id")
                 for s in (journal.get("calculation_records") or [])]),
        )
        ledger = generate_ledger([journal])
        check(
            f"ledger: '{q[:44]}' balanced (derived from journal)",
            bool(ledger.get("balanced")),
            str(ledger.get("accounts")),
        )
        tb = generate_trial_balance([journal])
        check(
            f"trial balance: '{q[:44]}' Dr == Cr, never forced",
            bool(tb.get("balanced"))
            and tb.get("total_debit") == tb.get("total_credit"),
            f"Dr={tb.get('total_debit')} Cr={tb.get('total_credit')}",
        )

    # 'Started business with cash Rs.50,000 and furniture Rs.20,000'
    startup = generate_journal(
        "Started business with cash Rs.50,000 and furniture Rs.20,000.")
    check("startup breakdown: Cash + Furniture -> Capital total 70,000",
          startup.get("status") == VERIFIED
          and _amounts(startup.get("debit_lines")) ==
          {"Cash": 50000.0, "Furniture": 20000.0}
          and _amounts(startup.get("credit_lines")) == {"Capital": 70000.0},
          str((_amounts(startup.get("debit_lines")),
               _amounts(startup.get("credit_lines")))))

    # multi-transaction: independent segments -> ONE ledger + ONE TB
    multi = reason_bk_question(
        "Purchased goods for cash Rs.10,000; Sold goods for cash Rs.15,000; "
        "Paid rent Rs.3,000.")
    check("multi: three transactions journal together",
          multi.get("status") == VERIFIED
          and multi.get("journal", {}).get("multi") is True
          and multi.get("journal", {}).get("count") == 3,
          f"status={multi.get('status')} "
          f"count={multi.get('journal', {}).get('count')}",
          )
    tb = multi.get("trial_balance") or {}
    check("multi: combined trial balance Dr == Cr (never forced)",
          bool(tb.get("balanced"))
          and tb.get("total_debit") == tb.get("total_credit")
          and tb.get("total_debit") == 15000,
          f"Dr={tb.get('total_debit')} Cr={tb.get('total_credit')}",
          )

    # pronoun + payment-step folding: 'paid him' merges into the prior
    # transaction (Purchases 10,000 / Cash 4,000 + Rahul 6,000)
    merged = reason_bk_question(
        "Purchased goods from Rahul on credit Rs.10,000; paid him Rs.4,000.")
    check("multi: 'paid him' folds into the prior credit purchase",
          merged.get("status") == VERIFIED
          and _amounts(merged.get("debit_lines")) == {"Purchases": 10000.0}
          and _amounts(merged.get("credit_lines")) ==
          {"Cash": 4000.0, "Rahul": 6000.0},
          f"Dr={_amounts(merged.get('debit_lines'))} "
          f"Cr={_amounts(merged.get('credit_lines'))}",
          )

    # requested operation wording
    j = reason_bk_question("Journalise: Purchased goods for cash Rs.10,000.")
    check("requested operation: 'Journalise:' -> Journal Entry",
          (j.get("understanding") or {}).get("requested_operation")
          == "Journal Entry",
          str((j.get("understanding") or {}).get("requested_operation")))


# ---------------------------------------------------------------------------
# 5. Trade discount / cash discount / partial payment (section 7)
# ---------------------------------------------------------------------------

DISCOUNT_CASES = [
    {
        "q": "Purchased goods from Amit Rs.10,000 with 10% trade discount.",
        "amounts": {"net_value": 9000, "trade_discount_rate": 10},
        "journal": {"debit": {"Purchases": 9000.0},
                    "credit": {"Amit": 9000.0}},
    },
    {
        # trade 10% -> net 9,000; half paid immediately; 2% cash discount
        # on the paid portion (4,500 x 2% = 90) -> cash paid 4,410
        "q": "Purchased goods from Rahul for Rs.10,000 on credit with 10% "
             "trade discount; paid half immediately with 2% cash discount.",
        "amounts": {"net_value": 9000, "paid_amount": 4500,
                    "credit_amount": 4500, "cash_discount_rate": 2,
                    "cash_discount_amount": 90, "cash_paid": 4410},
        "journal": {"debit": {"Purchases": 9000.0},
                    "credit": {"Cash": 4410.0, "Discount Received": 90.0,
                               "Rahul": 4500.0}},
    },
    {
        "q": "Purchased goods from Rahul Rs.10,000 on credit; paid half "
             "immediately.",
        "amounts": {"net_value": 10000, "paid_amount": 5000,
                    "credit_amount": 5000},
        "journal": {"debit": {"Purchases": 10000.0},
                    "credit": {"Cash": 5000.0, "Rahul": 5000.0}},
    },
    {
        # explicit discount AMOUNT settlement (not a % rate)
        "q": "Received from Mohan Rs.9,800, discount allowed Rs.200.",
        # Sprint 15I-L: the amount-based cash-discount pipeline now ALSO
        # exposes the settlement numbers (cash_paid / cash_discount_amount)
        # - the journals are unchanged, only the internal contract grew.
        "amounts": {"cash_paid": 9800.0, "cash_discount_amount": 200.0,
                    "explicit": {"party_total": 10000.0,
                                 "cash_amount": 9800.0,
                                 "discount_amount": 200.0}},
        "journal": {"debit": {"Cash": 9800.0, "Discount Allowed": 200.0},
                    "credit": {"Mohan": 10000.0}},
    },
    {
        "q": "Paid to Amit Rs.9,800, discount received Rs.200.",
        "amounts": {"cash_paid": 9800.0, "cash_discount_amount": 200.0,
                    "explicit": {"party_total": 10000.0,
                                 "cash_amount": 9800.0,
                                 "discount_amount": 200.0}},
        "journal": {"debit": {"Amit": 10000.0},
                    "credit": {"Cash": 9800.0, "Discount Received": 200.0}},
    },
    {
        "q": "Received from Mohan Rs.10,000; allowed him discount Rs.200.",
        "amounts": {"explicit": {"party_total": 10200.0,
                                 "cash_amount": 10000.0,
                                 "discount_amount": 200.0}},
        "journal": {"debit": {"Cash": 10000.0, "Discount Allowed": 200.0},
                    "credit": {"Mohan": 10200.0}},
    },
]


def test_discount_pipeline() -> None:
    for case in DISCOUNT_CASES:
        q = case["q"]
        amounts = resolve_transaction_amounts(q)
        journal = generate_journal(q)
        check(
            f"discount: '{q[:52]}' journal VERIFIED",
            journal.get("status") == VERIFIED,
            f"status={journal.get('status')} "
            f"why={str(journal.get('why_not'))[:60]}",
        )
        if journal.get("status") != VERIFIED:
            continue
        want_amounts = case.get("amounts") or {}
        if "net_value" in want_amounts:
            check(
                f"discount: '{q[:52]}' net value "
                f"{want_amounts['net_value']}",
                _num(amounts.get("net_value")) == want_amounts["net_value"],
                str(amounts.get("net_value")),
            )
        if "trade_discount_rate" in want_amounts:
            check(
                f"discount: '{q[:52]}' trade discount "
                f"{want_amounts['trade_discount_rate']}%",
                _num(amounts.get("trade_discount_rate"))
                == want_amounts["trade_discount_rate"],
                str(amounts.get("trade_discount_rate")),
            )
        for key in ("paid_amount", "credit_amount", "cash_discount_rate",
                    "cash_discount_amount", "cash_paid"):
            if key in want_amounts:
                check(
                    f"discount: '{q[:52]}' {key} "
                    f"{want_amounts[key]}",
                    _num(amounts.get(key)) == want_amounts[key],
                    str(amounts.get(key)),
                )
        want_journal = case.get("journal") or {}
        if "debit" in want_journal:
            check(
                f"discount: '{q[:52]}' Dr accounts/amounts exactly "
                f"{want_journal['debit']}",
                _amounts(journal.get("debit_lines"))
                == want_journal["debit"],
                str(_amounts(journal.get("debit_lines"))),
            )
        if "credit" in want_journal:
            check(
                f"discount: '{q[:52]}' Cr accounts/amounts exactly "
                f"{want_journal['credit']}",
                _amounts(journal.get("credit_lines"))
                == want_journal["credit"],
                str(_amounts(journal.get("credit_lines"))),
            )
        if "explicit" in want_amounts:
            explicit = amounts.get("explicit_discount") or {}
            check(
                f"discount: '{q[:52]}' explicit settlement "
                f"{want_amounts['explicit']}",
                {k: _num(explicit.get(k))
                 for k in ("party_total", "cash_amount",
                           "discount_amount")}
                == want_amounts["explicit"],
                str(explicit),
            )
            # the naive paid/credit split must NOT leak into the
            # understanding when an explicit discount settles the account
            u = build_bk_understanding(q)
            roles = [row.get("role") for row in
                     (u.get("amounts_identified") or [])]
            check(
                f"discount: '{q[:52]}' understanding shows the explicit "
                "settlement, not a misleading paid/credit split",
                "On credit" not in roles
                and any("Party account total" in r for r in roles),
                str(roles),
            )

    # discount with NO settlement context is never posted on its own
    standalone = generate_journal("Discount allowed Rs.200.")
    check("discount: standalone discount refused (no invented cash side)",
          standalone.get("status") == REVIEW_REQUIRED,
          f"status={standalone.get('status')}")
    # 'full settlement' without a stated discount is never guessed
    full = generate_journal("Received Rs.5,000 in full settlement of "
                            "Rs.5,200.")
    check("discount: 'full settlement' without discount amount refused",
          full.get("status") == REVIEW_REQUIRED,
          f"status={full.get('status')}")


# ---------------------------------------------------------------------------
# 6. Refusal boundaries (section 9)
# ---------------------------------------------------------------------------

REFUSAL_CASES = [
    # (question, expected status, expected behaviour)
    ("Paid rent.", BLOCKED, "amount missing"),
    ("Received commission.", BLOCKED, "amount missing"),
    ("Purchased goods.", REVIEW_REQUIRED, "cash or credit not stated"),
    ("Purchased goods for Rs.10,000.", REVIEW_REQUIRED,
     "cash or credit not stated"),
    ("Purchased furniture.", REVIEW_REQUIRED, "cash or credit not stated"),
    ("Sold machinery.", REVIEW_REQUIRED, "cash or credit not stated"),
    ("Discount allowed Rs.200.", REVIEW_REQUIRED, "no settlement context"),
    ("Received Rs.5,000 in full settlement of Rs.5,200.", REVIEW_REQUIRED,
     "unstated discount"),
    ("Calculate depreciation on machinery at 10% p.a.", NOT_SUPPORTED,
     "outside the FYJC boundary"),
    ("Prepare a Profit and Loss account.", NOT_SUPPORTED,
     "outside the FYJC boundary"),
]


def test_refusal_boundaries() -> None:
    for q, expected, why in REFUSAL_CASES:
        reason = reason_bk_question(q)
        check(
            f"refusal: '{q[:44]}' -> {expected} ({why})",
            reason.get("status") == expected,
            f"status={reason.get('status')}",
        )
        check(
            f"refusal: '{q[:44]}' carries no confident journal lines",
            not (reason.get("debit_lines") or reason.get("credit_lines")),
            "",
        )
        check(
            f"refusal: '{q[:44]}' explains why + next action",
            bool(reason.get("why_not")) and bool(reason.get("next_action")),
            f"why={str(reason.get('why_not'))[:50]}",
        )


# ---------------------------------------------------------------------------
# 7. C++ authority + hard invariants (section 8 + release gate)
# ---------------------------------------------------------------------------

def test_cpp_authority() -> None:
    # registered metric -> C++ authority, formula_id ALWAYS present
    pm = verify_bk_metric("Profit Margin",
                          facts={"Profit": 200, "Revenue": 1000})
    check("cpp: Profit Margin resolves via formula PROFIT_MARGIN",
          pm.get("formula_id") == "PROFIT_MARGIN"
          and pm.get("display_value") == "20.00%",
          f"formula={pm.get('formula_id')} display={pm.get('display_value')}")
    check("cpp: Profit Margin carries authority_state == cpp",
          pm.get("authority_state") == "cpp",
          str(pm.get("authority_state")))

    roe = verify_bk_metric("ROE",
                           facts={"Net Profit": 200, "Equity": 1000})
    check("cpp: ROE resolves via formula ROE with cpp authority",
          roe.get("formula_id") == "ROE"
          and roe.get("authority_state") == "cpp"
          and roe.get("display_value") == "20.00%",
          f"formula={roe.get('formula_id')} "
          f"authority={roe.get('authority_state')} "
          f"display={roe.get('display_value')}")

    # unsupported metric -> refused, NEVER a DERIVED/VERIFIED with
    # formula_id == None
    dep = verify_bk_metric("Depreciation")
    check("cpp: unsupported metric is refused (formula_id stays None)",
          dep.get("status") == "UNSUPPORTED"
          and dep.get("formula_id") is None
          and dep.get("resolved") is False,
          f"status={dep.get('status')} "
          f"formula={dep.get('formula_id')} "
          f"resolved={dep.get('resolved')}")


def test_hard_invariants() -> None:
    """Determinism + balance + provenance across every case in this gate."""
    all_questions = (
        [c[0] for c in ROUTING_CASES]
        + [c[0] for c in EXACT_ASSET_CASES]
        + LEDGER_CASES
        + [c["q"] for c in DISCOUNT_CASES]
        + [c[0] for c in REFUSAL_CASES]
    )
    bad_det: List[str] = []
    bad_balance: List[str] = []
    bad_provenance: List[str] = []
    bad_refusal_display: List[str] = []

    for q in all_questions:
        r1 = reason_bk_question(q)
        r2 = reason_bk_question(q)
        if json.dumps(r1, sort_keys=True, default=str) != \
                json.dumps(r2, sort_keys=True, default=str):
            bad_det.append(q[:40])
        if r1.get("status") == VERIFIED:
            journal = r1.get("journal") or {}
            if not journal.get("balanced"):
                bad_balance.append(q[:40])
            # single-transaction flows carry records on the journal;
            # multi-transaction flows expose them at the top level
            records = (r1.get("calculation_records") or [])
            if not records:
                records = journal.get("calculation_records") or []
            if not any(s.get("calculation_id") == "BK_LIST_PRICE"
                       for s in records):
                bad_provenance.append(q[:40])
        elif r1.get("status") in (BLOCKED, REVIEW_REQUIRED, NOT_SUPPORTED):
            if r1.get("debit_lines") or r1.get("credit_lines"):
                bad_refusal_display.append(q[:40])

    check("invariant: deterministic repeatability", not bad_det, str(bad_det))
    check("invariant: every VERIFIED journal is balanced",
          not bad_balance, str(bad_balance))
    check("invariant: every VERIFIED journal has calculation provenance",
          not bad_provenance, str(bad_provenance))
    check("invariant: refusals never carry journal lines",
          not bad_refusal_display, str(bad_refusal_display))


def main() -> int:
    test_question_routing()
    test_exact_account_identification()
    test_traditional_classification()
    test_journal_ledger_trial_balance()
    test_discount_pipeline()
    test_refusal_boundaries()
    test_cpp_authority()
    test_hard_invariants()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"SPRINT 15B BOOK-KEEPING REASONING GATE: {passed}/{total} checks "
          "passed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL - {f}")
        print("=" * 72)
        print("SPRINT 15B FAIL - BOOK-KEEPING REASONING BLOCKER REMAINS")
        return 1
    print("INVENTED ACCOUNTS: 0 | UNBALANCED JOURNALS: 0 | "
          "FORMULA_ID=None CONFIDENT ANSWERS: 0 | SILENT SUBSTITUTIONS: 0")
    print("C++ AUTHORITY: VERIFIED (registered metrics) | "
          "REFUSALS: BLOCKED / REVIEW_REQUIRED / NOT_SUPPORTED")
    print("=" * 72)
    print("SPRINT 15B PASS - FYJC BOOK-KEEPING REASONING HARDENED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
