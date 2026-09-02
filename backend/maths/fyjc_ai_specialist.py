"""
Platrixa — FYJC AI Specialist (Phase 2)
=========================================

Deterministic language-understanding component for FYJC accounting transactions.

Architecture:
    Student text
        ↓
    FYJCAISpecialist.parse()     ← THIS MODULE
        ↓
    ExpandedInterpretation dict   (18-field contract)
        ↓
    Schema verifier              (Phase 1: fyjc_contract.py + schema_verifier.py)
        ↓
    Grounding gate               (fyjc_ai_adapter.py)
        ↓
    Deterministic kernel         (fyjc_bk_reasoning.py — UNTOUCHED)

Responsibility boundary:
    THIS MODULE:
      - Understands natural language accounting text
      - Extracts parties, amounts, payment methods, references
      - Classifies transaction types
      - Detects ambiguity and missing information
      - Produces field-level confidence
      - Generates safety/scope flags

    NOT THIS MODULE:
      - Debit/credit decisions
      - Journal generation
      - Accounting rules
      - Balance validation
      - Final accounting treatment

Safety rules:
    - Never invent financial facts not present in the input
    - Never claim certainty when information is ambiguous
    - Never produce VERIFIED status
    - Never produce journal entries
    - Unknown/unresolvable information → UNRESOLVED with low confidence

Pure module: no Streamlit, no AI model, no network. Deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.maths.fyjc_contract import (
    ExpandedInterpretation,
    VALID_TRANSACTION_TYPES as VALID_TX_ENUM,
    VALID_PAYMENT_METHODS as VALID_PM_ENUM,
    VALID_AMBIGUITY_TYPES as VALID_AMBIG_FLAGS,
    VALID_GROUNDING_LEVELS,
    VALID_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS,
)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_PURCHASE_WORDS: Set[str] = {
    "purchased", "bought", "procured", "acquired", "obtained",
    "brought", "got", "taken",
}
_SALE_WORDS: Set[str] = {
    "sold", "supplied", "delivered", "sales",
}
_EXPENSE_WORDS: Set[str] = {
    "paid rent", "paid salary", "paid electricity", "paid wages",
    "paid carriage", "paid telephone", "paid insurance", "paid stationery",
    "paid for", "payment of",
}
_CAPITAL_WORDS: Set[str] = {
    "introduced capital", "capital introduced", "started business",
    "invested", "owner invested",
}
_RETURN_WORDS: Set[str] = {
    "returned", "return", "goods returned", "purchase return",
    "sales return", "returned goods",
}
_SETTLEMENT_WORDS: Set[str] = {
    "settled", "settlement", "full settlement", "paid in full",
    "cleared account", "account settled", "paid off",
}
_DRAWING_WORDS: Set[str] = {
    "withdrew", "withdrew for personal", "drawing", "owner withdrew",
}

_CASH_WORDS: Set[str] = {
    "cash", "by cash", "for cash", "in cash", "paid cash",
}
_CHEQUE_WORDS: Set[str] = {
    "cheque", "by cheque", "cheque no", "cheque number",
}
_BANK_WORDS: Set[str] = {
    "bank transfer", "neft", "rtgs", "imps", "online transfer",
}
_UPI_WORDS: Set[str] = {
    "upi", "phonepe", "paytm", "gpay", "google pay",
}
_CREDIT_WORDS: Set[str] = {
    "credit", "on credit", "on account", "credit purchase", "credit sale",
}

# Amount patterns (INR)
_AMOUNT_PATTERNS: List[re.Pattern] = [
    re.compile(r"Rs\.?\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
    re.compile(r"INR\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
    re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
    re.compile(r"rupees?\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
]

# Party extraction patterns
_PARTY_PATTERNS: List[re.Pattern] = [
    re.compile(r"from\s+([A-Z][a-zA-Z\s&.'-]+?)(?:\s+for|\s+Rs|\s+on|\s+worth|\s*,|\s*\.|\s*$)", re.IGNORECASE),
    re.compile(r"to\s+([A-Z][a-zA-Z\s&.'-]+?)(?:\s+for|\s+Rs|\s+on|\s+worth|\s*,|\s*\.|\s*$)", re.IGNORECASE),
    re.compile(r"paid\s+(?:to\s+)?([A-Z][a-zA-Z\s&.'-]+?)(?:\s+for|\s+Rs|\s+on|\s+worth|\s*,|\s*\.|\s*$)", re.IGNORECASE),
    re.compile(r"(?:from|to)\s+([A-Z][a-zA-Z\s&.'-]+?)(?:\s+(?:within|outside)\s+state)", re.IGNORECASE),
]

_NON_PARTY_WORDS: Set[str] = {
    "goods", "cash", "bank", "the", "some", "all", "furniture",
    "stationery", "rent", "salary", "wages", "electricity",
    "telephone", "carriage", "insurance", "office", "home",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Normalise text for matching."""
    return text.lower().strip().replace(",", "").replace(".", "")


