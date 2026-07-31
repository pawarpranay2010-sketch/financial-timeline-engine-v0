# REAL INDIAN FINANCIAL REPORT — END-TO-END STRESS TEST REPORT

**Date:** 2026-07-31
**Document:** Tata Motors Limited Form 20-F (annual report), filed with SEC EDGAR 2023-06-12, covering FY2023 / FY2022 / FY2021 (year ended 31 March). Official regulatory source. 13.2 MB, IFRS Inline XBRL (`ifrs-full` + company extension namespaces), ₹/INR, consolidated + standalone statements, ~1.36M extracted characters.
**Mode:** Validation only. No architecture, code, or test changes were made to fix findings. Every failure below has a proposed minimal fix awaiting approval.

---

## 1. EXECUTIVE VERDICT

**NOT document-agnostic yet. The system is SEC/US-GAAP-strong, IFRS-weak.**

| Dimension | Apple 10-K (US-GAAP XBRL) | Tata Motors 20-F (IFRS XBRL) |
|---|---|---|
| Overall | **11/11 PASS** (Extraction 2.0 e2e) | **40 PASS / 3 FAIL / 9 WARN** |
| Extraction accuracy vs authoritative XBRL | ~100% (XBRL-tagged facts dominate) | **50.0% (10/20)** |
| XBRL facts surviving mapping | 305 unique | **51 of 5,749 (0.9%)** |
| Period contamination | 0 | 8+ glossary-era periods (1978, 1986, FY2012…) |
| Scale propagation to EvidenceItem | broken (latent) | broken (now observed) |

**Root cause of the asymmetry:** the XBRL concept map in `financial_extractor_v2.py` is tuned to **US-GAAP** concept names (`Revenues`, `StockholdersEquity`, `CashAndCashEquivalentsAtCarryingValue`…). The Tata 20-F is **IFRS** (`ifrs-full:Revenue`, `ifrs-full:Equity`, `ifrs-full:CashAndCashEquivalents`…) and those local names are absent from `_XBRL_CONCEPT_MAP` and `_KEEP_UNMAPPED_CONCEPTS`, so ~5,698 of 5,749 structured facts were discarded before dedup. The extractor therefore fell back to table/text paths, where the accuracy problem was previously known to live.

---

## 2. TOTAL RESULTS

| # | Count |
|---|---|
| 1. Total tests | **52** |
| 2. Passed | **40** |
| 3. Failed | **3** |
| 4. Warnings | **9** |

**FAILURES**
1. `[3-Extraction] All 10 required metrics found — missing=['EBITDA']`
2. `[5-Scale] Scale survives fact→EvidenceItem — scale LOST at this boundary`
3. `[5-Scale] Scale notation equivalence` — **test-expectation bug, not code bug** (see 7)

**WARNINGS (9)**: OCR N/A (HTML) · table scale annotation only 33/1012 tables · 278 table rows missing label/cells · **extraction accuracy 50%** · **anchors traceable 37.9%** · period comparisons only 9 (<10) · 3 period mismatches · state dedup 954→954 (extraction already deduped) · provider/Redis N/A.

---

## 3. TEST-BY-TEST RESULTS

### TEST 1 — DOCUMENT INGESTION ✅
- Ingest 8.45 s · type html · 1,360,538 chars · **1,012 tables detected** · 144 chunks · **5,749 XBRL facts parsed** · 1,095 raw → 954 unique facts (141 SHA-256 duplicates suppressed) · extraction core 1.80 s · no crash, sane memory.
- BEFORE (regex-first) baseline on same doc: `Revenue=2022.0, NetProfit=2021.0, Assets=160.0, EPS=15.0` — the same fiscal-year poisoning class as the Apple run. V2 eliminates this class.

### TEST 2 — TABLE INTEGRITY ⚠️
- ✅ 1,012 structured tables · 81 with period columns · 7 with currency · row labels+cells mostly present.
- ⚠️ Only **33/1,012 tables carry scale annotation** and **7 carry currency** — the 20-F states "in millions"/"in crores" and "Rs." in **captions and column titles**, which the header-only detection frequently misses.
- ⚠️ **278 structural issues** — rows missing label/cells (merged headers, spanning rows in SEC-rendered HTML tables).

