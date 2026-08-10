"""
Financial Timeline Engine
Sprint 15B - FYJC Book-Keeping Question Understanding & Reasoning Hardening
backend/maths/fyjc_bk_reasoning.py

A hardened, deterministic Book-Keeping & Accountancy reasoning pipeline that
takes a REAL FYJC-style question (typed / photo-extracted / PDF text) and
produces a correct, student-readable solution:

    Photo / PDF / typed question
      -> normalized / structured IR            (wording variations collapse
                                                to ONE canonical type)
      -> exact account identification          (never invents Machinery for
                                                a Furniture purchase, etc.)
      -> traditional FYJC classification       (Real / Personal / Nominal)
      -> traditional Golden Rule               (debit/credit decision + why)
      -> journal entry generation              (date, particulars, Dr/Cr,
                                                amount, narration)
      -> ledger reasoning                      (derived from the journal IR)
      -> trial balance reasoning               (from the ledger state;
                                                Dr == Cr or exact discrepancy)
      -> trade / cash discount + partial-payment pipeline
      -> C++ mathematical verification         (registered metrics only)
      -> refusal boundaries                    (BLOCKED / REVIEW_REQUIRED /
                                                NOT_SUPPORTED)
      -> student-facing "What FT-E understood"

Architectural rules (unchanged from Sprint 12F/13/14/15)
---------------------------------------------------------
* C++ remains the sole mathematical authority for REGISTERED financial
  metrics. Any metric the question requests is routed through the existing
  production authority path (verify_maths_answer -> solve_strict -> C++),
  and carries authority_state 'cpp' with a valid formula_id. Python never
  silently computes a registered metric.
* The bookkeeping posting arithmetic in this module (journal totals,
  ledger balances, trial-balance totals, discount netting) is
  VERIFICATION / PREPARATION arithmetic, exactly like the Sprint 13
  engine: it derives the amounts to post from the question's own numbers
  and traces EVERY step with a `calculation_id`, formula text, inputs and
  result. It never fabricates a value, never claims to be the C++ engine,
  and never hides a number. A resolved journal/ledger/TB number ALWAYS
  carries provenance (question-sourced or a pipeline step).
* No invented accounts. No invented amounts. No silent substitution.
  Ambiguous wording -> REVIEW_REQUIRED; missing essential info -> BLOCKED;
  out-of-boundary topics -> NOT_SUPPORTED. FT-E never guesses a treatment.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_accounting import (
    ACCOUNT_ALIASES,
    ACCOUNT_ROLES,
    account_role,
    canonical_account,
    classify_transaction,
    post_ledger,
    build_trial_balance,
    named_assets,
    verify_arithmetic,
)
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
# Refusal vocabulary
# ---------------------------------------------------------------------------

# Sprint 15B uses the same refusal vocabulary as the rest of the FYJC stack:
# BLOCKED (missing information), REVIEW_REQUIRED (ambiguous), and
# NOT_SUPPORTED (outside the supported FYJC boundary).
NOT_SUPPORTED = "NOT_SUPPORTED"

SUPPORTED_STATUSES = (VERIFIED, BLOCKED, REVIEW_REQUIRED, NOT_SUPPORTED)

# ---------------------------------------------------------------------------
# Traditional FYJC account classes + Golden Rules (syllabus language)
# ---------------------------------------------------------------------------

CLASS_REAL = "Real"
CLASS_PERSONAL = "Personal"
CLASS_NOMINAL = "Nominal"

# Standard FYJC Golden Rules (traditional three-account approach).
TRADITIONAL_GOLDEN_RULES: Dict[str, str] = {
    CLASS_REAL: "Debit what comes in, credit what goes out.",
    CLASS_PERSONAL: "Debit the receiver, credit the giver.",
    CLASS_NOMINAL: "Debit expenses and losses, credit incomes and gains.",
}

# Account -> traditional class. Personal accounts are persons / firms /
# artificial persons (bank) and their representatives (capital, drawings,
# debtors, creditors, loan). Real accounts are assets. Nominal accounts are
# expenses, incomes, losses and gains. Never modified at runtime.
_TRADITIONAL_OVERRIDES: Dict[str, str] = {
    # personal (natural / artificial persons + representatives)
    "Capital": CLASS_PERSONAL, "Drawings": CLASS_PERSONAL,
    "Debtors": CLASS_PERSONAL, "Creditors": CLASS_PERSONAL,
    "Loan": CLASS_PERSONAL, "Bank Loan": CLASS_PERSONAL,
    "Bills Payable": CLASS_PERSONAL, "Bank Overdraft": CLASS_PERSONAL,
    "Bank": CLASS_PERSONAL, "Outstanding Expenses": CLASS_PERSONAL,
    "Unearned Income": CLASS_PERSONAL,
    # real (assets)
    "Cash": CLASS_REAL, "Stock": CLASS_REAL, "Inventory": CLASS_REAL,
    "Machinery": CLASS_REAL, "Furniture": CLASS_REAL, "Building": CLASS_REAL,
    "Land": CLASS_REAL, "Vehicle": CLASS_REAL, "Equipment": CLASS_REAL,
    "Office Equipment": CLASS_REAL, "Investments": CLASS_REAL,
    "Bills Receivable": CLASS_REAL, "Goodwill": CLASS_REAL,
    "Patents": CLASS_REAL, "Prepaid Expenses": CLASS_REAL,
    "Provision for Depreciation": CLASS_REAL,
    # nominal (expenses / incomes / losses / gains)
    "Purchases": CLASS_NOMINAL, "Sales": CLASS_NOMINAL,
    "Rent": CLASS_NOMINAL, "Salaries": CLASS_NOMINAL, "Wages": CLASS_NOMINAL,
    "Insurance": CLASS_NOMINAL, "Advertisement": CLASS_NOMINAL,
    "Electricity": CLASS_NOMINAL, "Office Expenses": CLASS_NOMINAL,
    "General Expenses": CLASS_NOMINAL, "Commission Paid": CLASS_NOMINAL,
    "Commission Received": CLASS_NOMINAL, "Interest Paid": CLASS_NOMINAL,
    "Interest Received": CLASS_NOMINAL, "Discount Allowed": CLASS_NOMINAL,
    "Discount Received": CLASS_NOMINAL, "Bad Debts": CLASS_NOMINAL,
    "Bad Debts Recovered": CLASS_NOMINAL, "Carriage Inward": CLASS_NOMINAL,
    "Carriage Outward": CLASS_NOMINAL, "Rent Paid": CLASS_NOMINAL,
    "Rent Received": CLASS_NOMINAL, "Interest on Capital": CLASS_NOMINAL,
    "Interest on Drawings": CLASS_NOMINAL, "Repairs": CLASS_NOMINAL,
    "Postage": CLASS_NOMINAL, "Stationery": CLASS_NOMINAL,
    "Audit Fees": CLASS_NOMINAL, "Legal Fees": CLASS_NOMINAL,
    "Fuel": CLASS_NOMINAL, "Income Tax": CLASS_NOMINAL,
    "Loss on Sale of Asset": CLASS_NOMINAL, "Profit on Sale of Asset":
        CLASS_NOMINAL, "Dividend Received": CLASS_NOMINAL,
    "Sales Returns": CLASS_NOMINAL, "Returns Inward": CLASS_NOMINAL,
    "Purchase Returns": CLASS_NOMINAL, "Returns Outward": CLASS_NOMINAL,
}

# Named parties (Rahul, Mohan, ...) are ALWAYS Personal accounts.
_PARTY_SUFFIXES = ("a/c", "account", "ltd", "limited", "& co", "and co")


def traditional_class_for(account: str) -> str:
    """The FYJC traditional class of a canonical account or named party.

    Override table wins; a non-chart account that reads as a proper noun
    (Capitalised, no internal spaces) is a Personal account (Rahul,
    Mohan, ...). Everything else falls back to Nominal defensively but
    should never be used to build an entry.
    """
    if not account:
        return CLASS_PERSONAL
    direct = _TRADITIONAL_OVERRIDES.get(account)
    if direct is not None:
        return direct
    cleaned = str(account).strip().rstrip(".;,")
    low = cleaned.lower()
    if any(low.endswith(s) for s in _PARTY_SUFFIXES):
        return CLASS_PERSONAL
    if cleaned[:1].isupper() and " " not in cleaned:
        return CLASS_PERSONAL
    return CLASS_NOMINAL


def golden_rule_for(account: str) -> str:
    """The traditional Golden Rule text for an account's class."""
    return TRADITIONAL_GOLDEN_RULES[traditional_class_for(account)]


def side_decision_for(account: str, side: str) -> str:
    """Student-readable WHY for debiting/crediting one account.

    Traditional FYJC language - never corporate terminology.
    """
    cls = traditional_class_for(account)
    if side in ("debit", "Dr"):
        if cls == CLASS_REAL:
            return (f"{account} (Real A/c): it comes in - Debit what comes "
                    "in.")
        if cls == CLASS_PERSONAL:
            return (f"{account} (Personal A/c): {account} is the receiver - "
                    "Debit the receiver.")
        return (f"{account} (Nominal A/c): it is an expense/loss - Debit "
                "expenses and losses.")
    if cls == CLASS_REAL:
        return (f"{account} (Real A/c): it goes out - Credit what goes out.")
    if cls == CLASS_PERSONAL:
        return (f"{account} (Personal A/c): {account} is the giver - Credit "
                "the giver.")
    return (f"{account} (Nominal A/c): it is an income/gain - Credit "
            "incomes and gains.")


