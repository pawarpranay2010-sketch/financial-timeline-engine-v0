"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/adapter.py

Thin adapter between the existing Formula Engine (backend/formula_engine.py)
and the new deterministic maths engine (this package).

The existing formula_engine.py remains the stable public interface; it
delegates ONLY when a new Sprint-12 formula/concept is explicitly
registered with the maths engine. This adapter translates between:

    existing Formula Engine input/output structures
        (pipeline fact dicts {metric: {value, source, ...}} and the
         legacy result dict shape)
    new GraphNode / FormulaRegistry structures

Compatibility contract
----------------------
* existing formulas            -> existing proven implementation (never routed here)
* new registered formulas      -> maths adapter -> deterministic graph engine
* existing callers never need to know backend/maths exists.

API
---
    can_solve_with_graph(metric_key)          -> bool
    calculate_with_graph(metric_key, financial_data, context) -> legacy-shaped
                                                  result dict (or None when the
                                                  metric is not a registered
                                                  Sprint-12 concept)
    get_graph_lineage(metric_key, financial_data, context) -> lineage dict
    get_graph_status(metric_key, financial_data, context)  -> six-tier status

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.evidence_resolver import PROVENANCE_TIER
from backend.maths.fact_model import build_fact_graph
from backend.maths.formula_registry import default_registry
from backend.maths.solver import Solver, format_value
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
)

# Concepts explicitly delegated from the existing Formula Engine to the
# maths engine. Only zero-collision concepts are listed - deliberately
# excludes workspace-ambiguous labels (Gross Profit, Working Capital,
# Cost of Sales) whose REVIEW_REQUIRED semantics live in student_workspace.
DELEGATED_CONCEPTS = frozenset({
    "Profit", "Expenses", "Loss",
    "Asset Turnover", "Equity Multiplier",
})

# Six-tier -> legacy status vocabulary (used only when the adapter result
# is handed back to existing formula_engine consumers).
_SIX_TO_LEGACY = {
    VERIFIED: "reported",
    DERIVED: "derived",
    RECONCILED: "derived",
    STUDENT_INPUT: "external_derived",
    REVIEW_REQUIRED: "blocked",
    BLOCKED: "blocked",
}

_STATUS_LABEL_LEGACY = {
    "reported": "🟢 Reported & Verified",
    "derived": "🔵 Derived from Verified Inputs",
    "external_derived": "🟣 External + Derived",
    "blocked": "🔴 Blocked",
    "unanalyzed": "⚪ Unanalyzed",
}

_PROVENANCE_LABELS = {
    PROVENANCE_TIER.DOCUMENT: "Document",
    PROVENANCE_TIER.APPENDIX: "Appendix",
    PROVENANCE_TIER.REGULATORY_API: "Regulatory API",
    PROVENANCE_TIER.DERIVED: "Derived",
    PROVENANCE_TIER.EXTERNAL_DERIVED: "External + Derived",
    PROVENANCE_TIER.BLOCKED: "Blocked",
    PROVENANCE_TIER.UNANALYZED: "Unanalyzed",
}


def _is_delegated(metric_key: Optional[str]) -> bool:
    return str(metric_key or "") in DELEGATED_CONCEPTS


def can_solve_with_graph(metric_key: Optional[str]) -> bool:
    """True when this concept is explicitly registered for delegation with
    the maths engine (either as a formula target or a reverse-solvable
    variable)."""
    if not _is_delegated(metric_key):
        return False
    reg = default_registry()
    return (
        reg.is_registered_target(str(metric_key))
        or reg.can_reverse_solve(str(metric_key))
    )


