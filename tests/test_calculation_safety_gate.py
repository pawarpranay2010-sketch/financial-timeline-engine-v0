"""
Calculation Safety Gate — Fix #3 Test Suite

Tests:
A. VERIFIED revenue + VERIFIED net income → profit margin allowed
B. PENDING revenue → blocked
C. CONFLICTING revenue → blocked
D. UNRESOLVED_CONFLICT → blocked
E. REJECTED fact → blocked
F. INSUFFICIENT_EVIDENCE → blocked
G. MISSING required metric → blocked
H. CURRENCY_MISMATCH → blocked
I. PERIOD_MISMATCH → blocked
J. Empty evidence set → blocked
K. VERIFIED evidence with valid scale normalization → calculation allowed
L. VERIFIED INR/INR inputs → allowed
M. VERIFIED EUR/USD incompatible inputs → blocked
N. No blocked calculation produces a numeric result
O. No blocked calculation silently falls back to raw/unverified values
P. Existing calculations remain numerically unchanged for valid VERIFIED inputs

Plus orchestrator integration:
Q. Orchestrator canonical set admits VERIFIED only (PENDING leak killed)
R. Orchestrator blocks calculation on INSUFFICIENT_EVIDENCE / RETRIEVAL_LIMIT_REACHED
"""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest

logging.getLogger("fte").setLevel(logging.CRITICAL)

from backend.financial_calculator import (
    FinancialCalculator,
    safe_calculate_financial_ratios,
)
from backend.intelligence.calculation_safety_gate import CalculationSafetyGate
from backend.intelligence.agentic_rag_orchestrator import (
    AgenticRAGOrchestrator,
    CanonicalEvidenceSet,
)
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState,
    EvidenceItem,
    InformationRequirement,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_RETRIEVAL_LIMIT_REACHED,
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


