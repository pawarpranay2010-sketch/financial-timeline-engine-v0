"""
Financial Timeline Engine
Sprint 13 - FYJC Book-Keeping & Accountancy Readiness
backend/maths/fyjc_accounting.py

A deterministic verification layer for FYJC Book-Keeping & Accountancy:

    Transaction / Student's Journal Entry
      -> debit/credit identification (golden rules, modern approach)
      -> journal entry verification (arithmetic + direction)
      -> ledger posting + balance verification
      -> trial balance + discrepancy identification
      -> accounting calculations (Gross Profit, Working Capital, ...)
         computed by the C++ mathematical authority

Rules
-----
* The golden rules applied here are the standard FYJC conventions:
  - Assets & Expenses increase on the Debit side.
  - Liabilities, Capital & Income increase on the Credit side.
  - Personal accounts: debit the receiver, credit the giver.
* FT-E NEVER invents accounting rules, never infers an ambiguous
  transaction, and never guesses an amount. When the description does not
  determine the treatment, the outcome is REVIEW_REQUIRED with a
  student-readable explanation; when essential information is missing, it
  is BLOCKED with the exact next step.
* Registered financial formulas (Gross Profit, Working Capital, Profit,
  margins, ratios ...) are computed ONLY through the C++ mathematical
  authority (see fyjc_maths.solve_strict). Python performs no fallback
  calculation for those. The pure bookkeeping arithmetic in this module
  (journal totals, ledger balances, trial-balance totals) is VERIFICATION
  arithmetic: it checks the numbers the student posted - it never
  calculates a financial result for them and never fabricates values.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_maths import verify_maths_answer
from backend.maths.normalization import parse_numeric_text
from backend.maths.status import (
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
    STATUS_LABELS,
)
from backend.maths.student_sandbox import STATUS_WORDS

# ---------------------------------------------------------------------------
# Account chart (canonical FYJC accounts) and roles - modern approach.
# Role -> side rule: assets & expenses (+ contra of capital/liability/
# income) debit; liabilities, capital & income (+ contra of asset/expense)
# credit. Never modified at runtime.
# ---------------------------------------------------------------------------

ASSET = "asset"
EXPENSE = "expense"
LIABILITY = "liability"
CAPITAL = "capital"
INCOME = "income"
CONTRA_CAPITAL = "contra_capital"       # e.g. Drawings (debited)
CONTRA_INCOME = "contra_income"         # e.g. Sales Returns (debited)
CONTRA_EXPENSE = "contra_expense"       # e.g. Purchase Returns (credited)
CONTRA_ASSET = "contra_asset"           # e.g. Provision for Depreciation

ACCOUNT_ROLES: Dict[str, str] = {
    # assets
    "Cash": ASSET, "Bank": ASSET, "Debtors": ASSET, "Stock": ASSET,
    "Inventory": ASSET, "Machinery": ASSET, "Furniture": ASSET,
    "Building": ASSET, "Land": ASSET, "Vehicle": ASSET, "Equipment": ASSET,
    "Investments": ASSET, "Prepaid Expenses": ASSET, "Bills Receivable": ASSET,
    "Goodwill": ASSET, "Patents": ASSET, "Office Equipment": ASSET,
    # expenses
    "Purchases": EXPENSE, "Rent": EXPENSE, "Salaries": EXPENSE,
    "Wages": EXPENSE, "Insurance": EXPENSE, "Advertisement": EXPENSE,
    "Electricity": EXPENSE, "Office Expenses": EXPENSE,
    "General Expenses": EXPENSE, "Commission Paid": EXPENSE,
    "Interest Paid": EXPENSE, "Carriage Inward": EXPENSE,
    "Carriage Outward": EXPENSE, "Discount Allowed": EXPENSE,
    "Bad Debts": EXPENSE, "Interest on Capital": EXPENSE, "Repairs": EXPENSE,
    "Postage": EXPENSE, "Stationery": EXPENSE, "Audit Fees": EXPENSE,
    "Legal Fees": EXPENSE, "Fuel": EXPENSE, "Rent Paid": EXPENSE,
    "Loss on Sale of Asset": EXPENSE, "Income Tax": EXPENSE,
    # Sprint 15I-J vocabulary expansion: unambiguous FYJC expense accounts
    # that student wording commonly names with synonyms.
    "Conveyance": EXPENSE, "Printing": EXPENSE,
    "Telephone Expenses": EXPENSE,
    # liabilities
    "Creditors": LIABILITY, "Loan": LIABILITY, "Bank Loan": LIABILITY,
    "Bills Payable": LIABILITY, "Outstanding Expenses": LIABILITY,
    "Unearned Income": LIABILITY, "Bank Overdraft": LIABILITY,
    # Sprint 15I-K GST accounts: input tax credit is an asset (receivable
    # from the government), output tax is a liability (payable to it).
    "Input CGST": ASSET, "Input SGST": ASSET, "Input IGST": ASSET,
    "Output CGST": LIABILITY, "Output SGST": LIABILITY,
    "Output IGST": LIABILITY,
    # capital
    "Capital": CAPITAL,
    # income
    "Sales": INCOME, "Commission Received": INCOME, "Interest Received": INCOME,
    "Discount Received": INCOME, "Bad Debts Recovered": INCOME,
    "Interest on Drawings": INCOME, "Rent Received": INCOME,
    "Profit on Sale of Asset": INCOME, "Dividend Received": INCOME,
    # contra accounts
    "Drawings": CONTRA_CAPITAL,
    "Sales Returns": CONTRA_INCOME, "Returns Inward": CONTRA_INCOME,
    "Purchase Returns": CONTRA_EXPENSE, "Returns Outward": CONTRA_EXPENSE,
    "Provision for Depreciation": CONTRA_ASSET,
}

ACCOUNT_ALIASES: Dict[str, str] = {
    "cash in hand": "Cash", "cash": "Cash",
    "bank account": "Bank", "bank": "Bank", "cheque": "Bank",
    "accounts receivable": "Debtors", "debtors": "Debtors",
    "sundry debtors": "Debtors", "receivable": "Debtors",
    "accounts payable": "Creditors", "creditors": "Creditors",
    "sundry creditors": "Creditors", "payable": "Creditors",
    "stock": "Stock", "inventory": "Stock", "closing stock": "Stock",
    "machinery": "Machinery", "plant and machinery": "Machinery",
    "furniture": "Furniture", "fixtures": "Furniture",
    "building": "Building", "buildings": "Building", "land": "Land",
    "vehicle": "Vehicle", "vehicles": "Vehicle",
    "rent": "Rent", "rent paid": "Rent",
    "salary": "Salaries", "salaries": "Salaries", "wages": "Wages",
    "insurance": "Insurance", "insurance premium": "Insurance",
    "advertisement": "Advertisement", "advertising": "Advertisement",
    "advertisement expenses": "Advertisement",
    "electricity": "Electricity", "electricity charges": "Electricity",
    "commission paid": "Commission Paid",
    "interest paid": "Interest Paid", "interest expense": "Interest Paid",
    "carriage": "Carriage Inward", "carriage inward": "Carriage Inward",
    "carriage on purchases": "Carriage Inward",
    "carriage outward": "Carriage Outward",
    "carriage on sales": "Carriage Outward",
    "discount allowed": "Discount Allowed",
    "bad debts": "Bad Debts", "bad debts written off": "Bad Debts",
    "purchases": "Purchases", "purchase": "Purchases",
    "sales": "Sales", "sale": "Sales",
    "capital": "Capital", "drawings": "Drawings",
    "sales returns": "Sales Returns", "returns inward": "Sales Returns",
    "purchase returns": "Purchase Returns", "returns outward": "Purchase Returns",
    "commission received": "Commission Received",
    "interest received": "Interest Received", "interest income": "Interest Received",
    # Sprint 15I-J synonym layer - each alias has ONE explicit accounting
    # meaning and is pinned by the 15J coverage matrix.
    "conveyance": "Conveyance", "conveyance expenses": "Conveyance",
    "conveyance charges": "Conveyance", "transport": "Conveyance",
    "transportation": "Conveyance", "transport charges": "Conveyance",
    "transport expenses": "Conveyance", "travelling": "Conveyance",
    "travel": "Conveyance", "travelling expenses": "Conveyance",
    "travel expenses": "Conveyance",
    "printing": "Printing", "printing charges": "Printing",
    "printing expenses": "Printing",
    "telephone": "Telephone Expenses", "telephone bill": "Telephone Expenses",
    "telephone expenses": "Telephone Expenses",
    "telephone charges": "Telephone Expenses", "phone": "Telephone Expenses",
    "phone bill": "Telephone Expenses", "mobile": "Telephone Expenses",
    "mobile bill": "Telephone Expenses", "mobile charges": "Telephone Expenses",
    "discount received": "Discount Received",
    "bad debts recovered": "Bad Debts Recovered",
    "interest on drawings": "Interest on Drawings",
    "interest on capital": "Interest on Capital",
    "loan": "Loan", "bank loan": "Bank Loan",
    "loan from bank": "Bank Loan", "loan from": "Loan",
    "rent received": "Rent Received", "rent income": "Rent Received",
    "profit on sale of asset": "Profit on Sale of Asset",
    "profit on sale": "Profit on Sale of Asset",
    "loss on sale of asset": "Loss on Sale of Asset",
    "office expenses": "Office Expenses",
    "general expenses": "General Expenses",
    "repairs": "Repairs", "postage": "Postage", "stationery": "Stationery",
    "audit fees": "Audit Fees", "legal fees": "Legal Fees",
    "fuel": "Fuel", "income tax": "Income Tax",
    # Sprint 15I-K: GST account aliases - only the full input/output forms
    # resolve; a bare 'CGST'/'SGST'/'IGST' is ambiguous between input and
    # output and is never silently mapped.
    "input cgst": "Input CGST", "input sgst": "Input SGST",
    "input igst": "Input IGST", "output cgst": "Output CGST",
    "output sgst": "Output SGST", "output igst": "Output IGST",
}

SIDE_EXPLANATION = {
    ASSET: "Assets increase on the Debit side.",
    EXPENSE: "Expenses increase on the Debit side.",
    LIABILITY: "Liabilities increase on the Credit side.",
    CAPITAL: "Capital increases on the Credit side.",
    INCOME: "Income increases on the Credit side.",
    CONTRA_CAPITAL: "Drawings reduce capital and are debited.",
    CONTRA_INCOME: "Returns Inward reduce sales and are debited.",
    CONTRA_EXPENSE: "Returns Outward reduce purchases and are credited.",
    CONTRA_ASSET: "Contra-assets reduce assets and are credited.",
}


def canonical_account(name: Any) -> Optional[str]:
    """Resolve an account name (aliases tolerated) to the canonical chart
    account, or None when it cannot be identified."""
    if name is None:
        return None
    key = " ".join(str(name).strip().lower().split())
    direct = ACCOUNT_ALIASES.get(key)
    if direct is not None:
        return direct
    # Title-case lookup against the chart itself.
    if key in {a.lower() for a in ACCOUNT_ROLES}:
        for account in ACCOUNT_ROLES:
            if account.lower() == key:
                return account
    return None


def account_role(account: str) -> Optional[str]:
    return ACCOUNT_ROLES.get(account)


def side_for_role(role: str) -> str:
    """Debit-increase roles vs credit-increase roles (modern approach)."""
    if role in (ASSET, EXPENSE, CONTRA_CAPITAL, CONTRA_INCOME):
        return "debit"
    if role in (LIABILITY, CAPITAL, INCOME, CONTRA_EXPENSE, CONTRA_ASSET):
        return "credit"
    return "debit"  # unreachable for chart accounts; defensive


# ---------------------------------------------------------------------------
# Amount extraction (deterministic; never guesses)
# ---------------------------------------------------------------------------

# A decimal point is only a decimal point when at least one digit
# follows it; a trailing '.' is sentence punctuation, not part of the
# amount (e.g. "Rs.50,000."). parse_numeric_text stays fail-closed -
# the tokenizer simply does not hand it a false decimal marker.
_NUMBER_TOKEN = re.compile(
    r"(?:₹|Rs\.?|INR|Rs)?\s*\(?\s*-?\s*\d[\d,]*(?:\.\d+)?\s*\)?"
)

_CURRENCY_PREFIX = re.compile(r"^(?:₹|Rs\.?|INR)\s*")


def _extract_amounts(text: str) -> Tuple[List[Decimal], bool]:
    """Extract every number-like token from a description. Returns
    (amounts, ambiguous): ambiguous is True when any token could not be
    parsed cleanly (OCR-style uncertainty is never silently corrected).
    Currency decorations (₹ / Rs. / INR) are stripped from the token
    before the deterministic normalizer parses it - the value itself is
    never guessed."""
    amounts: List[Decimal] = []
    ambiguous = False
    for match in _NUMBER_TOKEN.finditer(str(text)):
        token = match.group(0).strip()
        if not token or not re.search(r"\d", token):
            continue
        token = _CURRENCY_PREFIX.sub("", token).strip()
        parsed = parse_numeric_text(token)
        if parsed.value is None or parsed.ambiguity:
            ambiguous = True
            continue
        amounts.append(parsed.value)
    return amounts, ambiguous


# ---------------------------------------------------------------------------
# Deterministic transaction pattern engine (golden rules, modern approach)
# ---------------------------------------------------------------------------

# Each pattern: first matching rule wins (ordered, deterministic). `when`
# phrases are matched case-insensitively as substrings. `debit`/`credit`
# are canonical accounts or {"party": "receiver"/"giver"} placeholders.
# `requires_amount` controls whether a BLOCKED is returned without one.
_TRANSACTION_PATTERNS: List[Dict[str, Any]] = [
    {
        "key": "START_BUSINESS",
        "when": ("started business", "commenced business",
                 "started the business", "began business"),
        "debit": ["Cash"], "credit": ["Capital"],
        "rule": "Started business with cash: Cash (asset) increases - "
                "Debit Cash; Capital (capital) increases - Credit Capital.",
    },
    {
        "key": "CAPITAL_INTRODUCED",
        "when": ("brought in as capital", "brought ... as capital",
                 "additional capital", "introduced capital"),
        "debit": ["Cash", "Bank"], "credit": ["Capital"],
        "rule": "Capital introduced: Cash/Bank (asset) increases - Debit; "
                "Capital (capital) increases - Credit.",
    },
    {
        "key": "DRAWINGS_CASH",
        "when": ("withdrew for personal use", "withdrawn for personal use",
                 "drawings", "for personal use", "for private use"),
        "debit": ["Drawings"], "credit": ["Cash", "Bank"],
        "rule": "Drawings: Drawings reduces capital (contra-capital) and "
                "is debited; Cash/Bank decreases - Credit.",
    },
    {
        "key": "SALE_CASH",
        "when": ("sold goods for cash", "sold for cash", "cash sale",
                 "cash sales"),
        "debit": ["Cash", "Bank"],
        "credit": ["Sales"],
        "rule": "Goods sold for cash: Cash/Bank (asset) increases - Debit; "
                "Sales (income) increases - Credit.",
    },
    {
        "key": "SALE_CREDIT",
        "when": ("sold goods to", "sold to", "credit sale", "sold on credit",
                 "on credit to", "sold goods on credit",
                 "sold goods on credit to"),
        "debit": [{"party": "receiver"}],
        "credit": ["Sales"],
        "rule": "Goods sold on credit: the buyer is debited (personal "
                "account: debit the receiver); Sales (income) increases - "
                "Credit.",
    },
    {
        "key": "PAID_TO",
        "when": ("paid to", "paid ... to", "paid cash to"),
        "debit": [{"party": "giver"}],
        "credit": ["Cash", "Bank"],
        "rule": "Payment to a creditor/person: the person is debited "
                "(personal account: debit the receiver); Cash/Bank "
                "decreases - Credit.",
    },
    {
        "key": "RECEIVED_FROM",
        "when": ("received from", "received cash from"),
        "debit": ["Cash", "Bank"],
        "credit": [{"party": "giver"}],
        "rule": "Receipt from a debtor/person: Cash/Bank (asset) increases "
                "- Debit; the person is credited (personal account: credit "
                "the giver).",
    },
    {
        "key": "PURCHASE_RETURN",
        "when": ("returned goods to", "returned ... to",
                 "goods returned to", "returned to"),
        "debit": [{"party": "giver"}],
        "credit": ["Purchase Returns"],
        "rule": "Goods returned to supplier: the supplier is debited "
                "(personal account); Purchase Returns reduces purchases "
                "(contra-expense) - Credit.",
    },
    {
        "key": "SALES_RETURN",
        "when": ("returned goods by", "goods returned by", "returned by",
                 "returns inward", "sales returns"),
        "debit": ["Sales Returns"],
        "credit": [{"party": "giver"}],
        "rule": "Goods returned by customer: Sales Returns reduces sales "
                "(contra-income) - Debit; the customer is credited "
                "(personal account).",
    },
    {
        "key": "DISCOUNT_ALLOWED",
        "when": ("discount allowed",),
        "debit": ["Discount Allowed"],
        "credit": [{"party": "giver"}],
        "rule": "Discount allowed to customer: Discount Allowed (expense) "
                "increases - Debit; the customer is credited.",
    },
    {
        "key": "DISCOUNT_RECEIVED",
        "when": ("discount received",),
        "debit": [{"party": "giver"}],
        "credit": ["Discount Received"],
        "rule": "Discount received from supplier: the supplier is debited; "
                "Discount Received (income) increases - Credit.",
    },
    {
        "key": "CASH_INTO_BANK",
        "when": ("deposited into bank", "deposited cash into bank",
                 "paid into bank", "deposited in bank"),
        "debit": ["Bank"],
        "credit": ["Cash"],
        "rule": "Cash deposited into bank: Bank (asset) increases - Debit; "
                "Cash (asset) decreases - Credit.",
    },
    {
        "key": "CASH_FROM_BANK",
        "when": ("withdrew from bank", "withdrawn from bank",
                 "drew from bank", "drawn from bank", "cash from bank"),
        "debit": ["Cash"],
        "credit": ["Bank"],
        "rule": "Cash withdrawn from bank: Cash (asset) increases - Debit; "
                "Bank (asset) decreases - Credit.",
    },
    {
        "key": "LOAN_TAKEN",
        "when": ("took loan", "took a loan", "loan from bank",
                 "loan from", "taken a loan", "raised loan"),
        "debit": ["Cash", "Bank"],
        "credit": ["Loan"],
        "rule": "Loan taken: Cash/Bank (asset) increases - Debit; Loan "
                "(liability) increases - Credit.",
    },
    {
        "key": "LOAN_REPAID",
        "when": ("repaid loan", "loan repaid", "paid ... loan",
                 "loan returned", "returned loan"),
        "debit": ["Loan"],
        "credit": ["Cash", "Bank"],
        "rule": "Loan repaid: Loan (liability) decreases - Debit; "
                "Cash/Bank (asset) decreases - Credit.",
    },
    {
        "key": "BAD_DEBTS",
        "when": ("bad debts written off", "written off as bad",
                 "wrote off", "bad debts"),
        "debit": ["Bad Debts"],
        "credit": [{"party": "giver"}],
        "rule": "Bad debts written off: Bad Debts (expense) increases - "
                "Debit; the debtor is credited.",
    },
    {
        "key": "BAD_DEBTS_RECOVERED",
        "when": ("bad debts recovered", "recovered from bad debt"),
        "debit": ["Cash", "Bank"],
        "credit": ["Bad Debts Recovered"],
        "rule": "Bad debts recovered: Cash/Bank (asset) increases - Debit; "
                "Bad Debts Recovered (income) increases - Credit.",
    },
    {
        "key": "GOODS_PERSONAL_USE",
        "when": ("goods for personal use", "goods taken for personal use",
                 "goods ... personal use", "goods used for personal"),
        "debit": ["Drawings"],
        "credit": ["Purchases"],
        "rule": "Goods taken for personal use: Drawings (contra-capital) "
                "increases - Debit; Purchases decreases - Credit.",
    },
    {
        "key": "FREE_SAMPLES",
        "when": ("free samples", "distributed as samples",
                 "goods distributed as free samples"),
        "debit": ["Advertisement"],
        "credit": ["Purchases"],
        "rule": "Goods distributed as free samples: Advertisement "
                "(expense) increases - Debit; Purchases decreases - Credit.",
    },
    {
        "key": "INTEREST_ON_CAPITAL",
        "when": ("interest on capital"),
        "debit": ["Interest on Capital"],
        "credit": ["Capital"],
        "rule": "Interest on capital allowed: Interest on Capital "
                "(expense) increases - Debit; Capital increases - Credit.",
    },
    {
        "key": "INTEREST_ON_DRAWINGS",
        "when": ("interest on drawings"),
        "debit": ["Drawings"],
        "credit": ["Interest on Drawings"],
        "rule": "Interest on drawings charged: Drawings (contra-capital) "
                "increases - Debit; Interest on Drawings (income) "
                "increases - Credit.",
    },
]


def _expense_income_rule(description: str) -> Optional[Dict[str, Any]]:
    """Paid-expense / received-income rules (single-amount patterns)."""
    low = " " + description.lower() + " "
    if "received" in low or "received from" in low:
        for phrase, account in (
            ("commission received", "Commission Received"),
            ("received commission", "Commission Received"),
            ("interest received", "Interest Received"),
            ("received interest", "Interest Received"),
            ("rent received", "Rent Received"),
            ("received rent", "Rent Received"),
            ("discount received", "Discount Received"),
            ("received discount", "Discount Received"),
        ):
            if phrase in low:
                return {
                    "key": "INCOME_RECEIVED",
                    "debit": ["Cash", "Bank"],
                    "credit": [account],
                    "rule": f"{account} (income) increases - Credit; "
                            "Cash/Bank (asset) increases - Debit.",
                }
    for phrase, account in (
        ("rent", "Rent"), ("salary", "Salaries"), ("salaries", "Salaries"),
        ("wages", "Wages"), ("insurance premium", "Insurance"),
        ("insurance", "Insurance"), ("advertisement", "Advertisement"),
        ("electricity", "Electricity"), ("office expenses", "Office Expenses"),
        ("general expenses", "General Expenses"),
        ("commission paid", "Commission Paid"),
        ("interest paid", "Interest Paid"),
        ("carriage inward", "Carriage Inward"),
        ("carriage outward", "Carriage Outward"),
        ("carriage on purchases", "Carriage Inward"),
        ("carriage on sales", "Carriage Outward"),
    ):
        if phrase in low:
            return {
                "key": "EXPENSE_PAID",
                "debit": [account],
                "credit": ["Cash", "Bank"],
                "rule": f"{account} (expense) increases - Debit; "
                        "Cash/Bank (asset) decreases - Credit.",
            }
    return None


_PURCHASE_KEYWORDS = ("purchas", "bought")


def _purchase_rule(description: str) -> Optional[Dict[str, Any]]:
    """Deterministic cash/credit disambiguation for goods purchases.

    'Purchased ...' alone is ambiguous (cash vs credit) and produces a
    refuse pattern (REVIEW_REQUIRED downstream - never assumed). 'for
    cash' / 'cash purchase' -> cash; 'on credit' / 'credit purchase' /
    'from <party>' -> credit. Asset purchases (machinery, furniture,
    building, equipment) are already matched earlier by the ordered
    pattern table (ASSET_PURCHASE_CASH).
    """
    low = " " + str(description or "").lower() + " "
    if not any(k in low for k in _PURCHASE_KEYWORDS):
        return None
    if "for cash" in low or "cash purchase" in low \
            or re.search(r"\bcash\b", low):
        return {
            "key": "PURCHASE_CASH",
            "debit": ["Purchases"],
            "credit": ["Cash", "Bank"],
            "rule": "Goods purchased for cash: Purchases (expense) "
                    "increases - Debit; Cash/Bank (asset) decreases - "
                    "Credit.",
        }
    if "on credit" in low or "credit purchase" in low \
            or re.search(r"\bfrom\b", low):
        return {
            "key": "PURCHASE_CREDIT",
            "debit": ["Purchases"],
            "credit": [{"party": "giver"}],
            "rule": "Goods purchased on credit: Purchases (expense) "
                    "increases - Debit; the seller is credited (personal "
                    "account: credit the giver).",
        }
    return {
        "key": "PURCHASE_AMBIGUOUS",
        "refuse": True,
        "rule": "Goods purchased: state whether the purchase was for cash "
                "or on credit.",
    }


# Asset-word table: description phrase -> the ONE canonical asset
# account it names. LONGEST phrase first so 'plant and machinery' wins
# over 'plant'. Only these words may produce a fixed-asset account -
# a purchase is never routed to Machinery/Building unless the question
# actually names that asset (Sprint 15B: no invented accounts).
_ASSET_WORDS: List[Tuple[str, str]] = [
    ("plant and machinery", "Machinery"),
    ("office equipment", "Office Equipment"),
    ("machinery", "Machinery"),
    ("machine", "Machinery"),
    # Sprint 15I-J common student misspellings (exact-token only).
    ("machinary", "Machinery"),
    ("furniture", "Furniture"),
    ("furnitures", "Furniture"),
    ("furnature", "Furniture"),
    ("fixtures", "Furniture"),
    ("building", "Building"),
    ("land", "Land"),
    ("vehicles", "Vehicle"),
    ("vehicle", "Vehicle"),
    ("equipment", "Equipment"),
    ("computer", "Equipment"),
]


def named_assets(description: str) -> List[str]:
    """The exact fixed-asset accounts NAMED by the description, in
    canonical chart spelling. Returns [] when no asset word is present.
    Never invents an asset the question did not name (Sprint 15B)."""
    low = " " + str(description or "").lower() + " "
    found: List[str] = []
    covered: List[Tuple[int, int]] = []
    for phrase, account in _ASSET_WORDS:
        # _ASSET_WORDS is longest-first, so a phrase that falls INSIDE a
        # span already claimed by a longer phrase ('equipment' inside
        # 'office equipment') is a sub-match, not a second asset - it must
        # not produce an invented sibling account (Sprint 15B).
        m = re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])",
                      low)
        if not m:
            continue
        if any(m.start() >= s and m.end() <= e for s, e in covered):
            continue
        if account not in found:
            found.append(account)
        covered.append((m.start(), m.end()))
    return found


def _asset_purchase_rule(description: str) -> Optional[Dict[str, Any]]:
    """Deterministic rule for fixed-asset purchases (Sprint 15B).

    'Purchased Furniture for Cash Rs.15,000' must produce EXACTLY
    Furniture A/c Dr / Cash A/c Cr - never Machinery or Building. The
    asset account comes only from the asset word actually present; more
    than one distinct asset in one sentence is ambiguous (refused).
    Cash vs credit disambiguation mirrors the goods-purchase rule.
    """
    low = " " + str(description or "").lower() + " "
    if not any(k in low for k in ("purchas", "bought")):
        return None
    assets = named_assets(description)
    if not assets:
        return None  # not an asset purchase -> goods/other rules handle it
    if len(assets) > 1:
        return {
            "key": "ASSET_PURCHASE_AMBIGUOUS",
            "refuse": True,
            "rule": (f"The description names more than one fixed asset "
                     f"({', '.join(assets)}) in one transaction. FT-E never "
                     "guesses how the amount is split between assets."),
        }
    asset = assets[0]
    if "for cash" in low or "cash purchase" in low \
            or re.search(r"\bcash\b", low):
        return {
            "key": "PURCHASE_ASSET_CASH",
            "debit": [asset], "credit": ["Cash", "Bank"],
            "rule": (f"{asset} purchased for cash: {asset} (asset) "
                     "increases - Debit; Cash/Bank (asset) decreases - "
                     "Credit."),
        }
    if "on credit" in low or "credit purchase" in low \
            or re.search(r"\bfrom\b", low):
        return {
            "key": "PURCHASE_ASSET_CREDIT",
            "debit": [asset], "credit": [{"party": "giver"}],
            "rule": (f"{asset} purchased on credit: {asset} (asset) "
                     "increases - Debit; the seller is credited (personal "
                     "account: credit the giver)."),
        }
    return {
        "key": "ASSET_PURCHASE_AMBIGUOUS",
        "refuse": True,
        "rule": (f"{asset} purchased: state whether the purchase was for "
                 "cash or on credit."),
    }


def _asset_sale_rule(description: str) -> Optional[Dict[str, Any]]:
    """Deterministic rule for fixed-asset sales (Sprint 15B). Only the
    asset actually named is credited - never an invented sibling asset."""
    low = " " + str(description or "").lower() + " "
    if not any(k in low for k in ("sold", "sale of")):
        return None
    assets = named_assets(description)
    if not assets:
        return None  # goods sales -> the pattern table handles them
    if len(assets) > 1:
        return {
            "key": "ASSET_SALE_AMBIGUOUS",
            "refuse": True,
            "rule": (f"The description names more than one fixed asset "
                     f"({', '.join(assets)}) in one transaction. FT-E never "
                     "guesses how the amount is split."),
        }
    asset = assets[0]
    if "for cash" in low or "cash sale" in low \
            or re.search(r"\bcash\b", low):
        return {
            "key": "SALE_ASSET_CASH",
            "debit": ["Cash", "Bank"], "credit": [asset],
            "rule": (f"{asset} sold for cash: Cash/Bank (asset) increases "
                     "- Debit; {asset} (asset) decreases - Credit."),
        }
    if "on credit" in low or "credit sale" in low \
            or re.search(r"\bto\b", low):
        return {
            "key": "SALE_ASSET_CREDIT",
            "debit": [{"party": "receiver"}], "credit": [asset],
            "rule": (f"{asset} sold on credit: the buyer is debited "
                     "(personal account: debit the receiver); {asset} "
                     "(asset) decreases - Credit."),
        }
    return {
        "key": "ASSET_SALE_AMBIGUOUS",
        "refuse": True,
        "rule": (f"{asset} sold: state whether the sale was for cash or "
                 "on credit."),
    }


def _party_from(description: str, preposition: str) -> Optional[str]:
    """Extract the counterparty name after 'from'/'to' (capitalised)."""
    low = description.lower()
    for marker in ("on credit from ", "sold goods on credit to ",
                   "on credit to ", "purchased goods from ",
                   "purchased from ", "bought goods from ", "bought from ",
                   "sold goods to ", "paid to ", "received from ",
                   "sold to ", "returned goods to ", "returned by ",
                   "goods returned by ", "received cash from ",
                   "paid cash to ", "discount allowed to ",
                   "allowed to "):
        if marker in low:
            idx = low.index(marker) + len(marker)
            rest = description[idx:]
            m = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+and\s+"
                         r"|\s+₹|\s+Rs|\s+\d|,|$)", rest)
            if m:
                party = m.group(1).strip()
                if party:
                    return party
    # Sprint 15B generic fallback: a Capitalised proper noun after
    # 'from' / 'to' / 'by' is a PERSONAL account (covers asset
    # transactions such as 'Purchased Furniture from Rahul on credit'
    # and 'Sold Machinery to Sharma for cash'). Lower-case nouns (the
    # bank, the seller) never match - FT-E never invents a party.
    for marker in (" from ", " to ", " by "):
        if marker in low:
            idx = low.index(marker) + len(marker)
            rest = description[idx:]
            m = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+and\s+"
                         r"|\s+₹|\s+Rs|\s+\d|,|$)", rest)
            if m:
                party = m.group(1).strip()
                if party and not party.lower().endswith(
                        ("a/c", "account", "ltd", "limited")):
                    return party
    return None


def _collapse_cash_bank(accounts: List[str], selected: str) -> List[str]:
    """Collapse a pattern's [Cash, Bank] either/or to the chosen side."""
    if "Cash" in accounts and "Bank" in accounts:
        return [selected]
    return accounts


