"""
Financial Timeline Engine
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening
backend/maths/forensic_reconciliation.py

Forensic cross-statement reconciliation registry (expansion of 12B).

New deterministic identities (all registered declaratively on the 12B
ReconciliationEngine - no engine changes):

    BS_IDENTITY_ASSETS           Total Assets = Liabilities + Equity
    CF_CASH_RECONCILIATION       Ending Cash = Beginning Cash
                                     + CFO + CFI + CFF + FX Effect
    WC_RECONCILIATION            Working Capital
                                     = Current Assets - Current Liabilities
    RE_STRAP_NET_PROFIT          (12B default, retained)
    CF_IDENTITY_NET_PROFIT       (12B default, retained)

Rules (inherited from the 12B engine and always enforced):
    * tolerance explicit; variance stored
    * source values immutable; no smoothing / averaging / replacement
    * reconciliation conflict -> REVIEW_REQUIRED
    * missing components -> BLOCKED
    * every result carries complete provenance and lineage

Identity-aware matching: the forensic layer additionally rejects rules
whose sources are not identity-compatible (entity / statement / period /
currency) via backend/maths/fact_identity.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.fact_model import from_pipeline_fact
from backend.maths.identity import (
    differing_dimensions,
    same_identity,
)
from backend.maths.reconciliation import (
    ReconciliationEngine,
    ReconciliationRegistry,
    ReconciliationRule,
    ReconciliationResult,
    build_default_rules,
)

TOTAL_ASSETS = "Total Assets"
LIABILITIES = "Liabilities"
EQUITY = "Equity"
BEGINNING_CASH = "Beginning Cash"
END_CASH = "Ending Cash"
CFO = "Cash from Operating Activities"
CFI = "Cash from Investing Activities"
CFF = "Cash from Financing Activities"
FX_EFFECT = "FX Effect"
CURRENT_ASSETS = "Current Assets"
CURRENT_LIABILITIES = "Current Liabilities"
WORKING_CAPITAL = "Working Capital"


def build_forensic_rules() -> ReconciliationRegistry:
    """The Sprint 12D forensic registry: 12B defaults + new identities."""
    reg = build_default_rules()  # fresh registry, 12B rules intact
    reg.register(ReconciliationRule(
        rule_id="BS_IDENTITY_ASSETS",
        target=TOTAL_ASSETS,
        expected_expression="Liabilities + Equity",
        sources=[LIABILITIES, EQUITY],
        period_mode="same",
        tolerance_rel=Decimal("0.01"),
        unit_kind="amount",
        description=(
            "Balance-sheet identity: Total Assets = Liabilities + Equity."
        ),
        version="1.0",
        source_ref="Accounting identity: Assets = Liabilities + Equity",
    ))
    reg.register(ReconciliationRule(
        rule_id="CF_CASH_RECONCILIATION",
        target=END_CASH,
        expected_expression=(
            "Beginning Cash + Cash from Operating Activities "
            "+ Cash from Investing Activities + Cash from Financing "
            "Activities + FX Effect"
        ),
        sources=[
            BEGINNING_CASH, CFO, CFI, CFF, FX_EFFECT,
        ],
        period_mode="same",
        tolerance_rel=Decimal("0.02"),
        unit_kind="amount",
        description=(
            "Cash-flow identity: Ending Cash = Beginning Cash + CFO + CFI "
            "+ CFF + FX Effect."
        ),
        version="1.0",
        source_ref="Cash flow statement identity",
    ))
    reg.register(ReconciliationRule(
        rule_id="WC_RECONCILIATION",
        target=WORKING_CAPITAL,
        expected_expression="Current Assets - Current Liabilities",
        sources=[CURRENT_ASSETS, CURRENT_LIABILITIES],
        period_mode="same",
        tolerance_rel=Decimal("0.01"),
        unit_kind="amount",
        description="Working Capital = Current Assets - Current Liabilities.",
        version="1.0",
        source_ref="Working-capital identity",
    ))
    return reg


FORENSIC_RECONCILIATION_REGISTRY = build_forensic_rules()


class ForensicReconciliationEngine:
    """Deterministic forensic reconciliation over the 12B engine with
    identity-aware matching."""

    def __init__(self, registry: Optional[ReconciliationRegistry] = None,
                 prefer_cpp: bool = True) -> None:
        self.registry = (
            registry if registry is not None
            else FORENSIC_RECONCILIATION_REGISTRY
        )
        self.engine = ReconciliationEngine(self.registry,
                                           prefer_cpp=prefer_cpp)

    # ------------------------------------------------------------------
    def reconcile(self, rule_id: str, reported_fact: Dict[str, Any],
                  facts: Dict[str, Any],
                  tolerance_rel: Optional[Decimal] = None,
                  tolerance_abs: Optional[Decimal] = None,
                  reported_statement: Optional[str] = None,
                  ) -> ReconciliationResult:
        """Identity-gated reconciliation for one registered rule."""
        rule = self.registry.require(rule_id)
        problem = self._identity_gate(rule, reported_fact, facts)
        if problem is not None:
            kind, reason = problem
            result = ReconciliationResult(
                reconciliation_id=f"REC-{rule.rule_id}",
                target=rule.target,
                rule_id=rule.rule_id,
                expected_relationship=(
                    f"{rule.expected_expression}  [{rule.description}]"
                ),
                status=("BLOCKED" if kind == "missing"
                        else "REVIEW_REQUIRED"),
                reason=reason,
            )
            return result
        return self.engine.reconcile(
            rule, reported_fact, facts,
            tolerance_rel=tolerance_rel,
            tolerance_abs=tolerance_abs,
            reported_statement=reported_statement,
        )

    # ------------------------------------------------------------------
    def reconcile_identity(self, rule_id: str,
                           reported_fact: Dict[str, Any],
                           facts: Dict[str, Any],
                           tolerance_rel: Decimal = Decimal("0.01"),
                           ) -> ReconciliationResult:
        """Convenience with a documented default tolerance."""
        return self.reconcile(
            rule_id, reported_fact, facts,
            tolerance_rel=tolerance_rel,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _identity_gate(rule: ReconciliationRule,
                       reported_fact: Dict[str, Any],
                       facts: Dict[str, Any]) -> Optional[tuple]:
        """Fail closed when the rule's sources are not identity-
        compatible with the reported fact (entity/statement/period/
        currency). Uses backend/maths/identity (Sprint 12D) semantics.
        Returns (kind, reason) or None."""
        rid = rule.target
        base = from_pipeline_fact(rid, reported_fact)
        has_identity = any([
            base.entity, base.statement, base.period, base.period_type,
            base.currency,
        ])
        if not has_identity:
            return ("missing", f"{rid}: reported fact has no identity "
                               "metadata - cannot gate safely.")
        for src in rule.sources:
            f = (facts or {}).get(src)
            if not isinstance(f, dict):
                continue
            other = from_pipeline_fact(src, f)
            if not same_identity(base, other):
                dims = differing_dimensions(base, other)
                return ("discrepancy",
                        f"IDENTITY GATE: {src} differs on "
                        f"{', '.join(dims)} - never combined silently.")
        return None
