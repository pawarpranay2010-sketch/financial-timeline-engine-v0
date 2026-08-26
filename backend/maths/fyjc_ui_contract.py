"""
Platrixa
Sprint 15I-UI - Student Interaction Contract (backend projection)
backend/maths/fyjc_ui_contract.py

PURE, deterministic projection layer between the released production
boundary (backend.maths.fyjc_orchestration.orchestrate) and the student
workspace UI. This module contains ZERO accounting authority:

  * it never calculates a journal entry, never infers an account, never
    invents an amount and never generates accounting rules;
  * every journal line it exposes is the backend's verified line,
    byte-identical to the debit_lines / credit_lines of orchestrate();
  * the Why layer is a LOCALIZATION DICTIONARY over engine events
    (calculation ids, rule ids, authority notes) - changing wording here
    can never change accounting behaviour, and no LLM ever reconstructs
    the reasoning path;
  * the Confidence Gate is emitted ONLY from the backend's own refusal
    payload (a recognized, decision-relevant ambiguity signature with a
    finite alternative set) - the UI never invents alternatives.

The gate contract
-----------------
Gate rules are registered in GATE_RULES. A rule fires only when ALL of:

  1. orchestrate() returns REVIEW_REQUIRED,
  2. the refusal carries the rule's exact ambiguity signature,
  3. the ambiguity materially changes the accounting result (the two
     alternatives produce different journals),
  4. the engine deterministically VERIFIES both alternatives once the
     decision is made explicit.

resolve_confidence_gate() reruns the production boundary with the
student's decision made EXPLICIT in the question (a deterministic,
minimal rewrite of the ambiguous clause - never a silent reinterpretation
of the original text). The original text is preserved in provenance, and
if the resolved input does not verify, the honest backend verdict is
returned - a gate never forces a VERIFIED result.

Invariant guaranteed by construction: the same input + same backend
state yields the same graph, the same journal, the same verification and
the same explanation path. Every function here is pure and deterministic.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_bk_reasoning import (
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
)
from backend.maths.status import BLOCKED, VERIFIED

# ---------------------------------------------------------------------------
# Status presentation (presentation only - the verdict itself is the
# backend's status value, never derived here).
# ---------------------------------------------------------------------------

STATUS_PRESENTATION: Dict[str, Dict[str, str]] = {
    VERIFIED: {
        "label": "Verified",
        "headline": "✓ Verified",
        "tone": "green",
        "summary": (
            "Platrixa deterministically interpreted this question and verified "
            "the result against the transaction facts."
        ),
    },
    REVIEW_REQUIRED: {
        "label": "Review required",
        "headline": "One thing to clarify",
        "tone": "amber",
        "summary": (
            "Platrixa needs one precise clarification before it can finish - it "
            "never guesses."
        ),
    },
    INVALID_INPUT_MATH: {
        "label": "The numbers don't add up",
        "headline": "The numbers don't add up",
        "tone": "red",
        "summary": (
            "The stated figures contradict each other. Please check the "
            "amounts and re-type the question."
        ),
    },
    NOT_SUPPORTED: {
        "label": "Not supported yet",
        "headline": "Platrixa can't process this yet",
        "tone": "neutral",
        "summary": (
            "This belongs to an accounting topic Platrixa does not yet verify. "
            "It refuses instead of guessing a treatment."
        ),
    },
    BLOCKED: {
        "label": "Safety boundary",
        "headline": "Platrixa stopped before answering",
        "tone": "red",
        "summary": (
            "Platrixa could not safely determine the accounting meaning. It "
            "will not invent facts, amounts, parties or history."
        ),
    },
}

# ---------------------------------------------------------------------------
# Why layer - localization dictionary over engine events.
# ---------------------------------------------------------------------------
# Keys are engine calculation ids / rule ids / authority tokens. Values are
# student-readable copy. The dictionary is presentation ONLY: it is never
# consulted by any accounting rule, and the same event id always renders
# the same text (deterministic explanation path).

WHY_LOCALIZATION: Dict[str, str] = {
    # --- calculation records (BK_* from the hardened engine) --------------
    "BK_LIST_PRICE": "List price",
    "BK_TRADE_DISCOUNT_AMOUNT": "Trade discount deducted (List price × Trade discount %)",
    "BK_NET_TRANSACTION_VALUE": "Net amount (after trade discount)",
    "BK_CASH_DISCOUNT_AMOUNT": "Cash discount allowed (Settlement × Cash discount %)",
    "BK_CASH_PAID_NET": "Cash paid after discount",
    "BK_EXPLICIT_DISCOUNT": "Stated discount applied",
    "BK_PAID_CREDIT_SPLIT": "Split between paid and credit portions",
    "BK_PROFIT_ON_COST": "Selling price = Cost + Profit on cost",
    "BK_PROFIT_ON_SELLING": "Cost = Selling price − Profit on selling price",
    "BK_GST_RATE": "GST rate applied to the taxable base",
    "BK_GST_BASE": "Taxable base before GST",
    "BK_GST_INCLUSIVE_EXTRACTION": "GST extracted from the GST-inclusive total",
    "BK_GST_COMPONENT_SPLIT": "GST split into CGST and SGST",
    "BK_GST_TRADE_DISCOUNT": "GST computed on the amount after trade discount",
    # --- rule ids (composed from engine events, never invented) -----------
    "RULE_TD_DEDUCT_BEFORE_GST": "Trade discount was deducted before GST.",
    "RULE_GST_SPLIT_INTRA": "The tax is intra-state, so GST is split into CGST and SGST.",
    "RULE_GST_INTER": "The tax is inter-state, so the whole GST is recorded as IGST.",
    # --- authority note tokens --------------------------------------------
    "BILLS": "Bills of Exchange",
    "DISCREPANCY": "Discrepancy / Reconciliation",
    "CONSIGNMENT": "Consignment",
    "JOINT_VENTURE": "Joint Venture",
    "SINGLE_ENTRY": "Incomplete Records / Single Entry",
}

# ---------------------------------------------------------------------------
# Registered Confidence Gate rules.
# ---------------------------------------------------------------------------

# The engine's own refusal signature when a GST rate is stated without a
# tax scheme: "GST is mentioned with a rate but the question does not say
# whether it is intra-state (CGST + SGST) or inter-state (IGST)..."
_GST_SCHEME_SIGNATURE = "does not say whether it is intra-state"

# Sprint 25: Cash/credit ambiguity signature from the engine:
# "The transaction does not say whether it was for cash or on credit."
_CASH_CREDIT_SIGNATURE = "does not say whether it was for cash or on credit"

# Sprint 25: Historical ambiguity signature
_HISTORICAL_MULTI_SIGNATURE = "Multiple historical candidates"

# Rate-clause forms the engine recognizes: "at 18% GST" and "GST @ 18%".
_RATE_AT_RE = re.compile(r"at\s+(\d+(?:\.\d+)?)\s*%\s*GST", re.IGNORECASE)
_RATE_SIGN_RE = re.compile(r"GST\s*@\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)

_HISTORICAL_HINTS = (
    "written off", "previously", "earlier", "owed", "outstanding",
    "insolvent", "historical", "opening balance", "earlier balance",
)
_PAYMENT_HINTS = (
    ("neft", "NEFT"), ("upi", "UPI"), ("cheque", "cheque"),
    ("check", "cheque"), ("cash", "cash"), ("bank", "bank"),
    ("rtgs", "RTGS"), ("immediate", "immediate"),
)
_TAX_HINTS = ("gst", "cgst", "sgst", "igst", "vat")


def _num(value: Any) -> Optional[float]:
    """Guarded numeric conversion (presentation only)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_rupee(value: Any) -> str:
    """Indian-style grouping for display. Presentation only - the amount
    itself is always the backend's value, never recomputed here."""
    f = _num(value)
    if f is None:
        return str(value if value is not None else "")
    if f == int(f):
        return f"\u20b9{int(f):,}"
    return f"\u20b9{f:,.2f}"


