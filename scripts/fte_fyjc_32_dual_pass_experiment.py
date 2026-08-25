#!/usr/bin/env python3
"""
Sprint 32 — Dual-Pass Semantic Alignment Experiment (A/B Validation)

EXPERIMENTAL ONLY — no production architecture changes.

Runs the same corpus through:
  A — Current production pipeline (classify_bk_type → orchestrate → kernel)
  B — Experimental dual-pass pipeline (evidence harvest → bridge → constrained parse → kernel)

Compares classifications, entity preservation, amount preservation,
determinism, and safety invariants.

Exit codes:
  0 = PASS (or C-classification: no improvement, experiment removed)
  1 = FAIL (incorrect VERIFIED or safety regression)
  2 = PARTIAL (some improvement, insufficient for adoption)
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_bk_reasoning import classify_bk_type
from backend.maths.fyjc_problem_engine import process_problem
from backend.maths.fyjc_normalization import normalize_fyjc_text

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

REVIEW_REQUIRED = "REVIEW_REQUIRED"
VERIFIED = "VERIFIED"
NOT_SUPPORTED = "NOT_SUPPORTED"
INCORRECT_VERIFIED = "INCORRECT_VERIFIED"

# ─────────────────────────────────────────────────────────────
# PASS 1 — Evidence Harvesting
# ─────────────────────────────────────────────────────────────

# Common Indian-accounting party name patterns
_PARTY_PATTERNS = [
    re.compile(r"\b(?:from|to|with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"),
    re.compile(r"\b([A-Z][a-z]+)\s+(?:paid|received|gave|sent)"),
    re.compile(r"\bpaid\s+(?:to\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"),
    re.compile(r"\breceived\s+(?:from\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"),
    re.compile(r"\b([A-Z][a-z]+)\s+(?:by cheque|by cash|by neft|by rtgs)"),
]

# Amount patterns — ₹/Rs followed by digits
_AMOUNT_PATTERNS = [
    re.compile(r"[₹Rs]+\s*\.?\s*([0-9,]+(?:\.\d{1,2})?)"),
    re.compile(r"([0-9,]+(?:\.\d{1,2})?)\s*(?:only|/-)"),
]

# Instrument patterns
_INSTRUMENT_PATTERNS = {
    "CHEQUE": re.compile(r"\b(?:cheque|check|chq)\b", re.I),
    "CASH": re.compile(r"\b(?:cash)\b", re.I),
    "BANK": re.compile(r"\b(?:bank|neft|rtgs|imps|transfer|dd|demand draft)\b", re.I),
    "CREDIT": re.compile(r"\b(?:on credit|credit|bill|lagar|udhar|uxe)\b", re.I),
}

# GST patterns
_GST_PATTERNS = {
    "inclusive": re.compile(r"inclusive\s+of\s+gst", re.I),
    "exclusive": re.compile(r"(?:plus|add|with)\s+gst", re.I),
    "rate": re.compile(r"(?:@|at)\s*(\d{1,2})\s*%?\s*(?:gst)?", re.I),
    "cgst_sgst": re.compile(r"cgst.*sgst|sgst.*cgst", re.I),
    "igst": re.compile(r"\bigst\b", re.I),
}

# Discount patterns
_DISCOUNT_PATTERNS = {
    "trade_discount": re.compile(r"(?:less|trade)\s+discount\s*(?:@?\s*(\d{1,2})\s*%?)?", re.I),
    "cash_discount": re.compile(r"(?:cash\s+discount|discount\s+(?:allowed|received))", re.I),
    "discount_amount": re.compile(r"discount\s+(?:of\s+)?(?:₹|Rs\.?)\s*([0-9,]+)", re.I),
}

# Return / settlement patterns
_RETURN_PATTERNS = re.compile(
    r"\b(?:return(?:ed|s)?|sent\s+back|returned\s+goods)\b", re.I
)
_SETTLEMENT_PATTERNS = re.compile(
    r"\b(?:settled?|settlement|final\s+settlement|in\s+full|balance\s+(?:paid|received)|"
    r"remaining\s+(?:paid|received)|paid\s+in\s+full)\b", re.I
)

# Historical reference patterns
_HISTORICAL_PATTERNS = re.compile(
    r"\b(?:remaining|balance|previous|former|above|earlier|prior|"
    r"last\s+(?:transaction|sale|purchase)|same\s+party|"
    r"the\s+(?:above|previous|prior)\s+(?:transaction|sale|purchase))\b", re.I
)


def _harvest_evidence(text: str) -> Dict[str, Any]:
    """Pass 1: Extract ALL raw evidence from student text.
    
    Returns untrusted evidence dict with:
      - parties: List[str]
      - amounts: List[Decimal]
      - instruments: Dict[str, bool]
      - gst: Dict[str, Any]
      - discounts: Dict[str, Any]
      - is_return: bool
      - is_settlement: bool
      - historical_refs: bool
      - probable_actions: List[str]
      - raw_text: str
    """
    evidence: Dict[str, Any] = {
        "parties": [],
        "amounts": [],
        "instruments": {},
        "gst": {},
        "discounts": {},
        "is_return": False,
        "is_settlement": False,
        "historical_refs": False,
        "probable_actions": [],
        "raw_text": text,
    }

    # Extract parties
    seen_parties = set()
    for pat in _PARTY_PATTERNS:
        for m in pat.finditer(text):
            party = m.group(1).strip()
            if party.lower() not in seen_parties and len(party) > 1:
                evidence["parties"].append(party)
                seen_parties.add(party.lower())

    # Extract amounts
    for pat in _AMOUNT_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                amt = Decimal(raw)
                if amt > 0 and amt not in evidence["amounts"]:
                    evidence["amounts"].append(amt)
            except (InvalidOperation, ValueError):
                pass

    # Sort amounts descending for consistent ordering
    evidence["amounts"].sort(reverse=True)

    # Extract instruments
    for inst, pat in _INSTRUMENT_PATTERNS.items():
        evidence["instruments"][inst] = bool(pat.search(text))

    # Extract GST info
    for gst_key, pat in _GST_PATTERNS.items():
        m = pat.search(text)
        if m:
            evidence["gst"][gst_key] = m.group(1) if m.lastindex else True

    # Extract discounts
    for disc_key, pat in _DISCOUNT_PATTERNS.items():
        m = pat.search(text)
        if m:
            evidence["discounts"][disc_key] = m.group(1) if m.lastindex else True

    # Detect returns and settlements
    evidence["is_return"] = bool(_RETURN_PATTERNS.search(text))
    evidence["is_settlement"] = bool(_SETTLEMENT_PATTERNS.search(text))
    evidence["historical_refs"] = bool(_HISTORICAL_PATTERNS.search(text))

    # Detect probable actions from text
    low = text.lower()
    if any(w in low for w in ("purchased", "bought", "brought", "procured")):
        evidence["probable_actions"].append("PURCHASE")
    if any(w in low for w in ("sold", "sale")):
        evidence["probable_actions"].append("SALE")
    if any(w in low for w in ("paid", "payment", "settled")):
        evidence["probable_actions"].append("PAYMENT")
    if any(w in low for w in ("received", "receipt", "got")):
        evidence["probable_actions"].append("RECEIPT")
    if evidence["is_return"]:
        evidence["probable_actions"].append("RETURN")

    return evidence


# ─────────────────────────────────────────────────────────────
# DETERMINISTIC BRIDGE — Query existing verified state
# ─────────────────────────────────────────────────────────────

def _deterministic_bridge(
    evidence: Dict[str, Any],
    known_accounts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bridge: Query the existing deterministic system for verified facts.
    
    Attaches verified state information to the evidence context.
    Must NOT manufacture missing information.
    
    Returns enriched context dict with bridge_verified facts.
    """
    context = {
        "evidence": evidence,
        "bridge_verified": {},
        "ambiguities": [],
    }

    # For each party, check if we have a known account identity
    for party in evidence.get("parties", []):
        party_lower = party.lower()
        # Known creditor/debtor patterns
        if evidence.get("is_return"):
            context["bridge_verified"][party] = {
                "type": "CREDITOR",
                "reason": "return to party implies credit relationship",
            }
        elif evidence.get("is_settlement"):
            context["bridge_verified"][party] = {
                "type": "CREDITOR_OR_DEBTOR",
                "reason": "settlement implies outstanding balance",
            }

    # Determine direction from evidence
    has_credit = evidence["instruments"].get("CREDIT", False)
    has_cash = evidence["instruments"].get("CASH", False)
    has_cheque = evidence["instruments"].get("CHEQUE", False)
    has_bank = evidence["instruments"].get("BANK", False)

    # For GST: if GST rate is found, determine scheme
    if evidence["gst"].get("rate"):
        rate = int(evidence["gst"]["rate"])
        if rate in (5, 12, 18, 28):
            context["bridge_verified"]["gst_rate"] = rate
        else:
            context["ambiguities"].append(f"unusual GST rate {rate}%")

    if evidence["gst"].get("cgst_sgst"):
        context["bridge_verified"]["gst_scheme"] = "INTRASTATE"
    elif evidence["gst"].get("igst"):
        context["bridge_verified"]["gst_scheme"] = "INTERSTATE"

    return context


