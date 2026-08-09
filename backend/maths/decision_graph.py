"""
Financial Timeline Engine
Sprint 12C - Evidence-Aware Decision Graph & Production Integration
backend/maths/decision_graph.py

Deterministic decision layer above the Sprint 12A calculation graph and
the Sprint 12B reasoning layer.

A DecisionNode reports deterministic analytical state - never subjective
financial advice. Supported conclusions:

    METRIC_AVAILABLE         fact directly supported by evidence
    METRIC_DERIVED           computed through registered formulas
    METRIC_RECONCILED        obtained through a documented reconciliation
    METRIC_STUDENT_INPUT     produced by an explicit student adjustment
    EVIDENCE_CONFLICT        conflicting sources / ambiguous derivation
    RECONCILIATION_REQUIRED  cross-statement variance needs review
    ADJUSTMENT_REQUIRED      an anomaly candidate needs a decision
    METRIC_BLOCKED           a required dependency is unavailable/invalid
    INSUFFICIENT_EVIDENCE    no registered relationship can produce it

The decision is a PURE, deterministic function of:

    solution status/value   (from the 12A Solver)
    provenance verdict      (from the 12C ProvenanceGate)
    anomaly candidates      (from the 12B AdjustmentEngine)
    reconciliation results  (from the 12B ReconciliationEngine)

No LLM generation. No guessing. The decision layer can explain, classify
and construct graph relationships - the C++ deterministic maths engine
remains the mathematical authority.

Every decision exposes the Sprint 12C evidence-aware payload:

    {target, status, value, formula, dependencies, lineage, evidence,
     blocking_reason}

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.evidence import (
    EvidenceTrace,
    TIER_1_DOCUMENT,
    TIER_2_APPENDIX,
    TIER_3_REGULATORY_API,
    tier_of,
    trace_leaves,
)
from backend.maths.excel_compiler import (
    DEFAULT_COMPILER,
    ExcelFormula,
    ExcelLineageCompiler,
    render_excel_lineage_text,
)
from backend.maths.fact_model import FactGraph, build_fact_graph
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.provenance import (
    GATE_BLOCKED,
    GATE_PASS,
    GATE_REVIEW,
    ProvenanceGate,
    ProvenanceVerdict,
)
from backend.maths.solver import Solver, Solution
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
)
from backend.maths.sufficiency import INSUFFICIENT

# ---------------------------------------------------------------------------
# Decision states
# ---------------------------------------------------------------------------

METRIC_AVAILABLE = "METRIC_AVAILABLE"
METRIC_DERIVED = "METRIC_DERIVED"
METRIC_RECONCILED = "METRIC_RECONCILED"
METRIC_STUDENT_INPUT = "METRIC_STUDENT_INPUT"
EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
ADJUSTMENT_REQUIRED = "ADJUSTMENT_REQUIRED"
METRIC_BLOCKED = "METRIC_BLOCKED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

DECISION_STATES = (
    METRIC_AVAILABLE, METRIC_DERIVED, METRIC_RECONCILED,
    METRIC_STUDENT_INPUT, EVIDENCE_CONFLICT, RECONCILIATION_REQUIRED,
    ADJUSTMENT_REQUIRED, METRIC_BLOCKED, INSUFFICIENT_EVIDENCE,
)

# Anomaly kinds that represent evidence conflicts (deterministic table).
_CONFLICT_ANOMALY_KINDS = frozenset({
    "CROSS_STATEMENT_DISCREPANCY",
    "CONFLICTING_SOURCE_VALUES",
    "SCALE_MISMATCH",
    "CONFLICTING_PROVENANCE",
})

# Deterministic confidence-state mapping (1:1 with decision - a decision
# never claims more certainty than its weakest dependency).
CONFIDENCE_BY_DECISION = {
    METRIC_AVAILABLE: "verified",
    METRIC_DERIVED: "derived",
    METRIC_RECONCILED: "reconciled",
    METRIC_STUDENT_INPUT: "student_input",
    EVIDENCE_CONFLICT: "review_required",
    RECONCILIATION_REQUIRED: "review_required",
    ADJUSTMENT_REQUIRED: "review_required",
    METRIC_BLOCKED: "blocked",
    INSUFFICIENT_EVIDENCE: "insufficient",
}

# Deterministic next-action mapping.
NEXT_ACTION_BY_DECISION = {
    METRIC_AVAILABLE: "none",
    METRIC_DERIVED: "none",
    METRIC_RECONCILED: "none",
    METRIC_STUDENT_INPUT: "none",
    EVIDENCE_CONFLICT: "review_conflicting_evidence",
    RECONCILIATION_REQUIRED: "review_reconciliation",
    ADJUSTMENT_REQUIRED: "decide_adjustment",
    METRIC_BLOCKED: "provide_missing_evidence",
    INSUFFICIENT_EVIDENCE: "provide_evidence_or_register_relationship",
}

_TIER_LABEL_BY_NUMBER = {
    TIER_1_DOCUMENT: "DOCUMENT",
    TIER_2_APPENDIX: "APPENDIX",
    TIER_3_REGULATORY_API: "REGULATORY_API",
}


def confidence_for(decision: str) -> str:
    """Deterministic confidence state for a decision."""
    return CONFIDENCE_BY_DECISION.get(decision, "blocked")


def next_action_for(decision: str) -> str:
    """Deterministic next action for a decision."""
    return NEXT_ACTION_BY_DECISION.get(decision, "none")


def source_tier_for(evidence) -> str:
    """Strongest approved tier present among the evidence leaves
    (Tier 1 > Tier 2 > Tier 3). '—' when no leaves exist."""
    if evidence is None or not evidence.leaves:
        return "—"
    numbers = [tier_of(l.tier) for l in evidence.leaves]
    valid = [n for n in numbers
             if n in (TIER_1_DOCUMENT, TIER_2_APPENDIX,
                      TIER_3_REGULATORY_API)]
    if not valid:
        return "—"
    return _TIER_LABEL_BY_NUMBER[min(valid)]


# ---------------------------------------------------------------------------
# Decision node
# ---------------------------------------------------------------------------


@dataclass
class DecisionNode:
    """One deterministic conclusion about one metric."""

    node_id: str
    target: str
    decision: str = METRIC_BLOCKED
    status: str = BLOCKED
    confidence_state: str = "blocked"
    value: Optional[Decimal] = None
    display_value: str = "—"
    reason: str = ""
    blocking_reason: Optional[str] = None
    formula_id: Optional[str] = None
    formula: str = "—"
    dependencies: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    sufficiency_state: str = ""
    source_tier: str = "—"
    provenance_verdict: Optional[ProvenanceVerdict] = None
    evidence: Optional[EvidenceTrace] = None
    excel_formula: Optional[ExcelFormula] = None
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    reconciliation: List[Dict[str, Any]] = field(default_factory=list)
    reconciliation_refs: List[str] = field(default_factory=list)
    adjustment_refs: List[str] = field(default_factory=list)
    explanation: Dict[str, Any] = field(default_factory=dict)
    next_action: str = "none"
    lineage_text: str = ""

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "target": self.target,
            "decision": self.decision,
            "status": self.status,
            "confidence_state": self.confidence_state,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "reason": self.reason,
            "blocking_reason": self.blocking_reason,
            "formula_id": self.formula_id,
            "formula": self.formula,
            "dependencies": list(self.dependencies),
            "missing": list(self.missing),
            "sufficiency_state": self.sufficiency_state,
            "source_tier": self.source_tier,
            "provenance_verdict": (
                self.provenance_verdict.to_dict()
                if self.provenance_verdict else None
            ),
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "excel_formula": (
                self.excel_formula.to_dict()
                if self.excel_formula else None
            ),
            "anomalies": list(self.anomalies),
            "reconciliation": list(self.reconciliation),
            "reconciliation_refs": list(self.reconciliation_refs),
            "adjustment_refs": list(self.adjustment_refs),
            "explanation": dict(self.explanation),
            "next_action": self.next_action,
            "lineage_text": self.lineage_text,
        }

    def to_payload(self) -> Dict[str, Any]:
        """Sprint 12C section 11 progressive agent representation.

        The maths layer exposes structured state only; the Agent UI may
        explain this payload, but the maths layer never generates
        free-form conclusions.
        """
        evidence_leaves = (
            [l.to_dict() for l in self.evidence.leaves]
            if self.evidence else []
        )
        lineage_steps = list(self.evidence.chain) if self.evidence else []
        return {
            "target": self.target,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "status": self.status,
            "decision": self.decision,
            "confidence_state": self.confidence_state,
            "formula": self.formula,
            "formula_id": self.formula_id,
            "dependencies": list(self.dependencies),
            "missing": list(self.missing),
            "lineage": lineage_steps,
            "evidence": evidence_leaves,
            "provenance": (
                self.provenance_verdict.to_dict()
                if self.provenance_verdict else None
            ),
            "anomalies": list(self.anomalies),
            "reconciliation": list(self.reconciliation),
            "source_tier": self.source_tier,
            "explanation": dict(self.explanation),
            "next_action": self.next_action,
            "blocking_reason": self.blocking_reason,
            "reason": self.reason,
            "sufficiency_state": self.sufficiency_state,
            "excel_formula": (
                self.excel_formula.formula
                if self.excel_formula else None
            ),
        }


# ---------------------------------------------------------------------------
# Deterministic decision function
# ---------------------------------------------------------------------------


def decide_state(*, solution: Solution,
                 provenance_verdict: ProvenanceVerdict,
                 conflict_anomalies: List[Any],
                 adjustment_anomalies: List[Any],
                 reconciliation_results: List[Any],
                 registry: FormulaRegistry,
                 ) -> Dict[str, Any]:
    """Pure deterministic mapping of analytical state to a decision.

    Evaluation order (fixed and documented):
      1. provenance gate blocked             -> METRIC_BLOCKED (gate)
      2. evidence-conflict anomalies present -> EVIDENCE_CONFLICT
      3. reconciliation review required      -> RECONCILIATION_REQUIRED
      4. adjustment candidate present        -> ADJUSTMENT_REQUIRED
      5. solution status                     -> direct mapping

    Returns a dict with keys: decision, reason, blocking_reason.
    """
    # 1. provenance gate
    if provenance_verdict.verdict == GATE_BLOCKED:
        return {
            "decision": METRIC_BLOCKED,
            "reason": "Provenance integrity gate blocked the evidence: "
                      + "; ".join(provenance_verdict.reasons[:2]),
            "blocking_reason": "invalid provenance",
        }

    # 2. reconciliation failures take precedence (blocked or reviewed)
    recon_failed = [r for r in reconciliation_results
                    if r.status in (REVIEW_REQUIRED, BLOCKED)]
    if recon_failed:
        r = recon_failed[0]
        return {
            "decision": RECONCILIATION_REQUIRED,
            "reason": r.reason or (
                f"{solution.target} requires reconciliation review."
            ),
            "blocking_reason": None,
        }

    # 3. a conflict ABOUT THIS METRIC itself (even when the solver is
    #    BLOCKED) is EVIDENCE_CONFLICT - there IS evidence, it conflicts,
    #    and nothing is silently chosen. Downstream blocking is handled
    #    by the solver's status propagation.
    direct_conflicts = [a for a in conflict_anomalies
                        if getattr(a, "target", None) == solution.target]
    if direct_conflicts:
        kinds = sorted({a.kind for a in direct_conflicts})
        return {
            "decision": EVIDENCE_CONFLICT,
            "reason": (
                f"Conflicting evidence detected for {solution.target} "
                f"({', '.join(kinds)}). Both facts are preserved; review "
                "required - downstream calculations that require an "
                "unresolved value are blocked."
            ),
            "blocking_reason": None,
        }

    # 4. a BLOCKED solution is a missing-evidence state, never an
    #    adjustment state: missing dependencies -> METRIC_BLOCKED /
    #    INSUFFICIENT_EVIDENCE (see tail mapping).
    if solution.status == BLOCKED:
        if solution.missing and not registry.is_registered_target(
                solution.target):
            return {
                "decision": INSUFFICIENT_EVIDENCE,
                "reason": (
                    f"{solution.target}: no registered mathematical "
                    f"relationship exists and it is not a known fact"
                    + (f" (missing {', '.join(sorted(solution.missing))})"
                       if solution.missing else "")
                    + "."
                ),
                "blocking_reason": solution.reason,
            }
        return {
            "decision": METRIC_BLOCKED,
            "reason": solution.reason or (
                f"{solution.target} is blocked: required information is "
                "unavailable or invalid."
            ),
            "blocking_reason": solution.reason,
        }

    # 5. lineage-level evidence conflict (computable result only)
    if conflict_anomalies:
        kinds = sorted({a.kind for a in conflict_anomalies})
        return {
            "decision": EVIDENCE_CONFLICT,
            "reason": (
                f"Conflicting evidence detected in the dependency path of "
                f"{solution.target} ({', '.join(kinds)}). Both facts are "
                "preserved; review required - never silently choose."
            ),
            "blocking_reason": None,
        }

    # 6. adjustment candidates (only for computable/reviewable results -
    #    a blocked result was already decided in step 4)
    if adjustment_anomalies:
        kinds = sorted({a.kind for a in adjustment_anomalies})
        return {
            "decision": ADJUSTMENT_REQUIRED,
            "reason": (
                f"Adjustment candidate(s) detected for {solution.target} "
                f"({', '.join(kinds)}) - an explicit student decision is "
                "required; the engine never auto-corrects."
            ),
            "blocking_reason": None,
        }

    # 7. solution status (computed results only)
    if solution.status == REVIEW_REQUIRED:
        return {
            "decision": EVIDENCE_CONFLICT,
            "reason": solution.reason or (
                f"{solution.target} requires review - never presented "
                "as verified."
            ),
            "blocking_reason": None,
        }
    if solution.status == VERIFIED:
        return {
            "decision": METRIC_AVAILABLE,
            "reason": f"{solution.target} is directly supported by "
                      "accepted source evidence.",
            "blocking_reason": None,
        }
    if solution.status == DERIVED:
        return {
            "decision": METRIC_DERIVED,
            "reason": f"{solution.target} is calculated deterministically "
                      "from verified dependencies.",
            "blocking_reason": None,
        }
    if solution.status == RECONCILED:
        return {
            "decision": METRIC_RECONCILED,
            "reason": f"{solution.target} was obtained through a "
                      "documented reconciliation relationship.",
            "blocking_reason": None,
        }
    if solution.status == STUDENT_INPUT:
        return {
            "decision": METRIC_STUDENT_INPUT,
            "reason": f"{solution.target} is an explicit student-input "
                      "analytical value with full lineage to the original "
                      "facts and decision.",
            "blocking_reason": None,
        }
    return {
        "decision": METRIC_BLOCKED,
        "reason": solution.reason or (
            f"{solution.target} is blocked: required information is "
            "unavailable or invalid."
        ),
        "blocking_reason": solution.reason,
    }


# ---------------------------------------------------------------------------
# Decision graph
# ---------------------------------------------------------------------------


class DecisionGraph:
    """Deterministic evidence-aware decision layer."""

    def __init__(self, registry: Optional[FormulaRegistry] = None,
                 prefer_cpp: bool = True,
                 cpp_authority: bool = False,
                 excel_compiler: Optional[ExcelLineageCompiler] = None,
                 gate: Optional[ProvenanceGate] = None) -> None:
        from backend.maths.extended_registry import EXTENDED_REGISTRY
        self.registry = (
            registry if registry is not None else EXTENDED_REGISTRY
        )
        self.prefer_cpp = prefer_cpp
        self.cpp_authority = cpp_authority
        self.compiler = (
            excel_compiler if excel_compiler is not None
            else ExcelLineageCompiler(self.registry)
        )
        self.gate = gate if gate is not None else ProvenanceGate()

    # ------------------------------------------------------------------
    def evaluate(self, target: str, facts: Any,
                 coordinate_map: Optional[Dict[str, str]] = None,
                 reference: Optional[Dict[str, Any]] = None,
                 anomalies: Optional[List[Any]] = None,
                 reconciliation_results: Optional[List[Any]] = None,
                 ) -> DecisionNode:
        """Deterministic end-to-end evaluation of one metric.

        Orchestration (fixed order):
            fact graph -> provenance gate -> solver -> anomaly/recon
            context -> decision -> evidence trace -> Excel formula.
        """
        graph = facts if isinstance(facts, FactGraph) \
            else build_fact_graph(facts or {})

        provenance_verdict = self.gate.validate_facts(
            graph, reference, target
        )
        solver = Solver(self.registry, prefer_cpp=self.prefer_cpp,
                        cpp_authority=self.cpp_authority)
        solution = solver.solve(target, graph)

        # -- anomaly / reconciliation context --------------------------
        relevant_anomalies = (
            self._relevant_anomalies(target, solution, anomalies, graph)
        )
        relevant_recons = [
            r for r in (reconciliation_results or [])
            if getattr(r, "target", None) == target
        ]

        verdict = decide_state(
            solution=solution,
            provenance_verdict=provenance_verdict,
            conflict_anomalies=[
                a for a in relevant_anomalies
                if a.kind in _CONFLICT_ANOMALY_KINDS
            ],
            adjustment_anomalies=[
                a for a in relevant_anomalies
                if a.kind not in _CONFLICT_ANOMALY_KINDS
            ],
            reconciliation_results=relevant_recons,
            registry=self.registry,
        )

        evidence_trace = trace_leaves(solution, graph)
        excel_formula = self.compiler.compile(
            solution, graph, coordinate_map
        )
        # Conflict / reconciliation / adjustment decisions surface the
        # six-tier REVIEW_REQUIRED status - the metric is never presented
        # as verified, whatever the raw solver state was.
        if verdict["decision"] in (
            EVIDENCE_CONFLICT, RECONCILIATION_REQUIRED,
            ADJUSTMENT_REQUIRED,
        ):
            node_status = REVIEW_REQUIRED
        else:
            node_status = solution.status
        decision = verdict["decision"]
        next_action = next_action_for(decision)
        source_tier = source_tier_for(evidence_trace)
        missing = list(solution.missing or [])
        explanation = self._explanation(
            target, solution, verdict, evidence_trace, missing,
            next_action,
        )
        return DecisionNode(
            node_id=f"DECISION:{target}",
            target=target,
            decision=decision,
            status=node_status,
            confidence_state=confidence_for(decision),
            value=solution.value,
            display_value=solution.display_value,
            reason=verdict["reason"],
            blocking_reason=verdict["blocking_reason"],
            formula_id=solution.formula_id,
            formula=solution.formula,
            dependencies=[i.concept for i in solution.inputs],
            missing=missing,
            sufficiency_state=solution.sufficiency_state,
            source_tier=source_tier,
            provenance_verdict=provenance_verdict,
            evidence=evidence_trace,
            excel_formula=excel_formula,
            anomalies=[a.to_dict() for a in relevant_anomalies],
            reconciliation=[r.to_dict() for r in relevant_recons],
            reconciliation_refs=[
                r.reconciliation_id for r in relevant_recons
            ],
            adjustment_refs=[
                a.anomaly_id for a in relevant_anomalies
            ],
            explanation=explanation,
            next_action=next_action,
            lineage_text=render_excel_lineage_text(solution),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _explanation(target: str, solution: Solution, verdict: Dict[str, Any],
                     evidence_trace: EvidenceTrace, missing: List[str],
                     next_action: str) -> Dict[str, Any]:
        """Deterministic structured explanation: WHAT happened, WHY,
        WHAT evidence supports it, WHAT dependencies were used, WHAT is
        missing, WHETHER user action is required."""
        leaves = evidence_trace.leaves if evidence_trace else []
        evidence_support = [
            f"{l.concept} ({l.source}"
            + (f", p.{l.page}" if l.page not in ("", "—") else "")
            + ")"
            for l in leaves
        ]
        return {
            "what": f"{target} {solution.display_value} ({solution.status})",
            "why": verdict["reason"],
            "evidence_support": evidence_support,
            "dependencies_used": [i.concept for i in solution.inputs],
            "missing": list(missing),
            "user_action_required": next_action != "none",
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _relevant_anomalies(target: str, solution: Solution,
                            anomalies: Optional[List[Any]],
                            graph: FactGraph) -> List[Any]:
        """Anomaly candidates that concern this metric or its lineage.

        When `anomalies` is None the AdjustmentEngine scans the graph
        deterministically (cheap; every condition is a candidate)."""
        from backend.maths.adjustments import AdjustmentEngine
        candidates = (
            anomalies
            if anomalies is not None
            else AdjustmentEngine().detect_anomalies(graph)
        )
        lineage_concepts = set(solution.traversal_path or [])
        lineage_concepts.add(target)
        out = []
        for a in candidates:
            # Relevant when the anomaly is ABOUT this metric, or about a
            # concept that actually participates in this metric's lineage.
            # Unrelated missing-dependency noise for other formulas is
            # never allowed to flip this metric's decision.
            if getattr(a, "target", None) == target:
                out.append(a)
                continue
            if (getattr(a, "target", None) in lineage_concepts
                    and getattr(a, "target", None) != target):
                out.append(a)
        return out


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def evaluate_metric(target: str, facts: Any,
                    registry: Optional[FormulaRegistry] = None,
                    coordinate_map: Optional[Dict[str, str]] = None,
                    reference: Optional[Dict[str, Any]] = None,
                    anomalies: Optional[List[Any]] = None,
                    reconciliation_results: Optional[List[Any]] = None,
                    prefer_cpp: bool = True,
                    cpp_authority: bool = False) -> DecisionNode:
    """Convenience entry point returning a DecisionNode."""
    return DecisionGraph(
        registry=registry, prefer_cpp=prefer_cpp,
        cpp_authority=cpp_authority,
    ).evaluate(
        target, facts,
        coordinate_map=coordinate_map,
        reference=reference,
        anomalies=anomalies,
        reconciliation_results=reconciliation_results,
    )
