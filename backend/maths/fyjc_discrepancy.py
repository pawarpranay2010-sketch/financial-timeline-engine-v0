"""
Platrixa
Sprint 15I-DISC - Discrepancy Authority
backend/maths/fyjc_discrepancy.py

A dedicated deterministic authority for discrepancy / reconciliation /
reversal / omission / rectification logic in the FYJC (Grade 11)
book-keeping engine. It does NOT rewrite the hardened accounting
authority (backend.maths.fyjc_bk_reasoning) - it owns ONLY the
discrepancy surface and composes its journals with the hardened
engine's own line format (account, traditional class, golden rule,
per-line WHY) so the canonical result is byte-compatible with the
Study / Verify flow.

Pipeline (inside the 15I-WF orchestrator):

    normalized input -> segment -> route -> Discrepancy Authority ->
    deterministic resolution -> verify accounting consistency ->
    canonical result (VERIFIED / REVIEW_REQUIRED / NOT_SUPPORTED /
    BLOCKED / INVALID_INPUT_MATH)

Supported surfaces:    * BRS (bank reconciliation) single-case adjustments: a cheque issued
    but not presented for payment, a cheque deposited but not yet
    cleared, bank charges / bank interest / direct bank payments
    recorded by the bank but absent from the cash book, and a
    dishonoured cheque in a bank-reconciliation context. Every case
    identifies the affected accounts, the direction of the adjustment,
    the amount, the book it belongs to (Cash Book / Pass Book), and the
    resulting reconciliation effect. No amount or previous transaction
    is ever invented.
  * Dishonoured cheques with an ESTABLISHED prior record: when the
    question itself records the original receipt/deposit of the cheque
    (or the sale/purchase it settled), the authority reverses the
    original bank effect and reinstates the customer's balance at the
    SAME stated amount. A dishonour with NO reliable prior record
    refuses - Platrixa never reconstructs missing ledger history.
  * Omitted transactions whose transaction type, accounts and amount
    are all deterministically established (purchase / sale / return /
    expense). The missing canonical accounting effect is generated;
    otherwise REVIEW_REQUIRED with zero journal lines.
  * Rectification of errors modelled as 'what was recorded -> what
    should have been recorded -> correction required': wrong account,
    wrong amount, wrong side, complete omission, partial omission, and
    Suspense ONLY when the trial-balance discrepancy is explicitly and
    deterministically established. Suspense is never invented merely
    because the question is a rectification question.

Safety invariants (Sprint 15I-DISC section 7/11):

  * every stated amount receives a deterministic role, or the question
    refuses with zero journal lines;
  * no invented account, amount, party or historical state;
  * no unbalanced VERIFIED journal;
  * no duplicate correction (a repeated / double dishonour refuses);
  * no dropped segment (the whole question is resolved as one unit);
  * deterministic repeated execution (pure functions, no randomness).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Topic vocabulary (deterministic)
# ---------------------------------------------------------------------------

TOPIC_DISHONOUR = "dishonour"
TOPIC_BRS = "brs"
TOPIC_OMISSION = "omission"
TOPIC_RECTIFICATION = "rectification"

# Bills of exchange belong to the (unimplemented) Bills Authority - a
# dishonoured / not-honoured BILL is never routed to the Discrepancy
# Authority (which would book it as cash).
_BILLS_RE = re.compile(
    r"\bbills?\s+of\s+exchange\b|\bbills?\s+receivable\b|"
    r"\bbills?\s+payable\b", re.IGNORECASE)

_DISHONOUR_RE = re.compile(
    r"\b(?:dishonou?red?|dishonou?r|bounced|returned\s+unpaid|"
    r"not\s+honou?red)\b", re.IGNORECASE)

# BRS single-case signals. Spans use [^;\n] - never [^.;] - because
# 'Rs.10,000' carries a period inside the token and must not break a
# cheque-context scan.
_UNPRESENTED_RE = re.compile(
    r"\bunpresented\b"
    r"|\b(?:cheque|check)\b[^;\n]{0,70}?\bnot\s+(?:yet\s+)?(?:been\s+)?"
    r"presented\b"
    r"|\bnot\s+(?:yet\s+)?(?:been\s+)?presented\s+(?:for\s+(?:payment|"
    r"clearing)|to\s+(?:the\s+)?bank\s+for\s+payment)\b", re.IGNORECASE)

_UNCLEARED_RE = re.compile(
    r"\buncleared\b"
    r"|\b(?:cheque|check)\b[^;\n]{0,70}?\bnot\s+(?:yet\s+)?(?:been\s+)?"
    r"(?:cleared|collected|credited|realised)\b"
    r"|\bnot\s+(?:yet\s+)?(?:been\s+)?cleared\s+by\s+(?:the\s+)?bank\b"
    r"|\b(?:deposited|paid\s+in)\s+(?:a|the|his|her|their)?\s*"
    r"(?:cheque|check)\b[^;\n]{0,70}?\bnot\s+(?:yet\s+)?(?:been\s+)?"
    r"(?:cleared|collected|credited|realised)\b", re.IGNORECASE)

_BRS_BOOK_SIGNAL_RE = re.compile(
    r"\b(?:not\s+(?:recorded|entered|passed|posted|shown)\s+(?:in|into)\s+"
    r"(?:the\s+)?(?:cash\s*book|books)"
    r"|not\s+in\s+(?:the\s+)?(?:cash\s*book|books)"
    r"|per\s+pass\s+book|as\s+per\s+(?:the\s+)?pass\s+book)\b",
    re.IGNORECASE)

_BANK_CHARGES_RE = re.compile(
    r"\bbank\s+charges?\b(?:[^.;]{0,60}?\b(?:of|amounting\s+to|for)\s*"
    r"(?:rs\.?|\u20b9|inr)?\s*\d[0-9,]*(?:\.\d+)?)?",
    re.IGNORECASE)

_BANK_INTEREST_RE = re.compile(
    r"\b(?:bank\s+)?interest\b", re.IGNORECASE)

_DIRECT_BANK_PAYMENT_RE = re.compile(
    r"\b(?:paid|payment|pays?|made)\b[^.;]{0,70}?\b(?:directly\s+)?by\s+"
    r"(?:the\s+)?bank\b"
    r"|\bstanding\s+instructions?\b"
    r"|\bdirect\s+(?:payment|debit|credit)\s+by\s+(?:the\s+)?bank\b"
    r"|\bbank\b[^.;]{0,60}?\b(?:paid|debited)\s+on\s+(?:our\s+behalf|"
    r"the\s+firm'?s\s+behalf)\b", re.IGNORECASE)

_GENERAL_BRS_RE = re.compile(
    r"\bbank\s+reconciliation\b|\breconciliation\s+statement\b|"
    r"\bpass\s+book\b|\bcash\s*book\b", re.IGNORECASE)

# Omission: a transaction completely / partially missing from the books.
_OMISSION_RE = re.compile(
    r"\bomitt(?:ed|ing|s)?\b"
    r"|\bnot\s+(?:recorded|entered|passed|posted|journali[sz]ed)\s+"
    r"(?:in|into)\s+(?:the\s+)?(?:books|book|journal|ledger)\b"
    r"|\b(?:was|were)\s+(?:never|not)\s+(?:recorded|entered|posted)\s+"
    r"(?:in|into)\s+(?:the\s+)?(?:books|book|journal|ledger)\b",
    re.IGNORECASE)

# Rectification: an error of commission / omission with a wrong account,
# wrong amount, wrong side, or an explicitly established Suspense context.
_RECTIFICATION_RE = re.compile(
    r"\bwrong(?:ly)?\b|\bmistake(?:nly)?\b|\berroneously\b|"
    r"\bin\s+error\b|\bby\s+error\b|\brectif(?:y|ied|ication|ying)?\b|"
    r"\bsuspense\b|\berror\s+of\s+(?:commission|principle|omission|"
    r"compensation)\b"
    r"|\b(?:posted|entered|recorded|credited|debited)\b[^;\n]{0,60}?\b"
    r"instead\s+of\b"
    r"|\b(?:under|over)cast\b"
    r"|\btrial\s+balance\b[^;\n]{0,70}?\b(?:did\s+not|does\s+not)\s+"
    r"(?:tally|agree|balance)\b"
    r"|\btrial\s+balance\b[^;\n]{0,70}?\b(?:showed|shows)\s+"
    r"(?:a|the)?\s*difference\b", re.IGNORECASE)

# Partial-record wording (rectification / omission boundary).
_PARTIAL_RECORD_RE = re.compile(
    r"\bpartial(?:ly)?\b|\bonly\s+(?:rs\.?|\u20b9|inr)?\s*\d|"
    r"\bpart\s+of\s+(?:the\s+)?(?:amount|entry|transaction)\b",
    re.IGNORECASE)

# Dishonour direction helpers (which side of the cheque the business is on).
_CHEQUE_RECEIVED_RE = re.compile(
    r"\b(?:received|receiving|took|taken|got|obtained)\s+(?:a|the|his|her|"
    r"their)?\s*(?:cheque|check)\b"
    r"|\b(?:cheque|check)\b[^;\n]{0,70}?\b(?:was|has\s+been|had\s+been|is)\s+"
    r"(?:received|deposited|paid\s+in|credited\s+to\s+(?:the\s+)?bank)\b"
    r"|\bdeposited\s+(?:a|the|his|her|their)?\s*(?:cheque|check)\b"
    r"|\b(?:cheque|check)\s+(?:from|received\s+from)\b"
    r"|\b(?:cheque|check)\b[^;\n]{0,40}?\b(?:deposited|collected)\b",
    re.IGNORECASE)

_CHEQUE_ISSUED_RE = re.compile(
    r"\b(?:issued|gave|given|drawn|drew|sent)\s+(?:a|the|his|her|their)?\s*"
    r"(?:cheque|check)\b"
    r"|\b(?:cheque|check)\b[^;\n]{0,70}?\b(?:was|has\s+been|had\s+been|is)\s+"
    r"(?:issued|given|drawn|sent)\b"
    r"|\b(?:cheque|check)\s+(?:issued|given|drawn|sent)\b"
    r"|\bpaid\b[^;\n]{0,50}?\b(?:by|through|via)\s+(?:a\s+|the\s+)?"
    r"(?:cheque|check)\b", re.IGNORECASE)

# Wrong-account / wrong-amount / wrong-side / suspense shapes.
_EXPLICIT_WRONG_ACCOUNT_RE = re.compile(
    r"\b(?:wrongly|by\s+mistake|mistakenly|erroneously|in\s+error|by\s+error)?"
    r"\s*(?:credited|debited)\s+(?:to\s+)?"
    r"([A-Z][A-Za-z' .]{1,40}?)(?:'s)?\s+(?:account|a/c)\s+(?:instead\s+of|"
    r"rather\s+than)\s+([A-Z][A-Za-z' .]{1,40}?)(?:'s)?\s+(?:account|a/c)\b",
    re.IGNORECASE)

_IMPLICIT_WRONG_ACCOUNT_RE = re.compile(
    r"\b(?:wrongly|by\s+mistake|mistakenly|erroneously|in\s+error|by\s+error)?"
    r"\s*(?:posted|entered|recorded|passed)\s+(?:the\s+entry|it|the\s+"
    r"transaction)?\s*(?:to|in)\s+([A-Z][A-Za-z' .]{1,40}?)(?:'s)?\s+account\b",
    re.IGNORECASE)

_RECORDED_AMOUNT_RE = re.compile(
    r"(?:\b(?:recorded|entered|posted|passed|shown)\s+(?:at|as|for)\s+"
    r"(?:rs\.?|\u20b9|inr)?\s*"
    r"|\bonly\s+(?:rs\.?|\u20b9|inr)?\s*)"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)"
    r"(?:\s+(?:was|were)\s+(?:recorded|entered|posted|passed))?\b",
    re.IGNORECASE)

_UNDERCAST_OVERCAST_RE = re.compile(
    r"\b(sales|purchases|purchase|sale)\s+book\s+(?:was|were|is|are|has\s+"
    r"been|have\s+been)?\s*(under|over)cast\s+by\s+"
    r"(?:rs\.?|\u20b9|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\b",
    re.IGNORECASE)

_TRIAL_BALANCE_DIFFERENCE_RE = re.compile(
    r"\btrial\s+balance\b[^.;]{0,70}?\b(?:did\s+not|does\s+not)\s+(?:tally|"
    r"agree|balance)\b"
    r"|\btrial\s+balance\b[^.;]{0,70}?\b(?:showed|shows)\s+(?:a|the)?\s*"
    r"difference\b"
    r"|\btrial\s+balance\b[^.;]{0,70}?\b(?:was|is)\s+out\b", re.IGNORECASE)

_WRONG_SIDE_SALE_RE = re.compile(
    r"\b(?:sold|sale|goods\s+sold)\b[^;\n]{0,90}?\bfor\s+cash\b[^;\n]{0,90}?\b"
    r"(?:wrongly|by\s+mistake|mistakenly|erroneously|in\s+error)?\s*debited\s+"
    r"(?:to\s+)?([A-Z][A-Za-z' .]{1,40}?)(?:'s)?(?:\s+(?:account|a/c))?\s+"
    r"instead\s+of\s+(?:crediting|crediting\s+sales)\b", re.IGNORECASE)

_WRONG_SIDE_PURCHASE_RE = re.compile(
    r"\b(?:purchased|bought|purchase|goods\s+purchased)\b[^;\n]{0,90}?\b"
    r"for\s+cash\b[^;\n]{0,90}?\b"
    r"(?:wrongly|by\s+mistake|mistakenly|erroneously|in\s+error)?\s*credited\s+"
    r"(?:to\s+)?([A-Z][A-Za-z' .]{1,40}?)(?:'s)?(?:\s+(?:account|a/c))?\s+"
    r"instead\s+of\s+(?:debiting|debiting\s+purchases)\b", re.IGNORECASE)

_CHEQUE_IN_HAND_RE = re.compile(
    r"\bnot\s+(?:yet\s+)?presented\s+to\s+(?:the\s+)?bank\s+for\s+collection\b",
    re.IGNORECASE)

_REPEATED_DISHONOUR_RE = re.compile(
    r"\b(?:dishonou?red|bounced)\b[^.;]{0,50}?\b(?:again|twice|a\s+second\s+"
    r"time|second\s+time)\b|\btwo\s+(?:cheques|checks)\b[^.;]{0,40}?\b"
    r"dishonou?red\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Detection (routing from the orchestrator)
# ---------------------------------------------------------------------------


def detect_discrepancy(text: str) -> Optional[Dict[str, Any]]:
    """Return the discrepancy topic(s) carried by the question, or None.

    A bills-of-exchange question is NEVER routed here (the Bills
    Authority owns it). The result is a routing decision only - the
    resolver performs the actual deterministic resolution.
    """
    raw = str(text or "")
    low = " " + raw.lower() + " "
    if _BILLS_RE.search(low):
        return None
    topics: List[str] = []
    if _DISHONOUR_RE.search(low):
        topics.append(TOPIC_DISHONOUR)
    if (_UNPRESENTED_RE.search(low) or _UNCLEARED_RE.search(low)
            or _GENERAL_BRS_RE.search(low)):
        topics.append(TOPIC_BRS)
    if _OMISSION_RE.search(low):
        topics.append(TOPIC_OMISSION)
    if _RECTIFICATION_RE.search(low):
        topics.append(TOPIC_RECTIFICATION)
    # an implicit wrong-amount error: 'the entry was recorded at Rs.X'
    # with a SECOND, DIFFERENT stated amount (the correct value) - a
    # rectification signal even without the word 'wrongly'/'instead of'.
    # 'recorded at the same amount' is not an error.
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(raw)
    distinct = {a for a in amounts}
    m = _RECORDED_AMOUNT_RE.search(low)
    if m:
        recorded = _dec(m.group(1))
        if (recorded is not None and len(distinct) >= 2
                and distinct - {recorded}):
            topics.append(TOPIC_RECTIFICATION)
    # a partial omission: 'only Rs.X was recorded in the books' - the
    # recorded part is stated (and differs from the full amount), the
    # rest of the entry is missing.
    m2 = re.search(
        r"\bonly\s+(?:rs\.?|\u20b9|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
        r"(?:was|were)\s+(?:recorded|entered|posted|passed)\b", low)
    if m2:
        recorded2 = _dec(m2.group(1))
        if (recorded2 is not None and len(distinct) >= 2
                and distinct - {recorded2}):
            topics.append(TOPIC_RECTIFICATION)
    if not topics:
        return None
    return {"topics": topics, "text": raw}


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


def _refusal(status: str, why_not: str, next_action: str,
             discrepancy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import _refusal as engine_refusal
    result = engine_refusal(status, why_not, next_action)
    if discrepancy:
        result["discrepancy"] = discrepancy
    return result


def _line(account: str, amount: Decimal, side: str,
          why: Optional[str] = None) -> Dict[str, Any]:
    """One journal line in the hardened engine's exact format (the class
    override keeps a named party Personal even when its name reads like a
    noun). The WHY is discrepancy-specific when supplied; otherwise the
    engine's own golden-rule why is used."""
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


