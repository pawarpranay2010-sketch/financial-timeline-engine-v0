"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/sufficiency.py

Data Sufficiency Engine.

Given a requested target and the currently known facts, determine whether
the target is:

    DIRECT_KNOWN         already known as a fact
    FORWARD_SOLVABLE     produced by a registered equation whose inputs
                         are known (possibly via further derivation)
    REVERSE_SOLVABLE     solvable as a variable of a registered equation
                         whose other variables are known
    CHAINED_SOLVABLE     requires multi-step dependency traversal
    INSUFFICIENT         no registered relationship can produce it
    BLOCKED              a required dependency is explicitly blocked
    AMBIGUOUS            multiple mathematically possible derivations
    CYCLE                derivation would loop forever

The engine NEVER guesses missing values: if information is insufficient,
the state is INSUFFICIENT (-> BLOCKED at execution) with an explicit list
of missing dependencies.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from backend.maths.fact_model import FactGraph, FactNode, to_decimal
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.status import (
    BLOCKED,
    REVIEW_REQUIRED,
    is_computable,
)

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

DIRECT_KNOWN = "DIRECT_KNOWN"
FORWARD_SOLVABLE = "FORWARD_SOLVABLE"
REVERSE_SOLVABLE = "REVERSE_SOLVABLE"
CHAINED_SOLVABLE = "CHAINED_SOLVABLE"
INSUFFICIENT = "INSUFFICIENT"
BLOCKED = "BLOCKED_STATE"
AMBIGUOUS = "AMBIGUOUS"
CYCLE = "CYCLE"

ALL_STATES = (
    DIRECT_KNOWN, FORWARD_SOLVABLE, REVERSE_SOLVABLE, CHAINED_SOLVABLE,
    INSUFFICIENT, BLOCKED, AMBIGUOUS, CYCLE,
)


@dataclass
class Derivation:
    """One way to derive the target.

    kind     "direct" | "forward" | "reverse"
    formula_id   registered formula used (None for direct)
    variable     reverse: the target variable being solved
    formula_target  reverse: the formula's own target concept
    dependencies   concepts that must be known/derivable
    """

    kind: str
    formula_id: Optional[str] = None
    variable: Optional[str] = None
    formula_target: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    uses_inverse: bool = False

    def describe(self) -> str:
        if self.kind == "direct":
            return f"{self.variable or ''} is directly known"
        if self.kind == "forward":
            return f"{self.formula_id}: {self.formula_target or self.variable} = f({', '.join(self.dependencies)})"
        return (
            f"{self.formula_id} inverse: {self.variable} solved from "
            f"{self.formula_target or ''} and {', '.join(self.dependencies)}"
        )


@dataclass
class Sufficiency:
    """Structured sufficiency verdict."""

    target: str
    state: str
    reason: str = ""
    missing: List[str] = field(default_factory=list)
    derivations: List[Derivation] = field(default_factory=list)
    blocked_inputs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "target": self.target,
            "state": self.state,
            "reason": self.reason,
            "missing": list(self.missing),
            "derivations": [d.describe() for d in self.derivations],
            "blocked_inputs": list(self.blocked_inputs),
        }