# ---------------------------------------------------------------------------
# Amount extraction (deterministic; never guesses)
# ---------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(
    r"(?:₹|Rs\.?|INR|Rs)?\s*\(?\s*-?\s*\d[\d,]*(?:\.\d+)?\s*\)?"
)
_CURRENCY_PREFIX = re.compile(r"^(?:₹|Rs\.?|INR)\s*")
_PERCENT_TOKEN = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_FRACTION_WORDS = {
    "half": "50", "one-half": "50", "one half": "50", "50%": "50",
    "quarter": "25", "one-fourth": "25", "one fourth": "25", "25%": "25",
    "40%": "40", "30%": "30", "two-thirds": "66.6666666667",
    "three-fourths": "75", "75%": "75",
}


def _extract_amounts(text: str) -> Tuple[List[Decimal], bool]:
    """Every clean number-like token in the text (currency stripped).

    A number immediately followed by '%' is a RATE (trade/cash discount),
    never a money amount - it is excluded so '2% cash discount' cannot be
    misread as Rs.2 paid."""
    amounts: List[Decimal] = []
    ambiguous = False
    for match in _NUMBER_TOKEN.finditer(str(text)):
        token = match.group(0).strip()
        if not token or not re.search(r"\d", token):
            continue
        # rate token (n%): skip - it is not an amount
        after = str(text)[match.end():match.end() + 2]
        if after.lstrip().startswith("%"):
            continue
        token = _CURRENCY_PREFIX.sub("", token).strip()
        # 'Rs.9,800, discount ...' - the greedy number token swallows the
        # trailing comma; drop trailing separators before the hard parse so
        # 9,800 parses as 9800 instead of being flagged ambiguous.
        token = token.rstrip(",.")
        parsed = parse_numeric_text(token)
        if parsed.value is None or parsed.ambiguity:
            ambiguous = True
            continue
        amounts.append(parsed.value)
    return amounts, ambiguous


def _extract_percents(text: str) -> List[Tuple[Decimal, str]]:
    """(rate, label) for every '<n>%' token - the label is the surrounding
    text so 'trade discount' vs 'cash discount' can be told apart."""
    out: List[Tuple[Decimal, str]] = []
    low = " " + str(text or "").lower() + " "
    for match in _PERCENT_TOKEN.finditer(low):
        try:
            rate = Decimal(match.group(1))
        except (InvalidOperation, ValueError):
            continue
        before = low[max(0, match.start() - 24):match.start()]
        after = low[match.end():match.end() + 24]
        label = " ".join((before + after).split())
        out.append((rate, label))
    return out


def _paid_fraction(text: str) -> Optional[Decimal]:
    """Fraction of the net amount paid immediately, from wording like
    'paid half immediately', 'half paid', 'paid 40% at once'."""
    low = " " + str(text or "").lower() + " "
    for word, fraction in _FRACTION_WORDS.items():
        if f" {word} " in low or low.startswith(word + " ") \
                or f" {word} " in low:
            if ("paid" in low or "cash" in low or "immediately" in low):
                return Decimal(fraction)
    return None


# ---------------------------------------------------------------------------
# Wording normalization -> canonical transaction type (registry-driven)
# ---------------------------------------------------------------------------
# Each pattern: first matching rule wins (ordered, deterministic). `when`
# phrases are matched case-insensitively as substrings. `debit`/`credit`
# are canonical accounts or {"party": "receiver"/"giver"} / {"asset": True}
# placeholders. Equivalent wordings collapse onto the SAME type.

BK_PATTERNS: List[Dict[str, Any]] = [
    {
        "key": "START_BUSINESS",
        "label": "Started business with cash",
        "when": ("started business", "commenced business",
                 "started the business", "began business",
                 "started business with cash"),
        "debit": ["Cash"], "credit": ["Capital"],
    },
    {
        "key": "CAPITAL_INTRODUCED",
        "label": "Additional capital introduced",
        "when": ("brought in as capital", "brought ... as capital",
                 "additional capital", "introduced capital",
                 "brought in additional capital"),
        "debit": ["Cash", "Bank"], "credit": ["Capital"],
    },
    {
        "key": "DRAWINGS_CASH",
        "label": "Drawings (cash withdrawn for personal use)",
        "when": ("withdrew for personal use", "withdrawn for personal use",
                 "for personal use", "for private use", "drawings"),
        "debit": ["Drawings"], "credit": ["Cash", "Bank"],
    },
    {
        "key": "PURCHASE_GOODS_CASH",
        "label": "Goods purchased for cash",
        "when": ("purchased goods for cash", "bought goods for cash",
                 "goods purchased for cash", "cash purchase of goods",
                 "purchased goods paying cash", "bought goods paying cash",
                 "purchased goods in cash", "bought goods in cash",
                 "purchased goods for cash", "purchased stock for cash",
                 "purchased goods by cheque", "bought goods by cheque",
                 "purchased goods by check", "bought goods by check",
                 "purchased stock by cheque", "bought stock by cheque"),
        "debit": ["Purchases"], "credit": ["Cash", "Bank"],
    },
    {
        "key": "PURCHASE_GOODS_CREDIT",
        "label": "Goods purchased on credit",
        "when": ("purchased goods on credit", "purchased goods from",
                 "bought goods on credit", "bought goods from",
                 "credit purchase of goods", "purchased goods from rahul",
                 "purchased goods from amit", "bought stock on credit",
                 "bought stock from", "goods purchased on credit",
                 "goods bought on credit", "goods purchased from",
                 "goods bought from", "goods purchased by cheque",
                 "goods bought by cheque"),
        "debit": ["Purchases"], "credit": [{"party": "giver"}],
    },
    {
        "key": "SALE_GOODS_CASH",
        "label": "Goods sold for cash",
        "when": ("sold goods for cash", "sold for cash", "cash sale",
                 "cash sales", "sold goods in cash", "sold stock for cash",
                 "sold goods by cheque", "sold goods by check",
                 "sold stock by cheque", "sold by cheque", "sold by check"),
        "debit": ["Cash", "Bank"], "credit": ["Sales"],
    },
    {
        "key": "SALE_GOODS_CREDIT",
        "label": "Goods sold on credit",
        "when": ("sold goods to", "sold to", "credit sale", "sold on credit",
                 "on credit to", "sold goods on credit",
                 "sold goods on credit to", "credit sale to",
                 "goods sold to", "goods sold on credit",
                 "goods sold on credit to"),
        "debit": [{"party": "receiver"}], "credit": ["Sales"],
    },
    {
        "key": "EXPENSE_PAID",
        "label": "Expense paid",
        "when": ("paid rent", "paid salary", "paid salaries", "paid wages",
                 "paid insurance", "paid advertisement",
                 "paid electricity", "paid office expenses",
                 "paid general expenses", "paid commission",
                 "paid interest", "paid carriage", "paid repairs",
                 "paid postage", "paid stationery", "paid audit fees",
                 "paid legal fees", "paid income tax", "paid fuel",
                 "rent paid", "salary paid", "salaries paid", "wages paid",
                 "insurance paid", "purchased stationery",
                 "bought stationery", "stationery purchased",
                 "paid telephone", "telephone bill paid"),
        "debit": ["_EXPENSE_ACCOUNT"], "credit": ["Cash", "Bank"],
    },
    {
        "key": "INCOME_RECEIVED",
        "label": "Income received",
        "when": ("received commission", "commission received",
                 "received interest", "interest received",
                 "received rent", "rent received",
                 "received dividend", "dividend received"),
        "debit": ["Cash", "Bank"], "credit": ["_INCOME_ACCOUNT"],
    },
    {
        "key": "PAID_TO",
        "label": "Payment to a party",
        "when": ("paid to", "paid cash to", "paid ... to"),
        "debit": [{"party": "giver"}], "credit": ["Cash", "Bank"],
    },
    {
        "key": "RECEIVED_FROM",
        "label": "Receipt from a party",
        "when": ("received from", "received cash from"),
        "debit": ["Cash", "Bank"], "credit": [{"party": "giver"}],
    },
    {
        "key": "CASH_INTO_BANK",
        "label": "Cash deposited into bank",
        "when": ("deposited into bank", "deposited cash into bank",
                 "paid into bank", "deposited in bank", "cash into bank"),
        "debit": ["Bank"], "credit": ["Cash"],
    },
    {
        "key": "CASH_FROM_BANK",
        "label": "Cash withdrawn from bank",
        "when": ("withdrew from bank", "withdrawn from bank",
                 "drew from bank", "drawn from bank", "cash from bank",
                 "withdrew cash from bank", "withdrawn cash from bank"),
        "debit": ["Cash"], "credit": ["Bank"],
    },
    {
        "key": "CHEQUE_PAID",
        "label": "Payment by cheque",
        "when": ("paid by cheque", "cheque paid", "issued a cheque",
                 "gave a cheque", "cheque issued"),
        "debit": [{"party": "giver"}], "credit": ["Bank"],
    },
    {
        "key": "CHEQUE_RECEIVED",
        "label": "Receipt by cheque",
        "when": ("received a cheque", "received cheque", "cheque received",
                 "got a cheque"),
        "debit": ["Bank"], "credit": [{"party": "giver"}],
    },
    {
        "key": "PURCHASE_RETURN",
        "label": "Goods returned to supplier",
        "when": ("returned goods to", "goods returned to", "returned to",
                 "purchase return", "returns outward"),
        "debit": [{"party": "giver"}], "credit": ["Purchase Returns"],
    },
    {
        "key": "SALES_RETURN",
        "label": "Goods returned by customer",
        "when": ("returned goods by", "goods returned by", "returned by",
                 "returns inward", "sales returns", "sold goods returned"),
        "debit": ["Sales Returns"], "credit": [{"party": "giver"}],
    },
    {
        "key": "DISCOUNT_ALLOWED",
        "label": "Cash discount allowed to customer",
        "when": ("discount allowed", "allowed discount", "discount given"),
        "debit": ["Discount Allowed"], "credit": [{"party": "giver"}],
    },
    {
        "key": "DISCOUNT_RECEIVED",
        "label": "Cash discount received from supplier",
        "when": ("discount received", "received discount",
                 "discount received from"),
        "debit": [{"party": "giver"}], "credit": ["Discount Received"],
    },
    {
        "key": "LOAN_TAKEN",
        "label": "Loan taken",
        "when": ("took loan", "took a loan", "loan from bank",
                 "loan from", "taken a loan", "raised loan",
                 "borrowed from bank", "took a bank loan"),
        "debit": ["Cash", "Bank"], "credit": ["Loan"],
    },
    {
        "key": "LOAN_REPAID",
        "label": "Loan repaid",
        "when": ("repaid loan", "loan repaid", "paid ... loan",
                 "loan returned", "returned loan", "repaid the loan",
                 "repaid the bank loan"),
        "debit": ["Loan"], "credit": ["Cash", "Bank"],
    },
    {
        "key": "BAD_DEBTS",
        "label": "Bad debts written off",
        "when": ("bad debts written off", "written off as bad",
                 "wrote off", "bad debts"),
        "debit": ["Bad Debts"], "credit": [{"party": "giver"}],
    },
    {
        "key": "BAD_DEBTS_RECOVERED",
        "label": "Bad debts recovered",
        "when": ("bad debts recovered", "recovered from bad debt"),
        "debit": ["Cash", "Bank"], "credit": ["Bad Debts Recovered"],
    },
    {
        "key": "GOODS_PERSONAL_USE",
        "label": "Goods taken for personal use",
        "when": ("goods for personal use", "goods taken for personal use",
                 "goods ... personal use", "goods used for personal",
                 "goods for private use"),
        "debit": ["Drawings"], "credit": ["Purchases"],
    },
    {
        "key": "FREE_SAMPLES",
        "label": "Goods distributed as free samples",
        "when": ("free samples", "distributed as samples",
                 "goods distributed as free samples"),
        "debit": ["Advertisement"], "credit": ["Purchases"],
    },
    {
        "key": "INTEREST_ON_CAPITAL",
        "label": "Interest on capital allowed",
        "when": ("interest on capital",),
        "debit": ["Interest on Capital"], "credit": ["Capital"],
    },
    {
        "key": "INTEREST_ON_DRAWINGS",
        "label": "Interest on drawings charged",
        "when": ("interest on drawings",),
        "debit": ["Drawings"], "credit": ["Interest on Drawings"],
    },
]

