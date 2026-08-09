"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/fact_model.py

Canonical financial fact / graph-node model.

Every numerical fact becomes a canonical node that retains its ORIGINAL
source representation alongside its normalized representation:

    original_value / original_unit  -> what the source actually said
    normalized_value / normalized_unit -> what the engine computes with

The engine NEVER destroys the original representation and NEVER guesses a
missing value. Facts with no usable numeric value are carried with status
BLOCKED so downstream computation fails closed.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from backend.maths.status import (
    VERIFIED,
    BLOCKED,
    status_from_provenance,
)

# ---------------------------------------------------------------------------
# Numeric coercion (strict, deterministic)
# ---------------------------------------------------------------------------


def to_decimal(value: Any) -> Optional[Decimal]:
    """Strict numeric conversion. Never coerces labels, ranges, booleans,
    or None. Commas are the only tolerated decoration (mirrors the
    existing Formula Engine's `_d` rule)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        # Numeric only - anything else (ranges, labels) is None.
        Decimal(s)
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Canonical fact node
# ---------------------------------------------------------------------------


@dataclass
class FactNode:
    """One canonical financial fact node.

    Fields follow the Sprint 12A canonical fact model. Optional metadata
    stays absent (never fabricated); the original source representation is
    always preserved in original_value / original_unit / original_scale.
    """

    node_id: str                       # canonical concept (or source label)
    canonical_concept: str             # canonical concept key
    value: Optional[Decimal] = None    # working value (see normalize)
    original_value: Optional[Any] = None
    original_unit: Optional[str] = None
    original_scale: Optional[str] = None
    normalized_value: Optional[Decimal] = None
    normalized_unit: Optional[str] = None
    currency: Optional[str] = None
    period: Optional[str] = None
    period_type: Optional[str] = None  # e.g. FY / Q / TTM / average / ending
    source: Optional[str] = None
    source_tier: Optional[str] = None  # DOCUMENT / APPENDIX / REGULATORY_API ...
    document_name: Optional[str] = None
    page: Optional[str] = None
    evidence: Optional[str] = None
    status: str = VERIFIED
    status_reason: Optional[str] = None
    excel_cell_coordinate: Optional[str] = None
    version: Optional[str] = None       # restatement / version info
    # ---- Sprint 12D fact identity (additive) -------------------------
    # A fact is NEVER combined with another fact merely because the label
    # matches: identity distinguishes entity / statement / period / period
    # type / currency / unit-scale / source / version (see identity.py).
    entity: Optional[str] = None        # reporting entity (parent / sub /
                                        # consolidated / standalone)
    statement: Optional[str] = None     # Income Statement / Balance Sheet /
                                        # Cash Flow / Notes ...
    filing_id: Optional[str] = None     # filing identity (restatements /
                                        # amendments)
    apply_scale: bool = False           # True -> value is a scaled magnitude
                                        #        (e.g. 125.4 with scale
                                        #        "millions") that must be
                                        #        normalized to absolute.
    lineage: Optional[str] = None

    # ------------------------------------------------------------------
    def has_value(self) -> bool:
        return self.value is not None

    def numeric_value(self) -> Optional[Decimal]:
        return to_decimal(self.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "canonical_concept": self.canonical_concept,
            "value": float(self.value) if self.value is not None else None,
            "original_value": self.original_value,
            "original_unit": self.original_unit,
            "original_scale": self.original_scale,
            "normalized_value": (
                float(self.normalized_value)
                if self.normalized_value is not None else None
            ),
            "normalized_unit": self.normalized_unit,
            "currency": self.currency,
            "period": self.period,
            "period_type": self.period_type,
            "source": self.source,
            "source_tier": self.source_tier,
            "document_name": self.document_name,
            "page": self.page,
            "evidence": self.evidence,
            "status": self.status,
            "status_reason": self.status_reason,
            "excel_cell_coordinate": self.excel_cell_coordinate,
            "version": self.version,
            "entity": self.entity,
            "statement": self.statement,
            "filing_id": self.filing_id,
            "apply_scale": self.apply_scale,
            "lineage": self.lineage,
        }


# ---------------------------------------------------------------------------
# Pipeline-fact translation (existing FT-E fact dict -> FactNode)
# ---------------------------------------------------------------------------

_PIPELINE_TIER_KEYS = (
    "provenance_tier",
    "source_tier",
)
_PIPELINE_PERIOD_KEYS = ("reporting_period", "period")
_PIPELINE_CURRENCY_KEYS = ("currency_code", "currency")
_PIPELINE_ENTITY_KEYS = ("entity", "reporting_entity", "company")
_PIPELINE_STATEMENT_KEYS = ("statement", "financial_statement")
_PIPELINE_FILING_KEYS = ("filing_id", "filing", "amendment_id")


def from_pipeline_fact(metric: str, fact: Dict[str, Any]) -> FactNode:
    """Translate an existing pipeline fact dict (the shape produced by the
    extractor / evidence resolver / student workspace) into a FactNode.

    The pipeline stores values that are already normalized (e.g. absolute
    USD) together with display-scale metadata. When the fact carries an
    explicit normalized_value it is used as the working value; otherwise
    the raw value is used as-is (apply_scale=False). Callers that want the
    spec-style raw-scaled behavior (125.4 + scale "millions" -> absolute)
    construct FactNode directly with apply_scale=True.
    """
    value = to_decimal(fact.get("normalized_value", fact.get("value")))
    original_value = fact.get("original_value", fact.get("value"))
    tier = None
    for k in _PIPELINE_TIER_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            tier = str(v)
            break
    if tier is None and str(fact.get("source")) == "Calculated":
        tier = "DERIVED"
    period = None
    for k in _PIPELINE_PERIOD_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            period = str(v)
            break
    currency = None
    for k in _PIPELINE_CURRENCY_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            currency = str(v)
            break
    if currency is None:
        u = fact.get("unit")
        if u not in (None, ""):
            currency = str(u)
    entity = None
    for k in _PIPELINE_ENTITY_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            entity = str(v)
            break
    statement = None
    for k in _PIPELINE_STATEMENT_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            statement = str(v)
            break
    filing_id = None
    for k in _PIPELINE_FILING_KEYS:
        v = fact.get(k)
        if v not in (None, ""):
            filing_id = str(v)
            break
    status = status_from_provenance(
        tier,
        fact.get("extraction_state"),
    )
    return FactNode(
        node_id=str(metric),
        canonical_concept=str(metric),
        value=value,
        original_value=original_value,
        original_unit=fact.get("unit") or None,
        original_scale=fact.get("scale") or None,
        normalized_value=value,
        normalized_unit=fact.get("unit") or None,
        currency=currency,
        period=period,
        period_type=fact.get("period_type") or None,
        source=fact.get("source") or fact.get("document_name") or None,
        source_tier=tier,
        document_name=fact.get("document_name") or None,
        page=str(fact.get("page")) if fact.get("page") not in (None, "") else None,
        evidence=fact.get("evidence") or None,
        status=status,
        status_reason=fact.get("status_reason") or fact.get("reason") or None,
        excel_cell_coordinate=fact.get("excel_cell_coordinate") or None,
        version=fact.get("version") or None,
        entity=entity,
        statement=statement,
        filing_id=filing_id,
        apply_scale=False,
    )


# ---------------------------------------------------------------------------
# Fact graph (store)
# ---------------------------------------------------------------------------


class FactGraph:
    """Ordered store of canonical fact nodes keyed by node_id.

    Insertion order is preserved so traversal is deterministic.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, FactNode] = {}

    def add(self, node: FactNode) -> None:
        if node is not None and node.node_id not in self._nodes:
            self._nodes[node.node_id] = node

    def get(self, node_id: str) -> Optional[FactNode]:
        return self._nodes.get(node_id)

    def known_ids(self) -> List[str]:
        return list(self._nodes.keys())

    def has(self, node_id: str) -> bool:
        return node_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {k: v.to_dict() for k, v in self._nodes.items()}


def build_fact_graph(facts: Dict[str, Any]) -> FactGraph:
    """Build a FactGraph from a pipeline-shaped fact map
    ({metric: fact_dict}). Facts that are dicts with usable values become
    nodes; anything else is ignored (never invented)."""
    g = FactGraph()
    for metric, fact in (facts or {}).items():
        if not isinstance(fact, dict):
            continue
        node = from_pipeline_fact(str(metric), fact)
        if node.has_value():
            g.add(node)
        else:
            # Carry the fact so downstream blocking reasons can name it.
            node.status = BLOCKED
            g.add(node)
    return g
