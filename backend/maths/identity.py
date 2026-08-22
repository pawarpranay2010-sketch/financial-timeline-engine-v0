"""
Platrixa
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening
backend/maths/identity.py

Period / entity / statement isolation (Sprint 12D section C).

A fact is NEVER combined with another fact merely because the canonical
label matches. Fact identity distinguishes:

    ENTITY        consolidated vs standalone vs parent vs subsidiary
    STATEMENT     Income Statement / Balance Sheet / Cash Flow / Notes
    PERIOD        FY2024 vs FY2025
    PERIOD TYPE   annual vs quarterly vs TTM
    CURRENCY      USD vs INR
    UNIT / SCALE  absolute vs millions
    SOURCE        document / provider identity
    PROVENANCE    tier + version / filing identity

identity_key() builds a deterministic fingerprint over those dimensions.
group_by_identity() partitions a graph. detect_identity_ambiguity()
reports same-label facts whose identity dimensions conflict - these are
REVIEW_REQUIRED candidates; the engine fails closed rather than merging.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.maths.fact_model import FactGraph, FactNode

# ---------------------------------------------------------------------------
# Identity dimensions (fixed, deterministic order)
# ---------------------------------------------------------------------------

IDENTITY_DIMENSIONS = (
    "entity", "statement", "period", "period_type", "currency",
    "unit_scale", "source", "version",
)

# Dimensions that MUST match for two facts to be the same analytical fact.
STRICT_DIMENSIONS = (
    "entity", "statement", "period", "period_type", "currency",
)

# Dimensions that are part of identity but tolerate absence (one-sided
# unknown is allowed; two known-and-different values are a conflict).
SOFT_DIMENSIONS = ("unit_scale", "source", "version")


def _dim_value(node: FactNode, dim: str) -> str:
    if dim == "entity":
        return str(node.entity or "").strip()
    if dim == "statement":
        return str(node.statement or "").strip()
    if dim == "period":
        return str(node.period or "").strip()
    if dim == "period_type":
        return str(node.period_type or "").strip()
    if dim == "currency":
        return str(node.currency or "").strip().upper()
    if dim == "unit_scale":
        unit = str(node.original_unit or node.normalized_unit or "").strip()
        scale = str(node.original_scale or "").strip()
        return f"{unit}|{scale}"
    if dim == "source":
        return str(node.source or "").strip()
    if dim == "version":
        return str(node.version or node.filing_id or "").strip()
    return ""


def identity_key(node: FactNode) -> str:
    """Deterministic identity fingerprint of one fact node."""
    parts = [
        str(node.canonical_concept or "").strip(),
        *(_dim_value(node, d) for d in IDENTITY_DIMENSIONS),
    ]
    return "|".join(parts)


def same_identity(a: FactNode, b: FactNode,
                  ignore_version: bool = False) -> bool:
    """True when every strict dimension matches (and soft dimensions that
    are present on both sides match).

    ignore_version=True is used by the restatement layer: two values of
    the SAME fact at different versions must still be recognized as the
    same analytical fact so they can be classified as a RESTATEMENT
    rather than as different facts.
    """
    dims = STRICT_DIMENSIONS
    if ignore_version:
        dims = tuple(d for d in STRICT_DIMENSIONS if d != "version")
    for d in dims:
        va, vb = _dim_value(a, d), _dim_value(b, d)
        if va and vb and va != vb:
            return False
        if va != vb:  # one side known, other empty -> not provably same
            return False
    for d in SOFT_DIMENSIONS:
        if ignore_version and d == "version":
            continue
        va, vb = _dim_value(a, d), _dim_value(b, d)
        if va and vb and va != vb:
            return False
    return True


def differing_dimensions(a: FactNode, b: FactNode,
                         ignore_version: bool = False) -> List[str]:
    """Identity dimensions that differ between two facts (deterministic
    order)."""
    out = []
    for d in IDENTITY_DIMENSIONS:
        if ignore_version and d == "version":
            continue
        va, vb = _dim_value(a, d), _dim_value(b, d)
        if va != vb:
            out.append(d)
    return out


def group_by_identity(graph: FactGraph) -> Dict[str, List[FactNode]]:
    """Partition the graph's nodes by identity key (insertion order)."""
    groups: Dict[str, List[FactNode]] = {}
    for node_id in graph.known_ids():
        node = graph.get(node_id)
        if node is None:
            continue
        key = identity_key(node)
        groups.setdefault(key, []).append(node)
    return groups


