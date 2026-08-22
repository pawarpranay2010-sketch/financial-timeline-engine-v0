"""
Platrixa
Sprint 15I-BILLS - Bills of Exchange Authority
backend/maths/fyjc_bills.py

A dedicated deterministic authority for the FYJC (Grade 11) Bills of
Exchange lifecycle. It does NOT rewrite the hardened accounting
authority (backend.maths.fyjc_bk_reasoning) - it owns ONLY the bills
surface and composes its journals with the hardened engine's own line
format (account, traditional class, golden rule, per-line WHY) so the
canonical result is byte-compatible with the Study / Verify flow.

Pipeline (inside the 15I-WF orchestrator):

    normalized input -> segment -> route -> Bills Authority ->
    bill lifecycle state machine -> deterministic journals ->
    verify accounting consistency -> canonical result
    (VERIFIED / REVIEW_REQUIRED / NOT_SUPPORTED / INVALID_INPUT_MATH)

Supported lifecycle states (Sprint 15I-BILLS section 2):

    DRAWN -> ACCEPTED -> HELD
                        -> DISCOUNTED
                        -> ENDORSED (terminal for the drawer)
                        -> SENT_FOR_COLLECTION -> HONOURED / DISHONOURED
    HELD -> HONOURED | DISHONOURED
    DISCOUNTED -> HONOURED (bank collects; no drawer entry) | DISHONOURED

Only valid transitions are followed. Invalid transitions, an ambiguous
party-role assignment, a missing prior bill state, an unestablished
amount, or a mathematical contradiction all refuse with zero journal
lines - the authority never invents a bill, a party, an amount, a
maturity period or a previous state.

Supported journals (drawer's / holder's books unless stated):

  * drawing + acceptance     Bills Receivable Dr / Drawee A/c Cr
                             (drawee's books, 'X accepted Y's bill':
                              Y's A/c Dr / Bills Payable A/c Cr)
  * discounting with bank    Bank A/c Dr (proceeds) / Discount A/c Dr /
                             Bills Receivable A/c Cr (full amount)
  * endorsement              Endorsee's A/c Dr / Bills Receivable A/c Cr
  * sent for collection      Bills Sent for Collection A/c Dr /
                             Bills Receivable A/c Cr
  * collection               Bank A/c Dr / Bills Sent for Collection A/c Cr
  * honour at maturity       Cash / Bank A/c Dr / Bills Receivable A/c Cr
  * dishonour (held)         Drawee A/c Dr (bill + noting) /
                             Bills Receivable A/c Cr (bill) /
                             Cash / Bank A/c Cr (noting)
  * dishonour (discounted)   Drawee A/c Dr (bill + noting) /
                             Bank A/c Cr (bill + noting)

Bank discount (Sprint 15I-BILLS section 6): Bill x Rate x Time with
months / 12 and days / 365, plus the FYJC three days of grace when a
maturity date is computed. A maturity period is never assumed - when
discounting is stated, the discount must be computable (rate + period),
explicitly stated (proceeds / discount amount), or the question refuses.

The authority runs the SAME 15I-VY normalization + global mathematical
contradiction gates FIRST, so no 15I-VY refusal is weakened and single-
letter party tokens ('A draws a bill on B') refuse safely.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Topic + lifecycle state vocabulary
# ---------------------------------------------------------------------------

TOPIC_BILLS = "bills_of_exchange"

STATE_DRAWN = "DRAWN"
STATE_ACCEPTED = "ACCEPTED"
STATE_HELD = "HELD"
STATE_DISCOUNTED = "DISCOUNTED"
STATE_ENDORSED = "ENDORSED"
STATE_SENT_COLLECTION = "SENT_FOR_COLLECTION"
STATE_HONOURED = "HONOURED"
STATE_DISHONOURED = "DISHONOURED"

_VALID_TRANSITIONS: Dict[str, set] = {
    STATE_DRAWN: {STATE_ACCEPTED},
    STATE_ACCEPTED: {STATE_HELD, STATE_DISCOUNTED, STATE_ENDORSED,
                     STATE_SENT_COLLECTION},
    STATE_HELD: {STATE_HONOURED, STATE_DISHONOURED},
    STATE_DISCOUNTED: {STATE_HONOURED, STATE_DISHONOURED},
    STATE_ENDORSED: set(),
    STATE_SENT_COLLECTION: {STATE_HONOURED, STATE_DISHONOURED},
    STATE_HONOURED: set(),
    STATE_DISHONOURED: set(),
}

# ---------------------------------------------------------------------------
# Detection vocabulary (routing from the orchestrator)
# ---------------------------------------------------------------------------

# Bills-of-exchange wording (NOT the everyday 'electricity bill' / 'mobile
# recharge bill' expense context, which keeps its existing handling).
_BILLS_CORE_RE = re.compile(
    r"\bbills?\s+of\s+exchange\b|\bbills?\s+receivable\b|\bbills?\s+payable\b",
    re.IGNORECASE)

_EVERYDAY_BILL_RE = re.compile(
    r"\b(?:electricity|mobile|telephone|phone|water|recharge|gas|medical|"
    r"cell|rent|school|hotel|restaurant|postal)\s+(?:bill|bills)\b"
    r"|\b(?:bill|bills)\s+(?:for\s+)?(?:electricity|mobile|telephone|phone|"
    r"water|recharge|gas|medical|cell|rent|school|hotel|restaurant|postal)\b",
    re.IGNORECASE)

_BILL_LIFECYCLE_RE = re.compile(
    r"\bbill\b[^;\n]{0,60}?\b(?:drawer|drawee|acceptor|accepted|accepts|"
    r"discounted|discounting|endorsed|endorsing|for\s+collection|"
    r"honou?red|dishonou?red|noting\s+charges?|maturity|due\s+date|grace|"
    r"retained|drawn|negotiated)\b"
    r"|\b(?:drew|draws|drawing|drawn)\b[^;\n]{0,50}?\bbill\b"
    r"|\b(?:discounted|endorsed|accepted|accepts)\b[^;\n]{0,40}?\bbill\b",
    re.IGNORECASE)


def detect_bills(text: str) -> Optional[Dict[str, Any]]:
    """Return the bills topic when the question is a bills-of-exchange
    question, else None. An everyday bill (electricity / mobile / rent)
    is never routed here. Routing decision only - the resolver performs
    the deterministic resolution."""
    raw = str(text or "")
    low = " " + raw.lower() + " "
    if _EVERYDAY_BILL_RE.search(low):
        return None
    if _BILLS_CORE_RE.search(low) or _BILL_LIFECYCLE_RE.search(low):
        return {"topics": [TOPIC_BILLS], "text": raw}
    return None


# ---------------------------------------------------------------------------
# Fact vocabulary
# ---------------------------------------------------------------------------

_DRAW_ACTIVE_RE = re.compile(
    r"(?P<drawer>[A-Z][A-Za-z' .]{1,40}?)\s+(?:drew|draws|drawing|drawn)\s+"
    r"(?:a|the)?\s*bills?\s*(?:of\s+exchange)?[^;\n]{0,40}?\s+on\s+"
    r"(?P<drawee>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|amounting\s+to|"
    r"of|which|and|at|on|$))", re.IGNORECASE)

_DRAW_PASSIVE_RE = re.compile(
    r"(?:drawn|draw)\s+by\s+(?P<drawer>[A-Z][A-Za-z' .]{1,40}?)\s+on\s+"
    r"(?P<drawee>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|amounting\s+to|"
    r"of|which|and|at|on|$))", re.IGNORECASE)

_ACCEPT_BY_RE = re.compile(
    r"(?:accepted|accepts|accepting)\s+by\s+"
    r"(?P<acceptor>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|the|which|"
    r"and|at|on|$))", re.IGNORECASE)

_ACCEPT_SUBJECT_RE = re.compile(
    r"(?P<acceptor>[A-Z][A-Za-z' .]{1,40}?)\s+(?:accepted|accepts)\s+"
    r"(?:a|the)?\s*(?:bill|draft)\b", re.IGNORECASE)

# 'which Mohan accepted' / 'that Mohan accepted' (the bill's drawee
# accepting it) - acceptor is the party after which/that.
_ACCEPT_TAIL_RE = re.compile(
    r"(?:which|that)\s+(?P<acceptor>[A-Z][A-Za-z' .]{1,40}?)\s+"
    r"(?:accepted|accepts)\b", re.IGNORECASE)

# 'X accepted Y's bill' -> the drawee's books (Bills Payable side).
_ACCEPT_POSSESSIVE_RE = re.compile(
    r"(?P<acceptor>[A-Z][A-Za-z' .]{1,40}?)\s+(?:accepted|accepts)\s+"
    r"(?P<drawer>[A-Z][A-Za-z' .]{1,40}?)(?:'s|\u2019s)\s+bills?\b",
    re.IGNORECASE)

# 'Y's bill was accepted by X' -> same roles, passive form.
_ACCEPT_POSSESSIVE_PASSIVE_RE = re.compile(
    r"(?P<drawer>[A-Z][A-Za-z' .]{1,40}?)(?:'s|\u2019s)\s+bills?\s+"
    r"(?:was|has\s+been|is)?\s*(?:accepted|accepts)\s+by\s+"
    r"(?P<acceptor>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|which|and|"
    r"at|on|$))", re.IGNORECASE)

_RECEIVED_RE = re.compile(
    r"\breceived\s+(?:a|the)?\s*bills?\s*(?:of\s+exchange)?\b",
    re.IGNORECASE)

# party after 'from' inside the received-bill clause
_FROM_PARTY_RE = re.compile(
    r"\bfrom\s+(?P<party>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|"
    r"amounting\s+to|which|and|at|on|$))", re.IGNORECASE)

_ENDORSE_RE = re.compile(
    r"endorsed\s+(?:the|a)?\s*(?:bill|it|same)?\s*(?:in\s+(?:favour|favor)\s+"
    r"of|to|in\s+the\s+name\s+of)\s+(?:(?:our|the|his|her)\s+"
    r"(?:creditor|debtor)\s+)?"
    r"(?P<endorsee>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:for|which|and|"
    r"at|on|$))", re.IGNORECASE)

_DISCOUNT_ACTION_RE = re.compile(
    r"\bdiscount(?:ed|ing)?\b", re.IGNORECASE)

_SENT_COLLECTION_RE = re.compile(
    r"\bsent\s+(?:the|a)?\s*(?:bill|it|same)\s+(?:to\s+(?:the\s+)?bank\s+)?"
    r"for\s+collection\b"
    r"|\b(?:sent|given|handed)\s+to\s+(?:the\s+)?bank\s+for\s+collection\b",
    re.IGNORECASE)

_COLLECTED_RE = re.compile(
    r"\bcollected\s+(?:the|a)?\s*(?:bill|it|amount|same)\b"
    r"|\b(?:the\s+)?bill\b[^.;]{0,40}?\b(?:was|has\s+been|is)?\s*collected\b",
    re.IGNORECASE)

_HONOURED_RE = re.compile(
    r"\b(?:honou?red|honou?rs|honou?ring)\b", re.IGNORECASE)

_DISHONOURED_RE = re.compile(
    r"\b(?:dishonou?red|dishonou?rs?|dishonou?ring|bounced|"
    r"returned\s+unpaid)\b", re.IGNORECASE)

_REPEATED_DISHONOUR_RE = re.compile(
    r"\b(?:dishonou?red|bounced)\b[^.;]{0,50}?\b(?:again|twice|a\s+second\s+"
    r"time|second\s+time)\b", re.IGNORECASE)

_RETAINED_RE = re.compile(
    r"\b(?:retained|kept|held)\s+(?:the\s+)?(?:bill|it|same)\b"
    r"|\bbill\b[^.;]{0,30}?\b(?:retained|kept|held)\b"
    r"|\bretained\s+till\s+maturity\b", re.IGNORECASE)

_CASH_MODE_RE = re.compile(r"\b(?:in\s+cash|by\s+cash|for\s+cash)\b",
                           re.IGNORECASE)
_BANK_MODE_RE = re.compile(
    r"\b(?:by\s+(?:the\s+)?(?:cheque|bank)|through\s+(?:the\s+)?bank|"
    r"into\s+(?:the\s+)?bank)\b", re.IGNORECASE)

_NOTING_RE = re.compile(r"\bnoting\s+charges?\b", re.IGNORECASE)
_NOTING_BY_BANK_RE = re.compile(
    r"\bnoting\s+charges?\b[^.;]{0,40}?\b(?:paid|borne|met|discharged)\s+"
    r"by\s+(?:the\s+)?bank\b"
    r"|\bbank\b[^.;]{0,30}?\b(?:paid|pays)\b[^.;]{0,30}?\bnoting\s+charges?\b",
    re.IGNORECASE)

_DISCOUNT_RATE_RE = re.compile(r"\bat\s+([0-9]+(?:\.[0-9]+)?)\s*%",
                               re.IGNORECASE)

_PROCEDS_RE = re.compile(
    r"\bdiscount(?:ed|ing)?\b[^;\n]{0,60}?\bfor\s+(?:rs\.?|\u20b9|inr)\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)

_DISCOUNT_AMOUNT_RE = re.compile(
    r"\bdiscount\b[^;\n]{0,30}?\b(?:of|amounting\s+to|is|:)\s+"
    r"(?:rs\.?|\u20b9|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)

_PERIOD_RE = re.compile(
    r"\bfor\s+([0-9]+(?:\.[0-9]+)?)\s+(months?|days?|years?|weeks?)\b"
    r"|\b([0-9]+(?:\.[0-9]+)?)\s*(?:months?|days?|years?|weeks?)"
    r"(?:'|\u2019)?s?\s*(?:bill|period)\b", re.IGNORECASE)

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_TOKEN_RE = re.compile(
    r"\b([0-9]{1,2}(?:st|nd|rd|th)?)[\s/-]([A-Za-z]{3,9})[\s/,]"
    r"([0-9]{4})\b"
    r"|\b([A-Za-z]{3,9})\s+([0-9]{1,2}(?:st|nd|rd|th)?),?\s+([0-9]{4})\b"
    r"|\b([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})\b", re.IGNORECASE)

_DRAW_VERB_SIGNAL_RE = re.compile(r"\b(?:drew|draws|drawing|drawn)\b",
                                   re.IGNORECASE)

# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _fmt_amt(value: Any) -> str:
    from backend.maths.fyjc_bk_reasoning import _fmt_amt as fa
    return fa(value)


def _party_token(text: str) -> Optional[str]:
    from backend.maths.fyjc_bk_reasoning import _normalise_party_token
    party = str(text or "").strip().rstrip(".;, ")
    if not party:
        return None
    return _normalise_party_token(party)


def _money_amounts(text: str) -> List[Decimal]:
    """Every MONEY amount stated in the text. Period numbers ('for 3
    months'), percentages ('12%'), dates and years are excluded so they
    can never be mistaken for a bill / discount / noting amount."""
    out: List[Decimal] = []
    low = " " + str(text or "").lower() + " "
    for m in re.finditer(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\b", low):
        before = low[max(0, m.start() - 10):m.start()]
        after = low[m.end():m.end() + 16]
        if re.search(r"(?:rs\.?|\u20b9|inr)\s*$", before):
            value = _dec(m.group(1))
            if value is not None and value not in out:
                out.append(value)
            continue
        if re.match(r"\s*(?:months?|days?|years?|weeks?|%)(?![A-Za-z0-9])",
                    after):
            continue
        if re.match(r"\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                    r"[a-z]*\b", after):
            continue
        # a bare day/year that belongs to a stated date
        digits = m.group(1).replace(",", "")
        if len(digits) == 4 and re.search(
                r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                r"[a-z]*\b", low[max(0, m.start() - 40):m.end() + 40]):
            continue
        value = _dec(m.group(1))
        if value is not None and value not in out:
            out.append(value)
    return out


def _amount_near(low: str, words: str, window: int = 20,
                 mode: str = "both") -> Optional[Decimal]:
    from backend.maths.fyjc_normalization import _amount_near as near
    return near(low, words, window=window, mode=mode)


def _refusal(status: str, why_not: str, next_action: str,
             bills: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import _refusal as engine_refusal
    result = engine_refusal(status, why_not, next_action)
    if bills:
        result["bills"] = bills
    return result


def _line(account: str, amount: Decimal, side: str,
          why: Optional[str] = None) -> Dict[str, Any]:
    """One journal line in the hardened engine's exact format."""
    from backend.maths.fyjc_bk_reasoning import (
        CLASS_PERSONAL,
        TRADITIONAL_GOLDEN_RULES,
        side_decision_for,
        traditional_class_for,
    )
    cls = traditional_class_for(account)
    return {
        "account": account,
        "class": CLASS_PERSONAL if cls is None else cls,
        "rule": TRADITIONAL_GOLDEN_RULES[cls],
        "why": why or side_decision_for(account, side, cls),
        "amount": amount,
        "side": side,
    }


def _journal(debit_lines: List[Dict[str, Any]],
             credit_lines: List[Dict[str, Any]],
             narration: str,
             calculation_records: Optional[List[Dict[str, Any]]] = None
             ) -> Dict[str, Any]:
    from backend.maths.status import VERIFIED
    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))
    particulars = " / ".join(
        l["account"] for l in debit_lines) + " A/c Dr"
    return {
        "status": VERIFIED,
        "date": None,
        "particulars": particulars if debit_lines else "-",
        "debit_lines": debit_lines,
        "credit_lines": credit_lines,
        "narration": narration,
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
        "calculation_records": calculation_records or [],
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


def _compose(journals: List[Dict[str, Any]],
             bills: Dict[str, Any],
             next_action: str) -> Dict[str, Any]:
    """Shape the resolved journals into the hardened-engine envelope."""
    from backend.maths.fyjc_bk_reasoning import (
        STATUS_WORDS,
        _fmt_amt,
        generate_ledger,
        generate_trial_balance,
        verify_arithmetic,
    )
    from backend.maths.status import VERIFIED

    debit_lines = [l for j in journals for l in (j.get("debit_lines") or [])]
    credit_lines = [l for j in journals for l in (j.get("credit_lines") or [])]
    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))

    narration_parts: List[str] = []
    for j_idx, journal in enumerate(journals, start=1):
        narration_parts.append(f"Entry {j_idx}:")
        for line in journal.get("debit_lines") or []:
            narration_parts.append(
                f"{line['account']} A/c Dr {_fmt_amt(line['amount'])}")
        for line in journal.get("credit_lines") or []:
            narration_parts.append(
                f"To {line['account']} A/c {_fmt_amt(line['amount'])}")
    narration = "Being " + "; ".join(narration_parts) + "."

    if journals:
        ledger = generate_ledger(journals)
        trial_balance = generate_trial_balance(journals)
        verification = verify_arithmetic([
            {"side": line["side"], "amount": line["amount"]}
            for line in debit_lines + credit_lines
        ])
    else:
        ledger = None
        trial_balance = None
        verification = None

    return {
        "status": VERIFIED,
        "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
        "resolved": True,
        "understanding": None,
        "journal": {
            "status": VERIFIED,
            "multi": len(journals) > 1,
            "count": len(journals),
            "particulars": " / ".join(
                l["account"] for l in debit_lines) + " A/c Dr"
            if debit_lines else "-",
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
        "calculation_records": [
            step for j in journals for step in (j.get("calculation_records")
                                                or [])
        ],
        "why_not": None,
        "next_action": next_action,
        "bills": bills,
        "audit": {
            "authority": "bills-authority",
            "rule_key": None,
            "calculation_ids": [],
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "topic": bills.get("topic"),
            "case": bills.get("case"),
        },
    }


# ---------------------------------------------------------------------------
# Maturity mathematics (Sprint 15I-BILLS section 6)
# ---------------------------------------------------------------------------


def _parse_date_token(token: str) -> Optional[date]:
    """Parse one date token into a date, or None."""
    token = str(token or "").strip()
    m = re.fullmatch(r"([0-9]{1,2})(?:st|nd|rd|th)?\s*"
                     r"([A-Za-z]{3,9})\s*([0-9]{4})", token)
    if m:
        month = _MONTH_NAMES.get(m.group(2).lower()[:3])
        if month is None:
            return None
        try:
            return date(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            return None
    m = re.fullmatch(r"([A-Za-z]{3,9})\s*([0-9]{1,2})(?:st|nd|rd|th)?\s*,?\s*"
                     r"([0-9]{4})", token)
    if m:
        month = _MONTH_NAMES.get(m.group(1).lower()[:3])
        if month is None:
            return None
        try:
            return date(int(m.group(3)), month, int(m.group(2)))
        except ValueError:
            return None
    m = re.fullmatch(r"([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})", token)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, [31, 29 if year % 4 == 0 and (
        year % 100 != 0 or year % 400 == 0) else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _period_of(text: str) -> Optional[Tuple[Decimal, str]]:
    """(number, unit) of the stated bill period, or None."""
    low = " " + str(text or "").lower() + " "
    for m in _PERIOD_RE.finditer(low):
        num = _dec(m.group(1) or m.group(2))
        unit = (m.group(2) or m.group(3) or "").lower()
        if num is not None:
            return (num, unit)
    return None


def _dates_in(text: str) -> List[Tuple[int, date]]:
    out: List[Tuple[int, date]] = []
    low = " " + str(text or "").lower() + " "
    for m in _DATE_TOKEN_RE.finditer(low):
        token = "".join(g for g in m.groups() if g)
        d = _parse_date_token(token)
        if d is not None:
            out.append((m.start(), d))
    return out


def _compute_discount(bill: Decimal, rate: Decimal,
                      period_num: Decimal, period_unit: str) -> Decimal:
    """Bank discount = Bill x Rate x Time (months / 12, days / 365,
    years / 1). FYJC textbook convention, encoded explicitly."""
    if period_unit.startswith("month"):
        fraction = period_num / Decimal(12)
    elif period_unit.startswith("day"):
        fraction = period_num / Decimal(365)
    else:
        fraction = period_num
    return (bill * rate / Decimal(100) * fraction).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def _due_date(draw_date: date, period_num: Decimal,
              period_unit: str, days_of_grace: int = 3) -> date:
    if period_unit.startswith("month"):
        base = _add_months(draw_date, int(period_num))
    elif period_unit.startswith("day"):
        base = draw_date + timedelta(days=int(period_num))
    else:
        base = date(draw_date.year + int(period_num),
                    draw_date.month, draw_date.day)
    return base + timedelta(days=days_of_grace)


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------


def _roles(text: str) -> Dict[str, Any]:
    """Deterministic party-role extraction. Roles are read from explicit
    wording ('drew a bill on Y', 'accepted by Y', 'received ... from Y',
    'endorsed ... to Y') - never inferred from sentence position alone."""
    low = " " + str(text or "").lower() + " "
    roles: Dict[str, Any] = {
        "drawer": None, "drawee": None, "acceptor": None,
        "endorsee": None, "from_party": None,
    }

    m = _DRAW_ACTIVE_RE.search(text)
    if not m:
        m = _DRAW_PASSIVE_RE.search(text)
    if m:
        roles["drawer"] = _party_token(m.group("drawer"))
        roles["drawee"] = _party_token(m.group("drawee"))

    m = _ACCEPT_BY_RE.search(text)
    if m:
        roles["acceptor"] = _party_token(m.group("acceptor"))
    else:
        m = _ACCEPT_SUBJECT_RE.search(text)
        if m:
            roles["acceptor"] = _party_token(m.group("acceptor"))
        else:
            m = _ACCEPT_TAIL_RE.search(text)
            if m:
                roles["acceptor"] = _party_token(m.group("acceptor"))

    # 'X accepted Y's bill' -> acceptor X, drawer Y (the drawee's books).
    m = _ACCEPT_POSSESSIVE_RE.search(text)
    if m:
        roles["acceptor"] = _party_token(m.group("acceptor"))
        roles["drawer"] = _party_token(m.group("drawer"))
    else:
        # 'Y's bill was accepted by X' -> same roles, passive form.
        m = _ACCEPT_POSSESSIVE_PASSIVE_RE.search(text)
        if m:
            roles["drawer"] = _party_token(m.group("drawer"))
            roles["acceptor"] = _party_token(m.group("acceptor"))

    m = _FROM_PARTY_RE.search(text)
    if m and _RECEIVED_RE.search(low):
        roles["from_party"] = _party_token(m.group("party"))

    m = _ENDORSE_RE.search(text)
    if m:
        roles["endorsee"] = _party_token(m.group("endorsee"))
    return roles


def _bill_amount(text: str, noting: Optional[Decimal],
                 proceeds: Optional[Decimal],
                 discount_stated: Optional[Decimal]) -> Optional[Decimal]:
    """The ONE stated bill amount after the noting / proceeds / discount
    roles are consumed, or None when it cannot be established
    deterministically. Amounts are consumed exactly once."""
    money = _money_amounts(text)
    consumed = {a for a in (noting, proceeds, discount_stated)
                if a is not None}
    remaining = [a for a in money if a not in consumed]
    distinct = {a for a in remaining}
    if len(distinct) == 1:
        return distinct.pop()
    if len(distinct) == 0:
        return None
    # several unused amounts: only the one nearest the word 'bill' (the
    # instrument itself) may own the bill role; any other leftover
    # amount makes the question ambiguous.
    low = " " + str(text or "").lower() + " "
    nearest = _amount_near(low, r"bill", window=24)
    if nearest is not None and nearest in distinct:
        leftovers = distinct - {nearest}
        if not leftovers:
            return nearest
    return None


def _discount_facts(text: str) -> Dict[str, Any]:
    """Discount-related stated facts: rate, proceeds, stated discount
    amount, period, draw date, due date."""
    low = " " + str(text or "").lower() + " "
    facts: Dict[str, Any] = {
        "rate": None, "proceeds": None, "discount_stated": None,
        "period": None, "draw_date": None, "due_date": None,
    }
    for m in _DISCOUNT_RATE_RE.finditer(low):
        if re.search(r"discount", low[max(0, m.start() - 45):m.end() + 5]):
            facts["rate"] = _dec(m.group(1))
            break
    m = _PROCEDS_RE.search(low)
    if m:
        facts["proceeds"] = _dec(m.group(1))
    m = _DISCOUNT_AMOUNT_RE.search(low)
    if m:
        facts["discount_stated"] = _dec(m.group(1))
    facts["period"] = _period_of(text)
    dates = _dates_in(text)
    # the draw date is a date attached via 'on' after a draw verb
    # ('drew a bill ... on 1 January 2025'); the due date is a date
    # attached via 'on' near 'due' / 'maturity'.
    for p, d in dates:
        pre = low[max(0, p - 16):p]
        if (re.search(r"\bon\s+", pre)
                and _DRAW_VERB_SIGNAL_RE.search(low[max(0, p - 70):p])):
            facts["draw_date"] = d
            break
    for p, d in dates:
        pre = low[max(0, p - 16):p]
        if (re.search(r"\bon\s+", pre)
                and re.search(r"\b(?:due|maturity)\b",
                              low[max(0, p - 40):p])):
            facts["due_date"] = d
            break
    return facts


# ---------------------------------------------------------------------------
# Resolution (Sprint 15I-BILLS sections 3-6)
# ---------------------------------------------------------------------------


def _resolve_bills(text: str) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import (
        INVALID_INPUT_MATH,
        NOT_SUPPORTED,
        REVIEW_REQUIRED,
        _fmt_amt,
    )
    low = " " + str(text or "").lower() + " "
    roles = _roles(text)
    facts = _discount_facts(text)

    # -- noting charges (exactly-once role) ------------------------------
    noting = _amount_near(low, r"noting\s+charges?")
    noting_by_bank = _NOTING_BY_BANK_RE.search(low) is not None
    if noting is not None and noting <= 0:
        noting = None

    # -- stated events ----------------------------------------------------
    received = _RECEIVED_RE.search(low) is not None
    drawn = (_DRAW_ACTIVE_RE.search(text) is not None
             or _DRAW_PASSIVE_RE.search(text) is not None)
    accepted_payable = _ACCEPT_POSSESSIVE_RE.search(text) is not None
    accepted = (_ACCEPT_BY_RE.search(text) is not None
                or _ACCEPT_SUBJECT_RE.search(text) is not None
                or _ACCEPT_TAIL_RE.search(text) is not None)
    retained = _RETAINED_RE.search(low) is not None
    discounted = _DISCOUNT_ACTION_RE.search(low) is not None
    endorsed = _ENDORSE_RE.search(text) is not None
    sent_collection = _SENT_COLLECTION_RE.search(low) is not None
    collected = _COLLECTED_RE.search(low) is not None
    honoured = _HONOURED_RE.search(low) is not None
    dishonoured = _DISHONOURED_RE.search(low) is not None
    repeated_dishonour = _REPEATED_DISHONOUR_RE.search(low) is not None

    notes: List[str] = []
    states: List[Dict[str, Any]] = []
    transitions: List[Dict[str, Any]] = []

    bills = {
        "authority": "bills-authority",
        "topic": TOPIC_BILLS,
        "case": None,
        "amount": None,
        "roles": roles,
        "states": states,
        "transitions": transitions,
        "history": {"established": False, "source": None, "invented": False},
        "maturity": None,
        "discount": None,
        "noting_charges": str(noting) if noting is not None else None,
        "notes": notes,
        "invented_history": False,
        "duplicate_correction": False,
    }

    # -- repeated dishonour ----------------------------------------------
    if repeated_dishonour:
        bills["duplicate_correction"] = True
        return _refusal(
            REVIEW_REQUIRED,
            "The bill is stated to be dishonoured more than once. Platrixa "
            "never books a duplicate correction - state the dishonour "
            "exactly once.",
            "Re-type the dishonour once with the exact amount and party.",
            bills)

    # -- origin / perspective consistency ---------------------------------
    if received and drawn:
        return _refusal(
            REVIEW_REQUIRED,
            "The bill's origin is ambiguous: the question both states that "
            "the bill was received from a party and that it was drawn by "
            "another. Platrixa never guesses whose bill it is.",
            "State the bill's origin exactly once (received from X, or "
            "drawn by X on Y).",
            bills)
    if accepted and not accepted_payable and not drawn and not received:
        # 'Rahul accepted the bill' with no draw clause: the drawer is
        # unknown, so neither the drawer's Bills Receivable entry nor the
        # acceptor's Bills Payable entry can be composed.
        return _refusal(
            REVIEW_REQUIRED,
            "The bill's drawer is not established: the question states that "
            "a bill was accepted but not who drew it. Platrixa never invents "
            "the party who drew the bill.",
            "State the acceptance as 'X accepted Y's bill' or include the "
            "drawing (e.g. 'Y drew a bill on X which X accepted').",
            bills)

    # -- history gate (never invent a previous bill state) ----------------
    established = any((received, drawn, accepted, accepted_payable,
                       discounted, endorsed, sent_collection, collected))
    if dishonoured and not established:
        bills["history"]["established"] = False
        return _refusal(
            REVIEW_REQUIRED,
            "The dishonoured bill's prior state and amount cannot be "
            "established: the question does not record the bill being "
            "drawn, received, discounted or otherwise brought into the "
            "books. Platrixa never reconstructs missing bill history.",
            "Enter the bill's creation (e.g. 'Received a bill of exchange "
            "from Ram for Rs.10,000') and then its dishonour.",
            bills)

    # -- the bill amount (consumed exactly once) --------------------------
    proceeds = facts.get("proceeds")
    discount_stated = facts.get("discount_stated")
    bill_amt = _bill_amount(text, noting, proceeds, discount_stated)
    if bill_amt is None:
        return _refusal(
            REVIEW_REQUIRED,
            "The bill amount cannot be established deterministically: the "
            "stated amounts do not resolve to exactly one bill amount "
            "after the noting charges / discount / proceeds roles are "
            "consumed. Platrixa never guesses which amount belongs to the bill.",
            "State the bill amount exactly once (e.g. 'a bill of "
            "Rs.10,000').",
            bills)
    bills["amount"] = str(bill_amt)

    # -- every stated rate must have a deterministic role ------------------
    # A percentage that is not the consumed bank-discount rate (and is
    # not the discount rate on a non-discount question) has no role in a
    # bills question - refuse instead of silently ignoring it.
    for m in re.finditer(r"\b([0-9]+(?:\.[0-9]+)?)\s*%", low):
        rate = _dec(m.group(1))
        if discounted and rate == facts.get("rate"):
            continue
        return _refusal(
            REVIEW_REQUIRED,
            f"A stated rate ({m.group(1)}%) has no deterministic role in "
            "this bills-of-exchange transaction. Platrixa never silently "
            "ignores a stated rate.",
            "Remove the rate, or state it as the bank-discount rate "
            "('discounted at X% per annum').",
            bills)

    # -- noting charges require a dishonour -------------------------------
    if noting is not None and not dishonoured:
        return _refusal(
            REVIEW_REQUIRED,
            "Noting charges are stated, but no dishonour is stated. Platrixa "
            "never books noting charges without the dishonour that "
            "justifies them.",
            "State the bill's dishonour together with the noting charges.",
            bills)

    # -- party-role consistency -------------------------------------------
    if (roles["drawee"] and roles["acceptor"]
            and roles["drawee"] != roles["acceptor"]):
        return _refusal(
            REVIEW_REQUIRED,
            "The bill's acceptor differs from its drawee. In the FYJC "
            "bills-of-exchange surface the acceptor is the drawee; Platrixa "
            "never infers which party owes the bill.",
            "State the drawee and acceptor as the same party.",
            bills)
    if (accepted_payable and not roles["acceptor"]) or (
            accepted_payable and not roles["drawer"]):
        return _refusal(
            REVIEW_REQUIRED,
            "The accepted bill's drawer cannot be established "
            "deterministically from the wording. Platrixa never invents the "
            "party who drew the bill.",
            "State who drew the bill that was accepted.",
            bills)
    if roles["endorsee"] is None and endorsed:
        return _refusal(
            REVIEW_REQUIRED,
            "The endorsee of the bill is not established. Platrixa never "
            "invents the creditor to whom the bill was endorsed.",
            "State the party to whom the bill was endorsed.",
            bills)

    # -- maturity / due-date contradiction --------------------------------
    period = facts.get("period")
    if facts.get("due_date") and facts.get("draw_date") and period:
        computed = _due_date(facts["draw_date"], period[0], period[1])
        if computed != facts["due_date"]:
            due_str = facts["due_date"].strftime("%d %b %Y")
            draw_str = facts["draw_date"].strftime("%d %b %Y")
            computed_str = computed.strftime("%d %b %Y")
            return _refusal(
                REVIEW_REQUIRED,
                (f"The stated due date ({due_str}) contradicts the draw "
                 f"date ({draw_str}) plus the stated period "
                 f"({period[0]} {period[1]}) and the FYJC three days of "
                 f"grace (due {computed_str}). Platrixa never journals a bill "
                 "whose dates contradict."),
                "Correct the draw date, the period or the due date so the "
                "maturity mathematics reconcile.",
                bills)

    # -- discount resolution ----------------------------------------------
    discount: Optional[Decimal] = None
    discount_meta: Optional[Dict[str, Any]] = None
    if discounted:
        rate = facts.get("rate")
        computed = None
        if rate is not None and period is not None:
            computed = _compute_discount(bill_amt, rate, period[0],
                                         period[1])
        if proceeds is not None:
            derived = bill_amt - proceeds
            if derived < 0:
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the discounted proceeds "
                     f"(Rs.{_fmt_amt(proceeds)}) exceed the bill amount "
                     f"(Rs.{_fmt_amt(bill_amt)}), implying a negative "
                     "bank discount. Platrixa never journals an impossible "
                     "discount."),
                    "Correct the proceeds or the bill amount.",
                    bills)
            if computed is not None and derived != computed:
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the stated proceeds "
                     f"(Rs.{_fmt_amt(proceeds)}) imply a discount of "
                     f"Rs.{_fmt_amt(derived)}, which contradicts the "
                     f"stated rate ({rate}% p.a. for {period[0]} "
                     f"{period[1]} = Rs.{_fmt_amt(computed)}). Platrixa never "
                     "journals a contradictory discount."),
                    "Correct the proceeds or the rate/period so the "
                    "discount reconciles.",
                    bills)
            discount = derived
            discount_meta = {
                "basis": "stated proceeds",
                "rate": str(rate) if rate is not None else None,
                "period": (f"{period[0]} {period[1]}"
                           if period is not None else None),
                "discount": str(derived),
                "proceeds": str(proceeds),
                "formula": (f"{bill_amt} - {proceeds}"),
            }
        elif discount_stated is not None:
            if computed is not None and discount_stated != computed:
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the stated discount "
                     f"(Rs.{_fmt_amt(discount_stated)}) contradicts the "
                     f"stated rate ({rate}% p.a. for {period[0]} "
                     f"{period[1]} = Rs.{_fmt_amt(computed)}). Platrixa never "
                     "journals a contradictory discount."),
                    "Correct the discount amount or the rate/period so "
                    "they reconcile.",
                    bills)
            discount = discount_stated
            discount_meta = {
                "basis": "stated discount",
                "rate": str(rate) if rate is not None else None,
                "period": (f"{period[0]} {period[1]}"
                           if period is not None else None),
                "discount": str(discount_stated),
                "proceeds": str(bill_amt - discount_stated),
                "formula": f"stated discount Rs.{discount_stated}",
            }
        elif computed is not None:
            discount = computed
            discount_meta = {
                "basis": "computed",
                "rate": str(rate),
                "period": f"{period[0]} {period[1]}",
                "discount": str(computed),
                "proceeds": str(bill_amt - computed),
                "formula": (f"{bill_amt} x {rate}% x {period[0]} "
                            f"{period[1]}{' / 12' if period[1].startswith('month') else ' / 365' if period[1].startswith('day') else ''}"),
            }
        else:
            return _refusal(
                REVIEW_REQUIRED,
                "The bank discount cannot be computed: the question does "
                "not state the discount rate and period, the discounted "
                "proceeds, or the discount amount. Platrixa never silently "
                "assumes a rate or a maturity period.",
                "State the discount rate and period (e.g. 'at 12% per "
                "annum for 3 months'), the discounted proceeds, or the "
                "discount amount.",
                bills)
        if discount <= 0:
            return _refusal(
                INVALID_INPUT_MATH,
                "INVALID_INPUT_MATH: the resolved bank discount is zero or "
                "negative. Platrixa never journals a discount that does not "
                "reduce the proceeds.",
                "Correct the rate, period or stated amounts.",
                bills)
        bills["discount"] = discount_meta

    # -- maturity payload -------------------------------------------------
    maturity: Optional[Dict[str, Any]] = None
    if period:
        maturity = {
            "period": f"{period[0]} {period[1]}",
            "days_of_grace": 3,
            "draw_date": (facts["draw_date"].strftime("%d %b %Y")
                          if facts.get("draw_date") else None),
            "due_date": None,
        }
        if facts.get("draw_date"):
            maturity["due_date"] = _due_date(
                facts["draw_date"], period[0], period[1]).strftime("%d %b %Y")
    bills["maturity"] = maturity

    # -- state-machine walk ------------------------------------------------
    def _add_state(state: str, event: str, implicit: bool = False) -> None:
        if states and states[-1]["state"] == state:
            return
        states.append({"state": state, "event": event,
                       "implicit": bool(implicit)})

    def _transition_ok() -> bool:
        for i in range(1, len(states)):
            if states[i]["state"] not in _VALID_TRANSITIONS.get(
                    states[i - 1]["state"], set()):
                return False
        return True

    # build the ordered state sequence from the stated events. When the
    # drawer negotiates the bill (discount / endorse / send for
    # collection) or the bill is settled at maturity, the implied
    # intermediate states (ACCEPTED / HELD) are recorded as IMPLICIT -
    # they are the only possible prior states, never an invented ledger
    # entry.
    def _in_hand() -> None:
        if states and states[-1]["state"] == STATE_DRAWN:
            _add_state(STATE_ACCEPTED, "negotiation_implies_acceptance",
                       implicit=True)

    def _held_to_maturity() -> None:
        if states and states[-1]["state"] in (STATE_DRAWN, STATE_ACCEPTED):
            if states[-1]["state"] == STATE_DRAWN:
                _add_state(STATE_ACCEPTED, "held_until_maturity",
                           implicit=True)
            _add_state(STATE_HELD, "held_until_maturity", implicit=True)

    if received:
        _add_state(STATE_DRAWN, "received")
        _add_state(STATE_ACCEPTED, "received")
    elif accepted_payable:
        _add_state(STATE_DRAWN, "accepted_payable")
        _add_state(STATE_ACCEPTED, "accepted_payable")
    elif drawn:
        _add_state(STATE_DRAWN, "drawn")
        if accepted:
            _add_state(STATE_ACCEPTED, "accepted")
    if accepted and not accepted_payable and states and \
            states[-1]["state"] == STATE_DRAWN:
        _add_state(STATE_ACCEPTED, "accepted")

    if retained:
        _add_state(STATE_HELD, "retained", implicit=True)
    if discounted:
        _in_hand()
        _add_state(STATE_DISCOUNTED, "discounted")
    if endorsed:
        _in_hand()
        _add_state(STATE_ENDORSED, "endorsed")
    if sent_collection:
        _in_hand()
        _add_state(STATE_SENT_COLLECTION, "sent_for_collection")
    if collected and honoured:
        return _refusal(
            REVIEW_REQUIRED,
            "The bill's outcome is ambiguous: the question states both that "
            "the bill was collected and that it was honoured. Platrixa never "
            "journals two settlements for one bill.",
            "State the bill's outcome exactly once.",
            bills)
    if collected:
        if not discounted and not endorsed:
            _held_to_maturity()
        _add_state(STATE_HONOURED, "collected")
    elif honoured:
        if not discounted and not endorsed:
            _held_to_maturity()
        _add_state(STATE_HONOURED, "honoured")
    if dishonoured:
        if not discounted and not endorsed and not sent_collection:
            _held_to_maturity()
        _add_state(STATE_DISHONOURED, "dishonoured")

    # terminal-state guard: an endorsed bill belongs to the endorsee
    if endorsed and (collected or honoured or dishonoured):
        return _refusal(
            NOT_SUPPORTED,
            "The bill was endorsed to the endorsee, so its later "
            "settlement (honour / dishonour / collection) is recorded in "
            "the endorsee's books, not the endorser's. Platrixa does not "
            "invent that record.",
            "Enter the endorsement only, or state the bill's outcome from "
            "the endorsee's perspective.",
            bills)

    # invalid explicit transitions
    if not states:
        return _refusal(
            REVIEW_REQUIRED,
            "The question does not establish a bill lifecycle event "
            "(drawn / received / accepted / discounted / endorsed / "
            "collection / honour / dishonour). Platrixa never invents a bill "
            "transaction.",
            "Re-type the question with an explicit bill event.",
            bills)
    if not _transition_ok():
        return _refusal(
            REVIEW_REQUIRED,
            "The stated bill lifecycle violates the valid state "
            "transitions (DRAWN -> ACCEPTED -> HELD / DISCOUNTED / "
            "ENDORSED / SENT_FOR_COLLECTION -> HONOURED / DISHONOURED). "
            "Platrixa never journals an invalid bill transition.",
            "Re-type the bill lifecycle in a valid order.",
            bills)

    for i in range(1, len(states)):
        transitions.append({
            "from": states[i - 1]["state"],
            "to": states[i]["state"],
            "valid": True,
            "implicit": bool(states[i].get("implicit")),
        })

    # -- journals ----------------------------------------------------------
    journals: List[Dict[str, Any]] = []
    drawee = roles["drawee"] or roles["acceptor"] or roles["from_party"]

    # acceptance entry (drawer's / receiver's books)
    if received or drawn or (accepted and not accepted_payable):
        if drawee is None:
            return _refusal(
                REVIEW_REQUIRED,
                "The bill's drawee / acceptor is not established, so the "
                "Bills Receivable entry cannot be credited to anyone. "
                "Platrixa never invents the party who owes the bill.",
                "State the party on whom the bill was drawn (e.g. 'on "
                "Mohan').",
                bills)
        journals.append(_journal(
            [_line("Bills Receivable", bill_amt, "debit")],
            [_line(drawee, bill_amt, "credit")],
            f"Being a bill of exchange for Rs.{_fmt_amt(bill_amt)} "
            f"received / accepted from {drawee}.",
        ))
    elif accepted_payable:
        journals.append(_journal(
            [_line(roles["drawer"], bill_amt, "debit")],
            [_line("Bills Payable", bill_amt, "credit")],
            f"Being a bill of exchange for Rs.{_fmt_amt(bill_amt)} drawn "
            f"by {roles['drawer']} on us and accepted (Bills Payable).",
        ))

    # discounting entry
    if discounted:
        rate_part = (f" at {discount_meta['rate']}% p.a."
                     if discount_meta.get("rate") else "")
        period_part = (f" for {discount_meta['period']}"
                       if discount_meta.get("period") else "")
        journals.append(_journal(
            [_line("Bank", bill_amt - discount, "debit"),
             _line("Discount", discount, "debit")],
            [_line("Bills Receivable", bill_amt, "credit")],
            (f"Being the bill discounted with the bank{rate_part}"
             f"{period_part} (bank discount "
             f"Rs.{_fmt_amt(discount)})."),
            calculation_records=[{
                "rule": "BK_BILL_DISCOUNT",
                "formula": discount_meta["formula"],
                "result": str(discount),
                "explanation": (f"Bank discount of Rs.{_fmt_amt(discount)} "
                                f"on a bill of Rs.{_fmt_amt(bill_amt)}."),
            }],
        ))
        notes.append(f"Bill discounted: proceeds "
                     f"Rs.{_fmt_amt(bill_amt - discount)} after bank "
                     f"discount of Rs.{_fmt_amt(discount)}.")

    # endorsement entry
    if endorsed:
        journals.append(_journal(
            [_line(roles["endorsee"], bill_amt, "debit")],
            [_line("Bills Receivable", bill_amt, "credit")],
            f"Being the bill endorsed to {roles['endorsee']} in discharge "
            f"of the debt due to them.",
        ))

    # sent-for-collection entry
    if sent_collection:
        journals.append(_journal(
            [_line("Bills Sent for Collection", bill_amt, "debit")],
            [_line("Bills Receivable", bill_amt, "credit")],
            "Being the bill sent to the bank for collection.",
        ))

    # collection entry
    if collected:
        if sent_collection:
            journals.append(_journal(
                [_line("Bank", bill_amt, "debit")],
                [_line("Bills Sent for Collection", bill_amt, "credit")],
                "Being the bill collected by the bank on maturity.",
            ))
        else:
            journals.append(_journal(
                [_line("Bank", bill_amt, "debit")],
                [_line("Bills Receivable", bill_amt, "credit")],
                "Being the bill collected on maturity.",
            ))

    # honour entry (held bill)
    if honoured and not discounted and not sent_collection:
        if _BANK_MODE_RE.search(low):
            account = "Bank"
        else:
            account = "Cash"
        journals.append(_journal(
            [_line(account, bill_amt, "debit")],
            [_line("Bills Receivable", bill_amt, "credit")],
            f"Being the bill honoured on maturity (received "
            f"Rs.{_fmt_amt(bill_amt)} in {account}).",
        ))

    # dishonour entry
    if dishonoured:
        if drawee is None:
            return _refusal(
                REVIEW_REQUIRED,
                "The dishonoured bill's drawee / acceptor is not "
                "established, so the reinstated debtor cannot be named. "
                "Platrixa never invents the party who owes the bill.",
                "State the party on whom the bill was drawn.",
                bills)
        total_claim = bill_amt + (noting or Decimal(0))
        if discounted:
            journals.append(_journal(
                [_line(drawee, total_claim, "debit")],
                [_line("Bank", total_claim, "credit")],
                (f"Being the bill dishonoured on maturity; the bank "
                 f"recovered Rs.{_fmt_amt(total_claim)} (bill "
                 f"Rs.{_fmt_amt(bill_amt)}"
                 + (f" + noting charges Rs.{_fmt_amt(noting)}"
                    if noting is not None else "")
                 + ") from our account.",
                 ),
            ))
        elif sent_collection:
            credit_lines = [_line("Bills Sent for Collection", bill_amt,
                                  "credit")]
            if noting is not None:
                credit_lines.append(_line(
                    "Bank" if noting_by_bank else "Cash",
                    noting, "credit"))
            journals.append(_journal(
                [_line(drawee, total_claim, "debit")],
                credit_lines,
                (f"Being the bill sent for collection dishonoured on "
                 f"maturity; {drawee}'s balance is reinstated"
                 + (f" and noting charges Rs.{_fmt_amt(noting)} are due"
                    if noting is not None else "") + "."),
            ))
        else:
            credit_lines = [_line("Bills Receivable", bill_amt, "credit")]
            if noting is not None:
                credit_lines.append(_line(
                    "Bank" if noting_by_bank else "Cash",
                    noting, "credit"))
            journals.append(_journal(
                [_line(drawee, total_claim, "debit")],
                credit_lines,
                (f"Being the bill dishonoured on maturity; {drawee}'s "
                 f"balance is reinstated"
                 + (f" and noting charges Rs.{_fmt_amt(noting)} are due"
                    if noting is not None else "") + "."),
            ))
        notes.append(f"Bill dishonoured by {drawee}; the debtor balance "
                     f"is reinstated at Rs.{_fmt_amt(total_claim)}.")

    bills["history"]["established"] = True
    bills["history"]["source"] = (
        "received" if received else
        "drawn" if drawn else
        "accepted" if accepted_payable else
        "discounted" if discounted else
        "endorsed" if endorsed else
        "collection" if sent_collection or collected else None)

    # -- case key ----------------------------------------------------------
    if len(states) >= 3 or len(journals) >= 2:
        case = "bill_chain"
    elif dishonoured:
        case = "bill_dishonoured"
    elif discounted:
        case = "bill_discounted"
    elif endorsed:
        case = "bill_endorsed"
    elif sent_collection or collected:
        case = "bill_collected" if collected else "bill_sent_for_collection"
    elif honoured:
        case = "bill_honoured"
    elif accepted_payable:
        case = "bill_payable"
    else:
        case = "bill_receivable"
    bills["case"] = case

    # -- final balancing backstop ------------------------------------------
    debit_total = sum((l["amount"] for j in journals
                       for l in j.get("debit_lines") or []), Decimal(0))
    credit_total = sum((l["amount"] for j in journals
                        for l in j.get("credit_lines") or []), Decimal(0))
    if debit_total != credit_total:
        return _refusal(
            REVIEW_REQUIRED,
            "The resolved bills journals do not balance. Platrixa never "
            "reports an unbalanced bill treatment as verified.",
            "Re-check the stated amounts and re-type the question.",
            bills)

    notes.insert(0, f"Bill of exchange for Rs.{_fmt_amt(bill_amt)} "
                    "tracked through its lifecycle.")
    next_action = "Post the bill entries in your journal and verify them."
    return _compose(journals, bills, next_action)


