"""
Fix #5 — FX Metadata Validation & Currency Safety

Verifies deterministic currency/FX validation across the pipeline:

    ExtractedFact → EvidenceItem → EvidenceSummaryState →
    CanonicalEvidenceSet → CalculationSafetyGate → FinancialCalculator

Coverage (per Fix #5 requirements):

A. Same-currency compatibility (USD/USD, INR/INR, EUR/EUR)
B. Currency mismatch (EUR/USD, INR/USD, USD/GBP)
C. Role handling (same code different roles NOT a conflict;
   different codes different roles IS a conflict)
D. FX metadata completeness (valid / missing rate / missing source /
   missing timestamp / zero / negative / NaN / infinity / malformed)
E. Explicit conversion preserves original fact + audit trail
F. Ratio safety — EUR revenue / USD income BLOCKED; USD/USD ALLOWED
G. Regression protection — metadata survives every boundary; XBRL
   unit currency sanitization; freshness hook states; dedup hash
   keeps role-differentiated facts distinct
"""

from __future__ import annotations

import math
import os
import sys
import unittest

# Ensure the project root is on the Python path (same bootstrap as other suites)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta

from backend.intelligence.currency_validator import (
    CurrencyValidator,
    INVALID_FX_METADATA,
    FX_METADATA_VALID,
    FX_FRESHNESS_UNCONFIGURED,
    FX_STALE,
    FX_FRESH,
)
from backend.intelligence.evidence_summary_state import EvidenceItem, EvidenceSummaryState
from backend.intelligence.calculation_safety_gate import CalculationSafetyGate
from backend.financial_calculator import safe_calculate_financial_ratios

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FX_TS = "2026-01-01T00:00:00Z"


def fact(metric, value, ccy="USD", role="REPORTING", verified=True, **extra):
    """Build a canonical VERIFIED fact dict (EvidenceItem-shaped)."""
    f = {
        "metric": metric,
        "value": value,
        "normalized_value": value,
        "original_value": value,
        "scale": "unit",
        "currency_code": ccy,
        "currency_role": role,
        "fx_rate": None,
        "fx_source": "",
        "fx_timestamp": None,
        "reporting_period": "FY2025",
        "verification_status": "VERIFIED" if verified else "PENDING",
        "source": "test",
        "source_tier": 3,
    }
    f.update(extra)
    return f


def fx(rate=1.08, source="ECB", ts=FX_TS):
    """FX metadata kwargs for a fact dict."""
    return {"fx_rate": rate, "fx_source": source, "fx_timestamp": ts}


# ---------------------------------------------------------------------------
# A. Same-currency compatibility
# ---------------------------------------------------------------------------


class TestSameCurrencyCompatibility(unittest.TestCase):
    def test_usd_usd_compatible(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "USD"), fact("Net Profit", 10, "USD"),
        ])
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_inr_inr_compatible(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "INR"), fact("Net Profit", 10, "INR"),
        ])
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_eur_eur_compatible(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "EUR"), fact("Net Profit", 10, "EUR"),
        ])
        self.assertTrue(ok)
        self.assertIsNone(err)


# ---------------------------------------------------------------------------
# B. Currency mismatch
# ---------------------------------------------------------------------------


class TestCurrencyMismatch(unittest.TestCase):
    def test_eur_usd_mismatch(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "EUR"), fact("Net Profit", 10, "USD"),
        ])
        self.assertFalse(ok)
        self.assertIn("CURRENCY_MISMATCH", err or "")

    def test_inr_usd_mismatch(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "INR"), fact("Net Profit", 10, "USD"),
        ])
        self.assertFalse(ok)
        self.assertIn("CURRENCY_MISMATCH", err or "")

    def test_usd_gbp_mismatch(self):
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "USD"), fact("Net Profit", 10, "GBP"),
        ])
        self.assertFalse(ok)
        self.assertIn("CURRENCY_MISMATCH", err or "")

    def test_operation_currency_mismatch(self):
        ok, err = CurrencyValidator.check_operation_currency(
            fact("Revenue", 100, "EUR"), fact("Net Profit", 10, "USD"), "divide",
        )
        self.assertFalse(ok)
        self.assertIn("CURRENCY_MISMATCH", err or "")


# ---------------------------------------------------------------------------
# C. Role handling
# ---------------------------------------------------------------------------


