"""
Platrixa — P3: Persistent Validated Learning + Student Feedback Loop

Extends Sprint P2's ValidatedKnowledgeStore with:
  P3.1  Persistent JSON-backed storage (atomic writes)
  P3.2  Student correction feedback loop
  P3.3  UI-friendly knowledge suggestions
  P3.4  Deterministic learning metrics dashboard
  P3.5  Knowledge effectiveness tracking

Core principle:
  Learn probabilistically.  Verify deterministically.  Remember structurally.
  Persist safely.  Measure honestly.

Safety rules (inherited from P2):
  - Never approximate money (Decimal only)
  - Never infer missing amounts
  - Never invent payment methods
  - Never overwrite historical transactions
  - Never bypass the kernel
  - Never bypass integrity validation
  - Never auto-promote from a single evidence instance
  - Never allow unvalidated knowledge to affect verified truth
  - Never allow retired knowledge to affect runtime interpretation

Pure module: no Streamlit, no AI, no network.  Deterministic.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.maths.fyjc_validated_knowledge import (
    EvidenceItem,
    EvidenceSource,
    KnowledgeItem,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    PROMOTION_THRESHOLDS,
    StatusTransition,
    ValidatedKnowledgeStore,
    _compute_confidence,
    _generate_knowledge_id,
    _normalise_pattern,
)


# ---------------------------------------------------------------------------
# P3.1 — Persistence Layer
# ---------------------------------------------------------------------------

class KnowledgePersistence:
    """Atomic JSON-backed persistence for ValidatedKnowledgeStore.

    Writes are atomic: data goes to a temp file then is renamed into place.
    Reads are crash-safe: malformed files are ignored, never crash the app.
    """

    @staticmethod
    def save(store: ValidatedKnowledgeStore, path: str) -> bool:
        """Deterministically persist the knowledge store to a JSON file.

        Returns True on success, False on IO error.
        """
        try:
            data = store.to_dict()
            dir_name = os.path.dirname(path) or "."
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                os.replace(tmp_path, path)
                return True
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return False
        except Exception:
            return False

    @staticmethod
    def load(path: str) -> Optional[ValidatedKnowledgeStore]:
        """Load a knowledge store from a JSON file.

        Returns None if the file doesn't exist, is empty, or is malformed.
        Never crashes the application.
        """
        try:
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
            if not isinstance(data, dict):
                return None
            store = ValidatedKnowledgeStore.from_dict(data)
            return store
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def is_valid_store_file(path: str) -> bool:
        """Check if a file is a valid knowledge store without loading it."""
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return False
            data = json.loads(content)
            return isinstance(data, dict) and "items" in data
        except (json.JSONDecodeError, TypeError):
            return False


# ---------------------------------------------------------------------------
# P3.5 — Knowledge Effectiveness Tracking
# ---------------------------------------------------------------------------

class EffectivenessStatus(str, Enum):
    UNKNOWN = "UNKNOWN"         # Not yet used as suggestion
    HELPFUL = "HELPFUL"         # Suggestion accepted → kernel verified
    NEUTRAL = "NEUTRAL"         # Suggestion shown → student ignored → kernel verified
    REJECTED = "REJECTED"       # Suggestion shown → student dismissed → kernel still verified
    CONFLICTING = "CONFLICTING" # Suggestion shown → kernel returned different result
    RETIRE_CANDIDATE = "RETIRE_CANDIDATE"  # Enough negative evidence to consider retirement


@dataclass
class EffectivenessRecord:
    """Tracks the real-world outcome of a promoted knowledge item."""
    knowledge_id: str
    suggestion_shown: bool = False
    suggestion_accepted: bool = False
    suggestion_dismissed: bool = False
    kernel_result_status: Optional[str] = None
    kernel_matched_suggestion: Optional[bool] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "suggestion_shown": self.suggestion_shown,
            "suggestion_accepted": self.suggestion_accepted,
            "suggestion_dismissed": self.suggestion_dismissed,
            "kernel_result_status": self.kernel_result_status,
            "kernel_matched_suggestion": self.kernel_matched_suggestion,
            "timestamp": self.timestamp,
        }


def compute_effectiveness_status(
    records: List[EffectivenessRecord],
    min_samples: int = 3,
) -> EffectivenessStatus:
    """Deterministically compute effectiveness from outcome records.

    Requires at least `min_samples` records before classifying.
    """
    if len(records) < min_samples:
        return EffectivenessStatus.UNKNOWN

    accepted = sum(1 for r in records if r.suggestion_accepted)
    dismissed = sum(1 for r in records if r.suggestion_dismissed)
    conflicting = sum(1 for r in records if r.kernel_matched_suggestion is False)
    total = len(records)

    # Conflict rate > 20% → CONFLICTING
    if total > 0 and conflicting / total > 0.2:
        return EffectivenessStatus.CONFLICTING

    # Acceptance rate > 60% → HELPFUL
    if total > 0 and accepted / total > 0.6:
        return EffectivenessStatus.HELPFUL

    # Dismissal rate > 60% → REJECTED
    if total > 0 and dismissed / total > 0.6:
        return EffectivenessStatus.REJECTED

    # Retirement candidate: low effectiveness + many dismissals
    if total >= 5 and accepted == 0 and dismissed >= 3:
        return EffectivenessStatus.RETIRE_CANDIDATE

    return EffectivenessStatus.NEUTRAL


# ---------------------------------------------------------------------------
# P3.4 — Learning Metrics Dashboard
# ---------------------------------------------------------------------------

@dataclass
class LearningMetrics:
    """Deterministic metrics tracker for the learning system.

    Tracks all P3-required metrics without approximation.
    """
    total_transactions: int = 0
    verified_transactions: int = 0
    review_required_transactions: int = 0
    blocked_transactions: int = 0

    # Suggestion metrics
    suggestions_shown: int = 0
    suggestions_accepted: int = 0
    suggestions_dismissed: int = 0

    # Knowledge lifecycle metrics
    knowledge_candidates_created: int = 0
    knowledge_promoted: int = 0
    knowledge_rejected: int = 0
    knowledge_retired: int = 0
    knowledge_conflicts: int = 0
    rollback_events: int = 0

    # Safety metrics
    incorrect_verified_count: int = 0
    verified_empty_journal_count: int = 0

    # Correction tracking
    student_corrections_received: int = 0
    student_corrections_as_evidence: int = 0

    # Timestamps
    first_metrics_at: str = ""
    last_metrics_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_metrics_at:
            self.first_metrics_at = now
        self.last_metrics_at = now

    @property
    def clarification_rate(self) -> float:
        """Fraction of transactions that required clarification."""
        if self.total_transactions == 0:
            return 0.0
        return self.review_required_transactions / self.total_transactions

    @property
    def clarification_resolution_rate(self) -> float:
        """Fraction of review-required transactions that were resolved."""
        if self.review_required_transactions == 0:
            return 0.0
        resolved = self.suggestions_accepted
        return resolved / self.review_required_transactions

    @property
    def suggestion_acceptance_rate(self) -> float:
        """Fraction of shown suggestions that were accepted."""
        if self.suggestions_shown == 0:
            return 0.0
        return self.suggestions_accepted / self.suggestions_shown

    @property
    def knowledge_promotion_rate(self) -> float:
        """Fraction of candidates that were promoted."""
        total = self.knowledge_candidates_created
        if total == 0:
            return 0.0
        return self.knowledge_promoted / total

    def record_transaction(self, status: str) -> None:
        """Record a transaction outcome."""
        self.total_transactions += 1
        if status == "VERIFIED":
            self.verified_transactions += 1
        elif status == "REVIEW_REQUIRED":
            self.review_required_transactions += 1
        elif status == "BLOCKED":
            self.blocked_transactions += 1
        self.last_metrics_at = datetime.now(timezone.utc).isoformat()

    def record_suggestion(self, accepted: bool) -> None:
        """Record a suggestion outcome."""
        self.suggestions_shown += 1
        if accepted:
            self.suggestions_accepted += 1
        else:
            self.suggestions_dismissed += 1
        self.last_metrics_at = datetime.now(timezone.utc).isoformat()

    def record_knowledge_event(self, event: str) -> None:
        """Record a knowledge lifecycle event."""
        if event == "candidate_created":
            self.knowledge_candidates_created += 1
        elif event == "promoted":
            self.knowledge_promoted += 1
        elif event == "rejected":
            self.knowledge_rejected += 1
        elif event == "retired":
            self.knowledge_retired += 1
        elif event == "conflict":
            self.knowledge_conflicts += 1
        elif event == "rollback":
            self.rollback_events += 1
        elif event == "student_correction":
            self.student_corrections_received += 1
        elif event == "correction_as_evidence":
            self.student_corrections_as_evidence += 1
        self.last_metrics_at = datetime.now(timezone.utc).isoformat()

    def record_safety_violation(self, violation_type: str) -> None:
        """Record a safety violation."""
        if violation_type == "incorrect_verified":
            self.incorrect_verified_count += 1
        elif violation_type == "verified_empty_journal":
            self.verified_empty_journal_count += 1
        self.last_metrics_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic metrics snapshot."""
        return {
            "transactions": {
                "total": self.total_transactions,
                "verified": self.verified_transactions,
                "review_required": self.review_required_transactions,
                "blocked": self.blocked_transactions,
            },
            "rates": {
                "clarification_rate": round(self.clarification_rate, 4),
                "clarification_resolution_rate": round(self.clarification_resolution_rate, 4),
                "suggestion_acceptance_rate": round(self.suggestion_acceptance_rate, 4),
                "knowledge_promotion_rate": round(self.knowledge_promotion_rate, 4),
            },
            "suggestions": {
                "shown": self.suggestions_shown,
                "accepted": self.suggestions_accepted,
                "dismissed": self.suggestions_dismissed,
            },
            "knowledge_lifecycle": {
                "candidates_created": self.knowledge_candidates_created,
                "promoted": self.knowledge_promoted,
                "rejected": self.knowledge_rejected,
                "retired": self.knowledge_retired,
                "conflicts": self.knowledge_conflicts,
                "rollbacks": self.rollback_events,
            },
            "safety": {
                "incorrect_verified": self.incorrect_verified_count,
                "verified_empty_journal": self.verified_empty_journal_count,
            },
            "corrections": {
                "received": self.student_corrections_received,
                "as_evidence": self.student_corrections_as_evidence,
            },
            "timestamps": {
                "first": self.first_metrics_at,
                "last": self.last_metrics_at,
            },
        }


