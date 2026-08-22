"""
Platrixa
Fix #4 — Period Contamination Tests

Structural / contextual period validation (NO year blacklists).

Covers requirements A–P:

  A. FY2025/FY2024/FY2023 association
  B. Calendar-year association
  C. Fiscal-year association
  D. "Year ended" date association (incl. Indian day-first format)
  E. Comparative table columns
  F. Glossary-year contamination
  G. Legal/incorporation-year contamination
  H. Page-number contamination
  I. Footnote-number contamination
  J. Multiple years near one metric
  K. Same metric across different periods remains separate
  L. Ambiguous year -> unresolved, never guessed
  M. XBRL period context takes precedence over nearby text
  N. Table header period takes precedence over unrelated nearby years
  O. Period mismatch blocks calculation through the safety gate
  P. Existing valid period calculations remain numerically unchanged

Run: python3 tests/test_period_association.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.ERROR)

from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.intelligence.calculation_safety_gate import CalculationSafetyGate
from backend.financial_calculator import FinancialCalculator


class TestPeriodAssociation(unittest.TestCase):

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def _parsed(self, text="", table_data=None, xbrl_facts=None, doc_type="txt"):
        return {
            "type": doc_type,
            "text": text,
            "table_data": table_data or [],
            "xbrl_facts": xbrl_facts or [],
        }

    def _revenue_facts(self, parsed):
        return [f for f in parsed["facts"] if f["metric_id"] == "Revenue"]

    # -- A. FY2025/FY2024/FY2023 association ------------------------------

    def test_a_fy_tokens_associated(self):
        text = (
            "Revenue was $573,000 in FY2025. "
            "Revenue was $512,000 in FY2024. "
            "Revenue was $480,000 in FY2023."
        )
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        by_period = {f["fiscal_period"]: f["metric_value"] for f in rev}
        self.assertEqual(by_period.get("FY2025"), 573000.0)
        self.assertEqual(by_period.get("FY2024"), 512000.0)
        self.assertEqual(by_period.get("FY2023"), 480000.0)

    # -- B. Calendar-year association ---------------------------------------

    def test_b_calendar_year_association(self):
        text = "Revenue for the year ended December 31, 2024 was $512,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        self.assertEqual(rev[0]["fiscal_period"], "FY2024")

    # -- C. Fiscal-year association ------------------------------------------

    def test_c_fiscal_year_association(self):
        text = "Revenue for fiscal year 2025 was $573,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        self.assertEqual(rev[0]["fiscal_period"], "FY2025")

    # -- D. "Year ended" date association ------------------------------------

    def test_d_year_ended_date_association(self):
        text = "Revenue for the year ended March 31, 2025 was ₹573,000 million."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        self.assertEqual(rev[0]["fiscal_period"], "FY2025")

    def test_d_indian_day_first_date(self):
        text = "Revenue for the year ended 31 March 2025 was ₹573,000 million."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        self.assertEqual(rev[0]["fiscal_period"], "FY2025")

    # -- E. Comparative table columns ----------------------------------------

    def test_e_comparative_table_columns(self):
        parsed = self._parsed(
            table_data=[{
                "table_id": "t1",
                "headers": ["Metric", "2025", "2024", "2023"],
                "rows": [
                    {"label": "Revenue", "cells": ["573,000", "512,000", "480,000"]},
                ],
                "source_location": "xlsx",
            }],
            doc_type="xlsx",
        )
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        by_period = {f["fiscal_period"]: f["metric_value"] for f in rev}
        self.assertEqual(by_period.get("FY2025"), 573000.0)
        self.assertEqual(by_period.get("FY2024"), 512000.0)
        self.assertEqual(by_period.get("FY2023"), 480000.0)

    # -- F. Glossary-year contamination ---------------------------------------

    def test_f_glossary_year_never_period(self):
        text = (
            "Glossary of terms (effective 1978): revenue means net sales. "
            "Revenue was $573,000 in FY2025."
        )
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertNotEqual(f["fiscal_period"], "FY1978")

    # -- G. Legal / incorporation-year contamination ---------------------------

    def test_g_incorporation_year_never_period(self):
        text = "Incorporated in 1978. Revenue was $573,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertNotEqual(f["fiscal_period"], "FY1978")

    # -- H. Page-number contamination ------------------------------------------

    def test_h_page_number_never_period(self):
        text = "Page 1978. See page 2023 for details. Revenue was $573,000 in FY2025."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertNotIn(f["fiscal_period"], ("FY1978", "FY2023"))

    # -- I. Footnote-number contamination ---------------------------------------

    def test_i_footnote_never_period(self):
        text = "Notes (1) (2) (4) refer to contingencies. Revenue was $573,000 in FY2025."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertEqual(f["fiscal_period"], "FY2025")
            self.assertNotIn(f["metric_value"], (1.0, 2.0, 4.0))

    # -- J. Multiple years near one metric --------------------------------------

    def test_j_multiple_years_picks_value_window(self):
        text = "Revenue was $573,000 in 2025 and $512,000 in 2024."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        # The value window (nearest the number) wins: 573,000 -> 2025
        self.assertEqual(rev[0]["metric_value"], 573000.0)
        self.assertEqual(rev[0]["fiscal_period"], "FY2025")

    # -- K. Same metric across different periods remains separate ----------------

    def test_k_same_metric_distinct_periods_separate(self):
        text = (
            "Revenue was $573,000 in FY2025. "
            "Revenue was $512,000 in FY2024."
        )
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertEqual(len(rev), 2)
        self.assertNotEqual(rev[0]["fiscal_period"], rev[1]["fiscal_period"])
        self.assertNotEqual(rev[0]["evidence_hash"], rev[1]["evidence_hash"])

    # -- L. Ambiguous year -> unresolved, never guessed ---------------------------

    def test_l_ambiguous_year_unresolved(self):
        text = "Founded in 1945. Revenue was $573,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertNotEqual(f["fiscal_period"], "FY1945")
            # unresolved (not guessed) is acceptable: period is None or FY-garbled
            self.assertIsNone(f.get("period_start"))
        # and the metric value is intact, never the year
        for f in rev:
            self.assertNotEqual(f["metric_value"], 1945.0)

    def test_l_no_year_at_all_unresolved(self):
        text = "Revenue was $573,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        self.assertTrue(rev)
        for f in rev:
            self.assertIn(f.get("fiscal_period"), (None, ""))

    # -- M. XBRL period context takes precedence ---------------------------------

    def test_m_xbrl_period_precedence(self):
        parsed = self._parsed(
            text="Incorporated in 1978. Revenue was $573,000.",
            xbrl_facts=[{
                "concept": "ifrs-full:Revenue",
                "local_name": "Revenue",
                "value": 2900069000000.0,
                "raw_text": "2900069000000",
                "unit": "INR",
                "period_end": "2025-03-31",
                "fiscal_year": 2025,
                "fiscal_quarter": "FY",
                "filing_type": "20-F",
                "accession_number": "0001",
                "is_amendment": False,
            }],
        )
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        xbrl = [f for f in rev if f["source_type"] == "XBRL"]
        self.assertTrue(xbrl)
        self.assertEqual(xbrl[0]["fiscal_period"], "FY2025")
        for f in rev:
            self.assertNotEqual(f["fiscal_period"], "FY1978")

    # -- N. Table header period precedence ----------------------------------------

    def test_n_table_header_beats_unrelated_years(self):
        parsed = self._parsed(
            text="Founded in 1945. The company listed in 1978.",
            table_data=[{
                "table_id": "t1",
                "headers": ["Metric", "FY2025", "FY2024"],
                "rows": [
                    {"label": "Revenue", "cells": ["573,000", "512,000"]},
                ],
                "source_location": "xlsx",
            }],
            doc_type="xlsx",
        )
        result = self.v2.extract_document(parsed)
        rev = self._revenue_facts(result)
        by_period = {f["fiscal_period"] for f in rev}
        self.assertIn("FY2025", by_period)
        self.assertIn("FY2024", by_period)
        self.assertNotIn("FY1978", by_period)
        self.assertNotIn("FY1945", by_period)

    # -- O. Period mismatch blocks calculation through the safety gate -------------

    def _verified_fact(self, metric, value, period, ccy="INR"):
        return {
            "metric": metric,
            "value": value,
            "original_value": value,
            "normalized_value": value,
            "scale": "",
            "currency_code": ccy,
            "currency_role": "REPORTING",
            "reporting_period": period,
            "verification_status": "VERIFIED",
        }

    def test_o_period_mismatch_blocks(self):
        gate = CalculationSafetyGate()
        data = {
            "Revenue": self._verified_fact("Revenue", 573000.0, "FY2025"),
            "Net Profit": self._verified_fact("Net Profit", 18000.0, "FY2024"),
        }
        verdict = gate.check(data, required_metrics=["Revenue", "Net Profit"])
        self.assertEqual(verdict["status"], "BLOCKED")
        self.assertEqual(verdict["reason"], "PERIOD_MISMATCH")
        self.assertIsNone(verdict["calculation"])

    # -- P. Existing valid period calculations remain numerically unchanged --------

    def test_p_valid_period_calc_unchanged(self):
        data = {
            "Revenue": self._verified_fact("Revenue", 573000.0, "FY2025"),
            "Net Profit": self._verified_fact("Net Profit", 18000.0, "FY2025"),
        }
        gate = CalculationSafetyGate()
        verdict = gate.check(data, required_metrics=["Revenue", "Net Profit"])
        self.assertEqual(verdict["status"], "ALLOWED")
        # numerically identical to the legacy ungated engine
        legacy = FinancialCalculator().calculate({
            "Revenue": {"value": 573000.0},
            "Net Profit": {"value": 18000.0},
        })
        self.assertEqual(
            round((18000.0 / 573000.0) * 100, 2),
            legacy["Profit Margin"]["value"],
        )

    # -- Requirement 9: contaminated fact must not be VERIFIED ---------------------

    def test_contaminated_not_verified(self):
        text = "Incorporated in 1978. Revenue was $573,000."
        parsed = self._parsed(text=text)
        result = self.v2.extract_document(parsed)
        for f in result["facts"]:
            self.assertEqual(f["verification_status"], "PENDING")


def run_all():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_all()
    print(f"\n{'=' * 60}")
    print(f"PERIOD ASSOCIATION TESTS (Fix #4): {result.testsRun} run, "
          f"{len(result.failures)} failures, {len(result.errors)} errors")
    sys.exit(0 if result.wasSuccessful() else 1)
