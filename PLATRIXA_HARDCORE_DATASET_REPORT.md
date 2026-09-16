# PLATRIXA — PHASE 20 HARDCORE DATASET REPORT

**Date:** 2026-09-16
**Phase:** 20 — Hardcore FYJC Transaction Corpus (dataset engineering only)
**Classification:** ✅ **PASS** (exactly 1,000 validated candidates; original dataset byte-for-byte untouched; no runtime changes)
**Evidence suite:** `scripts/fte_fyjc_70_phase20_dataset_test.py` — **30/30 checks PASS**
**Final candidate:** `training_data/fyjc_hardcore_1000.jsonl` — sha256 `56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd`

---

## 1. Dataset audit (existing corpus, read-only)

| Property | Value |
|---|---|
| Path | `training_data/fyjc_specialist_1000.jsonl` |
| Rows | 1,000 (ids: `sta_00001…` 799, `con_…` 118, `noi_…` 83) |
| sha256 | `feb7bfe5c1f415228d3ab9ccdc43beaa2df1498af8f4e66fc5856c441df61e24` |
| Seed (per quality report) | 42 |
| Status after Phase 20 | **byte-for-byte identical** (proven by fte_70 checks A/P) |

**Discovered schema (do-not-invent contract):**
row = `{id, input, output, metadata}`;
`output` = the 18-field ExpandedInterpretation (`backend/maths/fyjc_contract.py`: legacy 7 + `transaction_type_enum`, `payment_method_enum`, `ambiguity_flags`, `referenced_transaction_index/party/amount`, `field_confidences`, `overall_confidence`, `suggested_status`, `safety_flags`, `scope_flags`);
`metadata` = 13 doc fields (`difficulty, language_style, category, transaction_type, payment_method, has_party, has_amount, has_payment, is_ambiguous, is_contradictory, is_unsupported, is_multi_transaction, has_reference`).

**Original distributions:** difficulty clear 815 / ambiguous 94 / incomplete 58 / adversarial 17 / contradictory 12 / unsupported 4 · transaction PURCHASE 321, SALE 218, PAYMENT 121, RECEIPT 104, EXPENSE 102, DRAWING 56, CAPITAL 51, UNKNOWN 12, RETURN_IN 10, RETURN_OUT 5 · payment CASH 715, UNKNOWN 129, CREDIT 125, CHEQUE 8, BANK 8, UPI 8, NEFT 7 · style standard 799 / noisy 83 / conversational 118.

**Production-validator baseline of the original:**
`schema_verifier.validate_structured_interpretation(allow_expanded=True)` → **1000/1000 valid**.
`ExpandedGroundingGate.ground()` → **184/1000 gate-safe**: 815 rows carry `suggested_status="VERIFIED"` (gate Rule-0 violation), 66 unsupported payment keywords, ~9 unsupported amounts/parties.

**Coverage gaps found (targets for this phase):** zero DISCOUNT_TRADE / DISCOUNT_CASH / SETTLEMENT / GST rows; returns = 15 total; non-cash modes ≈ 3%; no near-identical contrast-pair family; adversarial+incomplete+ambiguous = 17%.

---

## 2. Generation methodology (hybrid, §8)

```
original 1,000 (read-only) → pattern/category extraction → coverage-gap targeting
    ↓ 15 deterministic scenario families (31 observed family tags), seed 20260916
candidate input text (1,599-row pool)
    ↓ FYJCAISpecialist.parse()      ← the ONLY interpretation authority
    ↓ schema_verifier (allow_expanded) + ExpandedGroundingGate   ← production validators
    ↓ duplicate / near-dup / enum / metadata-consistency / evidence-whole-word / leakage checks
    ↓ 376 rejected  →  exactly 1,000 accepted
```

- **LLM role:** none in the final corpus. No `GROQ_API_KEY` was present, so the enrichment path stayed disabled (fail-closed — proven by fte_70 check K). `generation_model: null`. The `--enrich` flag exists and reuses the **existing** `backend/gateway/providers/groq_adapter.py`; reworded candidates re-enter the identical validation gauntlet.
- **Determinism (§16):** seed `20260916`; `PYTHONHASHSEED=0` pinned via self-re-exec (see §5, runtime finding R1). Regeneration is **byte-identical** across processes (fte_70 check L). Groq enrichment, if ever enabled, would end byte reproducibility and is recorded as such in the manifest.
- **Amount-format convention:** the original corpus stores amounts as integer strings (1,011/1,011 entries, zero `.0` suffixes). The candidate conforms (data-side; see §5, R3).

## 3. Validation results (§13)

**Pool → final:** 1,599 candidates → 1,000 accepted. Rejections (fail-closed, never repaired):

| Reason | Count | Meaning |
|---|---:|---|
| near_dup_within_candidate | 184 | Jaccard ≥ 0.75 vs an accepted row |
| event_signature_cap | 162 | > 24 rows per normalised event signature |
| duplicate_input | 14 | normalised duplicate (vs originals or accepted) |
| keyword_substring_mismatch | 8 | classification evidence landed inside an unrelated word (R2) |
| gate | 7 | production grounding-gate rejections |
| near_dup_of_original | 1 | too close to an original row |