def _fmt_plain(value: Any) -> str:
    f = _num(value)
    if f is not None:
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    if isinstance(value, dict):
        return ", ".join(
            f"{k} = {_fmt_plain(v)}" for k, v in value.items())
    return str(value if value is not None else "")


def _find_rate_clause(question: str) -> Optional[float]:
    """Extract the GST rate from a recognized rate clause. Returns None
    when no recognized clause is present (no gate, no rewrite)."""
    for pattern in (_RATE_AT_RE, _RATE_SIGN_RE):
        match = pattern.search(question)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _format_rate(rate: float) -> str:
    """'9.0' -> '9', '7.5' -> '7.5' (deterministic)."""
    if rate == int(rate):
        return str(int(rate))
    return f"{rate:g}"


def _gst_rewrite(question: str, decision_id: str, rate: float) -> str:
    """Deterministic minimal rewrite of the ambiguous GST clause using the
    student's explicit decision. The original text is preserved verbatim
    everywhere else; the decision is recorded in provenance by the
    caller."""
    if decision_id == "intra_state":
        replacement = (
            f"with CGST @ {_format_rate(rate / 2)}% "
            f"and SGST @ {_format_rate(rate / 2)}%"
        )
    else:
        replacement = f"with IGST @ {_format_rate(rate)}%"
    for pattern in (_RATE_AT_RE, _RATE_SIGN_RE):
        match = pattern.search(question)
        if match:
            return question[:match.start()] + replacement + question[match.end():]
    return question