def _party_after(text: str, marker: str) -> Optional[str]:
    """The party token right after a marker ('from ', 'to ', 'by ') -
    reuses the hardened engine's own party resolution when possible."""
    from backend.maths.fyjc_bk_reasoning import _party_from_text
    return _party_from_text(text)


def _possessive_party(text: str) -> Optional[str]:
    """'Ram's cheque' / 'Rahul's account' - the owner of the cheque or
    account. Never invents a name (single-letter tokens were already
    refused by the 15I-VY normalization layer upstream)."""
    m = re.match(
        r"\s*([A-Z][A-Za-z' .]{1,40}?)(?:'s|\u2019s)\s+(?:cheque|check)\b",
        str(text or ""))
    if not m:
        return None
    party = m.group(1).strip().rstrip(".;,")
    from backend.maths.fyjc_bk_reasoning import _normalise_party_token
    return _normalise_party_token(party)


def _amount_near(low: str, words: str,
                 window: int = 24) -> Optional[Decimal]:
    from backend.maths.fyjc_normalization import _amount_near as near
    return near(low, words, window=window)


def _single_amount(text: str) -> Optional[Decimal]:
    """The ONE stated money amount, or None when zero / several amounts
    make the role ambiguous."""
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(text)
    if len(amounts) != 1:
        return None
    return amounts[0]


