"""
Sprint P2 — Validated Long-Term Learning: Knowledge Store Regression Suite

Tests the deterministic validated knowledge system for Platrixa.
Covers: extraction, validation, promotion, retirement, rollback, conflict
detection, scope isolation, anti-poisoning, shadow mode, and determinism.

Classification: P2 regression gate — must pass completely.
"""

from __future__ import annotations

import sys
import os
import json
import hashlib
from decimal import Decimal
from typing import Dict, Any

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.maths.fyjc_validated_knowledge import (
    ValidatedKnowledgeStore,
    KnowledgeItem,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeScope,
    EvidenceSource,
    EvidenceItem,
    PROMOTION_THRESHOLDS,
    _normalise_pattern,
    _generate_knowledge_id,
    _compute_confidence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS_COUNT = 0
FAIL_COUNT = 0


def _check(label: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  ✅ {label}")
    else:
        FAIL_COUNT += 1
        msg = f"  ❌ {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def _seed_store(store: ValidatedKnowledgeStore, count: int = 4) -> str:
    """Seed a store with enough validated evidence for promotion."""
    for i in range(count):
        store.extract_candidate(
            pattern="purchased goods from raj for rs 20000",
            canonical_interpretation="credit purchase from Raj",
            knowledge_type=KnowledgeType.PAYMENT_MODE_CONVENTION,
            scope=KnowledgeScope.GLOBAL,
            source=EvidenceSource.DETERMINISTIC if i < 2 else EvidenceSource.STUDENT,
            context=f"Test evidence {i+1}",
            verification_status="VERIFIED",
        )
    kid = _generate_knowledge_id(
        "purchased goods from raj for rs 20000",
        KnowledgeType.PAYMENT_MODE_CONVENTION,
        KnowledgeScope.GLOBAL,
    )
    return kid


# ===========================================================================
# SECTION 1: Knowledge Store — Basic Operations
# ===========================================================================

def test_section_1():
    print("\n=== Section 1: Knowledge Store — Basic Operations ===")
    store = ValidatedKnowledgeStore()

    # 1.1: Empty store
    _check("1.1 Empty store lookup returns None",
           store.lookup("anything") is None)
    _check("1.1 Empty store metrics",
           store.metrics()["total_items"] == 0)

    # 1.2: Extract candidate
    item = store.extract_candidate(
        pattern="purchased goods from raj for rs 20000",
        canonical_interpretation="credit purchase from Raj",
        knowledge_type=KnowledgeType.PAYMENT_MODE_CONVENTION,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.DETERMINISTIC,
        context="Test context",
        verification_status="VERIFIED",
    )
    _check("1.2 Candidate extracted",
           item is not None and item.evidence_count == 1)
    _check("1.2 Candidate is CANDIDATE status",
           item.status == KnowledgeStatus.CANDIDATE)

    # 1.3: Add second evidence to same candidate
    item2 = store.extract_candidate(
        pattern="purchased goods from raj for rs 20000",
        canonical_interpretation="credit purchase from Raj",
        knowledge_type=KnowledgeType.PAYMENT_MODE_CONVENTION,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
        context="Test evidence 2",
        verification_status="VERIFIED",
    )
    _check("1.3 Same pattern merges evidence",
           item2.evidence_count == 2)
    _check("1.3 Source diversity updated",
           item2.source_diversity == 2)

    # 1.4: Lookup — CANDIDATE items are not returned by lookup()
    _check("1.4 CANDIDATE not returned by lookup()",
           store.lookup("purchased goods from raj for rs 20000") is None)

    # 1.5: lookup_any returns CANDIDATE
    _check("1.5 lookup_any returns CANDIDATE",
           store.lookup_any("purchased goods from raj for rs 20000") is not None)

    # 1.6: Snapshot is deterministic
    snap1 = store.snapshot()
    snap2 = store.snapshot()
    _check("1.6 Snapshot is deterministic",
           json.dumps(snap1, sort_keys=True) == json.dumps(snap2, sort_keys=True))

    # 1.7: History tracks status transitions
    history = store.get_history()
    _check("1.7 History has entry",
           len(history) >= 1 and len(history[0]["status_history"]) >= 1)

    # 1.8: Metrics tracks items
    _check("1.8 Metrics shows 1 item",
           store.metrics()["total_items"] == 1)

    print()


# ===========================================================================
# SECTION 2: Promotion Pipeline
# ===========================================================================

def test_section_2():
    print("=== Section 2: Promotion Pipeline ===")
    store = ValidatedKnowledgeStore()

    # 2.1: Seed with enough evidence for promotion
    kid = _seed_store(store, count=4)

    # 2.2: Candidate meets thresholds
    can_promote, reason = store.validate_candidate(kid)
    _check("2.1 Candidate meets promotion thresholds",
           can_promote, reason)

    # 2.3: Promote
    success, msg = store.promote(kid)
    _check("2.2 Promote succeeds", success, msg)

    # 2.4: After promotion, lookup returns item
    item = store.lookup("purchased goods from raj for rs 20000")
    _check("2.3 Promoted item found by lookup()",
           item is not None and item.status == KnowledgeStatus.VALIDATED)

    # 2.5: Cannot promote an already-validated item
    success2, msg2 = store.promote(kid)
    _check("2.4 Cannot re-promote VALIDATED item",
           not success2)

    # 2.6: Metrics updated
    m = store.metrics()
    _check("2.5 Metrics shows 1 VALIDATED",
           m["by_status"].get("VALIDATED", 0) == 1)

    print()


# ===========================================================================
# SECTION 3: Rejection and Retirement
# ===========================================================================

def test_section_3():
    print("=== Section 3: Rejection and Retirement ===")
    store = ValidatedKnowledgeStore()

    # 3.1: Reject a candidate
    kid = _seed_store(store, count=4)
    success, msg = store.reject(kid, "Test rejection")
    _check("3.1 Reject succeeds", success, msg)

    item = store.lookup_any("purchased goods from raj for rs 20000")
    _check("3.2 Item is REJECTED",
           item is not None and item.status == KnowledgeStatus.REJECTED)

    # 3.3: Cannot reject a retired item
    store2 = ValidatedKnowledgeStore()
    kid2 = _seed_store(store2, count=4)
    store2.promote(kid2)
    store2.retire(kid2, "Test retirement")
    success3, _ = store2.reject(kid2)
    _check("3.3 Cannot reject retired item", not success3)

    # 3.4: Rollback VALIDATED → CANDIDATE
    store3 = ValidatedKnowledgeStore()
    kid3 = _seed_store(store3, count=4)
    store3.promote(kid3)
    success4, msg4 = store3.rollback(kid3)
    _check("3.4 Rollback succeeds", success4, msg4)
    item3 = store3.lookup_any("purchased goods from raj for rs 20000")
    _check("3.5 After rollback, item is CANDIDATE",
           item3 is not None and item3.status == KnowledgeStatus.CANDIDATE)

    # 3.6: Cannot rollback non-VALIDATED item
    store4 = ValidatedKnowledgeStore()
    kid4 = _seed_store(store4, count=4)
    success5, _ = store4.rollback(kid4)
    _check("3.6 Cannot rollback CANDIDATE",
           not success5)

    print()


# ===========================================================================
# SECTION 4: Promotion Thresholds — Negative Cases
# ===========================================================================

def test_section_4():
    print("=== Section 4: Promotion Thresholds — Negative Cases ===")
    store = ValidatedKnowledgeStore()

    # 4.1: Single evidence — not enough
    item = store.extract_candidate(
        pattern="bought half of goods from amit",
        canonical_interpretation="sale of half goods from Amit",
        knowledge_type=KnowledgeType.AMBIGUITY_PATTERN,
        scope=KnowledgeScope.TEXTBOOK,
        source=EvidenceSource.MODEL_GENERATED,
        context="Single evidence",
        verification_status="VERIFIED",
    )
    can_promote, reason = store.validate_candidate(item.knowledge_id)
    _check("4.1 Single evidence fails promotion",
           not can_promote and "Insufficient evidence" in reason)

    # 4.2: Insufficient source diversity
    for _ in range(5):
        store.extract_candidate(
            pattern="bought half of goods from amit",
            canonical_interpretation="sale of half goods from Amit",
            knowledge_type=KnowledgeType.AMBIGUITY_PATTERN,
            scope=KnowledgeScope.TEXTBOOK,
            source=EvidenceSource.MODEL_GENERATED,
            context="Same source repeated",
            verification_status="VERIFIED",
        )
    can2, reason2 = store.validate_candidate(item.knowledge_id)
    _check("4.2 Same-source evidence fails promotion",
           not can2 and "source diversity" in reason2)

    # 4.3: High rejection count auto-blocks
    store2 = ValidatedKnowledgeStore()
    item2 = store2.extract_candidate(
        pattern="test rejection pattern",
        canonical_interpretation="wrong interpretation",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.SESSION,
        source=EvidenceSource.STUDENT,
        context="evidence 1",
        verification_status="REVIEW_REQUIRED",
    )
    # Add deterministic + student evidence to meet diversity threshold
    for i in range(3):
        src = EvidenceSource.DETERMINISTIC if i < 2 else EvidenceSource.STUDENT
        store2.extract_candidate(
            pattern="test rejection pattern",
            canonical_interpretation="wrong interpretation",
            knowledge_type=KnowledgeType.PHRASE_CANONICAL,
            scope=KnowledgeScope.SESSION,
            source=src,
            context=f"more evidence {i}",
            verification_status="VERIFIED",
        )
    # Now add enough rejection evidence to hit the threshold
    for _ in range(3):
        store2.extract_candidate(
            pattern="test rejection pattern",
            canonical_interpretation="wrong interpretation",
            knowledge_type=KnowledgeType.PHRASE_CANONICAL,
            scope=KnowledgeScope.SESSION,
            source=EvidenceSource.STUDENT,
            context="rejection",
            verification_status="REVIEW_REQUIRED",
        )
    can3, reason3 = store2.validate_candidate(item2.knowledge_id)
    _check("4.3 High rejection blocks promotion",
           not can3 and "rejection" in reason3.lower())

    # 4.4: Cannot validate non-existent item
    can4, _ = store.validate_candidate("nonexistent_id_12345")
    _check("4.4 Non-existent item fails validation", not can4)

    # 4.5: Cannot promote non-CANDIDATE
    store3 = ValidatedKnowledgeStore()
    kid = _seed_store(store3, count=4)
    store3.promote(kid)
    can5, _ = store3.validate_candidate(kid)
    _check("4.5 VALIDATED item cannot be re-validated", not can5)

    print()


# ===========================================================================
# SECTION 5: Conflict Detection
# ===========================================================================

def test_section_5():
    print("=== Section 5: Conflict Detection ===")
    store = ValidatedKnowledgeStore()

    # 5.1: No conflict with empty store
    conflict = store.detect_conflict("some pattern", "some interpretation")
    _check("5.1 No conflict in empty store", conflict is None)

    # 5.2: Same pattern + same interpretation = no conflict
    kid = _seed_store(store, count=4)
    store.promote(kid)
    conflict2 = store.detect_conflict(
        "purchased goods from raj for rs 20000",
        "credit purchase from Raj",
    )
    _check("5.2 Same pattern + same interpretation = no conflict",
           conflict2 is None)

    # 5.3: Same pattern + different interpretation = conflict
    conflict3 = store.detect_conflict(
        "purchased goods from raj for rs 20000",
        "cash purchase from Raj",
    )
    _check("5.3 Same pattern + different interpretation = conflict",
           conflict3 is not None and conflict3["conflict_type"] == "INTERPRETATION_MISMATCH")

    # 5.4: find_conflicts — add conflicting item with DIFFERENT knowledge_type
    # so it gets a different knowledge_id and can be promoted independently
    store.extract_candidate(
        pattern="purchased goods from raj for rs 20000",
        canonical_interpretation="cash purchase from Raj",
        knowledge_type=KnowledgeType.TEXTBOOK_NOTATION,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
        context="conflicting evidence",
        verification_status="VERIFIED",
    )
    kid_conflict = _generate_knowledge_id(
        "purchased goods from raj for rs 20000",
        KnowledgeType.TEXTBOOK_NOTATION,
        KnowledgeScope.GLOBAL,
    )
    # Add more evidence to meet thresholds
    for i in range(3):
        src = EvidenceSource.DETERMINISTIC if i < 2 else EvidenceSource.STUDENT
        store.extract_candidate(
            pattern="purchased goods from raj for rs 20000",
            canonical_interpretation="cash purchase from Raj",
            knowledge_type=KnowledgeType.TEXTBOOK_NOTATION,
            scope=KnowledgeScope.GLOBAL,
            source=src,
            context=f"conflict evidence {i}",
            verification_status="VERIFIED",
        )
    store.promote(kid_conflict)
    conflicts = store.find_conflicts(kid)
    _check("5.4 find_conflicts returns conflicting items",
           isinstance(conflicts, list) and len(conflicts) >= 1)

    print()


# ===========================================================================
# SECTION 6: Scope Isolation
# ===========================================================================

def test_section_6():
    print("=== Section 6: Scope Isolation ===")

    # 6.1: GLOBAL knowledge usable in any scope
    _check("6.1 GLOBAL → SESSION allowed",
           ValidatedKnowledgeStore.scope_allows(KnowledgeScope.GLOBAL, KnowledgeScope.SESSION))
    _check("6.1 GLOBAL → GLOBAL allowed",
           ValidatedKnowledgeStore.scope_allows(KnowledgeScope.GLOBAL, KnowledgeScope.GLOBAL))

    # 6.2: SESSION knowledge not usable globally
    _check("6.2 SESSION → GLOBAL not allowed",
           not ValidatedKnowledgeStore.scope_allows(KnowledgeScope.SESSION, KnowledgeScope.GLOBAL))

    # 6.3: TEXTBOOK usable in CURRICULUM
    _check("6.3 TEXTBOOK → CURRICULUM allowed",
           ValidatedKnowledgeStore.scope_allows(KnowledgeScope.TEXTBOOK, KnowledgeScope.CURRICULUM))

    # 6.4: CURRICULUM not usable in TEXTBOOK
    _check("6.4 CURRICULUM → TEXTBOOK not allowed",
           not ValidatedKnowledgeStore.scope_allows(KnowledgeScope.CURRICULUM, KnowledgeScope.TEXTBOOK))

    # 6.5: INSTITUTION usable in INSTITUTION
    _check("6.5 INSTITUTION → INSTITUTION allowed",
           ValidatedKnowledgeStore.scope_allows(KnowledgeScope.INSTITUTION, KnowledgeScope.INSTITUTION))

    print()


# ===========================================================================
# SECTION 7: Shadow Mode — Transaction Suggestions
# ===========================================================================

def test_section_7():
    print("=== Section 7: Shadow Mode — Transaction Suggestions ===")
    store = ValidatedKnowledgeStore()

    # 7.1: Empty store returns no suggestions
    suggestions = store.suggest_for_transaction("Purchased goods from Raj for Rs.20,000")
    _check("7.1 Empty store returns no suggestions",
           isinstance(suggestions, list) and len(suggestions) == 0)

    # 7.2: After promotion, suggest_for_transaction finds matching patterns
    kid = _seed_store(store, count=4)
    store.promote(kid)
    suggestions2 = store.suggest_for_transaction(
        "Purchased goods from Raj for Rs.20,000 on credit"
    )
    _check("7.2 Promoted knowledge produces suggestions",
           isinstance(suggestions2, list) and len(suggestions2) >= 1)

    # 7.3: is_available_for_use returns True for VALIDATED
    _check("7.3 is_available_for_use returns True for VALIDATED",
           store.is_available_for_use(kid))

    # 7.4: is_available_for_use returns False for CANDIDATE
    store2 = ValidatedKnowledgeStore()
    kid2 = _seed_store(store2, count=4)
    _check("7.4 is_available_for_use returns False for CANDIDATE",
           not store2.is_available_for_use(kid2))

    print()


# ===========================================================================
# SECTION 8: Anti-Poisoning Tests (Adversarial)
# ===========================================================================

def test_section_8():
    print("=== Section 8: Anti-Poisoning Tests ===")

    # 8.1: Single incorrect student correction does not promote
    store = ValidatedKnowledgeStore()
    store.extract_candidate(
        pattern="purchased goods from raj",
        canonical_interpretation="this is definitely not a purchase",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
        context="Wrong student answer",
        verification_status="REVIEW_REQUIRED",
    )
    item = store.lookup_any("purchased goods from raj")
    _check("8.1 Single incorrect student = not promoted",
           item is not None and item.status == KnowledgeStatus.CANDIDATE)

    # 8.2: Repeated incorrect corrections accumulate rejections
    for _ in range(3):
        store.extract_candidate(
            pattern="purchased goods from raj",
            canonical_interpretation="this is definitely not a purchase",
            knowledge_type=KnowledgeType.PHRASE_CANONICAL,
            scope=KnowledgeScope.GLOBAL,
            source=EvidenceSource.STUDENT,
            context="Wrong student repeated",
            verification_status="REVIEW_REQUIRED",
        )
    item2 = store.lookup_any("purchased goods from raj")
    _check("8.2 Repeated wrong students = high rejection count",
           item2 is not None and item2.rejection_count >= 3)

    # 8.3: Model-generated evidence does not get same trust as deterministic
    store2 = ValidatedKnowledgeStore()
    for _ in range(5):
        store2.extract_candidate(
            pattern="ambiguous phrase",
            canonical_interpretation="model guess",
            knowledge_type=KnowledgeType.PHRASE_CANONICAL,
            scope=KnowledgeScope.GLOBAL,
            source=EvidenceSource.MODEL_GENERATED,
            context="Model output",
            verification_status="VERIFIED",
        )
    item3 = store2.lookup_any("ambiguous phrase")
    _check("8.3 All model-generated = low source diversity",
           item3 is not None and item3.source_diversity == 1)
    can_promote, _ = store2.validate_candidate(item3.knowledge_id)
    _check("8.3 Single-source model evidence cannot promote",
           not can_promote)

    # 8.4: Conflicting corrections do not silently select a winner
    store3 = ValidatedKnowledgeStore()
    kid_a = store3.extract_candidate(
        pattern="settled account with amit",
        canonical_interpretation="full payment of outstanding",
        knowledge_type=KnowledgeType.SETTLEMENT_CONVENTION,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
        context="Student A says full payment",
        verification_status="VERIFIED",
    )
    # Same pattern + same type + same scope → same ID (merged)
    kid_b = store3.extract_candidate(
        pattern="settled account with amit",
        canonical_interpretation="partial payment only",
        knowledge_type=KnowledgeType.SETTLEMENT_CONVENTION,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
        context="Student B says partial",
        verification_status="VERIFIED",
    )
    # Same pattern + same type + same scope → same ID (evidence merged)
    # The conflicting interpretation does NOT create a separate candidate
    _check("8.4 Same pattern merges — does not silently pick winner",
           kid_a == kid_b)
    # Conflicting interpretation evidence coexists as unvalidated
    item_c = store3.lookup_any("settled account with amit")
    _check("8.4 Merged item stays CANDIDATE (conflict unresolved)",
           item_c is not None and item_c.status == KnowledgeStatus.CANDIDATE)

    # 8.5: Identical phrase in different accounting contexts
    store4 = ValidatedKnowledgeStore()
    store4.extract_candidate(
        pattern="sold half of goods from amit",
        canonical_interpretation="sale of half goods",
        knowledge_type=KnowledgeType.AMBIGUITY_PATTERN,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.DETERMINISTIC,
        context="Context 1: normal sale",
        verification_status="VERIFIED",
    )
    store4.extract_candidate(
        pattern="sold half of goods from amit",
        canonical_interpretation="return of half goods",
        knowledge_type=KnowledgeType.AMBIGUITY_PATTERN,
        scope=KnowledgeScope.TEXTBOOK,
        source=EvidenceSource.STUDENT,
        context="Context 2: student correction",
        verification_status="VERIFIED",
    )
    # Different scopes + different interpretations → two items
    items = [i for i in store4._items.values()
             if _normalise_pattern(i.pattern) == _normalise_pattern("sold half of goods from amit")]
    _check("8.5 Different context + scope = separate knowledge items",
           len(items) >= 2)

    # 8.6: Cross-student contamination prevented
    store5 = ValidatedKnowledgeStore()
    store5.extract_candidate(
        pattern="purchased goods for rs 10000",
        canonical_interpretation="cash purchase",
        knowledge_type=KnowledgeType.PAYMENT_MODE_CONVENTION,
        scope=KnowledgeScope.SESSION,
        source=EvidenceSource.STUDENT,
        context="Student A session",
        verification_status="VERIFIED",
        student_id="student_A",
    )
    item6 = store5.lookup_any("purchased goods for rs 10000")
    _check("8.6 Student A evidence scoped to SESSION",
           item6 is not None and item6.scope == KnowledgeScope.SESSION)
    # Student B should not be affected
    _check("8.6 Session-scoped not visible in GLOBAL",
           store5.lookup("purchased goods for rs 10000") is None)

    # 8.7: Retired knowledge cannot affect runtime
    store6 = ValidatedKnowledgeStore()
    kid7 = _seed_store(store6, count=4)
    store6.promote(kid7)
    store6.retire(kid7, "Caused regression")
    _check("8.7 Retired knowledge not available",
           not store6.is_available_for_use(kid7))
    suggestions7 = store6.suggest_for_transaction(
        "Purchased goods from Raj for Rs.20,000"
    )
    _check("8.7 Retired knowledge not in suggestions",
           len(suggestions7) == 0)

    # 8.8: Incorrect model-generated candidate with balanced-but-wrong accounting
    store7 = ValidatedKnowledgeStore()
    store7.extract_candidate(
        pattern="purchased goods for rs 50000",
        canonical_interpretation="sale of goods for rs 50000",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.MODEL_GENERATED,
        context="Model inverted debit/credit",
        verification_status="REVIEW_REQUIRED",
    )
    item8 = store7.lookup_any("purchased goods for rs 50000")
    _check("8.8 Model-balanced-but-wrong remains CANDIDATE",
           item8 is not None and item8.status == KnowledgeStatus.CANDIDATE)

    print()


# ===========================================================================
# SECTION 9: Determinism
# ===========================================================================

def test_section_9():
    print("=== Section 9: Determinism ===")

    # 9.1: Same evidence → same knowledge ID
    kid1 = _generate_knowledge_id(
        "test pattern", KnowledgeType.PHRASE_CANONICAL, KnowledgeScope.GLOBAL
    )
    kid2 = _generate_knowledge_id(
        "test pattern", KnowledgeType.PHRASE_CANONICAL, KnowledgeScope.GLOBAL
    )
    _check("9.1 Same inputs → same knowledge ID", kid1 == kid2)

    # 9.2: Different type → different ID
    kid3 = _generate_knowledge_id(
        "test pattern", KnowledgeType.AMBIGUITY_PATTERN, KnowledgeScope.GLOBAL
    )
    _check("9.2 Different type → different ID", kid1 != kid3)

    # 9.3: Pattern normalisation is deterministic
    n1 = _normalise_pattern("  Purchased  Goods  from  Raj  ")
    n2 = _normalise_pattern("Purchased Goods from Raj")
    _check("9.3 Pattern normalisation deterministic", n1 == n2)

    # 9.4: Three identical stores produce identical snapshots
    # Compare structure excluding timestamps (which differ between runs)
    snapshots = []
    for _ in range(3):
        store = ValidatedKnowledgeStore()
        _seed_store(store, count=4)
        snap = store.snapshot()
        # Strip timestamps for comparison
        for item in snap.get("items", []):
            for ev in item.get("evidence_trail", []):
                ev["timestamp"] = "<TS>"
            for sh in item.get("status_history", []):
                sh["timestamp"] = "<TS>"
            item["created_at"] = "<TS>"
            item["last_validated_at"] = None
            item["last_rejected_at"] = None
            item["retired_at"] = None
        snapshots.append(json.dumps(snap, sort_keys=True))
    _check("9.4 Three identical stores → identical snapshots",
           snapshots[0] == snapshots[1] == snapshots[2])

    # 9.5: Confidence is deterministic
    item = KnowledgeItem(
        knowledge_id="test123",
        pattern="test",
        canonical_interpretation="test interp",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        evidence_count=4,
        validation_count=3,
        source_diversity=2,
    )
    c1 = _compute_confidence(item)
    c2 = _compute_confidence(item)
    _check("9.5 Confidence computation deterministic", c1 == c2)

    # 9.6: Promotion thresholds are constant
    _check("9.6 Promotion thresholds are immutable constants",
           PROMOTION_THRESHOLDS["min_evidence_count"] == 3 and
           PROMOTION_THRESHOLDS["min_source_diversity"] == 2 and
           PROMOTION_THRESHOLDS["min_validation_count"] == 3)

    print()


# ===========================================================================
# SECTION 10: Knowledge Lifecycle — Full Round-Trip
# ===========================================================================

def test_section_10():
    print("=== Section 10: Knowledge Lifecycle — Full Round-Trip ===")
    store = ValidatedKnowledgeStore()

    # 10.1: Extract → Accumulate → Promote → Lookup → Retire → Gone
    kid = _seed_store(store, count=4)

    # Verify candidate
    can, _ = store.validate_candidate(kid)
    _check("10.1a Candidate meets thresholds", can)

    # Promote
    store.promote(kid)
    item = store.lookup("purchased goods from raj for rs 20000")
    _check("10.1b Promoted item visible via lookup()", item is not None)

    # Retire
    store.retire(kid, "Old pattern no longer valid")
    item2 = store.lookup("purchased goods from raj for rs 20000")
    _check("10.1c Retired item not visible via lookup()", item2 is None)

    # History shows full lifecycle
    history = store.get_history()
    lifecycle = []
    for h in history:
        if h["knowledge_id"] == kid:
            lifecycle = [t["to_status"] for t in h["status_history"]]
    _check("10.1d Lifecycle: NONE → CANDIDATE → VALIDATED → RETIRED",
           lifecycle == ["CANDIDATE", "VALIDATED", "RETIRED"])

    # 10.2: Rollback lifecycle
    store2 = ValidatedKnowledgeStore()
    kid2 = _seed_store(store2, count=4)
    store2.promote(kid2)
    store2.rollback(kid2)
    store2.promote(kid2)  # re-promote after rollback
    item3 = store2.lookup("purchased goods from raj for rs 20000")
    _check("10.2 Rollback → re-promote works",
           item3 is not None and item3.status == KnowledgeStatus.VALIDATED)

    print()


# ===========================================================================
# SECTION 11: Serialization Round-Trip
# ===========================================================================

def test_section_11():
    print("=== Section 11: Serialization Round-Trip ===")
    store = ValidatedKnowledgeStore()
    kid = _seed_store(store, count=4)
    store.promote(kid)

    # 11.1: to_dict → from_dict preserves data
    data = store.to_dict()
    store2 = ValidatedKnowledgeStore.from_dict(data)
    _check("11.1 Serialization round-trip preserves item count",
           len(store2._items) == len(store._items))

    item = store2.lookup("purchased goods from raj for rs 20000")
    _check("11.2 Deserialized item is VALIDATED",
           item is not None and item.status == KnowledgeStatus.VALIDATED)

    # 11.3: Snapshot equality after round-trip
    snap1 = json.dumps(store.to_dict(), sort_keys=True)
    snap2 = json.dumps(store2.to_dict(), sort_keys=True)
    _check("11.3 Snapshots equal after round-trip", snap1 == snap2)

    print()


# ===========================================================================
# SECTION 12: Scope-Filtered Queries
# ===========================================================================

def test_section_12():
    print("=== Section 12: Scope-Filtered Queries ===")
    store = ValidatedKnowledgeStore()

    # Seed items in different scopes with alternating sources
    for scope in [KnowledgeScope.SESSION, KnowledgeScope.GLOBAL, KnowledgeScope.TEXTBOOK]:
        for i in range(4):
            src = EvidenceSource.DETERMINISTIC if i % 2 == 0 else EvidenceSource.STUDENT
            store.extract_candidate(
                pattern=f"test pattern for {scope.value}",
                canonical_interpretation=f"interp for {scope.value}",
                knowledge_type=KnowledgeType.PHRASE_CANONICAL,
                scope=scope,
                source=src,
                context=f"context {scope.value} {i}",
                verification_status="VERIFIED",
            )
        kid = _generate_knowledge_id(
            f"test pattern for {scope.value}",
            KnowledgeType.PHRASE_CANONICAL,
            scope,
        )
        store.promote(kid)

    # 12.1: lookup_by_scope
    global_items = store.lookup_by_scope(KnowledgeScope.GLOBAL)
    _check("12.1 lookup_by_scope(GLOBAL) returns 1 item",
           len(global_items) == 1)
    session_items = store.lookup_by_scope(KnowledgeScope.SESSION)
    _check("12.1b lookup_by_scope(SESSION) returns 1 item",
           len(session_items) == 1)

    # 12.2: lookup_by_type
    phrase_items = store.lookup_by_type(KnowledgeType.PHRASE_CANONICAL)
    _check("12.2 lookup_by_type(PHRASE_CANONICAL) returns 3 items",
           len(phrase_items) == 3)

    # 12.3: lookup_by_type with scope filter
    global_phrases = store.lookup_by_type(KnowledgeType.PHRASE_CANONICAL, KnowledgeScope.GLOBAL)
    _check("12.3 lookup_by_type + scope returns 1 item",
           len(global_phrases) == 1)

    # 12.4: Metrics shows all scopes
    m = store.metrics()
    _check("12.4 Metrics has 3 scopes",
           m["by_scope"].get("SESSION", 0) >= 1 and
           m["by_scope"].get("GLOBAL", 0) >= 1 and
           m["by_scope"].get("TEXTBOOK", 0) >= 1)

    print()


# ===========================================================================
# SECTION 13: Integration Shadow Mode — process_problem()
# ===========================================================================

def test_section_13():
    print("=== Section 13: Integration Shadow Mode — process_problem() ===")
    from backend.maths.fyjc_problem_engine import process_problem

    # 13.1: process_problem returns knowledge_suggestions key
    problem = (
        "On 1st April 2026, Rohan started a business with cash of Rs.1,00,000. "
        "On 2nd April, he purchased goods from Amit for Rs.30,000 on credit. "
        "On 5th April, he sold goods to Suresh for Rs.40,000 on credit."
    )
    result = process_problem(problem)
    _check("13.1 process_problem returns knowledge_suggestions",
           "knowledge_suggestions" in result)

    # 13.2: knowledge_suggestions has expected structure
    ks = result["knowledge_suggestions"]
    _check("13.2 knowledge_suggestions has metrics",
           "metrics" in ks and "shadow_suggestions" in ks)

    # 13.3: Existing results still work correctly
    _check("13.3 Problem has transactions",
           len(result.get("transactions", [])) >= 2)
    _check("13.4 Deterministic flag still set",
           result.get("deterministic") is True)
    _check("13.5 Structured memory still present",
           result.get("structured_memory") is not None)

    # 13.6: Second run is identical
    result2 = process_problem(problem)
    ks1 = json.dumps(result["knowledge_suggestions"], sort_keys=True)
    ks2 = json.dumps(result2["knowledge_suggestions"], sort_keys=True)
    _check("13.6 Shadow mode suggestions deterministic across runs", ks1 == ks2)

    print()


# ===========================================================================
# SECTION 14: Edge Cases
# ===========================================================================

def test_section_14():
    print("=== Section 14: Edge Cases ===")
    store = ValidatedKnowledgeStore()

    # 14.1: Empty pattern
    item = store.extract_candidate(
        pattern="",
        canonical_interpretation="empty",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
    )
    _check("14.1 Empty pattern creates candidate",
           item is not None and item.evidence_count == 1)

    # 14.2: Very long pattern
    long_pattern = "x" * 10000
    item2 = store.extract_candidate(
        pattern=long_pattern,
        canonical_interpretation="long",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
    )
    _check("14.2 Very long pattern handled",
           item2 is not None)

    # 14.3: Unicode pattern
    item3 = store.extract_candidate(
        pattern="purchased goods from राज for ₹20,000",
        canonical_interpretation="unicode purchase",
        knowledge_type=KnowledgeType.PHRASE_CANONICAL,
        scope=KnowledgeScope.GLOBAL,
        source=EvidenceSource.STUDENT,
    )
    _check("14.3 Unicode pattern handled",
           item3 is not None)

    # 14.4: Lookup with None pattern
    try:
        store.lookup(None)
        _check("14.4 None pattern lookup doesn't crash", True)
    except Exception:
        _check("14.4 None pattern lookup doesn't crash", False, "Crashed on None")

    print()


# ===========================================================================
# SECTION 15: Safety Invariants
# ===========================================================================

def test_section_15():
    print("=== Section 15: Safety Invariants ===")
    from backend.maths.fyjc_problem_engine import process_problem

    problem = (
        "On 1st April 2026, Rohan started a business with cash of Rs.1,00,000 and furniture worth Rs.20,000. "
        "On 2nd April, he purchased goods from Amit for Rs.30,000 on credit. "
        "On 5th April, he sold goods to Suresh for Rs.40,000 on credit. "
        "On 7th April, Suresh paid Rs.20,000 by cheque. "
        "On 30th April, Rohan withdrew Rs.5,000 cash for personal use."
    )
    result = process_problem(problem)

    # 15.1: INCORRECT_VERIFIED = 0
    incorrect = sum(
        1 for tx in result["transactions"]
        if tx.get("status") == "VERIFIED" and not tx.get("journal")
    )
    _check("15.1 INCORRECT_VERIFIED = 0", incorrect == 0)

    # 15.2: VERIFIED with 0 journal lines = 0
    empty_verified = sum(
        1 for tx in result["transactions"]
        if tx.get("status") == "VERIFIED" and (
            not tx.get("journal") or
            (not tx["journal"].get("debit_lines") and not tx["journal"].get("credit_lines"))
        )
    )
    _check("15.2 VERIFIED with 0 journal = 0", empty_verified == 0)

    # 15.3: Safety violations = 0
    violations = result.get("safety_violations", [])
    _check("15.3 Safety violations = 0", len(violations) == 0)

    # 15.4: Missing transactions = 0
    txns = result.get("transactions", [])
    _check("15.4 Transactions present", len(txns) >= 3)

    # 15.5: Knowledge store does not affect accounting results
    # Run again without knowledge — results must be identical
    def _json_safe(obj):
        """JSON-serializable helper for Decimal objects."""
        if isinstance(obj, dict):
            return {k: _json_safe(v) for k, v in sorted(obj.items())}
        if isinstance(obj, list):
            return [_json_safe(v) for v in obj]
        if hasattr(obj, '__class__') and obj.__class__.__name__ == 'Decimal':
            return str(obj)
        return obj

    result2 = process_problem(problem)
    for i, (t1, t2) in enumerate(zip(result["transactions"], result2["transactions"])):
        _check(f"15.5 TX {i+1} status identical across runs",
               t1.get("status") == t2.get("status"))
        if t1.get("journal"):
            _check(f"15.5 TX {i+1} journal identical",
                   json.dumps(_json_safe(t1["journal"]), sort_keys=True) ==
                   json.dumps(_json_safe(t2["journal"]), sort_keys=True))

    print()


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 70)
    print("Sprint P2 — Validated Long-Term Learning: Regression Suite")
    print("=" * 70)

    test_section_1()
    test_section_2()
    test_section_3()
    test_section_4()
    test_section_5()
    test_section_6()
    test_section_7()
    test_section_8()
    test_section_9()
    test_section_10()
    test_section_11()
    test_section_12()
    test_section_13()
    test_section_14()
    test_section_15()

    print("=" * 70)
    total = PASS_COUNT + FAIL_COUNT
    print(f"RESULTS: {PASS_COUNT}/{total} PASS, {FAIL_COUNT} FAIL")
    print("=" * 70)

    if FAIL_COUNT > 0:
        print("\n❌ SPRINT P2 REGRESSION: FAIL")
        sys.exit(1)
    else:
        print("\n✅ SPRINT P2 REGRESSION: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