# Deterministic account selection for the variable expense/income patterns.
_EXPENSE_ACCOUNT_WORDS: List[Tuple[str, str]] = [
    ("rent", "Rent"), ("salary", "Salaries"), ("salaries", "Salaries"),
    ("wages", "Wages"), ("insurance", "Insurance"),
    ("advertisement", "Advertisement"), ("electricity", "Electricity"),
    ("office expenses", "Office Expenses"), ("office", "Office Expenses"),
    ("general expenses", "General Expenses"), ("commission", "Commission Paid"),
    ("interest", "Interest Paid"), ("carriage", "Carriage Inward"),
    ("repairs", "Repairs"), ("postage", "Postage"), ("stationery", "Stationery"),
    ("audit fees", "Audit Fees"), ("legal fees", "Legal Fees"),
    ("income tax", "Income Tax"), ("fuel", "Fuel"),
    ("telephone", "Telephone Expenses"), ("telephone bill",
                                              "Telephone Expenses"),
]
_INCOME_ACCOUNT_WORDS: List[Tuple[str, str]] = [
    ("commission", "Commission Received"), ("interest", "Interest Received"),
    ("rent", "Rent Received"), ("discount", "Discount Received"),
    ("dividend", "Dividend Received"),
]


def _resolve_variable_account(spec: str, text: str) -> Optional[str]:
    """Resolve the '_EXPENSE_ACCOUNT' / '_INCOME_ACCOUNT' placeholders."""
    low = " " + str(text or "").lower() + " "
    table = (_EXPENSE_ACCOUNT_WORDS if spec == "_EXPENSE_ACCOUNT"
             else _INCOME_ACCOUNT_WORDS)
    for phrase, account in table:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low):
            return account
    return None


def _resolve_bk_spec(spec: Any, text: str,
                     party_kind: str) -> Optional[str]:
    """Resolve one pattern account spec (fixed / party / asset / variable)."""
    if isinstance(spec, str):
        if spec.startswith("_"):
            return _resolve_variable_account(spec, text)
        return spec
    if isinstance(spec, dict):
        if spec.get("party"):
            return _party_from_text(text)
        if spec.get("asset"):
            assets = named_assets(text)
            return assets[0] if len(assets) == 1 else None
    return None


def _party_from_text(text: str) -> Optional[str]:
    """Extract a Capitalised proper-noun party from the description."""
    if not text:
        return None
    low = text.lower()
    for marker in ("on credit from ", "sold goods on credit to ",
                   "on credit to ", "purchased goods from ", "purchased from ",
                   "bought goods from ", "bought from ", "sold goods to ",
                   "paid to ", "received from ", "sold to ",
                   "returned goods to ", "returned by ", "goods returned by ",
                   "received cash from ", "paid cash to ", "paid ",
                   "from ", " to "):
        if marker in low:
            idx = low.index(marker) + len(marker)
            rest = text[idx:]
            m = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+and\s+"
                         r"|\s+in\s+|\s+₹|\s+Rs|\s+\d|,|$)", rest)
            if m:
                party = m.group(1).strip().rstrip(".;,")
                if party and not party.lower().endswith(
                        ("a/c", "account", "ltd", "limited")):
                    return party
    return None


def _resolve_cash_bank(text: str) -> str:
    low = " " + str(text or "").lower() + " "
    if "bank" in low or "cheque" in low or "check" in low:
        return "Bank"
    return "Cash"


def _resolve_side_specs(specs: List[Any], text: str,
                        party_kind: str) -> List[str]:
    """Resolve a pattern's account-spec list to concrete accounts.

    A [Cash, Bank] either/or collapses to the ONE side the description
    names (bank/cheque -> Bank, otherwise Cash) - it never posts to both.
    """
    resolved: List[str] = []
    for spec in specs:
        account = _resolve_bk_spec(spec, text, party_kind)
        if not account:
            continue
        if account in resolved:
            continue
        resolved.append(account)
    if "Cash" in resolved and "Bank" in resolved:
        keep = _resolve_cash_bank(text)
        return [a for a in resolved if a == keep]
    return resolved


