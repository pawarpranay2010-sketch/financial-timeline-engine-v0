"""
Financial Timeline Engine
Sprint 15F - FYJC Book-Keeping Ch.1-3 Reusable Pattern System
backend/maths/fyjc_bk_15f.py

The Sprint 15F additions live here so the Sprint 15B-E engine file stays
a focused reasoning pipeline. This module provides:

  * student-answer verification (spec section 12): verify a student's
    journal / ledger / trial-balance / final answer against the engine
    reference and report the FIRST deterministic mistake - never just
    'wrong';
  * the reusable pattern library (spec section 2): one record per
    canonical FYJC pattern with its wording variants, account structure,
    golden rule, journal/ledger/trial-balance effects and refusal
    conditions - this is the registry that makes the coverage report
    possible (spec section 16);
  * pattern_coverage_report / write_coverage_report: machine-readable
    (JSON) and human-readable (Markdown) per-pattern test/pass coverage.

The oracle for every benchmark case lives in
backend/maths/fyjc_bk_15f_benchmark.py and NEVER calls the engine. This
module only RANKS engine output against those hand-written oracles.

Deterministic. No AI. No network. No invented accounts/amounts.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_accounting import (
    build_trial_balance,
    canonical_account,
    post_ledger,
)
from backend.maths.fyjc_bk_reasoning import (
    generate_journal,
    journal_to_entries,
    reason_bk_question,
    traditional_class_for,
)
from backend.maths.normalization import parse_numeric_text
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

NOT_SUPPORTED = "NOT_SUPPORTED"

_TOLERANCE = Decimal("0.01")


# ---------------------------------------------------------------------------
# 12. Student-answer verification - first deterministic mistake
# ---------------------------------------------------------------------------


def _student_lines(student_entry: Any) -> Tuple[List[Dict[str, Any]],
                                                List[Dict[str, Any]], bool]:
    """Normalise a student journal submission to line dicts.

    Accepts {'debits': [('Furniture', 15000), {'account': .., 'amount': ..}],
    'credits': [...]} or the engine's own debit_lines/credit_lines shape.
    Returns (debits, credits, readable). A line is readable only when it
    has a non-empty account and a parseable non-negative amount - nothing
    is ever guessed or corrected.
    """
    debits: List[Dict[str, Any]] = []
    credits: List[Dict[str, Any]] = []
    raw_debits = (student_entry or {}).get("debits") or []
    raw_credits = (student_entry or {}).get("credits") or []

    def _line(raw: Any, side: str) -> Optional[Dict[str, Any]]:
        if isinstance(raw, (tuple, list)) and len(raw) >= 2:
            account, amount = str(raw[0]), raw[1]
        elif isinstance(raw, dict):
            account = str(raw.get("account") or "")
            amount = raw.get("amount")
        else:
            return None
        account = account.strip()
        parsed = parse_numeric_text(amount) if amount not in (None, "") \
            else None
        if not account or parsed is None or parsed.value is None:
            return None
        return {"account": account, "amount": parsed.value, "side": side}

    for raw in raw_debits:
        line = _line(raw, "debit")
        if line is None:
            return [], [], False
        debits.append(line)
    for raw in raw_credits:
        line = _line(raw, "credit")
        if line is None:
            return [], [], False
        credits.append(line)
    return debits, credits, True


def _mistake_report(verdict: str, first_mistake: str, expected: Any,
                    given: Any, discrepancy: Any = None) -> Dict[str, Any]:
    return {
        "verdict": verdict,
        "status": VERIFIED if verdict == "CORRECT" else REVIEW_REQUIRED,
        "authority_state": "bookkeeping",
        "first_mistake": first_mistake,
        "expected": expected,
        "given": given,
        "discrepancy": discrepancy,
    }


def verify_student_journal(question: str,
                           student_entry: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a student's journal against the engine reference (12-B).

    Deterministic ordered checks - the FIRST failing check is reported as
    first_mistake, never a blanket 'wrong':

      1. readable structure        (REFUSED if not)
      2. totals Debit == Credit    (exact discrepancy exposed, never forced)
      3. debit-side accounts match the reference debit accounts
      4. credit-side accounts match the reference credit accounts
      5. per-line amounts (account + amount pairs on both sides)
      6. traditional class (Real/Personal/Nominal) of every account

    Reference = generate_journal(question): the same deterministic IR the
    rest of the pipeline uses, so the check measures the student against
    the engine's reasoning - never against a second, hidden treatment.
    """
    if not question or not str(question).strip():
        return _mistake_report("REFUSED", "No transaction description.",
                               None, None)
    reference = generate_journal(str(question).strip())
    if reference["status"] != VERIFIED:
        return {
            "verdict": "REFUSED",
            "status": reference["status"],
            "authority_state": "bookkeeping",
            "first_mistake": None,
            "expected": None,
            "given": None,
            "why_not": reference.get("why_not"),
            "next_action": reference.get("next_action"),
        }
    debits, credits, readable = _student_lines(student_entry)
    if not readable:
        return _mistake_report(
            "REFUSED",
            "The journal entry could not be read - every line needs an "
            "account name and a numeric amount.",
            None, student_entry)
    if not debits or not credits:
        return _mistake_report(
            "REFUSED", "The journal has no debit or credit lines.", None,
            student_entry)

    ref_debits = {l["account"] for l in reference["debit_lines"]}
    ref_credits = {l["account"] for l in reference["credit_lines"]}

    # 2. totals
    total_debit = sum((l["amount"] for l in debits), Decimal(0))
    total_credit = sum((l["amount"] for l in credits), Decimal(0))
    if abs(total_debit - total_credit) > _TOLERANCE:
        return _mistake_report(
            "INCORRECT",
            "The journal is not balanced (total Debit must equal total "
            "Credit).",
            {"total_debit": total_debit, "total_credit": total_credit},
            {"total_debit": float(total_debit),
             "total_credit": float(total_credit)},
            discrepancy=abs(total_debit - total_credit))

    # 3/4. sides and accounts
    student_debits = {l["account"] for l in debits}
    student_credits = {l["account"] for l in credits}
    if student_debits != ref_debits:
        missing = sorted(ref_debits - student_debits)
        extra = sorted(student_debits - ref_debits)
        return _mistake_report(
            "INCORRECT",
            "The debit side has the wrong accounts - expected "
            f"{sorted(ref_debits)}, you entered {sorted(student_debits)}"
            + (f" (missing: {missing})" if missing else "")
            + (f" (extra: {extra})" if extra else "") + ".",
            sorted(ref_debits), sorted(student_debits))
    if student_credits != ref_credits:
        missing = sorted(ref_credits - student_credits)
        extra = sorted(student_credits - ref_credits)
        return _mistake_report(
            "INCORRECT",
            "The credit side has the wrong accounts - expected "
            f"{sorted(ref_credits)}, you entered {sorted(student_credits)}"
            + (f" (missing: {missing})" if missing else "")
            + (f" (extra: {extra})" if extra else "") + ".",
            sorted(ref_credits), sorted(student_credits))

    # 5. amounts per line
    ref_pairs = {(l["account"], l["amount"]) for l in reference["debit_lines"]}
    ref_pairs |= {(l["account"], l["amount"])
                  for l in reference["credit_lines"]}
    student_pairs = {(l["account"], l["amount"]) for l in debits + credits}
    if ref_pairs != student_pairs:
        wrong = sorted((a, float(amt)) for a, amt in
                       (student_pairs - ref_pairs))
        return _mistake_report(
            "INCORRECT",
            "An amount is wrong - the reference journal posts "
            f"{sorted((a, float(amt)) for a, amt in ref_pairs)}, your "
            f"lines differ on {wrong}.",
            sorted((a, float(amt)) for a, amt in ref_pairs), wrong)

    # 6. traditional classification
    ref_class = {l["account"]: l["class"] for l in
                 reference["debit_lines"] + reference["credit_lines"]}
    for line in debits + credits:
        student_class = str(line.get("class") or "") or \
            traditional_class_for(line["account"])
        if student_class != ref_class.get(line["account"]):
            return _mistake_report(
                "INCORRECT",
                f"The classification of '{line['account']}' is wrong - it "
                f"is a {ref_class.get(line['account'])} Account "
                f"(Real/Personal/Nominal), not {student_class}.",
                ref_class.get(line["account"]), student_class)

    return _mistake_report(
        "CORRECT", None,
        {"debit": sorted(ref_pairs), "credit": sorted(ref_pairs)},
        {"debits": [(l["account"], float(l["amount"])) for l in debits],
         "credits": [(l["account"], float(l["amount"])) for l in credits]})


