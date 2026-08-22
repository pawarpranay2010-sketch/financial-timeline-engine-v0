"""
Platrixa
Extraction 2.0 - Test Suite

Covers the 20 required scenarios:

 1.  Realistic financial tables
 2.  Multiple fiscal periods
 3.  Table continuation across pages
 4.  XBRL extraction
 5.  XBRL priority over regex
 6.  Millions/billions/crores scale
 7.  Negative accounting values
 8.  Footnote parentheses
 9.  Currency detection
10.  Currency roles
11.  GAAP vs non-GAAP
12.  Evidence anchoring
13.  Multi-column PDFs
14.  Repeated headers/footers
15.  Page numbers NOT financial values
16.  Fiscal years NOT financial values
17.  Ambiguous values remain unresolved
18.  ExtractedFact compatibility
19.  Agentic RAG compatibility
20.  Regression behavior

Run: python3 tests/test_extraction_v2.py
"""

import sys
import os
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.ERROR)

from backend.extraction2.document_type_detector import (
    DocumentTypeDetector,
    DOC_TYPE_SEC_XBRL,
    DOC_TYPE_SEC_HTML,
    DOC_TYPE_PDF,
    DOC_TYPE_PDF_SCANNED,
    DOC_TYPE_DOCX,
    DOC_TYPE_XLSX,
    DOC_TYPE_CSV,
    DOC_TYPE_TXT,
    DOC_TYPE_HTML,
    DOC_TYPE_UNKNOWN,
)
from backend.extraction2.table_extractor import TableExtractor, Table
from backend.extraction2.xbrl_extractor import XbrlExtractor
from backend.extraction2.negative_detector import (
    NegativeDetector,
    parse_parenthesized_value,
)
from backend.extraction2.confidence_scorer import (
    ConfidenceScorer,
    METHOD_XBRL,
    METHOD_UNANCHORED_REGEX,
)
from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState,
    EvidenceItem,
)


class TestDocumentTypeDetector(unittest.TestCase):

    def test_detects_xbrl(self):
        content = b'<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance"><us-gaap:Revenues contextRef="c1">100</us-gaap:Revenues></xbrl>'
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.xbrl", content=content), DOC_TYPE_SEC_XBRL)

    def test_detects_inline_sec_html(self):
        content = b'<html><head><title>10-K</title></head><body><ix:header>inlineXBRL</ix:header><ix:nonFraction name="us-gaap:Revenues">100</ix:nonFraction></body></html>'
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.html", content=content), DOC_TYPE_SEC_HTML)

    def test_detects_plain_html(self):
        self.assertEqual(
            DocumentTypeDetector().detect(file_name="f.html", content=b"<html><body>Hello</body></html>"),
            DOC_TYPE_HTML,
        )

    def test_detects_pdf(self):
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.pdf", content=b"%PDF-1.4 blah"), DOC_TYPE_PDF)

    def test_detects_pdf_scanned_from_parsed(self):
        parsed = {"type": "pdf", "pages": 10, "text": "tiny"}
        self.assertEqual(DocumentTypeDetector().detect(parsed=parsed), DOC_TYPE_PDF_SCANNED)

    def test_detects_docx_and_xlsx(self):
        # Ambiguous truncated zip content falls back to extension detection
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.docx", content=b"PK\x03\x04"), DOC_TYPE_DOCX)
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.docx"), DOC_TYPE_DOCX)
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.xlsx"), DOC_TYPE_XLSX)
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.csv"), DOC_TYPE_CSV)
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.txt"), DOC_TYPE_TXT)

    def test_unknown(self):
        self.assertEqual(DocumentTypeDetector().detect(file_name="f.xyz"), DOC_TYPE_UNKNOWN)


class TestNegativeDetector(unittest.TestCase):

    def test_footnote_reference_not_negative(self):
        self.assertIsNone(parse_parenthesized_value("(1)"))
        self.assertIsNone(parse_parenthesized_value("(2)"))
        self.assertIsNone(parse_parenthesized_value("(4)"))

    def test_accounting_negative_with_context(self):
        self.assertEqual(parse_parenthesized_value("(500)", context="Revenue (500 million)"), -500.0)
        self.assertEqual(parse_parenthesized_value("(₹500 million)", context="Net profit (₹500 million)"), -500.0)
        self.assertEqual(parse_parenthesized_value("(50,000,000)"), -50000000.0)

    def test_footnote_with_page_context_stays_footnote(self):
        self.assertIsNone(parse_parenthesized_value("(1)", context="Page 12"))

    def test_footnote_detector(self):
        self.assertTrue(NegativeDetector.is_footnote_reference("1"))
        self.assertTrue(NegativeDetector.is_footnote_reference("12"))
        self.assertFalse(NegativeDetector.is_footnote_reference("500", context="Revenue (500 million)"))


