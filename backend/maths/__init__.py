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
]