# ---------------------------------------------------------------------------
# P3.2 — Student Correction Feedback Loop
# ---------------------------------------------------------------------------

def record_student_correction(
    store: ValidatedKnowledgeStore,
    transaction_text: str,
    student_answer: str,
    knowledge_type: KnowledgeType = KnowledgeType.PAYMENT_MODE_CONVENTION,
    scope: KnowledgeScope = KnowledgeScope.SESSION,
    student_id: Optional[str] = None,
    verification_status: str = "REVIEW_REQUIRED",
) -> KnowledgeItem:
    """Capture a student's clarification response as candidate evidence.

    A single student correction NEVER automatically becomes validated knowledge.
    It passes through the existing P2 validation pipeline.

    Args:
        store: The validated knowledge store.
        transaction_text: The original transaction text that was ambiguous.
        student_answer: The student's clarification answer.
        knowledge_type: What kind of knowledge this is.
        scope: The scope (defaults to SESSION — never auto-global).
        student_id: Optional student identifier for tracking.
        verification_status: The verification status of the original transaction.

    Returns:
        The KnowledgeItem (candidate) that was created or updated.
    """
    # Build the pattern from the transaction text
    pattern = transaction_text.strip()
    interpretation = student_answer.strip()

    # Extract candidate through the standard pipeline
    item = store.extract_candidate(
        pattern=pattern,
        canonical_interpretation=interpretation,
        knowledge_type=knowledge_type,
        scope=scope,
        source=EvidenceSource.STUDENT,
        context=f"Student correction: '{student_answer}' for '{transaction_text}'",
        verification_status=verification_status,
        student_id=student_id,
    )

    return item


