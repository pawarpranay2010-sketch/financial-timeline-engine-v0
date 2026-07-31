"""
Comprehensive Test Suite for Agentic RAG Core

Tests:
1. Complete evidence on first retrieval
2. Missing evidence → second retrieval
3. Missing evidence → third retrieval
4. Fourth retrieval is blocked
5. Duplicate chunks are suppressed (SHA-256)
6. Repeated queries are suppressed
7. Evidence state remains compact
8. Currency mismatch blocks calculation
9. Metric-definition mismatch is not falsely treated as value conflict
10. Period mismatch is detected
11. Tier 3 source supersedes Tier 1 when applicable
12. 10-K/A can supersede original 10-K for affected facts
13. Insufficient evidence terminates safely
14. Material conflict terminates safely
15. Existing AI Executive integration remains functional
16. Existing PostgreSQL/Redis behavior remains functional
17. Existing Module 3 calculations remain functional
"""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch


# =========================================================================
# Test 1-7: Evidence Summary State
# =========================================================================


class TestEvidenceSummaryState(unittest.TestCase):
    """Tests for EvidenceSummaryState — SHA-256 dedup, requirement tracking."""

    def setUp(self):
        from backend.intelligence.evidence_summary_state import (
            EvidenceSummaryState, EvidenceItem, InformationRequirement,
            STATE_COMPLETE, STATE_RETRIEVAL_LIMIT_REACHED,
            STATE_INSUFFICIENT_EVIDENCE,
        )
        self.EvidenceSummaryState = EvidenceSummaryState
        self.EvidenceItem = EvidenceItem
        self.InformationRequirement = InformationRequirement
        self.STATE_COMPLETE = STATE_COMPLETE
        self.STATE_RETRIEVAL_LIMIT_REACHED = STATE_RETRIEVAL_LIMIT_REACHED
        self.STATE_INSUFFICIENT_EVIDENCE = STATE_INSUFFICIENT_EVIDENCE

    def test_1_complete_evidence_on_first_retrieval(self):
        """Test: requirements met immediately on first retrieval."""
        state = self.EvidenceSummaryState(max_iterations=3)
        state.add_requirement(self.InformationRequirement(
            id="req_1", metric="Revenue", period="FY2024"
        ))

        item = self.EvidenceItem(
            metric="Revenue", value=100.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item)

        self.assertTrue(state.all_requirements_satisfied)
        self.assertTrue(state.should_continue() is False)  # Loop stops
        self.assertEqual(state.state.terminal_state, self.STATE_COMPLETE)

    def test_2_missing_evidence_triggers_second_retrieval(self):
        """Test: missing requirement allows second retrieval."""
        state = self.EvidenceSummaryState(max_iterations=3)
        state.add_requirement(self.InformationRequirement(
            id="req_1", metric="Revenue", period="FY2024"
        ))
        state.add_requirement(self.InformationRequirement(
            id="req_2", metric="NetIncome", period="FY2024"
        ))

        # Only satisfy first requirement
        item = self.EvidenceItem(
            metric="Revenue", value=100.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item)
        state.record_iteration("revenue query", 1)

        self.assertFalse(state.all_requirements_satisfied)
        self.assertTrue(state.can_retrieve)  # Can try again
        self.assertTrue(state.should_continue())  # Loop continues

        # Add missing requirement
        item2 = self.EvidenceItem(
            metric="NetIncome", value=20.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item2)
        state.record_iteration("net income query", 1)

        self.assertTrue(state.all_requirements_satisfied)

    def test_3_missing_third_retrieval(self):
        """Test: missing evidence across 3 iterations works."""
        state = self.EvidenceSummaryState(max_iterations=3)
        state.add_requirement(self.InformationRequirement(
            id="req_1", metric="Revenue", period="FY2024"
        ))
        state.add_requirement(self.InformationRequirement(
            id="req_2", metric="NetIncome", period="FY2024"
        ))
        state.add_requirement(self.InformationRequirement(
            id="req_3", metric="EBITDA", period="FY2024"
        ))

        # Iteration 1
        item = self.EvidenceItem(
            metric="Revenue", value=100.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item)
        state.record_iteration("query 1", 1)

        # Iteration 2
        item2 = self.EvidenceItem(
            metric="NetIncome", value=20.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item2)
        state.record_iteration("query 2", 1)

        # Iteration 3
        item3 = self.EvidenceItem(
            metric="EBITDA", value=40.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item3)
        state.record_iteration("query 3", 1)

        self.assertTrue(state.all_requirements_satisfied)
        self.assertEqual(state.state.iterations_used, 3)

    def test_4_fourth_retrieval_blocked(self):
        """Test: fourth retrieval is blocked by max_iterations."""
        state = self.EvidenceSummaryState(max_iterations=3)

        for i in range(3):
            item = self.EvidenceItem(
                metric=f"Metric{i}", value=float(i),
                source="test", source_tier=1,
            )
            state.add_evidence(item)
            state.record_iteration(f"query {i}", 1)
            state.add_requirement(self.InformationRequirement(
                id=f"req_{i}", metric=f"Metric{i}"
            ))

        # Fourth retrieval should be blocked
        self.assertFalse(state.can_retrieve)
        self.assertEqual(state.state.iterations_used, 3)

    def test_5_duplicate_suppressed(self):
        """Test: SHA-256 dedup suppresses identical evidence."""
        state = self.EvidenceSummaryState()

        item1 = self.EvidenceItem(
            metric="Revenue", value=100.0, reporting_period="FY2024",
            source="sec", source_tier=3,
        )
        item1.evidence_hash = state.compute_evidence_hash(item1.to_dict())

        item2 = self.EvidenceItem(
            metric="Revenue", value=100.0, reporting_period="FY2024",
            source="sec", source_tier=3,
        )
        item2.evidence_hash = state.compute_evidence_hash(item2.to_dict())

        # First add should succeed
        self.assertTrue(state.add_evidence(item1))
        self.assertEqual(state.state.evidence_count, 1)

        # Second add (same hash) should be suppressed
        self.assertFalse(state.add_evidence(item2))
        self.assertEqual(state.state.evidence_count, 1)

    def test_6_repeated_query_suppressed(self):
        """Test: identical queries are suppressed."""
        state = self.EvidenceSummaryState()

        self.assertFalse(state.is_query_repeated("query one"))
        state.record_query("query one")
        self.assertTrue(state.is_query_repeated("query one"))

        # Similar but not identical query
        self.assertFalse(state.is_query_repeated("query two"))

    def test_7_evidence_state_compact(self):
        """Test: compact context does not dump full retrieval history."""
        state = self.EvidenceSummaryState()
        state.add_requirement(self.InformationRequirement(
            id="req_1", metric="Revenue", period="FY2024"
        ))

        # Add 5 evidence items
        for i in range(5):
            item = self.EvidenceItem(
                metric="Revenue" if i == 0 else f"Metric{i}",
                value=float(i * 10),
                reporting_period="FY2024",
                source="test", source_tier=2,
            )
            state.add_evidence(item)

        context = state.get_compact_context()

        # Compact context should show summary, not raw evidence
        self.assertIn("EVIDENCE STATUS", context)
        self.assertIn("Satisfied:", context)
        self.assertIn("Missing:", context)

        # Should NOT print every single chunk raw
        lines = context.split("\n")
        self.assertLess(len(lines), 50)  # Compact


