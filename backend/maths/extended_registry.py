"""
Financial Timeline Engine
Sprint 12C - Evidence-Aware Decision Graph & Production Integration
backend/maths/extended_registry.py

Declarative formula registry expansion (Sprint 12C section 8-9).

The registry remains the SINGLE source of truth for supported
relationships and formula registration stays fully separated from formula
application: adding a formula here requires NO solver or engine change.

* build_extended_registry() starts from every Sprint 12A default formula
  (copied, behavior-identical) and ADDS the new relationships:
      ROE, ROA, Current Ratio, Debt to Equity, Gross Margin,
      Operating Margin, EBITDA Margin, CAGR, EPS
  (Working Capital, Profit Margin, Asset Turnover, Equity Multiplier and
  the DuPont chain already exist in 12A/12B registries and are reused,
  never duplicated.)
* Every formula declares its required facts, unit kind, denominator
  constraints, period mode and - for reverse solving - its registered
  inverse relationships. Nothing is registered that the solver cannot
  support deterministically.
* EXTENDED_FORMULA_METADATA carries the Sprint 12C section 8 metadata:
  expected input kinds, output kind, status requirement, lineage
  behavior and the Excel formula template consumed by the Excel lineage
  compiler (excel_compiler.py).

Notes on deterministic gates
----------------------------
* CAGR uses the '^' power operator added to the expression language
  (additive) and requires an explicit integer span (years). The span is
  never guessed: it is either provided as a fact or derived from the two
  periods by derive_cagr_span() (deterministic, requires both periods).
* EPS divides Net Profit by Shares Outstanding. Per the 12A unit gate, a
  share count carrying a classified unit (e.g. "shares") is not combined
  with currency in a ratio - such a fact fails closed (BLOCKED). EPS
  computes only when the share count carries no classified unit. This is
  deliberate: the engine never silently mixes quantity kinds.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.formula_registry import (
    FormulaDefinition,
    FormulaRegistry,
    default_registry,
)
from backend.maths.status import DERIVED

# ---------------------------------------------------------------------------
# Canonical concept keys (must match the pipeline / workspace vocabulary)
# ---------------------------------------------------------------------------

ROE = "ROE"
ROA = "ROA"
CURRENT_RATIO = "Current Ratio"
DEBT_TO_EQUITY = "Debt to Equity"
GROSS_MARGIN = "Gross Margin"
OPERATING_MARGIN = "Operating Margin"
EBITDA_MARGIN = "EBITDA Margin"
CAGR = "CAGR"
EPS = "EPS"

# Sprint 12D additions
NET_MARGIN = "Net Margin"
QUICK_RATIO = "Quick Ratio"
DEBT_TO_ASSETS = "Debt to Assets"
INTEREST_COVERAGE = "Interest Coverage"
INVENTORY_TURNOVER = "Inventory Turnover"
RECEIVABLES_TURNOVER = "Receivables Turnover"
PAYABLES_TURNOVER = "Payables Turnover"

NET_PROFIT = "Net Profit"
REVENUE = "Revenue"
EQUITY = "Equity"
TOTAL_ASSETS = "Total Assets"
CURRENT_ASSETS = "Current Assets"
CURRENT_LIABILITIES = "Current Liabilities"
DEBT = "Debt"
GROSS_PROFIT = "Gross Profit"
OPERATING_PROFIT = "Operating Profit"
EBITDA = "EBITDA"
SHARES_OUTSTANDING = "Shares Outstanding"
CAGR_BEGINNING = "CAGR Beginning Value"
CAGR_ENDING = "CAGR Ending Value"
CAGR_SPAN = "CAGR Span Years"

# ---- Sprint 12D additions (section G) ----------------------------------
NET_MARGIN = "Net Margin"
QUICK_RATIO = "Quick Ratio"
DEBT_TO_ASSETS = "Debt to Assets"
INTEREST_COVERAGE = "Interest Coverage"
INVENTORY_TURNOVER = "Inventory Turnover"
RECEIVABLES_TURNOVER = "Receivables Turnover"
PAYABLES_TURNOVER = "Payables Turnover"
INVENTORY = "Inventory"
RECEIVABLES = "Receivables"
PAYABLES = "Payables"
INTEREST_EXPENSE = "Interest Expense"
EBIT = "EBIT"

# Sprint 12D dependency concepts
INVENTORY = "Inventory"
COST_OF_SALES = "Cost of Sales"
AVERAGE_INVENTORY = "Average Inventory"
AVERAGE_RECEIVABLES = "Average Receivables"
AVERAGE_PAYABLES = "Average Payables"
INTEREST_EXPENSE = "Interest Expense"

# ---------------------------------------------------------------------------
# CAGR helpers (deterministic; never guessed)
# ---------------------------------------------------------------------------


def derive_cagr_span(period_begin: Optional[str],
                     period_end: Optional[str]) -> Optional[int]:
    """Number of years between two reporting periods.

    Supports 'FY2024' / '2024' / 'FY24' / '2023-24' style labels.
    Returns None when either period is missing or the span cannot be
    established - the caller fails closed instead of guessing.
    """
    if not period_begin or not period_end:
        return None

    def _year(label: str) -> Optional[int]:
        s = str(label).strip()
        for prefix in ("FY", "fy", "F", "f"):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        s = s.split("-")[0].strip()
        if not s.isdigit():
            return None
        year = int(s)
        if 0 <= year <= 99:  # two-digit year -> assume 20xx
            year += 2000
        return year

    yb = _year(period_begin)
    ye = _year(period_end)
    if yb is None or ye is None:
        return None
    span = ye - yb
    if span < 1:
        return None
    return span


def cagr_span_from_facts(facts: Dict[str, Any]) -> Optional[int]:
    """Span from the beginning/ending period metadata of the two CAGR
    facts (0 when explicit CAGR Span Years is already a fact)."""
    span_fact = facts.get(CAGR_SPAN)
    if isinstance(span_fact, dict):
        from backend.maths.fact_model import to_decimal
        v = to_decimal(span_fact.get("normalized_value", span_fact.get("value")))
        if v is not None and v >= 1 and v == v.to_integral_value():
            return int(v)
    beg = facts.get(CAGR_BEGINNING)
    end = facts.get(CAGR_ENDING)
    pb = beg.get("reporting_period") or beg.get("period") if isinstance(beg, dict) else None
    pe = end.get("reporting_period") or end.get("period") if isinstance(end, dict) else None
    return derive_cagr_span(pb, pe)


# ---------------------------------------------------------------------------
# Extended registry construction
# ---------------------------------------------------------------------------


def _copy_default(reg: FormulaRegistry) -> FormulaRegistry:
    """Copy every Sprint 12A default formula into a fresh registry
    (behavior-identical; each copy re-validates at registration)."""
    for fid in default_registry().all_ids():
        d = default_registry().get(fid)
        if d is not None:
            reg.register(replace(d))
    return reg


def build_extended_registry() -> FormulaRegistry:
    """The Sprint 12C registry: 12A defaults + new declarative formulas."""
    reg = _copy_default(FormulaRegistry())

    reg.register(FormulaDefinition(
        formula_id="ROE",
        target=ROE,
        description="Return on Equity = Net Profit / Equity (percentage)",
        expression="Net Profit / Equity",
        dependencies=[NET_PROFIT, EQUITY],
        inverses={
            NET_PROFIT: "ROE * Equity / 100",
            EQUITY: "Net Profit / (ROE / 100)",
        },
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[EQUITY],
        version="1.0",
        source_ref="ROE = Net Profit / Shareholders' Equity",
    ))

    reg.register(FormulaDefinition(
        formula_id="ROA",
        target=ROA,
        description="Return on Assets = Net Profit / Total Assets (percentage)",
        expression="Net Profit / Total Assets",
        dependencies=[NET_PROFIT, TOTAL_ASSETS],
        inverses={
            NET_PROFIT: "ROA * Total Assets / 100",
            TOTAL_ASSETS: "Net Profit / (ROA / 100)",
        },
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[TOTAL_ASSETS],
        version="1.0",
        source_ref="ROA = Net Profit / Total Assets",
    ))

    reg.register(FormulaDefinition(
        formula_id="CURRENT_RATIO",
        target=CURRENT_RATIO,
        description="Current Ratio = Current Assets / Current Liabilities",
        expression="Current Assets / Current Liabilities",
        dependencies=[CURRENT_ASSETS, CURRENT_LIABILITIES],
        inverses={
            CURRENT_ASSETS: "Current Ratio * Current Liabilities",
            CURRENT_LIABILITIES: "Current Assets / Current Ratio",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[CURRENT_LIABILITIES],
        version="1.0",
        source_ref="Current Ratio = Current Assets / Current Liabilities",
    ))

    reg.register(FormulaDefinition(
        formula_id="DEBT_TO_EQUITY",
        target=DEBT_TO_EQUITY,
        description="Debt to Equity = Debt / Equity",
        expression="Debt / Equity",
        dependencies=[DEBT, EQUITY],
        inverses={
            DEBT: "Debt to Equity * Equity",
            EQUITY: "Debt / (Debt to Equity)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[EQUITY],
        version="1.0",
        source_ref="Debt to Equity = Total Debt / Shareholders' Equity",
    ))

    reg.register(FormulaDefinition(
        formula_id="GROSS_MARGIN",
        target=GROSS_MARGIN,
        description="Gross Margin = Gross Profit / Revenue (percentage)",
        expression="Gross Profit / Revenue",
        dependencies=[GROSS_PROFIT, REVENUE],
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[REVENUE],
        version="1.0",
        source_ref="Gross Margin = Gross Profit / Revenue",
    ))

    reg.register(FormulaDefinition(
        formula_id="OPERATING_MARGIN",
        target=OPERATING_MARGIN,
        description="Operating Margin = Operating Profit / Revenue (percentage)",
        expression="Operating Profit / Revenue",
        dependencies=[OPERATING_PROFIT, REVENUE],
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[REVENUE],
        version="1.0",
        source_ref="Operating Margin = Operating Profit / Revenue",
    ))

    reg.register(FormulaDefinition(
        formula_id="EBITDA_MARGIN",
        target=EBITDA_MARGIN,
        description="EBITDA Margin = EBITDA / Revenue (percentage)",
        expression="EBITDA / Revenue",
        dependencies=[EBITDA, REVENUE],
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[REVENUE],
        version="1.0",
        source_ref="EBITDA Margin = EBITDA / Revenue",
    ))

    reg.register(FormulaDefinition(
        formula_id="CAGR",
        target=CAGR,
        description="CAGR = (Ending / Beginning) ^ (1 / n) - 1 (percentage)",
        expression="(CAGR Ending Value / CAGR Beginning Value) "
                   "^ (1 / CAGR Span Years) - 1",
        dependencies=[CAGR_BEGINNING, CAGR_ENDING, CAGR_SPAN],
        unit_kind="percent",
        period_mode="span",
        denominator_constraints=[CAGR_BEGINNING],
        domain_rules=[
            (
                "CAGR span validation",
                lambda v: (
                    None if v.get(CAGR_SPAN) is not None
                    and v[CAGR_SPAN] >= 1
                    and v[CAGR_SPAN] == v[CAGR_SPAN].to_integral_value()
                    else "CAGR Span Years must be a whole number >= 1 "
                         "(never guessed)."
                ),
            ),
        ],
        version="1.0",
        source_ref="CAGR = (Ending / Beginning)^(1/n) - 1",
    ))

    reg.register(FormulaDefinition(
        formula_id="EPS",
        target=EPS,
        description="EPS = Net Profit / Shares Outstanding",
        expression="Net Profit / Shares Outstanding",
        dependencies=[NET_PROFIT, SHARES_OUTSTANDING],
        inverses={
            NET_PROFIT: "EPS * Shares Outstanding",
            SHARES_OUTSTANDING: "Net Profit / EPS",
        },
        unit_kind="amount",
        period_mode="same",
        denominator_constraints=[SHARES_OUTSTANDING],
        version="1.0",
        source_ref="EPS = Net Profit / Weighted Shares Outstanding",
    ))

    # ---- Sprint 12D additions (declarative; no solver changes) ------
    reg.register(FormulaDefinition(
        formula_id="NET_MARGIN",
        target=NET_MARGIN,
        description="Net Margin = Net Profit / Revenue (percentage)",
        expression="Net Profit / Revenue",
        dependencies=[NET_PROFIT, REVENUE],
        inverses={
            NET_PROFIT: "Net Margin * Revenue / 100",
            REVENUE: "Net Profit / (Net Margin / 100)",
        },
        unit_kind="percent",
        period_mode="same",
        denominator_constraints=[REVENUE],
        version="1.0",
        source_ref="Net Margin = Net Profit / Revenue",
    ))

    reg.register(FormulaDefinition(
        formula_id="QUICK_RATIO",
        target=QUICK_RATIO,
        description="Quick Ratio = (Current Assets - Inventory) / Current Liabilities",
        expression="(Current Assets - Inventory) / Current Liabilities",
        dependencies=[CURRENT_ASSETS, INVENTORY, CURRENT_LIABILITIES],
        inverses={
            CURRENT_ASSETS: "Quick Ratio * Current Liabilities + Inventory",
            CURRENT_LIABILITIES: "(Current Assets - Inventory) / (Quick Ratio)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[CURRENT_LIABILITIES],
        version="1.0",
        source_ref="Quick Ratio = (Current Assets - Inventory) / Current Liabilities",
    ))

    reg.register(FormulaDefinition(
        formula_id="DEBT_TO_ASSETS",
        target=DEBT_TO_ASSETS,
        description="Debt to Assets = Debt / Total Assets",
        expression="Debt / Total Assets",
        dependencies=[DEBT, TOTAL_ASSETS],
        inverses={
            DEBT: "Debt to Assets * Total Assets",
            TOTAL_ASSETS: "Debt / (Debt to Assets)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[TOTAL_ASSETS],
        version="1.0",
        source_ref="Debt to Assets = Total Debt / Total Assets",
    ))

    reg.register(FormulaDefinition(
        formula_id="INTEREST_COVERAGE",
        target=INTEREST_COVERAGE,
        description="Interest Coverage = Operating Profit / Interest Expense",
        expression="Operating Profit / Interest Expense",
        dependencies=[OPERATING_PROFIT, INTEREST_EXPENSE],
        inverses={
            OPERATING_PROFIT: "Interest Coverage * Interest Expense",
            INTEREST_EXPENSE: "Operating Profit / (Interest Coverage)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[INTEREST_EXPENSE],
        version="1.0",
        source_ref="Interest Coverage = EBIT / Interest Expense",
    ))

    reg.register(FormulaDefinition(
        formula_id="INVENTORY_TURNOVER",
        target=INVENTORY_TURNOVER,
        description="Inventory Turnover = Cost of Sales / Average Inventory",
        expression="Cost of Sales / Average Inventory",
        dependencies=[COST_OF_SALES, AVERAGE_INVENTORY],
        inverses={
            COST_OF_SALES: "Inventory Turnover * Average Inventory",
            AVERAGE_INVENTORY: "Cost of Sales / (Inventory Turnover)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[AVERAGE_INVENTORY],
        version="1.0",
        source_ref="Inventory Turnover = COGS / Average Inventory",
    ))

    reg.register(FormulaDefinition(
        formula_id="RECEIVABLES_TURNOVER",
        target=RECEIVABLES_TURNOVER,
        description="Receivables Turnover = Revenue / Average Receivables",
        expression="Revenue / Average Receivables",
        dependencies=[REVENUE, AVERAGE_RECEIVABLES],
        inverses={
            REVENUE: "Receivables Turnover * Average Receivables",
            AVERAGE_RECEIVABLES: "Revenue / (Receivables Turnover)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[AVERAGE_RECEIVABLES],
        version="1.0",
        source_ref="Receivables Turnover = Revenue / Average Receivables",
    ))

    reg.register(FormulaDefinition(
        formula_id="PAYABLES_TURNOVER",
        target=PAYABLES_TURNOVER,
        description="Payables Turnover = Cost of Sales / Average Payables",
        expression="Cost of Sales / Average Payables",
        dependencies=[COST_OF_SALES, AVERAGE_PAYABLES],
        inverses={
            COST_OF_SALES: "Payables Turnover * Average Payables",
            AVERAGE_PAYABLES: "Cost of Sales / (Payables Turnover)",
        },
        unit_kind="ratio",
        period_mode="same",
        denominator_constraints=[AVERAGE_PAYABLES],
        version="1.0",
        source_ref="Payables Turnover = COGS / Average Payables",
    ))

    # Sprint 12D section F: registered algebraic opposite links so
    # Revenue + Loss -> Profit and Profit + Revenue -> Loss are solvable
    # deterministically (never guessed; declared relationships only).
    reg.register(FormulaDefinition(
        formula_id="PROFIT_LOSS_OPPOSITE",
        target="Profit",
        description="Profit = -Loss (registered algebraic opposite)",
        expression="- Loss",
        dependencies=["Loss"],
        inverses={"Loss": "- Profit"},
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="Loss = Expenses - Revenue = -(Revenue - Expenses) = -Profit",
    ))

    reg.register(FormulaDefinition(
        formula_id="LOSS_PROFIT_OPPOSITE",
        target="Loss",
        description="Loss = -Profit (registered algebraic opposite)",
        expression="- Profit",
        dependencies=["Profit"],
        inverses={"Profit": "- Loss"},
        unit_kind="amount",
        period_mode="same",
        version="1.0",
        source_ref="Loss = -(Revenue - Expenses) = -Profit",
    ))

    return reg


EXTENDED_REGISTRY = build_extended_registry()


def extended_registry() -> FormulaRegistry:
    """Return the shared extended registry instance."""
    return EXTENDED_REGISTRY


# ---------------------------------------------------------------------------
# Formula metadata (Sprint 12C section 8): expected input kinds, output
# kind, status requirement, lineage behavior, Excel formula template.
# ---------------------------------------------------------------------------

EXTENDED_FORMULA_METADATA: Dict[str, Dict[str, Any]] = {
    "ROE": {
        "name": ROE,
        "output_kind": "percent",
        "expected_input_kinds": {NET_PROFIT: "amount", EQUITY: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Net Profit / Equity",
    },
    "ROA": {
        "name": ROA,
        "output_kind": "percent",
        "expected_input_kinds": {NET_PROFIT: "amount", TOTAL_ASSETS: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Net Profit / Total Assets",
    },
    "CURRENT_RATIO": {
        "name": CURRENT_RATIO,
        "output_kind": "ratio",
        "expected_input_kinds": {
            CURRENT_ASSETS: "amount", CURRENT_LIABILITIES: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Current Assets / Current Liabilities",
    },
    "DEBT_TO_EQUITY": {
        "name": DEBT_TO_EQUITY,
        "output_kind": "ratio",
        "expected_input_kinds": {DEBT: "amount", EQUITY: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Debt / Equity",
    },
    "GROSS_MARGIN": {
        "name": GROSS_MARGIN,
        "output_kind": "percent",
        "expected_input_kinds": {GROSS_PROFIT: "amount", REVENUE: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Gross Profit / Revenue",
    },
    "OPERATING_MARGIN": {
        "name": OPERATING_MARGIN,
        "output_kind": "percent",
        "expected_input_kinds": {
            OPERATING_PROFIT: "amount", REVENUE: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Operating Profit / Revenue",
    },
    "EBITDA_MARGIN": {
        "name": EBITDA_MARGIN,
        "output_kind": "percent",
        "expected_input_kinds": {EBITDA: "amount", REVENUE: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "EBITDA / Revenue",
    },
    "CAGR": {
        "name": CAGR,
        "output_kind": "percent",
        "expected_input_kinds": {
            CAGR_BEGINNING: "amount", CAGR_ENDING: "amount",
            CAGR_SPAN: "count",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "(CAGR Ending Value / CAGR Beginning Value) "
                         "^(1 / CAGR Span Years) - 1",
    },
    "EPS": {
        "name": EPS,
        "output_kind": "amount",
        "expected_input_kinds": {
            NET_PROFIT: "amount", SHARES_OUTSTANDING: "count",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Net Profit / Shares Outstanding",
    },
    # ---- Sprint 12D metadata (aliases + Excel templates) ----
    "NET_MARGIN": {
        "name": NET_MARGIN,
        "aliases": ["Net Profit Margin"],
        "output_kind": "percent",
        "expected_input_kinds": {NET_PROFIT: "amount", REVENUE: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Net Profit / Revenue",
    },
    "QUICK_RATIO": {
        "name": QUICK_RATIO,
        "aliases": ["Acid Test", "Acid-Test Ratio"],
        "output_kind": "ratio",
        "expected_input_kinds": {
            CURRENT_ASSETS: "amount", INVENTORY: "amount",
            CURRENT_LIABILITIES: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "(Current Assets - Inventory) / Current Liabilities",
    },
    "DEBT_TO_ASSETS": {
        "name": DEBT_TO_ASSETS,
        "aliases": ["Debt Ratio", "Debt/Assets"],
        "output_kind": "ratio",
        "expected_input_kinds": {DEBT: "amount", TOTAL_ASSETS: "amount"},
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Debt / Total Assets",
    },
    "INTEREST_COVERAGE": {
        "name": INTEREST_COVERAGE,
        "aliases": ["ICR", "Times Interest Earned"],
        "output_kind": "ratio",
        "expected_input_kinds": {
            OPERATING_PROFIT: "amount", INTEREST_EXPENSE: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Operating Profit / Interest Expense",
    },
    "INVENTORY_TURNOVER": {
        "name": INVENTORY_TURNOVER,
        "aliases": ["Stock Turnover"],
        "output_kind": "ratio",
        "expected_input_kinds": {
            COST_OF_SALES: "amount", AVERAGE_INVENTORY: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Cost of Sales / Average Inventory",
    },
    "RECEIVABLES_TURNOVER": {
        "name": RECEIVABLES_TURNOVER,
        "aliases": ["Debtors Turnover"],
        "output_kind": "ratio",
        "expected_input_kinds": {
            REVENUE: "amount", AVERAGE_RECEIVABLES: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Revenue / Average Receivables",
    },
    "PAYABLES_TURNOVER": {
        "name": PAYABLES_TURNOVER,
        "aliases": ["Creditors Turnover"],
        "output_kind": "ratio",
        "expected_input_kinds": {
            COST_OF_SALES: "amount", AVERAGE_PAYABLES: "amount",
        },
        "status_requirement": "weakest-link",
        "lineage_behavior": "full",
        "excel_template": "Cost of Sales / Average Payables",
    },
}


def metadata_for(formula_id: str) -> Optional[Dict[str, Any]]:
    """Formula metadata (section 8) or None for legacy 12A formulas."""
    return EXTENDED_FORMULA_METADATA.get(formula_id)


def excel_template_for(formula_id: str) -> Optional[str]:
    meta = EXTENDED_FORMULA_METADATA.get(formula_id)
    return meta.get("excel_template") if meta else None


# Default six-tier status requirement for every registered formula
# (documented, deterministic): weakest-link propagation, computed results
# are DERIVED at best - never VERIFIED.
DEFAULT_STATUS_REQUIREMENT = "weakest-link"
COMPUTED_RESULT_STATUS = DERIVED
