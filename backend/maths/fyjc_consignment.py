"""
Financial Timeline Engine
Sprint 15I-SPEC - Specialized Accounting Authorities
backend/maths/fyjc_consignment.py

The Consignment Authority: deterministic FYJC consignment-account
treatment from the CONSIGNOR's books.

    normalized input -> contradiction validation -> consignment facts
    -> valuation (closing stock, abnormal loss, commission, del
    credere, consignment profit) -> canonical journal -> verification

Ownership invariant (Sprint 15I-SPEC Part A):
  * goods remain the consignor's property until a sale event - the
    physical transfer to the consignee is NEVER booked as an ordinary
    sale (no 'Sales' account and no consignee customer entry for the
    goods sent);
  * every stated amount receives exactly one deterministic role (goods
    cost / expense / sales / commission / del credere / remittance /
    unsold stock / abnormal loss); an amount with no role refuses;
  * missing quantity, cost, expense basis, commission rate or required
    historical value -> REVIEW_REQUIRED / BLOCKED with zero journal
    lines - FT-E never invents a value.

Valuation conventions (FYJC):
  * non-recurring expenses (freight, carriage, insurance, loading,
    packing, octroi, transport) benefit every unit and are included
    PRO-RATA in closing-stock and abnormal-loss valuation;
  * recurring expenses (godown rent, advertisement, selling expenses,
    commission, del credere) do NOT form part of stock value;
  * normal loss is absorbed into the cost of the remaining goods (no
    separate entry); abnormal loss is separated and valued at cost +
    proportionate non-recurring expenses;
  * closing stock = (goods cost + non-recurring expenses) x unsold
    fraction, or the stated unsold value directly.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

TOPIC_CONSIGNMENT = "consignment"

# ---------------------------------------------------------------------------
# Recognition (routing decision only)
# ---------------------------------------------------------------------------
# The consign root is the strict consignment signal - 'consignment',
# 'consigned', 'consignor', 'consignee'. An everyday 'commission'
# expense ('paid commission to an agent') or an ordinary sale is NEVER
# routed here.

_CONSIGN_RE = re.compile(r"\bconsign\w*\b", re.IGNORECASE)
_DEL_CREDERE_RE = re.compile(r"\bdel\s*credere\b", re.IGNORECASE)


def detect_consignment(text: str) -> Optional[Dict[str, Any]]:
    """Return the consignment topic when the question is a genuine
    consignment question, else None. Routing decision only - the
    resolver performs the deterministic resolution."""
    raw = str(text or "")
    low = " " + raw.lower() + " "
    if _CONSIGN_RE.search(low) or _DEL_CREDERE_RE.search(low):
        return {"topics": [TOPIC_CONSIGNMENT], "text": raw}
    return None


# ---------------------------------------------------------------------------
# Fact vocabulary
# ---------------------------------------------------------------------------

# The party receiving the goods: the consignee (after 'to' in the
# consignment clause). 'consigned goods to Mohan' / 'sent goods on
# consignment to Mohan' / 'goods consigned to Mohan'.
_CONSIGNEE_RE = re.compile(
    r"\b(?:consign\w*|sent)\b[^.;]{0,40}?\b(?:to)\s+"
    r"(?P<consignee>[A-Z][A-Za-z' .]{1,40}?)(?=[,.;]|\s+(?:rs\.?|\u20b9|on|"
    r"worth|for|costing|amounting|of|which|and|at|$))", re.IGNORECASE)

_GOODS_COST_RE = re.compile(
    r"\bgoods\b[^.;]{0,40}?\b(?:worth|costing|of|for|amounting\s+to)\b"
    r"|(?:\bworth|costing)\b[^.;]{0,30}?\bgoods\b"
    r"|\bgoods\b[^.;]{0,40}?(?:rs\.?|\u20b9|inr)"
    r"|\bconsign\w*\b[^.;]{0,30}?\b(?:goods|stock)?\b",
    re.IGNORECASE)

_SALES_RE = re.compile(
    r"\bsold\b|\bsales\b|\bsale\s+proceeds\b|\bproceeds\b|\brealised\b|\b"
    r"realized\b", re.IGNORECASE)

_NON_RECURRING_RE = re.compile(
    r"\bfreight\b|\bcarriage\b|\binsurance\b|\bloading\b|\bpacking\b|"
    r"\boctroi\b|\btransport(?:ation)?\b|\bcartage\b|\bdock\s+charges\b|"
    r"\bclearing\s+charges\b|\bimport\s+duty\b", re.IGNORECASE)

_RECURRING_RE = re.compile(
    r"\b(?:godown|warehouse)\s+rent\b|\badvertis(?:ing|ement)\b|"
    r"\bselling\s+expenses\b|\bselling\s+commission\b|\bsalary\b|"
    r"\bwages\b|\bbrokerage\b|\bstorage\s+charges\b", re.IGNORECASE)

_COMMISSION_RE = re.compile(r"\bcommission\b", re.IGNORECASE)
_DEL_CREDERE_RATE_RE = re.compile(r"\bdel\s+credere\b", re.IGNORECASE)

_UNSOLD_VALUE_RE = re.compile(
    r"\bunsold\b[^.;]{0,40}?\b(?:worth|costing|of|for|amounting\s+to|"
    r"valued\s+at)\b"
    r"|\b(?:worth|valued\s+at)\b[^.;]{0,30}?\bunsold\b"
    r"|\bclosing\s+stock\b[^.;]{0,40}?\b(?:worth|valued\s+at|of|for)\b",
    re.IGNORECASE)

_UNSOLD_FRACTION_RE = re.compile(
    r"\bunsold\b[^.;]{0,40}?\b(?:fraction|part|portion|share)\b"
    r"|\b(?:goods|stock)\b[^.;]{0,30}?\b(?:remained|left|remaining|"
    r"unsold)\b[^.;]{0,30}?\b(?:to\s+the\s+extent\s+of)?\b",
    re.IGNORECASE)

_ABNORMAL_VALUE_RE = re.compile(
    r"\babnormal\s+loss\b[^.;]{0,40}?\b(?:worth|of|for|amounting\s+to|"
    r"valued\s+at)\b"
    r"|\b(?:destroyed|lost|burnt|stolen)\b[^.;]{0,30}?\b(?:worth|of|for|"
    r"amounting\s+to)\b", re.IGNORECASE)

_ABNORMAL_FRACTION_RE = re.compile(
    r"\babnormal\s+loss\b[^.;]{0,30}?\b(?:fraction|part|portion|share)\b"
    r"|\b(?:goods|stock)\b[^.;]{0,20}?\b(?:destroyed|lost)\b[^.;]{0,20}?\b"
    r"(?:to\s+the\s+extent\s+of)?\b", re.IGNORECASE)

_ABNORMAL_UNITS_RE = re.compile(
    r"\b(?P<lost>[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:units?|articles?|"
    r"packets?|bales?|boxes?)\b[^.;]{0,30}?\b(?:destroyed|lost|burnt|"
    r"stolen)\b"
    r"|\b(?:destroyed|lost|burnt|stolen)\b[^.;]{0,30}?\b"
    r"(?P<lost2>[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:units?|articles?|"
    r"packets?|bales?|boxes?)\b", re.IGNORECASE)

_TOTAL_UNITS_RE = re.compile(
    r"\b(?:sent|consigned|dispatched|forwarded)\b[^.;]{0,40}?\b"
    r"(?P<total>[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:units?|articles?|"
    r"packets?|bales?|boxes?)\b"
    r"|\b(?P<total2>[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:units?|articles?|"
    r"packets?|bales?|boxes?)\b[^.;]{0,40}?\b(?:sent|consigned|dispatched)"
    r"\b", re.IGNORECASE)

_REMITTANCE_RE = re.compile(
    r"\bremitted\b|\bremittance\b|\bsent\s+(?:us|the\s+firm)\s+"
    r"(?:a\s+)?(?:cheque|draft|banker)\b|\bpaid\s+the\s+balance\b|\b"
    r"received\s+from\s+(?:the\s+)?consignee\b", re.IGNORECASE)

_NORMAL_LOSS_RE = re.compile(r"\bnormal\s+(?:loss|wastage|spoilage)\b",
                             re.IGNORECASE)

# a fraction token: '1/5', '1/4th', '25%', 'one-fifth' (word forms kept
# simple and deterministic - digit forms only).
_FRACTION_TOKEN_RE = re.compile(
    r"\b([0-9]+)\s*/\s*([0-9]+)(?:th|ths)?\b"
    r"|\b([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)

_CONSIGNEE_PAID_RE = re.compile(
    r"\b(?:paid|borne|met)\s+by\s+(?:the\s+)?consignee\b"
    r"|\bconsignee\s+(?:paid|bore|met)\b", re.IGNORECASE)

_CASH_SALES_RE = re.compile(r"\bcash\s+sales\b|\bsold\s+for\s+cash\b|\b"
                            r"in\s+cash\b", re.IGNORECASE)

_CREDIT_SALES_RE = re.compile(
    r"\bcredit\s+sales\b|\bsold\s+on\s+credit\b|\bon\s+credit\b",
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
    from backend.maths.fyjc_bk_reasoning import _fmt_amt as engine_fmt
    return engine_fmt(value)


def _refusal(status: str, why_not: str, next_action: str,
             consignment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import _refusal as engine_refusal
    result = engine_refusal(status, why_not, next_action)
    if consignment:
        result["consignment"] = consignment
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
    """All stated money amounts in order of appearance."""
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(low)
    return amounts


_AMOUNT_RE = re.compile(
    r"(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)


def _amount_token_ok(low: str, m: "re.Match[str]") -> bool:
    """Skip bare digit tokens that are actually fraction numerators /
    denominators or ratio parts ('4/5 of the goods', 'shared 3:2'): a
    bare '4' or '5' is never a money amount. A currency-marked token
    (Rs./inr/\u20b9) is always a money amount."""
    if m.group(0).lstrip().lower().startswith(("rs", "\u20b9", "inr")):
        return True
    before = low[m.start() - 1] if m.start() > 0 else " "
    after = low[m.end()] if m.end() < len(low) else " "
    return before not in "/:" and after not in "/:"


def _amount_after(low: str, keyword: str, max_gap: int = 44) -> Optional[Decimal]:
    """The FIRST money amount after a keyword occurrence, within the
    same clause (cut at sentence/comma punctuation so a number is never
    truncated at a fixed window edge). Deterministic: the first keyword
    occurrence with an in-clause amount wins."""
    for m in re.finditer(keyword, low):
        tail = low[m.end():m.end() + max_gap]
        cut = re.search(r"(?<!rs)\.|;", tail)
        if cut:
            tail = tail[:cut.start()]
        for am in _AMOUNT_RE.finditer(tail):
            if _amount_token_ok(tail, am):
                return _dec(am.group(1))
    return None


def _amount_before(low: str, keyword: str, max_gap: int = 44) -> Optional[Decimal]:
    """The LAST money amount BEFORE a keyword occurrence, within the
    same clause (the amount that the keyword labels)."""
    for m in re.finditer(keyword, low):
        head = low[max(0, m.start() - max_gap):m.start()]
        cut = re.search(r"(?<!rs)\.|;", head)
        if cut:
            head = head[cut.start() + 1:]
        ams = [am for am in _AMOUNT_RE.finditer(head)
               if _amount_token_ok(head, am)]
        if ams:
            return _dec(ams[-1].group(1))
    return None


def _percent_after(low: str, keyword: str, max_gap: int = 30) -> Optional[Decimal]:
    """The FIRST percent after a keyword occurrence (e.g. 'commission
    5%'), within the same clause."""
    for m in re.finditer(keyword, low):
        tail = low[m.end():m.end() + max_gap]
        cut = re.search(r"(?<!rs)\.|;", tail)
        if cut:
            tail = tail[:cut.start()]
        pm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", tail)
        if pm:
            return _dec(pm.group(1))
    return None


def _percent_before(low: str, keyword: str, max_gap: int = 30) -> Optional[Decimal]:
    """The LAST percent BEFORE a keyword occurrence (e.g. '2% del
    credere'), within the same clause."""
    for m in re.finditer(keyword, low):
        head = low[max(0, m.start() - max_gap):m.start()]
        cut = re.search(r"(?<!rs)\.|;", head)
        if cut:
            head = head[cut.start() + 1:]
        pms = list(re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*%", head))
        if pms:
            return _dec(pms[-1].group(1))
    return None


def _fraction_near(low: str, keyword: str,
                    window: int = 40) -> Optional[Decimal]:
    """The fraction token NEAREST to a keyword occurrence ('1/5' ->
    0.2, '25%' -> 0.25), so a commission rate ('5%') can never be
    mistaken for an unsold/abnormal fraction ('1/5 of the goods
    remained unsold'). Returns None when none is near enough."""
    best: Optional[Decimal] = None
    best_dist = 10 ** 9
    for m in re.finditer(keyword, low):
        kpos = m.start()
        for fm in _FRACTION_TOKEN_RE.finditer(low):
            dist = abs(fm.start() - kpos)
            if dist > window or dist >= best_dist:
                continue
            tup = fm.groups()
            if tup[0] and tup[1]:
                frac = _fraction_value(f"{tup[0]}/{tup[1]}")
            elif tup[2]:
                frac = _fraction_value(f"{tup[2]}%")
            else:
                frac = None
            if frac is not None and Decimal(0) < frac < Decimal(1):
                best = frac
                best_dist = dist
    return best


def _fraction_value(token: str) -> Optional[Decimal]:
    """A deterministic fraction from a token: '1/5' -> 0.2, '25%' -> 0.25.
    Returns None when the token is not a clean fraction."""
    token = str(token or "").strip()
    m = re.match(r"^([0-9]+)\s*/\s*([0-9]+)(?:th|ths)?$", token)
    if m:
        den = int(m.group(2))
        if den <= 0:
            return None
        return Decimal(int(m.group(1))) / Decimal(den)
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*%$", token)
    if m:
        return _dec(m.group(1)) / Decimal(100)
    return None


def _party_token(text: str) -> Optional[str]:
    from backend.maths.fyjc_bk_reasoning import _normalise_party_token
    return _normalise_party_token(str(text or "").strip())


# ---------------------------------------------------------------------------
# Consignment facts (Sprint 15I-SPEC Part A sections 1-10)
# ---------------------------------------------------------------------------

def _consignment_facts(text: str) -> Dict[str, Any]:
    """Deterministic role extraction for every stated amount. Each amount
    must receive exactly one role; a leftover amount refuses below."""
    low = " " + str(text or "").lower() + " "
    facts: Dict[str, Any] = {
        "consignee": None,
        "goods_cost": None,
        "sales": None,
        "cash_sales": False,
        "expenses_non_recurring": [],   # List[(Decimal, label)]
        "expenses_recurring": [],       # List[(Decimal, label)]
        "consignee_expenses": [],       # List[Decimal] (credited to consignee)
        "commission_rate": None,
        "commission_amount": None,
        "del_credere_rate": None,
        "del_credere_amount": None,
        "unsold_value": None,
        "unsold_fraction": None,
        "abnormal_loss_value": None,
        "abnormal_loss_fraction": None,
        "abnormal_loss_units": None,
        "total_units": None,
        "remittance": None,
        "normal_loss": False,
        "used_amounts": [],
        "ambiguous": [],
    }

    m = _CONSIGNEE_RE.search(text)
    if m:
        party = _party_token(m.group("consignee"))
        if party:
            facts["consignee"] = party

    # -- goods cost ---------------------------------------------------------
    # Anchored on the exact word 'consignment' (never 'consignor' /
    # 'consignee'): 'Goods of Rs.50,000 were sent on consignment' puts
    # the value BEFORE the anchor; 'a consignment of goods worth
    # Rs.50,000' puts it AFTER it. Fall back to the first 'goods'
    # occurrence for goods-sent wording without the word 'consignment'.
    goods = _amount_after(low, r"\bconsignment\b")
    if goods is None:
        goods = _amount_before(low, r"\bconsignment\b")
    if goods is None:
        goods = _amount_after(low, r"\bgoods\b")
    if goods is None:
        goods = _amount_before(low, r"\bgoods\b")
    if goods is not None:
        facts["goods_cost"] = goods
        facts["used_amounts"].append(goods)

    # -- sales --------------------------------------------------------------
    if _SALES_RE.search(low):
        sales = _amount_after(low, r"\bsold\b|\bsales?\b|\bproceeds\b|"
                              r"\brealis(?:ed|ed)\b")
        if sales is None:
            sales = _amount_before(low, r"\bsales?\b|\bproceeds\b")
        if sales is not None and sales not in facts["used_amounts"]:
            facts["sales"] = sales
            facts["used_amounts"].append(sales)
            facts["cash_sales"] = _CASH_SALES_RE.search(low) is not None
        elif sales is not None and sales in facts["used_amounts"]:
            facts["sales"] = sales
        elif sales is None and not (
                _UNSOLD_VALUE_RE.search(low) or
                _UNSOLD_FRACTION_RE.search(low) or
                re.search(r"\bsold\b[^.;]{0,12}?\b[0-9][0-9,]*\s*/\s*"
                          r"[0-9][0-9,]*\b|\b[0-9][0-9,]*\s*/\s*[0-9][0-9,]*"
                          r"\b[^.;]{0,20}?\b(?:sold|sold\s+away)\b|\bsold\b"
                          r"[^.;]{0,20}?\b(?:fraction|part|portion|share)\b"
                          r"|\b(?:fraction|part|portion|share)\b[^.;]{0,20}?"
                          r"\bsold\b", low)):
            # no sales figure AND no unsold indicator - the sales role is
            # genuinely missing (a stock-only question never needs one).
            facts["ambiguous"].append("sales amount missing")

    # -- expenses (non-recurring then recurring) ----------------------------
    for label, pattern, bucket in (
            ("freight", r"freight", "expenses_non_recurring"),
            ("carriage", r"carriage", "expenses_non_recurring"),
            ("insurance", r"insurance", "expenses_non_recurring"),
            ("loading", r"loading", "expenses_non_recurring"),
            ("packing", r"packing", "expenses_non_recurring"),
            ("octroi", r"octroi", "expenses_non_recurring"),
            ("transport", r"transport(?:ation)?", "expenses_non_recurring"),
            ("cartage", r"cartage", "expenses_non_recurring"),
            ("dock charges", r"dock\s+charges", "expenses_non_recurring"),
            ("clearing charges", r"clearing\s+charges",
             "expenses_non_recurring"),
            ("import duty", r"import\s+duty", "expenses_non_recurring"),
            ("godown rent", r"(?:godown|warehouse)\s+rent",
             "expenses_recurring"),
            ("advertisement", r"advertis(?:ing|ement)", "expenses_recurring"),
            ("selling expenses", r"selling\s+expenses", "expenses_recurring"),
            ("selling commission", r"selling\s+commission",
             "expenses_recurring"),
            ("salary", r"salary", "expenses_recurring"),
            ("wages", r"wages", "expenses_recurring"),
            ("brokerage", r"brokerage", "expenses_recurring"),
            ("storage charges", r"storage\s+charges", "expenses_recurring"),
    ):
        if not re.search(pattern, low):
            continue
        value = _amount_after(low, pattern)
        if value is None:
            value = _amount_before(low, pattern)
        if value is None:
            # 'expenses paid by consignee Rs.X' without a specific label -
            # a generic 'expenses' figure after the word 'expenses'.
            value = _amount_after(low, r"\bexpenses?\b")
        if value is not None and value not in facts["used_amounts"]:
            facts[bucket].append((value, label))
            facts["used_amounts"].append(value)
            if _CONSIGNEE_PAID_RE.search(low):
                facts["consignee_expenses"].append(value)

    # generic 'expenses Rs.X' with no specific label -> a RECURRING
    # expense (its basis is unknown, so it is conservatively excluded
    # from stock valuation; it still enters the profit calculation).
    value = _amount_after(low, r"\bexpenses?\b")
    if value is None:
        value = _amount_before(low, r"\bexpenses?\b")
    if value is not None and value not in facts["used_amounts"]:
        facts["expenses_recurring"].append((value, "expenses"))
        facts["used_amounts"].append(value)
        if _CONSIGNEE_PAID_RE.search(low):
            facts["consignee_expenses"].append(value)

    # -- del credere ---------------------------------------------------------
    # Parsed FIRST so the plain-commission branch can tell a real
    # commission from a single 'X% del credere' rate (never
    # double-counted as both).
    if _DEL_CREDERE_RE.search(low):
        rate = _percent_after(low, r"\bdel\s+credere\b")
        if rate is None:
            rate = _percent_before(low, r"\bdel\s+credere\b")
        amount = _amount_after(low, r"\bdel\s+credere\s+(?:of|is|:)\b")
        if rate is not None:
            facts["del_credere_rate"] = rate
        if amount is not None:
            facts["del_credere_amount"] = amount
            facts["used_amounts"].append(amount)
        if rate is None and amount is None:
            facts["ambiguous"].append("del credere rate/amount missing")

    # -- commission ---------------------------------------------------------
    # A plain 'commission' occurrence that is NOT part of a 'del credere
    # commission' phrase: a question may carry BOTH 'commission 10%' and
    # 'del credere commission 2%'. A single 'X% del credere' rate is the
    # del credere rate only.
    if _COMMISSION_RE.search(low):
        dc_rate = facts["del_credere_rate"]
        dc_amount = facts["del_credere_amount"]
        rate = None
        for m in re.finditer(r"\bcommission\b", low):
            if re.search(r"del\s+credere",
                         low[max(0, m.start() - 20):m.start()]):
                continue  # part of the phrase 'del credere commission'
            tail = low[m.end():m.end() + 30]
            cut = re.search(r"(?<!rs)\.|;", tail)
            if cut:
                tail = tail[:cut.start()]
            pm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", tail)
            if pm:
                value = _dec(pm.group(1))
                if dc_rate is not None and value == dc_rate and \
                        re.search(r"del\s+credere", tail[pm.end():]):
                    continue  # 'X% del credere' - the del credere rate
                rate = value
                break
        amount = _amount_after(low, r"\bcommission\s+(?:of|amounting\s+to|"
                               r"is|:)\b")
        if rate is not None and amount is not None and \
                abs(rate - amount) < Decimal("0.0001"):
            amount = None
        if rate is not None:
            facts["commission_rate"] = rate
        if amount is not None:
            facts["commission_amount"] = amount
            facts["used_amounts"].append(amount)
        if rate is None and amount is None and dc_rate is None \
                and dc_amount is None:
            facts["ambiguous"].append("commission rate/amount missing")

    # -- unsold stock ---------------------------------------------------------
    if _UNSOLD_VALUE_RE.search(low):
        value = _amount_after(low, r"\bunsold\b|\bclosing\s+stock\b")
        if value is None:
            value = _amount_before(low, r"\bunsold\b|\bclosing\s+stock\b")
        if value is not None and value not in facts["used_amounts"]:
            facts["unsold_value"] = value
            facts["used_amounts"].append(value)
    if _UNSOLD_FRACTION_RE.search(low) and facts["unsold_value"] is None:
        facts["unsold_fraction"] = _fraction_near(
            low, r"\bunsold\b|\bremained\b|\bleft\b|\bremaining\b")
    # 'X/Y of the goods were SOLD' - the unsold fraction is the
    # deterministic complement (1 - sold fraction), never a guess.
    if facts["unsold_value"] is None and facts["unsold_fraction"] is None:
        sold_frac = _fraction_near(low, r"\bsold\b")
        if sold_frac is not None and Decimal(0) < sold_frac < Decimal(1):
            facts["unsold_fraction"] = Decimal(1) - sold_frac
    # explicit unit-based unsold: 'X units remained unsold'
    m = re.search(
        r"\b(?P<u>[0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:units?|articles?|"
        r"packets?|bales?|boxes?)\b[^.;]{0,30}?\b(?:remained\s+unsold|"
        r"unsold|left|remaining)\b", low)
    if m:
        facts["unsold_units"] = _dec(m.group("u"))

    # -- abnormal loss ---------------------------------------------------------
    if _ABNORMAL_VALUE_RE.search(low) or re.search(
            r"\b(?:destroyed|lost|burnt|stolen)\b", low):
        value = _amount_after(low, r"\babnormal\s+loss\b|\bdestroyed\b|"
                              r"\blost\b|\bburnt\b|\bstolen\b")
        if value is None:
            value = _amount_before(low, r"\babnormal\s+loss\b|"
                                   r"\bdestroyed\b|\blost\b|\bburnt\b|"
                                   r"\bstolen\b")
        if value is not None and value not in facts["used_amounts"]:
            facts["abnormal_loss_value"] = value
            facts["used_amounts"].append(value)
    if facts["abnormal_loss_value"] is None and \
            _ABNORMAL_FRACTION_RE.search(low):
        frac = _fraction_near(low, r"\babnormal\s+loss\b|\bdestroyed\b|"
                              r"\blost\b|\bburnt\b|\bstolen\b")
        if frac is not None and (
                facts["unsold_fraction"] is None or
                abs(frac - facts["unsold_fraction"]) > Decimal("1e-9")):
            facts["abnormal_loss_fraction"] = frac
    m = _ABNORMAL_UNITS_RE.search(low)
    if m:
        lost = _dec(m.group("lost") or m.group("lost2"))
        if lost is not None:
            facts["abnormal_loss_units"] = lost
    m = _TOTAL_UNITS_RE.search(low)
    if m:
        total = _dec(m.group("total") or m.group("total2"))
        if total is not None:
            facts["total_units"] = total

    # -- remittance ---------------------------------------------------------
    if _REMITTANCE_RE.search(low):
        remittance = _amount_after(
            low, r"\bremitted\b|\bremittance\b|\bpaid\s+the\s+balance\b|"
                 r"\breceived\s+from\b")
        if remittance is None:
            remittance = _amount_before(low, r"\bremitted\b|\bremittance\b")
        if remittance is not None and \
                remittance not in facts["used_amounts"]:
            facts["remittance"] = remittance
            facts["used_amounts"].append(remittance)

    facts["normal_loss"] = _NORMAL_LOSS_RE.search(low) is not None

    # -- leftover amounts -----------------------------------------------------
    # Fraction ('1/5') and percent ('5%') tokens are NOT money amounts.
    skip = set()
    for m in re.finditer(r"\b([0-9][0-9,]*)\s*/\s*([0-9][0-9,]*)\b", low):
        skip.add(m.group(1).replace(",", ""))
        skip.add(m.group(2).replace(",", ""))
    for m in re.finditer(r"\b([0-9][0-9,]*)\s*%", low):
        skip.add(m.group(1).replace(",", ""))
    for amount in _money_amounts(low):
        key = str(amount)
        if key in skip:
            continue
        if amount not in facts["used_amounts"]:
            facts["ambiguous"].append(
                f"amount Rs.{_fmt_amt(amount)} has no consignment role")
    return facts


# ---------------------------------------------------------------------------
# Resolution (Sprint 15I-SPEC Part A)
# ---------------------------------------------------------------------------

def _resolve_consignment(text: str) -> Dict[str, Any]:
    from backend.maths.fyjc_bk_reasoning import (
        BLOCKED,
        REVIEW_REQUIRED,
        _fmt_amt,
    )

    low = " " + str(text or "").lower() + " "
    facts = _consignment_facts(text)
    notes: List[str] = []
    calculations: List[Dict[str, Any]] = []

    # -- what the question asks for ------------------------------------------
    # A stock / abnormal-loss valuation question (with no profit ask)
    # answers with the valuation and never books a profit/loss transfer.
    asks_profit = bool(re.search(
        r"\b(?:find|calculate|compute|determine|ascertain|work\s+out)"
        r"\b[^.;]{0,40}?\b(?:the\s+)?(?:consignment\s+)?profit\b"
        r"|\bprofit\s+on\s+consignment\b|\bconsignment\s+profit\b", low))
    asks_stock = bool(re.search(
        r"\b(?:value|find|calculate)\b[^.;]{0,40}?\b(?:closing\s+)?"
        r"(?:consignment\s+)?stock\b"
        r"|\bclosing\s+(?:consignment\s+)?stock\b"
        r"|\bunsold\s+(?:goods|stock)\b", low))
    asks_abnormal = bool(re.search(
        r"\b(?:find|value|calculate)\b[^.;]{0,40}?\babnormal\s+loss\b",
        low))
    valuation_only = (asks_stock or asks_abnormal) and not asks_profit

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
            "authority": "consignment-authority",
            "topic": TOPIC_CONSIGNMENT,
            "consignee": facts.get("consignee"),
            "notes": notes,
            "calculations": calculations,
            "invented_history": False,
            "unresolved": facts.get("ambiguous"),
        }
        return _refusal(status, why,
                        "Re-state every amount with its role (goods cost, "
                        "expenses, sales, commission, del credere, unsold "
                        "stock, abnormal loss) and re-type the question.",
                        payload)

    if facts["ambiguous"]:
        return refuse("; ".join(facts["ambiguous"]))

    # -- goods cost is the minimum required fact ----------------------------
    if facts["goods_cost"] is None:
        return refuse("The cost of the goods sent on consignment is not "
                      "established. FT-E never invents the consignment "
                      "value.")

    # -- compute -------------------------------------------------------------
    goods_cost = facts["goods_cost"]
    notes.append(
        f"Goods costing Rs.{_fmt_amt(goods_cost)} sent on consignment to "
        f"{facts['consignee'] or 'the consignee'} - they remain the "
        "consignor's property until sold.")

    non_recurring = sum((v for v, _ in facts["expenses_non_recurring"]),
                        Decimal(0))
    recurring = sum((v for v, _ in facts["expenses_recurring"]), Decimal(0))
    for value, label in facts["expenses_non_recurring"]:
        notes.append(f"{label.title()} Rs.{_fmt_amt(value)} is a "
                     "non-recurring expense - it forms part of the value "
                     "of unsold stock and abnormal loss.")
    for value, label in facts["expenses_recurring"]:
        notes.append(f"{label.title()} Rs.{_fmt_amt(value)} is a recurring "
                     "expense - it does NOT form part of the value of "
                     "unsold stock.")

    valuation_base = goods_cost + non_recurring

    # -- abnormal loss --------------------------------------------------------
    abnormal_value = facts["abnormal_loss_value"]
    if abnormal_value is None and facts["abnormal_loss_fraction"] is not None:
        abnormal_value = valuation_base * facts["abnormal_loss_fraction"]
    elif abnormal_value is None and facts["abnormal_loss_units"] is not None:
        if facts["total_units"] is None:
            return refuse("The abnormal loss is stated in units but the "
                          "total quantity consigned is not established - "
                          "FT-E cannot compute the loss pro-rata without "
                          "it.")
        total_units = facts["total_units"]
        if total_units <= 0:
            return refuse("The total quantity consigned is invalid.")
        abnormal_value = valuation_base * (
            facts["abnormal_loss_units"] / total_units)
    if abnormal_value is not None:
        # 'abnormal loss of Rs.X' states the loss itself (expenses
        # already loaded); 'goods worth/costing Rs.X destroyed' states
        # the COST of the destroyed goods, so the deterministic
        # pro-rata share of non-recurring expenses is loaded on top.
        stated_as_loss = bool(re.search(
            r"\babnormal\s+loss\b[^.;]{0,40}?\b(?:of|for|amounting\s+to|"
            r"was|is)\b", low))
        if not stated_as_loss and non_recurring > 0 and goods_cost > 0:
            pro_rata = (abnormal_value * non_recurring) / goods_cost
            abnormal_value = abnormal_value + pro_rata
            record("abnormal_loss",
                   "Abnormal loss valuation (cost + pro-rata "
                   "non-recurring expenses)",
                   abnormal_value,
                   (f"{_fmt_amt(abnormal_value - pro_rata)} + "
                    f"{_fmt_amt(abnormal_value - pro_rata)} x "
                    f"({_fmt_amt(non_recurring)} / "
                    f"{_fmt_amt(goods_cost)})"))
        else:
            record("abnormal_loss",
                   "Abnormal loss valuation (cost + pro-rata "
                   "non-recurring expenses)",
                   abnormal_value, None)
        notes.append(f"Abnormal loss valued at Rs.{_fmt_amt(abnormal_value)} "
                     "- separated from normal loss and transferred to the "
                     "Abnormal Loss A/c.")

    # -- closing stock ----------------------------------------------------------
    stock_value = facts["unsold_value"]
    if stock_value is None and facts["unsold_fraction"] is not None:
        # abnormal loss is removed FIRST; the unsold fraction applies to
        # the remaining goods (standard FYJC convention)
        remaining = valuation_base - (abnormal_value or Decimal(0))
        stock_value = remaining * facts["unsold_fraction"]
    elif stock_value is None and facts.get("unsold_units") is not None:
        if facts["total_units"] is None:
            return refuse("The unsold stock is stated in units but the "
                          "total quantity consigned is not established - "
                          "FT-E cannot compute the stock value without it.")
        remaining_units = facts["total_units"] - (
            facts["abnormal_loss_units"] or Decimal(0))
        if remaining_units <= 0:
            return refuse("The remaining quantity after abnormal loss is "
                          "zero or negative - FT-E cannot value stock on "
                          "an invalid quantity.")
        remaining = valuation_base - (abnormal_value or Decimal(0))
        stock_value = remaining * (
            facts["unsold_units"] / remaining_units)
    if stock_value is not None:
        record("closing_stock",
               "Closing consignment stock valuation (cost + pro-rata "
               "non-recurring expenses)",
               stock_value,
               f"{_fmt_amt(valuation_base)} x unsold fraction")
        notes.append(f"Closing consignment stock valued at "
                     f"Rs.{_fmt_amt(stock_value)}.")
    if facts["normal_loss"]:
        notes.append("Normal loss is absorbed into the cost of the "
                     "remaining goods - it needs no separate journal "
                     "entry and does not affect the total value.")

    # -- commission --------------------------------------------------------------
    commission = facts["commission_amount"]
    if commission is None and facts["commission_rate"] is not None:
        if facts["sales"] is None:
            return refuse("Commission is stated as a rate but the sales "
                          "amount is not established - FT-E cannot "
                          "compute the commission without the sales.")
        commission = facts["sales"] * facts["commission_rate"] / Decimal(100)
    if commission is not None:
        record("commission", "Consignee commission",
               commission,
               (f"{_fmt_amt(facts['sales'])} x "
                f"{_fmt_amt(facts['commission_rate'])}%" if facts.get(
                    "commission_rate") else "stated amount"))
        notes.append(f"Commission Rs.{_fmt_amt(commission)}.")

    # -- del credere --------------------------------------------------------------
    del_credere = facts["del_credere_amount"]
    if del_credere is None and facts["del_credere_rate"] is not None:
        if facts["sales"] is None:
            return refuse("Del credere commission is stated as a rate but "
                          "the sales amount is not established - FT-E "
                          "cannot compute it without the sales.")
        del_credere = (facts["sales"] *
                       facts["del_credere_rate"] / Decimal(100))
    if del_credere is not None:
        record("del_credere", "Del credere commission",
               del_credere,
               (f"{_fmt_amt(facts['sales'])} x "
                f"{_fmt_amt(facts['del_credere_rate'])}%" if facts.get(
                    "del_credere_rate") else "stated amount"))
        notes.append(f"Del credere commission Rs.{_fmt_amt(del_credere)} - "
                     "covers bad debts on credit sales.")

    # -- consignment profit/loss ---------------------------------------------------
    total_expenses = non_recurring + recurring + (commission or Decimal(0)) \
        + (del_credere or Decimal(0))
    profit = None
    if not valuation_only and \
            (facts["sales"] is not None or stock_value is not None):
        credit_side = (facts["sales"] or Decimal(0)) \
            + (stock_value or Decimal(0))
        debit_side = goods_cost + total_expenses \
            + (abnormal_value or Decimal(0))
        profit = credit_side - debit_side
        record("consignment_profit",
               "Consignment profit/loss (Sales + Closing Stock - Goods "
               "cost - expenses - commission - del credere - abnormal "
               "loss)",
               profit,
               f"{_fmt_amt(credit_side)} - {_fmt_amt(debit_side)}")
        notes.append(
            f"Consignment {'profit' if profit >= 0 else 'loss'} "
            f"Rs.{_fmt_amt(abs(profit))}.")

    # -- journal composition (consignor's books) -------------------------------------
    journals: List[Dict[str, Any]] = []
    consignee = facts["consignee"]

    # 1. goods sent
    journals.append(_journal(
        [_line("Consignment", goods_cost, "debit",
               why="Goods sent on consignment remain the consignor's "
                   "property - the consignment account is debited with "
                   "their cost.")],
        [_line("Goods Sent on Consignment", goods_cost, "credit",
               why="Goods sent on consignment are credited to the Goods "
                   "Sent on Consignment A/c - never to Sales.")],
        "Being goods sent on consignment."))

    # 2. consignor expenses
    for value, label in facts["expenses_non_recurring"] + \
            facts["expenses_recurring"]:
        if value in facts["consignee_expenses"]:
            if consignee is None:
                return refuse(
                    f"The {label} expense is stated as paid by the "
                    "consignee but no consignee party is established - "
                    "FT-E never invents a party.")
            journals.append(_journal(
                [_line("Consignment", value, "debit",
                       why=f"{label.title()} incurred for the consignment "
                           "is a consignment expense.")],
                [_line(consignee, value, "credit",
                       why=f"The consignee paid the {label} on the "
                           "consignor's behalf - the consignee's account "
                           "is credited.")],
                f"Being {label} paid by the consignee."))
        else:
            journals.append(_journal(
                [_line("Consignment", value, "debit",
                       why=f"{label.title()} incurred for the consignment "
                           "is a consignment expense.")],
                [_line("Bank", value, "credit",
                       why="The expense was paid by the consignor out of "
                           "the bank.")],
                f"Being {label} paid by the consignor."))

    # 3. sales by the consignee
    if facts["sales"] is not None:
        if facts["cash_sales"]:
            journals.append(_journal(
                [_line("Bank", facts["sales"], "debit",
                       why="Cash sales made by the consignee belong to "
                           "the consignor and are received in the bank.")],
                [_line("Consignment", facts["sales"], "credit",
                       why="Sales proceeds are credited to the "
                           "Consignment A/c.")],
                "Being cash sales by the consignee."))
        else:
            if consignee is None:
                return refuse("The goods were sold by a consignee but no "
                              "consignee party is established - FT-E "
                              "never invents a party.")
            journals.append(_journal(
                [_line(consignee, facts["sales"], "debit",
                       why="The consignee collected the sale proceeds on "
                           "the consignor's behalf - the consignee's "
                           "account is debited.")],
                [_line("Consignment", facts["sales"], "credit",
                       why="Sales proceeds are credited to the "
                           "Consignment A/c.")],
                "Being sales effected by the consignee."))

    # 4. commission + del credere
    if commission is not None:
        if consignee is None:
            return refuse("Commission is due to the consignee but no "
                          "consignee party is established - FT-E never "
                          "invents a party.")
        journals.append(_journal(
            [_line("Consignment", commission, "debit",
                   why="Commission payable to the consignee is a "
                       "consignment expense.")],
            [_line(consignee, commission, "credit",
                   why="The consignee's account is credited with the "
                       "commission earned.")],
            "Being commission payable to the consignee."))
    if del_credere is not None:
        if consignee is None:
            return refuse("Del credere commission is due to the consignee "
                          "but no consignee party is established - FT-E "
                          "never invents a party.")
        journals.append(_journal(
            [_line("Consignment", del_credere, "debit",
                   why="Del credere commission is a consignment "
                       "expense covering bad debts.")],
            [_line(consignee, del_credere, "credit",
                   why="The consignee's account is credited with the del "
                       "credere commission earned.")],
            "Being del credere commission payable to the consignee."))

    # 5. remittance from the consignee
    if facts["remittance"] is not None:
        if consignee is None:
            return refuse("A remittance is stated but no consignee party "
                          "is established - FT-E never invents a party.")
        journals.append(_journal(
            [_line("Bank", facts["remittance"], "debit",
                   why="The remittance from the consignee is received in "
                       "the bank.")],
            [_line(consignee, facts["remittance"], "credit",
                   why="The consignee's account is reduced by the amount "
                       "remitted.")],
            "Being amount remitted by the consignee."))

    # 6. closing stock
    if stock_value is not None:
        journals.append(_journal(
            [_line("Consignment Stock", stock_value, "debit",
                   why="Unsold goods remain the consignor's property and "
                       "are brought into the books at their valuation.")],
            [_line("Consignment", stock_value, "credit",
                   why="Closing stock is credited to the Consignment A/c "
                       "as the value of goods still unsold.")],
            "Being closing consignment stock."))

    # 7. abnormal loss
    if abnormal_value is not None:
        journals.append(_journal(
            [_line("Abnormal Loss", abnormal_value, "debit",
                   why="Goods destroyed/lost in transit are an abnormal "
                       "loss - valued at cost plus proportionate "
                       "non-recurring expenses.")],
            [_line("Consignment", abnormal_value, "credit",
                   why="The abnormal loss is credited to the Consignment "
                       "A/c.")],
            "Being abnormal loss on consignment."))

    # 8. profit / loss transfer
    if profit is not None:
        if profit >= 0:
            journals.append(_journal(
                [_line("Consignment", profit, "debit",
                       why="The credit balance of the Consignment A/c is "
                           "the profit on consignment.")],
                [_line("Profit on Consignment", profit, "credit",
                       why="Profit on consignment is credited to the "
                           "Profit on Consignment A/c.")],
                "Being profit on consignment transferred."))
        else:
            journals.append(_journal(
                [_line("Loss on Consignment", abs(profit), "debit",
                       why="The debit balance of the Consignment A/c is "
                           "the loss on consignment.")],
                [_line("Consignment", abs(profit), "credit",
                       why="The loss on consignment is credited to the "
                           "Consignment A/c to close it.")],
                "Being loss on consignment transferred."))

    solved_for = None
    headline = None
    if valuation_only:
        if asks_stock and stock_value is not None:
            solved_for, headline = "closing stock", stock_value
        elif asks_abnormal and abnormal_value is not None:
            solved_for, headline = "abnormal loss", abnormal_value
    else:
        if profit is not None:
            solved_for, headline = "consignment profit/loss", profit

    return _compose(journals, facts, notes, calculations,
                    solved_for=solved_for, result=headline)


def _compose(journals: List[Dict[str, Any]],
             facts: Dict[str, Any],
             notes: List[str],
             calculations: List[Dict[str, Any]],
             solved_for: Optional[str] = None,
             result: Optional[Decimal] = None) -> Dict[str, Any]:
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

    payload = {
        "authority": "consignment-authority",
        "topic": TOPIC_CONSIGNMENT,
        "consignee": facts.get("consignee"),
        "notes": notes,
        "calculations": calculations,
        "invented_history": False,
        "solved_for": solved_for,
        "result": result,
    }

    narration = "Being consignment transactions in the consignor's books."
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
        "next_action": "Post these entries in the consignor's books and "
                       "verify them.",
        "consignment": payload,
        "audit": {
            "authority": "consignment-authority",
            "rule_key": None,
            "calculation_ids": [],
            "total_debit": float(total_debit),
            "total_credit": float(total_credit),
            "topic": TOPIC_CONSIGNMENT,
            "case": "consignor-books",
        },
    }


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