def _context_facts(financial_data: Dict[str, Any],
                   context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge pipeline facts with context primary_facts (mirrors the
    existing engine's primary_facts merging)."""
    context = context or {}
    extra = dict(context.get("primary_facts") or {})
    return {**extra, **dict(financial_data or {})}


def _legacy_result(metric_key: str, solution) -> Optional[Dict[str, Any]]:
    """Translate a maths Solution into the existing Formula Engine result
    dict shape so formula_engine.py can return it unchanged."""
    if solution is None:
        return None
    legacy_status = _SIX_TO_LEGACY.get(solution.status, "blocked")
    result: Dict[str, Any] = {
        "metric_key": metric_key,
        "display_name": str(
            solution.formula_id or metric_key
        ),
        "value": float(solution.value) if solution.value is not None else None,
        "display_value": solution.display_value,
        "unit": solution.unit_kind,
        "status": legacy_status,
        "status_label": _STATUS_LABEL_LEGACY[legacy_status],
        "formula": solution.formula or "—",
        "inputs": [],
        "input_keys": [],
        "calculation_steps": [],
        "provenance": (
            PROVENANCE_TIER.BLOCKED
            if solution.status == BLOCKED
            else PROVENANCE_TIER.DERIVED
        ),
        "reason": solution.reason,
        "error": solution.reason if solution.status in (BLOCKED, REVIEW_REQUIRED) else None,
        "lineage": solution.lineage.render_text() if solution.lineage else "",
        "maths_status": solution.status,
        "maths_sufficiency": solution.sufficiency_state,
    }
    for i in solution.inputs:
        result["inputs"].append({
            "metric": i.concept,
            "value": float(i.value) if i.value is not None else None,
            "display_value": i.display_value,
            "provenance_tier": i.provenance_tier,
            "tier_label": _PROVENANCE_LABELS.get(
                str(i.provenance_tier).upper(), i.provenance_tier
            ),
            "page": i.page,
            "source": i.source,
            "evidence": i.evidence,
            "unit": None,
            "scale": None,
            "status": i.status,
        })
    result["input_keys"] = [i.concept for i in solution.inputs]
    for step in solution.intermediates:
        result["calculation_steps"].append(
            f"{step.concept} = {step.display_value} "
            f"[{step.formula_id}, {step.status}]"
        )
    return result


def calculate_with_graph(
    metric_key: str,
    financial_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Delegate ONE new-registry metric to the maths engine.

    Returns a legacy-shaped result dict (identical in structure to
    calculate_metric) or None when the metric is NOT a delegated
    Sprint-12 concept (the caller keeps its existing behavior).
    """
    if not _is_delegated(metric_key):
        return None
    facts = build_fact_graph(_context_facts(financial_data, context))
    solver = Solver(default_registry(), prefer_cpp=True)
    solution = solver.solve(str(metric_key), facts)
    return _legacy_result(str(metric_key), solution)


def get_graph_lineage(
    metric_key: str,
    financial_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Structured lineage for a delegated metric (dict form)."""
    if not _is_delegated(metric_key):
        return {
            "target": metric_key,
            "status": "UNANALYZED",
            "reason": "Not a registered maths-engine concept.",
        }
    facts = build_fact_graph(_context_facts(financial_data, context))
    solver = Solver(default_registry(), prefer_cpp=True)
    solution = solver.solve(str(metric_key), facts)
    return solution.to_dict()


def get_graph_status(
    metric_key: str,
    financial_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Six-tier status of a delegated metric."""
    if not _is_delegated(metric_key):
        return "UNANALYZED"
    facts = build_fact_graph(_context_facts(financial_data, context))
    solver = Solver(default_registry(), prefer_cpp=True)
    solution = solver.solve(str(metric_key), facts)
    return solution.status


# ---------------------------------------------------------------------------
# Direct maths-engine entry (used by the new-engine tests and by future
# FT-E integration phases)
# ---------------------------------------------------------------------------


def solve_concept(
    target: str,
    facts: Dict[str, Any],
    prefer_cpp: bool = True,
) -> Dict[str, Any]:
    """Solve one concept against a pipeline-shaped fact map. Returns the
    full maths Solution dict (six-tier statuses preserved)."""
    graph = build_fact_graph(facts)
    solver = Solver(default_registry(), prefer_cpp=prefer_cpp)
    return solver.solve(str(target), graph).to_dict()


def display_value_for(value: Any, kind: str, precision: int = 2) -> str:
    """Display-format a numeric value (public helper for tests/UI)."""
    from backend.maths.fact_model import to_decimal
    d = to_decimal(value)
    if d is None:
        return "—"
    return format_value(d, kind, precision)
