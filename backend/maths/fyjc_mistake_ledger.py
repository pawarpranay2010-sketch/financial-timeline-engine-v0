"""
Financial Timeline Engine
Sprint 15I-H - Mistake Ledger
backend/maths/fyjc_mistake_ledger.py

Records structured learning evidence for every verified-incorrect student
attempt. A mistake is NEVER deleted; the ledger only adds evidence and
moves the record through OPEN -> IMPROVING -> RESOLVED.

Mistake identity (deterministic): a mistake is keyed by
(student_id, question_id, concept_key, mistake_category), so a repeated
error on the same question increments occurrence_count instead of
creating duplicate records. A different category on the same question is
a different mistake.

This module is PURE: it owns no persistence. The Practice Engine (or any
caller) supplies records and reads them back; storage is the caller's
concern (JSON store in fyjc_practice_engine.PracticeStore).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

MISTAKE_OPEN = "OPEN"
MISTAKE_IMPROVING = "IMPROVING"
MISTAKE_RESOLVED = "RESOLVED"

# Deterministic mistake taxonomy (Sprint 15I-H section 11).
MISTAKE_CATEGORIES = (
    "ACCOUNT_SELECTION",
    "DEBIT_CREDIT_DIRECTION",
    "AMOUNT_ERROR",
    "PARTY_ROLE_ERROR",
    "TRANSACTION_CLASSIFICATION",
    "GST_ERROR",
    "TRADE_DISCOUNT_ERROR",
    "CASH_DISCOUNT_ERROR",
    "MULTI_TRANSACTION_ERROR",
    "LEDGER_BALANCING_ERROR",
    "FORMAT_ERROR",
    "UNSUPPORTED_RESPONSE",
    "AMBIGUOUS_RESPONSE",
    "UNKNOWN",
)

# Configuration (explicit, documented - see section 14).
CONSECUTIVE_CORRECT_TO_RESOLVE = 2


class MistakeLedger:
    """In-memory mistake ledger (persistence delegated to the caller)."""

    def __init__(self, records: Optional[Dict[str, Dict[str, Any]]] = None,
                 now_fn=None) -> None:
        self._records: Dict[str, Dict[str, Any]] = dict(records or {})
        self._now_fn = now_fn or (lambda: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def records(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._records)

    def get(self, mistake_id: str) -> Optional[Dict[str, Any]]:
        rec = self._records.get(mistake_id)
        return dict(rec) if rec else None

    def open_mistakes(self, student_id: Optional[str] = None,
                      concept_key: Optional[str] = None
                      ) -> List[Dict[str, Any]]:
        out = [dict(r) for r in self._records.values()
               if r["status"] in (MISTAKE_OPEN, MISTAKE_IMPROVING)]
        if student_id is not None:
            out = [r for r in out if r["student_id"] == student_id]
        if concept_key is not None:
            out = [r for r in out if r["concept_key"] == concept_key]
        return sorted(out, key=lambda r: r["mistake_id"])

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def record(self, *, student_id: str, session_id: str,
               question_id: str, attempt_id: str, concept_key: str,
               concept: str, transaction_type: str, difficulty: Any,
               mistake_category: str, expected_journal_reference: str,
               student_response: Any,
               raw_response: str = "", now: Optional[str] = None) -> str:
        """Record (or increment) a mistake for one incorrect attempt.

        Returns the mistake_id. Recurrence on the same question+category
        increments occurrence_count and re-opens the record. A mistake
        whose category cannot be determined must be UNKNOWN - never
        guessed.
        """
        if mistake_category not in MISTAKE_CATEGORIES:
            mistake_category = "UNKNOWN"
        at = now or self._now_fn()
        mid = self._mistake_id(student_id, question_id, concept_key,
                               mistake_category)
        existing = self._records.get(mid)
        if existing is None:
            self._records[mid] = {
                "mistake_id": mid,
                "student_id": student_id,
                "session_id": session_id,
                "question_id": question_id,
                "attempt_id": attempt_id,
                "concept": concept,
                "concept_key": concept_key,
                "transaction_type": transaction_type,
                "difficulty": difficulty,
                "mistake_category": mistake_category,
                "expected_journal_reference": expected_journal_reference,
                "student_response": student_response,
                "raw_response": raw_response,
                "created_at": at,
                "last_occurrence_at": at,
                "resolved_at": None,
                "occurrence_count": 1,
                "consecutive_correct": 0,
                "status": MISTAKE_OPEN,
                "evidence": [{
                    "attempt_id": attempt_id, "at": at, "event": "occurred",
                }],
            }
        else:
            existing["occurrence_count"] += 1
            existing["last_occurrence_at"] = at
            existing["session_id"] = session_id
            existing["attempt_id"] = attempt_id
            existing["student_response"] = student_response
            existing["raw_response"] = raw_response
            existing["consecutive_correct"] = 0
            existing["status"] = MISTAKE_OPEN
            existing["resolved_at"] = None
            existing["evidence"].append({
                "attempt_id": attempt_id, "at": at, "event": "occurred",
            })
        return mid

    def record_correct(self, *, student_id: str, question_id: str,
                       attempt_id: str, now: Optional[str] = None) -> List[str]:
        """Feed a verified-correct attempt into every open mistake on the
        same question: consecutive_correct += 1; the record moves
        OPEN -> IMPROVING -> RESOLVED after CONSECUTIVE_CORRECT_TO_RESOLVE
        verified successes in a row. Historical records are never
        deleted."""
        at = now or self._now_fn()
        touched: List[str] = []
        for mid, rec in self._records.items():
            if rec["student_id"] != student_id \
                    or rec["question_id"] != question_id:
                continue
            if rec["status"] == MISTAKE_RESOLVED:
                continue
            rec["consecutive_correct"] += 1
            rec["evidence"].append({
                "attempt_id": attempt_id, "at": at,
                "event": "correct_after_mistake",
            })
            if rec["consecutive_correct"] >= CONSECUTIVE_CORRECT_TO_RESOLVE:
                rec["status"] = MISTAKE_RESOLVED
                rec["resolved_at"] = at
            elif rec["status"] == MISTAKE_OPEN:
                rec["status"] = MISTAKE_IMPROVING
            touched.append(mid)
        return touched

    # ------------------------------------------------------------------

    @staticmethod
    def _mistake_id(student_id: str, question_id: str, concept_key: str,
                    category: str) -> str:
        import hashlib
        seed = f"{student_id}::{question_id}::{concept_key}::{category}"
        return "M-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
