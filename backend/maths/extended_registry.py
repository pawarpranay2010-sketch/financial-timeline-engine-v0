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