def _cheque_amount(low: str, text: str) -> Optional[Decimal]:
    """The cheque's amount: 'cheque of/for Rs.X' wins, then the single
    stated amount, then the amount nearest the word 'cheque'."""
    from backend.maths.fyjc_bk_reasoning import _cheque_amount_in
    direct = _cheque_amount_in(low)
    if direct is not None:
        return direct
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(text)
    if len(amounts) == 1:
        return amounts[0]
    if len(amounts) > 1:
        return _amount_near(low, r"cheque|check", window=24)
    return None


def _sale_or_purchase_value(low: str, text: str,
                            cheque_amount: Decimal) -> Optional[Decimal]:
    """The stated transaction value when the segment also contains a
    sale/purchase whose settlement cheque was dishonoured. The value is
    the stated amount that is NOT the cheque amount; when only one
    amount exists it is both (the full settlement)."""
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(text)
    others = [a for a in amounts if a != cheque_amount]
    if not others:
        return cheque_amount
    if len(others) == 1:
        return others[0]
    return None


def _expense_account_in(low: str) -> Optional[str]:
    """The ONE registered expense account named in the text, or None."""
    from backend.maths.fyjc_bk_reasoning import _EXPENSE_ACCOUNT_WORDS
    found: List[str] = []
    for phrase, account in _EXPENSE_ACCOUNT_WORDS:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low):
            if account not in found:
                found.append(account)
    return found[0] if len(found) == 1 else None


def _party_from_text(text: str) -> Optional[str]:
    from backend.maths.fyjc_bk_reasoning import _party_from_text as pt
    return pt(text)


def _fmt_amt(value: Any) -> str:
    from backend.maths.fyjc_bk_reasoning import _fmt_amt as fa
    return fa(value)


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
             discrepancy: Dict[str, Any],
             next_action: str) -> Dict[str, Any]:
    """Shape the resolved journals into the hardened-engine envelope
    (multi-transaction shape: journals + merged lines + ledger + trial
    balance + verification)."""
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
            if debit_lines else "No journal entry required (timing difference)",
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
        "discrepancy": discrepancy,
        "audit": {
            "authority": "discrepancy-authority",
            "rule_key": None,
            "calculation_ids": [],
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "topic": discrepancy.get("topic"),
            "case": discrepancy.get("case"),
        },
    }


# ---------------------------------------------------------------------------
# Dishonour (Sprint 15I-DISC sections 2-3, 6)
# ---------------------------------------------------------------------------


def _resolve_dishonour(text: str, brs_context: bool) -> Dict[str, Any]:
    low = " " + text.lower() + " "
    discrepancy: Dict[str, Any] = {
        "authority": "discrepancy-authority",
        "topic": TOPIC_DISHONOUR,
        "case": "dishonoured_cheque",
        "reconciliation": [],
        "correction_model": None,
        "history": {"established": False, "direction": None, "source": None},
        "notes": [],
        "invented_history": False,
        "duplicate_correction": False,
    }

    # -- repeated / double dishonour ---------------------------------------
    if _REPEATED_DISHONOUR_RE.search(low):
        discrepancy["duplicate_correction"] = True
        return _refusal(
            "REVIEW_REQUIRED",
            ("The question states the same cheque was dishonoured more than "
             "once. A second dishonour would duplicate the reversal of the "
             "first - Platrixa never posts a double correction, and it does not "
             "guess which dishonour the question means."),
            "State the dishonour of ONE cheque (or enter a new cheque "
            "separately).",
            discrepancy)

    # -- the cheque amount --------------------------------------------------
    cheque_amount = _cheque_amount(low, text)
    if cheque_amount is None:
        return _refusal(
            "REVIEW_REQUIRED",
            "The dishonoured cheque has no stated amount (or several "
            "amounts whose roles are unclear). Platrixa never invents the "
            "amount of a bounced cheque.",
            "State the cheque amount, e.g. 'Ram's cheque of Rs.5,000 was "
            "dishonoured.'",
            discrepancy)

    # -- the party ----------------------------------------------------------
    party = (_possessive_party(text) or _party_from_text(text))
    if not party:
        return _refusal(
            "REVIEW_REQUIRED",
            "The dishonoured cheque does not name whose cheque it was. "
            "Platrixa never invents a party identity.",
            "Name the party, e.g. 'Received a cheque from Ram for Rs.10,000 "
            "which was dishonoured.'",
            discrepancy)

    # -- direction / historical dependency (section 6 gate) -----------------
    received = bool(_CHEQUE_RECEIVED_RE.search(low))
    issued = bool(_CHEQUE_ISSUED_RE.search(low))
    if not received and not issued:
        discrepancy["history"]["established"] = False
        return _refusal(
            "REVIEW_REQUIRED",
            (f"Platrixa has no reliable record that the Rs.{_fmt_amt(cheque_amount)} "
             f"cheque was previously received or recorded - the question "
             f"states only the dishonour, not the original receipt/deposit. "
             "Platrixa never reconstructs the missing ledger history and never "
             "treats a dishonoured cheque as a new unrelated receipt."),
            ("Enter the original receipt first (e.g. 'Received a cheque from "
             f"{party} for Rs.{_fmt_amt(cheque_amount)}'), then state the "
             "dishonour."),
            discrepancy)
    discrepancy["history"]["established"] = True
    discrepancy["history"]["direction"] = "received" if received else "issued"
    discrepancy["history"]["source"] = (
        "stated in the question (the original receipt/deposit)")

    journals: List[Dict[str, Any]] = []

    # -- underlying credit sale (the cheque settled it) ---------------------
    sale_value: Optional[Decimal] = None
    purchase_value: Optional[Decimal] = None
    from backend.maths.fyjc_bk_reasoning import (
        _purchase_direction_in,
        _sale_direction_in,
    )
    if received and _sale_direction_in(low):
        sale_value = _sale_or_purchase_value(low, text, cheque_amount)
        if sale_value is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The question states several amounts and Platrixa cannot tell "
                "which is the sale value and which is the cheque. It never "
                "guesses the split.",
                "State the sale value and the cheque amount explicitly.",
                discrepancy)
        journals.append(_journal(
            [_line(party, sale_value, "debit",
                   f"Credit sale to {party} - the cheque that later bounced "
                   "settled this receivable.")],
            [_line("Sales", sale_value, "credit",
                   "Goods sold on credit - income recognised at the stated "
                   "value.")],
            f"Being the credit sale to {party} for "
            f"Rs.{_fmt_amt(sale_value)}; the settlement cheque was later "
            "dishonoured."))
    elif received and _purchase_direction_in(low):
        # a purchase settled by a RECEIVED cheque is not a coherent chain
        # (a purchase is paid, not received) - refuse rather than guess.
        return _refusal(
            "REVIEW_REQUIRED",
            "The question combines a purchase with a RECEIVED cheque that "
            "was dishonoured. Platrixa cannot determine the settlement structure "
            "deterministically - it never guesses.",
            "Enter the purchase and the cheque receipt as separate "
            "transactions.",
            discrepancy)

    # -- the original receipt / payment and its reversal --------------------
    if received:
        journals.append(_journal(
            [_line("Bank", cheque_amount, "debit",
                   f"Cheque received from {party} - credited to the bank on "
                   "receipt.")],
            [_line(party, cheque_amount, "credit",
                   f"{party} settled by cheque - the customer's balance was "
                   "reduced.")],
            f"Being the cheque of Rs.{_fmt_amt(cheque_amount)} received from "
            f"{party}."))
        journals.append(_journal(
            [_line(party, cheque_amount, "debit",
                   f"The cheque from {party} was dishonoured - the customer's "
                   "outstanding balance is reinstated.")],
            [_line("Bank", cheque_amount, "credit",
                   "The bank reversed the credit when the cheque bounced - "
                   "the original bank effect is reversed.")],
            f"Being the dishonour of {party}'s cheque of "
            f"Rs.{_fmt_amt(cheque_amount)} - the original bank effect is "
            "reversed and the customer's balance reinstated."))
    else:
        journals.append(_journal(
            [_line(party, cheque_amount, "debit",
                   f"Cheque issued to {party} - a payment made.")],
            [_line("Bank", cheque_amount, "credit",
                   "Payment by cheque - the bank account is reduced.")],
            f"Being the cheque of Rs.{_fmt_amt(cheque_amount)} issued to "
            f"{party}."))
        journals.append(_journal(
            [_line("Bank", cheque_amount, "debit",
                   f"The cheque issued to {party} was dishonoured - the "
                   "payment is reversed and the bank account restored.")],
            [_line(party, cheque_amount, "credit",
                   f"{party}'s balance is reinstated - the payment no longer "
                   "stands.")],
            f"Being the dishonour of the cheque issued to {party} - the "
            "payment is reversed and the creditor's balance reinstated."))

    if brs_context:
        discrepancy["reconciliation"].append({
            "book": "Cash Book",
            "direction": "deduct",
            "amount": str(cheque_amount),
            "effect": (f"The bank has debited Rs.{_fmt_amt(cheque_amount)} "
                       "for the dishonoured cheque, which is not recorded in "
                       "the Cash Book - DEDUCT it from the Cash Book balance "
                       "(or ADD it to the Pass Book balance) while "
                       "reconciling."),
        })
    discrepancy["notes"].append(
        f"The dishonour reverses the original Rs.{_fmt_amt(cheque_amount)} "
        f"bank effect and reinstates {party}'s outstanding balance - the "
        "amount is preserved exactly, never re-stated.")
    return _compose(
        journals, discrepancy,
        "Post the reversal entry; the customer's balance stands at "
        f"Rs.{_fmt_amt(cheque_amount)}.")