# =========================================================================
# Test 8: Currency mismatch
# =========================================================================


class TestCurrencyValidator(unittest.TestCase):
    """Tests for CurrencyValidator."""

    def setUp(self):
        from backend.intelligence.currency_validator import CurrencyValidator
        self.CurrencyValidator = CurrencyValidator

    def test_8_currency_mismatch_blocks_calculation(self):
        """Test: incompatible currencies return CURRENCY_MISMATCH."""
        facts = [
            {"currency_code": "EUR", "currency_role": "REPORTING"},
            {"currency_code": "USD", "currency_role": "REPORTING"},
        ]
        compatible, error = self.CurrencyValidator.check_currency_compatibility(facts)
        self.assertFalse(compatible)
        self.assertIn("CURRENCY_MISMATCH", error or "")

    def test_same_currency_is_compatible(self):
        facts = [
            {"currency_code": "USD", "currency_role": "REPORTING"},
            {"currency_code": "USD", "currency_role": "FUNCTIONAL"},
        ]
        compatible, error = self.CurrencyValidator.check_currency_compatibility(facts)
        self.assertTrue(compatible)

    def test_different_role_incompatible(self):
        """Test: EUR revenue ÷ USD income returns CURRENCY_MISMATCH."""
        left = {"currency_code": "EUR", "currency_role": "REPORTING"}
        right = {"currency_code": "USD", "currency_role": "TRANSACTION"}
        compatible, error = self.CurrencyValidator.check_operation_currency(
            left, right, "divide"
        )
        self.assertFalse(compatible)
        self.assertIn("CURRENCY_MISMATCH", error or "")

    def test_same_role_different_currency_needs_fx(self):
        """Test: same role, different currency incompatible without FX."""
        facts = [
            {"currency_code": "EUR", "currency_role": "REPORTING"},
            {"currency_code": "USD", "currency_role": "REPORTING"},
        ]
        compatible, error = self.CurrencyValidator.check_currency_compatibility(facts)
        self.assertFalse(compatible)
        self.assertIn("CURRENCY_MISMATCH", error or "")

    def test_fx_metadata_makes_compatible(self):
        """Test: FX metadata allows compatibility for same-role currencies."""
        facts = [
            {"currency_code": "EUR", "currency_role": "REPORTING",
             "fx_rate": 1.1, "fx_source": "ECB",
             "fx_timestamp": "2026-01-01T00:00:00Z"},
            {"currency_code": "USD", "currency_role": "REPORTING",
             "fx_rate": 0.91, "fx_source": "ECB",
             "fx_timestamp": "2026-01-01T00:00:00Z"},
        ]
        compatible, error = self.CurrencyValidator.check_currency_compatibility(facts)
        self.assertTrue(compatible)


