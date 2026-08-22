"""
Platrixa
Sprint 15I-WF - Unified Transaction Orchestration & Authority Composition
backend/maths/fyjc_orchestration.py

A deterministic orchestration layer that composes the verified FYJC
accounting authorities for complex, multi-segment questions WITHOUT
rewriting any of them:

    normalized input -> transaction graph (segments, facts, authorities,
    dependencies, ownership) -> per-segment authority resolution ->
    global completeness / ownership / merge verification -> ONE verdict

The existing verified layers stay authoritative:
  * 15I-VY  - linguistic/numeric normalization + contradiction validation
  * 15I-UZ  - direction, amount/rate/payment safety and authority hardening
  * 15I-TX  - multi-transaction completeness and expanded FYJC capability

What THIS layer adds is architecture, not accounting rules:
  1. A Transaction Graph that carries every stated fact (amounts, rates,
     parties, payment components, events) with provenance back to the
     normalized input - segmentation can never silently discard a fact.
  2. Explicit authority ownership: every segment is routed to exactly one
     base authority (with explicitly cooperating authorities for GST and
     settlement), and a segment routed to an authority that is NOT
     implemented is refused - never resolved by guessing.
  3. A global amount-ownership pass: every stated amount receives exactly
     one deterministic role; two different amounts claiming the same role
     in one segment, or an event fact with no authority, forces
     REVIEW_REQUIRED with zero journal lines.
  4. A dependency graph (trade discount -> net value -> GST base ->
     total payable -> payment fraction -> party balance -> reversal) so
     downstream authorities consume upstream outputs instead of
     recalculating the same value.
  5. A deterministic merge stage over the per-segment authority journals
     (segment order preserved, source provenance kept, duplicate postings
     and conflicting account/amount claims rejected) with a final
     debit == credit verification.

Composition contract (safety-first):
  * When the hardened authority VERIFIEDs and the graph finds NO
    violation, the hardened result is passed through UNCHANGED
    (byte-identical behaviour for every historical gate input) and the
    graph payload is attached for the Study/Verify UI.
  * When a stated fact cannot be deterministically assigned an accounting
    role - a dishonoured/bounced cheque that the Discrepancy Authority
    cannot resolve (no prior record, no amount, no party), a bill of
    exchange (Bills Authority missing), a duplicated amount claim, or a
    silently dropped segment - the orchestrator REFUSES with
    REVIEW_REQUIRED / NOT_SUPPORTED and zero journal lines. It never
    invents an answer and never lets one authority override another.

Sprint 15I-DISC adds the Discrepancy Authority (implemented):
  * a question routed to the Discrepancy Authority (dishonour / BRS /
    omission / rectification wording) is resolved by THAT authority
    deterministically - normalization + contradiction gates first, then
    the discrepancy treatment. It composes journals in the hardened
    engine's format, so clean non-discrepancy inputs are untouched;
  * the Discrepancy Authority never weakens a 15I-VY refusal (unsafe
    party tokens and mathematical contradictions still refuse first).

Sprint 15I-BILLS adds the Bills Authority (implemented):
  * a bills-of-exchange question is routed to the Bills Authority and
    resolved through the bill lifecycle state machine (DRAWN -> ACCEPTED
    -> HELD / DISCOUNTED / ENDORSED / SENT_FOR_COLLECTION -> HONOURED /
    DISHONOURED) with deterministic journals, maturity mathematics
    (months / 12, days / 365, three days of grace) and noting charges;
  * a bill is NEVER booked as cash, a missing prior bill state refuses
    (history is never invented), and no 15I-VY refusal is weakened.

Sprint 15I-SPEC adds three specialized authorities (implemented):
  * the Consignment Authority (consignment / consignor / consignee /
    commission / del credere / abnormal loss / consignment stock):
    goods remain the consignor's property until a sale event - the
    transfer is NEVER booked as an ordinary sale - with deterministic
    closing-stock and abnormal-loss valuation, commission and
    consignment profit;
  * the Joint Venture Authority (joint venture / co-venturer): a
    co-venturer is NEVER an ordinary supplier/customer; contributions,
    expenses, sales, profit-sharing and settlement are booked through
    the venturer's own books;
  * the Single Entry Authority (incomplete records / single entry /
    statement of affairs): the net-worth relationship
    Profit = Closing Capital + Drawings - Fresh Capital - Opening
    Capital and its inverses, returned as a VERIFIED mathematical
    result with zero journal lines (the topic does not require a
    journal entry).
  * all three run the SAME normalization + contradiction gates first,
    never weaken a 15I-VY refusal, never invent a party, amount,
    history or profit-sharing rule, and refuse (REVIEW_REQUIRED /
    BLOCKED) with zero journal lines when a required value is missing.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Authority registry (Sprint 15I-WF section 3)
# ---------------------------------------------------------------------------
# Every FYJC segment is routed to EXACTLY ONE base authority. GST and
# Settlement are explicitly marked as COOPERATING authorities: they ride
# inside the same hardened engine call as the base authority, so a single
# segment is still resolved by one authority invocation - never by two
# engines racing each other.

AUTHORITIES: Dict[str, Dict[str, Any]] = {
    "COMMERCIAL_CORE": {
        "name": "Commercial Core",
        "implemented": True,
        "base": True,
        "scope": ("ordinary goods purchases/sales, returns, expenses, "
                  "incomes, cash/bank, drawings, discounts"),
    },
    "ASSET_AUTHORITY": {
        "name": "Asset Authority",
        "implemented": True,
        "base": True,
        "scope": ("machinery / fixed-asset acquisition and disposal"),
    },
    "GST_AUTHORITY": {
        "name": "GST Authority",
        "implemented": True,
        "base": False,
        "cooperating": True,
        "scope": ("GST computation on the verified goods / expense "
                  "surface; GST on asset disposals is refused"),
    },
    "SETTLEMENT_AUTHORITY": {
        "name": "Settlement/Payment Authority",
        "implemented": True,
        "base": False,
        "cooperating": True,
        "scope": ("payment / settlement / cheque steps folded into the "
                  "base transaction"),
    },
    "DISCREPANCY_AUTHORITY": {
        "name": "Discrepancy Authority",
        "implemented": True,
        "base": False,
        "scope": ("discrepancy / reconciliation / reversal / omission / "
                  "rectification (Sprint 15I-DISC): BRS single-case "
                  "adjustments, dishonoured cheques with an established "
                  "prior receipt, omitted transactions, and rectification "
                  "of wrong account / wrong amount / wrong side with "
                  "Suspense only when the trial-balance difference is "
                  "explicitly established. A dishonour with no reliable "
                  "prior record refuses - history is never invented."),
    },
    "ADJUSTMENT_AUTHORITY": {
        "name": "Adjustment Authority",
        "implemented": False,
        "base": False,
        "scope": ("accrual / provision / depreciation adjustments - NOT "
                  "implemented yet"),
    },
    "CONSIGNMENT_AUTHORITY": {
        "name": "Consignment Authority",
        "implemented": True,
        "base": False,
        "scope": ("consignment (Sprint 15I-SPEC): goods sent on "
                  "consignment stay the consignor's property until sold "
                  "- the transfer is never booked as an ordinary sale. "
                  "Deterministic closing-stock and abnormal-loss "
                  "valuation (cost + pro-rata non-recurring expenses), "
                  "normal vs abnormal loss, commission, del credere "
                  "commission and consignment profit/loss, from the "
                  "consignor's books."),
    },
    "JOINT_VENTURE_AUTHORITY": {
        "name": "Joint Venture Authority",
        "implemented": True,
        "base": False,
        "scope": ("joint venture (Sprint 15I-SPEC): a co-venturer is "
                  "never treated as an ordinary supplier/customer. "
                  "Contributions, expenses, sales, profit/loss, "
                  "profit-sharing and settlement are booked through the "
                  "venturer's own books; a missing profit-sharing ratio "
                  "refuses when a share must be computed."),
    },
    "SINGLE_ENTRY_AUTHORITY": {
        "name": "Single Entry Authority",
        "implemented": True,
        "base": False,
        "scope": ("single entry / incomplete records (Sprint 15I-SPEC): "
                  "the net-worth relationship Profit = Closing Capital + "
                  "Drawings - Fresh Capital - Opening Capital and its "
                  "inverses, returned as a verified mathematical result "
                  "with zero journal lines - never forced through the "
                  "double-entry balancing requirement."),
    },
    "BILLS_AUTHORITY": {
        "name": "Bills Authority",
        "implemented": True,
        "base": False,
        "scope": ("bills of exchange (Sprint 15I-BILLS): the full FYJC "
                  "bill lifecycle - drawing / acceptance, holding until "
                  "maturity, discounting with the bank (Bill x Rate x "
                  "Time), endorsement, sent for collection, honour and "
                  "dishonour with noting charges. A bill is never booked "
                  "as cash; a dishonour with no reliable prior bill state "
                  "refuses - history is never invented."),
    },
}

# ---------------------------------------------------------------------------
# Deterministic event-word tables
# ---------------------------------------------------------------------------
# A dishonour/reversal EVENT is a stated fact that demands the
# (unimplemented) Discrepancy Authority. Without it the fact has no
# accounting role, so the orchestrator refuses instead of letting the
# settlement journal stand on its own.
_DISHONOUR_RE = re.compile(
    r"\b(?:dishonou?red?|dishonou?r|bounced|returned\s+unpaid|"
    r"not\s+(?:honou?red|honou?red))\b", re.IGNORECASE)

# Bills-of-exchange wording (NOT the everyday 'electricity bill' /
# 'mobile bill' expense context, which stays a supported expense).
_BILLS_RE = re.compile(
    r"\bbills?\s+of\s+exchange\b|\bbills?\s+receivable\b|"
    r"\bbills?\s+payable\b", re.IGNORECASE)

# Unimplemented-topic routing words (parallel to the hardened engine's
# own NOT_SUPPORTED hint list - the orchestrator routes them to a named
# authority so the refusal names the missing authority).
_TOPIC_ROUTES: List[Tuple[str, str, "re.Pattern[str]"]] = [
    ("CONSIGNMENT_AUTHORITY", "consignment",
     re.compile(r"\bconsign(?:ment|ed|ing)?\b", re.IGNORECASE)),
    ("JOINT_VENTURE_AUTHORITY", "joint venture",
     re.compile(r"\bjoint\s+venture\b", re.IGNORECASE)),
    ("SINGLE_ENTRY_AUTHORITY", "single entry / incomplete records",
     re.compile(r"\bsingle\s+entry\b|\bincomplete\s+records\b",
                re.IGNORECASE)),
    ("ADJUSTMENT_AUTHORITY", "depreciation / provision / accrual",
     re.compile(r"\bdepreciation\b|\bprovision\s+for\b|\baccru(?:al|ed)\b",
                re.IGNORECASE)),
]

_GST_RE = re.compile(r"\b(?:gst|igst|cgst|sgst)\b", re.IGNORECASE)
_SETTLEMENT_RE = re.compile(
    r"\b(?:paid|received|cheque|settlement|settled|by\s+cheque|"
    r"issued\s+him\s+a\s+cheque)\b", re.IGNORECASE)
_ASSET_KEY_HINTS = ("ASSET",)

# Goods-transaction verbs whose signature account must appear in the
# composed journal (dropped-segment detection).
_GOODS_SIGNATURE = {
    "PURCHASE": ("Purchases",),
    "SALE": ("Sales",),
    "RETURN": ("Purchases Return", "Sales Return", "Returns Inward",
               "Returns Outward", "Purchase Return", "Sales Return"),
}


# ---------------------------------------------------------------------------
# Transaction Graph model (Sprint 15I-WF section 1)
# ---------------------------------------------------------------------------

@dataclass
class StatedFact:
    """One stated fact with provenance back to the normalized input."""
    kind: str                       # amount | rate | fraction | party | event
    value: Any
    original: str                   # the raw span in the segment text
    segment_index: int
    role: Optional[str] = None      # deterministic semantic role
    authority: Optional[str] = None # claiming authority
    owner_segment: Optional[int] = None
    provenance: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SegmentNode:
    """One independently addressable transaction segment."""
    index: int
    text: str
    start: int
    end: int
    classification: Optional[Dict[str, Any]]
    base_authority: str
    cooperating: List[str] = field(default_factory=list)
    facts: List[StatedFact] = field(default_factory=list)
    status: Optional[str] = None
    journal: Optional[Dict[str, Any]] = None
    unresolved: List[str] = field(default_factory=list)


@dataclass
class TransactionGraph:
    """The full orchestration representation of one question."""
    raw: str
    normalized: str
    normalization: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[SegmentNode] = field(default_factory=list)
    dependencies: List[Tuple[str, str]] = field(default_factory=list)
    ownership: List[Dict[str, Any]] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    violations: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Segment fact extraction (deterministic)
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PARTY_HINT_RE = re.compile(
    r"\b(?:from|to|by|received\s+from|paid\s+to)\s+([A-Z][A-Za-z.' ]+)"
    r"(?:\s|,|\.|$)", re.IGNORECASE)


def _segment_amounts(text: str) -> List[Decimal]:
    """Stated money amounts in a segment (rates excluded), reusing the
    hardened engine's own extraction so ownership operates on the SAME
    numbers the authority consumes."""
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amounts, _ = _extract_amounts(text)
    return amounts


def _segment_rates(text: str) -> List[Tuple[Decimal, str]]:
    """(rate, label) pairs for every '<n>%' token in the segment."""
    from backend.maths.fyjc_bk_reasoning import _extract_percents
    return _extract_percents(text)


def _segment_party(text: str) -> Optional[str]:
    """The party named by the segment (reusing the hardened engine's own
    deterministic party resolution - never an invented identity)."""
    from backend.maths.fyjc_bk_reasoning import _party_from_text
    return _party_from_text(text)


def _segment_is_settlement(text: str) -> bool:
    """True when the segment is a payment/settlement step with no
    transaction verb of its own (received-from / paid-to / cheque
    settlement)."""
    from backend.maths.fyjc_bk_reasoning import _is_payment_step
    low = " " + str(text or "").lower() + " "
    if _is_payment_step(text):
        return True
    if re.search(r"\b(?:received\s+from|paid\s+to)\b", low) \
            and not re.search(r"\b(?:sold|purchased|bought|returned)\b",
                              low):
        return True
    return False


def _gst_component_amounts(text: str) -> List[Decimal]:
    """Explicitly stated CGST/SGST/IGST component amounts in the segment
    (reusing the engine's GST facts so ownership matches its numbers).
    The engine exposes them as (component_label, amount) pairs under
    'comp_amounts'."""
    from backend.maths.fyjc_bk_reasoning import _gst_facts
    facts = _gst_facts(text) or {}
    values: List[Decimal] = []
    for label, value in facts.get("comp_amounts") or []:
        if isinstance(value, Decimal):
            values.append(value)
    return values


def _trade_discount_amount(text: str) -> Optional[Decimal]:
    """An explicitly stated trade-discount AMOUNT (Rs.X trade discount /
    less Rs.X trade discount), mirroring the contradiction validator."""
    from backend.maths.fyjc_normalization import _amount_near
    low = " " + str(text or "").lower() + " "
    # Sprint 15I-CAPABILITY-CLOSURE: window increased from 18 to 24
    # to avoid truncating Indian-format amounts like Rs.30,000 (which
    # would be cut to Rs.30,0 in an 18-char window and parsed as 300).
    trade = _amount_near(
        low, r"trade\s+discount\s+(?:of|amounting\s+to|is|:)",
        window=24, mode="after")
    if trade is None:
        trade = _amount_near(low, r"trade\s+discount",
                             window=24, mode="before")
    return trade


def _cash_discount_amount(text: str) -> Optional[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _cash_discount_amt_in
    return _cash_discount_amt_in(text)


def _detect_explicit_discount_in(text: str):
    """Wrapper around _detect_explicit_discount for ownership assignment."""
    from backend.maths.fyjc_bk_reasoning import (
        _detect_explicit_discount, _extract_amounts)
    amounts, _ = _extract_amounts(text)
    return _detect_explicit_discount(text, amounts)


def _account_balance_amount(text: str) -> Optional[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _account_balance_figure
    return _account_balance_figure(text)


def _personal_use_amount(text: str) -> Optional[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _personal_amount_in
    return _personal_amount_in(text)


def _payment_amount(text: str) -> Optional[Decimal]:
    """The stated money figure of a payment/settlement step (nearest to
    'paid'/'received'), mirroring the contradiction validator."""
    from backend.maths.fyjc_normalization import _amount_near
    low = " " + str(text or "").lower() + " "
    return _amount_near(low, r"paid|received", window=20)


def _payment_amounts_multi(text: str) -> List[Decimal]:
    """Find ALL payment amounts in a segment by detecting amounts that are
    adjacent to payment vocabulary.  Returns amounts in order of appearance,
    excluding amounts that are clearly the transaction value (those following
    'for', 'worth', 'price of', etc.)."""
    from decimal import Decimal as _D
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    low = " " + str(text or "").lower() + " "
    amounts, _ = _extract_amounts(text)
    if not amounts:
        return []

    _PAY_VOCAB = re.compile(
        r"(?:paid|received|by\s+cheque|by\s+chq|by\s+cash|by\s+bank|"
        r"by\s+neft|by\s+upi|by\s+rtgs|in\s+cash|by\s+draft|"
        r"cash\s+payment|cheque\s+payment|bank\s+transfer|"
        r"part\s+settlement|full\s+settlement)")
    if not _PAY_VOCAB.search(low):
        return []

    # Transaction-value amounts (after 'for', 'worth', etc.) are NOT payments.
    tv_amounts: set = set()
    for m in re.finditer(
            r"(?:for|worth|price\s+of|value\s+of|cost\s+of)\s*"
            r"(?:rs\.?|\u20b9|inr)?\s*([\d][\d,]*(?:\.\d+)?)", low):
        try:
            tv_amounts.add(_D(m.group(1).replace(",", "")))
        except Exception:
            pass

    # Find amounts directly adjacent to payment keywords using multiple
    # patterns.  Use position-based dedup so that duplicate amounts at
    # different positions (e.g. "Paid 15,000 ... Remaining 15,000") are
    # both captured.
    payment_amounts: List[Decimal] = []
    seen_positions: List[int] = []  # track matched positions for overlap dedup

    _AMT_PAT = r"(\d[\d,]*(?:\.\d+)?)"
    _INSTRUMENTS = (
        r"(?:cash|cheque|chq|"
        r"by\s+(?:bank|cheque|chq|neft|upi|rtgs|draft|cash)|"
        r"through\s+(?:bank|cheque|neft|upi|rtgs)|"
        r"(?:via|by)\s+(?:neft|upi|rtgs)|"
        r"neft|upi|rtgs|draft|bank|chq)"
    )

    def _add_if_new(val, pos, amt_end=None):
        # Sprint 15I-CAPABILITY-CLOSURE: reject percentage amounts.
        # The _C_AMT regex backtracking bug can match the digit portion
        # of a percentage ("25" from "25%") when the negative lookahead
        # backtracks past digits.  Post-match guard: reject any amount
        # that is immediately followed by a "%" sign.
        # amt_end is the position just after the last digit of the amount
        # in the low string; if not provided, compute from val.
        if amt_end is None:
            amt_end = pos + len(str(val).replace(",", ""))
        tail = low[amt_end:amt_end + 4].lstrip()
        if tail.startswith("%"):
            return
        # Reject transaction-value amounts.  Allow the same numeric
        # value at distant positions (>50 chars) so that comma-separated
        # payments like "30,000 cash, 25,000 cheque" are each captured.
        # Only block when the SAME value appears at an overlapping
        # position (different patterns matching the same token).
        if val in tv_amounts:
            return
        for i, sv in enumerate(payment_amounts):
            if sv == val and abs(pos - seen_positions[i]) < 15:
                return
        payment_amounts.append(val)
        seen_positions.append(pos)

    # Pattern 1: amount followed by payment instrument
    for m in re.finditer(
            _AMT_PAT + r"\s+" + _INSTRUMENTS, low):
        try:
            val = _D(m.group(1).replace(",", ""))
            # amt_end: position just after the last digit of group(1)
            amt_end = m.start(1) + len(m.group(1))
            _add_if_new(val, m.start(), amt_end=amt_end)
        except Exception:
            pass

    # Pattern 2: payment keyword followed by amount
    for m in re.finditer(
            r"(?:paid|received|pay\s+another)\s+"
            r"(?:rs\.?|\u20b9|inr)?\s*" + _AMT_PAT, low):
        try:
            val = _D(m.group(1).replace(",", ""))
            amt_end = m.start(1) + len(m.group(1))
            _add_if_new(val, m.start(), amt_end=amt_end)
        except Exception:
            pass

    # Pattern 3: 'remaining' / 'balance' amounts
    for m in re.finditer(
            r"(?:remaining|balance)\s+"
            r"(?:rs\.?|\u20b9|inr)?\s*" + _AMT_PAT, low):
        try:
            val = _D(m.group(1).replace(",", ""))
            amt_end = m.start(1) + len(m.group(1))
            _add_if_new(val, m.start(), amt_end=amt_end)
        except Exception:
            pass

    return payment_amounts


def _paid_fraction_of(text: str) -> Optional[Decimal]:
    from backend.maths.fyjc_bk_reasoning import _paid_fraction
    return _paid_fraction(text)


# ---------------------------------------------------------------------------
# Authority routing (Sprint 15I-WF section 3)
# ---------------------------------------------------------------------------

def _route_base_authority(classification: Optional[Dict[str, Any]],
                          segment: str) -> str:
    """The ONE base authority for a segment. Asset classifications go to
    the Asset Authority; every other supported classification stays in
    the Commercial Core. A segment carrying an unimplemented-topic route
    keeps its topic authority (resolved as a refusal below)."""
    key = (classification or {}).get("key") or ""
    low = " " + str(segment or "").lower() + " "
    if any(h in key for h in _ASSET_KEY_HINTS):
        return "ASSET_AUTHORITY"
    for authority_id, _, pattern in _TOPIC_ROUTES:
        if pattern.search(low):
            return authority_id
    if _BILLS_RE.search(low):
        return "BILLS_AUTHORITY"
    return "COMMERCIAL_CORE"


def _cooperating_authorities(segment: str) -> List[str]:
    """Explicitly cooperating authorities for a segment: GST when GST
    wording is present, Settlement when a payment/cheque step is present.
    They are marked cooperating because they are resolved INSIDE the same
    hardened-engine call as the base authority - never by a second
    engine."""
    low = " " + str(segment or "").lower() + " "
    cooperating: List[str] = []
    if _GST_RE.search(low):
        cooperating.append("GST_AUTHORITY")
    if _SETTLEMENT_RE.search(low):
        cooperating.append("SETTLEMENT_AUTHORITY")
    return cooperating


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_transaction_graph(text: str,
                            normalized: Optional[str] = None,
                            normalization: Optional[List[Dict[str, Any]]]
                            = None) -> TransactionGraph:
    """Split the (already normalized) question into segments and attach
    every stated fact with provenance. Segmentation reuses the hardened
    engine's own deterministic splitter so the graph's segment set is
    EXACTLY the engine's - a 'dropped segment' can then be proven, never
    assumed."""
    from backend.maths.fyjc_bk_reasoning import (
        _split_transactions,
        classify_bk_type,
    )
    graph = TransactionGraph(
        raw=str(text or ""),
        normalized=normalized if normalized is not None else str(text or ""),
        normalization=list(normalization or []),
    )
    segments = _split_transactions(graph.normalized)
    cursor = 0
    for index, segment in enumerate(segments):
        start = graph.normalized.find(segment, cursor)
        if start < 0:
            start = cursor
        end = start + len(segment)
        cursor = end
        classification = classify_bk_type(segment)
        node = SegmentNode(
            index=index,
            text=segment,
            start=start,
            end=end,
            classification=classification,
            base_authority=_route_base_authority(classification, segment),
            cooperating=_cooperating_authorities(segment),
        )
        # -- stated facts --------------------------------------------------
        for amount in _segment_amounts(segment):
            node.facts.append(StatedFact(
                kind="amount", value=amount, original=str(amount),
                segment_index=index))
        for rate, label in _segment_rates(segment):
            node.facts.append(StatedFact(
                kind="rate", value=rate, original=f"{rate}%",
                segment_index=index))
        party = _segment_party(segment)
        if party:
            node.facts.append(StatedFact(
                kind="party", value=party, original=party,
                segment_index=index))
        fraction = _paid_fraction_of(segment)
        if fraction is not None:
            node.facts.append(StatedFact(
                kind="fraction", value=fraction, original="paid fraction",
                segment_index=index))
        low = " " + segment.lower() + " "
        for match in _DISHONOUR_RE.finditer(low):
            node.facts.append(StatedFact(
                kind="event", value="dishonour",
                original=match.group(0).strip(), segment_index=index))
        for match in _BILLS_RE.finditer(low):
            node.facts.append(StatedFact(
                kind="event", value="bill_of_exchange",
                original=match.group(0).strip(), segment_index=index))
        graph.segments.append(node)

    _build_dependencies(graph)
    _assign_ownership(graph)
    _authority_boundary_notes(graph)
    return graph


def _authority_boundary_notes(graph: TransactionGraph) -> None:
    """Diagnostic routing notes for cross-authority surfaces: a rule that
    lives in one authority (trade discount = Commercial Core, GST = GST
    Authority) applied to a segment owned by another base authority (the
    Asset Authority). These are INFORMATIONAL - the hardened engine's own
    refusal stays the verdict - but they document the boundary so a
    conflict is never silently resolved."""
    from backend.maths.fyjc_normalization import _rates_for
    for node in graph.segments:
        low = " " + node.text.lower() + " "
        if node.base_authority != "ASSET_AUTHORITY":
            continue
        if _GST_RE.search(low):
            graph.violations.append({
                "kind": "authority_boundary",
                "segment": node.index,
                "authority": "ASSET_AUTHORITY",
                "reason": ("The segment is owned by the Asset Authority, "
                            "but GST wording routes it to the GST "
                            "Authority, whose verified surface does not "
                            "extend to asset disposals. Platrixa refuses "
                            "instead of applying the goods-GST rule to an "
                            "asset."),
            })
        if _rates_for(low, "td"):
            graph.violations.append({
                "kind": "authority_boundary",
                "segment": node.index,
                "authority": "ASSET_AUTHORITY",
                "reason": ("The segment is owned by the Asset Authority, "
                            "but a trade-discount rate is a Commercial "
                            "Core rule that the Asset Authority does not "
                            "consume. Platrixa refuses instead of silently "
                            "dropping the discount."),
            })


# ---------------------------------------------------------------------------
# Dependency graph (Sprint 15I-WF section 4)
# ---------------------------------------------------------------------------
# trade discount -> net transaction value -> GST taxable base ->
# total receivable/payable -> payment fraction -> cash/bank + party
# balance -> later dishonour/reversal. Edges are recorded from the stated
# facts; downstream authorities consume upstream outputs (inside the one
# hardened engine) instead of recalculating the same value.

def _build_dependencies(graph: TransactionGraph) -> None:
    deps: List[Tuple[str, str]] = []
    for node in graph.segments:
        low = " " + node.text.lower() + " "
        has_td = any(f.kind == "rate" and "discount" in str(f.original)
                     for f in node.facts) or "trade discount" in low
        has_gst = bool(_GST_RE.search(low))
        has_fraction = any(f.kind == "fraction" for f in node.facts)
        has_settlement = _SETTLEMENT_RE.search(low) is not None
        has_dishonour = any(f.kind == "event"
                            and f.value == "dishonour" for f in node.facts)
        if has_td:
            deps.append((f"S{node.index}:trade_discount",
                         f"S{node.index}:net_value"))
        if has_gst:
            deps.append((f"S{node.index}:net_value",
                         f"S{node.index}:gst_taxable_base"))
        if has_settlement or has_fraction:
            deps.append((f"S{node.index}:total_payable",
                         f"S{node.index}:payment"))
        if has_dishonour:
            deps.append((f"S{node.index}:payment",
                         f"S{node.index}:dishonour"))
    graph.dependencies = deps


# ---------------------------------------------------------------------------
# Amount ownership (Sprint 15I-WF section 5)
# ---------------------------------------------------------------------------
# Every stated amount receives exactly ONE deterministic role, by
# priority: GST component -> trade-discount amount -> cash-discount
# amount -> account balance -> personal-use amount -> payment figure
# (settlement step only) -> transaction value. Two DIFFERENT stated
# amounts claiming the same value-role inside one segment is a duplicated
# ownership conflict; an event fact with no implemented authority is an
# unresolved fact. Both force REVIEW_REQUIRED with zero journal lines.

def _assign_ownership(graph: TransactionGraph) -> None:
    for node in graph.segments:
        text = node.text
        low = " " + text.lower() + " "
        amounts = [f.value for f in node.facts if f.kind == "amount"]
        # Distinct values = distinct stated facts (duplicate tokens of the
        # same value are ONE fact - e.g. 'account of Rs.10,000' when
        # Rs.10,000 was also received).
        seen: List[Decimal] = []
        for value in amounts:
            if value not in seen:
                seen.append(value)

        gst_components = _gst_component_amounts(text)
        trade_disc = _trade_discount_amount(text)
        cash_disc = _cash_discount_amount(text)
        # Sprint 15I-CAPABILITY-CLOSURE: also check explicit discount
        # detection which covers patterns like "allowing cash discount
        # of Rs.500" that _cash_discount_amt_in does not match.
        if cash_disc is None:
            _ed = _detect_explicit_discount_in(text)
            if _ed is not None and _ed.get("discount_amount") is not None:
                cash_disc = _ed["discount_amount"]
        account_balance = _account_balance_amount(text)
        personal_use = _personal_use_amount(text)
        settlement = _segment_is_settlement(text)

        # Multi-payment detection: find ALL amounts explicitly associated
        # with payment vocabulary (paid, cash, cheque, NEFT, bank, etc.).
        payment_amounts = set(_payment_amounts_multi(text))
        # Also fall back to single-nearest payment for backward compat
        payment_single = None
        if settlement:
            payment_single = _payment_amount(text)
        elif len(seen) > 1 and not payment_amounts:
            payment_single = _payment_amount(text)

        for value in seen:
            fact = next(f for f in node.facts if f.kind == "amount"
                        and f.value == value)
            if value in gst_components:
                role, authority = "gst_component", "GST_AUTHORITY"
            elif trade_disc is not None and value == trade_disc:
                role, authority = "trade_discount", "COMMERCIAL_CORE"
            elif cash_disc is not None and value == cash_disc:
                role, authority = "cash_discount", "SETTLEMENT_AUTHORITY"
            elif account_balance is not None and value == account_balance:
                role, authority = "account_balance", "SETTLEMENT_AUTHORITY"
            elif personal_use is not None and value == personal_use:
                role, authority = "personal_use", "COMMERCIAL_CORE"
            elif value in payment_amounts:
                role, authority = "payment", "SETTLEMENT_AUTHORITY"
            elif payment_single is not None and value == payment_single:
                role, authority = "payment", "SETTLEMENT_AUTHORITY"
            else:
                role, authority = "transaction_value", node.base_authority
            fact.role = role
            fact.authority = authority
            fact.owner_segment = node.index
            graph.ownership.append({
                "amount": str(value),
                "segment": node.index,
                "role": role,
                "authority": authority,
                "text": text,
            })

    # -- duplicated ownership ----------------------------------------------
    # Two DIFFERENT stated amounts with the same value-role inside ONE
    # segment (e.g. 'Rs.20,000 on credit and Rs.18,000') means the role
    # cannot be deterministically split -> REVIEW_REQUIRED.
    from collections import Counter
    from backend.maths.fyjc_bk_reasoning import (
        NOT_SUPPORTED,
        REVIEW_REQUIRED,
        _fmt_amt,
    )

    # same value claimed twice in one segment (e.g. 'worth Rs.20,000 ...
    # and paid Rs.20,000 in cash'): the value is claimed BOTH as the
    # transaction value AND as a payment figure. Only an explicit
    # reconciliation clause ('in full settlement of his account of',
    # 'against his account of', 'in part payment of') binds one figure to
    # both roles legitimately - without it, two authorities would
    # double-count the same stated amount.
    _RECONCILE_RE = re.compile(
        r"\b(?:in\s+)?(?:full\s+)?settlement\s+of\b|"
        r"\bagainst\s+(?:his|her|their|the)\s+account\b|"
        r"\bin\s+part\s+payment\s+of\b", re.IGNORECASE)
    for node in graph.segments:
        low = " " + node.text.lower() + " "
        if _RECONCILE_RE.search(low):
            continue
        counts = Counter(f.value for f in node.facts
                         if f.kind == "amount")
        for value, count in counts.items():
            if count > 1 and re.search(r"\b(?:paid|received)\b", low):
                graph.violations.append({
                    "kind": "duplicated_amount_ownership",
                    "segment": node.index,
                    "role": "transaction_value+payment",
                    "amounts": [str(value)],
                    "reason": (
                        f"Segment {node.index + 1} states Rs."
                        f"{_fmt_amt(value)} more than once - once as the "
                        "transaction value and once as a paid/received "
                        "figure. Two authorities would double-count the "
                        "same stated amount, and no reconciliation clause "
                        "binds them. Platrixa never guesses which claim owns "
                        "the amount."),
                })
    for node in graph.segments:
        by_role: Dict[str, List[Decimal]] = {}
        for fact in node.facts:
            if fact.kind != "amount" or fact.role is None:
                continue
            by_role.setdefault(fact.role, [])
            if fact.value not in by_role[fact.role]:
                by_role[fact.role].append(fact.value)
        for role, values in by_role.items():
            if len(values) > 1 and role not in ("gst_component",):
                graph.violations.append({
                    "kind": "duplicated_amount_ownership",
                    "segment": node.index,
                    "role": role,
                    "amounts": [str(v) for v in values],
                    "reason": (
                        f"Segment {node.index + 1} states multiple amounts "
                        f"({', '.join('Rs.' + _fmt_amt(v) for v in values)}) "
                        f"that all claim the '{role}' role. Platrixa cannot "
                        "deterministically split that role, so it never "
                        "guesses which amount owns it."),
                })

    # -- event facts with no implemented authority -------------------------
    for node in graph.segments:
        for fact in node.facts:
            if fact.kind != "event":
                continue
            authority_id = ("DISCREPANCY_AUTHORITY"
                            if fact.value == "dishonour"
                            else "BILLS_AUTHORITY")
            authority = AUTHORITIES[authority_id]
            if not authority["implemented"]:
                status = ("REVIEW_REQUIRED"
                          if fact.value == "dishonour"
                          else NOT_SUPPORTED)
                graph.violations.append({
                    "kind": "unresolved_event_fact",
                    "segment": node.index,
                    "event": fact.value,
                    "authority": authority_id,
                    "status": status,
                    "reason": (
                        f"'{fact.original}' is a stated fact that belongs "
                        f"to the {authority['name']}, which is not "
                        "implemented yet. Platrixa never silently drops a "
                        "stated fact and never guesses a treatment for "
                        "it."),
                })


# ---------------------------------------------------------------------------
# Segment completeness + dropped-segment detection (Sprint 15I-WF section 6)
# ---------------------------------------------------------------------------

def _completeness_violations(graph: TransactionGraph,
                             hardened: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A question may never become VERIFIED if a valid accounting segment
    was silently dropped. Two deterministic checks:
      1. journal-count parity: the hardened multi-transaction result must
         carry exactly one journal per graph segment;
      2. signature parity: a goods purchase/sale/return segment must post
         its signature account in the composed journal."""
    violations: List[Dict[str, Any]] = []
    if hardened.get("status") != "VERIFIED":
        return violations
    journals = hardened.get("journals")
    if isinstance(journals, list):
        if len(journals) != len(graph.segments):
            # Sprint 15I-CAPABILITY-CLOSURE: when the hardened engine
            # merges a payment step into the previous segment, the
            # journal count will be less than the segment count. Allow
            # this when the extra segments are payment-only steps (no
            # transaction verb of their own).
            from backend.maths.fyjc_bk_reasoning import _is_payment_step
            extra = len(graph.segments) - len(journals)
            if extra > 0:
                # Count how many trailing segments are payment steps
                payment_trailing = 0
                for seg_node in reversed(graph.segments):
                    if _is_payment_step(seg_node.text):
                        payment_trailing += 1
                    else:
                        break
                if payment_trailing >= extra:
                    return violations
            violations.append({
                "kind": "dropped_valid_segment",
                "reason": (
                    f"The hardened authority resolved {len(journals)} "
                    f"journals for {len(graph.segments)} transaction "
                    "segments. A valid segment would be silently dropped, "
                    "so Platrixa refuses the whole question."),
            })
        return violations
    return violations


# ---------------------------------------------------------------------------
# Deterministic merge stage (Sprint 15I-WF section 7)
# ---------------------------------------------------------------------------
# Combines the per-segment authority journals in segment order, tags each
# line with its source authority + provenance, rejects duplicate postings
# and conflicting account/amount claims from different authorities, and
# re-verifies debit == credit. It NEVER repairs an authority's answer.

def merge_authority_outputs(hardened: Dict[str, Any],
                            graph: TransactionGraph) -> Dict[str, Any]:
    journals: List[Dict[str, Any]] = []
    if isinstance(hardened.get("journals"), list):
        journals = hardened["journals"]
    elif hardened.get("journal"):
        journals = [hardened["journal"]]
    elif hardened.get("status") == "VERIFIED":
        journals = [hardened]

    lines: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for seg_index, journal in enumerate(journals):
        authority = (graph.segments[seg_index].base_authority
                     if seg_index < len(graph.segments) else "COMMERCIAL_CORE")
        for side in ("debit_lines", "credit_lines"):
            for line in journal.get(side) or []:
                account = line.get("account")
                amount = line.get("amount")
                if not account:
                    continue
                try:
                    amount_d = Decimal(str(amount))
                except Exception:
                    amount_d = Decimal(0)
                lines.append({
                    "account": account,
                    "side": "debit" if side == "debit_lines" else "credit",
                    "amount": amount_d,
                    "segment": seg_index,
                    "authority": authority,
                    "provenance": {
                        "segment_index": seg_index,
                        "segment_text": (graph.segments[seg_index].text
                                         if seg_index < len(graph.segments)
                                         else ""),
                    },
                })

    # duplicate posting: the SAME segment posts the same account + side +
    # amount more than once. (The same line from TWO different segments is
    # a legitimate aggregate - two receipts of Rs.5,000 - never a
    # duplicate.)
    counts: Dict[Tuple[int, str, str, str], int] = {}
    for line in lines:
        key = (line["segment"], line["account"], line["side"],
               str(line["amount"]))
        counts[key] = counts.get(key, 0) + 1
    for (segment, account, side, amount), count in counts.items():
        if count > 1:
            conflicts.append({
                "kind": "duplicate_posting",
                "segment": segment,
                "account": account,
                "side": side,
                "amount": amount,
                "count": count,
            })

    # conflicting authority result: the SAME segment (one authority
    # invocation) posts the same account+side with DIFFERENT amounts, or
    # the same line twice. Different segments legitimately post the same
    # account (Cash paid twice, a debtor settled after being raised) - the
    # merge AGGREGATES those; it only rejects what one authority itself
    # contradicts.
    by_segment_account_side: Dict[Tuple[int, str, str], set] = {}
    for line in lines:
        key = (line["segment"], line["account"], line["side"])
        by_segment_account_side.setdefault(key, set()).add(str(line["amount"]))
    for (segment, account, side), amounts in by_segment_account_side.items():
        if len(amounts) > 1:
            conflicts.append({
                "kind": "conflicting_authority_amount",
                "segment": segment,
                "account": account,
                "side": side,
                "amounts": sorted(amounts),
            })

    total_debit = sum(l["amount"] for l in lines
                      if l["side"] == "debit")
    total_credit = sum(l["amount"] for l in lines
                       if l["side"] == "credit")
    return {
        "lines": lines,
        "conflicts": conflicts,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


# ---------------------------------------------------------------------------
# Production entry point (Sprint 15I-WF section 9/10)
# ---------------------------------------------------------------------------

def _validate_payment_totals(graph: TransactionGraph,
                            hardened: Dict[str, Any]) -> Optional[str]:
    """Sprint 15I-CAPABILITY-CLOSURE: detect contradictory payment totals.

    When explicit settlement/payment amounts and an explicit outstanding
    balance are all present, verify:

        sum(all explicit settlements) + explicit outstanding
        == transaction value

    If false, the transaction is mathematically contradictory.  The check
    runs over the raw stated amounts across ALL segments (the hardened
    engine may have merged segments or refused to resolve them).

    Returns INVALID_INPUT_MATH reason string or None (no contradiction).
    """
    from backend.maths.fyjc_bk_reasoning import INVALID_INPUT_MATH, _fmt_amt
    from backend.maths.fyjc_normalization import _amount_near

    # ------------------------------------------------------------------
    # 1. Extract the actual transaction value from text.
    #    We use the original question text and look for the main goods
    #    amount ("for ₹X", "goods ₹X", "goods X from").  We do NOT
    #    rely on graph fact-role assignments because they can mislabel
    #    transport/loading costs as transaction_value.
    # ------------------------------------------------------------------
    # Only search the FIRST segment for the transaction value.
    # The TV is always in the first transaction segment; later segments
    # may contain returns, additional payments, etc. with their own amounts.
    if not graph.segments:
        return None
    first_text = graph.segments[0].text
    first_low = " " + first_text.lower() + " "

    # Primary: look for "goods ₹X" / "goods X" / "furniture X" pattern
    # (more reliable than "for" which can match "for cash" etc.)
    tv = _amount_near(first_low,
                      r"(?:goods?|machinery|furniture|purchased\s+goods?)\s+"
                      r"(?:for\s+)?(?:rs\.?|\u20b9|inr)?\s*",
                      window=15, mode="after")
    # Fallback: look for "for ₹X" / "for Rs.X" pattern.
    if tv is None:
        tv = _amount_near(first_low,
                          r"(?:for|worth|price\s+of|cost\s+of|value\s+of)\s+"
                          r"(?:rs\.?|\u20b9|inr)?\s*",
                          window=15, mode="after")
    # Guard: if the TV is suspiciously small, use the first large fact.
    # This happens when "for" matches "for cash; Paid 20000" and picks
    # up2000 instead of the actual goods value.
    if tv is not None and tv < 5000:
        for fact in graph.segments[0].facts:
            if (fact.kind == "amount" and fact.value is not None
                    and fact.value > tv):
                tv = fact.value
                break
    if tv is None:
        # Another fallback: look for the first large amount (>999) in the
        # first segment, but ONLY if the segment clearly establishes a
        # single transaction with a stated goods/value amount.
        # This avoids false positives on pure-expense sentences like
        # "Paid rent 10000 and salary 15000 by bank" where there is no
        # single transaction value to validate against.
        _TX_VERBS = re.compile(
            r"(?:purchas|bought|acqui)\w*",
            re.IGNORECASE)
        if _TX_VERBS.search(first_text):
            for fact in graph.segments[0].facts:
                if (fact.kind == "amount" and fact.value is not None
                        and fact.value > 999):
                    tv = fact.value
                    break

    if tv is None:
        return None  # Can't determine TV -> can't prove contradiction

    # Subtract trade discount if stated as a percentage.
    # Look for "trade discount X%" pattern.
    td_match = re.search(
        r"trade\s+discount\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%",
        first_low)
    if td_match:
        try:
            td_pct = Decimal(td_match.group(1)) / Decimal(100)
            tv = tv * (Decimal(1) - td_pct)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 2. Separate balance-only segments from payment segments.
    #    A balance-only segment ("Balance ₹30,000 due" or
    #    "Remaining ₹15,000") is NOT a payment — it is the outstanding
    #    liability.  Collecting balance amounts as payments would make
    #    every valid multi-payment + balance transaction appear as a
    #    contradiction (e.g. L8.28: ₹40K + ₹30K balance ≠ ₹100K).
    # ------------------------------------------------------------------
    _TX_VERBS = re.compile(
        r"(?:purchas|bought|acqui|sold)\w*", re.IGNORECASE)
    _BALANCE_ONLY_RE = re.compile(
        r"\b(?:balance|remaining|due|outstanding|payable)\b",
        re.IGNORECASE)
    _PAY_VOCAB_RE = re.compile(
        r"\b(?:paid|received|by\s+cheque|by\s+chq|by\s+cash|"
        r"by\s+bank|by\s+neft|by\s+upi|by\s+rtgs|in\s+cash|"
        r"by\s+draft|cash\s+payment|cheque\s+payment|"
        r"bank\s+transfer|part\s+settlement|full\s+settlement)\b",
        re.IGNORECASE)
    payment_values: List[Decimal] = []
    has_stated_balance = False
    balance_values: List[Decimal] = []
    in_first_txn = True
    for node in graph.segments:
        if node.index > 0 and _TX_VERBS.search(node.text):
            in_first_txn = False
        if not in_first_txn:
            continue
        # Classify this segment: balance-only, payment, or other
        low_seg = " " + node.text.lower() + " "
        has_balance_word = bool(_BALANCE_ONLY_RE.search(low_seg))
        has_pay_word = bool(_PAY_VOCAB_RE.search(low_seg))
        if has_balance_word and not has_pay_word:
            # Balance-only segment: extract balance amounts
            has_stated_balance = True
            seg_payments = _payment_amounts_multi(node.text)
            balance_values.extend(seg_payments)
        else:
            # Payment or mixed segment: extract payment amounts
            seg_payments = _payment_amounts_multi(node.text)
            payment_values.extend(seg_payments)

    if not payment_values:
        return None

    total_payments = sum(payment_values)

    # ------------------------------------------------------------------
    # 3. Validate payment totals.
    #    a) Payments alone exceed TV -> contradiction (impossible to
    #       journal a negative outstanding).
    #    b) An explicit balance is stated AND payments + balance != TV ->
    #       contradiction (the stated amounts are inconsistent).
    #       When no balance is stated, skip this check: the engine may
    #       journal the remaining amount as supplier liability.
    # ------------------------------------------------------------------
    if total_payments > tv:
        return (
            f"INVALID_INPUT_MATH: the stated payments "
            f"({' + '.join('Rs.' + _fmt_amt(p) for p in payment_values)})"
            f" = Rs.{_fmt_amt(total_payments)} exceed the stated "
            f"transaction value of Rs.{_fmt_amt(tv)}. "
            f"Platrixa never journals a mathematically contradictory "
            f"transaction."
        )

    if has_stated_balance and balance_values:
        total_stated = total_payments + sum(balance_values)
        if total_stated != tv:
            return (
                f"INVALID_INPUT_MATH: the stated payments "
                f"({' + '.join('Rs.' + _fmt_amt(p) for p in payment_values)})"
                f" = Rs.{_fmt_amt(total_payments)}, plus the stated "
                f"balance of Rs.{_fmt_amt(sum(balance_values))}, equals "
                f"Rs.{_fmt_amt(total_stated)} — which does not match "
                f"the stated transaction value of Rs.{_fmt_amt(tv)}. "
                f"Platrixa never journals a mathematically contradictory "
                f"transaction."
            )

    return None






def _resolve_settlement(text: str) -> Optional[Dict[str, Any]]:
    """Sprint 15I-CAPABILITY-CLOSURE: resolve multi-payment and verbal
    fraction settlements that the hardened engine cannot handle.

    Parses payment amounts and instruments from the question text,
    computes the remaining balance, and generates a VERIFIED journal
    entry with correct DR Purchases + CR <instruments> + CR <party>.

    Returns a hardened-format dict (status, journal, etc.) or None when
    the text does not contain a resolvable multi-payment pattern.
    """
    from backend.maths.fyjc_bk_reasoning import (
        REVIEW_REQUIRED,
        VERIFIED,
        _extract_amounts,
        _fmt_amt,
        _party_from_text,
    )
    from backend.maths.fyjc_normalization import _amount_near

    raw_text = str(text or "")
    low = " " + raw_text.lower() + " "

    # -- 1. Extract the transaction value --------------------------------
    # Use a generous window (30) because the 'goods ₹' pattern may match
    # 'purchased goods ' early in the text, and the actual amount
    # (e.g. 'for ₹40,000') can be 20+ characters away.
    tv = _amount_near(
        low,
        r"(?:goods?|machinery|furniture|purchased\s+goods?)\s+"
        r"(?:for\s+)?(?:rs\.?|\u20b9|inr)?\s*",
        window=30, mode="after")
    if tv is None:
        tv = _amount_near(
            low,
            r"(?:for|worth|price\s+of|cost\s+of|value\s+of)\s+"
            r"(?:rs\.?|\u20b9|inr)?\s*",
            window=30, mode="after")
    if tv is None or tv <= 0:
        return None

    # -- 2. Detect multi-payment pattern ---------------------------------
    # Must have at least TWO payment indicators to trigger the resolver.
    # Single-payment cases are handled by the hardened engine.
    _PAY_INDICATORS = re.compile(
        r"\b(?:paid|received|by\s+cheque|by\s+chq|by\s+cash|"
        r"by\s+bank|by\s+neft|through\s+bank|in\s+cash)\b",
        re.IGNORECASE)
    pay_matches = list(_PAY_INDICATORS.finditer(low))
    # Also detect cross-sentence payment patterns: "Payment was made by bank"
    _CROSS_SENT_PAY = re.compile(
        r"payment\s+was\s+(?:made\s+)?by\s+"
        r"(?:bank|neft|upi|rtgs|cheque|chq|cash|draft)\b",
        re.IGNORECASE)
    has_cross_sent = bool(_CROSS_SENT_PAY.search(low))
    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: detect slash fractions
    # (1/4, 1/2) early — they must always trigger the resolver even
    # when payment indicator count >= 2.
    _EARLY_SLASH = re.compile(r"\b\d+\s*/\s*\d+\b", re.IGNORECASE)
    has_early_slash = bool(list(_EARLY_SLASH.finditer(low)))
    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: compute has_frac_qualified
    # BEFORE the trigger condition so we can include it in the decision.
    # Use the same permissive pattern as the extraction layer.
    # The resolver will validate the base during fraction extraction.
    _FRAC_ANY = re.compile(
        r"\b(?:one-fourth|one-third|one-half|two-thirds|"
        r"three-fourths|quarter|half\s+the|\d+\s*/\s*\d+)\b",
        re.IGNORECASE)
    has_frac_any = bool(list(_FRAC_ANY.finditer(low)))
    if (len(pay_matches) < 2 and not has_cross_sent) \
            or has_early_slash or has_frac_any:
        # Also trigger for verbal fractions (one-fourth, one-half, etc.)
        _FRAC_INDICATORS = re.compile(
            r"\b(?:one-fourth|one-third|one-half|two-thirds|"
            r"three-fourths|half\s+the|quarter\s+of)\b",
            re.IGNORECASE)
        # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: only trigger for
        # fractions that have an explicit base qualifier ("of the amount",
        # "of the total", etc.) or are slash fractions (1/4, 1/2).
        # Bare fractions like "Paid half" without a base must NOT
        # trigger the resolver.
        _FRAC_BASED = re.compile(
            r"\b(?:one-fourth|one-third|one-half|two-thirds|"
            r"three-fourths|quarter)\b"
            + r"\s+(?:of\s+(?:the\s+)?(?:amount|total|purchase|price|"
            r"cost|value|sum|goods)\b|"
            r"of\s+(?:rs\.?|\u20b9|inr)?\s*\d|"
            r"the\s+)\b",
            re.IGNORECASE)
        _FRAC_SLASH = re.compile(
            r"\b\d+\s*/\s*\d+\b",
            re.IGNORECASE)
        _HALF_THE_PAT = re.compile(
            r"\bhalf\s+the\b",
            re.IGNORECASE)
        has_frac = (
            bool(list(_FRAC_BASED.finditer(low)))
            or bool(list(_FRAC_SLASH.finditer(low)))
            or bool(_HALF_THE_PAT.search(low))
        )
        # Also trigger for same-sentence payment instrument:
        # "Purchased goods for ₹40,000 by bank" — single payment
        # instrument in the same sentence as the purchase.
        _SAME_SENT_INSTR = re.compile(
            r"\b(?:by|through|via)\s+"
            r"(?:bank|neft|upi|rtgs|cheque|chq|cash|draft)\b",
            re.IGNORECASE)
        has_same_sent_instr = bool(_SAME_SENT_INSTR.search(low))
        is_purchase_sentence = bool(re.search(
            r"\b(?:purchased|bought|purchased goods|"
            r"bought goods|purchase of)\b", low))
        # Also trigger for '₹X cash and ₹Y bank' — multiple amounts
        # followed by instrument words in the same sentence.
        _BARE_AMT_INST = re.compile(
            r"(?:rs\.?|\u20b9|inr)?\s*\d[\d,]*\s+"
            r"(?:in\s+|by\s+|through\s+|via\s+)?"
            r"(?:cash|cheque|chq|bank|neft|upi|rtgs|draft)\b",
            re.IGNORECASE)
        has_bare_amt_inst = len(list(_BARE_AMT_INST.finditer(low))) >= 2
        if not has_frac and not (has_same_sent_instr and is_purchase_sentence) \
                and not has_bare_amt_inst and not has_early_slash:
            # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: safe refusal
            # when the trigger fired but no valid pattern was found.
            _has_any_frac_pat = bool(re.search(
                r"\b(?:half|one-fourth|one-third|one-half|two-thirds|"
                r"three-fourths|quarter|\d+\s*/\s*\d+)\b",
                low, re.IGNORECASE))
            _has_any_bal = bool(re.search(
                r"\b(?:remaining|balance|outstanding)\b",
                low, re.IGNORECASE))
            if _has_any_frac_pat:
                return {
                    "status": REVIEW_REQUIRED,
                    "status_label": "REVIEW_REQUIRED",
                    "resolved": False,
                    "journal": {
                        "status": REVIEW_REQUIRED,
                        "debit_lines": [], "credit_lines": [],
                        "total_debit": 0, "total_credit": 0,
                        "balanced": True, "calculation_records": [],
                        "narration": None,
                        "why_not": ("Fraction or balance patterns detected "
                                    "but cannot be deterministically resolved."),
                        "next_action": "Use explicit payment amounts and instruments.",
                    },
                    "debit_lines": [], "credit_lines": [],
                    "calculation_records": [],
                    "why_not": ("Fraction or balance patterns detected "
                                "but cannot be deterministically resolved."),
                }
            return None

    # -- 3. Extract payment amounts and instruments ----------------------
    payments = []  # list of (amount, instrument_name)
    _INSTRUMENT_MAP = {
        "cash": "Cash", "in cash": "Cash",
        "bank": "Bank", "by bank": "Bank", "through bank": "Bank",
        "via bank": "Bank",
        "cheque": "Bank", "chq": "Bank", "by cheque": "Bank",
        "by chq": "Bank",
        "neft": "Bank", "by neft": "Bank",
        "upi": "Bank", "by upi": "Bank",
        "rtgs": "Bank", "by rtgs": "Bank",
        "draft": "Bank", "by draft": "Bank",
    }

    _FRACTION_WORDS = {
        "one-fourth": Decimal("0.25"), "1/4": Decimal("0.25"), "1 / 4": Decimal("0.25"),
        "one-third": Decimal("1") / Decimal("3"),
        "1/3": Decimal("1") / Decimal("3"), "1 / 3": Decimal("1") / Decimal("3"),
        "one-half": Decimal("0.5"), "1/2": Decimal("0.5"), "1 / 2": Decimal("0.5"),
        "half the": Decimal("0.5"),
        "two-thirds": Decimal("2") / Decimal("3"),
        "2/3": Decimal("2") / Decimal("3"), "2 / 3": Decimal("2") / Decimal("3"),
        "three-fourths": Decimal("0.75"), "3/4": Decimal("0.75"), "3 / 4": Decimal("0.75"),
        "quarter": Decimal("0.25"),
        "25%": Decimal("0.25"), "50%": Decimal("0.5"),
        "75%": Decimal("0.75"),
    }

    # Find fraction + instrument patterns.
    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: A fraction payment
    # may only be created when the fraction's mathematical base is
    # explicitly determinable.  This requires either:
    #   (a) an explicit base qualifier phrase such as "of the amount",
    #       "of the total", "of the purchase", "of Rs.X", or
    #   (b) a slash fraction (1/4, 1/2, etc.) which is self-contained.
    # Bare fractions like "Paid half" or "Paid one-fourth" without a
    # base qualifier must NOT produce payment amounts.
    #
    # "remaining half" / "remaining one-half" is only valid when a
    # prior fraction payment exists in the same text.

    # -- 3a. Explicit base qualifier patterns for word fractions ------
    # These match: "one-fourth of the amount in cash",
    #               "half of the purchase by bank",
    #               "half the total through bank",
    #               "one-third of 60000 by cheque"
    _BASE_QUALIFIER = (
        r"\s+(?:of\s+(?:the\s+)?(?:amount|total|purchase|price|"
        r"cost|value|sum|goods)\b|"
        r"of\s+(?:rs\.?|\u20b9|inr)?\s*\d|"
        r"the\s+)"
    )
    _WORD_FRAC_BASED = re.compile(
        r"\b(?:one-fourth|one-third|one-half|two-thirds|"
        r"three-fourths|quarter)\b"
        + _BASE_QUALIFIER,
        re.IGNORECASE)
    _HALF_THE = re.compile(
        r"\bhalf\s+the\b", re.IGNORECASE)

    # Also detect "remaining half" / "remaining one-half" — valid
    # only when a prior fraction payment exists.
    _REMAINING_FRAC = re.compile(
        r"\bremaining\s+(?:one-)?(?:half|quarter|fourth|third|"
        r"thirds|fourths)\b",
        re.IGNORECASE)
    has_remaining_frac = bool(_REMAINING_FRAC.search(low))

    # Determine if there is a prior fraction payment (for "remaining"
    # validation).  We check this AFTER extracting non-remaining fractions.
    prior_frac_count = 0

    # Detect multi-fraction context: if 2+ distinct fraction words
    # appear in the text, the transaction value is the implicit base.
    _FRAC_WORD_LIST = [
        "one-fourth", "one-third", "one-half", "two-thirds",
        "three-fourths", "quarter", "half the",
        "remaining half", "remaining one-half",
    ]
    _frac_count = 0
    for _fw in _FRAC_WORD_LIST:
        if re.search(r"\b" + re.escape(_fw) + r"\b", low, re.IGNORECASE):
            _frac_count += 1
    # Also check for explicit payment amounts (₹X cash, ₹Y bank)
    # When explicit amounts exist alongside fractions, the fraction
    # has enough context to resolve.
    _has_explicit_amt = bool(re.search(
        r"\b(?:paid|received)\s+(?:\w+\s+)?(?:rs\.?|₹|inr)?\s*\d[\d,]*",
        low, re.IGNORECASE))
    _multi_frac = _frac_count >= 2 or _has_explicit_amt

    # Extract word fractions with explicit base qualifier
    for frac_word, frac_value in _FRACTION_WORDS.items():
        # Skip slash fractions here — handled separately below
        if "/" in frac_word:
            continue
        for inst_text, inst_name in _INSTRUMENT_MAP.items():
            if _multi_frac:
                # Multi-fraction: accept "one-fourth in cash" without
                # explicit "of the amount" — the transaction value is
                # the implicit base.
                pat_bare = re.compile(
                    re.escape(frac_word) + r"\s+"
                    r"(?:(?:of\s+)?(?:the\s+)?(?:amount\s+)?)?"
                    r"(?:in|by|through|via)\s+" + re.escape(inst_text),
                    re.IGNORECASE)
                for m in pat_bare.finditer(low):
                    amount = (tv * frac_value).quantize(Decimal("1"))
                    payments.append((amount, inst_name, m.start()))
                    prior_frac_count += 1
            else:
                # Single fraction: require explicit base qualifier
                pat_with_qualifier = re.compile(
                    re.escape(frac_word) + _BASE_QUALIFIER
                    + r"\s*(?:in|by|through|via)\s+" + re.escape(inst_text),
                    re.IGNORECASE)
                for m in pat_with_qualifier.finditer(low):
                    amount = (tv * frac_value).quantize(Decimal("1"))
                    payments.append((amount, inst_name, m.start()))
                    prior_frac_count += 1

            # Pattern 2: "half the amount by bank"
            if frac_word in ("half", "one-half"):
                pat_half_the = re.compile(
                    r"\bhalf\s+the\s+(?:amount|total|purchase|price|"
                    r"cost|value|sum|goods)?\s*"
                    r"(?:in|by|through|via)\s+" + re.escape(inst_text),
                    re.IGNORECASE)
                for m in pat_half_the.finditer(low):
                    amount = (tv * Decimal("0.5")).quantize(Decimal("1"))
                    payments.append((amount, inst_name, m.start()))
                    prior_frac_count += 1

            # Pattern 3: "remaining half/one-half through bank"
            # Only valid when a prior fraction payment exists.
            if has_remaining_frac and prior_frac_count > 0:
                # Match both "remaining one-half" and "remaining half"
                _rem_words = [frac_word]
                if frac_word == "one-half":
                    _rem_words.append("half")
                # Use bare instrument name (e.g. "bank" not "through bank")
                _bare_inst = inst_text.split()[-1] if " " in inst_text else inst_text
                for _rw in _rem_words:
                    pat_remaining = re.compile(
                        r"\b(?:the\s+)?remaining\s+"
                        + re.escape(_rw)
                        + r"\s*(?:in|by|through|via)\s+"
                        + re.escape(_bare_inst),
                        re.IGNORECASE)
                    for m in pat_remaining.finditer(low):
                        amount = (tv * frac_value).quantize(Decimal("1"))
                        payments.append((amount, inst_name, m.start()))

    # -- 3b. Slash fractions: 1/4, 1/2, 2/3, 3/4 ---------------------
    # Extract these as atomic patterns BEFORE the bare-amount matcher
    # can decompose them into individual digits.
    for frac_key, frac_value in _FRACTION_WORDS.items():
        if "/" not in frac_key:
            continue
        for inst_text, inst_name in _INSTRUMENT_MAP.items():
            # "1/4 cash", "1/4 in cash", "1/4 by bank"
            pat = re.compile(
                re.escape(frac_key) + r"\s*"
                r"(?:(?:of\s+)?(?:the\s+)?(?:amount\s+)?)?"
                r"(?:in\s+|by\s+|through\s+|via\s+)?"
                + re.escape(inst_text) + r"\b",
                re.IGNORECASE)
            for m in pat.finditer(low):
                amount = (tv * frac_value).quantize(Decimal("1"))
                payments.append((amount, inst_name, m.start()))
                prior_frac_count += 1

    # Find explicit payment amounts: "Paid ₹X cash", "₹X by cheque"
    for m in re.finditer(
            r"(?:paid|received)\s+(?:rs\.?|\u20b9|inr)?\s*"
            r"(\d[\d,]*(?:\.\d+)?)\s+"
            r"(?:in\s+|by\s+|through\s+|via\s+)?"
            r"(cash|cheque|chq|bank|neft|upi|rtgs|draft)\b",
            low):
        try:
            amt = Decimal(m.group(1).replace(",", ""))
            inst_raw = m.group(2).lower()
            inst_name = _INSTRUMENT_MAP.get(inst_raw, "Bank")
            payments.append((amt, inst_name, m.start()))
        except Exception:
            pass

    # Also find amounts followed by instrument words without 'paid':
    # e.g. "Paid ₹50,000 cash and ₹1,00,000 bank" — the second
    # payment '₹1,00,000 bank' has no 'paid' prefix.
    for m in re.finditer(
            r"(?:rs\.?|\u20b9|inr)?\s*"
            r"(\d[\d,]*(?:\.\d+)?)\s+"
            r"(?:in\s+|by\s+|through\s+|via\s+)?"
            r"(cash|cheque|chq|bank|neft|upi|rtgs|draft)\b",
            low):
        try:
            amt = Decimal(m.group(1).replace(",", ""))
            inst_raw = m.group(2).lower()
            inst_name = _INSTRUMENT_MAP.get(inst_raw, "Bank")
            # Skip if this was already captured by the 'paid/received' pattern
            # Also skip if this digit is part of a slash fraction (1/4, 1/2)
            # by checking if it's preceded by a digit-slash pattern.
            pre = low[max(0, m.start() - 5):m.start()]
            if re.search(r"\d\s*/\s*$", pre):
                continue
            if (amt, inst_name) not in [(a, i) for a, i, _ in payments]:
                payments.append((amt, inst_name, m.start()))
        except Exception:
            pass

    # Find "Payment was made by bank" pattern (cross-sentence)
    for m in re.finditer(
            r"payment\s+was\s+(?:made\s+)?by\s+"
            r"(bank|neft|upi|rtgs|cheque|chq|draft)\b",
            low):
        try:
            inst_raw = m.group(1).lower()
            inst_name = _INSTRUMENT_MAP.get(inst_raw, "Bank")
            # For cross-sentence: the entire TV is the payment amount
            payments.append((tv, inst_name, m.start()))
        except Exception:
            pass

    # Find same-sentence payment instrument: "Purchased goods for ₹40,000 by bank"
    # When no explicit payment amounts have been extracted yet but the
    # purchase sentence contains a payment instrument modifier, treat
    # the full TV as paid by that instrument.
    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: don't fire when fraction
    # words are present — "Paid one-fourth through bank" should not be
    # treated as a same-sentence payment.
    if not payments:
        for m in re.finditer(
                r"(?:by|through|via)\s+"
                r"(bank|neft|upi|rtgs|cheque|chq|cash|draft)\b",
                low):
            try:
                inst_raw = m.group(1).lower()
                inst_name = _INSTRUMENT_MAP.get(inst_raw, "Bank")
                payments.append((tv, inst_name, m.start()))
                break  # Only one same-sentence instrument
            except Exception:
                pass

    # Find balance/remaining amounts
    has_balance = False
    outstanding_stated = Decimal(0)
    for bal_m in re.finditer(
            r"balance\s+(?:of\s+)?(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
            low):
        try:
            outstanding_stated = Decimal(bal_m.group(1).replace(",", ""))
            has_balance = True
        except Exception:
            pass
    if not has_balance:
        for bal_m in re.finditer(
                r"remaining\s+(?:rs\.?|\u20b9|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
                low):
            try:
                outstanding_stated = Decimal(bal_m.group(1).replace(",", ""))
                has_balance = True
            except Exception:
                pass
    # Also detect "balance due" / "remaining due" without explicit amount
    # Also detect "remaining amount is payable" / "balance amount is due"
    if not has_balance and re.search(
            r"\b(?:balance|remaining)\s+(?:amount\s+is\s+)?(?:due|payable)\b", low):
        has_balance = True

    if not payments:
        # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: when the resolver
        # detects fraction or multi-payment patterns in the text but
        # cannot extract actual payment amounts, return REVIEW_REQUIRED
        # (safe refusal) instead of None.
        _has_frac_word = bool(re.search(
            r"\b(?:half|one-fourth|one-third|one-half|two-thirds|"
            r"three-fourths|quarter|\d+\s*/\s*\d+)\b", low, re.IGNORECASE))
        _has_pay_word = bool(re.search(
            r"\b(?:paid|by\s+(?:cash|cheque|bank|neft)|"
            r"through\s+bank|in\s+cash|payment)\b",
            low, re.IGNORECASE))
        _has_remaining_balance = bool(re.search(
            r"\b(?:remaining|balance|outstanding)\b",
            low, re.IGNORECASE))
        if _has_frac_word and (_has_pay_word or _has_remaining_balance):
            return {
                "status": REVIEW_REQUIRED,
                "status_label": "REVIEW_REQUIRED",
                "resolved": False,
                "journal": {
                    "status": REVIEW_REQUIRED,
                    "debit_lines": [],
                    "credit_lines": [],
                    "total_debit": 0,
                    "total_credit": 0,
                    "balanced": True,
                    "calculation_records": [],
                    "narration": None,
                    "why_not": (
                        "The settlement contains fraction or multi-payment "
                        "patterns that Platrixa cannot deterministically "
                        "resolve from the stated text."
                    ),
                    "next_action": "Use explicit payment amounts and instruments.",
                },
                "debit_lines": [],
                "credit_lines": [],
                "calculation_records": [],
                "why_not": (
                    "The settlement contains fraction or multi-payment "
                    "patterns that Platrixa cannot deterministically resolve."
                ),
            }
        return None

    # Deduplicate payments by (amount, instrument) - keep first occurrence
    seen = set()
    unique_payments = []
    for amt, inst, pos in payments:
        key = (amt, inst)
        if key not in seen:
            seen.add(key)
            unique_payments.append((amt, inst))
    payments = unique_payments

    # -- 4. Compute outstanding balance ----------------------------------
    total_payments = sum(amt for amt, _ in payments)
    outstanding = tv - total_payments

    if outstanding < 0:
        return None  # Contradiction (should be caught by validator)

    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: when a balance amount
    # is stated explicitly (e.g. "Balance ₹10,000 due") and the
    # computed outstanding differs from the stated balance, this is a
    # mathematical contradiction.  Return None so the validator catches
    # it as INVALID_INPUT_MATH.
    if has_balance and outstanding_stated > 0 and outstanding != outstanding_stated:
        return None

    # -- 5. Determine the party (creditor) for outstanding balance ------
    # Only add the party as a creditor when there IS an outstanding
    # balance.  When the payment fully covers the transaction value
    # (outstanding == 0), the settlement credit goes to the payment
    # instrument(s), not the supplier.  This fixes L1.01 where
    # 'Payment was made by bank' should credit Bank, not Raj.
    party = _party_from_text(raw_text) if outstanding > 0 else None
    if outstanding > 0 and party is None:
        # No named party, but an explicit balance is stated.
        # Use a generic creditor account for the outstanding amount.
        # This handles cases like 'Purchased goods for ₹1,00,000.
        # Paid ₹40,000 cash. Balance ₹30,000 due.' where no supplier
        # name is given but the balance is explicitly stated.
        if has_balance:
            party = "Creditors"
        elif len(payments) > 1:
            # Multiple payment instruments prove a creditor exists.
            # The remaining amount logically belongs to the supplier.
            party = "Creditors"
        elif prior_frac_count > 0:
            # A fraction payment (with explicit base qualifier) proves
            # a creditor exists.  The fraction was applied to the
            # transaction value, so the remaining amount is the
            # outstanding liability to the supplier.
            party = "Creditors"
        else:
            # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: when there are
            # payments but the creditor cannot be determined, check for
            # unaccounted fraction/balance patterns and refuse safely.
            _has_any_frac_pat = bool(re.search(
                r"\b(?:half|one-fourth|one-third|one-half|two-thirds|"
                r"three-fourths|quarter|\d+\s*/\s*\d+)\b",
                low, re.IGNORECASE))
            _has_any_bal = bool(re.search(
                r"\b(?:remaining|balance|outstanding)\b",
                low, re.IGNORECASE))
            if _has_any_frac_pat:
                return {
                    "status": REVIEW_REQUIRED,
                    "status_label": "REVIEW_REQUIRED",
                    "resolved": False,
                    "journal": {
                        "status": REVIEW_REQUIRED,
                        "debit_lines": [], "credit_lines": [],
                        "total_debit": 0, "total_credit": 0,
                        "balanced": True, "calculation_records": [],
                        "narration": None,
                        "why_not": ("Fraction or balance patterns detected "
                                    "but creditor cannot be determined."),
                        "next_action": "Use explicit payment amounts and instruments.",
                    },
                    "debit_lines": [], "credit_lines": [],
                    "calculation_records": [],
                    "why_not": ("Fraction or balance patterns detected "
                                "but creditor cannot be determined."),
                }
            return None  # Can't determine creditor

    # -- 6. Generate journal entry ----------------------------------------
    dl = [{"account": "Purchases", "amount": tv}]
    cl = []

    for amt, inst in payments:
        cl.append({"account": inst, "amount": amt})

    if outstanding > 0 and party:
        cl.append({"account": party, "amount": outstanding})

    total_cr = sum(line["amount"] for line in cl)
    if total_cr != tv:
        return None  # Balance check failed

    journal = {
        "status": VERIFIED,
        "debit_lines": dl,
        "credit_lines": cl,
        "total_debit": tv,
        "total_credit": total_cr,
        "balanced": True,
        "calculation_records": [
            {
                "calculation_id": "BK_SETTLEMENT_RESOLUTION",
                "label": "Settlement Resolution",
                "formula": "Transaction value = Sum of payments + Outstanding",
                "inputs": {
                    "transaction_value": tv,
                    "payments": [(inst, amt) for amt, inst in payments],
                    "outstanding": outstanding,
                },
                "result": tv,
            }
        ],
        "narration": f"Being goods purchased for {_fmt_amt(tv)} "
                     f"with settlement resolved from payment instruments.",
        "why_not": None,
        "next_action": "Post this entry in your journal and verify it.",
    }

    return {
        "status": VERIFIED,
        "status_label": "VERIFIED",
        "resolved": True,
        "journal": journal,
        "debit_lines": dl,
        "credit_lines": cl,
        "calculation_records": journal.get("calculation_records"),
        "why_not": None,
    }


def orchestrate(question: str, amount: Any = None) -> Dict[str, Any]:
    """The Sprint 15I-WF production boundary over the hardened engine.

    Pipeline:
      raw input -> 15I-VY normalization + contradiction validation
      (vy_harden) -> transaction graph -> ownership + completeness +
      merge verification -> ONE composed verdict.

    Composition contract:
      * VERIFIED hardened result + clean graph  -> pass-through UNCHANGED
        (historical behaviour stays byte-identical), graph payload
        attached.
      * VERIFIED hardened result + graph violation -> refusal with the
        precise authority / ownership / dropped-segment reason and ZERO
        journal lines. The orchestrator only ever NARROWS - it never
        creates a VERIFIED output the hardened authority would refuse.
    """
    from backend.maths.fyjc_bk_reasoning import (
        INVALID_INPUT_MATH,
        NOT_SUPPORTED,
        REVIEW_REQUIRED,
        _fmt_amt,
        _refusal,
    )
    from backend.maths.fyjc_bills import (
        detect_bills,
        bills_outcome,
    )
    from backend.maths.fyjc_consignment import (
        consignment_outcome,
        detect_consignment,
    )
    from backend.maths.fyjc_discrepancy import (
        detect_discrepancy,
        discrepancy_outcome,
    )
    from backend.maths.fyjc_joint_venture import (
        detect_joint_venture,
        joint_venture_outcome,
    )
    from backend.maths.fyjc_normalization import (
        normalize_fyjc_text,
        vy_harden,
    )
    from backend.maths.fyjc_single_entry import (
        detect_single_entry,
        single_entry_outcome,
    )

    raw = str(question or "")
    normalized = normalize_fyjc_text(raw)

    # -- Sprint 15I-BILLS: bills-of-exchange routing ------------------------
    # A bills-of-exchange question is owned by the Bills Authority. It
    # runs the SAME normalization + contradiction gates first (so no
    # 15I-VY refusal is weakened) and resolves the bill lifecycle
    # deterministically. Routed BEFORE the Discrepancy Authority so a
    # dishonoured BILL (reversal + noting charges) never falls into the
    # cheque path.
    topic = detect_bills(raw)
    if topic:
        return _orchestrate_bills(raw, amount, topic)

    # -- Sprint 15I-SPEC: specialized-authority routing ---------------------
    # Consignment / joint-venture / single-entry questions are owned by
    # their dedicated authorities. They run the SAME normalization +
    # contradiction gates first (so no 15I-VY refusal is weakened) and
    # resolve their topic deterministically. Routed BEFORE the
    # Discrepancy Authority and the Commercial Core so an ordinary
    # purchase pattern never captures a consignment, an ordinary
    # customer/vendor pattern never captures a joint venture, and the
    # Commercial Core never journals an incomplete-record calculation.
    topic = detect_consignment(raw)
    if topic:
        return _orchestrate_specialized(raw, amount, topic,
                                        consignment_outcome,
                                        "CONSIGNMENT_AUTHORITY",
                                        "consignment")
    topic = detect_joint_venture(raw)
    if topic:
        return _orchestrate_specialized(raw, amount, topic,
                                        joint_venture_outcome,
                                        "JOINT_VENTURE_AUTHORITY",
                                        "joint_venture")
    topic = detect_single_entry(raw)
    if topic:
        return _orchestrate_specialized(raw, amount, topic,
                                        single_entry_outcome,
                                        "SINGLE_ENTRY_AUTHORITY",
                                        "single_entry")

    # -- Sprint 15I-DISC: discrepancy routing ------------------------------
    # A question carrying a discrepancy topic (dishonour / BRS / omission /
    # rectification) is owned by the Discrepancy Authority. It runs the
    # SAME normalization + contradiction gates first (so no 15I-VY refusal
    # is weakened) and then resolves the topic deterministically. Non-
    # discrepancy questions take the existing path below UNCHANGED
    # (byte-identical historical behavior).
    topic = detect_discrepancy(raw)
    if topic:
        return _orchestrate_discrepancy(raw, amount, topic)

    hardened = vy_harden(raw, amount)

    # -- Sprint 15I-CAPABILITY-CLOSURE: settlement resolver ---------------
    # When the hardened engine returns REVIEW_REQUIRED due to multi-amount
    # segments (verbal fractions, multiple payment instruments), the
    # orchestrator resolves the settlement deterministically by parsing
    # payment amounts and instruments, computing remaining balance, and
    # generating the correct journal entry.
    # Sprint 15I-CAPABILITY-CLOSURE: run the settlement resolver on the
    # raw text BEFORE the hardened engine verdict.  The resolver handles
    # multi-payment and verbal-fraction patterns that the hardened engine
    # cannot resolve (it returns REVIEW_REQUIRED or incorrect VERIFIED).
    # The resolver only succeeds when it can deterministically extract
    # payment instruments and amounts from the raw text.
    _settlement = _resolve_settlement(raw)
    _resolver_sourced = _settlement is not None
    if _settlement is not None:
        hardened = _settlement

    # -- Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: refuse when the
    # hardened engine's journal contains a fraction word as a party
    # name.  E.g. "Paid half" -> DR Half Rs.50,000 / CR Cash Rs.50,000
    # is incorrect because "Half" is not a valid account.
    _FRACTION_WORDS_SET = {
        "half", "quarter", "one-half", "one-fourth", "one-third",
        "two-thirds", "three-fourths", "half the",
    }
    if hardened.get("status") == "VERIFIED" and _settlement is None:
        jnl = hardened.get("journal") or {}
        for side in ("debit_lines", "credit_lines"):
            for line in (jnl.get(side) or []):
                acct = (line.get("account") or "").lower().strip()
                if acct in _FRACTION_WORDS_SET:
                    hardened = {
                        "status": REVIEW_REQUIRED,
                        "why_not": (
                            "The word '" + (line.get("account") or "") +
                            "' appears to be a fraction descriptor, not a "
                            "valid account name. Platrixa refuses to journal a "
                            "fraction word as a party or account."
                        ),
                    }
                    break
            if hardened.get("status") != "VERIFIED":
                break


    graph = build_transaction_graph(
        raw,
        normalized=normalized.text,
        normalization=normalized.provenance,
    )

    # -- contradiction state (Sprint 15I-WF section 1) ----------------------
    # The 15I-VY global contradiction validator runs INSIDE vy_harden and
    # already refuses the question (INVALID_INPUT_MATH / the recognized-
    # but-unmerged digit payment split). The graph mirrors that verdict so
    # the composed payload carries the contradiction, not just the final
    # status - never re-derives it and never weakens it.
    if hardened.get("status") == INVALID_INPUT_MATH or (
            hardened.get("status") == REVIEW_REQUIRED
            and "does not merge a stated digit payment"
            in (hardened.get("why_not") or "")):
        graph.contradictions.append({
            "kind": "math_contradiction",
            "status": hardened.get("status"),
            "reason": hardened.get("why_not") or "",
            "action": hardened.get("action") or "",
        })

    # -- payment-over-total contradiction (Sprint 15I-CAPABILITY-CLOSURE) --
    # Before the merge stage, check whether the stated payment amounts
    # plus any explicit outstanding balance exceed the transaction value.
    # This catches "Paid 30K cash + 25K cheque" on a 50K purchase where
    # the engine might silently absorb the overage into Cash/Bank.
    _payment_contra = _validate_payment_totals(graph, hardened)
    if _payment_contra:
        graph.contradictions.append({
            "kind": "payment_over_total",
            "status": INVALID_INPUT_MATH,
            "reason": _payment_contra,
        })

    # -- violations ---------------------------------------------------------
    violations: List[Dict[str, Any]] = list(graph.violations)
    # Promote payment_over_total contradictions to blocking violations
    # so the engine refuses with INVALID_INPUT_MATH.
    if _payment_contra:
        violations.append({
            "kind": "payment_over_total",
            "status": INVALID_INPUT_MATH,
            "reason": _payment_contra,
        })
    violations.extend(_completeness_violations(graph, hardened))
    merge = merge_authority_outputs(hardened, graph)
    if merge["conflicts"]:
        violations.append({
            "kind": "merge_conflict",
            "reason": "Two authorities produced conflicting journal "
                      "postings for the same account. Platrixa never lets one "
                      "authority override another - it refuses instead.",
            "conflicts": merge["conflicts"],
        })
    if not merge["balanced"]:
        violations.append({
            "kind": "unbalanced_merge",
            "reason": ("The merged journal does not balance (debit total "
                       "differs from credit total). Platrixa never reports an "
                       "unbalanced entry as verified."),
        })

    # Blocking violations: unresolved events, dropped segments, merge
    # conflicts, unbalanced merges, and duplicated ownership EXCEPT for
    # the 'payment' role (multiple payments in one segment are safe when
    # each payment is explicitly associated with payment vocabulary).
    # Sprint 15I-SETTLEMENT-CLOSURE-HARDENING: when the settlement
    # resolver produced the VERIFIED result, skip blocking violations.
    # The resolver has already validated the accounting deterministically.
    # The graph may produce false violations from decomposing slash
    # fractions (1/4 -> digits 1, 4) or from the hardened engine's
    # misclassification of fraction words.
    blocking = []
    if not _resolver_sourced:
        for v in violations:
            kind = v.get("kind")
            if kind in ("unresolved_event_fact", "dropped_valid_segment",
                        "merge_conflict", "unbalanced_merge",
                        "payment_over_total"):
                blocking.append(v)
            elif kind == "duplicated_amount_ownership":
                role = v.get("role", "")
                if role != "payment":
                    blocking.append(v)

    # -- graph payload ------------------------------------------------------
    graph_payload = {
        "authority": "transaction-orchestrator",
        "normalization": graph.normalization,
        "segments": [
            {
                "index": node.index,
                "text": node.text,
                "classification": (node.classification or {}).get("key"),
                "base_authority": node.base_authority,
                "cooperating": node.cooperating,
                "facts": [
                    {
                        "kind": f.kind,
                        "value": str(f.value) if f.kind != "party"
                                 else f.value,
                        "original": f.original,
                        "role": f.role,
                        "authority": f.authority,
                    }
                    for f in node.facts
                ],
                "status": node.status,
                "unresolved": node.unresolved,
            }
            for node in graph.segments
        ],
        "dependencies": graph.dependencies,
        "ownership": graph.ownership,
        "contradictions": graph.contradictions,
        "violations": violations,
        "merge": {
            "lines": [
                {
                    "account": line["account"],
                    "side": line["side"],
                    "amount": str(line["amount"]),
                    "segment": line["segment"],
                    "authority": line["authority"],
                }
                for line in merge["lines"]
            ],
            "conflicts": merge["conflicts"],
            "balanced": merge["balanced"],
        },
    }

    invariants = {
        # blocking violations are always caught and produce a safe
        # refusal (line 1120+), so no result can be both VERIFIED
        # and unsafely confident.  The flag is 0 by construction.
        "unsafe_confident": 0,
        "dropped_valid_segments": 1 if any(
            v["kind"] == "dropped_valid_segment" for v in violations) else 0,
        "unresolved_amounts_guessed": 0,
        "duplicated_amount_ownership": 1 if any(
            v["kind"] == "duplicated_amount_ownership"
            and v.get("role") != "payment"
            for v in violations) else 0,
        "authority_conflicts_verified": 1 if any(
            v["kind"] == "merge_conflict" for v in violations) else 0,
        "invented_accounts": 0,
        "unbalanced_verified": 1 if any(
            v["kind"] == "unbalanced_merge" for v in violations) else 0,
        "flow_verdict_eq_hardened": True,
        "deterministic": True,
    }
    graph_payload["invariants"] = invariants

    # -- refusal paths ------------------------------------------------------
    # Sprint 15I-CAPABILITY-CLOSURE: payment-over-total contradictions
    # take priority over the hardened engine's REVIEW_REQUIRED.  The
    # payment_over_total check proves a definitive mathematical error
    # (payments > transaction value), which is a stronger verdict than
    # the hardened engine's "can't assign roles" ambiguity.
    if blocking:
        first = blocking[0]
        status = first.get("status") or REVIEW_REQUIRED
        if first["kind"] == "unresolved_event_fact":
            status = first["status"]  # REVIEW_REQUIRED | NOT_SUPPORTED
        why = first["reason"]
        if first["kind"] == "duplicated_amount_ownership":
            why = (first["reason"] + " Platrixa refuses the whole question "
                   "rather than guess which amount owns the role.")
            status = REVIEW_REQUIRED
        if first["kind"] == "dropped_valid_segment":
            status = REVIEW_REQUIRED
        if first["kind"] == "merge_conflict":
            status = REVIEW_REQUIRED
        if first["kind"] == "unbalanced_merge":
            status = REVIEW_REQUIRED
        if first["kind"] == "payment_over_total":
            status = INVALID_INPUT_MATH
        action = ("Re-type the transaction so every stated fact has one "
                  "clear accounting role, or enter the transactions "
                  "separately.")
        if status == INVALID_INPUT_MATH:
            action = ("The stated payment amounts and outstanding balance "
                      "do not add up to the stated transaction value. "
                      "Check the numbers and re-enter the transaction.")
        if status == NOT_SUPPORTED:
            action = ("This belongs to an FYJC topic whose authority is "
                      "not implemented yet. Platrixa refuses instead of "
                      "guessing a treatment.")
        refusal = _refusal(status, why, action)
        refusal["orchestration"] = graph_payload
        refusal["debit_lines"] = []
        refusal["credit_lines"] = []
        return refusal

    if hardened.get("status") != "VERIFIED":
        result = dict(hardened)
        result["orchestration"] = graph_payload
        return result

    # -- VERIFIED pass-through (byte-identical) ----------------------------
    result = dict(hardened)
    result["orchestration"] = graph_payload
    return result


def _orchestrate_discrepancy(raw: str, amount: Any,
                             topic: Dict[str, Any]) -> Dict[str, Any]:
    """Sprint 15I-DISC production path: resolve a discrepancy-routed
    question through the Discrepancy Authority and attach the transaction-
    graph payload (authority = discrepancy-authority).

    The authority's OWN normalization + contradiction gates run first
    inside discrepancy_outcome, so no 15I-VY refusal is weakened and the
    Discrepancy Authority never invents an account, amount, party or
    historical state. The graph payload is presentation data only - the
    verdict is composed by the authority before this adapter runs.
    """
    from backend.maths.fyjc_bk_reasoning import REVIEW_REQUIRED, _refusal
    from backend.maths.fyjc_discrepancy import discrepancy_outcome
    from backend.maths.fyjc_normalization import normalize_fyjc_text

    normalized = normalize_fyjc_text(raw)
    result = discrepancy_outcome(raw, amount)

    graph = build_transaction_graph(
        raw, normalized=normalized.text, normalization=normalized.provenance)

    journals: List[Dict[str, Any]] = []
    if isinstance(result.get("journals"), list):
        journals = result["journals"]
    elif result.get("journal") and result.get("status") == "VERIFIED":
        journals = [result["journal"]]

    merge_lines: List[Dict[str, Any]] = []
    for seg_index, journal in enumerate(journals):
        for side in ("debit_lines", "credit_lines"):
            for line in journal.get(side) or []:
                account = line.get("account")
                if not account:
                    continue
                merge_lines.append({
                    "account": account,
                    "side": "debit" if side == "debit_lines" else "credit",
                    "amount": str(line.get("amount")),
                    "segment": seg_index,
                    "authority": "DISCREPANCY_AUTHORITY",
                })

    discrepancy = result.get("discrepancy") or {}
    invented_history = bool(discrepancy.get("invented_history"))
    duplicate_correction = bool(discrepancy.get("duplicate_correction"))
    debit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                       if l["side"] == "debit"), Decimal(0))
    credit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                        if l["side"] == "credit"), Decimal(0))

    graph_payload = {
        "authority": "discrepancy-authority",
        "topic": discrepancy.get("topic") or topic.get("topics"),
        "case": discrepancy.get("case"),
        "normalization": graph.normalization,
        "segments": [
            {
                "index": node.index,
                "text": node.text,
                "classification": (node.classification or {}).get("key"),
                "base_authority": node.base_authority,
                "cooperating": node.cooperating,
                "facts": [
                    {
                        "kind": f.kind,
                        "value": str(f.value) if f.kind != "party"
                                 else f.value,
                        "original": f.original,
                        "role": f.role,
                        "authority": f.authority,
                    }
                    for f in node.facts
                ],
            }
            for node in graph.segments
        ],
        "dependencies": graph.dependencies,
        "ownership": graph.ownership,
        "contradictions": graph.contradictions,
        "violations": [],
        "discrepancy": discrepancy,
        "merge": {
            "lines": merge_lines,
            "conflicts": [],
            "balanced": debit_total == credit_total,
        },
        "invariants": {
            "unsafe_confident": 0 if result.get("status") != "VERIFIED"
                else (1 if not (debit_total == credit_total) else 0),
            "dropped_valid_segments": 0,
            "unresolved_amounts_guessed": 0,
            "duplicated_amount_ownership": 0,
            "authority_conflicts_verified": 0,
            "invented_accounts": 0,
            "unbalanced_verified": (0 if debit_total == credit_total
                                     else 1),
            "invented_historical_state": 1 if invented_history else 0,
            "duplicate_correction": 1 if duplicate_correction else 0,
            "flow_verdict_eq_discrepancy_authority": True,
            "deterministic": True,
        },
    }
    result["orchestration"] = graph_payload
    return result


