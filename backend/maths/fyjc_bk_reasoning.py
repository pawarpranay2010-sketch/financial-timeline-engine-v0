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
    "half": "50", "one-half": "50", "one half": "50",
    "quarter": "25", "one-fourth": "25", "one fourth": "25",
    "two-thirds": "66.6666666667", "two thirds": "66.6666666667",
    "three-fourths": "75", "three fourths": "75",
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
    'paid half immediately', 'half paid', 'paid 40% at once'.

    Word fractions ('half', 'quarter', 'three-fourths', ...) can never
    collide with a discount rate. A '<n>%' token is a payment fraction
    ONLY when its surrounding label does NOT say 'discount' - a trade/cash
    discount rate is a rate, never the paid portion (Sprint 15F:
    '...at 25% trade discount; paid three-fourths immediately' must pay
    75%, never the 25% of the discount)."""
    low = " " + str(text or "").lower() + " "
    for word, fraction in _FRACTION_WORDS.items():
        if f" {word} " in low:
            if ("paid" in low or "cash" in low or "immediately" in low
                    or "at once" in low):
                return Decimal(fraction)
    # percent-based payment fractions: a '<n>%' token is the PAID portion
    # only when its immediate neighbourhood says 'paid'/'immediately'/'at
    # once'/'cash' and does NOT say 'discount'. The window is tight
    # (+/- 12 chars) so a trade-discount rate elsewhere in the sentence
    # ('...at 15% trade discount; paid 50% immediately') never poisons the
    # payment fraction.
    for m in _PERCENT_TOKEN.finditer(low):
        window = low[max(0, m.start() - 12):m.end() + 12]
        if "discount" in window:
            continue
        if any(k in window for k in ("paid", "immediately", "at once",
                                     "cash")):
            try:
                return Decimal(m.group(1))
            except (InvalidOperation, ValueError):
                continue
    return None


# Words that mark a PARTIAL payment. When one of these precedes an
# 'immediately'-style phrase, only part of the amount is settled NOW and
# the transaction must NOT flip into cash mode - the unpaid balance stays
# on credit (Sprint 15E: 'Half the amount was paid immediately' stays a
# credit purchase with a partial cash payment).
_FRACTION_HINTS = (
    "half", "quarter", "one-third", "one third", "two-thirds",
    "two thirds", "three-fourths", "three fourths", "partly",
    " part ", " portion ", " some of the amount ",
    "40%", "50%", "25%", "30%", "75%",
)


def _full_immediate_settlement(low: str) -> bool:
    """True when the wording settles the ENTIRE amount now ('payment made
    immediately', 'paid the full amount immediately'). A PARTIAL payment
    ('Half the amount was paid immediately') is never a full settlement -
    the balance stays on credit, so cash mode must not fire. Deterministic:
    only an explicit 'full amount' wording overrides a fraction hint."""
    has_fraction = any(h in low for h in _FRACTION_HINTS)
    if has_fraction:
        return "paid the full amount immediately" in low \
            or "full amount paid immediately" in low
    return any(k in low for k in (
        "payment made immediately", "payment was made immediately",
        "payment made at once", "paid the full amount immediately",
        "the amount was paid immediately", "payment is made immediately",
        "paid immediately"))


def _contradictory_cash_credit(low: str) -> bool:
    """True when the wording states BOTH a cash mode and a credit mode
    with no payment/collection step that explains the cash side
    ('Purchased goods for cash on credit from Rahul Rs.10,000' is
    contradictory and must be REVIEW_REQUIRED, never guessed; a credit
    purchase with 'half the amount paid immediately' is NOT contradictory
    - the cash words describe the partial settlement, Sprint 15F).
    'cash discount' never counts as a cash mode (Sprint 15E)."""
    _cash_phr = re.sub(r"\bcash\s+discount\b", " ", low)
    # 'cash book' / 'cashbook' / 'cashier' name a record-keeping place,
    # never a settlement mode - 'Enter ... in the cash book ... on credit'
    # is a CREDIT purchase, not a contradiction (Sprint 15H).
    _cash_phr = re.sub(r"\b(?:cash\s+book|cashbook|cash\s+bank|cashier)\b",
                       " ", _cash_phr)
    has_cash = ("for cash" in _cash_phr or "paid cash" in _cash_phr
                or "cash purchase" in _cash_phr
                or re.search(r"\bcash\b", _cash_phr))
    has_credit = ("on credit" in low or "on account" in low
                  or "credit" in low)
    if not (has_cash and has_credit):
        return False
    payment_step = (
        re.search(r"\b(?:paid|received)\b", low) is not None
        or "immediately" in low or "at once" in low
        or "half" in low or "quarter" in low
        or "full settlement" in low
    )
    return not payment_step


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
        # [Cash, Bank] either/or collapses to the named side, so
        # 'Started business with bank balance Rs.X' posts to BANK, not
        # Cash (Sprint 15E).
        "debit": ["Cash", "Bank"], "credit": ["Capital"],
    },
    {
        "key": "CAPITAL_INTRODUCED",
        "label": "Additional capital introduced",
        "when": ("brought in as capital", "brought ... as capital",
                 "additional capital", "introduced capital",
                 "brought in additional capital", "brought into the business",
                 "into the business as capital", "as capital"),
        "debit": ["Cash", "Bank"], "credit": ["Capital"],
    },
    {
        "key": "DRAWINGS_CASH",
        "label": "Drawings (cash withdrawn for personal use)",
        "when": ("withdrew for personal use", "withdrawn for personal use",
                 "for personal use", "for private use", "drawings",
                 # passive-voice homework phrasing (Sprint 15H)
                 "cash withdrawn by", "for personal expenses",
                 "for private expenses", "personal expenses",
                 "private expenses"),
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
                 "goods bought by cheque",
                 # 'worth Rs.X from <party>' is the same credit purchase
                 # (the amount sits between 'goods' and 'from') (Sprint 15E)
                 "purchased goods worth", "bought goods worth",
                 "goods worth", "purchased stock worth", "stock worth"),
        "debit": ["Purchases"], "credit": [{"party": "giver"}],
    },
    {
        "key": "SALE_GOODS_CASH",
        "label": "Goods sold for cash",
        "when": ("sold goods for cash", "sold for cash", "cash sale",
                 "cash sales", "sold goods in cash", "sold stock for cash",
                 "sold goods by cheque", "sold goods by check",
                 "sold stock by cheque", "sold by cheque", "sold by check",
                 # passive sale with explicit cash receipt (Sprint 15H):
                 # 'Goods were sold and cash received immediately' is a
                 # CASH sale - never a debtor.
                 "sold and cash received", "sold and received"),
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
                 "paid telephone", "telephone bill paid",
                 # 'Paid for stationery in cash Rs.500' / 'Paid for repairs'
                 # - the expense word follows 'paid for' (Sprint 15E)
                 "paid for ",
                 # 'Payment made for rent Rs.5,000 in cash' - same expense
                 # family with the payment noun (Sprint 15F)
                 "payment made for ", "payment made for"),
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
                 "paid into bank", "deposited in bank", "cash into bank",
                 # 'the bank' wording variants (Sprint 15F)
                 "deposited into the bank", "deposited cash into the bank",
                 "cash deposited into the bank", "deposited the cash into "
                 "the bank", "paid into the bank", "deposited in the bank"),
        "debit": ["Bank"], "credit": ["Cash"],
    },
    {
        "key": "CASH_FROM_BANK",
        "label": "Cash withdrawn from bank",
        "when": ("withdrew from bank", "withdrawn from bank",
                 "drew from bank", "drawn from bank", "cash from bank",
                 "withdrew cash from bank", "withdrawn cash from bank",
                 # 'the bank' wording variants (Sprint 15F)
                 "withdrew from the bank", "withdrew cash from the bank",
                 "withdrawn from the bank", "withdrawn cash from the bank",
                 "drew from the bank", "drew cash from the bank",
                 "drawn from the bank", "cash from the bank"),
        "debit": ["Cash"], "credit": ["Bank"],
    },
    {
        "key": "CHEQUE_PAID",
        "label": "Payment by cheque",
        "when": ("paid by cheque", "cheque paid", "issued a cheque",
                 "gave a cheque", "cheque issued", "by cheque", "by check",
                 "cheque issued to", "cheque paid to", "paid ... by cheque"),
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
                 "purchase return", "returns outward", "purchases returns",
                 "purchases return", "returned goods worth",
                 "goods returned worth", "returned stock", "stock returned to"),
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
    ("interest", "Interest Paid"),
    # longest first: 'carriage outward'/'carriage on sales' must win over
    # the generic 'carriage' word, otherwise 'Paid carriage outward'
    # would be posted to the wrong nominal account (Sprint 15E).
    ("carriage outward", "Carriage Outward"),
    ("carriage on sales", "Carriage Outward"),
    ("carriage inward", "Carriage Inward"),
    ("carriage on purchases", "Carriage Inward"),
    ("carriage", "Carriage Inward"),
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



_AUX_BEFORE_VERB = ("was", "were", "has", "have", "had", "is", "are", "be",
                   "been", "being", "am")


def _strip_aux_before_verb(name: str) -> str:
    """'Rent was paid' -> 'Rent': an auxiliary verb between a name and the
    main verb belongs to the verb phrase, never to the name (Sprint 15H).
    Deterministic - only trailing aux verbs are removed, at most three."""
    for _ in range(3):
        head, _, tail = name.rpartition(" ")
        if head and tail.lower() in _AUX_BEFORE_VERB:
            name = head
        else:
            break
    return name


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
                   "from ", " to ",
                   # cheque-in-favour wording (Sprint 15F)
                   "in favour of ", "in favor of ", "cheque in favour of "):
        if marker in low:
            idx = low.index(marker) + len(marker)
            rest = text[idx:]
            m = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+worth\s+"
                         r"|\s+and\s+|\s+in\s+|\s+at\s+|\s+₹|\s+Rs|\s+\d|,|$)", rest)
            if m:
                party = m.group(1).strip().rstrip(".;,")
                if party and not party.lower().endswith(
                        ("a/c", "account", "ltd", "limited")):
                    return party
    # '<Party> paid ...' - the party is the SUBJECT of the payment verb
    # (a receipt to the business: 'Mohan paid Rs.12,000'). The subject
    # position before 'paid' is deterministic - never an invented name.
    m_subj = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)\s+paid\b",
                      str(text or ""))
    if m_subj:
        subject = _strip_aux_before_verb(
            m_subj.group(1).strip().rstrip(".;,"))
        # Sprint 15I-D: a bare auxiliary verb is never a party - 'Was paid
        # Rs.5,000.' has NO subject, so the aux must not become the account.
        if subject.lower() not in ("paid", "he", "she", "they", "the",
                                   "we", "i", "him", "her", "them", "it",
                                   "was", "were", "has", "have", "had",
                                   "is", "are", "be", "been", "being",
                                   "am", "does", "did"):
            return subject
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


_RETURN_NONPARTY_WORDS = {"goods", "stock", "the", "he", "she", "they",
                          "returned", "sold"}


def _returns_rule(text: str) -> Optional[Dict[str, Any]]:
    """Goods-return transactions, told apart by STRUCTURE rather than by
    enumerating wordings (Sprint 15E):

      * 'Purchases returns to Rahul Rs.1,000' / 'returned ... to <party>'
        -> PURCHASE_RETURN (Dr party / Cr Purchase Returns);
      * 'Sales returns from Mohan Rs.800' / '<party> returned goods ...'
        -> SALES_RETURN (Dr Sales Returns / Cr party).

    Returns None when the wording is not a goods return. A party-less
    'returned goods worth Rs.1,000' stays a party placeholder and is
    refused unless the multi-transaction layer can inherit the previous
    segment's party."""
    low = " " + str(text or "").lower() + " "
    if "purchases returns" in low or "purchase returns" in low \
            or "returns outward" in low:
        return {
            "key": "PURCHASE_RETURN", "label": "Goods returned to supplier",
            "debit": [{"party": "giver"}], "credit": ["Purchase Returns"],
        }
    if "sales returns" in low or "sales return" in low \
            or "returns inward" in low:
        return {
            "key": "SALES_RETURN", "label": "Goods returned by customer",
            "debit": ["Sales Returns"], "credit": [{"party": "giver"}],
        }
    if "returned" not in low or ("goods" not in low and "stock" not in low):
        return None
    # 'returned (goods|stock) ... to <party>' -> the business returns goods
    # to its supplier: Dr party / Cr Purchase Returns.
    if re.search(r"\breturned\b.*?\bto\b", low):
        return {
            "key": "PURCHASE_RETURN", "label": "Goods returned to supplier",
            "debit": [{"party": "giver"}], "credit": ["Purchase Returns"],
        }
    # '<Party> returned goods ...' -> the customer returns goods to the
    # business: Dr Sales Returns / Cr party.
    m = re.match(r"\s*([A-Z][A-Za-z' .]{1,40}?)\s+returned\b",
                 str(text or ""))
    if m:
        name = _strip_aux_before_verb(m.group(1).strip().rstrip(".;,"))
        if name.lower() not in _RETURN_NONPARTY_WORDS:
            return {
                "key": "SALES_RETURN", "label": "Goods returned by customer",
                "debit": ["Sales Returns"], "credit": [name],
            }
    return None


def classify_bk_type(question: str) -> Optional[Dict[str, Any]]:
    """The canonical transaction type for a description (first match wins),
    with its resolved debit/credit account specs. None when unrecognised.
    Fixed-asset purchases/sales are detected BEFORE the goods patterns so
    the exact asset named is used (never an invented sibling account)."""
    text = str(question or "").strip()
    if not text:
        return None
    # Sprint 15I-D transaction-local isolation: a two-sentence compound
    # (';', '. ' or ' - ' separated) whose SECOND transaction carries its
    # own identity (a registered expense word, a subject-passive party
    # payment, a 'Paid <Party>' tail, or a subjectless passive tail) is
    # NEVER silently folded into the first transaction as a partial
    # payment - the previous transaction's state must not bleed into the
    # next one. FT-E refuses the compound so the student enters the two
    # transactions separately (deterministic; never an invented journal,
    # never a silent combination). The head need NOT be a purchase: the
    # multi-transaction fallback path also folds a failed payment step
    # after ANY prior journal, so a sale/other head + own-identity tail
    # ('Sold goods to Ram on credit Rs.12,000. Was paid Rs.5,000.') must
    # be refused the same way (Sprint 15I-D adversarial finding).
    _c_parts = re.split(
        r";\s*| - |(?<=[a-z0-9)])\.\s+(?=[A-Z])", text)
    if len(_c_parts) == 2:
        _c_head, _c_tail = _c_parts
        _c_low = " " + _c_tail.lower() + " "
        _c_head_pattern = classify_bk_type(_c_head)
        _c_purchase_head = bool(_c_head_pattern) and \
            "PURCHASE" in _c_head_pattern["key"]
        # An expense-word tail ('paid postage Rs.250', 'paid rent
        # Rs.4,000') is only ambiguous AFTER a PURCHASE (it could be part
        # of that purchase, e.g. carriage); after any other head it is a
        # clean second expense transaction and must stay journaled
        # independently ('Started business with cash Rs.50,000; paid rent
        # Rs.4,000' -> two journals, corpus-verified). So _c_expense is
        # purchase-head-gated; the party/subjectless-passive checks below
        # are not (a party payment or subjectless passive tail after ANY
        # head carries its own identity and must never be folded).
        _c_expense = _c_purchase_head and any(
            phrase != "office" and re.search(
                r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])",
                _c_low)
            for phrase, _ in _EXPENSE_ACCOUNT_WORDS)
        # '<Party> was paid ...' - a proper-noun SUBJECT of a passive
        # payment, so the tail is a payment TO that party, never a
        # settlement step of the previous transaction. Quantifier
        # subjects ('Half the amount was paid') are never parties.
        _c_passive_party = False
        _c_m_passive = re.match(
            r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:was|were|"
            r"has been|have been|had been)\s+paid\b", _c_tail)
        if _c_m_passive and _c_m_passive.group(1).lower() not in (
                "half", "one", "quarter", "third", "fourth", "the",
                "a", "an", "amount", "balance", "full", "some",
                "rest", "part", "cash", "goods", "stock", "money",
                "cheque"):
            _c_passive_party = True
        # 'Paid <Party> Rs.X' with a Capitalised party (not a
        # pronoun 'him/her').
        _c_paid_party = bool(re.match(
            r"\s*Paid\s+[A-Z][A-Za-z' .]{1,40}?(\s|,|\.)", _c_tail))
        # subjectless passive tail ('Was paid Rs.X ...') - the aux
        # alone is never a party, and without a continuation pronoun
        # the tail cannot be tied to the previous transaction.
        _c_subjless = bool(re.match(
            r"\s*(?:was|were|has been|have been|had been)\s+paid\b",
            _c_tail, re.IGNORECASE))
        if (_c_expense or _c_passive_party or _c_paid_party
                or _c_subjless):
            return {
                "key": "COMPOUND_OWN_IDENTITY",
                "label": "Two separate transactions entered together",
                "refuse": True, "debit": [], "credit": [],
                "why": ("The description joins two transactions that "
                        "FT-E will not silently combine: the second "
                        "one carries its own expense/party identity. "
                        "FT-E never folds it into the first transaction "
                        "as a partial payment - enter the two "
                        "transactions separately."),
            }
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
        # assets INTRODUCED as capital ('Brought machinery worth Rs.50,000
        # into the business', 'Brought furniture into the business as
        # capital Rs.20,000') -> the exact asset debited, Capital credited.
        # More than one named asset is never split (refused).
        if any(k in low for k in ("capital", "into the business",
                                  "introduced", "brought into")):
            if len(assets) > 1:
                return {
                    "key": "ASSET_AMBIGUOUS",
                    "label": "Ambiguous capital-asset introduction",
                    "refuse": True, "debit": [], "credit": [],
                    "why": ("The description names more than one asset for "
                            "capital. FT-E never guesses the split."),
                }
            return {
                "key": "CAPITAL_ASSET_INTRODUCED",
                "label": f"{assets[0]} introduced as capital",
                "debit": assets, "credit": ["Capital"],
            }
    # Installation / erection charges paid on a fixed asset are
    # CAPITALISED into that asset (Sprint 15H real-world finding):
    # 'Paid wages for installation of machinery Rs.5,000' -> Machinery A/c
    # Dr, never a standalone Wages expense. Narrow trigger - only an
    # installation/fixing phrase + exactly one named asset; anything else
    # stays with the normal expense patterns.
    if any(k in low for k in (
            "for installation", "installation of", "installation charges",
            "for erection", "erection of", "for fixing")):
        _cap_assets = named_assets(text)
        if len(_cap_assets) == 1:
            return {
                "key": "CAPITALISE_EXPENSE",
                "label": f"Installation charge capitalised into "
                         f"{_cap_assets[0]}",
                "debit": _cap_assets, "credit": ["Cash", "Bank"],
            }
        if len(_cap_assets) > 1:
            return {
                "key": "CAPITALISE_AMBIGUOUS",
                "label": "Ambiguous installation charge",
                "refuse": True, "debit": [], "credit": [],
                "why": ("The installation charge names more than one asset "
                        "to capitalise into. FT-E never guesses the split."),
            }
    # goods-return wording ('returned ... to <party>' = purchase return;
    # '<party> returned goods' = sales return) - structural, registry-free.
    returns = _returns_rule(text)
    if returns is not None:
        return returns
    # 'for cash' decides the MODE even when a party is named
    # ('Sold goods to Mohan for cash', 'Purchased goods from Amit for
    # cash'). A named party is just the counterparty - the settlement
    # mode comes from the words, so a party never flips a cash
    # transaction into a credit one. Contradictory 'cash ... on credit'
    # wording is ambiguous and stays with the refusal layer below.
    full_immediate_payment = _full_immediate_settlement(low)
    payment_by_cheque = any(k in low for k in (
        "by cheque", "by check", "payment by cheque",
        "payment made by cheque", "paid by cheque", "paid by check"))
    # 'cash discount' is a discount ON the cash portion - the word 'cash'
    # inside it never proves the transaction itself was for cash (Sprint
    # 15E: '...at 10% trade discount. Half the amount was paid immediately
    # and a cash discount of 2%...' stays a CREDIT purchase). Strip the
    # phrase before deciding the settlement mode.
    _cash_mode_text = re.sub(r"\bcash\s+discount\b", " ", low)
    has_cash_mode = ("for cash" in _cash_mode_text
                     or "paid cash" in _cash_mode_text
                     or re.search(r"\bcash\b", _cash_mode_text)
                     or full_immediate_payment or payment_by_cheque)
    has_credit_mode = ("credit" in low or "on account" in low)
    # Contradictory 'for cash ... on credit' wording (no payment step that
    # explains the cash side) is REVIEW_REQUIRED - FT-E never guesses the
    # settlement mode (Sprint 15H ambiguity attacks).
    if _contradictory_cash_credit(low):
        return {
            "key": "MODE_CONTRADICTORY",
            "label": "Cash and credit mode both stated",
            "refuse": True, "debit": [], "credit": [],
            "why": ("The description states both a cash mode and a credit "
                    "mode with no payment step to reconcile them. FT-E "
                    "never guesses which settlement applies."),
        }
    goods_purchase_words = (
        "purchased goods", "bought goods", "goods purchased",
        "goods bought", "purchased stock", "bought stock",
        "stock purchased", "stock bought",
        "purchased goods worth", "bought goods worth", "goods worth",
        "purchased stock worth", "stock worth",
    )
    goods_sale_words = (
        "sold goods", "goods sold", "sold stock", "stock sold",
        "sold goods to", "goods sold to", "sold goods worth",
        "sold stock worth",
    )
    # 'Goods costing Rs.10,000 sold ... for cash Rs.12,000' is a sale; the
    # COST figure is not the sale value (dropped in the amount pipeline).
    costing_sale = "costing" in low and any(k in low for k in (
        "sold", "sale ", "sales"))
    if has_cash_mode and not has_credit_mode:
        if any(k in low for k in goods_purchase_words):
            return {
                "key": "PURCHASE_GOODS_CASH",
                "label": "Goods purchased for cash",
                "debit": ["Purchases"], "credit": ["Cash", "Bank"],
            }
        if any(k in low for k in goods_sale_words) or costing_sale:
            # 'Sold goods to Mohan ... ; received cash for half at once' is
            # a CREDIT sale with a PARTIAL collection (Mohan stays a
            # debtor for the unpaid balance). The 'cash' word describes the
            # collection, not the sale mode - a named customer + a payment
            # fraction keeps the sale on credit unless the wording says
            # 'for cash' (Sprint 15F: Mohan never becomes a debtor for a
            # true cash sale, and never disappears from a partial one).
            _sale_party = bool(re.search(
                r"\bsold\b[^.;]*?\bto\b\s+[a-z]", low)) \
                or "sold to" in low
            _partial_collection = bool(_paid_fraction(question)) \
                or "half" in low or "quarter" in low
            if _sale_party and _partial_collection \
                    and "for cash" not in _cash_mode_text:
                return {
                    "key": "SALE_GOODS_CREDIT",
                    "label": "Goods sold on credit",
                    "debit": [{"party": "receiver"}], "credit": ["Sales"],
                }
            return {
                "key": "SALE_GOODS_CASH",
                "label": "Goods sold for cash",
                "debit": ["Cash", "Bank"], "credit": ["Sales"],
            }
    # 'on credit' / 'on account' decides CREDIT mode even when the amount
    # sits between the goods word and the party ('Purchased goods for
    # Rs.10,000 on credit from Rahul', 'Bought goods on account from
    # Rahul'). A party named with credit wording never becomes a cash
    # transaction (Sprint 15F: 'on account' = 'on credit').
    if has_credit_mode and not has_cash_mode:
        if any(k in low for k in goods_purchase_words):
            return {
                "key": "PURCHASE_GOODS_CREDIT",
                "label": "Goods purchased on credit",
                "debit": ["Purchases"], "credit": [{"party": "giver"}],
            }
        if any(k in low for k in goods_sale_words):
            return {
                "key": "SALE_GOODS_CREDIT",
                "label": "Goods sold on credit",
                "debit": [{"party": "receiver"}], "credit": ["Sales"],
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
    if "goods" in low and any(k in low for k in (
            "personal use", "private use", "personal expenses",
            "private expenses")):
        return {
            "key": "GOODS_PERSONAL_USE",
            "label": "Goods taken for personal use",
            "debit": ["Drawings"], "credit": ["Purchases"],
        }
    # 'Discount received from Rahul Rs.150' is a DISCOUNT entry (Dr Rahul
    # / Cr Discount Received) - the generic 'received from' phrase must
    # not shadow it into a plain cash receipt. Fires ONLY when the discount
    # phrase OPENS the sentence; a 'Paid to X ..., discount received ...'
    # or 'Received from X ..., discount received ...' settlement stays
    # with the explicit-discount machinery (Sprint 15E).
    _stripped = low.strip()
    if _stripped.startswith("discount received")             or _stripped.startswith("received discount"):
        return {
            "key": "DISCOUNT_RECEIVED",
            "label": "Cash discount received from supplier",
            "debit": [{"party": "giver"}], "credit": ["Discount Received"],
        }

    # Passive-voice expenses ('Rent was paid in cash', 'Wages were paid'):
    # the expense word PRECEDES a passive 'paid' instead of following it.
    # Registry-driven via the same expense words. Checked BEFORE the
    # subject-position receipt branch ('<Party> paid ...') so an expense
    # name can never be mistaken for a paying party (Sprint 15H).
    m_exp = re.search(
        r"\b(rent|salary|salaries|wages|insurance|electricity|"
        r"advertisement|commission|interest|carriage|repairs|postage|"
        r"stationery|audit fees|legal fees|income tax|fuel|telephone)\b"
        r"\s+(?:was|were|has been|have been|had been|is|are)\s+paid\b",
        low)
    if m_exp:
        return {
            "key": "EXPENSE_PAID",
            "label": "Expense paid",
            "debit": ["_EXPENSE_ACCOUNT"], "credit": ["Cash", "Bank"],
        }
    # Subjectless passive expense ('Was paid Rs.3,000 carriage', 'Was paid
    # wages Rs.2,000'): the sentence opens with an auxiliary verb and the
    # expense word FOLLOWS 'paid' (postposed subject). The auxiliary verb
    # alone is never a party and never names an account - the registered
    # expense word carries the transaction, so it resolves to the SAME
    # EXPENSE_PAID treatment as the subject-first passive form. A named
    # party ('Was paid Rs.3,000 to Rahul') stays a party payment below
    # (Sprint 15I-D).
    if re.match(r"\s*(?:was|were|has\s+been|have\s+been|had\s+been|is|are)"
                r"\s+paid\b", low) and _party_from_text(text) is None:
        _subjless_expense = _resolve_variable_account(
            "_EXPENSE_ACCOUNT", text)
        if _subjless_expense:
            return {
                "key": "EXPENSE_PAID",
                "label": "Expense paid",
                "debit": ["_EXPENSE_ACCOUNT"], "credit": ["Cash", "Bank"],
            }
    # '<Party> paid ...' (a debtor settles the business, e.g. 'Mohan paid
    # Rs.12,000 immediately') is a RECEIPT: Cash/Bank Dr, <Party> Cr - the
    # exact reverse of 'paid <Party> Rs.X'. The party's SUBJECT position
    # before the verb decides the direction (Sprint 15F: a party is never
    # treated as being paid when the party name opens the sentence). Guarded
    # against expense/income verbs so 'Rahul paid rent ...' stays with the
    # expense machinery instead of becoming a receipt.
    m_party_paid = re.match(
        r"\s*([A-Z][A-Za-z' .]{1,40}?)\s+(?:has\s+|had\s+)?paid\b", text)
    _party_paid_passive = False
    if m_party_paid:
        m_party_paid_name = _strip_aux_before_verb(
            m_party_paid.group(1).strip().rstrip(".;,"))
        if not m_party_paid_name:
            m_party_paid = None
        else:
            # Passive voice ('Mohan was paid Rs.5,000', 'Rahul has been
            # paid Rs.4,000') means the business PAID the party -> the
            # party is debited and Cash/Bank credited (PAID_TO). Active
            # voice ('Mohan paid Rs.12,000') means the party settled the
            # business -> Cash/Bank Dr, party Cr (RECEIVED_FROM). The
            # auxiliary verb between the name and 'paid' decides the
            # direction deterministically - never a reversed confident
            # answer (Sprint 15H).
            _party_paid_passive = re.search(
                r"\b(?:was|were|is|are|has\s+been|have\s+been|"
                r"had\s+been)\b", m_party_paid.group(1).lower())
    if m_party_paid and not any(k in low for k in (
            "rent", "salary", "salaries", "wages", "commission",
            "insurance", "electricity", "advertisement", "stationery",
            "repairs", "interest", "dividend", "purchased", "bought",
            "sold", "carriage", "postage", "tax", "fee", "discount")):
        if _party_paid_passive:
            return {
                "key": "PAID_TO",
                "label": "Payment to a party",
                "debit": [{"party": "giver"}], "credit": ["Cash", "Bank"],
            }
        return {
            "key": "RECEIVED_FROM",
            "label": "Receipt from a party",
            "debit": ["Cash", "Bank"], "credit": [{"party": "giver"}],
        }
    # A CHEQUE deposited into the bank is never cash: the counterparty is
    # the drawer of the cheque. 'Cheque deposited into bank' without a
    # named drawer is REVIEW_REQUIRED - FT-E never turns a cheque into a
    # cash deposit (Sprint 15F: 0 silent substitutions).
    if "cheque" in low and "bank" in low and any(k in low for k in
                                                  ("deposited", "deposit")):
        party = _party_from_text(text)
        if party:
            return {
                "key": "CHEQUE_DEPOSITED",
                "label": "Cheque deposited into bank",
                "debit": ["Bank"], "credit": [{"party": "giver"}],
            }
        return {
            "key": "CHEQUE_DEPOSIT_AMBIGUOUS",
            "label": "Cheque deposited into bank",
            "refuse": True, "debit": [], "credit": [],
            "why": ("The cheque was deposited into the bank but the drawer "
                    "(the person from whom the cheque was received) is not "
                    "named. FT-E never treats a cheque as cash."),
        }
    # A CHEQUE received is a bank transaction: 'Cheque received from Mohan'
    # and 'Received a cheque from Mohan' are the SAME CHEQUE_RECEIVED
    # pattern (Bank Dr / <party> Cr). The generic 'received from' phrase in
    # RECEIVED_FROM must not shadow it. Fires ONLY when the cheque is the
    # OBJECT of the receipt ('received a cheque', 'got a cheque'), the
    # SUBJECT ('cheque received'), or a drawer is named - 'Interest
    # received by cheque' is an INCOME received by cheque (Sprint 15F).
    if "cheque" in low and ("received a cheque" in low
                            or "got a cheque" in low
                            or "cheque received" in low
                            or "cheque was received" in low):
        return {
            "key": "CHEQUE_RECEIVED",
            "label": "Receipt by cheque",
            "debit": ["Bank"], "credit": [{"party": "giver"}],
        }
    # 'Withdrew Rs.5,000 from bank for office use' - a bank withdrawal
    # WITHOUT the word 'cash': the direction is structural (withdraw verb
    # + 'from bank'), never inferred from the word 'cash' alone (Sprint
    # 15H wording gap - the registry phrase 'withdrew from bank' cannot
    # match when the amount sits between). A personal/private purpose
    # stays with the drawings machinery below, and a bare 'Withdrew
    # Rs.5,000.' (no bank) stays with the ambiguity layer.
    if re.search(r"\b(?:withdrew|withdrawn|drew)\b.*?\bfrom\s+"
                 r"(?:the\s+)?bank\b", low) \
            and not any(k in low for k in (
                "for personal use", "for private use", "personal expenses",
                "private expenses", "for personal", "for private")):
        return {
            "key": "CASH_FROM_BANK",
            "label": "Cash withdrawn from bank",
            "debit": ["Cash"], "credit": ["Bank"],
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
    # 'payment made immediately' settles in full NOW (cash); 'by cheque' /
    # 'payment made by cheque' settles through the bank (Sprint 15E). Both
    # are full settlements - neither creates a creditor.
    # 'payment made immediately' settles in full NOW (cash); a PARTIAL
    # payment ('half ... paid immediately') never flips an asset purchase
    # into cash mode (Sprint 15E).
    full_immediate = _full_immediate_settlement(low)
    by_cheque = any(k in low for k in ("by cheque", "by check",
                                       "payment made by cheque",
                                       "payment by cheque"))
    if purchase:
        if "for cash" in low or "cash purchase" in low \
                or re.search(r"\bcash\b", low) or full_immediate \
                or by_cheque:
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
            or re.search(r"\bcash\b", low) or "received a cheque" in low \
            or by_cheque:
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


_SETTLEMENT_PHRASES = (
    "in full settlement of", "full settlement of", "settlement of",
    "his account of", "her account of", "their account of", "being",
)


def _detect_explicit_discount(question: str,
                              amounts: List[Decimal])\
        -> Optional[Dict[str, Any]]:
    """An explicit discount AMOUNT in the description (not a % rate).

    Standard FYJC wording - 'Received from Mohan Rs.9,800, discount
    allowed Rs.200' - gives the CASH amount and the DISCOUNT amount; the
    party account is their SUM.

    Amounts are mapped POSITIONALLY (never positionally guessed):
      * discount amount  = the figure after 'discount allowed/received';
      * party total      = the figure after a settlement phrase
        ('in full settlement of', 'his account of', 'being', ...) or the
        sum of cash + discount when no settlement figure is stated;
      * cash amount      = the remaining stated figure.

    When BOTH the cash amount and the party total are stated but no
    discount word appears ('Received from Mohan Rs.5,000 in full
    settlement of his account of Rs.5,200'), the discount is DERIVED by
    subtraction - deterministic arithmetic on stated figures, never an
    invented number. Returns {kind, party_total, cash_amount,
    discount_amount} or None (-> the refusal layer, never a guess).
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
    settlement_only = ("full settlement" in low or "settlement of" in low
                       or "in settlement of" in low or "account of" in low)
    if not (allowed or received or settlement_only) or len(amounts) < 2:
        return None
    kind = "allowed" if allowed else ("received" if received else None)
    if kind is None and settlement_only:
        # direction from the wording: 'received ... in full settlement'
        # discounts the DEBTOR (Discount Allowed); 'paid ...' discounts
        # the CREDITOR (Discount Received).
        if "received" in low and "paid" not in low:
            kind = "allowed"
        elif "paid" in low:
            kind = "received"
    if kind is None:
        return None

    def _amount_after(phrase: str) -> Optional[Decimal]:
        m = re.search(
            re.escape(phrase) + r"\s*(?:rs\.?|₹|inr)?\s*"
            r"(\d[\d,]*(?:\.\d+)?)", low)
        if not m:
            return None
        # a '%' right after the figure means it is a RATE (e.g. '2% cash
        # discount'), never a money amount - it must not be read as one.
        after = low[m.end():m.end() + 2]
        if after.lstrip().startswith("%"):
            return None
        try:
            return Decimal(m.group(1).replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    discount_amount = None
    for ph in ("discount allowed", "discount received",
               "allowed discount", "received discount",
               "allowed him discount", "allowed her discount",
               "allowed discount of", "discount of"):
        v = _amount_after(ph)
        if v is not None:
            discount_amount = v
            break
    if discount_amount is None:
        # pronoun-resolved form: 'allowed Mohan discount Rs.200' (the
        # 'him/her' was substituted with the party name) - the amount
        # follows the discount word of the allowed/received span.
        m_span = re.search(
            r"allowed\s+[A-Za-z][A-Za-z' .]{0,40}?\s+discount\s*"
            r"(?:rs\.?|₹|inr)?\s*(\d[\d,]*(?:\.\d+)?)", low)
        if m_span:
            after = low[m_span.end():m_span.end() + 2]
            if not after.lstrip().startswith("%"):
                try:
                    discount_amount = Decimal(
                        m_span.group(1).replace(",", ""))
                except (InvalidOperation, ValueError):
                    discount_amount = None
    party_total = None
    for ph in _SETTLEMENT_PHRASES:
        v = _amount_after(ph)
        if v is not None:
            party_total = v
            break

    remaining = [a for a in amounts
                 if a != discount_amount and a != party_total]
    cash_amount = remaining[0] if len(remaining) == 1 else None
    if cash_amount is None and len(amounts) == 2 \
            and discount_amount is not None:
        cash_amount = (amounts[0] if amounts[0] != discount_amount
                       else amounts[1])
    if cash_amount is None:
        return None
    if party_total is None and discount_amount is not None:
        party_total = cash_amount + discount_amount
    if party_total is None:
        return None
    if discount_amount is None:
        # both figures stated, no discount word: derive by subtraction
        if party_total >= cash_amount:
            discount_amount = party_total - cash_amount
        else:
            return None
    if party_total != cash_amount + discount_amount:
        return None
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
    # 'Goods costing Rs.10,000 sold to Mohan for cash Rs.12,000' - the
    # figure after 'costing' is the COST PRICE, never the sale value. When
    # a separate selling price is also stated the cost figure is dropped
    # (deterministic; the sale amount is what gets posted). With a single
    # amount ('Purchased goods costing Rs.15,000 ...') the cost IS the
    # transaction value and is kept.
    if len(amounts) >= 2 and "costing" in low:
        m_cost = re.search(r"costing\s*(?:rs\.?|₹|inr)?\s*"
                           r"(\d[\d,]*(?:\.\d+)?)", low)
        if m_cost:
            try:
                cost_value = Decimal(m_cost.group(1).replace(",", ""))
                amounts = [a for a in amounts if a != cost_value]
            except (InvalidOperation, ValueError):
                pass
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
        elif ("on credit" not in low and "credit" not in low
              and "on account" not in low):
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
    # 'Received Rs.5,000.' with no purpose/context is REVIEW_REQUIRED,
    # parallel to 'Paid Rs.5,000.' - never NOT_SUPPORTED (Sprint 15F)
    "received rs.", "received cash of rs.",
    # a bare goods transaction without a cash/credit word is ambiguous -
    # FT-E never assumes one.
    "purchased goods", "bought goods", "goods purchased",
    "goods bought", "sold goods", "goods sold",
    "purchased stock", "sold stock",
    # bare 'Withdrew Rs.5,000.' is unclear (cash/bank/purpose) - ask,
    # never NOT_SUPPORTED (Sprint 15H ambiguity attacks)
    "withdrew rs.", "withdrawn rs.", "withdrew ", "withdrawn ",
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
    # Protect honorific titles ('Mr. Sharma', 'Mrs. Rao', 'Dr. Desai') so
    # the '.' after 'Mr' is never treated as a sentence boundary (Sprint
    # 15E) - restored after splitting.
    _TITLE_RE = re.compile(r"\b(mr|mrs|ms|dr|prof|rev|st)\.\s+",
                           re.IGNORECASE)
    raw = str(question or "")
    raw = _TITLE_RE.sub(lambda m: m.group(1).lower() + " \x01", raw)
    raw = re.split(r";\s*", raw)
    pieces: List[str] = []
    for part in raw:
        # sentence boundaries AND comma-joined goods-returns ('Purchased
        # goods from Rahul on credit Rs.20,000, returned goods worth
        # Rs.1,000') are split in ONE pass - the return is its own
        # transaction, never silently swallowed by the purchase (Sprint
        # 15E: 0 silent substitutions).
        pieces.extend(re.split(
            r"(?<=[a-z0-9)])\.\s+(?=[A-Z])|"
            r",\s+(?=returned (?:goods|stock)|goods returned|"
            r"purchases returns|purchases return|sales returns|"
            r"sales return)", part, flags=re.IGNORECASE))
    segments = [seg.replace("\x01", ". ").strip() for seg in pieces
                if seg.strip()]
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


_PRONOUN_RE = re.compile(r"\b(him|her|them|he|she|they)\b",
                    re.IGNORECASE)


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
    word 'cash'. 'Started business with cash Rs.1,00,000 and bank balance
    Rs.50,000' additionally captures the BANK component (Sprint 15E).
    None when the amounts cannot be read deterministically (then the
    question is refused, never guessed)."""
    low = " " + str(text or "").lower() + " "
    if not any(k in low for k in ("started business", "commenced business",
                                  "started the business")):
        return None
    named = named_assets(text)
    breakdown: Dict[str, Decimal] = {}
    for asset in named:
        m = re.search(
            re.escape(asset)
            + r"\s+(?:(?:for|worth)\s+)?(?:Rs\.?|₹|INR)?\s*"
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
    cash_amt: Optional[Decimal] = None
    if m_cash:
        parsed_cash = parse_numeric_text(m_cash.group(1).replace(",", ""))
        if parsed_cash.value is None:
            return None
        cash_amt = parsed_cash.value
    m_bank = re.search(
        r"\bbank\b(?:\s+balance)?\s*(?:Rs\.?|₹|INR)?\s*"
        r"(\d[\d,]*(?:\.\d+)?)", text, re.IGNORECASE)
    bank_amt: Optional[Decimal] = None
    if m_bank:
        parsed_bank = parse_numeric_text(m_bank.group(1).replace(",", ""))
        if parsed_bank.value is None:
            return None
        bank_amt = parsed_bank.value
    if not named and bank_amt is None:
        return None
    components: List[Tuple[str, Decimal]] = []
    if cash_amt is not None:
        components.append(("Cash", cash_amt))
    if bank_amt is not None:
        components.append(("Bank", bank_amt))
    for asset, amount in breakdown.items():
        components.append((asset, amount))
    total = sum((amount for _, amount in components), Decimal(0))
    return {
        "cash": cash_amt if cash_amt is not None else Decimal(0),
        "assets": breakdown, "total": total, "bank": bank_amt,
        "components": components,
    }


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
    # not silently invent the Rs.200 discount. EXCEPTION (Sprint 15E):
    # when a party is named AND the account total is also stated
    # ('Received from Mohan Rs.5,000 in full settlement of his account of
    # Rs.5,200'), the discount is DERIVED deterministically from the two
    # stated figures - both numbers come from the question, so no value is
    # fabricated.
    low_check = " " + text.lower() + " "
    if "full settlement" in low_check and "discount" not in low_check:
        _fs_amounts, _ = _extract_amounts(text)
        _fs_party = _party_from_text(text)
        if _fs_party is None or len(_fs_amounts) < 2:
            return {
                "status": REVIEW_REQUIRED,
                "why_not": ("'Full settlement' wording implies a discount, "
                            "but no discount amount is stated. FT-E never "
                            "invents the difference."),
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
                    "CAPITAL_ASSET_INTRODUCED", "EXPENSE",
                    "INTEREST_ON_CAPITAL"))
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
                if explicit["discount_amount"] > 0:
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
                if explicit["discount_amount"] > 0:
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
            if startup.get("components"):
                for account, amount in startup["components"]:
                    debit_lines.append(_line(account, amount, "debit"))
            else:
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
        # a party-less goods-return ('Returned goods worth Rs.1,000')
        # inherits the party of the previous segment: 'to <party>' after a
        # PURCHASE (we return goods to the supplier), 'by <party>' after a
        # SALE (the customer returns goods). Deterministic - never invents
        # a name when no prior party exists (Sprint 15E).
        low_seg = " " + segment.lower() + " "
        if prior_party and _party_from_text(segment) is None and (
                "returned goods" in low_seg or "goods returned" in low_seg
                or "returned stock" in low_seg or "stock returned" in low_seg):
            prior_journal = journals[-1] if journals else None
            is_sale_prior = bool(prior_journal) and any(
                (line.get("account") or "") == "Sales"
                for line in (prior_journal.get("credit_lines") or []))
            joiner = "by" if is_sale_prior else "to"
            segment = segment.rstrip(". ") + f" {joiner} {prior_party}."
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
