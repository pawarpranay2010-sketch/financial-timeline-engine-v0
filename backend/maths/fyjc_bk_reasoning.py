"""
Platrixa
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
      -> student-facing "What Platrixa understood"

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
  out-of-boundary topics -> NOT_SUPPORTED. Platrixa never guesses a treatment.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from collections import Counter
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

# Sprint 15I-VY: a stated MATHEMATICAL contradiction (payment + outstanding
# != transaction value, GST components inconsistent with the stated rate,
# discount inconsistent with the stated base) is a deterministic INPUT
# error, distinct from ambiguity (REVIEW_REQUIRED) and from being outside
# the supported FYJC surface (NOT_SUPPORTED). Always zero journal lines.
INVALID_INPUT_MATH = "INVALID_INPUT_MATH"

SUPPORTED_STATUSES = (VERIFIED, BLOCKED, REVIEW_REQUIRED, NOT_SUPPORTED,
                      INVALID_INPUT_MATH)

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
    # Sprint 15I-BILLS: a bill sent to the bank for collection remains
    # the firm's asset (Bills Sent for Collection A/c) until collected.
    "Bills Sent for Collection": CLASS_REAL,
    "Bills for Collection": CLASS_REAL,
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
    # Sprint 15I-BILLS: bank discount charged by the bank when a bill is
    # discounted (a loss for the drawer) is the 'Discount' account.
    "Discount": CLASS_NOMINAL,
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
    # Sprint 15I-TX: Donation A/c is the nominal expense debited when
    # goods/cash are given away - a single Capitalised word would
    # otherwise be read as a Personal account (a party).
    "Donation": CLASS_NOMINAL,
}

# Named parties (Rahul, Mohan, ...) are ALWAYS Personal accounts.
_PARTY_SUFFIXES = ("a/c", "account", "ltd", "limited", "& co", "and co")


def traditional_class_for(account: str) -> str:
    """The FYJC traditional class of a canonical account or named party.

    Override table wins; a non-chart account that reads as a proper noun
    (Capitalised, no internal spaces) is a Personal account (Rahul,
    Mohan, ...). Everything else falls back to Nominal defensively but
    should never be used to build an entry. Multi-word party names are
    classified Personal through the line builders' party context
    (_line(..., party=True)) - never by broadening this name-based
    fallback, which the 15G canonical authority also uses to separate a
    genuine party from an invented account.
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


def side_decision_for(account: str, side: str,
                      cls: Optional[str] = None) -> str:
    """Student-readable WHY for debiting/crediting one account.

    Traditional FYJC language - never corporate terminology. An explicit
    class wins when the caller already resolved it (the journal line
    builders pass the party-resolved class so a multi-word party name is
    never read as a Nominal account).
    """
    cls = cls or traditional_class_for(account)
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

# Sprint 15I-UZ (D2): a two-letter abbreviation ('T.D.', 'C.D.') is NEVER
# a sentence boundary - '12% T.D. He issued a bearer cheque ...' is ONE
# transaction, not a sale plus a separate payment sentence. The dotted
# abbreviation is swapped to sentinel characters before splitting and
# restored to exact dotted form afterwards.
_ABBREV_RE = re.compile(r"\b([A-Za-z])\.([A-Za-z])\.(?=\s)")
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
    _customer_cheque = _customer_issued_cheque(low)
    for word, fraction in _FRACTION_WORDS.items():
        if f" {word} " in low:
            if ("paid" in low or "cash" in low or "immediately" in low
                    or "at once" in low):
                return Decimal(fraction)
    # Sprint 15I-UZ (D5): a word fraction OF THE AMOUNT with a payment
    # mode ('half of the amount by cheque', 'half the amount paid by
    # bank') - the fraction of the transaction value actually paid.
    for word, fraction in _FRACTION_WORDS.items():
        if f" {word} " in low and re.search(
                r"\b(?:of\s+the\s+)?(?:amount|total|transaction|payment)\b",
                low):
            if any(k in low for k in ("cheque", "check", "paid", "bank",
                                      "immediately", "at once")):
                # a cheque ISSUED BY THE CUSTOMER in a sale is received
                # only on deposit (a later step) - never the business's
                # own payment fraction.
                if _customer_cheque and "paid" not in low:
                    continue
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
    # Sprint 15I-UZ (D5): a percent OF THE AMOUNT with an explicit
    # payment clause ('issued a cheque in his favour for 50% of the
    # amount', 'paid 50% of the total by bank').
    for m in _PERCENT_TOKEN.finditer(low):
        after = low[m.end():m.end() + 40]
        if re.match(r"\s*(?:of|for)\s+(?:the\s+)?(?:amount|total|"
                    r"transaction|purchase price)", after):
            window = low[max(0, m.start() - 40):m.end() + 12]
            if "discount" in window:
                continue
            if _customer_cheque:
                continue
            try:
                return Decimal(m.group(1))
            except (InvalidOperation, ValueError):
                continue
    return None


# Sprint 15I-UZ direction helpers. The transaction DIRECTION (sale vs
# purchase) is established by the VERB before any word-list match, so a
# sale sentence ('Sold goods worth Rs.X to <party>') can never fall
# through to the purchase patterns (D1). Bare 'goods worth' (no verb) is
# the established credit-purchase form only when no sale verb is present.
_SALE_STRONG_HINTS = (
    "sold goods", "goods sold", "sold stock", "stock sold", "sold to",
    "credit sale", "sold on credit", "sold goods to",
    "sold goods on credit", "sold goods worth", "sold stock worth",
    "sold goods for cash", "sold for cash", "sale of goods", "cash sale",
    "goods sold to", "sold goods in cash",
)
_PURCHASE_STRONG_HINTS = (
    "purchased goods", "bought goods", "goods purchased", "goods bought",
    "purchased stock", "bought stock", "stock purchased", "stock bought",
    "purchased goods worth", "bought goods worth", "purchased stock worth",
    "stock worth", "purchased goods for cash", "bought goods for cash",
    "goods purchased for cash", "purchased goods by cheque",
    "bought goods by cheque", "purchased goods on credit",
    "credit purchase", "goods bought for cash", "purchased stock for cash",
)


def _sale_direction_in(low: str) -> bool:
    """True when the text deterministically states a SALE direction."""
    return any(k in low for k in _SALE_STRONG_HINTS)


def _purchase_direction_in(low: str) -> bool:
    """True when the text deterministically states a PURCHASE direction."""
    return any(k in low for k in _PURCHASE_STRONG_HINTS)


def _direction_scan_text(low: str) -> str:
    """Lowercased text with abbreviation dots removed ('Mr.', 'Rs.',
    'T.D.', 'C.D.') so direction regexes can cross party/currency
    boundaries deterministically ('Sold goods purchased from Mr. Roger
    Federer of Rs.25,000 (cost price) to Mr. Novak Djokovic' must read
    the 'to <party>' clause). Used ONLY for direction/recipient
    detection - never for amounts or account names."""
    t = re.sub(r"\b(?:mr|mrs|ms|dr|prof|rev|st)\.(?=\s|$)", "", low)
    t = re.sub(r"\b(?:rs|inr)\.(?=\s|\d|$)", "", t)
    t = _ABBREV_RE.sub(lambda m: m.group(1) + m.group(2), t)
    return t


def _customer_issued_cheque(low: str) -> bool:
    """True when a CUSTOMER (not the business) issued the cheque - a sale
    clause like 'He issued a bearer cheque ...' is received only on
    deposit (a later step) and is never the business's own payment."""
    if not _sale_direction_in(low):
        return False
    return bool(re.search(
        r"\b(?:he|she|they|the customer|the buyer)\b[^.;]*?"
        r"\b(?:issued|gave|handed|sent)\b[^.;]*?\bcheque\b", low))


def _cheque_amount_in(low: str) -> Optional[Decimal]:
    """The figure stated as 'cheque of Rs.X' / 'cheque for Rs.X', or None.
    A '%' right after it means a rate, never a money amount."""
    m = re.search(
        r"\b(?:cheque|check)\s+(?:of|for)\s*(?:rs\.?|\u20b9|inr)?\s*"
        r"(\d[\d,]*(?:\.\d+)?)", low)
    if not m:
        return None
    after = low[m.end():m.end() + 2]
    if after.lstrip().startswith("%"):
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _account_balance_figure(low: str) -> Optional[Decimal]:
    """The STATED account balance ('his account of Rs.X', 'balance of
    Rs.X'). A receipt/payment against this figure is a partial (or at-par)
    settlement - the difference is never an invented discount (D4)."""
    m = re.search(
        r"\b(?:account|balance)\s+of\s*(?:rs\.?|\u20b9|inr)?\s*"
        r"(\d[\d,]*(?:\.\d+)?)", low)
    if not m:
        return None
    after = low[m.end():m.end() + 2]
    if after.lstrip().startswith("%"):
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


# Sprint 15I-UZ (D3): profit-on-cost vs profit-on-selling-price. The rate
# may precede ('at 30% profit on cost') or follow ('profit of 30% on
# cost') the profit noun.
_PROFIT_ON_COST_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*(?:%|percent)\s*profit|profit\s+of\s+"
    r"(\d+(?:\.\d+)?)\s*(?:%|percent))\s+on\s+(?:the\s+)?"
    r"(?:cost\s+price|cost)\b")
_PROFIT_ON_SELLING_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*(?:%|percent)\s*profit|profit\s+of\s+"
    r"(\d+(?:\.\d+)?)\s*(?:%|percent))\s+on\s+(?:the\s+)?"
    r"(?:selling\s+price|sale\s+price|selling)\b")


