"""
Financial Timeline Engine
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening
backend/maths/restatement.py

Restatement / amendment handling (Sprint 12D section D).

Multiple versions of the same financial fact are handled deterministically:

* NEVER overwrite historical evidence - original and restated facts are
  both preserved.
* Version / filing identity is tracked where available (version, filing_id,
  amended/restated markers).
* Every pair of same-concept facts is classified as one of:
      DUPLICATE              same analytical fact, same value
      RESTATEMENT            same fact, different value, EXPLICIT version
                             metadata distinguishes them
      CONFLICT               same fact, different value, no version
                             metadata - genuine disagreement
      INCOMPATIBLE_PERIODS   different periods - not the same fact
      DIFFERENT_IDENTITY     entity/statement/currency/unit differ - not
                             the same analytical fact
      REVIEW_REQUIRED        cannot deterministically distinguish
                             restatement from conflict
* resolve_analytical_fact() picks the CURRENT analytical fact only when
  an explicit restatement marker exists (deterministic: highest version,
  or the explicitly restated/amended fact). Otherwise the state is
  REVIEW_REQUIRED and BOTH values are preserved.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.maths.fact_model import FactNode
from backend.maths.identity import differing_dimensions, same_identity

# ---------------------------------------------------------------------------
# Classification kinds
# ---------------------------------------------------------------------------

DUPLICATE = "DUPLICATE"
RESTATEMENT = "RESTATEMENT"
CONFLICT = "CONFLICT"
INCOMPATIBLE_PERIODS = "INCOMPATIBLE_PERIODS"
DIFFERENT_IDENTITY = "DIFFERENT_IDENTITY"
REVIEW_REQUIRED = "REVIEW_REQUIRED"

_RESTATEMENT_MARKERS = (
    "restated", "restatement", "amended", "amendment", "as restated",
    "revised", "recast", "reclassified", "final", "corrected",
)


def _has_restatement_marker(*values: Optional[str]) -> bool:
    for v in values:
        if v is None:
            continue
        low = str(v).lower()
        if any(m in low for m in _RESTATEMENT_MARKERS):
            return True
    return False


def _version_rank(node: FactNode) -> int:
    """Deterministic version ordering. Higher wins. Facts with no version
    rank 0; explicit restatement markers rank above plain versions."""
    if _has_restatement_marker(node.version, node.filing_id,
                               node.status_reason, node.evidence):
        return 2
    v = str(node.version or node.filing_id or "")
    m = re.search(r"(\d+)(?:\.(\d+))?", v)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2) or "0")
        return 100 + major * 10 + minor
    if v:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Pair classification
# ---------------------------------------------------------------------------


@dataclass
class RestatementVerdict:
    """Deterministic classification of one pair of same-concept facts."""

    concept: str
    node_a: str
    node_b: str
    kind: str = REVIEW_REQUIRED
    value_a: Optional[Decimal] = None
    value_b: Optional[Decimal] = None
    reason: str = ""
    dimensions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "node_a": self.node_a,
            "node_b": self.node_b,
            "kind": self.kind,
            "value_a": (float(self.value_a)
                        if self.value_a is not None else None),
            "value_b": (float(self.value_b)
                        if self.value_b is not None else None),
            "reason": self.reason,
            "dimensions": list(self.dimensions),
        }


def classify_pair(a: FactNode, b: FactNode) -> RestatementVerdict:
    """Deterministic classification of two same-canonical-concept facts."""
    verdict = RestatementVerdict(
        concept=a.canonical_concept,
        node_a=a.node_id,
        node_b=b.node_id,
        value_a=a.value,
        value_b=b.value,
    )
    # 1. incompatible periods
    if (a.period and b.period and a.period != b.period):
        verdict.kind = INCOMPATIBLE_PERIODS
        verdict.reason = (
            f"{a.canonical_concept} is reported for different periods "
            f"({a.period} vs {b.period}) - these are NOT the same fact; "
            "never compared across periods."
        )
        return verdict
    # 2. different identity (entity / statement / currency / unit).
    #    Version differences are IGNORED here: two values of the SAME
    #    fact at different versions must be recognized as the same
    #    analytical fact so they can be classified as a RESTATEMENT.
    if not same_identity(a, b, ignore_version=True):
        dims = differing_dimensions(a, b, ignore_version=True)
        verdict.kind = DIFFERENT_IDENTITY
        verdict.dimensions = dims
        verdict.reason = (
            f"{a.canonical_concept} differs on identity dimensions "
            f"({', '.join(dims)}) - not the same analytical fact; never "
            "merged silently."
        )
        return verdict
    # 3. same identity, same value -> duplicate evidence
    if a.value is not None and b.value is not None and a.value == b.value:
        verdict.kind = DUPLICATE
        verdict.reason = (
            f"{a.canonical_concept} appears as identical evidence "
            f"({a.value}) from {a.source or a.node_id} and "
            f"{b.source or b.node_id} - confirm it is not a stale copy."
        )
        return verdict
    # 4. same identity, different values
    #    Explicit restatement requires EITHER a declared restatement/
    #    amendment marker on either fact, OR version/filing metadata on
    #    BOTH facts. One-sided version metadata cannot prove a
    #    restatement - that is REVIEW_REQUIRED (indistinguishable from
    #    a genuine conflict).
    explicit = (
        _has_restatement_marker(a.version, a.filing_id, a.status_reason,
                                a.evidence)
        or _has_restatement_marker(b.version, b.filing_id, b.status_reason,
                                   b.evidence)
        or (_version_rank(a) > 0 and _version_rank(b) > 0)
    )
    if explicit:
        verdict.kind = RESTATEMENT
        current = "a" if _version_rank(a) >= _version_rank(b) else "b"
        current_label = "node A" if current == "a" else "node B"
        verdict.reason = (
            f"{a.canonical_concept} was restated: "
            f"{a.version or a.filing_id or a.node_id} = {a.value} vs "
            f"{b.version or b.filing_id or b.node_id} = {b.value}. "
            f"Both preserved; current analytical fact is {current_label} "
            "(higher version)."
        )
        return verdict
    # 5. no version metadata -> cannot distinguish restatement from conflict
    verdict.kind = REVIEW_REQUIRED
    verdict.reason = (
        f"{a.canonical_concept} has conflicting values ({a.value} vs "
        f"{b.value}) with no version/filing metadata - cannot "
        "deterministically distinguish a restatement from a genuine "
        "conflict. Both preserved; review required."
    )
    return verdict


# ---------------------------------------------------------------------------
# Analytical fact resolution
# ---------------------------------------------------------------------------


@dataclass
class AnalyticalFact:
    """The current analytical representation of one canonical fact."""

    concept: str
    current: Optional[FactNode] = None
    value: Optional[Decimal] = None
    status: str = REVIEW_REQUIRED
    status_reason: str = ""
    all_facts: List[FactNode] = field(default_factory=list)
    verdicts: List[RestatementVerdict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "current_node": self.current.node_id if self.current else None,
            "value": float(self.value) if self.value is not None else None,
            "status": self.status,
            "status_reason": self.status_reason,
            "all_facts": [
                {
                    "node_id": n.node_id,
                    "value": (float(n.value) if n.value is not None else None),
                    "version": n.version,
                    "filing_id": n.filing_id,
                    "source": n.source,
                }
                for n in self.all_facts
            ],
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def resolve_analytical_fact(nodes: List[FactNode],
                            concept: Optional[str] = None
                            ) -> AnalyticalFact:
    """Deterministically resolve a set of same-canonical-concept facts to
    ONE current analytical fact - or REVIEW_REQUIRED when the version
    metadata does not make the choice safe.

    Never overwrites: every fact is preserved in all_facts.
    """
    if not nodes:
        return AnalyticalFact(
            concept=concept or "", status="BLOCKED",
            status_reason="No facts provided - nothing to resolve.",
        )
    nodes = sorted(nodes, key=lambda n: n.node_id)  # deterministic order
    concept = concept or nodes[0].canonical_concept

    # single fact -> itself (analytical node, value preserved)
    if len(nodes) == 1:
        n = nodes[0]
        return AnalyticalFact(
            concept=concept, current=n, value=n.value,
            status=n.status, status_reason=n.status_reason or "",
            all_facts=list(nodes),
        )

    verdicts = [
        classify_pair(nodes[i], nodes[j])
        for i in range(len(nodes))
        for j in range(i + 1, len(nodes))
    ]
    kinds = {v.kind for v in verdicts}

    # any pair we cannot safely distinguish -> REVIEW_REQUIRED
    if REVIEW_REQUIRED in kinds or CONFLICT in kinds:
        reason = next(
            v.reason for v in verdicts
            if v.kind in (REVIEW_REQUIRED, CONFLICT)
        )
        return AnalyticalFact(
            concept=concept,
            value=None,
            status="REVIEW_REQUIRED",
            status_reason=reason,
            all_facts=list(nodes),
            verdicts=verdicts,
        )

    # all duplicates -> pick the first (deterministic), value identical
    if kinds <= {DUPLICATE}:
        chosen = nodes[0]
        return AnalyticalFact(
            concept=concept, current=chosen, value=chosen.value,
            status="VERIFIED", status_reason="Duplicate evidence, same "
            "value - analytical fact resolved deterministically.",
            all_facts=list(nodes), verdicts=verdicts,
        )

    # restatements (explicit version metadata) -> highest version wins
    if kinds <= {DUPLICATE, RESTATEMENT}:
        chosen = max(nodes, key=lambda n: (
            _version_rank(n), n.node_id,
        ))
        return AnalyticalFact(
            concept=concept, current=chosen, value=chosen.value,
            status="VERIFIED", status_reason=(
                f"Restated filing handled: both versions preserved; the "
                f"analytical fact uses {chosen.node_id} "
                f"(version {chosen.version or chosen.filing_id or '—'})."
            ),
            all_facts=list(nodes), verdicts=verdicts,
        )

    # incompatible periods / different identity: not the same analytical
    # fact - the resolution is not defined; fail closed.
    if INCOMPATIBLE_PERIODS in kinds:
        return AnalyticalFact(
            concept=concept, value=None, status="BLOCKED",
            status_reason=next(
                v.reason for v in verdicts
                if v.kind == INCOMPATIBLE_PERIODS
            ),
            all_facts=list(nodes), verdicts=verdicts,
        )
    return AnalyticalFact(
        concept=concept, value=None, status="REVIEW_REQUIRED",
        status_reason="Facts cannot be combined into one analytical fact "
                      "- identity conflict requires review.",
        all_facts=list(nodes), verdicts=verdicts,
    )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def classify_restatement_group(nodes: List[FactNode]) -> AnalyticalFact:
    """Convenience entry point (alias of resolve_analytical_fact)."""
    return resolve_analytical_fact(nodes)
