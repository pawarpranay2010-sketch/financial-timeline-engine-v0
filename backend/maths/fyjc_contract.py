"""
Platrixa — Expanded StructuredInterpretation Contract (Phase 1)
================================================================

Defines the canonical 18-field ExpandedInterpretation schema that unifies:
  - The legacy 7-field StructuredInterpretation (fyjc_p4_2_dataset_quality.py)
  - The richer AIInterpretation adapter (fyjc_ai_adapter.py)
  - The audit-proposed expansion (safety flags, scope, structured grounding)

LEGACY CONTRACT (7 fields — PRESERVED, never removed):
  1.  transaction_type: str
  2.  parties: List[str]
  3.  amounts: List[Dict[str, str]]
  4.  payment_method: str
  5.  references: List[str]
  6.  ambiguities: List[str]
  7.  grounding: Dict[str, Any]

EXPANDED CONTRACT (18 fields — NEW, backward compatible):
  1–7.   Legacy fields (exact same keys, same types, same semantics)
  8.     transaction_type_enum: str (validated enum value)
  9.     payment_method_enum: str (validated enum value)
  10.    ambiguity_flags: List[str] (structured ambiguity classification)
  11.    referenced_transaction_index: Optional[int]
  12.    referenced_party: Optional[str]
  13.    referenced_amount: Optional[str]
  14.    field_confidences: List[Dict] (per-field confidence + grounding)
  15.    overall_confidence: str (0.0–1.0, computed if missing)
  16.    suggested_status: str (AI suggestion, overridden by grounding gate)
  17.    safety_flags: List[str] (grounding gate safety checks)
  18.    scope_flags: List[str] (problem classification tags)

SAFE:
  - Pure Python dataclasses + enums. No Streamlit, no model, no network.
  - All validation is deterministic.
  - Never auto-repairs malformed input.
  - Never fabricates financial facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransactionTypeEnum(str, Enum):
    """Valid transaction type classification values."""
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    PAYMENT = "PAYMENT"
    RECEIPT = "RECEIPT"
    CAPITAL = "CAPITAL"
    EXPENSE = "EXPENSE"
    RETURN_OUT = "RETURN_OUT"
    RETURN_IN = "RETURN_IN"
    DISCOUNT_TRADE = "DISCOUNT_TRADE"
    DISCOUNT_CASH = "DISCOUNT_CASH"
    SETTLEMENT = "SETTLEMENT"
    GST = "GST"
    DRAWING = "DRAWING"
    DEPRECIATION = "DEPRECIATION"
    UNKNOWN = "UNKNOWN"


class PaymentMethodEnum(str, Enum):
    """Valid payment method classification values."""
    CASH = "CASH"
    BANK = "BANK"
    CHEQUE = "CHEQUE"
    NEFT = "NEFT"
    UPI = "UPI"
    CREDIT = "CREDIT"
    UNKNOWN = "UNKNOWN"


class AmbiguityTypeEnum(str, Enum):
    """Structured ambiguity classification."""
    MISSING_PAYMENT_MODE = "MISSING_PAYMENT_MODE"
    MISSING_AMOUNT = "MISSING_AMOUNT"
    MISSING_PARTY = "MISSING_PARTY"
    AMBIGUOUS_REFERENCE = "AMBIGUOUS_REFERENCE"
    MULTIPLE_INTERPRETATIONS = "MULTIPLE_INTERPRETATIONS"
    CONFLICTING_INFORMATION = "CONFLICTING_INFORMATION"
    UNRESOLVED_PRONOUN = "UNRESOLVED_PRONOUN"
    HISTORICAL_DEPENDENCY = "HISTORICAL_DEPENDENCY"
    NONE = "NONE"


class GroundingLevel(str, Enum):
    """Field-level grounding status."""
    GROUNDED = "GROUNDED"
    INFERRED = "INFERRED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTING = "CONFLICTING"


class SafetyFlag(str, Enum):
    """Grounding gate safety check flags."""
    AI_CLAIMED_VERIFIED = "AI_CLAIMED_VERIFIED"
    JOURNAL_ENTRIES_PRODUCED = "JOURNAL_ENTRIES_PRODUCED"
    LEDGER_BALANCES_PRODUCED = "LEDGER_BALANCES_PRODUCED"
    MISSING_REQUIRED_FIELDS = "MISSING_REQUIRED_FIELDS"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNRESOLVED_FIELDS = "UNRESOLVED_FIELDS"
    AMBIGUITY_DETECTED = "AMBIGUITY_DETECTED"
    EMPTY_PARTIES = "EMPTY_PARTIES"
    NONE = "NONE"


class ScopeFlag(str, Enum):
    """Problem classification tags."""
    SINGLE_TRANSACTION = "SINGLE_TRANSACTION"
    MULTI_TRANSACTION = "MULTI_TRANSACTION"
    SINGLE_AUTHORITY = "SINGLE_AUTHORITY"
    MULTI_AUTHORITY = "MULTI_AUTHORITY"
    GST_SPECIFIC = "GST_SPECIFIC"
    SETTLEMENT_CALCULATION = "SETTLEMENT_CALCULATION"
    RETURN_PROCESSING = "RETURN_PROCESSING"
    DISCOUNT_APPLICATION = "DISCOUNT_APPLICATION"
    EDGE_CASE = "EDGE_CASE"
    ADVERSARIAL = "ADVERSARIAL"


# ---------------------------------------------------------------------------
# Valid values sets (for validation)
# ---------------------------------------------------------------------------

VALID_TRANSACTION_TYPES: Set[str] = {e.value for e in TransactionTypeEnum}
VALID_PAYMENT_METHODS: Set[str] = {e.value for e in PaymentMethodEnum}
VALID_AMBIGUITY_TYPES: Set[str] = {e.value for e in AmbiguityTypeEnum}
VALID_GROUNDING_LEVELS: Set[str] = {e.value for e in GroundingLevel}
VALID_SAFETY_FLAGS: Set[str] = {e.value for e in SafetyFlag}
VALID_SCOPE_FLAGS: Set[str] = {e.value for e in ScopeFlag}

# Legacy field names (the canonical 7)
LEGACY_FIELDS: Set[str] = {
    "transaction_type", "parties", "amounts", "payment_method",
    "references", "ambiguities", "grounding",
}

# Expanded-only field names (fields 8–18)
EXPANDED_FIELDS: Set[str] = {
    "transaction_type_enum", "payment_method_enum", "ambiguity_flags",
    "referenced_transaction_index", "referenced_party", "referenced_amount",
    "field_confidences", "overall_confidence", "suggested_status",
    "safety_flags", "scope_flags",
}

# All valid field names for the expanded contract
ALL_VALID_FIELDS: Set[str] = LEGACY_FIELDS | EXPANDED_FIELDS


# ---------------------------------------------------------------------------
# Legacy → Enum mapping
# ---------------------------------------------------------------------------

_LEGACY_TX_MAP: Dict[str, str] = {
    "purchase": "PURCHASE", "sale": "SALE", "payment": "PAYMENT",
    "receipt": "RECEIPT", "capital_introduction": "CAPITAL",
    "capital": "CAPITAL", "expense": "EXPENSE",
    "return": "RETURN_OUT", "return_out": "RETURN_OUT",
    "return_in": "RETURN_IN",
    "discount_trade": "DISCOUNT_TRADE", "discount_cash": "DISCOUNT_CASH",
    "settlement": "SETTLEMENT", "gst": "GST",
    "drawing": "DRAWING", "depreciation": "DEPRECIATION",
}

_LEGACY_PM_MAP: Dict[str, str] = {
    "cash": "CASH", "bank": "BANK", "bank_transfer": "BANK",
    "cheque": "CHEQUE", "neft": "NEFT", "rtgs": "NEFT",
    "imps": "NEFT", "upi": "UPI",
    "credit": "CREDIT", "on_credit": "CREDIT",
}

_LEGACY_AMBIG_MAP: Dict[str, str] = {
    "missing_payment_mode": "MISSING_PAYMENT_MODE",
    "missing amount": "MISSING_AMOUNT",
    "missing party": "MISSING_PARTY",
    "ambiguous reference": "AMBIGUOUS_REFERENCE",
    "multiple interpretations": "MULTIPLE_INTERPRETATIONS",
    "conflicting information": "CONFLICTING_INFORMATION",
    "unresolved pronoun": "UNRESOLVED_PRONOUN",
    "historical dependency": "HISTORICAL_DEPENDENCY",
}


# ---------------------------------------------------------------------------
# ExpandedInterpretation — the unified 18-field contract
# ---------------------------------------------------------------------------

@dataclass
class FieldConfidenceRecord:
    """Per-field confidence and grounding information."""
    field_name: str
    value: Any
    confidence: str = "0.0"
    grounding: str = "UNRESOLVED"
    source_text: str = ""
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": str(self.value) if self.value is not None else None,
            "confidence": self.confidence,
            "grounding": self.grounding,
            "source_text": self.source_text,
            "reasoning": self.reasoning,
        }


@dataclass
class ExpandedInterpretation:
    """The canonical 18-field expanded StructuredInterpretation contract.

    Fields 1–7 are the exact legacy contract. Fields 8–18 are new.
    Legacy records must pass validation without any new fields present.
    Expanded records must pass validation with all fields.

    The AI produces this output. The grounding gate verifies it.
    The deterministic kernel remains the source of accounting truth.
    """

    # --- Legacy fields (1–7): exact same keys and types as StructuredInterpretation ---
    transaction_type: str = ""
    parties: List[str] = field(default_factory=list)
    amounts: List[Dict[str, str]] = field(default_factory=list)
    payment_method: str = ""
    references: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    grounding: Dict[str, Any] = field(default_factory=lambda: {
        "all_fields_explicitly_grounded": True,
        "inferred_fields": [],
    })

    # --- Expanded fields (8–18) ---
    transaction_type_enum: str = "UNKNOWN"
    payment_method_enum: str = "UNKNOWN"
    ambiguity_flags: List[str] = field(default_factory=list)
    referenced_transaction_index: Optional[int] = None
    referenced_party: Optional[str] = None
    referenced_amount: Optional[str] = None
    field_confidences: List[Dict[str, Any]] = field(default_factory=list)
    overall_confidence: str = "0.0"
    suggested_status: str = "REVIEW_REQUIRED"
    safety_flags: List[str] = field(default_factory=list)
    scope_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Full 18-field serialization."""
        result: Dict[str, Any] = {
            # Legacy 7
            "transaction_type": self.transaction_type,
            "parties": list(self.parties),
            "amounts": [dict(a) for a in self.amounts],
            "payment_method": self.payment_method,
            "references": list(self.references),
            "ambiguities": list(self.ambiguities),
            "grounding": dict(self.grounding),
            # Expanded 11
            "transaction_type_enum": self.transaction_type_enum,
            "payment_method_enum": self.payment_method_enum,
            "ambiguity_flags": list(self.ambiguity_flags),
            "referenced_transaction_index": self.referenced_transaction_index,
            "referenced_party": self.referenced_party,
            "referenced_amount": self.referenced_amount,
            "field_confidences": [fc if isinstance(fc, dict) else fc.to_dict()
                                  for fc in self.field_confidences],
            "overall_confidence": self.overall_confidence,
            "suggested_status": self.suggested_status,
            "safety_flags": list(self.safety_flags),
            "scope_flags": list(self.scope_flags),
        }
        return result

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Serialize as the legacy 7-field format for backward compatibility."""
        return {
            "transaction_type": self.transaction_type,
            "parties": list(self.parties),
            "amounts": [dict(a) for a in self.amounts],
            "payment_method": self.payment_method,
            "references": list(self.references),
            "ambiguities": list(self.ambiguities),
            "grounding": dict(self.grounding),
        }

    def to_json_string(self) -> str:
        """JSON serialization of the full expanded contract."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Contract classification