### TEST 3 — FINANCIAL EXTRACTION ❌ (9/10 metrics)
- Found: Revenue (135), NetIncome (97), TotalAssets (33), TotalLiabilities (30), CashAndEquivalents (69), TotalDebt (106), OperatingCashFlow (7), EPS (4), ShareholdersEquity (16).
- **MISSING: EBITDA — root cause: the 20-F text contains zero "EBITDA" labels (diagnostic confirmed `EBITDA mentions: 0`).** Tata Motors' IFRS 20-F does not use the literal label "EBITDA"; the required-metrics list assumes it will. The system correctly did NOT invent an EBITDA value (no hallucination), but the check fails.
- Confirmed **false positive class**: `Revenue=197.0 (period 1978)`, `Revenue=201.0 (FY2015)`, `IncomeTax=198.0 (1986)` — small bare numbers in legal/glossary text at the front of the filing get paired with a nearby year and accepted.

### TEST 4 — REAL-VALUE VALIDATION ⚠️ **50.0% (10/20)**
Validated 20 metric×period facts against the filing's own authoritative XBRL values. Mismatches, all SCALE_MISMATCH class (text path):
- Revenue FY2023: XBRL 1,093,017,200,000 vs extracted 102,990 (≈5.8% at crore scale — closest but outside 2% tolerance)
- Revenue FY2022: XBRL 780,372,700,000 vs 521,107.8 (33% off)
- Equity FY2022: XBRL 440,564,600,000 vs 1.0
- CashAndEquivalents FY2023: XBRL 3,880,600,000 vs 3.0; FY2022: 381,590,100,000 vs 1.8
- Equity FY2021, Cash FY2021/FY2020: NO_EXTRACTION

**Root cause:** the XBRL path that would have provided exact values was disabled by the US-GAAP-only concept map; the table/text fallback then produced raw cell values with **scale words applied unreliably** (only when `abs(value) < 10,000` and only from the adjacent window). **Fairness caveat:** the 20-F contains multiple Revenue-family XBRL concepts (total vs segment vs JLR); the validator used the first matching concept, so part of the mismatch may be concept-selection ambiguity rather than pure extraction error. Both are real; neither is fixed yet.

### TEST 5 — SCALE STRESS ❌
- ✅ Multiplier math correct: 1,250 crore → 12,500,000,000 ✓ · 1.25 billion → 1,250,000,000 ✓ · 125,000 million → 125,000,000,000 ✓.
- ❌ **TEST-EXPECTATION BUG (disclosed):** my harness expected `12,500 lakh = 12,500,000,000`, but 12,500 × 100,000 = **1,250,000,000 (1.25B)**. The code's multiplier is correct; the test data was wrong. Not a code defect.
- ❌ **REAL FINDING — scale lost at EvidenceItem boundary:** `to_evidence_item_dict()` copies raw `metric_value` (e.g., 2,900,069 for a millions-denominated table cell) and **drops `scale`/`normalized_value`**. The frozen `EvidenceItem` interface has no scale field. **Boundary where scale disappears: `FinancialExtractorV2.to_evidence_item_dict()` → `EvidenceItem.value`.** Downstream EvidenceConsolidator / calculation would see 2,900,069, not 2,900,069,000,000.

### TEST 6 — CURRENCY STRESS ✅ 4/4
INR/INR compatible ✓ · INR/USD blocked (`CURRENCY_MISMATCH: Cannot divide INR and USD`) ✓ · EUR/USD blocked ✓ · bulk mixed set blocked ✓. Real doc facts: 41 INR / 122 USD facts — mixed-currency sets correctly gated.

### TEST 7 — ACCOUNTING DEFINITION STRESS ✅
18 metrics preserved with >1 distinct `metric_definition` (e.g., `NetIncome` keeps "Net income/(loss) attributable to non-controlling interest", "Net income/(loss) before tax", XBRL concept, and contextual matches as separate definitions). No name-based merging observed. IFRS/US-GAAP/Ind-AS distinctions survive via the definition string.

### TEST 8 — NEGATIVE VALUES ✅
18 genuine negative facts found (e.g., NetIncome −113,699; −142,701; −112,372.2) · footnote refs `(1)…(5)` NOT converted (0 false positives) · `(500)` → −500 ✓ · **false-positive rate 0%**.

### TEST 9 — PERIOD ASSOCIATION ⚠️
Only 9 metric×period comparisons were constructible (needs ≥10). 3 mismatches (Revenue FY2023/FY2022/FY2021) — all traceable to the same scale/fallback root cause. **Plus 8 glossary-era contamination facts** (periods 1978, 1986, FY2012, FY2013, FY2015, FY 2018, FY 2020) from the definitions/legal-text tables at the front of the 20-F.