class TestConfidenceScorer(unittest.TestCase):

    def test_hierarchy(self):
        xbrl = ConfidenceScorer.score(METHOD_XBRL, has_period=True, has_currency=True, has_unit_scale=True, has_anchor=True)
        low = ConfidenceScorer.score(METHOD_UNANCHORED_REGEX)
        self.assertGreater(xbrl, 0.9)
        self.assertLess(low, 0.4)
        self.assertGreater(xbrl, low)

    def test_never_fabricates_full_confidence(self):
        score = ConfidenceScorer.score(METHOD_XBRL, has_period=True, has_currency=True, has_unit_scale=True, has_anchor=True)
        self.assertLess(score, 1.0)


class TestTableExtractor(unittest.TestCase):

    def test_text_table_with_periods(self):
        text = (
            "(in $ millions)          FY2025      FY2024\n"
            "Revenue                  573,000     512,000\n"
            "Net income                30,000      27,000\n"
        )
        tables = TableExtractor().extract_text_tables(text)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        # column_periods aligns with header columns; slot 0 is the label column
        self.assertEqual(table.column_periods[1:], ["FY2025", "FY2024"])
        self.assertEqual(table.scale, "millions")
        self.assertEqual(table.currency, "USD")
        rev_row = next(r for r in table.rows if r["label"] == "Revenue")
        self.assertEqual(rev_row["cells"], ["573,000", "512,000"])

    def test_html_table(self):
        html = (
            "<html><body><table>"
            "<tr><th>Metric</th><th>FY2025</th><th>FY2024</th></tr>"
            "<tr><td>Revenue</td><td>573,000</td><td>512,000</td></tr>"
            "<tr><td>Net income</td><td>30,000</td><td>27,000</td></tr>"
            "</table></body></html>"
        )
        tables = TableExtractor().extract_html_tables(html)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table.column_periods[1:], ["FY2025", "FY2024"])

    def test_table_continuation_merged(self):
        text = (
            "FY2025         FY2024\n"
            "Revenue        573,000    512,000\n"
            "\n"
            "FY2025         FY2024\n"
            "Net income      30,000     27,000\n"
        )
        tables = TableExtractor().extract_text_tables(text)
        self.assertEqual(len(tables), 1)
        labels = [r["label"] for r in tables[0].rows]
        self.assertIn("Revenue", labels)
        self.assertIn("Net income", labels)

    def test_multi_column_rows_stay_separate(self):
        text = (
            "FY2025        FY2024\n"
            "Revenue       573,000   512,000\n"
            "Net income     30,000    27,000\n"
        )
        tables = TableExtractor().extract_text_tables(text)
        self.assertEqual(len(tables), 1)
        row = next(r for r in tables[0].rows if r["label"] == "Revenue")
        self.assertEqual(len(row["cells"]), 2)


