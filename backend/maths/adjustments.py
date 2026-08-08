"""
Financial Timeline Engine
Sprint 12B - Contextual Financial Reasoning Layer
backend/maths/adjustments.py

Deterministic Adjustment / Anomaly Reasoning.

The engine DETECTS adjustment CANDIDATES - it never applies corrections:

  VERIFIED source
        ↓
  ANOMALY DETECTED
        ↓
  REVIEW_REQUIRED
        ↓
  explicit user/student adjustment
        ↓
  STUDENT_INPUT
        ↓
  recalculate graph

The forbidden flow (automatic adjustment VERIFIED -> VERIFIED) never
happens: every candidate is surfaced for review and every applied
adjustment becomes a STUDENT_INPUT analytical node.

IMMUTABILITY: original extracted facts are never mutated. An adjustment
creates a NEW analytical node:

    Original Fact + Adjustment Candidate + User Decision = New Analytical Node

Every adjusted node preserves lineage back to the original facts, the
adjustment and the decision.

Detected conditions (candidates, not corrections):
  cross-statement discrepancy, conflicting source values, duplicate facts,
  incompatible units, period mismatch, unexpected sign, missing dependency,
  zero denominator, suspicious scale mismatch, unsupported accounting
  label, conflicting provenance.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.maths.fact_model import FactGraph, FactNode, build_fact_graph, to_decimal
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.solver import Solver, Solution
from backend.maths.status import (
    BLOCKED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
)
from backend.maths.sufficiency import INSUFFICIENT, SufficiencyEngine
from backend.maths.units import (
    classify_quantity,
    scale_multiplier,
)

# ---------------------------------------------------------------------------
# Status flow constants
# ---------------------------------------------------------------------------

ANOMALY_DETECTED = "ANOMALY_DETECTED"

# Anomaly kinds (deterministic labels)
CROSS_STATEMENT_DISCREPANCY = "CROSS_STATEMENT_DISCREPANCY"
CONFLICTING_SOURCE_VALUES = "CONFLICTING_SOURCE_VALUES"
DUPLICATE_FACT = "DUPLICATE_FACT"
INCOMPATIBLE_UNITS = "INCOMPATIBLE_UNITS"
PERIOD_MISMATCH = "PERIOD_MISMATCH"
UNEXPECTED_SIGN = "UNEXPECTED_SIGN"
MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
SCALE_MISMATCH = "SCALE_MISMATCH"
UNSUPPORTED_LABEL = "UNSUPPORTED_LABEL"
CONFLICTING_PROVENANCE = "CONFLICTING_PROVENANCE"

ANOMALY_KINDS = (
    CROSS_STATEMENT_DISCREPANCY, CONFLICTING_SOURCE_VALUES, DUPLICATE_FACT,
    INCOMPATIBLE_UNITS, PERIOD_MISMATCH, UNEXPECTED_SIGN, MISSING_DEPENDENCY,
    ZERO_DENOMINATOR, SCALE_MISMATCH, UNSUPPORTED_LABEL, CONFLICTING_PROVENANCE,
)

_SEVERITY = {
    CROSS_STATEMENT_DISCREPANCY: "warning",
    CONFLICTING_SOURCE_VALUES: "warning",
    DUPLICATE_FACT: "info",
    INCOMPATIBLE_UNITS: "warning",
    PERIOD_MISMATCH: "info",
    UNEXPECTED_SIGN: "warning",
    MISSING_DEPENDENCY: "warning",
    ZERO_DENOMINATOR: "warning",
    SCALE_MISMATCH: "warning",
    UNSUPPORTED_LABEL: "info",
    CONFLICTING_PROVENANCE: "warning",
}

# Deterministic rule tables -------------------------------------------------
# Concepts that must never be negative (conservative; documented convention).
NON_NEGATIVE_CONCEPTS = frozenset({"Revenue", "Total Assets"})

# Recognized accounting concepts (registry concepts + common FT-E metrics).
KNOWN_CONCEPTS = frozenset({
    # Sprint 12A registry
    "Profit", "Loss", "Gross Profit", "Working Capital", "Asset Turnover",
    "Equity Multiplier", "Profit Margin", "Revenue", "Expenses",
    "Cost of Sales", "Current Assets", "Current Liabilities", "Assets",
    "Equity",
    # DuPont (12B)
    "Net Profit", "Total Assets", "Return on Equity",
    # Reconciliation (12B)
    "Retained Earnings Ending", "Retained Earnings Beginning",
    "Dividends Paid", "Net Profit Cash Flow",
    # Common FT-E metrics
    "ROE", "ROA", "Operating Margin", "Current Ratio", "Debt to Equity",
    "Revenue Growth", "EPS Growth", "CAGR", "EPS", "Net Income",
    "Operating Profit", "Interest", "Tax", "Depreciation", "Amortization",
    "EBITDA", "EBIT", "Gross Margin",
})


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class AnomalyCandidate:
    """One detected condition. A CANDIDATE - never an automatic correction."""

    anomaly_id: str
    kind: str
    target: str
    description: str
    severity: str = "warning"
    node_ids: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    status: str = ANOMALY_DETECTED
    review_required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "kind": self.kind,
            "target": self.target,
            "description": self.description,
            "severity": self.severity,
            "node_ids": list(self.node_ids),
            "details": dict(self.details),
            "status": self.status,
            "review_required": self.review_required,
        }


@dataclass
class AdjustmentRecord:
    """Structured record of one explicit student/user adjustment."""

    adjustment_id: str
    anomaly_id: str
    target: str
    original_values: Dict[str, str] = field(default_factory=dict)
    adjusted_value: str = ""
    decision: str = "ADJUST"
    reason: str = ""
    status: str = STUDENT_INPUT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adjustment_id": self.adjustment_id,
            "anomaly_id": self.anomaly_id,
            "target": self.target,
            "original_values": dict(self.original_values),
            "adjusted_value": self.adjusted_value,
            "decision": self.decision,
            "reason": self.reason,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AdjustmentEngine:
    """Deterministic anomaly detection + immutable adjustment flow."""

    def __init__(self, known_concepts: Optional[Set[str]] = None) -> None:
        self.known_concepts = set(
            known_concepts if known_concepts is not None else KNOWN_CONCEPTS
        )

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_anomalies(self, facts: Any,
                         registries: Optional[List[FormulaRegistry]] = None,
                         ) -> List[AnomalyCandidate]:
        """Deterministic scan of the fact map (or a FactGraph) for
        adjustment candidates.

        Every condition is a CANDIDATE requiring review - nothing is ever
        auto-corrected. The scan order and dedup rules are deterministic.
        Accepts a pipeline fact dict OR a FactGraph (a graph allows several
        nodes to share one canonical_concept, e.g. "Net Profit (IS)" and
        "Net Profit (CF)").
        """
        from backend.maths.dupont import DUPONT_REGISTRY
        from backend.maths.reconciliation import DEFAULT_RECONCILIATION_RULES

        regs = list(registries if registries is not None else [])
        if not regs:
            from backend.maths.formula_registry import default_registry
            regs = [default_registry(), DUPONT_REGISTRY]
            regs.append(DEFAULT_RECONCILIATION_RULES.registry())

        graph = self._as_graph(facts)
        candidates: List[AnomalyCandidate] = []
        seen: Set[Tuple[str, str]] = set()

        def add(kind: str, target: str, description: str,
                node_ids: List[str], details: Optional[Dict[str, Any]] = None,
                dedup_key: Optional[Tuple[str, str]] = None) -> None:
            key = dedup_key or (kind, target)
            if key in seen:
                return
            seen.add(key)
            candidates.append(AnomalyCandidate(
                anomaly_id=f"{kind}:{target}:{len(candidates)}",
                kind=kind, target=target, description=description,
                severity=_SEVERITY.get(kind, "info"),
                node_ids=node_ids, details=details or {},
            ))

        # -- group by canonical concept ---------------------------------
        by_concept: Dict[str, List[FactNode]] = {}
        for nid in graph.known_ids():
            node = graph.get(nid)
            if node is None:
                continue
            by_concept.setdefault(node.canonical_concept, []).append(node)

        for concept in sorted(by_concept):
            nodes = by_concept[concept]
            # unsupported accounting label
            if concept not in self.known_concepts and \
                    not any(reg.is_registered_target(concept) for reg in regs):
                add(
                    UNSUPPORTED_LABEL, concept,
                    f"Concept {concept!r} is not a recognized accounting "
                    "label - verify the source wording.",
                    [n.node_id for n in nodes],
                    {"concept": concept},
                )
            # unexpected sign
            if concept in NON_NEGATIVE_CONCEPTS:
                for n in nodes:
                    if n.value is not None and n.value < 0:
                        add(
                            UNEXPECTED_SIGN, concept,
                            f"{concept} is negative ({n.value}) - expected "
                            "non-negative by accounting convention.",
                            [n.node_id],
                            {"concept": concept, "value": str(n.value)},
                        )
            # same-concept comparisons
            if len(nodes) > 1:
                self._group_anomalies(add, concept, nodes)

        # -- registry-driven conditions ---------------------------------
        for reg in regs:
            for fid in reg.all_ids():
                formula = reg.get(fid)
                if formula is None:
                    continue
                # zero denominators
                for den in formula.denominator_constraints:
                    node = graph.get(den)
                    if node is not None and node.value is not None \
                            and node.value == 0:
                        add(
                            ZERO_DENOMINATOR, formula.target,
                            f"{den} is zero - {formula.target} "
                            f"({formula.formula_id}) is mathematically "
                            "undefined.",
                            [den],
                            {"concept": den, "value": "0",
                             "formula_id": formula.formula_id},
                        )
                # missing dependencies (registered targets only)
                if graph.has(formula.target):
                    continue
                verdict = SufficiencyEngine(reg).analyze(
                    formula.target, graph
                )
                if verdict.state == INSUFFICIENT and verdict.missing:
                    real_missing = [
                        m for m in verdict.missing if m != formula.target
                    ]
                    if real_missing:
                        add(
                            MISSING_DEPENDENCY, formula.target,
                            f"{formula.target} cannot be computed: missing "
                            f"or invalid {', '.join(sorted(real_missing))}.",
                            list(graph.known_ids()),
                            {"missing": sorted(real_missing),
                             "formula_id": formula.formula_id},
                            dedup_key=(MISSING_DEPENDENCY, formula.target),
                        )
        return candidates

    # ------------------------------------------------------------------
    def _group_anomalies(self, add, concept: str,
                         nodes: List[FactNode]) -> None:
        """Deterministic same-concept candidate detection."""
        node_ids = [n.node_id for n in nodes]

        # provenance conflict
        statuses = {n.status for n in nodes}
        if VERIFIED in statuses and statuses & {REVIEW_REQUIRED, BLOCKED}:
            add(
                CONFLICTING_PROVENANCE, concept,
                f"{concept} mixes verified and "
                f"{' / '.join(sorted(statuses & {REVIEW_REQUIRED, BLOCKED}))} "
                "evidence - provenance conflict requires review.",
                node_ids,
                {"statuses": sorted(statuses)},
            )

        # units / scale
        kinds = {
            n.node_id: classify_quantity(n.normalized_unit or n.original_unit)
            for n in nodes
        }
        uniq_kinds = {k for k in kinds.values() if k != "unclassified"}
        if len(uniq_kinds) > 1:
            add(
                INCOMPATIBLE_UNITS, concept,
                f"{concept} carries incompatible unit kinds "
                f"({sorted(uniq_kinds)}) - never combined silently.",
                node_ids,
                {"unit_kinds": sorted(uniq_kinds)},
            )
        unique_scales = {n.original_scale for n in nodes}
        if len(unique_scales) > 1:
            values = {n.node_id: str(self._normalized(n)) for n in nodes}
            add(
                SCALE_MISMATCH, concept,
                f"{concept} has conflicting scale metadata "
                f"({sorted(str(s) for s in unique_scales)}); after "
                f"normalization the values are {values} - suspicious "
                "scale discrepancy, verify before use (never silently "
                "choose one).",
                node_ids,
                {"scales": sorted(str(s) for s in unique_scales),
                 "normalized_values": dict(values)},
            )

        # period type clash (same period label, different period types)
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                if a.period and a.period == b.period and a.period_type \
                        and b.period_type and a.period_type != b.period_type:
                    add(
                        PERIOD_MISMATCH, concept,
                        f"{concept} has period {a.period} with conflicting "
                        f"period types ({a.period_type} vs {b.period_type}).",
                        [a.node_id, b.node_id],
                        {"period": a.period,
                         "period_types": sorted({a.period_type, b.period_type})},
                    )

        # conflicting / duplicate values (same period + currency)
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                if a.value is None or b.value is None:
                    continue
                same_period = (a.period or b.period) and a.period == b.period
                same_currency = (
                    not (a.currency or b.currency) or a.currency == b.currency
                )
                if same_period and same_currency:
                    if a.value != b.value:
                        add(
                            CONFLICTING_SOURCE_VALUES, concept,
                            f"{concept} has conflicting source values "
                            f"({a.value} from {a.source or a.node_id} vs "
                            f"{b.value} from {b.source or b.node_id}) for "
                            "the same period - review, never average.",
                            [a.node_id, b.node_id],
                            {"values": {a.node_id: str(a.value),
                                        b.node_id: str(b.value)}},
                        )
                        if a.source and b.source and a.source != b.source:
                            add(
                                CROSS_STATEMENT_DISCREPANCY, concept,
                                f"{concept} differs between independent "
                                f"statements ({a.source}: {a.value} vs "
                                f"{b.source}: {b.value}) - cross-statement "
                                "discrepancy requires review.",
                                [a.node_id, b.node_id],
                                {"statements": sorted({a.source, b.source}),
                                 "values": {a.node_id: str(a.value),
                                            b.node_id: str(b.value)}},
                            )
                    else:
                        add(
                            DUPLICATE_FACT, concept,
                            f"{concept} appears as an exact duplicate "
                            f"({a.value}) - confirm it is not a stale copy.",
                            [a.node_id, b.node_id],
                            {"value": str(a.value)},
                            dedup_key=(DUPLICATE_FACT, concept),
                        )

    # ------------------------------------------------------------------
    @staticmethod
    def _as_graph(facts: Any) -> FactGraph:
        if isinstance(facts, FactGraph):
            return facts
        return build_fact_graph(facts or {})

    @staticmethod
    def _normalized(node: FactNode) -> Decimal:
        value = node.value
        if value is None:
            return Decimal(0)
        if node.apply_scale and node.original_scale:
            mult = scale_multiplier(node.original_scale)
            if mult is not None:
                value = value * mult
        return value

    # ------------------------------------------------------------------
    # Adjustment flow (immutable)
    # ------------------------------------------------------------------

    def propose_adjustment(self, anomaly: AnomalyCandidate,
                           adjusted_value: Any,
                           facts: Dict[str, Any],
                           decision: str = "ADJUST",
                           reason: str = "") -> Tuple[FactNode, AdjustmentRecord]:
        """Create a STUDENT_INPUT analytical node for an explicit
        adjustment. The original source facts are NEVER mutated."""
        new_value = to_decimal(adjusted_value)
        if new_value is None:
            raise ValueError(
                f"Adjustment value {adjusted_value!r} is not numeric - "
                "no adjustment is created (never guess)."
            )
        graph = self._as_graph(facts)
        original_values: Dict[str, str] = {}
        originals: List[Dict[str, Any]] = []
        for nid in anomaly.node_ids:
            node = graph.get(nid)
            if node is None:
                continue
            original_values[nid] = str(node.value)
            originals.append({
                "node_id": nid,
                "value": str(node.value),
                "unit": node.original_unit or node.normalized_unit,
                "scale": node.original_scale,
                "status": node.status,
                "source": node.source,
                "page": node.page,
                "evidence": node.evidence,
                "period": node.period,
                "currency": node.currency,
            })
        record = AdjustmentRecord(
            adjustment_id=f"ADJ-{anomaly.anomaly_id}",
            anomaly_id=anomaly.anomaly_id,
            target=anomaly.target,
            original_values=original_values,
            adjusted_value=str(new_value),
            decision=decision,
            reason=reason,
            status=STUDENT_INPUT,
        )
        lineage = (
            f"Adjustment {record.adjustment_id} on {anomaly.target}: "
            f"originals {original_values} -> {new_value} "
            f"[{decision}: {reason or 'student adjustment'}] "
            f"(anomaly {anomaly.kind})"
        )
        node = FactNode(
            node_id=f"{anomaly.target} (Adjusted)",
            canonical_concept=anomaly.target,
            value=new_value,
            original_value=str(new_value),
            original_unit=None,
            status=STUDENT_INPUT,
            status_reason=f"{decision}: {reason or 'student adjustment'}",
            source="Student adjustment",
            evidence=lineage,
            lineage=lineage,
        )
        # preserve period/currency of the original where unambiguous
        if len(originals) == 1:
            o = originals[0]
            node.period = o.get("period", None)
            node.currency = o.get("currency", None)
        return node, record

    def build_graph_with_adjustments(self, facts: Any,
                                     adjusted_nodes: List[FactNode]) -> FactGraph:
        """New analytical graph: original facts EXCLUDED for every adjusted
        concept, replaced by the STUDENT_INPUT analytical node keyed by the
        canonical concept. Source facts remain untouched."""
        graph = self._as_graph(facts)
        replaced: Set[str] = {
            n.canonical_concept for n in adjusted_nodes
        }
        out = FactGraph()
        for nid in graph.known_ids():
            node = graph.get(nid)
            if node is None or node.canonical_concept in replaced:
                continue
            out.add(node)
        for node in adjusted_nodes:
            keyed = FactNode(
                node_id=node.canonical_concept,
                canonical_concept=node.canonical_concept,
                value=node.value,
                original_value=node.original_value,
                original_unit=node.original_unit,
                status=node.status,
                status_reason=node.status_reason,
                source=node.source,
                period=node.period,
                currency=node.currency,
                evidence=node.evidence,
                lineage=node.lineage,
            )
            out.add(keyed)
        return out

    def resolve_with_adjustments(self, target: str, facts: Any,
                                 adjusted_nodes: List[FactNode],
                                 registry: Optional[FormulaRegistry] = None,
                                 prefer_cpp: bool = True) -> Solution:
        """Recalculate the graph with the explicit adjustments applied."""
        from backend.maths.formula_registry import default_registry
        reg = registry if registry is not None else default_registry()
        graph = self.build_graph_with_adjustments(facts, adjusted_nodes)
        return Solver(reg, prefer_cpp=prefer_cpp).solve(target, graph)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def detect_anomalies(facts: Any,
                     registries: Optional[List[FormulaRegistry]] = None,
                     ) -> List[AnomalyCandidate]:
    return AdjustmentEngine().detect_anomalies(facts, registries)


def propose_adjustment(anomaly: AnomalyCandidate, adjusted_value: Any,
                       facts: Any, decision: str = "ADJUST",
                       reason: str = "") -> Tuple[FactNode, AdjustmentRecord]:
    return AdjustmentEngine().propose_adjustment(
        anomaly, adjusted_value, facts, decision, reason
    )


def resolve_with_adjustments(target: str, facts: Any,
                             adjusted_nodes: List[FactNode],
                             registry: Optional[FormulaRegistry] = None,
                             prefer_cpp: bool = True) -> Solution:
    return AdjustmentEngine().resolve_with_adjustments(
        target, facts, adjusted_nodes, registry, prefer_cpp
    )
