# PLATRIXA — PHASE H REPORT
## Financial Semantic Training Dataset (8,000 examples)

**Date:** 2026-09-19 · **Status:** dataset-generation checkpoint (no changes to any authority, contract, or production code) · **Commit context:** Phases A–F frozen at `1ed73c5`; Phase G committed at `0f73202`

---

## 1. Executive summary

Phase H produced `training_data/phase_h_v01_8000.jsonl` — an 8,000-example financial semantic training dataset that broadens beyond the FYJC/payment-heavy distribution of prior corpora while exercising **only the capabilities already implemented by the three existing authorities** (Phases A–F).

Core principle, enforced in code:

```
EXISTING AUTHORITIES DEFINE THE CAPABILITY BOUNDARY.
PHASE H TRAINS THE MODEL TO RECOGNIZE AND USE THAT BOUNDARY.
```

Every accepted row's model target is the **exact existing 18-field CandidateSemanticIR** (re-verified across all 8,000 rows — zero violations), with Phase G dataset metadata (`docs/phase_g/dataset_architecture.json`) kept in a strictly separate `metadata` object that never leaks into the production output. The model target never claims `VERIFIED`; status verdicts live in metadata only, and the runtime remains the sole authority for status.

Machine-readable artifacts live in `docs/phase_h/` (6 files). This report is the canonical narrative; the manifest `training_data/phase_h_v01_manifest.json` is the canonical machine contract.

## 2. Source of truth

Phase G artifacts were hashed into the manifest and verified byte-intact before generation and again at freeze:

| Artifact | sha256 (first 16) |
|---|---|
| `PLATRIXA_PHASE_G_REPORT.md` | `e277dc937a272e68…` |
| `docs/phase_g/market_use_cases.json` | `a345339447b6ccd5…` |
| `docs/phase_g/financial_input_taxonomy.json` | `3252c72df9cdc428…` |
| `docs/phase_g/financial_event_ontology.json` | `668fbb0474790d17…` |
| `docs/phase_g/relationship_taxonomy.json` | `50cb785389c3370d…` |
| `docs/phase_g/defensive_taxonomy.json` | `896456c02dcece2f…` |
| `docs/phase_g/18_field_coverage_matrix.json` | `c35944e10f39db6a…` |
| `docs/phase_g/dataset_architecture.json` | `13d18f5ab35d00e2…` |

Capability was measured from the live implementation (kernel pre-flight against `backend.maths.fyjc_bk_reasoning.reason_bk_question`, registry counts from `backend.maths.capability_registry`), not inferred from documentation.

## 3. Dataset architecture

```
template registry (event families from Phase G ontology)
    ↓  design-time KERNEL PRE-FLIGHT: every supported family must prove
    ↓  kernel-VERIFIED wordings before entering the pool (families that
    ↓  fail are demoted to defensive/REVIEW_REQUIRED and logged)
deterministic style engine (16 styles; degraded styles become defensive classes)
    ↓
FYJCAISpecialist.parse()          ← structural 18-field machinery
    ↓
kernel-wording family consensus   ← honest classification layer
    ↓   (the heuristic specialist provably cannot label RECEIPT/PAYMENT
    ↓    families; kernel wordings are the ground truth — metadata
    ↓    records label_source for every row)
schema_verifier (allow_expanded)  ← production contract validation
    ↓
ExpandedGroundingGate.ground()    ← production grounding (fail-closed)
    ↓
duplicate / near-dup / leakage / party-sanity / metadata-consistency
    ↓
accepted row (18-field output + STRICTLY SEPARATE Phase G metadata)
```

**Non-goals enforced in code (`training/phase_h_generate.py`):**

- the model target NEVER claims `VERIFIED` — `expected_status` lives in metadata only
- no new 18-field fields, enums, or authority capabilities
- no OCR engine, no document parser, no LLM generation (deterministic only)
- determinism: `PYTHONHASHSEED=0` pinned before imports, `random.Random(SEED)` only, sorted iteration, no timestamps — byte-identical rebuild (seed `20260919`, `llm_used: false`)

## 4. Dataset composition

**Contract:** 18-field output re-measured at freeze — 8,000/8,000 rows match the exact field set; **0 rows claim `VERIFIED`**; `suggested_status` distribution: `REVIEW_REQUIRED` 7,713 · `UNSUPPORTED` 217 · `BLOCKED` 70.

