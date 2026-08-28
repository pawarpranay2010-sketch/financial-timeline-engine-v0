# Sprint P3 — Persistent Validated Learning + Student Feedback Loop
## Final Report

**Classification:** PASS — 135/135 tests green
**Date:** 2026-08-28

---

## 1. Root Cause / Motivation

Sprint P2 built a validated-knowledge store that ran purely in-memory. Every restart lost all accumulated learning. Students' corrections were never captured as evidence. No metrics existed to measure whether learning was actually helping. P3 addresses all three gaps:

- **Persistence**: Knowledge survives across sessions
- **Feedback loop**: Student corrections become candidate evidence
- **Metrics**: Measurable evidence of whether learning reduces REVIEW_REQUIRED

---

## 2. Files Created / Modified

| File | Type | LOC | Purpose |
|------|------|----:|---------|
| `backend/maths/fyjc_p3_learning_system.py` | **NEW** | +520 | P3 learning system: persistence, feedback, metrics, effectiveness, suggestions |
| `scripts/fte_fyjc_p3_learning_test.py` | **NEW** | +540 | 135-test regression suite |

**Production LOC delta: +520** (1 new file)
**Kernel changes: 0** — accounting kernel, splitter, normalization, orchestration untouched

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                P3LearningManager                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │Persistence│  │ Feedback │  │    Metrics       │  │
│  │  Layer    │  │   Loop   │  │   Dashboard      │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────────┐  ┌────────────────────────────┐   │
│  │  UI Suggest  │  │  Effectiveness Tracking    │   │
│  └──────────────┘  └────────────────────────────┘   │
│                     ↓ wraps ↓                       │
│            ValidatedKnowledgeStore (P2)              │
└─────────────────────────────────────────────────────┘
         ↑ external wrapper (NOT embedded in engine)
         ↓
┌─────────────────────────────────────────────────────┐
│  process_problem() — UNCHANGED (deterministic)      │
│  resolve_problem_transaction() — UNCHANGED          │
└─────────────────────────────────────────────────────┘
```

**Key design decision:** P3 is a standalone wrapper layer, NOT embedded in `process_problem()`. This preserves byte-identical determinism of the engine output across runs.

---

## 4. Persistence Design (P3.1)

| Feature | Implementation |
|---------|---------------|
| Format | JSON (deterministic serialization) |
| Atomic writes | tempfile → os.replace() (crash-safe) |
| Corruption handling | Malformed files return None, never crash |
| Empty file handling | Returns None |
| Missing file handling | Returns None |
| Round-trip fidelity | Evidence trail, status history, timestamps preserved |
| Retired items | Status preserved, excluded from lookups after load |

**API:**
- `KnowledgePersistence.save(store, path) → bool`
- `KnowledgePersistence.load(path) → Optional[ValidatedKnowledgeStore]`
- `KnowledgePersistence.is_valid_store_file(path) → bool`

---

## 5. Student-Feedback Flow (P3.2)

```
Student resolves REVIEW_REQUIRED
         ↓
record_student_correction()
         ↓
  pattern = transaction text
  interpretation = student answer
  source = STUDENT
  scope = SESSION (never auto-global)
         ↓
  extract_candidate()  ← P2 pipeline
         ↓
  Candidate with evidence_count += 1
         ↓
  Promotion only after threshold met
