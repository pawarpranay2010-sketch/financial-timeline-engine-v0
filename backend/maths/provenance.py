"""
Financial Timeline Engine
Sprint 12C - Evidence-Aware Decision Graph & Production Integration
backend/maths/provenance.py

Provenance Integrity Gate.

Before any result is marked VERIFIED or DERIVED, every source leaf must
pass deterministic validation:

    * source exists
    * source type / provenance tier is allowed (Tier 1-3; Tier 4
      forbidden - never silently open-web)
    * document identity exists where required
    * page provenance exists for page-backed documents
    * period matches the reference context where one is provided
    * currency matches the reference context where one is provided
    * units / scales are compatible (known scales only - never guessed)
    * source evidence is non-empty where required (document-backed facts)
    * dependency statuses are valid (fail closed on BLOCKED / unknown)

Missing provenance is never fabricated. Insufficient provenance yields
REVIEW_REQUIRED or BLOCKED according to the 12A/12B status semantics:

    * a fact with no source at all / unknown tier   -> REVIEW_REQUIRED
      (nothing proves it wrong, but nothing proves it right)
    * a fact from a FORBIDDEN tier / unanalyzable  -> BLOCKED
    * missing page anchor on a page-backed document -> REVIEW_REQUIRED
    * missing evidence text on a document-backed fact -> REVIEW_REQUIRED

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.maths.evidence import (
    ALLOWED_SOURCE_TIERS,
    FORBIDDEN_SOURCE_TIERS,
    TIER_BLOCKED,
    TIER_DOCUMENT,
    TIER_APPENDIX,
    TIER_UNANALYZED,
    is_allowed_source,
    tier_of,
    TIER_4_FORBIDDEN,
)
from backend.maths.fact_model import FactGraph
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED, is_status
from backend.maths.units import scale_multiplier

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

GATE_PASS = "PASS"
GATE_REVIEW = "REVIEW_REQUIRED"
GATE_BLOCKED = "BLOCKED"

# Tiers that require a page anchor when page-backed documents are used.
_PAGE_BACKED_TIERS = frozenset({TIER_DOCUMENT, TIER_APPENDIX})
# Tiers that require non-empty evidence text.
_EVIDENCE_REQUIRED_TIERS = frozenset({TIER_DOCUMENT, TIER_APPENDIX})


@dataclass
class ProvenanceCheck:
    """One leaf's deterministic gate result."""

    concept: str
    passed: bool = False
    verdict: str = GATE_REVIEW
    reasons: List[str] = field(default_factory=list)
    tier: str = "—"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "passed": self.passed,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "tier": self.tier,
        }


