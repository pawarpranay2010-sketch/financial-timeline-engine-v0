"""
Financial Timeline Engine
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening
backend/maths/recovery.py

Production evidence recovery (Sprint 12D section A).

A deterministic orchestrator over the existing 4-tier hierarchy:

    Tier 1  uploaded primary document              (DOCUMENT)
    Tier 2  user-uploaded parent/appendix docs     (APPENDIX)
    Tier 3  approved regulatory / structured APIs  (REGULATORY_API,
                                                    EXTERNAL_DERIVED)
    Tier 4  everything else = FORBIDDEN

Rules (all deterministic, all enforced):
  1. Never silently search the open web.
  2. Never substitute an unapproved source.
  3. Never replace an existing fact merely because an external source
     disagrees - both values are preserved.
  4. Every external fact retains provider / source identifier / retrieval
     timestamp / raw value / normalized value / unit / currency / period /
     provenance status.
  5. External evidence begins UNVERIFIED until the provenance gate
     validates it.
  6. Conflicting evidence preserves BOTH values.
  7. Conflicting evidence produces REVIEW_REQUIRED / EVIDENCE_CONFLICT.
  8. Missing evidence produces BLOCKED - never an invented value.

Recovery is tier-priority (Tier 1 > Tier 2 > Tier 3). Within one tier,
identical values choose the first candidate deterministically (sorted by
source label); different values are a conflict (both preserved, status
REVIEW_REQUIRED, chosen = None).

Pure module: no Streamlit, no AI, no network (except explicitly provided
retrieval timestamps). Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.evidence import (
    ALLOWED_SOURCE_TIERS,
    ExternalEvidenceRecord,
    external_record_from_fact,
    is_allowed_source,
    tier_of,
)
from backend.maths.provenance import GATE_BLOCKED, GATE_PASS, ProvenanceGate
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

# ---------------------------------------------------------------------------
# Recovery states
# ---------------------------------------------------------------------------

RECOVERED = "RECOVERED"                 # single approved analytical fact
CONFLICT = "EVIDENCE_CONFLICT"          # approved sources disagree
BLOCKED = "BLOCKED"                     # no approved evidence
MISSING = "MISSING"                     # no source supplied at all


@dataclass
class RecoveryResult:
    """Deterministic outcome of one evidence recovery request."""

    metric: str
    status: str = MISSING
    value: Optional[Decimal] = None
    chosen: Optional[Dict[str, Any]] = None
    tier: Optional[int] = None
    source_tier: Optional[str] = None
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    external_record: Optional[ExternalEvidenceRecord] = None
    retrieval_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "status": self.status,
            "value": float(self.value) if self.value is not None else None,
            "chosen": self.chosen,
            "tier": self.tier,
            "source_tier": self.source_tier,
            "conflicts": list(self.conflicts),
            "candidates": list(self.candidates),
            "reason": self.reason,
            "external_record": (
                self.external_record.to_dict()
                if self.external_record else None
            ),
            "retrieval_timestamp": self.retrieval_timestamp,
        }


class EvidenceRecoveryEngine:
    """Deterministic evidence recovery over the approved tier hierarchy."""

    def __init__(self, gate: Optional[ProvenanceGate] = None) -> None:
        self.gate = gate if gate is not None else ProvenanceGate()

    # ------------------------------------------------------------------
    def recover(self, metric: str, sources: Dict[str, Any],
                reference: Optional[Dict[str, Any]] = None,
                retrieval_timestamp: str = "") -> RecoveryResult:
        """Recover the analytical fact for `metric` from supplied sources.

        `sources` is a {label: fact_dict} map. Forbidden tiers are
        rejected outright; approved sources are tier-prioritized; equal
        values within the best tier choose the first candidate
        deterministically; different values are a preserved conflict.
        """
        result = RecoveryResult(metric=metric,
                                retrieval_timestamp=retrieval_timestamp)
        if not sources:
            result.status = MISSING
            result.reason = (
                f"No evidence sources supplied for {metric} - nothing "
                "was fabricated."
            )
            return result

        # -- split approved vs forbidden ---------------------------------
        approved: List[tuple] = []
        forbidden: List[str] = []
        for label in sorted(sources):
            fact = sources[label]
            if not isinstance(fact, dict):
                continue
            tier = str(fact.get("provenance_tier")
                       or fact.get("source_tier") or "").strip().upper()
            if not is_allowed_source(tier):
                forbidden.append(label)
                continue
            approved.append((label, fact, tier))

        result.conflicts = [
            {
                "label": label,
                "source_tier": str(
                    sources[label].get("provenance_tier")
                    or sources[label].get("source_tier") or ""
                ).upper(),
                "reason": "forbidden source tier - never used",
            }
            for label in forbidden
        ]

        if not approved:
            result.status = BLOCKED
            result.reason = (
                f"No approved evidence for {metric}: only forbidden "
                f"sources supplied ({', '.join(forbidden) or 'none'}) - "
                "Tier 4 is never substituted."
            )
            return result

        # -- tier priority (Tier 1 > 2 > 3) ------------------------------
        best_tier = min(tier_of(t) for _, _, t in approved)
        best = [
            (label, fact, t) for label, fact, t in approved
            if tier_of(t) == best_tier
        ]
        result.tier = best_tier
        result.source_tier = best[0][2]

        # -- value extraction ---------------------------------------------
        values: List[tuple] = []
        for label, fact, t in best:
            value = None
            raw = fact.get("normalized_value", fact.get("value"))
            try:
                value = Decimal(str(raw)) if raw not in (None, "") else None
            except (ValueError, TypeError, ArithmeticError):
                value = None
            values.append((label, fact, t, value))

        usable = [(l, f, t, v) for l, f, t, v in values if v is not None]
        if not usable:
            result.status = BLOCKED
            result.reason = (
                f"Approved evidence for {metric} carries no usable "
                "numeric value - nothing was invented."
            )
            return result

        # -- dedup within best tier ----------------------------------------
        distinct: List[tuple] = []
        for entry in usable:
            if not any(entry[3] == d[3] for d in distinct):
                distinct.append(entry)
        result.candidates = [
            {
                "label": l,
                "value": float(v),
                "source_tier": t,
                "source": f.get("source"),
                "provider": f.get("provider"),
                "identifier": f.get("provider_identifier"),
                "period": f.get("reporting_period") or f.get("period"),
            }
            for l, f, t, v in distinct
        ]

        # -- provenance gate on the chosen candidate -----------------------
        chosen = distinct[0]
        chosen_label, chosen_fact, chosen_tier, chosen_value = chosen

        if len(distinct) > 1:
            # conflicting approved values: BOTH preserved, review required
            result.status = CONFLICT
            result.value = None
            result.chosen = None
            result.reason = (
                f"{metric} has conflicting approved-source values "
                f"({', '.join(f'{l}={v}' for l, _, _, v in distinct)}) - "
                "both are preserved; review required, never silently "
                "choose one."
            )
            result.conflicts.extend([
                {
                    "label": l,
                    "value": float(v),
                    "source_tier": t,
                    "reason": "conflicting approved values (preserved)",
                }
                for l, _, t, v in distinct[1:]
            ])
            self._attach_external_record(result, chosen_fact,
                                         chosen_tier, chosen_value,
                                         retrieval_timestamp)
            return result

        # single approved value -> gate validation
        gate_ok, gate_reason = self._validate_candidate(
            chosen_fact, chosen_label, reference
        )
        if not gate_ok:
            result.status = BLOCKED
            result.value = None
            result.chosen = None
            result.reason = gate_reason
            return result

        result.status = RECOVERED
        result.value = chosen_value
        result.chosen = {
            "label": chosen_label,
            "value": float(chosen_value),
            "source_tier": chosen_tier,
            "source": chosen_fact.get("source"),
            "provider": chosen_fact.get("provider"),
            "identifier": chosen_fact.get("provider_identifier"),
            "period": (chosen_fact.get("reporting_period")
                       or chosen_fact.get("period")),
            "currency": (chosen_fact.get("currency_code")
                         or chosen_fact.get("currency")),
            "unit": chosen_fact.get("unit"),
            "scale": chosen_fact.get("scale"),
        }
        result.reason = (
            f"{metric} recovered from {chosen_tier} evidence "
            f"({chosen_label}); provenance gate passed."
        )
        self._attach_external_record(result, chosen_fact, chosen_tier,
                                     chosen_value, retrieval_timestamp)
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _validate_candidate(fact: Dict[str, Any], label: str,
                            reference: Optional[Dict[str, Any]]) -> tuple:
        """Run the provenance integrity gate on one candidate fact.
        Returns (ok, reason)."""
        if reference:
            if reference.get("period"):
                period = (fact.get("reporting_period") or fact.get("period"))
                if period and str(period).strip() != str(reference["period"]).strip():
                    return (False,
                            f"{label} period {period} does not match the "
                            f"reference period {reference['period']} - "
                            "BLOCKED.")
            if reference.get("currency"):
                currency = (fact.get("currency_code")
                            or fact.get("currency") or "").strip().upper()
                ref_cur = str(reference["currency"]).strip().upper()
                if currency and currency != ref_cur:
                    return (False,
                            f"{label} currency {currency} does not match "
                            f"the reference currency {ref_cur} - BLOCKED.")
        tier = str(fact.get("provenance_tier")
                   or fact.get("source_tier") or "").strip().upper()
        if tier not in ALLOWED_SOURCE_TIERS:
            return (False,
                    f"{label} carries a forbidden source tier {tier!r} - "
                    "never substituted.")
        return (True, "")

    @staticmethod
    def _attach_external_record(result: RecoveryResult,
                                fact: Dict[str, Any], tier: str,
                                value: Optional[Decimal],
                                retrieval_timestamp: str) -> None:
        """External facts become ExternalEvidenceRecords beginning
        UNVERIFIED until the provenance gate validates them."""
        if tier in ("REGULATORY_API", "EXTERNAL_DERIVED"):
            rec = external_record_from_fact(fact, retrieval_timestamp)
            if rec is not None:
                # External evidence begins UNVERIFIED; the gate (which ran
                # above) upgrades it only when recovery succeeded.
                rec.verification_status = (
                    "VERIFIED" if result.status == RECOVERED else "UNVERIFIED"
                )
                result.external_record = rec


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

DEFAULT_RECOVERY = EvidenceRecoveryEngine()


def recover_evidence(metric: str, sources: Dict[str, Any],
                     reference: Optional[Dict[str, Any]] = None,
                     retrieval_timestamp: str = "") -> RecoveryResult:
    """Convenience entry point."""
    return DEFAULT_RECOVERY.recover(
        metric, sources, reference, retrieval_timestamp
    )