def _extract_parties(text: str) -> List[str]:
    """Extract party names from natural language text."""
    parties: List[str] = []
    for pattern in _PARTY_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            # Clean trailing prepositions
            name = re.sub(r"\s+(for|Rs|on|worth|by|and|within|outside)\s*$", "", name, flags=re.IGNORECASE).strip()
            # Remove locality phrases
            name = re.sub(r"\s+(within state|outside state|intra.state|inter.state|local|imported)\s*$", "", name, flags=re.IGNORECASE).strip()
            # Filter non-party words
            if len(name) > 1 and name.lower() not in _NON_PARTY_WORDS:
                parties.append(name)

    # Deduplicate preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for p in parties:
        canonical = p.lower().strip()
        if canonical not in seen:
            seen.add(canonical)
            unique.append(p)
    return unique


def _extract_amounts(text: str) -> List[Dict[str, str]]:
    """Extract monetary amounts from text."""
    amounts: List[Dict[str, str]] = []
    for pattern in _AMOUNT_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                value = str(round(float(raw), 2))
                amounts.append({"value": value, "currency": "INR", "source": "explicit"})
            except ValueError:
                pass
    # Deduplicate by value
    seen: Set[str] = set()
    unique: List[Dict[str, str]] = []
    for a in amounts:
        if a["value"] not in seen:
            seen.add(a["value"])
            unique.append(a)
    return unique


def _detect_payment_method(text: str) -> Tuple[str, str, float]:
    """Detect payment method. Returns (method, source_text, confidence)."""
    lower = text.lower()
    for w in _UPI_WORDS:
        if w in lower:
            return "UPI", w, 0.90
    for w in _CHEQUE_WORDS:
        if w in lower:
            return "CHEQUE", w, 0.90
    for w in _BANK_WORDS:
        if w in lower:
            return "BANK", w, 0.85
    for w in _CASH_WORDS:
        if w in lower:
            return "CASH", w, 0.90
    for w in _CREDIT_WORDS:
        if w in lower:
            return "CREDIT", w, 0.85
    return "UNKNOWN", "", 0.10


def _detect_transaction_type(text: str) -> Tuple[str, str, float]:
    """Detect transaction type. Returns (type, source_text, confidence)."""
    lower = text.lower()
    # Check returns first (more specific)
    for w in _RETURN_WORDS:
        if w in lower:
            return "RETURN_OUT", w, 0.85
    for w in _SETTLEMENT_WORDS:
        if w in lower:
            return "SETTLEMENT", w, 0.85
    for w in _DRAWING_WORDS:
        if w in lower:
            return "DRAWING", w, 0.85
    for w in _CAPITAL_WORDS:
        if w in lower:
            return "CAPITAL", w, 0.85
    for w in _EXPENSE_WORDS:
        if w in lower:
            return "EXPENSE", w, 0.80
    for w in _SALE_WORDS:
        if w in lower:
            return "SALE", w, 0.85
    for w in _PURCHASE_WORDS:
        if w in lower:
            return "PURCHASE", w, 0.85
    return "UNKNOWN", "", 0.10


def _detect_ambiguity_flags(
    parties: List[str],
    amounts: List[Dict[str, str]],
    payment_method: str,
    transaction_type: str,
    text: str,
) -> List[str]:
    """Detect ambiguity flags based on what's missing."""
    flags: List[str] = []
    if payment_method == "UNKNOWN":
        flags.append("MISSING_PAYMENT_MODE")
    if not amounts:
        flags.append("MISSING_AMOUNT")
    if not parties:
        flags.append("MISSING_PARTY")
    if transaction_type == "UNKNOWN":
        flags.append("MULTIPLE_INTERPRETATIONS")
    # Check for pronouns / references
    lower = text.lower()
    pronouns = ["he", "she", "it", "they", "his", "her", "its", "their", "them"]
    for p in pronouns:
        if re.search(r"\b" + p + r"\b", lower):
            flags.append("UNRESOLVED_PRONOUN")
            break
    if not flags:
        flags.append("NONE")
    return flags


