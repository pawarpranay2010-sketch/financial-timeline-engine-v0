"""
Platrixa — FYJC Live Database Persistence Tests
backend/fyjc_db_persistence_test.py

Regression tests for the FYJC live database persistence layer.
Tests field mapping, dedup, training eligibility, and graceful degradation.

Run: python3 -m pytest backend/fyjc_db_persistence_test.py -v
     or: python3 backend/fyjc_db_persistence_test.py
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Sample projection/orchestrate data (representative of real engine output)
# ---------------------------------------------------------------------------

_SAMPLE_VERIFIED_PROJECTION = {
    "status": "VERIFIED",
    "status_label": "Verified",
    "headline": "Verified",
    "tone": "green",
    "summary": "Platrixa deterministically interpreted this question and verified the result.",
    "understanding": {
        "transaction_type": "PURCHASE",
        "parties": ["Rahul"],
        "amounts": [{"original": "10000", "display": "₹10,000"}],
        "rates": [],
        "taxes": [],
        "fractions": [],
        "payment": ["CREDIT"],
        "historical": [],
        "accounts": ["Purchases", "Rahul"],
    },
    "journal": {
        "rows": [
            {"account": "Purchases", "side": "debit", "display": "₹10,000"},
            {"account": "Rahul", "side": "credit", "display": "₹10,000"},
        ],
        "total_debit": 10000,
        "total_credit": 10000,
    },
    "verification": {"total_debit": 10000, "total_credit": 10000, "verdict": "BALANCED"},
    "why": {"events": [{"event_id": "RULE_GOLDEN_PURCHASE", "text": "Purchase of goods on credit."}]},
    "calculation": {"records": []},
    "confidence_gate": None,
    "gate_resolution": None,
    "why_not": None,
    "next_action": None,
    "result": {
        "status": "VERIFIED",
        "debit_lines": [
            {"account": "Purchases", "amount": 10000, "side": "debit", "rule": "Purchase of goods"},
        ],
        "credit_lines": [
            {"account": "Rahul", "amount": 10000, "side": "credit", "rule": "Credit purchase"},
        ],
        "journal_balanced": True,
        "journal": {
            "narration": "Purchased goods from Rahul on credit for Rs.10,000.",
            "calculation_records": [],
        },
    },
}

_SAMPLE_REVIEW_REQUIRED_PROJECTION = {
    "status": "REVIEW_REQUIRED",
    "status_label": "One thing to clarify",
    "headline": "One thing to clarify",
    "tone": "amber",
    "summary": "Platrixa needs one precise clarification before it can finish.",
    "understanding": {
        "transaction_type": "PURCHASE",
        "parties": ["Raj"],
        "amounts": [{"original": "50000", "display": "₹50,000"}],
        "rates": [],
        "taxes": [],
        "fractions": [],
        "payment": [],
        "historical": [],
        "accounts": ["Purchases", "Raj"],
        "concerns": ["The transaction does not say whether it was for cash or on credit."],
    },
    "journal": {"rows": [], "total_debit": 0, "total_credit": 0},
    "verification": None,
    "why": {"events": []},
    "calculation": {"records": []},
    "confidence_gate": {"gate_id": "cash_credit", "question": "Cash or credit?", "alternatives": []},
    "gate_resolution": None,
    "why_not": "Missing payment method",
    "next_action": "Specify cash or credit.",
    "result": {
        "status": "REVIEW_REQUIRED",
        "debit_lines": [],
        "credit_lines": [],
        "journal_balanced": False,
        "journal": {"narration": None, "calculation_records": []},
    },
}

_SAMPLE_BLOCKED_PROJECTION = {
    "status": "BLOCKED",
    "status_label": "Safety boundary",
    "headline": "Platrixa stopped",
    "tone": "red",
    "summary": "Platrixa could not safely determine the accounting meaning.",
    "understanding": {
        "transaction_type": "UNKNOWN",
        "parties": [],
        "amounts": [],
        "rates": [],
        "taxes": [],
        "fractions": [],
        "payment": [],
        "historical": [],
        "accounts": [],
    },
    "journal": {"rows": [], "total_debit": 0, "total_credit": 0},
    "verification": None,
    "why": {"events": []},
    "calculation": {"records": []},
    "confidence_gate": None,
    "gate_resolution": None,
    "why_not": "Insufficient information",
    "next_action": "Provide more details.",
    "result": {
        "status": "BLOCKED",
        "debit_lines": [],
        "credit_lines": [],
        "journal_balanced": False,
        "journal": {"narration": None, "calculation_records": []},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPersistFyjcResult(unittest.TestCase):
    """Tests for persist_fyjc_result."""

    def setUp(self):
        """Reset the dedup set before each test."""
        from backend.fyjc_db_persistence import _persisted_fingerprints
        _persisted_fingerprints.clear()

    @patch("backend.fyjc_db_persistence._get_session")
    def test_verified_creates_all_three_tables(self, mock_get_session):
        """VERIFIED projection creates interactions + interpretations + candidates."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result

        question = "Purchased goods from Rahul on credit for Rs.10,000."
        fp = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:16]

        result = persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)

        self.assertTrue(result)
        self.assertEqual(mock_session.add.call_count, 3)  # interaction + interpretation + candidate
        mock_session.commit.assert_called_once()

    @patch("backend.fyjc_db_persistence._get_session")
    def test_review_required_creates_candidate(self, mock_get_session):
        """REVIEW_REQUIRED projection creates a candidate (eligible for training)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result

        question = "Purchased goods from Raj for Rs.50,000 cash or credit?"
        fp = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:16]

        result = persist_fyjc_result(_SAMPLE_REVIEW_REQUIRED_PROJECTION, question, fp)

        self.assertTrue(result)
        # interaction + interpretation + candidate = 3
        self.assertEqual(mock_session.add.call_count, 3)

    @patch("backend.fyjc_db_persistence._get_session")
    def test_blocked_does_not_create_candidate(self, mock_get_session):
        """BLOCKED projection creates interaction + interpretation only (no candidate)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result

        question = "Do something with this."
        fp = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:16]

        result = persist_fyjc_result(_SAMPLE_BLOCKED_PROJECTION, question, fp)

        self.assertTrue(result)
        # interaction + interpretation = 2 (no candidate for BLOCKED)
        self.assertEqual(mock_session.add.call_count, 2)

    @patch("backend.fyjc_db_persistence._get_session")
    def test_dedup_prevents_duplicate_writes(self, mock_get_session):
        """Same fingerprint persists only once across reruns."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result

        question = "Purchased goods from Rahul."
        fp = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:16]

        # First call: persists
        result1 = persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)
        self.assertTrue(result1)
        self.assertEqual(mock_session.add.call_count, 3)

        # Second call: dedup, no additional writes
        result2 = persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)
        self.assertTrue(result2)
        self.assertEqual(mock_session.add.call_count, 3)  # still 3, not 6

    @patch("backend.fyjc_db_persistence._get_session")
    def test_db_failure_returns_false(self, mock_get_session):
        """Database failure returns False without raising."""
        mock_get_session.return_value = None

        from backend.fyjc_db_persistence import persist_fyjc_result

        result = persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, "test", "abc")
        self.assertFalse(result)

    @patch("backend.fyjc_db_persistence._get_session")
    def test_exception_during_commit_returns_false(self, mock_get_session):
        """Exception during commit rolls back and returns False."""
        mock_session = MagicMock()
        mock_session.commit.side_effect = Exception("Connection lost")
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result

        result = persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, "test", "xyz")
        self.assertFalse(result)
        mock_session.rollback.assert_called()


class TestFieldExtraction(unittest.TestCase):
    """Tests for field extraction helpers."""

    def test_extract_understanding(self):
        from backend.fyjc_db_persistence import _extract_understanding
        u = _extract_understanding(_SAMPLE_VERIFIED_PROJECTION)
        self.assertEqual(u["transaction_type"], "PURCHASE")
        self.assertEqual(u["parties"], ["Rahul"])

    def test_extract_understanding_empty(self):
        from backend.fyjc_db_persistence import _extract_understanding
        u = _extract_understanding({})
        self.assertEqual(u, {})

    def test_extract_journal_accounts(self):
        from backend.fyjc_db_persistence import _extract_journal_accounts
        result = _SAMPLE_VERIFIED_PROJECTION["result"]
        debits, credits = _extract_journal_accounts(result)
        self.assertEqual(len(debits), 1)
        self.assertEqual(debits[0]["account"], "Purchases")
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0]["account"], "Rahul")

    def test_extract_journal_accounts_empty(self):
        from backend.fyjc_db_persistence import _extract_journal_accounts
        debits, credits = _extract_journal_accounts({})
        self.assertEqual(debits, [])
        self.assertEqual(credits, [])

    def test_extract_calculations(self):
        from backend.fyjc_db_persistence import _extract_calculations
        calcs = _extract_calculations(_SAMPLE_VERIFIED_PROJECTION["result"])
        self.assertIsInstance(calcs, list)

    def test_is_training_eligible_verified(self):
        from backend.fyjc_db_persistence import _is_training_eligible
        self.assertTrue(_is_training_eligible("VERIFIED"))

    def test_is_training_eligible_review_required(self):
        from backend.fyjc_db_persistence import _is_training_eligible
        self.assertTrue(_is_training_eligible("REVIEW_REQUIRED"))

    def test_is_training_eligible_blocked(self):
        from backend.fyjc_db_persistence import _is_training_eligible
        self.assertFalse(_is_training_eligible("BLOCKED"))

    def test_is_training_eligible_not_supported(self):
        from backend.fyjc_db_persistence import _is_training_eligible
        self.assertFalse(_is_training_eligible("NOT_SUPPORTED"))


class TestModelMapping(unittest.TestCase):
    """Tests that ORM model fields map correctly."""

    @patch("backend.fyjc_db_persistence._get_session")
    def test_interaction_fields(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result
        question = "Purchased goods from Rahul on credit for Rs.10,000."
        fp = "test_fp_1234"
        persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)

        # Get the FYJCInteraction that was added
        interaction_call = mock_session.add.call_args_list[0]
        interaction = interaction_call[0][0]
        self.assertEqual(interaction.raw_input, question)
        self.assertEqual(interaction.session_id, fp)
        self.assertIsNone(interaction.board)

    @patch("backend.fyjc_db_persistence._get_session")
    def test_interpretation_fields(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result
        question = "Purchased goods from Rahul on credit for Rs.10,000."
        fp = "test_fp_5678"
        persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)

        # Get the FYJCInterpretation that was added
        interp_call = mock_session.add.call_args_list[1]
        interp = interp_call[0][0]
        self.assertEqual(interp.model_id, "kernel-only")
        self.assertEqual(interp.kernel_status, "VERIFIED")
        self.assertTrue(interp.parse_success)
        self.assertTrue(interp.journal_balanced)
        self.assertEqual(interp.transaction_type, "PURCHASE")
        self.assertEqual(interp.parties, ["Rahul"])

    @patch("backend.fyjc_db_persistence._get_session")
    def test_candidate_fields(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        from backend.fyjc_db_persistence import persist_fyjc_result
        question = "Purchased goods from Rahul on credit for Rs.10,000."
        fp = "test_fp_9012"
        persist_fyjc_result(_SAMPLE_VERIFIED_PROJECTION, question, fp)

        # Get the FYJCTrainingCandidate that was added
        candidate_call = mock_session.add.call_args_list[2]
        candidate = candidate_call[0][0]
        self.assertEqual(candidate.status, "CANDIDATE")
        self.assertFalse(candidate.human_approved)
        self.assertFalse(candidate.exported_to_jsonl)
        self.assertEqual(candidate.evidence_count, 1)
        self.assertEqual(candidate.validation_count, 1)  # VERIFIED => 1
        self.assertEqual(candidate.version, 1)


if __name__ == "__main__":
    unittest.main()