# ---------------------------------------------------------------------------

def classify_record(record: Dict[str, Any]) -> str:
    """Classify a parsed JSON record as LEGACY, EXPANDED, or INVALID.

    Returns:
        "LEGACY"  — exactly the 7 legacy fields (no expanded fields)
        "EXPANDED" — has at least some expanded fields beyond the 7
        "INVALID"  — missing required fields, wrong types, etc.
    """
    if not isinstance(record, dict):
        return "INVALID"

    keys = set(record.keys())
    has_legacy = LEGACY_FIELDS.issubset(keys)
    has_expanded = bool(keys & EXPANDED_FIELDS)
    has_unknown = bool(keys - ALL_VALID_FIELDS)

    if has_unknown:
        return "INVALID"
    if not has_legacy:
        return "INVALID"
    if has_expanded:
        return "EXPANDED"
    return "LEGACY"


# ---------------------------------------------------------------------------
# Legacy → Expanded normalization
# ---------------------------------------------------------------------------

def _map_legacy_tx(raw: str) -> str:
    """Map a legacy string transaction type to the enum value."""
    if not raw:
        return "UNKNOWN"
    normalised = raw.strip().lower().replace(" ", "_")
    return _LEGACY_TX_MAP.get(normalised, "UNKNOWN")


def _map_legacy_pm(raw: str) -> str:
    """Map a legacy string payment method to the enum value."""
    if not raw:
        return "UNKNOWN"
    normalised = raw.strip().lower().replace(" ", "_")
    return _LEGACY_PM_MAP.get(normalised, "UNKNOWN")