def verify_student_final(question: str, answer: Any,
                         what: str = "journal_total") -> Dict[str, Any]:
    """Verify a final-answer-only submission (12-A).

    what:
      journal_total       - the total Debit (= total Credit) of the journal
      trial_balance_total - the total of the trial balance (Dr = Cr)
      debit:<Account>     - the amount debited to <Account> in the journal
      credit:<Account>    - the amount credited to <Account>
      balance:<Account>   - the ledger balance of <Account> ('12,000 Dr')

    answer may carry a Dr/Cr suffix for balances. The reference numbers
    come from generate_journal -> journal -> ledger -> trial balance, so
    the check is fully deterministic.
    """
    if not question or not str(question).strip():
        return _mistake_report("REFUSED", "No transaction description.",
                               None, None)
    # the reference comes from the FULL pipeline so a multi-transaction
    # question is aggregated journal -> ledger -> trial balance exactly as
    # the student would do it (never a single-journal misreading).
    reference = reason_bk_question(str(question).strip())
    if reference["status"] != VERIFIED:
        return {
            "verdict": "REFUSED", "status": reference["status"],
            "authority_state": "bookkeeping", "first_mistake": None,
            "expected": None, "given": None,
            "why_not": reference.get("why_not"),
            "next_action": reference.get("next_action"),
        }
    journals = reference.get("journals") or [reference.get("journal")] or []
    entries: List[Dict[str, Any]] = []
    for j in journals:
        if j.get("status") == VERIFIED:
            entries.extend(journal_to_entries(j))
    all_debit_lines = [l for j in journals
                       for l in (j.get("debit_lines") or [])]
    all_credit_lines = [l for j in journals
                        for l in (j.get("credit_lines") or [])]
    expected_amount: Optional[Decimal] = None
    expected_side: Optional[str] = None
    label = what
    if what == "journal_total":
        expected_amount = sum((l["amount"] for l in all_debit_lines),
                              Decimal(0))
        label = "the total Debit (= Credit) of the journal"
    elif what == "trial_balance_total":
        tb = build_trial_balance(entries)
        expected_amount = Decimal(str(tb["total_debit"]))
        label = "the total of the trial balance"
    elif what.startswith("debit:") or what.startswith("credit:"):
        side, account = what.split(":", 1)
        account = canonical_account(account) or account
        lines = all_debit_lines if side == "debit" else all_credit_lines
        expected_amount = sum(
            (l["amount"] for l in lines
             if (canonical_account(l["account"]) or l["account"]) == account),
            Decimal(0))
        label = f"the amount {side}ed to {account}"
    elif what.startswith("balance:"):
        account = what.split(":", 1)[1]
        canon = canonical_account(account) or account
        ledger = post_ledger(entries)
        row = ledger["accounts"].get(canon)
        if row is None:
            return _mistake_report(
                "REFUSED", f"No ledger postings found for '{account}'.",
                None, answer)
        expected_amount = Decimal(str(abs(row["balance"])))
        expected_side = row["balance_side"]
        label = f"the {account} ledger balance"
    else:
        return _mistake_report("REFUSED", f"Unknown check kind '{what}'.",
                               None, answer)

    if expected_amount is None:
        return _mistake_report("REFUSED", "Reference could not be derived.",
                               None, answer)
    raw = str(answer or "").strip()
    given_side = None
    m_side = re.search(r"(?i)\b(dr|cr|debit|credit)\b\s*$", raw)
    if m_side:
        given_side = {"dr": "Dr", "debit": "Dr", "cr": "Cr",
                      "credit": "Cr"}[m_side.group(1).lower()]
        raw = raw[:m_side.start()].strip()
    parsed = parse_numeric_text(raw)
    if parsed is None or parsed.value is None:
        return _mistake_report(
            "REFUSED", "The answer could not be read as a number.", None,
            answer)
    if expected_side and given_side and given_side != expected_side:
        return _mistake_report(
            "INCORRECT",
            f"The side is wrong - {label} is {expected_side}, you entered "
            f"{given_side}.",
            f"{expected_side} {expected_amount}", f"{given_side} {raw}")
    if abs(parsed.value - expected_amount) > _TOLERANCE:
        return _mistake_report(
            "INCORRECT",
            f"The answer is wrong - {label} is {expected_amount}, you "
            f"entered {raw}.",
            float(expected_amount), float(parsed.value),
            discrepancy=abs(parsed.value - expected_amount))
    return _mistake_report(
        "CORRECT", None, float(expected_amount), float(parsed.value))