def _compute_field_confidences(
    transaction_type: str,
    parties: List[str],
    amounts: List[Dict[str, str]],
    payment_method: str,
    references: List[str],
    tx_source: str,
    tx_conf: float,
    pm_source: str,
    pm_conf: float,
    text: str,
) -> List[Dict[str, Any]]:
    """Compute per-field confidence records."""
    confs: List[Dict[str, Any]] = []

    # Transaction type
    confs.append({
        "field_name": "transaction_type",
        "value": transaction_type,
        "confidence": f"{tx_conf:.2f}",
        "grounding": "GROUNDED" if tx_source else "UNRESOLVED",
        "source_text": tx_source,
        "reasoning": f"detected from '{tx_source}'" if tx_source else "no keyword match",
    })

    # Parties
    if parties:
        confs.append({
            "field_name": "parties",
            "value": str(parties),
            "confidence": "0.85",
            "grounding": "GROUNDED",
            "source_text": ", ".join(parties),
            "reasoning": "extracted from party markers",
        })
    else:
        confs.append({
            "field_name": "parties",
            "value": "[]",
            "confidence": "0.05",
            "grounding": "UNRESOLVED",
            "source_text": "",
            "reasoning": "no party found in text",
        })

    # Amounts
    if amounts:
        confs.append({
            "field_name": "amounts",
            "value": str(amounts),
            "confidence": "0.90",
            "grounding": "GROUNDED",
            "source_text": ", ".join(a.get("value", "") for a in amounts),
            "reasoning": "extracted from amount patterns",
        })
    else:
        confs.append({
            "field_name": "amounts",
            "value": "[]",
            "confidence": "0.05",
            "grounding": "UNRESOLVED",
            "source_text": "",
            "reasoning": "no amount found in text",
        })

    # Payment method
    confs.append({
        "field_name": "payment_method",
        "value": payment_method,
        "confidence": f"{pm_conf:.2f}",
        "grounding": "GROUNDED" if pm_source else "UNRESOLVED",
        "source_text": pm_source,
        "reasoning": f"detected from '{pm_source}'" if pm_source else "no payment keyword",
    })

    # References
    if references:
        confs.append({
            "field_name": "references",
            "value": str(references),
            "confidence": "0.80",
            "grounding": "GROUNDED",
            "source_text": ", ".join(references),
            "reasoning": "extracted references",
        })

    return confs


def _determine_scope_flags(
    transaction_type: str,
    text: str,
) -> List[str]:
    """Determine scope flags."""
    flags: List[str] = []
    lower = text.lower()

    # Single vs multi transaction
    compound_markers = ["and also", "separately", "two transactions", "second transaction"]
    is_compound = any(m in lower for m in compound_markers)
    flags.append("MULTI_TRANSACTION" if is_compound else "SINGLE_TRANSACTION")

    # GST
    if "gst" in lower or "cgst" in lower or "sgst" in lower or "igst" in lower:
        flags.append("GST_SPECIFIC")

    # Settlement
    if transaction_type == "SETTLEMENT":
        flags.append("SETTLEMENT_CALCULATION")

    # Return
    if transaction_type in ("RETURN_OUT", "RETURN_IN"):
        flags.append("RETURN_PROCESSING")

    return flags


def _compute_overall_confidence(field_confidences: List[Dict[str, Any]]) -> str:
    """Compute deterministic overall confidence as average of field confidences."""
    if not field_confidences:
        return "0.00"
    values = []
    for fc in field_confidences:
        try:
            values.append(float(fc.get("confidence", "0.0")))
        except (ValueError, TypeError):
            pass
    if not values:
        return "0.00"
    avg = sum(values) / len(values)
    return f"{avg:.2f}"


# ---------------------------------------------------------------------------
# Main Specialist
# ---------------------------------------------------------------------------

