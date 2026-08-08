"""
Financial Timeline Engine
Sprint 12C - Evidence-Aware Decision Graph & Production Integration
backend/maths/excel_compiler.py

Excel lineage compiler.

Every derived result can produce:

  1. a human-readable lineage (from the 12A LineageRecord), and
  2. an ACTIVE Excel formula that references the Financial Data sheet
     coordinates of its dependencies - e.g.

        ROE  ->  ='Financial Data'!E3/'Financial Data'!E9

Rules
-----
* Derived values are NEVER hardcoded into formula cells when a valid
  dependency graph exists - the cell holds the live algebraic chain.
* For multi-step chains (e.g. DuPont ROE) the full nested algebraic
  formula chain is preserved where possible:
        ROE = (PM cell) * (AT cell) * (EM cell)
  where each component cell is itself a live expression over the
  Financial Data sheet.
* BLOCKED calculations preserve the blocked state - the compiler returns
  no formula (None) with the reason, never a fabricated value.
* REVIEW_REQUIRED / RECONCILED / STUDENT_INPUT results still compile the
  algebraic formula when all coordinates resolve, but the status is
  carried through so the workbook never presents them as verified.
* If a required coordinate cannot be resolved the formula is NOT
  fabricated: it compiles to None with an explicit reason.
* Coordinates are resolved from (in order): the fact's own
  excel_cell_coordinate metadata, then the caller's coordinate map
  ({concept: 'Financial Data'!E5}). The existing deterministic Excel
  serialization behavior of excel_working_model.py is untouched - this
  module is additive.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.maths.fact_model import FactGraph
from backend.maths.formula_registry import (
    FormulaRegistry,
    _identifiers_in,
    parse_expression,
)
from backend.maths.solver import Solution
from backend.maths.status import BLOCKED, REVIEW_REQUIRED

SHEET_REF = "'Financial Data'"

# Excel cell reference pattern (e.g. 'Financial Data'!E5 or Financial_Data!E3)
_CELL_RE = None  # resolved lazily to avoid import cost


@dataclass
class ExcelFormula:
    """One compiled Excel artifact (or an explicit non-state)."""

    formula: Optional[str] = None
    status: str = BLOCKED
    reason: str = ""
    cell_refs: List[str] = field(default_factory=list)
    nested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formula": self.formula,
            "status": self.status,
            "reason": self.reason,
            "cell_refs": list(self.cell_refs),
            "nested": self.nested,
        }


def _looks_like_reference(value: Optional[str]) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    if not s or s == "—":
        return False
    if s.startswith("="):
        s = s[1:]
    return (
        "!" in s
        or (len(s) >= 2 and s[0].isalpha() and s[1:].isdigit())
    )


def resolve_cell_reference(concept: str, facts: FactGraph,
                           coordinate_map: Optional[Dict[str, str]]
                           ) -> Optional[str]:
    """Resolve the Excel coordinate for a concept, in deterministic
    order: fact metadata first, then the caller's coordinate map."""
    fact = facts.get(concept)
    if fact is not None and _looks_like_reference(fact.excel_cell_coordinate):
        return str(fact.excel_cell_coordinate).strip()
    if coordinate_map and concept in coordinate_map:
        return str(coordinate_map[concept]).strip()
    return None