```

**Rules enforced:**
- Single correction → CANDIDATE, never VALIDATED
- Scope defaults to SESSION (never auto-global)
- Source diversity required (student + deterministic = 2 sources)
- Conflicting corrections detected, not silently resolved
- Student evidence cannot create journal entries or modify accounting truth

---

## 6. UI Knowledge Suggestions (P3.3)

| Rule | Implementation |
|------|---------------|
| Visually distinct | Prefixed with "Previously seen:" |
| Never silently change | Suggestions are advisory only |
| Student can ignore | Suggestions don't trigger any accounting action |
| Cannot bypass REVIEW_REQUIRED | Suggestions shown alongside, not instead |
| Cannot create journal | Suggestions have no journal field |
| Cannot mutate ledger | Suggestions are read-only metadata |
| Low-confidence filtered | Min confidence 0.85 to show |
| Conflicting knowledge hidden | find_conflicts() → skip |
| Retired knowledge hidden | Status check before display |

**Example output:**
```json
{
  "hint": "Previously seen: this phrase often indicates a credit transaction.",
  "interpretation": "credit purchase",
  "knowledge_type": "PAYMENT_MODE_CONVENTION",
  "confidence": "0.88",
  "scope": "GLOBAL"
}
```

---

## 7. Metrics Dashboard (P3.4)

Tracks all required metrics deterministically:

| Category | Metrics |
|----------|---------|
| Transactions | total, verified, review_required, blocked |
| Rates | clarification_rate, resolution_rate, acceptance_rate, promotion_rate |
| Suggestions | shown, accepted, dismissed |
| Knowledge lifecycle | candidates_created, promoted, rejected, retired, conflicts, rollbacks |
| Safety | incorrect_verified, verified_empty_journal |
| Corrections | received, as_evidence |

**Most important metric:** REVIEW_REQUIRED rate ↓ while INCORRECT_VERIFIED = 0

---

## 8. Knowledge Effectiveness Tracking (P3.5)

| Status | Condition |
|--------|-----------|
| UNKNOWN | < 3 samples |
| HELPFUL | > 60% acceptance rate |
| NEUTRAL | Mixed, < 60% any direction |
| REJECTED | > 60% dismissal rate |
| CONFLICTING | > 20% kernel mismatch |
| RETIRE_CANDIDATE | 0 accept + ≥3 dismissals in 5+ samples |

---

## 9. Tests Added

**135 tests** across 10 sections:

| Section | Tests | Coverage |
|---------|:-----:|----------|
| 1. Persistence | 22 | save/load, atomic, corrupt, empty, missing, round-trip, retired, evidence trail |
| 2. Feedback Loop | 11 | correction flow, promotion prevention, evidence accumulation, scope default |
| 3. UI Suggestions | 12 | validated items, hint text, low-confidence filter, conflict filter, retired filter |
| 4. Metrics Dashboard | 18 | recording, rates, lifecycle, safety, snapshot determinism, sections |
| 5. Effectiveness | 10 | unknown, helpful, rejected, conflicting, neutral, retire_candidate, manager |
| 6. Anti-Poisoning | 7 | single incorrect, repeated incorrect, conflicts, cross-student, rollback, malicious |
| 7. Scope Isolation | 8 | session/curriculum/global scope, hierarchy allows/blocks |
| 8. Determinism | 7 | 3-run snapshot, metrics, suggestions, persistence round-trip |
| 9. Integration | 9 | process_problem output, P3 wrapper, feedback, safety gates |
| 10. Regression Gates | 31 | py_compile, all sprint suites, git diff, kernel signature, 3-run determinism |

---

## 10. Full Regression Results

| Gate | Result |
|------|:------:|
| P3 Learning Tests | 135/135 ✅ |
| Sprint 35 Integrity | 9/9 ✅ |
| Sprint 36 UI Contract | 36/36 ✅ |
| Sprint 37 Calc Scoping | 31/31 ✅ |
| Sprint 43 Structured Memory | 34/34 ✅ |
| Sprint P2 Validated Knowledge | 95/95 ✅ |
| py_compile | PASS ✅ |
| git diff --check | PASS ✅ |
| 3-run determinism | PASS ✅ |
| Kernel signature preserved | PASS ✅ |

---

## 11. Safety Invariants

| Invariant | Status |
|-----------|:------:|
| INCORRECT_VERIFIED = 0 | ✅ |
| VERIFIED with 0 journal = 0 | ✅ |
| Integrity violations = 0 | ✅ |
| Mutation violations = 0 | ✅ |
| Missing transactions = 0 | ✅ |
| Duplicate transactions = 0 | ✅ |
| Cross-contamination = 0 | ✅ |
| Calculation contamination = 0 | ✅ |
| Unvalidated knowledge → truth = 0 | ✅ |
| Retired knowledge in runtime = 0 | ✅ |
| Scoped knowledge leakage = 0 | ✅ |
| Silent conflict resolution = 0 | ✅ |
| Same input + same state = byte-identical output | ✅ |

---

## 12. Determinism Results

- **process_problem() output**: Byte-identical across 3 runs ✅
- **P3 metrics snapshot**: Deterministic (timestamps excluded) ✅
- **P3 suggestions**: Deterministic for same input ✅
- **P3 persistence round-trip**: Deterministic across 2 save/load cycles ✅
- **Knowledge store snapshot**: Identical across 3 fresh stores with same seed ✅

---

## 13. Remaining Unresolved Cases

| Issue | Impact | Recommendation |
|-------|--------|----------------|
| No real student pilot data | Cannot measure if P3 actually reduces REVIEW_REQUIRED | Requires production deployment + real student usage |
| Effectiveness thresholds not calibrated | May be too aggressive or too lenient | Calibrate after 100+ real suggestion outcomes |
| No file locking for concurrent writes | Two processes writing to same file could corrupt | Add advisory file locking in future sprint |
| Metrics timestamps in output | Could break byte-identical comparison if embedded in engine | Current design (standalone wrapper) avoids this |

---

## 14. Critical Question: Does P3 reduce REVIEW_REQUIRED without increasing incorrect verification?

**Answer: Not yet measurable — production/student pilot data required.**

P3 provides the infrastructure to measure this:
- Metrics track clarification_rate and suggestion_acceptance_rate
- Effectiveness tracking measures whether suggestions help
- Safety metrics (INCORRECT_VERIFIED) are continuously monitored

But the actual measurement requires real students interacting with real accounting problems over multiple sessions. The persistence layer ensures that learning accumulates, and the feedback loop captures corrections — but whether this translates to fewer REVIEW_REQUIRED outcomes is an empirical question that cannot be answered from synthetic test data alone.

---

## 15. Recommendation for P4

1. **Student pilot deployment** — Deploy P3 to a small group of real students and measure clarification_rate over 2 weeks
2. **Metrics dashboard UI** — Wire LearningMetrics.snapshot() into a Streamlit admin page for real-time monitoring
3. **Session persistence** — Persist knowledge store per-student (not just globally) to track individual learning patterns
4. **Compound feedback** — When a student resolves TX3's ambiguity, check if the same pattern applies to TX1 and TX7 automatically
5. **Knowledge graduation** — After enough CURRICULUM-scoped evidence, promote to GLOBAL with student pilot data as evidence

---

## 16. What Was NOT Changed

| Component | Status |
|-----------|--------|
| Accounting kernel | ✅ Untouched |
| Splitter | ✅ Untouched |
| Normalization | ✅ Untouched |
| Orchestration | ✅ Untouched |
| Historical state resolution | ✅ Untouched |
| Deterministic verification rules | ✅ Untouched |
| process_problem() output | ✅ Untouched (P3 is standalone) |
| resolve_problem_transaction() | ✅ Untouched |
| Existing regression suites | ✅ All pass unchanged |
| No new dependencies | ✅ Pure Python stdlib only |