# ---------------------------------------------------------------------------
# P3.3 — UI-Friendly Knowledge Suggestions
# ---------------------------------------------------------------------------

def get_ui_suggestions(
    store: ValidatedKnowledgeStore,
    transaction_text: str,
    min_confidence: Decimal = Decimal("0.85"),
) -> List[Dict[str, Any]]:
    """Get UI-friendly knowledge suggestions for a transaction.

    Returns suggestions only for VALIDATED knowledge items with sufficient
    confidence.  Each suggestion includes a human-readable hint text.

    Rules:
      - Suggestion must never silently change the transaction
      - Student must be able to ignore/dismiss it
      - Low-confidence knowledge is not shown
      - Retired knowledge is never shown
      - Conflicting knowledge is not shown
    """
    raw_suggestions = store.suggest_for_transaction(transaction_text)
    ui_suggestions = []

    for s in raw_suggestions:
        conf = Decimal(s.get("confidence", "0"))
        if conf < min_confidence:
            continue

        kid = s.get("knowledge_id", "")
        item = store._items.get(kid)
        if item is None:
            continue

        # Skip if there are known conflicts
        conflicts = store.find_conflicts(kid)
        if conflicts:
            continue

        # Build human-readable hint
        hint = _build_suggestion_hint(
            item.knowledge_type,
            item.canonical_interpretation,
        )

        ui_suggestions.append({
            "knowledge_id": kid,
            "hint": hint,
            "interpretation": item.canonical_interpretation,
            "knowledge_type": item.knowledge_type.value,
            "confidence": str(item.confidence),
            "scope": item.scope.value,
        })

    return ui_suggestions


