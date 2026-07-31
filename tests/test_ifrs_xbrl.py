"""
Financial Timeline Engine
Fix #1 - IFRS XBRL Support - Test Suite

Proves:
 1. US-GAAP XBRL concepts still resolve to canonical metrics (no regression)
 2. IFRS (ifrs-full) concepts resolve to the same canonical metrics
 3. Unknown IFRS concepts are PRESERVED as structured facts (never discarded)
 4. Semantically different concepts (GAAP vs IFRS, or distinct IFRS tags)
    are never merged, even when values/periods match
 5. Taxonomy, original concept tag, definition, basis and confidence survive

Run: python3 tests/test_ifrs_xbrl.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.ERROR)

from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState,
    EvidenceItem,
)


def _fact(concept, value, unit="INR", period_end="2025-03-31", fiscal_year=2025,
          fiscal_quarter="FY", decimals=None, scale=None):
    """Build an XbrlFact-shaped dict (same shape XbrlExtractor produces)."""
    return {
        "concept": concept,
        "local_name": concept.split(":")[-1],
        "value": value,
        "raw_text": str(value),
        "decimals": decimals,
        "scale": scale,
        "unit": unit,
        "context_ref": "c1",
        "period_start": "2024-04-01" if period_end else None,
        "period_end": period_end,
        "instant": None,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "duration_type": "duration" if period_end else "instant",
        "filing_type": "20-F",
        "accession_number": "0000000000-25-000000",
        "is_amendment": False,
        "source_location": f"xbrl:{concept}",
    }


class TestIFRSXbrlResolution(unittest.TestCase):

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def _extract(self, facts):
        parsed = {"type": "html", "text": "", "table_data": [], "xbrl_facts": facts}
        return self.v2.extract_document(parsed)

    # ------------------------------------------------------------------
    # 1. US-GAAP concepts still work (no regression)
    # ------------------------------------------------------------------

    def test_us_gaap_still_works(self):
        result = self._extract([_fact("us-gaap:Revenues", 391035000000.0)])
        facts = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertTrue(facts)
        self.assertEqual(facts[0]["metric_id"], "Revenue")
        self.assertEqual(facts[0]["accounting_basis"], "GAAP")
        self.assertEqual(facts[0]["taxonomy"], "us-gaap")
        self.assertEqual(facts[0]["metric_value"], 391035000000.0)

    def test_us_gaap_net_income_and_eps(self):
        result = self._extract([
            _fact("us-gaap:NetIncomeLoss", 93736000000.0),
            _fact("us-gaap:EarningsPerShareDiluted", 6.08, unit="USD/shares"),
        ])
        by_id = {f["metric_id"]: f for f in result["facts"] if f["source_type"] == "XBRL"}
        self.assertEqual(by_id["NetIncome"]["metric_value"], 93736000000.0)
        self.assertEqual(by_id["EPS"]["metric_value"], 6.08)

    def test_us_gaap_unknown_concept_still_dropped(self):
        # Backward-compatible: unmapped US-GAAP concepts are NOT kept
        result = self._extract([_fact("us-gaap:SomeObscureConcept", 42.0)])
        self.assertEqual([f for f in result["facts"] if f["source_type"] == "XBRL"], [])

    # ------------------------------------------------------------------
    # 2. IFRS concepts resolve to canonical metrics
    # ------------------------------------------------------------------

    def test_ifrs_revenue(self):
        result = self._extract([_fact("ifrs-full:Revenue", 297396.0, unit="INR", scale=6)])
        facts = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertTrue(facts)
        self.assertEqual(facts[0]["metric_id"], "Revenue")
        self.assertEqual(facts[0]["accounting_basis"], "IFRS")
        self.assertEqual(facts[0]["taxonomy"], "ifrs-full")
        self.assertEqual(facts[0]["metric_definition"], "XBRL concept ifrs-full:Revenue")
        self.assertEqual(facts[0]["currency_code"], "INR")
        self.assertEqual(facts[0]["fiscal_period"], "FY2025")
        self.assertGreaterEqual(facts[0]["confidence_score"], 0.9)

    def test_ifrs_required_concepts(self):
        cases = [
            ("ifrs-full:Revenue", "Revenue"),
            ("ifrs-full:Equity", "ShareholdersEquity"),
            ("ifrs-full:CashAndCashEquivalents", "CashAndEquivalents"),
            ("ifrs-full:Assets", "TotalAssets"),
            ("ifrs-full:Liabilities", "TotalLiabilities"),
            ("ifrs-full:ProfitLoss", "NetIncome"),
            ("ifrs-full:ProfitLossFromOperatingActivities", "OperatingIncome"),
            ("ifrs-full:CashFlowsFromUsedInOperatingActivities", "OperatingCashFlow"),
            ("ifrs-full:EarningsPerShareBasicAndDiluted", "EPS"),
            ("ifrs-full:BasicEarningsPerShare", "EPS"),
            ("ifrs-full:Inventories", "Inventories"),
            ("ifrs-full:RetainedEarnings", "RetainedEarnings"),
            ("ifrs-full:TradeAndOtherCurrentReceivables", "AccountsReceivable"),
            ("ifrs-full:TotalBorrowings", "TotalDebt"),
            ("ifrs-full:EquityAttributableToOwnersOfParent", "ShareholdersEquity"),
        ]
        for concept, expected in cases:
            result = self._extract([_fact(concept, 100.0)])
            xbrl = [f for f in result["facts"] if f["source_type"] == "XBRL"]
            self.assertEqual(
                [f["metric_id"] for f in xbrl], [expected],
                msg=f"{concept} should map to {expected}, got {xbrl}",
            )
            for f in xbrl:
                self.assertEqual(f["accounting_basis"], "IFRS", msg=concept)
                self.assertEqual(f["taxonomy"], "ifrs-full", msg=concept)

    # ------------------------------------------------------------------
    # 3. Unknown IFRS concepts preserved (never silently discarded)
    # ------------------------------------------------------------------

    def test_unknown_ifrs_concept_preserved(self):
        concept = "ifrs-full:ProportionOfOwnershipInterestInSubsidiary"
        result = self._extract([_fact(concept, 76.69, unit="pure", period_end=None)])
        xbrl = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertEqual(len(xbrl), 1, "unknown IFRS concept must be preserved")
        f = xbrl[0]
        self.assertEqual(f["metric_id"], "ProportionOfOwnershipInterestInSubsidiary")
        self.assertEqual(f["metric_definition"], f"XBRL concept {concept}")
        self.assertEqual(f["taxonomy"], "ifrs-full")
        self.assertEqual(f["accounting_basis"], "IFRS")
        self.assertEqual(f["metric_value"], 76.69)

    def test_multiple_unknown_ifrs_concepts_all_preserved(self):
        facts = [
            _fact("ifrs-full:ProportionOfOwnershipInterestInSubsidiary", 76.69, unit="pure", period_end=None),
            _fact("ifrs-full:NameOfSubsidiary", 1.0, unit="pure", period_end=None),
        ]
        result = self._extract(facts)
        xbrl = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertEqual(len(xbrl), 2)

    # ------------------------------------------------------------------
    # 4. Semantically different concepts are never merged
    # ------------------------------------------------------------------

    def test_gaap_vs_ifrs_not_merged(self):
        # Same value, same period, same unit: still TWO distinct facts
        result = self._extract([
            _fact("us-gaap:Revenues", 391035000000.0),
            _fact("ifrs-full:Revenue", 391035000000.0),
        ])
        xbrl = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertEqual(len(xbrl), 2)
        hashes = {f["evidence_hash"] for f in xbrl}
        self.assertEqual(len(hashes), 2, "GAAP and IFRS facts must not collide")
        definitions = {f["metric_definition"] for f in xbrl}
        self.assertIn("XBRL concept us-gaap:Revenues", definitions)
        self.assertIn("XBRL concept ifrs-full:Revenue", definitions)
        basis = {f["accounting_basis"] for f in xbrl}
        self.assertEqual(basis, {"GAAP", "IFRS"})

    def test_distinct_ifrs_concepts_not_merged(self):
        # ProfitLoss vs ProfitLossFromContinuingOperations are distinct facts
        result = self._extract([
            _fact("ifrs-full:ProfitLoss", 100.0),
            _fact("ifrs-full:ProfitLossFromContinuingOperations", 100.0),
        ])
        xbrl = [f for f in result["facts"] if f["source_type"] == "XBRL"]
        self.assertEqual(len(xbrl), 2)
        self.assertEqual(
            {f["metric_definition"] for f in xbrl},
            {
                "XBRL concept ifrs-full:ProfitLoss",
                "XBRL concept ifrs-full:ProfitLossFromContinuingOperations",
            },
        )

    # ------------------------------------------------------------------
    # 5. Downstream compatibility (EvidenceItem / Agentic RAG)
    # ------------------------------------------------------------------

    def test_ifrs_fact_maps_to_evidence_item_and_dedups(self):
        state = EvidenceSummaryState(max_iterations=3)
        fact = self._extract([_fact("ifrs-full:Revenue", 297396.0, scale=6)])["facts"][0]
        item = EvidenceItem(**FinancialExtractorV2.to_evidence_item_dict(fact))
        self.assertEqual(item.metric, "Revenue")
        self.assertEqual(item.accounting_basis, "IFRS")
        self.assertEqual(item.currency_code, "INR")
        self.assertTrue(item.evidence_hash)

        # duplicate add is suppressed
        self.assertTrue(state.add_evidence(item))
        self.assertFalse(state.add_evidence(item))
        self.assertEqual(state.state.evidence_count, 1)


def run_all():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_all()
    print(f"\n{'=' * 60}")
    print(f"IFRS XBRL TESTS: {result.testsRun} run, "
          f"{len(result.failures)} failures, {len(result.errors)} errors")
    sys.exit(0 if result.wasSuccessful() else 1)