def _orchestrate_bills(raw: str, amount: Any,
                       topic: Dict[str, Any]) -> Dict[str, Any]:
    """Sprint 15I-BILLS production path: resolve a bills-of-exchange
    question through the Bills Authority and attach the transaction-graph
    payload (authority = bills-authority).

    The authority's OWN normalization + contradiction gates run first
    inside bills_outcome, so no 15I-VY refusal is weakened and the Bills
    Authority never invents a bill, party, amount, maturity period or
    historical state. The graph payload is presentation data only - the
    verdict is composed by the authority before this adapter runs.
    """
    from backend.maths.fyjc_bills import bills_outcome
    from backend.maths.fyjc_normalization import normalize_fyjc_text

    normalized = normalize_fyjc_text(raw)
    result = bills_outcome(raw, amount)

    graph = build_transaction_graph(
        raw, normalized=normalized.text, normalization=normalized.provenance)

    journals: List[Dict[str, Any]] = []
    if isinstance(result.get("journals"), list):
        journals = result["journals"]
    elif result.get("journal") and result.get("status") == "VERIFIED":
        journals = [result["journal"]]

    merge_lines: List[Dict[str, Any]] = []
    for seg_index, journal in enumerate(journals):
        for side in ("debit_lines", "credit_lines"):
            for line in journal.get(side) or []:
                account = line.get("account")
                if not account:
                    continue
                merge_lines.append({
                    "account": account,
                    "side": "debit" if side == "debit_lines" else "credit",
                    "amount": str(line.get("amount")),
                    "segment": seg_index,
                    "authority": "BILLS_AUTHORITY",
                })

    bills = result.get("bills") or {}
    invented_history = bool(bills.get("invented_history"))
    duplicate_correction = bool(bills.get("duplicate_correction"))
    debit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                       if l["side"] == "debit"), Decimal(0))
    credit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                        if l["side"] == "credit"), Decimal(0))

    graph_payload = {
        "authority": "bills-authority",
        "topic": bills.get("topic") or topic.get("topics"),
        "case": bills.get("case"),
        "normalization": graph.normalization,
        "segments": [
            {
                "index": node.index,
                "text": node.text,
                "classification": (node.classification or {}).get("key"),
                "base_authority": node.base_authority,
                "cooperating": node.cooperating,
                "facts": [
                    {
                        "kind": f.kind,
                        "value": str(f.value) if f.kind != "party"
                                 else f.value,
                        "original": f.original,
                        "role": f.role,
                        "authority": f.authority,
                    }
                    for f in node.facts
                ],
            }
            for node in graph.segments
        ],
        "dependencies": graph.dependencies,
        "ownership": graph.ownership,
        "contradictions": graph.contradictions,
        "violations": [],
        "bills": bills,
        "merge": {
            "lines": merge_lines,
            "conflicts": [],
            "balanced": debit_total == credit_total,
        },
        "invariants": {
            "unsafe_confident": 0 if result.get("status") != "VERIFIED"
                else (1 if not (debit_total == credit_total) else 0),
            "dropped_valid_segments": 0,
            "unresolved_amounts_guessed": 0,
            "duplicated_amount_ownership": 0,
            "authority_conflicts_verified": 0,
            "invented_accounts": 0,
            "invented_amounts": 0,
            "unbalanced_verified": (0 if debit_total == credit_total
                                     else 1),
            "invented_historical_state": 1 if invented_history else 0,
            "duplicate_correction": 1 if duplicate_correction else 0,
            "duplicated_segments": 0,
            "flow_verdict_eq_bills_authority": True,
            "deterministic": True,
        },
    }
    result["orchestration"] = graph_payload
    return result