def _map_legacy_ambiguity(raw: str) -> str:
    """Map a legacy ambiguity string to the structured enum value."""
    if not raw:
        return "NONE"
    normalised = raw.strip().lower()
    return _LEGACY_AMBIG_MAP.get(normalised, "NONE")


def normalize_legacy_to_expanded(record: Dict[str, Any]) -> ExpandedInterpretation:
    """Convert a legacy 7-field record to an ExpandedInterpretation.

    Fills expanded fields with deterministic defaults. Never invents
    financial facts. The transaction_type_enum is derived from the
    legacy transaction_type string via a fixed mapping table.
    """
    # Validate it at least has the legacy fields
    missing = LEGACY_FIELDS - set(record.keys())
    if missing:
        raise ValueError(f"Cannot normalize: missing legacy fields: {sorted(missing)}")

    # Map transaction type
    tx_str = record.get("transaction_type", "")
    tx_enum = _map_legacy_tx(tx_str)

    # Map payment method
    pm_str = record.get("payment_method", "")
    pm_enum = _map_legacy_pm(pm_str)

    # Map ambiguity flags
    legacy_ambigs = record.get("ambiguities", [])
    if isinstance(legacy_ambigs, list):
        ambiguity_flags = [
            _map_legacy_ambiguity(a) for a in legacy_ambigs
            if isinstance(a, str) and a.strip()
        ]
    else:
        ambiguity_flags = []

    # Determine scope
    scope_flags = ["SINGLE_TRANSACTION"]  # default for legacy

    # Compute confidence from grounding
    grounding = record.get("grounding", {})
    if isinstance(grounding, dict):
        all_grounded = grounding.get("all_fields_explicitly_grounded", True)
        inferred = grounding.get("inferred_fields", [])
        if all_grounded and not inferred:
            overall_conf = "0.90"
        elif inferred:
            overall_conf = "0.70"
        else:
            overall_conf = "0.50"
    else:
        overall_conf = "0.50"

    # Build minimal field confidences from grounding info
    field_confidences = []
    parties = record.get("parties", [])
    amounts = record.get("amounts", [])
    if parties:
        field_confidences.append({
            "field_name": "parties",
            "value": str(parties),
            "confidence": "0.90",
            "grounding": "GROUNDED" if "parties" not in (grounding.get("inferred_fields", []) if isinstance(grounding, dict) else []) else "INFERRED",
            "source_text": "",
            "reasoning": "from legacy grounding",
        })
    if amounts:
        field_confidences.append({
            "field_name": "amounts",
            "value": str(amounts),
            "confidence": "0.90",
            "grounding": "GROUNDED",
            "source_text": "",
            "reasoning": "from legacy grounding",
        })

    return ExpandedInterpretation(
        # Legacy 7
        transaction_type=tx_str,
        parties=list(record.get("parties", [])),
        amounts=list(record.get("amounts", [])),
        payment_method=pm_str,
        references=list(record.get("references", [])),
        ambiguities=list(record.get("ambiguities", [])),
        grounding=dict(grounding) if isinstance(grounding, dict) else {},
        # Expanded 11
        transaction_type_enum=tx_enum,
        payment_method_enum=pm_enum,
        ambiguity_flags=ambiguity_flags,
        referenced_transaction_index=record.get("referenced_transaction_index"),
        referenced_party=record.get("referenced_party"),
        referenced_amount=record.get("referenced_amount"),
        field_confidences=field_confidences,
        overall_confidence=overall_conf,
        suggested_status="REVIEW_REQUIRED",
        safety_flags=["NONE"],
        scope_flags=scope_flags,
    )


def normalize_expanded_to_legacy(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the legacy 7 fields from any record (legacy or expanded)."""
    return {
        "transaction_type": record.get("transaction_type", ""),
        "parties": record.get("parties", []),
        "amounts": record.get("amounts", []),
        "payment_method": record.get("payment_method", ""),
        "references": record.get("references", []),
        "ambiguities": record.get("ambiguities", []),
        "grounding": record.get("grounding", {
            "all_fields_explicitly_grounded": True,
            "inferred_fields": [],
        }),
    }
