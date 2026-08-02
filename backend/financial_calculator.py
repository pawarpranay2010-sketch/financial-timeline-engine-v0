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

import re
from typing import Any, Dict, List, Optional

from backend.intelligence.calculation_safety_gate import CalculationSafetyGate

# Input metric keys required for the Current Ratio
CURRENT_RATIO_INPUTS = ("Current Assets", "Current Liabilities")

# Input metric keys required for CAGR (beginning / ending value facts)
CAGR_BEGIN_KEY = "CAGR Beginning Value"
CAGR_END_KEY = "CAGR Ending Value"


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
        # ----------------------------------------

        current_assets = self._value(financial_data, "Current Assets")

        current_liabilities = self._value(financial_data, "Current Liabilities")

        if (current_assets is not None
                and current_liabilities is not None
                and current_liabilities > 0
                and current_assets >= 0):

            ratios["Current Ratio"] = {

                "value": round(current_assets / current_liabilities, 2),

                "source": "Calculated"

            }

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
        if required_metrics is None:
            required_metrics = gate._infer_required_metrics(financial_data)
        required = list(dict.fromkeys(required_metrics))
        # Current Ratio: if any input is present, both are required so a
        # lone input blocks with MISSING instead of silently computing nothing.
        present = set(financial_data.keys())
        if present & set(CURRENT_RATIO_INPUTS):
            for m in CURRENT_RATIO_INPUTS:
                if m not in required:
                    required.append(m)
        verdict = gate.check(financial_data, required)
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


    # ------------------------------------------------------------
    # CAGR
    # ------------------------------------------------------------

    @staticmethod
    def _period_year(period: Any) -> Optional[int]:
        """Extract a 4-digit year from a reporting-period value.
        Never guesses — returns None when the year cannot be parsed.
        Supported shapes: "FY2022", "2022", "2022-09-24", datetime/date.
        """
        if period is None:
            return None
        if hasattr(period, "year"):
            return int(period.year)
        m = re.search(r"(19|20)\d{2}", str(period))
        return int(m.group(0)) if m else None

    def calculate_cagr(self, financial_data: Dict[str, Any]) -> Dict[str, Any]:
        """CAGR = (Ending / Beginning) ** (1/n) - 1.

        Uses ONLY the beginning and ending values present in
        financial_data, and derives n from the reporting-period
        metadata of the two facts. Never guesses n; returns {} when
        any input is missing, zero/negative, or periods are unusable.
        """
        begin = financial_data.get(CAGR_BEGIN_KEY)
        end = financial_data.get(CAGR_END_KEY)
        if not begin or not end:
            return {}
        bv, ev = begin.get("value"), end.get("value")
        n = self._cagr_years(begin, end)
        if bv is None or ev is None or bv <= 0 or ev <= 0 or n is None or n < 1:
            return {}
        cagr = (ev / bv) ** (1.0 / n) - 1.0
        return {
            "CAGR": {
                "value": round(cagr, 4),
                "source": "Calculated",
                "years": n,
                "beginning_period": begin.get("reporting_period", ""),
                "ending_period": end.get("reporting_period", ""),
            }
        }

    def _cagr_years(self, begin: Dict[str, Any], end: Dict[str, Any]) -> Optional[int]:
        """n = end_year - begin_year from period metadata. None if unusable."""
        by = self._period_year(begin.get("reporting_period"))
        ey = self._period_year(end.get("reporting_period"))
        if by is None or ey is None:
            return None
        n = ey - by
        return n if n >= 1 else None

    def safe_calculate_cagr(self, financial_data: Dict[str, Any]) -> Dict[str, Any]:
        """Gated CAGR. Each input fact passes through the existing
        CalculationSafetyGate (VERIFIED-only, currency/scale sanity); the
        CAGR-specific period math is handled separately because a CAGR
        inherently spans two different reporting periods.

        Returns the same structured BLOCKED / ALLOWED shape used by the
        other gated calculations.
        """
        gate = CalculationSafetyGate()
        required = [CAGR_BEGIN_KEY, CAGR_END_KEY]

        missing = [m for m in required if m not in financial_data]
        if missing:
            return {
                "status": "BLOCKED",
                "reason": "MISSING",
                "required_facts": required,
                "rejected_facts": [{"metric": m, "status": "MISSING"} for m in missing],
                "calculation": None,
            }

        # Per-input gate validation (VERIFIED, currency, scale sanity)
        for m in required:
            verdict = gate.check({m: financial_data[m]}, [m])
            if verdict["status"] == "BLOCKED":
                verdict["required_facts"] = required
                return verdict

        begin = financial_data[CAGR_BEGIN_KEY]
        end = financial_data[CAGR_END_KEY]
        bv, ev = begin.get("value"), end.get("value")

        if bv is None or ev is None:
            return {
                "status": "BLOCKED",
                "reason": "REJECTED",
                "required_facts": required,
                "rejected_facts": [{"metric": m, "status": "REJECTED", "reason": "MISSING_VALUE"} for m in required],
                "calculation": None,
            }
        if bv <= 0:
            return {
                "status": "BLOCKED",
                "reason": "INVALID_INPUT",
                "required_facts": required,
                "rejected_facts": [{"metric": CAGR_BEGIN_KEY, "status": "INVALID_INPUT",
                                     "reason": "BEGINNING_VALUE_NOT_POSITIVE", "value": bv}],
                "calculation": None,
            }
        if ev <= 0:
            return {
                "status": "BLOCKED",
                "reason": "INVALID_INPUT",
                "required_facts": required,
                "rejected_facts": [{"metric": CAGR_END_KEY, "status": "INVALID_INPUT",
                                     "reason": "ENDING_VALUE_NOT_POSITIVE", "value": ev}],
                "calculation": None,
            }

        # Currency compatibility (both facts must share currency)
        bc = (begin.get("currency_code") or "").upper()
        ec = (end.get("currency_code") or "").upper()
        if bc and ec and bc != ec:
            return {
                "status": "BLOCKED",
                "reason": "CURRENCY_MISMATCH",
                "required_facts": required,
                "rejected_facts": [{"metric": m, "status": "CURRENCY_MISMATCH"} for m in required],
                "calculation": None,
            }

        # Scale consistency (both facts must carry the same scale)
        bs = begin.get("scale") or ""
        es = end.get("scale") or ""
        if bs != es:
            return {
                "status": "BLOCKED",
                "reason": "SCALE_MISMATCH",
                "required_facts": required,
                "rejected_facts": [
                    {"metric": m, "status": "SCALE_MISMATCH",
                     "scale": (begin if m == CAGR_BEGIN_KEY else end).get("scale", "")}
                    for m in required
                ],
                "calculation": None,
            }

        # Period association (n from metadata; end must be after begin)
        n = self._cagr_years(begin, end)
        if n is None or n < 1:
            return {
                "status": "BLOCKED",
                "reason": "PERIOD_MISMATCH",
                "required_facts": required,
                "rejected_facts": [
                    {"metric": m, "status": "PERIOD_MISMATCH",
                     "period": financial_data[m].get("reporting_period", "")}
                    for m in required
                ],
                "calculation": None,
            }

        cagr = (ev / bv) ** (1.0 / n) - 1.0
        return {
            "status": "ALLOWED",
            "reason": "",
            "required_facts": required,
            "rejected_facts": [],
            "calculation": {
                "CAGR": {
                    "value": round(cagr, 4),
                    "source": "Calculated",
                    "years": n,
                    "beginning_period": begin.get("reporting_period", ""),
                    "ending_period": end.get("reporting_period", ""),
                    "scale": bs,
                }
            },
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

def calculate_cagr_ratios(financial_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ungated module-level entry point for CAGR (beginning/ending values).
    Returns {} when inputs are missing/invalid — never a fabricated value."""
    return FinancialCalculator().calculate_cagr(financial_data)

def safe_calculate_cagr_ratios(financial_data: Dict[str, Any]) -> Dict[str, Any]:
    """Gated module-level entry point for CAGR (beginning/ending values)."""
    return FinancialCalculator().safe_calculate_cagr(financial_data)