def _profit_on_cost(text: str) -> Optional[Tuple[Decimal, str]]:
    """(rate, kind) for profit wording - kind is 'on_cost', 'on_selling'
    or 'ambiguous'. None when no profit wording exists at all."""
    low = " " + str(text or "").lower() + " "
    m = _PROFIT_ON_COST_RE.search(low)
    if m:
        raw = m.group(1) or m.group(2)
        try:
            rate = Decimal(raw)
        except (InvalidOperation, ValueError):
            return (None, "ambiguous")
        if 0 < rate < Decimal(1000):
            return (rate, "on_cost")
        return (None, "ambiguous")
    m = _PROFIT_ON_SELLING_RE.search(low)
    if m:
        raw = m.group(1) or m.group(2)
        try:
            rate = Decimal(raw)
        except (InvalidOperation, ValueError):
            return (None, "ambiguous")
        if 0 < rate < Decimal(100):
            return (rate, "on_selling")
        return (None, "ambiguous")
    if re.search(r"\bprofit\b", low):
        return (None, "ambiguous")
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
                 "cheque issued to", "cheque paid to", "paid ... by cheque",
                 # Sprint 15I-TX: settlement-cheque wording
                 # ('Settled Mr. Roger Federer's account by issuing him a
                 # cheque of Rs.41,500') is a payment to the party by
                 # cheque - Dr party / Cr Bank, never a guess.
                 "issued him a cheque", "issued her a cheque",
                 "issued them a cheque", "issuing him a cheque",
                 "issuing her a cheque", "issuing them a cheque",
                 "settled by cheque", "settled by a cheque"),
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
            party = spec.get("party")
            # a spec carrying a literal party name (already extracted by
            # the rule, e.g. the '<Party> returned goods' subject form)
            # resolves to that exact name; only the placeholder kinds
            # ('giver' / 'receiver') re-run the text parser.
            if isinstance(party, str) and party not in ("giver", "receiver"):
                return party
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
    Platrixa never invents a party from an arbitrary lowercase word.
    """
    if not text:
        return None
    # Sprint 15I-TX: 'Settled Mr. Roger Federer's account by issuing him
    # a cheque of Rs.41,500' / 'Settled the account of Mr. Roger Federer
    # by cheque' - the party is the OWNER of the settled account, read
    # from the possessive / 'of' form (never an invented name).
    m_own = re.match(
        r"\s*(?:settled|paid)\s+(?:the\s+)?"
        r"([A-Za-z][A-Za-z' .]{1,40}?)'s\s+account\b",
        str(text or ""), re.IGNORECASE)
    if m_own:
        party = _normalise_party_token(
            m_own.group(1).strip().rstrip(".;,"))
        if party:
            return party
    m_of = re.match(
        r"\s*(?:settled|paid)\s+the\s+account\s+of\s+"
        r"([A-Za-z][A-Za-z' .]{1,40}?)(?:\s+(?:by|for|with|at|on)\b|$)",
        str(text or ""), re.IGNORECASE)
    if m_of:
        party = _normalise_party_token(
            m_of.group(1).strip().rstrip(".;,"))
        if party:
            return party
    low = text.lower()
    # Sprint 15I-UZ (D1): a SALE with a named buyer ('Sold goods
    # [purchased from X] ... to Y') resolves the party from the BUYER
    # clause - the supplier in the provenance clause is never the
    # receiver account, and 'purchased goods from ' must not fire before
    # 'sold ... to ' (the old order captured the supplier and a trailing
    # 'of' -> 'Mr. Roger Federer Of').
    _sale_with_buyer = bool(
        re.search(r"\bsold\b", low)
        and re.search(r"\bsold\b[^.;]*?\bto\b\s+[a-z]",
                      _direction_scan_text(low)))
    if _sale_with_buyer:
        markers = ("sold goods on credit to ", "sold goods to ",
                   "on credit to ", "sold to ", " to ",
                   # cheque-in-favour wording (Sprint 15F)
                   "in favour of ", "in favor of ", "cheque in favour of ")
    else:
        markers = ("on credit from ", "sold goods on credit to ",
                   "on credit to ", "purchased goods from ", "purchased from ",
                   "bought goods from ", "bought from ", "sold goods to ",
                   "paid to ", "received from ", "sold to ",
                   "returned goods to ", "returned by ", "goods returned by ",
                   "received cash from ", "paid cash to ", "paid ",
                   "from ", " to ",
                   # cheque-in-favour wording (Sprint 15F)
                   "in favour of ", "in favor of ", "cheque in favour of ")
    for marker in markers:
        if marker in low:
            idx = low.index(marker) + len(marker)
            rest = text[idx:]
            m = re.match(r"\s*([A-Za-z][A-Za-z' .]{1,40}?)(?:\s+by\s+|\s+for\s+"
                         r"|\s+against\s+|\s+on\s+|\s+with\s+|\s+worth\s+"
                         r"|\s+and\s+|\s+in\s+|\s+at\s+|\s+costing\s+"
                         r"|\s+₹|\s+Rs|\s+\d|,|$)",
                         rest, re.IGNORECASE)
            if m:
                party = m.group(1).strip().rstrip(".;,")
                if party and not party.lower().endswith(
                        ("a/c", "account", "ltd", "limited")):
                    # Filter out common non-party nouns that follow 'paid'
                    # (e.g. 'paid transportation of', 'paid freight for').
                    # These describe what was paid, not who was paid.
                    _NON_PARTY_AFTER_PAID = (
                        "transportation", "freight", "carriage",
                        "rent", "salary", "salaries", "wages",
                        "interest", "insurance", "commission",
                        "electricity", "telephone", "printing",
                        "stationery", "repairs", "maintenance",
                    )
                    if party.lower().split()[0] in _NON_PARTY_AFTER_PAID:
                        continue
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


def _party_accounts_for(specs: List[Any], text: str,
                        party_kind: str) -> set:
    """The accounts a spec list resolves that are PARTIES (any spec whose
    placeholder is {'party': ...}). Sprint 15I-R: the journal line
    builders use this to classify a resolved party as a Personal account
    regardless of its name shape ('Ravi Kumar' is a person, never a
    Nominal account). Presentation metadata only - journal decisions are
    unchanged. The name-based traditional_class_for() fallback stays
    strict because the 15G canonical authority also uses it to separate a
    genuine party from an invented account.
    """
    out: set = set()
    for spec in specs:
        if isinstance(spec, dict) and spec.get("party"):
            account = _resolve_bk_spec(spec, text, party_kind)
            if account:
                out.add(account)
    return out


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
                # the customer is a PARTY (Personal account) even though
                # the name came from the subject position - the literal
                # party spec keeps the line's class Personal.
                "debit": ["Sales Returns"], "credit": [{"party": name}],
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
    # next one. Platrixa refuses the compound so the student enters the two
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
                        "Platrixa will not silently combine: the second "
                        "one carries its own expense/party identity. "
                        "Platrixa never folds it into the first transaction "
                        "as a partial payment - enter the two "
                        "transactions separately."),
            }
    # Sprint 15I-TX: placing an order is NOT a transaction - no journal
    # entry is recorded until the goods are actually received/supplied.
    if re.search(r"\bplaced\s+(?:an\s+)?order\b",
                 " " + text.lower() + " "):
        return {
            "key": "ORDER_PLACED",
            "label": "Order placed (not a transaction yet)",
            "refuse": True, "debit": [], "credit": [],
            "why": ("Placing an order is not a transaction: no journal "
                    "entry is recorded until the goods are actually "
                    "received or supplied. Platrixa does not journal an "
                    "order."),
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
                            "capital. Platrixa never guesses the split."),
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
                        "to capitalise into. Platrixa never guesses the split."),
            }
    # goods-return wording ('returned ... to <party>' = purchase return;
    # '<party> returned goods' = sales return) - structural, registry-free.
    returns = _returns_rule(text)
    if returns is not None:
        return returns
    # Sprint 15I-TX: business/personal-use split transactions
    # ('Withdrew Rs.10,000 from Bank, out of which Rs.3,500 for personal
    # use', 'Purchased goods worth Rs.10,000, out of which goods worth
    # Rs.2,000 were taken for personal use') - every stated amount gets a
    # deterministic role, resolved by _business_personal_split().
    _bp_split = _business_personal_split(text)
    if _bp_split is not None:
        if _bp_split["kind"] == "bank_withdrawal":
            return {
                "key": "BANK_WITHDRAWAL_PERSONAL_SPLIT",
                "label": "Bank withdrawal split: personal + office use",
                "debit": ["Cash"], "credit": ["Bank"],
            }
        return {
            "key": "GOODS_PURCHASE_PERSONAL_SPLIT",
            "label": "Goods purchased: personal-use portion taken",
            "debit": ["Purchases"], "credit": [{"party": "giver"}],
        }
    # Sprint 15I-TX: donations of goods (Donation A/c Dr / Purchases A/c
    # Cr at the stated value) and cash donations (Donation A/c Dr / Cash
    # A/c Cr). A donation carrying a stated PROFIT element is refused -
    # the treatment of the profit portion is not deterministically
    # established, so Platrixa never invents it.
    if re.search(r"\bdonat", low):
        if re.search(
                r"\b(?:including|with|at|less)\s+(?:a\s+)?profit\b"
                r"|\bprofit\s+of\b|\bprofit\s+on\s+cost\b", low):
            return {
                "key": "DONATION_PROFIT_AMBIGUOUS",
                "label": "Donated goods with a profit element",
                "refuse": True, "debit": [], "credit": [],
                "why": ("The donation carries a stated profit element on "
                        "the goods. The treatment of that profit portion "
                        "is not deterministically established in the "
                        "verified FYJC surface - Platrixa never invents it. "
                        "State the cost of the goods donated."),
            }
        if "goods" in low:
            return {
                "key": "DONATION_OF_GOODS",
                "label": "Goods donated",
                "debit": ["Donation"], "credit": ["Purchases"],
            }
        return {
            "key": "DONATION_CASH",
            "label": "Cash donated",
            "debit": ["Donation"], "credit": ["Cash", "Bank"],
        }
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
    # explains the cash side) is REVIEW_REQUIRED - Platrixa never guesses the
    # settlement mode (Sprint 15H ambiguity attacks).
    if _contradictory_cash_credit(low):
        return {
            "key": "MODE_CONTRADICTORY",
            "label": "Cash and credit mode both stated",
            "refuse": True, "debit": [], "credit": [],
            "why": ("The description states both a cash mode and a credit "
                    "mode with no payment step to reconcile them. Platrixa "
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
    # Sprint 15I-UZ (D1): the transaction DIRECTION is decided by the
    # VERB before any word-list match. 'Sold goods worth Rs.X to
    # <party>' is a SALE - the bare 'goods worth' purchase keyword must
    # never flip it into a purchase. When both a sale verb and a
    # purchase verb appear, the wording is only a sale when the purchase
    # phrase is provenance INSIDE the sale clause ('Sold goods
    # [purchased from X] to Y'); otherwise the direction is genuinely
    # ambiguous and Platrixa refuses (never guesses).
    sale_verb = _sale_direction_in(low)
    purchase_verb = _purchase_direction_in(low)
    direction = None
    if sale_verb and purchase_verb:
        scan = _direction_scan_text(low)
        m_sale_start = re.search(r"\bsold\b", scan)
        m_to = re.search(r"\bsold\b[^.;]*?\bto\b\s+[a-z]", scan)
        m_from = re.search(
            r"\b(?:purchased|bought)\b[^.;]*?\bfrom\b\s+[a-z]", scan)
        provenance = bool(m_sale_start and m_to and m_from
                          and m_sale_start.start() <= m_from.start()
                          and m_from.end() < m_to.end())
        if provenance:
            direction = "sale"
        else:
            return {
                "key": "DIRECTION_CONTRADICTORY",
                "label": "Purchase and sale wording both present",
                "refuse": True, "debit": [], "credit": [],
                "why": ("The description contains BOTH purchase and sale "
                        "wording and Platrixa cannot deterministically decide "
                        "which direction the goods moved. It never guesses "
                        "a direction - split it into two transactions."),
            }
    elif sale_verb:
        direction = "sale"
    elif purchase_verb:
        direction = "purchase"
    elif "goods worth" in low or "stock worth" in low:
        # 'Goods worth Rs.X from <party>' (no verb) is the established
        # credit-purchase form (Sprint 15E).
        direction = "purchase"
    # 'Goods costing Rs.10,000 sold ... for cash Rs.12,000' is a sale; the
    # COST figure is not the sale value (dropped in the amount pipeline).
    costing_sale = "costing" in low and any(k in low for k in (
        "sold", "sale ", "sales"))
    if has_cash_mode and not has_credit_mode:
        if direction != "sale" and any(k in low for k in goods_purchase_words):
            return {
                "key": "PURCHASE_GOODS_CASH",
                "label": "Goods purchased for cash",
                "debit": ["Purchases"], "credit": ["Cash", "Bank"],
            }
        if direction == "sale" and (any(k in low for k in goods_sale_words)
                                    or costing_sale):
            # 'Sold goods to Mohan ... ; received cash for half at once' is
            # a CREDIT sale with a PARTIAL collection (Mohan stays a
            # debtor for the unpaid balance). The 'cash' word describes the
            # collection, not the sale mode - a named customer + a payment
            # fraction keeps the sale on credit unless the wording says
            # 'for cash' (Sprint 15F: Mohan never becomes a debtor for a
            # true cash sale, and never disappears from a partial one).
            _sale_party = bool(re.search(
                r"\bsold\b[^.;]*?\bto\b\s+[a-z]",
                _direction_scan_text(low))) or "sold to" in low
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
        if direction != "sale" and any(k in low for k in goods_purchase_words):
            return {
                "key": "PURCHASE_GOODS_CREDIT",
                "label": "Goods purchased on credit",
                "debit": ["Purchases"], "credit": [{"party": "giver"}],
            }
        if direction == "sale" and any(k in low for k in goods_sale_words):
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
    # Sprint 15I-TORTURE: a RECEIPT of an amount that was previously
    # written off as bad ('Received Rs.2,000 from Kamal, which had
    # earlier been written off as bad') is a BAD-DEBT RECOVERY - the
    # existing BAD_DEBTS_RECOVERED rule (Dr Cash/Bank, Cr Bad Debts
    # Recovered) applies, never an ordinary receipt (Cr the debtor's
    # personal account would invent an active debtor balance for a debt
    # that was written off), and never an invented concatenated account
    # like 'Kamal As Bad Debts Recovered'. Fires BEFORE the
    # cheque/deposit/receipt branches so a recovery by cheque also
    # credits Bad Debts Recovered (Bank Dr), and BEFORE the generic
    # RECEIVED_FROM fallback. A write-OFF without receipt evidence
    # ('Bad debts written off ...') never reaches this branch - the
    # BAD_DEBTS machinery is never weakened.
    _recovery_evidence = (
        "bad debts recovered" in low or "bad debt recovered" in low
        or "recovered from bad debt" in low or "recovered from bad" in low
        or ("written off" in low and ("received" in low or "got" in low))
    )
    if _recovery_evidence and "from" in low:
        return {
            "key": "BAD_DEBTS_RECOVERED",
            "label": "Bad debts recovered",
            "debit": (["Bank"] if ("cheque" in low or "check" in low)
                      else ["Cash", "Bank"]),
            "credit": ["Bad Debts Recovered"],
        }
    # A CHEQUE deposited into the bank is never cash: the counterparty is
    # the drawer of the cheque. 'Cheque deposited into bank' without a
    # named drawer is REVIEW_REQUIRED - Platrixa never turns a cheque into a
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
                    "named. Platrixa never treats a cheque as cash."),
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
                            or "cheque was received" in low
                            or ("received" in low and "by cheque" in low)):
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
    # Sprint 15I-TX: 'paid Rs.500 for mobile recharge' / 'Paid Rs.4,000
    # for shop rent' / 'Paid interest for loan by cheque' - the amount
    # often sits between 'paid' and 'for', so the contiguous registry
    # phrases cannot match. The REGISTERED expense word on either side
    # of 'for' carries the account; Platrixa never promotes an ordinary word
    # into a party. A possessive-pronoun bill ('paid his mobile bill')
    # marks a personal bill and is never silently booked as a business
    # expense.
    if "paid" in low and " for " in low:
        if _expense_near_for(low) is not None:
            return {
                "key": "EXPENSE_PAID",
                "label": "Expense paid",
                "debit": ["_EXPENSE_ACCOUNT"], "credit": ["Cash", "Bank"],
            }
    for cand in BK_PATTERNS:
        when = cand["when"]
        phrases = when if isinstance(when, (tuple, list)) else (when,)
        if any(phrase in low for phrase in phrases):
            # Sprint 15I-UZ (D1): the direction decided by the verb wins
            # over a matching word list - a sale sentence never matches a
            # purchase pattern (and vice versa).
            if direction == "sale" and "PURCHASE" in cand["key"]:
                continue
            if direction == "purchase" and "SALE" in cand["key"]:
                continue
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
    # Sprint 15I-UZ (D1): 'Sold goods worth Rs.X to <party>' - the amount
    # sits between the goods word and 'to', so no contiguous phrase can
    # match, but a named recipient is the SAME credit-sale evidence as
    # 'Sold goods to Ram' (never a purchase, never a refusal - a sale
    # sentence can never fall through to the 'goods worth' purchase
    # pattern). With no named recipient the sale stays in the ambiguity
    # layer (REVIEW_REQUIRED) - the cash/credit mode is never guessed.
    if direction == "sale" and any(k in low for k in goods_sale_words):
        if re.search(r"\bsold\b[^.;]*?\bto\b\s+[a-z]",
                     _direction_scan_text(low)):
            return {
                "key": "SALE_GOODS_CREDIT",
                "label": "Goods sold on credit",
                "debit": [{"party": "receiver"}], "credit": ["Sales"],
            }
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
                    f"({', '.join(assets)}). Platrixa never guesses the split."),
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
    # Sprint 15I-UZ (D4): a discount is derived ONLY from genuine
    # SETTLEMENT wording. 'against his account of Rs.X' / 'in part
    # payment of' / 'on account' describe a PARTIAL receipt/payment - the
    # shortfall is never an invented discount.
    settlement_only = ("full settlement" in low or "settlement of" in low
                       or "in settlement of" in low
                       or "settling his account" in low
                       or "settled his account" in low
                       or "final settlement" in low)
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


def _last_amount_bound_party(question: str) -> Optional[str]:
    """The party named by a trailing 'to <Name>' / 'from <Name>' clause
    attached to the LAST stated figure, or None. Sprint 15I-S: 'Paid
    Rs.9,000 to Mohan and Rs.8,000 to Rahul.' binds the second figure to
    Rahul - the paid-split's 'last amount is the payment' heuristic must
    never consume a figure the wording claims for a DIFFERENT party.
    Deterministic; only a Capitalised name reads as a party ('to the
    bank' never does)."""
    raw = " " + str(question or "") + " "
    # the LAST stated figure is the one the paid-split would consume -
    # check the clause attached to it specifically.
    last_num = None
    for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", raw):
        last_num = match
    if last_num is None:
        return None
    tail = raw[last_num.end():]
    relation = re.match(r"\s*(?:to|from)\s+", tail, flags=re.IGNORECASE)
    if not relation:
        return None
    body = tail[relation.end():]
    _continuation = re.compile(
        r"(?:,|\.|;|and\b|immediately\b|at once\b|in cash\b|"
        r"for cash\b|on credit\b|in full\b|on account\b|"
        r"in settlement\b|with\b)", re.IGNORECASE)
    m1 = re.match(r"([A-Za-z][A-Za-z'.]*)", body)
    if not m1:
        return None
    first = m1.group(1).rstrip(".")
    after_first = body[m1.end():].lstrip()
    name = first
    if not _continuation.match(after_first):
        m2 = re.match(r"([A-Za-z][A-Za-z'.]*)", after_first)
        if m2:
            second = m2.group(1).rstrip(".")
            after_second = after_first[m2.end():].lstrip()
            if not after_second or _continuation.match(after_second):
                name = f"{first} {second}"
                after_first = after_second
    # the party clause must end cleanly right after the captured name -
    # otherwise the following words belong to the verb phrase, not the
    # party ('paid Rs.5,000 to Rahul immediately' names Rahul, never
    # 'Rahul Immediately').
    if after_first and not _continuation.match(after_first):
        return None
    # _normalise_party_token gates the name against ordinary words
    # ('to the bank', 'from customers') and normalises case
    # ('rahul' -> 'Rahul'); None means it is not a real party.
    return _normalise_party_token(name)


def _paid_list_binds_multiple(question: str) -> bool:
    """True when the payment verb binds MORE THAN ONE stated figure
    ('paid Rs.20,000 and Rs.18,000 to Rahul'). Both figures are then
    payments - neither has a deterministically established role, so the
    paid-split must never claim one of them as 'the' payment (Sprint
    15I-S). The figures must be joined by 'and' under the SAME paid
    clause; a legitimate single payment after a purchase ('... on
    credit, paid Rs.5,000 immediately') is never matched, and a
    settlement phrase ('paid Rs.9,500 in full settlement of his account
    of Rs.10,000') is routed through the explicit-discount branch, never
    here."""
    low = " " + str(question or "").lower() + " "
    _amt = r"(?:rs\.?|\u20b9|inr)?\s*\d[\d,]*(?:\.\d+)?"
    # 'paid Rs.X and Rs.Y' - the paid verb introduces the pair.
    if re.search(
            r"\bpaid\s+" + _amt + r"\s+and\s+" + _amt, low):
        return True
    # 'Rs.X and Rs.Y ... paid' - the paid verb follows the pair within
    # the same clause ('.' / ';' end the clause).
    if re.search(
            _amt + r"\s+and\s+" + _amt
            + r"\s+[^.;]{0,40}?\bpaid\b", low):
        return True
    # 'paid ... Rs.X and Rs.Y' - the paid verb precedes the pair with
    # wording between ('paid to Rahul Rs.X and Rs.Y', 'paid him
    # Rs.X and Rs.Y') but still inside the same clause.
    if re.search(
            r"\bpaid\b[^.;]{0,60}?" + _amt + r"\s+and\s+" + _amt, low):
        return True
    return False


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

    # Sprint 15I-UZ (D3): profit evidence - 'at Y% profit on cost price'
    # (sale value = cost x (1 + Y%)) vs 'profit on selling price' (sale
    # value = cost / (1 - Y%)). A profit wording whose convention cannot
    # be read deterministically is never silently dropped - it stays a
    # concern that forces REVIEW_REQUIRED.
    profit_rate: Optional[Decimal] = None
    profit_kind: Optional[str] = None
    _profit_info = _profit_on_cost(question)
    if _profit_info is not None:
        profit_rate, profit_kind = _profit_info

    # Sprint 15I-TX: business/personal-use splits resolve BOTH stated
    # figures deterministically (withdrawal/purchase total + personal-use
    # portion), so the unresolved-amount gate below never fires on them.
    _bp_split = _business_personal_split(question)
    if _bp_split is not None:
        return _resolve_business_personal_split(_bp_split, amounts)

    concerns: List[str] = []
    if ambiguous:
        concerns.append("An amount could not be read cleanly (OCR-style "
                        "uncertainty). Platrixa never silently corrects it.")

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

    # Sprint 15I-UZ (D3): profit-on-cost / profit-on-selling-price
    # modifier. The stated figure is the COST; the posted sale value is
    # cost x (1 + profit%) for profit ON COST, or cost / (1 - profit%)
    # for profit ON SELLING PRICE. Both are deterministic FYJC
    # conventions - the stated profit percentage is never dropped.
    _sale_dir_text = _sale_direction_in(low)
    if profit_rate is not None and profit_kind == "on_cost" and _sale_dir_text:
        list_price = (list_price * (Decimal(100) + profit_rate)
                      / Decimal(100)).quantize(Decimal("0.01"))
        steps.append({
            "calculation_id": "BK_PROFIT_ON_COST",
            "label": "Apply Profit on Cost",
            "formula": "Selling price = Cost x (1 + Profit on cost %)",
            "inputs": {"cost": amounts[0], "profit_on_cost": profit_rate},
            "result": list_price,
        })
    elif profit_rate is not None and profit_kind == "on_selling" \
            and _sale_dir_text:
        if profit_rate >= Decimal(100):
            concerns.append(
                "The stated profit on selling price is 100% or more - "
                "the selling price cannot be derived. Platrixa never "
                "guesses it.")
        elif "costing" in low or "cost price" in low:
            list_price = (list_price * Decimal(100)
                          / (Decimal(100) - profit_rate)).quantize(
                              Decimal("0.01"))
            steps.append({
                "calculation_id": "BK_PROFIT_ON_SELLING",
                "label": "Apply Profit on Selling Price",
                "formula": ("Selling price = Cost / (1 - Profit on "
                            "selling price %)"),
                "inputs": {"cost": amounts[0],
                           "profit_on_selling": profit_rate},
                "result": list_price,
            })
        else:
            concerns.append(
                "Profit on selling price is stated but the COST figure "
                "is not identifiable. Platrixa never guesses the base.")
    elif profit_rate is not None and profit_kind in ("on_cost", "on_selling") \
            and not _sale_dir_text:
        concerns.append(
            "Profit wording appears outside a sale - Platrixa never guesses "
            "the convention.")
    elif profit_rate is None and profit_kind == "ambiguous":
        concerns.append(
            "Profit is mentioned but its percentage or convention cannot "
            "be read deterministically. Platrixa never silently drops it.")

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
    # Sprint 15I-UZ (D2): the 'T.D.' abbreviation ('worth Rs.X @ 12%
    # T.D.') is the same trade-discount rate as 'trade discount' - it is
    # applied, never silently ignored. A 'C.D.' label is a cash-discount
    # hint and never becomes a trade discount here.
    if trade_rate is None:
        for rate, label in percents:
            if (re.search(r"\bt\.?d\.?\b", label)
                    and not re.search(r"\bc\.?d\.?\b", label)):
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
        if m_td is None:
            # Sprint 15I-S: the stated amount directly BEFORE the noun
            # ('with Rs.2,000 trade discount', 'Rs.2,000 trade discount')
            # is the same deterministic TD amount - it is netted, never
            # silently dropped as an unresolved figure.
            m_td = re.search(
                r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
                r"\s+(?:trade\s+discount|td)\b", low)
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
                        "positive and smaller than the list price). Platrixa "
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
    # Sprint 15I-UZ: payment-fraction role (pre-declared so the central
    # rate-consumption gate below can reference it even when the
    # explicit-discount path applies).
    fraction: Optional[Decimal] = None
    # Sprint 15I-L: a settlement figure EXPLICITLY STATED in the
    # question ('paid Rs.9,800', 'Received Rs.9,800 from Ram') is NET
    # evidence - a cash-discount rate must apply to the amount due,
    # never to the stated figure itself.
    paid_stated = False
    explicit_paid: Optional[Decimal] = None
    if explicit_discount is None:
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
        # Sprint 15I-UZ (D5): an explicit cheque amount ('issued a cheque
        # of Rs.20,000 in his favour') is a stated payment step - the
        # figure is consumed as the paid portion. A cheque issued BY THE
        # CUSTOMER in a sale is received only on deposit and is never the
        # business's own payment.
        _cheque_amt = _cheque_amount_in(low)
        if _cheque_amt is not None and _cheque_amt in amounts \
                and not _customer_issued_cheque(low):
            explicit_paid = _cheque_amt
            paid_stated = True
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
                "deterministically. Platrixa never silently drops it.")

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
                "given. Platrixa never applies a discount rate to the "
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
                    "stated discount rate on the amount due. Platrixa never "
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
                "transaction value. Platrixa never records them.")
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
            "apply it to. Platrixa never applies a discount without a "
            "settlement step.")

    # Sprint 15I-UZ (central invariant): every stated RATE/percent must
    # be consumed by a deterministic role (trade discount, cash discount,
    # payment fraction, profit). A rate with no role is silently-ignored
    # evidence - REVIEW_REQUIRED, never a confident journal.
    consumed_rates: set = set()
    if trade_rate is not None:
        consumed_rates.add(trade_rate)
    if cash_discount_rate is not None:
        consumed_rates.add(cash_discount_rate)
    if profit_rate is not None:
        consumed_rates.add(profit_rate)
    if fraction is not None:
        consumed_rates.add(fraction)
    # Sprint 15I-UZ: GST rates are consumed by the GST journal authority
    # (_gst_facts/_gst_journal) whenever GST evidence is deterministically
    # established - they are never "unassigned" by the non-GST roles here.
    _gst_facts_here = _gst_facts(question)
    if _gst_facts_here is not None:
        for _rate, _kind in _gst_facts_here.get("rates") or []:
            consumed_rates.add(_rate)
    for rate, _label in percents:
        if rate not in consumed_rates:
            concerns.append(
                f"A stated rate ({_label.strip() or rate}%) could not be "
                "assigned a deterministic accounting role. Platrixa never "
                "silently ignores it.")
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

    # Sprint 15I-S: unresolved multi-amount gate. Every figure stated in
    # the question must be consumed by a DETERMINISTIC role (list price,
    # an explicit trade/cash discount amount, a stated payment, a
    # full-settlement pair, or a started-business asset component). A
    # stated amount that no role consumes is UNRESOLVED - Platrixa never
    # picks one amount over another by position ('first amount wins' is
    # forbidden), so the transaction is REVIEW_REQUIRED instead of a
    # confident journal built on the first figure.
    #
    # The gate fires only for ONE transaction description (after
    # payment-step re-joining, _split_transactions returns a single
    # segment). A multi-transaction question is resolved per-segment by
    # the book-keeping authority, where this same gate applies to every
    # segment - the whole-text resolver is never the authority for a
    # combined wording, and the canonical-lineage layer (which consults
    # it for metadata) must not see a VERIFIED multi-transaction
    # question as ambiguous.
    if status == VERIFIED and len(_split_transactions(question)) == 1:
        consumed: Counter = Counter()
        if explicit_discount is not None:
            # full settlement: the stated cash amount and the party total
            # are the two figures (list_price IS the settlement cash
            # here); a stated discount amount is consumed as well.
            consumed[explicit_discount["cash_amount"]] += 1
            consumed[explicit_discount["party_total"]] += 1
            if explicit_discount["discount_amount"] in amounts:
                consumed[explicit_discount["discount_amount"]] += 1
        else:
            if list_price in amounts:
                consumed[list_price] += 1
            elif amounts:
                # profit-on-cost: the posted value is cost x (1 + p) -
                # the STATED figure is the cost and is consumed.
                consumed[amounts[0]] += 1
            if trade_amount is not None and trade_rate is None:
                # explicit trade-discount AMOUNT ('less Rs.2,000 trade
                # discount') - the stated figure fills that role.
                consumed[trade_amount] += 1
            if cash_discount_amt is not None:
                consumed[cash_discount_amt] += 1
            if paid_stated and explicit_paid is not None:
                # Sprint 15I-S: the last figure must not be claimed by a
                # DIFFERENT party ('paid Rs.9,000 to Mohan and Rs.8,000
                # to Rahul') - the paid-split would otherwise consume
                # Rahul's figure as cash paid on Mohan's transaction.
                # Likewise 'paid Rs.X and Rs.Y' binds BOTH figures to the
                # payment - neither role is deterministic, so the paid
                # figure is not consumed and the gate refuses instead of
                # building a journal on a positional pick.
                bound = _last_amount_bound_party(question)
                primary = _party_from_text(question)
                if (bound is None or primary is None
                        or bound == _normalise_party_token(primary)) \
                        and not _paid_list_binds_multiple(question):
                    consumed[explicit_paid] += 1
            # Sprint 15I-UZ (D4): the stated account balance ('his
            # account of Rs.X' / 'against his account of Rs.X') is a
            # deterministic role - the received/paid figure is a partial
            # (or at-par) settlement and the difference is never an
            # invented discount. Both stated figures are consumed.
            _balance_fig = _account_balance_figure(low)
            if _balance_fig is not None and _balance_fig in amounts:
                consumed[_balance_fig] += 1
            # started business: every stated figure is a named asset
            # component (Cash / Furniture / Bank ...) - the deterministic
            # breakdown consumes them all when it reconciles with the
            # stated total.
            startup = _startup_asset_breakdown(question)
            if startup is not None and startup.get("total") == sum(amounts):
                consumed = Counter(amounts)
        unresolved = Counter(amounts) - consumed
        if unresolved:
            def _fmt_figure(a: Decimal) -> str:
                return f"Rs.{float(a):,.2f}".rstrip("0").rstrip(".")
            all_figures = ", ".join(_fmt_figure(a) for a in sorted(amounts))
            un_figures = ", ".join(_fmt_figure(a) for a in sorted(unresolved))
            plural = "s" if len(unresolved) > 1 else ""
            concerns.append(
                "Several amounts are present (" + all_figures + ") but "
                "Platrixa cannot assign every one a deterministic role ("
                + un_figures + " remain" + plural + " unexplained). It "
                "never chooses one amount over another - clarify which "
                "figure is the transaction amount.")
            status = REVIEW_REQUIRED
            why_not = "; ".join(concerns)
            next_action = ("Re-type the transaction so each amount has a "
                           "clear role (amount, discount, payment, "
                           "settlement), or split it into separate "
                           "transactions.")

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
    # Platrixa never assumes one.
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
    # Sprint 15I-UZ (D2): protect two-letter abbreviations ('T.D.',
    # 'C.D.', 'N.E.F.T.') so the '.' inside them is never treated as a
    # sentence boundary ('at 12% T.D.' must stay ONE transaction - it is
    # the trade-discount rate, not the end of a sentence). The \x02
    # sentinel is restored to '.' after splitting.
    raw = _ABBREV_RE.sub(lambda m: m.group(1) + "\x02" + m.group(2) + "\x02",
                         raw)
    # Sprint 15I-TX: the protected title keeps its ORIGINAL case and the
    # \x01 sentinel is restored to '. ' - so 'Mr. Novak' survives the
    # split as 'Mr. Novak' (never the broken 'mr . Novak' that made the
    # capitalised-party detection in _returns_rule/_party_from_text
    # miss a party that legitimately opens the sentence).
    raw = _TITLE_RE.sub(lambda m: m.group(1) + "\x01", raw)
    # Sprint 15I-TX: a comma-joined return chain ('X returned us goods
    # worth Rs.6,500, and the same were returned to Y') is TWO return
    # transactions - the customer-return and the subsequent supplier-
    # return. Normalise the joiner into a ';' boundary so the second
    # return is journaled independently and never silently swallowed by
    # the first (15I-TX regression: Test 9-style returns).
    raw = _RETURN_CHAIN_RE.sub("; the same were returned", raw)
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
            r"(?<=[a-zA-Z0-9)%])\.\s+(?=[A-Z])|"
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
    segments = [seg.replace("\x01", ". ").replace("\x02", ".").strip()
                for seg in pieces if seg.strip()]
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
        # Sprint 15I-CAPABILITY-CLOSURE: recognise a purchase even when
        # classify_bk_type returns None (no explicit cash/credit marker).
        # 'Purchased goods for ₹50,000' is still a purchase — it just
        # needs the payment steps merged so the engine can resolve the
        # cash/credit split deterministically.
        is_purchase_prior = bool(prior_pattern) and \
            "PURCHASE" in prior_pattern["key"]
        if not is_purchase_prior and prior:
            _prior_low = (prior or "").lower()
            is_purchase_prior = bool(re.search(
                r"\b(?:purchas|bought|acqui)\w*\b", _prior_low))
        low_seg = " " + seg.lower() + " "
        # Sprint 15I-CAPABILITY-CLOSURE: merge ALL payment steps into a
        # purchase, not just the first one.  Multi-payment transactions
        # ('Paid ₹40K cash. Paid ₹30K cheque. Balance ₹30K due.') need
        # all payment steps resolved inside one segment so the engine can
        # determine the cash/credit split and outstanding liability
        # deterministically.  The prior_has_pay guard was preventing the
        # second and subsequent payments from merging.
        # Guard: do NOT merge a payment into a prior purchase when the
        # payment names a different party.  'Paid Rs.10000 to Ramesh'
        # following 'Purchased goods from Mehta' is an independent
        # settlement transaction, not a payment step of Mehta's purchase.
        _pay_party = _extract_party_from_segment(seg)
        _prior_party = _extract_party_from_segment(prior) if prior else None
        _cross_party = bool(_pay_party and _prior_party
                            and _pay_party.lower() != _prior_party.lower())
        if prior and _is_payment_step(seg) and is_purchase_prior \
                and not _cross_party \
                and (" paid " in low_seg or " discount " in low_seg or " payment " in low_seg or " was paid " in low_seg or " was made " in low_seg):
            merged[-1] = merged[-1] + "; " + seg
        else:
            merged.append(seg)
    return merged


