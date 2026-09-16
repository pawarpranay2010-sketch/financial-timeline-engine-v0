# PLATRIXA — PHASE 21 AUDIT REPORT: R2/R4/R5 + HARDCORE DATASET FREEZE

**Date:** 2026-09-16
**Phase:** 21 — R2/R4/R5 audit + Phase 20 corpus freeze
**Classification:** ✅ **PASS** (53/53 audit checks, 545/545 regression checks, dataset integrity proven, runtime untouched)
**Evidence suite:** `scripts/fte_fyjc_71_phase21_audit_test.py` — **53/53 PASS**

---

## Dataset (freeze target)

| Property | Value |
|---|---|
| Filename | `training_data/fyjc_hardcore_1000.jsonl` |
| SHA-256 | `56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd` |
| Rows | exactly 1,000 |
| ID range | `hc_00001` … `hc_01000` (no gaps, no duplicates) |
| Status distribution | `REVIEW_REQUIRED` 1000/1000 · VERIFIED claims **0** |
| Original corpus | `fyjc_specialist_1000.jsonl` byte-for-byte identical (`feb7bfe5…61e24`) |
| Manifest | `fyjc_hardcore_1000.manifest.json` hash + row count match the JSONL |

---

## R2 — substring keyword false positive

**R2 = CONFIRMED BUG — FUTURE PATCH REQUIRED**

**Reproduction (production `FYJCAISpecialist.parse()`, unmodified):**

| Input | Current classification | Recorded evidence |
|---|---|---|
| "paid off" | SETTLEMENT ✅ | `paid off` |
| "paid office" | **SETTLEMENT ❌ false positive** | `paid off` |
| "paid off today" | SETTLEMENT ✅ | `paid off` |
| "was paid off" | SETTLEMENT ✅ | `paid off` |
| "office was paid" | UNKNOWN ✅ (no false SETTLEMENT) | — |

**Root cause (exact source):** `backend/maths/fyjc_ai_specialist.py`, `_detect_transaction_type()` — the loop `for w in _SETTLEMENT_WORDS: if w in lower:` (lines 216–218) performs **raw substring matching** on the lowercased text; `"paid off"` is a substring of `"paid office"`. The identical `if w in lower:` mechanism is used in **all 12 vocabulary loops** (7 transaction-type + 5 payment-method).

**Affected matcher scope (collision scan):**

| Matcher | Keyword | Collides inside |
|---|---|---|
| SETTLEMENT | `paid off` | `paid office` |
| PURCHASE | `got` | `forgotten`, `gotten`, `gotcha` |
| SALE | `sales` | `salesgirl` |
| PM CASH | `cash` | `cashless`, `cashew`, `cashing` |
| PM CHEQUE | `cheque` | `chequebook`, `cheques` (plural is legitimate) |
| PM CREDIT | `credit` | `creditor`, `credited`, `discredited` |
| RETURN | `return` | `returns` (legitimate plural) |
| DRAWING | `drawing` | `drawings` (legitimate plural) |
| SETTLEMENT | `settlement` | `settlements` (legitimate plural) |

**Mechanism:** raw substring matching (`in` operator), not token, normalized, or regex matching. The grounding gate's keyword checks (`fyjc_grounding_gate.py` lines 294/335) use the same `kw in text_lower` pattern and share the defect class.

**Proposed future fix (specification only — NOT implemented):** boundary-aware phrase matching — for each keyword, require the match to start at a word boundary and end at a word boundary, tolerating standard English inflectional suffixes (`-ed`, `-d`, `-es`, `-s`, `-ing`): conceptually `\b<keyword>(?:ed|d|es|s|ing)?\b` against the normalised text, or equivalent token-level matching after punctuation stripping. This preserves the legitimate plurals/inflections above while eliminating `paid office`/`forgotten`/`salesgirl`/`cashless` collisions. Prefer this over an exclusion list.

**Regression-test requirement:** the fte_71 R2 section (12 checks) encodes exactly this specification — the five phrase cases, the `forgotten`/`salesgirl`/`cashless` probes, the "must keep" inflection probes, and the currently-misclassified `"Paid office expenses…"` case. Any future patch must turn `R2.paid_office_false_positive` and the other false-positive checks to "not SETTLEMENT/PURCHASE/SALE/CASH" while keeping the inflection checks green.

**Impact on the Phase 20 corpus:** the 8 spurious-evidence rows were already rejected by the Phase 20 `keyword_substring_mismatch` guard (data-side); the frozen 1,000 contain **no** spurious-evidence rows.

---

## R4 — generic receipts classify UNKNOWN

**R4 = VOCABULARY_GAP** (with the kernel-side semantics fully present downstream)

**Representative cases (production trace, each verified schema-valid):**

| Input | tx_enum | flags | gate |
|---|---|---|---|
| "Received Rs.5,000 cash from Rahul against our bill." | UNKNOWN | MULTIPLE_INTERPRETATIONS | clean* |
| "Received a cheque of Rs.25,000 from Sharma Traders." | UNKNOWN | MULTIPLE_INTERPRETATIONS | clean* |
| "Sharma Traders sent Rs. 60000 by NEFT towards dues." | UNKNOWN | MISSING_PARTY + MULTIPLE_INTERPRETATIONS | clean* |
| "Received commission Rs.2,000." | UNKNOWN | MISSING_* + MULTIPLE_INTERPRETATIONS | clean* |

