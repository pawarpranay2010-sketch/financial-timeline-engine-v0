"""
Platrixa
Sprint 15I-SPEC - Specialized Accounting Authorities
backend/maths/fyjc_joint_venture.py

The Joint Venture Authority: deterministic FYJC joint-venture treatment
from ONE venturer's (own) books.

    normalized input -> contradiction validation -> joint-venture facts
    -> profit / profit-sharing / settlement computation -> canonical
    journal -> verification

Boundaries (Sprint 15I-SPEC Part B):
  * a co-venturer is NEVER treated as an ordinary supplier/customer -
    contributions and expenses by the co-venturer credit the
    co-venturer's personal account, never Purchases / an expense A/c;
  * explicit contribution/profit-sharing relationships are recorded in
    the transaction graph;
  * missing profit-sharing ratio -> REVIEW_REQUIRED when a share must
    be computed, unless an unambiguous default (equal sharing) is
    explicitly established by the input;
  * a contribution whose form (cash vs goods) is not stated refuses -
    Platrixa never guesses the basis;
  * when both venturers are active subjects and no books-holder is
    named, the first-named venturer in 'X and Y entered into a joint
    venture' is the books-holder (explicit 'in the books of X' /
    'X's books' wins when present).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

TOPIC_JOINT_VENTURE = "joint_venture"

# ---------------------------------------------------------------------------
# Recognition (routing decision only)
# ---------------------------------------------------------------------------
# Genuine joint-venture wording only. A bare 'venture' (e.g. 'started a
# business venture') is never routed here.

_JV_CORE_RE = re.compile(
    r"\bjoint\s+venture\b|\bco-?venturer\b|\bventure\s+(?:a/c|account)\b|"
    r"\bventurer(?:s)?\b", re.IGNORECASE)


def detect_joint_venture(text: str) -> Optional[Dict[str, Any]]:
    """Return the joint-venture topic when the question is a genuine JV
    question, else None. Routing decision only."""
    raw = str(text or "")
    low = " " + raw.lower() + " "
    if _JV_CORE_RE.search(low):
        return {"topics": [TOPIC_JOINT_VENTURE], "text": raw}
    return None


# ---------------------------------------------------------------------------
# Fact vocabulary
# ---------------------------------------------------------------------------

# 'X and Y entered into a joint venture' (books-holder = X by
# convention; explicit 'in the books of X' / 'X's books' wins).
_JV_PARTIES_RE = re.compile(
    r"\b(?P<a>[A-Z][A-Za-z' .]{1,30}?)\s+and\s+"
    r"(?P<b>[A-Z][A-Za-z' .]{1,30}?)\s+(?:entered\s+into|formed|started|"
    r"undertook)\s+(?:a\s+)?joint\s+venture\b", re.IGNORECASE)

# 'entered into a joint venture with Y' -> the firm is the books-holder,
# Y is the co-venturer.
_JV_WITH_RE = re.compile(
    r"\b(?:entered\s+into|formed|started)\s+(?:a\s+)?joint\s+venture\s+with\s+"
    r"(?P<co>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:and|contribut|for|"
    r"where|in|on|$))"
    r"|\bjoint\s+venture\b[^.;]{0,25}?\b(?:was\s+|is\s+)?"
    r"(?:entered\s+into|formed|started)\s+with\s+"
    r"(?P<co2>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:and|contribut|for|"
    r"where|in|on|$))", re.IGNORECASE)

# 'in the books of X' / "X's books" / 'in X's books'
_BOOKS_OF_RE = re.compile(
    r"\bin\s+the\s+books\s+of\s+(?P<who>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|$)"
    r"|\b(?P<who2>[A-Z][A-Za-z' .]{1,30}?)(?:'s|\u2019s)\s+books\b",
    re.IGNORECASE)

_GOODS_CONTRIBUTION_RE = re.compile(
    r"\b(?:contributed|brought\s+in|introduced|put\s+in)\b[^.;]{0,40}?\b"
    r"(?:goods|stock|merchandise)\b"
    r"|\b(?:goods|stock)\b[^.;]{0,40}?\b(?:contributed|brought\s+in|"
    r"introduced|put\s+in)\b", re.IGNORECASE)

_CASH_CONTRIBUTION_RE = re.compile(
    r"\b(?:contributed|brought\s+in|introduced|put\s+in)\b[^.;]{0,40}?\b"
    r"(?:cash|money|amount)\b"
    r"|\b(?:cash|money)\b[^.;]{0,40}?\b(?:contributed|brought\s+in|"
    r"introduced|put\s+in)\b", re.IGNORECASE)

_VENTURE_PURCHASE_RE = re.compile(
    r"\b(?:purchased|bought)\b[^.;]{0,30}?\b(?:for\s+the\s+venture|for\s+"
    r"the\s+joint\s+venture|for\s+the\s+jv)\b", re.IGNORECASE)

_SALES_RE = re.compile(
    r"\bsold\b|\bsales\b|\bproceeds\b|\brealis(?:ed|ed)\b|\breceived\s+"
    r"(?:from\s+sales|from\s+the\s+venture)\b|\btotal\s+sales\b",
    re.IGNORECASE)

_RATIO_RE = re.compile(
    r"\b(?:in\s+the\s+)?ratio\s+(?:of\s+)?(?P<a>[0-9]+)\s*:\s*(?P<b>[0-9]+)\b"
    r"|\b(?:in\s+the\s+)?ratio\s+(?P<a2>[0-9]+)\s*(?:to|and)\s*"
    r"(?P<b2>[0-9]+)\b", re.IGNORECASE)

_EQUAL_RE = re.compile(r"\b(?:equally|equal\s+share|equal\s+shares|"
                       r"in\s+equal\s+parts|shared\s+equally)\b",
                       re.IGNORECASE)

_SHARE_FRACTION_RE = re.compile(
    r"\b(?P<who>[A-Z][A-Za-z' .]{1,30}?)(?:'s|\u2019s)?\s+"
    r"(?:share|portion|part)\b[^.;]{0,30}?\b(?:was|is|amounted\s+to|of)\b"
    r"[^.;]{0,10}?\b(?P<num>[0-9]+)\s*/\s*(?P<den>[0-9]+)\b"
    r"|\b(?P<who2>[A-Z][A-Za-z' .]{1,30}?)\s+(?:got|receives?|is\s+"
    r"entitled\s+to)\b[^.;]{0,20}?\b(?P<num2>[0-9]+)\s*/\s*"
    r"(?P<den2>[0-9]+)\b\s+(?:of\s+the\s+)?profit", re.IGNORECASE)

_SETTLE_RE = re.compile(
    r"\bsettled\b|\bsettlement\b|\bpaid\s+the\s+balance\b|\bbalance\s+paid\b"
    r"|\breceived\s+the\s+balance\b|\bthe\s+account\s+was\s+settled\b",
    re.IGNORECASE)

_COMMISSION_RE = re.compile(r"\bcommission\b", re.IGNORECASE)


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
             joint_venture: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import _refusal as engine_refusal
    result = engine_refusal(status, why_not, next_action)
    if joint_venture:
        result["joint_venture"] = joint_venture
    return result


def _line(account: str, amount: Decimal, side: str,
          why: Optional[str] = None) -> Dict[str, Any]:
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


def _money_amounts(low: str) -> List[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(low)
    return amounts


def _amount_after(low: str, keyword: str, max_gap: int = 44) -> Optional[Decimal]:
    """The FIRST money amount after a keyword occurrence, within the
    same clause (cut at comma/sentence punctuation so a number is never
    truncated at a fixed window edge)."""
    for m in re.finditer(keyword, low):
        tail = low[m.end():m.end() + max_gap]
        cut = re.search(r"(?<!rs)\.|;", tail)
        if cut:
            tail = tail[:cut.start()]
        am = re.search(r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                       tail)
        if am:
            return _dec(am.group(1))
    return None


def _amount_before(low: str, keyword: str, max_gap: int = 44) -> Optional[Decimal]:
    """The LAST money amount BEFORE a keyword occurrence, within the
    same clause."""
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


def _amount_after_pos(low: str, pos: int, max_gap: int = 44) -> Optional[Decimal]:
    """The first money amount after a specific character position,
    within the same clause (never truncated at a fixed window edge)."""
    tail = low[pos:pos + max_gap]
    cut = re.search(r"(?<!rs)\.|;", tail)
    if cut:
        tail = tail[:cut.start()]
    am = re.search(r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                   tail)
    if am:
        return _dec(am.group(1))
    return None


def _party_token(text: str) -> Optional[str]:
    from backend.maths.fyjc_bk_reasoning import _normalise_party_token
    return _normalise_party_token(str(text or "").strip())


def _party_in_clause(clause: str, parties: List[str]) -> Optional[str]:
    """The party named by a clause, deterministically (the party token
    that appears in the clause and is in the venturer set). The clause
    is lowercased, so the party token is matched case-insensitively."""
    clow = str(clause or "").lower()
    for party in parties:
        if re.search(r"\b" + re.escape(str(party).lower()) + r"\b", clow):
            return party
    return None


# ---------------------------------------------------------------------------
# Joint-venture facts
# ---------------------------------------------------------------------------

def _joint_venture_facts(text: str) -> Dict[str, Any]:
    low = " " + str(text or "").lower() + " "
    facts: Dict[str, Any] = {
        "venturer": None,
        "co_venturer": None,
        "contribution_goods": [],   # List[(Decimal, 'firm'|'co')]
        "contribution_cash": [],    # List[(Decimal, 'firm'|'co')]
        "purchases": [],            # List[(Decimal, 'firm'|'co')]
        "expenses": [],             # List[(Decimal, 'firm'|'co')]
        "sales": None,
        "commission_rate": None,
        "commission_who": None,
        "ratio": None,              # (firm_units, co_units)
        "co_share_fraction": None,
        "settlement": None,
        "ambiguous": [],
        "used_amounts": [],
    }

    # -- parties -------------------------------------------------------------
    m = _BOOKS_OF_RE.search(text)
    if m:
        who = _party_token(m.group("who") or m.group("who2"))
        if who:
            facts["venturer"] = who
    m = _JV_PARTIES_RE.search(text)
    if m:
        a = _party_token(m.group("a"))
        b = _party_token(m.group("b"))
        if facts["venturer"] is None:
            facts["venturer"] = a
        facts["co_venturer"] = b if a == facts["venturer"] else a
    m = _JV_WITH_RE.search(text)
    if m:
        co = _party_token(m.group("co") or m.group("co2"))
        if facts["venturer"] is None and co:
            facts["venturer"] = "firm"
        if co and co != facts["venturer"]:
            facts["co_venturer"] = co

    # -- contribution / purchase / expense / sales clauses -------------------
    # Protect the currency abbreviation before splitting on sentence
    # punctuation ('Rs.' must never split 'Rs.20,000' into two clauses),
    # then scan each clause for occurrence-anchored amounts: the amount
    # attributed to a party is the one in THAT party's clause segment,
    # never the character-nearest number.
    protected = str(text or "").replace("Rs.", "Rs").replace("rs.", "rs")
    clauses = [c.strip() for c in re.split(r"[.;\n]", protected)
               if c.strip()]
    parties = [p for p in (facts["venturer"], facts["co_venturer"])
               if p and p != "firm"]
    for clause in clauses:
        clow = " " + clause.lower() + " "
        who = _party_in_clause(clause, parties or [])
        if who == facts.get("venturer"):
            who = "firm"  # the venturer IS the books-holder
        if who is None:
            who = "firm"  # no named party -> the books-holder

        # contributions: one occurrence per party ('A contributed
        # Rs.X and B contributed Rs.Y' -> two contributions). The amount
        # is anchored to THIS occurrence, never the character-nearest
        # number.
        contrib_matches = list(re.finditer(
            r"\b(?:contribut(?:ed|ing)|brought\s+in|introduced|put\s+in|"
            r"gave|supplied)\b|\bcontribution\b", clow))
        if contrib_matches:
            for m in contrib_matches:
                segment = clow[m.start():]
                cut = re.search(r"(?<!rs)\.|;", segment)
                if cut:
                    segment = segment[:cut.start()]
                # per-occurrence party: the party named in the window
                # around this contribution verb ('Rahul contributed ...',
                # 'goods worth Rs.X were contributed by Rahul')
                who = _party_in_clause(
                    clow[max(0, m.start() - 24):m.end() + 40],
                    parties or [])
                if who == facts.get("venturer"):
                    who = "firm"
                if who is None:
                    who = "firm"
                amount = _amount_after_pos(clow, m.end())
                if amount is None:
                    amount = _amount_after(segment, r"goods|stock|cash|"
                                            r"amount")
                if amount is None or amount in facts["used_amounts"]:
                    continue
                if _GOODS_CONTRIBUTION_RE.search(segment):
                    facts["contribution_goods"].append((amount, who))
                    facts["used_amounts"].append(amount)
                elif _CASH_CONTRIBUTION_RE.search(segment):
                    facts["contribution_cash"].append((amount, who))
                    facts["used_amounts"].append(amount)
                else:
                    facts["ambiguous"].append(
                        "a contribution amount is stated without "
                        "establishing whether it is goods or cash")
                    facts["used_amounts"].append(amount)
            continue

        if _VENTURE_PURCHASE_RE.search(clow):
            amount = _amount_after(clow, r"\b(?:purchased|bought)\b")
            if amount is None:
                amount = _amount_after(clow, r"for\s+the\s+(?:joint\s+)?"
                                        r"venture")
            if amount is not None and amount not in facts["used_amounts"]:
                facts["purchases"].append((amount, who))
                facts["used_amounts"].append(amount)
            continue

        if re.search(r"\b(?:expenses?|paid|incurred)\b", clow) and \
                not re.search(r"\b(?:sales|sold|proceeds)\b", clow):
            # per-occurrence: 'Rahul paid expenses of Rs.1,000 and
            # Mohan paid Rs.500' -> TWO expenses, each anchored to its
            # own amount and its own party (never the clause-first
            # party, never the character-nearest number).
            for m in re.finditer(r"\bexpenses?\b|\bpaid\b|\bincurred\b",
                                 clow):
                segment = clow[m.start():]
                cut = re.search(r"(?<!rs)\.|;", segment)
                if cut:
                    segment = segment[:cut.start()]
                who = _party_in_clause(
                    clow[max(0, m.start() - 24):m.end() + 40],
                    parties or [])
                if who == facts.get("venturer"):
                    who = "firm"
                if who is None:
                    who = "firm"
                amount = _amount_after_pos(clow, m.end())
                if amount is None or amount in facts["used_amounts"]:
                    continue
                facts["expenses"].append((amount, who))
                facts["used_amounts"].append(amount)
            continue

        if _SALES_RE.search(clow):
            amount = _amount_after(
                clow, r"\bsold\b|\bsales?\b|\bproceeds\b|"
                      r"\brealis(?:ed|ed)\b|\breceived\b")
            if amount is None:
                amount = _amount_before(clow, r"\bsales?\b|\bproceeds\b")
            if amount is not None and amount not in facts["used_amounts"]:
                facts["sales"] = amount
                facts["used_amounts"].append(amount)

    # -- commission -------------------------------------------------------------
    if _COMMISSION_RE.search(low):
        rate = None
        for m in re.finditer(r"\bcommission\b", low):
            head = low[max(0, m.start() - 20):m.start()]
            tail = low[m.end():m.end() + 30]
            rm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", head + "|" + tail)
            if rm:
                rate = _dec(rm.group(1))
                break
        if rate is not None:
            facts["commission_rate"] = rate
            who = None
            for party in parties:
                if re.search(r"\b" + re.escape(party) + r"\b",
                             low[max(0, low.find("commission") - 60):
                                 low.find("commission") + 60]):
                    who = party
                    break
            facts["commission_who"] = who or "co"

    # -- profit-sharing ---------------------------------------------------------
    m = _RATIO_RE.search(text)
    if m:
        a = _dec(m.group("a") or m.group("a2"))
        b = _dec(m.group("b") or m.group("b2"))
        if a is not None and b is not None and a > 0 and b > 0:
            facts["ratio"] = (a, b)
    elif _EQUAL_RE.search(low):
        facts["ratio"] = (Decimal(1), Decimal(1))
    else:
        m = _SHARE_FRACTION_RE.search(text)
        if m:
            num = _dec(m.group("num") or m.group("num2"))
            den = _dec(m.group("den") or m.group("den2"))
            if num is not None and den is not None and den > 0:
                who = _party_token(m.group("who") or m.group("who2"))
                if who and who == facts["co_venturer"]:
                    facts["co_share_fraction"] = num / den
                elif who and who == facts["venturer"]:
                    facts["ratio"] = (den - num, num)

    # -- settlement ---------------------------------------------------------------
    if _SETTLE_RE.search(low):
        facts["settlement"] = True

    # -- leftover amounts -----------------------------------------------------------
    # Fraction ('1/5') and percent ('5%') tokens are NOT money amounts.
    skip = set()
    for m in re.finditer(r"\b([0-9][0-9,]*)\s*/\s*([0-9][0-9,]*)\b", low):
        skip.add(m.group(1).replace(",", ""))
        skip.add(m.group(2).replace(",", ""))
    for m in re.finditer(r"\b([0-9][0-9,]*)\s*%", low):
        skip.add(m.group(1).replace(",", ""))
    # profit-sharing ratio tokens ('3:2') are NOT money amounts
    for m in re.finditer(r"\b([0-9]+)\s*:\s*([0-9]+)\b", low):
        skip.add(m.group(1))
        skip.add(m.group(2))
    for amount in _money_amounts(low):
        key = str(amount)
        if key in skip:
            continue
        if amount not in facts["used_amounts"]:
            facts["ambiguous"].append(
                f"amount Rs.{_fmt_amt(amount)} has no joint-venture role")
    return facts


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_joint_venture(text: str) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import REVIEW_REQUIRED

    facts = _joint_venture_facts(text)
    notes: List[str] = []
    calculations: List[Dict[str, Any]] = []

    def record(kind: str, label: str, value: Any,
               formula: Optional[str] = None) -> None:
        calculations.append({
            "kind": kind,
            "label": label,
            "value": str(value),
            "formula": formula,
        })

    def refuse(why: str, status: str = REVIEW_REQUIRED) -> Dict[str, Any]:
        payload = {
            "authority": "joint-venture-authority",
            "topic": TOPIC_JOINT_VENTURE,
            "venturer": facts.get("venturer"),
            "co_venturer": facts.get("co_venturer"),
            "notes": notes,
            "calculations": calculations,
            "invented_history": False,
            "unresolved": facts.get("ambiguous"),
        }
        return _refusal(status, why,
                        "Re-state the joint-venture question with each "
                        "amount's role (goods/cash contribution, expenses, "
                        "sales) and the profit-sharing basis, and re-type "
                        "it.",
                        payload)

    if facts["ambiguous"]:
        return refuse("; ".join(facts["ambiguous"]))

    venturer = facts["venturer"]
    co = facts["co_venturer"]

    # a contribution with no goods/cash basis refuses (never guess)
    contributed = (facts["contribution_goods"] + facts["contribution_cash"])
    if contributed and not facts["contribution_goods"] and \
            not facts["contribution_cash"]:
        return refuse("A contribution is stated but its form (goods or "
                      "cash) is not established - Platrixa never guesses the "
                      "basis.")

    if not contributed and not facts["purchases"] and not facts["expenses"] \
            and facts["sales"] is None:
        return refuse("No joint-venture transaction is established "
                      "(contribution, purchase, expense or sale).")

    if co is None and (facts["sales"] is not None or
                       facts["expenses"] or facts["purchases"] or
                       any(w == "co" for _, w in
                           facts["contribution_goods"] +
                           facts["contribution_cash"])):
        return refuse("A co-venturer is required to record this "
                      "joint-venture transaction but none is established "
                      "- Platrixa never invents a party.")

    def _who_text(who: str) -> str:
        if who == "firm":
            return "The firm"
        return venturer or "The co-venturer"

    def _paid_text(who: str) -> str:
        return "paid by the firm" if who == "firm" else "paid by the " \
            "co-venturer"

    for value, who in facts["contribution_goods"]:
        notes.append(
            f"{_who_text(who)} contributed goods worth "
            f"Rs.{_fmt_amt(value)} to the venture.")
    for value, who in facts["contribution_cash"]:
        notes.append(
            f"{_who_text(who)} contributed Rs.{_fmt_amt(value)} in cash "
            "to the venture.")
    for value, who in facts["purchases"]:
        notes.append(
            f"Venture purchase of Rs.{_fmt_amt(value)} "
            f"({_paid_text(who)}).")
    for value, who in facts["expenses"]:
        notes.append(
            f"Venture expense Rs.{_fmt_amt(value)} "
            f"({_paid_text(who)}).")

    # -- profit ------------------------------------------------------------------
    debit_total = sum((v for v, _ in facts["contribution_goods"]),
                      Decimal(0)) \
        + sum((v for v, _ in facts["contribution_cash"]), Decimal(0)) \
        + sum((v for v, _ in facts["purchases"]), Decimal(0)) \
        + sum((v for v, _ in facts["expenses"]), Decimal(0))
    commission = None
    if facts["commission_rate"] is not None:
        if facts["sales"] is None:
            return refuse("Commission is stated as a rate but the sales "
                          "amount is not established - Platrixa cannot "
                          "compute it without the sales.")
        commission = facts["sales"] * facts["commission_rate"] / Decimal(100)
        debit_total += commission
        record("commission", "Venture commission",
               commission,
               f"{_fmt_amt(facts['sales'])} x "
               f"{_fmt_amt(facts['commission_rate'])}%")
        notes.append(f"Commission Rs.{_fmt_amt(commission)}.")

    profit = None
    if facts["sales"] is not None:
        profit = facts["sales"] - debit_total
        record("joint_venture_profit",
               "Venture profit/loss (Sales - contributions - purchases - "
               "expenses - commission)",
               profit,
               f"{_fmt_amt(facts['sales'])} - {_fmt_amt(debit_total)}")
        notes.append(f"Venture {'profit' if profit >= 0 else 'loss'} "
                     f"Rs.{_fmt_amt(abs(profit))}.")

    # -- profit share ---------------------------------------------------------------
    co_share = None
    if profit is not None and profit > 0 and co is not None:
        if facts["co_share_fraction"] is not None:
            co_share = profit * facts["co_share_fraction"]
        elif facts["ratio"] is not None:
            firm_units, co_units = facts["ratio"]
            co_share = profit * co_units / (firm_units + co_units)
        else:
            return refuse(
                "The venture made a profit but the profit-sharing basis "
                "is not established (no ratio and no 'shared equally' "
                "wording). Platrixa never invents a sharing rule.")
        record("co_venturer_share", "Co-venturer's share of profit",
               co_share,
               (f"profit x {_fmt_amt(facts['co_share_fraction'])}"
                if facts["co_share_fraction"] is not None else
                f"profit x {_fmt_amt(facts['ratio'][1])} / "
                f"({_fmt_amt(facts['ratio'][0])} + "
                f"{_fmt_amt(facts['ratio'][1])})"))
        notes.append(f"{co}'s share of profit Rs.{_fmt_amt(co_share)}.")

    # -- journals ---------------------------------------------------------------------
    journals: List[Dict[str, Any]] = []
    who_display = {  # noqa: F841 - kept for readability
        "firm": "the firm",
    }

    # 1. firm's goods contribution
    for value, who in facts["contribution_goods"]:
        if who == "firm":
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Goods contributed by the venturer are a "
                           "venture asset - the Joint Venture A/c is "
                           "debited.")],
                [_line("Goods", value, "credit",
                       why="Goods contributed to the venture are "
                           "credited to the Goods A/c - never booked as "
                           "a sale.")],
                "Being goods contributed to the joint venture."))
        else:
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Goods contributed by the co-venturer are a "
                           "venture asset - the Joint Venture A/c is "
                           "debited.")],
                [_line(co, value, "credit",
                       why="The co-venturer's personal account is "
                           "credited - never Purchases or a supplier "
                           "account.")],
                f"Being goods contributed by {co} to the joint venture."))

    # 2. cash contributions
    for value, who in facts["contribution_cash"]:
        if who == "firm":
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Cash contributed by the venturer is a "
                           "venture asset - the Joint Venture A/c is "
                           "debited.")],
                [_line("Bank", value, "credit",
                       why="Cash paid into the venture is credited to "
                           "the Bank A/c.")],
                "Being cash contributed to the joint venture."))
        else:
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Cash contributed by the co-venturer is a "
                           "venture asset - the Joint Venture A/c is "
                           "debited.")],
                [_line(co, value, "credit",
                       why="The co-venturer's personal account is "
                           "credited.")],
                f"Being cash contributed by {co} to the joint venture."))

    # 3. venture purchases
    for value, who in facts["purchases"]:
        if who == "firm":
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Venture purchases are a venture expense - "
                           "the Joint Venture A/c is debited.")],
                [_line("Bank", value, "credit",
                       why="The purchase was paid for by the venturer "
                           "out of the bank.")],
                "Being venture purchases paid by the firm."))
        else:
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Venture purchases by the co-venturer are a "
                           "venture expense - the Joint Venture A/c is "
                           "debited.")],
                [_line(co, value, "credit",
                       why="The co-venturer's personal account is "
                           "credited for the purchases made.")],
                f"Being venture purchases paid by {co}."))

    # 4. expenses
    for value, who in facts["expenses"]:
        if who == "firm":
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Venture expenses paid by the venturer are "
                           "charged to the Joint Venture A/c.")],
                [_line("Bank", value, "credit",
                       why="The expense was paid out of the bank.")],
                "Being venture expenses paid by the firm."))
        else:
            journals.append(_journal(
                [_line("Joint Venture", value, "debit",
                       why="Venture expenses paid by the co-venturer "
                           "are charged to the Joint Venture A/c.")],
                [_line(co, value, "credit",
                       why="The co-venturer's personal account is "
                           "credited for the expenses paid.")],
                f"Being venture expenses paid by {co}."))

    # 5. sales
    if facts["sales"] is not None:
        journals.append(_journal(
            [_line("Bank", facts["sales"], "debit",
                   why="Sale proceeds of the venture are received in "
                       "the bank.")],
            [_line("Joint Venture", facts["sales"], "credit",
                   why="Sale proceeds are credited to the Joint Venture "
                       "A/c.")],
            "Being venture sales proceeds."))

    # 6. commission
    if commission is not None:
        journals.append(_journal(
            [_line("Joint Venture", commission, "debit",
                   why="Commission for the venture is a venture "
                       "expense.")],
            [_line(co, commission, "credit",
                   why="The co-venturer's personal account is credited "
                       "with the commission earned.")],
            f"Being commission payable to {co}."))

    # 7. profit / loss transfer
    if profit is not None:
        if profit >= 0:
            journals.append(_journal(
                [_line("Joint Venture", profit, "debit",
                       why="The credit balance of the Joint Venture A/c "
                           "is the venture profit.")],
                [_line("Profit on Joint Venture", profit, "credit",
                       why="Venture profit is credited to the Profit on "
                           "Joint Venture A/c.")],
                "Being profit on joint venture transferred."))
        else:
            journals.append(_journal(
                [_line("Loss on Joint Venture", abs(profit), "debit",
                       why="The debit balance of the Joint Venture A/c "
                           "is the venture loss.")],
                [_line("Joint Venture", abs(profit), "credit",
                       why="The venture loss is credited to the Joint "
                           "Venture A/c to close it.")],
                "Being loss on joint venture transferred."))

    # 8. co-venturer's share of profit
    if co_share is not None:
        journals.append(_journal(
            [_line("Profit on Joint Venture", co_share, "debit",
                   why="The co-venturer's share is paid out of the "
                       "venture profit.")],
            [_line(co, co_share, "credit",
                   why="The co-venturer's personal account is credited "
                       "with the share of profit.")],
            f"Being {co}'s share of venture profit."))

    # 9. settlement
    if facts["settlement"]:
        if co is None:
            return refuse("A settlement is stated but no co-venturer "
                          "party is established - Platrixa never invents a "
                          "party.")
        if co_share is None and profit is not None and profit > 0:
            return refuse(
                "A settlement is requested but the co-venturer's share "
                "of profit cannot be computed because the profit-sharing "
                "basis is not established.")
        co_balance = Decimal(0)
        for value, who in facts["contribution_goods"] + \
                facts["contribution_cash"] + facts["purchases"] + \
                facts["expenses"]:
            if who != "firm":
                co_balance += value
        if commission is not None and \
                facts["commission_who"] != "firm":
            co_balance += commission
        if co_share is not None:
            co_balance += co_share
        if co_balance > 0:
            journals.append(_journal(
                [_line(co, co_balance, "debit",
                       why="The net balance due to the co-venturer is "
                           "paid in settlement of the venture account.")],
                [_line("Bank", co_balance, "credit",
                       why="The settlement is paid out of the bank.")],
                f"Being settlement of {co}'s venture account."))
        elif co_balance < 0:
            journals.append(_journal(
                [_line("Bank", abs(co_balance), "debit",
                       why="The co-venturer owes the firm the net "
                           "balance of the venture account.")],
                [_line(co, abs(co_balance), "credit",
                       why="The co-venturer's account is settled by "
                           "receiving the balance.")],
                f"Being settlement received from {co}."))

    return _compose(journals, facts, notes, calculations, profit=profit)


def _compose(journals: List[Dict[str, Any]],
             facts: Dict[str, Any],
             notes: List[str],
             calculations: List[Dict[str, Any]],
             profit: Optional[Decimal] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import (
        STATUS_WORDS,
        generate_ledger,
        generate_trial_balance,
        verify_arithmetic,
    )
    from backend.maths.status import VERIFIED

    debit_lines = [l for j in journals for l in (j.get("debit_lines") or [])]
    credit_lines = [l for j in journals for l in (j.get("credit_lines") or [])]
    total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
    total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))

    payload = {
        "authority": "joint-venture-authority",
        "topic": TOPIC_JOINT_VENTURE,
        "venturer": facts.get("venturer"),
        "co_venturer": facts.get("co_venturer"),
        "notes": notes,
        "calculations": calculations,
        "invented_history": False,
        "solved_for": "joint venture profit/loss" if profit is not None
                       else None,
        "result": profit,
    }

    narration = "Being joint-venture transactions in the venturer's books."
    if journals:
        ledger = generate_ledger(journals)
        trial_balance = generate_trial_balance(journals)
        verification = verify_arithmetic([
            {"side": line["side"], "amount": line["amount"]}
            for line in debit_lines + credit_lines
        ])
    else:
        ledger = trial_balance = verification = None

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
        ] + calculations,
        "why_not": None,
        "next_action": "Post these entries in the venturer's books and "
                       "verify them.",
        "joint_venture": payload,
        "audit": {
            "authority": "joint-venture-authority",
            "rule_key": None,
            "calculation_ids": [],
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "topic": TOPIC_JOINT_VENTURE,
            "case": "own-books",
        },
    }


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

def joint_venture_outcome(question: str,
                          amount: Any = None) -> Dict[str, Any]:
    """Resolve ONE joint-venture-routed question deterministically.

    Pipeline: raw input -> 15I-VY normalization -> safety concerns ->
    global math contradiction validation -> joint-venture resolution ->
    canonical result. The SAME gates the hardened authority applies run
    FIRST, so the Joint Venture Authority never bypasses or weakens a
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
            "re-type the joint-venture transaction.")
        result["normalization"] = norm.provenance
        return result

    contradiction = math_contradiction(text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "\U0001f534 INVALID INPUT (MATH)"
        return contradiction

    detected = detect_joint_venture(text)
    if detected is None:
        fallback = vy_harden(text, amount)
        fallback["normalization"] = norm.provenance
        return fallback

    result = _resolve_joint_venture(text)
    result["normalization"] = norm.provenance

    if result.get("status") == "VERIFIED":
        debit_lines = result.get("debit_lines") or []
        credit_lines = result.get("credit_lines") or []
        if sum((_dec(l["amount"]) or Decimal(0)
                for l in debit_lines), Decimal(0)) != sum(
                    (_dec(l["amount"]) or Decimal(0)
                     for l in credit_lines), Decimal(0)):
            return _refusal(
                "REVIEW_REQUIRED",
                "The resolved joint-venture journal does not balance. "
                "Platrixa never reports an unbalanced entry as verified.",
                "Re-check the stated amounts and re-type the question.",
                result.get("joint_venture"))
    return result