# Sprint 15I-TX: a comma-joined '... and the same were returned to
# <party>' continuation - the goods returned by a customer are then
# returned to the supplier, a SECOND return transaction that must never
# be silently absorbed by the first.
_RETURN_CHAIN_RE = re.compile(
    r",\s+and\s+the\s+same\s+(?:goods\s+)?(?:were|was|have been|"
    r"had been|have\s+been|had\s+been)\s+returned\b",
    re.IGNORECASE)


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
        after = low_rates[match.end():match.end() + 20]
        # Sprint 15I-TX: the look-back is truncated at the nearest
        # preceding clause boundary (comma / semicolon / period) so a
        # rate in a LATER clause is never labelled by a GST token from
        # an EARLIER one - '... CGST and SGST @ 9% each, and issued a
        # cheque for 50% of the amount' must not read the 50% as a
        # second GST rate.
        _cut = max(before.rfind(","), before.rfind(";"),
                   before.rfind("."))
        if _cut != -1:
            before = before[_cut + 1:]
        # Sprint 24: also check text immediately AFTER the % token
        # for a GST label - 'plus 18% GST' has 'gst' after the rate.
        _cut_after = min(
            (after.find(",") if "," in after else 999),
            (after.find(";") if ";" in after else 999),
            (after.find(".") if "." in after else 999),
        )
        if _cut_after != 999:
            after = after[:_cut_after]
        combined = before + " " + after
        if not _GST_TOKEN_RE.search(combined):
            continue
        kind = "total"
        for comp in ("igst", "cgst", "sgst"):
            if comp in combined:
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
        # Sprint 15I-TX: same clause-boundary rule as the rate labelling
        # - an amount after a punctuation break belongs to its own
        # clause and is never labelled by an earlier GST component
        # ('... CGST Rs.900. Paid Rs.500 for something' -> the 500 is
        # unlabeled, never a second CGST amount).
        _cut = max(before.rfind(","), before.rfind(";"),
                   before.rfind("."))
        if _cut != -1:
            before = before[_cut + 1:]
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
    if re.search(r"\binclusive\s+of\s+(?:[\d,]+%\s+)?gst\b|\bgst\s+inclusive\b|"
                 r"\bincluding\s+(?:[\d,]+%\s+)?gst\b|\bgst\s+included\b", low):
        facts["inclusive"] = True
    if re.search(r"\bexclusive\s+of\s+gst\b|\bgst\s+exclusive\b|"
                 r"\bexcluding\s+gst\b|\bgst\s+excluded\b|"
                 r"\bplus\s+(?:[\d,]+%\s+)?gst\b|\bgst\s+extra\b|"
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
    # practice; Platrixa never guesses it (rule 8 -> NOT_SUPPORTED).
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
        # can make it resolvable, and Platrixa never guesses a tax treatment
        # for a transaction the syllabus does not tax.
        return _gst_refusal(
            NOT_SUPPORTED,
            "GST is only supported on goods purchases, goods sales and "
            "expenses in the verified FYJC surface. This transaction "
            "type is not one of them, so Platrixa does not guess its GST "
            "treatment.",
            "Use a supported transaction (purchase, sale, expense) with "
            "an explicit GST rate/components.",
            facts)

    debit_specs = pattern.get("debit") or []
    credit_specs = pattern.get("credit") or []
    # a party spec on the money side means a credit transaction
    is_credit = any(isinstance(s, dict) and "party" in s
                    for s in (credit_specs if not is_sale else debit_specs))

    # Sprint 15I-TX: a GST transaction carrying a PARTIAL payment step
    # (cheque for X%, half paid, issued a cheque in his favour, ...)
    # must not post the full consideration to the party/cash - splitting
    # a GST journal across cash/party is outside the verified surface,
    # so Platrixa refuses instead of silently dropping the payment step.
    if _gst_partial_payment(text):
        return _refuse(
            "The GST transaction also carries a partial payment step "
            "(cheque or payment fraction). Platrixa does not split a GST "
            "journal across cash/party in the verified surface - enter "
            "the GST purchase/sale and the payment as separate steps.",
            "Enter the GST transaction, then the payment separately.")

    # -- GST + discount ----------------------------------------------------
    # Sprint 15I-L: TRADE discount is deterministic with GST - the taxable
    # value is the list price LESS the trade discount, and GST is computed
    # on that net value (trade discount is never a separate journal line).
    # A CASH discount / settlement discount (or a bare 'discount') is a
    # settlement fact, not an invoice fact - Platrixa never folds it into a GST
    # journal (sprint rule 10) and refuses instead of guessing.
    _gst_td = _gst_trade_discount(text)
    if _gst_td is None and "discount" in low:
        return _refuse(
            "A transaction combining GST with a cash/settlement discount is "
            "outside the verified GST surface. Platrixa never applies both "
            "treatments on its own.",
            "Enter the GST transaction and the discount settlement as "
            "separate steps.")

    # -- component scheme (never guessed) ----------------------------------
    comps = set(facts["components"])
    if "IGST" in comps and (comps & {"CGST", "SGST"}):
        return _refuse(
            "The question names both IGST and CGST/SGST for the same "
            "transaction. Platrixa never guesses which tax treatment applies.",
            "State one treatment: either CGST + SGST (intra-state) or "
            "IGST (inter-state).")
    if ("CGST" in comps) != ("SGST" in comps):
        present = "CGST" if "CGST" in comps else "SGST"
        missing = "SGST" if present == "CGST" else "CGST"
        return _refuse(
            f"{present} is named without {missing}. Platrixa never invents the "
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
                "and inter-state. Platrixa never guesses which applies.",
                "State one: intra-state (CGST + SGST) or inter-state (IGST).")
        if facts["intra_state"]:
            scheme = "CGST_SGST"
        elif facts["inter_state"]:
            scheme = "IGST"
        else:
            # Sprint 24: "plus X% GST" without explicit components or
            # state markers defaults to CGST_SGST (intra-state) which is
            # the standard FYJC syllabus assumption.  Exclusive GST with
            # a total rate and no CGST/SGST/IGST components means the
            # student is expected to split the rate equally.
            if facts.get("exclusive") or facts.get("inclusive"):
                # "plus X% GST" (exclusive) or "inclusive of GST" (inclusive)
                # without explicit components defaults to CGST_SGST (intra-state)
                # which is the standard FYJC syllabus assumption.
                scheme = "CGST_SGST"
            else:
                return _refuse(
                    "GST is mentioned with a rate but the question does not "
                    "say whether it is intra-state (CGST + SGST) or "
                    "inter-state (IGST). Platrixa never picks one.",
                    "Name the components ('CGST and SGST') or state the "
                    "intra/inter-state status.")

    if scheme == "CGST_SGST" and facts["inter_state"]:
        return _refuse(
            "CGST + SGST (intra-state) is contradicted by an inter-state "
            "marker. Platrixa never guesses which treatment applies.",
            "State one treatment: CGST + SGST (intra-state) or IGST "
            "(inter-state).")
    if scheme == "IGST" and facts["intra_state"]:
        return _refuse(
            "IGST (inter-state) is contradicted by an intra-state marker. "
            "Platrixa never guesses which treatment applies.",
            "State one treatment: CGST + SGST (intra-state) or IGST "
            "(inter-state).")

    # input vs output side: explicit mentions must match the direction
    side = facts["side"]
    if len(side) > 1:
        return _refuse(
            "The question names both input and output GST for the same "
            "transaction. Platrixa never guesses which applies.",
            "State the tax side for this transaction.")
    expected_side = "output" if is_sale else "input"
    if side and expected_side not in side:
        return _refuse(
            f"The question names "
            f"{'input' if 'input' in side else 'output'} GST on a "
            f"{'sale' if is_sale else 'purchase/expense'} whose verified "
            f"tax side is "
            f"{'output' if expected_side == 'output' else 'input'}. Platrixa "
            "never overrides the accounting direction.",
            "Use the correct tax side for the transaction.")

    # -- rate extraction (explicit only) -----------------------------------
    rates: Dict[str, List[Decimal]] = {
        "total": [], "cgst": [], "sgst": [], "igst": []}
    for rate, kind in facts["rates"]:
        if rate <= 0 or rate > Decimal(100):
            return _refuse(
                f"The stated GST rate ({rate}%) is impossible. Platrixa never "
                "records an invalid rate.",
                "State a valid GST rate (0 < rate <= 100).")
        rates[kind].append(rate)
    for kind in ("total", "cgst", "sgst", "igst"):
        if len({v.quantize(Decimal("0.01")) for v in rates[kind]}) > 1:
            return _refuse(
                f"The question states contradictory {kind.upper()} rates. "
                "Platrixa never guesses which is correct.",
                "State one rate per tax component.")

    def _uniq(vals: List[Decimal]) -> Optional[Decimal]:
        uniq = {v.quantize(Decimal("0.01")) for v in vals}
        return uniq.pop() if len(uniq) == 1 else None

    total_rate: Optional[Decimal] = None
    if scheme == "CGST_SGST":
        if rates["igst"]:
            return _refuse(
                "An IGST rate on an intra-state CGST + SGST transaction. "
                "Platrixa never guesses which treatment applies.",
                "State one treatment.")
        cgst_r = _uniq(rates["cgst"])
        sgst_r = _uniq(rates["sgst"])
        if cgst_r is not None and sgst_r is not None and cgst_r != sgst_r:
            return _refuse(
                "CGST and SGST rates differ. Platrixa never records a "
                "non-standard split.",
                "State equal CGST and SGST rates (or a single GST rate).")
        total_r = _uniq(rates["total"])
        if total_r is not None:
            if cgst_r is not None and cgst_r != (total_r / Decimal(2)):
                return _refuse(
                    "The CGST/SGST rates do not match half the stated GST "
                    "rate. Platrixa never guesses which is correct.",
                    "State consistent rates.")
            total_rate = total_r
        elif cgst_r is not None:
            total_rate = cgst_r * Decimal(2)
    else:  # IGST
        if rates["cgst"] or rates["sgst"]:
            return _refuse(
                "A CGST/SGST rate on an IGST transaction. Platrixa never "
                "guesses which treatment applies.",
                "State one treatment.")
        igst_r = _uniq(rates["igst"])
        total_r = _uniq(rates["total"])
        if igst_r is not None and total_r is not None and igst_r != total_r:
            return _refuse(
                "The IGST rate differs from the stated GST rate. Platrixa "
                "never guesses which is correct.",
                "State consistent rates.")
        total_rate = igst_r if igst_r is not None else total_r

    # -- inclusive / exclusive mode ----------------------------------------
    if facts["inclusive"] and facts["exclusive"]:
        return _refuse(
            "The question says the amount is BOTH inclusive of GST and "
            "exclusive/plus GST. Platrixa never guesses which is meant.",
            "State one: 'inclusive of GST' or 'GST added separately'.")
    mode = "inclusive" if facts["inclusive"] else "exclusive"

    # -- amounts ------------------------------------------------------------
    unlabeled = facts["unlabeled"]
    if len(unlabeled) != 1:
        return _refuse(
            "The GST transaction must carry exactly one stated amount for "
            "the goods/service value. Platrixa never picks between multiple "
            "figures (and never treats a payment step as part of the GST "
            "transaction).",
            "Enter the single transaction amount; enter a partial payment "
            "as a separate step.")
    list_price = unlabeled[0]
    if list_price <= 0:
        return _refuse(
            "The stated amount must be positive. Platrixa never records a "
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
                "smaller than the list price). Platrixa never records it.",
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
                "A CGST/SGST amount on an IGST transaction. Platrixa never "
                "guesses which treatment applies.",
                "State one treatment.")
    else:
        if "IGST" in comp_amt:
            return _refuse(
                "An IGST amount on an intra-state CGST + SGST transaction. "
                "Platrixa never guesses which treatment applies.",
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
                "amount to extract the taxable base. Platrixa never estimates "
                "the base.",
                "State the GST rate (e.g. 'inclusive of GST @ 18%') or the "
                "GST amount.")
        if base <= 0:
            return _refuse(
                "The taxable base derived from the inclusive amount is not "
                "positive. Platrixa never records it.",
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
                "stated. Platrixa never guesses the tax.",
                "State the GST rate (e.g. 'GST @ 18%') or the GST amount.")
        if gst_total <= 0:
            return _refuse(
                "The computed GST amount is not positive. Platrixa never "
                "records it.",
                "Re-check the amount and rate.")

    # -- component split ----------------------------------------------------
    if scheme == "IGST":
        if total_rate is not None:
            igst_amt = _q(base * total_rate / Decimal(100))
            if "IGST" in comp_amt and comp_amt["IGST"] != igst_amt:
                return _refuse(
                    "The stated IGST amount does not match the stated rate "
                    "times the base. Platrixa never guesses which is correct.",
                    "State consistent amounts/rates.")
        elif "IGST" in comp_amt:
            igst_amt = comp_amt["IGST"]
        else:
            return _refuse(
                "IGST is mentioned without a rate or an IGST amount. Platrixa "
                "never guesses the tax.",
                "State the IGST rate or the IGST amount.")
        if igst_amt <= 0:
            return _refuse(
                "The IGST amount is not positive. Platrixa never records it.",
                "Re-check the amount and rate.")
        raw_components = [("IGST", igst_amt)]
    else:
        cgst_amt = comp_amt.get("CGST")
        sgst_amt = comp_amt.get("SGST")
        if (cgst_amt is None) != (sgst_amt is None):
            return _refuse(
                "Only one of the CGST/SGST amounts is stated. Platrixa never "
                "invents the missing component.",
                "State both component amounts (or a rate).")
        if total_rate is not None:
            half = _q(base * total_rate / Decimal(200))
            if cgst_amt is not None and (cgst_amt != half
                                         or sgst_amt != half):
                return _refuse(
                    "The stated CGST/SGST amounts do not match the stated "
                    "rate. Platrixa never guesses which is correct.",
                    "State consistent amounts/rates.")
            cgst_amt = half
            sgst_amt = half
        elif cgst_amt is not None:
            pass  # both stated amounts are used as-is
        else:
            return _refuse(
                "CGST + SGST is mentioned without a rate or component "
                "amounts. Platrixa never guesses the tax.",
                "State the GST rate or the CGST and SGST amounts.")
        if cgst_amt <= 0 or sgst_amt <= 0:
            return _refuse(
                "A GST component amount is not positive. Platrixa never "
                "records it.",
                "Re-check the amounts and rate.")
        raw_components = [("CGST", cgst_amt), ("SGST", sgst_amt)]

    gst_total = sum(amt for _, amt in raw_components)
    total = base + gst_total

    # -- journal build ------------------------------------------------------
    prefix = "Output" if is_sale else "Input"
    components_out = [(f"{prefix} {comp}", amt)
                      for comp, amt in raw_components]

    # Sprint 15I-R: accounts resolved from a {'party': ...} spec are
    # Personal accounts regardless of name shape ('Ravi Kumar' is a
    # person, never a Nominal account). Presentation metadata only -
    # journal decisions are unchanged; the name-based fallback stays
    # strict for the 15G canonical authority.
    party_accounts: set = set()

    def _line(account: str, amount: Decimal, side: str) -> Dict[str, Any]:
        cls = CLASS_PERSONAL if account in party_accounts else traditional_class_for(account)
        return {
            "account": account,
            "class": cls,
            "rule": TRADITIONAL_GOLDEN_RULES[cls],
            "why": side_decision_for(account, side, cls),
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
                    "The credit sale does not name the customer. Platrixa "
                    "never invents a person's name.",
                    "Add the customer's name.")
            party_accounts.add(party)
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
                "resolved. Platrixa never invents an account.",
                "Re-type the transaction with the account explicit.")
        debit_lines.append(_line(debit_accounts[0], base, "debit"))
        for account, amount in components_out:
            debit_lines.append(_line(account, amount, "debit"))
        if is_credit:
            party = _resolve_bk_spec({"party": "giver"}, stripped, "giver")
            if party is None:
                return _refuse(
                    "The credit purchase does not name the supplier. Platrixa "
                    "never invents a person's name.",
                    "Add the supplier's name.")
            party_accounts.add(party)
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