class ExcelLineageCompiler:
    """Deterministic compiler from a Solution + fact graph to Excel."""

    def __init__(self, registry: Optional[FormulaRegistry] = None) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    def compile(self, solution: Solution, facts: FactGraph,
                coordinate_map: Optional[Dict[str, str]] = None,
                ) -> ExcelFormula:
        """Compile the solution's lineage into an active Excel formula.

        Deterministic: lineage steps are processed in traversal order
        (dependencies first); identifiers are replaced by their resolved
        coordinates, and derived intermediates are inlined as nested
        parenthesized expressions.
        """
        if solution.status == BLOCKED:
            return ExcelFormula(
                formula=None,
                status="BLOCKED",
                reason=solution.reason or (
                    f"{solution.target} is blocked - the Excel cell "
                    "preserves the blocked state; no value is fabricated."
                ),
            )
        lineage = solution.lineage
        steps = list(lineage.steps) if lineage is not None else []
        if not steps and solution.value is not None:
            # Direct fact: reference its coordinate directly.
            ref = resolve_cell_reference(
                solution.target, facts, coordinate_map
            )
            if ref is None:
                return ExcelFormula(
                    formula=None,
                    status="NO_COORDINATE",
                    reason=f"{solution.target} is directly known but has no "
                           "Excel coordinate - the formula cannot be "
                           "compiled (never fabricated).",
                )
            return ExcelFormula(
                formula=f"={ref}",
                status=solution.status,
                reason="Direct reference to the Financial Data sheet.",
                cell_refs=[ref],
            )

        # concept -> compiled formula fragment
        compiled: Dict[str, str] = {}
        cell_refs: List[str] = []
        blocked_reason: Optional[str] = None

        for step in steps:
            if step.kind == "direct":
                # Direct-fact steps carry no expression; record their
                # coordinate (if any) and continue.
                ref = resolve_cell_reference(
                    step.concept, facts, coordinate_map
                )
                if ref is not None and ref not in cell_refs:
                    cell_refs.append(ref)
                continue
            expr = self._usable_expression(step)
            identifiers = _identifiers_in(expr)
            missing: List[str] = []
            parts: List[str] = []
            for ident in identifiers:
                if ident in compiled:
                    parts.append(f"({compiled[ident]})")
                    continue
                ref = resolve_cell_reference(
                    ident, facts, coordinate_map
                )
                if ref is None:
                    missing.append(ident)
                    continue
                if ref not in cell_refs:
                    cell_refs.append(ref)
                parts.append(ref)
            if missing:
                # Cannot fabricate: fail closed with the named gaps.
                blocked_reason = (
                    f"{step.concept}: Excel coordinates are unavailable "
                    f"for {', '.join(sorted(missing))} - the formula is "
                    "not fabricated."
                )
                break
            # Substitute each identifier with its resolved fragment.
            compiled[step.concept] = self._substitute(
                expr, identifiers, parts
            )

        if blocked_reason is not None:
            return ExcelFormula(
                formula=None,
                status="NO_COORDINATE",
                reason=blocked_reason,
                cell_refs=cell_refs,
            )
        if not compiled:
            # A pure direct-fact solution (single direct step) still
            # compiles to a plain reference over the data sheet.
            if len(steps) == 1 and steps[0].kind == "direct":
                ref = resolve_cell_reference(
                    steps[0].concept, facts, coordinate_map
                )
                if ref is not None:
                    return ExcelFormula(
                        formula=f"={ref}",
                        status=solution.status,
                        reason="Direct reference to the Financial Data sheet.",
                        cell_refs=[ref],
                    )
            return ExcelFormula(
                formula=None,
                status=solution.status,
                reason="No lineage steps to compile.",
            )
        root_concept = steps[-1].concept
        root_expr = compiled.get(root_concept)
        if not root_expr:
            return ExcelFormula(
                formula=None,
                status="NO_COORDINATE",
                reason=f"Could not compile {solution.target}.",
                cell_refs=cell_refs,
            )
        return ExcelFormula(
            formula=f"={root_expr}",
            status=solution.status,
            reason="Live Excel formula over the Financial Data sheet "
                   "(nested algebraic chain preserved).",
            cell_refs=cell_refs,
            nested=len(steps) > 1,
        )

    # ------------------------------------------------------------------
    def _usable_expression(self, step) -> str:
        """The algebraic expression of a lineage step. Reverse steps are
        compiled from the REGISTERED inverse relationship when available
        (the inverse expression over the step's inputs); otherwise the
        forward expression is used with the 'reverse' marker stripped."""
        expr = str(step.formula or "")
        if step.kind == "reverse" and self.registry is not None \
                and step.formula_id:
            formula = self.registry.get(step.formula_id)
            if formula is not None and step.concept in formula.inverses:
                return formula.inverses[step.concept]
        if step.kind == "reverse":
            if expr.startswith("reverse(") and expr.endswith(")"):
                expr = expr[len("reverse("):-1]
            elif expr.startswith("reverse: "):
                expr = expr[len("reverse: "):]
        return expr

    @staticmethod
    def _substitute(expr: str, identifiers: List[str],
                    fragments: List[str]) -> str:
        """Replace identifier tokens with fragments token-by-token using
        the expression tokenizer (safe; never string-regex on the raw
        text)."""
        from backend.maths.formula_registry import _tokenize
        tokens = _tokenize(expr)
        out: List[str] = []
        fi = 0
        for kind, value in tokens:
            if kind == "ident":
                frag = fragments[fi] if fi < len(fragments) else value
                out.append(frag)
                fi += 1
            elif kind == "num":
                out.append(str(value))
            else:
                out.append(str(value))
        return " ".join(out)


# ---------------------------------------------------------------------------
# Human-readable lineage (Excel Notes column friendly)
# ---------------------------------------------------------------------------


def render_excel_lineage_text(solution: Solution) -> str:
    """Deterministic human-readable lineage for the Excel Notes column."""
    lineage = solution.lineage
    if lineage is None:
        return f"{solution.target}: {solution.status}"
    lines = [f"{lineage.target} = {lineage.display_value} ({lineage.status})"]
    for s in lineage.steps:
        if s.kind == "direct":
            lines.append(f"  ← {s.concept} [direct fact]")
            continue
        inputs = ", ".join(i.concept for i in s.inputs)
        lines.append(
            f"  ← {s.concept} = f({inputs}) via {s.formula_id}"
        )
    if solution.reason:
        lines.append(f"  reason: {solution.reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

DEFAULT_COMPILER = ExcelLineageCompiler()


def compile_excel_formula(solution: Solution, facts: FactGraph,
                          coordinate_map: Optional[Dict[str, str]] = None,
                          ) -> ExcelFormula:
    """Convenience entry point."""
    return DEFAULT_COMPILER.compile(solution, facts, coordinate_map)