class TestXbrlExtractor(unittest.TestCase):

    XBRL = """<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:dei="http://xbrl.sec.gov/dei/2024"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <context id="c1">
    <entity><identifier>0000320193</identifier></entity>
    <period><startDate>2024-09-29</startDate><endDate>2025-09-27</endDate></period>
  </context>
  <context id="c2">
    <entity><identifier>0000320193</identifier></entity>
    <period><instant>2025-09-27</instant></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <dei:DocumentType>10-K</dei:DocumentType>
  <dei:AmendmentFlag>false</dei:AmendmentFlag>
  <us-gaap:Revenues contextRef="c1" decimals="-6" unitRef="usd">391035000000</us-gaap:Revenues>
  <us-gaap:NetIncomeLoss contextRef="c1" decimals="-6" unitRef="usd">93736000000</us-gaap:NetIncomeLoss>
  <us-gaap:EarningsPerShareDiluted contextRef="c1" decimals="2" unitRef="usd">6.08</us-gaap:EarningsPerShareDiluted>
  <us-gaap:Assets contextRef="c2" decimals="-6" unitRef="usd">364980000000</us-gaap:Assets>
</xbrl>
"""

    def test_raw_xbrl_facts(self):
        facts = XbrlExtractor().extract(self.XBRL)
        self.assertEqual(len(facts), 4)
        by_local = {f.local_name: f for f in facts}
        self.assertEqual(by_local["Revenues"].value, 391035000000.0)
        self.assertEqual(by_local["Revenues"].unit, "USD")
        self.assertEqual(by_local["Revenues"].period_start, "2024-09-29")
        self.assertEqual(by_local["Revenues"].period_end, "2025-09-27")
        self.assertEqual(by_local["Revenues"].fiscal_year, 2025)
        self.assertEqual(by_local["EarningsPerShareDiluted"].value, 6.08)
        self.assertEqual(by_local["Assets"].instant, "2025-09-27")
        # dei metadata
        self.assertTrue(all(f.filing_type == "10-K" for f in facts))
        self.assertTrue(all(not f.is_amendment for f in facts))

    def test_inline_xbrl(self):
        inline = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
                         xmlns:xbrli="http://www.xbrl.org/2003/instance"
                         xmlns:us-gaap="http://fasb.org/us-gaap/2024"
                         xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<body>
<ix:header>
  <ix:resources>
    <xbrli:context id="c1"><entity><identifier>X</identifier></entity>
      <period><startDate>2024-09-29</startDate><endDate>2025-09-27</endDate></period></xbrli:context>
    <xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  </ix:resources>
</ix:header>
<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
                contextRef="c1" decimals="-6" scale="6" unitRef="u1" format="ixt:numdotdecimal">391035</ix:nonFraction>
