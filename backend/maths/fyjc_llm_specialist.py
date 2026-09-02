"""
Platrixa — FYJC Local Model Specialist (Phase 3 — Corrected)
=============================================================

Local Hugging Face model-backed language-understanding component
for FYJC accounting. Zero external API dependency.

Target architecture:
    Student text
        ↓
    FYJCLLMSpecialist.interpret()
        ↓
    LocalModelRunner (Hugging Face Transformers)
        ↓
    Trained/LoRA model artifact
        ↓
    Raw model response (JSON string)
        ↓
    JSON extraction + strict schema validation
        ↓
    ExpandedInterpretation dict (18-field contract)
        ↓
    Grounding gate
        ↓
    Deterministic kernel (UNTOUCHED)

The model handles language understanding.
The kernel determines accounting truth.

Safety rules:
    - AI produces INTERPRETATION only, never accounting truth
    - Invalid model output → MODEL_NOT_AVAILABLE or REVIEW_REQUIRED
    - Malformed JSON → REVIEW_REQUIRED
    - Model cannot claim VERIFIED
    - Model cannot produce journal entries
    - Missing information represented as UNRESOLVED/UNKNOWN, never invented
    - Fail-closed: reject invalid output, never silently repair

NO external API dependency. NO Gemini. NO Groq. NO OpenRouter.
The model is LOCAL Hugging Face only.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_contract import (
    ExpandedInterpretation,
    ALL_VALID_FIELDS,
    VALID_TRANSACTION_TYPES as VALID_TX,
    VALID_PAYMENT_METHODS as VALID_PM,
    VALID_AMBIGUITY_TYPES as VALID_AMB,
    VALID_GROUNDING_LEVELS,
    VALID_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS,
)
from backend.maths.schema_verifier import (
    validate_structured_interpretation,
    ValidationStatus,
)
from backend.maths.fyjc_grounding_gate import (
    ExpandedGroundingGate,
    GroundingResult,
)
from backend.maths.fyjc_local_model_runner import (
    LocalModelRunner,
    MockModelRunner,
    check_transformers_available,
    get_model_config,
)

logger = logging.getLogger(__name__)

# Status constant for model unavailable
MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the Platrixa FYJC accounting language-understanding specialist.

Interpret natural-language accounting input into the required 18-field structured interpretation.

You are NOT the accounting calculation authority.
Do NOT:
- Generate authoritative journal entries
- Choose final debit/credit accounts
- Invent missing facts
- Infer unstated payment methods
- Fabricate parties, amounts, or references
- Claim VERIFIED status

Represent uncertainty explicitly using ambiguity_flags and confidence fields.

Return ONLY a JSON object matching this exact schema. No markdown, no explanation.

{
  "transaction_type": "<string: PURCHASE|SALE|PAYMENT|RECEIPT|CAPITAL|EXPENSE|RETURN_OUT|RETURN_IN|DISCOUNT_TRADE|DISCOUNT_CASH|SETTLEMENT|GST|DRAWING|DEPRECIATION|UNKNOWN>",
  "parties": ["<party name>"],
  "amounts": [{"value": "<number string>", "currency": "INR", "source": "explicit|inferred"}],
  "payment_method": "<string: CASH|BANK|CHEQUE|NEFT|UPI|CREDIT|UNKNOWN>",
  "references": ["<any reference text>"],
  "ambiguities": ["<human-readable ambiguity>"],
  "grounding": {"all_fields_explicitly_grounded": true, "inferred_fields": []},
  "transaction_type_enum": "<same as transaction_type>",
  "payment_method_enum": "<same as payment_method>",
  "ambiguity_flags": ["MISSING_PAYMENT_MODE|MISSING_AMOUNT|MISSING_PARTY|AMBIGUOUS_REFERENCE|MULTIPLE_INTERPRETATIONS|CONFLICTING_INFORMATION|UNRESOLVED_PRONOUN|HISTORICAL_DEPENDENCY|NONE"],
  "referenced_transaction_index": null,
  "referenced_party": null,
  "referenced_amount": null,
  "field_confidences": [
    {"field_name": "transaction_type", "value": "...", "confidence": "0.0-1.0", "grounding": "GROUNDED|INFERRED|UNRESOLVED", "source_text": "...", "reasoning": "..."}
  ],
  "overall_confidence": "0.0-1.0",
  "suggested_status": "REVIEW_REQUIRED",
  "safety_flags": ["NONE"],
  "scope_flags": ["SINGLE_TRANSACTION"]
}

Rules:
- suggested_status MUST ALWAYS be "REVIEW_REQUIRED". Never set VERIFIED.
- If a field is not determinable, leave null/empty with low confidence.
- Do NOT fabricate information not present in the input.
- Return ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# JSON Extraction (robust, fail-closed)
# ---------------------------------------------------------------------------

def _extract_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from model response text.

    Handles:
    - Raw JSON
    - JSON in ```json fences
    - JSON in ``` fences
    - Leading/trailing prose
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Strip markdown code fences
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    fence_match = fence_pattern.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try parsing the whole text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Find first { to last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# Fail-closed validation helpers
# ---------------------------------------------------------------------------

def _validate_strict(parsed: Dict[str, Any], raw_input: str) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Strict fail-closed validation. Returns (valid, errors, enriched_dict).

    Does NOT silently repair. Invalid values are rejected.
    """
    errors = []
    result = dict(parsed)

    # Reject unknown fields
    unknown = set(result.keys()) - ALL_VALID_FIELDS - {"interpretation_model", "raw_input", "_error"}
    if unknown:
        errors.append(f"Unknown fields rejected: {sorted(unknown)}")

    # Validate enums (reject, don't replace)
    tx = result.get("transaction_type_enum", "")
    if tx and tx not in VALID_TX:
        errors.append(f"Invalid transaction_type_enum: '{tx}'")
    pm = result.get("payment_method_enum", "")
    if pm and pm not in VALID_PM:
        errors.append(f"Invalid payment_method_enum: '{pm}'")

    # Validate ambiguity_flags (reject unknown flags)
    valid_amb = set(VALID_AMB)
    bad_flags = [f for f in result.get("ambiguity_flags", []) if f not in valid_amb]
    if bad_flags:
        errors.append(f"Invalid ambiguity_flags: {bad_flags}")

    # Validate safety_flags
    valid_safety = set(VALID_SAFETY_FLAGS)
    bad_safety = [f for f in result.get("safety_flags", []) if f not in valid_safety]
    if bad_safety:
        errors.append(f"Invalid safety_flags: {bad_safety}")

    # Validate scope_flags
    valid_scope = set(VALID_SCOPE_FLAGS)
    bad_scope = [f for f in result.get("scope_flags", []) if f not in valid_scope]
    if bad_scope:
        errors.append(f"Invalid scope_flags: {bad_scope}")

    # Validate overall_confidence (reject out of range)
    oc = result.get("overall_confidence", "")
    if oc:
        try:
            c = float(oc)
            if c < 0.0 or c > 1.0:
                errors.append(f"overall_confidence out of range: {oc} (must be 0.0-1.0)")
        except (ValueError, TypeError):
            errors.append(f"overall_confidence not numeric: '{oc}'")

    # Validate field_confidences
    for fc in result.get("field_confidences", []):
        if not isinstance(fc, dict) or "field_name" not in fc:
            errors.append(f"Invalid field_confidence entry: missing field_name")
            continue
        try:
            c = float(fc.get("confidence", "0.0"))
            if c < 0.0 or c > 1.0:
                errors.append(f"field_confidence out of range for {fc['field_name']}: {c}")
        except (ValueError, TypeError):
            errors.append(f"field_confidence not numeric for {fc['field_name']}")
        if fc.get("grounding") not in VALID_GROUNDING_LEVELS:
            errors.append(f"Invalid grounding level for {fc['field_name']}: '{fc.get('grounding')}'")

    # Reject forbidden accounting truth fields
    forbidden = {"journal", "debit_lines", "credit_lines", "ledger",
                 "balances", "debit_account", "credit_account", "journal_entry"}
    found_forbidden = forbidden & set(result.keys())
    if found_forbidden:
        errors.append(f"Forbidden accounting truth fields present: {sorted(found_forbidden)}")

    return (len(errors) == 0, errors, result if not errors else None)