# ---------------------------------------------------------------------------
# Production entry point (called by the 15I-WF orchestrator)
# ---------------------------------------------------------------------------


def bills_outcome(question: str,
                  amount: Any = None) -> Dict[str, Any]:
    """Resolve ONE bills-of-exchange question deterministically.

    Pipeline: raw input -> 15I-VY normalization -> safety concerns ->
    global math contradiction validation -> lifecycle resolution ->
    verify accounting consistency -> canonical result. The SAME gates
    the hardened authority applies run FIRST, so the Bills Authority
    never bypasses or weakens a 15I-VY refusal.
    """
    from backend.maths.fyjc_normalization import (
        INVALID_INPUT_MATH,
        math_contradiction,
        normalize_fyjc_text,
        vy_harden,
    )
    raw = str(question or "")
    norm = normalize_fyjc_text(raw)
    text = norm.text

    # 15I-VY party/abbreviation safety: identity must be established
    # before ANY bill resolution (never a guessed party, never a
    # single-letter initial).
    if norm.concerns:
        result = _refusal(
            "REVIEW_REQUIRED",
            norm.concerns[0],
            "Replace the abbreviation or initial with its full meaning and "
            "re-type the bill transaction.")
        result["normalization"] = norm.provenance
        return result

    # 15I-VY global mathematical contradiction.
    contradiction = math_contradiction(text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "\U0001f534 INVALID INPUT (MATH)"
        return contradiction

    detected = detect_bills(text)
    if detected is None:
        # should never happen (the orchestrator routes before calling) -
        # fall back to the hardened boundary rather than guess.
        fallback = vy_harden(text, amount)
        fallback["normalization"] = norm.provenance
        return fallback

    result = _resolve_bills(text)
    result["normalization"] = norm.provenance
    return result
