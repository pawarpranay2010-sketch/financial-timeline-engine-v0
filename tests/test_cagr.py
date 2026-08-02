"""
Financial Timeline Engine
CAGR — Regression Test Suite

CAGR = (Ending Value / Beginning Value) ** (1/n) - 1

Covers requirements a–g:
  a. valid CAGR → ALLOWED with numeric calculation + period metadata
  b. missing beginning value → BLOCKED(MISSING)
  c. missing ending value → BLOCKED(MISSING)
  d. invalid / zero starting value → BLOCKED(INVALID_INPUT)
  e. incorrect period association → BLOCKED(PERIOD_MISMATCH)
  f. scale mismatch → BLOCKED(SCALE_MISMATCH)
  g. successful verified calculation → ALLOWED, correct value

All inputs flow through the existing CalculationSafetyGate; the number of
years n is derived from reporting-period metadata — never guessed.

Run: python3 -m pytest tests/test_cagr.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest

logging.getLogger("fte").setLevel(logging.CRITICAL)

from backend.financial_calculator import (
    FinancialCalculator,
    CAGR_BEGIN_KEY,
    CAGR_END_KEY,
    calculate_cagr_ratios,
    safe_calculate_cagr_ratios,
)


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


class TestCagrValid(unittest.TestCase):
    """a/g. Valid CAGR → ALLOWED with the mathematically correct value."""

    def test_valid_cagr_two_years(self):
        # 100 -> 121 over 2 years: (121/100)^(1/2) - 1 = 0.10
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["rejected_facts"], [])
        calc = result["calculation"]["CAGR"]
        self.assertAlmostEqual(calc["value"], 0.10, places=4)
        self.assertEqual(calc["source"], "Calculated")
        self.assertEqual(calc["years"], 2)
        self.assertEqual(calc["beginning_period"], "FY2022")
        self.assertEqual(calc["ending_period"], "FY2024")

    def test_valid_cagr_one_year(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2023"),
            CAGR_END_KEY: _fact(110.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertAlmostEqual(result["calculation"]["CAGR"]["value"], 0.10, places=4)
        self.assertEqual(result["calculation"]["CAGR"]["years"], 1)

    def test_valid_cagr_ungated_helper(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        calc = calculate_cagr_ratios(data)
        self.assertAlmostEqual(calc["CAGR"]["value"], 0.10, places=4)
        self.assertEqual(calc["CAGR"]["years"], 2)

    def test_valid_cagr_scale_metadata_preserved(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100000000.0, period="FY2022", scale="millions",
                                  original_value=100.0, normalized_value=100000000.0),
            CAGR_END_KEY: _fact(121000000.0, period="FY2024", scale="millions",
                                original_value=121.0, normalized_value=121000000.0),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertAlmostEqual(result["calculation"]["CAGR"]["value"], 0.10, places=4)
        self.assertEqual(result["calculation"]["CAGR"]["scale"], "millions")


class TestCagrMissing(unittest.TestCase):
    """b/c. Missing inputs → BLOCKED(MISSING), calculation=None."""

    def test_missing_beginning_value(self):
        data = {
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])
        missing = {r["metric"] for r in result["rejected_facts"]}
        self.assertIn(CAGR_BEGIN_KEY, missing)

    def test_missing_ending_value(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])
        missing = {r["metric"] for r in result["rejected_facts"]}
        self.assertIn(CAGR_END_KEY, missing)

    def test_empty_input(self):
        result = safe_calculate_cagr_ratios({})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])


class TestCagrInvalidInput(unittest.TestCase):
    """d. Invalid / zero starting value → BLOCKED(INVALID_INPUT)."""

    def test_zero_beginning_value_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(0.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_INPUT")
        self.assertIsNone(result["calculation"])
        self.assertEqual(result["rejected_facts"][0]["metric"], CAGR_BEGIN_KEY)

    def test_negative_beginning_value_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(-100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_INPUT")
        self.assertIsNone(result["calculation"])

    def test_negative_ending_value_blocked(self):
        # Negative ending value must BLOCK, never produce a complex/NaN.
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(-121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_INPUT")
        self.assertIsNone(result["calculation"])

    def test_zero_ending_value_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(0.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_INPUT")
        self.assertIsNone(result["calculation"])


class TestCagrPeriodAssociation(unittest.TestCase):
    """e. Incorrect period association → BLOCKED(PERIOD_MISMATCH)."""

    def test_ending_before_beginning_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2024"),
            CAGR_END_KEY: _fact(121.0, period="FY2022"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PERIOD_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_same_period_blocked(self):
        # n must be >= 1; same-year inputs cannot form a CAGR.
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2024"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PERIOD_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_unparseable_period_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="TBD"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PERIOD_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_period_year_extraction_from_dates(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="2022-09-24"),
            CAGR_END_KEY: _fact(121.0, period="2024-09-28"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["CAGR"]["years"], 2)


class TestCagrScaleMismatch(unittest.TestCase):
    """f. Scale mismatch → BLOCKED(SCALE_MISMATCH)."""

    def test_scale_mismatch_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022", scale="millions",
                                  original_value=100.0, normalized_value=100000000.0),
            CAGR_END_KEY: _fact(121000000.0, period="FY2024", scale="billions",
                                original_value=121.0, normalized_value=121000000000.0),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SCALE_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_scale_mismatch_beginning_flagged(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022", scale="millions",
                                  original_value=100.0, normalized_value=100000000.0),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SCALE_MISMATCH")
        self.assertIsNone(result["calculation"])


class TestCagrVerificationGating(unittest.TestCase):
    """g. Only VERIFIED inputs may compute; unverified inputs block."""

    def test_pending_beginning_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022", status="PENDING"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])

    def test_rejected_ending_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024", status="REJECTED"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])

    def test_currency_mismatch_blocked(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022", currency="USD"),
            CAGR_END_KEY: _fact(121.0, period="FY2024", currency="EUR"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_verified_always_returns_same_structure(self):
        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = FinancialCalculator().safe_calculate_cagr(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertIn("CAGR", result["calculation"])
        self.assertEqual(result["required_facts"], [CAGR_BEGIN_KEY, CAGR_END_KEY])


if __name__ == "__main__":
    unittest.main()