def _resolve_side(entries: List[Any], cash_or_bank: List[str]) -> str:
    """Choose Cash vs Bank deterministically for a pattern that allows both:
    explicit 'bank'/'cheque' wins, otherwise Cash."""
    low = " ".join(str(e).lower() for e in entries)
    if "bank" in low or "cheque" in low or "by cheque" in low:
        return "Bank"
    return "Cash"


# ---------------------------------------------------------------------------
# Public classification entry points
# ---------------------------------------------------------------------------


def hardened_bookkeeping_outcome(description: str,
                                 amount: Any = None) -> Dict[str, Any]:
    """Route ONE bookkeeping question through the hardened FT-E engine
    (backend.maths.fyjc_bk_reasoning.reason_bk_question) - the SAME
    accounting authority the QuestionBank / PracticeEngine path uses -
    and shape the canonical result into the legacy Study/Verify outcome
    contract (debit_lines / credit_lines / rule / rule_key / status /
    status_label / why_not / next_action) that run_fyjc_accounting_flow
    and verify_journal_entry already consume.

    Sprint 15I-O: the FYJC Study / Verify flow must use the hardened
    engine as its ONLY bookkeeping authority. This adapter adds NO
    accounting rules: every account, side, amount, status and refusal
    comes verbatim from reason_bk_question(). It only translates
    presentation fields (modern role for the FYJC class display, the
    per-line golden-rule 'why' for the Debit/Credit decision step) that
    the Study / Verify UI renders.

    The lazy import keeps the module graph acyclic: fyjc_bk_reasoning
    imports this module at module scope; reason_bk_question is only
    needed at call time.
    """
    from backend.maths.fyjc_bk_reasoning import reason_bk_question

    res = reason_bk_question(description, amount)
    status = res.get("status") or REVIEW_REQUIRED

    lines: List[Dict[str, Any]] = []
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        account = line.get("account")
        if not account:
            continue
        lines.append({
            "account": account,
            "side": line.get("side"),
            "amount": line.get("amount"),
            "role": account_role(account),
            "side_hint": line.get("why"),
            "class": line.get("class"),
            # Sprint 15I-R: pass the per-line golden rule through so the
            # Study / Verify 'Why?' section can show the rule that matches
            # the account type ('Credit the giver' for a party, never
            # 'credit incomes and gains'). Presentation only.
            "rule": line.get("rule"),
        })

    rule = None
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        if line.get("rule"):
            rule = line["rule"]
            break

    return {
        "status": status,
        "status_label": (
            res.get("status_label")
            or STATUS_LABELS.get(status, status)
        ),
        "debit_lines": [line for line in lines if line["side"] == "debit"],
        "credit_lines": [line for line in lines if line["side"] == "credit"],
        "rule": rule,
        "rule_key": None,
        "why_not": res.get("why_not"),
        "next_action": res.get("next_action"),
        "authority_state": "bookkeeping",
        # Sprint 15I-R: the engine's deterministic calculation records
        # (e.g. BK_LIST_PRICE -> BK_TRADE_DISCOUNT_AMOUNT ->
        # BK_NET_TRANSACTION_VALUE) are passed through for the student
        # 'Trade discount' breakdown. Read-only presentation data from
        # the hardened engine - no arithmetic is done here.
        "calculation_records": res.get("calculation_records") or [],
    }


