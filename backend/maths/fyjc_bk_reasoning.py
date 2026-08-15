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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
    # Sprint 15I-K GST accounts: input tax credit (asset) and output tax
    # payable (liability) are both Real accounts in the traditional FYJC
    # threefold classification. Without the override a Capitalised word
    # would be read as a Personal account (a party).
    "Input CGST": CLASS_REAL, "Input SGST": CLASS_REAL,
    "Input IGST": CLASS_REAL, "Output CGST": CLASS_REAL,
    "Output SGST": CLASS_REAL, "Output IGST": CLASS_REAL,
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
    # Sprint 15I-J: expense accounts added with the synonym vocabulary -
    # a single Capitalised word is otherwise read as a PERSONAL account
    # ("Conveyance" would become a party). Nominal, like every expense.
    "Conveyance": CLASS_NOMINAL, "Printing": CLASS_NOMINAL,
    "Telephone Expenses": CLASS_NOMINAL,
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
# Sprint 15I-L: word-percent rate token ('10 percent', '10 per cent',
# '10 per-cent'). Only the number is captured; the label window picks up
# 'trade'/'cash discount' exactly like a '%' token.
_WORD_PERCENT_TOKEN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s+per(?:[\- ])?cent\b")
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
        # Sprint 15I-L: a word-percent rate token ('10 percent trade
        # discount', '2 per cent cash discount') is a RATE like '10%',
        # never a money amount - the digit must not leak into the
        # amounts list ('2 percent' can never read as Rs.2 paid).
        if re.match(r"\s*per(?:[ -]?cent)\b",
                    str(text)[match.end():match.end() + 14].lower()):
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
    text so 'trade discount' vs 'cash discount' can be told apart.

    Sprint 15I-L: a word-percent token ('10 percent trade discount',
    'less 10 per cent') is the same rate with explicit wording - it is
    parsed the same way and labeled identically. A '%' token and its
    word-percent twin for the SAME number in the SAME question are
    deliberately NOT deduplicated here: a question that states both
    ('10% and 10 percent') is contradictory evidence and must be caught
    by the downstream rate-consistency gates, never silently collapsed."""
    out: List[Tuple[Decimal, str]] = []
    low = " " + str(text or "").lower() + " "
    seen_spans: List[Tuple[int, int]] = []
    for match in _PERCENT_TOKEN.finditer(low):
        try:
            rate = Decimal(match.group(1))
        except (InvalidOperation, ValueError):
            continue
        before = low[max(0, match.start() - 24):match.start()]
        after = low[match.end():match.end() + 24]
        label = " ".join((before + after).split())
        out.append((rate, label))
        seen_spans.append((match.start(), match.end()))
    # word-percent: '10 percent' / '10 per cent' / '10 per-cent'. A
    # number ALREADY consumed by a '%' token is never re-read (the same
    # '10' in '10%' must not yield two rate rows).
    for match in _WORD_PERCENT_TOKEN.finditer(low):
        number_span = (match.start(), match.start() + len(match.group(1)))
        if any(s <= number_span[0] and number_span[1] <= e
               for s, e in seen_spans):
            continue
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
        # Sprint 15I-L: 'cash discount' rates are never paid portions. The
        # bare word 'cash' is not enough - a payment-fraction percent must
        # be tied to an actual payment verb ('paid'/'immediately'/'at
        # once') so 'allowed 5% cash discount at settlement' is never read
        # as a 5% payment.
        if any(k in window for k in ("paid", "immediately", "at once")):
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
                 # Sprint 15I-J synonym + misspelling coverage - each
                 # entry resolves through _EXPENSE_ACCOUNT_WORDS to the
                 # ONE canonical expense account it names.
                 "paid conveyance", "paid conveyance charges",
                 "paid conveyance expenses",
                 "paid transport", "paid transportation",
                 "paid travelling expenses", "paid travel expenses",
                 "paid transport charges", "paid transport expenses",
                 "paid printing", "paid printing charges",
                 "paid mobile", "paid mobile bill", "paid phone",
                 "paid phone bill", "paid telephone charges",
                 "paid telephone expenses",
                 "paid electrisity", "paid sallery", "paid salery",
                 "paid stionary", "paid telefone", "paid telphone",
                 "paid convayance", "paid convayence",
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
        "when": ("paid to", "paid cash to", "paid ... to",
                 # Sprint 15I-J: 'gave' wordings are student cash
                 # payments to a party, not receipts.
                 "gave cash to", "gave money to", "gave ... to"),
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
    # Sprint 15I-J synonym layer (longest phrase first within each family;
    # each entry has one explicit accounting meaning, pinned by the 15J
    # coverage matrix - never a blind mapping).
    ("transport charges", "Conveyance"),
    ("transport expenses", "Conveyance"),
    ("transportation", "Conveyance"),
    ("transport", "Conveyance"),
    ("conveyance charges", "Conveyance"),
    ("conveyance expenses", "Conveyance"),
    ("conveyance", "Conveyance"),
    ("travelling expenses", "Conveyance"),
    ("travel expenses", "Conveyance"),
    ("travelling", "Conveyance"),
    ("travel", "Conveyance"),
    ("printing charges", "Printing"),
    ("printing expenses", "Printing"),
    ("printing", "Printing"),
    ("mobile bill", "Telephone Expenses"),
    ("mobile charges", "Telephone Expenses"),
    ("mobile", "Telephone Expenses"),
    ("phone bill", "Telephone Expenses"),
    ("phone", "Telephone Expenses"),
    ("telephone charges", "Telephone Expenses"),
    ("telephone expenses", "Telephone Expenses"),
    # Sprint 15I-J common student misspellings (exact-token only, with
    # an explicit account meaning - never fuzzy matching).
    ("electrisity", "Electricity"),
    ("sallery", "Salaries"),
    ("salery", "Salaries"),
    ("stionary", "Stationery"),
    ("telefone", "Telephone Expenses"),
    ("telphone", "Telephone Expenses"),
    ("convayance", "Conveyance"),
    ("convayence", "Conveyance"),
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


# Sprint 15I-D / 15I-F P1-B: words that can never be the party SUBJECT of
# a payment sentence - bare auxiliary verbs ('Was paid Rs.X.' has NO
# subject), pronouns and the payment verb itself. Shared by
# _party_from_text and the classify_bk_type m_party_paid path so the two
# voice-resolution branches stay structurally consistent.
_NON_PARTY_PAYMENT_SUBJECTS = (
    "paid", "he", "she", "they", "the", "we", "i", "him", "her", "them",
    "it", "was", "were", "has", "have", "had", "is", "are", "be", "been",
    "being", "am", "does", "did",
)


# Sprint 15I-P: words that can NEVER be a LOWERCASE party. A lowercase
# token is accepted as a party only when the surrounding transaction
# structure already identifies it ('from <name>', 'to <name>', 'received
# ... from <name>', 'paid ... to <name>') AND the token is an ordinary
# person's name - never an arbitrary lowercase word (the bank, the
# seller, cash, credit, ...). Capitalised proper nouns keep the existing
# deterministic path untouched.
_LOWERCASE_NON_PARTY_WORDS = frozenset({
    # articles / pronouns / function words
    "the", "a", "an", "his", "her", "their", "them", "him", "us", "you",
    "me", "we", "they", "he", "she", "it", "i", "my", "our", "your",
    "this", "that", "these", "those", "some", "any", "all", "each",
    "every", "both", "such", "same", "who", "whom", "whose", "which",
    "one", "two", "three",
    # money / credit machinery
    "cash", "bank", "cheque", "check", "credit", "loan", "overdraft",
    "money", "amount", "payment", "paid", "price", "rate", "list",
    "net", "gross",
    # chart / account words
    "goods", "stock", "inventory", "purchases", "purchase", "sales",
    "sale", "returns", "return", "drawings", "capital", "assets",
    "liabilities", "expenses", "expense", "income", "revenue", "profit",
    "loss", "account", "accounts", "ledger", "journal",
    # generic counterparty nouns (never proper names)
    "party", "parties", "customer", "customers", "supplier", "suppliers",
    "seller", "sellers", "vendor", "vendors", "buyer", "buyers",
    "debtor", "debtors", "creditor", "creditors", "firm", "company",
    "shop", "store", "business", "trader", "traders", "person", "people",
    # expense / income words that could follow 'paid' / 'received'
    "rent", "salary", "salaries", "wages", "commission", "interest",
    "insurance", "electricity", "advertisement", "advertising",
    "stationery", "repairs", "carriage", "freight", "postage", "tax",
    "taxes", "fee", "fees", "discount", "dividend", "conveyance",
    "travelling",
})


def _normalise_party_token(party: str) -> Optional[str]:
    """Sprint 15I-P: gate + normalise a party token that is not already
    a normal proper noun.

    A lowercase token is only accepted when it is an ordinary person's
    name - every word must be free of the ordinary-word blocklist
    (never 'the seller', 'the shop', 'cash', 'bank', ...). Accepted
    names are normalised deterministically: 'rahul' -> 'Rahul',
    'ravi kumar' -> 'Ravi Kumar'. An ALL-CAPS token ('RAHUL') is the
    same name in shouting case and is normalised the same way. Already
    properly-cased tokens (first letter upper, not all caps) pass
    through unchanged (historical behavior preserved).
    """
    if not party:
        return None
    words = [w for w in str(party).strip().split() if w]
    if not words:
        return None
    first_upper = party[0].isupper()
    if first_upper and not party.isupper():
        # Already-capitalised token: keep the historical deterministic
        # pass-through when it is already canonical Title Case
        # ('Rahul', 'Sharma & Sons', "D'Souza"). A MIXED-CASE
        # capitalised token ('RaHuL') is not a properly-cased proper
        # noun and is normalised deterministically below, like
        # lowercase and ALL-CAPS forms.
        if party == party.title():
            return party
    if any(w.lower() in _LOWERCASE_NON_PARTY_WORDS for w in words):
        return None
    return " ".join(w[:1].upper() + w[1:].lower() for w in words)


def _party_from_text(text: str) -> Optional[str]:
    """Extract a party from the description.

    Capitalised proper nouns keep the Sprint 15B/15F/15H deterministic
    behavior. Sprint 15I-P additionally accepts a LOWERCASE name when the
    surrounding structure clearly identifies the token as the party
    ('from rahul', 'received ... from amit', 'paid ... to mehta',
    'sold goods to kavita'); the token is gated against ordinary words so
    FT-E never invents a party from an arbitrary lowercase word.
    """
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
            m = re.match(r"\s*([A-Za-z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+worth\s+"
                         r"|\s+and\s+|\s+in\s+|\s+at\s+|\s+₹|\s+Rs|\s+\d|,|$)",
                         rest, re.IGNORECASE)
            if m:
                party = m.group(1).strip().rstrip(".;,")
                if party and not party.lower().endswith(
                        ("a/c", "account", "ltd", "limited")):
                    party = _normalise_party_token(party)
                    if party:
                        return party
    # '<Party> paid ...' - the party is the SUBJECT of the payment verb
    # (a receipt to the business: 'Mohan paid Rs.12,000', 'rahul paid
    # Rs.12,000'). The subject position before 'paid' is deterministic -
    # never an invented name.
    m_subj = re.match(r"\s*([A-Za-z][A-Za-z' .]{1,40}?)\s+paid\b",
                      str(text or ""), re.IGNORECASE)
    if m_subj:
        subject = _strip_aux_before_verb(
            m_subj.group(1).strip().rstrip(".;,"))
        # Sprint 15I-D: a bare auxiliary verb is never a party - 'Was paid
        # Rs.5,000.' has NO subject, so the aux must not become the account.
        if subject.lower() not in _NON_PARTY_PAYMENT_SUBJECTS:
            subject = _normalise_party_token(subject)
            if subject:
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
    # Sprint 15I-J: bullets (\u2022 etc.) and a Capital-letter-joined 'and'
    # ('Sold goods to Ram Rs.12,000 and Mohan was paid Rs.5,000.') are
    # ALSO compound separators - without them the tail's own identity
    # would be silently absorbed into the head transaction (a confident
    # wrong journal). Lower-case 'and' joins ('cash and furniture') never
    # split. Any part after the first is checked, so a 3+ part compound
    # is refused too, never combined.
    _c_parts = re.split(
        r";\s*| - |(?<=[a-z0-9)])\.\s+(?=[A-Z])|"
        r"\s+and\s+(?=[A-Z])|\s*[\u2022\u25E6\u25AA]\s+(?=[A-Z])",
        text)
    if len(_c_parts) > 1:
        _c_head, _c_tail = _c_parts[0], _c_parts[-1]
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
            # a currency token is never a party name: 'Paid Rs.9,800
            # and received Rs.200 cash discount' is a settlement step,
            # not a compound with its own party identity (Sprint 15I-L).
            r"\s*Paid\s+(?!(?:Rs\.?|INR|\u20b9)\b)[A-Z][A-Za-z' .]"
            r"{1,40}?(\s|,|\.)", _c_tail))
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
    # Sprint 15I-J: the passive-expense vocabulary now includes the
    # synonym layer and the common misspellings, so 'Conveyance was
    # paid', 'Transport was paid' and 'Electrisity bill was paid' resolve
    # to the same EXPENSE_PAID treatment as their standard forms.
    m_exp = re.search(
        r"\b(rent|salary|salaries|wages|insurance|electricity|"
        r"advertisement|commission|interest|carriage|repairs|postage|"
        r"stationery|audit fees|legal fees|income tax|fuel|telephone|"
        r"conveyance|transport|transportation|printing|mobile|phone|"
        r"telephone charges|transport charges|electrisity|sallery|salery|"
        r"stionary|telefone|telphone|convayance|convayence)\b"
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
        if (not m_party_paid_name
                or m_party_paid_name.lower() in _NON_PARTY_PAYMENT_SUBJECTS):
            # Sprint 15I-D guard parity (Sprint 15I-F P1-B): a bare
            # auxiliary verb is never a party on this path either -
            # 'Was paid Rs.3,000 transport.' must never let 'Was' become
            # the account of a PAID_TO journal.
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
    # Sprint 15I-J: 'Deposited Rs.10,000 into bank' / 'Paid Rs.8,000 into
    # the bank' - the amount sits between the verb and 'bank', so the
    # contiguous registry phrases cannot match. The direction is
    # structural (deposit/paid ... into bank = Bank Dr / Cash Cr), never
    # inferred from the word 'cash'. A cheque deposit is handled by the
    # cheque rules above, never here (no silent cheque-as-cash).
    if re.search(
            r"\b(?:deposited|depositing|paid)\b.*?\binto\s+"
            r"(?:the\s+)?bank\b", low) \
            and "cheque" not in low and "check" not in low:
        return {
            "key": "CASH_INTO_BANK",
            "label": "Cash deposited into bank",
            "debit": ["Bank"], "credit": ["Cash"],
        }
    # Sprint 15I-F P1-C: opening a bank account ('Opened an account with
    # Bank of India Rs.20,000') is a deterministic BANK Dr / Cash Cr
    # transaction - the money moves from the till into the new account.
    # A cheque/check opening would credit Bank instead of Cash (not this
    # pattern) - the phrase then simply does not match and the
    # transaction falls through to the refusal layer. Only the named
    # opening wording fires; nothing is guessed.
    if any(k in low for k in ("opened an account", "opened a bank account",
                              "opened a current account",
                              "opened a savings account")) \
            and "cheque" not in low and "check" not in low:
        return {
            "key": "BANK_ACCOUNT_OPENED",
            "label": "Bank account opened",
            "debit": ["Bank"], "credit": ["Cash"],
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
        if cand["key"] == "PAID_TO" and ("paid" in low or "gave" in low) \
                and "cheque" not in low and "check" not in low \
                and (re.search(r"\bpaid\s+[a-z]", low)
                     or re.search(r"\bpaid\b.*\bto\b", low)
                     # Sprint 15I-J: 'gave' is a student verb for a cash
                     # payment - 'Gave cash to Mohan Rs.5,000' / 'Gave
                     # Rs.5,000 cash to Mohan' (the amount often sits
                     # between the verb and the party).
                     or re.search(r"\bgave\b.*\bto\b", low)
                     or "gave cash to" in low or "gave money to" in low):
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

# Sprint 15I-L: settlement-side discount-rate hints. A rate carrying these
# words ('after allowing 10% discount', 'after receiving 5% discount',
# 'discount allowed on the amount paid') is a CASH discount at settlement,
# never a trade discount netting the list price.
_CD_RATE_HINTS = re.compile(
    r"after allowing|after receiving|allowed (?:him|her|them)?\s|"
    r"received discount|allowed discount|discount allowed|"
    r"discount received|on settlement|at settlement|on the amount paid",
    re.IGNORECASE)


def _cash_discount_amt_in(low: str) -> Optional[Decimal]:
    """The stated CASH-discount AMOUNT in normalized text, or None.
    Matches the amount BEFORE the 'cash discount' noun ('received Rs.500
    cash discount', 'after Rs.500 cash discount'). The post-noun forms
    ('discount allowed Rs.200') are handled by _detect_explicit_discount;
    a '%' adjacent to the figure means it is a rate, never an amount."""
    m = re.search(
        r"(?:allowed|received|after)\s+(?:rs\.?|\u20b9|inr)?\s*"
        r"(\d[\d,]*(?:\.\d+)?)\s+cash\s+discount\b", low)
    if not m:
        return None
    after = low[m.end():m.end() + 2]
    if after.lstrip().startswith("%"):
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _gst_trade_discount(text: str) -> Optional[Tuple[Decimal, str]]:
    """(td_amount, kind) from EXPLICIT trade-discount evidence, or None.
    Only 'trade discount' (or the unambiguous 'TD' abbreviation) qualifies
    with GST; a bare or cash discount is a settlement concept and never
    nets the taxable value. Returns None when the discount cannot be read
    deterministically - the caller then refuses (never guesses)."""
    low = " " + str(text or "").lower() + " "
    if "trade discount" not in low and not re.search(r"\btd\b", low):
        return None
    m_r = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:trade\s+discount|td)\b",
        low)
    if m_r:
        try:
            rate = Decimal(m_r.group(1))
        except (InvalidOperation, ValueError):
            rate = None
        if rate is not None and 0 < rate <= Decimal(100):
            return (rate, "rate")
        return None
    m_a = re.search(
        r"less\s+(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
        r"\s+(?:trade\s+discount|td)\b", low)
    if m_a is None:
        m_a = re.search(
            r"(?:trade\s+discount|td)\s+(?:of\s+)?"
            r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
            low)
    if m_a:
        try:
            return (Decimal(m_a.group(1).replace(",", "")), "amount")
        except (InvalidOperation, ValueError):
            return None
    return None


# Sprint 15I-L: a PURE settlement ('Received Rs.X from <party>',
# 'Paid <party> Rs.X') carries no transaction value of its own - a
# stated cash-discount rate there has no amount due to apply to and
# must refuse rather than relabel the settlement figure. The amount
# can sit between the verb and the direction word ('Received Rs.9,800
# from Ram', 'Paid him Rs.9,800'), so the relationship is tested
# structurally, never as a contiguous phrase.


def _rate_is_paid_based(low: str, rate: Decimal) -> bool:
    """True when the rate token is followed by 'on the (amount) paid'
    / 'on the paid amount' - the rate applies to the PAID figure itself
    (gross), never to an amount due. Read from the full text window
    after the token (the 24-char rate label truncates the phrase).
    """
    for m in _PERCENT_TOKEN.finditer(low):
        try:
            if Decimal(m.group(1)) == rate:
                after = low[m.end():m.end() + 60]
                if re.search(r"on\s+(?:the\s+)?(?:amount\s+paid|"
                             r"paid\s+amount|amount)\b", after):
                    return True
        except (InvalidOperation, ValueError):
            continue
    for m in _WORD_PERCENT_TOKEN.finditer(low):
        try:
            if Decimal(m.group(1)) == rate:
                after = low[m.end():m.end() + 60]
                if re.search(r"on\s+(?:the\s+)?(?:amount\s+paid|"
                             r"paid\s+amount|amount)\b", after):
                    return True
        except (InvalidOperation, ValueError):
            continue
    return False
_SETTLEMENT_VALUE_RE = re.compile(
    r"\b(?:goods|stock|purchased|purchase|purchases|bought|sold|sale|"
    r"wages|rent|salary|electricity|telephone|stationery|carriage|"
    r"postage|commission|insurance|interest|machinery|furniture|"
    r"equipment|computer|vehicle|building|drawings|loan)\b")


def _settlement_only_text(low: str) -> bool:
    """True when the text is a settlement with no transaction value."""
    if _SETTLEMENT_VALUE_RE.search(low):
        return False
    received = (re.search(r"\breceived\b", low)
                and re.search(r"\bfrom\b", low))
    paid = (re.search(r"\bpaid\b", low)
            and re.search(r"\b(?:to|him|her|them)\b", low))
    return bool(received or paid)


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
    # Sprint 15I-L: an explicit CASH-discount AMOUNT can appear BEFORE
    # the 'cash discount' noun ('allowed Rs.500 cash discount', 'received
    # Rs.500 cash discount', 'after Rs.500 cash discount') - the same
    # explicit-settlement evidence as the post-noun forms.
    _cash_discount_before = re.search(
        r"(?:allowed|received|after)\s+(?:rs\.?|\u20b9|inr)?\s*"
        r"(\d[\d,]*(?:\.\d+)?)\s+cash\s+discount\b", low)
    if not (allowed or received or settlement_only
            or _cash_discount_before) or len(amounts) < 2:
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
    if kind is None and _cash_discount_before:
        # Sprint 15I-L before-noun direction (deterministic):
        #   * 'paid ... and received ... cash discount' -> the supplier
        #     granted us the discount (Discount Received);
        #   * 'Received X from <party> after ... cash discount' -> the
        #     debtor settled at a discount (Discount Allowed);
        #   * 'Paid <party> ... after ... cash discount' -> supplier
        #     discount (Discount Received);
        #   * 'allowed ... cash discount' -> Discount Allowed.
        if "paid" in low and "received" in low:
            kind = "received"
        elif "received" in low and "from " in low and "paid" not in low:
            kind = "allowed"
        elif "paid" in low and "to " in low:
            kind = "received"
        elif "allowed" in low:
            kind = "allowed"
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
    if discount_amount is None and _cash_discount_before:
        # Sprint 15I-L: amount BEFORE the 'cash discount' noun ('allowed
        # Rs.500 cash discount', 'after Rs.500 cash discount').
        after = low[_cash_discount_before.end():_cash_discount_before.end() + 2]
        if not after.lstrip().startswith("%"):
            try:
                discount_amount = Decimal(
                    _cash_discount_before.group(1).replace(",", ""))
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
    # deducted from the list price BEFORE the amount is posted. Sprint
    # 15I-L: 'less 10%' names a trade discount too; a rate on the
    # SETTLEMENT side ('after allowing 10% discount', 'after receiving
    # 5% discount', 'discount allowed at settlement') is a CASH discount
    # and is never read as a trade discount.
    trade_rate: Optional[Decimal] = None
    for rate, label in percents:
        if "trade" in label:
            trade_rate = rate
            break
    if trade_rate is None:
        for rate, label in percents:
            if ("discount" in label or "less" in label) \
                    and "cash discount" not in label \
                    and not _CD_RATE_HINTS.search(label):
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
    # Sprint 15I-L: an explicit TRADE-discount AMOUNT ('less Rs.2,000
    # trade discount', 'less Rs.2,000 TD', 'trade discount of Rs.2,000')
    # nets the list price the same way as a rate and is NEVER recorded as
    # a separate account. It only applies when the question names 'trade
    # discount' (or the unambiguous 'TD' abbreviation) explicitly.
    if trade_amount is None and re.search(
            r"\btrade\s+discount\b|\btd\b", low):
        m_td = re.search(
            r"less\s+(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
            r"\s+(?:trade\s+discount|td)\b", low)
        if m_td is None:
            m_td = re.search(
                r"(?:trade\s+discount|td)\s+(?:of\s+)?"
                r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                low)
        if m_td is not None:
            after = low[m_td.end():m_td.end() + 2]
            if after.lstrip().startswith("%"):
                m_td = None
        if m_td is not None:
            try:
                td_amt = Decimal(m_td.group(1).replace(",", ""))
            except (InvalidOperation, ValueError):
                td_amt = None
            if td_amt is not None and 0 < td_amt < list_price:
                trade_amount = td_amt
                steps.append({
                    "calculation_id": "BK_TRADE_DISCOUNT_AMOUNT",
                    "label": "Deduct Trade Discount",
                    "formula": "Trade discount amount from the question",
                    "inputs": {"list_price": list_price,
                               "trade_discount_amount": td_amt},
                    "result": td_amt,
                })
            elif td_amt is not None:
                # impossible discount: the refusal below fires, never a
                # negative journal.
                trade_amount = td_amt
    net_value = list_price - trade_amount if trade_amount is not None \
        else list_price
    if trade_amount is not None and (trade_amount <= 0
                                     or trade_amount >= list_price):
        return {
            "status": REVIEW_REQUIRED, "steps": steps,
            "list_price": list_price, "trade_discount_rate": trade_rate,
            "trade_discount_amount": trade_amount, "net_value": None,
            "paid_amount": None, "credit_amount": None,
            "cash_discount_rate": None, "cash_discount_amount": None,
            "cash_paid": None, "explicit_discount": None,
            "concerns": concerns,
            "why_not": ("The stated trade discount is impossible (it is not "
                        "positive and smaller than the list price). FT-E "
                        "never records it."),
            "next_action": "Re-check the discount amount or rate.",
        }
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

    # Sprint 15I-L: the stated CASH-discount AMOUNT ('discount allowed
    # Rs.200', 'received Rs.500 cash discount') is a settlement figure -
    # the naive paid-split must never read it as the paid amount. When a
    # discount is mentioned but its amount cannot be read, the split is
    # refused instead of silently dropping the discount.
    cash_discount_amt: Optional[Decimal] = None
    if explicit_discount is not None:
        cash_discount_amt = explicit_discount["discount_amount"]
    elif _cash_discount_amt_in(low) is not None:
        cash_discount_amt = _cash_discount_amt_in(low)

    # -- paid vs credit split --------------------------------------------
    paid_amount: Optional[Decimal] = None
    credit_amount: Optional[Decimal] = None
    # Sprint 15I-L: a settlement figure EXPLICITLY STATED in the
    # question ('paid Rs.9,800', 'Received Rs.9,800 from Ram') is NET
    # evidence - a cash-discount rate must apply to the amount due,
    # never to the stated figure itself.
    paid_stated = False
    if explicit_discount is None:
        explicit_paid = None
        # an explicit paid amount ('paid Rs.4,000 immediately'); a
        # RECEIPT counts as a stated settlement only when it carries a
        # resolvable discount amount or a settlement-side discount rate
        # ('Received Rs.9,800 from Ram, after allowing 2% cash
        # discount') - a plain receipt inside a multi-transaction text
        # must never be merged into the previous transaction's amounts
        # (Sprint 15I-L).
        _received_settlement = ("received" in low
                                and (cash_discount_amt is not None
                                     or any(_CD_RATE_HINTS.search(label)
                                            for _, label in percents)))
        if len(amounts) >= 2 and ("paid" in low or "immediately" in low
                                  or _received_settlement):
            _paid_candidates = [a for a in amounts
                                if cash_discount_amt is None
                                or a != cash_discount_amt]
            explicit_paid = (_paid_candidates[-1] if _paid_candidates
                             else amounts[-1])
        fraction = _paid_fraction(question)
        if explicit_paid is not None and explicit_paid < net_value:
            paid_amount = explicit_paid
            paid_stated = True
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
        # Sprint 15I-L: a discount WORD whose amount/rate cannot be read is
        # missing required information - refuse instead of silently
        # dropping the discount from the journal. Fires only when several
        # figures are present (a single amount cannot hide a discount) and
        # no discount amount or rate was resolvable.
        if ("discount" in low and len(amounts) >= 2
                and cash_discount_amt is None
                and trade_amount is None
                and not any("discount" in label for _, label in percents)
                and re.search(
                    r"\b(?:cash\s+)?discount\s+(?:allowed|received|of)\b"
                    # a readable amount right after the discount word means
                    # the discount IS resolvable ('discount allowed Rs.200')
                    # - nothing is being silently dropped (Sprint 15I-L).
                    r"(?!\s*(?:rs\.?|\u20b9|inr|\d))"
                    r"|\b(?:allowed|received)\s+discount\b"
                    r"(?!\s*(?:rs\.?|\u20b9|inr|\d))",
                    low)):
            concerns.append(
                "A discount is mentioned but its amount cannot be read "
                "deterministically. FT-E never silently drops it.")

    # -- Cash discount (paid portion only) --------------------------------
    cash_discount_rate: Optional[Decimal] = None
    # ONLY a literal 'cash discount' phrase (or an explicit settlement-side
    # rate: 'after allowing 10% discount', 'after receiving 5% discount',
    # 'discount allowed on the amount paid') is a cash discount. A trade
    # discount (or a plain 'discount' that is not cash) only nets the list
    # price and is never recorded as a cash discount - so 'for cash ... with
    # 10% trade discount' must NOT produce a cash-discount line.
    cd_rate_label: Optional[str] = None
    for rate, label in percents:
        if "cash discount" in label or _CD_RATE_HINTS.search(label):
            cash_discount_rate = rate
            cd_rate_label = label
            break
    cash_discount_amount = None
    cash_paid = paid_amount
    if cash_discount_rate is not None and paid_amount is not None:
        # 'on the (amount) paid' / 'on the paid amount' anchors the rate
        # to the PAID figure itself (gross): the discount is computed on
        # that payment ('paid him Rs.3,000 immediately and 2% cash
        # discount on the paid amount' -> 2% of 3,000 = 60), never
        # reconciled against the transaction value (Sprint 15I-L).
        paid_based_rate = _rate_is_paid_based(low, cash_discount_rate)
        if _settlement_only_text(low) and explicit_discount is None \
                and not paid_based_rate:
            # a pure receipt/payment with a discount rate but NO stated
            # amount due ('Received Rs.9,800 from Ram, after allowing 2%
            # cash discount' with no 'his account of') - applying the
            # rate to the settlement figure itself would silently invent
            # a new cash amount, so it refuses (Sprint 15I-L).
            concerns.append(
                "A cash discount rate is stated but the amount due is not "
                "given. FT-E never applies a discount rate to the "
                "settlement figure itself.")
        elif paid_stated and not paid_based_rate:
            # the stated cash/paid figure is the NET settlement; the
            # rate applies to the amount due (party total from the
            # merged transaction value or an explicit settlement
            # phrase). A non-reconciling combination is contradictory
            # and refuses (never a guessed discount).
            base = (explicit_discount["party_total"]
                    if explicit_discount is not None else net_value)
            expected_discount = (base * cash_discount_rate
                                 / Decimal(100)).quantize(
                                     Decimal("0.01"))
            expected_cash = base - expected_discount
            if expected_cash == paid_amount:
                cash_discount_amount = expected_discount
                cash_paid = paid_amount
                if credit_amount is not None:
                    credit_amount = max(Decimal(0), credit_amount
                                        - expected_discount)
                steps.append({
                    "calculation_id": "BK_CASH_DISCOUNT_AMOUNT",
                    "label": "Apply Cash Discount (settlement rate)",
                    "formula": "Cash discount = Amount due x rate",
                    "inputs": {"amount_due": base,
                               "cash_discount_rate": cash_discount_rate,
                               "stated_cash": paid_amount},
                    "result": expected_discount,
                })
            else:
                concerns.append(
                    "The stated cash figure does not reconcile with the "
                    "stated discount rate on the amount due. FT-E never "
                    "applies the rate to the cash figure itself.")
        else:
            # a payment DERIVED from the question ('paid half
            # immediately with 2% cash discount', or a full-cash
            # transaction value 'Sold goods for cash Rs.10,000, discount
            # allowed 2%') has no separate stated net - the rate applies
            # to the derived payment/value.
            cash_discount_amount = (paid_amount * cash_discount_rate
                                    / Decimal(100)).quantize(
                                        Decimal("0.01"))
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
    # Sprint 15I-L: an amount-based cash discount from a merged
    # settlement step ('Purchased goods from Rahul ... Paid him Rs.9,500
    # and received Rs.500 cash discount'): the stated cash figure is the
    # net cash out, the stated discount is the discount - the gross
    # amount settled is cash + discount and the credit remainder shrinks
    # by the discount. A combination that exceeds the transaction value
    # is contradictory and refuses (never an invented discount).
    elif (cash_discount_amt is not None and paid_amount is not None
          and cash_discount_amount is None):
        if paid_amount + cash_discount_amt > net_value:
            concerns.append(
                "The cash amount and the cash discount together exceed the "
                "transaction value. FT-E never records them.")
        else:
            cash_discount_amount = cash_discount_amt
            cash_paid = paid_amount
            if credit_amount is not None:
                credit_amount = max(Decimal(0), credit_amount
                                    - cash_discount_amt)
            steps.append({
                "calculation_id": "BK_CASH_DISCOUNT_AMOUNT",
                "label": "Apply Cash Discount (stated amount)",
                "formula": "Cash discount from the question",
                "inputs": {"paid_amount": paid_amount,
                           "cash_discount_amount": cash_discount_amt},
                "result": cash_discount_amt,
            })

    # Sprint 15I-L: a cash-discount rate without a payment step has
    # nothing to apply it to ('allowed 5% cash discount at settlement'
    # with no payment amount) - the discount is ambiguous and must not be
    # silently dropped or guessed.
    if cash_discount_rate is not None and paid_amount is None:
        concerns.append(
            "A cash discount is stated but no payment amount is given to "
            "apply it to. FT-E never applies a discount without a "
            "settlement step.")

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
        # Sprint 15I-L: expose the settlement numbers so a PURCHASE whose
        # settlement step carried the discount ('Purchased goods from Rahul
        # ... Paid him Rs.9,500 and received Rs.500 cash discount') can post
        # the COMPOUND journal (Purchases Dr net / Cash Cr cash + Discount
        # Received Cr discount [+ party Cr remainder]) instead of a
        # settlement-only entry that would drop the purchase account. The
        # remainder is the un-settled credit balance; a negative remainder
        # means the numbers do not describe this transaction's settlement
        # and the explicit path (not the compound path) stays authoritative.
        paid_amount = explicit_discount["cash_amount"]
        cash_discount_amount = explicit_discount["discount_amount"]
        cash_paid = explicit_discount["cash_amount"]
        remainder = net_value - paid_amount - cash_discount_amount
        credit_amount = remainder if remainder > 0 else None

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
            # Sprint 15I-F P0-A: em dash (\u2014), en dash (\u2013) and
            # newline act as transaction boundaries under the SAME
            # lookbehind/lookahead guards as the period rule - only a
            # Capital-letter start after a digit/lower-case/')' splits, so
            # an intra-transaction dash or a continuation sentence is never
            # split. The ASCII-hyphen compound refusal from Sprint 15I-D is
            # untouched (an ASCII hyphen is NOT a splitter boundary;
            # classify_bk_type still catches own-identity compounds).
            # Sprint 15I-K: '%' joins the lookbehind set - a GST clause
            # ending in a rate ('...CGST @ 9% and SGST @ 9%. Sold goods
            # ...') is a real sentence boundary, never a swallowed second
            # transaction.
            r"(?<=[a-z0-9)%])\.\s+(?=[A-Z])|"
            r"(?<=[0-9)])(?:\u2014|\u2013)\s+(?=[A-Z])|"
            r"(?<=[0-9)]\s)(?:\u2014|\u2013)\s+(?=[A-Z])|"
            r"(?<![A-Za-z])\n\s*(?=[A-Z])|"
            # Sprint 15I-J: bullet separators (\u2022 / \u25E6 / \u25AA)
            # are explicit transaction boundaries - a bullet followed by a
            # Capital letter splits, so 'Rs.12,000 \u2022 Mohan was paid
            # Rs.5,000.' never silently absorbs the second transaction
            # into the first as a partial payment.
            r"\s*[\u2022\u25E6\u25AA]\s+(?=[A-Z])|"
            r",\s+(?=returned (?:goods|stock)|goods returned|"
            r"purchases returns|purchases return|sales returns|"
            r"sales return)", part, flags=re.IGNORECASE))
    segments = [seg.replace("\x01", ". ").strip() for seg in pieces
                if seg.strip()]
    # Sprint 15I-K: a trailing segment that is ONLY a GST component
    # ('CGST @ 9% and SGST @ 9%', 'SGST Rs.900', '• IGST @ 18%') belongs
    # to the previous transaction's GST clause - textbooks often
    # sentence-break or bullet the tax line. It is rejoined, never
    # journaled as an independent transaction. A segment carrying any
    # transaction word stays independent.
    frag_merged: List[str] = []
    for seg in segments:
        low_seg = " " + seg.lower() + " "
        is_fragment = bool(re.match(
            r"\s*(?:and\s+)?(?:input\s+|output\s+)?(?:cgst|sgst|igst)\b",
            seg, re.IGNORECASE)) and not any(
                v in low_seg for v in _GST_FRAGMENT_VERBS)
        if is_fragment and frag_merged:
            frag_merged[-1] = frag_merged[-1] + "; " + seg
        else:
            frag_merged.append(seg)
    merged: List[str] = []
    for seg in frag_merged:
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


# ---------------------------------------------------------------------------
# Sprint 15I-K - deterministic GST layer
#
# GST is a deterministic domain of its own. When ANY GST evidence is
# present, ONLY the GST journal path may post: the plain patterns would
# silently drop the tax from the journal (a safety bug). Every GST fact
# the syllabus rule requires - rate, components, inclusive/exclusive
# mode, intra/inter-state status - must be explicitly supported by the
# question wording. Ambiguity is REVIEW_REQUIRED, never a guess:
#   * CGST + SGST requires explicit evidence (both components named, or
#     an explicit intra-state marker with a single GST rate);
#   * IGST requires explicit evidence (IGST named, or an explicit
#     inter-state marker);
#   * a bare 'GST @ r%' with no component or state evidence is ambiguous
#     -> REVIEW_REQUIRED.
# ---------------------------------------------------------------------------

_GST_TOKEN_RE = re.compile(
    r"\b(?:gst|cgst|sgst|igst)\b|goods and services tax", re.IGNORECASE)
_GST_COMPONENT_RE = re.compile(
    r"\b(?:input\s+|output\s+)?(?:cgst|sgst|igst)\b", re.IGNORECASE)

# A whole GST clause (rate + optional component tail) - used to strip GST
# phrases out of the text so the UNDERLYING transaction can be classified
# deterministically. Never strips transaction words.
_GST_PHRASE = re.compile(
    r"(?:inclusive\s+of\s+|including\s+|exclusive\s+of\s+|excluding\s+|"
    r"plus\s+|in\s+addition\s+to\s+|extra\s+with\s+|with\s+|at\s+)?"
    r"(?:goods\s+and\s+services\s+tax|gst|"
    r"(?:input\s+|output\s+)?(?:cgst|sgst|igst))"
    r"(?:\s*[@-]?\s*(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*%?)?"
    r"(?:\s*(?:and|&)\s*(?:input\s+|output\s+)?(?:cgst|sgst)"
    r"(?:\s*[@-]?\s*(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*%?)?)?",
    re.IGNORECASE)

# A trailing segment that is ONLY a GST component ('CGST @ 9% and SGST
# @ 9%', 'SGST Rs.900') is part of the previous transaction's GST clause
# - textbooks often sentence-break or bullet the tax line. A segment
# carrying any transaction word stays an independent transaction.
_GST_FRAGMENT_VERBS = (
    "purchased", "bought", "sold", "paid", "received", "started",
    "commenced", "withdrew", "deposited", "returned", "rent", "salary",
    "wages", "goods", "stock", "furniture", "machinery", "cash", "bank",
    "cheque", "capital", "drawings", "conveyance", "printing", "telephone")


def _gst_amt_token(token: str) -> Optional[Decimal]:
    """Parse one money token like _extract_amounts (currency prefix
    stripped, trailing separators dropped, hard parse or None)."""
    t = _CURRENCY_PREFIX.sub("", str(token).strip()).rstrip(",.")
    if not t or not re.search(r"\d", t):
        return None
    parsed = parse_numeric_text(t)
    if parsed.value is None or parsed.ambiguity:
        return None
    return parsed.value


def _gst_facts(text: str) -> Optional[Dict[str, Any]]:
    """Deterministic GST evidence sheet, or None when the question carries
    no GST evidence at all. Every field derives from explicit wording only."""
    low = " " + str(text or "").lower() + " "
    if not _GST_TOKEN_RE.search(low):
        return None
    facts: Dict[str, Any] = {
        "components": [],       # ["CGST","SGST"] / ["IGST"] / []
        "side": set(),          # {"input"} / {"output"} / {}
        "rates": [],            # (Decimal rate, kind) kind in total/cgst/sgst/igst
        "comp_amounts": [],     # (kind, Decimal) amounts adjacent to a component
        "unlabeled": [],        # amounts not adjacent to any component token
        "inclusive": False,
        "exclusive": False,
        "intra_state": False,
        "inter_state": False,
    }
    for m in _GST_COMPONENT_RE.finditer(low):
        up = m.group(0).strip().upper()
        for comp in ("CGST", "SGST", "IGST"):
            if comp in up and comp not in facts["components"]:
                facts["components"].append(comp)
        if "INPUT" in up:
            facts["side"].add("input")
        if "OUTPUT" in up:
            facts["side"].add("output")
    # rates whose PRECEDING label mentions a GST token. The kind comes
    # ONLY from the text immediately before the '<n>%' token ('CGST @
    # 9%') - never from text that follows it, so 'GST @ 18%, CGST and
    # SGST' labels the 18% as the TOTAL rate (split into CGST 9% + SGST
    # 9%), instead of being misread as a per-component CGST rate that
    # would double the tax.
    low_rates = " " + str(text or "").lower() + " "
    for match in _PERCENT_TOKEN.finditer(low_rates):
        try:
            rate = Decimal(match.group(1))
        except (InvalidOperation, ValueError):
            continue
        before = low_rates[max(0, match.start() - 40):match.start()]
        if not _GST_TOKEN_RE.search(before):
            continue
        kind = "total"
        for comp in ("igst", "cgst", "sgst"):
            if comp in before:
                kind = comp
                break
        facts["rates"].append((rate, kind))
    # amounts adjacent to a component token vs unlabeled
    raw = str(text or "")
    for match in _NUMBER_TOKEN.finditer(raw):
        after = raw[match.end():match.end() + 2]
        if after.lstrip().startswith("%"):
            continue
        value = _gst_amt_token(match.group(0))
        if value is None:
            continue
        before = raw[max(0, match.start() - 30):match.start()].lower()
        comp = None
        last = None
        for cm in _GST_COMPONENT_RE.finditer(before):
            last = cm
        if last is not None:
            up = last.group(0).strip().upper()
            for name in ("IGST", "CGST", "SGST"):
                if name in up:
                    comp = name
                    break
        if comp:
            facts["comp_amounts"].append((comp, value))
        else:
            facts["unlabeled"].append(value)
    # inclusive / exclusive markers (deterministic, mutually exclusive)
    if re.search(r"\binclusive\s+of\s+gst\b|\bgst\s+inclusive\b|"
                 r"\bincluding\s+gst\b|\bgst\s+included\b", low):
        facts["inclusive"] = True
    if re.search(r"\bexclusive\s+of\s+gst\b|\bgst\s+exclusive\b|"
                 r"\bexcluding\s+gst\b|\bgst\s+excluded\b|"
                 r"\bplus\s+gst\b|\bgst\s+extra\b|"
                 r"\bgst\s+added\s+separately\b|"
                 r"\bin\s+addition\s+to\s+gst\b", low):
        facts["exclusive"] = True
    # intra / inter-state markers (explicit wording only)
    if re.search(r"\bintra[- ]state\b|\bwithin\s+the\s+state\b|"
                 r"\bwithin\s+maharashtra\b|"
                 r"\bwithin\s+the\s+same\s+state\b|\blocal\b", low):
        facts["intra_state"] = True
    if re.search(r"\binter[- ]state\b|\boutside\s+the\s+state\b|"
                 r"\bfrom\s+another\s+state\b|"
                 r"\bto\s+another\s+state\b|\bother\s+state\b", low):
        facts["inter_state"] = True
    return facts


def _gst_refusal(status: str, why: str, action: str,
                 facts: Dict[str, Any]) -> Dict[str, Any]:
    """A GST refusal shares generate_journal's refusal shape so every
    caller treats it identically."""
    return {
        "status": status,
        "why_not": why,
        "next_action": action,
        "debit_lines": [], "credit_lines": [],
        "narration": None, "calculation_records": [],
        "total_debit": 0, "total_credit": 0, "balanced": True,
        "gst": facts,
    }


def _gst_journal(text: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """The deterministic GST journal for ONE transaction carrying GST
    evidence. Reviews (never guesses) every GST fact the syllabus rule
    requires; returns REVIEW_REQUIRED whenever any fact is missing or
    contradictory."""
    low = " " + str(text or "").lower() + " "

    def _refuse(why: str, action: str) -> Dict[str, Any]:
        return _gst_refusal(REVIEW_REQUIRED, why, action, facts)

    # -- underlying transaction (GST phrases stripped) ---------------------
    stripped = _GST_PHRASE.sub(" ", str(text or ""))
    stripped = re.sub(r"\s+", " ", stripped).strip()
    pattern = classify_bk_type(stripped)
    if pattern is None or pattern.get("refuse"):
        return _refuse(
            "The underlying transaction could not be classified "
            "deterministically once the GST wording is removed (the "
            "cash/credit mode or the party may be missing).",
            "State the transaction in standard FYJC wording with the mode "
            "(for cash / on credit from <name>) and the party.")
    key = pattern.get("key") or ""
    # Sprint 15I-K: the GST surface is an EXPLICIT allowlist - only the
    # goods purchase/sale and expense patterns. Goods RETURNS and
    # FIXED-ASSET purchases/sales carry their own tax treatment in
    # practice; FT-E never guesses it (rule 8 -> NOT_SUPPORTED).
    if key in ("PURCHASE_GOODS_CASH", "PURCHASE_GOODS_CREDIT"):
        is_sale = False
    elif key in ("SALE_GOODS_CASH", "SALE_GOODS_CREDIT"):
        is_sale = True
    elif key == "EXPENSE_PAID":
        is_sale = False
    else:
        # Sprint 15I-K rule 8: GST on a transaction type OUTSIDE the
        # verified GST surface (capital introduction, drawings, loans,
        # returns, fixed assets, discount settlements, ...) is
        # NOT_SUPPORTED - no amount of re-stating within the GST domain
        # can make it resolvable, and FT-E never guesses a tax treatment
        # for a transaction the syllabus does not tax.
        return _gst_refusal(
            NOT_SUPPORTED,
            "GST is only supported on goods purchases, goods sales and "
            "expenses in the verified FYJC surface. This transaction "
            "type is not one of them, so FT-E does not guess its GST "
            "treatment.",
            "Use a supported transaction (purchase, sale, expense) with "
            "an explicit GST rate/components.",
            facts)

    debit_specs = pattern.get("debit") or []
    credit_specs = pattern.get("credit") or []
    # a party spec on the money side means a credit transaction
    is_credit = any(isinstance(s, dict) and "party" in s
                    for s in (credit_specs if not is_sale else debit_specs))

    # -- GST + discount ----------------------------------------------------
    # Sprint 15I-L: TRADE discount is deterministic with GST - the taxable
    # value is the list price LESS the trade discount, and GST is computed
    # on that net value (trade discount is never a separate journal line).
    # A CASH discount / settlement discount (or a bare 'discount') is a
    # settlement fact, not an invoice fact - FT-E never folds it into a GST
    # journal (sprint rule 10) and refuses instead of guessing.
    _gst_td = _gst_trade_discount(text)
    if _gst_td is None and "discount" in low:
        return _refuse(
            "A transaction combining GST with a cash/settlement discount is "
            "outside the verified GST surface. FT-E never applies both "
            "treatments on its own.",
            "Enter the GST transaction and the discount settlement as "
            "separate steps.")

    # -- component scheme (never guessed) ----------------------------------
    comps = set(facts["components"])
    if "IGST" in comps and (comps & {"CGST", "SGST"}):
        return _refuse(
            "The question names both IGST and CGST/SGST for the same "
            "transaction. FT-E never guesses which tax treatment applies.",
            "State one treatment: either CGST + SGST (intra-state) or "
            "IGST (inter-state).")
    if ("CGST" in comps) != ("SGST" in comps):
        present = "CGST" if "CGST" in comps else "SGST"
        missing = "SGST" if present == "CGST" else "CGST"
        return _refuse(
            f"{present} is named without {missing}. FT-E never invents the "
            "missing component.",
            f"State both components (including {missing}) or use IGST.")
    if "IGST" in comps:
        scheme = "IGST"
    elif "CGST" in comps and "SGST" in comps:
        scheme = "CGST_SGST"
    else:
        if facts["intra_state"] and facts["inter_state"]:
            return _refuse(
                "The question marks the transaction as both intra-state "
                "and inter-state. FT-E never guesses which applies.",
                "State one: intra-state (CGST + SGST) or inter-state (IGST).")
        if facts["intra_state"]:
            scheme = "CGST_SGST"
        elif facts["inter_state"]:
            scheme = "IGST"
        else:
            return _refuse(
                "GST is mentioned with a rate but the question does not "
                "say whether it is intra-state (CGST + SGST) or "
                "inter-state (IGST). FT-E never picks one.",
                "Name the components ('CGST and SGST') or state the "
                "intra/inter-state status.")

    if scheme == "CGST_SGST" and facts["inter_state"]:
        return _refuse(
            "CGST + SGST (intra-state) is contradicted by an inter-state "
            "marker. FT-E never guesses which treatment applies.",
            "State one treatment: CGST + SGST (intra-state) or IGST "
            "(inter-state).")
    if scheme == "IGST" and facts["intra_state"]:
        return _refuse(
            "IGST (inter-state) is contradicted by an intra-state marker. "
            "FT-E never guesses which treatment applies.",
            "State one treatment: CGST + SGST (intra-state) or IGST "
            "(inter-state).")

    # input vs output side: explicit mentions must match the direction
    side = facts["side"]
    if len(side) > 1:
        return _refuse(
            "The question names both input and output GST for the same "
            "transaction. FT-E never guesses which applies.",
            "State the tax side for this transaction.")
    expected_side = "output" if is_sale else "input"
    if side and expected_side not in side:
        return _refuse(
            f"The question names "
            f"{'input' if 'input' in side else 'output'} GST on a "
            f"{'sale' if is_sale else 'purchase/expense'} whose verified "
            f"tax side is "
            f"{'output' if expected_side == 'output' else 'input'}. FT-E "
            "never overrides the accounting direction.",
            "Use the correct tax side for the transaction.")

    # -- rate extraction (explicit only) -----------------------------------
    rates: Dict[str, List[Decimal]] = {
        "total": [], "cgst": [], "sgst": [], "igst": []}
    for rate, kind in facts["rates"]:
        if rate <= 0 or rate > Decimal(100):
            return _refuse(
                f"The stated GST rate ({rate}%) is impossible. FT-E never "
                "records an invalid rate.",
                "State a valid GST rate (0 < rate <= 100).")
        rates[kind].append(rate)
    for kind in ("total", "cgst", "sgst", "igst"):
        if len({v.quantize(Decimal("0.01")) for v in rates[kind]}) > 1:
            return _refuse(
                f"The question states contradictory {kind.upper()} rates. "
                "FT-E never guesses which is correct.",
                "State one rate per tax component.")

    def _uniq(vals: List[Decimal]) -> Optional[Decimal]:
        uniq = {v.quantize(Decimal("0.01")) for v in vals}
        return uniq.pop() if len(uniq) == 1 else None

    total_rate: Optional[Decimal] = None
    if scheme == "CGST_SGST":
        if rates["igst"]:
            return _refuse(
                "An IGST rate on an intra-state CGST + SGST transaction. "
                "FT-E never guesses which treatment applies.",
                "State one treatment.")
        cgst_r = _uniq(rates["cgst"])
        sgst_r = _uniq(rates["sgst"])
        if cgst_r is not None and sgst_r is not None and cgst_r != sgst_r:
            return _refuse(
                "CGST and SGST rates differ. FT-E never records a "
                "non-standard split.",
                "State equal CGST and SGST rates (or a single GST rate).")
        total_r = _uniq(rates["total"])
        if total_r is not None:
            if cgst_r is not None and cgst_r != (total_r / Decimal(2)):
                return _refuse(
                    "The CGST/SGST rates do not match half the stated GST "
                    "rate. FT-E never guesses which is correct.",
                    "State consistent rates.")
            total_rate = total_r
        elif cgst_r is not None:
            total_rate = cgst_r * Decimal(2)
    else:  # IGST
        if rates["cgst"] or rates["sgst"]:
            return _refuse(
                "A CGST/SGST rate on an IGST transaction. FT-E never "
                "guesses which treatment applies.",
                "State one treatment.")
        igst_r = _uniq(rates["igst"])
        total_r = _uniq(rates["total"])
        if igst_r is not None and total_r is not None and igst_r != total_r:
            return _refuse(
                "The IGST rate differs from the stated GST rate. FT-E "
                "never guesses which is correct.",
                "State consistent rates.")
        total_rate = igst_r if igst_r is not None else total_r

    # -- inclusive / exclusive mode ----------------------------------------
    if facts["inclusive"] and facts["exclusive"]:
        return _refuse(
            "The question says the amount is BOTH inclusive of GST and "
            "exclusive/plus GST. FT-E never guesses which is meant.",
            "State one: 'inclusive of GST' or 'GST added separately'.")
    mode = "inclusive" if facts["inclusive"] else "exclusive"

    # -- amounts ------------------------------------------------------------
    unlabeled = facts["unlabeled"]
    if len(unlabeled) != 1:
        return _refuse(
            "The GST transaction must carry exactly one stated amount for "
            "the goods/service value. FT-E never picks between multiple "
            "figures (and never treats a payment step as part of the GST "
            "transaction).",
            "Enter the single transaction amount; enter a partial payment "
            "as a separate step.")
    list_price = unlabeled[0]
    if list_price <= 0:
        return _refuse(
            "The stated amount must be positive. FT-E never records a "
            "zero or negative transaction.",
            "Enter the correct positive amount.")
    # Sprint 15I-L: trade discount nets the taxable value BEFORE GST. The
    # inclusive/exclusive mode then applies to the POST-trade-discount
    # amount (the invoice value is the discounted price).
    stated = list_price
    _gst_steps: List[Dict[str, Any]] = []
    if _gst_td is not None:
        td_amount, td_kind = _gst_td
        if td_kind == "rate":
            td_amount = (list_price * td_amount / Decimal(100)).quantize(
                Decimal("0.01"))
        if td_amount <= 0 or td_amount >= list_price:
            return _refuse(
                "The stated trade discount is impossible (not positive and "
                "smaller than the list price). FT-E never records it.",
                "Re-check the discount amount or rate.")
        stated = list_price - td_amount
        _gst_steps.append({
            "calculation_id": "BK_GST_TRADE_DISCOUNT",
            "label": "Trade discount nets the taxable value",
            "formula": "Taxable value = List price - Trade discount",
            "inputs": {"list_price": list_price,
                       "trade_discount": td_amount},
            "result": stated,
        })

    comp_amt: Dict[str, Decimal] = dict(facts["comp_amounts"])
    if scheme == "IGST":
        if any(k in comp_amt for k in ("CGST", "SGST")):
            return _refuse(
                "A CGST/SGST amount on an IGST transaction. FT-E never "
                "guesses which treatment applies.",
                "State one treatment.")
    else:
        if "IGST" in comp_amt:
            return _refuse(
                "An IGST amount on an intra-state CGST + SGST transaction. "
                "FT-E never guesses which treatment applies.",
                "State one treatment.")

    def _q(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # -- base / GST computation --------------------------------------------
    if mode == "inclusive":
        if total_rate is not None:
            gst_total = _q(stated * total_rate / (Decimal(100) + total_rate))
            base = stated - gst_total
        elif comp_amt:
            gst_total = sum(comp_amt.values())
            base = stated - gst_total
        else:
            return _refuse(
                "'Inclusive of GST' requires a GST rate or a stated GST "
                "amount to extract the taxable base. FT-E never estimates "
                "the base.",
                "State the GST rate (e.g. 'inclusive of GST @ 18%') or the "
                "GST amount.")
        if base <= 0:
            return _refuse(
                "The taxable base derived from the inclusive amount is not "
                "positive. FT-E never records it.",
                "Re-check the amount and rate.")
    else:
        base = stated
        if total_rate is not None:
            gst_total = _q(base * total_rate / Decimal(100))
        elif comp_amt:
            gst_total = sum(comp_amt.values())
        else:
            return _refuse(
                "GST is mentioned but neither a rate nor a GST amount is "
                "stated. FT-E never guesses the tax.",
                "State the GST rate (e.g. 'GST @ 18%') or the GST amount.")
        if gst_total <= 0:
            return _refuse(
                "The computed GST amount is not positive. FT-E never "
                "records it.",
                "Re-check the amount and rate.")

    # -- component split ----------------------------------------------------
    if scheme == "IGST":
        if total_rate is not None:
            igst_amt = _q(base * total_rate / Decimal(100))
            if "IGST" in comp_amt and comp_amt["IGST"] != igst_amt:
                return _refuse(
                    "The stated IGST amount does not match the stated rate "
                    "times the base. FT-E never guesses which is correct.",
                    "State consistent amounts/rates.")
        elif "IGST" in comp_amt:
            igst_amt = comp_amt["IGST"]
        else:
            return _refuse(
                "IGST is mentioned without a rate or an IGST amount. FT-E "
                "never guesses the tax.",
                "State the IGST rate or the IGST amount.")
        if igst_amt <= 0:
            return _refuse(
                "The IGST amount is not positive. FT-E never records it.",
                "Re-check the amount and rate.")
        raw_components = [("IGST", igst_amt)]
    else:
        cgst_amt = comp_amt.get("CGST")
        sgst_amt = comp_amt.get("SGST")
        if (cgst_amt is None) != (sgst_amt is None):
            return _refuse(
                "Only one of the CGST/SGST amounts is stated. FT-E never "
                "invents the missing component.",
                "State both component amounts (or a rate).")
        if total_rate is not None:
            half = _q(base * total_rate / Decimal(200))
            if cgst_amt is not None and (cgst_amt != half
                                         or sgst_amt != half):
                return _refuse(
                    "The stated CGST/SGST amounts do not match the stated "
                    "rate. FT-E never guesses which is correct.",
                    "State consistent amounts/rates.")
            cgst_amt = half
            sgst_amt = half
        elif cgst_amt is not None:
            pass  # both stated amounts are used as-is
        else:
            return _refuse(
                "CGST + SGST is mentioned without a rate or component "
                "amounts. FT-E never guesses the tax.",
                "State the GST rate or the CGST and SGST amounts.")
        if cgst_amt <= 0 or sgst_amt <= 0:
            return _refuse(
                "A GST component amount is not positive. FT-E never "
                "records it.",
                "Re-check the amounts and rate.")
        raw_components = [("CGST", cgst_amt), ("SGST", sgst_amt)]

    gst_total = sum(amt for _, amt in raw_components)
    total = base + gst_total

    # -- journal build ------------------------------------------------------
    prefix = "Output" if is_sale else "Input"
    components_out = [(f"{prefix} {comp}", amt)
                      for comp, amt in raw_components]

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

    cash_or_bank = _resolve_cash_bank(text)
    cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
    debit_lines: List[Dict[str, Any]] = []
    credit_lines: List[Dict[str, Any]] = []

    if is_sale:
        if is_credit:
            party = _resolve_bk_spec({"party": "receiver"}, stripped,
                                     "receiver")
            if party is None:
                return _refuse(
                    "The credit sale does not name the customer. FT-E "
                    "never invents a person's name.",
                    "Add the customer's name.")
            debit_lines.append(_line(party, total, "debit"))
        else:
            debit_lines.append(_line(cash_acct, total, "debit"))
        credit_lines.append(_line("Sales", base, "credit"))
        for account, amount in components_out:
            credit_lines.append(_line(account, amount, "credit"))
    else:
        debit_accounts = _resolve_side_specs(debit_specs, stripped,
                                             "receiver")
        if not debit_accounts:
            return _refuse(
                "The underlying purchase/expense account could not be "
                "resolved. FT-E never invents an account.",
                "Re-type the transaction with the account explicit.")
        debit_lines.append(_line(debit_accounts[0], base, "debit"))
        for account, amount in components_out:
            debit_lines.append(_line(account, amount, "debit"))
        if is_credit:
            party = _resolve_bk_spec({"party": "giver"}, stripped, "giver")
            if party is None:
                return _refuse(
                    "The credit purchase does not name the supplier. FT-E "
                    "never invents a person's name.",
                    "Add the supplier's name.")
            credit_lines.append(_line(party, total, "credit"))
        else:
            credit_lines.append(_line(cash_acct, total, "credit"))

    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))

    steps: List[Dict[str, Any]] = list(_gst_steps)
    if total_rate is not None:
        steps.append({
            "calculation_id": "BK_GST_RATE",
            "label": "GST rate",
            "formula": "Rate from the question",
            "inputs": {"gst_rate": total_rate, "scheme": scheme},
            "result": total_rate,
        })
    if mode == "inclusive":
        steps.append({
            "calculation_id": "BK_GST_INCLUSIVE_EXTRACTION",
            "label": "Extract taxable base from inclusive amount",
            "formula": "Base = total / (1 + rate)",
            "inputs": {"inclusive_total": stated, "gst_rate": total_rate},
            "result": base,
        })
    steps.append({
        "calculation_id": "BK_GST_BASE",
        "label": "Taxable base",
        "formula": "Base from the question" if mode == "exclusive"
                   else "Extracted from the inclusive amount",
        "inputs": {"base": base},
        "result": base,
    })
    steps.append({
        "calculation_id": "BK_GST_COMPONENT_SPLIT",
        "label": "GST component split",
        "formula": ("CGST = SGST = total GST / 2" if scheme == "CGST_SGST"
                    else "IGST = total GST"),
        "inputs": {"components": raw_components, "gst_total": gst_total},
        "result": gst_total,
    })
    steps.append({
        "calculation_id": "BK_GST_TOTAL",
        "label": "Total consideration",
        "formula": "Total = taxable base + GST",
        "inputs": {"base": base, "gst_total": gst_total},
        "result": total,
    })

    narration_parts: List[str] = []
    for line in debit_lines:
        narration_parts.append(f"{line['account']} A/c Dr "
                               f"{_fmt_amt(line['amount'])}")
    for line in credit_lines:
        narration_parts.append(f"To {line['account']} A/c "
                               f"{_fmt_amt(line['amount'])}")
    if total_rate is not None:
        narration_parts.append(f"(GST @ {total_rate}%"
                               + (" inclusive" if mode == "inclusive" else "")
                               + ")")
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
        "calculation_records": steps,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
        "gst": {
            "scheme": scheme,
            "base": base,
            "gst_total": gst_total,
            "total": total,
            "rate": total_rate,
            "mode": mode,
            "components": raw_components,
        },
    }


def discount_evidence(raw: str) -> Dict[str, Any]:
    """Deterministic discount metadata for the content compiler (15I-L
    section 16). Every field derives ONLY from explicit question wording;
    a field that cannot be established deterministically is UNKNOWN - never
    guessed. Returns {trade_discount, cash_discount, discount_percentage,
    discount_amount, gross_amount, net_amount, settlement_amount}."""
    unk = "UNKNOWN"
    text = str(raw or "").strip()
    if not text:
        return {"trade_discount": unk, "cash_discount": unk,
                "discount_percentage": unk, "discount_amount": unk,
                "gross_amount": unk, "net_amount": unk,
                "settlement_amount": unk}
    low = " " + text.lower() + " "
    if "discount" not in low and not re.search(r"\btd\b", low):
        return {"trade_discount": "NONE", "cash_discount": "NONE",
                "discount_percentage": unk, "discount_amount": unk,
                "gross_amount": unk, "net_amount": unk,
                "settlement_amount": unk}
    has_trade = "trade discount" in low or bool(re.search(r"\btd\b", low))
    has_cash = ("cash discount" in low or "discount allowed" in low
                or "discount received" in low or "allowed discount" in low
                or "received discount" in low)
    out = {
        "trade_discount": "YES" if has_trade else "NO",
        "cash_discount": "YES" if has_cash else "NO",
        "discount_percentage": unk,
        "discount_amount": unk,
        "gross_amount": unk,
        "net_amount": unk,
        "settlement_amount": unk,
    }
    for rate, label in _extract_percents(text):
        if "discount" in label or re.search(r"\btd\b", label):
            out["discount_percentage"] = rate
            break
    if has_trade:
        m_td = re.search(
            r"less\s+(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
            r"\s+(?:trade\s+discount|td)\b", low)
        if m_td is None:
            m_td = re.search(
                r"(?:trade\s+discount|td)\s+(?:of\s+)?"
                r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                low)
        if m_td:
            try:
                out["discount_amount"] = Decimal(
                    m_td.group(1).replace(",", ""))
            except (InvalidOperation, ValueError):
                pass
    elif has_cash and not has_trade:
        # the amount must be anchored to the word 'discount': either
        # directly AFTER it ('discount allowed Rs.200', 'discount of
        # Rs.200', 'discount received Rs.200') or directly BEFORE it
        # ('allowed Rs.500 cash discount', 'received Rs.500 discount').
        # 'Received Rs.9,500 from him' is a receipt, never a discount
        # amount - the cash figure is never misread as discount metadata
        # (Sprint 15I-L).
        m_cd = re.search(
            r"discount\s+(?:of\s+|allowed\s+|received\s+)?"
            r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
            low)
        if m_cd is None:
            m_cd = re.search(
                r"(?:allowed|received|after)\s+"
                r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
                r"\s+(?:cash\s+)?discount\b",
                low)
        if m_cd:
            after = low[m_cd.end():m_cd.end() + 2]
            if not after.lstrip().startswith("%"):
                try:
                    out["discount_amount"] = Decimal(
                        m_cd.group(1).replace(",", ""))
                except (InvalidOperation, ValueError):
                    pass
    res = resolve_transaction_amounts(text)
    if res.get("status") == VERIFIED:
        if res.get("list_price") is not None:
            out["gross_amount"] = res["list_price"]
        if res.get("net_value") is not None:
            out["net_amount"] = res["net_value"]
        if res.get("explicit_discount") is not None:
            out["settlement_amount"] = res["explicit_discount"]["party_total"]
        elif (res.get("paid_amount") is not None
              and res.get("cash_discount_amount") is not None):
            # the party's full settlement = cash settled + the cash
            # discount allowed/received (Sprint 15I-L).
            out["settlement_amount"] = (res["paid_amount"]
                                        + res["cash_discount_amount"])
        elif res.get("cash_paid") is not None:
            out["settlement_amount"] = res["cash_paid"]

    return out


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

    # Sprint 15I-K: when ANY GST evidence is present, ONLY the GST path may
    # journal - the plain patterns would silently drop the tax.
    _gst_facts_here = _gst_facts(text)
    if _gst_facts_here is not None:
        return _gst_journal(text, _gst_facts_here)

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
            # Sprint 15I-L: a TRADE discount on a goods purchase/sale with
            # an explicit mode ('for cash' / 'on credit') is a full
            # transaction, not a standalone discount entry - the amount-TD
            # and word-percent forms carry no '%' token and must not trip
            # the standalone-discount guard.
            or ("trade discount" in low_check
                and "goods" in low_check
                and ("for cash" in low_check or "credit" in low_check))
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
    # Sprint 15I-L: a RECEIPT from a debtor is the settlement direction
    # that is the mirror of a payment - the business receives cash and
    # ALLOWS the discount, never Discount Received.
    receipt = pattern["key"] == "RECEIVED_FROM"
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
    # Sprint 15I-L: a PURCHASE carrying an explicit discount amount from a
    # merged settlement step posts the COMPOUND journal through the split
    # machinery below - never a settlement-only entry that would drop the
    # purchase account. The compound form is used ONLY when the settlement
    # numbers reconcile exactly with the purchase value (net == cash +
    # discount + remainder); any other combination keeps the explicit path.
    compound_explicit = bool(
        explicit is not None and purchase and not sale
        and amounts.get("paid_amount") is not None
        and amounts.get("cash_discount_amount") is not None
        and net == (amounts["paid_amount"] + amounts["cash_discount_amount"]
                    + (amounts.get("credit_amount") or Decimal(0))))
    if explicit is not None and not compound_explicit:
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
        elif receipt and split and cash_discount is not None \
                and cash_discount > 0:
            # a debtor settling with a cash discount: the business
            # RECEIVES the net cash and ALLOWS the discount. A partial
            # receipt combined with a cash discount is ambiguous (the
            # receivable remainder must survive) and refuses instead of
            # guessing (Sprint 15I-L).
            if credit_portion is None or credit_portion <= 0:
                cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
                if cash_paid is not None and cash_paid > 0:
                    debit_lines.append(_line(cash_acct, cash_paid, "debit"))
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
        elif receipt and split and cash_discount is not None \
                and cash_discount > 0 \
                and (credit_portion is None or credit_portion <= 0):
            # the debtor's account is settled for the gross amount
            # (net cash received + the discount allowed).
            party = (_resolve_bk_spec({"party": "giver"}, text, "giver")
                     or _resolve_bk_spec({"party": "receiver"}, text,
                                         "receiver"))
            if party:
                credit_lines.append(_line(party, net, "credit"))
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


def _merge_preserves_integrity(prior_segment: str, merged_text: str,
                               step_segment: str,
                               merged: Dict[str, Any]) -> bool:
    """Sprint 15I-F P0-B merge-path integrity invariant.

    A payment-step merge must NEVER change the semantic nature of the
    already-established prior transaction. Returns False (the merge is
    refused with REVIEW_REQUIRED) when the merged journal:
      * swaps the accounting MODE of the prior pattern (credit <-> cash,
        e.g. SALE_GOODS_CREDIT -> SALE_GOODS_CASH merely because the
        continuation says 'cash'/'further cash'),
      * swaps the transaction FAMILY (sale <-> purchase), or
      * drops an explicitly stated amount from the continuation step.
    Deterministic - compares the classify_bk_type keys of the prior
    segment and the merged text, and checks every currency amount stated
    in the step against the merged journal's line amounts. No guessing.
    """
    prior_pattern = classify_bk_type(prior_segment)
    merged_pattern = classify_bk_type(merged_text)
    if prior_pattern and merged_pattern:
        pk = prior_pattern["key"]
        mk = merged_pattern["key"]
        if ("SALE" in pk) != ("SALE" in mk) or \
                ("PURCHASE" in pk) != ("PURCHASE" in mk):
            return False
        if ("CASH" in pk, "CREDIT" in pk) != ("CASH" in mk, "CREDIT" in mk):
            return False
    step_amounts, _ = _extract_amounts(step_segment)
    line_amounts = [line.get("amount") for line in
                    (merged.get("debit_lines") or [])
                    + (merged.get("credit_lines") or [])]
    for amount in step_amounts:
        if amount not in line_amounts:
            return False
    return True


def _party_role_in_journal(journal: Dict[str, Any],
                           party: str) -> str:
    """The accounting ROLE of a party inside a journal (Sprint 15I-F
    P1-A): DEBTOR when the party sits on the DEBIT side (a receivable -
    the party owes the business), CREDITOR when on the CREDIT side (a
    payable - the business owes the party), NEUTRAL otherwise. Only the
    side of the party's own line decides - never the wording."""
    debits = [line.get("account") for line in
              (journal.get("debit_lines") or [])]
    credits = [line.get("account") for line in
               (journal.get("credit_lines") or [])]
    if party in debits and party in credits:
        return "NEUTRAL"
    if party in debits:
        return "DEBTOR"
    if party in credits:
        return "CREDITOR"
    return "NEUTRAL"


def _bank_continuation(segment: str,
                       prior_journal: Optional[Dict[str, Any]]) -> Optional[str]:
    """Sprint 15I-J: a follow-up 'Deposited/Withdrew further cash
    Rs.X' with no transaction identity of its own is a continuation of
    the PREVIOUS transaction's bank context. Fires ONLY when (a) the
    prior journal actually contains a Bank line, (b) the segment
    carries an explicit direction verb + a continuation marker
    (further/additional/more/again) + the word cash + exactly one
    amount, and (c) the segment classifies to NOTHING on its own.
    Returns the canonical single-transaction wording (so the existing
    registered pipeline journals it), or None - a bare 'further cash'
    without a direction verb, or without a bank context, is never
    guessed. Deterministic; no invented amounts."""
    if not prior_journal:
        return None
    has_bank = any((line.get("account") or "") == "Bank"
                   for line in (prior_journal.get("debit_lines") or [])
                   + (prior_journal.get("credit_lines") or []))
    if not has_bank:
        return None
    low = " " + str(segment or "").lower() + " "
    if not re.search(r"\b(?:further|additional|more|again)\b", low):
        return None
    if not re.search(r"\bcash\b", low):
        return None
    amounts, _ = _extract_amounts(segment)
    if len(amounts) != 1:
        return None
    amount = str(amounts[0])
    if amount.endswith(".0"):
        amount = amount[:-2]
    if re.search(r"\b(?:deposited|depositing|paid\s+into)\b.*\bcash\b",
                 low):
        return f"Deposited cash into bank Rs.{amount}"
    if re.search(r"\b(?:withdrew|withdrawn|drew|drawn|took\s+out)\b.*"
                 r"\bcash\b", low):
        return f"Withdrew cash from bank Rs.{amount}"
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
    prior_role: str = "NEUTRAL"
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
        # Sprint 15I-F P1-A accounting-role continuity: a payment step
        # that PAYS the party the previous transaction established as a
        # DEBTOR ('Sold goods to Ram on credit Rs.12,000. Paid him
        # Rs.5,000.') contradicts the debtor relationship - FT-E never
        # blindly posts the generic PAID_TO direction for a debtor it
        # already placed on the debit side. REVIEW_REQUIRED so the
        # student re-states the direction ('Received from him' if the
        # party settled). Creditor settlements ('Bought goods from Rahul
        # ... Paid him ...') keep the existing purchase-continuation
        # pipeline untouched, as does generic pronoun resolution.
        _low_seg = " " + segment.lower() + " "
        _seg_pattern = classify_bk_type(segment)
        _role_conflict = (i > 0 and prior_party
                          and prior_role == "DEBTOR"
                          and _is_payment_step(raw_segment)
                          and " paid " in _low_seg
                          and _party_from_text(segment) == prior_party
                          # only a payment TO the debtor contradicts the
                          # debtor relationship - an ACTIVE-voice receipt
                          # ('Mohan paid Rs.12,000' = Mohan settles the
                          # business) classifies RECEIVED_FROM and must
                          # keep the 15F settlement behaviour.
                          and bool(_seg_pattern)
                          and _seg_pattern.get("key") == "PAID_TO")
        if _role_conflict:
            journal = {
                "status": REVIEW_REQUIRED,
                "why_not": (f"The previous transaction made {prior_party} "
                            "a debtor (they owe the business), so a "
                            "payment TO that party now would contradict "
                            "the debtor relationship. FT-E never guesses "
                            "the payment direction."),
                "next_action": ("Re-type the settlement with an explicit "
                                "direction, e.g. 'Received Rs.X from "
                                f"{prior_party}.' if the party settled."),
                "debit_lines": [], "credit_lines": [],
                "narration": None, "calculation_records": [],
                "total_debit": 0, "total_credit": 0, "balanced": True,
            }
        else:
            # Sprint 15I-J: a bank continuation step ('Deposited further
            # cash Rs.5,000' after 'Opened an account with Bank of India
            # Rs.20,000') with no identity of its own inherits ONLY the
            # prior journal's bank context and its explicit direction
            # verb - never an invented mode or amount.
            _bank_cont = None
            if i > 0 and classify_bk_type(segment) is None:
                _bank_cont = _bank_continuation(segment, journals[-1])
            if _bank_cont:
                journal = generate_journal(_bank_cont)
            else:
                journal = generate_journal(segment)
        if (not _role_conflict and journal["status"] != VERIFIED
                and i > 0 and _is_payment_step(raw_segment)):
            # payment/discount step -> re-run the discount pipeline over
            # the previous transaction PLUS this step as ONE journal.
            merged_text = resolved_segments[i - 1] + "; " + segment
            merged = generate_journal(merged_text)
            if merged["status"] == VERIFIED \
                    and _merge_preserves_integrity(
                        resolved_segments[i - 1], merged_text,
                        raw_segment, merged):
                journals[-1] = merged
                party = _party_from_journal(merged)
                if party:
                    prior_party = party
                    prior_role = _party_role_in_journal(merged, party)
                else:
                    prior_role = "NEUTRAL"
                continue
            if merged["status"] == VERIFIED:
                # the merge would silently change the prior transaction
                # (mode/family flip or a dropped stated amount) - never
                # reinterpret or repair the previous journal (Sprint
                # 15I-F P0-B). REVIEW_REQUIRED with the calm refusal.
                journal = {
                    "status": REVIEW_REQUIRED,
                    "why_not": ("FT-E will not silently re-interpret the "
                                "previous transaction while folding this "
                                "payment step into it (it would change "
                                "the sale/purchase mode or drop a stated "
                                "amount). Enter the two transactions "
                                "separately."),
                    "next_action": ("Separate the transactions, e.g. 'Sold "
                                    "goods to Ram on credit Rs.12,000.' "
                                    "then 'Received Rs.5,000 from Ram.'."),
                    "debit_lines": [], "credit_lines": [],
                    "narration": None, "calculation_records": [],
                    "total_debit": 0, "total_credit": 0, "balanced": True,
                }
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
            prior_role = _party_role_in_journal(journal, party)
        else:
            prior_role = "NEUTRAL"

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