class TestCalculationSafetyGate(unittest.TestCase):
    """Tests A–J: verification-status gating."""

    def setUp(self):
        self.gate = CalculationSafetyGate()

    # ----------------------------------------------------------
    # A — VERIFIED inputs allowed
    # ----------------------------------------------------------

    def test_A_verified_profit_margin_allowed(self):
        data = {
            "Revenue": _fact(1000.0),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["rejected_facts"], [])

    def test_A_calculator_safe_calculate_returns_numeric(self):
        data = {
            "Revenue": _fact(1000.0),
            "Net Profit": _fact(200.0),
        }
        result = FinancialCalculator().safe_calculate(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertIsNotNone(result["calculation"])
        self.assertIn("Profit Margin", result["calculation"])
        self.assertEqual(result["calculation"]["Profit Margin"]["value"], 20.0)

    # ----------------------------------------------------------
    # B — PENDING blocked
    # ----------------------------------------------------------

    def test_B_pending_revenue_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="PENDING"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PENDING")
        self.assertIsNone(result["calculation"])
        self.assertEqual(len(result["rejected_facts"]), 1)

    # ----------------------------------------------------------
    # C — CONFLICTING blocked
    # ----------------------------------------------------------

    def test_C_conflicting_revenue_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="CONFLICTING"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CONFLICTING")

    def test_C_conflict_status_alias_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="CONFLICT"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CONFLICTING")

    # ----------------------------------------------------------
    # D — UNRESOLVED_CONFLICT blocked
    # ----------------------------------------------------------

    def test_D_unresolved_conflict_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="UNRESOLVED_CONFLICT"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "UNRESOLVED_CONFLICT")

    # ----------------------------------------------------------
    # E — REJECTED blocked
    # ----------------------------------------------------------

    def test_E_rejected_fact_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="REJECTED"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "REJECTED")

    # ----------------------------------------------------------
    # F — INSUFFICIENT_EVIDENCE blocked
    # ----------------------------------------------------------

    def test_F_insufficient_evidence_blocked(self):
        data = {
            "Revenue": _fact(1000.0, status="INSUFFICIENT_EVIDENCE"),
            "Net Profit": _fact(200.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INSUFFICIENT_EVIDENCE")

    # ----------------------------------------------------------
    # G — MISSING metric blocked
    # ----------------------------------------------------------

    def test_G_missing_required_metric_blocked(self):
        data = {
            "Revenue": _fact(1000.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertEqual(len(result["rejected_facts"]), 1)

    # ----------------------------------------------------------
    # H — CURRENCY_MISMATCH blocked
    # ----------------------------------------------------------

    def test_H_currency_mismatch_blocked(self):
        data = {
            "Revenue": _fact(1000.0, currency="EUR"),
            "Net Profit": _fact(200.0, currency="USD"),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")

    def test_H_calculator_blocks_eur_usd(self):
        data = {
            "Revenue": _fact(1000.0, currency="EUR"),
            "Net Profit": _fact(200.0, currency="USD"),
        }
        result = FinancialCalculator().safe_calculate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")
        self.assertIsNone(result["calculation"])

    # ----------------------------------------------------------
    # I — PERIOD_MISMATCH blocked
    # ----------------------------------------------------------

    def test_I_period_mismatch_blocked(self):
        data = {
            "Revenue": _fact(1000.0, period="FY2024"),
            "Net Profit": _fact(200.0, period="FY2023"),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PERIOD_MISMATCH")

    # ----------------------------------------------------------
    # J — empty evidence set blocked
    # ----------------------------------------------------------

    def test_J_empty_evidence_blocked(self):
        result = self.gate.check({})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INSUFFICIENT_EVIDENCE")

    def test_J_empty_required_metrics_blocked(self):
        result = self.gate.check({}, [])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "INSUFFICIENT_EVIDENCE")

    # ----------------------------------------------------------
    # K — VERIFIED with valid scale normalization → allowed
    # ----------------------------------------------------------

    def test_K_scale_normalized_allowed(self):
        data = {
            "Revenue": _fact(3457000000000.0, scale="crores", original_value=3457.0,
                             normalized_value=3457000000000.0),
            "Net Profit": _fact(345700000000.0, scale="crores", original_value=345.7,
                                normalized_value=345700000000.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "ALLOWED")

    def test_K_scale_mismatch_rejected(self):
        # value not normalized → gate refuses rather than silently using raw
        data = {
            "Revenue": _fact(3457.0, scale="crores", original_value=3457.0,
                             normalized_value=3457000000000.0),
            "Net Profit": _fact(345700000000.0, scale="crores", original_value=345.7,
                                normalized_value=345700000000.0),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SCALE_MISMATCH")

    # ----------------------------------------------------------
    # L — VERIFIED INR/INR allowed
    # ----------------------------------------------------------

    def test_L_inr_inr_allowed(self):
        data = {
            "Revenue": _fact(12500000000.0, currency="INR", currency_role="REPORTING"),
            "Net Profit": _fact(2500000000.0, currency="INR", currency_role="REPORTING"),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "ALLOWED")

    # ----------------------------------------------------------
    # M — VERIFIED EUR/USD incompatible → blocked (no FX)
    # ----------------------------------------------------------

    def test_M_eur_usd_blocked(self):
        data = {
            "Revenue": _fact(1000.0, currency="EUR", currency_role="REPORTING"),
            "Net Profit": _fact(200.0, currency="USD", currency_role="REPORTING"),
        }
        result = self.gate.check(data, ["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")

    # ----------------------------------------------------------
    # N — no blocked calculation produces a numeric result
    # ----------------------------------------------------------

    def test_N_no_numeric_result_when_blocked(self):
        blocked_cases = [
            {"Revenue": _fact(1000.0, status="PENDING"), "Net Profit": _fact(200.0)},
            {"Revenue": _fact(1000.0), "Net Profit": _fact(200.0, status="CONFLICTING")},
            {"Revenue": _fact(1000.0, currency="EUR"), "Net Profit": _fact(200.0, currency="USD")},
            {"Revenue": _fact(1000.0, period="FY2024"), "Net Profit": _fact(200.0, period="FY2023")},
            {"Revenue": _fact(1000.0), "Net Profit": _fact(200.0, status="REJECTED")},
        ]
        for data in blocked_cases:
            result = FinancialCalculator().safe_calculate(data)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIsNone(result["calculation"])
            self.assertNotIn("Profit Margin", result)

    def test_N_missing_metric_no_numeric_result(self):
        # Revenue present, Net Profit missing (explicit requirement)
        data = {"Revenue": _fact(1000.0)}
        result = FinancialCalculator().safe_calculate(
            data, required_metrics=["Revenue", "Net Profit"]
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "MISSING")
        self.assertIsNone(result["calculation"])

    # ----------------------------------------------------------
    # O — no fallback to raw/unverified values when blocked
    # ----------------------------------------------------------

    def test_O_no_silent_fallback(self):
        # PENDING revenue with a raw value present — must NOT compute
        data = {
            "Revenue": _fact(1000.0, status="PENDING"),
            "Net Profit": _fact(200.0),
        }
        result = FinancialCalculator().safe_calculate(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["calculation"])
        # rejected fact must be surfaced, not silently used
        self.assertEqual(result["rejected_facts"][0]["metric"], "Revenue")

    # ----------------------------------------------------------
    # P — numerics unchanged for valid VERIFIED inputs
    # ----------------------------------------------------------

    def test_P_numerics_unchanged_for_verified(self):
        data = {
            "Revenue": _fact(3457999.0),
            "Net Profit": _fact(113558.0),
            "Equity": _fact(620643.0),
            "Assets": _fact(3897806.0),
            "Liabilities": _fact(3277163.0),
            "Debt": _fact(1452455.0),
        }
        legacy = FinancialCalculator().calculate(data)
        gated = FinancialCalculator().safe_calculate(data)
        self.assertEqual(gated["status"], "ALLOWED")
        for key in legacy:
            self.assertIn(key, gated["calculation"])
            self.assertEqual(gated["calculation"][key]["value"], legacy[key]["value"])


class TestCalculationSafetyGateCanonical(unittest.TestCase):
    """Tests Q–R: orchestrator/canonical integration."""

    # ----------------------------------------------------------
    # Q — canonical set admits VERIFIED only (PENDING leak killed)
    # ----------------------------------------------------------

    def test_Q_pending_evidence_never_enters_canonical(self):
        state = EvidenceSummaryState(max_iterations=1)
        state.add_requirement(InformationRequirement(id="r1", metric="Revenue", period="FY2024"))

        verified = EvidenceItem(
            metric="Revenue", value=1000.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        pending = EvidenceItem(
            metric="NetIncome", value=9999.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="PENDING",
        )
        state.add_evidence(verified)
        state.add_evidence(pending)
        state.evaluate_requirements()

        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=1)
        # Simulate the Phase 5 assembly logic
        canonical = CanonicalEvidenceSet(state.state)
        if orch._check_calculation_block(state):
            for item in state.state.evidence.values():
                if item.verification_status == "VERIFIED" and item.value is not None:
                    canonical.add_resolved(item.to_dict())

        resolved = canonical.to_dict()["resolved_facts"]
        metrics = {r.get("metric") for r in resolved}
        self.assertIn("Revenue", metrics)
        self.assertNotIn("NetIncome", metrics)  # PENDING never admitted
        self.assertEqual(len(resolved), 1)

    def test_Q_pending_954_scenario_blocked(self):
        """The Tata case: 954 PENDING items must not flow as resolved."""
        state = EvidenceSummaryState(max_iterations=3)
        state.add_requirement(InformationRequirement(id="r1", metric="Revenue", period="FY2023"))

        # 954 pending evidence items
        for i in range(954):
            item = EvidenceItem(
                metric="Revenue", value=float(i + 1), reporting_period="FY2023",
                source="sec", source_tier=3, verification_status="PENDING",
            )
            state.add_evidence(item)
        state.evaluate_requirements()

        orch = AgenticRAGOrchestrator(ticker="TTM", max_iterations=3)
        # Requirement is FOUND (pending evidence) not VERIFIED → must block
        blocked = not orch._check_calculation_block(state)
        self.assertTrue(blocked)

        canonical = CanonicalEvidenceSet(state.state)
        if orch._check_calculation_block(state):
            for item in state.state.evidence.values():
                if item.verification_status == "VERIFIED" and item.value is not None:
                    canonical.add_resolved(item.to_dict())

        self.assertEqual(canonical.resolved_count, 0)  # nothing leaked

    # ----------------------------------------------------------
    # R — orchestrator blocks on insufficient evidence / limit
    # ----------------------------------------------------------

    def test_R_insufficient_evidence_blocks(self):
        state = EvidenceSummaryState(max_iterations=1)
        state.set_terminal(STATE_INSUFFICIENT_EVIDENCE, "no evidence found")
        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=1)
        self.assertFalse(orch._check_calculation_block(state))

    def test_R_retrieval_limit_blocks(self):
        state = EvidenceSummaryState(max_iterations=3)
        state.set_terminal(STATE_RETRIEVAL_LIMIT_REACHED, "max iterations reached")
        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=3)
        self.assertFalse(orch._check_calculation_block(state))

    def test_R_missing_requirement_blocks(self):
        state = EvidenceSummaryState(max_iterations=3)
        state.add_requirement(InformationRequirement(id="r1", metric="EBITDA", period="FY2099"))
        state.evaluate_requirements()
        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=3)
        self.assertFalse(orch._check_calculation_block(state))

    def test_R_complete_verified_allows(self):
        state = EvidenceSummaryState(max_iterations=3)
        state.add_requirement(InformationRequirement(id="r1", metric="Revenue", period="FY2024"))
        item = EvidenceItem(
            metric="Revenue", value=1000.0, reporting_period="FY2024",
            source="sec", source_tier=3, verification_status="VERIFIED",
        )
        state.add_evidence(item)
        state.evaluate_requirements()
        orch = AgenticRAGOrchestrator(ticker="AAPL", max_iterations=3)
        self.assertTrue(orch._check_calculation_block(state))

    # ----------------------------------------------------------
    # Module-level gated helper
    # ----------------------------------------------------------

    def test_module_level_gated_helper(self):
        data = {
            "Revenue": _fact(1000.0),
            "Net Profit": _fact(200.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["Profit Margin"]["value"], 20.0)

        data_pending = {
            "Revenue": _fact(1000.0, status="PENDING"),
            "Net Profit": _fact(200.0),
        }
        result2 = safe_calculate_financial_ratios(data_pending)
        self.assertEqual(result2["status"], "BLOCKED")
        self.assertIsNone(result2["calculation"])


if __name__ == "__main__":
    unittest.main()