def classify_transaction(description: str, amount: Any = None) -> Dict[str, Any]:
    """Deterministically classify ONE transaction into a journal entry.

    Returns student-readable fields: debit/credit lines (accounts + roles
    + amounts when known), the applied golden rule, status, why-not and
    next action. Ambiguity -> REVIEW_REQUIRED; missing essential
    information -> BLOCKED. Never fabricates a treatment or an amount.
    """
    desc = str(description or "").strip()
    if not desc:
        return {
            "status": BLOCKED,
            "status_label": STATUS_LABELS[BLOCKED],
            "debit_lines": [], "credit_lines": [],
            "rule": None, "why_not": "No transaction was provided.",
            "next_action": "Type or photograph the transaction description.",
        }

    amounts, ambiguous = _extract_amounts(desc)
    if amount is not None:
        parsed = parse_numeric_text(amount)
        if parsed.value is not None and not parsed.ambiguity:
            amounts.insert(0, parsed.value)

    # -- pattern engine ---------------------------------------------------
    # Sprint 15B: fixed-asset transactions are resolved FIRST so the
    # exact asset named by the question is used - never an invented
    # sibling account (Machinery for a Furniture purchase, etc.) and
    # never a goods 'Purchases' account for a fixed asset.
    pattern = None
    low = desc.lower()
    pattern = _asset_purchase_rule(desc)
    if pattern is None:
        pattern = _asset_sale_rule(desc)
    if pattern is None:
        for cand in _TRANSACTION_PATTERNS:
            when = cand["when"]
            phrases = when if isinstance(when, (tuple, list)) else (when,)
            if any(phrase in low for phrase in phrases):
                pattern = cand
                break
    if pattern is None:
        pattern = _purchase_rule(desc)
    if pattern is None:
        pattern = _expense_income_rule(desc)

    if pattern is None:
        return {
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "debit_lines": [], "credit_lines": [],
            "rule": None,
            "why_not": ("This transaction could not be recognised "
                        "deterministically from the description. FT-E never "
                        "guesses an accounting treatment."),
            "next_action": ("Re-write the transaction in standard FYJC "
                            "wording (e.g. 'Purchased goods for cash', "
                            "'Sold goods on credit to X', 'Paid rent')."),
        }

    if pattern.get("refuse"):
        return {
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "debit_lines": [], "credit_lines": [],
            "rule": pattern["rule"],
            "why_not": ("The transaction is ambiguous: 'purchased goods' "
                        "does not say whether it was for cash or on "
                        "credit. FT-E never assumes one."),
            "next_action": ("Add 'for cash' or 'on credit from <name>' to "
                            "the transaction description."),
        }

    # -- resolve accounts -------------------------------------------------
    cash_or_bank = _resolve_side([desc], pattern["credit"] + pattern["debit"])
    debit_accounts = [_resolve_account_spec(spec, desc, "receiver")
                      for spec in pattern["debit"]]
    credit_accounts = [_resolve_account_spec(spec, desc, "giver")
                       for spec in pattern["credit"]]
    debit_accounts = [a for a in debit_accounts if a is not None]
    credit_accounts = [a for a in credit_accounts if a is not None]
    debit_accounts = _collapse_cash_bank(debit_accounts, cash_or_bank)
    credit_accounts = _collapse_cash_bank(credit_accounts, cash_or_bank)

    # -- ambiguity / missing-info gates -----------------------------------
    # expected sides are counted POST-Collapse: a [Cash, Bank] either/or
    # resolves to exactly one account, so it contributes one side.
    expected_sides = sum(1 for spec in pattern["debit"]) \
        + sum(1 for spec in pattern["credit"])
    for side in (pattern["debit"], pattern["credit"]):
        if (all(isinstance(s, str) for s in side)
                and "Cash" in side and "Bank" in side):
            expected_sides -= 1
    resolved_sides = len(debit_accounts) + len(credit_accounts)
    if expected_sides > 0 and resolved_sides < expected_sides:
        party_missing = any(isinstance(spec, dict) for spec in
                            pattern["debit"] + pattern["credit"])
        if party_missing:
            return {
                "status": REVIEW_REQUIRED,
                "status_label": STATUS_LABELS[REVIEW_REQUIRED],
                "debit_lines": [], "credit_lines": [],
                "rule": pattern["rule"],
                "why_not": ("The transaction names a party but the "
                            "counterparty name could not be read "
                            "deterministically. FT-E never invents a "
                            "person's name."),
                "next_action": ("Re-type the transaction with the person's "
                                "name (e.g. 'Sold goods on credit to "
                                "Mohan')."),
            }
        return {
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "debit_lines": [], "credit_lines": [],
            "rule": pattern["rule"],
            "why_not": ("The accounts for this transaction could not be "
                        "fully determined."),
            "next_action": "Provide more detail (cash or credit, and names).",
        }

    if len(amounts) > 1:
        return {
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "debit_lines": [], "credit_lines": [],
            "rule": pattern["rule"],
            "why_not": ("More than one amount was found in the description "
                        "and the mapping to accounts is ambiguous. FT-E "
                        "never guesses which amount belongs to which "
                        "account."),
            "next_action": ("Submit the journal entry directly (debit line "
                            "and credit line with amounts) instead of a "
                            "combined sentence."),
        }
    if ambiguous:
        return {
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "debit_lines": [], "credit_lines": [],
            "rule": pattern["rule"],
            "why_not": ("An amount could not be read cleanly (OCR-style "
                        "uncertainty). FT-E never silently corrects an "
                        "uncertain value."),
            "next_action": "Re-enter the amount clearly (e.g. 5,000).",
        }

    if not amounts:
        # treatment is determinable; the amount is missing -> BLOCKED.
        return {
            "status": BLOCKED,
            "status_label": STATUS_LABELS[BLOCKED],
            "debit_lines": [
                {"account": a, "role": account_role(a),
                 "side": "debit", "amount": None,
                 "side_hint": SIDE_EXPLANATION.get(account_role(a))}
                for a in debit_accounts
            ],
            "credit_lines": [
                {"account": a, "role": account_role(a),
                 "side": "credit", "amount": None,
                 "side_hint": SIDE_EXPLANATION.get(account_role(a))}
                for a in credit_accounts
            ],
            "rule": pattern["rule"],
            "rule_key": pattern["key"],
            "why_not": ("The treatment is clear but the amount is missing. "
                        "The amount is required to post the journal entry."),
            "next_action": "Enter the amount of the transaction.",
        }

    amount_value = amounts[0]
    debit_lines = [{
        "account": a, "role": account_role(a),
        "side": "debit", "amount": amount_value,
        "side_hint": SIDE_EXPLANATION.get(account_role(a)),
    } for a in debit_accounts]
    credit_lines = [{
        "account": a, "role": account_role(a),
        "side": "credit", "amount": amount_value,
        "side_hint": SIDE_EXPLANATION.get(account_role(a)),
    } for a in credit_accounts]

    return {
        "status": VERIFIED,
        "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
        "debit_lines": debit_lines,
        "credit_lines": credit_lines,
        "rule": pattern["rule"],
        "rule_key": pattern["key"],
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
    }


