#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 12A - Core Deterministic Mathematics Engine (Phase 1)

Comprehensive deterministic test suite for backend/maths:

  PART A - FORMULA REGISTRY
      declarative registration, separation from execution, extension
      without engine changes, versioning, registration-time validation
  PART B - CANONICAL FACT MODEL
      original representation preserved, normalized working value,
      strict numeric coercion, fail-closed statuses
  PART C - DIRECTED ACCOUNTING GRAPH
      multi-step chains, topological order, cycle detection,
      deterministic traversal
  PART D - SUFFICIENCY ENGINE
      DIRECT_KNOWN / FORWARD_SOLVABLE / REVERSE_SOLVABLE /
      CHAINED_SOLVABLE / INSUFFICIENT / BLOCKED / AMBIGUOUS
  PART E - FORWARD SOLVER
  PART F - REVERSE SOLVER  (only where a registered inverse is valid)
  PART G - SIX-TIER STATUS PROPAGATION  (weakest-link, never upgrades)
  PART H - MATHEMATICAL SAFETY
      division by zero, missing deps, nulls, unit/currency/period
      mismatch, cycles, underdetermined equations, ambiguity,
      precision/overflow
  PART I - UNIT / SCALE NORMALIZATION
  PART J - DETERMINISTIC LINEAGE
  PART K - ADAPTER + EXISTING ENGINE INTEGRATION
      formula_engine.py delegation routing (legacy 9 untouched,
      new concepts routed to the maths engine)
  PART L - C++ ENGINE BRIDGE
      registry contracts (--registry == 9, --registry-ext == 7),
      forward + reverse solving through the binary