def _build_suggestion_hint(
    knowledge_type: KnowledgeType,
    interpretation: str,
) -> str:
    """Build a student-friendly hint string from knowledge metadata."""
    type_hints = {
        KnowledgeType.PAYMENT_MODE_CONVENTION: (
            f"Previously seen: this phrase often indicates a {interpretation} transaction."
        ),
        KnowledgeType.SETTLEMENT_CONVENTION: (
            f"Previously seen: this type of settlement usually means {interpretation}."
        ),
        KnowledgeType.PHRASE_CANONICAL: (
            f"Previously seen: this phrase typically means \"{interpretation}\" in accounting."
        ),
        KnowledgeType.AMBIGUITY_PATTERN: (
            f"Previously seen: similar phrasing was resolved as {interpretation}."
        ),
        KnowledgeType.TEXTBOOK_NOTATION: (
            f"Previously seen: this notation is commonly interpreted as {interpretation}."
        ),
        KnowledgeType.CLARIFICATION_MAP: (
            f"Previously seen: this clarification usually leads to {interpretation}."
        ),
    }
    return type_hints.get(
        knowledge_type,
        f"Previously seen: this pattern maps to \"{interpretation}\".",
    )


# ---------------------------------------------------------------------------
# P3 Integrated Learning Manager
# ---------------------------------------------------------------------------

