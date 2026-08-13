"""
Financial Timeline Engine
Sprint 15I-N - Advanced Personalization & Adaptive Learning Engine
backend/maths/fyjc_personalization_engine.py

A pure, deterministic LEARNING DECISION layer on top of the persisted
evidence produced by the 15I-H Practice / Mistake / Mastery stack and
the 15I-G verified Question Bank.

Authority boundary (non-negotiable)
----------------------------------
15I-N is NOT an accounting reasoning engine. It NEVER:
  * generates or modifies canonical journals,
  * verifies or reinterprets student answers,
  * approves content or changes QuestionBank verification,
  * overrides FT-E, and
  * treats any external (LLM / teacher) suggestion as accounting truth.
FT-E remains the ONLY accounting authority. This module contains no
accounting rules and no LLM calls - it only decides WHAT an approved
student should practice next, WHEN revision is due and HOW hard the
next verified question should be.

Evidence model
--------------
The engine consumes ONLY persisted evidence:
  * attempts   - PracticeStore attempts (outcome, question_id,
                 submitted_at, mistake_category)
  * mistakes   - MistakeLedger records (category, occurrence_count,
                 status OPEN/IMPROVING/RESOLVED, concept_key)
  * mastery    - MasteryEngine records (mastery_state, accuracy,
                 recent_accuracy, recent window, last_attempt_at)
  * questions  - APPROVED QuestionBank content (concept_key, difficulty,
                 transaction_types, canonical_id) - read-only metadata
Unknown / missing evidence stays UNKNOWN and is never guessed.

Determinism
-----------
Given identical QuestionBank + student history + configuration +
current timestamp + seed, the profile and every ranking are identical.
Clocks are injectable; no uncontrolled randomness is used (ranking
tie-breaks use a stable (score, sha1(seed:qid)) order).

Thresholds are explicit, documented constants in CONFIG_DEFAULT - never
invented, never tuned by opaque statistics.
"""

from __future__ import annotations