# =========================================================================
# Test 9: Metric definition mismatch
# =========================================================================


class TestMetricDefinition(unittest.TestCase):
    """Tests for metric semantic identity in normalizer."""

    def setUp(self):
        from backend.module4.normalizer import MetricDictionary
        self.MetricDictionary = MetricDictionary

    def test_9_metric_definition_mismatch_detected(self):
        """Test: GAAP vs non-GAAP are distinguished."""
        name_a, def_a = self.MetricDictionary.resolve_with_definition("GAAP Revenue")
        name_b, def_b = self.MetricDictionary.resolve_with_definition("non-GAAP Revenue")

        self.assertEqual(name_a, "Revenue")
        self.assertEqual(name_b, "Revenue")
        self.assertEqual(def_a, "GAAP")
        self.assertEqual(def_b, "non-GAAP")
        self.assertFalse(self.MetricDictionary.definitions_match(def_a, def_b))

    def test_metric_definition_default_compatible(self):
        """Test: empty definition and 'reported' are compatible."""
        self.assertTrue(self.MetricDictionary.definitions_match("", ""))
        self.assertTrue(self.MetricDictionary.definitions_match("", "reported"))


# =========================================================================
# Test 10: Period mismatch
# =========================================================================


class TestPeriodDetection(unittest.TestCase):
    """Tests for period mismatch in extraction auditor."""

    def setUp(self):
        from backend.intelligence.extraction_auditor import ExtractionAuditor, PERIOD_MISMATCH
        self.ExtractionAuditor = ExtractionAuditor
        self.PERIOD_MISMATCH = PERIOD_MISMATCH

    def test_10_period_mismatch_detected(self):
        """Test: different reporting periods are detected."""
        result = self.ExtractionAuditor.compare(
            {"metric_name": "Revenue", "value": 100.0, "period_end": "2024-09-30"},
            {"metric_name": "Revenue", "value": 90.0, "period_end": "2023-09-30"},
        )
        self.assertEqual(result.state, self.PERIOD_MISMATCH)

    def test_same_period_agreement(self):
        """Test: same period is not flagged."""
        result = self.ExtractionAuditor.compare(
            {"metric_name": "Revenue", "value": 100.0, "period_end": "2024-09-30"},
            {"metric_name": "Revenue", "value": 101.0, "period_end": "2024-09-30"},
        )
        self.assertEqual(result.state, "SEMANTIC_EQUIVALENCE")


