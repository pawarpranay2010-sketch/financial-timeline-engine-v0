"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/formula_registry.py

Versioned, DECLARATIVE Formula Registry.

Separation of concerns (Sprint 12A mandate):
    FORMULA REGISTRY  <>  FORMULA APPLICATION / GRAPH EXECUTION

* A formula is registered as DATA: target concept, named-variable
  equation expression, dependencies, inverse-solving relationships,
  unit/period requirements, denominator constraints, version, and a
  source/definition reference.
* The execution engine (solver.py / accounting_graph.py) is GENERIC: it
  reads registry metadata and never hard-codes a calculation. Adding a
  new formula requires ONLY registration - no engine changes.
* Expressions are evaluated by a SAFE recursive-descent evaluator
  (no eval / exec - stdlib Decimal arithmetic only). Division by zero is
  a structured DomainError, never a crash.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.maths.exceptions import (
    DomainError,
    RegistrationError,
    UnregisteredFormulaError,
)
from backend.maths.status import DERIVED, VERIFIED

# ---------------------------------------------------------------------------
# Safe expression evaluator (recursive descent; no eval/exec)
# ---------------------------------------------------------------------------
# Grammar:
#   expr   := term (('+'|'-') term)*
#   term   := power (('*'|'/') power)*
#   power  := factor ('^' power)?          (right-associative)
#   factor := NUMBER | IDENT | '-' factor | '(' expr ')'
# IDENT is a run of non-operator characters (letters/digits/spaces/
# apostrophes/ampersands), so "Cost of Sales" is a single variable name.
#
# The '^' power operator (Sprint 12C, additive) lets the registry express
# compounding relationships such as CAGR declaratively. It is evaluated
# with Decimal power under the fixed default context - deterministic.
# ---------------------------------------------------------------------------

_OPERATOR_CHARS = set("+-*/^()")
_NUM_RE = re.compile(r"\d+(\.\d*)?|\.\d+")


class _ExprError(DomainError):
    pass


def _tokenize(expr: str) -> List[Tuple[str, Any]]:
    tokens: List[Tuple[str, Any]] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c in _OPERATOR_CHARS:
            tokens.append((c, c))
            i += 1
            continue
        m = _NUM_RE.match(expr, i)
        if m:
            tokens.append(("num", Decimal(m.group(0))))
            i = m.end()
            continue
        # identifier: a run of non-operator characters
        j = i
        while j < n and expr[j] not in _OPERATOR_CHARS:
            j += 1
        ident = expr[i:j].strip()
        if not ident:
            raise RegistrationError(f"Invalid token in expression: {expr!r}")
        tokens.append(("ident", ident))
        i = j
    return tokens


def _identifiers_in(expr: str) -> List[str]:
    """Deterministic, ordered list of variable names used in an expression."""
    return [ident for kind, ident in _tokenize(expr) if kind == "ident"]


def _parse_expr(tokens: List[Tuple[str, Any]], pos: int) -> Tuple[Any, int]:
    """expr := term (('+'|'-') term)* -> ('bin', op, l, r) nodes."""
    node, pos = _parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] in ("+", "-"):
        op = tokens[pos][1]
        right, pos = _parse_term(tokens, pos + 1)
        node = ("bin", op, node, right)
    return node, pos


def _parse_term(tokens: List[Tuple[str, Any]], pos: int) -> Tuple[Any, int]:
    """term := power (('*'|'/') power)*"""
    node, pos = _parse_power(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] in ("*", "/"):
        op = tokens[pos][1]
        right, pos = _parse_power(tokens, pos + 1)
        node = ("bin", op, node, right)
    return node, pos


def _parse_power(tokens: List[Tuple[str, Any]], pos: int) -> Tuple[Any, int]:
    """power := factor ('^' power)?  (right-associative, binds tighter
    than '*' and '/') - e.g. (E/B) ^ (1 / n) - 1 parses as intended."""
    node, pos = _parse_factor(tokens, pos)
    if pos < len(tokens) and tokens[pos][0] == "^":
        op = tokens[pos][1]
        right, pos = _parse_power(tokens, pos + 1)
        node = ("bin", op, node, right)
    return node, pos