# ---------------------------------------------------------------------------
# BRS (Sprint 15I-DISC section 2)
# ---------------------------------------------------------------------------


def _resolve_brs(text: str) -> Dict[str, Any]:
    low = " " + text.lower() + " "
    discrepancy: Dict[str, Any] = {
        "authority": "discrepancy-authority",
        "topic": TOPIC_BRS,
        "case": "bank_reconciliation",
        "reconciliation": [],
        "correction_model": None,
        "history": None,
        "notes": [],
        "invented_history": False,
        "duplicate_correction": False,
    }
    amount = _single_amount(text)

    # -- 0. list-form BRS (a particulars statement) is NOT supported --------
    # Platrixa resolves ONE adjustment per question; a statement built from a
    # list of particulars (with several adjustments) would silently drop
    # items. NOT_SUPPORTED, never a partial statement.
    if re.search(r"\b(?:particulars|the\s+following|given\s+below|"
                 r"following\s+items|following\s+adjustments)\b", low):
        return _refusal(
            "NOT_SUPPORTED",
            ("A full Bank Reconciliation Statement from a list of "
             "particulars is not supported yet - Platrixa resolves ONE "
             "adjustment at a time (an unpresented cheque, an uncleared "
             "cheque, bank charges, bank interest, a direct bank payment, "
             "or a dishonoured cheque)."),
            "Enter each BRS adjustment separately as its own question.",
            discrepancy)

    # -- 1. cheque issued but not yet presented -----------------------------
    if _UNPRESENTED_RE.search(low):
        if _CHEQUE_IN_HAND_RE.search(low):
            return _refusal(
                "REVIEW_REQUIRED",
                ("The cheque was received but not yet presented to the bank "
                 "for collection - it is a cheque in hand, not an unpresented "
                 "cheque in the BRS sense. Platrixa does not guess whether it "
                 "was banked."),
                "State whether the cheque was deposited or kept in hand.",
                discrepancy)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The unpresented cheque has no single stated amount. Platrixa "
                "never invents the amount of a timing difference.",
                "State the cheque amount.",
                discrepancy)
        discrepancy["case"] = "cheque_issued_not_presented"
        discrepancy["reconciliation"].append({
            "book": "Pass Book",
            "direction": "add",
            "amount": str(amount),
            "effect": (f"The payment of Rs.{_fmt_amt(amount)} is recorded in "
                       "the Cash Book but not yet in the Pass Book (the "
                       "cheque has not been presented to the bank) - ADD it "
                       "to the Pass Book balance (or DEDUCT it from the Cash "
                       "Book balance) while reconciling."),
        })
        discrepancy["notes"].append(
            "No journal entry is required for the timing difference itself - "
            "the original cheque-issue entry already stands in the books.")
        return _compose(
            [], discrepancy,
            "Record the timing difference in the Bank Reconciliation "
            "Statement (ADD to the Pass Book balance).")

    # -- 2. cheque deposited but not yet cleared ----------------------------
    if _UNCLEARED_RE.search(low):
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The uncleared cheque has no single stated amount. Platrixa "
                "never invents the amount of a timing difference.",
                "State the cheque amount.",
                discrepancy)
        discrepancy["case"] = "cheque_deposited_not_cleared"
        discrepancy["reconciliation"].append({
            "book": "Pass Book",
            "direction": "deduct",
            "amount": str(amount),
            "effect": (f"The deposit of Rs.{_fmt_amt(amount)} is recorded in "
                       "the Cash Book but not yet in the Pass Book (the "
                       "cheque has not been cleared by the bank) - DEDUCT it "
                       "from the Pass Book balance (or ADD it to the Cash "
                       "Book balance) while reconciling."),
        })
        discrepancy["notes"].append(
            "No journal entry is required for the timing difference itself - "
            "the original cheque-deposit entry already stands in the books.")
        return _compose(
            [], discrepancy,
            "Record the timing difference in the Bank Reconciliation "
            "Statement (DEDUCT from the Pass Book balance).")

    # -- 3. bank charges recorded by the bank only --------------------------
    if _BANK_CHARGES_RE.search(low) and _BRS_BOOK_SIGNAL_RE.search(low):
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The bank charges have no single stated amount. Platrixa never "
                "invents the amount.",
                "State the bank-charges amount.",
                discrepancy)
        discrepancy["case"] = "bank_charges"
        journal = _journal(
            [_line("Bank Charges", amount, "debit",
                   "Charges debited by the bank but absent from the Cash "
                   "Book - the books must record the expense.")],
            [_line("Bank", amount, "credit",
                   "The bank has already debited the account - the Cash Book "
                   "is updated to agree with the Pass Book.")],
            f"Being the bank charges of Rs.{_fmt_amt(amount)} recorded by "
            "the bank but not yet entered in the Cash Book.")
        discrepancy["reconciliation"].append({
            "book": "Cash Book",
            "direction": "deduct",
            "amount": str(amount),
            "effect": (f"The bank has debited Rs.{_fmt_amt(amount)} in the "
                       "Pass Book but it is not in the Cash Book - DEDUCT it "
                       "from the Cash Book balance (or ADD it to the Pass "
                       "Book balance) while reconciling."),
        })
        return _compose(
            [journal], discrepancy,
            "Post the bank-charges entry so the Cash Book agrees with the "
            "Pass Book.")

    # -- 4. bank interest / direct credit recorded by the bank --------------
    if _BANK_INTEREST_RE.search(low) and (
            _BRS_BOOK_SIGNAL_RE.search(low)
            or re.search(r"\b(?:credited|allowed)\s+by\s+(?:the\s+)?bank\b",
                         low)
            or re.search(r"\bbank\b[^.;]{0,40}?\b(?:credited|allowed)\b",
                         low)):
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The bank interest has no single stated amount. Platrixa never "
                "invents the amount.",
                "State the interest amount.",
                discrepancy)
        credited = bool(re.search(
            r"\b(?:credited|allowed|received)\b[^.;]{0,40}?\b(?:by\s+)?"
            r"(?:the\s+)?bank\b"
            r"|\bbank\b[^.;]{0,40}?\bcredited\b", low))
        if credited:
            discrepancy["case"] = "bank_interest_credit"
            journal = _journal(
                [_line("Bank", amount, "debit",
                       "Interest credited by the bank but absent from the "
                       "Cash Book - the bank balance is higher.")],
                [_line("Interest Received", amount, "credit",
                       "Interest income recorded by the bank - recognised "
                       "now that it is determinable.")],
                f"Being the interest of Rs.{_fmt_amt(amount)} credited by the "
                "bank but not yet entered in the Cash Book.")
            discrepancy["reconciliation"].append({
                "book": "Cash Book",
                "direction": "add",
                "amount": str(amount),
                "effect": (f"The bank has credited Rs.{_fmt_amt(amount)} in "
                           "the Pass Book but it is not in the Cash Book - "
                           "ADD it to the Cash Book balance (or DEDUCT it "
                           "from the Pass Book balance) while reconciling."),
            })
        else:
            discrepancy["case"] = "bank_interest_debit"
            journal = _journal(
                [_line("Interest Paid", amount, "debit",
                       "Interest debited by the bank but absent from the "
                       "Cash Book - the expense must be recorded.")],
                [_line("Bank", amount, "credit",
                       "The bank has already debited the account.")],
                f"Being the interest of Rs.{_fmt_amt(amount)} debited by the "
                "bank but not yet entered in the Cash Book.")
            discrepancy["reconciliation"].append({
                "book": "Cash Book",
                "direction": "deduct",
                "amount": str(amount),
                "effect": (f"The bank has debited Rs.{_fmt_amt(amount)} in "
                           "the Pass Book but it is not in the Cash Book - "
                           "DEDUCT it from the Cash Book balance (or ADD it "
                           "to the Pass Book balance) while reconciling."),
            })
        return _compose(
            [journal], discrepancy,
            "Post the interest entry so the Cash Book agrees with the Pass "
            "Book.")

    # -- 5. direct payment made by the bank ---------------------------------
    if (_DIRECT_BANK_PAYMENT_RE.search(low)
            and _BRS_BOOK_SIGNAL_RE.search(low)):
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The direct bank payment has no single stated amount. Platrixa "
                "never invents the amount.",
                "State the payment amount.",
                discrepancy)
        expense = _expense_account_in(low)
        if expense is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The direct bank payment does not name which expense the "
                "bank paid (rent, insurance, electricity, ...). Platrixa never "
                "invents the expense account.",
                "Name the expense paid by the bank, e.g. 'Insurance premium "
                "paid directly by the bank.'",
                discrepancy)
        discrepancy["case"] = "direct_bank_payment"
        journal = _journal(
            [_line(expense, amount, "debit",
                   f"The {expense} was paid by the bank on our behalf - the "
                   "expense must be recorded in the books.")],
            [_line("Bank", amount, "credit",
                   "The bank has already debited the account for the direct "
                   "payment.")],
            f"Being the {expense.lower()} of Rs.{_fmt_amt(amount)} paid "
            "directly by the bank but not yet entered in the Cash Book.")
        discrepancy["reconciliation"].append({
            "book": "Cash Book",
            "direction": "deduct",
            "amount": str(amount),
            "effect": (f"The bank has debited Rs.{_fmt_amt(amount)} for the "
                       "direct payment, which is not in the Cash Book - "
                       "DEDUCT it from the Cash Book balance (or ADD it to "
                       "the Pass Book balance) while reconciling."),
        })
        return _compose(
            [journal], discrepancy,
            "Post the direct-payment entry so the Cash Book agrees with the "
            "Pass Book.")

    # -- 6. general BRS (list form) not yet supported -----------------------
    return _refusal(
        "NOT_SUPPORTED",
        ("A full Bank Reconciliation Statement from a list of particulars "
         "is not supported yet - Platrixa resolves ONE adjustment at a time "
         "(an unpresented cheque, an uncleared cheque, bank charges, bank "
         "interest, a direct bank payment, or a dishonoured cheque)."),
        "Enter each BRS adjustment separately as its own question.",
        discrepancy)