# =========================================================================
# Test 11: Tier 3 supersedes Tier 1
# =========================================================================


class TestSourceResolution(unittest.TestCase):
    """Tests for source resolver tier hierarchy."""

    def setUp(self):
        from backend.intelligence.source_resolver import SourceResolver
        self.SourceResolver = SourceResolver

    def test_11_tier_3_supersedes_tier_1(self):
        """Test: Tier 3 authoritative source supersedes Tier 1 public source."""
        resolver = self.SourceResolver()
        items = [
            {"source": "news_article", "source_tier": 1, "value": 95.0,
             "filing_type": "", "confidence": 0.7},
            {"source": "sec", "source_tier": 3, "value": 100.0,
             "filing_type": "10-K", "confidence": 0.99},
        ]
        status, resolved = resolver.resolve_conflict(items)
        self.assertEqual(status, "RESOLVED")
        self.assertEqual(resolved["value"], 100.0)
        self.assertEqual(resolved["source"], "sec")

    def test_tier_3_always_wins(self):
        """Test: Tier 3 always wins regardless of order."""
        resolver = self.SourceResolver()
        items = [
            {"source": "fmp", "source_tier": 2, "value": 99.0,
             "filing_type": "", "confidence": 0.9},
            {"source": "sec_filing", "source_tier": 3, "value": 100.0,
             "filing_type": "10-K", "confidence": 0.99},
            {"source": "news", "source_tier": 1, "value": 101.0,
             "filing_type": "", "confidence": 0.6},
        ]
        status, resolved = resolver.resolve_conflict(items)
        self.assertEqual(status, "RESOLVED")
        self.assertEqual(resolved["value"], 100.0)


# =========================================================================
# Test 12: 10-K/A supersedes 10-K
# =========================================================================


class TestFilingPrecedence(unittest.TestCase):
    """Tests for filing precedence — 10-K/A supersedes 10-K."""

    def setUp(self):
        from backend.intelligence.source_resolver import check_filing_precedence
        from backend.database.models import Filing
        self.check_filing_precedence = check_filing_precedence
        self.Filing = Filing

    def _make_filing(self, id_num, filing_type, fiscal_year=2024, fiscal_quarter=None,
                     filing_date_str="2024-01-15", is_amendment=False):
        return self.Filing(
            id=id_num,
            company_id=1,
            filing_type=filing_type,
            filing_date=datetime.strptime(filing_date_str, "%Y-%m-%d").date(),
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            is_amendment=is_amendment,
        )

    def test_12_10K_A_supersedes_10K(self):
        """Test: 10-K/A supersedes original 10-K for same period."""
        original = self._make_filing(1, "10-K", fiscal_year=2024, filing_date_str="2024-03-15")
        amendment = self._make_filing(2, "10-K/A", fiscal_year=2024, filing_date_str="2024-04-15", is_amendment=True)

        result = self.check_filing_precedence(amendment, original)
        self.assertEqual(result, 1)  # Amendment wins

    def test_10Q_A_supersedes_10Q(self):
        """Test: 10-Q/A supersedes original 10-Q for same period."""
        original = self._make_filing(1, "10-Q", fiscal_year=2024, fiscal_quarter="Q1",
                                     filing_date_str="2024-05-15")
        amendment = self._make_filing(2, "10-Q/A", fiscal_year=2024, fiscal_quarter="Q1",
                                      filing_date_str="2024-06-15", is_amendment=True)

        result = self.check_filing_precedence(amendment, original)
        self.assertEqual(result, 1)  # Amendment wins

    def test_different_periods_no_precedence(self):
        """Test: different fiscal years — no precedence."""
        f1 = self._make_filing(1, "10-K", fiscal_year=2023, filing_date_str="2024-03-15")
        f2 = self._make_filing(2, "10-K", fiscal_year=2024, filing_date_str="2025-03-15")

        result = self.check_filing_precedence(f1, f2)
        self.assertIsNone(result)  # Cannot establish precedence across periods