def _parse_factor(tokens: List[Tuple[str, Any]], pos: int) -> Tuple[Any, int]:
    """factor := NUMBER | IDENT | '-' factor | '(' expr ')'"""
    if pos >= len(tokens):
        raise RegistrationError("Unexpected end of expression.")
    kind, value = tokens[pos]
    if kind == "num":
        return ("num", value), pos + 1
    if kind == "ident":
        return ("ident", value), pos + 1
    if kind == "-":
        node, pos = _parse_factor(tokens, pos + 1)
        return ("neg", node), pos
    if kind == "(":
        node, pos = _parse_expr(tokens, pos + 1)
        if pos >= len(tokens) or tokens[pos][0] != ")":
            raise RegistrationError("Missing closing parenthesis.")
        return node, pos + 1
    raise RegistrationError(f"Unexpected token: {value!r}")


def parse_expression(expr: str) -> Any:
    """Parse an expression into an AST (or raise RegistrationError)."""
    tokens = _tokenize(expr)
    if not tokens:
        raise RegistrationError("Empty expression.")
    node, pos = _parse_expr(tokens, 0)
    if pos != len(tokens):
        raise RegistrationError(
            f"Unexpected trailing token in expression {expr!r}."
        )
    return node


def _divisor_name(node: Any) -> Optional[str]:
    """Name a divisor when it is a single variable (for zero-denominator
    messages)."""
    if node[0] == "ident":
        return str(node[1])
    if node[0] == "neg" and node[1][0] == "ident":
        return str(node[1][1])
    return None


def eval_expression(
    expr: str,
    values: Dict[str, Decimal],
) -> Tuple[Decimal, List[str]]:
    """Evaluate a registered expression with the given variable values.

    Returns (result, used_variables) in deterministic order. Raises
    DomainError for division by zero, and RegistrationError for unknown
    variables (a programming/registration error - the engine itself never
    invents values).
    """
    node = parse_expression(expr)
    used: List[str] = []

    def walk(n: Any) -> Decimal:
        kind = n[0]
        if kind == "num":
            return n[1]
        if kind == "ident":
            name = str(n[1])
            if name not in values:
                raise RegistrationError(
                    f"Expression {expr!r} references unknown variable "
                    f"{name!r}."
                )
            if name not in used:
                used.append(name)
            return values[name]
        if kind == "neg":
            return -walk(n[1])
        if kind == "bin":
            op, left_n, right_n = n[1], n[2], n[3]
            left = walk(left_n)
            right = walk(right_n)
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                if right == 0:
                    dname = _divisor_name(right_n)
                    if dname:
                        raise DomainError(
                            f"Division by zero: {dname} is zero - "
                            f"the result is mathematically undefined."
                        )
                    raise DomainError(
                        f"Division by zero in expression {expr!r}."
                    )
                return left / right
            if op == "^":
                # Decimal power under the fixed default context
                # (deterministic). Negative bases with fractional
                # exponents and zero bases with non-positive exponents
                # raise InvalidOperation -> structured DomainError.
                try:
                    return left ** right
                except InvalidOperation as exc:
                    raise DomainError(
                        f"Invalid power in expression {expr!r}: "
                        f"{left} ** {right} is mathematically undefined "
                        f"({exc})."
                    ) from exc
            raise RegistrationError(f"Unknown operator {op!r}.")
        raise RegistrationError(f"Unknown node {n!r}.")

    try:
        result = walk(node)
    except InvalidOperation as exc:  # Decimal overflow / NaN etc.
        raise DomainError(
            f"Arithmetic error while evaluating {expr!r}: {exc}"
        ) from exc
    return result, used


# ---------------------------------------------------------------------------
# Formula definition
# ---------------------------------------------------------------------------

FormulaValidator = Callable[[Dict[str, Decimal]], Optional[str]]


