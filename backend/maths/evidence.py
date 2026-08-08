"""
Financial Timeline Engine
Sprint 12C - Evidence-Aware Decision Graph & Production Integration
backend/maths/evidence.py

Evidence-aware graph: every analytical result can be traced recursively to
its source leaves (document / page / evidence / provider). This module owns:

* the STRICT source hierarchy (Sprint 6.5 FT-E external-evidence tiers):
    Tier 1  uploaded primary document            (DOCUMENT)
    Tier 2  user-uploaded parent/appendix docs   (APPENDIX)
    Tier 3  approved regulatory/structured APIs  (REGULATORY_API,
                                                  EXTERNAL_DERIVED)
    Tier 4  everything else - FORBIDDEN          (OPEN_WEB etc.)
  The maths engine NEVER silently retrieves values from the open web; a
  forbidden source tier fails closed.
* external-evidence records: every externally recovered fact retains
  provider, retrieval timestamp, identifier, source type, period,
  currency, unit, raw value, normalized value, evidence/reference
  metadata and verification status. An external value never becomes
  VERIFIED merely because an API returned it - it must pass the
  provenance integrity gate (provenance.py) like any other fact.
* recursive leaf tracing: trace_leaves() walks a solved lineage from the
  root result down to the source leaves and produces a machine-readable
  evidence tree (not merely formatted text).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from backend.maths.fact_model import FactGraph, FactNode, to_decimal
from backend.maths.solver import Solution

# ---------------------------------------------------------------------------
# Source hierarchy (deterministic tiers)
# ---------------------------------------------------------------------------

TIER_1_DOCUMENT = 1
TIER_2_APPENDIX = 2
TIER_3_REGULATORY_API = 3
TIER_4_FORBIDDEN = 4

# Provenance-tier vocabulary (must mirror backend/evidence_resolver.py).
TIER_DOCUMENT = "DOCUMENT"
TIER_APPENDIX = "APPENDIX"
TIER_REGULATORY_API = "REGULATORY_API"
TIER_EXTERNAL_DERIVED = "EXTERNAL_DERIVED"
TIER_DERIVED = "DERIVED"
TIER_BLOCKED = "BLOCKED"
TIER_UNANALYZED = "UNANALYZED"
TIER_STUDENT_INPUT = "STUDENT_INPUT"

# Approved tiers: only these may feed the calculation graph.
ALLOWED_SOURCE_TIERS = frozenset({
    TIER_DOCUMENT,
    TIER_APPENDIX,
    TIER_REGULATORY_API,
    TIER_EXTERNAL_DERIVED,
    TIER_DERIVED,          # computed inside the engine itself
    TIER_STUDENT_INPUT,    # explicit student entry (12B adjustment flow)
})

# Explicitly forbidden source labels (anything not in ALLOWED_SOURCE_TIERS
# also fails closed - including OPEN_WEB / WEB / INTERNET / UNKNOWN).
FORBIDDEN_SOURCE_TIERS = frozenset({
    "OPEN_WEB", "WEB", "INTERNET", "FORBIDDEN", "UNKNOWN",
    "SCRAPED", "RANDOM_WEB", "UNAPPROVED_API",
})

_TIER_MAP: Dict[str, int] = {
    TIER_DOCUMENT: TIER_1_DOCUMENT,
    TIER_APPENDIX: TIER_2_APPENDIX,
    TIER_REGULATORY_API: TIER_3_REGULATORY_API,
    TIER_EXTERNAL_DERIVED: TIER_3_REGULATORY_API,
    TIER_DERIVED: TIER_3_REGULATORY_API,      # internal computation
    TIER_STUDENT_INPUT: TIER_3_REGULATORY_API,
}


def tier_of(source_tier: Optional[str]) -> int:
    """Deterministic tier number of a provenance tier label.

    Unknown / forbidden labels resolve to TIER_4_FORBIDDEN so the engine
    fails closed - an unrecognized source type is never assumed safe.
    """
    if source_tier is None:
        return TIER_4_FORBIDDEN
    label = str(source_tier).strip().upper()
    if label in FORBIDDEN_SOURCE_TIERS:
        return TIER_4_FORBIDDEN
    return _TIER_MAP.get(label, TIER_4_FORBIDDEN)


def is_allowed_source(source_tier: Optional[str]) -> bool:
    """True when the tier may enter the calculation graph."""
    if source_tier is None:
        return False
    return str(source_tier).strip().upper() in ALLOWED_SOURCE_TIERS


def describe_hierarchy() -> str:
    return (
        "Tier 1 = uploaded primary document | Tier 2 = user-uploaded "
        "parent/appendix documents | Tier 3 = approved regulatory / "
        "structured APIs | Tier 4 = everything else FORBIDDEN."
    )


# ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRef:
    """One machine-readable reference to a source leaf.

    Exposes exactly what the agent UI needs to answer: where did this
    value come from, and what was it when the source said it?
    """

    concept: str
    value: Optional[Decimal] = None
    display_value: str = "—"
    status: str = "—"
    tier: str = "—"
    source: str = "—"
    document_name: str = "—"
    page: str = "—"
    evidence: str = "—"
    provider: str = "—"
    identifier: str = "—"
    period: str = "—"
    currency: str = "—"
    unit: str = "—"
    excel_coordinate: str = "—"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "status": self.status,
            "tier": self.tier,
            "source": self.source,
            "document_name": self.document_name,
            "page": self.page,
            "evidence": self.evidence,
            "provider": self.provider,
            "identifier": self.identifier,
            "period": self.period,
            "currency": self.currency,
            "unit": self.unit,
            "excel_coordinate": self.excel_coordinate,
        }


# ---------------------------------------------------------------------------
# External evidence records (approved adapters only)
# ---------------------------------------------------------------------------


@dataclass
class ExternalEvidenceRecord:
    """Every externally recovered fact, fully attributed.

    The maths engine never retrieves from the open web; records are only
    created by an explicitly approved evidence adapter. The verification
    status is assigned by the provenance integrity gate - an API response
    does not become VERIFIED merely by arriving.
    """

    provider: str
    retrieval_timestamp: str
    identifier: str = "—"
    source_type: str = TIER_REGULATORY_API
    concept: str = ""
    period: str = "—"
    currency: str = "—"
    unit: str = "—"
    scale: str = "—"
    raw_value: Optional[Decimal] = None
    normalized_value: Optional[Decimal] = None
    evidence: str = "—"
    verification_status: str = "UNVERIFIED"
    source: str = "Regulatory API"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "retrieval_timestamp": self.retrieval_timestamp,
            "identifier": self.identifier,
            "source_type": self.source_type,
            "concept": self.concept,
            "period": self.period,
            "currency": self.currency,
            "unit": self.unit,
            "scale": self.scale,
            "raw_value": (float(self.raw_value)
                          if self.raw_value is not None else None),
            "normalized_value": (float(self.normalized_value)
                                 if self.normalized_value is not None
                                 else None),
            "evidence": self.evidence,
            "verification_status": self.verification_status,
            "source": self.source,
        }


def external_record_from_fact(fact: Dict[str, Any],
                              retrieval_timestamp: str = "") -> ExternalEvidenceRecord:
    """Translate an approved-adapter fact dict into a full external record.

    The fact must carry a tier of REGULATORY_API or EXTERNAL_DERIVED;
    anything else is rejected (returns None) so unapproved sources can
    never masquerade as external evidence.
    """
    tier = str(fact.get("provenance_tier") or "").strip().upper()
    if tier not in (TIER_REGULATORY_API, TIER_EXTERNAL_DERIVED):
        return None  # type: ignore[return-value]
    value = to_decimal(fact.get("normalized_value", fact.get("value")))
    return ExternalEvidenceRecord(
        provider=str(fact.get("provider") or "—"),
        retrieval_timestamp=retrieval_timestamp or "—",
        identifier=str(fact.get("provider_identifier")
                       or fact.get("identifier") or "—"),
        source_type=tier,
        concept=str(fact.get("metric") or fact.get("canonical") or "—"),
        period=str(fact.get("reporting_period") or fact.get("period") or "—"),
        currency=str(fact.get("currency") or "—"),
        unit=str(fact.get("unit") or "—"),
        scale=str(fact.get("scale") or "—"),
        raw_value=to_decimal(fact.get("value")),
        normalized_value=value,
        evidence=str(fact.get("evidence") or "—"),
        verification_status="UNVERIFIED",
        source=str(fact.get("source") or "Regulatory API"),
    )


# ---------------------------------------------------------------------------
# Recursive leaf tracing
# ---------------------------------------------------------------------------


@dataclass
class EvidenceTrace:
    """Machine-readable evidence lineage for one solved result.

    leaves: the terminal source facts (never computed, never fabricated).
    chain:  every derivation step from the leaves up to the target,
            in deterministic traversal order (dependencies first).
    """

    target: str
    status: str = "—"
    leaves: List[EvidenceRef] = field(default_factory=list)
    chain: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "leaves": [l.to_dict() for l in self.leaves],
            "chain": list(self.chain),
        }


def _evidence_ref_from_fact(concept: str, fact: FactNode) -> EvidenceRef:
    return EvidenceRef(
        concept=concept,
        value=fact.value,
        display_value=str(fact.value) if fact.value is not None else "—",
        status=fact.status,
        tier=fact.source_tier or "—",
        source=fact.source or "—",
        document_name=fact.document_name or "—",
        page=fact.page or "—",
        evidence=fact.evidence or "—",
        provider=str(fact.source or "—"),
        identifier=str(fact.source or "—"),
        period=fact.period or "—",
        currency=fact.currency or "—",
        unit=(fact.original_unit or fact.normalized_unit) or "—",
        excel_coordinate=fact.excel_cell_coordinate or "—",
    )


def _evidence_ref_from_input(concept: str, facts: FactGraph) -> EvidenceRef:
    fact = facts.get(concept)
    if fact is not None:
        return _evidence_ref_from_fact(concept, fact)
    return EvidenceRef(concept=concept)


def trace_leaves(solution: Solution, facts: FactGraph) -> EvidenceTrace:
    """Recursively trace a solution's lineage to its source leaves.

    Deterministic: walks the solution's lineage steps in traversal order.
    A step input whose concept is itself a derived step (in the lineage
    chain) is NOT a leaf - its own inputs are expanded recursively. Only
    facts that terminate the chain (not produced by a step in this
    lineage) become leaves. Missing provenance is never fabricated - it
    is carried through as '—' so the gate can flag it.
    """
    leaves: List[EvidenceRef] = []
    seen_leaf_concepts: Set[str] = set()
    chain: List[Dict[str, Any]] = []
    derived: Set[str] = set()

    lineage = solution.lineage
    steps = list(lineage.steps) if lineage is not None else []
    # Deterministic ordered set of every concept PRODUCED by a formula
    # step. Direct-fact steps are leaves, not derivations.
    for s in steps:
        if s.kind != "direct":
            derived.add(s.concept)
        chain.append({
            "concept": s.concept,
            "formula_id": s.formula_id or "",
            "formula": s.formula,
            "value": (float(s.value) if s.value is not None else None),
            "display_value": s.display_value,
            "status": s.status,
            "kind": s.kind,
            "inputs": [
                {
                    "concept": i.concept,
                    "value": (float(i.value) if i.value is not None else None),
                    "display_value": i.display_value,
                    "status": i.status,
                    "provenance_tier": i.provenance_tier,
                    "source": i.source,
                    "page": i.page,
                    "evidence": i.evidence,
                }
                for i in s.inputs
            ],
        })
    # Walk step inputs; anything not produced by a step is a source leaf.
    for s in steps:
        for i in s.inputs:
            if i.concept in derived:
                continue
            if i.concept in seen_leaf_concepts:
                continue
            seen_leaf_concepts.add(i.concept)
            leaves.append(_evidence_ref_from_input(i.concept, facts))
    # Direct facts (no derivation steps at all) are their own leaves.
    if not steps and solution.value is not None:
        fact = facts.get(solution.target)
        if fact is not None:
            leaves.append(_evidence_ref_from_fact(solution.target, fact))
    return EvidenceTrace(
        target=solution.target,
        status=solution.status,
        leaves=leaves,
        chain=chain,
    )


def render_evidence_tree(trace: EvidenceTrace) -> str:
    """Human-readable rendering of the machine-readable evidence trace."""
    lines = [f"{trace.target} ({trace.status})"]
    for c in trace.chain:
        lines.append(
            f"├── {c['concept']} = {c['display_value']} "
            f"[{c['formula_id'] or c['kind']}, {c['status']}]"
        )
        for i in c["inputs"]:
            pg = f" p.{i['page']}" if i["page"] not in ("", "—") else ""
            ev = f" ({i['evidence']})" if i["evidence"] not in ("", "—") else ""
            lines.append(
                f"│   ├── {i['concept']} = {i['display_value']} "
                f"[{i['status']}, {i['provenance_tier']}]{pg}{ev}"
            )
    for leaf in trace.leaves:
        pg = f" p.{leaf.page}" if leaf.page not in ("", "—") else ""
        lines.append(
            f"└── {leaf.concept} = {leaf.display_value} "
            f"[{leaf.status}, {leaf.tier}]{pg} ({leaf.source})"
        )
    return "\n".join(lines)