# ---------------------------------------------------------------------------
# Omission (Sprint 15I-DISC section 4)
# ---------------------------------------------------------------------------


def _resolve_omission(text: str) -> Dict[str, Any]:
    low = " " + text.lower() + " "
    discrepancy: Dict[str, Any] = {
        "authority": "discrepancy-authority",
        "topic": TOPIC_OMISSION,
        "case": "omitted_transaction",
        "reconciliation": [],
        "correction_model": None,
        "history": None,
        "notes": [],
        "invented_history": False,
        "duplicate_correction": False,
    }
    amount = _single_amount(text)
    if amount is None:
        return _refusal(
            "REVIEW_REQUIRED",
            "The omitted transaction has no single stated amount. Platrixa never "
            "invents the amount of a missing entry.",
            "State the amount of the omitted transaction.",
            discrepancy)

    from backend.maths.fyjc_bk_reasoning import (
        _purchase_direction_in,
        _sale_direction_in,
    )

    # -- returns ------------------------------------------------------------
    if re.search(r"\breturn(?:ed|s|ing)?\b", low):
        party = _party_from_text(text)
        if "goods returned by" in low or "returned by" in low \
                or "returned us goods" in low:
            if party is None:
                return _refusal(
                    "REVIEW_REQUIRED",
                    "The omitted customer-return does not name the customer. "
                    "Platrixa never invents a party.",
                    "Name the customer, e.g. 'Goods returned by Mohan worth "
                    "Rs.1,200.'",
                    discrepancy)
            discrepancy["case"] = "omitted_customer_return"
            journal = _journal(
                [_line("Sales Returns", amount, "debit",
                       "Goods returned by a customer - the return reduces "
                       "the sale income.")],
                [_line(party, amount, "credit",
                       f"{party} returned goods - their outstanding balance "
                       "is reduced.")],
                f"Being the goods of Rs.{_fmt_amt(amount)} returned by "
                f"{party}, omitted from the books.")
        elif "returned to" in low or "returned goods to" in low:
            if party is None:
                return _refusal(
                    "REVIEW_REQUIRED",
                    "The omitted supplier-return does not name the supplier. "
                    "Platrixa never invents a party.",
                    "Name the supplier, e.g. 'Returned goods worth Rs.800 to "
                    "Rahul.'",
                    discrepancy)
            discrepancy["case"] = "omitted_supplier_return"
            journal = _journal(
                [_line(party, amount, "debit",
                       f"Goods returned to {party} - the supplier's balance "
                       "is reduced.")],
                [_line("Purchase Returns", amount, "credit",
                       "Return of goods to a supplier - purchase income "
                       "recognised.")],
                f"Being the goods of Rs.{_fmt_amt(amount)} returned to "
                f"{party}, omitted from the books.")
        else:
            return _refusal(
                "REVIEW_REQUIRED",
                "The omitted return does not say who returned the goods (or "
                "to whom they were returned). Platrixa never guesses the "
                "direction of a return.",
                "State the return fully, e.g. 'Goods returned by Mohan worth "
                "Rs.1,200' or 'Returned goods to Rahul worth Rs.800.'",
                discrepancy)
        discrepancy["notes"].append(
            "The transaction type, accounts and amount are all "
            "deterministically established from the wording - the missing "
            "canonical effect is generated exactly once.")
        return _compose(
            [journal], discrepancy,
            "Record the omitted entry so the books are complete.")

    # -- purchases ----------------------------------------------------------
    if _purchase_direction_in(low):
        if "from " in low or "from\n" in low:
            party = _party_from_text(text)
            if party is None:
                return _refusal(
                    "REVIEW_REQUIRED",
                    "The omitted purchase does not name the supplier. Platrixa "
                    "never invents a party.",
                    "Name the supplier, e.g. 'Purchased goods from Rahul for "
                    "Rs.20,000.'",
                    discrepancy)
            discrepancy["case"] = "omitted_purchase"
            journal = _journal(
                [_line("Purchases", amount, "debit",
                       "Goods purchased on credit - the purchase must be "
                       "recorded.")],
                [_line(party, amount, "credit",
                       f"{party} supplied goods on credit - the creditor's "
                       "balance is raised.")],
                f"Being the purchase of goods worth Rs.{_fmt_amt(amount)} "
                f"from {party}, omitted from the books.")
        elif "for cash" in low or "by cash" in low:
            discrepancy["case"] = "omitted_purchase"
            journal = _journal(
                [_line("Purchases", amount, "debit",
                       "Goods purchased for cash - the purchase must be "
                       "recorded.")],
                [_line("Cash", amount, "credit",
                       "Cash paid for the goods.")],
                f"Being the cash purchase of goods worth Rs.{_fmt_amt(amount)}, "
                "omitted from the books.")
        elif "by cheque" in low:
            discrepancy["case"] = "omitted_purchase"
            journal = _journal(
                [_line("Purchases", amount, "debit",
                       "Goods purchased by cheque - the purchase must be "
                       "recorded.")],
                [_line("Bank", amount, "credit",
                       "Payment by cheque for the goods.")],
                f"Being the purchase of goods worth Rs.{_fmt_amt(amount)} by "
                "cheque, omitted from the books.")
        else:
            return _refusal(
                "REVIEW_REQUIRED",
                "The omitted purchase does not state whether it was for cash "
                "or on credit (and from whom). Platrixa never guesses the mode.",
                "Add 'for cash' or 'on credit from <name>' to the "
                "description.",
                discrepancy)
        discrepancy["notes"].append(
            "The transaction type, accounts and amount are all "
            "deterministically established from the wording - the missing "
            "canonical effect is generated exactly once.")
        return _compose(
            [journal], discrepancy,
            "Record the omitted entry so the books are complete.")

    # -- sales --------------------------------------------------------------
    if _sale_direction_in(low):
        if re.search(r"\bsold\b[^.;]{0,60}?\bto\b", low):
            party = _party_from_text(text)
            if party is None:
                return _refusal(
                    "REVIEW_REQUIRED",
                    "The omitted sale does not name the customer. Platrixa never "
                    "invents a party.",
                    "Name the customer, e.g. 'Sold goods to Ram for "
                    "Rs.10,000.'",
                    discrepancy)
            discrepancy["case"] = "omitted_sale"
            journal = _journal(
                [_line(party, amount, "debit",
                       f"{party} bought goods on credit - the customer's "
                       "balance is raised.")],
                [_line("Sales", amount, "credit",
                       "Goods sold on credit - income recognised.")],
                f"Being the credit sale of goods worth Rs.{_fmt_amt(amount)} "
                f"to {party}, omitted from the books.")
        elif "for cash" in low or "by cash" in low:
            discrepancy["case"] = "omitted_sale"
            journal = _journal(
                [_line("Cash", amount, "debit",
                       "Cash received for the goods.")],
                [_line("Sales", amount, "credit",
                       "Goods sold for cash - income recognised.")],
                f"Being the cash sale of goods worth Rs.{_fmt_amt(amount)}, "
                "omitted from the books.")
        elif "by cheque" in low:
            discrepancy["case"] = "omitted_sale"
            journal = _journal(
                [_line("Bank", amount, "debit",
                       "Cheque received for the goods.")],
                [_line("Sales", amount, "credit",                        "Goods sold - income recognised.")],
                f"Being the sale of goods worth Rs.{_fmt_amt(amount)} by "
                "cheque, omitted from the books.")
        else:
            return _refusal(
                "REVIEW_REQUIRED",
                "The omitted sale does not state whether it was for cash or "
                "on credit (and to whom). Platrixa never guesses the mode.",
                "Add 'for cash' or 'on credit to <name>' to the description.",
                discrepancy)
        discrepancy["notes"].append(
            "The transaction type, accounts and amount are all "
            "deterministically established from the wording - the missing "
            "canonical effect is generated exactly once.")
        return _compose(
            [journal], discrepancy,
            "Record the omitted entry so the books are complete.")

    # -- expenses -----------------------------------------------------------
    if re.search(r"\bpaid\b[^.;]{0,50}?\b(?:rent|salary|salaries|wages|"
                 r"insurance|electricity|advertisement|stationery|postage|"
                 r"repairs|conveyance|telephone|printing|carriage|audit|"
                 r"legal|income tax|fuel|office)\b", low):
        expense = _expense_account_in(low)
        if expense is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The omitted expense does not name a registered expense "
                "account. Platrixa never invents an expense.",
                "Name the expense (rent, salaries, insurance, electricity, "
                "...).",
                discrepancy)
        from backend.maths.fyjc_bk_reasoning import _resolve_cash_bank
        cash_or_bank = _resolve_cash_bank(text)
        counter = "Bank" if cash_or_bank == "Bank" else "Cash"
        discrepancy["case"] = "omitted_expense"
        journal = _journal(
            [_line(expense, amount, "debit",
                   f"The {expense} expense was omitted - it must be "
                   "recorded.")],
            [_line(counter, amount, "credit",
                   f"Payment of {_fmt_amt(amount)} for the expense.")],
            f"Being the {expense.lower()} of Rs.{_fmt_amt(amount)}, omitted "
            "from the books.")
        discrepancy["notes"].append(
            "The transaction type, accounts and amount are all "
            "deterministically established from the wording - the missing "
            "canonical effect is generated exactly once.")
        return _compose(
            [journal], discrepancy,
            "Record the omitted entry so the books are complete.")

    # -- not determinable ---------------------------------------------------
    return _refusal(
        "REVIEW_REQUIRED",
        ("The omitted transaction's type and accounts cannot be "
         "deterministically established from the wording (Platrixa cannot tell "
         "what was purchased/sold/returned or from/to whom). It never "
         "guesses the missing entry."),
        "State the omitted transaction fully, e.g. 'Purchased goods from "
        "Rahul for Rs.20,000 were omitted from the books.'",
        discrepancy)