# ---------------------------------------------------------------------------
# FYJCLLMSpecialist — Local Model Specialist
# ---------------------------------------------------------------------------

class FYJCLLMSpecialist:
    """Local Hugging Face model specialist for FYJC accounting.

    Uses LocalModelRunner for local inference. No external API dependency.

    Usage:
        specialist = FYJCLLMSpecialist()
        result = specialist.interpret("Purchased furniture from Amit for Rs.25000")
        # result is a dict matching the 18-field ExpandedInterpretation contract

    If no model is available, returns MODEL_NOT_AVAILABLE/REVIEW_REQUIRED.
    Never falls back to external APIs or regex parsing.
    """

    def __init__(
        self,
        model_runner: Optional[Any] = None,
    ):
        """Initialize specialist.

        Args:
            model_runner: Optional model runner instance.
                If None, uses the LocalModelRunner singleton.
                For testing, pass a MockModelRunner.
        """
        self._runner = model_runner

    @property
    def _model_runner(self):
        """Get model runner (lazy)."""
        if self._runner is None:
            self._runner = LocalModelRunner()
        return self._runner

    @property
    def model_name(self) -> str:
        status = self._model_runner.status()
        return status.get("model_id", "unknown")

    @property
    def model_version(self) -> str:
        return "3.1.0-phase3-local"

    def interpret(self, text: str) -> Dict[str, Any]:
        """Interpret student text using the local model.

        Args:
            text: Raw student transaction text.

        Returns:
            18-field ExpandedInterpretation dict (validated).
            On failure: REVIEW_REQUIRED or MODEL_NOT_AVAILABLE dict.
        """
        if not text or not text.strip():
            return self._make_error_result(
                text or "",
                "Empty input",
                status=MODEL_NOT_AVAILABLE,
            )

        # Step 1: Check model availability
        runner = self._model_runner
        if not runner.is_available():
            error = runner.status().get("error", "Model not loaded")
            return self._make_error_result(
                text,
                f"Local model not available: {error}",
                status=MODEL_NOT_AVAILABLE,
            )

        # Step 2: Generate from local model
        response_text, gen_error = runner.generate(
            prompt=text,
            system_prompt=SYSTEM_PROMPT,
        )

        if response_text is None:
            return self._make_error_result(
                text,
                f"Model generation failed: {gen_error}",
            )

        # Step 3: Extract JSON
        parsed = _extract_json_from_response(response_text)

        if parsed is None:
            return self._make_error_result(
                text,
                "Model returned non-JSON response",
                raw_response=response_text[:500],
            )

        # Step 4: Strict fail-closed validation
        valid, errors, enriched = _validate_strict(parsed, text)

        if not valid:
            return self._make_error_result(
                text,
                f"Strict validation failed: {'; '.join(errors)}",
                raw_model_output=parsed,
                validation_errors=errors,
            )

        # Step 5: Ensure all 18 fields present with defaults
        enriched = self._ensure_contract_fields(enriched)

        # Step 6: Run schema verifier for comprehensive validation
        report = validate_structured_interpretation(enriched, allow_expanded=True)
        if not report.valid:
            return self._make_error_result(
                text,
                f"Schema validation failed: {'; '.join(e.issue for e in report.errors)}",
                validation_errors=[e.to_dict() for e in report.errors],
                raw_model_output=parsed,
            )

        # Step 7: Grounding gate — verify interpretation against source text
        gate = ExpandedGroundingGate()
        grounding = gate.ground(enriched, text)

        if not grounding.safe_for_kernel:
            enriched["suggested_status"] = "REVIEW_REQUIRED"
            enriched["_grounding_issues"] = grounding.issues
            enriched["_grounded"] = False
            return enriched

        # Step 8: Inject metadata
        enriched["interpretation_model"] = self.model_name
        enriched["raw_input"] = text
        enriched["suggested_status"] = "REVIEW_REQUIRED"  # AI never claims VERIFIED
        enriched["_grounded"] = True
        enriched["_grounding_summary"] = grounding.summary

        return enriched

    def _ensure_contract_fields(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure all 18 fields present with valid defaults for missing ones."""
        result = dict(parsed)

        # Legacy 7
        result.setdefault("transaction_type", "")
        result.setdefault("parties", [])
        result.setdefault("amounts", [])
        result.setdefault("payment_method", "")
        result.setdefault("references", [])
        result.setdefault("ambiguities", [])
        result.setdefault("grounding", {
            "all_fields_explicitly_grounded": True,
            "inferred_fields": [],
        })

        # Expanded 11
        result.setdefault("transaction_type_enum", "UNKNOWN")
        result.setdefault("payment_method_enum", "UNKNOWN")
        result.setdefault("ambiguity_flags", ["NONE"])
        result.setdefault("referenced_transaction_index", None)
        result.setdefault("referenced_party", None)
        result.setdefault("referenced_amount", None)
        result.setdefault("field_confidences", [])
        result.setdefault("overall_confidence", "0.50")
        result.setdefault("suggested_status", "REVIEW_REQUIRED")
        result.setdefault("safety_flags", ["NONE"])
        result.setdefault("scope_flags", ["SINGLE_TRANSACTION"])

        return result

    def _make_error_result(
        self,
        text: str,
        error: str,
        status: str = "REVIEW_REQUIRED",
        raw_response: str = "",
        raw_model_output: Optional[Dict] = None,
        validation_errors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a structured error result."""
        return {
            "transaction_type": "",
            "parties": [],
            "amounts": [],
            "payment_method": "",
            "references": [],
            "ambiguities": [error],
            "grounding": {
                "all_fields_explicitly_grounded": False,
                "inferred_fields": [],
            },
            "transaction_type_enum": "UNKNOWN",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MULTIPLE_INTERPRETATIONS"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.00",
            "suggested_status": status,
            "safety_flags": ["MISSING_REQUIRED_FIELDS"],
            "scope_flags": ["SINGLE_TRANSACTION"],
            "interpretation_model": f"local-error({self.model_name})",
            "raw_input": text,
            "_error": error,
            "_raw_response": raw_response,
            "_raw_model_output": raw_model_output,
            "_validation_errors": validation_errors,
        }


# ---------------------------------------------------------------------------
# Deterministic Reference Specialist (Phase 2 — testing only)
# ---------------------------------------------------------------------------

from backend.maths.fyjc_ai_specialist import FYJCAISpecialist as DeterministicSpecialist  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def interpret_with_local_model(text: str) -> Dict[str, Any]:
    """One-shot local model interpretation."""
    specialist = FYJCLLMSpecialist()
    return specialist.interpret(text)


def interpret_deterministic(text: str) -> Dict[str, Any]:
    """One-shot deterministic interpretation (for testing)."""
    specialist = DeterministicSpecialist()
    return specialist.parse(text)