def consignment_outcome(question: str,
                        amount: Any = None) -> Dict[str, Any]:
    """Resolve ONE consignment-routed question deterministically.

    Pipeline: raw input -> 15I-VY normalization -> safety concerns ->
    global math contradiction validation -> consignment resolution ->
    canonical result. The SAME gates the hardened authority applies run
    FIRST, so the Consignment Authority never bypasses or weakens a
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

    # 15I-VY party/abbreviation safety: identity must be established
    # before ANY consignment resolution (never a guessed party).
    if norm.concerns:
        result = _refusal(
            "REVIEW_REQUIRED",
            norm.concerns[0],
            "Replace the abbreviation or initial with its full meaning and "
            "re-type the consignment transaction.")
        result["normalization"] = norm.provenance
        return result

    # 15I-VY global mathematical contradiction.
    contradiction = math_contradiction(text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "\U0001f534 INVALID INPUT (MATH)"
        return contradiction

    detected = detect_consignment(text)
    if detected is None:
        # should never happen (the orchestrator routes before calling) -
        # fall back to the hardened boundary rather than guess.
        fallback = vy_harden(text, amount)
        fallback["normalization"] = norm.provenance
        return fallback

    result = _resolve_consignment(text)
    result["normalization"] = norm.provenance

    # final balancing backstop: a VERIFIED consignment journal must
    # balance.
    if result.get("status") == "VERIFIED":
        debit_lines = result.get("debit_lines") or []
        credit_lines = result.get("credit_lines") or []
        if sum((_dec(l["amount"]) or Decimal(0)
                for l in debit_lines), Decimal(0)) != sum(
                    (_dec(l["amount"]) or Decimal(0)
                     for l in credit_lines), Decimal(0)):
            return _refusal(
                "REVIEW_REQUIRED",
                "The resolved consignment journal does not balance. FT-E "
                "never reports an unbalanced entry as verified.",
                "Re-check the stated amounts and re-type the question.",
                result.get("consignment"))
    return result