class SufficiencyEngine:
    """Deterministic sufficiency analysis over facts + registry.

    Deliberately stateless (no memoization of derivations): derivations
    depend on the recursion stack for cycle-guarding, so caching a result
    computed under one stack could poison a different derivation path.
    The solver memoizes final Solutions instead - that is where repeated
    evaluation is avoided.
    """

    def __init__(self, registry: FormulaRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    def derivations_for(self, target: str, facts: FactGraph,
                        stack: Optional[Set[str]] = None) -> Optional[List[Derivation]]:
        """All top-level derivations for `target`, or None when the target
        is not derivable at all. Deterministic: forward formulas in
        registration order, then reverse formulas in registration order.
        Recursion guards against cycles via `stack`.

        A fact with a value but a non-computable status (e.g. BLOCKED) is
        NOT a valid direct derivation: blocked facts can never satisfy a
        dependency (fail closed)."""
        stack = stack if stack is not None else set()
        if target in stack:
            return []  # cycle - no finite derivation exists

        stack.add(target)
        out: List[Derivation] = []
        direct_fact = facts.get(target)
        if (direct_fact is not None and direct_fact.has_value()
                and is_computable(direct_fact.status)):
            out.append(Derivation(
                kind="direct", variable=target, dependencies=[],
            ))

        # Forward: formulas producing the target.
        for formula in self.registry.formulas_for_target(target):
            ok = True
            for dep in formula.dependencies:
                if not self._satisfiable(dep, facts, stack):
                    ok = False
                    break
            if ok:
                out.append(Derivation(
                    kind="forward", formula_id=formula.formula_id,
                    variable=target, formula_target=target,
                    dependencies=list(formula.dependencies),
                ))

        # Reverse: formulas consuming the target as a dependency.
        for formula in self.registry.formulas_consuming(target):
            if formula.target == target:
                continue  # already covered as forward
            if target not in formula.inverses:
                continue  # no inverse registered for this variable
            # formula target must be satisfiable (known or derivable)
            if not self._satisfiable(formula.target, facts, stack):
                continue
            ok = True
            for dep in formula.dependencies:
                if dep == target:
                    continue
                if not self._satisfiable(dep, facts, stack):
                    ok = False
                    break
            if ok:
                out.append(Derivation(
                    kind="reverse", formula_id=formula.formula_id,
                    variable=target, formula_target=formula.target,
                    dependencies=[d for d in formula.dependencies if d != target],
                    uses_inverse=True,
                ))
        stack.discard(target)
        return out or None

    def _satisfiable(self, concept: str, facts: FactGraph,
                     stack: Set[str]) -> bool:
        if concept in stack:
            return False  # cycle guard
        f = facts.get(concept)
        if f is not None and f.has_value() and is_computable(f.status):
            return True
        return self.derivations_for(concept, facts, stack) is not None

    def missing_dependencies(self, target: str, facts: FactGraph) -> List[str]:
        """All dependencies that block EVERY registered derivation of
        `target` (deterministic, sorted). Facts that are blocked, absent,
        or not derivable are named - the engine never guesses them."""
        missing: List[str] = []
        candidates: List = list(
            self.registry.formulas_for_target(target)
        )
        candidates += [
            f for f in self.registry.formulas_consuming(target)
            if f.target != target and target in f.inverses
        ]
        for formula in candidates:
            if formula.target == target:
                reqs: List[str] = list(formula.dependencies)
            else:
                reqs = [
                    d for d in formula.dependencies if d != target
                ] + [formula.target]
            for dep in reqs:
                f = facts.get(dep)
                usable = (
                    f is not None and f.has_value()
                    and is_computable(f.status)
                )
                if usable:
                    continue
                if self.derivations_for(dep, facts) is not None:
                    continue
                if dep not in missing:
                    missing.append(dep)
        return sorted(missing)

    # ------------------------------------------------------------------
    def analyze(self, target: str, facts: FactGraph) -> Sufficiency:
        """Deterministic sufficiency verdict for `target` against facts."""
        fact = facts.get(target)
        if fact is not None and fact.has_value():
            if not is_computable(fact.status):
                return Sufficiency(
                    target=target, state=BLOCKED,
                    reason=f"{target} is directly known but its status "
                           f"({fact.status}) blocks computation.",
                    blocked_inputs=[target],
                )
            return Sufficiency(
                target=target, state=DIRECT_KNOWN,
                reason=f"{target} is directly known from the fact graph.",
                derivations=[Derivation(kind="direct", variable=target)],
            )

        derivations = self.derivations_for(target, facts)
        if derivations is None:
            # Either no registered relationship touches the target, or
            # every registered relationship is missing required inputs.
            missing = self.missing_dependencies(target, facts)
            if missing:
                return Sufficiency(
                    target=target, state=INSUFFICIENT,
                    reason=f"Insufficient verified evidence: {target} "
                           f"requires {', '.join(missing)}, which is "
                           "unavailable or not derivable.",
                    missing=missing,
                )
            return Sufficiency(
                target=target, state=INSUFFICIENT,
                reason=f"No registered mathematical relationship exists for "
                       f"{target}, and it is not a known fact.",
                missing=[target],
            )
        if not derivations:
            return Sufficiency(
                target=target, state=CYCLE,
                reason=f"Deriving {target} would require a circular "
                       "dependency chain.",
            )

        # Distinguish states by the derivations found.
        blocked_inputs: List[str] = []
        for formula in self.registry.formulas_consuming(target):
            for dep in formula.dependencies:
                f = facts.get(dep)
                if f is not None and not is_computable(f.status):
                    if dep not in blocked_inputs:
                        blocked_inputs.append(dep)
            f = facts.get(formula.target)
            if f is not None and not is_computable(f.status):
                if formula.target not in blocked_inputs:
                    blocked_inputs.append(formula.target)
        if blocked_inputs:
            return Sufficiency(
                target=target, state=BLOCKED,
                reason="A required dependency is blocked, so the target "
                       "cannot be computed.",
                blocked_inputs=blocked_inputs,
                derivations=derivations,
            )

        kinds = {d.kind for d in derivations}
        if len(derivations) > 1:
            return Sufficiency(
                target=target, state=AMBIGUOUS,
                reason=f"{target} has multiple mathematically possible "
                       "derivations; the solver compares them and marks "
                       "REVIEW_REQUIRED when they disagree.",
                derivations=derivations,
            )
        d = derivations[0]
        if d.kind == "forward":
            multi = any(
                self._derivation_is_chained(dep, facts)
                for dep in d.dependencies
            )
            state = CHAINED_SOLVABLE if multi else FORWARD_SOLVABLE
            label = "forward-solvable" + (" (multi-step)" if multi else "")
        else:
            state = REVERSE_SOLVABLE
            label = "reverse-solvable"
        return Sufficiency(
            target=target, state=state,
            reason=f"{target} is {label} via {d.formula_id}.",
            derivations=[d],
        )

    def _derivation_is_chained(self, concept: str, facts: FactGraph) -> bool:
        """True when `concept` is not a direct fact but derivable."""
        f = facts.get(concept)
        if f is not None and f.has_value():
            return False
        return self.derivations_for(concept, facts) is not None


def analyze_sufficiency(target: str, facts: FactGraph,
                        registry: FormulaRegistry) -> Sufficiency:
    """Module-level convenience."""
    return SufficiencyEngine(registry).analyze(target, facts)