# ---------------------------------------------------------------------------
# Rectification (Sprint 15I-DISC section 5)
# ---------------------------------------------------------------------------


def _rectification_model(recorded: List[Dict[str, Any]],
                         should: List[Dict[str, Any]],
                         correction: List[Dict[str, Any]],
                         suspense_used: bool) -> Dict[str, Any]:
    return {
        "recorded": recorded,
        "should": should,
        "correction": correction,
        "suspense_used": suspense_used,
    }


def _underlying_accounts(text: str, low: str
                         ) -> Optional[Tuple[str, str, str]]:
    """(debit_account, credit_account, kind) of the underlying fully
    stated transaction ('purchased from X' -> Purchases / X; 'sold to X'
    -> X / Sales), or None when the transaction cannot be identified."""
    from backend.maths.fyjc_bk_reasoning import (
        _purchase_direction_in,
        _sale_direction_in,
    )
    party = _party_from_text(text)
    if _purchase_direction_in(low) and party:
        return ("Purchases", party, "purchase")
    if _sale_direction_in(low) and party:
        return (party, "Sales", "sale")
    return None


def _resolve_rectification(text: str) -> Dict[str, Any]:
    low = " " + text.lower() + " "
    discrepancy: Dict[str, Any] = {
        "authority": "discrepancy-authority",
        "topic": TOPIC_RECTIFICATION,
        "case": "rectification",
        "reconciliation": [],
        "correction_model": None,
        "history": None,
        "notes": [],
        "invented_history": False,
        "duplicate_correction": False,
    }
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(text)
    suspense_mentioned = bool(re.search(r"\bsuspense\b", low))

    # -- A. Suspense with an explicitly established TB discrepancy ----------
    if _TRIAL_BALANCE_DIFFERENCE_RE.search(low):
        m = _UNDERCAST_OVERCAST_RE.search(low)
        if not m:
            return _refusal(
                "REVIEW_REQUIRED",
                ("A trial-balance discrepancy is stated, but Platrixa only "
                 "rectifies a book-total error it can read deterministically "
                 "(an undercast/overcast Sales or Purchases book). It never "
                 "invents the error."),
                "State the book-total error, e.g. 'The Sales book was "
                "undercast by Rs.500.'",
                discrepancy)
        book, direction, amount_str = m.group(1), m.group(2), m.group(3)
        amount = _dec(amount_str)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The undercast/overcast amount could not be read. Platrixa never "
                "invents it.",
                "State the amount of the book-total error.",
                discrepancy)
        discrepancy["case"] = "suspense_book_total"
        is_sales = book.startswith("sales") or book == "sale"
        under = direction == "under"
        if is_sales:
            if under:
                journal = _journal(
                    [_line("Suspense", amount, "debit",
                           "The Sales book was undercast - the credit to "
                           "Sales is short by this amount.")],
                    [_line("Sales", amount, "credit",
                           "Credit Sales with the shortfall discovered "
                           "through the trial-balance difference.")],
                    f"Being the rectification of the Sales book undercast by "
                    f"Rs.{_fmt_amt(amount)} (the trial balance did not "
                    "tally).")
            else:
                journal = _journal(
                    [_line("Sales", amount, "debit",
                           "The Sales book was overcast - Sales was credited "
                           "too much.")],
                    [_line("Suspense", amount, "credit",
                           "The excess credit is removed through the "
                           "trial-balance difference.")],
                    f"Being the rectification of the Sales book overcast by "
                    f"Rs.{_fmt_amt(amount)} (the trial balance did not "
                    "tally).")
        else:
            if under:
                journal = _journal(
                    [_line("Purchases", amount, "debit",
                           "The Purchases book was undercast - the debit to "
                           "Purchases is short by this amount.")],
                    [_line("Suspense", amount, "credit",
                           "The shortfall debit is completed through the "
                           "trial-balance difference.")],
                    f"Being the rectification of the Purchases book undercast "
                    f"by Rs.{_fmt_amt(amount)} (the trial balance did not "
                    "tally).")
            else:
                journal = _journal(
                    [_line("Suspense", amount, "debit",
                           "The Purchases book was overcast - Purchases was "
                           "debited too much.")],
                    [_line("Purchases", amount, "credit",
                           "Remove the excess debit through the "
                           "trial-balance difference.")],
                    f"Being the rectification of the Purchases book overcast "
                    f"by Rs.{_fmt_amt(amount)} (the trial balance did not "
                    "tally).")
        discrepancy["correction_model"] = _rectification_model(
            recorded=[], should=[], correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=True)
        discrepancy["notes"].append(
            "Suspense is used because the trial-balance discrepancy is "
            "explicitly stated - the error surfaced only through the "
            "difference.")
        return _compose(
            [journal], discrepancy,
            "Post the rectification entry; the Suspense account now carries "
            "the stated difference.")

    # -- B. wrong account (explicit 'credited/debited X instead of Y') ------
    m = _EXPLICIT_WRONG_ACCOUNT_RE.search(low)
    if m:
        wrong_account = m.group(1).strip().rstrip(".;,")
        right_account = m.group(2).strip().rstrip(".;,")
        from backend.maths.fyjc_bk_reasoning import _normalise_party_token
        wrong_account = _normalise_party_token(wrong_account) or wrong_account
        right_account = _normalise_party_token(right_account) or right_account
        amount = _single_amount(text)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The wrong-account error has no single stated amount. Platrixa "
                "never invents the amount.",
                "State the amount of the wrongly-posted entry.",
                discrepancy)
        clause = low[m.start():m.end()]
        credited_wrong = "credited" in clause
        if credited_wrong:
            journal = _journal(
                [_line(wrong_account, amount, "debit",
                       f"{wrong_account} was credited instead of "
                       f"{right_account} - remove the wrong credit.")],
                [_line(right_account, amount, "credit",
                       f"Post the credit to the correct account "
                       f"{right_account}.")],
                f"Being the rectification of the wrong credit to "
                f"{wrong_account} instead of {right_account} - "
                f"Rs.{_fmt_amt(amount)}.")
        else:
            journal = _journal(
                [_line(right_account, amount, "debit",
                       f"Post the debit to the correct account "
                       f"{right_account}.")],
                [_line(wrong_account, amount, "credit",
                       f"{wrong_account} was debited instead of "
                       f"{right_account} - remove the wrong debit.")],
                f"Being the rectification of the wrong debit to "
                f"{wrong_account} instead of {right_account} - "
                f"Rs.{_fmt_amt(amount)}.")
        discrepancy["case"] = "wrong_account"
        discrepancy["correction_model"] = _rectification_model(
            recorded=[{"account": wrong_account, "side":
                       "credit" if credited_wrong else "debit",
                       "amount": str(amount)}],
            should=[{"account": right_account, "side":
                     "credit" if credited_wrong else "debit",
                     "amount": str(amount)}],
            correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=False)
        if suspense_mentioned:
            discrepancy["notes"].append(
                "Suspense is NOT used: the wrong and correct accounts are "
                "both known, so the correction is posted directly.")
        return _compose(
            [journal], discrepancy,
            "Post the transfer entry; the wrong account is cleared and the "
            "correct account carries the balance.")

    # -- C. wrong account (implicit: 'posted to X's account instead') -------
    m = _IMPLICIT_WRONG_ACCOUNT_RE.search(low)
    if m:
        wrong_account = m.group(1).strip().rstrip(".;,")
        from backend.maths.fyjc_bk_reasoning import _normalise_party_token
        wrong_account = (_normalise_party_token(wrong_account)
                         or wrong_account)
        underlying = _underlying_accounts(text, low)
        if underlying is None:
            return _refusal(
                "REVIEW_REQUIRED",
                ("The rectification says an entry was posted to the wrong "
                 "account, but the correct account cannot be derived from "
                 "the transaction wording. Platrixa never guesses which account "
                 "was meant."),
                "Name the correct account explicitly (e.g. '... instead of "
                "Rahul').",
                discrepancy)
        debit_account, credit_account, _kind = underlying
        amount = _single_amount(text)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The wrong-account error has no single stated amount. Platrixa "
                "never invents the amount.",
                "State the amount of the wrongly-posted entry.",
                discrepancy)
        # the wrong posting sat on the CREDIT side of a purchase (the
        # supplier credit) or the DEBIT side of a sale (the customer debit).
        if _kind == "purchase":
            journal = _journal(
                [_line(wrong_account, amount, "debit",
                       f"{wrong_account} was credited instead of "
                       f"{credit_account} - remove the wrong credit.")],
                [_line(credit_account, amount, "credit",
                       f"Post the credit to the correct account "
                       f"{credit_account}.")],
                f"Being the rectification of the wrong credit to "
                f"{wrong_account} instead of {credit_account} - "
                f"Rs.{_fmt_amt(amount)}.")
            recorded_side, should_side = "credit", "credit"
        else:
            journal = _journal(
                [_line(debit_account, amount, "debit",
                       f"Post the debit to the correct account "
                       f"{debit_account}.")],
                [_line(wrong_account, amount, "credit",
                       f"{wrong_account} was debited instead of "
                       f"{debit_account} - remove the wrong debit.")],
                f"Being the rectification of the wrong debit to "
                f"{wrong_account} instead of {debit_account} - "
                f"Rs.{_fmt_amt(amount)}.")
            recorded_side, should_side = "debit", "debit"
        discrepancy["case"] = "wrong_account"
        discrepancy["correction_model"] = _rectification_model(
            recorded=[{"account": wrong_account, "side": recorded_side,
                       "amount": str(amount)}],
            should=[{"account": (credit_account if _kind == "purchase"
                                 else debit_account), "side": should_side,
                     "amount": str(amount)}],
            correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=False)
        if suspense_mentioned:
            discrepancy["notes"].append(
                "Suspense is NOT used: the wrong and correct accounts are "
                "both known, so the correction is posted directly.")
        return _compose(
            [journal], discrepancy,
            "Post the transfer entry; the wrong account is cleared and the "
            "correct account carries the balance.")

    # -- D. wrong side (sale for cash / purchase for cash shapes) -----------
    m = _WRONG_SIDE_SALE_RE.search(low)
    if m:
        party = m.group(1).strip().rstrip(".;,")
        from backend.maths.fyjc_bk_reasoning import _normalise_party_token
        party = _normalise_party_token(party) or party
        amount = _single_amount(text)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The wrong-side error has no single stated amount. Platrixa "
                "never invents the amount.",
                "State the amount of the wrongly-posted entry.",
                discrepancy)
        discrepancy["case"] = "wrong_side"
        journal = _journal(
            [_line("Cash", amount, "debit",
                   "Cash sale proceeds - the correct debit is Cash.")],
            [_line(party, amount, "credit",
                   f"{party} was wrongly debited for a cash sale - remove "
                   "the wrong debit.")],
            f"Being the rectification of the wrong debit to {party} for the "
            f"cash sale of Rs.{_fmt_amt(amount)} (the correct debit is "
            "Cash).")
        discrepancy["correction_model"] = _rectification_model(
            recorded=[{"account": party, "side": "debit",
                       "amount": str(amount)},
                      {"account": "Sales", "side": "credit",
                       "amount": str(amount)}],
            should=[{"account": "Cash", "side": "debit",
                     "amount": str(amount)},
                    {"account": "Sales", "side": "credit",
                     "amount": str(amount)}],
            correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=False)
        return _compose(
            [journal], discrepancy,
            "Post the correction; Cash is debited and the wrongly-debited "
            "party is cleared.")

    m = _WRONG_SIDE_PURCHASE_RE.search(low)
    if m:
        party = m.group(1).strip().rstrip(".;,")
        from backend.maths.fyjc_bk_reasoning import _normalise_party_token
        party = _normalise_party_token(party) or party
        amount = _single_amount(text)
        if amount is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The wrong-side error has no single stated amount. Platrixa "
                "never invents the amount.",
                "State the amount of the wrongly-posted entry.",
                discrepancy)
        discrepancy["case"] = "wrong_side"
        journal = _journal(
            [_line("Purchases", amount, "debit",
                   "Cash purchase - the correct debit is Purchases."),
             _line(party, amount, "debit",
                   f"{party} was wrongly credited for a cash purchase - "
                   "remove the wrong credit.")],
            [_line("Cash", amount * Decimal(2), "credit",
                   "Reverse the wrong Cash debit and post the correct Cash "
                   "credit for the cash purchase.")],
            f"Being the rectification of the wrong credit to {party} for "
            f"the cash purchase of Rs.{_fmt_amt(amount)} (the correct debit "
            "is Purchases).")
        discrepancy["correction_model"] = _rectification_model(
            recorded=[{"account": "Cash", "side": "debit",
                       "amount": str(amount)},
                      {"account": party, "side": "credit",
                       "amount": str(amount)}],
            should=[{"account": "Purchases", "side": "debit",
                     "amount": str(amount)},
                    {"account": "Cash", "side": "credit",
                     "amount": str(amount)}],
            correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=False)
        return _compose(
            [journal], discrepancy,
            "Post the correction; Purchases is debited, the wrongly-credited "
            "party is cleared, and Cash reflects both the reversal and the "
            "correct payment.")

    # -- E. wrong amount / partial omission ---------------------------------
    m = _RECORDED_AMOUNT_RE.search(low)
    if m:
        recorded = _dec(m.group(1))
        if recorded is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The recorded amount could not be read. Platrixa never invents "
                "it.",
                "State the recorded amount explicitly.",
                discrepancy)
        others = [a for a in amounts if a != recorded]
        if len(others) != 1:
            return _refusal(
                "REVIEW_REQUIRED",
                "Platrixa cannot tell which stated amount is the correct one. "
                "It never guesses the correct amount.",
                "State the recorded amount and the correct amount "
                "explicitly.",
                discrepancy)
        correct = others[0]
        underlying = _underlying_accounts(text, low)
        if underlying is None:
            return _refusal(
                "REVIEW_REQUIRED",
                "The wrong-amount entry's accounts cannot be derived from "
                "the transaction wording. Platrixa never invents the accounts.",
                "State the transaction fully (e.g. 'Purchased goods from "
                "Rahul for Rs.20,000 ...').",
                discrepancy)
        debit_account, credit_account, kind = underlying
        difference = abs(correct - recorded)
        if correct > recorded:
            journal = _journal(
                [_line(debit_account, difference, "debit",
                       f"The entry was recorded short by "
                       f"Rs.{_fmt_amt(difference)} - complete the debit.")],
                [_line(credit_account, difference, "credit",
                       f"Complete the credit with the shortfall of "
                       f"Rs.{_fmt_amt(difference)}.")],
                f"Being the rectification of the short-recorded entry of "
                f"Rs.{_fmt_amt(recorded)} instead of "
                f"Rs.{_fmt_amt(correct)} - the shortfall of "
                f"Rs.{_fmt_amt(difference)} is posted.")
        else:
            journal = _journal(
                [_line(credit_account, difference, "debit",
                       f"The entry was recorded high by "
                       f"Rs.{_fmt_amt(difference)} - reverse the excess "
                       f"credit.")],
                [_line(debit_account, difference, "credit",
                       f"Reverse the excess debit of "
                       f"Rs.{_fmt_amt(difference)}.")],
                f"Being the rectification of the over-recorded entry of "
                f"Rs.{_fmt_amt(recorded)} instead of "
                f"Rs.{_fmt_amt(correct)} - the excess of "
                f"Rs.{_fmt_amt(difference)} is reversed.")
        discrepancy["case"] = ("partial_omission"
                               if _PARTIAL_RECORD_RE.search(low)
                               else "wrong_amount")
        discrepancy["correction_model"] = _rectification_model(
            recorded=[{"account": debit_account, "side": "debit",
                       "amount": str(recorded)},
                      {"account": credit_account, "side": "credit",
                       "amount": str(recorded)}],
            should=[{"account": debit_account, "side": "debit",
                     "amount": str(correct)},
                    {"account": credit_account, "side": "credit",
                     "amount": str(correct)}],
            correction=[
                {"account": l["account"], "side": l["side"],
                 "amount": str(l["amount"])}
                for l in (journal.get("debit_lines") or [])
                + (journal.get("credit_lines") or [])
            ],
            suspense_used=False)
        return _compose(
            [journal], discrepancy,
            "Post the difference; the entry now stands at the correct "
            "amount.")

    # -- F. no error established --------------------------------------------
    return _refusal(
        "REVIEW_REQUIRED",
        ("This is a rectification question, but no error is established by "
         "the wording (no wrongly-posted account, no wrong amount, no wrong "
         "side, no omission, and no stated trial-balance discrepancy). Platrixa "
         "never invents an error to rectify."),
        "Describe the error explicitly - what was recorded, what should "
        "have been recorded, and (only when established) the trial-balance "
        "difference.",
        discrepancy)


