"""
Platrixa
Sprint 12F - C++ Mathematical Authority (Production Gate)
backend/maths/authority.py

The compiled C++ formula engine is the SOLE production mathematical
authority for financial calculations.

    Student / Document
        -> Python ingestion + normalization
        -> Fact Identity + Evidence Graph
        -> Agentic Orchestration
        -> C++ Mathematical Authority          <-- arithmetic happens here
        -> Decision Graph
        -> Agent Explanation

Python may ingest, normalize, identify facts, construct graph nodes,
resolve evidence, select registered formulas, orchestrate calculations,
pass inputs to C++ and consume structured C++ results. Python NEVER
silently calculates a financial result as a fallback:

    C++ available + supported inputs  -> C++ calculation -> result
    C++ unavailable                   -> BLOCKED / ENGINE_UNAVAILABLE
    C++ unsupported                   -> UNSUPPORTED
    C++ calculation error             -> deterministic failure state

This module exposes the production gate (engine availability, formula
coverage, strict solve / analyze / DuPont entry points). The strict
Solver mode (backend.maths.solver, cpp_authority=True) enforces the
invariant at the arithmetic layer: every atomic financial step is
computed by the C++ engine or the request fails closed.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.formula_engine_cpp import (
    cpp_available,
    is_cpp_covered,
)
from backend.maths.dupont import (
    DUPONT_REGISTRY,
    DuPontAnalysis,
    DuPontEngine,
)
from backend.maths.extended_registry import EXTENDED_REGISTRY
from backend.maths.fact_model import FactGraph, build_fact_graph
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.solver import Solution, Solver
from backend.maths.status import BLOCKED, STATUS_LABELS

# ---------------------------------------------------------------------------
# Authority states (deterministic vocabulary)
# ---------------------------------------------------------------------------

AUTHORITY_CPP = "cpp"                 # computed by the C++ authority
AUTHORITY_UNSUPPORTED = "unsupported"  # not covered by C++ -> UNSUPPORTED
AUTHORITY_UNAVAILABLE = "engine_unavailable"  # no compiled binary

ENGINE_UNAVAILABLE_REASON = (
    "C++ mathematical authority is unavailable (no compiled formula "
    "engine binary). Production calculation is BLOCKED - no Python "
    "fallback is performed."
)


# ---------------------------------------------------------------------------
# Production formula set (extended + DuPont registries, deterministic)
# ---------------------------------------------------------------------------

PRODUCTION_FORMULA_IDS = sorted(
    set(EXTENDED_REGISTRY.all_ids()) | set(DUPONT_REGISTRY.all_ids())
)


def engine_available() -> bool:
    """True when the compiled C++ mathematical authority is available."""
    return bool(cpp_available())


def authority_state(formula_id: str) -> str:
    """Deterministic authority verdict for one registered formula."""
    if not engine_available():
        return AUTHORITY_UNAVAILABLE
    return AUTHORITY_CPP if is_cpp_covered(formula_id) else AUTHORITY_UNSUPPORTED


def coverage() -> Dict[str, str]:
    """{formula_id: authority state} for every production formula."""
    return {fid: authority_state(fid) for fid in PRODUCTION_FORMULA_IDS}


def cpp_covered_formulas() -> List[str]:
    """Production formulas the C++ authority can compute (sorted)."""
    return sorted(fid for fid in PRODUCTION_FORMULA_IDS if is_cpp_covered(fid))


def unsupported_formulas() -> List[str]:
    """Production formulas NOT covered by the C++ authority (sorted).
    With the Sprint 12F coverage extension this is expected to be empty -
    every production formula reaches C++."""
    return sorted(
        fid for fid in PRODUCTION_FORMULA_IDS if not is_cpp_covered(fid)
    )


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------


def _state_result(target: str, state: str, reason: str,
                  status: str = BLOCKED) -> Dict[str, Any]:
    return {
        "target": target,
        "authority_state": state,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "value": None,
        "display_value": "—",
        "formula": "—",
        "reason": reason,
        "inputs": [],
        "sufficiency_state": state,
    }


def _solution_result(target: str, sol: Solution) -> Dict[str, Any]:
    if sol.status == BLOCKED:
        state = sol.sufficiency_state or "BLOCKED"
        if state in ("UNSUPPORTED", "ENGINE_UNAVAILABLE"):
            pass
        else:
            state = AUTHORITY_CPP if engine_available() else AUTHORITY_UNAVAILABLE
        return _state_result(
            target, state, sol.reason or "Calculation is blocked.",
        )
    return {
        "target": target,
        "authority_state": AUTHORITY_CPP,
        "status": sol.status,
        "status_label": sol.status_label or STATUS_LABELS.get(sol.status, sol.status),
        "value": sol.value,
        "display_value": sol.display_value,
        "formula": sol.formula,
        "formula_id": sol.formula_id,
        "reason": sol.reason,
        "inputs": [
            {
                "concept": i.concept,
                "value": float(i.value) if i.value is not None else None,
                "display_value": i.display_value,
                "status": i.status,
                "provenance_tier": i.provenance_tier,
                "source": i.source,
                "page": i.page,
                "evidence": i.evidence,
            }
            for i in (sol.inputs or [])
        ],
        "sufficiency_state": sol.sufficiency_state,
    }


# ---------------------------------------------------------------------------
# Production entry points (strict C++ authority)
# ---------------------------------------------------------------------------


def production_solve(target: str, facts: Any,
                     registry: Optional[FormulaRegistry] = None,
                     ) -> Dict[str, Any]:
    """Strict production solve of ONE target through the C++ authority.

    Python performs no financial arithmetic: every atomic step is computed
    by the C++ engine, or the request fails closed with an explicit state
    (ENGINE_UNAVAILABLE / UNSUPPORTED / BLOCKED).
    """
    if not engine_available():
        return _state_result(
            target, AUTHORITY_UNAVAILABLE, ENGINE_UNAVAILABLE_REASON,
        )
    graph = facts if isinstance(facts, FactGraph) else build_fact_graph(facts or {})
    reg = registry if registry is not None else EXTENDED_REGISTRY
    solver = Solver(reg, prefer_cpp=True, cpp_authority=True)
    sol = solver.solve(target, graph)
    return _solution_result(target, sol)


def production_analyze(request: str,
                       existing_facts: Optional[Dict[str, Any]] = None,
                       source_pools: Optional[Dict[str, Any]] = None,
                       reference: Optional[Dict[str, Any]] = None,
                       coordinate_map: Optional[Dict[str, str]] = None,
                       retrieval_timestamp: str = "",
                       ):
    """Strict agentic analysis: the Agent plans/retrieves/gates/explains,
    the C++ authority calculates. Returns an AgentAnalysis (12E shape)
    with the strict solver wired in."""
    from backend.maths.agentic import AgenticOrchestrator

    if not engine_available():
        # Deterministic fail-closed analysis when the authority is absent.
        from backend.maths.agentic import AgentAnalysis
        from backend.maths.decision_graph import INSUFFICIENT_EVIDENCE

        analysis = AgentAnalysis(
            request=str(request),
            target=str(request),
            resolved=False,
            decision=INSUFFICIENT_EVIDENCE,
            status=BLOCKED,
            workflow_state="BLOCKED",
        )
        analysis.termination_reason = ENGINE_UNAVAILABLE_REASON
        analysis.next_action = "deploy_the_cpp_engine"
        analysis.explanation = {
            "what": f"Cannot calculate {request}.",
            "status": "BLOCKED",
            "status_label": STATUS_LABELS[BLOCKED],
            "why_not": ENGINE_UNAVAILABLE_REASON,
            "next_action": (
                "Deploy the compiled C++ formula engine. Platrixa never "
                "calculates financial results in Python."
            ),
        }
        return analysis
    orchestrator = AgenticOrchestrator(cpp_authority=True)
    return orchestrator.analyze_request(
        request, existing_facts, source_pools, reference,
        coordinate_map, retrieval_timestamp,
    )


def production_dupont(facts_by_period: Dict[str, Dict[str, Any]],
                      ) -> DuPontAnalysis:
    """Strict DuPont decomposition through the C++ authority."""
    if not engine_available():
        return DuPontAnalysis(
            status=BLOCKED,
            reason=ENGINE_UNAVAILABLE_REASON,
        )
    return DuPontEngine(cpp_authority=True).analyze(facts_by_period or {})
