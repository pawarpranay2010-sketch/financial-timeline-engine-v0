"""
Financial Timeline Engine
Sprint 15I-H - Student Practice Engine
backend/maths/fyjc_practice_engine.py

Closed deterministic learning loop:

    APPROVED QUESTION (Question Bank)
        -> PRACTICE SESSION
        -> STUDENT RESPONSE
        -> FT-E VERIFICATION (canonical verified journal = authority)
        -> CORRECT / INCORRECT / REVIEW_REQUIRED / NOT_SUPPORTED
        -> STRUCTURED MISTAKE RECORD (mistake ledger)
        -> MASTERY UPDATE (mastery engine)
        -> NEXT QUESTION SELECTION

Architectural boundaries (non-negotiable)
-----------------------------------------
* The FT-E reasoning engine (through the verified Question Bank) is the
  ONLY authority for journal correctness. This engine compares a student
  journal against the bank entry's canonical expected_journal - it
  contains NO accounting rules of its own.
* Question selection consumes ONLY approved Question Bank entries
  (list_approved / filter_by_*). DRAFT / COMPILED / VALIDATING /
  REJECTED / REVIEW_REQUIRED content is never selected.
* The mastery engine decides WHAT to practice next; it never decides
  whether an answer is correct.
* RAW student responses are preserved verbatim. Normalization is
  presentation-safe only.
* Determinism: given the same bank, student history, responses and
  configuration (including the RNG seed), every decision is identical.
  Random selection uses an explicitly seeded random.Random.

The legacy fyjc_accounting.verify_journal_entry path is intentionally
NOT the verification authority here: it drives a separate, un-hardened
pattern engine (classify_transaction) whose reference can contradict the
hardened canonical journal stored in the Question Bank. Correctness is
therefore always decided against the canonical verified journal.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_accounting import canonical_account
from backend.maths.normalization import parse_numeric_text
from backend.maths.fyjc_mistake_ledger import (
    MISTAKE_OPEN,
    MISTAKE_IMPROVING,
    MISTAKE_RESOLVED,
    MistakeLedger,
)
from backend.maths.fyjc_mastery_engine import (
    MASTERY_LEARNING,
    MASTERY_MASTERED,
    MASTERY_REVIEW,
    MASTERY_DEVELOPING,
    MasteryEngine,
)

# ---------------------------------------------------------------------------
# Session / outcome vocabulary
# ---------------------------------------------------------------------------

SESSION_CREATED = "CREATED"
SESSION_ACTIVE = "ACTIVE"
SESSION_PAUSED = "PAUSED"
SESSION_COMPLETED = "COMPLETED"
SESSION_ABANDONED = "ABANDONED"

OUTCOME_CORRECT = "CORRECT"
OUTCOME_INCORRECT = "INCORRECT"
OUTCOME_REVIEW_REQUIRED = "REVIEW_REQUIRED"
OUTCOME_NOT_SUPPORTED = "NOT_SUPPORTED"

# Practice modes (section 19).
MODE_NORMAL = "NORMAL"
MODE_WEAKNESS = "WEAKNESS"
MODE_CHAPTER = "CHAPTER"
MODE_EXAM_MIX = "EXAM_MIX"
MODE_MISTAKE_RETRY = "MISTAKE_RETRY"
MODE_REVISION = "REVISION"
MODES = (MODE_NORMAL, MODE_WEAKNESS, MODE_CHAPTER, MODE_EXAM_MIX,
         MODE_MISTAKE_RETRY, MODE_REVISION)

# Difficulty bands (bank difficulty 1/2/3).
DIFFICULTY_EASY = "EASY"
DIFFICULTY_MEDIUM = "MEDIUM"
DIFFICULTY_HARD = "HARD"
_DIFF_BAND = {1: DIFFICULTY_EASY, 2: DIFFICULTY_MEDIUM,
              3: DIFFICULTY_HARD}

# Anti-repetition / progression configuration (explicit, deterministic).
CONSECUTIVE_FAILURES_TO_LOWER = 2
CONSECUTIVE_SUCCESSES_TO_RAISE = 3
REVISION_SPACING_SECONDS = 7 * 24 * 3600  # 7 days default (injectable clock)

_UNSUPPORTED_HINTS = (
    "partnership", "balance sheet", "trading account",
    "profit and loss account", "i don't know", "skip", "not sure",
)


class PracticeStore:
    """Minimal JSON persistence for sessions / attempts / mistakes /
    mastery. The smallest repository-consistent storage abstraction
    (the repo's FYJC layer uses JSON + session state; no student-learning
    database exists). Tests always use isolated temp files."""

    def __init__(self, store_path: str) -> None:
        self.store_path = store_path
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.attempts: Dict[str, Dict[str, Any]] = {}
        self.mistakes: Dict[str, Dict[str, Any]] = {}
        self.mastery: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.store_path):
            return
        with open(self.store_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return
        self.sessions = data.get("sessions") or {}
        self.attempts = data.get("attempts") or {}
        self.mistakes = data.get("mistakes") or {}
        self.mastery = data.get("mastery") or {}

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        payload = {
            "schema_version": "15I-H-1",
            "sessions": self.sessions,
            "attempts": self.attempts,
            "mistakes": self.mistakes,
            "mastery": self.mastery,
        }
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        return self.store_path


class PracticeEngine:
    """Deterministic student practice orchestration."""

    def __init__(self, bank, store_path: str,
                 rng_seed: Optional[int] = None,
                 now_fn=None) -> None:
        self.bank = bank
        self.store = PracticeStore(store_path)
        self._now_fn = now_fn or (lambda: time.time())
        self._rng = random.Random(rng_seed)
        self.ledger = MistakeLedger(self.store.mistakes,
                                    now_fn=self._now)
        self.mastery = MasteryEngine(self.store.mastery,
                                     now_fn=self._now)

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(self._now_fn()))

    def _clock(self) -> float:
        return float(self._now_fn())

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, student_id: str, mode: str = MODE_NORMAL,
                       chapter: Optional[str] = None,
                       concept: Optional[str] = None,
                       transaction_type: Optional[str] = None,
                       difficulty: Optional[Any] = None,
                       question_count: Optional[int] = None,
                       objective: Optional[str] = None) -> str:
        if mode not in MODES:
            raise ValueError(f"unknown practice mode: {mode!r}")
        sid = "S-" + hashlib.sha1(
            f"{student_id}::{mode}::{self._clock()}::{self._rng.random()}"
            .encode("utf-8")).hexdigest()[:12]
        self.store.sessions[sid] = {
            "session_id": sid,
            "student_id": student_id,
            "mode": mode,
            "objective": objective,
            "chapter": chapter,
            "concept": concept,
            "transaction_type": transaction_type,
            "difficulty": difficulty,
            "question_count": question_count,
            "started_at": self._now(),
            "ended_at": None,
            "question_ids": [],
            "current_index": 0,
            "attempts": 0,
            "completed_count": 0,
            "correct_count": 0,
            "incorrect_count": 0,
            "review_required_count": 0,
            "status": SESSION_ACTIVE,
        }
        self.store.save()
        return sid

    def get_session(self, session_id: str) -> Dict[str, Any]:
        s = self.store.sessions.get(session_id)
        if s is None:
            raise KeyError(f"unknown session: {session_id}")
        return dict(s)

    def pause_session(self, session_id: str) -> None:
        s = self._session(session_id)
        if s["status"] == SESSION_ACTIVE:
            s["status"] = SESSION_PAUSED
            self.store.save()

    def resume_session(self, session_id: str) -> None:
        s = self._session(session_id)
        if s["status"] == SESSION_PAUSED:
            s["status"] = SESSION_ACTIVE
            self.store.save()

    def complete_session(self, session_id: str) -> Dict[str, Any]:
        s = self._session(session_id)
        if s["status"] == SESSION_COMPLETED:
            return dict(s)
        s["status"] = SESSION_COMPLETED
        s["ended_at"] = self._now()
        self.store.save()
        return dict(s)

    def abandon_session(self, session_id: str) -> Dict[str, Any]:
        s = self._session(session_id)
        if s["status"] == SESSION_COMPLETED:
            raise ValueError("cannot abandon a completed session")
        s["status"] = SESSION_ABANDONED
        s["ended_at"] = self._now()
        self.store.save()
        return dict(s)

    # ------------------------------------------------------------------
    # Question selection
    # ------------------------------------------------------------------

    def select_next(self, session_id: str, personalizer=None) -> str:
        """Deterministic next-question selection for a session.

        Priority ladder (section 16), each tier deterministic:
          1. open high-frequency mistakes
          2. weak concepts (LEARNING low accuracy / REVIEW)
          3. recently degraded concepts (REVIEW)
          4. difficulty targeting from recent session performance
          5. spaced revision (REVISION mode)
          6. normal mixed practice (seeded random; prefers unseen
             variants of already-answered canonicals)

        Anti-repetition (section 17): a question already answered in this
        session is only repeated for remediation / mistake retry when it
        carries an open mistake, or when nothing else remains.

        Sprint 15I-N hook (additive): when a PersonalizationEngine
        (`personalizer`) is supplied, its deterministic ranked selection
        is tried first (within the same approved-only scoped pool). The
        personalizer NEVER verifies answers or mutates content - it only
        decides WHAT to practice next; the ladder below remains the
        fallback, and FT-E verification is untouched. With no personalizer
        the selection is byte-identical to 15I-H behavior.
        """
        s = self._session(session_id)
        if s["status"] != SESSION_ACTIVE:
            raise ValueError(
                f"cannot select a question for a {s['status']} session")
        pool = self._scoped_pool(s)
        if not pool:
            raise ValueError("no approved questions match the session scope")
        answered = set(s.get("question_ids") or [])
        attempted = {a["question_id"]
                     for a in self.store.attempts.values()
                     if a["session_id"] == session_id}
        answered |= attempted
        open_mistakes = self.ledger.open_mistakes(
            student_id=s["student_id"])

        # Sprint 15I-N: optional personalization-driven selection. The
        # pick must be in the scoped pool and must respect anti-repetition
        # (the personalizer already filters answered questions; repeats are
        # only honoured for mistake-retry remediation).
        if personalizer is not None and s["mode"] != MODE_MISTAKE_RETRY:
            # MISTAKE_RETRY keeps the 15I-H ladder (open-mistake repeats
            # are its job); the personalizer never overrides remediation.
            pick = personalizer.select_question(
                student_id=s["student_id"],
                attempts=self.store.attempts,
                mistakes=self.ledger.records(),
                mastery=self.mastery.records(),
                pool=pool, session=s, answered=sorted(answered),
                clock=self._clock())
            if pick is not None and pick.get("question_id") in pool:
                qid = pick["question_id"]
                if qid not in answered or s["mode"] == MODE_MISTAKE_RETRY \
                        or len(pool) == 1:
                    s.setdefault("selection_reasons", []).append({
                        "question_id": qid,
                        "objective": pick.get("objective"),
                        "reason": pick.get("reason"),
                        "evidence": pick.get("evidence") or [],
                        "rank": pick.get("rank"),
                    })
                    return self._pick(s, [qid])

        # Tier 1: open high-frequency mistakes.
        if s["mode"] in (MODE_WEAKNESS, MODE_MISTAKE_RETRY):
            by_freq = sorted(open_mistakes,
                             key=lambda m: (-m["occurrence_count"],
                                            m["question_id"]))
            for mistake in by_freq:
                qid = mistake["question_id"]
                if qid in pool and (qid not in answered):
                    return self._pick(s, [qid])
                if qid in pool and s["mode"] == MODE_MISTAKE_RETRY:
                    return self._pick(s, [qid])  # remediation allows repeat

        # Tier 2/3: weak / degraded concepts.
        weak = self._weak_concept_questions(s["student_id"], pool)
        if weak and s["mode"] in (MODE_WEAKNESS, MODE_MISTAKE_RETRY,
                                  MODE_NORMAL):
            return self._pick(s, weak)

        # Tier 4: difficulty targeting.
        targeted = self._difficulty_target(s, pool)
        if targeted and len(targeted) >= 2:
            return self._pick(s, targeted)

        # Tier 5: spaced revision.
        if s["mode"] == MODE_REVISION:
            due = self._due_for_revision(s["student_id"], pool)
            if due:
                return self._pick(s, due)

        # Tier 6: normal mixed - prefer unseen variants of answered
        # canonicals (section 17), then any remaining, seeded random.
        remaining = [qid for qid in pool if qid not in answered]
        if not remaining:
            remaining = [qid for qid in pool]
        variants = [qid for qid in remaining if self._is_variant(qid)
                    and self._canonical_answered(qid, answered)]
        if variants:
            return self._pick(s, variants)
        return self._pick(s, remaining)

    def _pick(self, s: Dict[str, Any], qids: List[str]) -> str:
        qid = self._rng.choice(sorted(qids))
        s.setdefault("question_ids", []).append(qid)
        s["current_index"] = len(s["question_ids"])
        self.store.save()
        return qid

    # ------------------------------------------------------------------
    # Scoped candidate pool (approved content only)
    # ------------------------------------------------------------------

    def _scoped_pool(self, s: Dict[str, Any]) -> List[str]:
        approved = self.bank.list_approved()
        pool = [q["question_id"] for q in approved]
        if s.get("chapter"):
            pool = [q["question_id"] for q in approved
                    if q.get("chapter") == s["chapter"]]
        if s.get("concept"):
            pool = [q["question_id"] for q in approved
                    if q.get("concept_key") == s["concept"]
                    or q.get("concept") == s["concept"]]
        if s.get("transaction_type"):
            pool = [q["question_id"] for q in approved
                    if s["transaction_type"] in
                    (q.get("transaction_types") or [])]
        if s.get("difficulty") is not None:
            pool = [q["question_id"] for q in approved
                    if q.get("difficulty") == s["difficulty"]]
        return sorted(set(pool))

    def _weak_concept_questions(self, student_id: str,
                                pool: List[str]) -> List[str]:
        weak_concepts = set()
        for rec in self.mastery.records().values():
            if rec["student_id"] != student_id:
                continue
            state = rec["mastery_state"]
            if state in (MASTERY_REVIEW, MASTERY_LEARNING) and \
                    rec["accuracy"] < 0.75:
                weak_concepts.add(rec["concept_key"])
        out = [qid for qid in pool
               if self._concept_of(qid) in weak_concepts]
        return sorted(out)

    def _difficulty_target(self, s: Dict[str, Any],
                           pool: List[str]) -> List[str]:
        attempts = [a for a in self.store.attempts.values()
                    if a["session_id"] == s["session_id"]]
        outcomes = [a["outcome"] for a in attempts]
        consec_incorrect = 0
        consec_correct = 0
        for o in reversed(outcomes):
            if o == OUTCOME_INCORRECT:
                consec_incorrect += 1
                consec_correct = 0
            elif o == OUTCOME_CORRECT:
                consec_correct += 1
                consec_incorrect = 0
            else:
                break
        # Current difficulty band: the session's fixed difficulty when
        # set, else the last answered question's band, else EASY - so
        # progression works even for open (unscoped) sessions.
        base = s.get("difficulty")
        if base is None:
            last_qid = (s.get("question_ids") or [None])[-1]
            if last_qid:
                try:
                    base = self._question(last_qid).get("difficulty")
                except KeyError:
                    base = None
            if base is None:
                base = 1
        if consec_incorrect >= CONSECUTIVE_FAILURES_TO_LOWER:
            target = _DIFF_BAND.get(self._lower_band(base))
        elif consec_correct >= CONSECUTIVE_SUCCESSES_TO_RAISE:
            target = _DIFF_BAND.get(self._raise_band(base))
        else:
            target = None
        if target is None:
            return []
        return sorted(qid for qid in pool
                      if self._difficulty_of(qid) == target)

    @staticmethod
    def _lower_band(difficulty: Any) -> Optional[int]:
        mapping = {1: None, 2: 1, 3: 2}
        return mapping.get(difficulty)

    @staticmethod
    def _raise_band(difficulty: Any) -> Optional[int]:
        mapping = {1: 2, 2: 3, 3: None}
        return mapping.get(difficulty)

    def _due_for_revision(self, student_id: str,
                          pool: List[str]) -> List[str]:
        now = self._clock()
        due: List[str] = []
        for qid in pool:
            q = self._question(qid)
            rec = self.mastery.get(student_id, q.get("concept_key")
                                   or "UNKNOWN")
            if rec["mastery_state"] not in (MASTERY_MASTERED,
                                            MASTERY_DEVELOPING):
                continue
            last = rec.get("last_attempt_at")
            if last is None:
                due.append(qid)
                continue
            import calendar
            try:
                last_ts = calendar.timegm(
                    time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))
            except ValueError:
                due.append(qid)
                continue
            if now - last_ts >= REVISION_SPACING_SECONDS:
                due.append(qid)
        return sorted(due)

    # ------------------------------------------------------------------
    # Answer submission
    # ------------------------------------------------------------------

    def submit_answer(self, session_id: str, question_id: str,
                      debit_accounts: List[Any],
                      debit_amounts: List[Any],
                      credit_accounts: List[Any],
                      credit_amounts: List[Any],
                      raw_response: str = "",
                      now: Optional[str] = None) -> Dict[str, Any]:
        """Verify one student journal response and update the learning
        loop. Never overwrites historical attempts; the raw response is
        stored verbatim."""
        s = self._session(session_id)
        if s["status"] != SESSION_ACTIVE:
            raise ValueError(
                f"cannot submit to a {s['status']} session")
        question = self._approved_question(question_id)
        self._assert_in_scope(s, question)

        entry = self._build_entry(debit_accounts, debit_amounts,
                                  credit_accounts, credit_amounts)
        canonical = question.get("expected_journal") or {}
        outcome, category, verification_status, metadata = self._verify(
            canonical, entry, raw_response, question)

        at = now or self._now()
        aid = "A-" + hashlib.sha1(
            f"{session_id}::{question_id}::{s['attempts']}::{at}"
            .encode("utf-8")).hexdigest()[:12]
        mistake_id = None
        if outcome == OUTCOME_INCORRECT:
            mistake_id = self.ledger.record(
                student_id=s["student_id"], session_id=session_id,
                question_id=question_id, attempt_id=aid,
                concept_key=question.get("concept_key") or "UNKNOWN",
                concept=question.get("concept") or "UNKNOWN",
                transaction_type=",".join(
                    question.get("transaction_types") or []),
                difficulty=question.get("difficulty"),
                mistake_category=category,
                expected_journal_reference=question_id,
                student_response=self._journal_projection(entry),
                raw_response=str(raw_response or ""), now=at)
        elif outcome == OUTCOME_CORRECT:
            self.ledger.record_correct(
                student_id=s["student_id"], question_id=question_id,
                attempt_id=aid, now=at)

        attempt = {
            "attempt_id": aid,
            "session_id": session_id,
            "student_id": s["student_id"],
            "question_id": question_id,
            "raw_response": str(raw_response or ""),
            "normalized_response": self._normalize_response(raw_response),
            "submitted_at": at,
            "outcome": outcome,
            "verification_status": verification_status,
            "verified_journal": self._journal_projection(entry),
            "expected_journal_reference": question_id,
            "mistake_id": mistake_id,
            "mistake_category": category,
            "verification_metadata": metadata,
        }
        self.store.attempts[aid] = attempt

        # Mastery update (concept-level, deterministic).
        concept_key = question.get("concept_key") or "UNKNOWN"
        self.mastery.update(s["student_id"], concept_key, outcome, now=at)
        open_count = len(self.ledger.open_mistakes(
            student_id=s["student_id"], concept_key=concept_key))
        self.mastery.set_open_mistake_count(
            s["student_id"], concept_key, open_count)

        # Session counters.
        s["attempts"] += 1
        s["completed_count"] += 1
        if outcome == OUTCOME_CORRECT:
            s["correct_count"] += 1
        elif outcome == OUTCOME_INCORRECT:
            s["incorrect_count"] += 1
        else:
            s["review_required_count"] += 1

        self._sync_store()
        return dict(attempt)

    # ------------------------------------------------------------------
    # Verification (canonical journal = sole authority)
    # ------------------------------------------------------------------

    def _verify(self, canonical: Dict[str, List[List[Any]]],
                entry: Dict[str, Any], raw_response: str,
                question: Dict[str, Any]) -> tuple:
        """Deterministic verification: student entry vs canonical verified
        journal. Returns (outcome, mistake_category, verification_status,
        metadata). No accounting rules live here - the canonical journal
        is the FT-E engine's verified output."""
        metadata: Dict[str, Any] = {
            "reference": question["question_id"],
            "concept_key": question.get("concept_key"),
            "transaction_count": question.get("transaction_count"),
        }
        # -- unreadable / ambiguous / unsupported ----------------------
        if not entry["debit"] and not entry["credit"]:
            low = " " + str(raw_response or "").lower() + " "
            if any(hint in low for hint in _UNSUPPORTED_HINTS):
                return (OUTCOME_NOT_SUPPORTED, "UNSUPPORTED_RESPONSE",
                        "NOT_SUPPORTED", metadata)
            return (OUTCOME_REVIEW_REQUIRED, "AMBIGUOUS_RESPONSE",
                    "REVIEW_REQUIRED", metadata)

        exp_d = self._side(canonical, "debit")
        exp_c = self._side(canonical, "credit")
        stu_d = [l for l in entry["debit"]]
        stu_c = [l for l in entry["credit"]]
        td = sum(l["amount"] for l in stu_d)
        tc = sum(l["amount"] for l in stu_c)

        # -- balancing gate --------------------------------------------
        if abs(td - tc) > 0.01:
            metadata["totals"] = {"debit": td, "credit": tc}
            return (OUTCOME_INCORRECT, "LEDGER_BALANCING_ERROR",
                    "VERIFIED", metadata)

        accounts_match = (self._accounts(stu_d) == self._accounts(exp_d)
                          and self._accounts(stu_c) == self._accounts(exp_c))
        if accounts_match:
            if self._amounts(stu_d) == self._amounts(exp_d) \
                    and self._amounts(stu_c) == self._amounts(exp_c):
                return (OUTCOME_CORRECT, None, "VERIFIED", metadata)
            return (OUTCOME_INCORRECT,
                    self._refine(question, "AMOUNT_ERROR", entry, canonical),
                    "VERIFIED", metadata)

        # -- side reversal (same combined accounts, opposite sides) ----
        if (self._accounts(stu_d) == self._accounts(exp_c)
                and self._accounts(stu_c) == self._accounts(exp_d)):
            flipped = sorted(set(self._accounts(exp_c)))
            category = ("PARTY_ROLE_ERROR" if self._is_party(flipped)
                        else "DEBIT_CREDIT_DIRECTION")
            return (OUTCOME_INCORRECT, category, "VERIFIED", metadata)

        # -- missing / invented accounts ------------------------------
        if (question.get("transaction_count") or 1) > 1:
            return (OUTCOME_INCORRECT, "MULTI_TRANSACTION_ERROR",
                    "VERIFIED", metadata)
        exp_set = set(self._accounts(exp_d)) | set(self._accounts(exp_c))
        stu_set = set(self._accounts(stu_d)) | set(self._accounts(stu_c))
        if not (exp_set & stu_set):
            return (OUTCOME_INCORRECT, "TRANSACTION_CLASSIFICATION",
                    "VERIFIED", metadata)
        return (OUTCOME_INCORRECT,
                self._refine(question, "ACCOUNT_SELECTION", entry, canonical),
                "VERIFIED", metadata)

    # ------------------------------------------------------------------

    def _side(self, canonical: Dict[str, List[List[Any]]],
              side: str) -> List[Dict[str, Any]]:
        # Canonical accounts go through the SAME normalization as student
        # accounts (case-folded + canonical chart spelling) so 'amar' and
        # 'Amar' and 'cash' and 'Cash' compare equal.
        return [{"account": self._normalize_account(acc),
                 "amount": float(amt)}
                for acc, amt in (canonical.get(side) or [])]

    @staticmethod
    def _accounts(lines: List[Dict[str, Any]]) -> List[str]:
        return sorted(str(l["account"]) for l in lines)

    @staticmethod
    def _amounts(lines: List[Dict[str, Any]]) -> List[float]:
        # Per-account totals per side: split lines ('Cash Cr 20,000' +
        # 'Cash Cr 5,000') equal one combined 'Cash Cr 25,000' line -
        # standard journal equivalence, deterministic.
        totals: Dict[str, float] = {}
        for l in lines:
            key = str(l["account"])
            totals[key] = round(totals.get(key, 0.0)
                                + float(l["amount"]), 2)
        return sorted(round(v, 2) for v in totals.values())

    def _is_party(self, accounts: List[str]) -> bool:
        # A "party" is any account the chart does not know (personal
        # account - a person's name). Chart accounts are assets/liab/cap.
        return any(canonical_account(a) is None for a in accounts)

    def _refine(self, question: Dict[str, Any], base: str,
                entry: Dict[str, Any],
                canonical: Dict[str, List[List[Any]]]) -> str:
        """Deterministic refinement for GST / trade-discount /
        cash-discount content. Only fires when the canonical question
        carries explicit hints; otherwise the base category stands."""
        text = " " + str(question.get("raw_text") or "").lower() + " "
        tags = " ".join(str(t).lower() for t in (question.get("tags") or []))
        hints = text + " " + tags
        if "gst" in hints:
            return "GST_ERROR"
        if "trade discount" in hints and base == "AMOUNT_ERROR":
            return "TRADE_DISCOUNT_ERROR"
        canon_accounts = (set(self._accounts(
            self._side(canonical, "debit")))
            | set(self._accounts(
                self._side(canonical, "credit"))))
        if any("discount" in a.lower() for a in canon_accounts) \
                and "discount" in hints:
            stu_accounts = (set(self._accounts(entry["debit"]))
                            | set(self._accounts(entry["credit"])))
            stu_discount = {a for a in stu_accounts
                            if "discount" in a.lower()}
            canon_discount = {a for a in canon_accounts
                              if "discount" in a.lower()}
            if not stu_discount:
                # the student dropped the discount account entirely
                return "CASH_DISCOUNT_ERROR"
            if stu_discount != canon_discount:
                # the student used the WRONG discount account (Discount
                # Received instead of Discount Allowed, or an invented
                # discount line the canonical journal does not carry) - a
                # cash-discount classification mistake, never a generic
                # account-selection error (Sprint 15I-L).
                return "CASH_DISCOUNT_ERROR"
        return base

    # ------------------------------------------------------------------
    # Response parsing / normalization (presentation-safe only)
    # ------------------------------------------------------------------

    def _build_entry(self, debit_accounts: List[Any],
                     debit_amounts: List[Any],
                     credit_accounts: List[Any],
                     credit_amounts: List[Any]) -> Dict[str, Any]:
        debit = []
        credit = []
        for acc, amt in zip(debit_accounts or [], debit_amounts or []):
            parsed = self._parse_line(acc, amt)
            if parsed:
                debit.append(parsed)
        for acc, amt in zip(credit_accounts or [], credit_amounts or []):
            parsed = self._parse_line(acc, amt)
            if parsed:
                credit.append(parsed)
        return {"debit": debit, "credit": credit}

    def _parse_line(self, account: Any, amount: Any) -> Optional[Dict[str, Any]]:
        acc = self._normalize_account(account)
        if not acc:
            return None
        parsed = parse_numeric_text(amount)
        if parsed is None or parsed.value is None or parsed.ambiguity:
            return None
        try:
            value = float(parsed.value)
        except (TypeError, ValueError):
            return None
        return {"account": acc, "amount": value}

    @staticmethod
    def _normalize_account(account: Any) -> str:
        """Presentation-safe account normalization: strip + collapse
        whitespace + canonical chart spelling when the chart knows it.
        Case is folded for comparison (matching accounts case-
        insensitively), but the student's own text is never altered
        (raw_response is preserved verbatim)."""
        if account is None:
            return ""
        key = " ".join(str(account).strip().lower().split())
        if not key:
            return ""
        canon = canonical_account(key)
        return canon if canon else key

    @staticmethod
    def _normalize_response(raw: str) -> str:
        return " ".join(str(raw or "").split())

    @staticmethod
    def _journal_projection(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {"debit": [[l["account"], l["amount"]]
                          for l in entry["debit"]],
                "credit": [[l["account"], l["amount"]]
                           for l in entry["credit"]]}

    # ------------------------------------------------------------------
    # Dashboard / integrity helpers
    # ------------------------------------------------------------------

    def student_dashboard(self, student_id: str) -> Dict[str, Any]:
        """Deterministic aggregates (section 22). Nothing fabricated."""
        attempts = [a for a in self.store.attempts.values()
                    if a["student_id"] == student_id]
        attempts.sort(key=lambda a: a["submitted_at"])
        correct = sum(1 for a in attempts
                      if a["outcome"] == OUTCOME_CORRECT)
        incorrect = sum(1 for a in attempts
                        if a["outcome"] == OUTCOME_INCORRECT)
        review = sum(1 for a in attempts
                     if a["outcome"] == OUTCOME_REVIEW_REQUIRED)
        unsupported = sum(1 for a in attempts
                          if a["outcome"] == OUTCOME_NOT_SUPPORTED)
        # Streak = consecutive CORRECT at the end of the timeline.
        streak = 0
        for a in reversed(attempts):
            if a["outcome"] == OUTCOME_CORRECT:
                streak += 1
            else:
                break
        mistakes = [m for m in self.ledger.records().values()
                    if m["student_id"] == student_id]
        summary = self.mastery.summary(student_id)
        return {
            "student_id": student_id,
            "total_attempts": len(attempts),
            "correct": correct,
            "incorrect": incorrect,
            "review_required": review,
            "not_supported": unsupported,
            "lifetime_accuracy": correct / max(1, correct + incorrect),
            "recent_accuracy": (self._recent_accuracy(attempts)),
            "current_streak": streak,
            "mastery": summary,
            "open_mistakes": len([m for m in mistakes
                                  if m["status"] == MISTAKE_OPEN]),
            "improving_mistakes": len([m for m in mistakes
                                       if m["status"] == MISTAKE_IMPROVING]),
            "resolved_mistakes": len([m for m in mistakes
                                      if m["status"] == MISTAKE_RESOLVED]),
        }

    @staticmethod
    def _recent_accuracy(attempts: List[Dict[str, Any]],
                         window: int = 10) -> float:
        recent = attempts[-window:]
        scored = [1 if a["outcome"] == OUTCOME_CORRECT else
                  0 if a["outcome"] == OUTCOME_INCORRECT else None
                  for a in recent]
        scored = [v for v in scored if v is not None]
        return sum(scored) / len(scored) if scored else 0.0

    def replay_aggregates(self, student_id: str) -> Dict[str, Any]:
        """Stable projection of everything the learning layer derived -
        used to prove replay determinism (no timestamps/id noise)."""
        attempts = [a for a in self.store.attempts.values()
                    if a["student_id"] == student_id]
        mistakes = [m for m in self.ledger.records().values()
                    if m["student_id"] == student_id]
        mastery = [dict(m) for m in self.mastery.records().values()
                   if m["student_id"] == student_id]
        for m in mastery:
            m.pop("last_attempt_at", None)
            m.pop("transitions", None)
        return {
            "attempts": [(a["question_id"], a["outcome"],
                          a["mistake_category"]) for a in attempts],
            "mistakes": [(m["question_id"], m["mistake_category"],
                          m["occurrence_count"], m["status"])
                         for m in mistakes],
            "mastery": sorted(
                [(m["concept_key"], m["mastery_state"], m["attempts"],
                  round(m["accuracy"], 4), round(m["recent_accuracy"], 4))
                 for m in mastery]),
        }

    # ------------------------------------------------------------------

    def _session(self, session_id: str) -> Dict[str, Any]:
        s = self.store.sessions.get(session_id)
        if s is None:
            raise KeyError(f"unknown session: {session_id}")
        return s

    def _question(self, question_id: str) -> Dict[str, Any]:
        try:
            return self.bank.get_question(question_id)
        except KeyError:
            raise KeyError(f"unknown question: {question_id}")

    def _approved_question(self, question_id: str) -> Dict[str, Any]:
        q = self._question(question_id)
        if q.get("status") != "APPROVED":
            raise ValueError(
                f"question {question_id} is not APPROVED "
                f"(status={q.get('status')}) - only approved content is "
                "practicable")
        return q

    def _assert_in_scope(self, s: Dict[str, Any],
                         q: Dict[str, Any]) -> None:
        if s.get("chapter") and q.get("chapter") != s["chapter"]:
            raise ValueError(
                f"question {q['question_id']} chapter "
                f"{q.get('chapter')} is outside session chapter "
                f"{s['chapter']}")
        if s.get("concept") and q.get("concept_key") != s["concept"] \
                and q.get("concept") != s["concept"]:
            raise ValueError(
                f"question {q['question_id']} is outside session concept "
                f"{s['concept']}")
        if s.get("difficulty") is not None \
                and q.get("difficulty") != s["difficulty"]:
            raise ValueError(
                f"question {q['question_id']} difficulty "
                f"{q.get('difficulty')} is outside session difficulty "
                f"{s['difficulty']}")
        if s.get("transaction_type") and s["transaction_type"] not in \
                (q.get("transaction_types") or []):
            raise ValueError(
                f"question {q['question_id']} is outside session "
                f"transaction type {s['transaction_type']}")

    def _concept_of(self, question_id: str) -> str:
        return self._question(question_id).get("concept_key") or "UNKNOWN"

    def _difficulty_of(self, question_id: str) -> Optional[str]:
        return _DIFF_BAND.get(self._question(question_id).get("difficulty"))

    def _is_variant(self, question_id: str) -> bool:
        return bool(self._question(question_id).get("canonical_id"))

    def _canonical_answered(self, variant_id: str,
                            answered: set) -> bool:
        q = self._question(variant_id)
        return q.get("canonical_id") in answered

    def _sync_store(self) -> None:
        self.store.mistakes = self.ledger.records()
        self.store.mastery = self.mastery.records()
        self.store.save()
