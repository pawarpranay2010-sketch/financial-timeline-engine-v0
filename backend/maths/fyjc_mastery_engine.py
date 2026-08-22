"""
Platrixa
Sprint 15I-H - Mastery Engine
backend/maths/fyjc_mastery_engine.py

Maintains deterministic per-student mastery evidence by concept.

The mastery engine decides WHAT to practice next - it NEVER decides
whether an accounting answer is correct. Correctness comes exclusively
from the Platrixa verification path (via the Practice Engine). Every state
transition records the evidence that produced it.

Mastery states
--------------
UNSEEN     -> no attempts yet
LEARNING   -> insufficient evidence or repeated errors
DEVELOPING -> improving verified accuracy
MASTERED   -> sustained high verified accuracy across enough attempts
REVIEW     -> previously strong concept with recent degradation

Recency (section 15)
--------------------
Lifetime statistics and a bounded recent window (default 5 attempts)
are stored SEPARATELY. A student who was strong but now fails repeatedly
moves to REVIEW instead of staying MASTERED.

All thresholds are explicit configuration constants - never guessed or
tuned by opaque statistics.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

MASTERY_UNSEEN = "UNSEEN"
MASTERY_LEARNING = "LEARNING"
MASTERY_DEVELOPING = "DEVELOPING"
MASTERY_MASTERED = "MASTERED"
MASTERY_REVIEW = "REVIEW"

# Explicit deterministic policy (Sprint 15I-H section 14).
CONFIG_DEFAULT: Dict[str, Any] = {
    # Minimum attempts before DEVELOPING/MASTERED can be reached.
    "develop_min_attempts": 3,
    "master_min_attempts": 5,
    # Minimum lifetime verified accuracy for DEVELOPING / MASTERED.
    "develop_min_accuracy": 0.60,
    "master_lifetime_accuracy": 0.85,
    # Minimum accuracy within the recent window for MASTERED.
    "master_recent_accuracy": 0.80,
    # Recent-window accuracy (or consecutive mistakes) that triggers REVIEW.
    "degrade_recent_accuracy": 0.50,
    "degrade_consecutive_mistakes": 2,
    "degrade_min_attempts": 2,
    # Bounded recent window.
    "recent_window": 5,
}


class MasteryEngine:
    """Deterministic mastery tracker (pure; persistence by the caller)."""

    def __init__(self, records: Optional[Dict[str, Dict[str, Any]]] = None,
                 config: Optional[Dict[str, Any]] = None,
                 now_fn=None) -> None:
        self._records: Dict[str, Dict[str, Any]] = dict(records or {})
        self.config: Dict[str, Any] = dict(CONFIG_DEFAULT)
        if config:
            self.config.update(config)
        self._now_fn = now_fn or (lambda: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def records(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._records)

    def get(self, student_id: str, concept_key: str) -> Dict[str, Any]:
        key = self._key(student_id, concept_key)
        rec = self._records.get(key)
        if rec is None:
            return {
                "student_id": student_id,
                "concept_key": concept_key,
                "attempts": 0,
                "correct": 0,
                "incorrect": 0,
                "review_required": 0,
                "unsupported": 0,
                "accuracy": 0.0,
                "recent_accuracy": 0.0,
                "mistake_count": 0,
                "last_attempt_at": None,
                "mastery_state": MASTERY_UNSEEN,
                "recent": [],
                "transitions": [],
            }
        return dict(rec)

    def summary(self, student_id: str) -> Dict[str, Any]:
        """Deterministic per-student aggregates (section 22)."""
        recs = [r for r in self._records.values()
                if r["student_id"] == student_id]
        attempts = sum(r["attempts"] for r in recs)
        correct = sum(r["correct"] for r in recs)
        incorrect = sum(r["incorrect"] for r in recs)
        review = sum(r["review_required"] for r in recs)
        open_mistakes = sum(
            1 for r in recs if r.get("_open_mistake_count") or 0)
        states = {}
        for r in recs:
            states.setdefault(r["mastery_state"], 0)
            states[r["mastery_state"]] += 1
        return {
            "concepts_seen": len(recs),
            "attempts": attempts,
            "correct": correct,
            "incorrect": incorrect,
            "review_required": review,
            "lifetime_accuracy": (correct / max(1, correct + incorrect)),
            "mastery_states": states,
            "strongest": sorted(
                [r for r in recs if r["mastery_state"] == MASTERY_MASTERED],
                key=lambda r: r["accuracy"], reverse=True),
            "weakest": sorted(
                [r for r in recs
                 if r["mastery_state"] in (MASTERY_LEARNING, MASTERY_REVIEW)],
                key=lambda r: r["accuracy"]),
        }

    # ------------------------------------------------------------------
    # Updates (deterministic function of prior record + outcome)
    # ------------------------------------------------------------------

    def update(self, student_id: str, concept_key: str,
               outcome: str, now: Optional[str] = None) -> Dict[str, Any]:
        """Apply ONE verified attempt outcome to a concept's mastery.

        outcome is CORRECT / INCORRECT / REVIEW_REQUIRED / NOT_SUPPORTED
        (as produced by the Platrixa verification path). REVIEW_REQUIRED and
        NOT_SUPPORTED never count as correct or incorrect, never resolve
        mistakes and never move a concept toward MASTERED.
        """
        at = now or self._now_fn()
        rec = self.get(student_id, concept_key)
        key = self._key(student_id, concept_key)
        rec["attempts"] += 1
        rec["last_attempt_at"] = at
        window = list(rec.get("recent") or [])
        if outcome == "CORRECT":
            rec["correct"] += 1
            window.append(1)
        elif outcome == "INCORRECT":
            rec["incorrect"] += 1
            rec["mistake_count"] += 1
            window.append(0)
        elif outcome == "REVIEW_REQUIRED":
            rec["review_required"] += 1
            window.append(None)
        elif outcome == "NOT_SUPPORTED":
            rec["unsupported"] += 1
            window.append(None)
        else:
            raise ValueError(f"unknown outcome: {outcome!r}")
        rec["recent"] = window[-self.config["recent_window"]:]
        scored = [v for v in rec["recent"] if v is not None]
        rec["recent_accuracy"] = (
            sum(scored) / len(scored)) if scored else 0.0
        rec["accuracy"] = rec["correct"] / max(
            1, rec["correct"] + rec["incorrect"])

        prev_state = rec.get("mastery_state") or MASTERY_UNSEEN
        new_state = self._next_state(rec, prev_state)
        if new_state != prev_state:
            rec["mastery_state"] = new_state
            rec.setdefault("transitions", []).append({
                "from": prev_state, "to": new_state, "at": at,
                "evidence": {
                    "attempts": rec["attempts"],
                    "accuracy": rec["accuracy"],
                    "recent_accuracy": rec["recent_accuracy"],
                    "mistake_count": rec["mistake_count"],
                    "outcome": outcome,
                },
            })
        self._records[key] = rec
        return dict(rec)

    def set_open_mistake_count(self, student_id: str, concept_key: str,
                               count: int) -> None:
        """PracticeEngine syncs open-mistake counts into the mastery
        record so selection can see them (open mistakes are selection
        priority #1). Deterministic; never changes mastery state."""
        key = self._key(student_id, concept_key)
        rec = self._records.setdefault(key, self.get(
            student_id, concept_key))
        rec["_open_mistake_count"] = int(count)

    # ------------------------------------------------------------------

    def _next_state(self, rec: Dict[str, Any], prev_state: str) -> str:
        cfg = self.config
        if rec["attempts"] == 0:
            return MASTERY_UNSEEN
        recent = rec["recent_accuracy"]
        lifetime = rec["accuracy"]
        recent_mistakes = sum(1 for v in (rec.get("recent") or [])
                              if v == 0)
        # REVIEW: previously strong, now degrading. Sticky while the
        # degradation persists (REVIEW stays REVIEW on further bad
        # attempts; it recovers only through verified improvement).
        if prev_state in (MASTERY_MASTERED, MASTERY_DEVELOPING,
                          MASTERY_REVIEW) \
                and rec["attempts"] >= cfg["degrade_min_attempts"] \
                and (recent <= cfg["degrade_recent_accuracy"]
                     or recent_mistakes
                     >= cfg["degrade_consecutive_mistakes"]):
            return MASTERY_REVIEW
        # MASTERED: sustained high verified accuracy.
        if rec["attempts"] >= cfg["master_min_attempts"] \
                and lifetime >= cfg["master_lifetime_accuracy"] \
                and recent >= cfg["master_recent_accuracy"] \
                and (rec.get("recent") or [None])[-1] == 1:
            return MASTERY_MASTERED
        # DEVELOPING: improving, reasonable accuracy.
        if rec["attempts"] >= cfg["develop_min_attempts"] \
                and lifetime >= cfg["develop_min_accuracy"] \
                and recent >= lifetime:
            return MASTERY_DEVELOPING
        return MASTERY_LEARNING

    @staticmethod
    def _key(student_id: str, concept_key: str) -> str:
        import hashlib
        return "K-" + hashlib.sha1(
            f"{student_id}::{concept_key}".encode("utf-8")).hexdigest()[:12]