def classify_bk_type(question: str) -> Optional[Dict[str, Any]]:
    """The canonical transaction type for a description (first match wins),
    with its resolved debit/credit account specs. None when unrecognised.
    Fixed-asset purchases/sales are detected BEFORE the goods patterns so
    the exact asset named is used (never an invented sibling account)."""
    text = str(question or "").strip()
    if not text:
        return None
    # fixed-asset rules first (Sprint 15B exact-account guarantee)
    for rule in (classify_transaction,):
        # asset purchases/sales are handled by the accounting engine's
        # dedicated rules; reuse their account resolution via our own table.
        break
    low = " " + text.lower() + " "
    # asset purchase / sale (exact asset only)
    assets = named_assets(text)
    if assets:
        if any(k in low for k in ("purchas", "bought")):
            return _asset_pattern(text, assets, purchase=True)
        if any(k in low for k in ("sold", "sale of")):
            return _asset_pattern(text, assets, purchase=False)
    # 'for cash' decides the MODE even when a party is named
    # ('Sold goods to Mohan for cash', 'Purchased goods from Amit for
    # cash'). A named party is just the counterparty - the settlement
    # mode comes from the words, so a party never flips a cash
    # transaction into a credit one. Contradictory 'cash ... on credit'
    # wording is ambiguous and stays with the refusal layer below.
    has_cash_mode = ("for cash" in low or "paid cash" in low
                     or re.search(r"\bcash\b", low))
    has_credit_mode = ("credit" in low or "on account" in low)
    goods_purchase_words = (
        "purchased goods", "bought goods", "goods purchased",
        "goods bought", "purchased stock", "bought stock",
        "stock purchased", "stock bought",
    )
    goods_sale_words = (
        "sold goods", "goods sold", "sold stock", "stock sold",
        "sold goods to", "goods sold to",
    )
    if has_cash_mode and not has_credit_mode:
        if any(k in low for k in goods_purchase_words):
            return {
                "key": "PURCHASE_GOODS_CASH",
                "label": "Goods purchased for cash",
                "debit": ["Purchases"], "credit": ["Cash", "Bank"],
            }
        if any(k in low for k in goods_sale_words):
            return {
                "key": "SALE_GOODS_CASH",
                "label": "Goods sold for cash",
                "debit": ["Cash", "Bank"], "credit": ["Sales"],
            }
    # 'interest on drawings' is its own transaction - it must NEVER be
    # routed as a cash withdrawal by the bare 'drawings' phrase below.
    if "interest on drawings" in low:
        return {
            "key": "INTEREST_ON_DRAWINGS",
            "label": "Interest on drawings charged",
            "debit": ["Drawings"], "credit": ["Interest on Drawings"],
        }
    # 'goods ... for personal use' debits Drawings and credits PURCHASES
    # (the goods, not cash) - the cash-withdrawal pattern's generic
    # 'for personal use' phrase must not shadow it.
    if "goods" in low and ("personal use" in low or "private use" in low):
        return {
            "key": "GOODS_PERSONAL_USE",
            "label": "Goods taken for personal use",
            "debit": ["Drawings"], "credit": ["Purchases"],
        }
    for cand in BK_PATTERNS:
        when = cand["when"]
        phrases = when if isinstance(when, (tuple, list)) else (when,)
        if any(phrase in low for phrase in phrases):
            return dict(cand)
        # Sprint 15C P0 fallbacks: the amount often sits BETWEEN the verb
        # and the party ('Paid Rahul Rs.8,000 in cash', 'Paid Rs.8,000 to
        # Rahul', 'Received Rs.6,000 from Amit in cash'), so the
        # contiguous 'paid to'/'received from' phrases cannot match.
        # Deterministic - expense/income/cheque patterns are checked
        # BEFORE these keys, and a cheque wording never collapses into a
        # plain cash payment.
        if cand["key"] == "PAID_TO" and re.search(r"\bpaid\b", low) \
                and "cheque" not in low and "check" not in low \
                and (re.search(r"\bpaid\s+[a-z]", low)
                     or re.search(r"\bpaid\b.*\bto\b", low)):
            return dict(cand)
        if cand["key"] == "RECEIVED_FROM" and "received" in low \
                and "from" in low and "cheque" not in low \
                and "check" not in low:
            return dict(cand)
    return None


def _asset_pattern(text: str, assets: List[str],
                   purchase: bool) -> Dict[str, Any]:
    """An asset transaction pattern with EXACTLY the named asset."""
    if len(assets) > 1:
        return {
            "key": "ASSET_AMBIGUOUS",
            "label": "Ambiguous fixed-asset transaction",
            "refuse": True,
            "debit": [], "credit": [],
            "why": (f"The description names more than one fixed asset "
                    f"({', '.join(assets)}). FT-E never guesses the split."),
        }
    asset = assets[0]
    low = " " + text.lower() + " "
    if purchase:
        if "for cash" in low or "cash purchase" in low \
                or re.search(r"\bcash\b", low):
            return {
                "key": "PURCHASE_ASSET_CASH", "label": f"{asset} purchased "
                "for cash", "debit": [asset], "credit": ["Cash", "Bank"],
            }
        if "on credit" in low or "credit purchase" in low \
                or re.search(r"\bfrom\b", low):
            return {
                "key": "PURCHASE_ASSET_CREDIT", "label": f"{asset} purchased "
                "on credit", "debit": [asset],
                "credit": [{"party": "giver"}],
            }
        return {
            "key": "ASSET_PURCHASE_AMBIGUOUS", "label": f"{asset} purchased",
            "refuse": True, "debit": [], "credit": [],
            "why": (f"{asset} purchased - state whether the purchase was for "
                    "cash or on credit."),
        }
    if "for cash" in low or "cash sale" in low \
            or re.search(r"\bcash\b", low):
        return {
            "key": "SALE_ASSET_CASH", "label": f"{asset} sold for cash",
            "debit": ["Cash", "Bank"], "credit": [asset],
        }
    if "on credit" in low or "credit sale" in low or re.search(r"\bto\b", low):
        return {
            "key": "SALE_ASSET_CREDIT", "label": f"{asset} sold on credit",
            "debit": [{"party": "receiver"}], "credit": [asset],
        }
    return {
        "key": "ASSET_SALE_AMBIGUOUS", "label": f"{asset} sold",
        "refuse": True, "debit": [], "credit": [],
        "why": (f"{asset} sold - state whether the sale was for cash or on "
                "credit."),
    }


# ---------------------------------------------------------------------------
# Discount / partial-payment pipeline (section 7)
# ---------------------------------------------------------------------------
# Chronological order: Total/List Price -> Trade Discount -> Net Transaction
# Value -> paid vs credit split -> Cash Discount (paid portion only) ->
# final journal/ledger values. Every numeric step is TRACED with a
# calculation_id, formula text, inputs and result - never silent, never
# claimed as C++ (registered metrics still go through C++ via the maths
# authority; posting arithmetic is verification/preparation arithmetic).


def _detect_explicit_discount(question: str,
                              amounts: List[Decimal])\
        -> Optional[Dict[str, Any]]:
    """An explicit discount AMOUNT in the description (not a % rate).

    Standard FYJC wording - 'Received from Mohan Rs.9,800, discount
    allowed Rs.200' - gives the CASH amount and the DISCOUNT amount; the
    party account is their SUM. Two-amount form: (cash, discount).
    Three-amount form (debt stated explicitly): (party_total, cash,
    discount). Returns {kind, party_total, cash_amount, discount_amount}
    or None.
    """
    low = " " + str(question or "").lower() + " "
    # a TRADE discount amount reduces the list price; it is not a cash
    # discount and is handled by the trade-discount pipeline above.
    if "trade discount" in low:
        return None
    allowed = ("discount allowed" in low or "allowed discount" in low
               or "discount given" in low or "allowed him" in low
               or "allowed her" in low
               or bool(re.search(r"allowed\s+[A-Za-z][A-Za-z' ]{0,40}?\s+discount",
                                 low)))
    received = "discount received" in low or "received discount" in low
    if not (allowed or received) or len(amounts) < 2:
        return None
    kind = "allowed" if allowed else "received"
    if len(amounts) == 2:
        cash_amount = amounts[0]
        discount_amount = amounts[1]
        party_total = cash_amount + discount_amount
    else:
        party_total = amounts[0]
        cash_amount = amounts[1]
        discount_amount = amounts[2]
    return {
        "kind": kind, "party_total": party_total,
        "cash_amount": cash_amount, "discount_amount": discount_amount,
    }