# ---------------------------------------------------------------------------
# Ambiguity detection (fail closed, never merge)
# ---------------------------------------------------------------------------


@dataclass
class IdentityIssue:
    """One same-label identity conflict that requires review."""

    issue_id: str
    kind: str                      # ENTITY_MISMATCH / PERIOD_MISMATCH /
                                   # PERIOD_TYPE_MISMATCH / CURRENCY_MISMATCH
                                   # / STATEMENT_MISMATCH / UNIT_SCALE_MISMATCH
    concept: str
    node_ids: List[str] = field(default_factory=list)
    dimensions: List[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "kind": self.kind,
            "concept": self.concept,
            "node_ids": list(self.node_ids),
            "dimensions": list(self.dimensions),
            "detail": self.detail,
        }


_KIND_BY_DIMENSION = {
    "entity": "ENTITY_MISMATCH",
    "statement": "STATEMENT_MISMATCH",
    "period": "PERIOD_MISMATCH",
    "period_type": "PERIOD_TYPE_MISMATCH",
    "currency": "CURRENCY_MISMATCH",
    "unit_scale": "UNIT_SCALE_MISMATCH",
}


def detect_identity_ambiguity(graph: FactGraph,
                              concept: Optional[str] = None
                              ) -> List[IdentityIssue]:
    """Deterministic scan for same-canonical-label facts whose identity
    dimensions conflict. The engine NEVER merges them silently - each is
    reported as a review candidate.

    Facts with identical identity keys are simply the same analytical
    fact (duplicates / restatements are handled by restatement.py).
    """
    issues: List[IdentityIssue] = []
    seen: Set[Tuple[str, str, str]] = set()
    by_concept: Dict[str, List[FactNode]] = {}
    for node_id in graph.known_ids():
        node = graph.get(node_id)
        if node is None:
            continue
        by_concept.setdefault(node.canonical_concept, []).append(node)

    for c in sorted(by_concept):
        if concept is not None and c != concept:
            continue
        nodes = by_concept[c]
        if len(nodes) < 2:
            continue
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                if same_identity(a, b):
                    continue
                for d in differing_dimensions(a, b):
                    kind = _KIND_BY_DIMENSION.get(d)
                    if kind is None:
                        continue
                    key = (c, kind, d)
                    if key in seen:
                        continue
                    seen.add(key)
                    va = _dim_value(a, d) or "unknown"
                    vb = _dim_value(b, d) or "unknown"
                    issues.append(IdentityIssue(
                        issue_id=f"{kind}:{c}:{len(issues)}",
                        kind=kind,
                        concept=c,
                        node_ids=[a.node_id, b.node_id],
                        dimensions=[d],
                        detail=(
                            f"{c} carries conflicting {d} values "
                            f"({va!r} vs {vb!r}) - never merged silently."
                        ),
                    ))
    return issues


def canonical_node_ids(graph: FactGraph, concept: str) -> List[str]:
    """Distinct node ids for one canonical concept (deterministic)."""
    out = []
    for node_id in graph.known_ids():
        node = graph.get(node_id)
        if node is not None and node.canonical_concept == concept:
            out.append(node_id)
    return out


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def describe_fact_identity(node: FactNode) -> str:
    """Human-readable identity description of one fact."""
    dims = []
    for d in IDENTITY_DIMENSIONS:
        v = _dim_value(node, d)
        if v:
            dims.append(f"{d}={v}")
    return f"{node.canonical_concept} [{', '.join(dims)}]"
