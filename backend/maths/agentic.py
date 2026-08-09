"""
Financial Timeline Engine
Sprint 12E - Production Integration, Agentic Evidence Retrieval & Audit Loop
backend/maths/agentic.py

The Agent is an ORCHESTRATOR, never a mathematician.

It plans the canonical facts/formulas a request needs, inspects what
already exists in the fact graph, requests retrieval for missing
dependencies through the approved 4-tier hierarchy, runs the provenance
gate, invokes the existing deterministic graph/solver, observes the
resulting states (VERIFIED / DERIVED / RECONCILED / REVIEW_REQUIRED /
BLOCKED), and produces a structured explanation payload.

The Agent MUST NOT:
  * calculate financial metrics itself (the 12A solver is the authority);
  * invent or estimate missing values;
  * silently substitute another source;
  * browse arbitrary websites (Tier 4 stays FORBIDDEN);
  * override BLOCKED or REVIEW_REQUIRED;
  * overwrite conflicting evidence;
  * manufacture provenance;
  * generate Excel values independently of the compiler.

Retrieval loop termination (deterministic):
  * every fact is requested at most once (dedupe set);
  * tiers are visited in strict order Tier 1 -> 2 -> 3;
  * the loop stops after `max_rounds` rounds or when no new fact can be
    recovered (no-progress stop);
  * iteration order is always sorted (no dict-order dependence).

Every retrieval attempt records: requested fact, reason, source tier,
source/provider, identifier, retrieval timestamp, retrieval result,
provenance verdict, resulting fact status, next action.

Pure module: no Streamlit, no AI, no network. Deterministic except for
wall-clock stage timings (reported as metadata only, never as an
analytical result).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.decision_graph import (
    EVIDENCE_CONFLICT,
    INSUFFICIENT_EVIDENCE,
    METRIC_AVAILABLE,
    METRIC_BLOCKED,
    METRIC_DERIVED,
    METRIC_RECONCILED,
    METRIC_STUDENT_INPUT,
    DecisionGraph,
    DecisionNode,
)
from backend.maths.evidence import (
    EvidenceRef,
    EvidenceTrace,
    TIER_1_DOCUMENT,
    TIER_2_APPENDIX,
    TIER_3_REGULATORY_API,
    is_allowed_source,
    tier_of,
)
from backend.maths.fact_model import (
    FactGraph,
    build_fact_graph,
    from_pipeline_fact,
    to_decimal,
)
from backend.maths.extended_registry import (
    EXTENDED_FORMULA_METADATA,
    EXTENDED_REGISTRY,
)
from backend.maths.fact_model import FactGraph, build_fact_graph
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.provenance import GATE_BLOCKED, GATE_REVIEW, ProvenanceGate
from backend.maths.recovery import (
    CONFLICT,
    MISSING,
    RECOVERED,
    EvidenceRecoveryEngine,
)
from backend.maths.status import (
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
)

# ---------------------------------------------------------------------------
# Overall workflow states (Sprint 12E section 11)
# ---------------------------------------------------------------------------

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
REVIEW_REQUIRED_STATE = "REVIEW_REQUIRED"
BLOCKED_STATE = "BLOCKED"
EVIDENCE_CONFLICT_STATE = "EVIDENCE_CONFLICT"
UNSUPPORTED = "UNSUPPORTED"
RETRIEVAL_FAILED = "RETRIEVAL_FAILED"

# Deterministic decision -> workflow-state mapping.
WORKFLOW_STATE_BY_DECISION = {
    "METRIC_AVAILABLE": SUCCESS,
    "METRIC_DERIVED": SUCCESS,
    "METRIC_RECONCILED": SUCCESS,
    "METRIC_STUDENT_INPUT": SUCCESS,
    "EVIDENCE_CONFLICT": EVIDENCE_CONFLICT_STATE,
    "RECONCILIATION_REQUIRED": REVIEW_REQUIRED_STATE,
    "ADJUSTMENT_REQUIRED": REVIEW_REQUIRED_STATE,
    "METRIC_BLOCKED": BLOCKED_STATE,
    "INSUFFICIENT_EVIDENCE": UNSUPPORTED,
}

_TIER_ORDER = (TIER_1_DOCUMENT, TIER_2_APPENDIX, TIER_3_REGULATORY_API)


def _pool_has(source_pools: Optional[Dict[str, Any]],
              concept: str) -> bool:
    """True when an APPROVED source pool offers a candidate for the
    concept (used to decide whether a directly-present target needs a
    conflict check against the pools)."""
    for label, pool in (source_pools or {}).items():
        if (is_allowed_source(label) and isinstance(pool, dict)
                and concept in pool):
            return True
    return False

# Filler words stripped when resolving a free-form request.
_FILLER = frozenset({
    "calculate", "calc", "compute", "find", "what", "is", "the", "please",
    "for", "of", "in", "fy", "year", "years", "annual", "quarterly", "q1",
    "q2", "q3", "q4", "me", "show", "get", "need", "ratio", "ratios",
    "metric", "metrics", "value", "and", "a", "an", "to", "from", "vs",
})

# Deterministic phrase table: common human phrasings of financial metrics.
# Only targets present in the registry are ever resolved (never guessed).
_PHRASE_ALIASES = {
    "ROE": ["return on equity", "equity return", "roe"],
    "ROA": ["return on assets", "asset return", "roa"],
    "PROFIT_MARGIN": ["profit margin", "net margin", "net profit margin"],
    "NET_MARGIN": ["net margin", "net profit margin"],
    "GROSS_MARGIN": ["gross margin"],
    "OPERATING_MARGIN": ["operating margin", "ebit margin"],
    "EBITDA_MARGIN": ["ebitda margin"],
    "ASSET_TURNOVER": ["asset turnover", "asset turnover ratio"],
    "EQUITY_MULTIPLIER": ["equity multiplier"],
    "CURRENT_RATIO": ["current ratio", "working capital ratio"],
    "QUICK_RATIO": ["quick ratio", "acid test", "acid-test ratio"],
    "DEBT_TO_EQUITY": ["debt to equity", "debt equity", "debt/equity"],
    "DEBT_TO_ASSETS": ["debt to assets", "debt assets", "debt/assets"],
    "INTEREST_COVERAGE": ["interest coverage", "times interest earned", "icr"],
    "WORKING_CAPITAL": ["working capital"],
    "EPS": ["earnings per share", "eps"],
    "CAGR": ["cagr", "compound annual growth rate"],
    "INVENTORY_TURNOVER": ["inventory turnover", "stock turnover"],
    "RECEIVABLES_TURNOVER": ["receivables turnover", "debtors turnover"],
    "PAYABLES_TURNOVER": ["payables turnover", "creditors turnover"],
    "NET_PROFIT": ["net profit", "net income", "net earnings"],
    "REVENUE": ["revenue", "sales", "turnover"],
    "TOTAL_ASSETS": ["total assets", "assets"],
    "EQUITY": ["equity", "shareholders equity", "shareholder equity"],
}


# ---------------------------------------------------------------------------
# Retrieval attempt record
# ---------------------------------------------------------------------------


@dataclass
class RetrievalAttempt:
    """One deterministic evidence-retrieval attempt (Sprint 12E section 2)."""

    concept: str
    reason: str
    source_tier: str = "—"
    provider: str = "—"
    identifier: str = "—"
    retrieval_timestamp: str = ""
    retrieval_result: str = "NOT_ATTEMPTED"
    provenance_verdict: str = "—"
    fact_status: str = "—"
    next_action: str = "none"
    value: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "reason": self.reason,
            "source_tier": self.source_tier,
            "provider": self.provider,
            "identifier": self.identifier,
            "retrieval_timestamp": self.retrieval_timestamp,
            "retrieval_result": self.retrieval_result,
            "provenance_verdict": self.provenance_verdict,
            "fact_status": self.fact_status,
            "next_action": self.next_action,
            "value": float(self.value) if self.value is not None else None,
        }


# ---------------------------------------------------------------------------
# Dependency plan
# ---------------------------------------------------------------------------


@dataclass
class DependencyPlan:
    """Registry-driven plan for one target: which facts and formulas are
    needed, in deterministic order (dependencies first)."""

    target: str
    required_facts: List[str] = field(default_factory=list)
    required_formulas: List[str] = field(default_factory=list)
    supported: bool = True
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "required_facts": list(self.required_facts),
            "required_formulas": list(self.required_formulas),
            "supported": self.supported,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Agent analysis result
# ---------------------------------------------------------------------------


@dataclass
class AgentAnalysis:
    """Complete deterministic outcome of one agentic request."""

    request: str
    target: str
    resolved: bool
    plan: Optional[DependencyPlan] = None
    decision: str = METRIC_BLOCKED
    status: str = BLOCKED
    workflow_state: str = BLOCKED_STATE
    value: Optional[Decimal] = None
    display_value: str = "—"
    formula: str = "—"
    dependencies: List[str] = field(default_factory=list)
    existing_facts: List[str] = field(default_factory=list)
    missing_before_retrieval: List[str] = field(default_factory=list)
    missing_after_retrieval: List[str] = field(default_factory=list)
    retrieval_attempts: List[RetrievalAttempt] = field(default_factory=list)
    retrieved_facts: List[str] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    excel_formula: Optional[str] = None
    explanation: Dict[str, Any] = field(default_factory=dict)
    next_action: str = "none"
    termination_reason: str = ""
    timings_ms: Dict[str, float] = field(default_factory=dict)
    node_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request,
            "target": self.target,
            "resolved": self.resolved,
            "plan": self.plan.to_dict() if self.plan else None,
            "decision": self.decision,
            "status": self.status,
            "workflow_state": self.workflow_state,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "formula": self.formula,
            "dependencies": list(self.dependencies),
            "existing_facts": list(self.existing_facts),
            "missing_before_retrieval": list(self.missing_before_retrieval),
            "missing_after_retrieval": list(self.missing_after_retrieval),
            "retrieval_attempts": [a.to_dict() for a in self.retrieval_attempts],
            "retrieved_facts": list(self.retrieved_facts),
            "conflicts": list(self.conflicts),
            "provenance": dict(self.provenance),
            "evidence": dict(self.evidence),
            "excel_formula": self.excel_formula,
            "explanation": dict(self.explanation),
            "next_action": self.next_action,
            "termination_reason": self.termination_reason,
            "timings_ms": dict(self.timings_ms),
            "node_payload": dict(self.node_payload),
        }


# ---------------------------------------------------------------------------
# Intent resolution (deterministic)
# ---------------------------------------------------------------------------


def _known_concepts(registry: FormulaRegistry) -> set:
    """Every canonical concept the registry knows about: formula targets
    plus all leaf-fact dependencies (e.g. 'Net Profit', 'Equity')."""
    known = set(registry.targets())
    for formula_id in registry.all_ids():
        formula = registry.get(formula_id)
        if formula is not None:
            known.update(formula.dependencies)
    return known


def _alias_map(registry: FormulaRegistry) -> Dict[str, str]:
    """Build {normalized name -> canonical target} from the registry,
    formula metadata (name + aliases), the canonical target itself, and
    the deterministic phrase table (registered targets AND known facts
    only - never guesses)."""
    aliases: Dict[str, str] = {}
    phrase_by_norm: Dict[str, List[str]] = {
        _norm(key): phrases for key, phrases in _PHRASE_ALIASES.items()
    }
    for formula_id in registry.all_ids():
        aliases[_norm(formula_id)] = formula_id
        target = registry.get(formula_id).target if registry.get(formula_id) else formula_id
        aliases[_norm(target)] = target
        meta = EXTENDED_FORMULA_METADATA.get(formula_id) or {}
        name = meta.get("name")
        if name:
            aliases[_norm(str(name))] = target
        for alias in meta.get("aliases") or []:
            aliases[_norm(str(alias))] = target
        # phrase table - only when the phrase resolves to a registered
        # target of this registry (never guesses)
        for phrase in _PHRASE_ALIASES.get(formula_id, []):
            aliases[_norm(phrase)] = target
    # known facts (leaf dependencies): canonical name + phrase aliases
    for concept in sorted(_known_concepts(registry)):
        aliases[_norm(concept)] = concept
        for phrase in phrase_by_norm.get(_norm(concept), []):
            aliases[_norm(phrase)] = concept
    return aliases


def _norm(text: str) -> str:
    """Normalize a name for alias matching: lowercase, no fillers, no
    punctuation, collapsed whitespace."""
    import re as _re
    s = str(text or "").strip().lower()
    s = _re.sub(r"[^a-z0-9\\s]+", " ", s)
    words = [w for w in s.split() if w not in _FILLER]
    return " ".join(words).strip()


def resolve_target(request: str, registry: Optional[FormulaRegistry] = None) -> Optional[str]:
    """Resolve a free-form request or metric name to a canonical target.

    Returns None when the request cannot be mapped deterministically
    (-> UNSUPPORTED). Never guesses.
    """
    if request is None:
        return None
    reg = registry if registry is not None else EXTENDED_REGISTRY
    aliases = _alias_map(reg)
    raw = str(request).strip()
    if not raw:
        return None
    if raw in aliases:
        return aliases[raw]
    normed = _norm(raw)
    if normed in aliases:
        return aliases[normed]
    # direct registry target / fact-name match
    if reg.is_registered_target(raw) or raw in aliases:
        return aliases.get(raw, raw)
    return None


# ---------------------------------------------------------------------------
# Dependency planning
# ---------------------------------------------------------------------------


def plan_dependencies(target: str,
                      registry: Optional[FormulaRegistry] = None) -> DependencyPlan:
    """Deterministic closure of the facts and formulas a target needs.

    Walks the registry graph breadth-first (dependencies first); every
    dependency that is itself a registered formula target is expanded
    recursively. Leaf facts (not produced by any formula) are the
    `required_facts`. Never guesses a relationship that is not registered.
    """
    reg = registry if registry is not None else EXTENDED_REGISTRY
    # Accept a registered formula ID as well as the canonical target
    # name (e.g. CURRENT_RATIO vs "Current Ratio"): both must plan the
    # same way - the ID is never a different concept.
    if not reg.is_registered_target(target):
        _by_id = reg.get(target)
        if _by_id is not None and _by_id.target:
            target = _by_id.target
    plan = DependencyPlan(target=target)
    if not reg.is_registered_target(target):
        # Not a formula target. When the concept is a KNOWN leaf fact
        # (a dependency of some registered formula) the user may still
        # request it directly - supported, required facts = [target].
        # Truly unknown concepts stay UNSUPPORTED (never guessed).
        if target in _known_concepts(reg):
            plan.required_facts = [target]
            plan.supported = True
            plan.reason = (
                f"{target} is a known fact (leaf dependency); it can be "
                "requested directly with approved evidence."
            )
            return plan
        plan.supported = False
        plan.reason = (
            f"{target}: no registered relationship can produce it and it "
            "is not a known fact - UNSUPPORTED."
        )
        return plan

    required_facts: List[str] = []
    required_formulas: List[str] = []
    seen: set = set()
    queue = [target]
    while queue:
        concept = queue.pop(0)
        if concept in seen:
            continue
        seen.add(concept)
        formula = reg.get(concept) or (
            # target may be registered under a different id than the
            # canonical concept name; look up by target match.
            next((reg.get(fid) for fid in reg.all_ids()
                  if reg.get(fid) and reg.get(fid).target == concept), None)
        )
        if formula is not None:
            required_formulas.append(formula.formula_id)
            for dep in formula.dependencies:
                queue.append(dep)
        else:
            required_facts.append(concept)
    plan.required_facts = required_facts
    plan.required_formulas = required_formulas
    plan.supported = True
    plan.reason = (
        f"{target} requires facts {sorted(required_facts)} via formulas "
        f"{sorted(required_formulas)}."
    )
    return plan


# ---------------------------------------------------------------------------
# Retrieval loop
# ---------------------------------------------------------------------------


class AgenticRetrievalLoop:
    """Deterministic, terminating evidence-retrieval loop.

    For every missing dependency the loop visits the approved tiers in
    strict order (Tier 1 -> 2 -> 3). A fact is requested at most once;
    conflicts are preserved (never silently chosen); Tier 4 is never
    consulted; the loop stops on no-progress or after max_rounds.
    """

    def __init__(self, recovery: Optional[EvidenceRecoveryEngine] = None,
                 max_rounds: int = 3) -> None:
        self.recovery = recovery if recovery is not None \
            else EvidenceRecoveryEngine()
        self.max_rounds = max(1, int(max_rounds))

    def run(self, missing: List[str],
            source_pools: Dict[str, Dict[str, Any]],
            reference: Optional[Dict[str, Any]] = None,
            retrieval_timestamp: str = "") -> Dict[str, Any]:
        """Attempt to recover every missing fact from the given pools.

        `source_pools`: {tier_label: {concept: fact_dict}} - only approved
        tier labels are consulted; forbidden labels are ignored.

        Returns {attempts, recovered, conflicts, still_missing}.
        """
        attempts: List[RetrievalAttempt] = []
        recovered: Dict[str, Any] = {}
        conflicts: List[Dict[str, Any]] = []
        requested: set = set()

        # Pools keyed by tier number (approved only; Tier 4 ignored).
        pools_by_tier: Dict[int, Dict[str, Any]] = {}
        for label, pool in (source_pools or {}).items():
            tier_num = tier_of(label)
            if tier_num in (1, 2, 3) and is_allowed_source(label):
                pools_by_tier.setdefault(tier_num, {}).update(pool or {})

        for _round in range(self.max_rounds):
            pending = sorted(set(missing) - set(recovered) - requested)
            if not pending:
                break
            progress = False
            for concept in pending:
                if concept in requested:
                    continue
                requested.add(concept)
                for tier_num in _TIER_ORDER:
                    pool = pools_by_tier.get(tier_num)
                    if not pool or concept not in pool:
                        continue
                    candidate = pool[concept]
                    attempt = self._attempt(
                        concept, candidate, tier_num, reference,
                        retrieval_timestamp,
                    )
                    attempts.append(attempt)
                    if attempt.retrieval_result == RECOVERED:
                        recovered[concept] = candidate
                        progress = True
                        break
                    if attempt.retrieval_result == CONFLICT:
                        conflicts.append({
                            "concept": concept,
                            "reason": attempt.reason,
                            "next_action": "review_conflicting_evidence",
                        })
                        break
                    # BLOCKED / MISSING at this tier: continue to next tier.
                # end tier loop
            if not progress:
                break
        # end round loop

        still_missing = sorted(set(missing) - set(recovered))
        return {
            "attempts": attempts,
            "recovered": recovered,
            "conflicts": conflicts,
            "still_missing": still_missing,
        }

    # ------------------------------------------------------------------
    def _attempt(self, concept: str, fact: Dict[str, Any], tier_num: int,
                 reference: Optional[Dict[str, Any]],
                 retrieval_timestamp: str) -> RetrievalAttempt:
        """One retrieval attempt at one tier: recover -> gate -> status."""
        label = fact.get("source") or concept
        attempt = RetrievalAttempt(
            concept=concept,
            reason=f"missing dependency required by the requested metric",
            source_tier=(
                "TIER_1_DOCUMENT" if tier_num == 1
                else "TIER_2_APPENDIX" if tier_num == 2
                else "TIER_3_REGULATORY_API"
            ),
            provider=str(fact.get("provider") or "—"),
            identifier=str(fact.get("provider_identifier")
                           or fact.get("identifier") or "—"),
            retrieval_timestamp=retrieval_timestamp,
        )
        result = self.recovery.recover(
            concept, {label: fact}, reference, retrieval_timestamp,
        )
        attempt.retrieval_result = result.status
        attempt.value = result.value
        attempt.reason = result.reason or attempt.reason
        if result.status == RECOVERED:
            attempt.provenance_verdict = "PASS"
            attempt.fact_status = VERIFIED
            attempt.next_action = "none"
        elif result.status == CONFLICT:
            attempt.provenance_verdict = "REVIEW_REQUIRED"
            attempt.fact_status = REVIEW_REQUIRED
            attempt.next_action = "review_conflicting_evidence"
        elif result.status in (MISSING, "BLOCKED"):
            attempt.provenance_verdict = "BLOCKED"
            attempt.fact_status = BLOCKED
            attempt.next_action = "provide_missing_evidence"
        return attempt


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class AgenticOrchestrator:
    """Deterministic agent orchestration over the existing deterministic
    stack (12A solver + 12B reasoning + 12C decision graph + 12D
    recovery). The Agent plans, retrieves, gates and explains - it never
    calculates."""

    def __init__(self, registry: Optional[FormulaRegistry] = None,
                 prefer_cpp: bool = True,
                 gate: Optional[ProvenanceGate] = None,
                 max_rounds: int = 3) -> None:
        self.registry = registry if registry is not None else EXTENDED_REGISTRY
        self.prefer_cpp = prefer_cpp
        self.gate = gate if gate is not None else ProvenanceGate()
        self.loop = AgenticRetrievalLoop(max_rounds=max_rounds)
        self.graph = DecisionGraph(
            registry=self.registry, prefer_cpp=self.prefer_cpp,
            gate=self.gate,
        )

    # ------------------------------------------------------------------
    def analyze_request(self, request: str,
                        existing_facts: Optional[Dict[str, Any]] = None,
                        source_pools: Optional[Dict[str, Any]] = None,
                        reference: Optional[Dict[str, Any]] = None,
                        coordinate_map: Optional[Dict[str, str]] = None,
                        retrieval_timestamp: str = "") -> AgentAnalysis:
        """Run the full agentic loop for one request.

        Pipeline (fixed, deterministic):
            resolve -> plan -> existing graph -> missing -> retrieval
            (Tier 1-3) -> provenance gate -> graph update -> solver ->
            decision graph -> agent explanation.
        """
        timings: Dict[str, float] = {}

        t0 = time.perf_counter()
        target = resolve_target(request, self.registry)
        timings["intent_resolution_ms"] = (time.perf_counter() - t0) * 1000.0

        analysis = AgentAnalysis(
            request=str(request),
            target=target or str(request),
            resolved=target is not None,
        )
        if target is None:
            analysis.workflow_state = UNSUPPORTED
            analysis.decision = INSUFFICIENT_EVIDENCE
            analysis.termination_reason = (
                f"'{request}' could not be resolved to a registered metric "
                "or known fact - UNSUPPORTED, nothing was guessed."
            )
            analysis.explanation = self._unsupported_explanation(request)
            analysis.next_action = "provide_evidence_or_register_relationship"
            return analysis

        # -- dependency planning --------------------------------------
        t0 = time.perf_counter()
        plan = plan_dependencies(target, self.registry)
        timings["dependency_planning_ms"] = (time.perf_counter() - t0) * 1000.0
        analysis.plan = plan
        if not plan.supported:
            analysis.workflow_state = UNSUPPORTED
            analysis.decision = INSUFFICIENT_EVIDENCE
            analysis.termination_reason = plan.reason
            analysis.next_action = "provide_evidence_or_register_relationship"
            return analysis

        # -- existing fact graph --------------------------------------
        t0 = time.perf_counter()
        graph = build_fact_graph(existing_facts or {})
        analysis.existing_facts = sorted(graph.known_ids())
        timings["graph_build_ms"] = (time.perf_counter() - t0) * 1000.0

        # -- missing dependencies -------------------------------------
        # A target that is already a known direct fact needs no
        # dependency expansion or retrieval - it goes straight to the
        # solver (spec: CHECK EXISTING FACT GRAPH -> NO -> solver).
        target_direct = target in graph.known_ids()
        merged = dict(existing_facts or {})
        if target_direct:
            missing_before: List[str] = []
        else:
            missing_before = sorted(
                set(plan.required_facts) - set(graph.known_ids())
            )
        analysis.missing_before_retrieval = missing_before

        # -- retrieval: per-concept recovery over approved tiers -------
        # The Agent never substitutes: an approved pool value that
        # differs from an already-established fact is a PRESERVED
        # conflict, never an overwrite. A fact already in the graph is
        # only re-attempted when the pools actually offer a candidate
        # (conflict check); a missing fact is always attempted so the
        # outcome is MISSING / BLOCKED, never fabricated.
        t0 = time.perf_counter()
        retrieval_closure = set(missing_before)
        if target_direct and _pool_has(source_pools, target):
            retrieval_closure.add(target)
        attempts, recovered, conflicts, conflict_facts = \
            self._retrieve_closure(
                sorted(retrieval_closure), graph, existing_facts or {},
                source_pools or {}, reference, retrieval_timestamp,
            )
        timings["retrieval_ms"] = (time.perf_counter() - t0) * 1000.0
        analysis.retrieval_attempts = attempts
        analysis.retrieved_facts = sorted(recovered)
        analysis.conflicts = conflicts

        # -- merge recovered facts (never over a conflicting value) ----
        for concept, fact in recovered.items():
            if fact is not None:
                merged[concept] = fact
        # conflicted concepts keep BOTH values as separate graph nodes
        # so the deterministic anomaly/decision machinery reports
        # EVIDENCE_CONFLICT (12C behaviour) instead of silently choosing.
        for concept in conflict_facts:
            merged.pop(concept, None)
        graph = self._build_agent_graph(merged, conflict_facts)
        if target_direct:
            # the requested metric is itself established - the plan's
            # transitive facts are not actually required for THIS result.
            analysis.missing_after_retrieval = []
        else:
            analysis.missing_after_retrieval = sorted(
                set(plan.required_facts) - set(graph.known_ids())
            )

        # -- provenance gate on the updated graph ---------------------
        t0 = time.perf_counter()
        verdict = self.gate.validate_facts(graph, reference, target)
        timings["provenance_ms"] = (time.perf_counter() - t0) * 1000.0
        analysis.provenance = verdict.to_dict()

        # -- closure identity isolation (12D identity, fail closed) ---
        # Facts feeding ONE metric must share the strict identity
        # dimensions (entity / period / period type / currency /
        # unit-scale). A mismatch is REVIEW, never a silent merge.
        identity_issues = self._closure_identity_issues(
            sorted(set(plan.required_facts)), graph
        )
        if identity_issues:
            node = self._identity_conflict_node(target, graph,
                                                identity_issues)
            analysis.conflicts = list(analysis.conflicts) + [
                {
                    "concept": issue["concept"],
                    "kind": issue["kind"],
                    "dimension": issue.get("dimension", ""),
                    "reason": issue["detail"],
                    "variance": None,
                    "next_action": "review_conflicting_evidence",
                }
                for issue in identity_issues
            ]
        else:
            # -- solver + decision graph --------------------------------
            t0 = time.perf_counter()
            node = self.graph.evaluate(
                target, graph, coordinate_map=coordinate_map,
                reference=reference,
            )
            timings["solver_ms"] = (time.perf_counter() - t0) * 1000.0
            # -- 12E agent post-pass (never weakens the engine) --------
            node = self._apply_agent_findings(
                node, target, target_direct, merged, conflict_facts,
                verdict,
            )
        analysis.decision = node.decision
        analysis.status = node.status
        analysis.value = node.value
        analysis.display_value = node.display_value
        analysis.formula = node.formula
        analysis.dependencies = list(node.dependencies)
        analysis.evidence = node.evidence.to_dict() if node.evidence else {}
        analysis.excel_formula = (
            node.excel_formula.formula if node.excel_formula else None
        )
        analysis.next_action = node.next_action
        analysis.workflow_state = WORKFLOW_STATE_BY_DECISION.get(
            node.decision, BLOCKED_STATE,
        )
        analysis.node_payload = node.to_payload()

        # -- agent explanation ----------------------------------------
        t0 = time.perf_counter()
        analysis.explanation = self._explain(node, analysis)
        timings["explanation_ms"] = (time.perf_counter() - t0) * 1000.0

        # -- termination reason ---------------------------------------
        analysis.termination_reason = self._termination_reason(node, {})
        analysis.timings_ms = timings
        return analysis

    # ------------------------------------------------------------------
    # 12E retrieval + fail-closed helpers (deterministic)
    # ------------------------------------------------------------------

    @staticmethod
    def _candidates_for(concept: str, graph: FactGraph,
                        existing_facts: Dict[str, Any],
                        pools_by_label: Dict[str, Any]) -> Dict[str, Any]:
        """Gather EVERY approved candidate for one concept: the existing
        graph fact (when present) plus every approved source-pool fact.
        Recovery then applies tier priority + value conflict rules across
        the full candidate set - an existing value is never overwritten
        by a disagreeing approved source."""
        candidates: Dict[str, Any] = {}
        node = graph.get(concept)
        if node is not None and node.has_value():
            orig = existing_facts.get(concept)
            if isinstance(orig, dict):
                candidates[
                    f"existing:{str(node.source or concept)}"
                ] = orig
        for label in sorted(pools_by_label):
            pool = pools_by_label[label]
            fact = (pool or {}).get(concept)
            if isinstance(fact, dict):
                key = f"{label}:{str(fact.get('source') or concept)}"
                candidates.setdefault(key, fact)
        return candidates

    @staticmethod
    def _attempt_from_recovery(concept: str, result: Any,
                               retrieval_timestamp: str) -> RetrievalAttempt:
        """One RetrievalAttempt record from a recovery result."""
        tier_label = {
            "DOCUMENT": "TIER_1_DOCUMENT",
            "APPENDIX": "TIER_2_APPENDIX",
            "REGULATORY_API": "TIER_3_REGULATORY_API",
            "EXTERNAL_DERIVED": "TIER_3_REGULATORY_API",
        }.get(str(result.source_tier or "").upper(),
              str(result.source_tier or "—"))
        status = result.status or "MISSING"
        verdict_map = {
            RECOVERED: "PASS",
            CONFLICT: "REVIEW_REQUIRED",
            BLOCKED: "BLOCKED",
            MISSING: "BLOCKED",
        }
        fact_status_map = {
            RECOVERED: VERIFIED,
            CONFLICT: REVIEW_REQUIRED,
            BLOCKED: BLOCKED,
            MISSING: BLOCKED,
        }
        next_map = {
            RECOVERED: "none",
            CONFLICT: "review_conflicting_evidence",
            BLOCKED: "provide_missing_evidence",
            MISSING: "provide_missing_evidence",
        }
        return RetrievalAttempt(
            concept=concept,
            reason="dependency required by the requested metric",
            source_tier=tier_label,
            retrieval_timestamp=retrieval_timestamp,
            retrieval_result=status,
            provenance_verdict=verdict_map.get(status, "—"),
            fact_status=fact_status_map.get(status, "—"),
            next_action=next_map.get(status, "none"),
            value=result.value,
        )

    def _retrieve_closure(self, concepts: List[str], graph: FactGraph,
                          existing_facts: Dict[str, Any],
                          source_pools: Dict[str, Any],
                          reference: Optional[Dict[str, Any]],
                          retrieval_timestamp: str) -> tuple:
        """Recover every closure concept from its existing fact + the
        approved source pools (Tier 1 -> 2 -> 3 priority; Tier 4 ignored).

        Returns (attempts, recovered, conflicts, conflict_facts):
          * recovered    : {concept: fact} for genuinely NEW facts
          * conflicts    : preserved source-conflict records (with
                           variance when two values are known)
          * conflict_facts: {concept: [fact, ...]} all preserved values
        """
        attempts: List[RetrievalAttempt] = []
        recovered: Dict[str, Any] = {}
        conflicts: List[Dict[str, Any]] = []
        conflict_facts: Dict[str, List[Dict[str, Any]]] = {}
        pools_by_label = {
            label: pool for label, pool in (source_pools or {}).items()
            if is_allowed_source(label) and isinstance(pool, dict)
        }
        for concept in sorted(concepts):
            candidates = self._candidates_for(
                concept, graph, existing_facts, pools_by_label
            )
            result = self.loop.recovery.recover(
                concept, candidates, reference, retrieval_timestamp
            )
            attempts.append(
                self._attempt_from_recovery(concept, result,
                                            retrieval_timestamp)
            )
            if result.status == RECOVERED and result.chosen:
                chosen_label = str(result.chosen.get("label") or "")
                if not chosen_label.startswith("existing:"):
                    fact = candidates.get(chosen_label)
                    if isinstance(fact, dict):
                        recovered[concept] = fact
            elif result.status == CONFLICT:
                preserved = [
                    candidates[label] for label in sorted(candidates)
                    if isinstance(candidates.get(label), dict)
                ]
                conflict_facts[concept] = preserved
                values = [
                    float(v) for v in (
                        c.get("value") for c in result.candidates
                    ) if v is not None
                ]
                distinct = sorted(set(values))
                variance = (
                    abs(distinct[0] - distinct[1])
                    if len(distinct) >= 2 else None
                )
                conflicts.append({
                    "concept": concept,
                    "kind": "CONFLICTING_SOURCE_VALUES",
                    "reason": result.reason or (
                        f"{concept} has conflicting approved-source "
                        "values; both are preserved and nothing was "
                        "silently selected."
                    ),
                    "values": values,
                    "variance": variance,
                    "next_action": "review_conflicting_evidence",
                })
        return attempts, recovered, conflicts, conflict_facts

    @staticmethod
    def _build_agent_graph(
            merged: Dict[str, Any],
            conflict_facts: Dict[str, List[Dict[str, Any]]],
    ) -> FactGraph:
        """Deterministic graph for the merged facts; conflicted concepts
        become TWO nodes with the same canonical concept and distinct
        node ids so the 12B anomaly scan reports EVIDENCE_CONFLICT."""
        graph = build_fact_graph(merged)
        for concept in sorted(conflict_facts):
            facts = conflict_facts[concept]
            for i, fact in enumerate(facts):
                node = from_pipeline_fact(concept, fact)
                node.node_id = f"{concept}#{i}"
                node.canonical_concept = concept
                graph.add(node)
        return graph

    @staticmethod
    def _closure_identity_issues(concepts: List[str],
                                 graph: FactGraph) -> List[Dict[str, Any]]:
        """Cross-fact identity isolation for one calculation closure.

        Facts feeding ONE metric must share the strict identity
        dimensions (entity / period / period type / currency /
        unit-scale). Unknown on one side is tolerated; two known and
        different values are an identity conflict - the engine fails
        closed and never merges (12D identity semantics)."""
        def dim_value(node, dim: str) -> str:
            if dim == "entity":
                return str(node.entity or "").strip()
            if dim == "period":
                return str(node.period or "").strip()
            if dim == "period_type":
                return str(node.period_type or "").strip()
            if dim == "currency":
                return str(node.currency or "").strip().upper()
            if dim == "unit_scale":
                unit = str(node.original_unit
                           or node.normalized_unit or "").strip()
                scale = str(node.original_scale or "").strip()
                return f"{unit}|{scale}"
            return ""

        kinds = {
            "entity": "ENTITY_MISMATCH",
            "period": "PERIOD_MISMATCH",
            "period_type": "PERIOD_TYPE_MISMATCH",
            "currency": "CURRENCY_MISMATCH",
            "unit_scale": "UNIT_SCALE_MISMATCH",
        }
        dims = ("entity", "period", "period_type", "currency",
                "unit_scale")
        issues: List[Dict[str, Any]] = []
        for dim in dims:
            seen: Dict[str, List[str]] = {}
            for concept in concepts:
                node = graph.get(concept)
                if node is None:
                    continue
                value = dim_value(node, dim)
                if not value:
                    continue
                seen.setdefault(value, []).append(concept)
            if len(seen) <= 1:
                continue
            ordered = sorted(seen)
            detail_parts = [
                value + " (" + ", ".join(sorted(seen[value])) + ")"
                for value in ordered
            ]
            involved = sorted({
                c for names in seen.values() for c in names
            })
            issues.append({
                "concept": ", ".join(involved),
                "kind": kinds[dim],
                "dimension": dim,
                "detail": (
                    f"closure facts carry conflicting {dim} values "
                    f"({' vs '.join(detail_parts)}) - never merged "
                    "silently; review required."
                ),
            })
        return issues

    def _identity_conflict_node(self, target: str, graph: FactGraph,
                                issues: List[Dict[str, Any]]) -> DecisionNode:
        """Deterministic EVIDENCE_CONFLICT node for closure identity
        violations: no value is computed, both sides stay preserved."""
        leaves = []
        for concept in sorted(graph.known_ids()):
            node = graph.get(concept)
            if node is None:
                continue
            leaves.append(EvidenceRef(
                concept=node.canonical_concept,
                value=node.value,
                display_value=(str(node.value)
                               if node.value is not None else "—"),
                status=node.status,
                tier=node.source_tier or "—",
                source=node.source or "—",
                document_name=node.document_name or "—",
                page=node.page or "—",
                evidence=node.evidence or "—",
                period=node.period or "—",
                currency=node.currency or "—",
                unit=(node.original_unit or node.normalized_unit) or "—",
            ))
        reason = (
            "Identity isolation failed closed: "
            + "; ".join(i["detail"] for i in issues)
        )
        return DecisionNode(
            node_id=f"DECISION:{target}",
            target=target,
            decision=EVIDENCE_CONFLICT,
            status=REVIEW_REQUIRED,
            confidence_state="review_required",
            value=None,
            display_value="—",
            reason=reason,
            blocking_reason=None,
            dependencies=list(graph.known_ids()),
            missing=[],
            source_tier="—",
            next_action="review_conflicting_evidence",
            evidence=EvidenceTrace(
                target=target, status=REVIEW_REQUIRED,
                leaves=leaves, chain=[],
            ),
        )

    @staticmethod
    def _evidence_ref_from_fact_dict(concept: str,
                                     fact: Dict[str, Any]) -> EvidenceRef:
        """Machine-readable evidence leaf from a source fact dict (the
        pool/existing shape) - provider and identifier are retained."""
        value = to_decimal(fact.get("normalized_value", fact.get("value")))
        return EvidenceRef(
            concept=concept,
            value=value,
            display_value=str(value) if value is not None else "—",
            status=str(fact.get("status") or VERIFIED),
            tier=str(fact.get("provenance_tier")
                     or fact.get("source_tier") or "—"),
            source=str(fact.get("source") or "—"),
            document_name=str(fact.get("document_name") or "—"),
            page=str(fact.get("page") or "—"),
            evidence=str(fact.get("evidence") or "—"),
            provider=str(fact.get("provider")
                         or fact.get("source") or "—"),
            identifier=str(fact.get("provider_identifier")
                           or fact.get("identifier")
                           or fact.get("source") or "—"),
            period=str(fact.get("reporting_period")
                       or fact.get("period") or "—"),
            currency=str(fact.get("currency")
                         or fact.get("unit") or "—"),
            unit=str(fact.get("unit") or "—"),
            excel_coordinate=str(fact.get("excel_cell_coordinate")
                                 or "—"),
        )

    def _apply_agent_findings(self, node: DecisionNode, target: str,
                              target_direct: bool,
                              merged: Dict[str, Any],
                              conflict_facts: Dict[str, List[Dict[str, Any]]],
                              verdict: Any) -> DecisionNode:
        """Deterministic 12E post-pass over the solved DecisionNode.
        It never weakens the engine - it only:
          (a) surfaces a provenance-gate REVIEW as EVIDENCE_CONFLICT
              (a claimed document fact without its metadata is never
              presented as verified);
          (b) reports a directly provided target fact as
              METRIC_AVAILABLE instead of re-deriving it;
          (c) preserves BOTH values of an approved-source conflict in
              the evidence and exposes the variance on the anomalies;
          (d) enriches evidence leaves with the provider / identifier
              recorded on the source facts.
        """
        # (a) provenance review ----------------------------------------
        if (verdict is not None and verdict.verdict == GATE_REVIEW
                and node.decision in (
                    METRIC_AVAILABLE, METRIC_DERIVED, METRIC_RECONCILED,
                    METRIC_STUDENT_INPUT,
                )):
            node.decision = EVIDENCE_CONFLICT
            node.status = REVIEW_REQUIRED
            node.value = None
            node.display_value = "—"
            node.confidence_state = "review_required"
            node.reason = (
                "Provenance integrity gate requires review: "
                + "; ".join(verdict.reasons[:2])
            )
            node.next_action = "review_conflicting_evidence"

        # (b) directly provided target fact ----------------------------
        if target_direct and node.decision == METRIC_DERIVED:
            node.decision = METRIC_AVAILABLE
            node.status = VERIFIED
            node.confidence_state = "verified"
            node.reason = (
                f"{target} is directly provided as a fact; solved "
                "straight from the graph without retrieval."
            )

        # (c) conflicting approved values - both preserved --------------  
        for concept in sorted(conflict_facts):
            for fact in conflict_facts[concept]:
                leaf = self._evidence_ref_from_fact_dict(concept, fact)
                if node.evidence is None:
                    node.evidence = EvidenceTrace(
                        target=target, status=node.status, leaves=[],
                        chain=[],
                    )
                node.evidence.leaves.append(leaf)
        # expose the variance on the machine-reported anomalies
        for concept, facts in conflict_facts.items():
            values = []
            for f in facts:
                v = to_decimal(f.get("normalized_value", f.get("value")))
                if v is not None:
                    values.append(float(v))
            if len(values) >= 2:
                variance = abs(values[0] - values[1])
                for anomaly in node.anomalies:
                    if (anomaly.get("kind") in (
                            "CONFLICTING_SOURCE_VALUES",
                            "CROSS_STATEMENT_DISCREPANCY",
                            "SCALE_MISMATCH",
                            "CONFLICTING_PROVENANCE",
                    ) and anomaly.get("target") == concept):
                        anomaly["variance"] = variance

        # (d) provider / identifier enrichment from the source facts ----
        for concept, fact in merged.items():
            if not isinstance(fact, dict):
                continue
            provider = fact.get("provider")
            identifier = (fact.get("provider_identifier")
                          or fact.get("identifier"))
            for leaf in (node.evidence.leaves if node.evidence else []):
                if leaf.concept != concept:
                    continue
                if provider:
                    leaf.provider = str(provider)
                if identifier:
                    leaf.identifier = str(identifier)
        return node

    # ------------------------------------------------------------------
    @staticmethod
    def _unsupported_explanation(request: str) -> Dict[str, Any]:
        from backend.maths.agent_explainer import explain_unsupported
        return explain_unsupported(request)

    @staticmethod
    def _explain(node: DecisionNode, analysis: AgentAnalysis) -> Dict[str, Any]:
        from backend.maths.agent_explainer import explain_decision_node
        return explain_decision_node(node)

    @staticmethod
    def _termination_reason(node: DecisionNode,
                            result: Dict[str, Any]) -> str:
        if node.decision in (EVIDENCE_CONFLICT, INSUFFICIENT_EVIDENCE,
                             METRIC_BLOCKED):
            return node.reason or node.blocking_reason or (
                f"{node.target} could not be established from approved "
                "evidence."
            )
        if node.decision == "METRIC_DERIVED":
            return (
                f"{node.target} derived deterministically; retrieval loop "
                "terminated after all dependencies were satisfied."
            )
        return (
            f"{node.target} decision {node.decision}; retrieval loop "
            "terminated deterministically."
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

DEFAULT_ORCHESTRATOR = AgenticOrchestrator()


def analyze_request(request: str,
                    existing_facts: Optional[Dict[str, Any]] = None,
                    source_pools: Optional[Dict[str, Any]] = None,
                    reference: Optional[Dict[str, Any]] = None,
                    coordinate_map: Optional[Dict[str, str]] = None,
                    retrieval_timestamp: str = "") -> AgentAnalysis:
    """Convenience entry point (AgenticOrchestrator.analyze_request)."""
    return DEFAULT_ORCHESTRATOR.analyze_request(
        request, existing_facts, source_pools, reference,
        coordinate_map, retrieval_timestamp,
    )
