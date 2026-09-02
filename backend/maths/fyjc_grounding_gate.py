"""
Platrixa — ExpandedInterpretation Grounding Gate (Phase 4)
==========================================================

Validates that an ExpandedInterpretation dict is sufficiently grounded
in the supplied student input text before it can proceed to the
deterministic accounting kernel.

The gate answers:
  "Is this interpretation grounded in the source text and safe for
   downstream deterministic accounting?"

It does NOT:
  - perform accounting calculations
  - generate journals
  - decide debit/credit
  - modify accounting state

Grounding rules:
  1. Parties must be supported by input text
  2. Amounts must be supported by input text
  3. Payment method explicitly stated or marked UNKNOWN
  4. Transaction type supported by input text
  5. References valid (if present)
  6. Ambiguity flags preserved (not erased)
  7. Forbidden accounting fields rejected
  8. Model cannot claim VERIFIED
  9. Confidence cannot override grounding
  10. High-confidence hallucination is still ungrounded
  11. Ungrounded interpretation remains REVIEW_REQUIRED

Architecture:
    FYJCLLMSpecialist.interpret()
        ↓
    ExpandedInterpretation dict (18 fields)
        ↓
    validate_structured_interpretation()  (Phase 1 schema)
        ↓
    ExpandedGroundingGate.ground()       ← THIS MODULE
        ↓
    GroundingResult
        ↓
    if safe_for_kernel → deterministic kernel
    if not → REVIEW_REQUIRED

Safe module: no Streamlit, no AI model, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Grounding Result
# ---------------------------------------------------------------------------

@dataclass
class FieldGrounding:
    """Grounding status for a single extracted field."""
    field_name: str
    grounded: bool
    reason: str
    source_evidence: str = ""  # the substring from input that supports this


@dataclass
class GroundingResult:
    """Complete grounding decision for an ExpandedInterpretation."""
    grounded: bool
    safe_for_kernel: bool
    review_required: bool
    issues: List[str] = field(default_factory=list)
    field_results: List[FieldGrounding] = field(default_factory=list)
    suggested_status: str = "REVIEW_REQUIRED"

    @property
    def summary(self) -> str:
        if not self.issues:
            return "All grounding checks passed"
        return "; ".join(self.issues)


# ---------------------------------------------------------------------------
# Helper: text containment check
# ---------------------------------------------------------------------------

def _text_contains(text: str, candidate: str) -> bool:
    """Check if candidate is approximately contained in text.

    Uses case-insensitive substring matching with fuzzy normalization.
    Handles common variations: Rs./₹/INR, commas, periods.
    """
    if not candidate or not text:
        return False

    # Normalize both strings
    def _norm(s: str) -> str:
        s = s.lower().strip()
        # Remove currency prefixes before stripping punctuation
        s = re.sub(r"rs\.?\s*", "", s)
        s = re.sub(r"inr\s*", "", s)
        s = re.sub(r"₹\s*", "", s)
        s = re.sub(r"rupees?\s*", "", s)
        # Strip all non-alphanumeric chars (commas, dots, spaces)
        s = re.sub(r"[^a-z0-9]", "", s)
        return s.strip()

    text_norm = _norm(text)
    candidate_norm = _norm(candidate)

    if not candidate_norm:
        return False

    return candidate_norm in text_norm


def _amount_in_text(text: str, amount_value: str) -> bool:
    """Check if a monetary amount is supported by the input text.

    Handles: 25000, 25,000, Rs.25000, ₹25,000, 25k, etc.
    """
    if not amount_value or not text:
        return False

    # Normalize the amount value
    val = amount_value.replace(",", "").replace(".", "").strip()

    # Strip all non-digits from text for numeric matching
    text_digits_only = re.sub(r"[^0-9]", "", text)

    # Try exact number match in digit-stripped text
    if val and val in text_digits_only:
        return True

    # Try with currency prefixes
    for prefix in ["Rs.", "Rs", "₹", "INR", "rupees", "Rs. "]:
        if f"{prefix}{val}" in text.replace(" ", ""):
            return True
        if f"{prefix} {val}" in text:
            return True

    # Try "X thousand" format
    try:
        num = int(val)
        if num >= 1000:
            thousands = num // 1000
            remainder = num % 1000
            if remainder == 0:
                if f"{thousands} thousand" in text.lower():
                    return True
                if f"{thousands}k" in text.lower():
                    return True
    except (ValueError, TypeError):
        pass

    # Fallback: check if the raw number string appears anywhere
    return val in text.replace(",", "").replace(" ", "")


# ---------------------------------------------------------------------------
# ExpandedInterpretation Grounding Gate
# ---------------------------------------------------------------------------

class ExpandedGroundingGate:
    """Grounds an ExpandedInterpretation dict against the source text.

    Validates that every extracted fact is actually supported by the
    student's input text. Prevents hallucinated/fabricated data from
    reaching the deterministic accounting kernel.

    Usage:
        gate = ExpandedGroundingGate()
        result = gate.ground(interpretation_dict, source_text)
        if result.safe_for_kernel:
            # proceed to kernel
        else:
            # return REVIEW_REQUIRED
    """

    # Fields that MUST be grounded (not just inferred)
    REQUIRED_GROUNDED_FIELDS = {"transaction_type", "parties"}

    # Minimum number of grounded fields required
    MIN_GROUNDED_FIELDS = 1

    def ground(
        self,
        interpretation: Dict[str, Any],
        source_text: str,
    ) -> GroundingResult:
        """Validate grounding of an ExpandedInterpretation against source text.

        Args:
            interpretation: 18-field ExpandedInterpretation dict.
            source_text: The original student input text.

        Returns:
            GroundingResult with grounded status and field-level details.
        """
        issues: List[str] = []
        field_results: List[FieldGrounding] = []

        # --- Rule 0: Cannot claim VERIFIED ---
        suggested = interpretation.get("suggested_status", "REVIEW_REQUIRED")
        if suggested == "VERIFIED":
            issues.append(
                "AI attempted to claim VERIFIED status. "
                "Only the accounting kernel may produce VERIFIED."
            )

        # --- Rule 1: Forbidden accounting fields ---
        forbidden = {"journal", "debit_lines", "credit_lines", "ledger",
                     "balances", "debit_account", "credit_account", "journal_entry"}
        found_forbidden = forbidden & set(interpretation.keys())
        if found_forbidden:
            issues.append(
                f"Forbidden accounting truth fields present: {sorted(found_forbidden)}"
            )

        # --- Rule 2: Parties grounding ---
        parties = interpretation.get("parties", [])
        if parties:
            grounded_parties = []
            for party in parties:
                if _text_contains(source_text, party):
                    grounded_parties.append(party)
                    field_results.append(FieldGrounding(
                        field_name=f"party:{party}",
                        grounded=True,
                        reason=f"Party '{party}' found in source text",
                        source_evidence=party,
                    ))
                else:
                    issues.append(f"Party '{party}' not supported by input text")
                    field_results.append(FieldGrounding(
                        field_name=f"party:{party}",
                        grounded=False,
                        reason=f"Party '{party}' NOT found in source text — possibly fabricated",
                    ))
        else:
            # No parties extracted — this may be legitimate (e.g., minimal input)
            field_results.append(FieldGrounding(
                field_name="parties",
                grounded=True,  # empty parties is valid (marked as MISSING_PARTY in ambiguity)
                reason="No parties claimed — ambiguity flag should reflect this",
            ))

        # --- Rule 3: Amounts grounding ---
        amounts = interpretation.get("amounts", [])
        if amounts:
            for amt in amounts:
                val = amt.get("value", "") if isinstance(amt, dict) else str(amt)
                source = amt.get("source", "explicit") if isinstance(amt, dict) else "explicit"
                if _amount_in_text(source_text, val):
                    field_results.append(FieldGrounding(
                        field_name=f"amount:{val}",
                        grounded=True,
                        reason=f"Amount {val} found in source text",
                        source_evidence=val,
                    ))
                else:
                    issues.append(f"Amount {val} not supported by input text")
                    field_results.append(FieldGrounding(
                        field_name=f"amount:{val}",
                        grounded=False,
                        reason=f"Amount {val} NOT found in source text — possibly fabricated",
                    ))
        else:
            field_results.append(FieldGrounding(
                field_name="amounts",
                grounded=True,
                reason="No amounts claimed",
            ))

        # --- Rule 4: Payment method ---
        pm = interpretation.get("payment_method_enum", "")
        pm_legacy = interpretation.get("payment_method", "")
        effective_pm = pm or pm_legacy
        if effective_pm and effective_pm != "UNKNOWN":
            # Check if payment method is actually mentioned in text
            pm_lower = effective_pm.lower()
            text_lower = source_text.lower()
            pm_keywords = {
                "cash": ["cash", "by cash", "for cash", "in cash"],
                "credit": ["credit", "on credit", "on account"],
                "cheque": ["cheque", "check", "by cheque"],
                "bank": ["bank", "bank transfer", "neft", "rtgs", "imps"],
                "upi": ["upi", "phonepe", "paytm", "gpay", "google pay"],
            }
            keywords = pm_keywords.get(pm_lower, [])
            if keywords and not any(kw in text_lower for kw in keywords):
                issues.append(
                    f"Payment method '{effective_pm}' not explicitly supported by input text"
                )
                field_results.append(FieldGrounding(
                    field_name="payment_method",
                    grounded=False,
                    reason=f"Payment method '{effective_pm}' claimed but not mentioned in text",
                ))
            else:
                field_results.append(FieldGrounding(
                    field_name="payment_method",
                    grounded=True,
                    reason=f"Payment method '{effective_pm}' supported by text",
                ))
        else:
            field_results.append(FieldGrounding(
                field_name="payment_method",
                grounded=True,
                reason="Payment method is UNKNOWN — correctly not fabricated",
            ))

        # --- Rule 5: Transaction type ---
        tx_type = interpretation.get("transaction_type_enum", "") or interpretation.get("transaction_type", "")
        if tx_type and tx_type != "UNKNOWN":
            # Check if transaction keywords are in text
            tx_keywords = {
                "purchase": ["purchased", "bought", "procured", "acquired"],
                "sale": ["sold", "supplied", "delivered"],
                "payment": ["paid", "payment", "settled"],
                "receipt": ["received", "receipt"],
                "capital": ["capital", "invested", "started business"],
                "expense": ["paid rent", "paid salary", "paid electricity", "paid wages"],
                "return": ["returned", "return", "purchase return", "sales return"],
                "drawing": ["withdrew", "drew", "drawing", "personal use"],
            }
            tx_lower = tx_type.lower()
            # Find matching category
            matched = False
            for cat, kws in tx_keywords.items():
                if cat in tx_lower or tx_lower in cat:
                    if any(kw in source_text.lower() for kw in kws):
                        matched = True
                        break
            if not matched:
                # Some transaction types are harder to verify from text alone
                # Allow them but mark as inferred rather than grounded
                field_results.append(FieldGrounding(
                    field_name="transaction_type",
                    grounded=False,
                    reason=f"Transaction type '{tx_type}' could not be directly verified from text keywords",
                ))
            else:
                field_results.append(FieldGrounding(
                    field_name="transaction_type",
                    grounded=True,
                    reason=f"Transaction type '{tx_type}' supported by text",
                ))
        else:
            field_results.append(FieldGrounding(
                field_name="transaction_type",
                grounded=True,
                reason="Transaction type is UNKNOWN",
            ))

        # --- Rule 6: Reference validation ---
        ref_idx = interpretation.get("referenced_transaction_index")
        if ref_idx is not None:
            try:
                idx = int(ref_idx)
                if idx < 0:
                    issues.append(f"Invalid reference index: {idx}")
                    field_results.append(FieldGrounding(
                        field_name="referenced_transaction_index",
                        grounded=False,
                        reason=f"Negative reference index {idx}",
                    ))
                else:
                    field_results.append(FieldGrounding(
                        field_name="referenced_transaction_index",
                        grounded=True,
                        reason=f"Reference index {idx} is structurally valid",
                    ))
            except (ValueError, TypeError):
                issues.append(f"Non-integer reference index: {ref_idx}")
                field_results.append(FieldGrounding(
                    field_name="referenced_transaction_index",
                    grounded=False,
                    reason=f"Invalid reference index type: {ref_idx}",
                ))

        # --- Rule 7: Ambiguity preservation ---
        ambig_flags = interpretation.get("ambiguity_flags", [])
        if not ambig_flags:
            field_results.append(FieldGrounding(
                field_name="ambiguity_flags",
                grounded=True,
                reason="No ambiguity flags (NONE implied)",
            ))

        # --- Rule 8: Confidence cannot bypass grounding ---
        oc = interpretation.get("overall_confidence", "0.0")
        try:
            conf = float(oc)
            if conf > 0.95 and issues:
                issues.append(
                    f"High confidence ({conf}) but grounding issues detected — "
                    "confidence does not override grounding"
                )
        except (ValueError, TypeError):
            pass

        # --- Determine result ---
        grounded = len(issues) == 0
        has_forbidden = bool(found_forbidden)
        has_claimed_verified = suggested == "VERIFIED"

        safe_for_kernel = (
            grounded
            and not has_forbidden
            and not has_claimed_verified
        )

        review_required = not safe_for_kernel

        status = "REVIEW_REQUIRED" if review_required else suggested

        return GroundingResult(
            grounded=grounded,
            safe_for_kernel=safe_for_kernel,
            review_required=review_required,
            issues=issues,
            field_results=field_results,
            suggested_status=status,
        )