def resolve_transaction_amounts(question: str) -> Dict[str, Any]:
    """The discount/payment pipeline for one transaction description.

    Returns {status, steps, list_price, trade_discount_rate,
    trade_discount_amount, net_value, paid_amount, credit_amount,
    cash_discount_rate, cash_discount_amount, cash_paid, concerns}.
    """
    steps: List[Dict[str, Any]] = []
    low = " " + str(question or "").lower() + " "
    amounts, ambiguous = _extract_amounts(question)
    percents = _extract_percents(question)

    concerns: List[str] = []
    if ambiguous:
        concerns.append("An amount could not be read cleanly (OCR-style "
                        "uncertainty). FT-E never silently corrects it.")

    # -- Total / List price ----------------------------------------------
    if not amounts:
        return {
            "status": BLOCKED, "steps": [], "list_price": None,
            "trade_discount_rate": None, "trade_discount_amount": None,
            "net_value": None, "paid_amount": None, "credit_amount": None,
            "cash_discount_rate": None, "cash_discount_amount": None,
            "cash_paid": None, "concerns": concerns,
            "why_not": "The transaction amount is missing.",
            "next_action": "Enter the amount of the transaction.",
        }
    list_price = amounts[0]
    steps.append({
        "calculation_id": "BK_LIST_PRICE",
        "label": "Total / List Price",
        "formula": "List price from the question",
        "inputs": {"list_price": list_price},
        "result": list_price,
    })

    # -- Trade discount ---------------------------------------------------
    # A '<n>%' token whose surrounding text says 'trade' (or says
    # 'discount' without saying 'cash') is a TRADE discount - it is
    # deducted from the list price BEFORE the amount is posted.
    trade_rate: Optional[Decimal] = None
    for rate, label in percents:
        if "trade" in label:
            trade_rate = rate
            break
    if trade_rate is None:
        for rate, label in percents:
            if "discount" in label and "cash discount" not in label:
                trade_rate = rate
                break
    trade_amount = None
    if trade_rate is not None:
        trade_amount = (list_price * trade_rate / Decimal(100)).quantize(
            Decimal("0.01"))
        steps.append({
            "calculation_id": "BK_TRADE_DISCOUNT_AMOUNT",
            "label": "Deduct Trade Discount",
            "formula": "Trade discount = List price x Trade discount %",
            "inputs": {"list_price": list_price,
                       "trade_discount_rate": trade_rate},
            "result": trade_amount,
        })
    net_value = list_price - trade_amount if trade_amount is not None \
        else list_price
    steps.append({
        "calculation_id": "BK_NET_TRANSACTION_VALUE",
        "label": "Net Transaction Value",
        "formula": "Net = List price - Trade discount",
        "inputs": {"list_price": list_price, "trade_discount": trade_amount},
        "result": net_value,
    })

    # An explicit discount AMOUNT (Rs.9,800 paid, discount allowed
    # Rs.200) describes a full settlement - the naive paid/credit split
    # below must NOT run (it would misread the discount figure as a
    # partial payment). The settlement numbers come from the explicit
    # discount rows instead.
    explicit_discount = _detect_explicit_discount(question, amounts)

    # -- paid vs credit split --------------------------------------------
    paid_amount: Optional[Decimal] = None
    credit_amount: Optional[Decimal] = None
    if explicit_discount is None:
        explicit_paid = None
        # an explicit paid amount ('paid Rs.4,000 immediately')
        if len(amounts) >= 2 and ("paid" in low or "immediately" in low):
            explicit_paid = amounts[-1]
        fraction = _paid_fraction(question)
        if explicit_paid is not None and explicit_paid < net_value:
            paid_amount = explicit_paid
        elif fraction is not None:
            paid_amount = (net_value * fraction / Decimal(100)).quantize(
                Decimal("0.01"))
        elif "on credit" not in low and "credit" not in low:
            paid_amount = net_value
        if paid_amount is not None:
            credit_amount = net_value - paid_amount
            steps.append({
                "calculation_id": "BK_PAID_CREDIT_SPLIT",
                "label": "Split paid vs credit portion",
                "formula": "Credit = Net - Paid",
                "inputs": {"net_value": net_value,
                           "paid_amount": paid_amount},
                "result": {"paid": paid_amount, "credit": credit_amount},
            })

    # -- Cash discount (paid portion only) --------------------------------
    cash_discount_rate: Optional[Decimal] = None
    # ONLY a literal 'cash discount' phrase is a cash discount. A trade
    # discount (or a plain 'discount' that is not cash) only nets the list
    # price and is never recorded as a cash discount - so 'for cash ... with
    # 10% trade discount' must NOT produce a cash-discount line.
    for rate, label in percents:
        if "cash discount" in label:
            cash_discount_rate = rate
            break
    cash_discount_amount = None
    cash_paid = paid_amount
    if cash_discount_rate is not None and paid_amount is not None:
        cash_discount_amount = (paid_amount * cash_discount_rate
                                / Decimal(100)).quantize(Decimal("0.01"))
        cash_paid = paid_amount - cash_discount_amount
        steps.append({
            "calculation_id": "BK_CASH_DISCOUNT_AMOUNT",
            "label": "Apply Cash Discount (paid portion only)",
            "formula": "Cash discount = Paid x Cash discount %",
            "inputs": {"paid_amount": paid_amount,
                       "cash_discount_rate": cash_discount_rate},
            "result": cash_discount_amount,
        })
        steps.append({
            "calculation_id": "BK_CASH_PAID_NET",
            "label": "Net cash paid",
            "formula": "Cash paid = Paid - Cash discount",
            "inputs": {"paid_amount": paid_amount,
                       "cash_discount": cash_discount_amount},
            "result": cash_paid,
        })

    status = VERIFIED
    why_not = None
    next_action = "Post this entry in your journal and verify it."
    if concerns:
        status = REVIEW_REQUIRED
        why_not = "; ".join(concerns)
        next_action = "Re-enter the amount clearly (e.g. 5,000)."

    # explicit discount AMOUNT (Rs.9,800 paid, discount allowed Rs.200)
    if explicit_discount is not None:
        steps.append({
            "calculation_id": "BK_EXPLICIT_DISCOUNT",
            "label": "Explicit discount amount",
            "formula": "Party account = Cash paid + Discount amount",
            "inputs": {"cash": explicit_discount["cash_amount"],
                       "discount": explicit_discount["discount_amount"]},
            "result": explicit_discount["party_total"],
        })

    return {
        "status": status, "steps": steps, "list_price": list_price,
        "trade_discount_rate": trade_rate,
        "trade_discount_amount": trade_amount, "net_value": net_value,
        "paid_amount": paid_amount, "credit_amount": credit_amount,
        "cash_discount_rate": cash_discount_rate,
        "cash_discount_amount": cash_discount_amount, "cash_paid": cash_paid,
        "explicit_discount": explicit_discount,
        "concerns": concerns, "why_not": why_not,
        "next_action": next_action,
    }


# Topics outside the supported FYJC boundary -> NOT_SUPPORTED (never guess).
_NOT_SUPPORTED_HINTS = (
    "depreciation", "provision for doubtful", "goodwill",
    "consignment", "joint venture", "partnership", "dissolution",
    "hire purchase", "leasing", "royalty", "branch account",
    "departmental account", "single entry", "incomplete records",
    "insurance claim", "claim for loss", "amalgamation",
    "revaluation", "bonus shares", "right issue", "redemption of "
    "debenture", "issue of shares", "share capital", "final accounts",
    "balance sheet", "profit and loss account", "trading account",
    "adjusted trial balance", "opening entry",
)

# Wording that is ambiguous about cash vs credit (never assumed).
_AMBIGUOUS_HINTS = (
    "purchased goods for rs.", "sold goods for rs.", "purchased for rs.",
    "sold for rs.", "bought goods for rs.", "paid to",
    "received from", "on account",
    # a bare goods transaction without a cash/credit word is ambiguous -
    # FT-E never assumes one.
    "purchased goods", "bought goods", "goods purchased",
    "goods bought", "sold goods", "goods sold",
    "purchased stock", "sold stock",
)


# ---------------------------------------------------------------------------
# Journal entry generation (section 4)
# ---------------------------------------------------------------------------


def _split_transactions(question: str) -> List[str]:
    """Split a multi-transaction description on ';' or sentence
    boundaries ('. ' between a digit/lower-case letter and a Capital
    letter). Deterministic - abbreviations like 'Rs.9,800' or '& Co. for
    cash' are never split, and every resulting segment is a standalone
    transaction sentence ('Started business with cash Rs.1,00,000."
    "Purchased goods for cash Rs.20,000. Paid rent Rs.5,000.' becomes
    three independent segments).

    Sprint 15C: a segment that is only a PAYMENT/DISCOUNT step of the
    PREVIOUS transaction ('paid him Rs.4,000', 'Half the amount was paid
    immediately with a 2% cash discount') is merged back into the
    previous segment so the whole transaction resolves through the
    registered discount pipeline as ONE journal - it is NEVER posted as
    an independent entry. A step is merged only when it (a) is a payment
    step, (b) uses 'paid' wording and (c) follows a PURCHASE, so a
    'Received from <debtor>' settlement of a credit sale stays its own
    entry."""
    raw = re.split(r";\s*", str(question or ""))
    pieces: List[str] = []
    for part in raw:
        pieces.extend(
            re.split(r"(?<=[a-z0-9)])\.\s+(?=[A-Z])", part))
    segments = [seg.strip() for seg in pieces if seg.strip()]
    merged: List[str] = []
    for seg in segments:
        prior = merged[-1] if merged else None
        prior_pattern = classify_bk_type(prior) if prior else None
        is_purchase_prior = bool(prior_pattern) and \
            "PURCHASE" in prior_pattern["key"]
        low_seg = " " + seg.lower() + " "
        if prior and _is_payment_step(seg) and is_purchase_prior \
                and " paid " in low_seg:
            merged[-1] = merged[-1] + "; " + seg
        else:
            merged.append(seg)
    return merged


_PRONOUN_RE = re.compile(r"\b(him|her|them)\b")


def _resolve_pronouns(segment: str, prior_party: Optional[str]) -> str:
    """Substitute a following him/her/them with the previously named
    party. Deterministic - never resolves to an invented name."""
    if prior_party and _PRONOUN_RE.search(segment):
        return _PRONOUN_RE.sub(prior_party, segment)
    return segment


def _startup_asset_breakdown(text: str) -> Optional[Dict[str, Any]]:
    """'Started business with cash Rs.50,000 and furniture Rs.20,000' ->
    {cash, assets: {Furniture: 20000}, total: 70000}. Each named asset
    takes the amount adjacent to its name; the cash amount follows the
    word 'cash'. None when the amounts cannot be read deterministically
    (then the question is refused, never guessed)."""
    low = " " + str(text or "").lower() + " "
    if not any(k in low for k in ("started business", "commenced business")):
        return None
    named = named_assets(text)
    if not named:
        return None
    breakdown: Dict[str, Decimal] = {}
    for asset in named:
        m = re.search(
            re.escape(asset) + r"\s+(?:for\s+)?(?:Rs\.?|₹|INR)?\s*"
            r"(\d[\d,]*(?:\.\d+)?)", text, re.IGNORECASE)
        if not m:
            return None
        parsed = parse_numeric_text(m.group(1).replace(",", ""))
        if parsed.value is None:
            return None
        breakdown[asset] = parsed.value
    m_cash = re.search(
        r"\bcash\b\s+(?:Rs\.?|₹|INR)?\s*(\d[\d,]*(?:\.\d+)?)",
        text, re.IGNORECASE)
    if not m_cash:
        return None
    parsed_cash = parse_numeric_text(m_cash.group(1).replace(",", ""))
    if parsed_cash.value is None:
        return None
    cash_amt = parsed_cash.value
    total = cash_amt + sum(breakdown.values(), Decimal(0))
    return {"cash": cash_amt, "assets": breakdown, "total": total}


