"""
Financial Timeline Engine
Fix #2 - Scale Propagation - Test Suite

Proves scale metadata survives end-to-end:

  XBRL/Table/Text Extraction
    → FinancialFact
    → EvidenceItem
    → EvidenceSummaryState
    → CanonicalEvidenceSet
    → Calculation Engine

Supported scales: thousand, million, billion, lakh, crore, unit,
percentage, per-share. Scale is never inferred from magnitude — it
always comes from explicit metadata (scale word, XBRL scale, unit).

Tests A-L:
  A. Unit tests for every supported scale
  B. Million <-> billion equivalence
  C. Lakh <-> crore equivalence
  D. Mixed-scale table extraction
  E. XBRL scale/unit handling
  F. Scale preserved through EvidenceItem conversion
  G. Scale preserved through CanonicalEvidenceSet
  H. Calculation receives normalized values
  I. Original value + original scale available for auditing
  J. No accidental 1,000x or 100x transformations
  K. Existing US-GAAP tests remain green (run separately)
  L. Existing IFRS tests remain green (run separately)

Run: python3 tests/test_scale_propagation.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.ERROR)

from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.extraction2.xbrl_extractor import XbrlFact
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState,
    EvidenceItem,
)
from backend.intelligence.agentic_rag_orchestrator import CanonicalEvidenceSet
from backend.financial_calculator import FinancialCalculator


def _parsed(text="", table_data=None, xbrl_facts=None, doc_type="txt"):
    return {
        "type": doc_type,
        "text": text,
        "table_data": table_data or [],
        "xbrl_facts": xbrl_facts or [],
    }


class TestScaleUnits(unittest.TestCase):
    """A. Unit tests for every supported scale."""

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def _revenue_text(self, value_str):
        parsed = _parsed(text=f"Revenue was {value_str} in FY2025.")
        result = self.v2.extract_document(parsed)
        return [f for f in result["facts"] if f["metric_id"] == "Revenue"]

    def test_thousand(self):
        facts = self._revenue_text("₹1,250 thousand")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], "thousands")
        self.assertEqual(f["normalized_value"], 1_250_000)
        self.assertEqual(f["metric_value"], 1250.0)

    def test_million(self):
        facts = self._revenue_text("₹1,250 million")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], "millions")
        self.assertEqual(f["normalized_value"], 1_250_000_000)

    def test_billion(self):
        facts = self._revenue_text("₹1.25 billion")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], "billions")
        self.assertEqual(f["normalized_value"], 1_250_000_000)

    def test_lakh(self):
        facts = self._revenue_text("₹1,250 lakh")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], "lakhs")
        self.assertEqual(f["normalized_value"], 125_000_000)

    def test_crore(self):
        facts = self._revenue_text("₹1,250 crore")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], "crores")
        self.assertEqual(f["normalized_value"], 12_500_000_000)

    def test_unit(self):
        # No scale word → unit-scale; normalized == raw (no multiplier)
        facts = self._revenue_text("₹1,250")
        self.assertTrue(facts)
        f = facts[0]
        self.assertEqual(f["scale"], None)
        self.assertEqual(f["normalized_value"], 1250.0)

    def test_percentage(self):
        parsed = _parsed(text="Gross margin was 40% in FY2025.")
        result = self.v2.extract_document(parsed)
        gm = [f for f in result["facts"] if f["metric_id"] == "GrossMargin"]
        self.assertTrue(gm)
        f = gm[0]
        self.assertEqual(f["scale"], "percentage")
        self.assertEqual(f["normalized_value"], 40.0)  # no multiplier

    def test_per_share(self):
        # EPS XBRL with shares unit → per-share scale label
        xf = {
            "concept": "ifrs-full:EarningsPerShareBasicAndDiluted",
            "local_name": "EarningsPerShareBasicAndDiluted",
            "value": 45.6,
            "raw_text": "45.6",
            "unit": "INR/shares",
            "period_end": "2025-03-31",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_type": "20-F",
            "accession_number": "x",
            "is_amendment": False,
        }
        parsed = _parsed(xbrl_facts=[xf], doc_type="html")
        result = self.v2.extract_document(parsed)
        eps = [f for f in result["facts"] if f["metric_id"] == "EPS"]
        self.assertTrue(eps)
        f = eps[0]
        self.assertEqual(f["scale"], "per-share")
        self.assertEqual(f["normalized_value"], 45.6)


class TestScaleEquivalence(unittest.TestCase):
    """B/C. Equivalent magnitudes in different scale notations."""

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def _norm_values(self, text):
        parsed = _parsed(text=text)
        result = self.v2.extract_document(parsed)
        return {f["normalized_value"] for f in result["facts"] if f["metric_id"] == "Revenue"}

    def test_million_billion_equivalence(self):
        # B. 2,900,069 million == 2,900.069 billion == 2,900,069,000,000
        vals = self._norm_values(
            "Revenue was ₹2,900,069 million in FY2025. "
            "Revenue was ₹2,900.069 billion in FY2025."
        )
        self.assertEqual(vals, {2_900_069_000_000.0})

    def test_lakh_crore_equivalence(self):
        # C. 290,006.9 crore == 29,000,690 lakh... but per spec:
        # 12,500 lakh == 1,250,000,000 and 125 crore == 1,250,000,000
        vals = self._norm_values(
            "Revenue was ₹12,500 lakh in FY2025. "
            "Revenue was ₹125 crore in FY2025."
        )
        self.assertEqual(vals, {1_250_000_000.0})

    def test_crore_equivalence_example(self):
        # 290,006.9 crore == 2,900,069,000,000
        vals = self._norm_values("Revenue was ₹290,006.9 crore in FY2025.")
        self.assertEqual(vals, {2_900_069_000_000.0})

    def test_no_1000x_or_100x_transformations(self):
        # J. 12,500 lakh must NOT become 12,500,000,000 (1,000x) or
        # 1,250,000,000,000 (100,000x) — exact: 1,250,000,000
        vals = self._norm_values("Revenue was ₹12,500 lakh in FY2025.")
        self.assertEqual(vals, {1_250_000_000.0})
        # and 1,250 crore → 12,500,000,000 exactly
        vals2 = self._norm_values("Revenue was ₹1,250 crore in FY2025.")
        self.assertEqual(vals2, {12_500_000_000.0})


class TestScaleThroughPipeline(unittest.TestCase):
    """D/E/F/G/H/I — scale survives the full evidence pipeline."""

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def test_mixed_scale_table_extraction(self):
        # D. Table cells carry table-level scale into facts
        parsed = _parsed(
            table_data=[{
                "table_id": "t1",
                "headers": ["Metric", "FY2025", "FY2024"],
                "rows": [
                    {"label": "Revenue", "cells": ["573,000", "512,000"]},
                    {"label": "Net income", "cells": ["30,000", "27,000"]},
                ],
                "column_periods": ["", "FY2025", "FY2024"],
                "scale": "millions",
                "currency": "INR",
                "source_location": "xlsx",
            }],
            doc_type="xlsx",
        )
        result = self.v2.extract_document(parsed)
        rev25 = next(f for f in result["facts"]
                     if f["metric_id"] == "Revenue" and f["fiscal_period"] == "FY2025")
        self.assertEqual(rev25["scale"], "millions")
        self.assertEqual(rev25["normalized_value"], 573_000_000_000)
        self.assertEqual(rev25["metric_value"], 573000.0)  # original preserved

    def test_xbrl_scale_and_unit(self):
        # E. XBRL scale=6 applied and preserved
        xf = {
            "concept": "ifrs-full:Revenue",
            "local_name": "Revenue",
            "value": 391035000000.0,
            "raw_text": "391035",
            "unit": "INR",
            "scale": 6,
            "period_end": "2025-03-31",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_type": "20-F",
            "accession_number": "x",
            "is_amendment": False,
        }
        parsed = _parsed(xbrl_facts=[xf], doc_type="html")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(rev)
        self.assertEqual(rev[0]["scale"], "10^6")
        self.assertEqual(rev[0]["normalized_value"], 391035000000.0)

    def test_scale_survives_evidence_item_conversion(self):
        # F. FinancialFact → EvidenceItem: value is NORMALIZED, original + scale kept
        fact = {
            "metric_id": "Revenue", "metric_name": "Revenue",
            "metric_definition": "Table row: Revenue",
            "metric_value": 3457.0, "raw_value": "3,457", "normalized_value": 3457000000000.0,
            "unit": "", "scale": "crores", "currency_code": "INR", "currency_role": "REPORTING",
            "fiscal_period": "FY2023", "source": "Table", "source_tier": 3,
            "source_type": "TABLE", "evidence_text_anchor": "Revenue | 3,457",
            "confidence_score": 0.9, "verification_status": "PENDING",
            "page": 1, "table_id": "t1", "extraction_method": "table",
        }
        item_dict = FinancialExtractorV2.to_evidence_item_dict(fact)
        self.assertEqual(item_dict["value"], 3457000000000.0)   # normalized
        self.assertEqual(item_dict["original_value"], 3457.0)   # raw preserved
        self.assertEqual(item_dict["scale"], "crores")
        self.assertEqual(item_dict["normalized_value"], 3457000000000.0)
        # EvidenceItem accepts the fields (backward compatible construction)
        item = EvidenceItem(**item_dict)
        self.assertEqual(item.value, 3457000000000.0)
        self.assertEqual(item.scale, "crores")

    def test_scale_survives_state_and_canonical_set(self):
        # G. EvidenceSummaryState → CanonicalEvidenceSet keeps scale
        fact = {
            "metric_id": "Revenue", "metric_name": "Revenue",
            "metric_definition": "Table row: Revenue",
            "metric_value": 3457.0, "normalized_value": 3457000000000.0,
            "unit": "", "scale": "crores", "currency_code": "INR",
            "currency_role": "REPORTING", "fiscal_period": "FY2023",
            "source": "Table", "source_tier": 3, "source_type": "TABLE",
            "evidence_text_anchor": "Revenue | 3,457",
            "confidence_score": 0.9, "verification_status": "PENDING",
        }
        item_dict = FinancialExtractorV2.to_evidence_item_dict(fact)
        item = EvidenceItem(**item_dict)

        state = EvidenceSummaryState(max_iterations=3)
        self.assertTrue(state.add_evidence(item))
        self.assertEqual(state.state.evidence_count, 1)

        canon = CanonicalEvidenceSet(state.state)
        canon.add_resolved(item.to_dict())
        resolved = canon.to_dict()["resolved_facts"][0]
        self.assertEqual(resolved["value"], 3457000000000.0)
        self.assertEqual(resolved["scale"], "crores")
        self.assertEqual(resolved["original_value"], 3457.0)

    def test_calculation_receives_normalized_values(self):
        # H. Calculation engine consumes normalized values
        # ₹345.7 crore revenue + ₹34.57 crore net profit → margin 10%
        data = {
            "Revenue": {"value": 3_457_000_000.0},
            "Net Profit": {"value": 345_700_000.0},
            "Equity": {"value": 1_000_000_000.0},
            "Assets": {"value": 5_000_000_000.0},
            "Liabilities": {"value": 4_000_000_000.0},
            "Debt": {"value": 2_000_000_000.0},
        }
        ratios = FinancialCalculator().calculate(data)
        self.assertAlmostEqual(ratios["Profit Margin"]["value"], 10.0, places=2)
        self.assertAlmostEqual(ratios["Debt to Equity"]["value"], 2.0, places=2)
        self.assertAlmostEqual(ratios["ROE"]["value"], 34.57, places=2)

    def test_original_value_available_for_audit(self):
        # I. Raw representation + scale remain available
        fact = {
            "metric_id": "Revenue", "metric_name": "Revenue",
            "metric_definition": "Table row: Revenue",
            "metric_value": 2_900_069.0, "raw_value": "2,900,069",
            "normalized_value": 2_900_069_000_000.0,
            "unit": "", "scale": "millions", "currency_code": "INR",
            "currency_role": "REPORTING", "fiscal_period": "FY2025",
            "source": "Table", "source_tier": 3, "source_type": "TABLE",
            "evidence_text_anchor": "Revenue | 2,900,069",
            "confidence_score": 0.9, "verification_status": "PENDING",
        }
        item_dict = FinancialExtractorV2.to_evidence_item_dict(fact)
        # audit trail: raw value + raw string + scale all present
        self.assertEqual(item_dict["original_value"], 2_900_069.0)
        self.assertEqual(fact["raw_value"], "2,900,069")
        self.assertEqual(item_dict["scale"], "millions")
        # normalized is the canonical magnitude
        self.assertEqual(item_dict["value"], 2_900_069_000_000.0)


def run_all():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_all()
    print(f"\n{'=' * 60}")
    print(f"SCALE PROPAGATION TESTS: {result.testsRun} run, "
          f"{len(result.failures)} failures, {len(result.errors)} errors")
    sys.exit(0 if result.wasSuccessful() else 1)