# ---------------------------------------------------------------------------
# Topic resolution (combination rules)
# ---------------------------------------------------------------------------


def _resolve(topics: List[str], text: str) -> Dict[str, Any]:
    topic_set = set(topics)

    # dishonour (optionally in a BRS context)
    if TOPIC_DISHONOUR in topic_set and topic_set <= {
            TOPIC_DISHONOUR, TOPIC_BRS}:
        return _resolve_dishonour(text, brs_context=TOPIC_BRS in topic_set)

    # BRS alone
    if topic_set == {TOPIC_BRS}:
        return _resolve_brs(text)

    # omission (optionally framed as a rectification)
    if TOPIC_OMISSION in topic_set and topic_set <= {
            TOPIC_OMISSION, TOPIC_RECTIFICATION}:
        if TOPIC_RECTIFICATION in topic_set \
                and _PARTIAL_RECORD_RE.search(" " + text.lower() + " "):
            # a partial omission is a rectification of the recorded part
            return _resolve_rectification(text)
        return _resolve_omission(text)

    # rectification alone
    if topic_set == {TOPIC_RECTIFICATION}:
        return _resolve_rectification(text)

    # any other combination leaves several interpretations open
    return _refusal(
        "REVIEW_REQUIRED",
        ("This question combines several discrepancy topics ("
         + ", ".join(sorted(topic_set))
         + "). Platrixa cannot deterministically pick one treatment without "
         "dropping or re-interpreting a stated fact - it never guesses."),
        "Enter each discrepancy correction separately.",
        {"authority": "discrepancy-authority", "topic": sorted(topic_set),
         "case": "combined_topics", "reconciliation": [],
         "correction_model": None, "history": None, "notes": [],
         "invented_history": False, "duplicate_correction": False})


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------


