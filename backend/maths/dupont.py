"""
Platrixa
Sprint 12B - Contextual Financial Reasoning Layer
backend/maths/dupont.py

DuPont Decomposition Engine (deterministic, rules-based).

The DuPont identity is resolved through the existing Sprint 12A machinery -
Formula Registry + Accounting Graph + Sufficiency Engine + Solver - NOT as an
isolated hard-coded calculator:

    Return on Equity
     ├── Profit Margin      = Net Profit / Revenue
     ├── Asset Turnover     = Revenue / Total Assets
     └── Equity Multiplier  = Total Assets / Equity

Every node retains its FactStatus and its lineage. A BLOCKED dependency
blocks the downstream calculation; a REVIEW_REQUIRED dependency never
silently becomes VERIFIED.

Percent convention (documented)
-------------------------------
* Profit Margin is registered as a RATIO (fraction) so it can multiply
  safely inside the ROE chain. The final Return on Equity is registered as
  PERCENT and carries the percentage NUMBER (e.g. 40.00 for 40.00%), which
  is the Sprint 12A / legacy `_fmt_percent` convention.
* Percent-kind INPUT facts (unit "%" / "percent" carrying percentage
  numbers such as 20 for 20%) are normalized to fractions in the period
  graph before solving. The original representation is preserved.

Period comparison / contribution analysis
-----------------------------------------
For two consecutive periods the change is decomposed exactly with the
deterministic sequential-replacement identity (order is fixed and
documented: Profit Margin -> Asset Turnover -> Equity Multiplier):

    dROE = dPM * AT1 * EM1 + PM0 * dAT * EM1 + PM0 * AT0 * dEM

Contributions are reported in ROE units (percentage points). The identity
is exact: the three contributions always sum to the observed delta. No
causal claim beyond the mathematical decomposition is ever made; when the
delta cannot be established (a period is BLOCKED) the comparison is BLOCKED
with the reason named, never invented.

Pure module: no Streamlit, no AI, no network. Deterministic. The C++
deterministic engine remains the arithmetic authority where a registered
formula exists; the exact Decimal path (Sprint 12A precision guard) covers
newly registered DuPont formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.exceptions import MathsEngineError
from backend.maths.fact_model import FactGraph, FactNode, build_fact_graph, to_decimal
from backend.maths.formula_registry import FormulaDefinition, FormulaRegistry
from backend.maths.lineage import LineageRecord
from backend.maths.solver import Solver, Solution
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    REVIEW_REQUIRED,
    propagate_statuses,
    weaker,
)
from backend.maths.units import classify_quantity, PERCENT

# ---------------------------------------------------------------------------
# Canonical concepts (Sprint 12B spec names)
# ---------------------------------------------------------------------------

NET_PROFIT = "Net Profit"
REVENUE = "Revenue"
TOTAL_ASSETS = "Total Assets"
EQUITY = "Equity"
PROFIT_MARGIN = "Profit Margin"
ASSET_TURNOVER = "Asset Turnover"
EQUITY_MULTIPLIER = "Equity Multiplier"
RETURN_ON_EQUITY = "Return on Equity"

DUPONT_COMPONENTS = (
    PROFIT_MARGIN,
    ASSET_TURNOVER,
    EQUITY_MULTIPLIER,
)

# Sequential-replacement order used for the deterministic contribution
# analysis. Fixed and documented - the decomposition is exact for this order.
CONTRIBUTION_ORDER = (PROFIT_MARGIN, ASSET_TURNOVER, EQUITY_MULTIPLIER)
CONTRIBUTION_METHOD = (
    "Sequential replacement (order: Profit Margin -> Asset Turnover -> "
    "Equity Multiplier); exact identity "
    "dROE = dPM*AT1*EM1 + PM0*dAT*EM1 + PM0*AT0*dEM."
)

_PERCENT_FACTORS = {
    "percent": Decimal(100),
    "%": Decimal(100),
}


# ---------------------------------------------------------------------------
# DuPont registry (declarative - no engine changes)
# ---------------------------------------------------------------------------


def build_dupont_registry() -> FormulaRegistry:
    """The DuPont chain as registered, versioned formulas.

    Profit Margin is RATIO-kind (fraction) so it composes multiplicatively
    inside ROE. Return on Equity is PERCENT-kind and produces the percentage
    NUMBER (Sprint 12A / legacy convention).
    """
    reg = FormulaRegistry()
    reg.register(FormulaDefinition(
        formula_id="DUPONT_PROFIT_MARGIN",
        target=PROFIT_MARGIN,
        description="DuPont Profit Margin = Net Profit / Revenue (fraction)",
        expression="Net Profit / Revenue",
        dependencies=[NET_PROFIT, REVENUE],
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[REVENUE],
        version="1.0",
        source_ref="DuPont identity: Profit Margin = Net Profit / Revenue",
    ))
    reg.register(FormulaDefinition(
        formula_id="DUPONT_ASSET_TURNOVER",
        target=ASSET_TURNOVER,
        description="DuPont Asset Turnover = Revenue / Total Assets",
        expression="Revenue / Total Assets",
        dependencies=[REVENUE, TOTAL_ASSETS],
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[TOTAL_ASSETS],
        version="1.0",
        source_ref="DuPont identity: Asset Turnover = Revenue / Total Assets",
    ))
    reg.register(FormulaDefinition(
        formula_id="DUPONT_EQUITY_MULTIPLIER",
        target=EQUITY_MULTIPLIER,
        description="DuPont Equity Multiplier = Total Assets / Equity",
        expression="Total Assets / Equity",
        dependencies=[TOTAL_ASSETS, EQUITY],
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[EQUITY],
        version="1.0",
        source_ref="DuPont identity: Equity Multiplier = Total Assets / Equity",
    ))
    reg.register(FormulaDefinition(
        formula_id="DUPONT_ROE",
        target=RETURN_ON_EQUITY,
        description="DuPont ROE = Profit Margin x Asset Turnover x Equity Multiplier",
        expression="Profit Margin * Asset Turnover * Equity Multiplier",
        dependencies=[PROFIT_MARGIN, ASSET_TURNOVER, EQUITY_MULTIPLIER],
        unit_kind="percent",
        period_mode="same",
        version="1.0",
        source_ref="DuPont identity: ROE = PM x AT x EM",
    ))
    return reg


DUPONT_REGISTRY = build_dupont_registry()


# ---------------------------------------------------------------------------
# Percent-fact normalization (documented, deterministic; originals preserved)
# ---------------------------------------------------------------------------


def _build_period_graph(facts: Dict[str, Any]) -> FactGraph:
    """Build the per-period fact graph.

    Percent-kind input facts (percentage numbers such as 20 for 20%) are
    normalized to fractions for the DuPont chain. Original values are
    preserved in original_value / original_unit; the source facts are never
    mutated.
    """
    base = build_fact_graph(facts)
    out = FactGraph()
    for node_id in base.known_ids():
        node = base.get(node_id)
        if (node is not None and node.value is not None
                and classify_quantity(
                    node.normalized_unit or node.original_unit
                ) == PERCENT):
            copy = FactNode(
                node_id=node.node_id,
                canonical_concept=node.canonical_concept,
                value=node.value / Decimal(100),
                original_value=node.original_value,
                original_unit=node.original_unit,
                original_scale=node.original_scale,
                normalized_value=(
                    node.normalized_value / Decimal(100)
                    if node.normalized_value is not None else None
                ),
                normalized_unit=node.normalized_unit,
                currency=node.currency,
                period=node.period,
                period_type=node.period_type,
                source=node.source,
                source_tier=node.source_tier,
                document_name=node.document_name,
                page=node.page,
                evidence=node.evidence,
                status=node.status,
                status_reason=node.status_reason,
                excel_cell_coordinate=node.excel_cell_coordinate,
                version=node.version,
                apply_scale=node.apply_scale,
                lineage=node.lineage,
            )
            out.add(copy)
        else:
            out.add(node)
    return out


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class DuPontComponent:
    """One DuPont node: value, six-tier status, and its own lineage inputs."""

    concept: str
    value: Optional[Decimal] = None
    display_value: str = "—"
    status: str = BLOCKED
    reason: Optional[str] = None
    formula_id: Optional[str] = None
    inputs: List[Dict[str, Any]] = field(default_factory=list)
    sufficiency_state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "status": self.status,
            "reason": self.reason,
            "formula_id": self.formula_id,
            "inputs": list(self.inputs),
            "sufficiency_state": self.sufficiency_state,
        }


@dataclass
class DuPontPeriod:
    """One period's resolved DuPont tree."""

    period: str
    roe: DuPontComponent
    components: Dict[str, DuPontComponent] = field(default_factory=dict)
    status: str = BLOCKED
    reason: Optional[str] = None
    lineage: Optional[LineageRecord] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "roe": self.roe.to_dict(),
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "status": self.status,
            "reason": self.reason,
            "lineage": self.lineage.to_dict() if self.lineage else None,
        }


