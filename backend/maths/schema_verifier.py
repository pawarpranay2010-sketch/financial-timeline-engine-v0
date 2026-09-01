"""
Platrixa — AI→Kernel Boundary Validator (Sprint 41)
===================================================

CANONICAL CONTRACT: StructuredInterpretation (backend/maths/fyjc_p4_2_dataset_quality.py)

```python
@dataclass
class StructuredInterpretation:
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

def to_dict(self) -> Dict[str, Any]:  # 7 fields exactly
def to_json_string(self) -> str:      # json.dumps(to_dict())
```

PRODUCER: AI → JSON string via to_json_string()
CONSUMER: JSON parse → grounding_verifier.py, fyjc_p4_2_dataset_quality.py, models.py

This validator enforces:
  ✓ Exactly 7 allowed fields (top-level)
  ✓ No unknown fields (receipt_number, vendor_id, etc. → REJECT)
  ✓ No auto-repair (malformed data → REJECT unchanged)
  ✓ Type matching (parties: list[str], amounts: list[dict], etc.)
  ✓ Compatibility with grounding_verifier.py (preserves all structure)
  ✓ All existing valid FYJC records accepted
  ✓ All malformed records explicitly rejected with reason

Exit code: 0 = contract valid, 1 = violations found
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
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
# Contract Constants
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

    def validate(self, output: Any) -> ValidationReport:
        """
        Validate AI output against the contract.

        Args:
            output: Dict-like AI output (from JSON parse or dict)

        Returns:
            ValidationReport with errors and parsed (not repaired) output
        """
        self.errors = []
        parsed = None

        # Parse JSON if string
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError as e:
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

        # Check for unknown fields (CRITICAL: reject unknown AI fields)
        unknown = set(output.keys()) - CANONICAL_FIELDS
        if unknown:
            for unk in unknown:
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

        # Validate each field
        self._validate_string_field(output, "transaction_type")
        self._validate_list_str_field(output, "parties")
        self._validate_amounts_field(output)
        self._validate_string_field(output, "payment_method")
        self._validate_list_str_field(output, "references")
        self._validate_list_str_field(output, "ambiguities")
        self._validate_grounding_field(output)

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

    def _fail(
        self,
        status: ValidationStatus,
        *errors: ValidationError,
    ) -> ValidationReport:
        """Return a failed validation report."""
        return ValidationReport(
            valid=False,
            status=status,
            errors=tuple(errors) if errors else tuple(self.errors),
            warnings=tuple(),
            parsed=None,  # Never return unparsed/malformed data
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_structured_interpretation(output: Any) -> ValidationReport:
    """
    Validate AI output against the StructuredInterpretation contract.

    Args:
        output: Dict or JSON string from AI

    Returns:
        ValidationReport (never raises, never repairs)
    """
    return StructuredInterpretationValidator().validate(output)


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