def _resolve_account_spec(spec: Any, description: str,
                          party_kind: str) -> Optional[str]:
    """Resolve a pattern account spec: fixed account or party placeholder."""
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict) and spec.get("party"):
        party = _party_from(description, "")
        return party
    return None


def identify_debit_credit(description: str, amount: Any = None) -> Dict[str, Any]:
    """Debit/credit identification UX: which accounts, and why."""
    outcome = classify_transaction(description, amount)
    return {
        "status": outcome["status"],
        "status_label": outcome["status_label"],
        "debit": [
            {"account": line["account"], "side": line["side"],
             "role": line["role"], "side_hint": line["side_hint"]}
            for line in outcome.get("debit_lines", [])
        ],
        "credit": [
            {"account": line["account"], "side": line["side"],
             "role": line["role"], "side_hint": line["side_hint"]}
            for line in outcome.get("credit_lines", [])
        ],
        "rule": outcome.get("rule"),
        "why_not": outcome.get("why_not"),
        "next_action": outcome.get("next_action"),
    }


# ---------------------------------------------------------------------------
# Journal entry verification
# ---------------------------------------------------------------------------


def _line_amount(line: Any) -> Optional[Decimal]:
    """Parse one entry-line amount (number or numeric string)."""
    if not isinstance(line, dict):
        return None
    value = line.get("amount")
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    parsed = parse_numeric_text(str(value))
    if parsed.value is None or parsed.ambiguity:
        return None
    return parsed.value