@dataclass
class DuPontContribution:
    """Deterministic contribution of one component to the ROE delta."""

    component: str
    change: Optional[Decimal] = None          # native units (PM fraction, ratio)
    contribution: Optional[Decimal] = None    # ROE units (percentage points)
    display: str = "—"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "change": float(self.change) if self.change is not None else None,
            "contribution": (
                float(self.contribution) if self.contribution is not None else None
            ),
            "display": self.display,
        }


@dataclass
class DuPontComparison:
    """Deterministic change/delta analysis between two periods."""

    previous_period: str
    current_period: str
    previous_roe: DuPontComponent
    current_roe: DuPontComponent
    absolute_change: Optional[Decimal] = None       # ROE units (percentage points)
    percentage_change: Optional[Decimal] = None     # None when previous == 0
    percentage_change_note: str = ""
    component_changes: Dict[str, Optional[Decimal]] = field(default_factory=dict)
    contributions: List[DuPontContribution] = field(default_factory=list)
    largest_contributor: Optional[str] = None
    status: str = BLOCKED
    reason: Optional[str] = None
    method: str = CONTRIBUTION_METHOD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "previous_period": self.previous_period,
            "current_period": self.current_period,
            "previous_roe": self.previous_roe.to_dict(),
            "current_roe": self.current_roe.to_dict(),
            "absolute_change": (
                float(self.absolute_change)
                if self.absolute_change is not None else None
            ),
            "percentage_change": (
                float(self.percentage_change)
                if self.percentage_change is not None else None
            ),
            "percentage_change_note": self.percentage_change_note,
            "component_changes": {
                k: (float(v) if v is not None else None)
                for k, v in self.component_changes.items()
            },
            "contributions": [c.to_dict() for c in self.contributions],
            "largest_contributor": self.largest_contributor,
            "status": self.status,
            "reason": self.reason,
            "method": self.method,
        }