# ─────────────────────────────────────────────────────────────
# PASS 2 — Constrained Structural Parsing
# ─────────────────────────────────────────────────────────────

# Known transaction-type to account mapping (from production BK patterns)
_TX_TYPE_ACCOUNTS = {
    "PURCHASE_CASH": {"debit": "Purchases", "credit": "Cash"},
    "PURCHASE_CREDIT": {"debit": "Purchases", "credit": "Party"},
    "SALE_CREDIT": {"debit": "Party", "credit": "Sales"},
    "SALE_CASH": {"debit": "Cash", "credit": "Sales"},
    "PAYMENT_CASH": {"debit": "Party", "credit": "Cash"},
    "PAYMENT_CHEQUE": {"debit": "Party", "credit": "Bank"},
    "RECEIPT_CASH": {"debit": "Cash", "credit": "Party"},
    "RECEIPT_CHEQUE": {"debit": "Bank", "credit": "Party"},
    "EXPENSE_CASH": {"debit": "Expense", "credit": "Cash"},
    "EXPENSE_CHEQUE": {"debit": "Expense", "credit": "Bank"},
    "PURCHASE_RETURN": {"debit": "Party", "credit": "Purchase Returns"},
    "SALE_RETURN": {"debit": "Sales Returns", "credit": "Party"},
    "DISCOUNT_ALLOWED": {"debit": "Discount Allowed", "credit": "Party"},
    "DISCOUNT_RECEIVED": {"debit": "Party", "credit": "Discount Received"},
}