def _canonical_reference_amount(value: Any) -> Any:
    """Normalise one hardened-engine line amount for exact comparison.

    The hardened canonical journal carries Decimal amounts and the
    student entry amounts are parsed to Decimal by _line_amount; the two
    must share ONE numeric representation so a canonical amount and a
    student-entered '10,000' compare equal. Unreadable values are kept
    as-is (they can never match a Decimal and are reported as a
    discrepancy instead of crashing).
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        parsed = parse_numeric_text(value)
        if parsed.value is not None and not parsed.ambiguity:
            return parsed.value
        return value
    return value


def _journal_discrepancies(missing: Counter, extra: Counter,
                           side_label: str,
                           student_lines: List[Dict[str, Any]]) -> List[str]:
    """Human-readable differences between the student's entry and the
    hardened canonical journal on ONE side (debit / credit).

    missing - canonical (account, amount) lines the student did not post.
    extra   - student lines the canonical journal does not contain.
    student_lines - the student's normalised lines for this side, used to
    tell an AMOUNT MISMATCH ('the account was posted with a different
    amount') from a genuinely MISSING line, and an EXTRA line from a
    duplicated account.

    Deterministic; only shapes the hardened engine's expectation into
    student-readable prose - no accounting rule is applied here.
    """
    issues: List[str] = []
    for (account, amount), count in sorted(missing.items()):
        student_amounts = sorted(
            {line["amount"] for line in student_lines
             if line["account"] == account},
            key=str)
        repeat = f" (posted {count} times)" if count > 1 else ""
        if student_amounts:
            issues.append(
                f"{side_label} {account}: Expected Rs.{amount:,.2f} but "
                "you posted "
                + " / ".join(f"Rs.{amt:,.2f}" for amt in student_amounts)
                + repeat)
        else:
            issues.append(
                f"Expected {side_label} line: {account} Rs.{amount:,.2f} "
                "(missing from your entry)" + repeat)
    for (account, amount), count in sorted(extra.items()):
        if any(acc == account for (acc, _) in missing):
            continue  # already reported as an amount mismatch
        repeat = f" (posted {count} times)" if count > 1 else ""
        issues.append(
            f"Extra {side_label} line: {account} Rs.{amount:,.2f} "
            "(not in the expected entry)" + repeat)
    return issues


def _normalise_entry(entry: Any) -> Tuple[List[Dict[str, Any]],
                                          List[Dict[str, Any]], bool]:
    """Normalise a student journal entry into (debit lines, credit lines,
    ok). Lines are [{"account": canonical-or-raw, "amount": Decimal}]."""
    if not isinstance(entry, dict):
        return [], [], False
    debit_lines: List[Dict[str, Any]] = []
    credit_lines: List[Dict[str, Any]] = []
    for raw in entry.get("debits") or []:
        if not isinstance(raw, dict):
            continue
        account = raw.get("account")
        amount = _line_amount(raw)
        if account is None or amount is None:
            return [], [], False
        debit_lines.append({"account": str(account).strip(), "amount": amount})
    for raw in entry.get("credits") or []:
        if not isinstance(raw, dict):
            continue
        account = raw.get("account")
        amount = _line_amount(raw)
        if account is None or amount is None:
            return [], [], False
        credit_lines.append({"account": str(account).strip(), "amount": amount})
    return debit_lines, credit_lines, bool(debit_lines or credit_lines)


def verify_journal_entry(description: Optional[str],
                         entry: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a student's journal entry.

    Checks (all deterministic):
      1. structure / amounts readable
      2. totals: sum(debits) == sum(credits)
      3. accounts recognised (chart or named party)
      4. direction matches the golden-rule treatment when the transaction
         is determinable from the description

    Returns a student-readable outcome with verdict
    (CORRECT / INCORRECT / REFUSED), the exact discrepancy when totals
    differ, and the golden-rule explanation.
    """
    debit_lines, credit_lines, ok = _normalise_entry(entry)
    if not ok:
        return {
            "verdict": "REFUSED",
            "status": BLOCKED,
            "status_label": STATUS_LABELS[BLOCKED],
            "authority_state": "bookkeeping",
            "what": "The journal entry could not be read.",
            "why_not": ("Each journal line needs an account name and a "
                        "numeric amount."),
            "next_action": "Re-enter the entry as debit/credit lines with amounts.",
            "discrepancy": None,
        }

    total_debit = sum((line["amount"] for line in debit_lines), Decimal(0))
    total_credit = sum((line["amount"] for line in credit_lines), Decimal(0))
    difference = total_debit - total_credit

    # -- arithmetic (totals) gate ----------------------------------------
    if difference != 0:
        return {
            "verdict": "INCORRECT",
            "status": REVIEW_REQUIRED,
            "status_label": STATUS_LABELS[REVIEW_REQUIRED],
            "authority_state": "bookkeeping",
            "what": "The journal entry is not balanced.",
            "why_not": ("Every journal entry must have total Debit = total "
                        "Credit. Your entry has a difference of "
                        f"{abs(difference):.2f}."),
            "next_action": ("Re-check the amounts. Add or correct the line "
                            f"that fixes the {abs(difference):.2f} "
                            "difference."),
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "discrepancy": float(abs(difference)),
        }

    # -- account recognition ----------------------------------------------
    # A line that is not a recognised FYJC chart account is treated as a
    # NAMED PARTY (personal account) - the golden-rule direction check
    # below still catches a wrong account. Nothing is ever silently
    # rewritten.
    canonical_debits = [
        {"account": canonical_account(line["account"]) or line["account"],
         "amount": line["amount"], "side": "debit"}
        for line in debit_lines
    ]
    canonical_credits = [
        {"account": canonical_account(line["account"]) or line["account"],
         "amount": line["amount"], "side": "credit"}
        for line in credit_lines
    ]

    # -- direction check against the golden rule --------------------------
    # Sprint 15I-O: the treatment reference is the hardened FT-E engine
    # (reason_bk_question) - the same authority the QuestionBank /
    # PracticeEngine path uses. The legacy classifier is never the
    # reference for FYJC bookkeeping verification.
    reference: Optional[Dict[str, Any]] = None
    if description and str(description).strip():
        reference = hardened_bookkeeping_outcome(description)
        if reference["status"] == VERIFIED:
            # Sprint 15I-P: the student entry must match the hardened
            # canonical journal EXACTLY - account identity, debit/credit
            # side, amount and line multiplicity (duplicate lines are
            # never collapsed). The hardened engine remains the sole
            # authority: nothing is recomputed here, the canonical lines
            # are compared verbatim. A balanced entry with the right
            # accounts but a wrong amount is INCORRECT, never CORRECT.
            ref_debit_counter = Counter(
                (line["account"],
                 _canonical_reference_amount(line["amount"]))
                for line in reference["debit_lines"])
            ref_credit_counter = Counter(
                (line["account"],
                 _canonical_reference_amount(line["amount"]))
                for line in reference["credit_lines"])
            student_debit_counter = Counter(
                (line["account"], line["amount"])
                for line in canonical_debits)
            student_credit_counter = Counter(
                (line["account"], line["amount"])
                for line in canonical_credits)
            if (student_debit_counter == ref_debit_counter
                    and student_credit_counter == ref_credit_counter):
                return {
                    "verdict": "CORRECT",
                    "status": VERIFIED,
                    "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
                    "authority_state": "bookkeeping",
                    "what": ("The journal entry is correct and follows the "
                             "golden rule."),
                    "rule": reference["rule"],
                    "debit_lines": canonical_debits,
                    "credit_lines": canonical_credits,
                    "why_not": None,
                    "next_action": "Post it to the ledger.",
                    "total_debit": float(total_debit),
                    "total_credit": float(total_credit),
                    "discrepancy": None,
                }
            # -- exact discrepancy analysis -----------------------------
            # The same lines posted on the OPPOSITE sides get the classic
            # direction message; anything else is reported line-by-line
            # (wrong amount / missing line / extra line).
            fully_reversed = (
                student_debit_counter == ref_credit_counter
                and student_credit_counter == ref_debit_counter)
            missing_debits = ref_debit_counter - student_debit_counter
            missing_credits = ref_credit_counter - student_credit_counter
            extra_debits = student_debit_counter - ref_debit_counter
            extra_credits = student_credit_counter - ref_credit_counter
            issues = _journal_discrepancies(
                missing_debits, extra_debits, "debit", canonical_debits)
            issues += _journal_discrepancies(
                missing_credits, extra_credits, "credit", canonical_credits)
            if fully_reversed:
                ref_debit_accounts = sorted(
                    {a for (a, _) in ref_debit_counter})
                ref_credit_accounts = sorted(
                    {a for (a, _) in ref_credit_counter})
                what = ("The journal entry is balanced but the debit and "
                        "credit sides are reversed.")
                why_not = (
                    "Expected debit: "
                    f"{ref_debit_accounts}; expected credit: "
                    f"{ref_credit_accounts}. Your entry posts the same "
                    "lines on the opposite sides.")
            else:
                what = ("The journal entry is balanced but it does not "
                        "match the expected entry exactly (accounts, "
                        "side or amount).")
                why_not = (" ".join(issues)
                           or "Your entry differs from the expected "
                              "journal entry.")
            return {
                "verdict": "INCORRECT",
                "status": REVIEW_REQUIRED,
                "status_label": STATUS_LABELS[REVIEW_REQUIRED],
                "authority_state": "bookkeeping",
                "what": what,
                "rule": reference["rule"],
                "why_not": why_not,
                "next_action": ("Re-read the golden rule and the expected "
                                "journal entry: match every account, its "
                                "side and its exact amount."),
                "debit_lines": canonical_debits,
                "credit_lines": canonical_credits,
                "total_debit": float(total_debit),
                "total_credit": float(total_credit),
                "discrepancy": None,
            }

    # arithmetic is fine and no treatment reference was available
    return {
        "verdict": "BALANCED",
        "status": REVIEW_REQUIRED,
        "status_label": STATUS_LABELS[REVIEW_REQUIRED],
        "authority_state": "bookkeeping",
        "what": "The entry is balanced (Debit = Credit).",
        "why_not": (
            "No transaction description was provided (or the treatment is "
            "ambiguous), so FT-E cannot confirm the direction of the "
            "accounts."
            if not (description and str(description).strip())
            else reference.get("why_not", "The treatment is ambiguous.")
        ),
        "next_action": "Provide the original transaction wording so FT-E "
                       "can verify debit/credit direction.",
        "debit_lines": canonical_debits,
        "credit_lines": canonical_credits,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "discrepancy": None,
    }


# ---------------------------------------------------------------------------
# Ledger posting and verification
# ---------------------------------------------------------------------------


def post_ledger(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Post journal entries to the ledger (deterministic Decimal sums).

    Returns {accounts: {account: {debit, credit, balance, balance_side,
    role}}, total_debit, total_credit, balanced}."""
    ledger: Dict[str, Dict[str, Any]] = {}
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for entry in entries or []:
        debit_lines, credit_lines, ok = _normalise_entry(entry)
        if not ok:
            continue  # never fabricate a posting from an unreadable entry
        for line in debit_lines:
            account = canonical_account(line["account"]) or line["account"]
            ledger.setdefault(account, {"debit": Decimal(0),
                                        "credit": Decimal(0)})
            ledger[account]["debit"] += line["amount"]
            total_debit += line["amount"]
        for line in credit_lines:
            account = canonical_account(line["account"]) or line["account"]
            ledger.setdefault(account, {"debit": Decimal(0),
                                        "credit": Decimal(0)})
            ledger[account]["credit"] += line["amount"]
            total_credit += line["amount"]
    accounts: Dict[str, Dict[str, Any]] = {}
    for account in sorted(ledger):
        debit = ledger[account]["debit"]
        credit = ledger[account]["credit"]
        balance = debit - credit
        side = "Dr" if balance > 0 else ("Cr" if balance < 0 else "nil")
        accounts[account] = {
            "debit": float(debit), "credit": float(credit),
            "balance": float(balance), "balance_side": side,
            "role": account_role(account),
        }
    return {
        "accounts": accounts,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "balanced": total_debit == total_credit,
    }


def ledger_balance(account: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Engine-computed balance for one ledger account."""
    ledger = post_ledger(entries)
    canon = canonical_account(account) or str(account).strip()
    row = ledger["accounts"].get(canon)
    if row is None:
        return {
            "account": canon, "found": False, "balance": None,
            "balance_side": None,
            "why_not": f"No postings found for '{account}'.",
        }
    return {
        "account": canon, "found": True, "balance": row["balance"],
        "balance_side": row["balance_side"], "debit": row["debit"],
        "credit": row["credit"],
    }


def verify_ledger_balance(account: str, student_balance: Any,
                          student_side: str, entries: List[Dict[str, Any]],
                          tolerance: float = 0.01) -> Dict[str, Any]:
    """Verify a student's ledger balance for one account."""
    expected = ledger_balance(account, entries)
    if not expected["found"]:
        return {
            "verdict": "REFUSED",
            "status": BLOCKED,
            "status_label": STATUS_LABELS[BLOCKED],
            "authority_state": "bookkeeping",
            "what": f"No ledger postings found for {account}.",
            "why_not": expected["why_not"],
            "next_action": "Post the journal entries first.",
        }
    parsed = (parse_numeric_text(student_balance)
              if student_balance not in (None, "") else None)
    if parsed is None or parsed.value is None:
        return {
            "verdict": "REFUSED",
            "status": BLOCKED,
            "status_label": STATUS_LABELS[BLOCKED],
            "authority_state": "bookkeeping",
            "what": f"The balance for {account} could not be read.",
            "why_not": "Enter the balance as a number (e.g. 12,500).",
            "next_action": "Re-enter the balance and its side (Dr/Cr).",
        }
    student_num = float(parsed.value)
    expected_num = expected["balance"]
    correct_side = str(student_side or "").strip().lower() == \
        str(expected["balance_side"]).lower()
    delta = abs(student_num - abs(expected_num))
    correct_amount = delta <= float(tolerance)
    if correct_amount and correct_side:
        return {
            "verdict": "CORRECT",
            "status": VERIFIED,
            "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
            "authority_state": "bookkeeping",
            "what": (f"{account} ledger balance "
                     f"{expected['balance_side']} {expected_num:.2f} is "
                     "correct."),
            "why_not": None,
            "next_action": "Balance the next account or build the trial balance.",
            "expected": expected_num,
            "expected_side": expected["balance_side"],
        }
    problems = []
    if not correct_amount:
        problems.append(f"amount differs from the ledger total "
                        f"({expected_num:.2f} vs your {student_num:.2f})")
    if not correct_side:
        problems.append(f"side differs (ledger shows "
                        f"{expected['balance_side']}, you entered "
                        f"{student_side})")
    return {
        "verdict": "INCORRECT",
        "status": REVIEW_REQUIRED,
        "status_label": STATUS_LABELS[REVIEW_REQUIRED],
        "authority_state": "bookkeeping",
        "what": f"The {account} ledger balance is not correct.",
        "why_not": " and ".join(problems) + ".",
        "next_action": "Re-add the debit and credit postings for "
                       f"{account} in your ledger.",
        "expected": expected_num,
        "expected_side": expected["balance_side"],
        "discrepancy": float(delta),
    }


# ---------------------------------------------------------------------------
# Trial balance and arithmetic verification
# ---------------------------------------------------------------------------


def build_trial_balance(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Engine trial balance from posted ledger balances."""
    ledger = post_ledger(entries)
    rows: List[Dict[str, Any]] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for account in sorted(ledger["accounts"]):
        row = ledger["accounts"][account]
        # a trial balance shows the NET balance on its own side only
        if row["balance_side"] == "Dr":
            rows.append({"account": account, "debit": row["balance"],
                         "credit": 0.0})
            total_debit += Decimal(str(row["balance"]))
        elif row["balance_side"] == "Cr":
            rows.append({"account": account, "debit": 0.0,
                         "credit": -row["balance"]})
            total_credit += Decimal(str(-row["balance"]))
        # nil-balance accounts are omitted from a trial balance
    return {
        "rows": rows,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "balanced": total_debit == total_credit,
        "discrepancy": float(abs(total_debit - total_credit))
        if total_debit != total_credit else None,
    }


def verify_trial_balance(student_rows: List[Dict[str, Any]],
                         entries: List[Dict[str, Any]],
                         tolerance: float = 0.01) -> Dict[str, Any]:
    """Verify a student's trial balance against the posted ledger.

    Identifies missing rows, extra rows, wrong amounts/sides and the exact
    arithmetic discrepancy - never guesses which figure is right."""
    expected = build_trial_balance(entries)
    expected_map = {row["account"]: row for row in expected["rows"]}

    problems: List[str] = []
    seen: set = set()
    for row in student_rows or []:
        if not isinstance(row, dict):
            continue
        account = canonical_account(row.get("account")) or str(
            row.get("account") or "").strip()
        if not account:
            continue
        seen.add(account)
        exp = expected_map.get(account)
        if exp is None:
            problems.append(f"'{account}' has no balance in the ledger "
                            "(check whether it should be in the trial "
                            "balance).")
            continue
        debit = Decimal("0")
        credit = Decimal("0")
        try:
            debit = Decimal(str(row.get("debit") or 0))
            credit = Decimal(str(row.get("credit") or 0))
        except (InvalidOperation, ValueError):
            problems.append(f"'{account}' has an unreadable amount.")
            continue
        if abs(debit - Decimal(str(exp["debit"]))) > Decimal(str(tolerance)) \
                or abs(credit - Decimal(str(exp["credit"]))) \
                > Decimal(str(tolerance)):
            problems.append(
                f"'{account}' should read Dr {exp['debit']:.2f} / "
                f"Cr {exp['credit']:.2f} but you entered "
                f"{float(debit):.2f} / {float(credit):.2f}."
            )
    for account in expected_map:
        if account not in seen:
            exp = expected_map[account]
            problems.append(
                f"'{account}' (Dr {exp['debit']:.2f} / Cr {exp['credit']:.2f}) "
                "is missing from your trial balance."
            )

    student_total_debit = sum(
        (Decimal(str(row.get("debit") or 0)) for row in student_rows or []
         if isinstance(row, dict) and row.get("account")), Decimal(0))
    student_total_credit = sum(
        (Decimal(str(row.get("credit") or 0)) for row in student_rows or []
         if isinstance(row, dict) and row.get("account")), Decimal(0))
    student_discrepancy = abs(student_total_debit - student_total_credit)

    if not problems and student_discrepancy <= Decimal(str(tolerance)):
        return {
            "verdict": "CORRECT",
            "status": VERIFIED,
            "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
            "authority_state": "bookkeeping",
            "what": ("The trial balance is correct: total Debit "
                     f"{expected['total_debit']:.2f} = total Credit "
                     f"{expected['total_credit']:.2f}."),
            "why_not": None,
            "next_action": "Move to the next question.",
            "expected": expected,
            "discrepancy": None,
        }

    return {
        "verdict": "INCORRECT",
        "status": REVIEW_REQUIRED,
        "status_label": STATUS_LABELS[REVIEW_REQUIRED],
        "authority_state": "bookkeeping",
        "what": "The trial balance does not agree with the ledger.",
        "why_not": (" ").join(problems) if problems else (
            "The totals differ: "
            f"your Debit {float(student_total_debit):.2f} vs Credit "
            f"{float(student_total_credit):.2f} (difference "
            f"{float(student_discrepancy):.2f}). The ledger totals are "
            f"Dr {expected['total_debit']:.2f} / "
            f"Cr {expected['total_credit']:.2f}."
        ),
        "next_action": "Re-add each ledger balance and check every account "
                       "above, one line at a time.",
        "expected": expected,
        "student_total_debit": float(student_total_debit),
        "student_total_credit": float(student_total_credit),
        "discrepancy": float(student_discrepancy),
    }


def verify_arithmetic(lines: List[Dict[str, Any]],
                      tolerance: float = 0.01) -> Dict[str, Any]:
    """Generic arithmetic verification: does total debit equal total credit
    for a list of [{side: 'debit'|'credit', amount: number}]? Returns the
    exact discrepancy - the student's own numbers are never altered."""
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        amount = _line_amount(line)
        if amount is None:
            continue
        side = str(line.get("side") or "").lower()
        if side in ("debit", "dr"):
            total_debit += amount
        elif side in ("credit", "cr"):
            total_credit += amount
    difference = abs(total_debit - total_credit)
    balanced = difference <= Decimal(str(tolerance))
    return {
        "balanced": balanced,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "discrepancy": None if balanced else float(difference),
        "verdict": "CORRECT" if balanced else "INCORRECT",
        "what": "Debit total equals credit total." if balanced else
                "Debit total does not equal credit total.",
        "next_action": None if balanced else (
            f"Find the arithmetic error - the difference is "
            f"{float(difference):.2f}."
        ),
    }


# ---------------------------------------------------------------------------
# Accounting calculations (C++ mathematical authority)
# ---------------------------------------------------------------------------


def accounting_calculation(metric: str,
                           facts: Optional[Dict[str, Any]] = None,
                           text: Optional[str] = None,
                           documents: Optional[List[Dict[str, Any]]] = None,
                           student_answer: Any = None) -> Dict[str, Any]:
    """Basic accounting calculation through the C++ mathematical authority
    (e.g. Gross Profit, Working Capital, Profit, Net Margin). Reuses the
    FYJC maths verification path - Python performs no fallback arithmetic.
    """
    return verify_maths_answer(metric, facts=facts, text=text,
                               documents=documents,
                               student_answer=student_answer)
