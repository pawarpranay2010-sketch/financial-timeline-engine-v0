"""
Platrixa — Sprint P2: Validated Long-Term Learning Knowledge Store

A safe, deterministic knowledge system that allows Platrixa to improve
from validated experience without allowing probabilistic AI, incorrect
student inputs, or unverified model interpretations to modify financial truth.

Core principle:
    Learn probabilistically.  Verify deterministically.  Remember structurally.

Architecture:
    Student Input
      → AI Language Understanding
      → Validated Knowledge Lookup (this module)
      → Structured Transaction Memory (Sprint 43)
      → Deterministic Accounting Kernel
      → Independent Verification
      → Evidence Collection
      → Validation Pipeline (this module)
      → Long-Term Knowledge Store (this module)

Safety rules:
  - Never approximate money (Decimal only)
  - Never infer missing amounts
  - Never invent payment methods
  - Never overwrite historical transactions
  - Never bypass the kernel
  - Never bypass integrity validation
  - Never auto-promote from a single evidence instance
  - Never give model-generated evidence the same trust as deterministic verification
  - Unvalidated knowledge cannot affect verified truth
  - Retired knowledge cannot affect runtime interpretation
  - Scoped knowledge cannot leak outside its scope

Pure module: no Streamlit, no AI, no network.  Deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class KnowledgeStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class KnowledgeType(str, Enum):
    PHRASE_CANONICAL = "PHRASE_CANONICAL"           # textbook phrase → canonical term
    AMBIGUITY_PATTERN = "AMBIGUITY_PATTERN"          # ambiguous phrasing → resolved form
    SETTLEMENT_CONVENTION = "SETTLEMENT_CONVENTION"  # settlement phrasing conventions
    PAYMENT_MODE_CONVENTION = "PAYMENT_MODE_CONVENTION"  # cash/credit conventions
    TEXTBOOK_NOTATION = "TEXTBOOK_NOTATION"           # textbook-specific notation
    CLARIFICATION_MAP = "CLARIFICATION_MAP"          # clarification → resolution mapping


class KnowledgeScope(str, Enum):
    SESSION = "SESSION"     # single problem session
    CURRICULUM = "CURRICULUM"  # specific curriculum (FYJC, SYJC)
    INSTITUTION = "INSTITUTION"  # specific school/college
    TEXTBOOK = "TEXTBOOK"    # specific textbook edition
    GLOBAL = "GLOBAL"       # universal (requires strongest evidence)


class EvidenceSource(str, Enum):
    STUDENT = "STUDENT"
    DETERMINISTIC = "DETERMINISTIC"
    MODEL_GENERATED = "MODEL_GENERATED"


# ---------------------------------------------------------------------------
# Promotion thresholds — derived from the existing safety architecture
# ---------------------------------------------------------------------------

# These are calibrated to match the existing verification philosophy:
# - The kernel uses exact Decimal arithmetic
# - The confidence gate requires explicit student confirmation
# - Sprint 27 mutation safety requires zero mutations
# - Sprint 35 integrity requires verified ⇒ non-empty journal
# - The six-tier status system never silently upgrades

PROMOTION_THRESHOLDS = {
    # Minimum independent evidence instances required
    # Must come from at least 2 different evidence sources
    "min_evidence_count": 3,

    # Minimum number of distinct evidence sources
    "min_source_diversity": 2,

    # Minimum validation successes
    "min_validation_count": 3,

    # Maximum rejection count before candidate is auto-rejected
    "max_rejection_before_reject": 2,

    # Confidence threshold for promotion (0.0 to 1.0)
    "min_confidence": Decimal("0.85"),

    # Maximum age in days before a candidate is considered stale
    "max_candidate_age_days": 90,

    # Minimum evidence gap (hours) between first and last evidence
    # to prevent gaming the system in a single session
    "min_evidence_span_hours": 0,

    # Scope promotion requirements: how many scope levels needed
    # before knowledge can be promoted to the next wider scope
    "scope_promotion_threshold": 3,
}


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class EvidenceItem:
    """A single piece of evidence supporting or contradicting a knowledge candidate."""
    source: EvidenceSource
    context: str                    # the transaction text or phrase observed
    resolution: str                 # what the pattern maps to
    verification_status: str        # VERIFIED / REVIEW_REQUIRED / BLOCKED
    deterministic_result: Optional[Dict[str, Any]] = None  # kernel output if available
    student_id: Optional[str] = None
    timestamp: str = ""
    provenance: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "source": self.source.value if isinstance(self.source, EvidenceSource) else str(self.source),
            "context": self.context,
            "resolution": self.resolution,
            "verification_status": self.verification_status,
            "student_id": self.student_id,
            "timestamp": self.timestamp,
            "has_deterministic_result": self.deterministic_result is not None,
        }


@dataclass
class StatusTransition:
    """Records a status change for audit."""
    from_status: str
    to_status: str
    reason: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class KnowledgeItem:
    """A single piece of validated knowledge."""
    knowledge_id: str
    pattern: str                       # the input pattern observed
    canonical_interpretation: str       # what it maps to
    knowledge_type: KnowledgeType
    scope: KnowledgeScope
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    confidence: Decimal = Decimal("0.0")
    evidence_count: int = 0
    validation_count: int = 0
    rejection_count: int = 0
    source_diversity: int = 0          # number of distinct EvidenceSource types
    created_at: str = ""
    last_validated_at: Optional[str] = None
    last_rejected_at: Optional[str] = None
    retired_at: Optional[str] = None
    version: int = 1
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    evidence_trail: List[EvidenceItem] = field(default_factory=list)
    status_history: List[StatusTransition] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic snapshot — safe for serialization and comparison."""
        return {
            "knowledge_id": self.knowledge_id,
            "pattern": self.pattern,
            "canonical_interpretation": self.canonical_interpretation,
            "knowledge_type": self.knowledge_type.value if isinstance(self.knowledge_type, KnowledgeType) else str(self.knowledge_type),
            "scope": self.scope.value if isinstance(self.scope, KnowledgeScope) else str(self.scope),
            "status": self.status.value if isinstance(self.status, KnowledgeStatus) else str(self.status),
            "confidence": str(self.confidence),
            "evidence_count": self.evidence_count,
            "validation_count": self.validation_count,
            "rejection_count": self.rejection_count,
            "source_diversity": self.source_diversity,
            "created_at": self.created_at,
            "last_validated_at": self.last_validated_at,
            "last_rejected_at": self.last_rejected_at,
            "retired_at": self.retired_at,
            "version": self.version,
            "provenance": list(self.provenance),
            "evidence_trail": [e.snapshot() for e in self.evidence_trail],
            "status_history": [
                {"from_status": t.from_status, "to_status": t.to_status,
                 "reason": t.reason, "timestamp": t.timestamp}
                for t in self.status_history
            ],
        }


