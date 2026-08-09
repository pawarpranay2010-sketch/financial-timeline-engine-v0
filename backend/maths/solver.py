"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/solver.py

Deterministic Solver (Formula Application Engine).

The solver is GENERIC: it reads everything it needs from the Formula
Registry and the Sufficiency Engine. It never contains per-formula
arithmetic - a new registered formula is executable without any change to
this module.

Capabilities
------------
* forward solving   : Revenue + Expenses -> Profit
* reverse solving   : Revenue + Profit -> Expenses  (only when the
                      registered inverse relationship permits a unique
                      mathematically valid solution)
* chained relations : Net Profit -> Profit Margin -> ROE style chains are
                      traversed through the dependency graph
* NEVER guesses     : insufficient information -> BLOCKED with the exact
                      missing dependencies named; ambiguous derivations ->
                      REVIEW_REQUIRED with the competing derivations named.

Mathematical safety
-------------------
* division by zero, null values, invalid units, incompatible currencies /
  periods, circular dependencies, underdetermined equations, multiple
  mathematically possible solutions, and Decimal overflow/precision
  issues are all handled as structured BLOCKED / REVIEW_REQUIRED results
  with explicit reasons - never crashes, never silently substitutes.

Status propagation (weakest-link)
---------------------------------
A downstream result never claims stronger provenance than its weakest
required dependency. BLOCKED prevents computation; REVIEW_REQUIRED never
silently becomes VERIFIED or DERIVED. Conflicting derivations produce
REVIEW_REQUIRED with the reported value preserved - never silently chosen.

Arithmetic authority
--------------------
When the compiled C++ engine is available, each atomic formula step
delegates its arithmetic to the C++ engine (long double) via
backend/formula_engine_cpp; validation, graph traversal, unit/period
normalization and lineage stay in this deterministic Python layer. Values
that are not exactly representable as float64 (e.g. 1/3, or integers
above 2^53) stay on the exact Decimal path so precision is never
silently degraded by the bridge.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.maths.fact_model import FactGraph, FactNode, to_decimal
from backend.maths.exceptions import (
    CppAuthorityError,
    CppUnsupportedError,
    DomainError,
    PeriodMismatchError,
    UnitMismatchError,
)
from backend.maths.formula_registry import (
    FormulaDefinition,
    FormulaRegistry,
    default_registry,
    eval_expression,
    parse_expression,
)
from backend.maths.lineage import (
    LineageInput,
    LineageRecord,
    LineageStep,
)
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    REVIEW_REQUIRED,
    STATUS_LABELS,
    propagate_statuses,
)
from backend.maths.sufficiency import (
    AMBIGUOUS,
    SufficiencyEngine,
    Sufficiency,
)
from backend.maths.units import (
    classify_quantity,
    currencies_compatible,
    periods_compatible,
    quantities_compatible_for_add_sub,
    quantities_compatible_for_divide,
)

# ---------------------------------------------------------------------------
# C++ arithmetic delegation (optional; deterministic fallback otherwise)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard keeps the package importable
    from backend.formula_engine_cpp import (
        CPP_KEY_ALIASES,
        cpp_available,
        cpp_calculate,
        cpp_solve_metric,
        is_cpp_covered,
    )
except Exception:  # pragma: no cover
    CPP_KEY_ALIASES = {}
    cpp_available = lambda: False  # type: ignore
    cpp_calculate = None  # type: ignore
    cpp_solve_metric = None  # type: ignore
    is_cpp_covered = lambda _fid: False  # type: ignore


# ---------------------------------------------------------------------------
# Solution record
# ---------------------------------------------------------------------------