### TEST 10 — AGENTIC RAG RETRIEVAL ⚠️ (document-backed, real orchestrator)
- 5 requirements generated (Revenue, NetIncome, EBITDA, EBIT, TotalAssets — all FY2023).
- **2/3 iterations**, 2 retrieval calls; stopped early because iteration 2 returned only duplicates.
- **Terminal state: INSUFFICIENT_EVIDENCE** — 3 CONFLICTING (Revenue, NetIncome, TotalAssets), 2 MISSING (EBITDA, EBIT).
- Evidence: 954 unique · dedup functional · **missing metrics marked MISSING, not invented** ✓.
- Requirement evaluator correctly flags multi-value evidence as CONFLICTING rather than guessing ✓.

### TEST 11 — CONFLICT RESOLUTION ✅
Tier-3 vs Tier-1 → `RESOLVED`, winner tier 3 ✓ (deterministic, no LLM). `20-F` vs `20-F/A` → amendment wins ✓. Same-tier conflict → `UNRESOLVED_CONFLICT` (no guessing) ✓.

### TEST 12 — CALCULATION SAFETY ⚠️
- ✅ Currency mismatch blocks ✓ · missing evidence marked MISSING ✓ · unresolved conflict blocks in SourceResolver ✓.
- ⚠️ **REAL GAP:** `_check_calculation_block()` in the orchestrator returns "allow" for **INSUFFICIENT_EVIDENCE and RETRIEVAL_LIMIT_REACHED** — it only blocks on UNRESOLVED_CONFLICT / CURRENCY_MISMATCH / EXTRACTION_CORRUPTED. In this run, terminal = INSUFFICIENT_EVIDENCE with 3 CONFLICTING + 2 MISSING requirements, yet the canonical set was still populated with **954 PENDING (unverified) items** as "resolved_facts". A consumer reading `canonical.resolved_facts` directly would compute against unverified, conflicting evidence. The user's rule "MISSING/CONFLICTING evidence MUST block" is **not fully enforced at the orchestrator gate**.

### TEST 13 — CONTEXT-WINDOW SAFETY ✅
Extraction dedup 1,095→954 · EvidenceSummaryState held 954 unique (all already unique post-extraction) · **compact context ~79 chars vs 531,841 chars naive dump** (~6,400× smaller) · ~20 token estimate. The LLM is never fed the accumulated retrieval history ✓.

### TEST 14 — PERFORMANCE ✅
ingest+extract 8.45 s · extraction core 1.80 s · RAG orchestration 0.03 s · dedup 954 · total ≈ 8.5 s for a 13 MB, ~300-page-equivalent filing. No optimization attempted.

### TEST 15 — FAILURE SURVIVAL ✅ (6/6 + 1 N/A)
Empty extraction → empty facts, no crash ✓ · missing currency → compatible-default, no crash ✓ · conflicting values → unresolved, no invention ✓ · duplicate chunks suppressed ✓ · missing tables → safe fallback ✓ · provider/Redis — N/A (document-only pipeline).

---

## 4. FINAL REPORT — REQUIRED NUMBERS

| Item | Value |
|---|---|
| 5. Extraction accuracy | **50.0%** (10/20 vs authoritative XBRL) |
| 6. False-positive rate | **0%** on footnote references; small-number/year false positives present (197.0, 201.0) |
| 7. Scale accuracy | Multipliers correct; **scale lost at to_evidence_item_dict boundary** (real) |
| 8. Currency validation accuracy | **100%** (4/4 gates) |
| 9. Period-association accuracy | 9 comparisons, 3 mismatches (33%); +8 glossary contamination facts |
| 10. Evidence-anchor accuracy | 37.9% literal-resolvable (structured locators like `html table #269 | row:…` are not substring-searchable — partially a measurement artifact) |
| 11. RAG iterations | 2/3 (early stop on duplicate-only iteration), terminal INSUFFICIENT_EVIDENCE |
| 12. Context before/after dedup | 1,095 → 954 (extraction) · 954 unique in state · compact 79 chars vs 531,841 naive |
| 13. Performance | ingest 8.45 s · extract 1.80 s · RAG 0.03 s · total ≈ 8.5 s |
| 14. Provider failures | N/A (document-only pipeline) |
| 15. Calculation blocks | currency ✓ missing ✓ conflict-in-resolver ✓; **orchestrator gate fails to block on INSUFFICIENT_EVIDENCE/CONFLICTING (954 unverified items exposed)** |

---

## 5. DATA-CORRUPTION RISKS (ranked)

1. **Scale loss at `to_evidence_item_dict()`** — a table cell `2,900,069` (₹ millions) flows downstream as 2,900,069, silently 1,000,000× wrong if consumed for calculation. **Highest severity.**
2. **IFRS XBRL discard** — 5,698/5,749 authoritative facts dropped; system silently degrades to weaker paths. **Highest impact.**
3. **Glossary/legal-year period contamination** — Revenue=197.0 (period 1978) type facts poison period filters.
4. **Small-number false positives** (197, 201, 198) accepted as metric values via too-wide context windows.

