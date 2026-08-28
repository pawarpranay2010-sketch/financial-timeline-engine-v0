# PLATRIXA P4.3.2 — Storage Audit + Dataset Grounding Hardening

**Date:** 2026-08-28
**Classification:** ✅ PASS (32/32 core tests + regression gates green)
**Kernel modified:** NO
**Model trained:** NO
**Model downloaded:** NO

---

## CRITICAL FINDING: Storage Architecture

### 1. Which database/storage is P4 actually connected to right now?

**Local JSON file persistence.** NOT connected to any database.

### 2. Is it the existing Railway database?

**NO.**

### 3. What exact code proves that?

| Module | Persistence mechanism | Evidence |
|--------|----------------------|----------|
| `fyjc_p4_problem_learning.py` | `json.dump()` / `json.load()` with `tempfile.mkstemp` + `os.replace` | Lines 341-374 |
| `fyjc_p3_learning_system.py` | `json.dump()` / `json.load()` with atomic writes | Lines 76-127 |
| `fyjc_validated_knowledge.py` | In-memory only (no persistence) | No file I/O |

**Zero imports** from `backend.database`, `sqlalchemy`, `psycopg2`, or any database driver in any P4/P4.2/P3/P2 module.

### 4. What exact storage is being used?

- **P4 ProblemLearningDatabase**: Atomic JSON file at configurable `_db_path`
- **P3 KnowledgePersistence**: Atomic JSON file at configurable path
- **P4.2 JSONLExporter**: JSONL file writes
- **P4.2 DatasetTier builder**: JSONL file writes to `training_data/`

### 5. Is there currently more than one persistent problem-data store?

**YES — two separate stores:**

| Store | Technology | Used by | Location |
|-------|-----------|---------|----------|
| Railway PostgreSQL | SQLAlchemy + psycopg2 | Module 4 (financial data ingestion) | `backend/database/db.py` |
| Local JSON files | `json.dump/load` | P4/P4.2/P3/P2 (FYJC problem learning) | `training_data/*.jsonl` + configurable paths |

**These are completely independent.** P4 never imports or references the Railway database.

### 6. What future migration would be required?

A future sprint should migrate P4's local JSON persistence to the existing Railway PostgreSQL database to:
- Share the production database
- Enable cross-session persistence
- Support concurrent access
- Provide backup/recovery

**This migration was NOT performed in this sprint** — it requires careful architectural planning and is recommended for a future sprint.

---

## Dataset Re-audit (from actual files)

### Source Corpus

| Status | Count | Percentage |
|--------|:-----:|:----------:|
| VERIFIED | 47 | 47.0% |
| REVIEW_REQUIRED | 20 | 20.0% |
| NOT_SUPPORTED | 24 | 24.0% |
| BLOCKED | 7 | 7.0% |
| EXCEPTION | 2 | 2.0% |
| **Total** | **100** | **100%** |

### Four Tiers (from exported files)

| Tier | Records | Purpose |
|------|:-------:|---------|
| specialist_clean_training | **47** | Model training |
| specialist_ambiguity_eval | **20** | Ambiguity detection |
| specialist_unsupported_eval | **24** | Unsupported intent |
| specialist_robustness_eval | **9** | Safety/robustness |

---

## Field-Level Grounding Audit

### transaction_type (n=47)

| Classification | Count | Percentage |
|----------------|:-----:|:----------:|
| EXPLICIT | 46 | 97.9% |
| ABSENT | 1 | 2.1% |
| INFERRED | 0 | 0.0% |

### parties (n=47)

| Classification | Count | Percentage |
|----------------|:-----:|:----------:|
| EXPLICIT | 43 | 91.5% |
| ABSENT | 4 | 8.5% |

*ABSENT cases: "Paid rent Rs.5000", "Paid salary Rs.25000" — genuinely no party name in student text.*

### amounts (n=47)

| Classification | Count | Percentage |
|----------------|:-----:|:----------:|
| EXPLICIT | 46 | 97.9% |
| ABSENT | 1 | 2.1% |

### payment_method (n=47) — CRITICAL FINDING

| Classification | Count | Percentage | Handling |
|----------------|:-----:|:----------:|----------|
| EXPLICIT | 14 | 29.8% | Directly stated by student |
| INFERRED | 33 | 70.2% | Inferred from journal credit account |

**70.2% of payment methods are INFERRED.** This is correctly marked in the grounding metadata. Per the inferred field policy:

- ✅ These records remain ELIGIBLE for training
- ✅ The inference is grounded in deterministic kernel output
- ✅ `grounding.inferred_fields` contains `"payment_method"`
- ✅ The value is NOT presented as explicitly stated

### references (n=47)

| Classification | Count | Percentage |
|----------------|:-----:|:----------:|
| EXPLICIT | 6 | 12.8% |
| ABSENT | 41 | 87.2% |

### ambiguities (n=47)

| Classification | Count | Percentage |
|----------------|:-----:|:----------:|
| EXPLICIT | 30 | 63.8% |
| ABSENT | 17 | 36.2% |