No LLM. No AI. No network. Deterministic.
"""

import importlib.util
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal

from backend.maths import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    AccountingGraph,  # noqa: F401  (re-export check)
    CycleDetectedError,
    FormulaDefinition,
    FormulaRegistry,
    RegistrationError,
    Solver,
    SufficiencyEngine,
    UnregisteredFormulaError,
    build_fact_graph,
    default_registry,
)
from backend.maths.accounting_graph import AccountingGraph as AG
from backend.maths.exceptions import (
    AmbiguousEquationError,
    DomainError,
    InsufficientDataError,
    MathsEngineError,
    PeriodMismatchError,
    RegistrationError as RegErr,
    ScaleMismatchError,
    UnregisteredConceptError,
    UnregisteredFormulaError as UnregF,
    UnitMismatchError,
)
from backend.maths.fact_model import FactNode, from_pipeline_fact, to_decimal
from backend.maths.formula_registry import eval_expression
from backend.maths.solver import Solution
from backend.maths.status import (
    ALL_STATUSES,
    STATUS_LABELS,
    propagate_statuses,
    weaker,
)
from backend.maths.sufficiency import (
    AMBIGUOUS,
    BLOCKED as SUFF_BLOCKED,
    CHAINED_SOLVABLE,
    CYCLE,
    DIRECT_KNOWN,
    FORWARD_SOLVABLE,
    INSUFFICIENT,
    REVERSE_SOLVABLE,
    Sufficiency,
)
from backend.maths.units import (
    classify_quantity,
    normalize_value,
    scale_multiplier,
)

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------------------
# Fact helpers (pipeline-shaped; DOCUMENT provenance = VERIFIED)
# ---------------------------------------------------------------------------

def F(value, tier="DOCUMENT", unit="USD", period="FY2025", page="12",
      source="Doc.pdf", **kw):
    """Pipeline-shaped fact dict (mirrors the extractor / evidence layer)."""
    return {
        "value": value,
        "source": source,
        "reporting_period": period,
        "unit": unit,
        "provenance_tier": tier,
        "page": page,
        **kw,
    }


def graph(*facts):
    return build_fact_graph(dict(facts))


# ---------------------------------------------------------------------------
# PART A - FORMULA REGISTRY
# ---------------------------------------------------------------------------

def test_a_registry():
    print("PART A - FORMULA REGISTRY")

    # A1: declarative metadata is complete and versioned
    reg = default_registry()
    profit = reg.require("PROFIT")
    check("A1a. formula_id present", profit.formula_id == "PROFIT")
    check("A1b. canonical target present",
          profit.target == "Profit")
    check("A1c. declarative expression present",
          profit.expression == "Revenue - Expenses")
    check("A1d. dependencies declared",
          profit.dependencies == ["Revenue", "Expenses"])
    check("A1e. inverse relationships registered",
          set(profit.inverses) == {"Revenue", "Expenses"})
    check("A1f. unit kind valid", profit.unit_kind in ("amount", "ratio", "percent"))
    check("A1g. period mode valid",
          profit.period_mode in ("same", "different", "span", "any"))
    check("A1h. versioned", profit.version == "1.0")
    check("A1i. source/definition reference present", bool(profit.source_ref))
    check("A1j. metadata dict round-trips",
          profit.to_metadata()["formula_id"] == "PROFIT")
    check("A1k. default registry has 7 formulas", len(reg) == 7,
          str(len(reg)))
    check("A1l. all 7 targets registered",
          reg.is_registered_target("Profit")
          and reg.is_registered_target("Loss")
          and reg.is_registered_target("Gross Profit")
          and reg.is_registered_target("Working Capital")
          and reg.is_registered_target("Asset Turnover")
          and reg.is_registered_target("Equity Multiplier")
          and reg.is_registered_target("Profit Margin"))
    check("A1m. reverse-solvable variables recognized",
          reg.can_reverse_solve("Expenses") and reg.can_reverse_solve("Revenue")
          and reg.can_reverse_solve("Assets") and reg.can_reverse_solve("Equity"))

    # A2: formula registration is separated from execution - registering a
    # NEW formula requires no engine change (the generic solver runs it).
    custom = FormulaRegistry()
    custom.register(FormulaDefinition(
        formula_id="NET_INCOME_RATIO",
        target="Net Income Ratio",
        description="Net Income / Revenue",
        expression="Net Income / Revenue",
        dependencies=["Net Income", "Revenue"],
        inverses={
            "Net Income": "Net Income Ratio * Revenue",
            "Revenue": "Net Income / Net Income Ratio",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=["Revenue"],
        version="2.0",
        source_ref="test definition",
    ))
    sol = Solver(custom, prefer_cpp=False).solve(
        "Net Income Ratio",
        graph(("Net Income", F(50)), ("Revenue", F(1000))),
    )
    check("A2a. new formula executable with no engine change",
          sol.status == DERIVED and sol.display_value == "0.05",
          sol.display_value)
    sol2 = Solver(custom, prefer_cpp=False).solve(
        "Net Income",
        graph(("Net Income Ratio", F(0.05)), ("Revenue", F(1000))),
    )
    check("A2b. new formula reverse-solvable with no engine change",
          sol2.status == DERIVED and sol2.display_value == "50.00",
          sol2.display_value)

    # A3: registration-time validation (fail fast, never at execution)
    try:
        custom.register(FormulaDefinition(
            formula_id="BAD_EXPR", target="Bad",
            expression="Revenue - Expenses", dependencies=["Revenue"],
        ))
        check("A3a. expression with non-dependency rejected", False)
    except RegistrationError:
        check("A3a. expression with non-dependency rejected", True)
    try:
        custom.register(FormulaDefinition(
            formula_id="BAD_INV", target="Bad Inv",
            expression="Revenue - Expenses",
            dependencies=["Revenue", "Expenses"],
            inverses={"Revenue": "Expenses + NotAVariable"},
        ))
        check("A3b. inverse referencing unavailable variable rejected", False)
    except RegistrationError:
        check("A3b. inverse referencing unavailable variable rejected", True)
    try:
        custom.register(FormulaDefinition(
            formula_id="BAD_KIND", target="Bad Kind",
            expression="Revenue - Expenses",
            dependencies=["Revenue", "Expenses"],
            unit_kind="banana",
        ))
        check("A3c. invalid unit_kind rejected", False)
    except RegistrationError:
        check("A3c. invalid unit_kind rejected", True)
    try:
        custom.register(FormulaDefinition(
            formula_id="BAD_PERIOD", target="Bad Period",
            expression="Revenue - Expenses",
            dependencies=["Revenue", "Expenses"],
            period_mode="yesterday",
        ))
        check("A3d. invalid period_mode rejected", False)
    except RegistrationError:
        check("A3d. invalid period_mode rejected", True)
    try:
        custom.register(FormulaDefinition(
            formula_id="NET_INCOME_RATIO", target="Duplicate",
            expression="Revenue / Revenue", dependencies=["Revenue"],
        ))
        check("A3e. duplicate registration rejected", False)
    except RegistrationError:
        check("A3e. duplicate registration rejected", True)
    try:
        custom.require("NOT_REGISTERED")
        check("A3f. unknown formula raises UnregisteredFormulaError", False)
    except UnregisteredFormulaError:
        check("A3f. unknown formula raises UnregisteredFormulaError", True)

    # A4: safe expression evaluator (no eval/exec; Decimal arithmetic)
    val, used = eval_expression("Revenue - Expenses",
                                {"Revenue": Decimal("1000"),
                                 "Expenses": Decimal("800")})
    check("A4a. expression evaluator correct",
          val == Decimal("200") and used == ["Revenue", "Expenses"])
    try:
        eval_expression("Revenue / Zero", {"Revenue": Decimal("1"),
                                           "Zero": Decimal("0")})
        check("A4b. division by zero raises DomainError", False)
    except DomainError:
        check("A4b. division by zero raises DomainError", True)
    try:
        eval_expression("Revenue - Expenses", {"Revenue": Decimal("1")})
        check("A4c. unknown variable raises", False)
    except RegistrationError:
        check("A4c. unknown variable raises", True)

    # A5: denominator + domain constraints are metadata
    at = reg.require("ASSET_TURNOVER")
    check("A5a. denominator constraint declared", at.denominator_constraints == ["Assets"])
    pm = reg.require("PROFIT_MARGIN")
    check("A5b. percent kind declared", pm.unit_kind == "percent")


# ---------------------------------------------------------------------------
# PART B - CANONICAL FACT MODEL
# ---------------------------------------------------------------------------

def test_b_fact_model():
    print("PART B - CANONICAL FACT MODEL")

    # B1: original representation is preserved; normalized working value set
    node = FactNode(
        node_id="Revenue", canonical_concept="Revenue",
        value=Decimal("125400000"), original_value=125.4,
        original_unit="USD millions", original_scale="millions",
        normalized_value=Decimal("125400000"), normalized_unit="USD",
        currency="USD", period="FY2025", source_tier="DOCUMENT",
        status=VERIFIED, apply_scale=True,
    )
    check("B1a. original_value preserved", node.original_value == 125.4)
    check("B1b. original_unit preserved", node.original_unit == "USD millions")
    check("B1c. original_scale preserved", node.original_scale == "millions")
    check("B1d. normalized value present",
          node.normalized_value == Decimal("125400000"))
    check("B1e. canonical concept retained", node.canonical_concept == "Revenue")
    check("B1f. to_dict round-trip",
          node.to_dict()["original_value"] == 125.4)

    # B2: pipeline fact translation
    n = from_pipeline_fact("Profit", F(200))
    check("B2a. pipeline value converted", n.value == Decimal("200"))
    check("B2b. DOCUMENT tier -> VERIFIED", n.status == VERIFIED)
    n2 = from_pipeline_fact("Profit",
                            {"value": "200", "unit": "USD",
                             "provenance_tier": "APPENDIX"})
    check("B2c. APPENDIX tier -> VERIFIED", n2.status == VERIFIED)
    n3 = from_pipeline_fact("Profit",
                            {"value": "200", "unit": "USD",
                             "provenance_tier": "REGULATORY_API"})
    check("B2d. REGULATORY_API tier -> VERIFIED", n3.status == VERIFIED)
    n4 = from_pipeline_fact("Profit",
                            {"value": "200", "unit": "USD",
                             "provenance_tier": "STUDENT_INPUT"})
    check("B2e. STUDENT_INPUT tier -> STUDENT_INPUT", n4.status == STUDENT_INPUT)
    n5 = from_pipeline_fact("Profit",
                            {"value": "200", "unit": "USD",
                             "provenance_tier": "DERIVED"})
    check("B2f. DERIVED tier -> DERIVED", n5.status == DERIVED)

    # B3: strict numeric coercion (never coerces labels/ranges/bool/None)
    check("B3a. int coerces", to_decimal(200) == Decimal("200"))
    check("B3b. float coerces", to_decimal(200.5) == Decimal("200.5"))
    check("B3c. comma thousands coerces",
          to_decimal("1,234.56") == Decimal("1234.56"))
    check("B3d. label rejected", to_decimal("not a number") is None)
    check("B3e. range rejected", to_decimal("100-200") is None)
    check("B3f. boolean rejected", to_decimal(True) is None)
    check("B3g. None rejected", to_decimal(None) is None)
    check("B3h. empty string rejected", to_decimal("") is None)

    # B4: fail-closed statuses in the graph store
    g = build_fact_graph({
        "Revenue": F(1000),
        "Expenses": {"value": "not-a-number", "source": "Doc",
                     "provenance_tier": "DOCUMENT"},
        "Mystery": {"source": "Doc", "provenance_tier": "DOCUMENT"},
    })
    check("B4a. valid fact becomes node",
          g.get("Revenue") is not None and g.get("Revenue").has_value())
    check("B4b. non-numeric fact carried as BLOCKED",
          g.get("Expenses") is not None
          and g.get("Expenses").status == BLOCKED)
    check("B4c. valueless fact carried as BLOCKED",
          g.get("Mystery") is not None
          and g.get("Mystery").status == BLOCKED)


# ---------------------------------------------------------------------------
# PART C - DIRECTED ACCOUNTING GRAPH
# ---------------------------------------------------------------------------

def test_c_graph():
    print("PART C - DIRECTED ACCOUNTING GRAPH")

    # C1: multi-step chain builds and traverses deterministically
    g = AG(default_registry())
    g.add_fact("Revenue", Decimal("1000"), status=VERIFIED)
    g.add_fact("Expenses", Decimal("800"), status=VERIFIED)
    g.add_formula_application("Profit", "PROFIT", ["Revenue", "Expenses"])
    g.add_formula_application("Profit Margin", "PROFIT_MARGIN",
                              ["Profit", "Revenue"])
    order = g.topological_order("Profit Margin")
    # Dependencies before dependents; sibling tie-break is alphabetical
    # (deterministic): Expenses sorts before Revenue.
    check("C1a. multi-step topo order resolves dependencies first",
          order == ["Expenses", "Revenue", "Profit", "Profit Margin"],
          str(order))
    check("C1b. traversal path deterministic",
          g.traversal_path("Profit Margin") == order)
    check("C1c. traversal memoized/deduped", len(order) == len(set(order)))
    check("C1d. node ids sorted deterministically",
          g.node_ids() == sorted(g.node_ids()))
    check("C1e. cycle-free graph has no cycles", g.detect_cycles() == [])

    # C2: cycle detection
    cyc = AG(default_registry())
    cyc.add_fact("A", Decimal("1"), status=VERIFIED)
    cyc.add_formula_application("B", "F1", ["A"])
    cyc.add_formula_application("A", "F2", ["B"])  # A -> B -> A
    cycles = cyc.detect_cycles()
    check("C2a. cycle detected", len(cycles) >= 1, str(cycles))
    try:
        cyc.assert_acyclic()
        check("C2b. assert_acyclic raises CycleDetectedError", False)
    except CycleDetectedError:
        check("C2b. assert_acyclic raises CycleDetectedError", True)
    try:
        cyc.topological_order()
        check("C2c. topological_order refuses cyclic graphs", False)
    except CycleDetectedError:
        check("C2c. topological_order refuses cyclic graphs", True)

    # C3: a cyclic custom registry must never hang the solver (deterministic
    # BLOCKED via the sufficiency cycle guard)
    cyclic_reg = FormulaRegistry()
    cyclic_reg.register(FormulaDefinition(
        formula_id="CYCA", target="X",
        expression="X + Y", dependencies=["X", "Y"],
    ))
    sol = Solver(cyclic_reg, prefer_cpp=False).solve("X", graph())
    check("C3a. cyclic registry solve terminates (BLOCKED)",
          sol.status == BLOCKED)


# ---------------------------------------------------------------------------
# PART D - SUFFICIENCY ENGINE
# ---------------------------------------------------------------------------

def test_d_sufficiency():
    print("PART D - SUFFICIENCY ENGINE")

    suff = SufficiencyEngine(default_registry())

    # D1: DIRECT_KNOWN
    a = suff.analyze("Profit", graph(("Profit", F(200))))
    check("D1a. direct fact -> DIRECT_KNOWN", a.state == DIRECT_KNOWN, a.state)
    check("D1b. direct fact reason present", bool(a.reason))

    # D2: FORWARD_SOLVABLE
    a = suff.analyze("Profit", graph(("Revenue", F(1000)), ("Expenses", F(800))))
    check("D2a. Profit -> FORWARD_SOLVABLE", a.state == FORWARD_SOLVABLE, a.state)

    # D3: REVERSE_SOLVABLE
    a = suff.analyze("Expenses",
                     graph(("Revenue", F(1000)), ("Profit", F(200))))
    check("D3a. Expenses -> REVERSE_SOLVABLE", a.state == REVERSE_SOLVABLE, a.state)

    # D4: CHAINED_SOLVABLE (Profit derived from Revenue - Expenses first)
    a = suff.analyze("Profit Margin",
                     graph(("Revenue", F(1000)), ("Expenses", F(800))))
    check("D4a. Profit Margin -> CHAINED_SOLVABLE",
          a.state == CHAINED_SOLVABLE, a.state)
    a = suff.analyze("Profit", graph(("Revenue", F(1000)), ("Loss", F(200))))
    check("D4b. Profit via Loss chain -> CHAINED_SOLVABLE",
          a.state == CHAINED_SOLVABLE, a.state)

    # D5: INSUFFICIENT with named missing dependencies (spec example)
    a = suff.analyze("Profit", graph(("Revenue", F(1000))))
    check("D5a. Profit w/ Revenue only -> INSUFFICIENT",
          a.state == INSUFFICIENT, a.state)
    check("D5b. missing dependencies named",
          "Expenses" in a.missing, str(a.missing))
    a = suff.analyze("ROE", graph(("Revenue", F(1000))))
    check("D5c. ROE (no relation) -> INSUFFICIENT",
          a.state == INSUFFICIENT, a.state)

    # D6: BLOCKED state from blocked-status facts (fail closed)
    a = suff.analyze("Expenses", graph(("Expenses", F(800, tier="BLOCKED"))))
    check("D6a. blocked-status fact -> BLOCKED state",
          a.state == SUFF_BLOCKED, a.state)
    # A blocked dependency makes the target underivable -> INSUFFICIENT
    # naming the blocker; the SOLVER still returns BLOCKED (G6a).
    a = suff.analyze("Profit",
                     graph(("Revenue", F(1000)),
                           ("Expenses", F(800, tier="BLOCKED"))))
    check("D6b. blocked dependency -> INSUFFICIENT naming blocker",
          a.state == INSUFFICIENT and "Expenses" in a.missing,
          f"{a.state} missing={a.missing}")

    # D7: AMBIGUOUS when multiple derivations exist (target not directly
    # known - Expenses is reverse-solvable via both PROFIT and LOSS)
    a = suff.analyze("Expenses",
                     graph(("Revenue", F(1000)), ("Profit", F(200)),
                           ("Loss", F(200))))
    check("D7a. multiple derivations -> AMBIGUOUS",
          a.state == AMBIGUOUS, a.state)

    # D8: spec example - sufficiency verdicts are structured
    v = suff.analyze("Profit", graph(("Revenue", F(1000)), ("Expenses", F(800))))
    check("D8a. verdict has derivations", len(v.derivations) >= 1)
    check("D8b. derivation describes the path",
          "PROFIT" in v.derivations[0].describe())


# ---------------------------------------------------------------------------
# PART E - FORWARD SOLVER
# ---------------------------------------------------------------------------

def test_e_forward():
    print("PART E - FORWARD SOLVER")

    s = Solver(prefer_cpp=True)

    def fwd(target, facts):
        return s.solve(target, build_fact_graph(dict(facts)))

    # E1: P&L identity
    r = fwd("Profit", {"Revenue": F(1000), "Expenses": F(800)})
    check("E1a. Profit = Revenue - Expenses = 200",
          r.status == DERIVED and r.value == Decimal("200"),
          r.display_value)
    check("E1b. Profit kind = forward", r.kind == "forward", r.kind)
    check("E1c. formula recorded", r.formula_id == "PROFIT", str(r.formula_id))

    # E2: Loss magnitude (positive when expenses exceed revenue)
    r = fwd("Loss", {"Revenue": F(1000), "Expenses": F(1200)})
    check("E2a. Loss = Expenses - Revenue = 200",
          r.status == DERIVED and r.value == Decimal("200"),
          r.display_value)

    # E3: Gross Profit
    r = fwd("Gross Profit", {"Revenue": F(1000), "Cost of Sales": F(600)})
    check("E3a. Gross Profit = Revenue - Cost of Sales = 400",
          r.status == DERIVED and r.value == Decimal("400"),
          r.display_value)

    # E4: Working Capital
    r = fwd("Working Capital", {"Current Assets": F(500),
                                "Current Liabilities": F(300)})
    check("E4a. Working Capital = 200",
          r.status == DERIVED and r.value == Decimal("200"),
          r.display_value)

    # E5: Asset Turnover
    r = fwd("Asset Turnover", {"Revenue": F(1000), "Assets": F(2000)})
    check("E5a. Asset Turnover = 0.50",
          r.status == DERIVED and r.display_value == "0.50", r.display_value)

    # E6: Equity Multiplier
    r = fwd("Equity Multiplier", {"Assets": F(2000), "Equity": F(500)})
    check("E6a. Equity Multiplier = 4.00",
          r.status == DERIVED and r.display_value == "4.00", r.display_value)

    # E7: Profit Margin (percent)
    r = fwd("Profit Margin", {"Profit": F(200), "Revenue": F(1000)})
    check("E7a. Profit Margin = 20.00%",
          r.status == DERIVED and r.display_value == "20.00%",
          r.display_value)
    check("E7b. percent kind", r.unit_kind == "percent", r.unit_kind)

    # E8: chained - Profit from Revenue + Loss (Expenses derived first)
    r = fwd("Profit", {"Revenue": F(1000), "Loss": F(200)})
    check("E8a. chained Profit = -200 (Revenue - (Revenue + Loss))",
          r.status == DERIVED and r.value == Decimal("-200"),
          r.display_value)
    check("E8b. traversal path includes Expenses intermediate",
          "Expenses" in r.traversal_path, str(r.traversal_path))

    # E9: spec example - Revenue=1000, Profit Margin=20% -> Profit=200
    r = fwd("Profit", {"Profit Margin": F(20, unit="percent"),
                       "Revenue": F(1000)})
    check("E9a. Profit from Profit Margin x Revenue = 200",
          r.status == DERIVED and r.value == Decimal("200"),
          r.display_value)


# ---------------------------------------------------------------------------
# PART F - REVERSE SOLVER
# ---------------------------------------------------------------------------

def test_f_reverse():
    print("PART F - REVERSE SOLVER")

    s = Solver(prefer_cpp=True)

    def rev(target, facts):
        return s.solve(target, build_fact_graph(dict(facts)))

    # F1: Revenue + Profit -> Expenses
    r = rev("Expenses", {"Revenue": F(1000), "Profit": F(200)})
    check("F1a. Expenses = Revenue - Profit = 800",
          r.status == DERIVED and r.value == Decimal("800"),
          r.display_value)
    check("F1b. kind = reverse", r.kind == "reverse", r.kind)

    # F2: Profit + Expenses -> Revenue
    r = rev("Revenue", {"Profit": F(200), "Expenses": F(800)})
    check("F2a. Revenue = Profit + Expenses = 1000",
          r.status == DERIVED and r.value == Decimal("1000"),
          r.display_value)

    # F3: Revenue + Loss -> Expenses (loss semantics)
    r = rev("Expenses", {"Revenue": F(1000), "Loss": F(200)})
    check("F3a. Expenses = Revenue + Loss = 1200",
          r.status == DERIVED and r.value == Decimal("1200"),
          r.display_value)

    # F4: Expenses + Loss -> Revenue
    r = rev("Revenue", {"Expenses": F(1200), "Loss": F(200)})
    check("F4a. Revenue = Expenses - Loss = 1000",
          r.status == DERIVED and r.value == Decimal("1000"),
          r.display_value)

    # F5: percent inverse - Profit Margin + Revenue -> Profit
    r = rev("Profit", {"Profit Margin": F(20, unit="percent"),
                       "Revenue": F(1000)})
    check("F5a. Profit = PM x Revenue = 200",
          r.status == DERIVED and r.value == Decimal("200"),
          r.display_value)

    # F6: Profit + Profit Margin -> Revenue
    r = rev("Revenue", {"Profit": F(200), "Profit Margin": F(20, unit="percent")})
    check("F6a. Revenue = Profit / PM = 1000",
          r.status == DERIVED and r.value == Decimal("1000"),
          r.display_value)

    # F7: Asset Turnover inverse solving
    r = rev("Revenue", {"Asset Turnover": F(0.5), "Assets": F(2000)})
    check("F7a. Revenue = AT x Assets = 1000",
          r.status == DERIVED and r.value == Decimal("1000"),
          r.display_value)
    r = rev("Assets", {"Revenue": F(1000), "Asset Turnover": F(0.5)})
    check("F7b. Assets = Revenue / AT = 2000",
          r.status == DERIVED and r.value == Decimal("2000"),
          r.display_value)

    # F8: Equity Multiplier inverse solving
    r = rev("Equity", {"Assets": F(2000), "Equity Multiplier": F(4)})
    check("F8a. Equity = Assets / EM = 500",
          r.status == DERIVED and r.value == Decimal("500"),
          r.display_value)

    # F9: DO NOT GUESS - insufficient info must be BLOCKED (spec example:
    # Revenue=1000, Employees=500 -> ROE = BLOCKED)
    r = rev("ROE", {"Revenue": F(1000), "Employees": F(500)})
    check("F9a. ROE with unrelated facts -> BLOCKED",
          r.status == BLOCKED, r.status)
    r = rev("Profit", {"Revenue": F(1000)})
    check("F9b. Profit with only Revenue -> BLOCKED",
          r.status == BLOCKED and "Revenue" not in r.missing
          and "Expenses" in r.missing,
          f"{r.status} missing={r.missing}")

    # F10: solving for a non-variable is BLOCKED
    r = rev("Employees", {"Revenue": F(1000), "Profit": F(200)})
    check("F10a. solve for non-variable -> BLOCKED",
          r.status == BLOCKED, r.status)

    # F11: underdetermined ambiguity -> REVIEW_REQUIRED when two registered
    # inverses disagree (Expenses from Profit path vs Loss path)
    r = rev("Expenses", {"Revenue": F(1000), "Profit": F(200),
                         "Loss": F(200)})
    check("F11a. competing derivations -> REVIEW_REQUIRED",
          r.status == REVIEW_REQUIRED,
          f"{r.status} {r.reason or ''}")


# ---------------------------------------------------------------------------
# PART G - SIX-TIER STATUS PROPAGATION
# ---------------------------------------------------------------------------

def test_g_status():
    print("PART G - SIX-TIER STATUS PROPAGATION")

    s = Solver(prefer_cpp=True)

    # G1: direct VERIFIED fact stays VERIFIED
    r = s.solve("Revenue", graph(("Revenue", F(1000))))
    check("G1a. direct fact VERIFIED", r.status == VERIFIED, r.status)

    # G2: all-VERIFIED inputs -> DERIVED (never VERIFIED)
    r = s.solve("Profit", graph(("Revenue", F(1000)), ("Expenses", F(800))))
    check("G2a. computed from VERIFIED -> DERIVED", r.status == DERIVED, r.status)
    check("G2b. computed result is never VERIFIED",
          r.status != VERIFIED)

    # G3: STUDENT_INPUT propagates upward
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", F(800, tier="STUDENT_INPUT"))))
    check("G3a. STUDENT_INPUT input -> STUDENT_INPUT result",
          r.status == STUDENT_INPUT, r.status)

    # G4: RECONCILED propagates upward (direct node construction)
    g = graph(("Revenue", F(1000)))
    g.add(FactNode(node_id="Expenses", canonical_concept="Expenses",
                   value=Decimal("800"), status=RECONCILED,
                   source_tier="DOCUMENT"))
    r = s.solve("Profit", g)
    check("G4a. RECONCILED input -> RECONCILED result",
          r.status == RECONCILED, r.status)

    # G5: REVIEW_REQUIRED never silently becomes VERIFIED or DERIVED
    g = graph(("Revenue", F(1000)))
    g.add(FactNode(node_id="Expenses", canonical_concept="Expenses",
                   value=Decimal("800"), status=REVIEW_REQUIRED,
                   source_tier="DOCUMENT"))
    r = s.solve("Profit", g)
    check("G5a. REVIEW_REQUIRED input -> REVIEW_REQUIRED result",
          r.status == REVIEW_REQUIRED, r.status)
    check("G5b. never upgraded to VERIFIED/DERIVED",
          r.status not in (VERIFIED, DERIVED))
    check("G5c. review reason surfaced", bool(r.reason))

    # G6: BLOCKED propagates upward (blocks downstream computation)
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", F(800, tier="BLOCKED"))))
    check("G6a. BLOCKED dependency -> BLOCKED result", r.status == BLOCKED)

    # G7: conflicting reported fact vs derivation -> REVIEW_REQUIRED,
    # reported value preserved (never overwritten)
    r = s.solve("Profit",
                graph(("Revenue", F(1000)), ("Expenses", F(500)),
                      ("Profit", F(200))))
    check("G7a. conflict -> REVIEW_REQUIRED", r.status == REVIEW_REQUIRED,
          r.status)
    check("G7b. reported value preserved", r.value == Decimal("200"),
          str(r.value))
    check("G7c. conflict reason explains both values",
          "500" in (r.reason or "") and "200" in (r.reason or ""),
          r.reason or "")

    # G8: agreeing fact + derivation stays VERIFIED (direct)
    r = s.solve("Profit",
                graph(("Revenue", F(1000)), ("Expenses", F(800)),
                      ("Profit", F(200))))
    check("G8a. agreeing derivation -> VERIFIED direct", r.status == VERIFIED,
          r.status)

    # G9: propagate_statuses unit behavior (weakest-link)
    check("G9a. weakest link BLOCKED",
          propagate_statuses([VERIFIED, BLOCKED]) == BLOCKED)
    check("G9b. weakest link REVIEW_REQUIRED",
          propagate_statuses([VERIFIED, REVIEW_REQUIRED]) == REVIEW_REQUIRED)
    check("G9c. weakest link STUDENT_INPUT",
          propagate_statuses([VERIFIED, STUDENT_INPUT]) == STUDENT_INPUT)
    check("G9d. weakest link RECONCILED",
          propagate_statuses([VERIFIED, RECONCILED]) == RECONCILED)
    check("G9e. all VERIFIED -> DERIVED",
          propagate_statuses([VERIFIED, VERIFIED]) == DERIVED)
    check("G9f. no inputs -> DERIVED (vacuously computable)",
          propagate_statuses([]) == DERIVED)
    check("G9g. weaker() deterministic",
          weaker(VERIFIED, BLOCKED) == BLOCKED)
    check("G9h. all six statuses defined",
          set(ALL_STATUSES) == {VERIFIED, DERIVED, RECONCILED,
                                STUDENT_INPUT, REVIEW_REQUIRED, BLOCKED})
    check("G9i. every status has a label", all(
        STATUS_LABELS[st] for st in ALL_STATUSES))


# ---------------------------------------------------------------------------
# PART H - MATHEMATICAL SAFETY
# ---------------------------------------------------------------------------

def test_h_safety():
    print("PART H - MATHEMATICAL SAFETY")

    s = Solver(prefer_cpp=True)

    # H1: division by zero -> BLOCKED with explicit reason (no crash)
    r = s.solve("Asset Turnover",
                graph(("Revenue", F(1000)), ("Assets", F(0))))
    check("H1a. zero denominator -> BLOCKED", r.status == BLOCKED, r.status)
    check("H1b. division-by-zero reason surfaced",
          "zero" in (r.reason or "").lower(), r.reason or "")

    # H2: missing dependency -> BLOCKED (never guessed)
    r = s.solve("Profit", graph(("Revenue", F(1000))))
    check("H2a. missing dependency -> BLOCKED", r.status == BLOCKED)
    check("H2b. missing dependency named",
          "Expenses" in r.missing, str(r.missing))

    # H3: null / non-numeric values -> BLOCKED
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", {"value": None, "source": "Doc",
                                    "provenance_tier": "DOCUMENT"})))
    check("H3a. null input -> BLOCKED", r.status == BLOCKED)

    # H4: invalid units (currency vs shares) -> BLOCKED
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", F(800, unit="shares"))))
    check("H4a. quantity mismatch -> BLOCKED", r.status == BLOCKED, r.status)
    check("H4b. mismatch reason surfaced",
          "incompatible quantities" in (r.reason or "").lower(),
          r.reason or "")

    # H5: incompatible periods -> BLOCKED
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", F(800, period="FY2024"))))
    check("H5a. period mismatch -> BLOCKED", r.status == BLOCKED, r.status)
    check("H5b. period reason surfaced",
          "period" in (r.reason or "").lower(), r.reason or "")

    # H6: currency mismatch -> BLOCKED (never silently converted)
    r = s.solve("Profit",
                graph(("Revenue", F(1000)),
                      ("Expenses", F(800, unit="INR"))))
    check("H6a. currency mismatch -> BLOCKED", r.status == BLOCKED, r.status)
    check("H6b. currency reason surfaced",
          "currency" in (r.reason or "").lower(), r.reason or "")

    # H7: circular dependencies terminate deterministically as BLOCKED
    cyc = FormulaRegistry()
    cyc.register(FormulaDefinition(
        formula_id="CYCF", target="P",
        expression="P + Q", dependencies=["P", "Q"],
    ))
    r = Solver(cyc, prefer_cpp=False).solve("P", graph())
    check("H7a. circular registry -> BLOCKED (no hang)", r.status == BLOCKED)

    # H8: underdetermined equations -> BLOCKED (two unknowns, one equation
    # is never invented away)
    r = s.solve("Profit", graph(("Revenue", F(1000))))
    check("H8a. underdetermined -> BLOCKED", r.status == BLOCKED)

    # H9: overflow/precision safety - large Decimals compute exactly
    r = s.solve("Profit",
                graph(("Revenue", F("1000000000000000000")),
                      ("Expenses", F("999999999999999998"))))
    check("H9a. large-value arithmetic exact",
          r.status == DERIVED and r.value == Decimal("2"),
          r.display_value)

    # H10: division with tiny denominators is exact (no float drift in
    # value; display rounding at output only). The VALUE of a percent-kind
    # result is the percentage NUMBER (33.33 for 33.33%) - the contract of
    # the legacy engine (_fmt_percent: "Percent-kind formulas produce the
    # percentage NUMBER, e.g. 36.61") and of this solver's _compute_forward
    # (value * 100). The C++ bridge stays raw (fraction); the Python layer
    # owns percent scaling. Only the DISPLAY rounds to 2dp.
    r = s.solve("Profit Margin", graph(("Profit", F(1)), ("Revenue", F(3))))
    check("H10a. exact percentage number retained (no float drift)",
          r.status == DERIVED
          and r.value == Decimal("100") / Decimal("3"),
          str(r.value))
    check("H10b. percent display rounded at output only",
          r.display_value == "33.33%", r.display_value)


# ---------------------------------------------------------------------------
# PART I - UNIT / SCALE NORMALIZATION
# ---------------------------------------------------------------------------

def test_i_units():
    print("PART I - UNIT / SCALE NORMALIZATION")

    # I1: scale normalization (spec example: 125.4 USD millions -> 125400000)
    check("I1a. millions multiplier", scale_multiplier("millions") == Decimal("1000000"))
    check("I1b. normalize millions -> absolute",
          normalize_value(Decimal("125.4"), "millions") == Decimal("125400000"))
    check("I1c. normalize crores",
          normalize_value(Decimal("100"), "crores") == Decimal("1000000000"))
    check("I1d. normalize thousands",
          normalize_value(Decimal("5"), "thousands") == Decimal("5000"))
    check("I1e. unknown scale returns value unchanged (never guessed)",
          normalize_value(Decimal("5"), "wibbles") == Decimal("5"))

    # I2: apply_scale facts normalize inside the solver; originals preserved
    g = build_fact_graph({
        "Revenue": {
            "value": 125.4, "unit": "USD millions", "scale": "millions",
            "reporting_period": "FY2025", "provenance_tier": "DOCUMENT",
        },
        "Expenses": F(80, unit="USD"),
    })
    # NOTE: pipeline facts are already normalized (apply_scale=False), so a
    # direct FactNode with apply_scale=True is the spec-style path.
    g2 = graph(("Revenue", F(1000)), ("Expenses", F(800)))
    check("I2a. pipeline facts compute directly",
          Solver(prefer_cpp=False).solve("Profit", g2).value == Decimal("200"))

    node = FactNode(node_id="Revenue", canonical_concept="Revenue",
                    value=Decimal("125.4"), original_value=125.4,
                    original_unit="USD millions", original_scale="millions",
                    currency="USD", period="FY2025", source_tier="DOCUMENT",
                    status=VERIFIED, apply_scale=True)
    g3 = build_fact_graph({})
    g3.add(node)
    g3.add(FactNode(node_id="Expenses", canonical_concept="Expenses",
                    value=Decimal("100"), original_value=100,
                    original_unit="USD", original_scale="millions",
                    currency="USD", period="FY2025", source_tier="DOCUMENT",
                    status=VERIFIED, apply_scale=True))
    r = Solver(prefer_cpp=True).solve("Profit", g3)
    check("I2b. apply_scale facts normalized before arithmetic",
          r.value == Decimal("25400000"),
          f"{r.display_value} ({r.status})")

    # I3: incompatible quantities are never merged into one unit system
    from backend.maths.units import quantities_compatible_for_add_sub
    reason = quantities_compatible_for_add_sub(
        (classify_quantity("USD"), classify_quantity("shares")))
    check("I3a. currency vs shares -> mismatch reason",
          reason is not None and "Incompatible quantities" in reason,
          reason or "")
    reason = quantities_compatible_for_add_sub(
        (classify_quantity("USD"), classify_quantity("USD")))
    check("I3b. same currency compatible", reason is None)
    reason = quantities_compatible_for_add_sub(
        (classify_quantity(None), classify_quantity("USD")))
    check("I3c. unknown quantity tolerated", reason is None)
    # division between incompatible kinds is rejected (ratio safety)
    from backend.maths.units import quantities_compatible_for_divide
    reason = quantities_compatible_for_divide(
        classify_quantity("USD"), classify_quantity("shares"))
    check("I3d. currency / shares division rejected",
          reason is not None, reason or "")


# ---------------------------------------------------------------------------
# PART J - DETERMINISTIC LINEAGE
# ---------------------------------------------------------------------------

def test_j_lineage():
    print("PART J - DETERMINISTIC LINEAGE")

    s = Solver(prefer_cpp=True)

    # J1: forward result lineage is complete
    r = s.solve("Profit", graph(("Revenue", F(1000, page="12")),
                                ("Expenses", F(800, page="17"))))
    check("J1a. lineage record attached", r.lineage is not None)
    ld = r.lineage.to_dict()
    check("J1b. lineage has target", ld["target"] == "Profit")
    check("J1c. lineage has status", ld["status"] == DERIVED)
    check("J1d. lineage has formula id", ld["formula_id"] == "PROFIT")
    check("J1e. lineage has traversal path",
          "Revenue" in ld["traversal_path"] and "Expenses" in ld["traversal_path"])
    check("J1f. lineage has intermediate steps", len(ld["steps"]) >= 1)
    check("J1g. lineage steps carry inputs with provenance",
          any(step["inputs"] for step in ld["steps"]))
    text = r.lineage.render_text()
    check("J1h. lineage text shows values",
          "Revenue = 1000.00" in text and "Expenses = 800.00" in text, "")
    check("J1i. lineage text shows formula", "PROFIT" in text)
    check("J1j. lineage text shows pages", "p.12" in text and "p.17" in text)
    check("J1k. lineage text shows result", "200.00" in text)
    check("J1l. every derived result has lineage",
          r.lineage is not None)

    # J2: reverse result lineage notes the reverse relationship
    r = s.solve("Expenses", graph(("Revenue", F(1000)), ("Profit", F(200))))
    check("J2a. reverse lineage formula marked reverse",
          "reverse" in (r.lineage.to_dict()["formula"] or ""),
          r.lineage.to_dict()["formula"] or "")

    # J3: BLOCKED results carry an explicit reason
    r = s.solve("Profit", graph(("Revenue", F(1000))))
    check("J3a. blocked reason present", bool(r.reason), r.reason or "")
    check("J3b. blocked reason names the gap", "Expenses" in (r.reason or ""),
          r.reason or "")

    # J4: deterministic repeated execution (spec: repeated execution
    # produces identical results)
    results = [
        s.solve("Profit", graph(("Revenue", F(1000)), ("Expenses", F(800))))
        for _ in range(5)
    ]
    dicts = [r.to_dict() for r in results]
    check("J4a. 5 repeated solves identical",
          all(d == dicts[0] for d in dicts))
    r1 = s.solve("Profit Margin",
                 graph(("Profit", F(200)), ("Revenue", F(1000))))
    r2 = s.solve("Profit Margin",
                 graph(("Profit", F(200)), ("Revenue", F(1000))))
    check("J4b. percent result deterministic",
          r1.to_dict() == r2.to_dict())

    # J5: solve_many is deterministic and isolated
    many = s.solve_many(["Profit", "Expenses"],
                        graph(("Revenue", F(1000)), ("Profit", F(200)),
                              ("Expenses", F(800))))
    check("J5a. solve_many covers both targets",
          "Profit" in many and "Expenses" in many)
    check("J5b. solve_many values correct",
          many["Profit"].value == Decimal("200")
          and many["Expenses"].value == Decimal("800"))


# ---------------------------------------------------------------------------
# PART K - ADAPTER + EXISTING ENGINE INTEGRATION
# ---------------------------------------------------------------------------

def test_k_adapter():
    print("PART K - ADAPTER + EXISTING ENGINE INTEGRATION")

    from backend.maths.adapter import (
        calculate_with_graph,
        can_solve_with_graph,
        get_graph_lineage,
        get_graph_status,
    )

    # K1: delegation whitelist routing
    check("K1a. Profit is delegated", can_solve_with_graph("Profit") is True)
    check("K1b. Expenses is delegated (reverse)",
          can_solve_with_graph("Expenses") is True)
    check("K1c. Asset Turnover is delegated",
          can_solve_with_graph("Asset Turnover") is True)
    check("K1d. legacy ROE NOT delegated",
          can_solve_with_graph("ROE") is False)
    check("K1e. unanalyzed DCF NOT delegated",
          can_solve_with_graph("DCF") is False)
    check("K1f. ambiguous workspace labels NOT delegated",
          can_solve_with_graph("Working Capital") is False
          and can_solve_with_graph("Gross Profit") is False)

    # K2: adapter calculation returns legacy-shaped result
    facts = {"Revenue": F(1000), "Expenses": F(800)}
    r = calculate_with_graph("Profit", facts, None)
    check("K2a. adapter returns a result", r is not None)
    check("K2b. legacy-shaped value", r["value"] == 200.0)
    check("K2c. legacy status vocabulary", r["status"] == "derived",
          r["status"])
    check("K2d. display value", r["display_value"] == "200.00")
    check("K2e. six-tier status preserved internally",
          r["maths_status"] == DERIVED)
    check("K2f. inputs carry provenance",
          len(r["inputs"]) == 2
          and all(i["provenance_tier"] for i in r["inputs"]))
    check("K2g. lineage rendered", bool(r["lineage"]))

    # K3: non-delegated metrics return None (caller keeps existing behavior)
    check("K3a. ROE -> None from adapter",
          calculate_with_graph("ROE", facts, None) is None)
    check("K3b. DCF -> None from adapter",
          calculate_with_graph("DCF", facts, None) is None)

    # K4: get_graph_lineage / get_graph_status
    lin = get_graph_lineage("Profit", facts, None)
    check("K4a. lineage dict target", lin.get("target") == "Profit")
    check("K4b. status API", get_graph_status("Profit", facts, None) == DERIVED)
    check("K4c. status API blocked", get_graph_status("ROE", facts, None) == "UNANALYZED")

    # K5: EXISTING ENGINE - calculate_metric routes new concepts to the
    # maths engine and keeps the legacy 9 + UNANALYZED unchanged
    from backend.formula_engine import calculate_metric
    r = calculate_metric("Profit", facts, None)
    check("K5a. calculate_metric(Profit) delegates",
          r is not None and r["value"] == 200.0
          and r["status"] == "derived",
          str(r.get("status") if r else None))
    r = calculate_metric("Expenses",
                         {"Revenue": F(1000), "Profit": F(200)}, None)
    check("K5b. calculate_metric(Expenses) reverse delegates",
          r is not None and r["value"] == 800.0,
          str(r.get("value") if r else None))
    r = calculate_metric("ROE",
                         {"Net Profit": F(98300), "Equity": F(268500)}, None)
    check("K5c. legacy ROE still resolves via legacy path",
          r is not None and r.get("status") == "derived",
          str(r.get("status") if r else None))
    check("K5d. legacy ROE display unchanged",
          r["display_value"] == "36.61%", r["display_value"])
    r = calculate_metric("DCF", facts, None)
    check("K5e. DCF stays UNANALYZED",
          r is not None and r.get("status") == "unanalyzed"
          and r.get("error") == "UNSUPPORTED",
          str(r.get("status") if r else None))

    # K6: blocked delegation flows through the existing engine shape
    r = calculate_metric("Profit", {"Revenue": F(1000)}, None)
    check("K6a. blocked delegation surfaced",
          r is not None and r["status"] == "blocked" and r["reason"],
          str(r.get("status") if r else None))


# ---------------------------------------------------------------------------
# PART L - C++ ENGINE BRIDGE
# ---------------------------------------------------------------------------

def test_l_cpp():
    print("PART L - C++ ENGINE BRIDGE")

    from backend.formula_engine_cpp import (
        binary_path,
        cpp_available,
        cpp_calculate,
        cpp_solve_metric,
    )

    check("L0. C++ binary available", cpp_available())
    if not cpp_available():
        print("  (C++ binary unavailable - skipping C++ checks)")
        return

    # L1: registry contracts (legacy unchanged at 9; extended is the
    # Sprint 12F production coverage set - 24 additive formulas covering
    # EPS, margins, turnover ratios, Quick Ratio, the DuPont chain and
    # the registered +/-Loss opposites, plus ROA over Total Assets).
    bin_path = binary_path()
    out = subprocess.run([bin_path, "--registry"], capture_output=True,
                         text=True, timeout=30)
    reg = json.loads(out.stdout)
    check("L1a. --registry returns exactly 9 legacy formulas",
          len(reg) == 9, str(len(reg)))
    out = subprocess.run([bin_path, "--registry-ext"], capture_output=True,
                         text=True, timeout=30)
    ext = json.loads(out.stdout)
    check("L1b. --registry-ext returns 24 formulas (Sprint 12F coverage)",
          len(ext) == 24, str(len(ext)))
    keys = {e["metric_key"] for e in ext}
    check("L1c. extended registry keys (Sprint 12F coverage set)",
          keys == {"PROFIT", "LOSS", "GROSS_PROFIT", "WORKING_CAPITAL",
                   "ASSET_TURNOVER", "EQUITY_MULTIPLIER", "PROFIT_MARGIN",
                   "ROA_TOTAL_ASSETS", "GROSS_MARGIN", "EBITDA_MARGIN",
                   "NET_MARGIN", "EPS", "DEBT_TO_ASSETS",
                   "INTEREST_COVERAGE", "INVENTORY_TURNOVER",
                   "RECEIVABLES_TURNOVER", "PAYABLES_TURNOVER",
                   "QUICK_RATIO", "DUPONT_PROFIT_MARGIN",
                   "DUPONT_ASSET_TURNOVER", "DUPONT_EQUITY_MULTIPLIER",
                   "DUPONT_ROE", "PROFIT_LOSS_OPPOSITE",
                   "LOSS_PROFIT_OPPOSITE"},
          str(sorted(keys)))

    # L2: C++ forward calculation through the Python bridge
    facts = {"Revenue": F(1000), "Expenses": F(800)}
    out = cpp_calculate("PROFIT", facts)
    check("L2a. C++ PROFIT forward",
          out is not None and out["status"] == "derived"
          and out["value"] == 200.0, str(out))
    out = cpp_calculate("PROFIT_MARGIN", {"Profit": F(200), "Revenue": F(1000)})
    check("L2b. C++ PROFIT_MARGIN percent",
          out is not None and out["display_value"] == "20.00%",
          str(out))

    # L3: C++ reverse solving through the Python bridge
    out = cpp_solve_metric("PROFIT", "Expenses",
                           {"Revenue": F(1000), "Profit": F(200)})
    check("L3a. C++ reverse Expenses",
          out is not None and out["status"] == "derived"
          and out["value"] == 800.0, str(out))
    out = cpp_solve_metric("LOSS", "Expenses",
                           {"Revenue": F(1000), "Loss": F(200)})
    check("L3b. C++ reverse Expenses via Loss",
          out is not None and out["value"] == 1200.0, str(out))

    # L4: C++ blocked behavior (division by zero / unknown)
    out = cpp_calculate("ASSET_TURNOVER", {"Revenue": F(1000), "Assets": F(0)})
    check("L4a. C++ zero denominator blocked",
          out is not None and out["status"] == "blocked", str(out))
    out = cpp_calculate("DCF", facts)
    check("L4b. C++ unknown metric unanalyzed",
          out is not None and out["status"] == "unanalyzed", str(out))

    # L5: legacy 9 still compute via C++ (backward compatibility)
    out = cpp_calculate("ROE", {"Net Profit": F(98300), "Equity": F(268500)})
    check("L5a. C++ legacy ROE unchanged",
          out is not None and out["display_value"] == "36.61%",
          str(out))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("=" * 62)
    print("SPRINT 12A - CORE DETERMINISTIC MATHEMATICS ENGINE")
    print("=" * 62)
    test_a_registry()
    test_b_fact_model()
    test_c_graph()
    test_d_sufficiency()
    test_e_forward()
    test_f_reverse()
    test_g_status()
    test_h_safety()
    test_i_units()
    test_j_lineage()
    test_k_adapter()
    test_l_cpp()

    failed = [c for c in CHECKS if not c[1]]
    print("=" * 62)
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED CHECKS:")
        for name, _, detail in failed:
            print(f"  - {name}  [{detail}]")
        sys.exit(1)
    print("ALL CHECKS COMPLETE")
    sys.exit(0)


if __name__ == "__main__":
    main()
