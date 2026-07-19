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

from typing import Dict, Any


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


# ----------------------------------------------------


def calculate_financial_ratios(financial_data):

    calculator = FinancialCalculator()

    return calculator.calculate(financial_data)