@dataclass
class Solution:
    """Deterministic result of solving one target."""

    target: str
    value: Optional[Decimal] = None
    display_value: str = "—"
    unit_kind: str = "amount"
    status: str = DERIVED
    status_label: str = ""
    formula_id: Optional[str] = None
    formula: str = "—"
    kind: str = "direct"            # direct | forward | reverse
    inputs: List[LineageInput] = field(default_factory=list)
    traversal_path: List[str] = field(default_factory=list)
    intermediates: List[LineageStep] = field(default_factory=list)
    reason: Optional[str] = None
    missing: List[str] = field(default_factory=list)
    blocked_inputs: List[str] = field(default_factory=list)
    provenance_tier: str = "—"
    source: str = "—"
    page: str = "—"
    evidence: str = "—"
    sufficiency_state: str = ""
    lineage: Optional[LineageRecord] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "unit_kind": self.unit_kind,
            "status": self.status,
            "status_label": self.status_label,
            "formula_id": self.formula_id,
            "formula": self.formula,
            "kind": self.kind,
            "inputs": [i.to_dict() for i in self.inputs],
            "traversal_path": list(self.traversal_path),
            "intermediates": [s.to_dict() for s in self.intermediates],
            "reason": self.reason,
            "missing": list(self.missing),
            "blocked_inputs": list(self.blocked_inputs),
            "provenance_tier": self.provenance_tier,
            "source": self.source,
            "page": self.page,
            "evidence": self.evidence,
            "sufficiency_state": self.sufficiency_state,
            "lineage": self.lineage.to_dict() if self.lineage else None,
        }


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------


def format_value(value: Decimal, kind: str, precision: int) -> str:
    """Display rounding policy (full precision is preserved in `value`)."""
    q = Decimal(1).scaleb(-precision)
    try:
        rounded = value.quantize(q)
    except InvalidOperation:
        rounded = value
    num = format(rounded, "f")
    if kind == "percent":
        return f"{num}%"
    return num


def _top_level_op(expr: str) -> str:
    """Outermost binary operator of an expression (used for quantity
    compatibility classification). Returns '', '+', '-', '*', or '/'."""
    node = parse_expression(expr)
    if node[0] == "bin":
        return node[1]
    return ""


def _first_ident(expr: str) -> Optional[str]:
    node = parse_expression(expr)

    def leftmost(n: Any) -> Optional[str]:
        if n[0] == "ident":
            return str(n[1])
        if n[0] in ("neg",):
            return leftmost(n[1])
        if n[0] == "bin":
            return leftmost(n[2])
        return None

    return leftmost(node)


def _last_ident(expr: str) -> Optional[str]:
    node = parse_expression(expr)

    def rightmost(n: Any) -> Optional[str]:
        if n[0] == "ident":
            return str(n[1])
        if n[0] in ("neg",):
            return rightmost(n[1])
        if n[0] == "bin":
            return rightmost(n[3])
        return None

    return rightmost(node)


# ---------------------------------------------------------------------------
# Solver context (one per top-level solve call -> deterministic + memoized)
# ---------------------------------------------------------------------------