\* gate-clean after the corpus-convention amount-format fix (Phase 20 R3); raw specialist output trips only that artifact, no additional grounding failure.

**Execution trace / evidence:**
1. `_detect_transaction_type()` contains **no `return "RECEIPT"` path at all** (grep-level: absent from source) — the contract enum `TransactionTypeEnum.RECEIPT` is unreachable from the specialist; same for `"PAYMENT"`.
2. The specialist's only "received"-adjacent coverage is `_EXPENSE_WORDS` ("paid…"), `_SETTLEMENT_WORDS`, and nothing for money-in except three narrow noun templates; generic money-in therefore falls through to UNKNOWN + MULTIPLE_INTERPRETATIONS (honest low-confidence behaviour, not a wrong answer).
3. The **kernel owns full receipt semantics**: golden case A09 (`fyjc_dataset.py`: "Received commission Rs.2,000." → `INCOME_RECEIVED`, VERIFIED) and `fyjc_bk_reasoning.py` RECEIPT/PAYMENT handling — i.e., the downstream accounting layer understands the event; only the interpretation layer lacks the words.
4. Contrast: wording mapped by existing vocabulary resolves correctly (e.g., settlement phrasing → SETTLEMENT), proving the gap is lexical, not architectural.

**Not:** a semantic gap (kernel semantics exist), a grounding gap (facts in these rows ground cleanly), or intentional ambiguity by design (the ambiguity flags are a *symptom* of missing vocabulary, not the intent).

---

## R5 — generic on-account payments

**R5 = INTENTIONAL_UNKNOWN** (correct fail-closed resolution of genuinely mode-agnostic evidence; partial vocabulary overlap with R4)

**Representative cases (production trace):**

| Input | tx_enum | flags |
|---|---|---|
| "Paid Mohan Rs.5,000." | UNKNOWN | MISSING_PAYMENT_MODE + MULTIPLE_INTERPRETATIONS |
| "Paid Mohan Rs.5,000 in cash." | UNKNOWN | MULTIPLE_INTERPRETATIONS |
| "Paid Mohan Rs.5,000 in full settlement." | SETTLEMENT | MISSING_PAYMENT_MODE |
| "Paid Rs.3,000 to Suresh towards the outstanding dues." | UNKNOWN | MISSING_PAYMENT_MODE + MULTIPLE_INTERPRETATIONS |
| "Paid Mohan Rs.5,000 against the dues of Rs.10,000; balance still payable." | UNKNOWN | MISSING_PAYMENT_MODE + MULTIPLE_INTERPRETATIONS (both amounts found) |

