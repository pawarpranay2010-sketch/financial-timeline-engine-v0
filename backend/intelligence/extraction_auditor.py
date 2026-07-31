"""
Extraction Auditor — Dual-track extraction verification interface.

Provides a zero-trust verification layer for independently extracted
financial facts. Compares typed normalized facts (not raw JSON strings)
and detects disagreement in value, sign, unit, currency, metric definition,
and period.

Comparison States:
    AGREEMENT                     — Both extractions match on all dimensions
    SEMANTIC_EQUIVALENCE           — Values differ but are semantically equivalent
                                     (e.g., "1B" vs "1,000,000,000")
    CURRENCY_MISMATCH              — Different currencies detected
    UNIT_MISMATCH                  — Different units detected (e.g., millions vs billions)
    PERIOD_MISMATCH                — Different reporting periods
    SCOPE_MISMATCH                 — Different scope (consolidated vs standalone)
    ACCOUNTING_BASIS_MISMATCH      — Different accounting basis (GAAP vs IFRS)
    METRIC_DEFINITION_MISMATCH     — Different metric definitions
    MATERIAL_VALUE_CONFLICT        — Same metric/period/unit but materially different values
    EXTRACTION_CORRUPTED           — Extraction result is unparseable or invalid
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("fte.rag.extraction_auditor")

# ---------------------------------------------------------------------------
# Comparison state constants
# ---------------------------------------------------------------------------

AGREEMENT = "AGREEMENT"
SEMANTIC_EQUIVALENCE = "SEMANTIC_EQUIVALENCE"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
UNIT_MISMATCH = "UNIT_MISMATCH"
PERIOD_MISMATCH = "PERIOD_MISMATCH"
SCOPE_MISMATCH = "SCOPE_MISMATCH"
ACCOUNTING_BASIS_MISMATCH = "ACCOUNTING_BASIS_MISMATCH"
METRIC_DEFINITION_MISMATCH = "METRIC_DEFINITION_MISMATCH"
MATERIAL_VALUE_CONFLICT = "MATERIAL_VALUE_CONFLICT"
EXTRACTION_CORRUPTED = "EXTRACTION_CORRUPTED"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ExtractedFact:
    """Normalized extracted fact for comparison."""

    metric_name: str = ""
    metric_definition: str = ""
    value: Optional[float] = None
    unit: str = ""
    scale: str = "actual"
    currency_code: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    scope: str = ""
    accounting_basis: str = ""
    source_anchor: str = ""
    raw_text: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.metric_name and self.value is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "metric_definition": self.metric_definition,
            "value": self.value,
            "unit": self.unit,
            "scale": self.scale,
            "currency_code": self.currency_code,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "scope": self.scope,
            "accounting_basis": self.accounting_basis,
            "source_anchor": self.source_anchor,
        }


@dataclass
class ComparisonResult:
    """Result of comparing two extracted facts."""

    state: str = AGREEMENT
    fact_a: Optional[ExtractedFact] = None
    fact_b: Optional[ExtractedFact] = None
    differences: List[str] = field(default_factory=list)
    value_delta_pct: Optional[float] = None

    @property
    def is_agreement(self) -> bool:
        return self.state in (AGREEMENT, SEMANTIC_EQUIVALENCE)

    @property
    def is_mismatch(self) -> bool:
        return self.state not in (AGREEMENT, SEMANTIC_EQUIVALENCE)


# ---------------------------------------------------------------------------
# Extraction Auditor
# ---------------------------------------------------------------------------


class ExtractionAuditor:
    """
    Zero-trust extraction verification interface.

    Compares independently extracted financial facts and detects
    disagreement. Never compares raw JSON strings — compares typed
    normalized facts.
    """

    # Maximum allowed percentage difference before flagging as material conflict
    MATERIAL_THRESHOLD_PCT = 5.0

    # Scales that are semantically equivalent
    _SCALE_MAP = {
        "actual": 1,
        "units": 1,
        "thousands": 1_000,
        "millions": 1_000_000,
        "crores": 10_000_000,
        "billions": 1_000_000_000,
        "lakhs": 100_000,
        "trillions": 1_000_000_000_000,
    }

    @classmethod
    def normalize_to_actual(cls, value: float, scale: str) -> float:
        """Normalize a value to actual units based on its scale."""
        multiplier = cls._SCALE_MAP.get(scale.lower().strip(), 1)
        return value * multiplier

    @classmethod
    def compare(
        cls,
        fact_a_raw: Dict[str, Any],
        fact_b_raw: Dict[str, Any],
    ) -> ComparisonResult:
        """
        Compare two independently extracted facts.

        Args:
            fact_a_raw: Raw extraction result A (dict with metric_name, value, etc.)
            fact_b_raw: Raw extraction result B (dict with metric_name, value, etc.)

        Returns:
            ComparisonResult with the comparison state and details
        """
        # Convert to normalized facts
        fact_a = cls._normalize(fact_a_raw)
        fact_b = cls._normalize(fact_b_raw)

        # Check for corruption
        if not fact_a.is_valid and not fact_b.is_valid:
            return ComparisonResult(
                state=EXTRACTION_CORRUPTED,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=["Both extractions are invalid/missing"],
            )
        if not fact_a.is_valid:
            return ComparisonResult(
                state=EXTRACTION_CORRUPTED,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=["Extraction A is invalid"],
            )
        if not fact_b.is_valid:
            return ComparisonResult(
                state=EXTRACTION_CORRUPTED,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=["Extraction B is invalid"],
            )

        differences = []

        # 1. Check metric definition
        def_a = (fact_a.metric_definition or fact_a.metric_name).lower().strip()
        def_b = (fact_b.metric_definition or fact_b.metric_name).lower().strip()
        if def_a != def_b:
            return ComparisonResult(
                state=METRIC_DEFINITION_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[
                    f"Definition A: '{def_a}' vs Definition B: '{def_b}'"
                ],
            )

        # 2. Check currency
        if (fact_a.currency_code or "").upper() != (fact_b.currency_code or "").upper():
            return ComparisonResult(
                state=CURRENCY_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[
                    f"Currency A: '{fact_a.currency_code}' vs "
                    f"Currency B: '{fact_b.currency_code}'"
                ],
            )

        # 3. Check unit
        unit_a = (fact_a.unit or "").lower().strip()
        unit_b = (fact_b.unit or "").lower().strip()
        if unit_a and unit_b and unit_a != unit_b:
            return ComparisonResult(
                state=UNIT_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[f"Unit A: '{unit_a}' vs Unit B: '{unit_b}'"],
            )

        # 4. Check period
        period_a = fact_a.period_end or fact_a.period_start or ""
        period_b = fact_b.period_end or fact_b.period_start or ""
        if period_a and period_b and period_a != period_b:
            return ComparisonResult(
                state=PERIOD_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[
                    f"Period A: '{period_a}' vs Period B: '{period_b}'"
                ],
            )

        # 5. Check scope
        scope_a = (fact_a.scope or "").lower().strip()
        scope_b = (fact_b.scope or "").lower().strip()
        if scope_a and scope_b and scope_a != scope_b:
            return ComparisonResult(
                state=SCOPE_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[f"Scope A: '{scope_a}' vs Scope B: '{scope_b}'"],
            )

        # 6. Check accounting basis
        basis_a = (fact_a.accounting_basis or "").lower().strip()
        basis_b = (fact_b.accounting_basis or "").lower().strip()
        if basis_a and basis_b and basis_a != basis_b:
            return ComparisonResult(
                state=ACCOUNTING_BASIS_MISMATCH,
                fact_a=fact_a,
                fact_b=fact_b,
                differences=[
                    f"Basis A: '{basis_a}' vs Basis B: '{basis_b}'"
                ],
            )

        # 7. Compare values (scale-normalized)
        val_a = cls.normalize_to_actual(fact_a.value, fact_a.scale)
        val_b = cls.normalize_to_actual(fact_b.value, fact_b.scale)

        if val_a == val_b:
            return ComparisonResult(
                state=AGREEMENT,
                fact_a=fact_a,
                fact_b=fact_b,
            )

        # Calculate percentage difference
        max_val = max(abs(val_a), abs(val_b))
        if max_val > 0:
            delta_pct = abs(val_a - val_b) / max_val * 100
        else:
            delta_pct = 0.0

        if delta_pct <= cls.MATERIAL_THRESHOLD_PCT:
            return ComparisonResult(
                state=SEMANTIC_EQUIVALENCE,
                fact_a=fact_a,
                fact_b=fact_b,
                value_delta_pct=round(delta_pct, 2),
                differences=[f"Values differ by {delta_pct:.2f}% (within threshold)"],
            )

        return ComparisonResult(
            state=MATERIAL_VALUE_CONFLICT,
            fact_a=fact_a,
            fact_b=fact_b,
            value_delta_pct=round(delta_pct, 2),
            differences=[
                f"Value A: {val_a} vs Value B: {val_b} "
                f"(delta: {delta_pct:.2f}%)"
            ],
        )

    @classmethod
    def compare_batch(
        cls,
        batch_a: List[Dict[str, Any]],
        batch_b: List[Dict[str, Any]],
    ) -> Dict[str, ComparisonResult]:
        """
        Compare two batches of extracted facts.

        Matches facts by metric_name + period before comparing.

        Args:
            batch_a: List of extraction A fact dicts
            batch_b: List of extraction B fact dicts

        Returns:
            Dict mapping metric/period keys to ComparisonResults
        """
        # Index by metric + period
        def index_facts(batch: List[Dict]) -> Dict[str, Dict]:
            idx: Dict[str, Dict] = {}
            for fact in batch:
                key = f"{fact.get('metric_name', '')}|{fact.get('period_end', fact.get('period_start', ''))}"
                idx[key] = fact
            return idx

        idx_a = index_facts(batch_a)
        idx_b = index_facts(batch_b)

        results: Dict[str, ComparisonResult] = {}

        # Compare common keys
        for key in set(idx_a.keys()) & set(idx_b.keys()):
            results[key] = cls.compare(idx_a[key], idx_b[key])

        # Facts only in A or B
        for key in set(idx_a.keys()) - set(idx_b.keys()):
            results[key] = ComparisonResult(
                state=EXTRACTION_CORRUPTED,
                fact_a=cls._normalize(idx_a[key]),
                differences=[f"Fact '{key}' only present in extraction A"],
            )
        for key in set(idx_b.keys()) - set(idx_a.keys()):
            results[key] = ComparisonResult(
                state=EXTRACTION_CORRUPTED,
                fact_b=cls._normalize(idx_b[key]),
                differences=[f"Fact '{key}' only present in extraction B"],
            )

        return results

    @classmethod
    def _normalize(cls, raw: Dict[str, Any]) -> ExtractedFact:
        """Convert a raw extraction dict into a normalized ExtractedFact."""
        return ExtractedFact(
            metric_name=raw.get("metric_name") or raw.get("metric", "") or "",
            metric_definition=raw.get("metric_definition", "") or "",
            value=raw.get("value") or raw.get("metric_value"),
            unit=raw.get("unit", "") or "",
            scale=raw.get("scale", "actual") or "actual",
            currency_code=raw.get("currency_code", "") or "",
            period_start=str(raw.get("period_start", "")) if raw.get("period_start") else "",
            period_end=str(raw.get("period_end", "")) if raw.get("period_end") else "",
            scope=raw.get("scope", "") or "",
            accounting_basis=raw.get("accounting_basis", "") or "",
            source_anchor=raw.get("source_anchor", "") or "",
            raw_text=raw.get("raw_text", "") or "",
        )
