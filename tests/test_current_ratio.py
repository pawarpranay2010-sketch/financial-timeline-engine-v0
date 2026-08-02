"""
Financial Timeline Engine
Current Ratio — Regression Test Suite

Current Ratio = Current Assets / Current Liabilities

Covers requirements a–e:
  a. valid Current Ratio → ALLOWED with numeric calculation
  b. missing Current Assets → BLOCKED(MISSING), calculation=None
  c. missing Current Liabilities → BLOCKED(MISSING), calculation=None
  d. incorrect/unverified inputs are blocked (PENDING / REJECTED / CONFLICTING)
  e. scale / period handling (normalized scale allowed, scale mismatch blocked,
     period mismatch blocked, zero-liabilities never fabricated)

All inputs flow through the existing CalculationSafetyGate; only VERIFIED
canonical evidence may enter the deterministic calculation.

Run: python3 -m pytest tests/test_current_ratio.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest

logging.getLogger("fte").setLevel(logging.CRITICAL)

from backend.financial_calculator import (
    FinancialCalculator,
    calculate_financial_ratios,
    safe_calculate_financial_ratios,
)
from backend.intelligence.calculation_safety_gate import CalculationSafetyGate


def _fact(value, status="VERIFIED", currency="USD", currency_role="REPORTING",
          period="FY2024", scale="", original_value=None, normalized_value=None):
    f = {
        "value": value,
        "verification_status": status,
        "currency_code": currency,
        "currency_role": currency_role,
        "reporting_period": period,
    }
    if scale:
        f["scale"] = scale
    if original_value is not None:
        f["original_value"] = original_value
    if normalized_value is not None:
        f["normalized_value"] = normalized_value
    return f


class TestCurrentRatioValid(unittest.TestCase):
    """a. Valid Current Ratio."""

    def test_valid_current_ratio_ungated(self):
        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(250.0),
        }
        ratios = calculate_financial_ratios(data)
        self.assertIn("Current Ratio", ratios)
        self.assertEqual(ratios["Current Ratio"]["value"], 2.0)
        self.assertEqual(ratios["Current Ratio"]["source"], "Calculated")

    def test_valid_current_ratio_gated(self):
        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertIn("Current Ratio", result["calculation"])
        self.assertEqual(result["calculation"]["Current Ratio"]["value"], 2.0)

    def test_valid_current_ratio_decimal(self):
        data = {
            "Current Assets": _fact(300.0),
            "Current Liabilities": _fact(800.0),
        }
        ratios = calculate_financial_ratios(data)
        self.assertEqual(ratios["Current Ratio"]["value"], 0.38)


class TestCurrentRatioMissing(unittest.TestCase):
    """b/c. Missing inputs → BLOCKED(MISSING), calculation=None."""

    def test_missing_current_assets(self):
        data = {
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])
        missing = {r["metric"] for r in result["rejected_facts"]}
        self.assertIn("Current Assets", missing)

    def test_missing_current_liabilities(self):
        data = {
            "Current Assets": _fact(500.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])
        missing = {r["metric"] for r in result["rejected_facts"]}
        self.assertIn("Current Liabilities", missing)

    def test_lone_current_assets_blocks_entire_calculation(self):
        # A lone Current Assets fact must NOT silently compute nothing —
        # both inputs become required so the missing partner blocks.
        data = {
            "Revenue": _fact(1000.0),
            "Net Profit": _fact(200.0),
            "Current Assets": _fact(500.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])


class TestCurrentRatioUnverifiedBlocked(unittest.TestCase):
    """d. Unverified inputs are blocked."""

    def test_pending_current_assets_blocked(self):
        data = {
            "Current Assets": _fact(500.0, status="PENDING"),
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])

    def test_rejected_current_liabilities_blocked(self):
        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(250.0, status="REJECTED"),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])

    def test_conflicting_input_blocked(self):
        data = {
            "Current Assets": _fact(500.0, status="CONFLICTING"),
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])

    def test_gate_check_rejects_pending_directly(self):
        gate = CalculationSafetyGate()
        data = {
            "Current Assets": _fact(500.0, status="PENDING"),
            "Current Liabilities": _fact(250.0),
        }
        verdict = gate.check(data, ["Current Assets", "Current Liabilities"])
        self.assertEqual(verdict["status"], "BLOCKED")
        self.assertEqual(verdict["reason"], "PENDING")


class TestCurrentRatioScalePeriod(unittest.TestCase):
    """e. Scale / period handling."""

    def test_scale_normalized_allowed(self):
        # value == normalized_value with explicit scale metadata → allowed
        data = {
            "Current Assets": _fact(500000000.0, scale="millions",
                                    original_value=500.0, normalized_value=500000000.0),
            "Current Liabilities": _fact(250000000.0, scale="millions",
                                         original_value=250.0, normalized_value=250000000.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["Current Ratio"]["value"], 2.0)

    def test_scale_mismatch_blocked(self):
        # value not normalized (value != normalized_value) with scale present
        data = {
            "Current Assets": _fact(500.0, scale="millions",
                                    original_value=500.0, normalized_value=500000000.0),
            "Current Liabilities": _fact(250000000.0, scale="millions",
                                         original_value=250.0, normalized_value=250000000.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SCALE_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_period_mismatch_blocked(self):
        data = {
            "Current Assets": _fact(500.0, period="FY2024"),
            "Current Liabilities": _fact(250.0, period="FY2023"),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PERIOD_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_currency_mismatch_blocked(self):
        data = {
            "Current Assets": _fact(500.0, currency="EUR"),
            "Current Liabilities": _fact(250.0, currency="USD"),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_zero_liabilities_never_fabricated(self):
        # Division by zero is never fabricated into a ratio.
        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(0.0),
        }
        ratios = calculate_financial_ratios(data)
        self.assertNotIn("Current Ratio", ratios)

    def test_negative_liabilities_never_used(self):
        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(-250.0),
        }
        ratios = calculate_financial_ratios(data)
        self.assertNotIn("Current Ratio", ratios)


class TestCurrentRatioFormatConsistency(unittest.TestCase):
    """Returned format matches ROE / ROA / D/E / Profit Margin."""

    def test_same_structured_format_as_other_ratios(self):
        data = {
            "Revenue": _fact(1000.0),
            "Net Profit": _fact(200.0),
            "Equity": _fact(800.0),
            "Assets": _fact(2000.0),
            "Debt": _fact(400.0),
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        calc = result["calculation"]
        for key in ("Profit Margin", "ROE", "ROA", "Debt to Equity", "Current Ratio"):
            self.assertIn(key, calc)
            self.assertEqual(calc[key]["source"], "Calculated")
            self.assertIn("value", calc[key])
        self.assertEqual(calc["Current Ratio"]["value"], 2.0)

    def test_financial_calculator_instance_api(self):
        data = {
            "Current Assets": _fact(600.0),
            "Current Liabilities": _fact(200.0),
        }
        result = FinancialCalculator().safe_calculate(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["Current Ratio"]["value"], 3.0)


if __name__ == "__main__":
    unittest.main()