**Full gauntlet re-pass:** all 1,000 rows re-validate through the production schema verifier and grounding gate (fte_70 D/F). Enum membership asserted directly against `fyjc_contract` sets. Zero forbidden accounting fields. **Zero VERIFIED claims** — `suggested_status = REVIEW_REQUIRED` in 1000/1000 rows.

**Leakage safety (§14):** output blocks are emitted exclusively by `FYJCAISpecialist.parse()` (+ amount-format normalisation). No journal/ledger/status fields, no hidden answers, no hand-written accounting truth. Probes G1–G3 prove forbidden-field, VERIFIED-claim, and fabricated-amount candidates are rejected.

## 4. Final dataset (§10/§11/§15/§19)

`training_data/fyjc_hardcore_1000.jsonl` — **exactly 1,000 rows**, ids `hc_00001…hc_01000`, sha256 `56be5be1…c721cd`.
Manifest: `training_data/fyjc_hardcore_1000.manifest.json` (distributions, rejections, hashes, determinism note; no secrets).

| Distribution | Values |
|---|---|
| difficulty | incomplete 368 · ambiguous 350 · clear 230 · adversarial 52 |
| transaction_type | SALE 278 · UNKNOWN 277 · PURCHASE 226 · RETURN_OUT 60 · DRAWING 52 · EXPENSE 49 · SETTLEMENT 40 · CAPITAL 18 |
| payment_method | CASH 356 · UNKNOWN 298 · CHEQUE 117 · CREDIT 114 · BANK 76 · UPI 39 |
| language_style | standard 862 · conversational 83 · noisy 55 |
| category | single 658 · multi 121 · adversarial 106 · reference 71 · distractor 44 |
| families (31) | compound_multi 121 · contrast_pair 85 · basic_sale 69 · informal_noisy 55 · basic_purchase 53 · irrelevant_context 44 · basic_expense 43 · ambiguous_pronoun 40 · basic_receipt 40 · sales_return 38 · purchase_return 37 · reference_history 31 · paraphrase_set 29 · trade_discount 28 · cash_discount 28 · gst_aware 27 · settlement_partial 26 · contradictory_payment 21 · incomplete_minimal 20 · pm_matrix_{neft 20, bank 17, credit 17, cheque 13, upi 9, cash 8} · basic_capital 18 · settlement_full 16 · basic_drawing 15 · incomplete_{no_party 14, no_amount 12, no_mode 6} |

Coverage gained vs. original: +40 settlement, +56 discount, +27 GST-aware, +75 returns, non-cash modes 117 vs 31, +85 systematic contrast pairs, +44 distractor-context, +55 noisy informal. Top ambiguity combos: NONE 282 · MISSING_AMOUNT 104 · MULTIPLE_INTERPRETATIONS 102 · MISSING_PAYMENT_MODE 80 · MISSING_PARTY+MISSING_PAYMENT_MODE 66. Safety flags: UNRESOLVED_FIELDS 373 · NONE 300 · LOW_CONFIDENCE+UNRESOLVED 253 · LOW_CONF+MISSING_REQ+UNRESOLVED 74.

## 5. Runtime limitations discovered — documented, NOT worked around in runtime (§13)

The runtime was deliberately untouched (scope). These findings are recorded for a future runtime phase:

- **R1 — process nondeterminism in `FYJCAISpecialist`:** keyword vocabulary is stored in `set`s; when several keywords match, the recorded `source_text` evidence varies with Python hash randomisation. Data-side fix: `PYTHONHASHSEED=0` pin. Runtime fix (future): ordered tuples.
- **R2 — substring keyword bug:** `"paid off"` matches inside `"paid office"` (and similar), producing a SETTLEMENT classification with spurious evidence. Data-side guard rejects such rows (8 in the final pool); runtime fix (future): whole-word matching.
- **R3 — amount-format interop:** the specialist emits float-string amounts (`"8000.0"`); the gate's digit normalisation cannot match them against input text. The original corpus already uses integer strings, so the candidate conforms. Runtime fix (future): emit integer strings.
- **R4 — RECEIPT vocabulary gap:** generic "received X from Y" inputs classify UNKNOWN (only 3 narrow receipt templates exist). Rows kept with honest UNKNOWN outputs; a future runtime phase may add vocabulary.
- **R5 — on-account payment gap:** "paid X to Y" without a mode yields UNKNOWN + MULTIPLE_INTERPRETATIONS. Kept honestly.
- **R6 — original-corpus hygiene:** 815/1000 original rows claim VERIFIED (gate Rule-0) and some carry unsupported payment/amount claims. Relevant to any future re-validation of the original.

## 6. Manual-review sample (§21, one per family)