**Evidence / reasoning:**
1. The production stack **refuses to guess**: a bare "Paid X to Y" has no explicit mode and no settlement intent — the runtime flags it instead of fabricating CASH/CREDIT. This is the documented fail-closed contract working (contrast with the grounding gate's rule: UNKNOWN payment = "correctly not fabricated").
2. Wording carrying *resolvable* evidence does resolve: "in full settlement" → SETTLEMENT; explicit modes ground correctly. The distinction tracks evidence quality, not input length.
3. The partial-settlement case demonstrates genuine insufficiency: both amounts are extracted (5,000 and 10,000), but the relation "partial payment on account" is not an expressible interpretation in the current specialist contract — flagging MULTIPLE_INTERPRETATIONS is the honest response. The kernel-side settlement semantics (`fyjc_bk_reasoning.py`: `_full_immediate_settlement`, 104 settlement references, D4 anti-invented-discount guard) exist downstream and deliberately refuse to read "half paid" as "full settlement".
4. **Residual overlap with R4:** the tx=UNKNOWN *classification* component is the same missing-vocabulary issue; the *payment-mode* component here is correct behaviour. R5's net classification is therefore INTENTIONAL_UNKNOWN, with the classification-side vocabulary gap tracked under R4.

**Not patched.** No new accounting rules, no model changes, no runtime edits.

---

## Runtime integrity

All hashes verified unchanged during Phase 21 (fte_71 section I):

| File | SHA-256 |
|---|---|
| `backend/maths/fyjc_ai_specialist.py` | `bc722a9d3ed3…32d1` |
| `backend/maths/fyjc_contract.py` | `3535a6cfc872…4afa` |
| `backend/maths/fyjc_grounding_gate.py` | `3909af0ff3f1…feb3e` |
| `backend/maths/schema_verifier.py` | `539010bbb918…1c783` |
| `backend/kernel/kernel.py` | `ee524adc6519…1b17e` |
| `backend/model_provider/base.py` | `bf1e82830519…4fc2` |

Kernel, grounding invariants, CandidateSemanticIR / GroundedSemanticIR, schema verifier, provider adapters, model identity pins, evidence chain, and PostgreSQL persistence: **unchanged**.

---

## Regression results (exact counts and exit codes)

| Suite | Result | Exit code |
|---|---|---|
| fte_fyjc_70 (Phase 20 dataset checks A–P) | 30/30 | 0 |
| fte_fyjc_71 (Phase 21 audit + freeze gate) | 53/53 | 0 |
| fte_fyjc_52 (kernel boundary) | 20/20 | 0 |
| fte_fyjc_53 (grounding verification wiring) | 13/13 | 0 |
| fte_fyjc_62 (hosted API boundary) | 55/55 | 0 |
| fte_fyjc_65 (specialist suite) | 47/47 | 0 |
| fte_fyjc_66 (metered gate, real PostgreSQL) | 51/51 | 0 |
| fte_fyjc_67 (Phase 17 boundary) | 48/48 | 0 |
| fte_fyjc_68 (rules + benchmark) | 51/51 | 0 |
| fte_fyjc_69 (Phase 18 persistence, real PostgreSQL) | 49/49 | 0 |
| fte_fyjc_48 (training dataset) | 53/53 ✅ ALL TESTS PASSED | 0 |
| fte_fyjc_49 (autotrain preflight) | 75/75 ✅ ALL PREFLIGHT TESTS PASSED | 0 |
| **Total** | **545/545** | **0** |

No test was weakened; no expected value was altered. (fte_69 was run with its ephemeral PG cluster on `/dev/shm` because the sandbox disk is at 100% — an environment accommodation outside the repository, no repo file changed.)

---

## Training leakage check (21F)

| Independent set | Rows | Exact overlap | Near-dup (Jaccard ≥ 0.75) |
|---|---:|---:|---:|
| `specialist_ambiguity_eval.jsonl` (P5A) | 20 | **0** | **0** |
| `specialist_unsupported_eval.jsonl` (P5A) | 24 | **0** | **0** |
| `specialist_robustness_eval.jsonl` (P5A) | 9 | **0** | **0** |
| `specialist_clean_training.jsonl` | 46 | **0** | **0** |
| `fyjc_specialist_1000.jsonl` (original) | 1,000 | **0** | **0** |
| `training/phase17_benchmark.jsonl` (locked eval) | 92 | **0** | **2** |

**The 2 near-dups against the locked Phase 17 benchmark are template-level collisions with different amounts:**
- `PB-0005` "Sold goods to Amit for Rs.15000 for cash" ↔ `hc_00928` "Sold goods to Amit for Rs. 7500 cash." (J=0.78)
- `PB-0016` "Withdrew Rs.4000 from the business for personal use" ↔ `hc_00092` "Withdrew Rs. 50000 from the business for personal use." (J=0.80)

**Policy recorded for Phase 22:** the frozen corpus file is never altered; the training-slice builder must exclude `hc_00928` and `hc_00092` (the mandatory exclusion list). No new evaluation set was created by post-hoc splitting.

**INDEPENDENT_EVAL_SET = ESTABLISHED_BY_PRIOR_PHASES** (the P5A tiers and the locked 92-case Phase 17 benchmark already exist, are independent of this corpus, and are overlap-checked). Nothing was split out of the 1,000.

---

## Freeze declaration

> **Phase 20 hardcore corpus is frozen for LoRA training. No Phase 21 audit finding was silently patched into the runtime.**

- `training_data/fyjc_hardcore_1000.jsonl` — sha256 `56be5be1…c721cd`, 1,000 rows, `hc_00001…hc_01000`, 1000/1000 REVIEW_REQUIRED, 0 VERIFIED — frozen, byte-identical, manifest-consistent.
- R2 = CONFIRMED BUG — FUTURE PATCH REQUIRED (fix spec + regression spec recorded above; data-side guard already protected the corpus during Phase 20).
- R4 = VOCABULARY_GAP (kernel semantics exist downstream; specialist lacks money-in vocabulary).
- R5 = INTENTIONAL_UNKNOWN (correct fail-closed behaviour; classification-side vocabulary tracked under R4).
- Runtime modified: **NO**. Phase 20 artifacts modified: **NO**.

---

## Phase 22 recommendation — LoRA training v0.2 (short)

1. **Free GPU, pinned recipe:** train on free Colab/Kaggle T4 with the locked identity (`Qwen/Qwen2.5-1.5B-Instruct` @ `989aa798…`); new adapter `platrixa-fyjc-specialist-v0.2` — never overwrite v0.1.
2. **Data:** original 1,000 (cleaned of its 815 VERIFIED-status claims per R6 first — gate-clean them the same way Phase 20 did) + frozen hardcore 1,000, minus `hc_00928` and `hc_00092`; keep the P5A tiers and Phase 17 benchmark as untouched eval.
3. **Capacity bump:** QLoRA r=16→64, alpha=128, target all seven linear projections — still fits a T4 at 4-bit for a 1.5B model.
4. **Fixed decode params** (greedy or temperature 0) for reproducible evaluation; evaluate through the production schema verifier + grounding gate with whole-transaction compositional accuracy as the headline metric.
5. **Do not** touch the R2 matcher as part of training; land the boundary-aware fix in a separate runtime phase with the fte_71 R2 section as its acceptance test.