| Dimension | Coverage |
|---|---|
| Event families | 22 — PURCHASE 1,723 · SALE 1,328 · PAYMENT 1,193 · RECEIPT 1,167 · EXPENSE 518 · SETTLEMENT 387 · TAX_GST 305 · BAD_DEBT 230 · CREDIT_NOTE 170 · RETURN_OUT 161 · DRAWING 159 · BANK_FEE 106 · REFUND 104 · CAPITAL_CONTRIBUTION 96 · ACCRUAL 65 · DEPRECIATION 60 · RETURN_IN 50 · UNKNOWN 45 · TRANSFER 41 · PAYROLL 37 · ADJUSTMENT_MISC 32 · FX_EVENT 23 |
| Input types | 11 — journal_description 2,614 · bank_narration 1,433 · invoice 1,309 · payment_confirmation 890 · short_transaction_desc 414 · email_or_text_thread 403 · credit_note 324 · email_line 194 · debit_note 161 · bill 136 · api_payload_webhook 122 |
| Difficulty | medium 2,953 · hard 2,712 · easy 2,335 |
| Language style | 16 styles, all ≥ 327 (number-words, OCR-noisy, date-prefix, ack-suffix, clutter, loose punctuation, API-like, short-desc, narration, ₹/rupees/Rs, email-line, reorder-tail, plain, business, abbreviated, journal) |
| Defensive classes | 17 classes from the Phase G defensive taxonomy; abstention share **39.79%** (3,183 rows) |
| Label source | kernel_wording_family 6,511 · specialist_honest_degradation 1,197 · template_wording_family_kernel_gap 292 |
| Splits | train 6,856 / dev 1,144 (deterministic `md5(leakage_group)` bucket, ~85/15; no locked test split drawn from Phase H) |

## 5. Label architecture and honest degradation

Labels come from three provably honest sources, never from heuristic guesses:

1. **kernel_wording_family (6,511)** — wording families whose kernel pre-flight verdicts are `VERIFIED`; classification is by construction the kernel's own ground truth.
2. **specialist_honest_degradation (1,197)** — the structural specialist's fail-closed behavior on genuinely degraded inputs (missing parties, OCR corruption, invention traps). Training the model to abstain like the runtime.
3. **template_wording_family_kernel_gap (292)** — families whose wordings the pre-flight proved the kernel refuses; the metadata `expected_status` records the refusal class while the target still reflects the honest parse.

## 6. The LOAN / INTEREST / DEPOSIT exclusion (stop condition §20.3)

The frozen Phase G ontology marks `LOAN`, interest-on-loan, and refundable-deposit families as `gap=true`, while the live kernel **verifies basic loan postings**. Training either boundary would be improvising: examples labeled against the ontology would teach the model to refuse something the runtime verifies; examples labeled against the implementation would silently expand the declared Phase G boundary. All three families are therefore **excluded entirely** and the conflict is documented here for a future boundary-resolution phase. No kernel change was made to resolve it (that would violate the Phase H mandate).

## 7. Authority boundary — why there are no formula or knowledge rows

The live registry holds 12 `ACCOUNTING_KERNEL`, 30 `FORMULA_AUTHORITY`, and 2 `FINANCE_KNOWLEDGE` capabilities. Phase H rows exercise the kernel's event-level surface (7,989 rows) plus 11 `none` rows; **no formula-input or knowledge-claim examples were fabricated**, because the 18-field IR is a transaction-semantic contract — a formula vector or a knowledge claim cannot be represented as a transaction without inventing a second contract. Quality > count: the boundary is taught by abstention behavior, not by faking representations the contract does not carry.

## 8. Validation pipeline and results

All accepted rows passed (figures in `docs/phase_h/validation_report.json`, derived by `training/phase_h_reports.py` — read-only over the dataset, deterministic output):