def generate_journal(question: str) -> Dict[str, Any]:
    """The deterministic journal entry for ONE transaction description.

    Returns {date, particulars, debit_lines, credit_lines, narration,
    status, why_not, next_action, calculation_records, total_debit,
    total_credit, balanced}. Debit/credit lines carry the traditional
    class, the Golden Rule and the per-side WHY.
    """
    text = str(question or "").strip()
    if not text:
        return {
            "status": BLOCKED, "why_not": "No transaction was provided.",
            "next_action": "Type or photograph the transaction description.",
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": [], "total_debit": 0,
            "total_credit": 0, "balanced": True,
        }

    # A discount is NEVER a standalone journal entry - it only exists as
    # part of a settlement (payment/receipt with a named party or an
    # explicit cash amount). 'Discount received Rs.200' alone must NOT
    # post a confident 'Cash A/c Dr / Discount Received A/c Cr' entry
    # (the cash side would be invented). REVIEW_REQUIRED instead.
    low_check = " " + text.lower() + " "
    if "discount" in low_check:
        _has_settlement_context = (
            ("from " in low_check) or (" to " in low_check)
            or ("him" in low_check) or ("her" in low_check)
            or ("them" in low_check) or ("settlement" in low_check)
            or ("cheque" in low_check) or ("check " in low_check)
            or ("paid " in low_check)
            or ("% " in low_check)
            or ("received " in low_check
                and "discount received" not in low_check)
        )
        if not _has_settlement_context:
            return {
                "status": REVIEW_REQUIRED,
                "why_not": ("A discount is never a standalone journal entry; "
                            "it always accompanies a settlement with a named "
                            "party or a payment amount. FT-E never invents "
                            "the cash side of a discount."),
                "next_action": ("Add the settlement, e.g. 'Paid to Amit "
                                "Rs.9,800, discount received Rs.200' or "
                                "'Received from Mohan Rs.9,800, discount "
                                "allowed Rs.200'."),
                "debit_lines": [], "credit_lines": [], "narration": None,
                "calculation_records": [], "total_debit": 0,
                "total_credit": 0, "balanced": True,
            }

    # full-settlement wording implies a discount that is NOT stated
    # ('received Rs.5,000 in full settlement of Rs.5,200') - FT-E will
    # not silently invent the Rs.200 discount.
    low_check = " " + text.lower() + " "
    if "full settlement" in low_check and "discount" not in low_check:
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("'Full settlement' wording implies a discount, but "
                        "no discount amount is stated. FT-E never invents "
                        "the difference."),
            "next_action": "State the discount amount explicitly (e.g. "
                           "'discount allowed Rs.200').",
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": [], "total_debit": 0,
            "total_credit": 0, "balanced": True,
        }

    pattern = classify_bk_type(text)
    if pattern is None:
        low_text = " " + text.lower() + " "
        for hint in _AMBIGUOUS_HINTS:
            if hint in low_text:
                return {
                    "status": REVIEW_REQUIRED,
                    "why_not": ("The transaction does not say whether it "
                                "was for cash or on credit. FT-E never "
                                "assumes one."),
                    "next_action": ("Add 'for cash' or 'on credit from "
                                    "<name>' to the description."),
                    "debit_lines": [], "credit_lines": [],
                    "narration": None, "calculation_records": [],
                    "total_debit": 0, "total_credit": 0, "balanced": True,
                }
        return {
            "status": NOT_SUPPORTED,
            "why_not": ("This transaction is outside the currently supported "
                        "FYJC Book-Keeping syllabus boundary. FT-E does not "
                        "guess an accounting treatment."),
            "next_action": ("Use standard FYJC wording - e.g. 'Purchased "
                            "goods for cash', 'Sold goods on credit to X', "
                            "'Paid rent', 'Started business with cash'."),
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": [], "total_debit": 0,
            "total_credit": 0, "balanced": True,
        }
    if pattern.get("refuse"):
        return {
            "status": REVIEW_REQUIRED,
            "why_not": pattern.get("why") or pattern.get("label", ""),
            "next_action": "Add the missing detail (cash/credit, amount, "
                           "or the exact asset).",
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": [], "total_debit": 0,
            "total_credit": 0, "balanced": True,
        }

    amounts = resolve_transaction_amounts(text)
    if amounts["status"] != VERIFIED:
        return {
            "status": amounts["status"],
            "why_not": amounts.get("why_not"),
            "next_action": amounts.get("next_action"),
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": amounts.get("steps") or [],
            "total_debit": 0, "total_credit": 0, "balanced": True,
        }

    net = amounts["net_value"]
    paid = amounts.get("paid_amount")
    credit_portion = amounts.get("credit_amount")
    cash_discount = amounts.get("cash_discount_amount")
    cash_paid = amounts.get("cash_paid")

    debit_specs = pattern["debit"]
    credit_specs = pattern["credit"]
    cash_or_bank = _resolve_cash_bank(text)

    debit_lines: List[Dict[str, Any]] = []
    credit_lines: List[Dict[str, Any]] = []

    def _line(account: str, amount: Decimal, side: str) -> Dict[str, Any]:
        cls = traditional_class_for(account)
        return {
            "account": account,
            "class": cls,
            "rule": TRADITIONAL_GOLDEN_RULES[cls],
            "why": side_decision_for(account, side),
            "amount": amount,
            "side": side,
        }

    # --- journal shape ---------------------------------------------------
    # A real split (paid < net with a credit remainder, or a cash
    # discount) produces a COMPOUND journal. A fully-cash or fully-credit
    # transaction (credit_portion is 0/None) stays a SIMPLE entry.
    purchase = any(k in pattern["key"] for k in
                   ("PURCHASE", "START_BUSINESS", "CAPITAL_INTRODUCED",
                    "EXPENSE", "INTEREST_ON_CAPITAL"))
    sale = "SALE" in pattern["key"]
    split = (credit_portion is not None and credit_portion > 0) \
        or (cash_discount is not None and cash_discount > 0)

    # --- explicit discount AMOUNT (section 7) -----------------------------
    # 'Received from Mohan Rs.9,800, discount allowed Rs.200' -> Cash A/c
    # Dr 9,800; Discount Allowed A/c Dr 200; To Mohan A/c 10,000 (the
    # SUM). 'Paid to Amit Rs.9,800, discount received Rs.200' -> Amit A/c
    # Dr 10,000; By Cash 9,800; By Discount Received 200.
    explicit = amounts.get("explicit_discount")
    if explicit is not None and sale:
        # 'Sold goods to Mohan Rs.15,000, discount allowed Rs.200' without
        # a payment step is ambiguous (discount at sale time vs at
        # settlement) - FT-E never picks one silently.
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("The sale carries an explicit discount amount but "
                        "does not say whether the discount is deducted at "
                        "sale time or allowed at settlement. FT-E never "
                        "guesses which."),
            "next_action": "Split it into two steps: the credit sale, "
                           "then 'Received ... discount allowed ...'.",
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": amounts.get("steps") or [],
            "total_debit": 0, "total_credit": 0, "balanced": True,
        }
    if explicit is not None:
        cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
        if explicit["kind"] == "allowed":
            party = (_resolve_bk_spec({"party": "giver"}, text, "giver")
                     or _resolve_bk_spec({"party": "receiver"}, text,
                                         "receiver"))
            if party:
                debit_lines.append(_line(cash_acct, explicit["cash_amount"],
                                         "debit"))
                debit_lines.append(_line("Discount Allowed",
                                         explicit["discount_amount"],
                                         "debit"))
                credit_lines.append(_line(party, explicit["party_total"],
                                          "credit"))
        else:
            party = (_resolve_bk_spec({"party": "receiver"}, text,
                                     "receiver")
                     or _resolve_bk_spec({"party": "giver"}, text, "giver"))
            if party:
                debit_lines.append(_line(party, explicit["party_total"],
                                         "debit"))
                credit_lines.append(_line(cash_acct, explicit["cash_amount"],
                                          "credit"))
                credit_lines.append(_line("Discount Received",
                                          explicit["discount_amount"],
                                          "credit"))

    # --- started business with cash + assets -----------------------------
    # 'Started business with cash Rs.50,000 and furniture Rs.20,000' ->
    # Cash Dr 50,000; Furniture Dr 20,000; To Capital Cr 70,000.
    if not debit_lines and not credit_lines \
            and pattern["key"] in ("START_BUSINESS", "CAPITAL_INTRODUCED"):
        startup = _startup_asset_breakdown(text)
        if startup is not None:
            debit_lines.append(_line("Cash", startup["cash"], "debit"))
            for asset, amount in startup["assets"].items():
                debit_lines.append(_line(asset, amount, "debit"))
            credit_lines.append(_line("Capital", startup["total"],
                                      "credit"))

    # --- debit side ------------------------------------------------------
    if not debit_lines and not credit_lines:
        if sale and split:
            # split sale: debtor Dr credit portion + cash Dr paid portion
            if credit_portion and credit_portion > 0:
                party = _resolve_bk_spec({"party": "receiver"}, text,
                                         "receiver")
                if party:
                    debit_lines.append(_line(party, credit_portion, "debit"))
            cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
            if cash_paid is not None and cash_paid > 0:
                debit_lines.append(_line(cash_acct, cash_paid, "debit"))
            if cash_discount is not None and cash_discount > 0:
                debit_lines.append(_line("Discount Allowed", cash_discount,
                                         "debit"))
        else:
            for account in _resolve_side_specs(debit_specs, text,
                                               "receiver"):
                debit_lines.append(_line(account, net, "debit"))

        # --- credit side -------------------------------------------------
        if sale:
            for account in _resolve_side_specs(credit_specs, text, "giver"):
                credit_lines.append(_line(account, net, "credit"))
        elif split:
            cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
            if cash_paid is not None and cash_paid > 0:
                credit_lines.append(_line(cash_acct, cash_paid, "credit"))
            if cash_discount is not None and cash_discount > 0:
                credit_lines.append(_line("Discount Received", cash_discount,
                                          "credit"))
            if credit_portion is not None and credit_portion > 0:
                party = _resolve_bk_spec({"party": "giver"}, text, "giver")
                if party:
                    credit_lines.append(_line(party, credit_portion,
                                              "credit"))
                else:
                    credit_lines.append(_line("Creditors", credit_portion,
                                              "credit"))
        else:
            for account in _resolve_side_specs(credit_specs, text, "giver"):
                credit_lines.append(_line(account, net, "credit"))

    # fall back to the Sprint 13 golden-rule engine when our IR produced no
    # usable lines for a recognised pattern (should not happen for the
    # supported surface - defensive, still deterministic).
    if not debit_lines or not credit_lines:
        legacy = classify_transaction(text)
        if legacy.get("status") == VERIFIED and legacy.get("debit_lines") \
                and legacy.get("credit_lines"):
            debit_lines = [
                _line(l.get("account"), net, "debit")
                for l in legacy["debit_lines"] if l.get("account")]
            credit_lines = [
                _line(l.get("account"), net, "credit")
                for l in legacy["credit_lines"] if l.get("account")]

    if not debit_lines or not credit_lines:
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("The accounts for this transaction could not be "
                        "fully determined (a party name may be missing). "
                        "FT-E never invents a person's name."),
            "next_action": "Re-type the transaction with the person's name.",
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": amounts.get("steps") or [],
            "total_debit": 0, "total_credit": 0, "balanced": True,
        }

    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))
    balanced = total_debit == total_credit

    narration_parts: List[str] = []
    for line in debit_lines:
        narration_parts.append(f"{line['account']} A/c Dr "
                               f"{_fmt_amt(line['amount'])}")
    for line in credit_lines:
        narration_parts.append(f"To {line['account']} A/c "
                               f"{_fmt_amt(line['amount'])}")
    if amounts.get("trade_discount_rate") is not None:
        narration_parts.append(f"(with {amounts['trade_discount_rate']}% "
                               "trade discount)")
    if amounts.get("cash_discount_rate") is not None:
        narration_parts.append(f"(with {amounts['cash_discount_rate']}% cash "
                               "discount)")
    narration = "Being " + "; ".join(narration_parts) + "."

    return {
        "status": VERIFIED,
        "date": None,
        "particulars": " / ".join(l["account"] for l in debit_lines)
                       + " A/c Dr",
        "debit_lines": debit_lines,
        "credit_lines": credit_lines,
        "narration": narration,
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
        "calculation_records": amounts.get("steps") or [],
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": balanced,
    }