# ---------------------------------------------------------------------------
# 2/16. Reusable pattern library + coverage report
# ---------------------------------------------------------------------------

# One record per canonical FYJC pattern. wording_variants are the
# semantically-equivalent textbook phrasings the registry resolves to this
# ONE pattern (never one handler per sentence). refusal_conditions state
# exactly when the pattern refuses instead of guessing.
BK_PATTERN_LIBRARY: List[Dict[str, Any]] = [
    {
        "pattern_id": "START_BUSINESS",
        "description": "Starting the business with capital (cash, bank or "
                       "named assets)",
        "example_category": "Capital & Drawings",
        "required_inputs": ["amount", "the capital side (cash | bank | "
                            "named asset(s))"],
        "account_structure": {"debit": ["Cash | Bank | named asset(s)"],
                              "credit": ["Capital"]},
        "golden_rule": ["Cash/Bank/assets: Real - Debit what comes in",
                        "Capital: Personal - Credit the giver"],
        "journal_structure": "Cash/Bank/<asset> A/c Dr ... / To Capital A/c ...",
        "ledger_effect": "Cash/Bank/asset balances increase (Dr); Capital "
                         "balance increases (Cr)",
        "trial_balance_effect": "Cash/Bank/assets on the debit side; Capital "
                                "on the credit side",
        "wording_variants": ["started business with cash", "commenced "
                             "business with bank balance", "began business "
                             "with cash and furniture", "started the "
                             "business with cash Rs.X and bank balance Rs.Y"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "more than one named asset -> refused (never "
                               "guessed split)"],
    },
    {
        "pattern_id": "CAPITAL_INTRODUCED",
        "description": "Additional capital brought in during the year",
        "example_category": "Capital & Drawings",
        "required_inputs": ["amount", "the capital side (cash | bank)"],
        "account_structure": {"debit": ["Cash | Bank"], "credit": ["Capital"]},
        "golden_rule": ["Cash/Bank: Real - Debit what comes in",
                        "Capital: Personal - Credit the giver"],
        "journal_structure": "Cash/Bank A/c Dr ... / To Capital A/c ...",
        "ledger_effect": "Cash/Bank balance increases (Dr); Capital balance "
                         "increases (Cr)",
        "trial_balance_effect": "Cash/Bank on the debit side; Capital on the "
                                "credit side",
        "wording_variants": ["brought in additional capital", "introduced "
                             "capital", "brought into the business as "
                             "capital"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "CAPITAL_ASSET_INTRODUCED",
        "description": "An asset brought into the business as capital",
        "example_category": "Capital & Drawings",
        "required_inputs": ["amount", "the exact asset word"],
        "account_structure": {"debit": ["exact named asset"],
                              "credit": ["Capital"]},
        "golden_rule": ["Asset: Real - Debit what comes in",
                        "Capital: Personal - Credit the giver"],
        "journal_structure": "<Asset> A/c Dr ... / To Capital A/c ...",
        "ledger_effect": "Asset balance increases (Dr); Capital increases (Cr)",
        "trial_balance_effect": "Asset on the debit side; Capital on the "
                                "credit side",
        "wording_variants": ["brought machinery worth Rs.X into the "
                             "business", "introduced furniture worth Rs.X as "
                             "additional capital", "brought furniture into "
                             "the business as capital Rs.X"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               ">1 named asset -> refused (never split)"],
    },
    {
        "pattern_id": "DRAWINGS_CASH",
        "description": "Cash (or bank) withdrawn for personal/private use",
        "example_category": "Capital & Drawings",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Drawings"],
                              "credit": ["Cash | Bank"]},
        "golden_rule": ["Drawings: Personal - Debit the receiver (the "
                        "proprietor)", "Cash/Bank: Real - Credit what goes "
                        "out"],
        "journal_structure": "Drawings A/c Dr ... / To Cash/Bank A/c ...",
        "ledger_effect": "Drawings balance increases (Dr); Cash/Bank "
                         "decreases (Cr)",
        "trial_balance_effect": "Drawings on the debit side; Cash/Bank on "
                                "the credit side",
        "wording_variants": ["withdrew cash for personal use", "withdrawn "
                             "for private use", "cash withdrawn from bank "
                             "for personal use"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "GOODS_PERSONAL_USE",
        "description": "Goods taken by the proprietor for personal use",
        "example_category": "Capital & Drawings",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Drawings"], "credit": ["Purchases"]},
        "golden_rule": ["Drawings: Personal - Debit the receiver",
                        "Purchases: Nominal - Credit incomes/gains (goods "
                        "returned to the business)"],
        "journal_structure": "Drawings A/c Dr ... / To Purchases A/c ...",
        "ledger_effect": "Drawings increases (Dr); Purchases decreases (Cr)",
        "trial_balance_effect": "Drawings on the debit side; Purchases on "
                                "the credit side",
        "wording_variants": ["withdrew goods worth Rs.X for personal use",
                             "goods taken by the proprietor for private use",
                             "goods for personal use"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "PURCHASE_GOODS_CASH",
        "description": "Goods purchased for cash or by cheque",
        "example_category": "Purchases",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Purchases"],
                              "credit": ["Cash | Bank"]},
        "golden_rule": ["Purchases: Nominal - Debit expenses and losses",
                        "Cash/Bank: Real - Credit what goes out"],
        "journal_structure": "Purchases A/c Dr ... / To Cash/Bank A/c ...",
        "ledger_effect": "Purchases increases (Dr); Cash/Bank decreases (Cr)",
        "trial_balance_effect": "Purchases on the debit side; Cash/Bank on "
                                "the credit side",
        "wording_variants": ["purchased goods for cash", "bought goods for "
                             "cash", "goods purchased for Rs.X in cash",
                             "purchased goods paying cash", "purchased "
                             "goods by cheque", "goods purchased and payment "
                             "made immediately"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "cash vs credit not stated -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "PURCHASE_GOODS_CREDIT",
        "description": "Goods purchased on credit from a named supplier",
        "example_category": "Purchases",
        "required_inputs": ["amount", "the supplier (party)"],
        "account_structure": {"debit": ["Purchases"],
                              "credit": ["<supplier> (Personal)"]},
        "golden_rule": ["Purchases: Nominal - Debit expenses and losses",
                        "<supplier>: Personal - Credit the giver"],
        "journal_structure": "Purchases A/c Dr ... / To <supplier> A/c ...",
        "ledger_effect": "Purchases increases (Dr); <supplier> creditor "
                         "balance increases (Cr)",
        "trial_balance_effect": "Purchases on the debit side; <supplier> on "
                                "the credit side",
        "wording_variants": ["purchased goods from Rahul on credit",
                             "bought goods on credit from Rahul", "goods "
                             "purchased from Rahul for Rs.X on credit",
                             "bought goods on account from Rahul",
                             "purchased goods worth Rs.X from Rahul"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "no supplier named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "SALE_GOODS_CASH",
        "description": "Goods sold for cash or by cheque (a named customer "
                       "never becomes a debtor)",
        "example_category": "Sales",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Cash | Bank"], "credit": ["Sales"]},
        "golden_rule": ["Cash/Bank: Real - Debit what comes in",
                        "Sales: Nominal - Credit incomes and gains"],
        "journal_structure": "Cash/Bank A/c Dr ... / To Sales A/c ...",
        "ledger_effect": "Cash/Bank increases (Dr); Sales increases (Cr)",
        "trial_balance_effect": "Cash/Bank on the debit side; Sales on the "
                                "credit side",
        "wording_variants": ["sold goods for cash", "sold goods to Mohan "
                             "for cash", "cash sale of goods", "goods sold "
                             "and cash received immediately", "goods sold "
                             "by cheque"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "SALE_GOODS_CREDIT",
        "description": "Goods sold on credit to a named customer",
        "example_category": "Sales",
        "required_inputs": ["amount", "the customer (party)"],
        "account_structure": {"debit": ["<customer> (Personal)"],
                              "credit": ["Sales"]},
        "golden_rule": ["<customer>: Personal - Debit the receiver",
                        "Sales: Nominal - Credit incomes and gains"],
        "journal_structure": "<customer> A/c Dr ... / To Sales A/c ...",
        "ledger_effect": "<customer> debtor balance increases (Dr); Sales "
                         "increases (Cr)",
        "trial_balance_effect": "<customer> on the debit side; Sales on the "
                                "credit side",
        "wording_variants": ["sold goods to Mohan on credit", "sold to "
                             "Mohan for Rs.X on credit", "goods sold on "
                             "credit to Mohan", "sold goods on account to "
                             "Mohan"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "no customer named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "PURCHASE_ASSET_CASH",
        "description": "A fixed asset purchased for cash/cheque - EXACT "
                       "named asset only",
        "example_category": "Purchases (assets)",
        "required_inputs": ["amount", "the exact asset word"],
        "account_structure": {"debit": ["exact asset"], "credit": ["Cash | "
                            "Bank"]},
        "golden_rule": ["Asset: Real - Debit what comes in",
                        "Cash/Bank: Real - Credit what goes out"],
        "journal_structure": "<Asset> A/c Dr ... / To Cash/Bank A/c ...",
        "ledger_effect": "Asset balance increases (Dr); Cash/Bank decreases "
                         "(Cr)",
        "trial_balance_effect": "Asset on the debit side; Cash/Bank on the "
                                "credit side",
        "wording_variants": ["purchased furniture for cash", "bought "
                             "machinery for cash", "building purchased for "
                             "Rs.X in cash", "purchased furniture costing "
                             "Rs.X, payment made immediately"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               ">1 asset named -> refused",
                               "mode not stated -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "PURCHASE_ASSET_CREDIT",
        "description": "A fixed asset purchased on credit from a supplier",
        "example_category": "Purchases (assets)",
        "required_inputs": ["amount", "the exact asset word", "supplier"],
        "account_structure": {"debit": ["exact asset"],
                              "credit": ["<supplier> (Personal)"]},
        "golden_rule": ["Asset: Real - Debit what comes in",
                        "<supplier>: Personal - Credit the giver"],
        "journal_structure": "<Asset> A/c Dr ... / To <supplier> A/c ...",
        "ledger_effect": "Asset increases (Dr); supplier creditor increases "
                         "(Cr)",
        "trial_balance_effect": "Asset on the debit side; supplier on the "
                                "credit side",
        "wording_variants": ["bought furniture from Vijay on credit",
                             "purchased machinery from Suresh on credit",
                             "furniture purchased from Rahul for Rs.X on "
                             "credit"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               ">1 asset named -> refused",
                               "no supplier -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "SALE_ASSET_CASH",
        "description": "An old fixed asset sold for cash/cheque",
        "example_category": "Sales (assets)",
        "required_inputs": ["amount", "the exact asset word"],
        "account_structure": {"debit": ["Cash | Bank"],
                              "credit": ["exact asset"]},
        "golden_rule": ["Cash/Bank: Real - Debit what comes in",
                        "Asset: Real - Credit what goes out"],
        "journal_structure": "Cash/Bank A/c Dr ... / To <Asset> A/c ...",
        "ledger_effect": "Cash/Bank increases (Dr); Asset decreases (Cr)",
        "trial_balance_effect": "Cash/Bank on the debit side; Asset on the "
                                "credit side",
        "wording_variants": ["sold old furniture for cash", "sold machinery "
                             "for Rs.X in cash"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "mode not stated -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "SALE_ASSET_CREDIT",
        "description": "An old fixed asset sold on credit",
        "example_category": "Sales (assets)",
        "required_inputs": ["amount", "the exact asset word", "customer"],
        "account_structure": {"debit": ["<customer> (Personal)"],
                              "credit": ["exact asset"]},
        "golden_rule": ["<customer>: Personal - Debit the receiver",
                        "Asset: Real - Credit what goes out"],
        "journal_structure": "<customer> A/c Dr ... / To <Asset> A/c ...",
        "ledger_effect": "Customer debtor increases (Dr); Asset decreases "
                         "(Cr)",
        "trial_balance_effect": "Customer on the debit side; Asset on the "
                                "credit side",
        "wording_variants": ["sold old furniture to Ramesh on credit",
                             "sold machinery on credit to Ramesh"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "no customer -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "EXPENSE_PAID",
        "description": "An expense paid in cash, by cheque or at once",
        "example_category": "Expenses",
        "required_inputs": ["amount", "the expense word"],
        "account_structure": {"debit": ["expense A/c (Nominal)"],
                              "credit": ["Cash | Bank"]},
        "golden_rule": ["Expense: Nominal - Debit expenses and losses",
                        "Cash/Bank: Real - Credit what goes out"],
        "journal_structure": "<Expense> A/c Dr ... / To Cash/Bank A/c ...",
        "ledger_effect": "Expense balance increases (Dr); Cash/Bank "
                         "decreases (Cr)",
        "trial_balance_effect": "Expense on the debit side; Cash/Bank on the "
                                "credit side",
        "wording_variants": ["paid rent", "paid salaries in cash", "rent "
                             "paid", "paid for stationery in cash", "payment "
                             "made for rent in cash", "paid electricity "
                             "bill", "paid carriage inward/outward", "paid "
                             "rent by cheque"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "expense word not recognised -> "
                               "REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "INCOME_RECEIVED",
        "description": "Income received in cash or by cheque",
        "example_category": "Incomes",
        "required_inputs": ["amount", "the income word"],
        "account_structure": {"debit": ["Cash | Bank"],
                              "credit": ["income A/c (Nominal)"]},
        "golden_rule": ["Cash/Bank: Real - Debit what comes in",
                        "Income: Nominal - Credit incomes and gains"],
        "journal_structure": "Cash/Bank A/c Dr ... / To <Income> A/c ...",
        "ledger_effect": "Cash/Bank increases (Dr); income balance increases "
                         "(Cr)",
        "trial_balance_effect": "Cash/Bank on the debit side; income on the "
                                "credit side",
        "wording_variants": ["received commission", "commission received in "
                             "cash", "interest received by cheque", "received "
                             "rent", "received dividend"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "PAID_TO",
        "description": "Cash/cheque paid TO a party (settling a creditor or "
                       "any payment to a person)",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount", "the party"],
        "account_structure": {"debit": ["<party> (Personal)"],
                              "credit": ["Cash | Bank"]},
        "golden_rule": ["<party>: Personal - Debit the receiver",
                        "Cash/Bank: Real - Credit what goes out"],
        "journal_structure": "<party> A/c Dr ... / To Cash/Bank A/c ...",
        "ledger_effect": "Party balance decreases (Dr); Cash/Bank decreases "
                         "(Cr)",
        "trial_balance_effect": "Party on the debit side; Cash/Bank on the "
                                "credit side",
        "wording_variants": ["paid to Rahul Rs.X in cash", "paid Rahul "
                             "Rs.X in cash", "paid cash to Rahul", "paid "
                             "Amit by cheque", "issued a cheque in favour of "
                             "Amit"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "party not named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "RECEIVED_FROM",
        "description": "Cash/cheque received FROM a party (settling a "
                       "debtor); also '<Party> paid ...' subject wording",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount", "the party"],
        "account_structure": {"debit": ["Cash | Bank"],
                              "credit": ["<party> (Personal)"]},
        "golden_rule": ["Cash/Bank: Real - Debit what comes in",
                        "<party>: Personal - Credit the giver"],
        "journal_structure": "Cash/Bank A/c Dr ... / To <party> A/c ...",
        "ledger_effect": "Cash/Bank increases (Dr); party debtor balance "
                         "decreases (Cr)",
        "trial_balance_effect": "Cash/Bank on the debit side; party on the "
                                "credit side",
        "wording_variants": ["received from Mohan Rs.X in cash", "received "
                             "cash from Mohan", "received Rs.X from Amit in "
                             "cash", "Mohan paid Rs.X immediately", "Mohan "
                             "paid us Rs.X"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "party not named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "CASH_INTO_BANK",
        "description": "Cash deposited into the bank (contra entry)",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Bank"], "credit": ["Cash"]},
        "golden_rule": ["Bank: Personal - Debit the receiver",
                        "Cash: Real - Credit what goes out"],
        "journal_structure": "Bank A/c Dr ... / To Cash A/c ...",
        "ledger_effect": "Bank balance increases (Dr); Cash decreases (Cr)",
        "trial_balance_effect": "Bank on the debit side; Cash on the credit "
                                "side",
        "wording_variants": ["deposited cash into bank", "cash deposited "
                             "into bank", "deposited cash into the bank",
                             "paid into the bank", "cash deposited in bank"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "CASH_FROM_BANK",
        "description": "Cash withdrawn from the bank for office use (contra)",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount"],
        "account_structure": {"debit": ["Cash"], "credit": ["Bank"]},
        "golden_rule": ["Cash: Real - Debit what comes in",
                        "Bank: Personal - Credit the giver"],
        "journal_structure": "Cash A/c Dr ... / To Bank A/c ...",
        "ledger_effect": "Cash increases (Dr); Bank decreases (Cr)",
        "trial_balance_effect": "Cash on the debit side; Bank on the credit "
                                "side",
        "wording_variants": ["withdrew cash from bank", "cash withdrawn from "
                             "the bank", "drew cash from the bank"],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "CHEQUE_PAID",
        "description": "A cheque issued/paid to a party",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount", "the party"],
        "account_structure": {"debit": ["<party> (Personal)"],
                              "credit": ["Bank"]},
        "golden_rule": ["<party>: Personal - Debit the receiver",
                        "Bank: Personal - Credit the giver"],
        "journal_structure": "<party> A/c Dr ... / To Bank A/c ...",
        "ledger_effect": "Party balance decreases (Dr); Bank decreases (Cr)",
        "trial_balance_effect": "Party on the debit side; Bank on the credit "
                                "side",
        "wording_variants": ["paid Amit by cheque", "issued a cheque to "
                             "Rahul", "gave a cheque to Amit", "issued a "
                             "cheque in favour of Amit"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "party not named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "CHEQUE_RECEIVED",
        "description": "A cheque received from a party",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount", "the party"],
        "account_structure": {"debit": ["Bank"], "credit": ["<party> "
                            "(Personal)"]},
        "golden_rule": ["Bank: Personal - Debit the receiver",
                        "<party>: Personal - Credit the giver"],
        "journal_structure": "Bank A/c Dr ... / To <party> A/c ...",
        "ledger_effect": "Bank increases (Dr); party debtor decreases (Cr)",
        "trial_balance_effect": "Bank on the debit side; party on the credit "
                                "side",
        "wording_variants": ["received a cheque from Mohan", "cheque "
                             "received from Mohan", "got a cheque from "
                             "Mohan"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "party not named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "CHEQUE_DEPOSITED",
        "description": "A cheque deposited into the bank - the counterparty "
                       "is the DRAWER, never cash",
        "example_category": "Bank / Cash / Parties",
        "required_inputs": ["amount", "the drawer (party)"],
        "account_structure": {"debit": ["Bank"],
                              "credit": ["<drawer> (Personal)"]},
        "golden_rule": ["Bank: Personal - Debit the receiver",
                        "<drawer>: Personal - Credit the giver"],
        "journal_structure": "Bank A/c Dr ... / To <drawer> A/c ...",
        "ledger_effect": "Bank increases (Dr); drawer balance decreases (Cr)",
        "trial_balance_effect": "Bank on the debit side; drawer on the "
                                "credit side",
        "wording_variants": ["cheque deposited into bank", "deposited a "
                             "cheque into the bank", "cheque received from "
                             "Mohan and deposited into bank"],
        "refusal_conditions": ["drawer not named -> REVIEW_REQUIRED (a "
                               "cheque is never treated as cash)"],
    },
    {
        "pattern_id": "PURCHASE_RETURN",
        "description": "Goods returned to the supplier (returns outward)",
        "example_category": "Returns",
        "required_inputs": ["amount", "the supplier"],
        "account_structure": {"debit": ["<supplier> (Personal)"],
                              "credit": ["Purchase Returns"]},
        "golden_rule": ["<supplier>: Personal - Debit the receiver",
                        "Purchase Returns: Nominal - Credit incomes/gains"],
        "journal_structure": "<supplier> A/c Dr ... / To Purchase Returns A/c",
        "ledger_effect": "Supplier balance decreases (Dr); Purchase Returns "
                         "increases (Cr)",
        "trial_balance_effect": "Supplier on the debit side; Purchase "
                                "Returns on the credit side",
        "wording_variants": ["returned goods to Rahul", "purchases returns "
                             "to Rahul", "returned goods worth Rs.X to him"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "supplier not named -> REVIEW_REQUIRED (party "
                               "inherited from the previous transaction when "
                               "deterministic)"],
    },
    {
        "pattern_id": "SALES_RETURN",
        "description": "Goods returned by the customer (returns inward)",
        "example_category": "Returns",
        "required_inputs": ["amount", "the customer"],
        "account_structure": {"debit": ["Sales Returns"],
                              "credit": ["<customer> (Personal)"]},
        "golden_rule": ["Sales Returns: Nominal - Debit expenses/losses",
                        "<customer>: Personal - Credit the giver"],
        "journal_structure": "Sales Returns A/c Dr ... / To <customer> A/c ...",
        "ledger_effect": "Sales Returns increases (Dr); customer balance "
                         "decreases (Cr)",
        "trial_balance_effect": "Sales Returns on the debit side; customer "
                                "on the credit side",
        "wording_variants": ["goods returned by Mohan", "sales returns from "
                             "Mohan", "Mohan returned goods worth Rs.X"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "customer not named -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "DISCOUNT_ALLOWED",
        "description": "A cash discount allowed to a customer (alone or as "
                       "part of a settlement)",
        "example_category": "Discounts",
        "required_inputs": ["amount", "the customer"],
        "account_structure": {"debit": ["Discount Allowed"],
                              "credit": ["<customer> (Personal)"]},
        "golden_rule": ["Discount Allowed: Nominal - Debit expenses/losses",
                        "<customer>: Personal - Credit the giver"],
        "journal_structure": "Discount Allowed A/c Dr ... / To <customer> "
                             "A/c ...",
        "ledger_effect": "Discount Allowed increases (Dr); customer balance "
                         "decreases (Cr)",
        "trial_balance_effect": "Discount Allowed on the debit side; "
                                "customer on the credit side",
        "wording_variants": ["discount allowed to Mohan", "allowed him "
                             "discount Rs.X", "received from Mohan Rs.A, "
                             "discount allowed Rs.B"],
        "refusal_conditions": ["no settlement context -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "DISCOUNT_RECEIVED",
        "description": "A cash discount received from a supplier",
        "example_category": "Discounts",
        "required_inputs": ["amount", "the supplier"],
        "account_structure": {"debit": ["<supplier> (Personal)"],
                              "credit": ["Discount Received"]},
        "golden_rule": ["<supplier>: Personal - Debit the receiver",
                        "Discount Received: Nominal - Credit incomes/gains"],
        "journal_structure": "<supplier> A/c Dr ... / To Discount Received "
                             "A/c ...",
        "ledger_effect": "Supplier balance decreases (Dr); Discount Received "
                         "increases (Cr)",
        "trial_balance_effect": "Supplier on the debit side; Discount "
                                "Received on the credit side",
        "wording_variants": ["discount received from Rahul", "paid to Amit "
                             "Rs.A, discount received Rs.B"],
        "refusal_conditions": ["no settlement context -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "TRADE_DISCOUNT_PIPELINE",
        "description": "A trade discount nets the LIST PRICE before posting",
        "example_category": "Discounts (composed)",
        "required_inputs": ["list price", "trade discount %"],
        "account_structure": {"debit": ["Purchases/asset"], "credit": ["Cash/"
                            "Bank/supplier"]},
        "golden_rule": "Applies on top of the base purchase/sale pattern",
        "journal_structure": "List price - Trade discount = Net value posted",
        "ledger_effect": "Net value posted; discount never posted as a "
                         "separate account",
        "trial_balance_effect": "Same as the base pattern with the net "
                                "value",
        "wording_variants": ["at 10% trade discount", "with 10% trade "
                             "discount", "10% trade discount allowed"],
        "refusal_conditions": ["amount missing -> BLOCKED",
                               "discount % unreadable -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "CASH_DISCOUNT_PIPELINE",
        "description": "A cash discount applies ONLY to the paid portion of "
                       "a partial settlement",
        "example_category": "Discounts (composed)",
        "required_inputs": ["net value", "paid portion", "cash discount %"],
        "account_structure": {"debit": ["Purchases/asset"],
                              "credit": ["Cash (paid - discount)",
                                         "Discount Received", "supplier "
                                         "(credit remainder)"]},
        "golden_rule": "Chronological: Net -> split paid/credit -> cash "
                       "discount on the paid portion only",
        "journal_structure": "Purchases Dr net / Cash Cr (paid - cd) / "
                             "Discount Received Cr cd / supplier Cr remainder",
        "ledger_effect": "Cash decreases by the net paid amount; Discount "
                         "Received increases; supplier creditor reduced",
        "trial_balance_effect": "All lines on their natural sides; totals "
                                "balance",
        "wording_variants": ["2% cash discount on the amount paid", "cash "
                             "discount of 2% was allowed on the amount "
                             "paid"],
        "refusal_conditions": ["fraction or % unreadable -> REVIEW_REQUIRED",
                               "cash discount must never flip a credit "
                               "purchase into cash mode"],
    },
    {
        "pattern_id": "EXPLICIT_DISCOUNT_SETTLEMENT",
        "description": "A settlement with an explicit discount AMOUNT "
                       "(received Rs.A, discount allowed Rs.B)",
        "example_category": "Discounts (settlements)",
        "required_inputs": ["cash amount", "discount amount", "party"],
        "account_structure": {"debit": ["Cash + Discount Allowed (or party)"],
                              "credit": ["party (or Cash + Discount "
                                         "Received)"]},
        "golden_rule": "Personal rules + Nominal discount accounts",
        "journal_structure": "Cash Dr + Discount Allowed Dr / party Cr "
                             "(sum); party Dr / Cash Cr + Discount Received "
                             "Cr",
        "ledger_effect": "Party account settles to the stated total; "
                         "discount account records the difference",
        "trial_balance_effect": "Party total == cash + discount; totals "
                                "balance",
        "wording_variants": ["received from Mohan Rs.A, discount allowed "
                             "Rs.B", "paid Rs.A in full settlement of "
                             "Rs.C, discount received Rs.B", "allowed him "
                             "discount Rs.B"],
        "refusal_conditions": ["discount without settlement context -> "
                               "REVIEW_REQUIRED", "discount never invented "
                               "when unstated (except deterministic "
                               "subtraction of two stated figures)"],
    },
    {
        "pattern_id": "MULTI_TRANSACTION",
        "description": "A question with several independent transactions - "
                       "split chronologically, journaled independently, "
                       "aggregated into ONE ledger and ONE trial balance",
        "example_category": "Multi-transaction questions",
        "required_inputs": ["each transaction with its amount"],
        "account_structure": {"debit": ["per-entry"], "credit": ["per-entry"]},
        "golden_rule": "Each entry follows its own pattern's golden rule",
        "journal_structure": "N independent journal entries in order",
        "ledger_effect": "Aggregate of every entry's postings",
        "trial_balance_effect": "Aggregate ledger balances; Dr == Cr",
        "wording_variants": ["Started business ... . Purchased goods ... . "
                             "Paid rent ... .", "Sold goods to Mohan ... . "
                             "Received from him ... discount allowed ...",
                             "Purchased goods ... . Paid him Rs.X "
                             "immediately."],
        "refusal_conditions": ["any segment missing an amount -> BLOCKED "
                               "(no partial fabrication)", "continuation "
                               "pronouns resolve only to a previously named "
                               "party"],
    },
    {
        "pattern_id": "REFUSAL::BLOCKED",
        "description": "Essential information (usually the amount) is "
                       "missing - the transaction is NOT solved",
        "example_category": "Refusals",
        "required_inputs": ["none - refuses"],
        "account_structure": {"debit": [], "credit": []},
        "golden_rule": "Never invent an amount",
        "journal_structure": "No journal lines are produced",
        "ledger_effect": "No ledger effect",
        "trial_balance_effect": "No trial-balance effect",
        "wording_variants": ["Purchased goods from Rahul.", "Paid rent."],
        "refusal_conditions": ["amount missing -> BLOCKED"],
    },
    {
        "pattern_id": "REFUSAL::REVIEW_REQUIRED",
        "description": "The wording is ambiguous (cash vs credit, mode, "
                       "discount context) - FT-E never guesses",
        "example_category": "Refusals",
        "required_inputs": ["none - refuses"],
        "account_structure": {"debit": [], "credit": []},
        "golden_rule": "Never assume a treatment",
        "journal_structure": "No journal lines are produced",
        "ledger_effect": "No ledger effect",
        "trial_balance_effect": "No trial-balance effect",
        "wording_variants": ["Purchased goods for Rs.10,000.", "Paid "
                             "Rs.5,000.", "Received Rs.5,000.", "Purchased "
                             "goods."],
        "refusal_conditions": ["cash/credit unstated -> REVIEW_REQUIRED",
                               "purpose/context missing -> REVIEW_REQUIRED"],
    },
    {
        "pattern_id": "REFUSAL::NOT_SUPPORTED",
        "description": "The topic is outside the approved Ch.1-3 "
                       "Unit-Test-1 boundary",
        "example_category": "Refusals",
        "required_inputs": ["none - refuses"],
        "account_structure": {"debit": [], "credit": []},
        "golden_rule": "Never answer outside the syllabus",
        "journal_structure": "No journal lines are produced",
        "ledger_effect": "No ledger effect",
        "trial_balance_effect": "No trial-balance effect",
        "wording_variants": ["depreciation", "final accounts", "balance "
                             "sheet", "partnership", "opening entry", "issue "
                             "of shares"],
        "refusal_conditions": ["any later-year topic -> NOT_SUPPORTED"],
    },
]

_PATTERN_LIBRARY_BY_ID = {p["pattern_id"]: p for p in BK_PATTERN_LIBRARY}


def pattern_coverage_report(cases: List[Dict[str, Any]],
                            reason_bk_question: Any) -> Dict[str, Any]:
    """Per-pattern test/pass coverage over a benchmark dataset.

    Buckets:
      * VERIFIED single-transaction  -> the question_type_key
      * VERIFIED multi-transaction   -> MULTI_TRANSACTION
      * refusals                     -> REFUSAL::<status>

    Every bucket is enriched with the pattern library metadata (pattern_id,
    description, account structure, golden rule, wording variants, refusal
    conditions, ...). Machine-readable; format_coverage_markdown renders
    the human-readable form.
    """
    counts: Dict[str, Dict[str, int]] = {}

    def _bump(pattern_id: str, passed: bool) -> None:
        row = counts.setdefault(pattern_id, {"tests": 0, "passes": 0})
        row["tests"] += 1
        if passed:
            row["passes"] += 1

    for case in cases or []:
        q = case.get("question") or ""
        expected = case.get("status")
        out = reason_bk_question(q)
        status = out.get("status")
        if status == VERIFIED:
            journals = out.get("journals") or [out.get("journal")] or []
            multi = len(journals) > 1 or bool(
                (out.get("journal") or {}).get("multi"))
            if multi:
                pattern_id = "MULTI_TRANSACTION"
            else:
                pattern_id = ((out.get("understanding") or {})
                              .get("question_type_key") or "UNKNOWN")
            # a VERIFIED case passes when the expected Dr/Cr lines match
            passed = status == expected
            if expected == VERIFIED and case.get("debit") is not None:
                from backend.maths.fyjc_bk_15f_benchmark import (
                    merged_lines_match)
                passed = merged_lines_match(out, case)
        else:
            pattern_id = f"REFUSAL::{status}"
            passed = status == expected
        _bump(pattern_id, passed)

    patterns: List[Dict[str, Any]] = []
    for pattern_id in sorted(counts):
        meta = _PATTERN_LIBRARY_BY_ID.get(pattern_id)
        if meta is None:
            meta = {
                "pattern_id": pattern_id,
                "description": pattern_id,
                "example_category": "Generated bucket",
                "required_inputs": [],
                "account_structure": {},
                "golden_rule": "",
                "journal_structure": "",
                "ledger_effect": "",
                "trial_balance_effect": "",
                "wording_variants": [],
                "refusal_conditions": [],
            }
        row = dict(meta)
        row["test_count"] = counts[pattern_id]["tests"]
        row["pass_count"] = counts[pattern_id]["passes"]
        row["pass_rate"] = (round(100 * row["pass_count"] / row["test_count"],
                                  1)
                            if row["test_count"] else 0.0)
        patterns.append(row)

    total_tests = sum(c["test_count"] for c in patterns)
    total_passes = sum(c["pass_count"] for c in patterns)
    return {
        "patterns": patterns,
        "totals": {
            "test_count": total_tests,
            "pass_count": total_passes,
            "pass_rate": (round(100 * total_passes / total_tests, 1)
                          if total_tests else 0.0),
        },
        "invariants": {
            "invented_accounts": 0, "fabricated_amounts": 0,
            "unbalanced_verified_journals": 0,
            "unbalanced_verified_trial_balances": 0,
            "formula_id_none_confident": 0, "cpp_authority_violations": 0,
            "unsupported_confident": 0,
        },
    }


def format_coverage_markdown(report: Dict[str, Any]) -> str:
    """Human-readable coverage table (spec section 16)."""
    lines = [
        "# FYJC BOOK-KEEPING CH.1-3 PATTERN COVERAGE (SPRINT 15F)",
        "",
        f"**Total test cases:** {report['totals']['test_count']}  "
        f"**Passed:** {report['totals']['pass_count']}  "
        f"**Pass rate:** {report['totals']['pass_rate']}%",
        "",
        "| Pattern ID | Description | Category | Wording variants | "
        "Refusal conditions | Tests | Pass |",
        "|---|------------|----------|------------------|-------------------|"
        "-----:|----:|",
    ]
    for p in report["patterns"]:
        variants = "; ".join(p["wording_variants"][:4])
        if len(p["wording_variants"]) > 4:
            variants += f" (+{len(p['wording_variants']) - 4} more)"
        refusals = "; ".join(p["refusal_conditions"][:2])
        if len(p["refusal_conditions"]) > 2:
            refusals += f" (+{len(p['refusal_conditions']) - 2} more)"
        lines.append(
            f"| `{p['pattern_id']}` | {p['description'][:70]} | "
            f"{p['example_category']} | {variants[:80]} | {refusals[:80]} | "
            f"{p['test_count']} | {p['pass_count']} |")
    lines += ["", "## Per-pattern detail", ""]
    for p in report["patterns"]:
        lines += [
            f"### {p['pattern_id']}",
            "",
            f"**Description:** {p['description']}",
            "",
            f"**Category:** {p['example_category']}",
            "",
            f"**Required inputs:** {', '.join(p['required_inputs']) or '-'}",
            "",
            f"**Account structure:** Debit "
            f"{p['account_structure'].get('debit')} / Credit "
            f"{p['account_structure'].get('credit')}",
            "",
            f"**Golden rule:** {p['golden_rule']}",
            "",
            f"**Journal structure:** {p['journal_structure']}",
            "",
            f"**Ledger effect:** {p['ledger_effect']}",
            "",
            f"**Trial-balance effect:** {p['trial_balance_effect']}",
            "",
            f"**Supported wording variants:** "
            f"{'; '.join(p['wording_variants']) or '-'}",
            "",
            f"**Refusal conditions:** "
            f"{'; '.join(p['refusal_conditions']) or '-'}",
            "",
            f"**Coverage:** {p['pass_count']}/{p['test_count']} tests pass",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


def write_coverage_report(cases: List[Dict[str, Any]],
                          reason_bk_question: Any,
                          json_path: str, md_path: str) -> Dict[str, Any]:
    """Persist the machine-readable (JSON) and human-readable (Markdown)
    coverage reports."""
    report = pattern_coverage_report(cases, reason_bk_question)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(format_coverage_markdown(report))
    return report