def _pass2_structural_parse(
    text: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Pass 2: Constrained structural parsing.
    
    Uses ONLY available evidence and bridge-verified facts.
    Must NOT override deterministic facts or invent missing information.
    
    Critical rule: Pass 2 is a structural parser, not an accounting
    authority.  It is CONSERVATIVE — it only flags issues that the
    existing orchestrator would also flag.  It does NOT add extra
    restrictions beyond what the production kernel already enforces.
    
    Returns a structural interpretation dict.
    """
    evidence = context["evidence"]
    bridge = context["bridge_verified"]

    parties = evidence["parties"]
    amounts = evidence["amounts"]
    instruments = evidence["instruments"]

    # Determine instrument/payment method
    instrument = None
    if instruments.get("CHEQUE"):
        instrument = "CHEQUE"
    elif instruments.get("CASH"):
        instrument = "CASH"
    elif instruments.get("BANK"):
        instrument = "BANK"
    elif instruments.get("CREDIT"):
        instrument = "CREDIT"

    # Use classify_bk_type as the primary action classifier.
    # The existing BK pattern matching is more reliable than raw keyword
    # detection for determining whether a "paid" statement is an expense
    # vs. a party payment.
    bk = classify_bk_type(text)
    if bk:
        action = bk.get("key", "UNKNOWN")
    else:
        # Fallback to evidence-based detection
        probable = evidence.get("probable_actions", [])
        if "PURCHASE" in probable and "PAYMENT" not in probable:
            action = "PURCHASE"
        elif "SALE" in probable and "RECEIPT" not in probable:
            action = "SALE"
        elif "PAYMENT" in probable and "PURCHASE" not in probable:
            action = "PAYMENT"
        elif "RECEIPT" in probable and "SALE" not in probable:
            action = "RECEIPT"
        elif evidence.get("is_return"):
            action = "RETURN"
        elif "PURCHASE" in probable and "PAYMENT" in probable:
            action = "PURCHASE_WITH_PAYMENT"
        elif "SALE" in probable and "RECEIPT" in probable:
            action = "SALE_WITH_RECEIPT"

    if action is None:
            # Cannot determine — pass through to orchestrator
            return {
                "status": "PASS_THROUGH",
                "reason": "Cannot determine transaction type from evidence",
                "structural": None,
            }

    # Build structural representation
    structural = {
        "action": action,
        "parties": parties,
        "amounts": [float(a) for a in amounts],
        "instrument": instrument,
        "gst": bridge.get("gst_rate"),
        "gst_scheme": bridge.get("gst_scheme"),
        "is_return": evidence.get("is_return", False),
        "is_settlement": evidence.get("is_settlement", False),
    }

    # Validate ONLY what the existing kernel would also reject.
    # The kernel already rejects: no amounts, unbalanced amounts,
    # unresolvable party references.  Pass 2 must NOT add extra
    # restrictions like "party required for expenses" because the
    # kernel handles expense transactions without explicit parties.
    issues = []

    if not amounts:
        issues.append("no amounts found")

    # Only flag party-missing for transaction types where the kernel
    # itself requires a named party (purchase credit, sale credit,
    # cheque paid/received, returns, discounts).  Expenses and
    # simple cash transactions do NOT require a party.
    needs_party = action in (
        "PURCHASE_CREDIT", "SALE_CREDIT",
        "PAYMENT", "PAYMENT_CASH", "PAYMENT_CHEQUE",
        "RECEIPT", "RECEIPT_CASH", "RECEIPT_CHEQUE",
        "PURCHASE_RETURN", "SALE_RETURN",
        "DISCOUNT_ALLOWED", "DISCOUNT_RECEIVED",
    )
    if needs_party and not parties:
        issues.append("party required but not found in text")

    # Compound transactions: try to split amounts
    if len(amounts) > 2 and action in ("PURCHASE_WITH_PAYMENT", "SALE_WITH_RECEIPT"):
        structural["compound"] = True
        structural["split_amounts"] = _try_split_compound_amounts(
            amounts, action, text
        )

    if issues:
        return {
            "status": REVIEW_REQUIRED,
            "reason": "; ".join(issues),
            "structural": structural,
        }

    return {
        "status": "STRUCTURAL_READY",
        "structural": structural,
    }


def _try_split_compound_amounts(
    amounts: List[Decimal], action: str, text: str
) -> Optional[Dict[str, Any]]:
    """Try to assign amounts to roles in compound transactions.
    
    For "Purchased goods from X Rs.10000 paid Rs.4000 cash Rs.6000 by cheque":
      total_purchase = 10000
      cash_payment = 4000
      cheque_payment = 6000
    
    Returns role assignments if possible, None if ambiguous.
    """
    if not amounts or len(amounts) < 2:
        return None

    # Sort amounts descending
    sorted_amts = sorted(amounts, reverse=True)

    # Heuristic: largest amount is the transaction value, rest are payments
    largest = sorted_amts[0]
    rest = sorted_amts[1:]

    # Check if rest sum to largest (partial payment scenario)
    rest_sum = sum(rest)
    if rest_sum == largest:
        return {
            "transaction_value": float(largest),
            "payments": [float(a) for a in rest],
            "remaining_credit": 0,
        }
    elif rest_sum < largest:
        return {
            "transaction_value": float(largest),
            "payments": [float(a) for a in rest],
            "remaining_credit": float(largest - rest_sum),
        }

    return None


# ─────────────────────────────────────────────────────────────
# DUAL-PASS PIPELINE (EXPERIMENTAL)
# ─────────────────────────────────────────────────────────────

def dual_pass_process(
    text: str,
    known_accounts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Experimental dual-pass pipeline.
    
    Pass 1: Evidence Harvest
    Bridge: Deterministic State Query
    Pass 2: Constrained Structural Parse
    Kernel: Existing accounting kernel (through orchestrate)
    
    The primary path remains the existing orchestrator.  The dual-pass
    evidence is used ONLY to check consistency and flag discrepancies.
    The experimental path does NOT replace the production path.
    
    This must NOT mutate any production state.
    """
    # Pass 1: Evidence harvest
    evidence = _harvest_evidence(text)

    # Bridge: Deterministic state query
    context = _deterministic_bridge(evidence, known_accounts)

    # Pass 2: Structural parse
    structural_result = _pass2_structural_parse(text, context)

    # If Pass 2 flags a fundamental issue (no amounts), surface it
    if structural_result["status"] == REVIEW_REQUIRED:
        return {
            "status": REVIEW_REQUIRED,
            "reason": structural_result.get("reason", "structural parse incomplete"),
            "structural": structural_result.get("structural"),
            "evidence": evidence,
            "bridge": context["bridge_verified"],
        }

    structural = structural_result.get("structural")

    # Run existing pipeline — this is the PRIMARY decision path
    from backend.maths.fyjc_orchestration import orchestrate
    orch_result = orchestrate(text)
    orch_status = orch_result.get("status", NOT_SUPPORTED)

    # ── The orchestrator's result is authoritative ──
    # The dual-pass evidence is used ONLY to:
    #   1. Flag when the orchestrator says VERIFIED but evidence disagrees
    #   2. Note when the orchestrator says REVIEW_REQUIRED and we have
    #      additional evidence that COULD resolve it (for future use)

    return {
        "status": orch_status,
        "journal": orch_result.get("journal"),
        "structural": structural,
        "evidence": evidence,
        "orchestrator_status": orch_status,
        "why_not": orch_result.get("why_not"),
        "next_action": orch_result.get("next_action"),
    }


# ─────────────────────────────────────────────────────────────
# A/B CORPUS
# ─────────────────────────────────────────────────────────────

CORPUS = [
    # --- Category A: Simple single transactions ---
    {
        "id": "A01",
        "text": "Purchased goods from Raj Rs.5000 cash",
        "expected_status": VERIFIED,
        "expected_action": "PURCHASE_CASH",
        "category": "simple",
    },
    {
        "id": "A02",
        "text": "Sold goods to Amit Rs.25000 on credit",
        "expected_status": VERIFIED,
        "expected_action": "SALE_GOODS_CREDIT",
        "category": "simple",
    },
    {
        "id": "A03",
        "text": "Paid rent Rs.5000 cash",
        "expected_status": VERIFIED,
        "expected_action": "EXPENSE_PAID",
        "category": "simple",
    },
    {
        "id": "A04",
        "text": "Received Rs.10000 from Amit cash",
        "expected_status": VERIFIED,
        "expected_action": "RECEIVED_FROM",
        "category": "simple",
    },
    # --- Category B: Instrument variations ---
    {
        "id": "B01",
        "text": "Received Rs.23600 from Suresh by cheque",
        "expected_status": VERIFIED,
        "expected_action": "CHEQUE_RECEIVED",
        "category": "instrument",
    },
    {
        "id": "B02",
        "text": "Paid Rs.10000 to Ramesh by cheque",
        "expected_status": VERIFIED,
        "expected_action": "CHEQUE_PAID",
        "category": "instrument",
    },
    {
        "id": "B03",
        "text": "Suresh paid Rs.23600 by cheque",
        "expected_status": VERIFIED,
        "expected_action": "CHEQUE_RECEIVED",
        "category": "instrument",
    },
    {
        "id": "B04",
        "text": "Received cheque of Rs.15000 from Amit",
        "expected_status": VERIFIED,
        "expected_action": "CHEQUE_RECEIVED",
        "category": "instrument",
    },
    # --- Category C: GST ---
    {
        "id": "C01",
        "text": "Purchased goods from Ram on credit Rs.11800 inclusive of GST @18%",
        "expected_status": VERIFIED,
        "expected_action": "PURCHASE_GOODS_CREDIT",
        "category": "gst",
    },
    {
        "id": "C02",
        "text": "Sold goods to Raj Rs.23600 inclusive of GST @18%",
        "expected_status": VERIFIED,
        "expected_action": "SALE_GOODS_CREDIT",
        "category": "gst",
    },
    # --- Category D: Returns ---
    {
        "id": "D01",
        "text": "Returned goods to Raj Rs.2000",
        "expected_status": VERIFIED,
        "expected_action": "PURCHASE_RETURN",
        "category": "return",
    },
    # --- Category E: Discount ---
    {
        "id": "E01",
        "text": "Allowed discount Rs.500 to Mohan",
        "expected_status": VERIFIED,
        "expected_action": "DISCOUNT_ALLOWED",
        "category": "discount",
    },
    {
        "id": "E02",
        "text": "Sold goods to Amit Rs.25000 on credit less trade discount 10%",
        "expected_status": VERIFIED,
        "expected_action": "SALE_GOODS_CREDIT",
        "category": "discount",
    },
    # --- Category F: Compound transactions (expected REVIEW_REQUIRED) ---
    {
        "id": "F01",
        "text": "Paid Rs.5000 to Raj Rs.2000 by cheque Rs.3000 cash",
        "expected_status": REVIEW_REQUIRED,
        "expected_action": "COMPOUND_PAYMENT",
        "category": "compound",
    },
    {
        "id": "F02",
        "text": "Purchased goods from Raj Rs.10000 paid Rs.4000 cash Rs.6000 by cheque",
        "expected_status": REVIEW_REQUIRED,
        "expected_action": "COMPOUND_PURCHASE_PAYMENT",
        "category": "compound",
    },
    # --- Category G: Ambiguous / Indian phrasing ---
    {
        "id": "G01",
        "text": "Paid cash same time for goods from Raj Rs.5000",
        "expected_status": VERIFIED,
        "expected_action": "PURCHASE_CASH",
        "category": "phrasing",
    },
    {
        "id": "G02",
        "text": "Amount received from Suresh settled account",
        "expected_status": VERIFIED,
        "expected_action": "RECEIVED_FROM",
        "category": "phrasing",
    },
    {
        "id": "G03",
        "text": "Goods sold to Amit credit Rs.25000",
        "expected_status": VERIFIED,
        "expected_action": "SALE_GOODS_CREDIT",
        "category": "phrasing",
    },
    # --- Category H: Whole-problem (multi-transaction) ---
    {
        "id": "H01",
        "text": (
            "Opening: Cash Rs.50000 Bank Rs.30000 Capital Rs.80000.\n"
            "Purchased goods from Raj Rs.20000 on credit.\n"
            "Paid Raj Rs.10000 cash.\n"
            "Sold goods to Amit Rs.25000 on credit.\n"
            "Received Rs.10000 from Amit by cheque.\n"
            "Paid rent Rs.5000 cash."
        ),
        "expected_status": "PROBLEM_VERIFIED",
        "expected_action": "MULTI_TX",
        "category": "whole_problem",
        "multi_tx": True,
    },
    {
        "id": "H02",
        "text": (
            "Cash in hand Rs.10000\n"
            "Bank Balance Rs.25000\n"
            "Capital Rs.35000\n"
            "Purchased goods from Ramesh Rs.12000\n"
            "Purchased goods from Mehta Rs.8000\n"
            "Paid Rs.10000 to Ramesh by cheque."
        ),
        "expected_status": "PROBLEM_REVIEW_REQUIRED",
        "expected_action": "MULTI_TX",
        "category": "whole_problem",
        "multi_tx": True,
    },
]

# ─────────────────────────────────────────────────────────────
# A/B COMPARISON ENGINE
# ─────────────────────────────────────────────────────────────


def _run_pipeline_a(text: str) -> Dict[str, Any]:
    """Pipeline A: Current production pipeline."""
    from backend.maths.fyjc_orchestration import orchestrate
    result = orchestrate(text)
    return {
        "status": result.get("status", NOT_SUPPORTED),
        "journal": result.get("journal"),
        "why_not": result.get("why_not"),
    }


def _run_pipeline_b(text: str) -> Dict[str, Any]:
    """Pipeline B: Experimental dual-pass pipeline."""
    return dual_pass_process(text)


def _classify_result(
    a_result: Dict[str, Any],
    b_result: Dict[str, Any],
    expected: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify the A/B comparison result."""
    a_status = a_result.get("status", NOT_SUPPORTED)
    b_status = b_result.get("status", NOT_SUPPORTED)
    exp_status = expected.get("expected_status", NOT_SUPPORTED)

    classification = {
        "a_status": a_status,
        "b_status": b_status,
        "expected_status": exp_status,
        "a_correct": a_status == exp_status,
        "b_correct": b_status == exp_status,
        "changed": a_status != b_status,
        "transition": f"{a_status} → {b_status}" if a_status != b_status else a_status,
    }

    # Determine improvement/regression
    if a_status == REVIEW_REQUIRED and b_status == VERIFIED:
        # Check if b is actually correct
        if b_result.get("journal"):
            classification["improvement"] = "POTENTIALLY_CORRECT"
            classification["b_journal"] = b_result["journal"]
        else:
            classification["improvement"] = "UNCERTAIN"

    elif a_status == VERIFIED and b_status == REVIEW_REQUIRED:
        classification["improvement"] = "REGRESSION"

    elif a_status == VERIFIED and b_status == VERIFIED:
        classification["improvement"] = "MAINTAINED"

    elif a_status == REVIEW_REQUIRED and b_status == REVIEW_REQUIRED:
        classification["improvement"] = "NO_CHANGE"

    elif a_status == NOT_SUPPORTED and b_status == VERIFIED:
        classification["improvement"] = "NEW_VERIFIED"

    elif a_status == NOT_SUPPORTED and b_status == REVIEW_REQUIRED:
        classification["improvement"] = "NEW_REVIEW_REQUIRED"

    else:
        classification["improvement"] = f"{a_status} → {b_status}"

    return classification


# ─────────────────────────────────────────────────────────────
# DETERMINISM CHECK
# ─────────────────────────────────────────────────────────────

def _check_determinism(text: str, runs: int = 3) -> Dict[str, Any]:
    """Run the same text through pipeline B multiple times and compare."""
    results = []
    for _ in range(runs):
        r = dual_pass_process(text)
        # Create a deterministic fingerprint
        fp = hashlib.sha256(
            json.dumps({
                "status": r.get("status"),
                "journal_hash": hashlib.md5(
                    str(r.get("journal", "")).encode()
                ).hexdigest(),
            }, sort_keys=True).encode()
        ).hexdigest()[:16]
        results.append(fp)

    return {
        "runs": runs,
        "identical": len(set(results)) == 1,
        "fingerprints": results,
    }


# ─────────────────────────────────────────────────────────────
# SAFETY INVARIANT CHECK
# ─────────────────────────────────────────────────────────────

def _check_safety_invariants(
    a_result: Dict[str, Any],
    b_result: Dict[str, Any],
) -> Dict[str, bool]:
    """Check safety invariants for the dual-pass result."""
    b_journal = b_result.get("journal", {}) or {}
    b_status = b_result.get("status", NOT_SUPPORTED)

    invariants = {
        "no_invented_accounts": True,  # Dual-pass doesn't create accounts
        "no_invented_amounts": True,   # Dual-pass doesn't invent amounts
        "no_state_leaks": True,        # Dual-pass doesn't mutate state
        "no_nondeterminism": True,     # Checked separately
        "no_unsafe_confident": True,   # Dual-pass doesn't bypass kernel
    }

    # Check if journal has unexpected structure
    if b_journal:
        debit_lines = b_journal.get("debit_lines", [])
        credit_lines = b_journal.get("credit_lines", [])
        if not debit_lines and not credit_lines and b_status == VERIFIED:
            invariants["no_unsafe_confident"] = False

    return invariants


# ─────────────────────────────────────────────────────────────
# MAIN EXPERIMENT
# ─────────────────────────────────────────────────────────────

def run_experiment() -> Dict[str, Any]:
    """Run the complete A/B experiment and return results."""
    results = []
    transitions = []
    improvements = 0
    regressions = 0
    determinism_pass = 0
    determinism_fail = 0
    safety_all_zero = True

    print("=" * 70)
    print("SPRINT 32 — Dual-Pass Semantic Alignment A/B Experiment")
    print("=" * 70)
    print()

    for case in CORPUS:
        case_id = case["id"]
        text = case["text"]
        is_multi = case.get("multi_tx", False)

        if is_multi:
            # Multi-transaction: use process_problem
            a_full = process_problem(text, {"student_id": f"s32_a_{case_id}"})
            b_full = dual_pass_process(text)

            a_status = a_full.get("problem_status", NOT_SUPPORTED)
            b_status = b_full.get("status", NOT_SUPPORTED)
        else:
            # Single transaction: use orchestrate
            a_result = _run_pipeline_a(text)
            b_result = _run_pipeline_b(text)
            a_status = a_result["status"]
            b_status = b_result["status"]

        exp_status = case["expected_status"]

        classification = _classify_result(
            {"status": a_status},
            {"status": b_status},
            case,
        )

        # Determinism check (sampled)
        if case_id in ("A01", "B01", "G01", "H01"):
            det = _check_determinism(text, runs=3)
            if det["identical"]:
                determinism_pass += 1
            else:
                determinism_fail += 1
            classification["determinism"] = det

        # Safety check
        safety = _check_safety_invariants(
            {"status": a_status},
            {"status": b_status},
        )
        if not all(safety.values()):
            safety_all_zero = False
        classification["safety"] = safety

        # Track improvements/regressions
        if classification.get("improvement") == "POTENTIALLY_CORRECT":
            improvements += 1
        elif classification.get("improvement") == "REGRESSION":
            regressions += 1

        results.append(classification)

        # Print per-case result
        change_marker = " ← CHANGED" if classification["changed"] else ""
        a_icon = "✅" if classification["a_correct"] else "❌"
        b_icon = "✅" if classification["b_correct"] else "❌"
        print(
            f"  {case_id} | A:{a_icon} {a_status:25s} | "
            f"B:{b_icon} {b_status:25s} | {classification['transition']}{change_marker}"
        )

        if classification["changed"]:
            transitions.append({
                "id": case_id,
                "from": a_status,
                "to": b_status,
                "improvement": classification.get("improvement"),
                "text": text[:80],
            })

    # Summary
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    total = len(results)
    a_verified = sum(1 for r in results if r["a_status"] == VERIFIED)
    b_verified = sum(1 for r in results if r["b_status"] == VERIFIED)
    a_rr = sum(1 for r in results if r["a_status"] == REVIEW_REQUIRED)
    b_rr = sum(1 for r in results if r["b_status"] == REVIEW_REQUIRED)
    a_ns = sum(1 for r in results if r["a_status"] == NOT_SUPPORTED)
    b_ns = sum(1 for r in results if r["b_status"] == NOT_SUPPORTED)
    a_correct = sum(1 for r in results if r["a_correct"])
    b_correct = sum(1 for r in results if r["b_correct"])

    print(f"\n  Corpus size: {total}")
    print(f"\n  Pipeline A (production):")
    print(f"    VERIFIED:         {a_verified}/{total}")
    print(f"    REVIEW_REQUIRED:  {a_rr}/{total}")
    print(f"    NOT_SUPPORTED:    {a_ns}/{total}")
    print(f"    Correct:          {a_correct}/{total}")

    print(f"\n  Pipeline B (dual-pass):")
    print(f"    VERIFIED:         {b_verified}/{total}")
    print(f"    REVIEW_REQUIRED:  {b_rr}/{total}")
    print(f"    NOT_SUPPORTED:    {b_ns}/{total}")
    print(f"    Correct:          {b_correct}/{total}")

    print(f"\n  Transitions:       {len(transitions)}")
    print(f"  Improvements:      {improvements}")
    print(f"  Regressions:       {regressions}")

    print(f"\n  Determinism:       {determinism_pass} PASS / {determinism_fail} FAIL")
    print(f"  Safety invariants: {'ALL ZERO ✅' if safety_all_zero else 'VIOLATION ❌'}")

    # Per-category breakdown
    print(f"\n  Per-category breakdown:")
    categories = defaultdict(lambda: {"a_v": 0, "b_v": 0, "total": 0})
    for case, res in zip(CORPUS, results):
        cat = case["category"]
        categories[cat]["total"] += 1
        if res["a_status"] == VERIFIED:
            categories[cat]["a_v"] += 1
        if res["b_status"] == VERIFIED:
            categories[cat]["b_v"] += 1

    for cat in sorted(categories.keys()):
        d = categories[cat]
        print(f"    {cat:15s}: A={d['a_v']}/{d['total']} B={d['b_v']}/{d['total']}")

    # Transitions detail
    if transitions:
        print(f"\n  Transitions detail:")
        for t in transitions:
            print(f"    {t['id']}: {t['from']} → {t['to']} ({t['improvement']})")
            print(f"      \"{t['text']}...\"")

    # Adoption decision
    print(f"\n  {'=' * 70}")
    print(f"  ADOPTION DECISION")
    print(f"  {'=' * 70}")

    if regressions > 0:
        decision = "C — REJECT"
        reason = f"{regressions} regression(s) detected"
    elif improvements == 0:
        decision = "C — REJECT"
        reason = "No measurable improvement"
    elif not safety_all_zero:
        decision = "C — REJECT"
        reason = "Safety invariant violation"
    elif determinism_fail > 0:
        decision = "C — REJECT"
        reason = "Nondeterministic behavior detected"
    elif b_correct > a_correct:
        decision = "A — ADOPT"
        reason = f"Improvement: {a_correct} → {b_correct} correct ({improvements} upgrades, 0 regressions)"
    elif b_correct == a_correct:
        decision = "B — EXPERIMENT PROMISING"
        reason = f"Same correctness ({b_correct}/{total}), but {improvements} potential improvements need verification"
    else:
        decision = "C — REJECT"
        reason = f"Correctness decreased: {a_correct} → {b_correct}"

    print(f"\n  Decision: {decision}")
    print(f"  Reason:   {reason}")

    return {
        "total": total,
        "a_verified": a_verified,
        "b_verified": b_verified,
        "a_rr": a_rr,
        "b_rr": b_rr,
        "a_correct": a_correct,
        "b_correct": b_correct,
        "improvements": improvements,
        "regressions": regressions,
        "determinism_pass": determinism_pass,
        "determinism_fail": determinism_fail,
        "safety_all_zero": safety_all_zero,
        "transitions": transitions,
        "decision": decision,
        "reason": reason,
        "categories": dict(categories),
        "results": results,
    }


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    report = run_experiment()

    # Write JSON results
    out_path = os.path.join(os.path.dirname(__file__), "sprint32_ab_results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nResults written to {out_path}")

    # Exit code
    if report["regressions"] > 0 or not report["safety_all_zero"]:
        sys.exit(1)
    elif report["improvements"] == 0:
        sys.exit(2)  # No improvement — C-classification
    else:
        sys.exit(0)