# =========================================================================
# Test 13-14: Terminal states
# =========================================================================


class TestTerminalStates(unittest.TestCase):
    """Tests for safe termination."""

    def setUp(self):
        from backend.intelligence.evidence_summary_state import (
            EvidenceSummaryState, EvidenceItem, InformationRequirement,
            STATE_INSUFFICIENT_EVIDENCE, STATE_RETRIEVAL_LIMIT_REACHED,
            STATE_UNRESOLVED_CONFLICT, STATE_CURRENCY_MISMATCH,
            STATE_EXTRACTION_CORRUPTED,
        )
        self.EvidenceSummaryState = EvidenceSummaryState
        self.EvidenceItem = EvidenceItem
        self.InformationRequirement = InformationRequirement
        self.STATE_INSUFFICIENT_EVIDENCE = STATE_INSUFFICIENT_EVIDENCE
        self.STATE_RETRIEVAL_LIMIT_REACHED = STATE_RETRIEVAL_LIMIT_REACHED
        self.STATE_UNRESOLVED_CONFLICT = STATE_UNRESOLVED_CONFLICT
        self.STATE_CURRENCY_MISMATCH = STATE_CURRENCY_MISMATCH
        self.STATE_EXTRACTION_CORRUPTED = STATE_EXTRACTION_CORRUPTED

    def test_13_insufficient_evidence_terminates_safely(self):
        """Test: insufficient evidence sets terminal state."""
        state = self.EvidenceSummaryState(max_iterations=1)
        state.add_requirement(self.InformationRequirement(
            id="req_1", metric="Revenue", period="FY2099"
        ))

        # No evidence added — should terminate
        state.set_terminal(
            self.STATE_INSUFFICIENT_EVIDENCE,
            "No evidence found for FY2099",
        )
        self.assertTrue(state.is_complete)
        self.assertEqual(state.state.terminal_state, self.STATE_INSUFFICIENT_EVIDENCE)

    def test_14_material_conflict_terminates_safely(self):
        """Test: material conflict sets terminal state."""
        state = self.EvidenceSummaryState()

        state.set_terminal(
            self.STATE_UNRESOLVED_CONFLICT,
            "Conflicting values cannot be resolved",
        )
        self.assertTrue(state.is_complete)
        self.assertFalse(state.can_retrieve)


# =========================================================================
# Test 15-17: Integration checks
# =========================================================================


class TestIntegration(unittest.TestCase):
    """Tests for integration with existing systems."""

    def test_15_ai_executive_importable(self):
        """Test: AI Executive remains importable."""
        try:
            from backend.gateway.ai_executive import AIExecutive
            self.assertTrue(hasattr(AIExecutive, "generate"))
        except ImportError as e:
            self.fail(f"AIExecutive import failed: {e}")

    def test_16_postgresql_redis_importable(self):
        """Test: existing DB and Redis components remain importable."""
        try:
            from backend.module4.redis_cache import RedisCache
            self.assertTrue(hasattr(RedisCache, "get"))
        except ImportError as e:
            self.fail(f"RedisCache import failed: {e}")

        try:
            from backend.module4.db_cache import DBCache
            self.assertTrue(hasattr(DBCache, "get_fresh_profile"))
        except ImportError as e:
            self.fail(f"DBCache import failed: {e}")

        try:
            from backend.module4.database_manager import DatabaseManager
            self.assertTrue(hasattr(DatabaseManager, "save_extracted_fact"))
        except ImportError as e:
            self.fail(f"DatabaseManager import failed: {e}")

    def test_17_module3_importable(self):
        """Test: Module 3 remains importable."""
        try:
            from backend.module3_controller import run_module3
            self.assertTrue(callable(run_module3))
        except ImportError as e:
            self.fail(f"Module3 import failed: {e}")

    def test_evidence_consolidator_accepts_agentic_rag(self):
        """Test: EvidenceConsolidator accepts agentic_rag_result parameter."""
        try:
            from backend.intelligence.evidence_consolidator import EvidenceConsolidator
            import inspect
            sig = inspect.signature(EvidenceConsolidator.consolidate)
            params = list(sig.parameters.keys())
            self.assertIn("agentic_rag_result", params)
        except ImportError as e:
            self.fail(f"EvidenceConsolidator import failed: {e}")

    def test_normalizer_metric_definition(self):
        """Test: normalizer supports resolve_with_definition."""
        try:
            from backend.module4.normalizer import MetricDictionary
            result = MetricDictionary.resolve_with_definition("GAAP Revenue")
            self.assertEqual(result[0], "Revenue")
            self.assertEqual(result[1], "GAAP")
        except ImportError as e:
            self.fail(f"Normalizer import failed: {e}")