def discrepancy_outcome(question: str,
                        amount: Any = None) -> Dict[str, Any]:
    """Resolve ONE discrepancy-routed question deterministically.

    Pipeline: raw input -> 15I-VY normalization -> safety concerns ->
    global math contradiction validation -> topic resolution -> verify
    accounting consistency -> canonical result. The SAME gates the
    hardened authority applies run FIRST, so the Discrepancy Authority
    never bypasses or weakens a 15I-VY refusal.
    """
    from backend.maths.fyjc_normalization import (
        INVALID_INPUT_MATH,
        normalize_fyjc_text,
        vy_harden,
    )
    raw = str(question or "")
    norm = normalize_fyjc_text(raw)
    text = norm.text

    # 15I-VY party/abbreviation safety: identity must be established
    # before ANY discrepancy resolution (never a guessed party).
    if norm.concerns:
        result = _refusal(
            "REVIEW_REQUIRED",
            norm.concerns[0],
            "Replace the abbreviation or initial with its full meaning and "
            "re-type the transaction.")
        result["normalization"] = norm.provenance
        return result

    # 15I-VY global mathematical contradiction: a contradictory question
    # is INVALID_INPUT_MATH before any authority runs.
    from backend.maths.fyjc_normalization import math_contradiction
    contradiction = math_contradiction(text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "\U0001f534 INVALID INPUT (MATH)"
        return contradiction

    detected = detect_discrepancy(text)
    if detected is None:
        # should never happen (the orchestrator routes before calling) -
        # fall back to the hardened boundary rather than guess.
        fallback = vy_harden(text, amount)
        fallback["normalization"] = norm.provenance
        return fallback

    result = _resolve(detected["topics"], text)
    result["normalization"] = norm.provenance

    # final balancing backstop: a VERIFIED discrepancy result must balance.
    if result.get("status") == "VERIFIED":
        debit_lines = result.get("debit_lines") or []
        credit_lines = result.get("credit_lines") or []
        if sum((_dec(l["amount"]) or Decimal(0)
                for l in debit_lines), Decimal(0)) != sum(
                    (_dec(l["amount"]) or Decimal(0)
                     for l in credit_lines), Decimal(0)):
            return _refusal(
                "REVIEW_REQUIRED",
                "The resolved discrepancy journal does not balance. Platrixa "
                "never reports an unbalanced correction as verified.",
                "Re-check the stated amounts and re-type the question.",
                result.get("discrepancy"))
    return result
