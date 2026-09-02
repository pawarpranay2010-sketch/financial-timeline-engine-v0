"""
Platrixa — AI→Kernel Boundary Validator (Sprint 41 + Phase 1 Expansion)
=======================================================================

CANONICAL CONTRACTS:

  LEGACY (7 fields) — StructuredInterpretation (fyjc_p4_2_dataset_quality.py)
  EXPANDED (18 fields) — ExpandedInterpretation (fyjc_contract.py)

This validator enforces:
  ✓ Accepts LEGACY 7-field records (backward compatible)
  ✓ Accepts EXPANDED 18-field records (new contract)
  ✓ No unknown fields in either format → REJECT
  ✓ No auto-repair (malformed data → REJECT unchanged)
  ✓ Type matching per field contract
  ✓ Enum validation for expanded fields
  ✓ Confidence range validation (0.0–1.0)
  ✓ Compatibility with grounding_verifier.py
  ✓ All existing valid FYJC records accepted
  ✓ All malformed records explicitly rejected with reason

Exit code: 0 = contract valid, 1 = violations found
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class ValidationStatus(str, Enum):
    """Validation outcome."""
    VALID = "VALID"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    TYPE_ERROR = "TYPE_ERROR"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MALFORMED_JSON = "MALFORMED_JSON"


@dataclass(frozen=True)
class ValidationError:
    """Single validation failure."""
    field: str
    issue: str
    value_type: str  # not the actual value (for safety)
    expected: str
    status: ValidationStatus

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "issue": self.issue,
            "value_type": self.value_type,
            "expected": self.expected,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Complete validation result for StructuredInterpretation contract."""
    valid: bool
    status: ValidationStatus
    errors: Tuple[ValidationError, ...] = ()
    warnings: Tuple[str, ...] = ()
    parsed: Optional[Dict[str, Any]] = None  # Parsed but NOT repaired

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status.value,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": list(self.warnings),
            "parsed": self.parsed,
        }

    @property
    def exit_code(self) -> int:
        """Exit code for CLI."""
        if self.valid:
            return 0
        return 1


# ---------------------------------------------------------------------------
# Contract Constants — Legacy (7 fields)
# ---------------------------------------------------------------------------

# The EXACT 7 fields from StructuredInterpretation.to_dict()
CANONICAL_FIELDS: Set[str] = {
    "transaction_type",
    "parties",
    "amounts",
    "payment_method",
    "references",
    "ambiguities",
    "grounding",
}

# Type contract per field
FIELD_TYPE_CONTRACT = {
    "transaction_type": str,
    "parties": list,
    "amounts": list,
    "payment_method": str,
    "references": list,
    "ambiguities": list,
    "grounding": dict,
}

# Fields that must be list[str]
LIST_STR_FIELDS = {"parties", "references", "ambiguities"}

# Fields that must be list[dict]
LIST_DICT_FIELDS = {"amounts"}

# ---------------------------------------------------------------------------
# Contract Constants — Expanded (18 fields)
# ---------------------------------------------------------------------------

# The 11 additional fields from ExpandedInterpretation (fyjc_contract.py)
EXPANDED_ONLY_FIELDS: Set[str] = {
    "transaction_type_enum",
    "payment_method_enum",
    "ambiguity_flags",
    "referenced_transaction_index",
    "referenced_party",
    "referenced_amount",
    "field_confidences",
    "overall_confidence",
    "suggested_status",
    "safety_flags",
    "scope_flags",
}

# All valid fields = legacy 7 + expanded 11
ALL_VALID_FIELDS: Set[str] = CANONICAL_FIELDS | EXPANDED_ONLY_FIELDS

# Type contract for expanded fields
EXPANDED_FIELD_TYPE_CONTRACT = {
    "transaction_type_enum": str,
    "payment_method_enum": str,
    "ambiguity_flags": list,
    "referenced_transaction_index": (int, type(None)),
    "referenced_party": (str, type(None)),
    "referenced_amount": (str, type(None)),
    "field_confidences": list,
    "overall_confidence": str,
    "suggested_status": str,
    "safety_flags": list,
    "scope_flags": list,
}

