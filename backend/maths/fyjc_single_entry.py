"""
Platrixa
Sprint 15I-SPEC - Specialized Accounting Authorities
backend/maths/fyjc_single_entry.py

The Single Entry / Incomplete Records Authority: deterministic FYJC
profit-from-change-in-net-worth mathematics.

    normalized input -> contradiction validation -> capital facts ->
    net-worth relationship -> verified mathematical result

The authority computes the deterministic relationship

    Profit = Closing Capital + Drawings - Fresh Capital - Opening Capital

(and its inverses) WITHOUT forcing the answer through the double-entry
journal balancing requirement - the accounting topic itself does not
require journal entries, so a VERIFIED mathematical result is returned
with zero journal lines.

Boundaries (Sprint 15I-SPEC Part C):
  * recognition: 'single entry', 'incomplete records', 'statement of
    affairs', or BOTH opening and closing capital stated (the net-worth
    movement pattern). A lone 'drawings' / 'withdrew' transaction is
    NEVER routed here (it stays an ordinary transaction);
  * exactly ONE unknown variable is solved; two or more unknowns refuse
    with REVIEW_REQUIRED - Platrixa never invents a value;
  * a stated profit that contradicts the computed net-worth movement is
    INVALID_INPUT_MATH (a deterministic input error, zero journal
    lines).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

TOPIC_SINGLE_ENTRY = "single_entry"

# ---------------------------------------------------------------------------
# Recognition (routing decision only)
# ---------------------------------------------------------------------------

_SINGLE_ENTRY_RE = re.compile(
    r"\bsingle\s+entry\b|\bincomplete\s+records\b|\bstatement\s+of\s+"
    r"affairs\b", re.IGNORECASE)

_OPENING_CAPITAL_RE = re.compile(
    r"\bopening\s+capital\b|\bcapital\s+(?:at|in|as\s+at|as\s+on)\s+the\s+"
    r"(?:beginning|start|commencement)\b", re.IGNORECASE)

_CLOSING_CAPITAL_RE = re.compile(
    r"\bclosing\s+capital\b|\bcapital\s+(?:at|in|as\s+at|as\s+on)\s+the\s+"
    r"(?:end|close)\b", re.IGNORECASE)


def detect_single_entry(text: str) -> Optional[Dict[str, Any]]:
    """Return the single-entry topic when the question is a genuine
    single-entry / incomplete-records question, else None. Routing
    decision only."""
    raw = str(text or "")
    low = " " + raw.lower() + " "
    if _SINGLE_ENTRY_RE.search(low):
        return {"topics": [TOPIC_SINGLE_ENTRY], "text": raw}
    if _OPENING_CAPITAL_RE.search(low) and _CLOSING_CAPITAL_RE.search(low):
        return {"topics": [TOPIC_SINGLE_ENTRY], "text": raw}
    return None


# ---------------------------------------------------------------------------
# Fact vocabulary
# ---------------------------------------------------------------------------

_DRAWINGS_RE = re.compile(
    r"\bdrawings?\b|\bwithdrew\b|\bwithdrawn\b|\bamount\s+withdrawn\b",
    re.IGNORECASE)

_FRESH_CAPITAL_RE = re.compile(
    r"\bfresh\s+capital\b|\badditional\s+capital\b|\bcapital\s+"
    r"introduced\b|\bintroduced\s+(?:as\s+)?capital\b|\bextra\s+capital\b|"
    r"\bfurther\s+capital\b", re.IGNORECASE)

_PROFIT_RE = re.compile(r"\bprofit\b", re.IGNORECASE)
_LOSS_RE = re.compile(r"\bloss\b", re.IGNORECASE)

_ASSETS_LIABILITIES_RE = re.compile(
    r"\bassets?\b|\bliabilit(?:y|ies)\b", re.IGNORECASE)

# opening-period signal words (used to assign a lone statement of
# affairs to the opening capital)
_OPENING_PERIOD_RE = re.compile(
    r"\b(?:opening|beginning|start|commencement)\b|\b(?:1|01|1st)\s*"
    r"(?:st|nd|rd|th)?\s*(?:april|apr)\b|\b(?:as\s+on|as\s+at)\s+"
    r"(?:1|01|1st)\b", re.IGNORECASE)

_CLOSING_PERIOD_RE = re.compile(
    r"\b(?:closing|end|close)\b|\b(?:31|31st)\s*(?:st|nd|rd|th)?\s*"
    r"(?:march|mar)\b", re.IGNORECASE)


def _dec(value: Any) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _fmt_amt(value: Any) -> str:
    from backend.maths.fyjc_bk_reasoning import _fmt_amt as engine_fmt
    return engine_fmt(value)


def _refusal(status: str, why_not: str, next_action: str,
             single_entry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import _refusal as engine_refusal
    result = engine_refusal(status, why_not, next_action)
    if single_entry:
        result["single_entry"] = single_entry
    return result


def _amount_after(low: str, keyword: str, max_gap: int = 40) -> Optional[Decimal]:
    """The FIRST money amount after a keyword occurrence, within the
    same clause (cut at sentence punctuation so a number is never
    truncated at a fixed window edge). 'profit was Rs.X' never steals
    the fresh-capital figure before it."""
    for m in re.finditer(keyword, low):
        value = _amount_after_pos(low, m.end(), max_gap)
        if value is not None:
            return value
    return None


def _amount_before(low: str, keyword: str, max_gap: int = 44) -> Optional[Decimal]:
    """The LAST money amount BEFORE a keyword occurrence, within the
    same clause (the amount that the keyword labels)."""
    for m in re.finditer(keyword, low):
        head = low[max(0, m.start() - max_gap):m.start()]
        cut = re.search(r"(?<!rs)\.|;", head)
        if cut:
            head = head[cut.start() + 1:]
        ams = list(re.finditer(
            r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)", head))
        if ams:
            return _dec(ams[-1].group(1))
    return None


def _amount_after_pos(low: str, pos: int, max_gap: int = 40) -> Optional[Decimal]:
    """The FIRST money amount AFTER an exact character position, within
    the same clause - the per-occurrence form of `_amount_after` (each
    'assets' / 'liabilities' / 'profit' occurrence gets ITS OWN amount)."""
    tail = low[pos:pos + max_gap]
    cut = re.search(r"(?<!rs)\.|;", tail)
    if cut:
        tail = tail[:cut.start()]
    for am in re.finditer(r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                          tail):
        # a bare digit that is a fraction/ratio part ('1/2', '3:2') is
        # never a money amount.
        token = am.group(0).lstrip()
        if not token.lower().startswith(("rs", "\u20b9", "inr")):
            before = tail[am.start() - 1] if am.start() > 0 else " "
            after = tail[am.end()] if am.end() < len(tail) else " "
            if before in "/:" or after in "/:":
                continue
        return _dec(am.group(1))
    return None


def _profit_figure(low: str) -> Optional[Decimal]:
    """The stated profit/loss figure, signed by the keyword that labels
    it ('loss Rs.X' -> negative). None when no figure is stated after a
    profit/loss keyword."""
    for m in re.finditer(r"\b(?:profit|loss)\b", low):
        tail = low[m.end():m.end() + 40]
        cut = re.search(r"(?<!rs)\.|;", tail)
        if cut:
            tail = tail[:cut.start()]
        am = re.search(r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                       tail)
        if am:
            value = _dec(am.group(1))
            if value is None:
                continue
            return -value if m.group(0) == "loss" else value
    return None


def _money_amounts(low: str) -> List[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(low)
    return amounts


# ---------------------------------------------------------------------------
# Single-entry facts
# ---------------------------------------------------------------------------

def _single_entry_facts(text: str) -> Dict[str, Any]:
    low = " " + str(text or "").lower() + " "
    facts: Dict[str, Any] = {
        "opening_capital": None,
        "closing_capital": None,
        "drawings": None,
        "fresh_capital": None,
        "profit_stated": None,
        "asks_loss": False,
        "statement_of_affairs": False,
        "assets": [],           # List[Decimal] in order of appearance
        "liabilities": [],      # List[Decimal] in order of appearance
        "used_amounts": [],
        "ambiguous": [],
    }

    facts["statement_of_affairs"] = (
        re.search(r"\bstatement\s+of\s+affairs\b", low) is not None)

    # -- capital values -------------------------------------------------------
    opening = _amount_after(
        low, r"\bopening\s+capital\b|\bcapital\s+(?:at|in|as\s+at|as\s+on)"
             r"\s+the\s+(?:beginning|start|commencement)\b")
    if opening is not None:
        facts["opening_capital"] = opening
        facts["used_amounts"].append(opening)
    closing = _amount_after(
        low, r"\bclosing\s+capital\b|\bcapital\s+(?:at|in|as\s+at|as\s+on)"
             r"\s+the\s+(?:end|close)\b")
    if closing is not None:
        facts["closing_capital"] = closing
        facts["used_amounts"].append(closing)

    # -- drawings --------------------------------------------------------------
    if _DRAWINGS_RE.search(low):
        drawings = _amount_after(
            low, r"\bdrawings?\b|\bwithdrew\b|\bwithdrawn\b|"
                 r"\bamount\s+withdrawn\b")
        if drawings is not None and drawings not in facts["used_amounts"]:
            facts["drawings"] = drawings
            facts["used_amounts"].append(drawings)

    # -- fresh / additional capital ---------------------------------------------
    # 'no fresh capital' / 'without any fresh capital' is a deterministic
    # ZERO, not a missing value - never a guess.
    if re.search(r"\b(?:no|nil|zero|without|not\s+introduc\w*)\s+"
                 r"(?:any\s+)?(?:fresh|additional|extra|further)\s+capital\b",
                 low):
        facts["fresh_capital"] = Decimal(0)
        facts["used_amounts"].append(Decimal(0))
    elif _FRESH_CAPITAL_RE.search(low):
        fresh = _amount_after(
            low, r"\bfresh\s+capital\b|\badditional\s+capital\b|\bcapital"
                 r"\s+introduced\b|\bintroduced\s+(?:as\s+)?capital\b|"
                 r"\bextra\s+capital\b|\bfurther\s+capital\b")
        if fresh is not None and fresh not in facts["used_amounts"]:
            facts["fresh_capital"] = fresh
            facts["used_amounts"].append(fresh)

    # -- stated profit / loss -----------------------------------------------------
    profit = _profit_figure(low)
    if profit is not None and abs(profit) not in facts["used_amounts"]:
        facts["profit_stated"] = profit
        facts["used_amounts"].append(abs(profit))

    # a STRICT loss ask ('find the loss', with no profit in between) -
    # 'find the profit or loss' is a neutral ask, never a strict one.
    if re.search(r"\b(?:find|calculate|compute|determine|work\s+out|"
                 r"ascertain)\b(?:(?!\bprofit\b)[^.;]){0,60}?\b"
                 r"(?:the\s+)?loss\b", low):
        facts["asks_loss"] = True

    # -- statement of affairs: assets / liabilities --------------------------------
    if facts["statement_of_affairs"] or _ASSETS_LIABILITIES_RE.search(low):
        # collect (assets, liabilities) pairs in order of appearance -
        # each value is anchored to ITS OWN keyword occurrence
        # ('assets of Rs.80,000' / 'liabilities were Rs.30,000'), never
        # the character-nearest number.
        asset_values: List[Decimal] = []
        liability_values: List[Decimal] = []
        for m in re.finditer(r"\bassets?\b", low):
            value = _amount_after_pos(low, m.end())
            if value is None:
                value = _amount_before(low, r"\bassets?\b")
            if value is not None and value not in facts["used_amounts"]:
                asset_values.append(value)
                facts["used_amounts"].append(value)
        for m in re.finditer(r"\bliabilit(?:y|ies)\b", low):
            value = _amount_after_pos(low, m.end())
            if value is None:
                value = _amount_before(low, r"\bliabilit(?:y|ies)\b")
            if value is not None and value not in facts["used_amounts"]:
                liability_values.append(value)
                facts["used_amounts"].append(value)
        facts["assets"] = asset_values
        facts["liabilities"] = liability_values

    # -- leftover amounts ----------------------------------------------------------
    for amount in _money_amounts(low):
        if amount not in facts["used_amounts"]:
            facts["ambiguous"].append(
                f"amount Rs.{_fmt_amt(amount)} has no single-entry role")
    return facts


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_single_entry(text: str) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import (
        INVALID_INPUT_MATH,
        REVIEW_REQUIRED,
    )

    facts = _single_entry_facts(text)
    low = " " + str(text or "").lower() + " "
    calculations: List[Dict[str, Any]] = []

    def record(kind: str, label: str, value: Any,
               formula: Optional[str] = None) -> None:
        calculations.append({
            "kind": kind,
            "label": label,
            "value": str(value),
            "formula": formula,
        })

    def refusal(why: str, status: str = REVIEW_REQUIRED) -> Dict[str, Any]:
        payload = {
            "authority": "single-entry-authority",
            "topic": TOPIC_SINGLE_ENTRY,
            "variables": {
                "opening_capital": facts.get("opening_capital"),
                "closing_capital": facts.get("closing_capital"),
                "drawings": facts.get("drawings"),
                "fresh_capital": facts.get("fresh_capital"),
            },
            "notes": [],
            "calculations": calculations,
            "invented_history": False,
            "unresolved": facts.get("ambiguous"),
        }
        return _refusal(status, why,
                        "State the opening capital, closing capital, "
                        "drawings and fresh capital (or the figure you "
                        "know and the one you want) and re-type the "
                        "question.",
                        payload)

    if facts["ambiguous"]:
        return refusal("; ".join(facts["ambiguous"]))

    opening = facts["opening_capital"]
    closing = facts["closing_capital"]
    drawings = facts["drawings"]
    fresh = facts["fresh_capital"]
    profit_stated = facts["profit_stated"]

    # -- statement of affairs: capital = assets - liabilities ---------------------
    assets = facts["assets"]
    liabilities = facts["liabilities"]
    if assets and liabilities:
        if len(assets) == 1 and len(liabilities) == 1:
            capital = assets[0] - liabilities[0]
            # assign to the period signalled by the wording, else to the
            # missing capital variable, else refuse
            if _OPENING_PERIOD_RE.search(low) and not _CLOSING_PERIOD_RE.search(low):
                if opening is None:
                    opening = capital
            elif _CLOSING_PERIOD_RE.search(low) and not _OPENING_PERIOD_RE.search(low):
                if closing is None:
                    closing = capital
            elif opening is None and closing is not None:
                opening = capital
            elif closing is None and opening is not None:
                closing = capital
            else:
                return refusal(
                    "A statement of affairs gives assets and liabilities, "
                    "but the period (opening or closing) cannot be "
                    "determined and both capital figures are already "
                    "stated. Platrixa never guesses which statement applies "
                    "to which period.")
        elif len(assets) == 2 and len(liabilities) == 2:
            if opening is None:
                opening = assets[0] - liabilities[0]
            if closing is None:
                closing = assets[1] - liabilities[1]
        else:
            return refusal(
                "The statement of affairs amounts cannot be paired "
                "deterministically (assets and liabilities must appear "
                "once for each period, or twice in opening-then-closing "
                "order).")
        if opening is not None:
            record("capital_from_statement", "Opening capital "
                   "(assets - liabilities)", opening,
                   f"{_fmt_amt(assets[0])} - {_fmt_amt(liabilities[0])}")
        if closing is not None and len(assets) == 2:
            record("capital_from_statement", "Closing capital "
                   "(assets - liabilities)", closing,
                   f"{_fmt_amt(assets[1])} - {_fmt_amt(liabilities[1])}")

    # -- which variable is the unknown? --------------------------------------------
    known = {k: v for k, v in {
        "opening capital": opening,
        "closing capital": closing,
        "drawings": drawings,
        "fresh capital": fresh,
    }.items() if v is not None}

    if profit_stated is not None:
        # the profit is stated: solve the ONE missing capital variable, or
        # verify the stated profit against the movement
        missing = [k for k in ("opening capital", "closing capital",
                               "drawings", "fresh capital")
                   if k not in known]
        if len(missing) > 1:
            return refusal(
                "The profit is stated but more than one capital value is "
                "missing - Platrixa cannot solve the net-worth relationship "
                "with more than one unknown.")
        if len(missing) == 1:
            target = missing[0]
            oc = opening or Decimal(0)
            cc = closing or Decimal(0)
            d = drawings or Decimal(0)
            fc = fresh or Decimal(0)
            if target == "opening capital":
                value = cc + d - fc - profit_stated
                formula = (f"closing capital + drawings - fresh capital - "
                           f"profit = {_fmt_amt(cc)} + {_fmt_amt(d)} - "
                           f"{_fmt_amt(fc)} - {_fmt_amt(profit_stated)}")
            elif target == "closing capital":
                value = oc + fc + profit_stated - d
                formula = (f"opening capital + fresh capital + profit - "
                           f"drawings = {_fmt_amt(oc)} + {_fmt_amt(fc)} + "
                           f"{_fmt_amt(profit_stated)} - {_fmt_amt(d)}")
            elif target == "drawings":
                value = profit_stated + fc + oc - cc
                formula = (f"profit + fresh capital + opening capital - "
                           f"closing capital = {_fmt_amt(profit_stated)} + "
                           f"{_fmt_amt(fc)} + {_fmt_amt(oc)} - "
                           f"{_fmt_amt(cc)}")
            else:
                value = cc + d - oc - profit_stated
                formula = (f"closing capital + drawings - opening capital "
                           f"- profit = {_fmt_amt(cc)} + {_fmt_amt(d)} - "
                           f"{_fmt_amt(oc)} - {_fmt_amt(profit_stated)}")
            record(f"inverse_{target.replace(' ', '_')}",
                   f"Solved {target} (net-worth relationship inverse)",
                   value, formula)
            return _math_result(facts, calculations, target, value, formula)
        # all four capital values stated: verify the profit
        computed = closing + (drawings or Decimal(0)) - \
            (fresh or Decimal(0)) - opening
        if computed != profit_stated:
            formula = (f"closing capital + drawings - fresh capital - "
                       f"opening capital = {_fmt_amt(closing)} + "
                       f"{_fmt_amt(drawings)} - {_fmt_amt(fresh)} - "
                       f"{_fmt_amt(opening)}")
            record("profit", "Profit from change in net worth", computed,
                   formula)
            payload = {
                "authority": "single-entry-authority",
                "topic": TOPIC_SINGLE_ENTRY,
                "variables": {
                    "opening_capital": opening,
                    "closing_capital": closing,
                    "drawings": drawings,
                    "fresh_capital": fresh,
                    "profit_stated": profit_stated,
                    "profit_computed": computed,
                },
                "formula": formula,
                "result": None,
                "solved_for": "profit",
                "direction": "profit",
                "contradiction": True,
                "notes": [],
                "calculations": calculations,
                "invented_history": False,
            }
            return _refusal(
                INVALID_INPUT_MATH,
                (f"The stated profit Rs.{_fmt_amt(profit_stated)} "
                 f"contradicts the net-worth movement "
                 f"Rs.{_fmt_amt(computed)} (closing capital + drawings - "
                 "fresh capital - opening capital). Platrixa never reports a "
                 "contradictory figure as verified."),
                "Re-check the stated capital, drawings and profit "
                "figures.",
                payload)
        formula = (f"closing capital + drawings - fresh capital - opening "
                   f"capital = {_fmt_amt(closing)} + {_fmt_amt(drawings)} "
                   f"- {_fmt_amt(fresh)} - {_fmt_amt(opening)}")
        record("profit", "Profit from change in net worth (confirmed)",
               profit_stated, formula)
        return _math_result(facts, calculations, "profit", profit_stated,
                            formula, confirmed=True)

    # -- profit is the unknown --------------------------------------------------------
    if len(known) < 4:
        return refusal(
            "The profit is not stated and fewer than four of (opening "
            "capital, closing capital, drawings, fresh capital) are "
            "established - Platrixa cannot solve the net-worth relationship "
            "with more than one unknown.")
    profit = closing + drawings - fresh - opening
    formula = (f"closing capital + drawings - fresh capital - opening "
               f"capital = {_fmt_amt(closing)} + {_fmt_amt(drawings)} - "
               f"{_fmt_amt(fresh)} - {_fmt_amt(opening)}")
    direction = "loss" if profit < 0 else "profit"
    if facts["asks_loss"] and profit > 0:
        record("profit", "Profit from change in net worth", profit, formula)
        payload = {
            "authority": "single-entry-authority",
            "topic": TOPIC_SINGLE_ENTRY,
            "variables": {
                "opening_capital": opening,
                "closing_capital": closing,
                "drawings": drawings,
                "fresh_capital": fresh,
                "profit_computed": profit,
            },
            "formula": formula,
            "result": profit,
            "solved_for": "profit",
            "direction": "profit",
            "contradiction": True,
            "notes": [],
            "calculations": calculations,
            "invented_history": False,
        }
        return _refusal(
            INVALID_INPUT_MATH,
            "The question asks for a loss but the net-worth movement is a "
            "profit - the stated direction contradicts the computed "
            "figure. Platrixa never reports a contradictory figure as "
            "verified.",
            "Re-check the stated capital and drawings figures.",
            payload)
    record("profit", "Profit from change in net worth", profit, formula)
    return _math_result(facts, calculations, "profit", profit, formula,
                        direction=direction)


def _math_result(facts: Dict[str, Any],
                 calculations: List[Dict[str, Any]],
                 solved_for: str,
                 value: Decimal,
                 formula: str,
                 direction: Optional[str] = None,
                 confirmed: bool = False) -> Dict[str, Any]:
    """A VERIFIED mathematical result with ZERO journal lines - the
    single-entry topic does not require a journal entry (Sprint 15I-SPEC
    Part C)."""
    from backend.maths.fyjc_bk_reasoning import STATUS_WORDS
    from backend.maths.status import VERIFIED

    if direction is None:
        direction = "loss" if value < 0 else "profit"
    display = abs(value)

    payload = {
        "authority": "single-entry-authority",
        "topic": TOPIC_SINGLE_ENTRY,
        "variables": {
            "opening_capital": facts.get("opening_capital"),
            "closing_capital": facts.get("closing_capital"),
            "drawings": facts.get("drawings"),
            "fresh_capital": facts.get("fresh_capital"),
            "profit_computed": value,
        },
        "formula": formula,
        "result": value,
        "solved_for": solved_for,
        "direction": direction,
        "confirmed": confirmed,
        "notes": [
            (f"{direction.title()} = closing capital + drawings - fresh "
             f"capital - opening capital"),
            f"{direction.title()} = Rs.{_fmt_amt(display)}",
        ],
        "calculations": calculations,
        "invented_history": False,
    }

    return {
        "status": VERIFIED,
        "status_label": STATUS_WORDS.get(VERIFIED, VERIFIED),
        "resolved": True,
        "understanding": None,
        "journal": None,
        "journals": [],
        "ledger": None,
        "trial_balance": None,
        "verification": {
            "ok": True,
            "message": (f"Net-worth relationship verified: "
                        f"{formula} = Rs.{_fmt_amt(display)} "
                        f"({direction})."),
        },
        "debit_lines": [],
        "credit_lines": [],
        "calculation_records": calculations,
        "why_not": None,
        "next_action": "This is a mathematical result - no journal entry "
                       "is required for the net-worth calculation.",
        "single_entry": payload,
        "audit": {
            "authority": "single-entry-authority",
            "rule_key": None,
            "calculation_ids": [],
            "total_debit": 0,
            "total_credit": 0,
            "topic": TOPIC_SINGLE_ENTRY,
            "case": "change-in-net-worth",
        },
    }


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

def single_entry_outcome(question: str,
                         amount: Any = None) -> Dict[str, Any]:
    """Resolve ONE single-entry / incomplete-records question
    deterministically.

    Pipeline: raw input -> 15I-VY normalization -> safety concerns ->
    global math contradiction validation -> net-worth resolution ->
    canonical result. The SAME gates the hardened authority applies run
    FIRST, so the Single Entry Authority never bypasses or weakens a
    15I-VY refusal.
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

    if norm.concerns:
        result = _refusal(
            "REVIEW_REQUIRED",
            norm.concerns[0],
            "Replace the abbreviation or initial with its full meaning and "
            "re-type the question.")
        result["normalization"] = norm.provenance
        return result

    contradiction = math_contradiction(text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "\U0001f534 INVALID INPUT (MATH)"
        return contradiction

    detected = detect_single_entry(text)
    if detected is None:
        fallback = vy_harden(text, amount)
        fallback["normalization"] = norm.provenance
        return fallback

    result = _resolve_single_entry(text)
    result["normalization"] = norm.provenance
    return result