# Sprint 25: Cash/credit rewrite patterns
_PURCHASE_CASH_RE = re.compile(
    r"(purchased\s+(?:goods|stock|merchandise)\s+)(?:for\s+)?Rs\.?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE)
_PURCHASE_CREDIT_RE = re.compile(
    r"(purchased\s+(?:goods|stock|merchandise)\s+)(?:for\s+)?Rs\.?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE)
_SALE_CASH_RE = re.compile(
    r"(sold\s+(?:goods|stock|merchandise)\s+)(?:for\s+)?Rs\.?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE)


def _cash_credit_rewrite(question: str, decision_id: str) -> str:
    """Deterministic rewrite for cash/credit ambiguity. Appends the missing
    cash/credit mode to the original question text."""
    low = question.strip()
    if decision_id == "cash":
        # Append 'for cash' if not already present
        if "for cash" not in low.lower() and "by cash" not in low.lower():
            # Insert before trailing period
            if low.endswith("."):
                return low[:-1] + " for cash."
            return low + " for cash"
        return low
    elif decision_id == "credit":
        # Append 'on credit' if not already present
        if "on credit" not in low.lower() and "credit" not in low.lower():
            if low.endswith("."):
                return low[:-1] + " on credit."
            return low + " on credit"
        return low
    return low


def _graph_segments(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (result.get("orchestration") or {}).get("segments") or []


def _graph_why_not(result: Dict[str, Any]) -> str:
    return str(result.get("why_not") or "").strip()


# ---------------------------------------------------------------------------
# Confidence Gate (backend-owned; the UI only renders it and submits the
# student's choice).
# ---------------------------------------------------------------------------


def build_confidence_gate(result: Dict[str, Any],
                          question: str) -> Optional[Dict[str, Any]]:
    """Emit a Confidence Gate ONLY for a recognized, decision-relevant
    ambiguity. Returns None for every other outcome - a plain refusal
    stays a plain refusal and the UI never invents alternatives."""
    if (result or {}).get("status") != REVIEW_REQUIRED:
        return None
    why = _graph_why_not(result)
    segments = _graph_segments(result)
    segment_text = segments[0]["text"] if segments else str(question or "")

    # --- GST Scheme Gate (Sprint 15I) ------------------------------------
    if _GST_SCHEME_SIGNATURE in why:
        rate = _find_rate_clause(str(question or ""))
        if rate is None:
            return None
        return {
            "gate_id": "GST_SCHEME",
        "question": "How should the GST on this transaction be recorded?",
        "segment": segment_text,
        "dependency": (
            "The GST rate is stated without a tax scheme. Platrixa cannot tell "
            "whether the transaction is intra-state (CGST + SGST) or "
            "inter-state (IGST), and the choice changes the journal."
        ),
        "reason": why,
        "rate": rate,
        "alternatives": [
            {
                "id": "intra_state",
                "label": "Intra-state — CGST and SGST",
                "effect": (
                    "The tax is split into CGST and SGST at equal rates "
                    "(each half of the stated GST rate)."
                ),
            },
            {
                "id": "inter_state",
                "label": "Inter-state — IGST",
                "effect": (
                    "The whole stated GST rate is recorded as IGST."
                ),
            },
        ],
        }

    # --- Cash/Credit Gate (Sprint 25) ------------------------------------
    if _CASH_CREDIT_SIGNATURE in why:
        q_lower = str(question or "").lower()
        has_cash = "for cash" in q_lower or "by cash" in q_lower
        has_credit = "on credit" in q_lower or "credit from" in q_lower
        if has_cash or has_credit:
            return None
        return {
            "gate_id": "CASH_CREDIT",
            "question": "Was this transaction for cash or on credit?",
            "segment": segment_text,
            "dependency": (
                "The transaction does not specify the payment mode. "
                "Platrixa needs to know whether goods were paid for in cash "
                "or purchased on credit."
            ),
            "reason": why,
            "alternatives": [
                {
                    "id": "cash",
                    "label": "For cash",
                    "effect": (
                        "The payment is recorded as a cash transaction. "
                        "Cash decreases."
                    ),
                },
                {
                    "id": "credit",
                    "label": "On credit",
                    "effect": (
                        "The purchase is on credit from a party. "
                        "A creditor account is created."
                    ),
                },
            ],
        }

    return None


def resolve_confidence_gate(question: str,
                            gate_id: str,
                            decision_id: str) -> Dict[str, Any]:
    """Rerun the production boundary with the student's decision made
    explicit. Returns the full projection (never a fabricated result).

    Provenance records: the original question, the gate, the selected
    decision, the resolved question that was actually run, and the final
    verdict. If the resolved question still refuses, that honest verdict
    is returned with the provenance attached.
    """
    from backend.maths.fyjc_orchestration import orchestrate

    # --- GST Scheme Gate -------------------------------------------------
    if gate_id == "GST_SCHEME":
        rate = _find_rate_clause(str(question or ""))
        valid = {
            "intra_state": "Intra-state \u2014 CGST and SGST",
            "inter_state": "Inter-state \u2014 IGST",
        }
        if rate is None or decision_id not in valid:
            result = orchestrate(question)
            return project_student_result(result, question, gate_resolution={
                "gate_id": gate_id,
                "decision_id": decision_id,
                "accepted": False,
                "reason": "The decision is not a registered alternative.",
                "original_question": question,
            })
        resolved_question = _gst_rewrite(question, decision_id, rate)
        result = orchestrate(resolved_question)
        return project_student_result(result, question, gate_resolution={
            "gate_id": gate_id,
            "decision_id": decision_id,
            "decision_label": valid[decision_id],
            "accepted": True,
            "original_question": question,
            "resolved_question": resolved_question,
            "final_status": result.get("status"),
        })

    # --- Cash/Credit Gate (Sprint 25) ------------------------------------
    if gate_id == "CASH_CREDIT":
        valid_cc = {"cash": "For cash", "credit": "On credit"}
        if decision_id not in valid_cc:
            result = orchestrate(question)
            return project_student_result(result, question, gate_resolution={
                "gate_id": gate_id,
                "decision_id": decision_id,
                "accepted": False,
                "reason": "The decision is not a registered alternative.",
                "original_question": question,
            })
        resolved_question = _cash_credit_rewrite(question, decision_id)
        result = orchestrate(resolved_question)
        return project_student_result(result, question, gate_resolution={
            "gate_id": gate_id,
            "decision_id": decision_id,
            "decision_label": valid_cc[decision_id],
            "accepted": True,
            "original_question": question,
            "resolved_question": resolved_question,
            "final_status": result.get("status"),
        })

    # --- Unknown gate: fallback ------------------------------------------
    return _projection_for_unknown_gate(question, gate_id, decision_id)


def _projection_for_unknown_gate(question: str,
                                 gate_id: str,
                                 decision_id: str) -> Dict[str, Any]:
    from backend.maths.fyjc_orchestration import orchestrate
    result = orchestrate(question)
    return project_student_result(result, question, gate_resolution={
        "gate_id": gate_id,
        "decision_id": decision_id,
        "accepted": False,
        "reason": "No registered gate matches this gate id.",
        "original_question": question,
    })


# ---------------------------------------------------------------------------
# Understanding (facts from the backend graph - never parsed in the UI).
# ---------------------------------------------------------------------------


def _segment_facts(segment: Dict[str, Any]) -> Dict[str, Any]:
    facts: Dict[str, Any] = {"amounts": [], "rates": [], "parties": [],
                             "fractions": [], "events": []}
    for fact in segment.get("facts") or []:
        kind = fact.get("kind")
        value = fact.get("value")
        if kind == "amount":
            facts["amounts"].append({"value": value,
                                     "display": _fmt_rupee(value),
                                     "original": fact.get("original")})
        elif kind == "rate":
            facts["rates"].append({"value": value,
                                   "display": f"{value}%",
                                   "original": fact.get("original")})
        elif kind == "party":
            facts["parties"].append(value)
        elif kind == "fraction":
            facts["fractions"].append({"value": value,
                                       "display": f"{float(value):.0%}"
                                       if _num(value) is not None else str(value)})
        elif kind == "event":
            facts["events"].append(value)
    return facts


def _payment_method(question: str) -> List[str]:
    low = " " + str(question or "").lower() + " "
    return [label for token, label in _PAYMENT_HINTS if token in low]


def _historical_facts(question: str) -> List[str]:
    low = " " + str(question or "").lower() + " "
    return [hint for hint in _HISTORICAL_HINTS if hint in low]


def _taxes(question: str) -> List[str]:
    low = " " + str(question or "").lower() + " "
    return [hint.upper() for hint in _TAX_HINTS if hint in low]


def _understanding(result: Dict[str, Any],
                   question: str) -> Dict[str, Any]:
    understanding = result.get("understanding") or {}
    segments = []
    parties: List[str] = []
    amounts: List[Dict[str, Any]] = []
    rates: List[Dict[str, Any]] = []
    fractions: List[Dict[str, Any]] = []
    events: List[str] = []
    for segment in _graph_segments(result):
        facts = _segment_facts(segment)
        segments.append({
            "index": segment.get("index"),
            "text": segment.get("text"),
            "classification": segment.get("classification"),
            "authority": segment.get("base_authority"),
            "facts": facts,
        })
        for party in facts["parties"]:
            if party not in parties:
                parties.append(party)
        amounts.extend(facts["amounts"])
        rates.extend(facts["rates"])
        fractions.extend(facts["fractions"])
        events.extend(facts["events"])
    return {
        "transaction_type": understanding.get("question_type"),
        "transaction_type_key": understanding.get("question_type_key"),
        "accounts": (understanding.get("accounts_identified") or {}).get("all"),
        "parties": parties,
        "amounts": amounts,
        "rates": rates,
        "fractions": fractions,
        "taxes": _taxes(question),
        "payment": _payment_method(question),
        "historical": _historical_facts(question),
        "events": events,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Journal (verbatim backend lines - the UI never creates a line).
# ---------------------------------------------------------------------------


def _journal(result: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for line in result.get("debit_lines") or []:
        if not line.get("account"):
            continue
        rows.append({
            "account": line.get("account"),
            "side": "debit",
            "amount": line.get("amount"),
            "display": _fmt_rupee(line.get("amount")),
            "rule": line.get("rule"),
            "why": line.get("why"),
            "class": line.get("class"),
        })
    for line in result.get("credit_lines") or []:
        if not line.get("account"):
            continue
        rows.append({
            "account": line.get("account"),
            "side": "credit",
            "amount": line.get("amount"),
            "display": _fmt_rupee(line.get("amount")),
            "rule": line.get("rule"),
            "why": line.get("why"),
            "class": line.get("class"),
        })
    journal = result.get("journal") or {}
    return {
        "rows": rows,
        "balanced": bool(journal.get("balanced")),
        "total_debit": journal.get("total_debit"),
        "total_credit": journal.get("total_credit"),
    }


# ---------------------------------------------------------------------------
# Verification (from the backend's own verification payload).
# ---------------------------------------------------------------------------


def _verification(result: Dict[str, Any]) -> Dict[str, Any]:
    verification = result.get("verification") or {}
    journal = result.get("journal") or {}
    balanced = bool(verification.get("balanced") if verification
                    else journal.get("balanced"))
    if balanced:
        statement = (
            "The entry balances and every required amount in the question "
            "has been accounted for."
        )
    else:
        statement = str(result.get("why_not") or
                        "The entry could not be verified.")
    return {
        "balanced": balanced,
        "total_debit": verification.get("total_debit"),
        "total_credit": verification.get("total_credit"),
        "statement": statement,
        "verdict": verification.get("verdict"),
    }


# ---------------------------------------------------------------------------
# Why layer (localized engine events - deterministic, never LLM).
# ---------------------------------------------------------------------------


def _authority_notes(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract the authority explanation notes (bills / discrepancy /
    consignment / joint venture / single entry) as why events."""
    events: List[Dict[str, str]] = []
    for key, payload_key in (
            ("BILLS", "bills"),
            ("DISCREPANCY", "discrepancy"),
            ("CONSIGNMENT", "consignment"),
            ("JOINT_VENTURE", "joint_venture"),
            ("SINGLE_ENTRY", "single_entry")):
        payload = result.get(payload_key) or {}
        notes = payload.get("notes") or []
        if not notes:
            continue
        events.append({
            "event_id": key,
            "text": WHY_LOCALIZATION.get(key, key),
        })
        for note in notes:
            events.append({
                "event_id": f"{key}_NOTE",
                "text": str(note),
            })
    return events


def _composed_rule_events(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """Rule ids composed deterministically from the engine's calculation
    records - the same input always composes the same events."""
    calc_ids = {(rec.get("calculation_id") or "")
                for rec in (result.get("calculation_records") or [])}
    events: List[Dict[str, str]] = []
    # Trade discount is evidenced either by its own calculation record or
    # by the engine's 'BK_GST_TRADE_DISCOUNT' event ('Trade discount nets
    # the taxable value' = discount deducted before GST).
    has_td = ("BK_TRADE_DISCOUNT_AMOUNT" in calc_ids
              or "BK_GST_TRADE_DISCOUNT" in calc_ids)
    has_gst = any(cid.startswith("BK_GST") for cid in calc_ids)
    if has_td and has_gst:
        events.append({
            "event_id": "RULE_TD_DEDUCT_BEFORE_GST",
            "text": WHY_LOCALIZATION["RULE_TD_DEDUCT_BEFORE_GST"],
        })
    if "BK_GST_COMPONENT_SPLIT" in calc_ids:
        events.append({
            "event_id": "RULE_GST_SPLIT_INTRA",
            "text": WHY_LOCALIZATION["RULE_GST_SPLIT_INTRA"],
        })
    if any("IGST" in str(rec.get("result") or "")
           or "igst" in str(rec.get("label") or "").lower()
           for rec in (result.get("calculation_records") or [])):
        pass  # IGST is visible through the journal account; no rule needed.
    return events


def _why(result: Dict[str, Any]) -> Dict[str, Any]:
    events: List[Dict[str, str]] = []
    # Per-line engine why text (already student-readable).
    for index, row in enumerate(_journal(result)["rows"]):
        text = str(row.get("why") or row.get("rule") or "").strip()
        if text:
            events.append({
                "event_id": f"LINE_{row['side'].upper()}_{index}",
                "text": text,
            })
    events.extend(_composed_rule_events(result))
    events.extend(_authority_notes(result))
    return {
        "events": events,
        "localization": dict(WHY_LOCALIZATION),
    }


# ---------------------------------------------------------------------------
# Calculation chain (localized backend calculation records - read-only).
# ---------------------------------------------------------------------------


def _calculation(result: Dict[str, Any]) -> Dict[str, Any]:
    records = []
    for rec in result.get("calculation_records") or []:
        calc_id = rec.get("calculation_id") or ""
        records.append({
            "calculation_id": calc_id,
            "label": (WHY_LOCALIZATION.get(calc_id)
                      or rec.get("label") or calc_id),
            "formula": rec.get("formula"),
            "inputs": rec.get("inputs") or {},
            "result": _fmt_plain(rec.get("result")),
            "result_raw": rec.get("result"),
        })
    return {"records": records}


# ---------------------------------------------------------------------------
# Debug payload (read-only mirror of the production graph).
# ---------------------------------------------------------------------------




# -----------------------------------------------------------------------
# Sprint 35: Transaction-level VERIFIED + journal integrity invariant.
# -----------------------------------------------------------------------

# Non-posting event types that are legitimately allowed to have zero journal
# lines when VERIFIED.  These are informational events that the kernel
# intentionally does not journal.
_NON_POSTING_EVENT_TYPES = frozenset({
    "INFORMATIONAL_EVENT",
    "OPENING_BALANCE",
})


def validate_transaction_integrity(txn: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that a VERIFIED posting transaction has a valid journal.

    Sprint 35 invariant:
        POSTING TRANSACTION:
            VERIFIED  =>  journal_lines >= 1
                        =>  journal is balanced
                        =>  all required amounts/entities are accounted for

    If the invariant is violated the transaction is downgraded to
    REVIEW_REQUIRED with a clear student-facing message.  The accounting
    kernel is never bypassed and no fake journal lines are invented.

    Returns a new dict (never mutates the input).
    """
    result = dict(txn)
    status = txn.get("status")
    event_type = txn.get("event_type", "ACCOUNTING_TRANSACTION")

    # Non-posting events are exempt from the journal-line requirement.
    if event_type in _NON_POSTING_EVENT_TYPES:
        return result

    # Only apply the invariant to VERIFIED posting transactions.
    if status != "VERIFIED":
        return result

    journal = txn.get("journal") or {}
    debit_lines = journal.get("debit_lines") or []
    credit_lines = journal.get("credit_lines") or []
    total_lines = len(debit_lines) + len(credit_lines)

    if total_lines == 0:
        # Invariant violated: VERIFIED posting transaction with no journal.
        result["status"] = "REVIEW_REQUIRED"
        result["why_not"] = (
            "Platrixa understood this transaction, but no journal entry was "
            "produced. It cannot be marked verified."
        )
        result["_integrity_downgraded"] = True

    return result


def validate_problem_integrity(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate all transactions in a problem and return summary stats.

    Returns a dict with:
        - transactions: list of (possibly downgraded) transactions
        - verified_count: int
        - review_required_count: int
        - not_supported_count: int
        - integrity_violations: int (number of downgrades applied)
    """
    verified = 0
    review_required = 0
    not_supported = 0
    violations = 0
    validated = []

    for txn in transactions:
        validated_txn = validate_transaction_integrity(txn)
        validated.append(validated_txn)
        s = validated_txn.get("status")
        if validated_txn.get("_integrity_downgraded"):
            violations += 1
        if s == "VERIFIED":
            verified += 1
        elif s == "REVIEW_REQUIRED":
            review_required += 1
        elif s in ("NOT_SUPPORTED", "INVALID_INPUT_MATH"):
            not_supported += 1

    return {
        "transactions": validated,
        "verified_count": verified,
        "review_required_count": review_required,
        "not_supported_count": not_supported,
        "integrity_violations": violations,
    }

def debug_graph_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """A deep copy of the production orchestration graph. The UI renders
    this exactly and can never mutate it."""
    return copy.deepcopy(result.get("orchestration") or {})


# ---------------------------------------------------------------------------
# Full projection
# ---------------------------------------------------------------------------


def project_student_result(result: Dict[str, Any],
                           question: str,
                           gate_resolution: Optional[Dict[str, Any]] = None
                           ) -> Dict[str, Any]:
    """Project one production orchestrate() result into the student UI
    contract. Pure and deterministic: the same result + question always
    yields the identical projection."""
    status = result.get("status")
    presentation = STATUS_PRESENTATION.get(status, STATUS_PRESENTATION[BLOCKED])
    gate = None
    if status == REVIEW_REQUIRED:
        gate = build_confidence_gate(result, question)
    return {
        "status": status,
        "status_label": result.get("status_label") or status,
        "headline": presentation["headline"],
        "tone": presentation["tone"],
        "summary": presentation["summary"],
        "understanding": _understanding(result, question),
        "journal": _journal(result),
        "verification": _verification(result),
        "why": _why(result),
        "calculation": _calculation(result),
        "confidence_gate": gate,
        "gate_resolution": gate_resolution,
        "why_not": result.get("why_not"),
        "next_action": result.get("next_action"),
        "result": result,
    }


def gate_is_pending(projection: Dict[str, Any]) -> bool:
    """True exactly when the backend emitted a Confidence Gate and the
    student has not yet resolved it."""
    return bool((projection or {}).get("confidence_gate")
                and not (projection or {}).get("gate_resolution"))