---

## Labeling Mechanism Trace

| Field | Function | Mechanism | Classification |
|-------|----------|-----------|----------------|
| transaction_type | `_detect_transaction_type()` | Regex keywords + journal account names | EXPLICIT or INFERRED |
| parties | `_extract_parties()` | Regex "from/to \<Name\>" | EXPLICIT |
| amounts | `_extract_amounts()` | Regex Rs./₹/INR patterns | EXPLICIT |
| payment_method | `_detect_payment_method()` | Regex cash/credit/cheque keywords OR journal credit account | EXPLICIT or INFERRED |
| references | `_detect_references()` | Regex pronoun/historical patterns | EXPLICIT |
| ambiguities | `_detect_ambiguities()` | Regex ambiguity patterns | EXPLICIT |

---

## Training Eligibility

| Metric | Value |
|--------|:-----:|
| Usable before grounding filter | 47 |
| Removed by grounding filter | 0 |
| **Usable after grounding filter** | **47** |
| Minimum floor | 40 |
| **PASS/FAIL** | **PASS** |

**No records were removed by the grounding filter.** All 47 VERIFIED records have substantive interpretations with correctly marked inferred fields.

---

## Deduplication

| Metric | Value |
|--------|:-----:|
| Total records across tiers | 100 |
| Unique problem IDs | 100 |
| Intra-tier duplicates | 0 |
| Cross-tier overlap | 0 |

---

## AI Target Safety

| Check | Result |
|-------|:------:|
| Journal fields in training output | 0 / 47 ✅ |
| Interpretation fields present | 47 / 47 ✅ |
| AI target = structured interpretation | YES ✅ |
| Truth Kernel = sole accounting authority | YES ✅ |

---

## Inferred Field Policy — Implemented

| Rule | Status |
|------|:------:|
| INFERRED records remain eligible | ✅ |
| Inference marked in grounding.inferred_fields | ✅ |
| Inference NOT presented as explicit | ✅ |
| No fabrication of missing information | ✅ |
| Genuinely absent fields correctly classified | ✅ |

---

## Safety Invariants

```
Kernel unchanged:                    ✅
No Railway connection in P4:         ✅
Atomic JSON persistence:             ✅
No fabricated labels:                 ✅
INFERRED fields correctly marked:    ✅
No journal in training target:       ✅
Train/eval overlap = 0:              ✅
Duplicate problem_ids = 0:           ✅
No model downloaded:                 ✅
No training executed:                ✅
Existing regression suites green:    ✅ (Sprint 35: 9/9, Sprint 36: 36/36)
```

---

## Test Results

| Suite | Result |
|-------|:------:|
| P4.3.2 Core Tests | 32/32 ✅ |
| Sprint 35 Integrity | 9/9 ✅ |
| Sprint 36 UI Contract | 36/36 ✅ |
| py_compile | PASS ✅ |

---

## Files

| File | Type | Purpose |
|------|------|---------|
| `scripts/fte_fyjc_p4_3_2_storage_audit_test.py` | NEW | 32-test regression suite |
| `PLATRIXA_P4_3_2_STORAGE_AUDIT_REPORT.md` | NEW | This report |

**Zero existing files modified.**

---

## Recommended Next Sprint

**P5 — Railway Migration + Model Download**

1. Migrate P4 local JSON persistence to Railway PostgreSQL
2. Download Qwen2.5-1.5B-Instruct model weights
3. Run first training experiment on 47 clean training examples
4. Evaluate on all four tiers
5. Wire model adapter to grounding/confidence gate

---

## Answers to Final Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Database/storage P4 connected to? | **Local JSON file persistence** |
| 2 | Is it Railway? | **NO** |
| 3 | What code proves it? | `json.dump/load` in `fyjc_p4_problem_learning.py:341-374` |
| 4 | If not Railway, what? | Atomic JSON files + JSONL exports |
| 5 | More than one store? | **YES** — Railway (Module 4) + Local JSON (P4) |
| 6 | Future migration needed? | **YES** — recommended for P5 |
| 7 | Genuinely eligible training examples? | **47** |
| 8 | Passes 40-example floor? | **YES** (47 ≥ 40) |
| 9 | EXPLICIT vs INFERRED vs ABSENT? | See field audit above |
| 10 | Any fields incorrectly marked? | **NO** |
| 11 | Any fabricated values? | **NO** |
| 12 | Reproduces P4.2 statistics? | **YES** — all counts match |
| 13 | AI target = interpretation? | **YES** |
| 14 | Truth Kernel sole authority? | **YES** |
| 15 | Structured Memory restricted? | **YES** |
| 16 | AI/Kernel boundary preserved? | **YES** |
| 17 | Models downloaded? | **NO** |
| 18 | Training executed? | **NO** |
| 19 | Kernel modified? | **NO** |
| 20 | Next sprint? | **P5 — Railway Migration + Model Download** |