# ---------------------------------------------------------------------------
# Sprint 15I-TX helpers - business/personal splits, return-chain
# continuations, contextual expenses and GST partial-payment guards.
# All deterministic; nothing here duplicates an accounting rule - every
# account, side and amount still comes from the single hardened engine.
# ---------------------------------------------------------------------------


def _personal_amount_in(low: str) -> Optional[Decimal]:
    """The stated personal-use figure inside a business/personal split
    clause, read before or after the personal-use phrase. None when it
    cannot be read deterministically (the caller then refuses)."""
    # The clause between the personal-use figure and the personal/private
    # marker may carry a title period ('used by Mr. Carlos Alcaraz for
    # personal use') - only semicolons/newlines are real clause breaks.
    m = re.search(
        r"\b(?:rs\.?|\u20b9|inr)?\s*([\d,]+(?:\.\d+)?)\s+(?:were|"
        r"was|have been|has been)\s+(?:used|utilised|taken)"
        r"[^;\n]{0,50}?\b(?:personal|private)\b"
        r"|\b(?:rs\.?|\u20b9|inr)?\s*([\d,]+(?:\.\d+)?)"
        r"\s+for\s+(?:personal|private)\s+(?:use|expenses|purpose)\b"
        r"|\b(?:for|used for)\s+(?:personal|private)\s+(?:use|"
        r"expenses|purpose)\b\s*(?:rs\.?|\u20b9|inr)?\s*"
        r"([\d,]+(?:\.\d+)?)",
        low)
    if not m:
        return None
    val = next((g for g in m.groups() if g), None)
    if val is None:
        return None
    try:
        return Decimal(val.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _business_personal_split(question: str) -> Optional[Dict[str, Any]]:
    """Deterministic business/personal-use split: a bank withdrawal
    with an explicitly stated personal-use portion, or a goods purchase
    where an explicitly stated goods value was taken for personal use.
    Every stated amount gets a role; None when the wording does not
    anchor both figures (the caller then refuses - never a guess)."""
    text = str(question or "").strip()
    if not text:
        return None
    low = " " + text.lower() + " "
    amounts, _ = _extract_amounts(question)
    if len(amounts) != 2:
        return None
    total, personal = amounts[0], amounts[1]
    if total <= 0 or personal <= 0 or personal >= total:
        return None
    if not re.search(
            r"\b(?:personal|private)\s+(?:use|expenses|purpose)\b",
            low):
        return None
    # --- bank withdrawal -------------------------------------------------
    if re.search(
            r"\b(?:withdrew|withdrawn|drew)\b.*?\bfrom\s+"
            r"(?:the\s+)?bank\b", low):
        m_w = re.search(
            r"\b(?:withdrew|withdrawn|drew)\b\s*(?:rs\.?|\u20b9|inr)?"
            r"\s*([\d,]+(?:\.\d+)?)", low)
        if m_w is None:
            return None
        try:
            w_amt = Decimal(m_w.group(1).replace(",", ""))
        except (InvalidOperation, ValueError):
            w_amt = None
        p_amt = _personal_amount_in(low)
        if w_amt == total and p_amt == personal:
            return {"kind": "bank_withdrawal", "total": total,
                    "personal": personal, "business": total - personal,
                    "mode": "bank", "party": None}
        return None
    # --- goods purchase with personal-use portion ------------------------
    if not any(k in low for k in (
            "purchased goods", "bought goods", "goods purchased",
            "goods bought", "goods worth", "purchased stock",
            "bought stock", "stock worth")):
        return None
    m_g = re.search(
        r"\b(?:purchased|bought)\b\s+(?:goods|stock)\b[^.;]{0,80}?"
        r"\b(?:rs\.?|\u20b9|inr)?\s*([\d,]+(?:\.\d+)?)", low)
    if m_g is None:
        m_g = re.search(
            r"\b(?:goods|stock)\s+worth\s*(?:rs\.?|\u20b9|inr)?"
            r"\s*([\d,]+(?:\.\d+)?)", low)
    if m_g is None:
        return None
    try:
        g_amt = Decimal(m_g.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        g_amt = None
    p_amt = _personal_amount_in(low)
    if g_amt != total or p_amt != personal:
        return None
    if re.search(r"\bfor cash\b", low) \
            and not re.search(r"\bon credit\b", low):
        mode = "cash"
    elif re.search(r"\bon credit\b", low) \
            or re.search(r"\bfrom\b", low):
        mode = "credit"
    else:
        return None
    party = _party_from_text(text) if mode == "credit" else None
    if mode == "credit" and party is None:
        return None
    return {"kind": "goods_purchase", "total": total,
            "personal": personal, "business": total - personal,
            "mode": mode, "party": party}


def _resolve_business_personal_split(
        split: Dict[str, Any],
        amounts: List[Decimal]) -> Dict[str, Any]:
    """The amount-resolver result for a business/personal split - both
    stated figures are consumed by deterministic roles, so the
    unresolved-amount gate never fires on them."""
    total = split["total"]
    personal = split["personal"]
    business = split["business"]
    steps: List[Dict[str, Any]] = [
        {"calculation_id": "BK_SPLIT_TOTAL",
         "label": "Total withdrawal / purchase",
         "formula": "Total from the question",
         "inputs": {"total": total}, "result": total},
        {"calculation_id": "BK_SPLIT_PERSONAL",
         "label": "Personal-use portion",
         "formula": "Personal-use amount from the question",
         "inputs": {"personal": personal}, "result": personal},
        {"calculation_id": "BK_SPLIT_BUSINESS",
         "label": "Business / office portion",
         "formula": "Business = Total - Personal-use",
         "inputs": {"total": total, "personal": personal},
         "result": business},
    ]
    return {
        "status": VERIFIED, "steps": steps,
        "list_price": total, "trade_discount_rate": None,
        "trade_discount_amount": None, "net_value": total,
        "paid_amount": None, "credit_amount": None,
        "cash_discount_rate": None, "cash_discount_amount": None,
        "cash_paid": None, "explicit_discount": None,
        "split": split, "concerns": [],
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
    }


def _build_personal_split_journal(
        text: str, pattern: Dict[str, Any],
        amounts: Dict[str, Any]) -> Dict[str, Any]:
    """The compound journal for a business/personal-use split: both the
    business portion and the personal-use (drawings) portion post in ONE
    balanced entry. Every amount comes from the question; the business
    remainder is the derived difference (traced, never silent)."""
    split = amounts["split"]
    total = split["total"]
    personal = split["personal"]
    business = split["business"]
    party_accounts: set = set()

    def _line(account: str, amount: Decimal, side: str) -> Dict[str, Any]:
        cls = CLASS_PERSONAL if account in party_accounts \
            else traditional_class_for(account)
        return {
            "account": account, "class": cls,
            "rule": TRADITIONAL_GOLDEN_RULES[cls],
            "why": side_decision_for(account, side, cls),
            "amount": amount, "side": side,
        }

    debit_lines: List[Dict[str, Any]] = []
    credit_lines: List[Dict[str, Any]] = []
    if split["kind"] == "bank_withdrawal":
        debit_lines.append(_line("Cash", total, "debit"))
        debit_lines.append(_line("Drawings", personal, "debit"))
        credit_lines.append(_line("Bank", total, "credit"))
        credit_lines.append(_line("Cash", personal, "credit"))
    else:
        debit_lines.append(_line("Purchases", business, "debit"))
        debit_lines.append(_line("Drawings", personal, "debit"))
        if split["mode"] == "cash":
            cash_acct = ("Bank" if _resolve_cash_bank(text) == "Bank"
                         else "Cash")
            credit_lines.append(_line(cash_acct, total, "credit"))
        else:
            party = split.get("party")
            party_accounts.add(party)
            credit_lines.append(_line(party, total, "credit"))

    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))
    narration_parts: List[str] = []
    for line in debit_lines:
        narration_parts.append(f"{line['account']} A/c Dr "
                               f"{_fmt_amt(line['amount'])}")
    for line in credit_lines:
        narration_parts.append(f"To {line['account']} A/c "
                               f"{_fmt_amt(line['amount'])}")
    return {
        "status": VERIFIED, "date": None,
        "particulars": " / ".join(l["account"] for l in debit_lines)
                       + " A/c Dr",
        "debit_lines": debit_lines, "credit_lines": credit_lines,
        "narration": "Being " + "; ".join(narration_parts) + ".",
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
        "calculation_records": amounts.get("steps") or [],
        "total_debit": total_debit, "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


def _expense_near_for(low: str) -> Optional[str]:
    """The REGISTERED expense word adjacent to 'for' in a 'paid ...
    for ...' / 'paid <expense> ... for ...' clause, or None. A
    possessive-pronoun bill ('paid his mobile bill') is a personal bill,
    never silently booked as a business expense (Sprint 15I-TX)."""
    # Sprint 15I-TX: the amount between 'paid' and 'for' carries a
    # currency period ('paid rs.500 for ...') - a '[^.;]' clause class
    # would stop at the 'rs.' dot and miss the clause entirely. Only
    # semicolons/newlines are real clause breaks here.
    # The tail after 'for' is GREEDY so the registered expense word
    # ('paid rs.500 for MOBILE recharge') is inside the scanned clause -
    # a non-greedy tail would stop at 'for' and miss the expense word.
    m = re.search(r"\bpaid\b[^;\n]{0,80}?\bfor\b[^;\n]{0,80}\b", low)
    if m is None:
        return None
    clause = m.group(0)
    for phrase, account in _EXPENSE_ACCOUNT_WORDS:
        for mm in re.finditer(
                r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", clause):
            before = clause[max(0, mm.start() - 10):mm.start()]
            if re.search(r"\b(?:his|her|their|its|my|our)\b\s*$", before):
                continue
            return account
    return None


_GST_PARTIAL_PAYMENT_RE = re.compile(
    r"\b(?:issued|gave|issuing|giving)\s+(?:a|the|him|her|them)"
    r"(?:\s+(?:bearer|crossed|blank|post[- ]?dated))?\s+cheque\b"
    r"|\b(?:paid|paying)\s+(?:him|her|them)\b"
    r"|\b(?:half|quarter|one[- ]?third|two[- ]?third|1/3|1/4|50%)"
    r"\s+of\s+the\s+amount\b"
    r"|\bfor\s+(?:50%|half|quarter|one[- ]?third|1/3rd)\b"
    r"|\bin\s+his\s+favour\s+for\b"
    r"|\b(?:paid|received)\s+(?:half|50%)\b"
    r"|\bpartly\s+paid\b",
    re.IGNORECASE)


def _gst_partial_payment(text: str) -> bool:
    """True when a GST transaction carries a PARTIAL payment step (a
    cheque issued for a fraction, a stated fraction of the amount, or a
    payment to the party). The verified GST surface posts only the FULL
    consideration; a partial payment would silently change the party /
    bank split, so the caller refuses instead of dropping the step."""
    low = " " + str(text or "").lower() + " "
    return bool(_GST_PARTIAL_PAYMENT_RE.search(low))


def _return_chain_continuation(
        segment: str,
        prior: Optional[Dict[str, Any]]
) -> Optional[Tuple[Optional[str], Optional[Dict[str, Any]]]]:
    """Sprint 15I-TX: 'the same were returned to <party>' continues the
    previous goods entry (a purchase or a sales return): the returned
    goods' VALUE is inherited from that entry (never invented) and the
    continuation journals as a PURCHASE_RETURN. Returns (rewritten_segment,
    None) to journal the continuation; (None, refusal) when the goods
    identity is unclear; None when the segment is not a continuation."""
    if not re.match(r"^\s*(?:and\s+)?the\s+same\b", segment,
                    re.IGNORECASE):
        return None
    prior_accounts = {
        l.get("account") for l in
        ((prior or {}).get("debit_lines") or [])
        + ((prior or {}).get("credit_lines") or [])}
    prior_amount = None
    if prior is not None:
        for l in ((prior or {}).get("debit_lines") or []) \
                + ((prior or {}).get("credit_lines") or []):
            if l.get("amount") is not None:
                prior_amount = l.get("amount")
                break
    if prior is None or not (
            "Purchases" in prior_accounts
            or "Sales Returns" in prior_accounts):
        return (None, {
            "status": REVIEW_REQUIRED,
            "why_not": ("'The same were returned' refers to goods from the "
                        "previous transaction, but the previous transaction "
                        "is not a goods purchase or a goods return here. "
                        "Platrixa never guesses which goods are being returned."),
            "next_action": ("State the return fully, e.g. 'Sold goods to "
                            "Mohan; Mohan returned goods worth Rs.6,500; "
                            "the same were returned to Rahul.'"),
            "debit_lines": [], "credit_lines": [],
            "narration": None, "calculation_records": [],
            "total_debit": 0, "total_credit": 0, "balanced": True,
        })
    party = _party_from_text(segment)
    if not party or prior_amount is None:
        return (None, {
            "status": REVIEW_REQUIRED,
            "why_not": ("The returned-goods continuation does not name the "
                        "party receiving the return, or the previous goods "
                        "entry has no value to carry over. Platrixa never "
                        "invents either."),
            "next_action": "Name the party and enter the return amount.",
            "debit_lines": [], "credit_lines": [],
            "narration": None, "calculation_records": [],
            "total_debit": 0, "total_credit": 0, "balanced": True,
        })
    return (f"Returned goods worth Rs.{_fmt_amt(prior_amount)} "
            f"to {party}.", None)


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

    # Sprint 15I-TX: a standalone 'the same were returned to <party>'
    # continuation has no identified goods (the 'same' refers to an
    # earlier transaction) - REVIEW_REQUIRED, never a confident return
    # journal built on an unidentified goods value.
    if re.match(r"^\s*(?:and\s+)?the\s+same\b", text, re.IGNORECASE):
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("'The same were returned' refers to goods from an "
                        "earlier transaction, but this question does not "
                        "identify which goods. Platrixa never guesses."),
            "next_action": ("State the return fully, e.g. 'Sold goods to "
                            "Mohan; Mohan returned goods worth Rs.6,500; "
                            "the same were returned to Rahul.'"),
            "debit_lines": [], "credit_lines": [], "narration": None,
            "calculation_records": [], "total_debit": 0,
            "total_credit": 0, "balanced": True,
        }

    # Sprint 15I-UZ (D2): the T.D./C.D. abbreviation protection keeps an
    # order question with discount/GST wording ONE segment, so the order
    # refusal must fire BEFORE the GST path - placing an order is not a
    # transaction even when it quotes rates (the GST path would otherwise
    # produce a less accurate refusal).
    if re.search(r"\bplaced\s+(?:an\s+)?order\b",
                 " " + text.lower() + " "):
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("Placing an order is not a transaction: no journal "
                        "entry is recorded until the goods are actually "
                        "received or supplied. Platrixa does not journal an "
                        "order."),
            "next_action": ("Record the journal when the goods are actually "
                            "received or supplied."),
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
                            "party or a payment amount. Platrixa never invents "
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
    # ('received Rs.5,000 in full settlement of Rs.5,200') - Platrixa will
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
                            "but no discount amount is stated. Platrixa never "
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
                                "was for cash or on credit. Platrixa never "
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
                        "FYJC Book-Keeping syllabus boundary. Platrixa does not "
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

    # Sprint 15I-TX: business/personal-use split journal (a compound
    # entry built from the split resolution - every stated amount is
    # consumed by a deterministic role).
    if amounts.get("split") is not None:
        return _build_personal_split_journal(text, pattern, amounts)

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

    # Sprint 15I-R: accounts resolved from a {'party': ...} spec are
    # Personal accounts regardless of name shape ('Ravi Kumar' is a
    # person, never a Nominal account). Presentation metadata only -
    # journal decisions are unchanged; the name-based fallback stays
    # strict for the 15G canonical authority.
    party_accounts: set = set()

    def _line(account: str, amount: Decimal, side: str) -> Dict[str, Any]:
        cls = CLASS_PERSONAL if account in party_accounts else traditional_class_for(account)
        return {
            "account": account,
            "class": cls,
            "rule": TRADITIONAL_GOLDEN_RULES[cls],
            "why": side_decision_for(account, side, cls),
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
        # settlement) - Platrixa never picks one silently.
        return {
            "status": REVIEW_REQUIRED,
            "why_not": ("The sale carries an explicit discount amount but "
                        "does not say whether the discount is deducted at "
                        "sale time or allowed at settlement. Platrixa never "
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
                party_accounts.add(party)
                credit_lines.append(_line(party, explicit["party_total"],
                                          "credit"))
        else:
            party = (_resolve_bk_spec({"party": "receiver"}, text,
                                     "receiver")
                     or _resolve_bk_spec({"party": "giver"}, text, "giver"))
            if party:
                party_accounts.add(party)
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
                    party_accounts.add(party)
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
                if account in _party_accounts_for(debit_specs, text, "receiver"):
                    party_accounts.add(account)
                debit_lines.append(_line(account, net, "debit"))

        # --- credit side -------------------------------------------------
        if sale:
            for account in _resolve_side_specs(credit_specs, text, "giver"):
                if account in _party_accounts_for(credit_specs, text, "giver"):
                    party_accounts.add(account)
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
                party_accounts.add(party)
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
                    party_accounts.add(party)
                    credit_lines.append(_line(party, credit_portion,
                                              "credit"))
                else:
                    credit_lines.append(_line("Creditors", credit_portion,
                                              "credit"))
        else:
            # Sprint 15I-UZ (D5): a purchase whose FULL value is settled
            # by an explicit payment step ('and paid the full amount by
            # cheque', '... and paid by cheque', 'for cash') credits the
            # cash/bank account - the creditor is fully paid and must not
            # remain credited.
            # Sprint 15I-UZ (D5): ONLY a goods purchase (PURCHASE_GOODS_*)
            # settles against the cash/bank account when fully paid - the
            # 'purchase' flag also covers START_BUSINESS / CAPITAL_INTRODUCED,
            # which must always credit Capital, never Cash.
            _full_payment_credit = (
                "PURCHASE" in pattern["key"] and paid is not None
                and net is not None and paid == net
                and re.search(
                    r"\b(?:cheque|check|paid|for cash|cash)\b",
                    " " + text.lower() + " ")
                and "on credit" not in (" " + text.lower() + " "))
            if _full_payment_credit:
                cash_acct = "Bank" if cash_or_bank == "Bank" else "Cash"
                credit_lines.append(_line(cash_acct, net, "credit"))
            else:
                for account in _resolve_side_specs(
                        credit_specs, text, "giver"):
                    if account in _party_accounts_for(
                            credit_specs, text, "giver"):
                        party_accounts.add(account)
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
                        "Platrixa never invents a person's name."),
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
    """What Platrixa understood for a Book-Keeping question:
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
        f"Platrixa reads this as **{pattern['label']}** (Book-Keeping & "
        f"Accountancy). Accounts identified: "
        f"{', '.join(debit_accounts + credit_accounts) or 'none yet'}. "
        f"Requested operation: {requested}."
        if pattern else
        "Platrixa could not reliably identify the transaction type. It will "
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
# verb on its own, so it must be folded into the previous journal - Platrixa
# never posts it as an independent entry.
_PAYMENT_STEP_HINTS = (
    "paid", "received", "immediately", "half", "quarter", "%",
    "discount", "settlement", "cheque", "check", "cash paid",
    "payment", "was paid", "was made",
)
_STRONG_TRANSACTION_VERBS = (
    "purchased", "bought", "sold", "started business",
    "commenced business", "started the business", "withdrew",
    "deposited", "returned", "loan", "rent ", "salary", "salaries",
    "wages", "insurance", "commission", "interest", "drawings",
    "capital", "expense", "advertisement", "electricity",
)


def _extract_party_from_segment(segment: str) -> Optional[str]:
    """Extract the named party from a transaction segment.

    Checks 'from X', 'to X', 'X paid', 'paid X' patterns.  Returns the
    normalised party name or *None* when no named party is found.  Used
    by the splitter merge guard to prevent cross-party merging.
    """
    if not segment:
        return None
    low = segment.lower()
    # 'from <Party>' / 'to <Party>'
    m = re.search(r"\b(?:from|to)\s+([A-Z][a-z]+)", segment)
    if m:
        return _normalise_party_token(m.group(1)) or m.group(1)
    # '<Party> paid' / 'paid <Party>'
    m = re.search(r"\b([A-Z][a-z]+)\s+paid\b", segment)
    if m:
        return _normalise_party_token(m.group(1)) or m.group(1)
    m = re.search(r"\bpaid\s+([A-Z][a-z]+)\b", segment)
    if m:
        return _normalise_party_token(m.group(1)) or m.group(1)
    return None


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
        # Rs.5,000.') contradicts the debtor relationship - Platrixa never
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
                            "the debtor relationship. Platrixa never guesses "
                            "the payment direction."),
                "next_action": ("Re-type the settlement with an explicit "
                                "direction, e.g. 'Received Rs.X from "
                                f"{prior_party}.' if the party settled."),
                "debit_lines": [], "credit_lines": [],
                "narration": None, "calculation_records": [],
                "total_debit": 0, "total_credit": 0, "balanced": True,
            }
        else:
            # Sprint 15I-TX: a 'the same were returned to <party>'
            # continuation ('X returned us goods ... and the same were
            # returned to Y') inherits the returned-goods VALUE from the
            # previous goods entry (a purchase or a sales return) - never
            # invented. Standalone or after any other transaction the
            # goods identity is unclear -> REVIEW_REQUIRED (Test 9-style
            # return chains).
            _cont = _return_chain_continuation(
                segment, journals[-1] if journals else None)
            if _cont is not None and _cont[0] is not None:
                segment = _cont[0]
                resolved_segments[-1] = segment
                journal = generate_journal(segment)
            elif _cont is not None:
                journal = _cont[1]
            else:
                # Sprint 15I-J: a bank continuation step ('Deposited
                # further cash Rs.5,000' after 'Opened an account with
                # Bank of India Rs.20,000') with no identity of its own
                # inherits ONLY the prior journal's bank context and its
                # explicit direction verb - never an invented mode or
                # amount.
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
            elif merged["status"] == VERIFIED:
                # the merge would silently change the prior transaction
                # (mode/family flip or a dropped stated amount) - never
                # reinterpret or repair the previous journal (Sprint
                # 15I-F P0-B). REVIEW_REQUIRED with the calm refusal.
                journal = {
                    "status": REVIEW_REQUIRED,
                    "why_not": ("Platrixa will not silently re-interpret the "
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
            else:
                # Sprint 15I-S: the merged resolution itself refuses
                # (unresolved multi-amount / ambiguous role) - surface
                # that refusal instead of the lone segment's status so
                # the whole question refuses with the merged verdict.
                journal = merged
        if journal["status"] != VERIFIED:
            status = journal["status"]
            refusal = _refusal(
                status,
                f"Transaction {i + 1} of {len(segments)}: "
                + (journal.get("why_not") or "Platrixa could not reason about "
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
                f"'{hint}' is outside the FYJC Book-Keeping topics Platrixa "
                "currently supports (journal entries, ledger posting, "
                "trial balance, debit/credit reasoning for standard "
                "transactions). Platrixa does not guess a treatment.",
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
            status, journal.get("why_not") or "Platrixa could not reason about "
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