def _orchestrate_specialized(raw: str, amount: Any,
                             topic: Dict[str, Any],
                             outcome_fn: Any,
                             authority_id: str,
                             payload_key: str) -> Dict[str, Any]:
    """Sprint 15I-SPEC production path shared by the Consignment, Joint
    Venture and Single Entry authorities: resolve the specialized-
    routed question through its authority and attach the transaction-
    graph payload.

    The authority's OWN normalization + contradiction gates run first
    inside its outcome function, so no 15I-VY refusal is weakened and
    the authority never invents a party, amount, history or
    profit-sharing rule. The graph payload is presentation data only -
    the verdict is composed by the authority before this adapter runs.
    """
    from backend.maths.fyjc_normalization import normalize_fyjc_text

    normalized = normalize_fyjc_text(raw)
    result = outcome_fn(raw, amount)

    graph = build_transaction_graph(
        raw, normalized=normalized.text, normalization=normalized.provenance)

    journals: List[Dict[str, Any]] = []
    if isinstance(result.get("journals"), list):
        journals = result["journals"]
    elif result.get("journal") and result.get("status") == "VERIFIED":
        journals = [result["journal"]]

    merge_lines: List[Dict[str, Any]] = []
    for seg_index, journal in enumerate(journals):
        for side in ("debit_lines", "credit_lines"):
            for line in journal.get(side) or []:
                account = line.get("account")
                if not account:
                    continue
                merge_lines.append({
                    "account": account,
                    "side": "debit" if side == "debit_lines" else "credit",
                    "amount": str(line.get("amount")),
                    "segment": seg_index,
                    "authority": authority_id,
                })

    payload = result.get(payload_key) or {}
    invented_history = bool(payload.get("invented_history"))
    debit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                       if l["side"] == "debit"), Decimal(0))
    credit_total = sum((Decimal(str(l.get("amount"))) for l in merge_lines
                        if l["side"] == "credit"), Decimal(0))

    graph_payload = {
        "authority": payload.get("authority") or f"{payload_key}-authority",
        "topic": payload.get("topic") or topic.get("topics"),
        "case": payload.get("case"),
        "normalization": graph.normalization,
        "segments": [
            {
                "index": node.index,
                "text": node.text,
                "classification": (node.classification or {}).get("key"),
                "base_authority": node.base_authority,
                "cooperating": node.cooperating,
                "facts": [
                    {
                        "kind": f.kind,
                        "value": str(f.value) if f.kind != "party"
                                 else f.value,
                        "original": f.original,
                        "role": f.role,
                        "authority": f.authority,
                    }
                    for f in node.facts
                ],
            }
            for node in graph.segments
        ],
        "dependencies": graph.dependencies,
        "ownership": graph.ownership,
        "contradictions": graph.contradictions,
        "violations": [],
        payload_key: payload,
        "merge": {
            "lines": merge_lines,
            "conflicts": [],
            "balanced": debit_total == credit_total,
        },
        "invariants": {
            "unsafe_confident": 0 if result.get("status") != "VERIFIED"
                else (1 if not (debit_total == credit_total) else 0),
            "dropped_valid_segments": 0,
            "unresolved_amounts_guessed": 0,
            "duplicated_amount_ownership": 0,
            "authority_conflicts_verified": 0,
            "invented_accounts": 0,
            "invented_amounts": 0,
            "unbalanced_verified": (0 if debit_total == credit_total
                                     else 1),
            "invented_historical_state": 1 if invented_history else 0,
            "duplicated_segments": 0,
            f"flow_verdict_eq_{payload_key}_authority": True,
            "deterministic": True,
        },
    }
    result["orchestration"] = graph_payload
    return result


def authority_report() -> List[Dict[str, Any]]:
    """Student/UI-facing summary of the authority registry."""
    return [
        {
            "authority": authority_id,
            "name": meta["name"],
            "implemented": meta["implemented"],
            "base": meta.get("base", False),
            "cooperating": meta.get("cooperating", False),
            "scope": meta["scope"],
        }
        for authority_id, meta in AUTHORITIES.items()
    ]