@dataclass
class ProvenanceVerdict:
    """Aggregate gate result for one target / fact set."""

    target: str
    verdict: str = GATE_PASS
    checks: List[ProvenanceCheck] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == GATE_PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "verdict": self.verdict,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class ProvenanceGate:
    """Deterministic provenance validation for source leaves.

    validate_facts(facts, reference=None) validates every leaf in a fact
    graph. validate_leaf(node, reference=None) validates one node.
    `reference` is an optional dict with the expected context:
        {"period": ..., "currency": ..., "document": ...}
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    def validate_facts(self, facts: FactGraph,
                       reference: Optional[Dict[str, Any]] = None,
                       target: str = "ROOT") -> ProvenanceVerdict:
        checks: List[ProvenanceCheck] = []
        for node_id in facts.known_ids():
            node = facts.get(node_id)
            if node is None:
                continue
            checks.append(self.validate_leaf(node, reference))
        reasons: List[str] = []
        for c in checks:
            reasons.extend(c.reasons)
        # Aggregate: any BLOCKED -> BLOCKED; else any REVIEW -> REVIEW.
        verdict = GATE_PASS
        if any(c.verdict == GATE_BLOCKED for c in checks):
            verdict = GATE_BLOCKED
        elif any(c.verdict == GATE_REVIEW for c in checks):
            verdict = GATE_REVIEW
        return ProvenanceVerdict(
            target=target, verdict=verdict, checks=checks, reasons=reasons,
        )

    # ------------------------------------------------------------------
    def validate_leaf(self, node,
                      reference: Optional[Dict[str, Any]] = None) -> ProvenanceCheck:
        """Validate ONE fact node. Deterministic check order is fixed:
        tier/source -> document -> page -> evidence -> period -> currency
        -> scale -> status. The first failing category decides the verdict
        severity (BLOCKED beats REVIEW_REQUIRED)."""
        concept = node.node_id
        tier = str(node.source_tier or "").strip().upper() or None
        reasons: List[str] = []
        hard = False  # BLOCKED severity

        # 1. source exists --------------------------------------------
        source = (node.source or "").strip()
        document = (node.document_name or "").strip()
        if not source and not document and tier != TIER_UNANALYZED:
            reasons.append(
                f"{concept}: no source or document identity is recorded."
            )

        # 2. source type / tier allowed -------------------------------
        if tier is None:
            reasons.append(
                f"{concept}: provenance tier is missing - cannot confirm "
                "the source type is approved."
            )
        elif not is_allowed_source(tier):
            if tier in FORBIDDEN_SOURCE_TIERS:
                hard = True
                reasons.append(
                    f"{concept}: source tier {tier!r} is FORBIDDEN - the "
                    "maths engine never accepts open-web / unapproved "
                    "evidence."
                )
            elif tier == TIER_UNANALYZED:
                hard = True
                reasons.append(
                    f"{concept}: source is UNANALYZED - fail closed, no "
                    "verified value."
                )
            else:
                reasons.append(
                    f"{concept}: source tier {tier!r} is not an approved "
                    "tier (Tier 1-3 only)."
                )

        # 3. document identity where required --------------------------
        if tier in _PAGE_BACKED_TIERS and not document:
            reasons.append(
                f"{concept}: a document-backed fact requires a document "
                "identity, but none is recorded."
            )

        # 4. page provenance for page-backed documents -----------------
        page = (node.page or "").strip()
        if tier in _PAGE_BACKED_TIERS and not page:
            reasons.append(
                f"{concept}: page provenance is missing for a "
                "page-backed document - the value cannot be re-verified."
            )

        # 5. evidence non-empty where required -------------------------
        evidence = (node.evidence or "").strip()
        if tier in _EVIDENCE_REQUIRED_TIERS and not evidence:
            reasons.append(
                f"{concept}: source evidence text is empty for a "
                "document-backed fact."
            )

        # 6. period matches reference ---------------------------------
        if reference and reference.get("period"):
            ref_period = str(reference["period"]).strip()
            node_period = (node.period or "").strip()
            if node_period and node_period != ref_period:
                reasons.append(
                    f"{concept}: period {node_period!r} does not match the "
                    f"reference period {ref_period!r}."
                )

        # 7. currency matches reference --------------------------------
        if reference and reference.get("currency"):
            ref_currency = str(reference["currency"]).strip().upper()
            node_currency = (node.currency or "").strip().upper()
            if node_currency and node_currency != ref_currency:
                reasons.append(
                    f"{concept}: currency {node_currency!r} does not match "
                    f"the reference currency {ref_currency!r}."
                )

        # 8. scale known (never guessed) -------------------------------
        scale = node.original_scale
        if scale not in (None, "") and scale_multiplier(scale) is None:
            hard = True
            reasons.append(
                f"{concept}: unknown scale {scale!r} cannot be normalized "
                "- never guessed."
            )

        # 9. dependency status valid -----------------------------------
        # A BLOCKED status that stems from MISSING provenance metadata is
        # REVIEW severity (insufficient provenance, not proven wrong) - the
        # solver's fail-closed status propagation is what actually blocks
        # computation. Explicitly forbidden/blocked tiers are already
        # hard-blocked in check 2.
        status = node.status
        if not is_status(status) or status == BLOCKED:
            reasons.append(
                f"{concept}: status {status!r} is not a verified/derived "
                "computable status - the solver fails closed on it."
            )

        if not reasons:
            return ProvenanceCheck(
                concept=concept, passed=True, verdict=GATE_PASS,
                tier=tier or "—",
            )
        verdict = GATE_BLOCKED if hard else GATE_REVIEW
        return ProvenanceCheck(
            concept=concept, passed=False, verdict=verdict,
            reasons=reasons, tier=tier or "—",
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

DEFAULT_GATE = ProvenanceGate()


def validate_provenance(facts: FactGraph,
                        reference: Optional[Dict[str, Any]] = None,
                        target: str = "ROOT") -> ProvenanceVerdict:
    """Convenience entry point."""
    return DEFAULT_GATE.validate_facts(facts, reference, target)