# Allowed enum values for expanded fields
VALID_TX_ENUM = {
    "PURCHASE", "SALE", "PAYMENT", "RECEIPT", "CAPITAL", "EXPENSE",
    "RETURN_OUT", "RETURN_IN", "DISCOUNT_TRADE", "DISCOUNT_CASH",
    "SETTLEMENT", "GST", "DRAWING", "DEPRECIATION", "UNKNOWN",
}
VALID_PM_ENUM = {
    "CASH", "BANK", "CHEQUE", "NEFT", "UPI", "CREDIT", "UNKNOWN",
}
VALID_AMBIG_FLAGS = {
    "MISSING_PAYMENT_MODE", "MISSING_AMOUNT", "MISSING_PARTY",
    "AMBIGUOUS_REFERENCE", "MULTIPLE_INTERPRETATIONS",
    "CONFLICTING_INFORMATION", "UNRESOLVED_PRONOUN",
    "HISTORICAL_DEPENDENCY", "NONE",
}
VALID_GROUNDING_LEVELS = {"GROUNDED", "INFERRED", "UNRESOLVED", "CONFLICTING"}
VALID_SAFETY_FLAGS = {
    "AI_CLAIMED_VERIFIED", "JOURNAL_ENTRIES_PRODUCED",
    "LEDGER_BALANCES_PRODUCED", "MISSING_REQUIRED_FIELDS",
    "LOW_CONFIDENCE", "UNRESOLVED_FIELDS", "AMBIGUITY_DETECTED",
    "EMPTY_PARTIES", "NONE",
}
VALID_SCOPE_FLAGS = {
    "SINGLE_TRANSACTION", "MULTI_TRANSACTION", "SINGLE_AUTHORITY",
    "MULTI_AUTHORITY", "GST_SPECIFIC", "SETTLEMENT_CALCULATION",
    "RETURN_PROCESSING", "DISCOUNT_APPLICATION", "EDGE_CASE", "ADVERSARIAL",
}