# ---------------------------------------------------------------------------
# Deterministic knowledge ID generation
# ---------------------------------------------------------------------------

def _generate_knowledge_id(pattern: str, knowledge_type: KnowledgeType, scope: KnowledgeScope) -> str:
    """Generate a deterministic knowledge ID from pattern + type + scope."""
    raw = f"{pattern.lower().strip()}|{knowledge_type.value}|{scope.value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Confidence computation
# ---------------------------------------------------------------------------

def _compute_confidence(item: KnowledgeItem) -> Decimal:
    """Compute deterministic confidence from evidence counts.

    Confidence = (validation_count / max(evidence_count, 1)) * source_diversity_factor

    source_diversity_factor = min(source_diversity / 2, 1.0)

    This means:
    - A single-source candidate can never reach confidence 1.0
    - Validation success rate drives the core confidence
    - More diverse sources = higher confidence
    """
    if item.evidence_count == 0:
        return Decimal("0.0")

    validation_rate = Decimal(str(item.validation_count)) / Decimal(str(max(item.evidence_count, 1)))

    # Source diversity factor: requires at least 2 distinct sources for full factor
    diversity_factor = Decimal(str(min(item.source_diversity, 2))) / Decimal("2")

    confidence = validation_rate * diversity_factor

    # Apply rejection penalty
    if item.rejection_count > 0:
        penalty = Decimal(str(min(item.rejection_count, 3))) * Decimal("0.15")
        confidence = max(confidence - penalty, Decimal("0.0"))

    return min(confidence, Decimal("1.0"))


# ---------------------------------------------------------------------------
# Pattern normalisation (for deterministic matching)
# ---------------------------------------------------------------------------