@dataclass
class FormulaDefinition:
    """One declarative, versioned formula registration.

    Attributes
    ----------
    formula_id        unique registry key (e.g. "PROFIT")
    target            canonical concept the formula produces
                      (e.g. "Profit")
    description       human-readable definition
    expression        declarative equation over named variables,
                      e.g. "Revenue - Expenses" (forward direction)
    dependencies      ordered list of required variable names
    inverses          {variable: inverse_expression} - reverse solving.
                      Only registered where the relationship yields a
                      mathematically valid, unambiguous solution.
    unit_kind         output kind: "amount" | "ratio" | "percent"
    period_mode       "same" | "different" | "span" | "any"
    denominator_constraints  variables that must not be zero
    domain_rules      optional [(label, callable(values)->reason|None)]
    version           registry version of this definition
    source_ref        accounting definition reference
    supports_forward  bool (True when expression is usable)
    supports_multi_step bool (True when the target may feed chains)
    """

    formula_id: str
    target: str
    expression: str
    dependencies: List[str]
    description: str = ""
    inverses: Dict[str, str] = field(default_factory=dict)
    unit_kind: str = "amount"
    period_mode: str = "same"
    denominator_constraints: List[str] = field(default_factory=list)
    domain_rules: List[Tuple[str, FormulaValidator]] = field(default_factory=list)
    version: str = "1.0"
    source_ref: str = ""
    supports_forward: bool = True
    supports_multi_step: bool = True

    def __post_init__(self) -> None:
        # Registration-time validation (fail fast, never at execution).
        if self.unit_kind not in ("amount", "ratio", "percent"):
            raise RegistrationError(
                f"Formula {self.formula_id}: invalid unit_kind "
                f"{self.unit_kind!r}."
            )
        if self.period_mode not in ("same", "different", "span", "any"):
            raise RegistrationError(
                f"Formula {self.formula_id}: invalid period_mode "
                f"{self.period_mode!r}."
            )
        expr_idents = _identifiers_in(self.expression)
        unknown = [v for v in expr_idents if v not in self.dependencies]
        if unknown:
            raise RegistrationError(
                f"Formula {self.formula_id}: expression references "
                f"non-dependency variables {unknown}."
            )
        for var, inv in self.inverses.items():
            if var not in self.dependencies:
                raise RegistrationError(
                    f"Formula {self.formula_id}: inverse target {var!r} "
                    "is not one of the dependencies."
                )
            allowed = set(self.dependencies) | {self.target} - {var}
            bad = [v for v in _identifiers_in(inv) if v not in allowed]
            if bad:
                raise RegistrationError(
                    f"Formula {self.formula_id}: inverse expression for "
                    f"{var!r} references {bad} which are not available "
                    "in that direction."
                )

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "target": self.target,
            "description": self.description,
            "expression": self.expression,
            "dependencies": list(self.dependencies),
            "inverses": dict(self.inverses),
            "unit_kind": self.unit_kind,
            "period_mode": self.period_mode,
            "denominator_constraints": list(self.denominator_constraints),
            "version": self.version,
            "source_ref": self.source_ref,
            "supports_forward": self.supports_forward,
            "supports_multi_step": self.supports_multi_step,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class FormulaRegistry:
    """Versioned formula registry. Independently testable and extensible:
    registering a new formula never requires touching the execution
    engine."""

    def __init__(self) -> None:
        self._by_id: Dict[str, FormulaDefinition] = {}
        self._by_target: Dict[str, List[str]] = {}  # target -> [formula_ids]

    # ------------------------------------------------------------------
    def register(self, definition: FormulaDefinition) -> FormulaDefinition:
        if definition.formula_id in self._by_id:
            raise RegistrationError(
                f"Formula {definition.formula_id!r} is already registered."
            )
        self._by_id[definition.formula_id] = definition
        self._by_target.setdefault(definition.target, []).append(
            definition.formula_id
        )
        return definition

    def get(self, formula_id: str) -> Optional[FormulaDefinition]:
        return self._by_id.get(formula_id)

    def require(self, formula_id: str) -> FormulaDefinition:
        d = self._by_id.get(formula_id)
        if d is None:
            raise UnregisteredFormulaError(
                f"Formula {formula_id!r} is not registered."
            )
        return d

    def formulas_for_target(self, target: str) -> List[FormulaDefinition]:
        """All formulas that can PRODUCE this target (deterministic order:
        registration order)."""
        return [
            self._by_id[fid]
            for fid in self._by_target.get(target, [])
            if fid in self._by_id
        ]

    def formulas_consuming(self, variable: str) -> List[FormulaDefinition]:
        """All formulas that USE this variable - either as a dependency
        (reverse-solvable) or as their target. Deterministic order."""
        out: List[FormulaDefinition] = []
        for d in self._by_id.values():
            if variable == d.target or variable in d.dependencies:
                out.append(d)
        return out

    def is_registered_target(self, concept: str) -> bool:
        return concept in self._by_target

    def can_reverse_solve(self, variable: str) -> bool:
        """True when some registered formula can solve for this variable
        via an inverse relationship."""
        for d in self._by_id.values():
            if variable in d.inverses:
                return True
        return False

    def all_ids(self) -> List[str]:
        return list(self._by_id.keys())

    def targets(self) -> List[str]:
        return sorted(set(self._by_target.keys()))

    def __len__(self) -> int:
        return len(self._by_id)