# =========================================================================
# Test: ExtractedFact model
# =========================================================================


class TestExtractedFactModel(unittest.TestCase):
    """Tests for ExtractedFact model definition."""

    def test_extracted_fact_has_required_fields(self):
        """Test: ExtractedFact model has all required fields."""
        from backend.database.models import ExtractedFact
        columns = [c.name for c in ExtractedFact.__table__.columns]
        required = [
            "company_id", "metric_id", "metric_name", "metric_definition",
            "metric_value", "currency_code", "currency_role",
            "period_start", "period_end", "fiscal_period",
            "accounting_basis", "scope", "source", "source_tier",
            "filing_type", "evidence_hash", "verification_status",
        ]
        for field in required:
            self.assertIn(field, columns, f"Missing field: {field}")

    def test_extracted_fact_unique_hash(self):
        """Test: evidence_hash has unique constraint."""
        from backend.database.models import ExtractedFact
        hash_col = [c for c in ExtractedFact.__table__.columns if c.name == "evidence_hash"]
        self.assertTrue(hash_col[0].unique)


# =========================================================================
# Test: Orchestrator flow
# =========================================================================


class TestOrchestratorFlow(unittest.TestCase):
    """Tests for AgenticRAGOrchestrator flow."""

    def test_orchestrator_parse_goal(self):
        """Test: orchestrator parses goals correctly."""
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator
        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=3)
        requirements = orch._parse_goal("Analyze AAPL's FY2024 revenue and net income")

        self.assertEqual(len(requirements), 2)
        metrics = {r.metric for r in requirements}
        self.assertIn("Revenue", metrics)
        self.assertIn("NetIncome", metrics)
        self.assertEqual(requirements[0].period, "FY2024")

    def test_orchestrator_parse_goal_usd(self):
        """Test: orchestrator detects USD currency in goal."""
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator
        orch = AgenticRAGOrchestrator(ticker="AAPL")
        requirements = orch._parse_goal("AAPL USD revenue")
        self.assertEqual(requirements[0].currency, "USD")

    def test_orchestrator_parse_goal_inr(self):
        """Test: orchestrator detects INR currency in goal."""
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator
        orch = AgenticRAGOrchestrator(ticker="RELIANCE")
        requirements = orch._parse_goal("Reliance INR revenue")
        self.assertEqual(requirements[0].currency, "INR")

    def test_orchestrator_default_metrics(self):
        """Test: orchestrator provides default metrics when none detected."""
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator
        orch = AgenticRAGOrchestrator(ticker="AAPL")
        requirements = orch._parse_goal("Analyze AAPL")
        self.assertGreaterEqual(len(requirements), 3)  # Revenue, NetIncome, EBITDA

    @patch("backend.intelligence.agentic_rag_orchestrator.RetrievalAgent")
    def test_orchestrator_execute_flow(self, mock_retrieval):
        """Test: orchestrator execute method produces CanonicalEvidenceSet."""
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator

        mock_retrieval_instance = MagicMock()
        mock_retrieval_instance.get_company.return_value = {
            "ticker": "AAPL", "source": "postgresql",
        }
        mock_retrieval_instance.get_financials.return_value = []
        mock_retrieval_instance.get_market_price.return_value = None
        mock_retrieval_instance.get_news.return_value = []
        mock_retrieval.return_value = mock_retrieval_instance

        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=1)
        result = orch.execute("Analyze AAPL's revenue")

        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, "to_dict"))
        self.assertTrue(hasattr(result, "get_summary_text"))


if __name__ == "__main__":
    unittest.main()
