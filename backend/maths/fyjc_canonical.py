"""
Financial Timeline Engine
Sprint 15D - FYJC Canonical Formula / Rule Registry
backend/maths/fyjc_canonical.py

One canonical formula/rule -> many validated solution paths -> many
textbook question forms.

This module owns the CANONICAL TRUTH for the FYJC surface:

  * CanonicalFormulaRegistry - a centralized, versioned registry of FYJC
    Maths / Commercial Arithmetic relationships. Each entry records the
    canonical equation, its variables, unit/percentage semantics, every
    supported target variable, the academic topic, the solution
    methodology, a version, its provenance and its validation status.

  * BK_RULES - the canonical Book-Keeping rules (Real / Personal /
    Nominal Golden Rules). The accounting reasoning layer composes these
    rules (transaction -> account -> class -> rule -> debit/credit ->
    journal -> ledger -> trial balance); it never invents new handlers
    per sentence pattern.

  * build_fyjc_formula_registry() - the EXECUTABLE registry the strict
    C++-authority solver consumes: the existing 12A-12F extended
    registry (behaviour-identical copy) plus the Sprint 15D commercial-
    arithmetic relationships registered as FormulaDefinition entries with
    expression + registered inverses, matching the C++ FYJC registry
    (--registry-fyjc) exactly.

Every derivation performed by the derivation engine
(backend/maths/fyjc_derivation.py) is checked against these registered
inverses; the C++ authority performs every numerical step.

Scope rule (Sprint 15D section 13): this is CONTROLLED derivation of
registered FYJC relationships. Simple Interest, Compound Interest,
Dividend, GST, Arithmetic/Geometric Progression and any other topic
outside the registered FYJC capability boundary stay UNSUPPORTED and are
refused - the pilot oracle (P12/P13/P14/P15/P16/P17) must remain green.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from backend.maths.extended_registry import (
    EXTENDED_REGISTRY,
    build_extended_registry,
)
from backend.maths.formula_registry import (
    FormulaDefinition,
    FormulaRegistry,
)

# ---------------------------------------------------------------------------
# Canonical Formula record (Sprint 15D section 1)
# ---------------------------------------------------------------------------

VALIDATION_PENDING = "PENDING"
VALIDATION_VALIDATED = "VALIDATED"
VALIDATION_REJECTED = "REJECTED"


@dataclass
class CanonicalFormula:
    """One canonical FYJC relationship with full Sprint 15D metadata.

    formula_id            unique registry key (e.g. "COMMISSION")
    canonical_formula     the canonical equation, e.g. "Profit = Revenue - Expenses"
    target                the canonical output concept (e.g. "Commission")
    expression            forward expression over the dependencies
                          (e.g. "Sales * Commission Rate / 100")
    dependencies          ordered required input concepts
    unit_kind             "amount" | "ratio" | "percent"
    percentage_semantics  how percentages are stated (e.g. "rate_is_percent_number")
    supported_targets     every variable of the relationship that has a
                          validated solution path (target + dependencies)
    academic_topic        FYJC topic label
    solution_methodology  student-facing solution method description
    version               registry version of this canonical definition
    provenance            source/definition reference
    validation_status     PENDING / VALIDATED / REJECTED (independent
                          validation is performed by the derivation engine)
    """

    formula_id: str
    canonical_formula: str
    target: str
    expression: str
    dependencies: List[str]
    unit_kind: str = "amount"
    percentage_semantics: str = "none"
    supported_targets: List[str] = field(default_factory=list)
    academic_topic: str = "Commercial Arithmetic"
    solution_methodology: str = ""
    version: str = "1.0"
    provenance: str = ""
    validation_status: str = VALIDATION_PENDING

    def __post_init__(self) -> None:
        if not self.supported_targets:
            self.supported_targets = [self.target] + list(self.dependencies)
        if self.unit_kind not in ("amount", "ratio", "percent"):
            raise ValueError(
                f"Canonical {self.formula_id}: invalid unit_kind "
                f"{self.unit_kind!r}."
            )

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "canonical_formula": self.canonical_formula,
            "target": self.target,
            "expression": self.expression,
            "dependencies": list(self.dependencies),
            "unit_kind": self.unit_kind,
            "percentage_semantics": self.percentage_semantics,
            "supported_targets": list(self.supported_targets),
            "academic_topic": self.academic_topic,
            "solution_methodology": self.solution_methodology,
            "version": self.version,
            "provenance": self.provenance,
            "validation_status": self.validation_status,
        }


class CanonicalFormulaRegistry:
    """Versioned registry of canonical FYJC relationships."""

    def __init__(self) -> None:
        self._by_id: Dict[str, CanonicalFormula] = {}
        self._by_target: Dict[str, List[str]] = {}

    def register(self, canonical: CanonicalFormula) -> CanonicalFormula:
        if canonical.formula_id in self._by_id:
            raise ValueError(
                f"Canonical {canonical.formula_id!r} already registered."
            )
        self._by_id[canonical.formula_id] = canonical
        self._by_target.setdefault(canonical.target, []).append(
            canonical.formula_id
        )
        return canonical

    def get(self, formula_id: str) -> Optional[CanonicalFormula]:
        return self._by_id.get(formula_id)

    def for_target(self, target: str) -> List[CanonicalFormula]:
        """Canonical formulas whose OUTPUT concept is `target`."""
        return [self._by_id[fid] for fid in self._by_target.get(target, [])]

    def covering(self, concept: str) -> List[CanonicalFormula]:
        """Every canonical formula for which `concept` is a target OR a
        dependency (deterministic registration order)."""
        out: List[CanonicalFormula] = []
        for c in self._by_id.values():
            if concept == c.target or concept in c.dependencies:
                out.append(c)
        return out

    def all(self) -> List[CanonicalFormula]:
        return list(self._by_id.values())

    def all_ids(self) -> List[str]:
        return list(self._by_id.keys())

    def __len__(self) -> int:
        return len(self._by_id)


def build_default_canonical_registry() -> CanonicalFormulaRegistry:
    """The canonical FYJC relationships (Sprint 15D section 1 + 5).

    Includes the Sprint 15D commercial-arithmetic set AND the core P&L
    identities already computed by the extended registry (demonstrating
    "one canonical formula -> many validated paths" for Profit/Loss too).
    """
    reg = CanonicalFormulaRegistry()

    # ---- Core P&L identities (existing registry, expressed canonically) ----
    reg.register(CanonicalFormula(
        formula_id="PROFIT",
        canonical_formula="Profit = Revenue - Expenses",
        target="Profit",
        expression="Revenue - Expenses",
        dependencies=["Revenue", "Expenses"],
        unit_kind="amount",
        supported_targets=["Profit", "Revenue", "Expenses"],
        academic_topic="Profit / Loss",
        solution_methodology=(
            "Profit = Revenue - Expenses; solve any missing figure by "
            "re-arranging the same relationship."
        ),
        version="1.0",
        provenance="Accounting identity: Income = Revenue - Expenses",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="LOSS",
        canonical_formula="Loss = Expenses - Revenue",
        target="Loss",
        expression="Expenses - Revenue",
        dependencies=["Revenue", "Expenses"],
        unit_kind="amount",
        supported_targets=["Loss", "Expenses", "Revenue"],
        academic_topic="Profit / Loss",
        solution_methodology=(
            "Loss = Expenses - Revenue (positive magnitude); solve any "
            "missing figure by re-arranging the same relationship."
        ),
        version="1.0",
        provenance="Loss = Expenses - Revenue = -(Revenue - Expenses)",
        validation_status=VALIDATION_VALIDATED,
    ))

    # ---- Core FYJC ratios (reverse-routing coverage: Find Equity,
    # Find Revenue via margin, Find Shares Outstanding, ...) ----
    reg.register(CanonicalFormula(
        formula_id="GROSS_PROFIT",
        canonical_formula="Gross Profit = Revenue - Cost of Sales",
        target="Gross Profit",
        expression="Revenue - Cost of Sales",
        dependencies=["Revenue", "Cost of Sales"],
        unit_kind="amount",
        supported_targets=["Gross Profit", "Revenue", "Cost of Sales"],
        academic_topic="Profit / Loss",
        solution_methodology=(
            "Gross Profit = Revenue - Cost of Sales; solve any missing "
            "figure by re-arranging the same relationship."
        ),
        version="1.0",
        provenance="P&L: Gross Profit = Revenue - Cost of Sales",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="PROFIT_MARGIN",
        canonical_formula="Profit Margin = Profit ÷ Revenue × 100",
        target="Profit Margin",
        expression="Profit / Revenue * 100",
        dependencies=["Profit", "Revenue"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["Profit Margin", "Profit", "Revenue"],
        academic_topic="Percentages",
        solution_methodology=(
            "Profit Margin = Profit ÷ Revenue × 100; solve Profit or "
            "Revenue from the same relationship."
        ),
        version="1.0",
        provenance="Profit Margin = Profit / Revenue (shown as a percentage)",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="NET_MARGIN",
        canonical_formula="Net Margin = Net Profit ÷ Revenue × 100",
        target="Net Margin",
        expression="Net Profit / Revenue * 100",
        dependencies=["Net Profit", "Revenue"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["Net Margin", "Net Profit", "Revenue"],
        academic_topic="Percentages",
        solution_methodology=(
            "Net Margin = Net Profit ÷ Revenue × 100; solve Net Profit or "
            "Revenue from the same relationship."
        ),
        version="1.0",
        provenance="Net Margin = Net Profit / Revenue",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="ROE",
        canonical_formula="ROE = Net Profit ÷ Equity × 100",
        target="ROE",
        expression="Net Profit / Equity * 100",
        dependencies=["Net Profit", "Equity"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["ROE", "Net Profit", "Equity"],
        academic_topic="Ratio & Proportion",
        solution_methodology=(
            "ROE = Net Profit ÷ Equity × 100; solve Net Profit or Equity "
            "from the same relationship."
        ),
        version="1.0",
        provenance="ROE = Net Profit / Shareholders' Equity",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="ROA",
        canonical_formula="ROA = Net Profit ÷ Total Assets × 100",
        target="ROA",
        expression="Net Profit / Total Assets * 100",
        dependencies=["Net Profit", "Total Assets"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["ROA", "Net Profit", "Total Assets"],
        academic_topic="Ratio & Proportion",
        solution_methodology=(
            "ROA = Net Profit ÷ Total Assets × 100; solve Net Profit or "
            "Total Assets from the same relationship."
        ),
        version="1.0",
        provenance="ROA = Net Profit / Total Assets",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="EPS",
        canonical_formula="EPS = Net Profit ÷ Shares Outstanding",
        target="EPS",
        expression="Net Profit / Shares Outstanding",
        dependencies=["Net Profit", "Shares Outstanding"],
        unit_kind="amount",
        supported_targets=["EPS", "Net Profit", "Shares Outstanding"],
        academic_topic="Shares / Dividend",
        solution_methodology=(
            "EPS = Net Profit ÷ Shares Outstanding; solve Net Profit or "
            "Shares Outstanding from the same relationship."
        ),
        version="1.0",
        provenance="EPS = Net Profit / Weighted Shares Outstanding",
        validation_status=VALIDATION_VALIDATED,
    ))

    # ---- Sprint 15D commercial arithmetic ----
    reg.register(CanonicalFormula(
        formula_id="COMMISSION",
        canonical_formula="Commission = Sales × Commission Rate ÷ 100",
        target="Commission",
        expression="Sales * Commission Rate / 100",
        dependencies=["Sales", "Commission Rate"],
        unit_kind="amount",
        percentage_semantics="rate_is_percent_number",
        supported_targets=["Commission", "Sales", "Commission Rate"],
        academic_topic="Commercial Arithmetic - Commission",
        solution_methodology=(
            "Commission = Sales × Rate ÷ 100. The rate is stated as a "
            "percent number (5 for 5%). Solve Sales or the rate from the "
            "same relationship."
        ),
        version="1.0",
        provenance="FYJC Commercial Arithmetic: commission on sales",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="TRADE_DISCOUNT",
        canonical_formula="Trade Discount = List Price × Trade Discount Rate ÷ 100",
        target="Trade Discount",
        expression="List Price * Trade Discount Rate / 100",
        dependencies=["List Price", "Trade Discount Rate"],
        unit_kind="amount",
        percentage_semantics="rate_is_percent_number",
        supported_targets=["Trade Discount", "List Price", "Trade Discount Rate"],
        academic_topic="Commercial Arithmetic - Trade Discount",
        solution_methodology=(
            "Trade Discount = List Price × Rate ÷ 100; Net Price = "
            "List Price - Trade Discount."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="CASH_DISCOUNT",
        canonical_formula="Cash Discount = Paid Amount × Cash Discount Rate ÷ 100",
        target="Cash Discount",
        expression="Paid Amount * Cash Discount Rate / 100",
        dependencies=["Paid Amount", "Cash Discount Rate"],
        unit_kind="amount",
        percentage_semantics="rate_is_percent_number",
        supported_targets=["Cash Discount", "Paid Amount", "Cash Discount Rate"],
        academic_topic="Commercial Arithmetic - Cash Discount",
        solution_methodology=(
            "Cash Discount = Paid Amount × Rate ÷ 100; Cash Paid = "
            "Paid Amount - Cash Discount."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="NET_PRICE",
        canonical_formula="Net Price = List Price - Trade Discount",
        target="Net Price",
        expression="List Price - Trade Discount",
        dependencies=["List Price", "Trade Discount"],
        unit_kind="amount",
        supported_targets=["Net Price", "List Price", "Trade Discount"],
        academic_topic="Commercial Arithmetic - Trade Discount",
        solution_methodology=(
            "Net Price = List Price - Trade Discount; solve any missing "
            "figure by re-arranging the same relationship."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="CASH_PAID",
        canonical_formula="Cash Paid = Paid Amount - Cash Discount",
        target="Cash Paid",
        expression="Paid Amount - Cash Discount",
        dependencies=["Paid Amount", "Cash Discount"],
        unit_kind="amount",
        supported_targets=["Cash Paid", "Paid Amount", "Cash Discount"],
        academic_topic="Commercial Arithmetic - Cash Discount",
        solution_methodology=(
            "Cash Paid = Paid Amount - Cash Discount; solve any missing "
            "figure by re-arranging the same relationship."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="CREDITOR_BALANCE",
        canonical_formula="Creditor Balance = Net Purchase - Amount Paid",
        target="Creditor Balance",
        expression="Net Purchase - Amount Paid",
        dependencies=["Net Purchase", "Amount Paid"],
        unit_kind="amount",
        supported_targets=["Creditor Balance", "Net Purchase", "Amount Paid"],
        academic_topic="Commercial Arithmetic - Partial Payment",
        solution_methodology=(
            "Creditor Balance = Net Purchase - Amount Paid (the amount "
            "still owing to the supplier)."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="DEBTOR_BALANCE",
        canonical_formula="Debtor Balance = Net Sale - Amount Received",
        target="Debtor Balance",
        expression="Net Sale - Amount Received",
        dependencies=["Net Sale", "Amount Received"],
        unit_kind="amount",
        supported_targets=["Debtor Balance", "Net Sale", "Amount Received"],
        academic_topic="Commercial Arithmetic - Partial Payment",
        solution_methodology=(
            "Debtor Balance = Net Sale - Amount Received (the amount "
            "still owing from the customer)."
        ),
        version="1.0",
        provenance="FYJC Book-Keeping / Commercial Arithmetic",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="SELLING_PRICE",
        canonical_formula="Selling Price = Cost Price + Profit",
        target="Selling Price",
        expression="Cost Price + Profit",
        dependencies=["Cost Price", "Profit"],
        unit_kind="amount",
        supported_targets=["Selling Price", "Cost Price", "Profit"],
        academic_topic="Commercial Arithmetic - Profit / Loss",
        solution_methodology=(
            "Selling Price = Cost Price + Profit; solve the cost price or "
            "the profit from the same relationship."
        ),
        version="1.0",
        provenance="FYJC Commercial Arithmetic: SP = CP + Profit",
        validation_status=VALIDATION_VALIDATED,
    ))
    # Percent-kind canonicals use DISPLAY-NUMBER semantics (25 for 25%):
    # the canonical expression is the equation exactly as a student
    # writes it (including ×100), so derivations agree with the
    # registered inverse expressions the C++ authority executes.
    reg.register(CanonicalFormula(
        formula_id="PROFIT_PERCENT",
        canonical_formula="Profit Percent = Profit ÷ Cost Price × 100",
        target="Profit Percent",
        expression="Profit / Cost Price * 100",
        dependencies=["Profit", "Cost Price"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["Profit Percent", "Profit", "Cost Price"],
        academic_topic="Commercial Arithmetic - Profit / Loss",
        solution_methodology=(
            "Profit % = Profit ÷ Cost Price × 100 (displayed as a "
            "percentage). Solve Profit or Cost Price from the same "
            "relationship."
        ),
        version="1.0",
        provenance="FYJC Commercial Arithmetic: P% = P / CP × 100",
        validation_status=VALIDATION_VALIDATED,
    ))
    reg.register(CanonicalFormula(
        formula_id="LOSS_PERCENT",
        canonical_formula="Loss Percent = Loss ÷ Cost Price × 100",
        target="Loss Percent",
        expression="Loss / Cost Price * 100",
        dependencies=["Loss", "Cost Price"],
        unit_kind="percent",
        percentage_semantics="target_is_percent_number",
        supported_targets=["Loss Percent", "Loss", "Cost Price"],
        academic_topic="Commercial Arithmetic - Profit / Loss",
        solution_methodology=(
            "Loss % = Loss ÷ Cost Price × 100 (displayed as a "
            "percentage). Solve Loss or Cost Price from the same "
            "relationship."
        ),
        version="1.0",
        provenance="FYJC Commercial Arithmetic: L% = L / CP × 100",
        validation_status=VALIDATION_VALIDATED,
    ))
    return reg


CANONICAL_REGISTRY = build_default_canonical_registry()


def canonical_registry() -> CanonicalFormulaRegistry:
    return CANONICAL_REGISTRY


# ---------------------------------------------------------------------------
# Canonical Book-Keeping rules (Sprint 15D section 6 + 7)
# ---------------------------------------------------------------------------
# The traditional FYJC Golden Rules. The reasoning layer composes these
# rules deterministically; it never holds a per-sentence handler.
# ---------------------------------------------------------------------------

BK_RULES: List[Dict[str, str]] = [
    {
        "rule_id": "REAL",
        "account_class": "Real",
        "golden_rule": "Debit what comes in. Credit what goes out.",
        "debit_when": "what comes in",
        "credit_when": "what goes out",
        "examples": "assets, property, machinery, furniture, cash, bank",
    },
    {
        "rule_id": "PERSONAL",
        "account_class": "Personal",
        "golden_rule": "Debit the receiver. Credit the giver.",
        "debit_when": "the receiver",
        "credit_when": "the giver",
        "examples": "persons, firms, capital, liabilities, creditors, debtors",
    },
    {
        "rule_id": "NOMINAL",
        "account_class": "Nominal",
        "golden_rule": "Debit expenses and losses. Credit incomes and gains.",
        "debit_when": "expenses and losses",
        "credit_when": "incomes and gains",
        "examples": "rent, salary, wages, commission received, discount allowed",
    },
]

BK_RULE_BY_CLASS: Dict[str, Dict[str, str]] = {
    rule["account_class"].lower(): rule for rule in BK_RULES
}


def golden_rule_for(account_class: Optional[str]) -> Optional[Dict[str, str]]:
    """The canonical Golden Rule for a traditional FYJC account class."""
    if not account_class:
        return None
    return BK_RULE_BY_CLASS.get(str(account_class).strip().lower())


def compose_transaction_rule(account: str,
                            account_class: Optional[str],
                            side: str) -> Dict[str, str]:
    """One auditable rule-composition record:

        account -> class -> golden rule -> debit/credit decision.

    Deterministic: the side is either provided by the caller (the golden
    rule engine's decision) or derived from the rule itself when the
    class admits only one side (Nominal expenses always debit, incomes
    always credit).
    """
    rule = golden_rule_for(account_class)
    if rule is None:
        return {
            "account": account,
            "account_class": None,
            "rule_id": None,
            "golden_rule": None,
            "side": side,
            "why": "No canonical rule applies to this account.",
        }
    if side is None or side == "":
        rule_text = str(rule.get("golden_rule", ""))
        if "expenses and losses" in rule_text:
            side = "debit"
        elif "incomes and gains" in rule_text:
            side = "credit"
        else:
            side = "debit"  # never reached for Real/Personal without a side
    return {
        "account": account,
        "account_class": rule["account_class"],
        "rule_id": rule["rule_id"],
        "golden_rule": rule["golden_rule"],
        "side": side,
        "why": (
            f"{rule['account_class']} Account - {rule['golden_rule']} "
            f"({side} because the account {rule.get('credit_when') if side == 'credit' else rule.get('debit_when')})."
        ),
    }


# ---------------------------------------------------------------------------
# Executable FYJC formula registry (extended + Sprint 15D commercial math)
# ---------------------------------------------------------------------------
# Every entry mirrors the C++ FYJC registry (--registry-fyjc) exactly:
# same ids, same expression semantics, same registered inverses, same
# unit/denominator contracts. The strict C++-authority solver executes
# these through the compiled engine.
# ---------------------------------------------------------------------------


def _new_formula_definitions() -> List[FormulaDefinition]:
    return [
        FormulaDefinition(
            formula_id="COMMISSION",
            target="Commission",
            description="Commission = Sales × Commission Rate ÷ 100",
            expression="Sales * Commission Rate / 100",
            dependencies=["Sales", "Commission Rate"],
            inverses={
                "Sales": "Commission * 100 / Commission Rate",
                "Commission Rate": "Commission * 100 / Sales",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Commercial Arithmetic: commission on sales",
        ),
        FormulaDefinition(
            formula_id="TRADE_DISCOUNT",
            target="Trade Discount",
            description="Trade Discount = List Price × Trade Discount Rate ÷ 100",
            expression="List Price * Trade Discount Rate / 100",
            dependencies=["List Price", "Trade Discount Rate"],
            inverses={
                "List Price": "Trade Discount * 100 / Trade Discount Rate",
                "Trade Discount Rate": "Trade Discount * 100 / List Price",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="CASH_DISCOUNT",
            target="Cash Discount",
            description="Cash Discount = Paid Amount × Cash Discount Rate ÷ 100",
            expression="Paid Amount * Cash Discount Rate / 100",
            dependencies=["Paid Amount", "Cash Discount Rate"],
            inverses={
                "Paid Amount": "Cash Discount * 100 / Cash Discount Rate",
                "Cash Discount Rate": "Cash Discount * 100 / Paid Amount",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="NET_PRICE",
            target="Net Price",
            description="Net Price = List Price - Trade Discount",
            expression="List Price - Trade Discount",
            dependencies=["List Price", "Trade Discount"],
            inverses={
                "List Price": "Net Price + Trade Discount",
                "Trade Discount": "List Price - Net Price",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="CASH_PAID",
            target="Cash Paid",
            description="Cash Paid = Paid Amount - Cash Discount",
            expression="Paid Amount - Cash Discount",
            dependencies=["Paid Amount", "Cash Discount"],
            inverses={
                "Paid Amount": "Cash Paid + Cash Discount",
                "Cash Discount": "Paid Amount - Cash Paid",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="CREDITOR_BALANCE",
            target="Creditor Balance",
            description="Creditor Balance = Net Purchase - Amount Paid",
            expression="Net Purchase - Amount Paid",
            dependencies=["Net Purchase", "Amount Paid"],
            inverses={
                "Net Purchase": "Creditor Balance + Amount Paid",
                "Amount Paid": "Net Purchase - Creditor Balance",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="DEBTOR_BALANCE",
            target="Debtor Balance",
            description="Debtor Balance = Net Sale - Amount Received",
            expression="Net Sale - Amount Received",
            dependencies=["Net Sale", "Amount Received"],
            inverses={
                "Net Sale": "Debtor Balance + Amount Received",
                "Amount Received": "Net Sale - Debtor Balance",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Book-Keeping / Commercial Arithmetic",
        ),
        FormulaDefinition(
            formula_id="SELLING_PRICE",
            target="Selling Price",
            description="Selling Price = Cost Price + Profit",
            expression="Cost Price + Profit",
            dependencies=["Cost Price", "Profit"],
            inverses={
                "Cost Price": "Selling Price - Profit",
                "Profit": "Selling Price - Cost Price",
            },
            unit_kind="amount",
            period_mode="same",
            version="1.0",
            source_ref="FYJC Commercial Arithmetic: SP = CP + Profit",
        ),
        FormulaDefinition(
            formula_id="PROFIT_PERCENT",
            target="Profit Percent",
            description="Profit Percent = Profit / Cost Price (percentage)",
            expression="Profit / Cost Price",
            dependencies=["Profit", "Cost Price"],
            inverses={
                "Profit": "(Profit Percent / 100) * Cost Price",
                "Cost Price": "Profit / (Profit Percent / 100)",
            },
            unit_kind="percent",
            period_mode="same",
            denominator_constraints=["Cost Price"],
            version="1.0",
            source_ref="FYJC Commercial Arithmetic: P% = P / CP × 100",
        ),
        FormulaDefinition(
            formula_id="LOSS_PERCENT",
            target="Loss Percent",
            description="Loss Percent = Loss / Cost Price (percentage)",
            expression="Loss / Cost Price",
            dependencies=["Loss", "Cost Price"],
            inverses={
                "Loss": "(Loss Percent / 100) * Cost Price",
                "Cost Price": "Loss / (Loss Percent / 100)",
            },
            unit_kind="percent",
            period_mode="same",
            denominator_constraints=["Cost Price"],
            version="1.0",
            source_ref="FYJC Commercial Arithmetic: L% = L / CP × 100",
        ),
    ]


def build_fyjc_formula_registry() -> FormulaRegistry:
    """The FYJC executable registry: 12A-12F extended (behaviour-identical
    copy) + the Sprint 15D commercial-arithmetic formulas. Deterministic;
    existing formula behaviour is never shadowed (each new id is fresh)."""
    reg = FormulaRegistry()
    for fid in EXTENDED_REGISTRY.all_ids():
        d = EXTENDED_REGISTRY.get(fid)
        if d is not None:
            reg.register(replace(d))
    for definition in _new_formula_definitions():
        reg.register(definition)
    return reg


FYJC_FORMULA_REGISTRY = build_fyjc_formula_registry()


def fyjc_formula_registry() -> FormulaRegistry:
    return FYJC_FORMULA_REGISTRY