class FYJCAISpecialist:
    """Deterministic FYJC accounting language understanding specialist.

    Parses natural-language accounting transactions and produces a validated
    ExpandedInterpretation dict conforming to the Phase 1 18-field contract.

    The specialist is LANGUAGE UNDERSTANDING only.
    The deterministic kernel handles all accounting truth.
    """

    def __init__(self) -> None:
        pass

    def parse(self, text: str) -> Dict[str, Any]:
        """Parse natural-language accounting text into the expanded contract.

        Args:
            text: Raw student transaction description.

        Returns:
            Dict conforming to ExpandedInterpretation (18 fields).
            Ready for schema_verifier validation.
        """
        # Step 1: Extract entities
        parties = _extract_parties(text)
        amounts = _extract_amounts(text)
        payment_method, pm_source, pm_conf = _detect_payment_method(text)
        transaction_type, tx_source, tx_conf = _detect_transaction_type(text)

        # Step 2: Detect references (look for "ref", "previous", "that" patterns)
        references: List[str] = []
        ref_patterns = [
            re.compile(r"ref[:\s]+(\S+)", re.IGNORECASE),
            re.compile(r"reference[:\s]+(\S+)", re.IGNORECASE),
        ]
        for rp in ref_patterns:
            for m in rp.finditer(text):
                references.append(m.group(1))

        # Step 3: Detect ambiguity
        ambiguity_flags = _detect_ambiguity_flags(
            parties, amounts, payment_method, transaction_type, text,
        )

        # Step 4: Compute field confidences
        field_confidences = _compute_field_confidences(
            transaction_type, parties, amounts, payment_method, references,
            tx_source, tx_conf, pm_source, pm_conf, text,
        )

        # Step 5: Determine scope
        scope_flags = _determine_scope_flags(transaction_type, text)

        # Step 6: Compute overall confidence
        overall_confidence = _compute_overall_confidence(field_confidences)

        # Step 7: Determine safety flags
        safety_flags: List[str] = []
        has_unresolved = any(
            fc.get("grounding") == "UNRESOLVED"
            for fc in field_confidences
        )
        if has_unresolved:
            safety_flags.append("UNRESOLVED_FIELDS")
        if float(overall_confidence) < 0.50:
            safety_flags.append("LOW_CONFIDENCE")
        if not parties and not amounts:
            safety_flags.append("MISSING_REQUIRED_FIELDS")
        if not safety_flags:
            safety_flags.append("NONE")

        # Step 8: Determine suggested status
        # AI must never claim VERIFIED
        if "NONE" in ambiguity_flags and float(overall_confidence) >= 0.70:
            suggested_status = "REVIEW_REQUIRED"
        else:
            suggested_status = "REVIEW_REQUIRED"

        # Step 9: Build legacy fields for backward compatibility
        # Legacy amounts format: list of dicts
        legacy_amounts = [
            {"value": a["value"], "currency": a.get("currency", "INR")}
            for a in amounts
        ]

        # Legacy ambiguities: human-readable strings
        legacy_ambiguities = []
        for flag in ambiguity_flags:
            if flag != "NONE":
                legacy_ambiguities.append(flag.replace("_", " ").lower())

        # Legacy grounding
        all_grounded = all(
            fc.get("grounding") == "GROUNDED" for fc in field_confidences
        )
        inferred_fields = [
            fc["field_name"] for fc in field_confidences
            if fc.get("grounding") == "INFERRED"
        ]

        # Step 10: Build the expanded interpretation dict
        result: Dict[str, Any] = {
            # Legacy 7 fields
            "transaction_type": transaction_type.lower() if transaction_type != "UNKNOWN" else "",
            "parties": parties,
            "amounts": legacy_amounts,
            "payment_method": payment_method.lower() if payment_method != "UNKNOWN" else "",
            "references": references,
            "ambiguities": legacy_ambiguities,
            "grounding": {
                "all_fields_explicitly_grounded": all_grounded,
                "inferred_fields": inferred_fields,
            },
            # Expanded 11 fields
            "transaction_type_enum": transaction_type,
            "payment_method_enum": payment_method,
            "ambiguity_flags": ambiguity_flags,
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": field_confidences,
            "overall_confidence": overall_confidence,
            "suggested_status": suggested_status,
            "safety_flags": safety_flags,
            "scope_flags": scope_flags,
        }

        return result


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def parse_accounting_text(text: str) -> Dict[str, Any]:
    """Parse accounting text into the expanded 18-field contract.

    Convenience wrapper around FYJCAISpecialist.parse().
    """
    specialist = FYJCAISpecialist()
    return specialist.parse(text)