# ---------------------------------------------------------------------------
# Default Phase-1 formulas (Sprint 12A)
# ---------------------------------------------------------------------------
# Only standard accounting relationships are registered - nothing is
# invented. Each formula declares its forward equation AND the inverse
# relationships that are mathematically valid and unambiguous.
# ---------------------------------------------------------------------------


def build_default_registry() -> FormulaRegistry:
    reg = FormulaRegistry()

    reg.register(FormulaDefinition(
        formula_id="PROFIT",
        target="Profit",
        description="P&L identity: Profit = Revenue - Expenses",
        expression="Revenue - Expenses",
        dependencies=["Revenue", "Expenses"],
        inverses={
            "Revenue": "Profit + Expenses",
            "Expenses": "Revenue - Profit",
        },
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="Accounting identity: Income = Revenue - Expenses",
    ))

    reg.register(FormulaDefinition(
        formula_id="LOSS",
        target="Loss",
        description="Loss magnitude = Expenses - Revenue (positive loss)",
        expression="Expenses - Revenue",
        dependencies=["Revenue", "Expenses"],
        inverses={
            "Expenses": "Revenue + Loss",
            "Revenue": "Expenses - Loss",
        },
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="Loss = Expenses - Revenue (positive magnitude)",
    ))

    reg.register(FormulaDefinition(
        formula_id="GROSS_PROFIT",
        target="Gross Profit",
        description="Gross Profit = Revenue - Cost of Sales",
        expression="Revenue - Cost of Sales",
        dependencies=["Revenue", "Cost of Sales"],
        inverses={
            "Revenue": "Gross Profit + Cost of Sales",
            "Cost of Sales": "Revenue - Gross Profit",
        },
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="P&L: Gross Profit = Revenue - Cost of Sales",
    ))

    reg.register(FormulaDefinition(
        formula_id="WORKING_CAPITAL",
        target="Working Capital",
        description="Working Capital = Current Assets - Current Liabilities",
        expression="Current Assets - Current Liabilities",
        dependencies=["Current Assets", "Current Liabilities"],
        inverses={
            "Current Assets": "Working Capital + Current Liabilities",
            "Current Liabilities": "Current Assets - Working Capital",
        },
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="Working Capital = Current Assets - Current Liabilities",
    ))

    reg.register(FormulaDefinition(
        formula_id="ASSET_TURNOVER",
        target="Asset Turnover",
        description="Asset Turnover = Revenue / Assets",
        expression="Revenue / Assets",
        dependencies=["Revenue", "Assets"],
        inverses={
            "Revenue": "Asset Turnover * Assets",
            "Assets": "Revenue / Asset Turnover",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=["Assets"],
        version="1.0",
        source_ref="Asset Turnover = Revenue / Average Total Assets",
    ))

    reg.register(FormulaDefinition(
        formula_id="EQUITY_MULTIPLIER",
        target="Equity Multiplier",
        description="Equity Multiplier = Assets / Equity",
        expression="Assets / Equity",
        dependencies=["Assets", "Equity"],
        inverses={
            "Assets": "Equity Multiplier * Equity",
            "Equity": "Assets / Equity Multiplier",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=["Equity"],
        version="1.0",
        source_ref="Equity Multiplier = Total Assets / Total Equity",
    ))

    reg.register(FormulaDefinition(
        formula_id="PROFIT_MARGIN",
        target="Profit Margin",
        description="Profit Margin (P&L) = Profit / Revenue (percentage)",
        expression="Profit / Revenue",
        dependencies=["Profit", "Revenue"],
        inverses={
            "Profit": "(Profit Margin / 100) * Revenue",
            "Revenue": "Profit / (Profit Margin / 100)",
        },
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=["Revenue"],
        version="1.0",
        source_ref="Profit Margin = Profit / Revenue (shown as a percentage)",
    ))

    return reg


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY = build_default_registry()


def default_registry() -> FormulaRegistry:
    """Return the shared default registry (deterministic; extensions should
    use their own FormulaRegistry instance)."""
    return DEFAULT_REGISTRY
