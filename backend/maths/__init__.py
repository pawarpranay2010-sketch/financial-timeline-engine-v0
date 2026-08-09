"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine

A deterministic, general-purpose financial mathematics engine:

    facts -> canonical fact nodes -> directed accounting graph
          -> sufficiency analysis -> forward/reverse/chained solving
          -> six-tier status propagation -> deterministic lineage

Architectural rules
-------------------
* 100% deterministic. No LLM/AI guessing inside the calculation engine.
* No arbitrary external web data enters the calculation graph.
* Formula registration is fully separated from formula application: the
  Formula Registry is declarative and extensible without touching the
  execution engine.
* Missing information -> BLOCKED with an explicit reason. Never guess,
  never interpolate, never silently substitute.
* Conflicting derivations -> REVIEW_REQUIRED (never silently chosen).
* A downstream result never claims stronger provenance than its weakest
  dependency permits.

Pure package: no Streamlit, no AI, no network. Deterministic.
"""

from backend.maths.exceptions import (
    AmbiguousEquationError,
    CycleDetectedError,
    DomainError,
    InsufficientDataError,
    MathsEngineError,
    PeriodMismatchError,
    RegistrationError,
    ScaleMismatchError,
    UnregisteredConceptError,
    UnregisteredFormulaError,
    UnitMismatchError,
)
from backend.maths.fact_model import (
    FactGraph,
    FactNode,
    build_fact_graph,
    from_pipeline_fact,
    to_decimal,
)
from backend.maths.formula_registry import (
    DEFAULT_REGISTRY,
    FormulaDefinition,
    FormulaRegistry,
    build_default_registry,
    default_registry,
    eval_expression,
)
from backend.maths.accounting_graph import (
    AccountingGraph,
    GraphNode,
)
from backend.maths.lineage import LineageInput, LineageRecord, LineageStep
from backend.maths.solver import Solution, Solver, format_value, solve_with_registry
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    propagate_statuses,
)
from backend.maths.sufficiency import (
    Sufficiency,
    SufficiencyEngine,
    analyze_sufficiency,
)

# ---------------------------------------------------------------------------
# Sprint 12B - Contextual Financial Reasoning Layer
# ---------------------------------------------------------------------------
# Deterministic reasoning built ON TOP of the 12A graph. No second engine:
# DuPont / reconciliation / adjustments all reuse the Formula Registry,
# Accounting Graph, Sufficiency Engine, Solver, six-tier statuses, and
# lineage from 12A. The C++ deterministic engine remains the mathematical
# authority where a registered formula exists.
from backend.maths.dupont import (
    ASSET_TURNOVER as DUPONT_ASSET_TURNOVER,
    ASSET_TURNOVER,
    EQUITY,
    EQUITY_MULTIPLIER,
    NET_PROFIT,
    PROFIT_MARGIN,
    RETURN_ON_EQUITY,
    REVENUE,
    TOTAL_ASSETS,
    CONTRIBUTION_METHOD,
    DUPONT_COMPONENTS,
    DUPONT_REGISTRY,
    DuPontAnalysis,
    DuPontComparison,
    DuPontComponent,
    DuPontContribution,
    DuPontEngine,
    DuPontPeriod,
    analyze_dupont,
    build_dupont_registry,
)
from backend.maths.reconciliation import (
    DEFAULT_RECONCILIATION_RULES,
    ReconciliationEngine,
    ReconciliationRegistry,
    ReconciliationResult,
    ReconciliationRule,
    build_default_rules,
)
from backend.maths.adjustments import (
    ANOMALY_DETECTED,
    ANOMALY_KINDS,
    CONFLICTING_PROVENANCE,
    CONFLICTING_SOURCE_VALUES,
    CROSS_STATEMENT_DISCREPANCY,
    DUPLICATE_FACT,
    INCOMPATIBLE_UNITS,
    KNOWN_CONCEPTS,
    MISSING_DEPENDENCY,
    NON_NEGATIVE_CONCEPTS,
    PERIOD_MISMATCH,
    SCALE_MISMATCH,
    UNEXPECTED_SIGN,
    UNSUPPORTED_LABEL,
    ZERO_DENOMINATOR,
    AdjustmentEngine,
    AdjustmentRecord,
    AnomalyCandidate,
    detect_anomalies,
    propose_adjustment,
    resolve_with_adjustments,
)

# ---------------------------------------------------------------------------
# Sprint 12C - Evidence-Aware Decision Graph & Production Integration
# ---------------------------------------------------------------------------
# Deterministic evidence/provenance/decision/Excel layer on top of the 12A
# graph and 12B reasoning. Additive only: no 12A/12B behavior is changed;
# the C++ deterministic engine remains the mathematical authority.
from backend.maths.evidence import (
    ALLOWED_SOURCE_TIERS,
    EvidenceRef,
    EvidenceTrace,
    ExternalEvidenceRecord,
    FORBIDDEN_SOURCE_TIERS,
    TIER_1_DOCUMENT,
    TIER_2_APPENDIX,
    TIER_3_REGULATORY_API,
    TIER_4_FORBIDDEN,
    describe_hierarchy,
    external_record_from_fact,
    is_allowed_source,
    render_evidence_tree,
    tier_of,
    trace_leaves,
)
from backend.maths.provenance import (
    GATE_BLOCKED,
    GATE_PASS,
    GATE_REVIEW,
    ProvenanceCheck,
    ProvenanceGate,
    ProvenanceVerdict,
    validate_provenance,
)
from backend.maths.extended_registry import (
    AVERAGE_INVENTORY,
    AVERAGE_PAYABLES,
    AVERAGE_RECEIVABLES,
    CAGR,
    CAGR_BEGINNING,
    CAGR_ENDING,
    CAGR_SPAN,
    COST_OF_SALES,
    CURRENT_RATIO,
    DEBT,
    DEBT_TO_ASSETS,
    DEBT_TO_EQUITY,
    EBITDA,
    EBITDA_MARGIN,
    EPS,
    EXTENDED_FORMULA_METADATA,
    EXTENDED_REGISTRY,
    GROSS_MARGIN,
    GROSS_PROFIT,
    INVENTORY,
    INVENTORY_TURNOVER,
    INTEREST_COVERAGE,
    INTEREST_EXPENSE,
    NET_MARGIN,
    OPERATING_MARGIN,
    OPERATING_PROFIT,
    PAYABLES_TURNOVER,
    QUICK_RATIO,
    RECEIVABLES_TURNOVER,
    ROA,
    ROE,
    SHARES_OUTSTANDING,
    build_extended_registry,
    cagr_span_from_facts,
    derive_cagr_span,
    excel_template_for,
    extended_registry,
    metadata_for,
)
from backend.maths.excel_compiler import (
    ExcelFormula,
    ExcelLineageCompiler,
    compile_excel_formula,
    render_excel_lineage_text,
    resolve_cell_reference,
)
from backend.maths.decision_graph import (
    ADJUSTMENT_REQUIRED,
    DECISION_STATES,
    DecisionGraph,
    DecisionNode,
    EVIDENCE_CONFLICT,
    INSUFFICIENT_EVIDENCE,
    METRIC_AVAILABLE,
    METRIC_BLOCKED,
    METRIC_DERIVED,
    METRIC_RECONCILED,
    METRIC_STUDENT_INPUT,
    RECONCILIATION_REQUIRED,
    confidence_for,
    decide_state,
    evaluate_metric,
    next_action_for,
    source_tier_for,
)

# ---------------------------------------------------------------------------
# Sprint 12D - Production-Grade Hardening Layer
# ---------------------------------------------------------------------------
# Deterministic production hardening around the 12A/12B/12C stack: fact
# identity isolation, adversarial normalization, restatement handling,
# tier-ordered evidence recovery, forensic reconciliation, and extended
# registry formulas. Additive only - the C++ engine remains the
# mathematical authority.
from backend.maths.identity import (
    IDENTITY_DIMENSIONS,
    IdentityIssue,
    STRICT_DIMENSIONS,
    canonical_node_ids,
    describe_fact_identity,
    detect_identity_ambiguity,
    differing_dimensions,
    group_by_identity,
    identity_key,
    same_identity,
)
from backend.maths.normalization import (
    ParseResult,
    harden_fact_text,
    normalize_value_text,
    parse_numeric_text,
)
from backend.maths.restatement import (
    CONFLICT,
    DIFFERENT_IDENTITY,
    DUPLICATE,
    INCOMPATIBLE_PERIODS,
    RESTATEMENT,
    AnalyticalFact,
    RestatementVerdict,
    classify_pair,
    classify_restatement_group,
    resolve_analytical_fact,
)
from backend.maths.recovery import (
    BLOCKED as RECOVERY_BLOCKED,
    CONFLICT as RECOVERY_CONFLICT,
    MISSING as RECOVERY_MISSING,
    RECOVERED,
    DEFAULT_RECOVERY,
    EvidenceRecoveryEngine,
    RecoveryResult,
    recover_evidence,
)
from backend.maths.forensic_reconciliation import (
    FORENSIC_RECONCILIATION_REGISTRY,
    ForensicReconciliationEngine,
    build_forensic_rules,
)

__all__ = [
    # exceptions
    "MathsEngineError", "RegistrationError", "UnregisteredFormulaError",
    "UnregisteredConceptError", "CycleDetectedError", "DomainError",
    "UnitMismatchError", "ScaleMismatchError", "PeriodMismatchError",
    "InsufficientDataError", "AmbiguousEquationError",
    # status
    "VERIFIED", "DERIVED", "RECONCILED", "STUDENT_INPUT",
    "REVIEW_REQUIRED", "BLOCKED", "propagate_statuses",
    # fact model
    "FactNode", "FactGraph", "build_fact_graph", "from_pipeline_fact",
    "to_decimal",
    # registry
    "FormulaRegistry", "FormulaDefinition", "default_registry",
    "DEFAULT_REGISTRY", "build_default_registry", "eval_expression",
    # graph / sufficiency / solver / lineage
    "AccountingGraph", "GraphNode",
    "Sufficiency", "SufficiencyEngine", "analyze_sufficiency",
    "Solver", "Solution", "solve_with_registry", "format_value",
    "LineageInput", "LineageStep", "LineageRecord",
    # ---- Sprint 12B reasoning layer ----
    # DuPont
    "DuPontEngine", "DuPontAnalysis", "DuPontPeriod", "DuPontComparison",
    "DuPontComponent", "DuPontContribution", "analyze_dupont",
    "build_dupont_registry", "DUPONT_REGISTRY", "DUPONT_COMPONENTS",
    "CONTRIBUTION_METHOD",
    "NET_PROFIT", "REVENUE", "TOTAL_ASSETS", "EQUITY", "PROFIT_MARGIN",
    "ASSET_TURNOVER", "EQUITY_MULTIPLIER", "RETURN_ON_EQUITY",
    # reconciliation
    "ReconciliationEngine", "ReconciliationRegistry",
    "ReconciliationResult", "ReconciliationRule",
    "DEFAULT_RECONCILIATION_RULES", "build_default_rules",
    # adjustments
    "AdjustmentEngine", "AnomalyCandidate", "AdjustmentRecord",
    "ANOMALY_DETECTED", "ANOMALY_KINDS", "detect_anomalies",
    "propose_adjustment", "resolve_with_adjustments",
    "CROSS_STATEMENT_DISCREPANCY", "CONFLICTING_SOURCE_VALUES",
    "DUPLICATE_FACT", "INCOMPATIBLE_UNITS", "PERIOD_MISMATCH",
    "UNEXPECTED_SIGN", "MISSING_DEPENDENCY", "ZERO_DENOMINATOR",
    "SCALE_MISMATCH", "UNSUPPORTED_LABEL", "CONFLICTING_PROVENANCE",
    "KNOWN_CONCEPTS", "NON_NEGATIVE_CONCEPTS",
    # ---- Sprint 12C evidence-aware decision graph ----
    # evidence / hierarchy
    "EvidenceRef", "EvidenceTrace", "ExternalEvidenceRecord",
    "ALLOWED_SOURCE_TIERS", "FORBIDDEN_SOURCE_TIERS", "tier_of",
    "is_allowed_source", "describe_hierarchy", "trace_leaves",
    "render_evidence_tree", "external_record_from_fact",
    "TIER_1_DOCUMENT", "TIER_2_APPENDIX", "TIER_3_REGULATORY_API",
    "TIER_4_FORBIDDEN",
    # provenance gate
    "ProvenanceGate", "ProvenanceVerdict", "ProvenanceCheck",
    "validate_provenance", "GATE_PASS", "GATE_REVIEW", "GATE_BLOCKED",
    # extended registry
    "EXTENDED_REGISTRY", "EXTENDED_FORMULA_METADATA",
    "build_extended_registry", "extended_registry", "metadata_for",
    "excel_template_for", "derive_cagr_span", "cagr_span_from_facts",
    "ROE", "ROA", "CURRENT_RATIO", "DEBT_TO_EQUITY", "GROSS_MARGIN",
    "OPERATING_MARGIN", "EBITDA_MARGIN", "CAGR", "EPS", "DEBT",
    "EBITDA", "SHARES_OUTSTANDING", "CAGR_BEGINNING", "CAGR_ENDING",
    "CAGR_SPAN", "GROSS_PROFIT", "OPERATING_PROFIT",
    "NET_MARGIN", "QUICK_RATIO", "DEBT_TO_ASSETS", "INTEREST_COVERAGE",
    "INVENTORY_TURNOVER", "RECEIVABLES_TURNOVER", "PAYABLES_TURNOVER",
    "INVENTORY", "COST_OF_SALES", "AVERAGE_INVENTORY",
    "AVERAGE_RECEIVABLES", "AVERAGE_PAYABLES", "INTEREST_EXPENSE",
    # excel compiler
    "ExcelLineageCompiler", "ExcelFormula", "compile_excel_formula",
    "resolve_cell_reference", "render_excel_lineage_text",
    # decision graph
    "DecisionGraph", "DecisionNode", "evaluate_metric", "decide_state",
    "DECISION_STATES", "METRIC_AVAILABLE", "METRIC_DERIVED",
    "METRIC_RECONCILED", "METRIC_STUDENT_INPUT", "EVIDENCE_CONFLICT",
    "RECONCILIATION_REQUIRED", "ADJUSTMENT_REQUIRED", "METRIC_BLOCKED",
    "INSUFFICIENT_EVIDENCE",    "confidence_for", "next_action_for",
    "source_tier_for",
    # ---- Sprint 12D hardening layer ----
    # fact identity (isolation)
    "IDENTITY_DIMENSIONS", "STRICT_DIMENSIONS", "IdentityIssue",
    "identity_key", "same_identity", "differing_dimensions",
    "group_by_identity", "detect_identity_ambiguity",
    "canonical_node_ids", "describe_fact_identity",
    # hardened normalization
    "ParseResult", "parse_numeric_text", "normalize_value_text",
    "harden_fact_text",
    # restatement / amendment handling
    "RestatementVerdict", "AnalyticalFact", "classify_pair",
    "resolve_analytical_fact", "classify_restatement_group",
    "DUPLICATE", "RESTATEMENT", "CONFLICT", "INCOMPATIBLE_PERIODS",
    "DIFFERENT_IDENTITY",
    # evidence recovery
    "EvidenceRecoveryEngine", "RecoveryResult", "recover_evidence",
    "DEFAULT_RECOVERY", "RECOVERED", "RECOVERY_CONFLICT",
    "RECOVERY_BLOCKED", "RECOVERY_MISSING",
    # forensic reconciliation
    "ForensicReconciliationEngine", "FORENSIC_RECONCILIATION_REGISTRY",
    "build_forensic_rules",
]
