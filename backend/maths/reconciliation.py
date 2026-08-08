"""
Financial Timeline Engine
Sprint 12B - Contextual Financial Reasoning Layer
backend/maths/reconciliation.py

Forensic Cross-Statement Reconciliation (deterministic, rules-based).

The engine reconciles a reported fact against an EXPECTED value produced by
a REGISTERED accounting relationship (bridge items included), e.g.:

    Net Profit (reported)
        vs
    Retained Earnings Ending - Retained Earnings Beginning + Dividends Paid

Matching rules (before ANY comparison):
  * canonical concept        - reconciled against the registered target
  * period                  - never reconcile FY2025 against FY2024 merely
                              because labels match; periods must be explicit
  * fiscal period type      - annual vs quarterly never silently mixed
  * currency                - never compare USD against INR
  * scale / unit            - values are normalized; unknown scales and
                              incompatible quantity kinds are rejected
  * source statement        - cross-statement rules require INDEPENDENT
                              source statements
  * provenance              - BLOCKED / unanalyzable sources fail closed

Variance rule:
    abs(variance) >= tolerance  ->  REVIEW_REQUIRED
All original source values, provenance and evidence are PRESERVED. The
engine never overwrites, smooths, averages or replaces one number with
another - it produces a structured reconciliation error payload.

If the relationship cannot be safely established:
    missing information  -> BLOCKED
    actual discrepancy   -> REVIEW_REQUIRED

Pure module: no Streamlit, no AI, no network. Deterministic. The expected
value is computed by the Sprint 12A Solver (C++ arithmetic authority where
registered; exact Decimal precision guard otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fact_model import FactNode, build_fact_graph, to_decimal
from backend.maths.formula_registry import FormulaDefinition, FormulaRegistry
from backend.maths.solver import Solver
from backend.maths.status import (
    BLOCKED,
    RECONCILED,
    REVIEW_REQUIRED,
    is_computable,
    weaker,
)
from backend.maths.units import (
    classify_quantity,
    quantities_compatible_for_add_sub,
    scale_multiplier,
)

# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationRule:
    """One registered reconciliation relationship (declarative).

    expected_expression is evaluated over `sources` by the Sprint 12A
    safe expression evaluator (bridge items included, e.g. Dividends Paid).
    period_mode "same" requires every non-empty period to be identical;
    period_mode "strap" requires the prior-period sources (retained-earnings
    style opening balances) to carry the PREVIOUS period while everything
    else carries the target period.
    """

    rule_id: str
    target: str
    expected_expression: str
    sources: List[str]
    period_mode: str = "same"                      # "same" | "strap"
    prior_period_sources: List[str] = field(default_factory=list)
    tolerance_rel: Decimal = Decimal("0.05")       # relative tolerance (5%)
    tolerance_abs: Decimal = Decimal("0")          # absolute tolerance override
    unit_kind: str = "amount"
    requires_distinct_statements: bool = True
    description: str = ""
    version: str = "1.0"
    source_ref: str = ""

    def expected_target(self) -> str:
        return f"Expected {self.target} ({self.rule_id})"


class ReconciliationRegistry:
    """Versioned reconciliation-rule registry.

    Each rule also registers a hidden FormulaDefinition so the expected
    value is computed through the standard Solver (status propagation,
    lineage and the C++ oracle for free).
    """

    def __init__(self) -> None:
        self._rules: Dict[str, ReconciliationRule] = {}
        self._formulas: FormulaRegistry = FormulaRegistry()

    def register(self, rule: ReconciliationRule) -> ReconciliationRule:
        if rule.rule_id in self._rules:
            raise ValueError(
                f"Reconciliation rule {rule.rule_id!r} is already registered."
            )
        if rule.period_mode not in ("same", "strap"):
            raise ValueError(
                f"Rule {rule.rule_id}: invalid period_mode {rule.period_mode!r}."
            )
        # Registration-time validation via FormulaDefinition (fail fast).
        FormulaDefinition(
            formula_id=f"REC_{rule.rule_id}",
            target=rule.expected_target(),
            expression=rule.expected_expression,
            dependencies=list(rule.sources),
            unit_kind=rule.unit_kind if rule.unit_kind in (
                "amount", "ratio", "percent") else "amount",
            period_mode="any",  # period validation is the engine's matching gate
            version=rule.version,
            source_ref=rule.source_ref,
        )
        self._rules[rule.rule_id] = rule
        self._formulas.register(FormulaDefinition(
            formula_id=f"REC_{rule.rule_id}",
            target=rule.expected_target(),
            expression=rule.expected_expression,
            dependencies=list(rule.sources),
            unit_kind=rule.unit_kind if rule.unit_kind in (
                "amount", "ratio", "percent") else "amount",
            period_mode="any",
            version=rule.version,
            source_ref=rule.source_ref,
        ))
        return rule

    def get(self, rule_id: str) -> Optional[ReconciliationRule]:
        return self._rules.get(rule_id)

    def require(self, rule_id: str) -> ReconciliationRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Reconciliation rule {rule_id!r} is not registered.")
        return rule

    def all_rules(self) -> List[ReconciliationRule]:
        return list(self._rules.values())

    def registry(self) -> FormulaRegistry:
        return self._formulas


# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------


def build_default_rules() -> ReconciliationRegistry:
    reg = ReconciliationRegistry()
    reg.register(ReconciliationRule(
        rule_id="RE_STRAP_NET_PROFIT",
        target="Net Profit",
        expected_expression=(
            "Retained Earnings Ending - Retained Earnings Beginning "
            "+ Dividends Paid"
        ),
        sources=[
            "Retained Earnings Ending",
            "Retained Earnings Beginning",
            "Dividends Paid",
        ],
        period_mode="strap",
        prior_period_sources=["Retained Earnings Beginning"],
        tolerance_rel=Decimal("0.05"),
        description=(
            "Retained-earnings bridge: Net Profit = Retained Earnings delta "
            "+ Dividends (subject to documented adjustments)."
        ),
        version="1.0",
        source_ref=(
            "Accounting identity: RE(End) = RE(Begin) + Net Profit - "
            "Dividends +/- other documented adjustments"
        ),
    ))
    reg.register(ReconciliationRule(
        rule_id="CF_IDENTITY_NET_PROFIT",
        target="Net Profit",
        expected_expression="Net Profit Cash Flow",
        sources=["Net Profit Cash Flow"],
        period_mode="same",
        tolerance_rel=Decimal("0.05"),
        description=(
            "Cash-flow net income identity: Income Statement Net Profit "
            "vs Cash Flow Statement net income for the same period."
        ),
        version="1.0",
        source_ref="Cross-statement identity (IS vs CF)",
    ))
    return reg


DEFAULT_RECONCILIATION_RULES = build_default_rules()


# ---------------------------------------------------------------------------
# Result payload (Sprint 12B structured reconciliation error)
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationResult:
    reconciliation_id: str
    target: str
    rule_id: Optional[str] = None
    status: str = BLOCKED
    expected_relationship: str = ""
    observed_value: Optional[Decimal] = None
    expected_value: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    absolute_variance: Optional[Decimal] = None
    relative_variance: Optional[Decimal] = None
    relative_variance_note: str = ""
    tolerance: Optional[Decimal] = None
    periods: Dict[str, Any] = field(default_factory=dict)
    currencies: Dict[str, Any] = field(default_factory=dict)
    units: Dict[str, Any] = field(default_factory=dict)
    source_nodes: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reconciliation_id": self.reconciliation_id,
            "target": self.target,
            "rule_id": self.rule_id,
            "status": self.status,
            "expected_relationship": self.expected_relationship,
            "observed_value": (
                float(self.observed_value)
                if self.observed_value is not None else None
            ),
            "expected_value": (
                float(self.expected_value)
                if self.expected_value is not None else None
            ),
            "variance": float(self.variance) if self.variance is not None else None,
            "absolute_variance": (
                float(self.absolute_variance)
                if self.absolute_variance is not None else None
            ),
            "relative_variance": (
                float(self.relative_variance)
                if self.relative_variance is not None else None
            ),
            "relative_variance_note": self.relative_variance_note,
            "tolerance": float(self.tolerance) if self.tolerance is not None else None,
            "periods": dict(self.periods),
            "currencies": dict(self.currencies),
            "units": dict(self.units),
            "source_nodes": list(self.source_nodes),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ReconciliationEngine:
    """Deterministic reconciliation over the Sprint 12A machinery."""

    def __init__(self, registry: Optional[ReconciliationRegistry] = None,
                 prefer_cpp: bool = True) -> None:
        self.registry = registry if registry is not None else DEFAULT_RECONCILIATION_RULES
        self.prefer_cpp = prefer_cpp

    # ------------------------------------------------------------------
    def reconcile(self, rule: ReconciliationRule, reported_fact: Dict[str, Any],
                  facts: Dict[str, Any],
                  tolerance_rel: Optional[Decimal] = None,
                  tolerance_abs: Optional[Decimal] = None,
                  reported_statement: Optional[str] = None,
                  ) -> ReconciliationResult:
        """Reconcile a reported fact against the rule's expected value.

        Returns a structured payload with status RECONCILED /
        REVIEW_REQUIRED / BLOCKED. Original values, provenance and evidence
        are always preserved - never overwritten or smoothed.
        """
        rule = self.registry.require(rule.rule_id) if isinstance(rule, str) else rule
        rid = f"REC-{rule.rule_id}"
        result = ReconciliationResult(
            reconciliation_id=rid,
            target=rule.target,
            rule_id=rule.rule_id,
            expected_relationship=(
                f"{rule.expected_expression}  [{rule.description}]"
            ),
            tolerance=(
                tolerance_abs if tolerance_abs is not None
                else tolerance_rel if tolerance_rel is not None
                else max(rule.tolerance_abs, Decimal(0))
            ),
        )

        # -- matching validation gates ---------------------------------
        problem = self._validate_matching(
            rule, reported_fact, facts, reported_statement
        )
        if problem is not None:
            kind, reason = problem
            result.status = BLOCKED if kind == "missing" else REVIEW_REQUIRED
            result.reason = reason
            self._attach_metadata(rule, reported_fact, facts, result)
            return result

        observed = self._fact_value(reported_fact)

        # -- expected value through the Solver -------------------------
        graph = build_fact_graph(facts or {})
        solver = Solver(self.registry.registry(), prefer_cpp=self.prefer_cpp)
        expected_sol = solver.solve(rule.expected_target(), graph)
        if expected_sol.status == BLOCKED:
            result.status = BLOCKED
            result.reason = (
                f"Expected value for {rule.target} is BLOCKED: "
                f"{expected_sol.reason or 'required evidence unavailable'}"
            )
            self._attach_metadata(rule, reported_fact, facts, result)
            result.expected_value = None
            return result
        if expected_sol.status == REVIEW_REQUIRED:
            # A reviewed source never silently reconciles.
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"Expected value for {rule.target} requires review - the "
                "reconciliation is never presented as verified."
            )
            self._attach_metadata(rule, reported_fact, facts, result,
                                  expected_sol=expected_sol)
            result.expected_value = expected_sol.value
            return result

        expected = expected_sol.value
        result.expected_value = expected
        result.observed_value = observed

        # -- variance + tolerance (deterministic) ----------------------
        variance = (observed or Decimal(0)) - (expected or Decimal(0))
        abs_var = abs(variance)
        rel_var: Optional[Decimal] = None
        note = ""
        if expected != 0:
            rel_var = abs_var / abs(expected)
        else:
            note = "Relative variance is undefined (expected value is zero)."
        tol_rel = tolerance_rel if tolerance_rel is not None else rule.tolerance_rel
        tol_abs = tolerance_abs if tolerance_abs is not None else rule.tolerance_abs
        threshold = max(tol_abs, tol_rel * abs(expected))
        result.variance = variance
        result.absolute_variance = abs_var
        result.relative_variance = rel_var
        result.relative_variance_note = note
        result.tolerance = threshold

        if abs_var <= threshold:
            result.status = RECONCILED
            result.reason = (
                f"{rule.target} reconciles within tolerance: observed "
                f"{observed} vs expected {expected} (variance {abs_var} "
                f"<= tolerance {threshold})."
            )
        else:
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"{rule.target} shows a material variance: observed "
                f"{observed} vs expected {expected} (variance {abs_var} "
                f">= tolerance {threshold}). Review required - original "
                "values preserved, no correction applied."
            )
        self._attach_metadata(rule, reported_fact, facts, result,
                              expected_sol=expected_sol)
        return result

    # ------------------------------------------------------------------
    def reconcile_cross_statement(self, target: str, fact_a: Dict[str, Any],
                                  statement_a: str, fact_b: Dict[str, Any],
                                  statement_b: str,
                                  tolerance_rel: Decimal = Decimal("0.05"),
                                  tolerance_abs: Decimal = Decimal("0"),
                                  ) -> ReconciliationResult:
        """Direct two-source reconciliation of the same concept from two
        independent statements (e.g. Income Statement vs Cash Flow)."""
        result = ReconciliationResult(
            reconciliation_id=f"REC-CROSS-{target}",
            target=target,
            status=BLOCKED,
            expected_relationship=(
                f"{target} ({statement_a}) == {target} ({statement_b}) "
                "[cross-statement identity, same period]"
            ),
            tolerance=max(tolerance_abs, Decimal(0)),
        )

        def node_meta(label: str, fact: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "label": label,
                "value": float(self._fact_value(fact))
                if self._fact_value(fact) is not None else None,
                "period": fact.get("reporting_period") or fact.get("period"),
                "currency": fact.get("currency_code") or fact.get("currency")
                or fact.get("unit"),
                "unit": fact.get("unit"),
                "scale": fact.get("scale"),
                "provenance_tier": fact.get("provenance_tier"),
                "source": fact.get("source"),
                "page": fact.get("page"),
                "evidence": fact.get("evidence"),
            }

        # -- matching validation (direct comparison) --------------------
        va = self._fact_value(fact_a)
        vb = self._fact_value(fact_b)
        if va is None or vb is None:
            result.reason = (
                f"BLOCKED: {target} is missing a usable numeric value in "
                "one of the two statements."
            )
            result.observed_value = va
            result.expected_value = vb
            return result

        pa = fact_a.get("reporting_period") or fact_a.get("period")
        pb = fact_b.get("reporting_period") or fact_b.get("period")
        if not pa or not pb:
            result.reason = (
                f"BLOCKED: {target} periods are not explicit in both "
                "statements - never reconciled on label match alone."
            )
            result.observed_value = va
            result.expected_value = vb
            return result
        if pa != pb:
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"PERIOD MISMATCH: {target} {statement_a} is {pa} but "
                f"{statement_b} is {pb} - never reconciled across periods."
            )
            result.observed_value = va
            result.expected_value = vb
            return result

        ta = fact_a.get("period_type")
        tb = fact_b.get("period_type")
        if ta and tb and ta != tb:
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"FISCAL PERIOD TYPE MISMATCH: {ta} vs {tb} for {target}."
            )
            result.observed_value = va
            result.expected_value = vb
            return result

        ca = (fact_a.get("currency_code") or fact_a.get("currency")
              or fact_a.get("unit") or "").strip().upper()
        cb = (fact_b.get("currency_code") or fact_b.get("currency")
              or fact_b.get("unit") or "").strip().upper()
        if ca and cb and ca != cb:
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"CURRENCY MISMATCH: {target} is {ca} vs {cb} - never "
                "converted silently."
            )
            result.observed_value = va
            result.expected_value = vb
            return result

        if statement_a == statement_b:
            result.reason = (
                f"BLOCKED: {target} requires two INDEPENDENT source "
                f"statements, but both facts come from {statement_a}."
            )
            result.observed_value = va
            result.expected_value = vb
            return result

        # -- variance + tolerance ---------------------------------------
        variance = va - vb
        abs_var = abs(variance)
        rel_var = abs_var / abs(vb) if vb != 0 else None
        threshold = max(tolerance_abs, tolerance_rel * abs(vb))
        result.observed_value = va
        result.expected_value = vb
        result.variance = variance
        result.absolute_variance = abs_var
        result.relative_variance = rel_var
        result.tolerance = threshold
        result.periods = {"reported": pa, "expected": pb}
        result.currencies = {"reported": ca or "unknown", "expected": cb or "unknown"}
        result.units = {
            "reported": fact_a.get("unit") or "unknown",
            "expected": fact_b.get("unit") or "unknown",
        }
        result.source_nodes = [node_meta(statement_a, fact_a),
                               node_meta(statement_b, fact_b)]
        if abs_var <= threshold:
            result.status = RECONCILED
            result.reason = (
                f"{target} reconciles across statements within tolerance "
                f"(variance {abs_var} <= tolerance {threshold})."
            )
        else:
            result.status = REVIEW_REQUIRED
            result.reason = (
                f"{target} shows a material cross-statement variance "
                f"(variance {abs_var} >= tolerance {threshold}). Review "
                "required - original values preserved."
            )
        return result

    # ------------------------------------------------------------------
    # Matching validation gates
    # ------------------------------------------------------------------

    def _validate_matching(
        self, rule: ReconciliationRule, reported_fact: Dict[str, Any],
        facts: Dict[str, Any], reported_statement: Optional[str],
    ) -> Optional[Tuple[str, str]]:
        """Return (kind, reason) when the relationship cannot be safely
        established. kind is 'missing' (-> BLOCKED) or 'discrepancy'
        (-> REVIEW_REQUIRED). None means the comparison may proceed."""
        observed = self._fact_value(reported_fact)
        if observed is None:
            return ("missing",
                    f"{rule.target} (reported) has no usable numeric value.")
        reported_status = str(reported_fact.get("status") or
                              reported_fact.get("extraction_state") or "")
        if reported_status and not is_computable(reported_status):
            return ("missing",
                    f"{rule.target} (reported) is not computable "
                    f"(status {reported_status}).")

        # every source must exist and be usable
        source_facts: Dict[str, Dict[str, Any]] = {}
        for src in rule.sources:
            f = (facts or {}).get(src)
            if not isinstance(f, dict):
                return ("missing",
                        f"Required reconciliation source {src!r} is absent.")
            if self._fact_value(f) is None:
                return ("missing",
                        f"Required reconciliation source {src!r} has no "
                        "usable numeric value.")
            source_facts[src] = f

        # period gates (explicit periods required; never label-matched)
        target_period = (
            reported_fact.get("reporting_period")
            or reported_fact.get("period") or ""
        ).strip()
        if not target_period:
            return ("missing",
                    f"{rule.target} (reported) has no explicit period.")
        periods: Dict[str, str] = {rule.target: target_period}
        for src in rule.sources:
            p = (source_facts[src].get("reporting_period")
                 or source_facts[src].get("period") or "").strip()
            if not p:
                return ("missing",
                        f"Source {src!r} has no explicit period - "
                        "never reconciled on label match alone.")
            periods[src] = p
        if rule.period_mode == "same":
            distinct = {v for v in periods.values() if v}
            if len(distinct) > 1:
                return ("discrepancy",
                        f"PERIOD MISMATCH: sources carry different periods "
                        f"({sorted(distinct)}) - never reconciled across "
                        "periods.")
        else:  # strap
            for src in rule.sources:
                if src in rule.prior_period_sources:
                    if periods[src] == target_period:
                        return ("discrepancy",
                                f"{src} must carry the PRIOR period "
                                f"(got {periods[src]} == target period "
                                f"{target_period}).")
                else:
                    if periods[src] != target_period:
                        return ("discrepancy",
                                f"{src} must carry the target period "
                                f"{target_period} (got {periods[src]}).")

        # fiscal period type
        types = {
            (reported_fact.get("period_type") or "").strip(),
            *(str(source_facts[s].get("period_type") or "").strip()
              for s in rule.sources),
        }
        types.discard("")
        if len(types) > 1:
            return ("discrepancy",
                    f"FISCAL PERIOD TYPE MISMATCH ({sorted(types)}).")

        # currency (never converted); unit label is a currency hint
        def _cur(f: Dict[str, Any]) -> str:
            return (
                f.get("currency_code") or f.get("currency")
                or f.get("unit") or ""
            ).strip().upper()

        currs = {
            _cur(reported_fact),
            *(_cur(source_facts[s]) for s in rule.sources),
        }
        currs.discard("")
        if len(currs) > 1:
            return ("discrepancy",
                    f"CURRENCY MISMATCH ({sorted(currs)}) - never "
                    "converted silently.")

        # scale / unit (unknown scales never guessed)
        for s in rule.sources:
            f = source_facts[s]
            scale = f.get("scale")
            if scale not in (None, "") and scale_multiplier(scale) is None:
                return ("missing",
                        f"Source {s!r} has unknown scale {scale!r} - "
                        "cannot normalize.")
        kinds = [
            classify_quantity(f.get("unit"))
            for f in [reported_fact] + list(source_facts.values())
        ]
        for i in range(1, len(kinds)):
            problem = quantities_compatible_for_add_sub((kinds[0], kinds[i]))
            if problem:
                return ("discrepancy", problem)

        # statement independence
        if rule.requires_distinct_statements and reported_statement:
            for s in rule.sources:
                src_stmt = source_facts[s].get("source")
                if src_stmt and reported_statement and \
                        str(src_stmt) == str(reported_statement):
                    return ("missing",
                            f"{rule.target} requires INDEPENDENT source "
                            "statements; both come from the same source "
                            f"({reported_statement}).")
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _fact_value(fact: Dict[str, Any]) -> Optional[Decimal]:
        return to_decimal(fact.get("normalized_value", fact.get("value")))

    def _attach_metadata(self, rule: ReconciliationRule,
                         reported_fact: Dict[str, Any],
                         facts: Dict[str, Any],
                         result: ReconciliationResult,
                         expected_sol=None) -> None:
        target_period = (
            reported_fact.get("reporting_period")
            or reported_fact.get("period") or "unknown"
        )
        result.periods = {"reported": target_period}
        result.currencies = {
            "reported": (reported_fact.get("currency_code")
                         or reported_fact.get("currency") or "unknown")
        }
        result.units = {"reported": reported_fact.get("unit") or "unknown"}
        source_nodes: List[Dict[str, Any]] = []
        if expected_sol is not None:
            for i in expected_sol.inputs:
                sf = (facts or {}).get(i.concept) or {}
                source_nodes.append({
                    "concept": i.concept,
                    "value": float(i.value) if i.value is not None else None,
                    "display_value": i.display_value,
                    "status": i.status,
                    "provenance_tier": i.provenance_tier,
                    "source": i.source,
                    "page": i.page,
                    "evidence": i.evidence,
                })
                result.periods[f"source:{i.concept}"] = str(
                    sf.get("reporting_period") or sf.get("period")
                    or target_period
                )
                result.currencies[f"source:{i.concept}"] = str(
                    sf.get("currency_code") or sf.get("currency")
                    or sf.get("unit") or "unknown"
                )
                result.units[f"source:{i.concept}"] = str(
                    sf.get("unit") or "unknown"
                )
        else:
            for s in rule.sources:
                f = (facts or {}).get(s) or {}
                source_nodes.append({
                    "concept": s,
                    "value": float(self._fact_value(f))
                    if self._fact_value(f) is not None else None,
                    "provenance_tier": f.get("provenance_tier"),
                    "source": f.get("source"),
                    "page": f.get("page"),
                    "evidence": f.get("evidence"),
                })
        result.source_nodes = source_nodes
