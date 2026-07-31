"""
Financial Timeline Engine
Module 3

Financial Calculator

Purpose
-------
Calculates financial ratios from extracted data.

Never guesses.

Only calculates when sufficient inputs exist.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.intelligence.calculation_safety_gate import CalculationSafetyGate


class FinancialCalculator:

    def __init__(self):
        pass

    # -----------------------------

    def calculate(self, financial_data: Dict[str, Any]) -> Dict[str, Any]:

        ratios = {}

        # -----------------------------
        # Revenue
        # -----------------------------

        revenue = self._value(financial_data, "Revenue")

        net_profit = self._value(financial_data, "Net Profit")

        equity = self._value(financial_data, "Equity")

        assets = self._value(financial_data, "Assets")

        liabilities = self._value(financial_data, "Liabilities")

        debt = self._value(financial_data, "Debt")

        # ----------------------------------------
        # Profit Margin
        # ----------------------------------------

        if revenue and net_profit:

            ratios["Profit Margin"] = {

                "value": round((net_profit / revenue) * 100, 2),

                "source": "Calculated"

            }

        # ----------------------------------------
        # ROE
        # ----------------------------------------

        if equity and net_profit:

            ratios["ROE"] = {

                "value": round((net_profit / equity) * 100, 2),

                "source": "Calculated"

            }

        # ----------------------------------------
        # ROA
        # ----------------------------------------

        if assets and net_profit:

            ratios["ROA"] = {

                "value": round((net_profit / assets) * 100, 2),

                "source": "Calculated"

            }

        # ----------------------------------------
        # Debt / Equity
        # ----------------------------------------

        if debt and equity:

            ratios["Debt to Equity"] = {

                "value": round(debt / equity, 2),

                "source": "Calculated"

            }

        # ----------------------------------------
        # Current Ratio
        # (placeholder until Current Assets /
        # Current Liabilities extraction exists)
        # ----------------------------------------

        return ratios

    # --------------------------------------------

    def _value(self, financial_data, key):

        if key not in financial_data:

            return None

        return financial_data[key]["value"]

    # --------------------------------------------
    # Fix #3 — Calculation Safety Gate
    # --------------------------------------------

    def safe_calculate(
        self,
        financial_data: Dict[str, Any],
        required_metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Run the deterministic Calculation Safety Gate before calculating.

        This is the enforced entry point at the calculation-engine
        boundary. Returns the gate's structured result:

            BLOCKED -> {"status": "BLOCKED", "reason": ...,
                        "required_facts": [...], "rejected_facts": [...],
                        "calculation": None}
            ALLOWED -> {"status": "ALLOWED", ..., "calculation": {...}}

        PENDING / MISSING / CONFLICTING / UNRESOLVED_CONFLICT / REJECTED /
        INSUFFICIENT_EVIDENCE / CURRENCY_MISMATCH / PERIOD_MISMATCH inputs
        block the calculation. Only VERIFIED canonical evidence may pass.
        """
        gate = CalculationSafetyGate()
        verdict = gate.check(financial_data, required_metrics)
        if verdict["status"] == "BLOCKED":
            return verdict
        ratios = self.calculate(financial_data)
        return {
            "status": "ALLOWED",
            "reason": "",
            "required_facts": verdict["required_facts"],
            "rejected_facts": [],
            "calculation": ratios,
        }


# ----------------------------------------------------


def calculate_financial_ratios(financial_data):

    calculator = FinancialCalculator()

    return calculator.calculate(financial_data)


# ----------------------------------------------------
# Fix #3 — gated module-level entry point
# ----------------------------------------------------


def safe_calculate_financial_ratios(
    financial_data: Dict[str, Any],
    required_metrics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Gated module-level entry point for deterministic calculations.

    Verifies every required input through the Calculation Safety Gate
    before delegating to the deterministic engine. Returns the structured
    BLOCKED/ALLOWED result — never a numeric result for blocked input.
    """
    return FinancialCalculator().safe_calculate(financial_data, required_metrics)
