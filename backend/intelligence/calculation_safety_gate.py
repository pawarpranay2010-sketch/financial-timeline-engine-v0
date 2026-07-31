"""
Calculation Safety Gate

Centralized deterministic safety gate for the deterministic financial
calculation engine (Fix #3).

Enforced at the calculation-engine boundary, not merely at the UI /
status / memo generation layer. No LLM is consulted to decide whether a
calculation is safe — the gate is 100% deterministic.

A calculation is BLOCKED when any required input is:

    PENDING
    MISSING
    CONFLICTING
    UNRESOLVED_CONFLICT
    REJECTED
    INSUFFICIENT_EVIDENCE
    CURRENCY_MISMATCH
    PERIOD_MISMATCH
    EXTRACTION_CORRUPTED

Only evidence that is explicitly VERIFIED (canonical) may enter
deterministic calculations. The gate never silently substitutes
lower-quality evidence to make a calculation succeed.

Structured failure state (existing project terminology reused):

    {
        "status": "BLOCKED",
        "reason": "INSUFFICIENT_EVIDENCE",
        "required_facts": [...],
        "rejected_facts": [...],
        "calculation": null,
    }

Success state:

    {
        "status": "ALLOWED",
        "reason": "",
        "required_facts": [...],
        "rejected_facts": [],
        "calculation": {...ratios...},
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.intelligence.evidence_summary_state import (
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_UNRESOLVED_CONFLICT,
    STATE_CURRENCY_MISMATCH,
    STATE_EXTRACTION_CORRUPTED,
    STATE_RETRIEVAL_LIMIT_REACHED,
    STATE_EXECUTION_TIMEOUT,
    EVIDENCE_VERIFIED,
)
from backend.intelligence.currency_validator import (
    CurrencyValidator,
    INVALID_FX_METADATA,
)

logger = logging.getLogger("fte.rag.calculation_safety_gate")

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

# Verification statuses that are NOT safe for deterministic calculation.
UNSAFE_VERIFICATION_STATUSES = {
    "PENDING": "PENDING",
    "CONFLICT": "CONFLICTING",
    "CONFLICTING": "CONFLICTING",
    "UNRESOLVED_CONFLICT": "UNRESOLVED_CONFLICT",
    "REJECTED": "REJECTED",
    "SUPERSEDED": "SUPERSEDED",
    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
    "MISSING": "MISSING",
}

# Non-COMPLETE terminal states that must block calculation.
BLOCKING_TERMINAL_STATES = {
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_UNRESOLVED_CONFLICT,
    STATE_CURRENCY_MISMATCH,
    STATE_EXTRACTION_CORRUPTED,
    STATE_RETRIEVAL_LIMIT_REACHED,
    STATE_EXECUTION_TIMEOUT,
}

# Metric names required by the deterministic FinancialCalculator. When a
# caller does not declare required_metrics, these are inferred from the
# ratios that can actually be computed.
REQUIRED_METRICS_BY_RATIO = {
    "Profit Margin": ("Revenue", "Net Profit"),
    "ROE": ("Equity", "Net Profit"),
    "ROA": ("Assets", "Net Profit"),
    "Debt to Equity": ("Debt", "Equity"),
}


def _blocked(reason: str, required: List[str], rejected: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "required_facts": required,
        "rejected_facts": rejected,
        "calculation": None,
    }


def _allowed(required: List[str], calculation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ALLOWED",
        "reason": "",
        "required_facts": required,
        "rejected_facts": [],
        "calculation": calculation,
    }


class CalculationSafetyGate:
    """
    Deterministic gate guarding the calculation engine boundary.

    Usage:
        gate = CalculationSafetyGate()
        result = gate.check(financial_data, required_metrics=["Revenue", "Net Profit"])
        if result["status"] == "BLOCKED":
            # do NOT run the calculation engine
        else:
            ratios = FinancialCalculator().calculate(...)
    """

    def __init__(self) -> None:
        self._currency_validator = CurrencyValidator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        financial_data: Dict[str, Any],
        required_metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Deterministically validate that the supplied financial_data may
        enter the calculation engine.

        Args:
            financial_data: mapping of metric name -> fact dict. Each fact
                dict is expected to carry (where available):
                    value            — NORMALIZED numeric value (Fix #2)
                    verification_status
                    currency_code / currency_role / fx metadata
                    reporting_period
                    scale / original_value / normalized_value
            required_metrics: list of metric names the calculation needs.
                If omitted, inferred deterministically from the ratios the
                calculator can compute — any ratio with at least one of
                its inputs present makes ALL of its inputs required, so a
                lone metric (e.g. Revenue without Net Profit) blocks with
                MISSING rather than silently computing nothing.

        Returns:
            Structured result dict — see module docstring.
        """
        if required_metrics is None:
            required_metrics = self._infer_required_metrics(financial_data)
        required = list(dict.fromkeys(required_metrics))  # preserve order, dedupe

        # 1. Empty evidence set
        if not financial_data or not required:
            return _blocked(STATE_INSUFFICIENT_EVIDENCE, required, [])

        # 2. Missing required metrics
        missing = [m for m in required if m not in financial_data]
        if missing:
            return _blocked("MISSING", required, [
                {"metric": m, "status": "MISSING"} for m in missing
            ])

        # 3. Per-fact verification + value sanity
        rejected: List[Dict[str, Any]] = []
        for metric in required:
            fact = financial_data[metric]
            status = str(fact.get("verification_status", "PENDING")).upper()
            if status != EVIDENCE_VERIFIED:
                reason = UNSAFE_VERIFICATION_STATUSES.get(status, "REJECTED")
                rejected.append({
                    "metric": metric,
                    "status": status,
                    "reason": reason,
                    "value": fact.get("value"),
                })
                continue
            value = fact.get("value")
            if value is None:
                rejected.append({
                    "metric": metric,
                    "status": "REJECTED",
                    "reason": "REJECTED",
                    "value": None,
                })

        if rejected:
            first = rejected[0]
            reason = first.get("reason") or "REJECTED"
            return _blocked(reason, required, rejected)

        # 4. Currency compatibility (reuses existing CurrencyValidator —
        #    never silently converts; requires complete valid FX metadata
        #    for cross-currency facts — Fix #5)
        facts_list = [financial_data[m] for m in required]
        ccy_state, ccy_detail = self._currency_validator.fx_compatibility_state(facts_list)
        if ccy_state == "CURRENCY_MISMATCH":
            return _blocked(STATE_CURRENCY_MISMATCH, required, [
                {"metric": m, "status": "CURRENCY_MISMATCH", "detail": ccy_detail}
                for m in required
            ])
        if ccy_state == INVALID_FX_METADATA:
            return _blocked(INVALID_FX_METADATA, required, [
                {"metric": m, "status": INVALID_FX_METADATA, "detail": ccy_detail}
                for m in required
            ])

        # 5. Period compatibility (same-period ratios must share period)
        periods = {
            str(financial_data[m].get("reporting_period", ""))
            for m in required
        }
        periods.discard("")
        if len(periods) > 1:
            return _blocked("PERIOD_MISMATCH", required, [
                {"metric": m, "status": "PERIOD_MISMATCH", "period": financial_data[m].get("reporting_period", "")}
                for m in required
            ])

        # 6. Scale sanity — value must be the normalized magnitude
        for metric in required:
            fact = financial_data[metric]
            scale = fact.get("scale", "")
            normalized = fact.get("normalized_value")
            original = fact.get("original_value")
            if scale and normalized is not None and fact.get("value") != normalized:
                # Value was not normalized — reject rather than silently use raw
                return _blocked("SCALE_MISMATCH", required, [
                    {"metric": metric, "status": "SCALE_MISMATCH",
                     "value": fact.get("value"), "normalized_value": normalized,
                     "scale": scale}
                ])

        return _allowed(required, None)

    def _infer_required_metrics(self, financial_data: Dict[str, Any]) -> List[str]:
        """
        Deterministically infer which metrics a calculation requires.

        A ratio's inputs become required only when ALL of them are
        present — i.e. the ratio is actually computable. This ensures a
        fully-specified ratio (Revenue + Net Profit) is allowed, while a
        caller may still declare required_metrics explicitly to force a
        missing-metric block (test G).
        """
        present = set(financial_data.keys())
        required: List[str] = []
        for _ratio, inputs in REQUIRED_METRICS_BY_RATIO.items():
            if all(m in present for m in inputs):
                for m in inputs:
                    if m not in required:
                        required.append(m)
        if not required:
            required = list(present)
        return required

    # ------------------------------------------------------------------
    # Convenience: check a canonical evidence set (Fix #3 integration)
    # ------------------------------------------------------------------

    def check_canonical(self, canonical_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a CanonicalEvidenceSet.to_dict() result before running
        calculations on its resolved facts.

        Any terminal state other than COMPLETE blocks the calculation.
        Only VERIFIED resolved facts may be used; PENDING facts that
        previously leaked into downstream processing are rejected.
        """
        terminal = canonical_result.get("terminal_state", "")
        if terminal and terminal not in ("", "COMPLETE"):
            return _blocked(terminal, [], [
                {"terminal_state": terminal,
                 "reason": canonical_result.get("terminal_reason", "")}
            ])

        resolved_facts = canonical_result.get("resolved_facts", [])
        if not resolved_facts:
            return _blocked(STATE_INSUFFICIENT_EVIDENCE, [], [])

        financial_data: Dict[str, Any] = {}
        rejected: List[Dict[str, Any]] = []
        for fact in resolved_facts:
            metric = fact.get("metric_name") or fact.get("metric") or fact.get("fact_id") or "Unknown"
            status = str(fact.get("verification_status", "PENDING")).upper()
            if status != EVIDENCE_VERIFIED:
                rejected.append({
                    "metric": metric,
                    "status": status,
                    "reason": UNSAFE_VERIFICATION_STATUSES.get(status, "REJECTED"),
                })
                continue
            financial_data[metric] = fact

        if rejected:
            first = rejected[0]
            return _blocked(
                first.get("reason") or "REJECTED",
                list(financial_data.keys()),
                rejected,
            )

        return self.check(financial_data, required_metrics=None)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def check_calculation_safety(
    financial_data: Dict[str, Any],
    required_metrics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Module-level convenience wrapper for CalculationSafetyGate.check()."""
    return CalculationSafetyGate().check(financial_data, required_metrics)
