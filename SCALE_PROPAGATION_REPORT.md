# FIX #2 — SCALE PROPAGATION REPORT

**Date:** 2026-07-31
**Scope:** Preserve financial scale metadata end-to-end (extraction → EvidenceItem → EvidenceSummaryState → CanonicalEvidenceSet → Calculation Engine).
**Status:** ✅ COMPLETE & VERIFIED — no regressions. Fixes #3–#5 NOT implemented.

---

## 1. Root Cause (confirmed by trace)

Scale metadata was **dropped at the `FinancialFact → EvidenceItem` boundary**:

- `EvidenceItem.value` was set to the **raw extracted number** (`metric_value`), while both `normalized_value` and `scale` were discarded at conversion.
- Downstream consumers (SHA-256 dedup, conflict detection, CanonicalEvidenceSet, calculation input) therefore only ever saw the **ambiguous raw magnitude** — e.g. table cell `2,900,069` (₹ millions) could be read as 2.9M, 2.9B, or 290,006.9 crore equivalents depending on context.
- Two contributing defects found while tracing:
  1. `TableExtractor._from_native_dict` **ignored explicit `scale`/`currency` metadata keys** on native table data (only header-detection was used), so structured tables with scale annotations lost the annotation before facts were even built.
  2. `_facts_from_tables` applied an `abs(value) < 10_000` magnitude guard even when an **explicit table-level scale annotation** was present, blocking the authoritative annotation from being applied to large cell values (the `2,900,069 million → 2,900,069,000,000` case).

## 2. Changes Made (additive only — no architecture change)

| File | Change |
|---|---|
| `backend/intelligence/evidence_summary_state.py` | `EvidenceItem` extended **additively** with `original_value`, `scale`, `normalized_value`; `value` is now the **normalized magnitude**. (EvidenceSummaryState is NOT in the frozen list for Fix #2; requirement 7 explicitly requires scale to survive this layer.) |
| `backend/extraction2/financial_extractor_v2.py` | `to_evidence_item_dict()` now emits `original_value`, `scale`, `normalized_value` and sets `value = normalized_value`. Explicit scale annotations now apply **unconditionally** (magnitude-guard only applies to *inferred* fallbacks). Percentage / per-share scales carry explicit labels. |
| `backend/extraction2/table_extractor.py` | `_from_native_dict` now **prefers explicit `scale`/`currency` metadata keys**, falling back to header detection only when absent. |
| `tests/test_scale_propagation.py` | **NEW — 18 tests** covering requirements A–L: all scales (thousand/million/billion/lakh/crore/unit/percentage/per-share), million↔billion equivalence, lakh↔crore equivalence, mixed-scale tables, XBRL scale/unit, scale through EvidenceItem conversion, scale through CanonicalEvidenceSet, calculation receives normalized values, original value+scale retained for audit, **no accidental 1000×/100× transforms**, US-GAAP & IFRS suites kept green. |
| `tests/stress_test_indian_report.py` | Harness updated: lakh expectation corrected to the user's spec (`12,500 lakh → 1,250,000,000`, not 12.5B — the code multiplier was already correct); boundary checks now assert the fixed behavior. |

No database schema change was required for this fix (`metric_value` normalized + `unit` on `ExtractedFact`; scale/original are preserved on the extraction artifact and evidence layers).

## 3. BEFORE → AFTER — Tata Motors 20-F Scale Failures

| Check | BEFORE (INDIAN_STRESS_TEST_REPORT) | AFTER (Fix #2) |
|---|---|---|
| Scale through `to_evidence_item_dict()` | ❌ **LOST** — raw magnitude only downstream | ✅ `value=3457000000000.0 original=3457.0 scale=crores normalized=3457000000000.0` |
| Scale through EvidenceSummaryState → CanonicalEvidenceSet | ❌ lost at first boundary | ✅ `resolved value=3457000000000.0 scale=crores` |
| `2,900,069 million` equivalence | ❌ ambiguous raw magnitude | ✅ `→ 2,900,069,000,000` |
| `2,900.069 billion` equivalence | ❌ | ✅ `→ 2,900,069,000,000.0` |
| `290,006.9 crore` equivalence | ❌ | ✅ `→ 2,900,069,000,000.0` |
| `12,500 lakh` equivalence | ⚠️ harness expectation bug (disclosed) | ✅ `→ 1,250,000,000` (matches spec exactly) |
| `1,250 crore` / `125,000 million` / `1.25 billion` | — | ✅ all → `12,500,000,000` / `125,000,000,000` / `1,250,000,000` |
| Extraction accuracy vs XBRL | 100% (Fix #1) | **100% (20/20) maintained** |
| Tata stress-test totals | 40 PASS / 3 FAIL / 9 WARN | **45 PASS / 1 FAIL / 7 WARN (53 total)** |

The **only remaining FAIL** is `EBITDA missing` — the Tata 20-F genuinely contains **zero** mentions of "EBITDA" (verified: 0 occurrences), so the system correctly did **not** hallucinate it. This is a PASS-grade behavior flagged by the harness as a missing metric.

## 4. Full Verification Matrix

| Suite | Result |
|---|---|
| Scale Propagation (new, A–L) | **18/18 PASS** |
| Extraction 2.0 | **40/40 PASS** |
| IFRS XBRL | **10/10 PASS** |
| Agentic RAG | **35/35 PASS** |
| AI Executive integration | **57 PASS · 0 FAIL · 1 WARN** |
| App integration | **42 PASS · 0 FAIL** |
| Apple SEC/XBRL real-doc e2e | **11/11 PASS** (no US-GAAP regression) |
| Tata Motors 20-F real-doc stress | **45 PASS · 1 FAIL · 7 WARN** |

## 5. Remaining WARNs on Tata (context)

- Scale annotations: 33 tables carry scale (structural table issues still flagged — table-scale annotation in footers remains a known gap, documented in EXTRACTION_2_0_REPORT).
- Evidence anchors: 85.8% resolvable.
- Period association: 9 comparisons (harness threshold 10) — Fix #4 territory.
- RAG dedup `4174 → 4174`: no *new* duplicates remain at RAG level because SHA-256 dedup already collapsed 1007 duplicates at extraction (facts_unique=4174) — expected behavior.
- Provider/Redis isolation not exercised (document-only pipeline) — Fix #5 territory.

## 6. Frozen Components — Untouched ✅

`agentic_rag_orchestrator.py`, `source_resolver.py`, `currency_validator.py`, `extraction_auditor.py`, `ai_executive.py`, `database/models.py`, `database_manager.py`, `normalizer.py` — **no modifications** in this fix.

## 7. Next Steps (per one-at-a-time plan — NOT implemented)

1. Fix #3 — Calculation Safety Gate (`_check_calculation_block` must block PENDING/MISSING/CONFLICTING/INSUFFICIENT_EVIDENCE at the engine boundary).
2. Fix #4 — Period Contamination (glossary/legal-text years like `Revenue=1978`).
3. Fix #5 — FX Metadata Validation (INR/USD, stale/missing FX rate).

**STOPPING here — awaiting approval before implementing Fix #3.**