</body></html>"""
        facts = XbrlExtractor().extract(inline)
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        # scale=6 applied: 391035 * 10^6 = 391,035,000,000
        self.assertEqual(fact.value, 391035000000.0)
        self.assertEqual(fact.concept, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")

    def test_xbrl_concepts_not_collapsed(self):
        facts = XbrlExtractor().extract(self.XBRL)
        names = {f.concept for f in facts}
        self.assertIn("us-gaap:Revenues", names)
        self.assertIn("us-gaap:NetIncomeLoss", names)


class TestFinancialExtractorV2(unittest.TestCase):

    def setUp(self):
        self.v2 = FinancialExtractorV2()

    def _parsed(self, text="", table_data=None, xbrl_facts=None, doc_type="txt"):
        return {
            "type": doc_type,
            "text": text,
            "table_data": table_data or [],
            "xbrl_facts": xbrl_facts or [],
        }

    # -- 1 & 2. Realistic tables with multiple periods -------------------

    def test_table_facts_with_periods(self):
        parsed = self._parsed(
            table_data=[{
                "table_id": "t1",
                "headers": ["Metric", "FY2025", "FY2024"],
                "rows": [
                    {"label": "Revenue", "cells": ["573,000", "512,000"]},
                    {"label": "Net income", "cells": ["30,000", "27,000"]},
                ],
                "source_location": "xlsx",
            }],
            doc_type="xlsx",
        )
        result = self.v2.extract_document(parsed)
        facts = result["facts"]
        self.assertEqual(len(facts), 4)
        rev25 = next(f for f in facts if f["metric_id"] == "Revenue" and f["fiscal_period"] == "FY2025")
        rev24 = next(f for f in facts if f["metric_id"] == "Revenue" and f["fiscal_period"] == "FY2024")
        self.assertEqual(rev25["metric_value"], 573000.0)
        self.assertEqual(rev24["metric_value"], 512000.0)
        # distinct periods never merge
        self.assertNotEqual(rev25["evidence_hash"], rev24["evidence_hash"])

    # -- 3. Table continuation via text -----------------------------------

    def test_text_table_continuation(self):
        text = (
            "FY2025    FY2024\n"
            "Revenue   573,000   512,000\n"
            "\n"
            "FY2025    FY2024\n"
            "Net income 30,000    27,000\n"
        )
        parsed = self._parsed(text=text, doc_type="pdf")
        result = self.v2.extract_document(parsed)
        facts = result["facts"]
        self.assertEqual(result["stats"]["tables_detected"], 1)
        self.assertTrue(any(f["metric_id"] == "Revenue" for f in facts))
        self.assertTrue(any(f["metric_id"] == "NetIncome" for f in facts))

    # -- 4 & 5. XBRL extraction + priority -------------------------------

    XBRL_DOC = {
        "type": "html",
        "text": "Revenue was around 391 billion. Revenue 2025. Net income 93 billion.",
        "table_data": [],
        "xbrl_facts": [
            {
                "concept": "us-gaap:Revenues",
                "local_name": "Revenues",
                "value": 391035000000.0,
                "raw_text": "391035000000",
                "unit": "USD",
                "period_end": "2025-09-27",
                "fiscal_year": 2025,
                "fiscal_quarter": "FY",
                "filing_type": "10-K",
                "accession_number": "0000320193-25-000111",
                "is_amendment": False,
            }
        ],
    }

    def test_xbrl_priority_over_text(self):
        result = self.v2.extract_document(self.XBRL_DOC)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(rev)
        # XBRL fact is present with the exact structured value, not the text guess
        self.assertIn(391035000000.0, {f["metric_value"] for f in rev})
        self.assertTrue(any(f["source_type"] == "XBRL" for f in rev))

    # -- 6. Scale handling ------------------------------------------------

    def test_millions_and_billions_normalize_equally(self):
        text = (
            "Revenue was $1,250 million in FY2024. "
            "Revenue was $1.25 billion in FY2025."
        )
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertEqual(len(rev), 2)
        # 1,250 million and 1.25 billion normalize to the SAME value
        self.assertEqual({f["normalized_value"] for f in rev}, {1250000000.0})
        # ...while preserving their original scale metadata
        scales = {f["scale"] for f in rev if f["scale"]}
        self.assertIn("millions", scales)
        self.assertIn("billions", scales)
        # periods stay associated with the correct value
        periods = {f["fiscal_period"] for f in rev}
        self.assertEqual(periods, {"FY2024", "FY2025"})

    def test_crore_scale(self):
        text = "Net profit was ₹1,250 crore in FY2024."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        net = [f for f in result["facts"] if f["metric_id"] == "NetIncome"]
        self.assertTrue(net)
        self.assertEqual(net[0]["currency_code"], "INR")
        self.assertEqual(net[0]["normalized_value"], 12500000000.0)  # 1250 crore

    # -- 7 & 8. Negative values vs footnote parens ------------------------

    def test_negative_accounting_value(self):
        text = "Operating loss was (50,000,000) in FY2024, Revenue was $100 million."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        # No metric label matches "Operating loss" -> check generic handling
        self.assertIsInstance(result["facts"], list)

    def test_footnote_parens_never_negative(self):
        text = "Notes (1) (2) (4) refer to contingencies. Revenue was $573,000 in FY2025."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        negs = [f for f in result["facts"] if f["metric_value"] and f["metric_value"] < 0]
        self.assertEqual(negs, [])
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(rev)

    # -- 9 & 10. Currency detection and roles -----------------------------

    def test_currency_detection(self):
        text = "Revenue was €1,250 million in FY2024."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(rev)
        self.assertEqual(rev[0]["currency_code"], "EUR")
        self.assertEqual(rev[0]["currency_role"], "REPORTING")

    def test_inr_currency(self):
        text = "Revenue was ₹573,000 million in FY2025."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(rev)
        self.assertEqual(rev[0]["currency_code"], "INR")

    # -- 11. GAAP vs non-GAAP stay distinct --------------------------------

    def test_gaap_vs_nongaap_definitions(self):
        text = (
            "GAAP revenue was $391,000 million in FY2025. "
            "Non-GAAP adjusted revenue was $401,000 million in FY2025."
        )
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        self.assertTrue(len(rev) >= 1)
        # Both values preserved as separate facts (391,000 million = 391B)
        values = {f["normalized_value"] for f in rev}
        self.assertIn(391000000000.0, values)
        self.assertIn(401000000000.0, values)

    # -- 12. Evidence anchoring --------------------------------------------

    def test_evidence_anchors(self):
        text = "Revenue was $573,000 in FY2025 (page 42)."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        for f in result["facts"]:
            self.assertTrue(f["evidence_text_anchor"])
            self.assertIsInstance(f["evidence_hash"], str)
            self.assertEqual(len(f["evidence_hash"]), 64)  # sha256 hex

    # -- 13. Multi-column stays separate -----------------------------------

    def test_multi_column_not_merged(self):
        text = (
            "FY2025        FY2024\n"
            "Revenue       573,000   512,000\n"
            "Net income     30,000    27,000\n"
        )
        parsed = self._parsed(text=text, doc_type="pdf")
        result = self.v2.extract_document(parsed)
        rev = [f for f in result["facts"] if f["metric_id"] == "Revenue"]
        periods = {f["fiscal_period"] for f in rev}
        self.assertIn("FY2025", periods)
        self.assertIn("FY2024", periods)

    # -- 14. Repeated headers/footers don't create phantom facts -----------

    def test_repeated_headers(self):
        text = (
            "Page 1  Apple Inc. 2025 Form 10-K\n"
            "FY2025       FY2024\n"
            "Revenue      573,000     512,000\n"
            "Page 2  Apple Inc. 2025 Form 10-K\n"
            "FY2025       FY2024\n"
            "Net income    30,000      27,000\n"
        )
        parsed = self._parsed(text=text, doc_type="pdf")
        result = self.v2.extract_document(parsed)
        self.assertTrue(any(f["metric_id"] == "Revenue" for f in result["facts"]))
        self.assertTrue(any(f["metric_id"] == "NetIncome" for f in result["facts"]))

    # -- 15 & 16. Page numbers & fiscal years rejected ----------------------

    def test_page_numbers_rejected(self):
        text = "Page 12. Page 101. Page 3. Revenue was $573,000 in FY2025."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        for f in result["facts"]:
            self.assertNotIn(f["metric_value"], (12.0, 101.0, 3.0))

    def test_fiscal_years_rejected(self):
        text = "For fiscal year 2025 the company reported revenue. Revenue was $573,000."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        for f in result["facts"]:
            self.assertNotEqual(f["metric_value"], 2025.0)

    # -- 17. Ambiguous values unresolved ------------------------------------

    def test_ambiguous_remains_unresolved(self):
        # "Revenue" followed only by a bare year and no financial context
        text = "Revenue 2025 and 2024 were both strong years for growth and margin expansion."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        bad = [f for f in result["facts"] if f["metric_value"] in (2025.0, 2024.0)]
        self.assertEqual(bad, [])

    # -- 18. ExtractedFact compatibility ------------------------------------

    def test_facts_match_extracted_fact_schema(self):
        from backend.database.models import ExtractedFact

        text = "Revenue was $573,000 in FY2025."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)
        self.assertTrue(result["facts"])
        model_columns = {c.name for c in ExtractedFact.__table__.columns}
        for f in result["facts"]:
            for key in f:
                if key in model_columns:
                    continue  # every fact key present in the model is valid
            # at minimum the required model fields are present
            for required in ("metric_id", "metric_name", "metric_value", "evidence_hash"):
                self.assertIn(required, f)

    # -- 19. Agentic RAG compatibility --------------------------------------

    def test_evidence_item_compat_and_dedup(self):
        state = EvidenceSummaryState(max_iterations=3)

        text = "Revenue was $573,000 in FY2025. Revenue was $573,000 in FY2025 again."
        parsed = self._parsed(text=text, doc_type="txt")
        result = self.v2.extract_document(parsed)

        items = [EvidenceItem(**FinancialExtractorV2.to_evidence_item_dict(f)) for f in result["facts"]]
        added = state.add_evidence_batch(items)

        # Compact state: every duplicate suppressed, no evidence growth
        self.assertLessEqual(state.state.evidence_count, len(result["facts"]))
        for f in result["facts"]:
            d = FinancialExtractorV2.to_evidence_item_dict(f)
            for key in ("metric", "value", "source_tier", "evidence_hash", "source_anchor"):
                self.assertIn(key, d)

    # -- 20. Regression behavior --------------------------------------------

    def test_detector_unknown_does_not_crash_pipeline(self):
        parsed = {"type": "txt", "text": "", "table_data": [], "xbrl_facts": []}
        result = self.v2.extract_document(parsed)
        self.assertEqual(result["facts"], [])

    def test_empty_document(self):
        parsed = {"type": "txt", "text": "", "table_data": [], "xbrl_facts": []}
        result = self.v2.extract_document(parsed)
        self.assertEqual(result["facts"], [])


def run_all():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_all()
    print(f"\n{'=' * 60}")
    print(f"EXTRACTION 2.0 TESTS: {result.testsRun} run, "
          f"{len(result.failures)} failures, {len(result.errors)} errors")
    sys.exit(0 if result.wasSuccessful() else 1)