@dataclass
class DuPontAnalysis:
    """Full analysis: every resolved period plus consecutive comparisons."""

    periods: List[DuPontPeriod] = field(default_factory=list)
    comparisons: List[DuPontComparison] = field(default_factory=list)
    status: str = BLOCKED
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "periods": [p.to_dict() for p in self.periods],
            "comparisons": [c.to_dict() for c in self.comparisons],
            "status": self.status,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DuPontEngine:
    """Deterministic DuPont resolver over the Sprint 12A machinery."""

    def __init__(self, registry: Optional[FormulaRegistry] = None,
                 prefer_cpp: bool = True,
                 cpp_authority: bool = False) -> None:
        self.registry = registry if registry is not None else DUPONT_REGISTRY
        self.prefer_cpp = prefer_cpp
        self.cpp_authority = cpp_authority

    # ------------------------------------------------------------------
    def _component_from_solution(self, concept: str,
                                 sol: Solution) -> DuPontComponent:
        return DuPontComponent(
            concept=concept,
            value=sol.value,
            display_value=sol.display_value,
            status=sol.status,
            reason=sol.reason,
            formula_id=sol.formula_id,
            inputs=[
                {
                    "concept": i.concept,
                    "value": float(i.value) if i.value is not None else None,
                    "display_value": i.display_value,
                    "status": i.status,
                    "provenance_tier": i.provenance_tier,
                    "page": i.page,
                    "evidence": i.evidence,
                }
                for i in sol.inputs
            ],
            sufficiency_state=sol.sufficiency_state,
        )

    def solve_period(self, period: str, facts: Dict[str, Any]) -> DuPontPeriod:
        """Resolve the full DuPont tree for one period.

        Every node keeps its six-tier status: BLOCKED dependencies block the
        downstream node; REVIEW_REQUIRED never silently becomes VERIFIED.
        """
        graph = _build_period_graph(facts or {})
        solver = Solver(self.registry, prefer_cpp=self.prefer_cpp,
                        cpp_authority=self.cpp_authority)
        roe_sol = solver.solve(RETURN_ON_EQUITY, graph)
        component_solutions: Dict[str, Solution] = {}
        for concept in DUPONT_COMPONENTS:
            component_solutions[concept] = solver.solve(concept, graph)

        components = {
            concept: self._component_from_solution(
                concept, component_solutions[concept]
            )
            for concept in DUPONT_COMPONENTS
        }
        roe = self._component_from_solution(RETURN_ON_EQUITY, roe_sol)
        statuses = [roe.status] + [c.status for c in components.values()]
        status = propagate_statuses(statuses)
        reason = roe.reason or next(
            (c.reason for c in components.values() if c.reason), None
        )
        return DuPontPeriod(
            period=period,
            roe=roe,
            components=components,
            status=status,
            reason=reason,
            lineage=roe_sol.lineage,
        )

    # ------------------------------------------------------------------
    def analyze(self, facts_by_period: Dict[str, Dict[str, Any]]) -> DuPontAnalysis:
        """Deterministic multi-period analysis.

        Periods are processed in sorted order. Consecutive pairs produce
        change/delta comparisons with the sequential-replacement contribution
        analysis.
        """
        periods = sorted((facts_by_period or {}).keys())
        if not periods:
            return DuPontAnalysis(
                status=BLOCKED,
                reason="No periods provided - cannot analyze DuPont decomposition.",
            )
        results = [
            self.solve_period(p, facts_by_period[p]) for p in periods
        ]
        comparisons = [
            self._compare(results[i], results[i + 1])
            for i in range(len(results) - 1)
        ]
        all_statuses = [r.status for r in results] + [c.status for c in comparisons]
        return DuPontAnalysis(
            periods=results,
            comparisons=comparisons,
            status=propagate_statuses(all_statuses),
            reason=next(
                (r.reason for r in results if r.reason),
                next((c.reason for c in comparisons if c.reason), None),
            ),
        )

    # ------------------------------------------------------------------
    def _compare(self, prev: DuPontPeriod,
                 cur: DuPontPeriod) -> DuPontComparison:
        """Deterministic delta + contribution analysis for two periods.

        Contribution identity (exact, sequential replacement):
            dROE = dPM*AT1*EM1 + PM0*dAT*EM1 + PM0*AT0*dEM
        Contributions are reported in ROE units (percentage points).

        If the delta cannot be mathematically established the comparison is
        BLOCKED (or REVIEW_REQUIRED when a component needs review) with the
        reason named - never invented.
        """
        comp = DuPontComparison(
            previous_period=prev.period,
            current_period=cur.period,
            previous_roe=prev.roe,
            current_roe=cur.roe,
        )
        if BLOCKED in (prev.roe.status, cur.roe.status):
            blocked = [p for p, r in ((prev.period, prev.roe), (cur.period, cur.roe))
                       if r.status == BLOCKED]
            comp.status = BLOCKED
            comp.reason = (
                f"ROE for {', '.join(blocked)} is BLOCKED - the change "
                "cannot be established from verified evidence."
            )
            return comp
        if REVIEW_REQUIRED in (prev.roe.status, cur.roe.status):
            comp.status = REVIEW_REQUIRED
            comp.reason = (
                "One or both ROE values require review - the change is "
                "reported but never presented as verified."
            )
            return comp

        try:
            pm0 = prev.components[PROFIT_MARGIN].value
            pm1 = cur.components[PROFIT_MARGIN].value
            at0 = prev.components[ASSET_TURNOVER].value
            at1 = cur.components[ASSET_TURNOVER].value
            em0 = prev.components[EQUITY_MULTIPLIER].value
            em1 = cur.components[EQUITY_MULTIPLIER].value
            roe0 = prev.roe.value
            roe1 = cur.roe.value
            if any(v is None for v in (pm0, pm1, at0, at1, em0, em1, roe0, roe1)):
                raise ValueError("A DuPont component is missing a value.")
        except (ValueError, TypeError) as exc:
            comp.status = BLOCKED
            comp.reason = f"Cannot establish the change: {exc}"
            return comp

        dpm = pm1 - pm0
        dat = at1 - at0
        dem = em1 - em0
        c_pm = dpm * at1 * em1 * Decimal(100)
        c_at = pm0 * dat * em1 * Decimal(100)
        c_em = pm0 * at0 * dem * Decimal(100)
        d_roe = roe1 - roe0

        comp.absolute_change = d_roe
        if roe0 != 0:
            comp.percentage_change = d_roe / abs(roe0) * Decimal(100)
        else:
            comp.percentage_change = None
            comp.percentage_change_note = (
                "Percentage change is undefined (previous ROE is zero)."
            )
        comp.component_changes = {
            PROFIT_MARGIN: dpm,
            ASSET_TURNOVER: dat,
            EQUITY_MULTIPLIER: dem,
        }
        comp.contributions = [
            DuPontContribution(
                component=PROFIT_MARGIN, change=dpm, contribution=c_pm,
                display=format(c_pm, "f"),
            ),
            DuPontContribution(
                component=ASSET_TURNOVER, change=dat, contribution=c_at,
                display=format(c_at, "f"),
            ),
            DuPontContribution(
                component=EQUITY_MULTIPLIER, change=dem, contribution=c_em,
                display=format(c_em, "f"),
            ),
        ]
        comp.largest_contributor = max(
            (PROFIT_MARGIN, ASSET_TURNOVER, EQUITY_MULTIPLIER),
            key=lambda c: abs(
                next(x.contribution for x in comp.contributions if x.component == c)
                or Decimal(0)
            ),
        )
        comp.status = DERIVED
        return comp


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def analyze_dupont(facts_by_period: Dict[str, Dict[str, Any]],
                   prefer_cpp: bool = True,
                   cpp_authority: bool = False) -> DuPontAnalysis:
    """Convenience entry point."""
    return DuPontEngine(
        prefer_cpp=prefer_cpp, cpp_authority=cpp_authority,
    ).analyze(facts_by_period)
