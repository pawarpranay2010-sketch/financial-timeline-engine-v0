"""
Independent grounding verification for the Platrixa truth boundary.

The specialist model is allowed to interpret language, but it is not allowed
to decide whether an interpretation is grounded. This module therefore:

1. Reads the original user text.
2. Reads the structured interpretation.
3. Derives a grounding status from the original text and the interpreted
   value, without using the model's grounding label.
4. Rejects unsupported non-null claims and contradictory model labels.

The verifier is deliberately conservative. A value that is not explicitly
present or deterministically normalizable from the raw text is classified as
INFERRED, not upgraded to EXPLICIT because the model said so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class GroundingStatus(str, Enum):
    """Grounding classification independently derived from raw input text."""

    EXPLICIT = "EXPLICIT"
    NORMALIZED = "NORMALIZED"
    INFERRED = "INFERRED"
    ABSENT = "ABSENT"


@dataclass(frozen=True)
class FieldGrounding:
    """Independent grounding result for one interpretation field."""

    field: str
    value: Any
    status: GroundingStatus
    evidence: Optional[str]
    reason: str
    ai_claimed_status: Optional[str] = None

    @property
    def grounded(self) -> bool:
        """Whether the value is safe to treat as text-grounded input."""

        return self.status in (
            GroundingStatus.EXPLICIT,
            GroundingStatus.NORMALIZED,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "status": self.status.value,
            "grounded": self.grounded,
            "evidence": self.evidence,
            "reason": self.reason,
            "ai_claimed_status": self.ai_claimed_status,
        }


@dataclass(frozen=True)
class GroundingReport:
    """Complete verification result passed to the deterministic Kernel."""

    accepted: bool
    fields: Dict[str, FieldGrounding]
    violations: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "fields": {
                name: result.to_dict()
                for name, result in self.fields.items()
            },
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class _Claim:
    """A value plus an optional grounding label supplied by the AI."""

    value: Any
    ai_claimed_status: Optional[str]


class GroundingVerifier:
    """
    Independently checks grounding for structured AI interpretations.

    Interpretation formats supported:

    Plain values::

        {"party": "Raj", "amount": 20000}

    Value plus model label::

        {
            "amount": {
                "value": 10000,
                "grounding": "EXPLICIT",
            }
        }

    A top-level grounding map is also accepted and is ignored for derivation::

        {
            "amount": 10000,
            "_grounding": {"amount": "EXPLICIT"},
        }

    The ``grounding`` or ``status`` values are metadata from the AI only.
    They never influence the independently derived status.
    """

    _GROUNDING_KEYS = {
        "grounding",
        "grounding_status",
        "status",
        "grounding_label",
    }
    _METADATA_KEYS = {
        "_grounding",
        "grounding",
        "grounding_status",
        "field_grounding",
        "metadata",
    }

    # These phrases supply a relationship or operation, but not a concrete
    # amount. A numeric amount emitted for one of these is an unsupported
    # inference and must not enter the Truth Kernel.
    _RELATIVE_AMOUNT_PATTERN = re.compile(
        r"\b(?:half|quarter|third|remaining|balance|rest|"
        r"one[-\s]+half|two[-\s]+thirds?)\b",
        re.IGNORECASE,
    )

    _NUMBER_PATTERN = re.compile(
        r"(?<![\w])"
        r"(?:(?:₹|rs\.?|inr)\s*)?"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        r"(?![\w])",
        re.IGNORECASE,
    )

    # Language-to-canonical mappings are intentionally small and
    # deterministic. They are not model predictions.
    _FIELD_NORMALIZATIONS: Dict[str, Dict[str, str]] = {
        "transaction_type": {
            "paid": "PAYMENT",
            "pay": "PAYMENT",
            "payment": "PAYMENT",
            "purchased": "PURCHASE",
            "purchase": "PURCHASE",
            "bought": "PURCHASE",
            "sold": "SALE",
            "sale": "SALE",
            "received": "RECEIPT",
            "received payment": "RECEIPT",
        },
        "payment_method": {
            "cheque": "CHEQUE",
            "check": "CHEQUE",
            "cash": "CASH",
            "upi": "UPI",
            "bank transfer": "BANK_TRANSFER",
            "neft": "BANK_TRANSFER",
            "rtgs": "BANK_TRANSFER",
        },
        "currency": {
            "rs": "INR",
            "rs.": "INR",
            "rupee": "INR",
            "rupees": "INR",
            "₹": "INR",
            "inr": "INR",
            "$": "USD",
            "usd": "USD",
        },
    }

    def __init__(self, *, reject_inferred: bool = True):
        """
        Create a verifier.

        ``reject_inferred`` defaults to True because inferred truth-bearing
        values cannot be sent into the Kernel as facts. Callers may set it to
        False when they need a report-only mode, but the status remains
        INFERRED and the value remains ungrounded.
        """

        self.reject_inferred = reject_inferred

    def verify(
        self,
        raw_text: str,
        interpretation: Mapping[str, Any],
        *,
        expected_fields: Optional[Sequence[str]] = None,
    ) -> GroundingReport:
        """
        Verify every field in an interpretation against ``raw_text``.

        Parameters
        ----------
        raw_text:
            Original user sentence. This is the only evidence source.
        interpretation:
            Structured output from the specialist model.
        expected_fields:
            Optional schema fields that must also be represented. Missing
            expected fields are classified as ABSENT.
        """

        if not isinstance(raw_text, str):
            raise TypeError("raw_text must be a string")
        if not isinstance(interpretation, Mapping):
            raise TypeError("interpretation must be a mapping")

        top_level_labels = self._top_level_labels(interpretation)
        claims = dict(
            self._iter_claims(
                interpretation,
                prefix="",
                inherited_labels=top_level_labels,
            )
        )

        if expected_fields:
            for field in expected_fields:
                if field not in claims:
                    claims[field] = _Claim(
                        value=None,
                        ai_claimed_status=top_level_labels.get(field),
                    )

        results: Dict[str, FieldGrounding] = {}
        violations: List[str] = []

        for field, claim in claims.items():
            result = self._classify(
                raw_text=raw_text,
                field=field,
                value=claim.value,
                ai_claimed_status=claim.ai_claimed_status,
            )
            results[field] = result

            if (
                claim.ai_claimed_status
                and claim.ai_claimed_status != result.status.value
            ):
                violations.append(
                    f"{field}: AI claimed "
                    f"{claim.ai_claimed_status}, but independent check "
                    f"derived {result.status.value}"
                )

            if self.reject_inferred and result.status == GroundingStatus.INFERRED:
                violations.append(
                    f"{field}: unsupported value rejected; "
                    f"INFERRED values cannot enter the Truth Kernel"
                )

        return GroundingReport(
            accepted=not violations,
            fields=results,
            violations=tuple(violations),
        )

    def _classify(
        self,
        *,
        raw_text: str,
        field: str,
        value: Any,
        ai_claimed_status: Optional[str],
    ) -> FieldGrounding:
        if self._is_absent(value):
            return FieldGrounding(
                field=field,
                value=value,
                status=GroundingStatus.ABSENT,
                evidence=None,
                reason="No value was supplied for this field.",
                ai_claimed_status=ai_claimed_status,
            )

        base_field = self._base_field(field)
        raw_value = str(value)

        if self._is_numeric(value):
            return self._classify_numeric(
                raw_text=raw_text,
                field=field,
                value=value,
                ai_claimed_status=ai_claimed_status,
            )

        exact_evidence = self._find_text_evidence(raw_text, raw_value)
        if exact_evidence is not None:
            return FieldGrounding(
                field=field,
                value=value,
                status=GroundingStatus.EXPLICIT,
                evidence=exact_evidence,
                reason="The interpreted value appears directly in the raw text.",
                ai_claimed_status=ai_claimed_status,
            )

        normalized_evidence = self._find_normalization_evidence(
            raw_text=raw_text,
            field=base_field,
            value=raw_value,
        )
        if normalized_evidence is not None:
            source, canonical = normalized_evidence
            return FieldGrounding(
                field=field,
                value=value,
                status=GroundingStatus.NORMALIZED,
                evidence=source,
                reason=(
                    f"The raw phrase {source!r} deterministically normalizes "
                    f"to {canonical!r}."
                ),
                ai_claimed_status=ai_claimed_status,
            )

        return FieldGrounding(
            field=field,
            value=value,
            status=GroundingStatus.INFERRED,
            evidence=self._relative_evidence(raw_text, base_field),
            reason=(
                "The value is not stated or deterministically normalizable "
                "from the raw text."
            ),
            ai_claimed_status=ai_claimed_status,
        )

    def _classify_numeric(
        self,
        *,
        raw_text: str,
        field: str,
        value: Any,
        ai_claimed_status: Optional[str],
    ) -> FieldGrounding:
        target = self._to_decimal(value)
        number_matches = list(self._NUMBER_PATTERN.finditer(raw_text))

        for match in number_matches:
            candidate = self._to_decimal(match.group(1))
            if candidate == target:
                evidence = match.group(0)
                # Currency symbols, separators, and formatting are a
                # deterministic representation change; the amount itself is
                # still explicit in the sentence.
                formatted = match.group(1)
                status = (
                    GroundingStatus.NORMALIZED
                    if any(char in evidence for char in ",₹")
                    or re.search(r"\b(?:rs\.?|inr)\b", evidence, re.I)
                    else GroundingStatus.EXPLICIT
                )
                return FieldGrounding(
                    field=field,
                    value=value,
                    status=status,
                    evidence=evidence,
                    reason=(
                        "The numeric value matches a numeric literal in the "
                        "raw text."
                    ),
                    ai_claimed_status=ai_claimed_status,
                )

        relative_evidence = self._relative_evidence(raw_text, self._base_field(field))
        if relative_evidence is not None:
            return FieldGrounding(
                field=field,
                value=value,
                status=GroundingStatus.INFERRED,
                evidence=relative_evidence,
                reason=(
                    "The raw text describes a relative amount, but does not "
                    "state a concrete numeric amount."
                ),
                ai_claimed_status=ai_claimed_status,
            )

        return FieldGrounding(
            field=field,
            value=value,
            status=GroundingStatus.INFERRED,
            evidence=None,
            reason=(
                "No matching numeric literal was found in the raw text."
            ),
            ai_claimed_status=ai_claimed_status,
        )

    def _iter_claims(
        self,
        value: Any,
        *,
        prefix: str,
        inherited_labels: Mapping[str, str],
    ) -> Iterable[Tuple[str, _Claim]]:
        """Flatten nested interpretation fields while ignoring metadata."""

        if isinstance(value, Mapping):
            if "value" in value:
                label = self._read_status(value)
                field = prefix
                yield field, _Claim(
                    value=value.get("value"),
                    ai_claimed_status=label or inherited_labels.get(field),
                )
                return

            for key, child in value.items():
                if not prefix and key in self._METADATA_KEYS:
                    continue
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                yield from self._iter_claims(
                    child,
                    prefix=child_prefix,
                    inherited_labels=inherited_labels,
                )
            return

        if isinstance(value, list):
            if not value:
                yield prefix, _Claim(
                    value=None,
                    ai_claimed_status=inherited_labels.get(prefix),
                )
                return
            for index, child in enumerate(value):
                child_prefix = f"{prefix}[{index}]"
                yield from self._iter_claims(
                    child,
                    prefix=child_prefix,
                    inherited_labels=inherited_labels,
                )
            return

        yield prefix, _Claim(
            value=value,
            ai_claimed_status=inherited_labels.get(prefix),
        )

    def _top_level_labels(
        self,
        interpretation: Mapping[str, Any],
    ) -> Dict[str, str]:
        for key in (
            "_grounding",
            "grounding",
            "grounding_status",
            "field_grounding",
        ):
            candidate = interpretation.get(key)
            if isinstance(candidate, Mapping):
                return {
                    str(field): str(status).upper()
                    for field, status in candidate.items()
                    if status is not None
                }
        return {}

    def _read_status(self, value: Mapping[str, Any]) -> Optional[str]:
        for key in self._GROUNDING_KEYS:
            status = value.get(key)
            if status is not None:
                return str(status).upper()
        return None

    def _find_normalization_evidence(
        self,
        *,
        raw_text: str,
        field: str,
        value: str,
    ) -> Optional[Tuple[str, str]]:
        wanted = self._canonical(value)
        aliases = self._FIELD_NORMALIZATIONS.get(field, {})

        for source, canonical in aliases.items():
            if self._canonical(canonical) != wanted:
                continue
            if self._find_text_evidence(raw_text, source) is not None:
                return source, canonical

        return None

    def _relative_evidence(
        self,
        raw_text: str,
        field: str,
    ) -> Optional[str]:
        if field in {
            "amount",
            "quantity",
            "balance",
            "remaining_balance",
            "payment_amount",
        }:
            match = self._RELATIVE_AMOUNT_PATTERN.search(raw_text)
            return match.group(0) if match else None
        return None

    @staticmethod
    def _base_field(field: str) -> str:
        base = field.rsplit(".", 1)[-1]
        return re.sub(r"\[\d+\]$", "", base)

    @staticmethod
    def _canonical(value: str) -> str:
        return re.sub(r"[\s_-]+", " ", value.strip().lower())

    @classmethod
    def _find_text_evidence(
        cls,
        raw_text: str,
        value: str,
    ) -> Optional[str]:
        value = value.strip()
        if not value:
            return None

        # Phrase matches use normalized whitespace; word-like values use
        # boundaries so "Raj" does not accidentally match "Maharaj".
        pattern = re.escape(value).replace(r"\ ", r"\s+")
        if re.fullmatch(r"[\w\s-]+", value, flags=re.UNICODE):
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        return match.group(0) if match else None

    @staticmethod
    def _is_absent(value: Any) -> bool:
        return value is None or (
            isinstance(value, str) and not value.strip()
        )

    @staticmethod
    def _is_numeric(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float, Decimal)):
            return True
        if isinstance(value, str):
            try:
                Decimal(value.replace(",", "").strip())
                return True
            except (InvalidOperation, ValueError):
                return False
        return False

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return Decimal(str(value))