import calendar
import hashlib
import time
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_question_bank import QuestionBank, STATUS_APPROVED
from backend.maths.fyjc_mistake_ledger import (
    MISTAKE_CATEGORIES,
    MISTAKE_IMPROVING,
    MISTAKE_OPEN,
    MISTAKE_RESOLVED,
)
from backend.maths.fyjc_mastery_engine import (
    MASTERY_DEVELOPING,
    MASTERY_LEARNING,
    MASTERY_MASTERED,
    MASTERY_REVIEW,
    MASTERY_UNSEEN,
)
from backend.maths.fyjc_practice_engine import (
    MODE_CHAPTER,
    MODE_EXAM_MIX,
    MODE_MISTAKE_RETRY,
    MODE_NORMAL,
    MODE_REVISION,
    MODE_WEAKNESS,
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_NOT_SUPPORTED,
    OUTCOME_REVIEW_REQUIRED,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

PERSONALIZATION_VERSION = "15I-N-1"

MODE_COLD_START = "COLD_START"
MODE_ADAPTIVE = "ADAPTIVE"

DIRECTION_ADVANCE = "ADVANCE"
DIRECTION_REINFORCE = "REINFORCE"
DIRECTION_REMEDIATION = "REMEDIATION"
DIRECTION_MIXED = "MIXED"
DIRECTION_STAY = "STAY"

# Session objectives (Sprint 15I-N section 10).
OBJECTIVE_REMEDIATION = "REMEDIATION"
OBJECTIVE_REVISION = "REVISION"
OBJECTIVE_MASTERY_BUILDING = "MASTERY_BUILDING"
OBJECTIVE_EXAM_PREPARATION = "EXAM_PREPARATION"
OBJECTIVE_MIXED_PRACTICE = "MIXED_PRACTICE"
OBJECTIVE_WEAK_AREA_FOCUS = "WEAK_AREA_FOCUS"
OBJECTIVE_CHAPTER_FOCUS = "CHAPTER_FOCUS"
OBJECTIVE_DIFFICULTY_PROGRESSION = "DIFFICULTY_PROGRESSION"
OBJECTIVES = (
    OBJECTIVE_REMEDIATION,
    OBJECTIVE_REVISION,
    OBJECTIVE_MASTERY_BUILDING,
    OBJECTIVE_EXAM_PREPARATION,
    OBJECTIVE_MIXED_PRACTICE,
    OBJECTIVE_WEAK_AREA_FOCUS,
    OBJECTIVE_CHAPTER_FOCUS,
    OBJECTIVE_DIFFICULTY_PROGRESSION,
)

OBJECTIVE_AUTO = "AUTO"  # UI convenience label: derive from evidence/mode

# Ranking factor names (kept stable for the profile/evidence contract).
FACTOR_WEAKNESS = "weakness"
FACTOR_MISTAKE = "mistake"
FACTOR_REVISION = "revision"
FACTOR_DIFFICULTY = "difficulty"
FACTOR_DIVERSITY = "diversity"
FACTOR_CHALLENGE = "challenge"
FACTOR_MAINTENANCE = "maintenance"
FACTORS = (FACTOR_WEAKNESS, FACTOR_MISTAKE, FACTOR_REVISION,
           FACTOR_DIFFICULTY, FACTOR_DIVERSITY, FACTOR_CHALLENGE,
           FACTOR_MAINTENANCE)

# ---------------------------------------------------------------------------
# Configuration (explicit, documented thresholds - Sprint 15I-N sections
# 4 / 7 / 8 / 12 / 21)
# ---------------------------------------------------------------------------

CONFIG_DEFAULT: Dict[str, Any] = {
    # Cold start: below this many SCORED attempts the student stays in
    # baseline mixed practice with NO inferred weaknesses/strengths.
    "min_adaptive_attempts": 5,
    # Recency window (matches MasteryEngine's bounded recent window).
    "recent_window": 5,
    # ---- Weakness model (section 4) ---------------------------------
    "weak_recent_accuracy": 0.50,          # recent accuracy at/below -> flag
    "weak_min_attempts": 3,                # minimum scored attempts to flag
    "weak_open_mistake_min": 1,            # at least one OPEN/IMPROVING
    "weak_repeat_occurrence_min": 2,       # repeated same-category mistakes
    "weak_difficulty_failure_rate": 0.67,  # failure rate at a difficulty band
    "weak_difficulty_min_attempts": 3,     #   with >= this many band attempts
    "weak_type_failure_rate": 0.67,        # failure rate on a transaction type
    "weak_type_min_attempts": 3,           #   with >= this many type attempts
    # ---- Strength model (section 5) ----------------------------------
    # Conservative: strength is only claimed with sustained evidence.
    "strength_min_attempts": 5,
    "strength_lifetime_accuracy": 0.85,
    "strength_recent_accuracy": 0.80,
    "strength_resolved_mistake_min": 1,
    # ---- Difficulty readiness (section 7) -----------------------------
    "advance_consecutive_correct": 3,
    "reinforce_consecutive_incorrect": 2,
    "readiness_recent_window": 6,
    "default_difficulty_band": 2,          # MEDIUM for evidence-free students
    # ---- Deterministic revision-priority model (section 8) ------------
    # A documented, deterministic revision-priority score - explicitly NOT
    # claimed to be a scientifically calibrated memory-decay curve.
    "revision_review_interval_days": 3,
    "revision_learning_interval_days": 5,
    "revision_developing_interval_days": 7,
    "revision_mastered_interval_days": 14,
    "revision_due_threshold": 0.10,
    "revision_priority_cap": 1.0,
    # ---- Mistake-pattern model (section 6) ----------------------------
    "pattern_occurrence_min": 2,           # repeated occurrences to flag
    "pattern_open_min": 1,                 # at least one active mistake
    "pattern_recent_window": 5,            # occurrences in the last N records
    # ---- Adaptive mix (section 12) ------------------------------------
    # Adaptive-mode recommended mix (percentages, sum = 1.0).
    "mix_weakness_remediation": 0.30,
    "mix_revision": 0.25,
    "mix_current_level": 0.20,
    "mix_challenge": 0.10,
    "mix_maintenance": 0.15,
    # Cold-start mix: baseline mixed practice, broad coverage, moderate
    # difficulty, NO fabricated weakness/remediation weighting.
    "cold_start_mix_weakness_remediation": 0.00,
    "cold_start_mix_revision": 0.00,
    "cold_start_mix_current_level": 0.50,
    "cold_start_mix_challenge": 0.15,
    "cold_start_mix_maintenance": 0.35,
    # ---- Ranking weights per objective (section 9) --------------------
    # Each objective maps the seven deterministic factors to weights; the
    # weights sum to 1.0 per objective.
    "objective_weights": {
        OBJECTIVE_REMEDIATION: {
            FACTOR_WEAKNESS: 0.30, FACTOR_MISTAKE: 0.30,
            FACTOR_REVISION: 0.10, FACTOR_DIFFICULTY: 0.10,
            FACTOR_DIVERSITY: 0.10, FACTOR_CHALLENGE: 0.00,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_REVISION: {
            FACTOR_WEAKNESS: 0.10, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.40, FACTOR_DIFFICULTY: 0.10,
            FACTOR_DIVERSITY: 0.20, FACTOR_CHALLENGE: 0.00,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_MASTERY_BUILDING: {
            FACTOR_WEAKNESS: 0.15, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.10, FACTOR_DIFFICULTY: 0.25,
            FACTOR_DIVERSITY: 0.20, FACTOR_CHALLENGE: 0.10,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_EXAM_PREPARATION: {
            FACTOR_WEAKNESS: 0.20, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.15, FACTOR_DIFFICULTY: 0.20,
            FACTOR_DIVERSITY: 0.25, FACTOR_CHALLENGE: 0.05,
            FACTOR_MAINTENANCE: 0.05,
        },
        OBJECTIVE_MIXED_PRACTICE: {
            FACTOR_WEAKNESS: 0.10, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.15, FACTOR_DIFFICULTY: 0.15,
            FACTOR_DIVERSITY: 0.30, FACTOR_CHALLENGE: 0.10,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_WEAK_AREA_FOCUS: {
            FACTOR_WEAKNESS: 0.35, FACTOR_MISTAKE: 0.25,
            FACTOR_REVISION: 0.05, FACTOR_DIFFICULTY: 0.10,
            FACTOR_DIVERSITY: 0.15, FACTOR_CHALLENGE: 0.00,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_CHAPTER_FOCUS: {
            FACTOR_WEAKNESS: 0.15, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.10, FACTOR_DIFFICULTY: 0.20,
            FACTOR_DIVERSITY: 0.30, FACTOR_CHALLENGE: 0.05,
            FACTOR_MAINTENANCE: 0.10,
        },
        OBJECTIVE_DIFFICULTY_PROGRESSION: {
            FACTOR_WEAKNESS: 0.10, FACTOR_MISTAKE: 0.10,
            FACTOR_REVISION: 0.05, FACTOR_DIFFICULTY: 0.40,
            FACTOR_DIVERSITY: 0.20, FACTOR_CHALLENGE: 0.10,
            FACTOR_MAINTENANCE: 0.05,
        },
    },
    # Recent-exposure look-back for diversity control (section 13).
    "diversity_lookback": 10,
    # Tie-break seed for deterministic ranking order.
    "seed": 0,
}


# ---------------------------------------------------------------------------
# Small helpers (pure)
# ---------------------------------------------------------------------------


def _now_str(now_fn) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(float(now_fn())))
    except (TypeError, ValueError, OverflowError):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(ts: Any) -> Optional[float]:
    if ts is None:
        return None
    try:
        return calendar.timegm(
            time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError, OverflowError):
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _round(value: Any, digits: int = 4) -> float:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def _rate(correct: int, incorrect: int) -> float:
    scored = correct + incorrect
    return (correct / scored) if scored else 0.0


def _scored(outcome: Any) -> Optional[int]:
    """1 = CORRECT, 0 = INCORRECT, None for the neutral outcomes."""
    if outcome == OUTCOME_CORRECT:
        return 1
    if outcome == OUTCOME_INCORRECT:
        return 0
    return None


# ---------------------------------------------------------------------------
# PersonalizationEngine
# ---------------------------------------------------------------------------


class PersonalizationEngine:
    """Deterministic personalization / adaptive-learning decision layer.

    Pure: it owns no persistence and mutates nothing. It reads persisted
    evidence (attempts / mistakes / mastery dicts) plus the QuestionBank
    (read-only) and returns profiles, rankings and explanations.
    """

    def __init__(self, bank: Optional[QuestionBank] = None,
                 config: Optional[Dict[str, Any]] = None,
                 now_fn=None,
                 seed: Optional[int] = None) -> None:
        self.bank = bank
        self.config: Dict[str, Any] = dict(CONFIG_DEFAULT)
        if config:
            self.config.update(config)
        if seed is not None:
            self.config["seed"] = int(seed)
        self._now_fn = now_fn or (lambda: time.time())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, student_id: str,
                 attempts: Dict[str, Dict[str, Any]],
                 mistakes: Dict[str, Dict[str, Any]],
                 mastery: Dict[str, Dict[str, Any]],
                 now: Optional[float] = None) -> Dict[str, Any]:
        """Produce the deterministic PersonalizationProfile for a student.

        Pure function of (evidence, bank metadata, config, clock).
        Never mutates any input.
        """
        now_epoch = now if now is not None else self._now_epoch()
        student_attempts = _student_attempts(attempts, student_id)
        student_mistakes = _student_mistakes(mistakes, student_id)
        student_mastery = _student_mastery(mastery, student_id)

        concepts = _aggregate_concepts(
            student_attempts, student_mistakes, student_mastery,
            self._question_meta, self.config)
        types = _aggregate_types(student_attempts, self._question_meta)
        patterns = _mistake_patterns(
            student_mistakes, student_attempts, self.config)
        weaknesses = _weakness_model(concepts, self.config)
        strengths = _strength_model(concepts, self.config)
        readiness = _difficulty_readiness(
            student_attempts, concepts, self.config,
            self._question_meta)
        revision = _revision_priorities(student_mastery, now_epoch,
                                        self.config)
        evidence_summary = _evidence_summary(
            student_attempts, student_mistakes, concepts)

        adaptive = (evidence_summary["scored_attempts"]
                    >= int(self.config["min_adaptive_attempts"]))
        mode = MODE_ADAPTIVE if adaptive else MODE_COLD_START
        mix = _recommended_mix(weaknesses, mode, self.config)
        focus = _focus_areas(weaknesses, patterns, revision, strengths,
                             readiness, mode)
        explanations = _explanations(focus, mode)
        confidence = _confidence(evidence_summary, weaknesses, patterns,
                                 revision, mode, self.config)

        # Transaction-type strength/weakness.
        type_weak, type_strong = _type_strength(types, self.config)

        profile: Dict[str, Any] = {
            "student_id": student_id,
            "mode": mode,
            "profile_version": PERSONALIZATION_VERSION,
            "evaluated_at": _now_str(lambda: now_epoch),
            "evidence_summary": evidence_summary,
            "concept_weaknesses": weaknesses,
            "concept_strengths": strengths,
            "mistake_patterns": patterns,
            "difficulty_readiness": readiness,
            "revision_candidates": revision,
            "transaction_type_strength": type_strong,
            "transaction_type_weakness": type_weak,
            "recommended_mix": mix,
            "recommended_objective": _recommended_objective(
                mode, weaknesses, patterns),
            "recommended_focus_areas": focus,
            "explanations": explanations,
            "confidence": confidence,
        }
        return profile

    def teacher_aggregates(
            self, attempts: Dict[str, Dict[str, Any]],
            mistakes: Dict[str, Dict[str, Any]],
            mastery: Dict[str, Dict[str, Any]],
            now: Optional[float] = None) -> Dict[str, Any]:
        """Deterministic cross-student aggregates for the teacher
        dashboard (Sprint 15I-N section 19). All values are derived from
        the same evidence; nothing is fabricated."""
        students = sorted({str(a.get("student_id"))
                           for a in attempts.values()
                           if isinstance(a, dict) and a.get("student_id")})
        weakest: List[Dict[str, Any]] = []
        strongest: List[Dict[str, Any]] = []
        improving: List[Dict[str, Any]] = []
        degrading: List[Dict[str, Any]] = []
        need_review: List[Dict[str, Any]] = []
        revision_due: List[Dict[str, Any]] = []
        categories: Dict[str, int] = {}
        for sid in students:
            profile = self.evaluate(sid, attempts, mistakes, mastery,
                                    now=now)
            for w in profile["concept_weaknesses"]:
                weakest.append({
                    "student_id": sid, "concept_key": w["concept_key"],
                    "score": w["score"], "evidence": w["evidence"]})
            for s_ in profile["concept_strengths"]:
                strongest.append({
                    "student_id": sid, "concept_key": s_["concept_key"],
                    "score": s_["score"], "evidence": s_["evidence"]})
            for r in profile["revision_candidates"]:
                revision_due.append({
                    "student_id": sid, "concept_key": r["concept_key"],
                    "priority": r["priority"],
                    "days_since": r["days_since"],
                    "mastery_state": r["mastery_state"]})
            for p in profile["mistake_patterns"]:
                cat = str(p["category"])
                categories[cat] = categories.get(cat, 0) \
                    + int(p["occurrence_count"])
            sm = _student_mastery(mastery, sid)
            for rec in sm.values():
                if rec.get("mastery_state") == MASTERY_REVIEW \
                        and int(rec.get("attempts") or 0) >= 2:
                    need_review.append({
                        "student_id": sid,
                        "concept_key": rec.get("concept_key"),
                        "recent_accuracy": rec.get("recent_accuracy"),
                    })
                improving_flag, degrading_flag = _trend(rec)
                if improving_flag:
                    improving.append({
                        "student_id": sid,
                        "concept_key": rec.get("concept_key"),
                        "lifetime_accuracy": rec.get("accuracy"),
                        "recent_accuracy": rec.get("recent_accuracy"),
                    })
                if degrading_flag:
                    degrading.append({
                        "student_id": sid,
                        "concept_key": rec.get("concept_key"),
                        "lifetime_accuracy": rec.get("accuracy"),
                        "recent_accuracy": rec.get("recent_accuracy"),
                    })
        return {
            "weakest_concepts": sorted(
                weakest, key=lambda x: (-x["score"], x["student_id"],
                                        x["concept_key"])),
            "strongest_concepts": sorted(
                strongest, key=lambda x: (-x["score"], x["student_id"],
                                          x["concept_key"])),
            "common_mistake_categories": sorted(
                [{"category": k, "occurrences": v}
                 for k, v in categories.items()],
                key=lambda x: (-x["occurrences"], x["category"])),
            "students_needing_review": sorted(
                need_review, key=lambda x: (x["student_id"],
                                            x["concept_key"])),
            "concepts_improving": sorted(
                improving, key=lambda x: (x["student_id"],
                                          x["concept_key"])),
            "concepts_degrading": sorted(
                degrading, key=lambda x: (x["student_id"],
                                          x["concept_key"])),
            "revision_due": sorted(
                revision_due, key=lambda x: (-x["priority"],
                                             x["student_id"],
                                             x["concept_key"])),
        }

    def ranked_questions(
            self, student_id: str,
            attempts: Dict[str, Dict[str, Any]],
            mistakes: Dict[str, Dict[str, Any]],
            mastery: Dict[str, Dict[str, Any]],
            pool: List[str],
            objective: Optional[str] = None,
            answered: Optional[List[str]] = None,
            now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Rank an APPROVED-question pool for one student/objective.

        Only APPROVED bank content is ever ranked (non-approved ids in
        the pool are filtered out). Deterministic: score descending, then
        sha1(seed:question_id) ascending. Every ranked entry carries the
        factor scores + a human-readable reason + evidence.
        """
        now_epoch = now if now is not None else self._now_epoch()
        profile = self.evaluate(student_id, attempts, mistakes, mastery,
                                now=now_epoch)
        if objective is None or objective not in OBJECTIVES:
            objective = profile["recommended_objective"]
        weights = dict(self.config["objective_weights"].get(
            objective, self.config["objective_weights"][
                OBJECTIVE_MIXED_PRACTICE]))
        answered_set = set(str(q) for q in (answered or []))

        approved_ids = set()
        if self.bank is not None:
            approved_ids = {q["question_id"]
                            for q in self.bank.list_approved()}
        candidates = [qid for qid in pool
                      if str(qid) in approved_ids]

        weakness = {w["concept_key"]: w["score"]
                    for w in profile["concept_weaknesses"]}
        mistake = {p["category"]: p["score"]
                   for p in profile["mistake_patterns"]}
        pattern_by_concept: Dict[str, float] = {}
        pattern_by_type: Dict[str, float] = {}
        pattern_by_question: Dict[str, float] = {}
        for p in profile["mistake_patterns"]:
            score = float(p["score"])
            for ck in p.get("concept_keys") or []:
                pattern_by_concept[ck] = max(
                    pattern_by_concept.get(ck, 0.0), score)
            for t in p.get("transaction_types") or []:
                pattern_by_type[t] = max(pattern_by_type.get(t, 0.0),
                                         score)
            for qid in p.get("question_ids") or []:
                pattern_by_question[qid] = max(
                    pattern_by_question.get(qid, 0.0), score)
        revision = {r["concept_key"]: r["priority"]
                    for r in profile["revision_candidates"]}

        readiness = profile["difficulty_readiness"]
        target_band = int(readiness.get("target_band")
                          or self.config["default_difficulty_band"])
        direction = readiness.get("direction") or DIRECTION_STAY

        # Recent exposure (diversity controller, section 13).
        exposure = _recent_exposure(attempts, student_id,
                                    self._question_meta,
                                    int(self.config["diversity_lookback"]))

        ranked: List[Dict[str, Any]] = []
        for qid in candidates:
            q = self._question_meta(qid)
            if q is None:
                continue
            factors = _question_factors(
                q, weakness, mistake, pattern_by_concept, pattern_by_type,
                pattern_by_question, revision, target_band, direction,
                exposure, weights, self.config, answered_set)
            score = sum(weights[f] * factors[f] for f in FACTORS)
            # Hard anti-repetition: a question answered in THIS session is
            # effectively eliminated from the ranked list; an unseen
            # variant of an answered canonical is demoted but remains
            # eligible (preferred over re-serving the canonical itself).
            if qid in answered_set:
                score *= 0.05
            elif str(q.get("canonical_id") or qid) in answered_set:
                score *= 0.6
            ranked.append({
                "question_id": qid,
                "score": _round(score, 6),
                "factors": {f: _round(factors[f], 6) for f in FACTORS},
                "objective": objective,
                "answered_in_session": qid in answered_set,
                "canonical_id": q.get("canonical_id") or qid,
                "_meta": q,
            })

        ranked.sort(key=lambda r: (
            -float(r["score"]),
            hashlib.sha1(
                f"{self.config['seed']}:{r['question_id']}"
                .encode("utf-8")).hexdigest()))
        for idx, r in enumerate(ranked):
            r["rank"] = idx + 1
            r["reason"], r["evidence"] = _selection_reason(
                r, profile, objective, weights, idx == 0)
        return ranked

    def should_personalize(self, student_id: str,
                           attempts: Dict[str, Dict[str, Any]]) -> bool:
        """Cold-start gate (section 15): only transition into adaptive
        personalization once enough SCORED evidence exists. Below the
        threshold the student stays on baseline mixed practice and no
        weaknesses/strengths are inferred."""
        scored = sum(1 for a in attempts.values()
                     if isinstance(a, dict)
                     and str(a.get("student_id")) == student_id
                     and a.get("outcome")
                     in (OUTCOME_CORRECT, OUTCOME_INCORRECT))
        return scored >= int(self.config["min_adaptive_attempts"])

    def select_question(
            self, student_id: str,
            attempts: Dict[str, Dict[str, Any]],
            mistakes: Dict[str, Dict[str, Any]],
            mastery: Dict[str, Dict[str, Any]],
            pool: List[str],
            session: Optional[Dict[str, Any]] = None,
            answered: Optional[List[str]] = None,
            clock: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Pick the single next question for a session (or None).

        Anti-repetition: the first ranked question not yet answered in
        this session is chosen. A repeat is only allowed when the session
        is a MISTAKE_RETRY remediation session (re-practicing the
        mistake's question is the point) or the pool is exhausted.
        """
        answered_set = set(str(q) for q in (answered or []))
        objective = self._objective_for(
            student_id, session, attempts, mistakes, mastery)
        ranked = self.ranked_questions(
            student_id, attempts, mistakes, mastery, pool,
            objective=objective, answered=answered, now=clock)
        if not ranked:
            return None
        allow_repeat = bool(
            session and session.get("mode") == MODE_MISTAKE_RETRY)
        for r in ranked:
            if r["question_id"] not in answered_set or allow_repeat:
                return {"question_id": r["question_id"],
                        "reason": r["reason"],
                        "evidence": r["evidence"],
                        "objective": r["objective"],
                        "rank": r["rank"],
                        "score": r["score"],
                        "factors": r["factors"]}
        # Everything answered and repeats not allowed: defer to the
        # PracticeEngine ladder (it already handles the exhausted-pool
        # fallback safely).
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _now_epoch(self) -> float:
        try:
            return float(self._now_fn())
        except (TypeError, ValueError):
            return float(time.time())

    def _question_meta(self, qid: str) -> Optional[Dict[str, Any]]:
        if self.bank is None:
            return None
        try:
            q = self.bank.get_question(qid)
        except KeyError:
            return None
        return {
            "question_id": q.get("question_id"),
            "concept_key": q.get("concept_key") or "UNKNOWN",
            "difficulty": q.get("difficulty"),
            "transaction_types": list(q.get("transaction_types") or []),
            "canonical_id": q.get("canonical_id"),
            "chapter": q.get("chapter"),
            "raw_text": q.get("raw_text"),
        }

    def _objective_for(self, student_id: str,
                       session: Optional[Dict[str, Any]],
                       attempts: Dict[str, Dict[str, Any]],
                       mistakes: Dict[str, Dict[str, Any]],
                       mastery: Dict[str, Dict[str, Any]]) -> str:
        if session:
            explicit = session.get("objective")
            if explicit and explicit in OBJECTIVES:
                return str(explicit)
            mode = session.get("mode")
            mode_map = {
                MODE_WEAKNESS: OBJECTIVE_WEAK_AREA_FOCUS,
                MODE_MISTAKE_RETRY: OBJECTIVE_REMEDIATION,
                MODE_REVISION: OBJECTIVE_REVISION,
                MODE_CHAPTER: OBJECTIVE_CHAPTER_FOCUS,
                MODE_EXAM_MIX: OBJECTIVE_EXAM_PREPARATION,
            }
            if mode in mode_map:
                return mode_map[mode]
        profile = self.evaluate(student_id, attempts, mistakes, mastery)
        return profile["recommended_objective"]


# ---------------------------------------------------------------------------
# Evidence extraction (pure; hostile input safe)
# ---------------------------------------------------------------------------


def _student_attempts(attempts: Dict[str, Dict[str, Any]],
                      student_id: str) -> List[Dict[str, Any]]:
    out = []
    for a in attempts.values():
        if not isinstance(a, dict):
            continue
        if str(a.get("student_id")) != student_id:
            continue
        if not a.get("question_id"):
            continue
        out.append(a)
    out.sort(key=lambda a: str(a.get("submitted_at") or ""))
    return out


def _student_mistakes(mistakes: Dict[str, Dict[str, Any]],
                      student_id: str) -> List[Dict[str, Any]]:
    out = []
    for m in mistakes.values():
        if not isinstance(m, dict):
            continue
        if str(m.get("student_id")) != student_id:
            continue
        out.append(m)
    return out


def _student_mastery(mastery: Dict[str, Dict[str, Any]],
                     student_id: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rec in mastery.values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("student_id")) != student_id:
            continue
        out[rec.get("concept_key") or "UNKNOWN"] = rec
    return out


def _aggregate_concepts(
        attempts: List[Dict[str, Any]],
        mistakes: List[Dict[str, Any]],
        mastery: Dict[str, Dict[str, Any]],
        question_meta,
        config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-concept evidence aggregation. UNKNOWN/missing metadata is
    never guessed; unresolvable attempts are excluded from concept
    attribution (still counted in the overall totals)."""
    window = int(config["recent_window"])
    agg: Dict[str, Dict[str, Any]] = {}
    for a in attempts:
        q = question_meta(a.get("question_id"))
        if q is None:
            continue
        ck = str(q.get("concept_key") or "UNKNOWN")
        rec = agg.setdefault(ck, {
            "concept_key": ck, "attempts": 0, "correct": 0,
            "incorrect": 0, "review_required": 0, "unsupported": 0,
            "recent": [], "last_attempt_at": None,
            "difficulty_outcomes": {}, "type_outcomes": {},
            "open_mistakes": 0, "resolved_mistakes": 0,
            "total_occurrences": 0,
            "mistake_categories": {}, "question_ids": set(),
        })
        outcome = a.get("outcome")
        rec["attempts"] += 1
        rec["last_attempt_at"] = a.get("submitted_at") or \
            rec["last_attempt_at"]
        rec["question_ids"].add(a.get("question_id"))
        scored = _scored(outcome)
        if scored is not None:
            rec["recent"].append(scored)
        if outcome == OUTCOME_CORRECT:
            rec["correct"] += 1
        elif outcome == OUTCOME_INCORRECT:
            rec["incorrect"] += 1
        elif outcome == OUTCOME_REVIEW_REQUIRED:
            rec["review_required"] += 1
        elif outcome == OUTCOME_NOT_SUPPORTED:
            rec["unsupported"] += 1
        band = q.get("difficulty")
        if band is not None:
            key = f"band:{band}"
            entry = rec["difficulty_outcomes"].setdefault(key, {
                "band": band, "correct": 0, "incorrect": 0})
            if outcome == OUTCOME_CORRECT:
                entry["correct"] += 1
            elif outcome == OUTCOME_INCORRECT:
                entry["incorrect"] += 1
        for t in q.get("transaction_types") or []:
            tkey = f"type:{t}"
            entry = rec["type_outcomes"].setdefault(tkey, {
                "type": t, "correct": 0, "incorrect": 0})
            if outcome == OUTCOME_CORRECT:
                entry["correct"] += 1
            elif outcome == OUTCOME_INCORRECT:
                entry["incorrect"] += 1
        rec["recent"] = rec["recent"][-window:]

    # Mistake evidence per concept (ledger records already carry it).
    for m in mistakes:
        ck = str(m.get("concept_key") or "UNKNOWN")
        rec = agg.setdefault(ck, {
            "concept_key": ck, "attempts": 0, "correct": 0,
            "incorrect": 0, "review_required": 0, "unsupported": 0,
            "recent": [], "last_attempt_at": None,
            "difficulty_outcomes": {}, "type_outcomes": {},
            "open_mistakes": 0, "resolved_mistakes": 0,
            "total_occurrences": 0,
            "mistake_categories": {}, "question_ids": set(),
        })
        occ = int(m.get("occurrence_count") or 1)
        status = m.get("status")
        rec["total_occurrences"] += occ
        cat = str(m.get("mistake_category") or "UNKNOWN")
        rec["mistake_categories"][cat] = \
            rec["mistake_categories"].get(cat, 0) + occ
        if status in (MISTAKE_OPEN, MISTAKE_IMPROVING):
            rec["open_mistakes"] += 1
        elif status == MISTAKE_RESOLVED:
            rec["resolved_mistakes"] += 1

    # Merge mastery state (authoritative state machine).
    for ck, rec in agg.items():
        mrec = mastery.get(ck)
        if mrec is None:
            continue
        rec["mastery_state"] = mrec.get("mastery_state") or MASTERY_UNSEEN
        rec["mastery_accuracy"] = mrec.get("accuracy")
        rec["mastery_recent_accuracy"] = mrec.get("recent_accuracy")
        if mrec.get("last_attempt_at"):
            rec["last_attempt_at"] = mrec["last_attempt_at"]

    for rec in agg.values():
        scored_recent = [v for v in rec["recent"] if v is not None]
        rec["recent_accuracy"] = (
            sum(scored_recent) / len(scored_recent)
            if scored_recent else 0.0)
        rec["accuracy"] = _rate(rec["correct"], rec["incorrect"])
        rec["question_ids"] = sorted(rec["question_ids"])
    return agg


def _aggregate_types(attempts: List[Dict[str, Any]],
                     question_meta) -> Dict[str, Dict[str, Any]]:
    """Per transaction-type evidence (from question metadata)."""
    types: Dict[str, Dict[str, Any]] = {}
    for a in attempts:
        q = question_meta(a.get("question_id"))
        if q is None:
            continue
        for t in q.get("transaction_types") or []:
            entry = types.setdefault(t, {
                "type": t, "attempts": 0, "correct": 0, "incorrect": 0,
                "recent": [], "last_attempt_at": None})
            outcome = a.get("outcome")
            entry["attempts"] += 1
            entry["last_attempt_at"] = a.get("submitted_at") or \
                entry["last_attempt_at"]
            scored = _scored(outcome)
            if scored is not None:
                entry["recent"].append(scored)
            if outcome == OUTCOME_CORRECT:
                entry["correct"] += 1
            elif outcome == OUTCOME_INCORRECT:
                entry["incorrect"] += 1
    for entry in types.values():
        scored = [v for v in entry["recent"] if v is not None]
        entry["recent_accuracy"] = (
            sum(scored) / len(scored) if scored else 0.0)
        entry["accuracy"] = _rate(entry["correct"], entry["incorrect"])
        entry["recent"] = entry["recent"][-10:]
    return types


def _mistake_patterns(
        mistakes: List[Dict[str, Any]],
        attempts: List[Dict[str, Any]],
        config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Aggregate mistakes by deterministic category (section 6)."""
    by_cat: Dict[str, Dict[str, Any]] = {}
    for m in mistakes:
        cat = str(m.get("mistake_category") or "UNKNOWN")
        if cat not in MISTAKE_CATEGORIES:
            cat = "UNKNOWN"
        rec = by_cat.setdefault(cat, {
            "category": cat, "occurrence_count": 0, "open_count": 0,
            "resolved_count": 0, "question_ids": set(),
            "concept_keys": set(), "transaction_types": set(),
            "last_occurrence_at": None, "recent_occurrences": 0,
            "evidence": [],
        })
        occ = int(m.get("occurrence_count") or 1)
        rec["occurrence_count"] += occ
        rec["last_occurrence_at"] = m.get("last_occurrence_at") or \
            rec["last_occurrence_at"]
        status = m.get("status")
        if status in (MISTAKE_OPEN, MISTAKE_IMPROVING):
            rec["open_count"] += 1
        elif status == MISTAKE_RESOLVED:
            rec["resolved_count"] += 1
        if m.get("question_id"):
            rec["question_ids"].add(str(m["question_id"]))
        if m.get("concept_key"):
            rec["concept_keys"].add(str(m["concept_key"]))
        tt = str(m.get("transaction_type") or "")
        if tt:
            rec["transaction_types"].update(
                t for t in tt.split(",") if t.strip())

    # Recency: occurrences of this category within the student's last N
    # mistake records (ordered by last_occurrence_at).
    ordered = sorted(mistakes,
                     key=lambda m: str(m.get("last_occurrence_at") or ""))
    recent_records = ordered[-
                             int(config["pattern_recent_window"]):]
    recent_cats: Dict[str, int] = {}
    for m in recent_records:
        cat = str(m.get("mistake_category") or "UNKNOWN")
        recent_cats[cat] = recent_cats.get(cat, 0) + \
            int(m.get("occurrence_count") or 1)

    patterns: List[Dict[str, Any]] = []
    for cat, rec in by_cat.items():
        rec["recent_occurrences"] = recent_cats.get(cat, 0)
        rec["question_ids"] = sorted(rec["question_ids"])
        rec["concept_keys"] = sorted(rec["concept_keys"])
        rec["transaction_types"] = sorted(rec["transaction_types"])
        occ_norm = _clamp(rec["occurrence_count"]
                          / max(1, int(config["pattern_occurrence_min"])))
        active = 1.0 if rec["open_count"] >= 1 else 0.0
        recent_norm = _clamp(rec["recent_occurrences"] / 2.0)
        score = _clamp(0.45 * occ_norm + 0.35 * active
                       + 0.20 * recent_norm)
        targeted = (score >= 0.45
                    or (rec["occurrence_count"]
                        >= int(config["pattern_occurrence_min"])
                        and rec["open_count"]
                        >= int(config["pattern_open_min"])))
        rec["score"] = _round(score, 4)
        rec["targeted"] = bool(targeted)
        rec["evidence"] = [
            {"kind": "occurrences",
             "detail": f"{rec['occurrence_count']} recorded occurrences"},
            {"kind": "open",
             "detail": f"{rec['open_count']} still open/improving"},
            {"kind": "recent",
             "detail": (f"{rec['recent_occurrences']} in the last "
                        f"{int(config['pattern_recent_window'])} records")},
        ]
        patterns.append(rec)
    return sorted(patterns, key=lambda p: (-p["score"], p["category"]))


def _weakness_model(concepts: Dict[str, Dict[str, Any]],
                    config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic weakness detection (section 4). A concept is weak
    only when supported by explicit evidence combinations."""
    out: List[Dict[str, Any]] = []
    for ck, rec in concepts.items():
        evidence: List[Dict[str, Any]] = []
        flags = 0
        attempts = int(rec["attempts"])
        recent_acc = float(rec["recent_accuracy"])
        # (a) low recent accuracy
        if attempts >= int(config["weak_min_attempts"]) \
                and rec["correct"] + rec["incorrect"] > 0 \
                and recent_acc <= float(config["weak_recent_accuracy"]):
            evidence.append({
                "kind": "recent_accuracy",
                "detail": (f"recent accuracy {recent_acc:.0%} over "
                           f"{attempts} attempts")})
            flags += 1
        # (b) unresolved repeated mistakes
        if rec["open_mistakes"] >= int(config["weak_open_mistake_min"]) \
                and rec["total_occurrences"] \
                >= int(config["weak_repeat_occurrence_min"]):
            evidence.append({
                "kind": "unresolved_mistakes",
                "detail": (f"{rec['open_mistakes']} open mistake(s), "
                           f"{rec['total_occurrences']} occurrences")})
            flags += 1
        # (c) degradation from a previously strong state
        if rec.get("mastery_state") == MASTERY_REVIEW:
            evidence.append({
                "kind": "mastery_degradation",
                "detail": "mastery state is REVIEW (previously strong)"})
            flags += 1
        # (d) repeated failures at a difficulty band
        for entry in rec["difficulty_outcomes"].values():
            total = entry["correct"] + entry["incorrect"]
            if total >= int(config["weak_difficulty_min_attempts"]) \
                    and entry["incorrect"] / total \
                    >= float(config["weak_difficulty_failure_rate"]):
                evidence.append({
                    "kind": "difficulty_failures",
                    "detail": (f"{entry['incorrect']}/{total} wrong at "
                               f"difficulty {entry['band']}")})
                flags += 1
        # (e) repeated failures on a transaction type
        for entry in rec["type_outcomes"].values():
            total = entry["correct"] + entry["incorrect"]
            if total >= int(config["weak_type_min_attempts"]) \
                    and entry["incorrect"] / total \
                    >= float(config["weak_type_failure_rate"]):
                evidence.append({
                    "kind": "type_failures",
                    "detail": (f"{entry['incorrect']}/{total} wrong on "
                               f"type {entry['type']}")})
                flags += 1
        if not flags:
            continue
        components = []
        if rec["correct"] + rec["incorrect"] > 0:
            components.append(1.0 - recent_acc)
        if rec["total_occurrences"] > 0:
            components.append(_clamp(
                0.5 * _clamp(rec["open_mistakes"] / 2.0)
                + 0.5 * _clamp(rec["total_occurrences"] / 5.0)))
        for entry in rec["difficulty_outcomes"].values():
            total = entry["correct"] + entry["incorrect"]
            if total:
                components.append(entry["incorrect"] / total)
        for entry in rec["type_outcomes"].values():
            total = entry["correct"] + entry["incorrect"]
            if total:
                components.append(entry["incorrect"] / total)
        score = _clamp(sum(components) / max(1, len(components)))
        out.append({
            "concept_key": ck,
            "score": _round(score, 4),
            "flags": flags,
            "recent_accuracy": _round(recent_acc, 4),
            "open_mistakes": rec["open_mistakes"],
            "mastery_state": rec.get("mastery_state") or MASTERY_UNSEEN,
            "evidence": evidence,
        })
    return sorted(out, key=lambda w: (-w["score"], w["concept_key"]))


def _strength_model(concepts: Dict[str, Dict[str, Any]],
                    config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Conservative strength detection (section 5). Strength is claimed
    only with sustained evidence - never from a single success."""
    out: List[Dict[str, Any]] = []
    for ck, rec in concepts.items():
        if int(rec["attempts"]) < int(config["strength_min_attempts"]):
            continue
        lifetime = float(rec["accuracy"])
        recent = float(rec["recent_accuracy"])
        if rec["correct"] + rec["incorrect"] == 0:
            continue
        if lifetime < float(config["strength_lifetime_accuracy"]) \
                or recent < float(config["strength_recent_accuracy"]):
            continue
        resolved = int(rec["resolved_mistakes"])
        never_mistaken = rec["total_occurrences"] == 0
        if not (resolved >= int(config["strength_resolved_mistake_min"])
                or never_mistaken):
            continue
        evidence = [
            {"kind": "sustained_accuracy",
             "detail": (f"lifetime {lifetime:.0%}, recent {recent:.0%} "
                        f"over {rec['attempts']} attempts")},
        ]
        if resolved >= 1:
            evidence.append({
                "kind": "resolved_mistakes",
                "detail": f"{resolved} historical mistake(s) resolved"})
        score = _clamp((lifetime + recent) / 2.0
                       + 0.05 * _clamp(resolved))
        out.append({
            "concept_key": ck,
            "score": _round(score, 4),
            "attempts": rec["attempts"],
            "lifetime_accuracy": _round(lifetime, 4),
            "recent_accuracy": _round(recent, 4),
            "resolved_mistakes": resolved,
            "evidence": evidence,
        })
    return sorted(out, key=lambda s: (-s["score"], s["concept_key"]))


def _type_strength(types: Dict[str, Dict[str, Any]],
                   config: Dict[str, Any]) -> tuple:
    weak: List[Dict[str, Any]] = []
    strong: List[Dict[str, Any]] = []
    for t, rec in types.items():
        attempts = int(rec["attempts"])
        if attempts < int(config["weak_type_min_attempts"]):
            continue
        accuracy = float(rec["accuracy"])
        if accuracy <= float(config["weak_type_failure_rate"]):
            weak.append({
                "transaction_type": t,
                "accuracy": _round(accuracy, 4),
                "attempts": attempts,
                "evidence": [{"kind": "type_accuracy",
                              "detail": (f"{rec['incorrect']}/{attempts} "
                                         f"wrong on type {t}")}]})
        if attempts >= int(config["strength_min_attempts"]) \
                and accuracy >= float(config["strength_lifetime_accuracy"]) \
                and float(rec["recent_accuracy"]) \
                >= float(config["strength_recent_accuracy"]):
            strong.append({
                "transaction_type": t,
                "accuracy": _round(accuracy, 4),
                "attempts": attempts,
                "evidence": [{"kind": "type_accuracy",
                              "detail": (f"{rec['correct']}/{attempts} "
                                         f"correct on type {t}")}]})
    return (sorted(weak, key=lambda x: (x["accuracy"], x["transaction_type"])),
            sorted(strong, key=lambda x: (-x["accuracy"],
                                          x["transaction_type"])))


def _difficulty_readiness(
        attempts: List[Dict[str, Any]],
        concepts: Dict[str, Dict[str, Any]],
        config: Dict[str, Any],
        question_meta=None) -> Dict[str, Any]:
    """Deterministic difficulty policy (section 7).

    Precedence: REMEDIATION (a REVIEW concept exists) > REINFORCE
    (repeated failures) > ADVANCE (repeated success) > MIXED > STAY.
    Difficulty bands are resolved from the APPROVED question metadata
    (attempt records do not carry difficulty).
    """
    window = int(config["readiness_recent_window"])
    scored = [a for a in attempts
              if a.get("outcome") in (OUTCOME_CORRECT, OUTCOME_INCORRECT)]
    recent = scored[-window:]

    bands: List[int] = []
    for a in recent:
        band = None
        if question_meta is not None:
            q = question_meta(a.get("question_id"))
            if q is not None:
                try:
                    band = int(q.get("difficulty") or 0)
                except (TypeError, ValueError):
                    band = None
        if band is None:
            try:
                band = int(a.get("difficulty") or 0)
            except (TypeError, ValueError):
                band = None
        if band in (1, 2, 3):
            bands.append(band)
    current_band = max(set(bands), key=bands.count) if bands \
        else int(config["default_difficulty_band"])

    consec_correct = 0
    consec_incorrect = 0
    for a in reversed(scored):
        if a.get("outcome") == OUTCOME_CORRECT:
            consec_correct += 1
            consec_incorrect = 0
        elif a.get("outcome") == OUTCOME_INCORRECT:
            consec_incorrect += 1
            consec_correct = 0
        else:
            break

    review_concepts = [ck for ck, rec in concepts.items()
                       if rec.get("mastery_state") == MASTERY_REVIEW]
    evidence: List[Dict[str, Any]] = [
        {"kind": "current_band",
         "detail": f"current difficulty band {current_band}"},
        {"kind": "recent_outcomes",
         "detail": (f"{consec_correct} consecutive correct / "
                    f"{consec_incorrect} consecutive incorrect")},
    ]

    if review_concepts:
        direction = DIRECTION_REMEDIATION
        target_band = current_band
        explanation = (
            "One or more previously strong concepts are in REVIEW, so "
            "practice reinforces the current level before advancing.")
        evidence.append({
            "kind": "review_concepts",
            "detail": ", ".join(sorted(review_concepts))})
    elif consec_incorrect >= int(config["reinforce_consecutive_incorrect"]):
        direction = DIRECTION_REINFORCE
        target_band = max(1, current_band - 1)
        explanation = (
            "Recent repeated failures suggest reinforcing the current "
            "or a slightly lower difficulty before moving on.")
    elif consec_correct >= int(config["advance_consecutive_correct"]):
        direction = DIRECTION_ADVANCE
        target_band = min(3, current_band + 1)
        explanation = (
            "You've been consistently successful at the current "
            "difficulty, so the next questions are slightly harder.")
    elif len(scored) >= 3 and consec_correct >= 1 and consec_incorrect >= 1:
        direction = DIRECTION_MIXED
        target_band = current_band
        explanation = (
            "Recent performance is mixed, so a balanced mix of "
            "difficulty levels keeps reinforcement and challenge "
            "together.")
    else:
        direction = DIRECTION_STAY
        target_band = current_band
        explanation = (
            "Not enough signal yet - staying at the current difficulty "
            "level.")
    return {
        "current_band": current_band,
        "direction": direction,
        "target_band": target_band,
        "explanation": explanation,
        "evidence": evidence,
        "consecutive_correct": consec_correct,
        "consecutive_incorrect": consec_incorrect,
    }


def _revision_priorities(mastery: Dict[str, Dict[str, Any]],
                         now_epoch: float,
                         config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic revision-priority score (section 8). Explicitly a
    documented scheduling rule - NOT a scientific memory-decay model."""
    interval_by_state = {
        MASTERY_REVIEW: float(config["revision_review_interval_days"]),
        MASTERY_LEARNING: float(config["revision_learning_interval_days"]),
        MASTERY_DEVELOPING: float(
            config["revision_developing_interval_days"]),
        MASTERY_MASTERED: float(config["revision_mastered_interval_days"]),
    }
    state_weight = {MASTERY_REVIEW: 1.0, MASTERY_LEARNING: 0.9,
                    MASTERY_DEVELOPING: 0.8, MASTERY_MASTERED: 0.7}
    out: List[Dict[str, Any]] = []
    for ck, rec in mastery.items():
        if int(rec.get("attempts") or 0) <= 0:
            continue
        state = rec.get("mastery_state") or MASTERY_UNSEEN
        interval = interval_by_state.get(
            state, float(config["revision_learning_interval_days"]))
        last_ts = _parse_iso(rec.get("last_attempt_at"))
        if last_ts is None:
            continue
        days = max(0.0, (now_epoch - last_ts) / 86400.0)
        if days <= 0:
            priority = 0.0
        else:
            overdue = days - interval
            base = _clamp(overdue / max(1.0, interval))
            priority = _clamp(
                base * state_weight.get(state, 0.8),
                hi=float(config["revision_priority_cap"]))
        if int(rec.get("_open_mistake_count") or 0) > 0:
            priority = _clamp(priority + 0.2)
        due = priority >= float(config["revision_due_threshold"])
        out.append({
            "concept_key": ck,
            "priority": _round(priority, 4),
            "days_since": _round(days, 2),
            "last_practiced_at": rec.get("last_attempt_at"),
            "mastery_state": state,
            "interval_days": interval,
            "due": bool(due),
            "evidence": [
                {"kind": "days_since",
                 "detail": (f"last practiced {days:.1f} days ago "
                            f"(interval {interval:.0f} days)")},
                {"kind": "mastery_state", "detail": state},
            ],
        })
    return sorted(out, key=lambda r: (-r["priority"], r["concept_key"]))


def _evidence_summary(
        attempts: List[Dict[str, Any]],
        mistakes: List[Dict[str, Any]],
        concepts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    correct = sum(1 for a in attempts
                  if a.get("outcome") == OUTCOME_CORRECT)
    incorrect = sum(1 for a in attempts
                    if a.get("outcome") == OUTCOME_INCORRECT)
    review = sum(1 for a in attempts
                 if a.get("outcome") == OUTCOME_REVIEW_REQUIRED)
    unsupported = sum(1 for a in attempts
                      if a.get("outcome") == OUTCOME_NOT_SUPPORTED)
    open_mistakes = sum(1 for m in mistakes
                        if m.get("status") in (MISTAKE_OPEN,
                                               MISTAKE_IMPROVING))
    resolved = sum(1 for m in mistakes
                   if m.get("status") == MISTAKE_RESOLVED)
    scored = correct + incorrect
    recent_scored = []
    for a in attempts[-10:]:
        s = _scored(a.get("outcome"))
        if s is not None:
            recent_scored.append(s)
    last_at = attempts[-1].get("submitted_at") if attempts else None
    return {
        "attempts": len(attempts),
        "scored_attempts": scored,
        "correct": correct,
        "incorrect": incorrect,
        "review_required": review,
        "unsupported": unsupported,
        "lifetime_accuracy": _rate(correct, incorrect),
        "recent_accuracy": (
            sum(recent_scored) / len(recent_scored)
            if recent_scored else 0.0),
        "open_mistakes": open_mistakes,
        "resolved_mistakes": resolved,
        "concepts_seen": len(concepts),
        "last_attempt_at": last_at,
    }


def _recommended_mix(weaknesses: List[Dict[str, Any]], mode: str,
                     config: Dict[str, Any]) -> Dict[str, float]:
    """Deterministic adaptive mix (section 12). Cold start uses the
    baseline mix; adaptive mode re-balances slightly when no weakness is
    present (remediation weight moves to current-level practice)."""
    if mode == MODE_COLD_START:
        return {
            "weakness_remediation": _round(
                float(config["cold_start_mix_weakness_remediation"])),
            "revision": _round(float(config["cold_start_mix_revision"])),
            "current_level": _round(
                float(config["cold_start_mix_current_level"])),
            "challenge": _round(float(config["cold_start_mix_challenge"])),
            "maintenance": _round(
                float(config["cold_start_mix_maintenance"])),
        }
    base = {
        "weakness_remediation": float(
            config["mix_weakness_remediation"]),
        "revision": float(config["mix_revision"]),
        "current_level": float(config["mix_current_level"]),
        "challenge": float(config["mix_challenge"]),
        "maintenance": float(config["mix_maintenance"]),
    }
    if not weaknesses:
        # No detected weakness: fold remediation share into current level.
        base["current_level"] = _round(
            base["current_level"] + base["weakness_remediation"])
        base["weakness_remediation"] = 0.0
    return {k: _round(v) for k, v in base.items()}


def _recommended_objective(mode: str,
                           weaknesses: List[Dict[str, Any]],
                           patterns: List[Dict[str, Any]]) -> str:
    if mode == MODE_COLD_START:
        return OBJECTIVE_MIXED_PRACTICE
    if weaknesses:
        return OBJECTIVE_WEAK_AREA_FOCUS
    targeted = [p for p in patterns if p.get("targeted")]
    if targeted:
        return OBJECTIVE_REMEDIATION
    return OBJECTIVE_MIXED_PRACTICE


def _focus_areas(weaknesses: List[Dict[str, Any]],
                 patterns: List[Dict[str, Any]],
                 revision: List[Dict[str, Any]],
                 strengths: List[Dict[str, Any]],
                 readiness: Dict[str, Any],
                 mode: str) -> List[Dict[str, Any]]:
    """Recommended focus areas with human-readable reasons + evidence."""
    areas: List[Dict[str, Any]] = []
    for w in weaknesses[:4]:
        areas.append({
            "area": w["concept_key"],
            "kind": "weakness",
            "reason": (f"You've had repeated difficulty with "
                       f"{w['concept_key']} - recent accuracy "
                       f"{w['recent_accuracy']:.0%}."),
            "evidence": w["evidence"],
        })
    for p in patterns:
        if not p.get("targeted"):
            continue
        areas.append({
            "area": p["category"],
            "kind": "mistake",
            "reason": (f"You keep making {p['category']} mistakes "
                       f"({p['occurrence_count']} occurrences, "
                       f"{p['open_count']} still open)."),
            "evidence": p["evidence"],
        })
    for r in revision:
        if not r.get("due"):
            continue
        areas.append({
            "area": r["concept_key"],
            "kind": "revision",
            "reason": (f"{r['concept_key']} has not been practiced in "
                       f"{r['days_since']:.0f} days, so it's due for "
                       "revision."),
            "evidence": r["evidence"],
        })
    if readiness.get("direction") == DIRECTION_ADVANCE:
        areas.append({
            "area": "difficulty",
            "kind": "difficulty",
            "reason": "You've been consistently successful at the "
                      "current difficulty, so the next questions are "
                      "slightly harder.",
            "evidence": readiness["evidence"],
        })
    if mode == MODE_ADAPTIVE and strengths:
        areas.append({
            "area": strengths[0]["concept_key"],
            "kind": "strength",
            "reason": (f"{strengths[0]['concept_key']} is a demonstrated "
                       "strength - keep it maintained with occasional "
                       "practice."),
            "evidence": strengths[0]["evidence"],
        })
    return areas[:8]


def _explanations(focus: List[Dict[str, Any]],
                  mode: str) -> List[str]:
    if mode == MODE_COLD_START:
        return [
            "Building your baseline - practice mixes concepts at a "
            "moderate level until enough evidence exists to personalize."
        ]
    return [f["reason"] for f in focus[:6]]


def _confidence(evidence_summary: Dict[str, Any],
                weaknesses: List[Dict[str, Any]],
                patterns: List[Dict[str, Any]],
                revision: List[Dict[str, Any]],
                mode: str,
                config: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic confidence: how much evidence supports each
    personalization dimension (0..1)."""
    min_attempts = max(1, int(config["min_adaptive_attempts"]))
    dims = {
        "evidence": _clamp(evidence_summary["scored_attempts"]
                           / min_attempts),
        "weakness": _clamp(len(weaknesses) / 3.0),
        "mistake_patterns": _clamp(
            len([p for p in patterns if p.get("targeted")]) / 3.0),
        "revision": _clamp(len(revision) / 5.0),
        "difficulty": _clamp(
            (evidence_summary["scored_attempts"] - 2) / 4.0),
    }
    overall = sum(dims.values()) / max(1, len(dims))
    return {
        "overall": _round(overall, 4),
        "mode": mode,
        "dimensions": {k: _round(v, 4) for k, v in dims.items()},
    }


def _recent_exposure(attempts: Dict[str, Dict[str, Any]],
                     student_id: str, question_meta,
                     lookback: int) -> Dict[str, float]:
    """Fraction of the student's recent attempts touching each dimension
    (concept / type / canonical). Used by the diversity controller."""
    student = _student_attempts(attempts, student_id)
    recent = student[-lookback:] if lookback else student
    n = max(1, len(recent))
    concept: Dict[str, int] = {}
    types: Dict[str, int] = {}
    canon: Dict[str, int] = {}
    for a in recent:
        q = question_meta(a.get("question_id"))
        if q is None:
            continue
        ck = str(q.get("concept_key") or "UNKNOWN")
        concept[ck] = concept.get(ck, 0) + 1
        cid = q.get("canonical_id") or q.get("question_id")
        canon[str(cid)] = canon.get(str(cid), 0) + 1
        for t in q.get("transaction_types") or []:
            types[t] = types.get(t, 0) + 1
    return {
        "concept": {k: v / n for k, v in concept.items()},
        "type": {k: v / n for k, v in types.items()},
        "canonical": {k: v / n for k, v in canon.items()},
    }


def _difficulty_fit(band: Any, target_band: int,
                    direction: str) -> float:
    try:
        b = int(band)
    except (TypeError, ValueError):
        return 0.0
    if b not in (1, 2, 3):
        return 0.0
    if direction == DIRECTION_ADVANCE:
        # Progress one step at a time: the next band is the best fit.
        if b == target_band:
            return 1.0
        if b == target_band + 1:
            return 0.6
        if b == target_band - 1:
            return 0.5
        return 0.2
    if direction == DIRECTION_REINFORCE:
        if b <= target_band:
            return 1.0
        if b == target_band + 1:
            return 0.5
        return 0.25
    if direction == DIRECTION_REMEDIATION:
        if b <= target_band:
            return 1.0
        return 0.5
    # MIXED / STAY
    if b == target_band:
        return 1.0
    if abs(b - target_band) == 1:
        return 0.6
    return 0.3


def _question_factors(
        q: Dict[str, Any],
        weakness: Dict[str, float],
        mistake: Dict[str, float],
        pattern_by_concept: Dict[str, float],
        pattern_by_type: Dict[str, float],
        pattern_by_question: Dict[str, float],
        revision: Dict[str, float],
        target_band: int,
        direction: str,
        exposure: Dict[str, Dict[str, float]],
        weights: Dict[str, float],
        config: Dict[str, Any],
        answered_set: Optional[set] = None) -> Dict[str, float]:
    ck = str(q.get("concept_key") or "UNKNOWN")
    band = q.get("difficulty")
    qid = q.get("question_id")
    cid = q.get("canonical_id") or qid

    # Weakness relevance.
    w_factor = _clamp(weakness.get(ck, 0.0))
    # Mistake relevance (best of concept / type / question association).
    m_values = [pattern_by_concept.get(ck, 0.0),
                pattern_by_question.get(qid, 0.0)]
    m_values += [pattern_by_type.get(t, 0.0)
                 for t in q.get("transaction_types") or []]
    m_factor = _clamp(max(m_values, default=0.0))
    # Revision due.
    r_factor = _clamp(revision.get(ck, 0.0))
    # Difficulty fit.
    d_factor = _difficulty_fit(band, target_band, direction)
    # Diversity (penalize dimensions touched recently).
    concept_exp = exposure["concept"].get(ck, 0.0)
    type_exp = max([exposure["type"].get(t, 0.0)
                    for t in q.get("transaction_types") or []],
                   default=0.0)
    canon_exp = exposure["canonical"].get(str(cid), 0.0)
    max_exp = max(concept_exp, type_exp, canon_exp)
    diversity = 1.0 - 0.8 * _clamp(max_exp)
    # Anti-repetition (sections 9/13): a question answered in THIS
    # session is never re-picked (except mistake-retry / exhausted pool);
    # an unseen variant of an answered canonical is preferred over the
    # canonical itself but still yields to fresh content.
    if answered_set:
        if qid in answered_set:
            diversity = 0.0
        elif str(cid) in answered_set:
            diversity = min(diversity, 0.5)
    # Challenge (only meaningful when the objective wants progression).
    try:
        b = int(band)
    except (TypeError, ValueError):
        b = 0
    challenge = 0.0
    if b in (1, 2, 3):
        if weights.get(FACTOR_CHALLENGE, 0.0) > 0:
            if b == target_band + 1:
                challenge = 1.0
            elif b == target_band:
                challenge = 0.5
            else:
                challenge = 0.2
        else:
            challenge = 0.0
    # Maintenance (current comfortable level).
    maintenance = 1.0 if b == target_band else 0.5
    return {
        FACTOR_WEAKNESS: _round(w_factor, 6),
        FACTOR_MISTAKE: _round(m_factor, 6),
        FACTOR_REVISION: _round(r_factor, 6),
        FACTOR_DIFFICULTY: _round(d_factor, 6),
        FACTOR_DIVERSITY: _round(diversity, 6),
        FACTOR_CHALLENGE: _round(challenge, 6),
        FACTOR_MAINTENANCE: _round(maintenance, 6),
    }


def _selection_reason(ranked: Dict[str, Any],
                      profile: Dict[str, Any],
                      objective: str,
                      weights: Dict[str, float],
                      is_top: bool) -> tuple:
    """Deterministic human-readable reason + evidence for one ranked
    question. The dominant factor (highest weight * factor) drives the
    reason template; every claim traces to the profile evidence."""
    factors = ranked["factors"]
    contributions = {f: weights.get(f, 0.0) * factors.get(f, 0.0)
                     for f in FACTORS}
    dominant = max(FACTORS, key=lambda f: contributions[f])
    qid = ranked["question_id"]
    q = _question_meta_of(ranked)
    ck = str((q or {}).get("concept_key") or "UNKNOWN")
    band = (q or {}).get("difficulty")

    evidence: List[Dict[str, Any]] = [
        {"kind": "objective", "detail": objective},
        {"kind": "question", "detail": qid},
    ]
    if band is not None:
        evidence.append({"kind": "difficulty", "detail": f"band {band}"})

    if dominant == FACTOR_WEAKNESS and factors[FACTOR_WEAKNESS] > 0:
        weak = next((w for w in profile["concept_weaknesses"]
                     if w["concept_key"] == ck), None)
        detail = (f"recent accuracy {weak['recent_accuracy']:.0%}"
                  if weak else "flagged weakness")
        reason = (f"This question practices {ck}, one of your weaker "
                  f"areas ({detail}).")
        evidence.extend((weak or {}).get("evidence") or [])
    elif dominant == FACTOR_MISTAKE and factors[FACTOR_MISTAKE] > 0:
        pat = max(
            [p for p in profile["mistake_patterns"]
             if p.get("score") and factors[FACTOR_MISTAKE]
             and _pattern_touches(p, q)],
            key=lambda p: p["score"], default=None)
        if pat is None:
            pat = max(profile["mistake_patterns"],
                      key=lambda p: p["score"], default=None)
        if pat is not None:
            reason = (f"It targets your recurring {pat['category']} "
                      f"mistakes in this area ({pat['occurrence_count']} "
                      f"occurrences, {pat['open_count']} still open).")
            evidence.extend(pat["evidence"])
        else:
            reason = "It targets your recurring mistake patterns."
    elif dominant == FACTOR_REVISION and factors[FACTOR_REVISION] > 0:
        rev = next((r for r in profile["revision_candidates"]
                    if r["concept_key"] == ck), None)
        if rev is not None:
            reason = (f"{ck} has not been practiced in "
                      f"{rev['days_since']:.0f} days, so it is due for "
                      "revision.")
            evidence.extend(rev["evidence"])
        else:
            reason = "This topic is due for revision."
    elif dominant == FACTOR_DIFFICULTY and factors[FACTOR_DIFFICULTY] > 0:
        direction = profile["difficulty_readiness"]["direction"]
        if direction == DIRECTION_ADVANCE:
            reason = ("You've been consistently successful at the "
                      "current difficulty, so this question is a step "
                      "harder.")
        elif direction == DIRECTION_REINFORCE:
            reason = ("Recent attempts suggest reinforcing the current "
                      "level before moving on.")
        elif direction == DIRECTION_REMEDIATION:
            reason = ("This stays at a level that reinforces your "
                      "flagged review concepts.")
        else:
            reason = "This question matches your target difficulty."
        evidence.extend(profile["difficulty_readiness"]["evidence"])
    elif dominant == FACTOR_CHALLENGE and factors[FACTOR_CHALLENGE] > 0:
        reason = "A slightly harder question to stretch your current level."
    elif dominant == FACTOR_DIVERSITY and factors[FACTOR_DIVERSITY] > 0:
        reason = ("This keeps your practice balanced across concepts "
                  "and transaction types.")
        evidence.append({"kind": "diversity", "detail": "balance control"})
    else:
        reason = ("A question at your current comfortable level to "
                  "maintain momentum.")
    if profile["mode"] == MODE_COLD_START:
        reason = ("Building your baseline - this question mixes "
                  "concepts at a moderate level.")
    return reason, evidence


def _pattern_touches(pattern: Dict[str, Any],
                     q: Optional[Dict[str, Any]]) -> bool:
    if q is None:
        return False
    ck = str(q.get("concept_key") or "UNKNOWN")
    qid = q.get("question_id")
    if ck in (pattern.get("concept_keys") or []):
        return True
    if qid in (pattern.get("question_ids") or []):
        return True
    for t in q.get("transaction_types") or []:
        if t in (pattern.get("transaction_types") or []):
            return True
    return False


def _question_meta_of(ranked: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Attach the question metadata captured at ranking time (kept on the
    # ranked entry itself so the reason builder needs no bank access).
    return ranked.get("_meta")


def _trend(rec: Dict[str, Any]) -> tuple:
    """Deterministic improving/degrading signals for the teacher view."""
    lifetime = float(rec.get("accuracy") or 0.0)
    recent = float(rec.get("recent_accuracy") or 0.0)
    attempts = int(rec.get("attempts") or 0)
    improving = (attempts >= 3 and recent > lifetime
                 and recent >= 0.6)
    degrading = (attempts >= 2 and recent < lifetime - 0.2
                 and recent <= 0.5)
    return improving, degrading


# ---------------------------------------------------------------------------
# Stable profile fingerprint (replay / determinism proofs)
# ---------------------------------------------------------------------------


def profile_fingerprint(profile: Dict[str, Any]) -> str:
    """Deterministic hash of a profile's DECISION content (evaluated_at
    excluded) - used to prove identical evidence -> identical profile."""
    stable = dict(profile)
    stable.pop("evaluated_at", None)
    payload = json_dumps_sorted(stable)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def json_dumps_sorted(value: Any) -> str:
    import json

    def _sort(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(k): _sort(item[k]) for k in sorted(item)}
        if isinstance(item, (list, tuple)):
            return [_sort(v) for v in item]
        return item

    return json.dumps(_sort(value), sort_keys=True, default=str)