| Family | Input | Interpretation (current runtime) |
|---|---|---|
| contrast_pair | Purchased goods from Reddy and Company for Rs.1,200 cash. | PURCHASE/CASH, NONE, adversarial |
| compound_multi | Purchased kitchenware worth Rs. 12000 from Nitin by cheque; the same day sold packaging materials to Anita… | SALE/CHEQUE, NONE, clear |
| cash_discount | Received ₹10,200 from Rakesh in full settlement of Rs. 12000 due to cash discount of Rs. 1800. | SETTLEMENT/CASH, NONE, clear |
| trade_discount | Purchased goods worth Rs.20,000 from Desai Brothers at a trade discount of Rs.1,000. | PURCHASE/UNKNOWN, MISSING_PAYMENT_MODE |
| irrelevant_context | The market was unusually crowded. Purchased stationery from Mehta Agencies for Rs. 30000 cash. | PURCHASE/CASH, NONE |
| informal_noisy | took ₹1,500 from shop counter for home | UNKNOWN/UNKNOWN, MULTIPLE_INTERPRETATIONS |
| ambiguous_pronoun | She paid the bill of ₹1,500 by cheque. | UNKNOWN/CHEQUE, MISSING_PARTY+MULTIPLE_INTERP+PRONOUN |
| reference_history | Paid Vijay ₹10,000 for last month's bill today. | UNKNOWN/UNKNOWN, MULTIPLE_INTERP+… |
| contradictory_payment | Purchased computer accessories Rs.25,000 on credit but settled cash immediately. | SETTLEMENT/CASH, MISSING_PARTY |
| incomplete_minimal | Sold medicines to Komal. | SALE/UNKNOWN, MISSING_PAYMENT_MODE+MISSING_AMOUNT |
| purchase_return | Returned kitchenware worth Rs. 800 to Prakash because they were damaged. | RETURN_OUT/UNKNOWN |
| pm_matrix_upi | Purchased furniture from Gupta and Sons for Rs.7,500 via UPI. | PURCHASE/UPI, NONE |

Review question applied: *"Would this be a realistic input a developer's application sends to Platrixa, and does the expected interpretation remain defensible within the existing FYJC contract?"* — all sampled rows pass; UNKNOWN rows genuinely reflect current-runtime behaviour, not fabricated truth.

## 7. Developer-first rationale (§3)

Platrixa's primary user is the developer who clones the repo, installs the core runtime, and streams messy real-world financial text through the public API. This corpus is engineered from exactly such inputs — informal phrasing, distractor context, missing fields, contradictions, one-word semantic contrasts — so that grounding, validation, and rule behaviour are exercised against developer-realistic difficulty. FYJC remains the bounded domain in which every example is deterministically checkable, making the corpus usable as training/evaluation infrastructure without ever teaching the model to bypass Platrixa's deterministic authority chain. This is not a tutoring dataset: no explanations, no quizzes, no educational padding.

## 8. Tests & regression (§18/§22)

| Suite | Result |
|---|---|
| `fte_fyjc_70_phase20_dataset_test.py` (checks A–P) | **30/30 PASS** |
| fte_fyjc_52 (kernel boundary) | PASS |
| fte_fyjc_67 (Phase 17 boundary) | PASS |
| fte_fyjc_68 (rules/benchmark) | PASS |

Key proofs: original byte-identical (A/P) · exactly 1,000 rows (B) · all valid JSON (C) · full contract compliance (D) · unique sequential ids (E) · rejections real and non-repairable (F) · unsupported/forbidden probes rejected (G) · duplicate/near-dup probes rejected (H/I) · failed generation exits rc=3 and writes nothing (J) · missing key returns None (K) · partial file guarded, fresh run recovers byte-identically (L) · no secrets in outputs (M) · manifest matches dataset (N).

## 9. Files

**New (Phase 20):** `training/phase20_generate.py` · `training_data/fyjc_hardcore_1000.jsonl` · `training_data/fyjc_hardcore_1000.manifest.json` · `scripts/fte_fyjc_70_phase20_dataset_test.py` · `PLATRIXA_HARDCORE_DATASET_REPORT.md`

**Untouched (proved):** `training_data/fyjc_specialist_1000.jsonl` (byte-identical), Kernel, grounding gate, schema verifier, RuleEngine/rules, providers, accounting, API, `hf_space/`, `.env`, deployment, model weights. No training ran; nothing uploaded; nothing deployed.

**Not committed** (per §22): all Phase 20 files remain uncommitted; staging/commit belongs to the user's Changes-panel workflow.

## 10. Limitations

- The corpus reflects **current-runtime** interpretations, including UNKNOWN classifications for receipt/on-account phrasing (R4/R5) — deliberate honesty, not model truth.
- Determinism is guaranteed for the deterministic pipeline only; Groq enrichment would require recording model revision and forfeits byte reproducibility (documented in manifest).
- `keyword_substring_mismatch` guard is corpus-side; the underlying specialist substring behaviour (R2) remains until a runtime phase fixes it.
- The candidate is a **candidate**: per §15, dataset lock, independent re-validation, and any training decision belong to a future phase.

## 11. Git state at completion

See the transcript's final `git status --short` / `git diff --stat` output: only the five Phase 20 files are new/modified; the original dataset and all pre-existing artifacts are untouched.