class P3LearningManager:
    """Integrated P3 manager that combines persistence, feedback, metrics,
    and effectiveness tracking.

    This is the single entry point for all P3 operations from the
    problem engine.
    """

    def __init__(
        self,
        store_path: Optional[str] = None,
        store: Optional[ValidatedKnowledgeStore] = None,
    ) -> None:
        self._store_path = store_path
        self._store = store or ValidatedKnowledgeStore()
        self._metrics = LearningMetrics()
        self._effectiveness: Dict[str, List[EffectivenessRecord]] = {}

        # Try to load persisted store
        if store_path:
            loaded = KnowledgePersistence.load(store_path)
            if loaded is not None:
                self._store = loaded

    @property
    def store(self) -> ValidatedKnowledgeStore:
        return self._store

    @property
    def metrics(self) -> LearningMetrics:
        return self._metrics

    # -- Persistence --

    def save(self) -> bool:
        """Persist the current store to disk."""
        if self._store_path:
            return KnowledgePersistence.save(self._store, self._store_path)
        return False

    def reload(self) -> bool:
        """Reload the store from disk."""
        if self._store_path:
            loaded = KnowledgePersistence.load(self._store_path)
            if loaded is not None:
                self._store = loaded
                return True
        return False

    # -- Student Feedback --

    def record_student_correction(
        self,
        transaction_text: str,
        student_answer: str,
        knowledge_type: KnowledgeType = KnowledgeType.PAYMENT_MODE_CONVENTION,
        scope: KnowledgeScope = KnowledgeScope.SESSION,
        student_id: Optional[str] = None,
    ) -> KnowledgeItem:
        """Record a student correction as candidate evidence."""
        self._metrics.record_knowledge_event("student_correction")
        self._metrics.record_knowledge_event("correction_as_evidence")

        item = record_student_correction(
            store=self._store,
            transaction_text=transaction_text,
            student_answer=student_answer,
            knowledge_type=knowledge_type,
            scope=scope,
            student_id=student_id,
        )

        # Track lifecycle
        if item.evidence_count == 1:
            self._metrics.record_knowledge_event("candidate_created")

        return item

    # -- UI Suggestions --

    def get_suggestions(self, transaction_text: str) -> List[Dict[str, Any]]:
        """Get UI-friendly suggestions for a transaction."""
        return get_ui_suggestions(self._store, transaction_text)

    # -- Effectiveness Tracking --

    def record_suggestion_outcome(
        self,
        knowledge_id: str,
        accepted: bool,
        kernel_status: Optional[str] = None,
        kernel_matched: Optional[bool] = None,
    ) -> None:
        """Record the outcome of a suggestion shown to a student."""
        record = EffectivenessRecord(
            knowledge_id=knowledge_id,
            suggestion_shown=True,
            suggestion_accepted=accepted,
            suggestion_dismissed=not accepted,
            kernel_result_status=kernel_status,
            kernel_matched_suggestion=kernel_matched,
        )

        if knowledge_id not in self._effectiveness:
            self._effectiveness[knowledge_id] = []
        self._effectiveness[knowledge_id].append(record)

        self._metrics.record_suggestion(accepted)

    def get_effectiveness(self, knowledge_id: str) -> EffectivenessStatus:
        """Get the current effectiveness status of a knowledge item."""
        records = self._effectiveness.get(knowledge_id, [])
        return compute_effectiveness_status(records)

    def get_all_effectiveness(self) -> Dict[str, str]:
        """Get effectiveness status for all tracked knowledge items."""
        return {
            kid: compute_effectiveness_status(records).value
            for kid, records in self._effectiveness.items()
        }

    # -- Metrics --

    def record_transaction(self, status: str) -> None:
        """Record a transaction outcome for metrics."""
        self._metrics.record_transaction(status)

    def record_safety_violation(self, violation_type: str) -> None:
        """Record a safety violation."""
        self._metrics.record_safety_violation(violation_type)

    # -- Full Snapshot --

    def snapshot(self) -> Dict[str, Any]:
        """Full P3 system snapshot."""
        return {
            "knowledge_store": self._store.snapshot(),
            "metrics": self._metrics.snapshot(),
            "effectiveness": {
                kid: compute_effectiveness_status(records).value
                for kid, records in self._effectiveness.items()
            },
            "persistence_path": self._store_path,
        }
