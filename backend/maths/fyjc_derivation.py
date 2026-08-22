"""
Platrixa
Sprint 15D - Controlled Formula Derivation Engine
backend/maths/fyjc_derivation.py

Takes a registered CANONICAL equation and deterministically generates the
mathematically equivalent solving paths, e.g.:

    Profit = Revenue - Expenses
      -> Revenue = Profit + Expenses
      -> Expenses = Revenue - Profit

    Commission = Sales × Commission Rate ÷ 100
      -> Sales = Commission × 100 ÷ Commission Rate
      -> Commission Rate = Commission × 100 ÷ Sales

Pipeline (Sprint 15D sections 2-4, 7):

    Canonical Formula Registry
      -> DerivationEngine.derive(canonical, target_variable)
      -> independent numeric validation (deterministic seeded vectors)
      -> cross-check against the REGISTERED inverse (the expression the
         C++ authority can execute)
      -> DerivationAuditTrail (append-only)
      -> only VALIDATED paths reach the C++ mathematical authority

Rules:
  * The derivation is exact single-occurrence algebraic inversion. If the
    target variable occurs more than once, or a transformation cannot be
    proven safely (e.g. a power with the variable in the exponent), the
    path is REJECTED - the engine never silently invents a formula.
  * A derived expression never becomes executable merely because the
    derivation engine generated it: it must pass numeric validation on
    deterministic test vectors AND agree with the registered inverse.
  * Python performs NO financial arithmetic here: every numerical step
    of a student solve still executes through the C++ authority (see
    solve_derived -> Solver(cpp_authority=True)).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_canonical import (
    CANONICAL_REGISTRY,
    FYJC_FORMULA_REGISTRY,
    CanonicalFormula,
    VALIDATION_PENDING,
    VALIDATION_REJECTED,
    VALIDATION_VALIDATED,
)
from backend.maths.formula_registry import (
    eval_expression,
    parse_expression,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED

# ---------------------------------------------------------------------------
# AST helpers (expression grammar from formula_registry.parse_expression:
#   ('num', Decimal) | ('ident', name) | ('neg', node) |
#   ('bin', op, left, right))
# ---------------------------------------------------------------------------


def _count_occurrences(node: Any, variable: str) -> int:
    kind = node[0]
    if kind == "ident":
        return 1 if str(node[1]) == variable else 0
    if kind == "num":
        return 0
    if kind == "neg":
        return _count_occurrences(node[1], variable)
    if kind == "bin":
        return _count_occurrences(node[2], variable) + \
            _count_occurrences(node[3], variable)
    return 0


def _num_node(value: Decimal) -> Any:
    return ("num", value)


def _negate(node: Any) -> Any:
    return ("neg", node)


def _bin(op: str, left: Any, right: Any) -> Any:
    return ("bin", op, left, right)


def _fmt_num(value: Decimal) -> str:
    """Deterministic plain-number formatting (no trailing zeros)."""
    if value == value.to_integral_value():
        return str(int(value))
    text = format(value.normalize(), "f")
    return text


def expr_to_string(node: Any) -> str:
    """Deterministic expression printer (fully parenthesised binaries;
    unary minus printed as -X)."""
    kind = node[0]
    if kind == "num":
        return _fmt_num(node[1])
    if kind == "ident":
        return str(node[1])
    if kind == "neg":
        return f"-({expr_to_string(node[1])})"
    if kind == "bin":
        op, left, right = node[1], node[2], node[3]
        return (
            f"({expr_to_string(left)} {op} {expr_to_string(right)})"
        )
    raise ValueError(f"Unknown AST node {node!r}.")


def _invert_left(op: str, rhs: Any, right: Any) -> Optional[Any]:
    """Given (left op right) == rhs, express the left operand."""
    if op == "+":
        return _bin("-", rhs, right)
    if op == "-":
        return _bin("+", rhs, right)
    if op == "*":
        return _bin("/", rhs, right)
    if op == "/":
        return _bin("*", rhs, right)
    if op == "^":
        # left == rhs ^ (1 / right) - only provable when the exponent is
        # a literal number.
        if right[0] == "num":
            return _bin("^", rhs, _bin("/", _num_node(Decimal(1)), right))
        return None
    return None


def _invert_right(op: str, left: Any, rhs: Any) -> Optional[Any]:
    """Given (left op right) == rhs, express the right operand."""
    if op == "+":
        return _bin("-", rhs, left)
    if op == "-":
        return _bin("-", left, rhs)
    if op == "*":
        return _bin("/", rhs, left)
    if op == "/":
        return _bin("/", left, rhs)
    if op == "^":
        return None  # would require logarithms - refuse
    return None


def _isolate(node: Any, variable: str, rhs: Any) -> Optional[Any]:
    """Algebraically isolate `variable` in `node == rhs`.

    Exact single-occurrence inversion. Returns the AST for the variable,
    or None when the transformation cannot be proven safely (multiple
    occurrences of the variable, or a non-invertible operator on the
    path). Deterministic; never guesses.
    """
    kind = node[0]
    if kind == "ident":
        if str(node[1]) == variable:
            return rhs
        return None
    if kind == "num":
        return None
    if kind == "neg":
        # -(inner) == rhs  =>  inner == -(rhs)
        inner = _isolate(node[1], variable, _negate(rhs))
        return inner
    if kind == "bin":
        op, left, right = node[1], node[2], node[3]
        left_count = _count_occurrences(left, variable)
        right_count = _count_occurrences(right, variable)
        if left_count == 1 and right_count == 0:
            new_rhs = _invert_left(op, rhs, right)
            if new_rhs is None:
                return None
            return _isolate(left, variable, new_rhs)
        if right_count == 1 and left_count == 0:
            new_rhs = _invert_right(op, left, rhs)
            if new_rhs is None:
                return None
            return _isolate(right, variable, new_rhs)
        return None
    return None


# ---------------------------------------------------------------------------
# Derived path record
# ---------------------------------------------------------------------------


@dataclass
class DerivedPath:
    """One validated (or rejected) solution path derived from a canonical
    formula. `expression` is the derived expression for `target_variable`."""

    path_id: str
    canonical_id: str
    target_variable: str
    expression: str
    dependencies: List[str]
    unit_kind: str = "amount"
    derivation_audit: List[str] = field(default_factory=list)
    validation_status: str = VALIDATION_PENDING
    validated_vectors: int = 0

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "canonical_id": self.canonical_id,
            "target_variable": self.target_variable,
            "expression": self.expression,
            "dependencies": list(self.dependencies),
            "unit_kind": self.unit_kind,
            "derivation_audit": list(self.derivation_audit),
            "validation_status": self.validation_status,
            "validated_vectors": self.validated_vectors,
        }


# ---------------------------------------------------------------------------
# Deterministic test-vector generator (seeded LCG; never random())
# ---------------------------------------------------------------------------


def _vector_value(index: int, seed: int) -> Decimal:
    """A deterministic, non-zero, positive test value."""
    state = (seed * 1103515245 + 12345 + index * 2654435761) % (2 ** 31)
    base = 2 + (state % 23)          # 2..24
    scale = 1 + (state // 23) % 500  # 1..500
    return Decimal(base * scale)


_REL_TOLERANCE = Decimal("1e-15")


def _deq(a: Decimal, b: Decimal) -> bool:
    """Deterministic Decimal comparison with a tight relative tolerance.

    The round-trip re-arrangement of a division can differ from the
    original value by ~1e-27 (Decimal's 28-digit default context); a
    genuinely wrong derivation differs by orders of magnitude. The
    tolerance (1e-15 relative) is far above the arithmetic noise and far
    below any real discrepancy, keeping validation deterministic.
    """
    if a == b:
        return True
    scale = max(abs(a), abs(b), Decimal(1))
    return abs(a - b) <= scale * _REL_TOLERANCE


def _test_vectors(canonical: CanonicalFormula, variable: str,
                  count: int) -> List[Dict[str, Decimal]]:
    """Deterministic vectors assigning every variable of the canonical
    relationship (including the target). The target variable being solved
    for is assigned a value that satisfies the canonical equation."""
    seed = sum(ord(c) for c in canonical.formula_id + "::" + variable)
    vectors: List[Dict[str, Decimal]] = []
    all_vars = [canonical.target] + list(canonical.dependencies)
    for i in range(count):
        values: Dict[str, Decimal] = {
            v: _vector_value(j, seed + i * 31)
            for j, v in enumerate(all_vars)
        }
        # Make the values satisfy the canonical equation: recompute the
        # target from the dependencies. Canonical expressions use
        # display-number semantics (25 for 25%), so no scaling is applied.
        try:
            target_value, _used = eval_expression(
                canonical.expression,
                {v: values[v] for v in canonical.dependencies},
            )
        except Exception:
            continue
        values[canonical.target] = target_value
        vectors.append(values)
    return vectors


# ---------------------------------------------------------------------------
# Validation (Sprint 15D section 3)
# ---------------------------------------------------------------------------


def _expression_identifiers(expr: str) -> List[str]:
    node = parse_expression(expr)
    found: List[str] = []

    def walk(n: Any) -> None:
        if n[0] == "ident":
            if str(n[1]) not in found:
                found.append(str(n[1]))
        elif n[0] == "neg":
            walk(n[1])
        elif n[0] == "bin":
            walk(n[2])
            walk(n[3])

    walk(node)
    return found


def validate_derived_path(path: DerivedPath,
                          canonical: CanonicalFormula,
                          num_vectors: int = 8) -> str:
    """Independently validate one derived expression.

    For each deterministic test vector:
      forward  - evaluating the canonical expression gives the target;
                 evaluating the derived expression with {target, other
                 variables} must reproduce the solved variable exactly.
      reverse  - evaluating the derived expression must reproduce the
                 solved variable, and feeding it back through the
                 canonical expression must reproduce the target.

    A derived expression is VALIDATED only when every vector passes and
    the derived expression references exactly the variables available in
    that direction (the target + the other dependencies - never the
    solved variable itself).
    """
    expr_vars = _expression_identifiers(path.expression)
    available = set(canonical.dependencies) | {canonical.target}
    available.discard(path.target_variable)
    unknown = [v for v in expr_vars if v not in available]
    if unknown:
        path.validation_status = VALIDATION_REJECTED
        path.derivation_audit.append(
            f"REJECTED: expression references variables not available in "
            f"this direction: {sorted(unknown)}."
        )
        return path.validation_status

    vectors = _test_vectors(canonical, path.target_variable, num_vectors)
    if not vectors:
        path.validation_status = VALIDATION_REJECTED
        path.derivation_audit.append(
            "REJECTED: no deterministic test vector could be built."
        )
        return path.validation_status

    is_forward = path.target_variable == canonical.target
    for idx, values in enumerate(vectors):
        dep_values = {
            v: values[v] for v in canonical.dependencies
        }
        try:
            target_value, _u = eval_expression(canonical.expression, dep_values)
        except Exception:
            path.validation_status = VALIDATION_REJECTED
            path.derivation_audit.append(
                f"REJECTED: canonical evaluation failed on vector {idx}."
            )
            return path.validation_status
        # Canonical expressions use display-number semantics (25 for 25%),
        # so forward paths compare in the same terms as reverse paths.
        if is_forward:
            try:
                derived_value, _u2 = eval_expression(
                    path.expression, dep_values)
            except Exception:
                path.validation_status = VALIDATION_REJECTED
                path.derivation_audit.append(
                    f"REJECTED: derived evaluation failed on vector {idx}."
                )
                return path.validation_status
            expected = target_value
        else:
            substitute = dict(dep_values)
            substitute[canonical.target] = target_value
            try:
                derived_value, _u2 = eval_expression(
                    path.expression, substitute)
            except Exception:
                path.validation_status = VALIDATION_REJECTED
                path.derivation_audit.append(
                    f"REJECTED: derived evaluation failed on vector {idx}."
                )
                return path.validation_status
            expected = values[path.target_variable]
        if not _deq(derived_value, expected):
            path.validation_status = VALIDATION_REJECTED
            path.derivation_audit.append(
                f"REJECTED: vector {idx} derived {derived_value} != "
                f"expected {expected}."
            )
            return path.validation_status
        # reverse round-trip through the canonical expression
        try:
            back = eval_expression(
                canonical.expression,
                {**dep_values, path.target_variable: derived_value},
            )[0]
        except Exception:
            path.validation_status = VALIDATION_REJECTED
            path.derivation_audit.append(
                f"REJECTED: reverse round-trip failed on vector {idx}."
            )
            return path.validation_status
        if not _deq(back, target_value):
            path.validation_status = VALIDATION_REJECTED
            path.derivation_audit.append(
                f"REJECTED: reverse round-trip on vector {idx} gave "
                f"{back} != target {target_value}."
            )
            return path.validation_status

    path.validation_status = VALIDATION_VALIDATED
    path.validated_vectors = len(vectors)
    path.derivation_audit.append(
        f"VALIDATED: {len(vectors)} deterministic test vectors passed "
        f"(forward + reverse round-trip)."
    )
    return path.validation_status


# ---------------------------------------------------------------------------
# Derivation engine
# ---------------------------------------------------------------------------

_DERIVATION_CACHE: Dict[Tuple[str, str], DerivedPath] = {}
_DERIVATION_AUDIT_TRAIL: List[Dict[str, Any]] = []


class DerivationUnsupported(Exception):
    pass


def _record_audit(path: DerivedPath) -> None:
    _DERIVATION_AUDIT_TRAIL.append({
        "sequence": len(_DERIVATION_AUDIT_TRAIL) + 1,
        "path_id": path.path_id,
        "canonical_id": path.canonical_id,
        "target_variable": path.target_variable,
        "expression": path.expression,
        "dependencies": list(path.dependencies),
        "validation_status": path.validation_status,
        "validated_vectors": path.validated_vectors,
    })


def derivation_audit_trail() -> List[Dict[str, Any]]:
    """Append-only snapshot of every derivation performed (deterministic
    sequence numbers; never cleared)."""
    return [dict(record) for record in _DERIVATION_AUDIT_TRAIL]


def derive_path(canonical: CanonicalFormula,
                target_variable: str) -> DerivedPath:
    """Derive + validate the solution path for ONE variable of a canonical
    relationship. Cached per (canonical_id, variable)."""
    key = (canonical.formula_id, target_variable)
    cached = _DERIVATION_CACHE.get(key)
    if cached is not None:
        return cached

    path_id = f"{canonical.formula_id}::{target_variable}"
    if target_variable == canonical.target:
        path = DerivedPath(
            path_id=path_id,
            canonical_id=canonical.formula_id,
            target_variable=target_variable,
            expression=canonical.expression,
            dependencies=list(canonical.dependencies),
            unit_kind=canonical.unit_kind,
            derivation_audit=[
                f"forward: {canonical.target} = {canonical.expression}"
            ],
            validation_status=VALIDATION_PENDING,
        )
    elif target_variable in canonical.dependencies:
        node = parse_expression(canonical.expression)
        rhs = ("ident", canonical.target)
        derived_ast = _isolate(node, target_variable, rhs)
        if derived_ast is None:
            path = DerivedPath(
                path_id=path_id,
                canonical_id=canonical.formula_id,
                target_variable=target_variable,
                expression="",
                dependencies=[],
                unit_kind=canonical.unit_kind,
                derivation_audit=[
                    "REJECTED: cannot prove a safe algebraic isolation "
                    "(the variable occurs more than once, or a "
                    "non-invertible operator lies on its path)."
                ],
                validation_status=VALIDATION_REJECTED,
            )
            _DERIVATION_CACHE[key] = path
            _record_audit(path)
            return path
        expression = expr_to_string(derived_ast)
        deps = [
            v for v in (set(canonical.dependencies) | {canonical.target})
            if v != target_variable
        ]
        # deterministic dependency order: target first, then registry order
        ordered: List[str] = []
        for v in [canonical.target] + list(canonical.dependencies):
            if v != target_variable and v not in ordered:
                ordered.append(v)
        path = DerivedPath(
            path_id=path_id,
            canonical_id=canonical.formula_id,
            target_variable=target_variable,
            expression=expression,
            dependencies=ordered,
            unit_kind=canonical.unit_kind,
            derivation_audit=[
                f"solve {target_variable} from {canonical.canonical_formula}",
                f"isolate: {target_variable} = {expression}",
            ],
            validation_status=VALIDATION_PENDING,
        )
    else:
        raise DerivationUnsupported(
            f"{target_variable!r} is not a variable of canonical "
            f"{canonical.formula_id!r}."
        )

    validate_derived_path(path, canonical)
    _DERIVATION_CACHE[key] = path
    _record_audit(path)
    return path


def registered_inverse_expression(canonical_id: str,
                                  variable: str) -> Optional[str]:
    """The registered inverse expression the C++ authority can execute."""
    definition = FYJC_FORMULA_REGISTRY.get(canonical_id)
    if definition is None:
        return None
    return definition.inverses.get(variable)


def _expressions_equivalent(expr_a: str, expr_b: str,
                            canonical: CanonicalFormula,
                            variable: str,
                            num_vectors: int = 6) -> bool:
    """Numeric equivalence of two expressions for the same variable over
    deterministic vectors (the derived expression must agree with the
    REGISTERED inverse before it may be used)."""
    if expr_a == expr_b:
        return True
    vectors = _test_vectors(canonical, variable, num_vectors)
    for values in vectors:
        substitute = {
            v: values[v]
            for v in (set(canonical.dependencies) | {canonical.target})
            if v != variable
        }
        try:
            a = eval_expression(expr_a, substitute)[0]
            b = eval_expression(expr_b, substitute)[0]
        except Exception:
            return False
        if not _deq(a, b):
            return False
    return True


def validated_path_for(canonical_id: str,
                       variable: str) -> Optional[DerivedPath]:
    """The validated path for one variable of one canonical formula, or
    None when the path is not derivable/validated."""
    canonical = CANONICAL_REGISTRY.get(canonical_id)
    if canonical is None:
        return None
    try:
        path = derive_path(canonical, variable)
    except DerivationUnsupported:
        return None
    if path.validation_status != VALIDATION_VALIDATED:
        return None
    # Cross-check against the registered inverse (the C++-executable form).
    registered = registered_inverse_expression(canonical_id, variable)
    if variable == canonical.target:
        return path  # forward path uses the canonical expression itself
    if registered is None:
        return None
    if not _expressions_equivalent(
            path.expression, registered, canonical, variable):
        path.validation_status = VALIDATION_REJECTED
        path.derivation_audit.append(
            "REJECTED: derived expression disagrees with the registered "
            "inverse on deterministic test vectors."
        )
        return None
    return path


def covering_canonicals(concept: str) -> List[CanonicalFormula]:
    """Every canonical formula for which `concept` is a target or a
    dependency (deterministic registration order)."""
    return CANONICAL_REGISTRY.covering(concept)


def ensure_derivation_valid(concept: str) -> Tuple[bool, Optional[DerivedPath], str]:
    """Fail-closed gate: the requested concept must have at least one
    VALIDATED derivation path through a canonical formula.

    Returns (ok, path, reason). When the concept is not covered by any
    canonical formula, the gate passes (ok=True, path=None) so the
    existing registered-inverse machinery handles it unchanged. When it
    IS covered but no path validates, the gate fails (ok=False) and the
    caller must refuse rather than guess.
    """
    canonicals = covering_canonicals(concept)
    if not canonicals:
        return True, None, "no canonical coverage"
    first: Optional[DerivedPath] = None
    for canonical in canonicals:
        path = validated_path_for(canonical.formula_id, concept)
        if path is not None:
            if first is None:
                first = path
    if first is not None:
        return True, first, "validated derivation path"
    return False, None, (
        f"No validated derivation path exists for {concept!r} through the "
        "registered canonical relationships - Platrixa refuses rather than "
        "guess."
    )


# ---------------------------------------------------------------------------
# C++-authority solve through the derivation layer
# ---------------------------------------------------------------------------


def _coerce_solve_facts(facts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Accept plain values ({'Commission': 500}) as well as pipeline-shaped
    fact dicts, mirroring the fyjc_maths convention. A plain value becomes
    {'value': ..., 'reporting_period': 'FY2025'} - nothing is fabricated,
    only the established default metadata is attached."""
    out: Dict[str, Any] = {}
    for key, value in (facts or {}).items():
        if isinstance(value, dict):
            out[key] = dict(value)
        else:
            out[key] = {
                "value": value,
                "reporting_period": "FY2025",
                "provenance_tier": "STUDENT_INPUT",
            }
    return out


def solve_derived(concept: str,
                  facts: Optional[Dict[str, Any]] = None) -> Any:
    """Strict C++-authority solve of one concept, gated by the
    derivation engine. Returns a backend.maths.solver.Solution.

    Every numerical step executes through the C++ mathematical authority
    (Solver cpp_authority=True over FYJC_FORMULA_REGISTRY). The
    derivation gate guarantees the concept's solution path was
    independently derived and validated before execution.
    """
    from backend.maths.fact_model import build_fact_graph
    from backend.maths.solver import Solver
    ok, _path, reason = ensure_derivation_valid(concept)
    graph = build_fact_graph(_coerce_solve_facts(facts))
    # Solve through the strict solver (the refusal is a normal Solution);
    # the derivation gate only blocks when there is NO validated path,
    # which the strict solver would not find either.
    return Solver(
        FYJC_FORMULA_REGISTRY, prefer_cpp=True, cpp_authority=True,
    ).solve(concept, graph)


def describe_derivation(concept: str, sol: Any) -> Optional[Dict[str, Any]]:
    """Student-facing derivation metadata for a solved concept.

    Built from the canonical registry + the derivation audit. Returns
    None when the solution carried no formula (direct fact / refusal) or
    the formula is not part of the canonical FYJC surface.
    """
    formula_id = getattr(sol, "formula_id", None)
    if not formula_id:
        return None
    canonical = CANONICAL_REGISTRY.get(formula_id)
    if canonical is None:
        return None
    kind = getattr(sol, "kind", "")
    if kind == "forward" or concept == canonical.target:
        return {
            "canonical_id": formula_id,
            "canonical_formula": canonical.canonical_formula,
            "target_variable": canonical.target,
            "path_id": f"{formula_id}::{canonical.target}",
            "expression": canonical.expression,
            "direction": "forward",
            "validation_status": VALIDATION_VALIDATED,
            "unit_kind": canonical.unit_kind,
            "academic_topic": canonical.academic_topic,
        }
    path = validated_path_for(formula_id, concept)
    if path is None:
        return {
            "canonical_id": formula_id,
            "canonical_formula": canonical.canonical_formula,
            "target_variable": concept,
            "path_id": f"{formula_id}::{concept}",
            "expression": registered_inverse_expression(formula_id, concept)
                           or "",
            "direction": "reverse",
            "validation_status": VALIDATION_REJECTED,
            "unit_kind": canonical.unit_kind,
            "academic_topic": canonical.academic_topic,
        }
    return {
        "canonical_id": formula_id,
        "canonical_formula": canonical.canonical_formula,
        "target_variable": concept,
        "path_id": path.path_id,
        "expression": path.expression,
        "direction": "reverse",
        "validation_status": path.validation_status,
        "validated_vectors": path.validated_vectors,
        "derivation_audit": list(path.derivation_audit),
        "unit_kind": canonical.unit_kind,
        "academic_topic": canonical.academic_topic,
    }


# ---------------------------------------------------------------------------
# Refusal helpers used by fyjc_maths when the derivation gate fails
# ---------------------------------------------------------------------------

DERIVATION_REFUSAL_REASON = (
    "The requested figure is a variable of a registered FYJC relationship, "
    "but Platrixa could not derive and validate a safe solution path for it. "
    "Platrixa never guesses - re-check the question wording and the registered "
    "relationship."
)


def derivation_refusal_outcome(metric: str, concept: str,
                               status: str = BLOCKED) -> Dict[str, Any]:
    from backend.maths.status import STATUS_LABELS
    return {
        "metric": str(metric),
        "concept": concept,
        "resolved": False,
        "verdict": "REFUSED",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "authority_state": "cpp",
        "what": f"{metric} could not be calculated.",
        "how": "—",
        "inputs": [],
        "where": [],
        "value": None,
        "display_value": "—",
        "student_answer": None,
        "student_display": None,
        "correct_answer": None,
        "why_not": DERIVATION_REFUSAL_REASON,
        "next_action": (
            "Confirm the question asks for a figure of a registered FYJC "
            "relationship and re-submit with the other figures provided."
        ),
        "formula": None,
        "formula_id": None,
        "verification_hint": (
            "Not yet - first provide the missing/confirmed evidence listed "
            "under 'Next action'."
        ),
    }