- `schema_verifier.validate_structured_interpretation(allow_expanded=True)` — all rows pass
- `ExpandedGroundingGate.safe_for_kernel` (fail-closed) — all rows pass
- enum membership (fyjc_contract vocabularies) — all rows pass
- party sanity (no settlement-word bleed — the Phase 22 defect class)
- status consistency (never `VERIFIED`; `UNSUPPORTED`/`BLOCKED` only by metadata class)
- no-invention (conflict/ambiguity flags required where designed)
- near-duplicate (Jaccard ≥ 0.75) vs 8 prior corpora and within candidates
- event-signature and family-cell caps

**Rejection statistics** (candidates rejected before acceptance): event_signature_cap 5,201 · near_dup_within_candidate 4,119 · party_sanity 714 · family_cell_cap 211 · duplicate_input 266 · near_dup_of_prior_corpus 66 · gate 80 · keyword_substring_mismatch 47. The heavy signature/near-dup rejections are the diversity engine working: language variants of one situation are collapsed via `leakage_group` rather than admitted as separate examples.

**Leakage controls** (`docs/phase_h/leakage_report.json`): exact duplicates vs prior corpora **0**; near duplicates vs prior corpora **0**; locked artifacts touched **none**; status **CLEAN**.

## 9. Frozen-checkpoint verification (this freeze)

| Check | Result |
|---|---|
| Phase F gate (`scripts/fte_authority_phase_f_test.py`) | **44/44 PASS** — knowledge authority verified, boundaries intact |
| Phase C+E gate (`scripts/fte_authority_phase_ce_test.py`) | **194/194 PASS** — registry + formula authority verified (12/30/2 capability matrix unchanged) |
| Batch-1 authority gate (`scripts/fte_authority_expansion_batch1_test.py`) | **59/59 PASS** — expanded kernel capabilities pinned |
| Phase G artifact hashes (8 files) | **8/8 intact** — byte-identical to manifest pins |
| Dataset integrity | 8,000 rows; sha256 `5c56b373537c982d…` matches manifest and validation report |
| 18-field contract re-measure | 8,000/8,000 exact field-set match; 0 `VERIFIED` claims |
| `docs/phase_h/` determinism | regenerated via `python3 training/phase_h_reports.py` — **byte-identical** output |

No authority, kernel, formula, knowledge, schema, grounding, or API code was modified. The 15J/15H pre-existing failures noted at Phase A–F freeze remain byte-identical and are outside Phase H scope.

## 10. Files in this checkpoint

| File | Role |
|---|---|
| `training/phase_h_generate.py` | deterministic generator (pre-flight → style → parse → gates) |
| `training/phase_h_reports.py` | derives all `docs/phase_h/` artifacts from manifest + dataset |
| `training_data/phase_h_v01_8000.jsonl` | the dataset (8,000 rows) |
| `training_data/phase_h_v01_manifest.json` | canonical machine contract (hashes, distributions, pre-flight verdicts) |
| `docs/phase_h/coverage.json` | event family × authority × status |
| `docs/phase_h/distribution.json` | all metadata dimensions |
| `docs/phase_h/authority_coverage.json` | per-authority registry vs dataset coverage |
| `docs/phase_h/defensive_coverage.json` | defensive taxonomy × status |
| `docs/phase_h/validation_report.json` | validation pipeline + rejection statistics |
| `docs/phase_h/leakage_report.json` | leakage controls + prior-corpus hashes |
| `PLATRIXA_PHASE_H_REPORT.md` | this report |

## 11. What Phase H deliberately does NOT do

- No Phase I+ implementation; no model training run; no dataset expansion beyond 8,000
- No change to the exact 18-field contract, kernel, Formula Authority, Finance Knowledge Authority, schema verifier, grounding gate, or API surface
- No locked evaluation artifact touched (test corpus frozen)
- No resolution of the LOAN/INTEREST/DEPOSIT Phase G ↔ implementation conflict (documented in §6 for a future phase)
- No conversion of UNVERIFIED/unsupported behavior into supported claims — abstention is the product

## 12. Next steps (for the next phase to decide)

1. Resolve the LOAN/INTEREST/DEPOSIT boundary conflict (§6) before any further kernel-facing dataset work.
2. Train against `phase_h_v01` (train 6,856 / dev 1,144) using the existing SFT jobs; evaluation must measure abstention precision, not just extraction accuracy.
3. Only after boundary resolution: consider a v0.2 dataset exercising the resolved families and a measured formula/knowledge surface if the contract grows a legitimate representation for them.