## 6. HALLUCINATION RISKS

**Low but present.** Positives: EBITDA absent → not invented; missing metrics marked MISSING; conflicts marked CONFLICTING. Residual risk: the orchestrator's canonical set includes **PENDING (unverified)** items on INSUFFICIENT_EVIDENCE — if the app consumes `resolved_facts` without re-checking `terminal_state`, an AI narrative layer could cite numbers that were never verified.

## 7. PRODUCTION BLOCKERS

1. XBRL concept map is **US-GAAP-only** → IFRS/Ind-AS filings lose structured facts (confirmed on real IFRS filing).
2. **Scale is not carried** through `to_evidence_item_dict()` → EvidenceItem → calculation (confirmed on real data).
3. Orchestrator calculation gate does **not block on INSUFFICIENT_EVIDENCE / RETRIEVAL_LIMIT_REACHED / CONFLICTING** and exposes unverified PENDING items.
4. Period detection accepts **legal/glossary years** as fiscal periods (1978, FY2012…) on Indian-format filings.
5. Required-metric registry assumes literal labels (EBITDA) that IFRS filings do not use.

---

## 8. PROPOSED MINIMAL FIXES (NOT IMPLEMENTED — awaiting approval)

| # | Failure | Affected component | Smallest fix |
|---|---|---|---|
| 1 | IFRS XBRL discard (0.9% survival) | `financial_extractor_v2._XBRL_CONCEPT_MAP` / `_KEEP_UNMAPPED_CONCEPTS` | Add IFRS mappings: `Revenue→Revenue`, `Equity→ShareholdersEquity`, `CashAndCashEquivalents→CashAndEquivalents`, `ProfitLoss→NetIncome`, `EarningsPerShareBasic/Diluted→EPS`, `Assets/Liabilities` (already present), plus the company-extension prefix (`cik…`) as a pass-through tier. ~30-line additive change. |
| 2 | Scale lost at EvidenceItem boundary | `financial_extractor_v2.to_evidence_item_dict()` | Carry `value` as **normalized_value** (scale applied) into `EvidenceItem.value` when `normalized_value` is present, keeping `raw_value` metadata in `source_anchor`/definition; OR thread a scale-aware value upstream at ingestion. 5-line change + test. |
| 3 | Gate doesn't block on missing/conflicting | `agentic_rag_orchestrator._check_calculation_block()` | Also return False for `INSUFFICIENT_EVIDENCE`, `RETRIEVAL_LIMIT_REACHED`, and states with `conflict_count > 0`; skip populating canonical set with PENDING items unless terminal is COMPLETE. ~10-line change. |
| 4 | Glossary-year contamination | `financial_extractor_v2._find_period_in_text` + `_first_valid_number` context window | Narrow the period window to reporting-statement context (require a currency/scale/table relation or a "FY" prefix / month-name date); reject bare 4-digit years unless directly adjacent to a number with financial context. ~15-line change. |
| 5 | Required-metric assumption (EBITDA) | `REQUIRED_METRICS` in harness / `METRIC_REGISTRY` | Treat "label absent" as legitimate missing-evidence when the filing's basis (IFRS) doesn't use it; report as MISSING rather than FAIL; optionally map `ifrs-full` equivalents. Test-only/harness change. |

---

## 9. REMAINING HONEST LIMITATIONS

- Test data was the **HTML Inline-XBRL** filing (Tata investor sites are Cloudflare-blocked; SEC EDGAR is the official source and was used). A **native ~300-page PDF** (multi-column, scanned pages) is still unvalidated; PDF table extraction is unit-tested only.
- "EBITDA" literal absence could also mean MD&A EBITDA was rendered in a format the text extraction missed — needs a manual grep of the original filing to distinguish (worth 1 check).
- Anchor-resolvability metric penalizes structured locators; a proper anchor resolver (table_id → row → cells) is needed before trusting the 37.9% figure.

---

## 10. VERDICT

**NOT production-ready for arbitrary documents. Internal-testing ready for US-GAAP SEC filings; FAIL on IFRS/Indian filings as-is.**

The gap between **11/11 (Apple, US-GAAP XBRL)** and **40/3/9 (Tata, IFRS XBRL)** proves the system is **not yet document-agnostic**: it is excellent when the XBRL concept map matches the taxonomy (US-GAAP), and degrades sharply when it does not (IFRS/Ind-AS), because the fallback table/text paths carry the previously-documented scale/period weaknesses. The fixes in §8 are small, additive, and confined to the extraction layer plus one gate function — the frozen Agentic RAG / verification / calculation architecture itself performed correctly and needs no redesign.