def _fmt_amt(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


# ---------------------------------------------------------------------------
# Ledger + Trial Balance reasoning (sections 5-6) - DERIVED from journal IR
# ---------------------------------------------------------------------------


def journal_to_entries(journal: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Shape a journal dict into the standard entry list consumed by
    post_ledger / build_trial_balance. The ledger NEVER re-interprets the
    transaction - it is derived from this journal IR only."""
    return [{
        "debits": [
            {"account": line["account"], "amount": line["amount"]}
            for line in journal.get("debit_lines") or []
        ],
        "credits": [
            {"account": line["account"], "amount": line["amount"]}
            for line in journal.get("credit_lines") or []
        ],
    }]


def generate_ledger(journals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ledger effects from the journal IR (deterministic Decimal sums)."""
    entries: List[Dict[str, Any]] = []
    for journal in journals or []:
        if journal.get("status") == VERIFIED:
            entries.extend(journal_to_entries(journal))
    return post_ledger(entries)


def generate_trial_balance(journals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Trial-balance effects from the ledger state. If Dr != Cr the exact
    discrepancy is exposed - it is NEVER forced into balance."""
    entries: List[Dict[str, Any]] = []
    for journal in journals or []:
        if journal.get("status") == VERIFIED:
            entries.extend(journal_to_entries(journal))
    return build_trial_balance(entries)


# ---------------------------------------------------------------------------
# Student-facing understanding (section 10)
# ---------------------------------------------------------------------------


def build_bk_understanding(question: str) -> Dict[str, Any]:
    """What FT-E understood for a Book-Keeping question:
    question type, accounts identified, amounts identified, requested
    operation. Deterministic; never claims OCR success."""
    text = str(question or "").strip()
    pattern = classify_bk_type(text) if text else None
    amounts = resolve_transaction_amounts(text) if text else {}

    debit_accounts: List[str] = []
    credit_accounts: List[str] = []
    if pattern and not pattern.get("refuse"):
        debit_accounts = _resolve_side_specs(
            pattern.get("debit") or [], text, "receiver")
        credit_accounts = _resolve_side_specs(
            pattern.get("credit") or [], text, "giver")

    requested = _requested_operation(text)

    amount_rows: List[Dict[str, Any]] = []
    if amounts.get("list_price") is not None:
        amount_rows.append({
            "role": "List / Gross amount",
            "value": _fmt_amt(amounts["list_price"]),
        })
    if amounts.get("trade_discount_rate") is not None:
        amount_rows.append({
            "role": "Trade discount",
            "value": f"{amounts['trade_discount_rate']}%",
        })
    if amounts.get("net_value") is not None:
        amount_rows.append({
            "role": "Net amount",
            "value": _fmt_amt(amounts["net_value"]),
        })
    if amounts.get("paid_amount") is not None:
        amount_rows.append({
            "role": "Paid immediately",
            "value": _fmt_amt(amounts["paid_amount"]),
        })
    if amounts.get("credit_amount") is not None:
        amount_rows.append({
            "role": "On credit",
            "value": _fmt_amt(amounts["credit_amount"]),
        })
    if amounts.get("cash_discount_rate") is not None:
        amount_rows.append({
            "role": "Cash discount",
            "value": f"{amounts['cash_discount_rate']}%",
        })
    explicit = amounts.get("explicit_discount")
    if explicit is not None:
        amount_rows.append({
            "role": "Cash received / paid",
            "value": _fmt_amt(explicit["cash_amount"]),
        })
        amount_rows.append({
            "role": "Discount amount",
            "value": _fmt_amt(explicit["discount_amount"]),
        })
        amount_rows.append({
            "role": "Party account total",
            "value": _fmt_amt(explicit["party_total"]),
        })

    interpretation = (
        f"FT-E reads this as **{pattern['label']}** (Book-Keeping & "
        f"Accountancy). Accounts identified: "
        f"{', '.join(debit_accounts + credit_accounts) or 'none yet'}. "
        f"Requested operation: {requested}."
        if pattern else
        "FT-E could not reliably identify the transaction type. It will "
        "not guess - type the transaction in standard FYJC wording."
    )

    return {
        "question_type": pattern["label"] if pattern else None,
        "question_type_key": pattern["key"] if pattern else None,
        "accounts_identified": {
            "debit": debit_accounts, "credit": credit_accounts,
            "all": debit_accounts + credit_accounts,
        },
        "amounts_identified": amount_rows,
        "requested_operation": requested,
        "interpretation": interpretation,
        "status": (
            VERIFIED if pattern and not pattern.get("refuse")
            and amounts.get("status") == VERIFIED else
            (amounts.get("status") if amounts else REVIEW_REQUIRED)
        ),
        "concerns": amounts.get("concerns") or [],
    }


def _requested_operation(text: str) -> str:
    low = " " + str(text or "").lower() + " "
    if "journal" in low:
        return "Journal Entry"
    if "ledger" in low or "post the following" in low or "posting" in low:
        return "Ledger Posting"
    if "trial balance" in low:
        return "Trial Balance"
    return "Transaction Analysis (debit/credit)"


# ---------------------------------------------------------------------------
# Refusal helpers (section 9)
# ---------------------------------------------------------------------------


def _refusal(status: str, why_not: str, next_action: str) -> Dict[str, Any]:
    return {
        "status": status,
        "status_label": STATUS_WORDS.get(status, status)
        if status in STATUS_WORDS else
        ("🟡 NOT SUPPORTED" if status == NOT_SUPPORTED else status),
        "resolved": False,
        "why_not": why_not,
        "next_action": next_action,
        "journal": None, "ledger": None, "trial_balance": None,
        "debit_lines": [], "credit_lines": [],
        "calculation_records": [],
    }


# ---------------------------------------------------------------------------
# Multi-transaction reasoning (section 1 - multiple transactions in ONE
# question). Each ';'-separated segment is journaled independently; every
# segment must resolve or the WHOLE question refuses (never a partial
# confident answer). Pronouns (him/her/them) resolve to the party named
# by the previous segment - deterministically, never invented. A
# ';'-segment that is a PAYMENT/DISCOUNT step of the previous transaction
# ('paid half immediately with 2% cash discount', 'paid him Rs.4,000') is
# folded into the previous journal through the full discount pipeline -
# it is NEVER posted as an independent entry. The resolved journals are
# then combined into ONE ledger and ONE trial balance.
# ---------------------------------------------------------------------------


# Wording that marks a ';'-segment as a PAYMENT / discount STEP of the
# PREVIOUS transaction rather than a new transaction. Never a transaction
# verb on its own, so it must be folded into the previous journal - FT-E
# never posts it as an independent entry.
_PAYMENT_STEP_HINTS = (
    "paid", "received", "immediately", "half", "quarter", "%",
    "discount", "settlement", "cheque", "check", "cash paid",
)
_STRONG_TRANSACTION_VERBS = (
    "purchased", "bought", "sold", "started business",
    "commenced business", "started the business", "withdrew",
    "deposited", "returned", "loan", "rent ", "salary", "salaries",
    "wages", "insurance", "commission", "interest", "drawings",
    "capital", "expense", "advertisement", "electricity",
)


def _is_payment_step(segment: str) -> bool:
    """True when a ';'-segment is a payment/discount step of the prior
    transaction (contains payment wording but NO transaction verb of its
    own). Deterministic."""
    low = " " + str(segment or "").lower() + " "
    if not any(h in low for h in _PAYMENT_STEP_HINTS):
        return False
    return not any(v in low for v in _STRONG_TRANSACTION_VERBS)


def _party_from_journal(journal: Dict[str, Any]) -> Optional[str]:
    """The Personal account named in a journal (the receiver/giver). Used
    to resolve a following 'paid him Rs.4,000'. Returns None when the
    journal has no named party (pure cash transactions)."""
    for line in (journal.get("debit_lines") or []) +             (journal.get("credit_lines") or []):
        account = line.get("account") or ""
        if account and traditional_class_for(account) == CLASS_PERSONAL                 and account not in ("Capital", "Bank", "Loan",
                                    "Bank Loan", "Drawings"):
            return account
    return None


def _reason_multi_transaction(text: str,
                              segments: List[str]) -> Dict[str, Any]:
    """Reason through a multi-transaction question (';'-separated).

    Deterministic and safe:
      * every segment must journal (status VERIFIED) or the WHOLE
        question refuses with the first failing segment's status;
      * a ';'-segment that is a PAYMENT/DISCOUNT step of the previous
        transaction ('paid half immediately with 2% cash discount',
        'paid him Rs.4,000') is folded into the previous journal through
        the full discount pipeline - it is NEVER posted as an
        independent entry;
      * a following 'him/her/them' resolves to the party named by the
        previous segment - never an invented name;
      * the resolved journals are combined into ONE ledger and ONE
        trial balance; if Dr != Cr the exact discrepancy is exposed.
    """
    understanding = build_bk_understanding(text)
    journals: List[Dict[str, Any]] = []
    prior_party: Optional[str] = None
    resolved_segments: List[str] = []

    for i, raw_segment in enumerate(segments):
        segment = _resolve_pronouns(raw_segment, prior_party)
        resolved_segments.append(segment)
        journal = generate_journal(segment)
        if journal["status"] != VERIFIED and i > 0                 and _is_payment_step(raw_segment):
            # payment/discount step -> re-run the discount pipeline over
            # the previous transaction PLUS this step as ONE journal.
            merged_text = resolved_segments[i - 1] + "; " + segment
            merged = generate_journal(merged_text)
            if merged["status"] == VERIFIED:
                journals[-1] = merged
                party = _party_from_journal(merged)
                if party:
                    prior_party = party
                continue
        if journal["status"] != VERIFIED:
            status = journal["status"]
            refusal = _refusal(
                status,
                f"Transaction {i + 1} of {len(segments)}: "
                + (journal.get("why_not") or "FT-E could not reason about "
                   "this transaction deterministically."),
                journal.get("next_action") or
                "Re-type each transaction in standard FYJC wording, "
                "separated by ';'.")
            refusal["understanding"] = understanding
            refusal["journal"] = journal
            refusal["journals"] = journals
            return refusal
        journals.append(journal)
        party = _party_from_journal(journal)
        if party:
            prior_party = party

    step_records: List[Dict[str, Any]] = [
        step for j in journals for step in (j.get("calculation_records") or [])
    ]

    ledger = generate_ledger(journals)
    trial_balance = generate_trial_balance(journals)
    verification = verify_arithmetic([
        {"side": line["side"], "amount": line["amount"]}
        for journal in journals
        for line in (journal.get("debit_lines") or [])
        + (journal.get("credit_lines") or [])
    ])

    debit_lines = [line for j in journals
                   for line in (j.get("debit_lines") or [])]
    credit_lines = [line for j in journals
                    for line in (j.get("credit_lines") or [])]
    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))

    narration_parts: List[str] = []
    for j_idx, journal in enumerate(journals, start=1):
        narration_parts.append(f"Entry {j_idx}:")
        for line in journal.get("debit_lines") or []:
            narration_parts.append(f"{line['account']} A/c Dr "
                                   f"{_fmt_amt(line['amount'])}")
        for line in journal.get("credit_lines") or []:
            narration_parts.append(f"To {line['account']} A/c "
                                   f"{_fmt_amt(line['amount'])}")
    narration = "Being " + "; ".join(narration_parts) + "."

    return {
        "status": VERIFIED,
        "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
        "resolved": True,
        "understanding": understanding,
        "journal": {
            "status": VERIFIED,
            "multi": True,
            "count": len(journals),
            "particulars": " / ".join(
                l["account"] for l in debit_lines) + " A/c Dr",
            "debit_lines": debit_lines,
            "credit_lines": credit_lines,
            "narration": narration,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balanced": total_debit == total_credit,
        },
        "journals": journals,
        "ledger": ledger,
        "trial_balance": trial_balance,
        "verification": verification,
        "debit_lines": debit_lines,
        "credit_lines": credit_lines,
        "calculation_records": step_records,
        "why_not": None,
        "next_action": "Post these entries in your journal and verify them.",
        "audit": {
            "authority": "bookkeeping",
            "rule_key": None,
            "calculation_ids": [
                step.get("calculation_id")
                for step in step_records if step.get("calculation_id")
            ],
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
        },
    }


# ---------------------------------------------------------------------------
# Full reasoning entry point
# ---------------------------------------------------------------------------


def reason_bk_question(question: str,
                       amount: Any = None) -> Dict[str, Any]:
    """The complete hardened Book-Keeping reasoning journey for ONE
    question (which may contain ONE transaction).

    Returns a student-readable flow: understanding -> accounts ->
    traditional classification -> Golden Rule -> debit/credit decision ->
    journal entry -> ledger effect -> trial-balance effect -> verification
    -> C++ metric verification (when a registered metric is requested).
    """
    text = str(question or "").strip()
    if not text:
        return _refusal(
            BLOCKED, "No transaction was provided.",
            "Type or photograph the transaction description.")

    low = " " + text.lower() + " "

    # -- NOT_SUPPORTED boundary (before classification) -------------------
    for hint in _NOT_SUPPORTED_HINTS:
        if hint in low:
            return _refusal(
                NOT_SUPPORTED,
                f"'{hint}' is outside the FYJC Book-Keeping topics FT-E "
                "currently supports (journal entries, ledger posting, "
                "trial balance, debit/credit reasoning for standard "
                "transactions). FT-E does not guess a treatment.",
                "Choose a supported topic: journal, ledger, trial balance, "
                "or a standard transaction (purchase, sale, expense, "
                "income, bank, drawings, returns, discounts).")

    segments = _split_transactions(text)
    if len(segments) > 1:
        return _reason_multi_transaction(text, segments)

    understanding = build_bk_understanding(text)
    journal = generate_journal(text)

    if journal["status"] != VERIFIED:
        status = journal["status"]
        refusal = _refusal(
            status, journal.get("why_not") or "FT-E could not reason about "
            "this transaction deterministically.",
            journal.get("next_action") or
            "Re-type the transaction in standard FYJC wording.")
        refusal["understanding"] = understanding
        refusal["journal"] = journal
        return refusal

    ledger = generate_ledger([journal])
    trial_balance = generate_trial_balance([journal])
    verification = verify_arithmetic([
        {"side": line["side"], "amount": line["amount"]}
        for line in (journal.get("debit_lines") or [])
        + (journal.get("credit_lines") or [])
    ])

    return {
        "status": VERIFIED,
        "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
        "resolved": True,
        "understanding": understanding,
        "journal": journal,
        "ledger": ledger,
        "trial_balance": trial_balance,
        "verification": verification,
        "debit_lines": journal.get("debit_lines"),
        "credit_lines": journal.get("credit_lines"),
        "calculation_records": journal.get("calculation_records"),
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
        "audit": {
            "authority": "bookkeeping",
            "rule_key": None,
            "calculation_ids": [
                step.get("calculation_id")
                for step in (journal.get("calculation_records") or [])
                if step.get("calculation_id")
            ],
            "total_debit": float(journal["total_debit"]),
            "total_credit": float(journal["total_credit"]),
        },
    }


# ---------------------------------------------------------------------------
# Metric verification through the C++ authority (section 8)
# ---------------------------------------------------------------------------


def verify_bk_metric(metric: str,
                     facts: Optional[Dict[str, Any]] = None,
                     text: Optional[str] = None,
                     documents: Optional[List[Dict[str, Any]]] = None,
                     student_answer: Any = None) -> Dict[str, Any]:
    """Verify a REGISTERED financial metric that arises from a book-keeping
    question through the C++ mathematical authority. This is the ONLY path
    that computes a financial result - Python never calculates it."""
    return verify_maths_answer(
        metric, facts=facts, text=text, documents=documents,
        student_answer=student_answer)