class _SolveContext:
    def __init__(self, registry: FormulaRegistry, facts: FactGraph,
                 prefer_cpp: bool, cpp_authority: bool,
                 precision: int) -> None:
        self.registry = registry
        self.facts = facts
        self.prefer_cpp = prefer_cpp
        self.cpp_authority = cpp_authority
        self.precision = precision
        self.memo: Dict[str, Solution] = {}
        self.stack: Set[str] = set()
        self.sufficiency = SufficiencyEngine(registry)

    # ------------------------------------------------------------------
    def solve_concept(self, concept: str) -> Solution:
        if concept in self.memo:
            return self.memo[concept]
        if concept in self.stack:
            return self._blocked(
                concept, reason=f"Circular dependency detected while "
                                f"deriving {concept}.",
                missing=[concept],
            )
        self.stack.add(concept)
        sol = self._solve(concept)
        self.stack.discard(concept)
        self.memo[concept] = sol
        return sol

    def _solve(self, concept: str) -> Solution:
        fact = self.facts.get(concept)
        if fact is not None and fact.has_value():
            # Conflicting-information gate: when a registered relationship
            # derives a DIFFERENT value, the reported fact is preserved
            # but the result is REVIEW_REQUIRED - never silently choose
            # between the reported value and a derivation.
            derivations = self.sufficiency.derivations_for(
                concept, self.facts, set()
            )
            conflicts: List[Solution] = []
            for d in derivations or []:
                if d.kind == "direct":
                    continue
                sol = self._solve_derivation(concept, d)
                if (sol.value is not None
                        and sol.value != fact.value):
                    conflicts.append(sol)
            if conflicts:
                competing = " | ".join(
                    f"{c.formula_id}->{c.display_value}"
                    for c in conflicts
                )
                reported = format_value(
                    fact.value, "amount", self.precision
                )
                return Solution(
                    target=concept,
                    value=fact.value,
                    display_value=reported,
                    status=REVIEW_REQUIRED,
                    status_label=STATUS_LABELS[REVIEW_REQUIRED],
                    kind="conflict",
                    reason=f"{concept} is reported as {reported} but a "
                           f"registered relationship derives a conflicting "
                           f"value ({competing}). The reported value is "
                           "preserved; review required - never silently "
                           "choose between them.",
                    traversal_path=[concept],
                    sufficiency_state="AMBIGUOUS",
                )
            return self._direct_solution(fact)

        derivations = self.sufficiency.derivations_for(
            concept, self.facts, set()
        )
        if derivations is None:
            missing = self.sufficiency.missing_dependencies(
                concept, self.facts
            )
            if missing:
                return self._blocked(
                    concept,
                    reason=f"Insufficient verified evidence for {concept}: "
                           f"missing or invalid "
                           f"{', '.join(sorted(missing))}.",
                    missing=sorted(missing),
                )
            return self._blocked(
                concept,
                reason=f"Insufficient verified evidence for {concept}. "
                       "No registered relationship can produce it.",
                missing=[concept],
            )
        if not derivations:
            return self._blocked(
                concept,
                reason=f"Deriving {concept} would require a circular "
                       "dependency chain.",
                missing=[concept],
            )
        if len(derivations) > 1:
            return self._solve_ambiguous(concept, derivations)
        return self._solve_derivation(concept, derivations[0])

    # ------------------------------------------------------------------
    def _direct_solution(self, fact: FactNode) -> Solution:
        value = fact.value
        kind = "amount"
        status = fact.status
        return Solution(
            target=fact.node_id,
            value=value,
            display_value=format_value(value, "amount", self.precision),
            unit_kind="amount",
            status=status,
            status_label=STATUS_LABELS.get(status, status),
            kind="direct",
            formula_id=None,
            formula="—",
            traversal_path=[fact.node_id],
            intermediates=[LineageStep(
                concept=fact.node_id, formula_id="", formula="direct fact",
                value=value,
                display_value=format_value(value, "amount", self.precision),
                status=status, kind="direct",
            )],
            provenance_tier=fact.source_tier or "DOCUMENT",
            source=fact.source or "—",
            page=fact.page or "—",
            evidence=fact.evidence or "—",
            sufficiency_state="DIRECT_KNOWN",
        )

    def _blocked(self, concept: str, reason: str,
                 missing: Optional[List[str]] = None,
                 blocked_inputs: Optional[List[str]] = None,
                 sufficiency_state: str = "BLOCKED") -> Solution:
        return Solution(
            target=concept,
            value=None,
            display_value="—",
            status=BLOCKED,
            status_label=STATUS_LABELS[BLOCKED],
            kind="blocked",
            reason=reason,
            missing=missing or [],
            blocked_inputs=blocked_inputs or [],
            traversal_path=[concept],
            sufficiency_state=sufficiency_state,
        )

    # ------------------------------------------------------------------
    def _solve_ambiguous(self, concept: str, derivations) -> Solution:
        """Evaluate every derivation; REVIEW_REQUIRED when they disagree
        (multiple mathematically possible solutions). Deterministic:
        derivations are evaluated in registry order."""
        results: List[Solution] = []
        values: List[Decimal] = []
        failures: List[Solution] = []
        for d in derivations:
            sol = self._solve_derivation(concept, d)
            if sol.value is not None:
                results.append(sol)
                values.append(sol.value)
            else:
                failures.append(sol)
        distinct: List[Decimal] = []
        for v in values:
            if not any(v == x for x in distinct):
                distinct.append(v)
        if len(distinct) <= 1:
            if results:
                return results[0]
            # Sprint 12F: every derivation failed - propagate the
            # strongest authority failure (ENGINE_UNAVAILABLE over
            # UNSUPPORTED) instead of collapsing to a generic BLOCKED.
            for preferred in ("ENGINE_UNAVAILABLE", "UNSUPPORTED"):
                for f in failures:
                    if f.sufficiency_state == preferred:
                        return f
            # otherwise preserve the most informative deterministic
            # failure reason (e.g. a period/unit/domain gate) rather
            # than discarding it.
            if failures:
                return self._blocked(
                    concept,
                    reason=failures[0].reason
                    or f"No usable derivation for {concept}.",
                    missing=[concept],
                )
            return self._blocked(
                concept, reason=f"No usable derivation for {concept}.",
                missing=[concept],
            )
        competing = " | ".join(
            f"{r.formula_id or r.kind}->{r.display_value}"
            for r in results
        )
        return Solution(
            target=concept,
            value=None,
            display_value="—",
            status=REVIEW_REQUIRED,
            status_label=STATUS_LABELS[REVIEW_REQUIRED],
            kind="ambiguous",
            reason=f"{concept} has multiple mathematically possible "
                   f"solutions that disagree ({competing}). Review "
                   "required - never silently choose one.",
            traversal_path=[concept],
            sufficiency_state="AMBIGUOUS",
        )

    # ------------------------------------------------------------------
    def _solve_derivation(self, concept: str, d) -> Solution:
        if d.kind == "direct":
            fact = self.facts.get(concept)
            if fact is not None and fact.has_value():
                return self._direct_solution(fact)
            return self._blocked(concept, reason=f"{concept} is not known.")
        if d.kind == "forward":
            return self._solve_forward(concept, d)
        return self._solve_reverse(concept, d)

    # ------------------------------------------------------------------
    def _solve_forward(self, concept: str, d) -> Solution:
        formula = self.registry.require(d.formula_id)
        dep_solutions = [
            self.solve_concept(dep) for dep in d.dependencies
        ]
        blocked = [s.target for s in dep_solutions if s.status == BLOCKED]
        if blocked:
            return self._blocked(
                concept,
                reason=f"{concept} is blocked because dependency "
                       f"{', '.join(sorted(blocked))} is unavailable or "
                       "invalid.",
                blocked_inputs=blocked,
            )
        try:
            normalized = {
                dep: self._normalized_for_step(dep, dep_solutions[i], formula)
                for i, dep in enumerate(d.dependencies)
            }
            value = self._compute_forward(formula, normalized, dep_solutions)
        except CppUnsupportedError as exc:
            return self._blocked(
                concept, reason=str(exc), sufficiency_state="UNSUPPORTED",
            )
        except CppAuthorityError as exc:
            return self._blocked(
                concept, reason=str(exc),
                sufficiency_state="ENGINE_UNAVAILABLE",
            )
        except (DomainError, UnitMismatchError, PeriodMismatchError,
                ValueError, InvalidOperation) as exc:
            return self._blocked(
                concept, reason=f"{concept} cannot be calculated: {exc}",
            )
        status = propagate_statuses([s.status for s in dep_solutions])
        reason = None
        if status == REVIEW_REQUIRED:
            reason = "One or more inputs require review - the result is " \
                     "computed but never presented as verified."
        inputs = self._lineage_inputs(dep_solutions)
        path = self._merge_paths([s.traversal_path for s in dep_solutions])
        step = LineageStep(
            concept=concept, formula_id=formula.formula_id,
            formula=formula.expression,
            value=value,
            display_value=format_value(value, formula.unit_kind, self.precision),
            status=status, kind="forward", inputs=inputs,
        )
        return Solution(
            target=concept,
            value=value,
            display_value=format_value(value, formula.unit_kind, self.precision),
            unit_kind=formula.unit_kind,
            status=status,
            status_label=STATUS_LABELS.get(status, status),
            formula_id=formula.formula_id,
            formula=formula.expression,
            kind="forward",
            inputs=inputs,
            traversal_path=path + [concept],
            intermediates=[step],
            reason=reason,
            provenance_tier="DERIVED",
            sufficiency_state="FORWARD_SOLVABLE",
        )

    # ------------------------------------------------------------------
    def _solve_reverse(self, concept: str, d) -> Solution:
        formula = self.registry.require(d.formula_id)
        variable = d.variable or concept
        target_solution = self.solve_concept(formula.target)
        other = [
            dep for dep in formula.dependencies if dep != variable
        ]
        other_solutions = [self.solve_concept(dep) for dep in other]
        inputs_solutions = [target_solution] + other_solutions
        blocked = [s.target for s in inputs_solutions if s.status == BLOCKED]
        if blocked:
            return self._blocked(
                concept,
                reason=f"Cannot reverse-solve {concept}: dependency "
                       f"{', '.join(sorted(blocked))} is unavailable or "
                       "invalid.",
                blocked_inputs=blocked,
            )
        try:
            normalized = {
                var: self._normalized_for_step(var, sol, formula)
                for var, sol in zip(
                    [formula.target] + other, inputs_solutions
                )
            }
            value = self._compute_reverse(
                formula, variable, normalized, inputs_solutions
            )
        except CppUnsupportedError as exc:
            return self._blocked(
                concept, reason=str(exc), sufficiency_state="UNSUPPORTED",
            )
        except CppAuthorityError as exc:
            return self._blocked(
                concept, reason=str(exc),
                sufficiency_state="ENGINE_UNAVAILABLE",
            )
        except (DomainError, UnitMismatchError, PeriodMismatchError,
                ValueError, InvalidOperation) as exc:
            return self._blocked(
                concept, reason=f"Cannot reverse-solve {concept}: {exc}",
            )
        var_kind = formula.unit_kind if variable == formula.target else "amount"
        status = propagate_statuses([s.status for s in inputs_solutions])
        reason = None
        if status == REVIEW_REQUIRED:
            reason = "One or more inputs require review - the result is " \
                     "computed but never presented as verified."
        inputs = self._lineage_inputs(inputs_solutions)
        path = self._merge_paths([s.traversal_path for s in inputs_solutions])
        step = LineageStep(
            concept=concept, formula_id=formula.formula_id,
            formula=f"reverse({formula.expression})",
            value=value,
            display_value=format_value(value, var_kind, self.precision),
            status=status, kind="reverse", inputs=inputs,
        )
        return Solution(
            target=concept,
            value=value,
            display_value=format_value(value, var_kind, self.precision),
            unit_kind=var_kind,
            status=status,
            status_label=STATUS_LABELS.get(status, status),
            formula_id=formula.formula_id,
            formula=f"reverse: {formula.expression}",
            kind="reverse",
            inputs=inputs,
            traversal_path=path + [concept],
            intermediates=[step],
            reason=reason,
            provenance_tier="DERIVED",
            sufficiency_state="REVERSE_SOLVABLE",
        )

    # ------------------------------------------------------------------
    # Normalization + validation + arithmetic
    # ------------------------------------------------------------------

    def _normalized_for_step(self, var: str, sol: Solution,
                             formula: FormulaDefinition) -> Decimal:
        """Working (normalized) value of one input. Facts that were
        constructed with apply_scale=True are normalized to absolute
        units here; pipeline facts are already normalized."""
        fact = self.facts.get(var)
        value = sol.value
        if value is None:
            raise ValueError(f"{var} has no numeric value.")
        if fact is not None and fact.apply_scale:
            from backend.maths.units import normalize_value
            value = normalize_value(
                value, fact.original_scale, fact.original_unit
            )
        return value

    def _validate_step(self, formula: FormulaDefinition,
                       normalized: Dict[str, Decimal]) -> None:
        """Deterministic validation gates for one formula application.
        Raises DomainError / UnitMismatchError / PeriodMismatchError with
        explicit reasons - never crashes, never silently converts."""
        deps = formula.dependencies
        # quantity compatibility
        op = _top_level_op(formula.expression)
        facts_meta = {
            v: self.facts.get(v) for v in deps
        }
        kinds = {
            v: classify_quantity(
                (f.original_unit or f.normalized_unit) if f else None
            ) for v, f in facts_meta.items()
        }
        if op in ("+", "-"):
            for i in range(1, len(deps)):
                reason = quantities_compatible_for_add_sub(
                    (kinds[deps[0]], kinds[deps[i]])
                )
                if reason:
                    raise UnitMismatchError(reason)
        elif op == "/":
            num = _first_ident(formula.expression)
            den = _last_ident(formula.expression)
            if num and den:
                reason = quantities_compatible_for_divide(
                    kinds.get(num), kinds.get(den)
                )
                if reason:
                    raise UnitMismatchError(reason)
        # currency compatibility (never convert)
        currencies = {
            str((f.currency or "").upper())
            for v in deps if (f := facts_meta.get(v)) and f.currency
        }
        if len(currencies) > 1:
            raise UnitMismatchError(
                f"Currency mismatch between inputs "
                f"({', '.join(sorted(currencies))})."
            )
        # period compatibility
        if formula.period_mode in ("same", "different"):
            periods = [
                (facts_meta[v].period if facts_meta[v] else None)
                for v in deps
            ]
            for i in range(1, len(periods)):
                reason = periods_compatible(
                    periods[0], periods[i], formula.period_mode
                )
                if reason:
                    raise PeriodMismatchError(reason)
        # identity isolation (Sprint 12D/12F): facts combined into ONE
        # formula step must share the strict analytical dimensions that
        # are not formula-driven. Entity and period-type mismatches are
        # never silently merged; `period` itself is governed by
        # period_mode above and `statement` legitimately differs (e.g.
        # income statement + balance sheet inputs for ROE).
        for dim in ("entity", "period_type"):
            values: Set[str] = set()
            for v in deps:
                f = facts_meta.get(v)
                val = str(getattr(f, dim, None) or "").strip()
                if val:
                    values.add(val.upper())
            if len(values) > 1:
                label = dim.replace("_", " ").title()
                raise PeriodMismatchError(
                    f"{label} mismatch between inputs "
                    f"({', '.join(sorted(values))}) - facts with "
                    "different analytical identity are never merged "
                    "silently."
                )
        # denominator constraints
        for den in formula.denominator_constraints:
            if normalized.get(den) == 0:
                raise DomainError(
                    f"Division by zero: {den} is zero - "
                    f"{formula.target} cannot be calculated."
                )
        # domain rules
        for label, rule in formula.domain_rules:
            problem = rule(normalized)
            if problem:
                raise DomainError(f"{label}: {problem}")

    def _compute_forward(self, formula: FormulaDefinition,
                         normalized: Dict[str, Decimal],
                         dep_solutions: List[Solution]) -> Decimal:
        """Deterministic arithmetic for the forward direction.

        The exact Decimal evaluation is always computed. The C++ engine is
        consulted for each atomic step, but its result is used ONLY when
        it reproduces the exact Decimal value (i.e. the long-double
        bridge lost no precision); otherwise the exact Decimal result is
        authoritative. Precision is never silently degraded by the
        bridge - results are byte-identical on every run."""
        self._validate_step(formula, normalized)
        if self.cpp_authority:
            return self._authority_forward(formula, normalized)
        exact, _used = eval_expression(formula.expression, normalized)
        cpp_value = self._cpp_forward(formula, normalized)
        value = exact if cpp_value is None else cpp_value
        if cpp_value is not None and cpp_value != exact:
            value = exact  # bridge lost precision - exact result stands
        if formula.unit_kind == "percent":
            value = value * Decimal(100)
        return value

    def _compute_reverse(self, formula: FormulaDefinition, variable: str,
                         normalized: Dict[str, Decimal],
                         input_solutions: List[Solution]) -> Decimal:
        self._validate_step(formula, normalized)
        inv_expr = formula.inverses.get(variable)
        if inv_expr is None:
            raise ValueError(
                f"No registered inverse relationship for {variable} in "
                f"{formula.formula_id}."
            )
        if self.cpp_authority:
            return self._authority_reverse(formula, variable, normalized)
        exact, _used = eval_expression(inv_expr, normalized)
        cpp_value = self._cpp_reverse(formula, variable, normalized)
        if cpp_value is None or cpp_value != exact:
            return exact  # no bridge or precision loss - exact result
        return cpp_value

    # ------------------------------------------------------------------
    @staticmethod
    def _cpp_exact(normalized: Dict[str, Decimal]) -> bool:
        """True when every value is exactly representable as a float64
        (Decimal.from_float round-trip). Values that would lose precision
        crossing the C++ JSON bridge (e.g. 1/3, or integers above 2^53)
        stay on the exact Decimal path - deterministic precision is a
        hard guarantee and the bridge must never silently degrade it."""
        for v in normalized.values():
            try:
                fv = float(v)
            except (TypeError, ValueError, OverflowError):
                return False
            try:
                back = Decimal.from_float(fv)
            except Exception:
                return False
            if back != v:
                return False
        return True

    def _cpp_forward(self, formula: FormulaDefinition,
                     normalized: Dict[str, Decimal]) -> Optional[Decimal]:
        if not self.prefer_cpp or cpp_calculate is None:
            return None
        if not self._cpp_exact(normalized):
            return None  # exact Decimal path preserves full precision
        try:
            facts = self._cpp_facts(formula, normalized)
            out = cpp_calculate(formula.formula_id, facts)
        except Exception:
            return None
        if out is None or out.get("value") is None:
            return None
        if str(out.get("status")) == "blocked":
            return None  # Python path applies its own (equivalent) gates
        return to_decimal(out.get("value"))

    def _cpp_reverse(self, formula: FormulaDefinition, variable: str,
                     normalized: Dict[str, Decimal]) -> Optional[Decimal]:
        if not self.prefer_cpp or cpp_solve_metric is None:
            return None
        if not self._cpp_exact(normalized):
            return None  # exact Decimal path preserves full precision
        try:
            facts = self._cpp_facts(formula, normalized)
            out = cpp_solve_metric(formula.formula_id, variable, facts)
        except Exception:
            return None
        if out is None or out.get("value") is None:
            return None
        return to_decimal(out.get("value"))

    def _cpp_facts(self, formula: FormulaDefinition,
                   normalized: Dict[str, Decimal]) -> Dict[str, Dict[str, Any]]:
        facts: Dict[str, Dict[str, Any]] = {}
        for var in list(formula.dependencies) + [formula.target]:
            if var not in normalized:
                continue
            node = self.facts.get(var)
            facts[var] = {
                "metric": var,
                "value": float(normalized[var]),
                "unit": (node.original_unit or node.normalized_unit)
                        if node else "",
                "scale": node.original_scale if node else "",
                "reporting_period": node.period if node else "",
                "provenance_tier": node.source_tier if node else "",
            }
        return facts

    # ------------------------------------------------------------------
    # Sprint 12F - strict C++ mathematical authority (no Python fallback)
    # ------------------------------------------------------------------
    def _authority_preconditions(self, formula: FormulaDefinition) -> None:
        """Sprint 12F strict gate. Python NEVER computes a financial
        result as a fallback: binary missing -> ENGINE_UNAVAILABLE, formula
        not covered by the C++ registry -> UNSUPPORTED."""
        if cpp_calculate is None or cpp_solve_metric is None \
                or not cpp_available():
            raise CppAuthorityError(
                "C++ mathematical authority is unavailable (no compiled "
                "formula engine binary). Production calculation is "
                "BLOCKED - no Python fallback is performed."
            )
        if not is_cpp_covered(formula.formula_id):
            raise CppUnsupportedError(
                f"{formula.formula_id} is not covered by the C++ "
                "mathematical authority - UNSUPPORTED (never silently "
                "computed in Python)."
            )

    def _authority_forward(self, formula: FormulaDefinition,
                           normalized: Dict[str, Decimal]) -> Decimal:
        """Sprint 12F strict forward: the financial result MUST come from
        the C++ engine. Python validates (orchestration), passes inputs
        and converts the C++ fraction to the percent-number display
        convention - it never performs the arithmetic."""
        self._authority_preconditions(formula)
        facts = self._cpp_facts(formula, normalized)
        out = cpp_calculate(
            CPP_KEY_ALIASES.get(formula.formula_id, formula.formula_id),
            facts,
        )
        if out is None:
            raise CppAuthorityError(
                "C++ engine call failed (missing binary, non-zero exit or "
                "malformed response). Production calculation is BLOCKED - "
                "no Python fallback is performed."
            )
        if str(out.get("status")) == "blocked":
            raise DomainError(
                out.get("block_reason")
                or "C++ mathematical authority blocked the calculation."
            )
        if out.get("value") is None:
            raise CppAuthorityError(
                "C++ mathematical authority returned no value. Production "
                "calculation is BLOCKED - no Python fallback is performed."
            )
        value = to_decimal(out.get("value"))
        if formula.unit_kind == "percent":
            value = value * Decimal(100)  # display convention only
        return value

    def _authority_reverse(self, formula: FormulaDefinition, variable: str,
                           normalized: Dict[str, Decimal]) -> Decimal:
        """Sprint 12F strict reverse: only REGISTERED inverse relationships
        are executed, and they are executed by the C++ authority."""
        if formula.inverses.get(variable) is None:
            raise CppUnsupportedError(
                f"No registered inverse relationship for {variable} in "
                f"{formula.formula_id} - UNSUPPORTED (never invented)."
            )
        self._authority_preconditions(formula)
        facts = self._cpp_facts(formula, normalized)
        out = cpp_solve_metric(
            CPP_KEY_ALIASES.get(formula.formula_id, formula.formula_id),
            variable, facts,
        )
        if out is None:
            raise CppAuthorityError(
                "C++ engine call failed (missing binary, non-zero exit or "
                "malformed response). Production calculation is BLOCKED - "
                "no Python fallback is performed."
            )
        if str(out.get("status")) == "blocked":
            raise DomainError(
                out.get("block_reason")
                or "C++ mathematical authority blocked the reverse "
                "calculation."
            )
        if out.get("value") is None:
            raise CppAuthorityError(
                "C++ mathematical authority returned no value. Production "
                "calculation is BLOCKED - no Python fallback is performed."
            )
        return to_decimal(out.get("value"))

    # ------------------------------------------------------------------
    def _lineage_inputs(self, solutions: List[Solution]) -> List[LineageInput]:
        return [
            LineageInput(
                concept=s.target,
                value=s.value,
                display_value=s.display_value,
                status=s.status,
                provenance_tier=s.provenance_tier,
                source=s.source,
                page=s.page,
                evidence=s.evidence,
            )
            for s in solutions
        ]

    @staticmethod
    def _merge_paths(paths: List[List[str]]) -> List[str]:
        """Deterministic deduped merge of dependency traversal paths."""
        out: List[str] = []
        for p in paths:
            for node in p:
                if node not in out:
                    out.append(node)
        return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class Solver:
    """Deterministic formula application engine over a registry."""

    def __init__(self, registry: Optional[FormulaRegistry] = None,
                 prefer_cpp: bool = True,
                 cpp_authority: bool = False) -> None:
        # Default to the shared Phase-1 registry (deterministic); callers
        # may pass their own extensible registry instance.
        self.registry = registry if registry is not None else default_registry()
        self.prefer_cpp = prefer_cpp
        # Sprint 12F: when True every atomic financial step MUST be
        # computed by the C++ engine (no Python fallback). Default False
        # preserves the exact 12A-12E additive behavior.
        self.cpp_authority = cpp_authority

    def solve(self, target: str, facts: FactGraph,
              display_precision: int = 2) -> Solution:
        """Solve ONE target against the fact graph. Deterministic.

        `facts` may also be a pipeline-shaped dict; use
        build_fact_graph() to construct a FactGraph first, or pass a
        FactGraph directly.
        """
        if not isinstance(facts, FactGraph):
            raise TypeError(
                "facts must be a backend.maths.fact_model.FactGraph "
                "(build one with build_fact_graph())."
            )
        ctx = _SolveContext(
            self.registry, facts, self.prefer_cpp, self.cpp_authority,
            display_precision,
        )
        sol = ctx.solve_concept(target)
        # Assemble the complete lineage: one step per node along the
        # deterministic traversal path (dependencies first).
        steps: List[LineageStep] = []
        for node_id in sol.traversal_path:
            node_sol = ctx.memo.get(node_id)
            if node_sol is not None and node_sol.intermediates:
                steps.append(node_sol.intermediates[0])
        sol.lineage = LineageRecord(
            target=target,
            status=sol.status,
            formula_id=sol.formula_id,
            formula=sol.formula,
            value=sol.value,
            display_value=sol.display_value,
            reason=sol.reason,
            steps=steps,
            traversal_path=list(sol.traversal_path),
        )
        return sol

    def solve_many(self, targets: List[str], facts: FactGraph) -> Dict[str, Solution]:
        """Solve several targets against the same fact graph; each target
        gets its own deterministic context (no cross-contamination)."""
        return {t: self.solve(t, facts) for t in targets}

    def can_solve(self, target: str, facts: FactGraph) -> bool:
        """True when the target is directly known or derivable."""
        if not isinstance(facts, FactGraph):
            raise TypeError("facts must be a FactGraph.")
        fact = facts.get(target)
        if fact is not None and fact.has_value():
            return True
        return (
            self.registry.is_registered_target(target)
            or self.registry.can_reverse_solve(target)
        )

    def analyze(self, target: str, facts: FactGraph) -> Sufficiency:
        """Deterministic sufficiency verdict (without evaluation)."""
        return SufficiencyEngine(self.registry).analyze(target, facts)


def solve_with_registry(target: str, facts: FactGraph,
                        registry: FormulaRegistry,
                        prefer_cpp: bool = True,
                        cpp_authority: bool = False) -> Solution:
    """Module-level convenience."""
    return Solver(
        registry, prefer_cpp=prefer_cpp, cpp_authority=cpp_authority,
    ).solve(target, facts)