class TestRoleHandling(unittest.TestCase):
    def test_same_currency_different_roles_not_a_conflict(self):
        """USD REPORTING vs USD FUNCTIONAL must NOT be a conflict (Case H)."""
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "USD", "REPORTING"),
            fact("Net Profit", 10, "USD", "FUNCTIONAL"),
        ])
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_different_codes_different_roles_blocked(self):
        """EUR REPORTING vs USD FUNCTIONAL — blocked (different roles)."""
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "EUR", "REPORTING"),
            fact("Net Profit", 10, "USD", "FUNCTIONAL"),
        ])
        self.assertFalse(ok)
        self.assertIn("CURRENCY_MISMATCH", err or "")

    def test_roles_remain_semantically_distinct(self):
        """The five roles must not collapse into one generic string."""
        from backend.intelligence.currency_validator import ALL_ROLES
        self.assertEqual(
            ALL_ROLES,
            {"REPORTING", "FUNCTIONAL", "PRESENTATION", "TRANSACTION", "TAX"},
        )


# ---------------------------------------------------------------------------
# D. FX metadata validation
# ---------------------------------------------------------------------------


class TestFxMetadataValidation(unittest.TestCase):
    def _state(self, left, right):
        return CurrencyValidator.fx_compatibility_state([left, right])

    def test_valid_metadata_compatible(self):
        state, detail = self._state(
            fact("Revenue", 100, "EUR", **fx()),
            fact("Net Profit", 10, "USD", **fx(0.93, "ECB")),
        )
        self.assertEqual(state, "COMPATIBLE")

    def test_missing_rate_rejected(self):
        state, detail = self._state(
            fact("Revenue", 100, "EUR", fx_rate=None, fx_source="ECB", fx_timestamp=FX_TS),
            fact("Net Profit", 10, "USD", **fx(0.93, "ECB")),
        )
        self.assertEqual(state, INVALID_FX_METADATA)
        self.assertIn("missing fx_rate", detail or "")

    def test_missing_source_rejected(self):
        state, detail = self._state(
            fact("Revenue", 100, "EUR", fx_rate=1.08, fx_source="", fx_timestamp=FX_TS),
            fact("Net Profit", 10, "USD", **fx(0.93, "ECB")),
        )
        self.assertEqual(state, INVALID_FX_METADATA)
        self.assertIn("missing fx_source", detail or "")

    def test_missing_timestamp_rejected(self):
        state, detail = self._state(
            fact("Revenue", 100, "EUR", fx_rate=1.08, fx_source="ECB", fx_timestamp=None),
            fact("Net Profit", 10, "USD", **fx(0.93, "ECB")),
        )
        self.assertEqual(state, INVALID_FX_METADATA)
        self.assertIn("missing fx_timestamp", detail or "")

    def test_zero_rate_rejected(self):
        ok, reason = CurrencyValidator.validate_fx_rate(0.0)
        self.assertFalse(ok)
        self.assertIn("positive", reason)

    def test_negative_rate_rejected(self):
        ok, reason = CurrencyValidator.validate_fx_rate(-1.5)
        self.assertFalse(ok)
        self.assertIn("positive", reason)

    def test_nan_rate_rejected(self):
        ok, reason = CurrencyValidator.validate_fx_rate(float("nan"))
        self.assertFalse(ok)
        self.assertIn("finite", reason)

    def test_infinity_rate_rejected(self):
        ok, reason = CurrencyValidator.validate_fx_rate(float("inf"))
        self.assertFalse(ok)
        self.assertIn("finite", reason)

    def test_malformed_rate_rejected(self):
        ok, reason = CurrencyValidator.validate_fx_rate("abc")
        self.assertFalse(ok)
        self.assertIn("not numeric", reason)

    def test_numeric_string_rate_accepted(self):
        ok, reason = CurrencyValidator.validate_fx_rate("1.08")
        self.assertTrue(ok)

    def test_gate_returns_invalid_fx_metadata_reason(self):
        """CalculationSafetyGate must surface INVALID_FX_METADATA distinctly."""
        gate = CalculationSafetyGate()
        result = gate.check({
            "Revenue": fact("Revenue", 100, "EUR", fx_rate=0.0, fx_source="ECB", fx_timestamp=FX_TS),
            "Net Profit": fact("Net Profit", 10, "USD", **fx(0.93, "ECB")),
        }, required_metrics=["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], INVALID_FX_METADATA)


# ---------------------------------------------------------------------------
# E. FX conversion — auditability preserved
# ---------------------------------------------------------------------------


class TestFxConversion(unittest.TestCase):
    def test_valid_eur_to_usd_conversion(self):
        original = fact("Revenue", 100, "EUR", **fx(1.08, "ECB"))
        converted = CurrencyValidator.convert_fact(original, "USD")

        # Original fact untouched
        self.assertEqual(original["value"], 100)
        self.assertEqual(original["currency_code"], "EUR")

        # Converted value
        self.assertEqual(converted["value"], 108.0)
        self.assertEqual(converted["currency_code"], "USD")

        # Conversion metadata preserved
        conv = converted["fx_conversion"]
        self.assertEqual(conv["rate"], 1.08)
        self.assertEqual(conv["source"], "ECB")
        self.assertEqual(conv["target_currency"], "USD")
        self.assertEqual(conv["original_value"], 100)
        self.assertEqual(conv["original_currency"], "EUR")
        self.assertEqual(conv["converted_value"], 108.0)

        # Audit trail — original fact retained on the converted record
        self.assertEqual(converted["original_fact"]["value"], 100)
        self.assertEqual(converted["original_fact"]["currency_code"], "EUR")

    def test_conversion_invalid_metadata_raises(self):
        original = fact("Revenue", 100, "EUR", **fx(1.08, "ECB", None))
        with self.assertRaises(ValueError):
            CurrencyValidator.convert_fact(original, "USD")

    def test_conversion_never_automatic(self):
        """No auto-conversion: cross-currency facts without metadata block."""
        ok, err = CurrencyValidator.check_currency_compatibility([
            fact("Revenue", 100, "EUR"), fact("Net Profit", 10, "USD"),
        ])
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# F. Ratio safety through the calculation safety gate
# ---------------------------------------------------------------------------


class TestRatioSafety(unittest.TestCase):
    def test_eur_revenue_usd_income_blocked(self):
        result = safe_calculate_financial_ratios({
            "Revenue": fact("Revenue", 100, "EUR"),
            "Net Profit": fact("Net Profit", 10, "USD"),
        }, required_metrics=["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")
        self.assertIsNone(result["calculation"])

    def test_usd_revenue_usd_income_allowed(self):
        result = safe_calculate_financial_ratios({
            "Revenue": fact("Revenue", 100, "USD"),
            "Net Profit": fact("Net Profit", 10, "USD"),
        }, required_metrics=["Revenue", "Net Profit"])
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["Profit Margin"]["value"], 10.0)

    def test_roe_currency_blocked(self):
        result = safe_calculate_financial_ratios({
            "Equity": fact("Equity", 50, "EUR"),
            "Net Profit": fact("Net Profit", 10, "USD"),
        }, required_metrics=["Equity", "Net Profit"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")

    def test_debt_equity_mixed_blocked(self):
        result = safe_calculate_financial_ratios({
            "Debt": fact("Debt", 40, "USD"),
            "Equity": fact("Equity", 50, "GBP"),
        }, required_metrics=["Debt", "Equity"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")


# ---------------------------------------------------------------------------
# G. Regression protection — metadata survives every boundary
# ---------------------------------------------------------------------------


class TestBoundaryPropagation(unittest.TestCase):
    def test_evidence_item_holds_fx_fields(self):
        item = EvidenceItem(
            metric="Revenue", value=100, currency_code="EUR",
            currency_role="REPORTING", fx_rate=1.08, fx_source="ECB",
            fx_timestamp=FX_TS,
        )
        d = item.to_dict()
        self.assertEqual(d["fx_rate"], 1.08)
        self.assertEqual(d["fx_source"], "ECB")
        self.assertEqual(d["fx_timestamp"], FX_TS)
        self.assertEqual(d["currency_role"], "REPORTING")

    def test_extractor_mapping_preserves_fx_fields(self):
        from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
        fact_dict = {
            "metric_id": "Revenue", "metric_name": "Revenue",
            "metric_value": 100, "normalized_value": 100,
            "unit": "", "scale": "unit",
            "currency_code": "EUR", "currency_role": "REPORTING",
            "fx_rate": 1.08, "fx_source": "ECB", "fx_timestamp": FX_TS,
            "fiscal_period": "FY2025", "accounting_basis": "", "scope": "",
            "source": "test", "source_tier": 3, "source_url": "",
            "evidence_text_anchor": "p1", "confidence_score": 0.9,
            "verification_status": "PENDING", "page": 1,
        }
        ev = FinancialExtractorV2.to_evidence_item_dict(fact_dict)
        self.assertEqual(ev["fx_rate"], 1.08)
        self.assertEqual(ev["fx_source"], "ECB")
        self.assertEqual(ev["fx_timestamp"], FX_TS)
        self.assertEqual(ev["currency_code"], "EUR")

    def test_xbrl_unit_currency_sanitized(self):
        from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
        self.assertEqual(FinancialExtractorV2._currency_from_unit("INR"), "INR")
        self.assertEqual(FinancialExtractorV2._currency_from_unit("USD/shares"), "USD")
        self.assertEqual(FinancialExtractorV2._currency_from_unit("EUR/share"), "EUR")
        self.assertEqual(FinancialExtractorV2._currency_from_unit("shares"), "")
        self.assertEqual(FinancialExtractorV2._currency_from_unit("pure"), "")
        self.assertEqual(FinancialExtractorV2._currency_from_unit(""), "")

    def test_evidence_state_roundtrip_preserves_fx(self):
        state = EvidenceSummaryState()
        item = EvidenceItem(
            metric="Revenue", value=100, currency_code="EUR",
            currency_role="REPORTING", fx_rate=1.08, fx_source="ECB",
            fx_timestamp=FX_TS,
        )
        state.add_evidence(item)
        stored = state.state.evidence[item.evidence_hash]
        d = stored.to_dict()
        self.assertEqual(d["fx_rate"], 1.08)
        self.assertEqual(d["fx_source"], "ECB")
        self.assertEqual(d["fx_timestamp"], FX_TS)

    def test_freshness_hook_states(self):
        info_ts = CurrencyValidator.validate_fact_currency(
            {"currency_code": "EUR", "fx_rate": 1.08, "fx_source": "ECB",
             "fx_timestamp": FX_TS}
        )
        # No policy → deterministic FRESHNESS_UNCONFIGURED state (no invented threshold)
        self.assertEqual(
            CurrencyValidator.check_fx_freshness(info_ts), FX_FRESHNESS_UNCONFIGURED
        )
        # With policy → FRESH (recent) / STALE (old)
        self.assertEqual(CurrencyValidator.check_fx_freshness(info_ts, 86400 * 365 * 10), FX_FRESH)
        self.assertEqual(CurrencyValidator.check_fx_freshness(info_ts, 60), FX_STALE)
        # Missing timestamp with policy → STALE
        no_ts = CurrencyValidator.validate_fact_currency(
            {"currency_code": "EUR", "fx_rate": 1.08, "fx_source": "ECB"}
        )
        self.assertEqual(CurrencyValidator.check_fx_freshness(no_ts, 60), FX_STALE)

    def test_dedup_hash_keeps_role_differentiated_facts_distinct(self):
        """Same metric/value/currency but different roles must NOT dedup."""
        a = EvidenceItem(metric="Revenue", value=100, currency_code="USD",
                         currency_role="REPORTING")
        b = EvidenceItem(metric="Revenue", value=100, currency_code="USD",
                         currency_role="FUNCTIONAL")
        ha = EvidenceSummaryState.compute_evidence_hash(a.to_dict())
        hb = EvidenceSummaryState.compute_evidence_hash(b.to_dict())
        self.assertNotEqual(ha, hb)

    def test_same_fact_duplicate_still_suppressed(self):
        """Identical facts (incl. FX metadata) still dedup."""
        a = EvidenceItem(metric="Revenue", value=100, currency_code="EUR",
                         currency_role="REPORTING", fx_rate=1.08, fx_source="ECB",
                         fx_timestamp=FX_TS)
        b = EvidenceItem(metric="Revenue", value=100, currency_code="EUR",
                         currency_role="REPORTING", fx_rate=1.08, fx_source="ECB",
                         fx_timestamp=FX_TS)
        ha = EvidenceSummaryState.compute_evidence_hash(a.to_dict())
        hb = EvidenceSummaryState.compute_evidence_hash(b.to_dict())
        self.assertEqual(ha, hb)


if __name__ == "__main__":
    unittest.main()