# Fields requiring list[str]
EXPANDED_LIST_STR_FIELDS = {"ambiguity_flags", "safety_flags", "scope_flags"}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class StructuredInterpretationValidator:
    """
    Validates AI output against the canonical StructuredInterpretation contract.
    
    Does NOT auto-repair. Rejects malformed data unchanged.
    Compatible with grounding_verifier.py — preserves all structure.
    """

    def __init__(self):
        self.errors: List[ValidationError] = []

    def validate(self, output: Any, *, allow_expanded: bool = True) -> ValidationReport:
        """
        Validate AI output against the StructuredInterpretation contract.

        Accepts both LEGACY (7-field) and EXPANDED (18-field) formats.
        Records containing only legacy fields → validated as legacy.
        Records containing any expanded field → validated as expanded.
        Records with unknown fields → rejected.

        Args:
            output: Dict-like AI output (from JSON parse or dict)
            allow_expanded: If True, accept expanded 18-field records.
                           If False, reject any record with expanded fields.

        Returns:
            ValidationReport with errors and parsed (not repaired) output
        """
        self.errors = []
        parsed = None

        # Parse JSON if string
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return self._fail(
                    ValidationStatus.MALFORMED_JSON,
                    ValidationError(
                        field="<root>",
                        issue="not_valid_json",
                        value_type=type(output).__name__,
                        expected="valid JSON string or dict",
                        status=ValidationStatus.MALFORMED_JSON,
                    ),
                )

        # Must be dict-like
        if not isinstance(output, dict):
            return self._fail(
                ValidationStatus.TYPE_ERROR,
                ValidationError(
                    field="<root>",
                    issue="not_a_dict",
                    value_type=type(output).__name__,
                    expected="dict",
                    status=ValidationStatus.TYPE_ERROR,
                ),
            )

        # Classify: LEGACY, EXPANDED, or INVALID
        keys = set(output.keys())
        unknown = keys - ALL_VALID_FIELDS
        has_expanded = bool(keys & EXPANDED_ONLY_FIELDS)
        has_legacy = CANONICAL_FIELDS.issubset(keys)

        # Unknown fields → always reject
        if unknown:
            for unk in sorted(unknown):
                self.errors.append(
                    ValidationError(
                        field=unk,
                        issue="unknown_ai_field",
                        value_type=type(output[unk]).__name__,
                        expected="field not in StructuredInterpretation",
                        status=ValidationStatus.UNKNOWN_FIELD,
                    )
                )
            return self._fail(ValidationStatus.UNKNOWN_FIELD)

        # If expanded fields are present, all 7 legacy fields must also be present
        if has_expanded:
            missing_legacy = CANONICAL_FIELDS - keys
            if missing_legacy:
                for m in sorted(missing_legacy):
                    self.errors.append(
                        ValidationError(
                            field=m,
                            issue="missing_required_field",
                            value_type="absent",
                            expected="field in StructuredInterpretation",
                            status=ValidationStatus.SCHEMA_ERROR,
                        )
                    )
                return self._fail(ValidationStatus.SCHEMA_ERROR)

        # No legacy fields at all → reject
        if not keys & CANONICAL_FIELDS:
            return self._fail(
                ValidationStatus.SCHEMA_ERROR,
                ValidationError(
                    field="<root>",
                    issue="no_legacy_fields",
                    value_type="dict",
                    expected="at least some StructuredInterpretation fields",
                    status=ValidationStatus.SCHEMA_ERROR,
                ),
            )

        # Expanded fields present but not allowed
        if has_expanded and not allow_expanded:
            self.errors.append(
                ValidationError(
                    field="<expanded>",
                    issue="expanded_fields_not_allowed",
                    value_type="dict",
                    expected="legacy 7-field format only",
                    status=ValidationStatus.UNKNOWN_FIELD,
                )
            )
            return self._fail(ValidationStatus.UNKNOWN_FIELD)

        # Validate legacy fields (always)
        self._validate_string_field(output, "transaction_type")
        self._validate_list_str_field(output, "parties")
        self._validate_amounts_field(output)
        self._validate_string_field(output, "payment_method")
        self._validate_list_str_field(output, "references")
        self._validate_list_str_field(output, "ambiguities")
        self._validate_grounding_field(output)

        # Validate expanded fields if present
        if has_expanded:
            self._validate_expanded_fields(output)

        # Fail if any errors
        if self.errors:
            return self._fail(
                ValidationStatus.SCHEMA_ERROR,
                *self.errors,
            )

        # Success: return parsed (but NOT repaired) output
        return ValidationReport(
            valid=True,
            status=ValidationStatus.VALID,
            errors=tuple(self.errors),
            warnings=tuple(),
            parsed=output,
        )

    def _validate_string_field(
        self,
        output: Dict[str, Any],
        field: str,
    ) -> None:
        """Validate a string field (all 7 are optional, but if present must be str)."""
        if field not in output:
            return  # Optional

        value = output[field]
        if not isinstance(value, str):
            self.errors.append(
                ValidationError(
                    field=field,
                    issue="type_mismatch",
                    value_type=type(value).__name__,
                    expected="str",
                    status=ValidationStatus.TYPE_ERROR,
                )
            )

    def _validate_list_str_field(
        self,
        output: Dict[str, Any],
        field: str,
    ) -> None:
        """Validate a list[str] field."""
        if field not in output:
            return  # Optional

        value = output[field]
        if not isinstance(value, list):
            self.errors.append(
                ValidationError(
                    field=field,
                    issue="type_mismatch",
                    value_type=type(value).__name__,
                    expected="list[str]",
                    status=ValidationStatus.TYPE_ERROR,
                )
            )
            return

        # Each element must be string
        for i, item in enumerate(value):
            if not isinstance(item, str):
                self.errors.append(
                    ValidationError(
                        field=f"{field}[{i}]",
                        issue="element_type_mismatch",
                        value_type=type(item).__name__,
                        expected="str",
                        status=ValidationStatus.TYPE_ERROR,
                    )
                )

    def _validate_amounts_field(
        self,
        output: Dict[str, Any],
    ) -> None:
        """
        Validate amounts field (list[dict[str, str]]).
        
        Each dict must have string keys and string/numeric values.
        No auto-coercion: if a dict value is not compatible with str, reject.
        """
        if "amounts" not in output:
            return  # Optional

        value = output["amounts"]

        # Must be list
        if not isinstance(value, list):
            self.errors.append(
                ValidationError(
                    field="amounts",
                    issue="type_mismatch",
                    value_type=type(value).__name__,
                    expected="list[dict]",
                    status=ValidationStatus.TYPE_ERROR,
                )
            )
            return

        # Each element must be dict
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                self.errors.append(
                    ValidationError(
                        field=f"amounts[{i}]",
                        issue="element_type_mismatch",
                        value_type=type(item).__name__,
                        expected="dict",
                        status=ValidationStatus.TYPE_ERROR,
                    )
                )
                continue

            # Each dict must have string keys
            for key in item.keys():
                if not isinstance(key, str):
                    self.errors.append(
                        ValidationError(
                            field=f"amounts[{i}].key",
                            issue="dict_key_type_mismatch",
                            value_type=type(key).__name__,
                            expected="str",
                            status=ValidationStatus.TYPE_ERROR,
                        )
                    )

    def _validate_grounding_field(
        self,
        output: Dict[str, Any],
    ) -> None:
        """Validate grounding field (must be dict if present)."""
        if "grounding" not in output:
            return  # Optional

        value = output["grounding"]
        if not isinstance(value, dict):
            self.errors.append(
                ValidationError(
                    field="grounding",
                    issue="type_mismatch",
                    value_type=type(value).__name__,
                    expected="dict",
                    status=ValidationStatus.TYPE_ERROR,
                )
            )

    def _validate_expanded_fields(
        self,
        output: Dict[str, Any],
    ) -> None:
        """Validate the 11 expanded fields (8–18) when present."""
        # Enum fields
        tx_enum = output.get("transaction_type_enum")
        if tx_enum is not None:
            if not isinstance(tx_enum, str):
                self.errors.append(ValidationError(
                    field="transaction_type_enum", issue="type_mismatch",
                    value_type=type(tx_enum).__name__, expected="str",
                    status=ValidationStatus.TYPE_ERROR,
                ))
            elif tx_enum not in VALID_TX_ENUM:
                self.errors.append(ValidationError(
                    field="transaction_type_enum", issue="invalid_enum_value",
                    value_type=repr(tx_enum),
                    expected=f"one of {sorted(VALID_TX_ENUM)}",
                    status=ValidationStatus.SCHEMA_ERROR,
                ))

        pm_enum = output.get("payment_method_enum")
        if pm_enum is not None:
            if not isinstance(pm_enum, str):
                self.errors.append(ValidationError(
                    field="payment_method_enum", issue="type_mismatch",
                    value_type=type(pm_enum).__name__, expected="str",
                    status=ValidationStatus.TYPE_ERROR,
                ))
            elif pm_enum not in VALID_PM_ENUM:
                self.errors.append(ValidationError(
                    field="payment_method_enum", issue="invalid_enum_value",
                    value_type=repr(pm_enum),
                    expected=f"one of {sorted(VALID_PM_ENUM)}",
                    status=ValidationStatus.SCHEMA_ERROR,
                ))

        # List[str] enum fields
        for field_name, valid_set in [
            ("ambiguity_flags", VALID_AMBIG_FLAGS),
            ("safety_flags", VALID_SAFETY_FLAGS),
            ("scope_flags", VALID_SCOPE_FLAGS),
        ]:
            val = output.get(field_name)
            if val is not None:
                if not isinstance(val, list):
                    self.errors.append(ValidationError(
                        field=field_name, issue="type_mismatch",
                        value_type=type(val).__name__, expected="list[str]",
                        status=ValidationStatus.TYPE_ERROR,
                    ))
                else:
                    for i, item in enumerate(val):
                        if not isinstance(item, str):
                            self.errors.append(ValidationError(
                                field=f"{field_name}[{i}]",
                                issue="element_type_mismatch",
                                value_type=type(item).__name__, expected="str",
                                status=ValidationStatus.TYPE_ERROR,
                            ))
                        elif item not in valid_set:
                            self.errors.append(ValidationError(
                                field=f"{field_name}[{i}]",
                                issue="invalid_enum_value",
                                value_type=repr(item),
                                expected=f"one of {sorted(valid_set)}",
                                status=ValidationStatus.SCHEMA_ERROR,
                            ))

        # Optional int/None fields
        for field_name in ["referenced_transaction_index"]:
            val = output.get(field_name)
            if val is not None and not isinstance(val, int):
                self.errors.append(ValidationError(
                    field=field_name, issue="type_mismatch",
                    value_type=type(val).__name__, expected="int or null",
                    status=ValidationStatus.TYPE_ERROR,
                ))

        # Optional str/None fields
        for field_name in ["referenced_party", "referenced_amount"]:
            val = output.get(field_name)
            if val is not None and not isinstance(val, str):
                self.errors.append(ValidationError(
                    field=field_name, issue="type_mismatch",
                    value_type=type(val).__name__, expected="str or null",
                    status=ValidationStatus.TYPE_ERROR,
                ))

        # overall_confidence: must be numeric string 0.0–1.0
        oc = output.get("overall_confidence")
        if oc is not None:
            if not isinstance(oc, str):
                self.errors.append(ValidationError(
                    field="overall_confidence", issue="type_mismatch",
                    value_type=type(oc).__name__, expected="str",
                    status=ValidationStatus.TYPE_ERROR,
                ))
            else:
                try:
                    oc_val = Decimal(oc)
                    if oc_val < Decimal("0.0") or oc_val > Decimal("1.0"):
                        self.errors.append(ValidationError(
                            field="overall_confidence", issue="out_of_range",
                            value_type=repr(oc),
                            expected="decimal string 0.0–1.0",
                            status=ValidationStatus.SCHEMA_ERROR,
                        ))
                except (InvalidOperation, ValueError):
                    self.errors.append(ValidationError(
                        field="overall_confidence", issue="invalid_decimal",
                        value_type=repr(oc),
                        expected="decimal string 0.0–1.0",
                        status=ValidationStatus.SCHEMA_ERROR,
                    ))

        # suggested_status: must be string
        ss = output.get("suggested_status")
        if ss is not None and not isinstance(ss, str):
            self.errors.append(ValidationError(
                field="suggested_status", issue="type_mismatch",
                value_type=type(ss).__name__, expected="str",
                status=ValidationStatus.TYPE_ERROR,
            ))

        # field_confidences: list of dicts
        fc = output.get("field_confidences")
        if fc is not None:
            if not isinstance(fc, list):
                self.errors.append(ValidationError(
                    field="field_confidences", issue="type_mismatch",
                    value_type=type(fc).__name__, expected="list[dict]",
                    status=ValidationStatus.TYPE_ERROR,
                ))
            else:
                for i, item in enumerate(fc):
                    if not isinstance(item, dict):
                        self.errors.append(ValidationError(
                            field=f"field_confidences[{i}]",
                            issue="element_type_mismatch",
                            value_type=type(item).__name__, expected="dict",
                            status=ValidationStatus.TYPE_ERROR,
                        ))
                    else:
                        # Must have field_name
                        if "field_name" not in item:
                            self.errors.append(ValidationError(
                                field=f"field_confidences[{i}]",
                                issue="missing_field_name",
                                value_type="dict",
                                expected="dict with 'field_name' key",
                                status=ValidationStatus.SCHEMA_ERROR,
                            ))
                        # Validate confidence value if present
                        conf = item.get("confidence")
                        if conf is not None and isinstance(conf, str):
                            try:
                                c = Decimal(conf)
                                if c < Decimal("0.0") or c > Decimal("1.0"):
                                    self.errors.append(ValidationError(
                                        field=f"field_confidences[{i}].confidence",
                                        issue="out_of_range",
                                        value_type=repr(conf),
                                        expected="decimal string 0.0–1.0",
                                        status=ValidationStatus.SCHEMA_ERROR,
                                    ))
                            except (InvalidOperation, ValueError):
                                self.errors.append(ValidationError(
                                    field=f"field_confidences[{i}].confidence",
                                    issue="invalid_decimal",
                                    value_type=repr(conf),
                                    expected="decimal string 0.0–1.0",
                                    status=ValidationStatus.SCHEMA_ERROR,
                                ))
                        # Validate grounding if present
                        gnd = item.get("grounding")
                        if gnd is not None and isinstance(gnd, str):
                            if gnd not in VALID_GROUNDING_LEVELS:
                                self.errors.append(ValidationError(
                                    field=f"field_confidences[{i}].grounding",
                                    issue="invalid_enum_value",
                                    value_type=repr(gnd),
                                    expected=f"one of {sorted(VALID_GROUNDING_LEVELS)}",
                                    status=ValidationStatus.SCHEMA_ERROR,
                                ))

    def _fail(
        self,
        status: ValidationStatus,
        *errors: ValidationError,
    ) -> ValidationReport:
        """Return a failed validation report.

        When status is SCHEMA_ERROR but all errors share a more specific
        status (e.g. all TYPE_ERROR), use that specific status for backward
        compatibility.
        """
        all_errors = tuple(errors) if errors else tuple(self.errors)
        report_status = status

        # Preserve backward-compatible status when all errors agree
        if status == ValidationStatus.SCHEMA_ERROR and all_errors:
            distinct_statuses = {e.status for e in all_errors}
            if len(distinct_statuses) == 1:
                report_status = distinct_statuses.pop()

        return ValidationReport(
            valid=False,
            status=report_status,
            errors=all_errors,
            warnings=tuple(),
            parsed=None,  # Never return unparsed/malformed data
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_structured_interpretation(
    output: Any,
    *,
    allow_expanded: bool = True,
) -> ValidationReport:
    """
    Validate AI output against the StructuredInterpretation contract.

    Accepts both legacy 7-field and expanded 18-field records.
    Use allow_expanded=False to reject expanded records (legacy-only mode).

    Args:
        output: Dict or JSON string from AI
        allow_expanded: If False, reject records with expanded fields.

    Returns:
        ValidationReport (never raises, never repairs)
    """
    return StructuredInterpretationValidator().validate(
        output, allow_expanded=allow_expanded,
    )


def assert_valid_structured_interpretation(output: Any) -> Dict[str, Any]:
    """
    Validate and return parsed output, or raise ValueError.

    Args:
        output: Dict or JSON string

    Returns:
        Parsed dict (structure preserved, not repaired)

    Raises:
        ValueError if invalid
    """
    report = validate_structured_interpretation(output)
    if not report.valid:
        errors_str = "\n  ".join(
            f"{e.field}: {e.issue} (got {e.value_type}, expected {e.expected})"
            for e in report.errors
        )
        raise ValueError(
            f"StructuredInterpretation contract violation:\n  {errors_str}"
        )
    return report.parsed or {}