def _normalise_pattern(text: str) -> str:
    """Deterministically normalise a pattern for matching.

    Lowercases, collapses whitespace, strips punctuation edges.
    Does NOT interpret meaning — purely structural.
    """
    low = text.lower().strip()
    # Collapse whitespace
    low = re.sub(r"\s+", " ", low)
    # Strip trailing/leading punctuation
    low = low.strip(".,;:!?")
    return low


# ---------------------------------------------------------------------------
# Knowledge Store
# ---------------------------------------------------------------------------

class ValidatedKnowledgeStore:
    """Deterministic validated knowledge store.

    Thread-safe for single-process use (no shared mutable state).
    All operations produce identical results given identical inputs.
    """

    def __init__(self) -> None:
        self._items: Dict[str, KnowledgeItem] = {}
        self._version: int = 1
        self._history: List[Dict[str, Any]] = []

    # -- Query --

    def lookup(self, pattern: Optional[str]) -> Optional[KnowledgeItem]:
        """Look up a validated knowledge item by pattern.

        Returns VALIDATED items only.  CANDIDATE, REJECTED, RETIRED items
        are never returned.
        """
        if not pattern:
            return None
        norm = _normalise_pattern(pattern)
        for item in self._items.values():
            if item.status != KnowledgeStatus.VALIDATED:
                continue
            if _normalise_pattern(item.pattern) == norm:
                return item
        return None

    def lookup_any(self, pattern: Optional[str]) -> Optional[KnowledgeItem]:
        """Look up any knowledge item by pattern (any status)."""
        if not pattern:
            return None
        norm = _normalise_pattern(pattern)
        for item in self._items.values():
            if _normalise_pattern(item.pattern) == norm:
                return item
        return None

    def lookup_by_type(self, knowledge_type: KnowledgeType,
                       scope: Optional[KnowledgeScope] = None) -> List[KnowledgeItem]:
        """Return all validated items of a given type, optionally scoped."""
        results = []
        for item in self._items.values():
            if item.status != KnowledgeStatus.VALIDATED:
                continue
            if item.knowledge_type != knowledge_type:
                continue
            if scope and item.scope != scope:
                continue
            results.append(item)
        return results

    def lookup_by_scope(self, scope: KnowledgeScope) -> List[KnowledgeItem]:
        """Return all validated items in a given scope."""
        return [i for i in self._items.values()
                if i.status == KnowledgeStatus.VALIDATED and i.scope == scope]

    # -- Candidate Extraction --

    def extract_candidate(
        self,
        pattern: str,
        canonical_interpretation: str,
        knowledge_type: KnowledgeType,
        scope: KnowledgeScope,
        source: EvidenceSource,
        context: str = "",
        verification_status: str = "VERIFIED",
        deterministic_result: Optional[Dict[str, Any]] = None,
        student_id: Optional[str] = None,
    ) -> KnowledgeItem:
        """Extract or update a candidate from new evidence.

        If a candidate with the same normalised pattern already exists,
        the evidence is added to it.  Otherwise a new candidate is created.

        This is the entry point for the learning pipeline:
            New experience → Candidate extraction → Validation → Promotion
        """
        norm = _normalise_pattern(pattern)
        kid = _generate_knowledge_id(norm, knowledge_type, scope)

        evidence = EvidenceItem(
            source=source,
            context=context or pattern,
            resolution=canonical_interpretation,
            verification_status=verification_status,
            deterministic_result=deterministic_result,
            student_id=student_id,
        )

        if kid in self._items:
            item = self._items[kid]
            # Add evidence
            item.evidence_trail.append(evidence)
            item.evidence_count = len(item.evidence_trail)

            # Track source diversity
            sources_seen = set()
            for e in item.evidence_trail:
                src = e.source if isinstance(e.source, EvidenceSource) else EvidenceSource(str(e.source))
                sources_seen.add(src)
            item.source_diversity = len(sources_seen)

            # Track validation/rejection
            if verification_status == "VERIFIED":
                item.validation_count += 1
                item.last_validated_at = datetime.now(timezone.utc).isoformat()
            elif verification_status in ("REVIEW_REQUIRED", "BLOCKED"):
                item.rejection_count += 1
                item.last_rejected_at = datetime.now(timezone.utc).isoformat()

            # Recompute confidence
            item.confidence = _compute_confidence(item)
            return item
        else:
            item = KnowledgeItem(
                knowledge_id=kid,
                pattern=pattern,
                canonical_interpretation=canonical_interpretation,
                knowledge_type=knowledge_type,
                scope=scope,
                evidence_count=1,
                validation_count=1 if verification_status == "VERIFIED" else 0,
                rejection_count=0 if verification_status == "VERIFIED" else 1,
                source_diversity=1,
                evidence_trail=[evidence],
                confidence=Decimal("0.0"),
            )
            item.confidence = _compute_confidence(item)
            item.status_history.append(StatusTransition(
                from_status="NONE",
                to_status=KnowledgeStatus.CANDIDATE.value,
                reason="Initial candidate created from evidence",
            ))
            self._items[kid] = item
            return item

    # -- Validation Pipeline --

    def validate_candidate(self, knowledge_id: str) -> Tuple[bool, str]:
        """Run deterministic promotion validation on a candidate.

        Returns (can_promote, reason).
        """
        item = self._items.get(knowledge_id)
        if item is None:
            return False, "Knowledge item not found"
        if item.status != KnowledgeStatus.CANDIDATE:
            return False, f"Item is {item.status.value}, not CANDIDATE"

        t = PROMOTION_THRESHOLDS

        # Evidence count
        if item.evidence_count < t["min_evidence_count"]:
            return False, (
                f"Insufficient evidence: {item.evidence_count}/{t['min_evidence_count']}"
            )

        # Source diversity
        if item.source_diversity < t["min_source_diversity"]:
            return False, (
                f"Insufficient source diversity: {item.source_diversity}/{t['min_source_diversity']}"
            )

        # Validation count
        if item.validation_count < t["min_validation_count"]:
            return False, (
                f"Insufficient validations: {item.validation_count}/{t['min_validation_count']}"
            )

        # Rejection count
        if item.rejection_count >= t["max_rejection_before_reject"]:
            return False, (
                f"Too many rejections: {item.rejection_count}/{t['max_rejection_before_reject']}"
            )

        # Confidence
        if item.confidence < t["min_confidence"]:
            return False, (
                f"Confidence too low: {item.confidence} < {t['min_confidence']}"
            )

        # All checks passed
        return True, "All promotion thresholds met"

    def promote(self, knowledge_id: str) -> Tuple[bool, str]:
        """Promote a candidate to VALIDATED.

        Only succeeds if validate_candidate() returns True.
        """
        can_promote, reason = self.validate_candidate(knowledge_id)
        if not can_promote:
            return False, reason

        item = self._items[knowledge_id]
        old_status = item.status
        item.status = KnowledgeStatus.VALIDATED
        item.last_validated_at = datetime.now(timezone.utc).isoformat()
        item.status_history.append(StatusTransition(
            from_status=old_status.value,
            to_status=KnowledgeStatus.VALIDATED.value,
            reason="Promoted: all thresholds met",
        ))
        self._version += 1
        return True, "Promoted to VALIDATED"

    def reject(self, knowledge_id: str, reason: str = "") -> Tuple[bool, str]:
        """Reject a candidate."""
        item = self._items.get(knowledge_id)
        if item is None:
            return False, "Knowledge item not found"
        if item.status == KnowledgeStatus.RETIRED:
            return False, "Cannot reject retired item"

        old_status = item.status
        item.status = KnowledgeStatus.REJECTED
        item.last_rejected_at = datetime.now(timezone.utc).isoformat()
        item.status_history.append(StatusTransition(
            from_status=old_status.value,
            to_status=KnowledgeStatus.REJECTED.value,
            reason=reason or "Rejected",
        ))
        self._version += 1
        return True, "Rejected"

    def retire(self, knowledge_id: str, reason: str = "") -> Tuple[bool, str]:
        """Retire a validated item (rollback/undo).

        Retired items can never affect runtime interpretation again.
        """
        item = self._items.get(knowledge_id)
        if item is None:
            return False, "Knowledge item not found"

        old_status = item.status
        item.status = KnowledgeStatus.RETIRED
        item.retired_at = datetime.now(timezone.utc).isoformat()
        item.status_history.append(StatusTransition(
            from_status=old_status.value,
            to_status=KnowledgeStatus.RETIRED.value,
            reason=reason or "Retired",
        ))
        self._version += 1
        return True, "Retired"

    def rollback(self, knowledge_id: str) -> Tuple[bool, str]:
        """Rollback a validated item to CANDIDATE status.

        Used when a promoted item later causes a regression.
        """
        item = self._items.get(knowledge_id)
        if item is None:
            return False, "Knowledge item not found"
        if item.status != KnowledgeStatus.VALIDATED:
            return False, f"Can only rollback VALIDATED items, got {item.status.value}"

        old_status = item.status
        item.status = KnowledgeStatus.CANDIDATE
        item.status_history.append(StatusTransition(
            from_status=old_status.value,
            to_status=KnowledgeStatus.CANDIDATE.value,
            reason="Rolled back due to regression",
        ))
        self._version += 1
        return True, "Rolled back to CANDIDATE"

    # -- Conflict Detection --

    def detect_conflict(self, pattern: str, interpretation: str) -> Optional[Dict[str, Any]]:
        """Check if a new pattern/interpretation conflicts with existing validated knowledge.

        Returns conflict info if found, None if no conflict.
        """
        norm = _normalise_pattern(pattern)
        for item in self._items.values():
            if item.status != KnowledgeStatus.VALIDATED:
                continue
            if _normalise_pattern(item.pattern) == norm:
                if _normalise_pattern(item.canonical_interpretation) != _normalise_pattern(interpretation):
                    return {
                        "existing_knowledge_id": item.knowledge_id,
                        "existing_interpretation": item.canonical_interpretation,
                        "new_interpretation": interpretation,
                        "conflict_type": "INTERPRETATION_MISMATCH",
                    }
        return None

    def find_conflicts(self, knowledge_id: str) -> List[Dict[str, Any]]:
        """Find all conflicts for a given knowledge item."""
        item = self._items.get(knowledge_id)
        if item is None:
            return []

        conflicts = []
        norm = _normalise_pattern(item.pattern)
        for other in self._items.values():
            if other.knowledge_id == knowledge_id:
                continue
            if other.status != KnowledgeStatus.VALIDATED:
                continue
            if _normalise_pattern(other.pattern) == norm:
                if _normalise_pattern(other.canonical_interpretation) != _normalise_pattern(item.canonical_interpretation):
                    conflicts.append({
                        "conflicting_id": other.knowledge_id,
                        "conflicting_interpretation": other.canonical_interpretation,
                        "this_interpretation": item.canonical_interpretation,
                    })
        return conflicts

    # -- Metrics --

    def metrics(self) -> Dict[str, Any]:
        """Deterministic metrics snapshot."""
        by_status = {}
        by_type = {}
        by_scope = {}
        for item in self._items.values():
            s = item.status.value
            by_status[s] = by_status.get(s, 0) + 1
            t = item.knowledge_type.value
            by_type[t] = by_type.get(t, 0) + 1
            sc = item.scope.value
            by_scope[sc] = by_scope.get(sc, 0) + 1

        return {
            "total_items": len(self._items),
            "by_status": by_status,
            "by_type": by_type,
            "by_scope": by_scope,
            "version": self._version,
            "total_evidence": sum(i.evidence_count for i in self._items.values()),
        }

    # -- Snapshot --

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic full snapshot of the knowledge store."""
        return {
            "version": self._version,
            "item_count": len(self._items),
            "items": [item.snapshot() for item in sorted(
                self._items.values(),
                key=lambda x: x.knowledge_id,
            )],
            "metrics": self.metrics(),
        }

    # -- Validation History --

    def get_history(self) -> List[Dict[str, Any]]:
        """Return the complete validation history."""
        return [
            {
                "knowledge_id": item.knowledge_id,
                "pattern": item.pattern,
                "status_history": [
                    {
                        "from_status": t.from_status,
                        "to_status": t.to_status,
                        "reason": t.reason,
                        "timestamp": t.timestamp,
                    }
                    for t in item.status_history
                ],
            }
            for item in self._items.values()
            if item.status_history
        ]

    # -- Scope Isolation --

    @staticmethod
    def scope_allows(s_item_scope: KnowledgeScope, query_scope: KnowledgeScope) -> bool:
        """Determine if knowledge from s_item_scope can be used in query_scope.

        Scope hierarchy (narrow → wide):
            SESSION → CURRICULUM → INSTITUTION → TEXTBOOK → GLOBAL

        A wider scope knowledge item can always be used in a narrower context.
        A narrower scope item can only be used in equal or narrower contexts.
        """
        scope_order = {
            KnowledgeScope.SESSION: 0,
            KnowledgeScope.CURRICULUM: 1,
            KnowledgeScope.INSTITUTION: 2,
            KnowledgeScope.TEXTBOOK: 3,
            KnowledgeScope.GLOBAL: 4,
        }
        item_rank = scope_order.get(s_item_scope, 0)
        query_rank = scope_order.get(query_scope, 0)
        # Item scope rank must be >= query scope rank (wider scope usable in narrower)
        return item_rank >= query_rank

    # -- Shadow Mode Helpers --

    def suggest_for_transaction(self, transaction_text: str) -> List[Dict[str, Any]]:
        """Look up validated knowledge that applies to a transaction.

        Returns suggestions without modifying any production state.
        Used in shadow mode to compare knowledge-assisted vs baseline interpretation.
        Uses word-overlap matching rather than strict substring to handle
        punctuation and amount format differences.
        """
        suggestions = []
        norm = _normalise_pattern(transaction_text)
        norm_words = set(norm.split())

        for item in self._items.values():
            if item.status != KnowledgeStatus.VALIDATED:
                continue
            item_norm = _normalise_pattern(item.pattern)
            item_words = set(item_norm.split())
            # Word-overlap match: at least 60% of pattern words appear in transaction
            if not item_words:
                continue
            overlap = len(item_words & norm_words) / len(item_words)
            if overlap >= 0.6:
                suggestions.append({
                    "knowledge_id": item.knowledge_id,
                    "pattern": item.pattern,
                    "interpretation": item.canonical_interpretation,
                    "knowledge_type": item.knowledge_type.value,
                    "scope": item.scope.value,
                    "confidence": str(item.confidence),
                })

        return suggestions

    def is_available_for_use(self, knowledge_id: str) -> bool:
        """Check if a knowledge item is safe to use in production.

        Must be VALIDATED and not retired.
        """
        item = self._items.get(knowledge_id)
        if item is None:
            return False
        return item.status == KnowledgeStatus.VALIDATED

    # -- Serialization --

    def to_dict(self) -> Dict[str, Any]:
        """Deterministic serialization."""
        return self.snapshot()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidatedKnowledgeStore":
        """Reconstruct from deterministic snapshot."""
        store = cls()
        store._version = data.get("version", 1)

        for item_data in data.get("items", []):
            kt = item_data.get("knowledge_type", "PHRASE_CANONICAL")
            ks = item_data.get("scope", "GLOBAL")
            kst = item_data.get("status", "CANDIDATE")

            # Reconstruct evidence trail
            evidence_trail = []
            for ev_data in item_data.get("evidence_trail", []):
                evidence_trail.append(EvidenceItem(
                    source=EvidenceSource(ev_data.get("source", "STUDENT")),
                    context=ev_data.get("context", ""),
                    resolution=ev_data.get("resolution", ""),
                    verification_status=ev_data.get("verification_status", "VERIFIED"),
                    student_id=ev_data.get("student_id"),
                    timestamp=ev_data.get("timestamp", ""),
                ))

            # Reconstruct status history
            status_history = []
            for sh_data in item_data.get("status_history", []):
                status_history.append(StatusTransition(
                    from_status=sh_data.get("from_status", ""),
                    to_status=sh_data.get("to_status", ""),
                    reason=sh_data.get("reason", ""),
                    timestamp=sh_data.get("timestamp", ""),
                ))

            item = KnowledgeItem(
                knowledge_id=item_data["knowledge_id"],
                pattern=item_data.get("pattern", ""),
                canonical_interpretation=item_data.get("canonical_interpretation", ""),
                knowledge_type=KnowledgeType(kt),
                scope=KnowledgeScope(ks),
                status=KnowledgeStatus(kst),
                confidence=Decimal(item_data.get("confidence", "0")),
                evidence_count=item_data.get("evidence_count", 0),
                validation_count=item_data.get("validation_count", 0),
                rejection_count=item_data.get("rejection_count", 0),
                source_diversity=item_data.get("source_diversity", 0),
                created_at=item_data.get("created_at", ""),
                last_validated_at=item_data.get("last_validated_at"),
                last_rejected_at=item_data.get("last_rejected_at"),
                retired_at=item_data.get("retired_at"),
                version=item_data.get("version", 1),
                provenance=item_data.get("provenance", []),
                evidence_trail=evidence_trail,
                status_history=status_history,
            )
            store._items[item.knowledge_id] = item

        return store
