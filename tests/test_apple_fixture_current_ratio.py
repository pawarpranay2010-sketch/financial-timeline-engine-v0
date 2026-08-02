"""
Financial Timeline Engine
Real Apple FY2024 10-K — Current Ratio Fixture Pipeline Test

Runs the REAL Apple 10-K (Inline XBRL HTML fixture) through the actual
application ingestion pipeline (ingestion.extract_document →
FinancialExtractorV2) and verifies:

  1. Current Assets (us-gaap:AssetsCurrent → CurrentAssets) are extracted
     with value, period, currency and scale metadata preserved.
  2. Current Liabilities (us-gaap:LiabilitiesCurrent → CurrentLiabilities)
     are extracted the same way.
  3. The FY2024 facts map into the gated calculator and produce the
     correct Current Ratio through CalculationSafetyGate.
  4. Missing/partial inputs still BLOCK rather than fabricating.

Run: python3 -m pytest tests/test_apple_fixture_current_ratio.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest

logging.getLogger("fte").setLevel(logging.CRITICAL)

from ingestion.extraction import extract_document
from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.financial_calculator import safe_calculate_financial_ratios

DOC_PATH = os.path.join(os.path.dirname(__file__), "test_data", "apple_10k_2024.html")


class _FileLike:
    """Minimal file-like wrapper (same as the project's own e2e scripts)."""

    def __init__(self, path):
        self.name = os.path.basename(path)
        with open(path, "rb") as f:
            self._data = f.read()
        self._pos = 0

    def read(self, *args):
        if args:
            n = args[0]
            out = self._data[self._pos:self._pos + n]
            self._pos += len(out)
            return out
        out = self._data[self._pos:]
        self._pos = len(self._data)
        return out

    def seek(self, offset, whence=0):
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = len(self._data) + offset
        return self._pos


@unittest.skipUnless(os.path.exists(DOC_PATH),
                     "Apple 10-K fixture missing — run scripts/download_sec_filing.py")
class TestAppleFixtureCurrentRatio(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = extract_document(_FileLike(DOC_PATH))
        cls.facts = cls.result["financial_facts"]
        cls.ca = [f for f in cls.facts
                  if f["metric_id"] == "CurrentAssets" and f["source_type"] == "XBRL"]
        cls.cl = [f for f in cls.facts
                  if f["metric_id"] == "CurrentLiabilities" and f["source_type"] == "XBRL"]

    # ------------------------------------------------------------------
    # 1. Current Assets extracted with metadata
    # ------------------------------------------------------------------

    def test_current_assets_extracted_from_xbrl(self):
        self.assertTrue(self.ca, "Expected CurrentAssets XBRL facts")

    def test_current_assets_fy2024_value_metadata(self):
        by_period = {f["fiscal_period"]: f for f in self.ca}
        self.assertIn("FY2024", by_period)
        fact = by_period["FY2024"]
        # Apple FY2024 current assets ≈ 152,987 million USD (scale 10^6)
        self.assertAlmostEqual(fact["normalized_value"], 152_987_000_000.0,
                               delta=5_000_000_000.0)
        self.assertEqual(fact["currency_code"], "USD")
        self.assertEqual(fact["scale"], "10^6")
        self.assertIn("AssetsCurrent", fact["metric_definition"])

    # ------------------------------------------------------------------
    # 2. Current Liabilities extracted with metadata
    # ------------------------------------------------------------------

    def test_current_liabilities_extracted_from_xbrl(self):
        self.assertTrue(self.cl, "Expected CurrentLiabilities XBRL facts")

    def test_current_liabilities_fy2024_value_metadata(self):
        by_period = {f["fiscal_period"]: f for f in self.cl}
        self.assertIn("FY2024", by_period)
        fact = by_period["FY2024"]
        # Apple FY2024 current liabilities ≈ 176,392 million USD (scale 10^6)
        self.assertAlmostEqual(fact["normalized_value"], 176_392_000_000.0,
                               delta=5_000_000_000.0)
        self.assertEqual(fact["currency_code"], "USD")
        self.assertEqual(fact["scale"], "10^6")
        self.assertIn("LiabilitiesCurrent", fact["metric_definition"])

    # ------------------------------------------------------------------
    # 3. Gated Current Ratio from the real FY2024 facts
    # ------------------------------------------------------------------

    def _evidence_fact(self, extracted):
        """Convert an ExtractedFact-shaped dict onto the EvidenceItem shape
        the CalculationSafetyGate expects, marking it VERIFIED (as the
        Agentic RAG orchestrator does before canonical admission)."""
        item = FinancialExtractorV2.to_evidence_item_dict(extracted)
        item["verification_status"] = "VERIFIED"
        return item

    def test_current_ratio_fy2024_gated(self):
        ca24 = next(f for f in self.ca if f["fiscal_period"] == "FY2024")
        cl24 = next(f for f in self.cl if f["fiscal_period"] == "FY2024")

        financial_data = {
            "Current Assets": self._evidence_fact(ca24),
            "Current Liabilities": self._evidence_fact(cl24),
        }
        result = safe_calculate_financial_ratios(financial_data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertIn("Current Ratio", result["calculation"])
        ratio = result["calculation"]["Current Ratio"]["value"]
        expected = round(152_987_000_000.0 / 176_392_000_000.0, 2)
        self.assertEqual(ratio, expected)

    def test_missing_liability_still_blocks(self):
        ca24 = next(f for f in self.ca if f["fiscal_period"] == "FY2024")
        financial_data = {"Current Assets": self._evidence_fact(ca24)}
        result = safe_calculate_financial_ratios(financial_data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])

    def test_fy2025_current_ratio_also_computes(self):
        ca25 = next((f for f in self.ca if f["fiscal_period"] == "FY2025"), None)
        cl25 = next((f for f in self.cl if f["fiscal_period"] == "FY2025"), None)
        if not ca25 or not cl25:
            self.skipTest("FY2025 current asset/liability facts not present")
        financial_data = {
            "Current Assets": self._evidence_fact(ca25),
            "Current Liabilities": self._evidence_fact(cl25),
        }
        result = safe_calculate_financial_ratios(financial_data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertIn("Current Ratio", result["calculation"])


if __name__ == "__main__":
    unittest.main()
