# Phase H v0.1 — Frozen DEV Evaluation (COMPLETE, NOT PROMOTED)

**Status: EVALUATION COMPLETE — Phase H is NOT promoted. Do NOT retrain.**

This is the durable record of the complete frozen Phase H DEV evaluation over all
**1,144 DEV examples**, executed in the Colab Tesla T4 environment. Machine-readable
companion: `reports/phase_h/phase_h_v01_dev_evaluation.json`.

---

## Executive summary

Phase H is a **broader financial-semantic LoRA** — explicitly *not* a FYJC-only model.
The frozen DEV evaluation is complete (1144/1144) and the numbers are recorded here in
full. The model is **not** promoted, and the single most important result is that
**grounding scores only 62.67%** — the lowest quality metric by a wide margin, and a
fail-closed gate in the production path.

| | |
|---|---|
| Evaluation | **COMPLETE** (1144/1144) |
| Promotion | **NOT PROMOTED** |
| Retrain | **DO NOT RETRAIN** |
| Blocking concern | **Grounding 62.67%** |
| Must-investigate | **1 accounting-leakage case** |
| Next step | Grounding + leakage failure analysis, then Phase 24 breadth/generalization eval |

---

## Training artifact

| Item | Value | Verified in-repo |
|---|---|---|
| Kind | Broader financial-semantic LoRA (**not** FYJC-only) | — |
| Base | `Qwen/Qwen2.5-1.5B-Instruct` | ✅ `phase_h_sft_job.py:93` |
| Base revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` | ✅ `phase_h_sft_job.py:94` |
| Adapter (training env) | `/content/drive/MyDrive/platrixa/phase_h_output` | Colab Drive mount path |
| Dataset | `phase_h_v01_8000` | ✅ |
| Dataset SHA256 | `5c56b373537c982d3784452a67f01d173fa390e093978e46df4788e3048d1f29` | ✅ exact match |
| Rows | 8,000 | ✅ exact match |
| Train / dev split | 6,856 / 1,144 | ✅ `TRAIN_COUNT_EXPECTED` / `VALID_COUNT_EXPECTED` |

### Hyperparameters

| Parameter | Value | Verified in-repo |
|---|---|---|
| Epochs | 3 | ✅ `EPOCHS = 3` |
| LoRA r | 16 | ✅ `LORA_R = 16` |
| LoRA alpha | 32 | ✅ `LORA_ALPHA = 32` |
| Per-device batch | 2 | ✅ `PER_DEVICE_BATCH = 2` |
| Grad accumulation | 4 | ✅ `GRAD_ACCUM = 4` |
| **Effective batch size** | **8** | ✅ `2 × 4 = 8` |
| Precision | fp16 | ✅ `FP16 = True` (T4 has no bf16) |
| Final train loss | 0.00531427 | — |
| Final eval loss | 0.00065822 | — |

---

## Evaluation methodology

| Parameter | Value |
|---|---|
| Dataset SHA256 | `5c56b373…04d1f29` (**matches the training dataset exactly**) |
| Frozen DEV examples | 1,144 |
| Batch size | 16 |
| `MAX_NEW_TOKENS` | 394 |
| GPU | Tesla T4 |
| Base revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Adapter | Phase H local adapter |
| Completed | **1144 / 1144** |
| Runtime | ~37m05s |

The evaluation dataset digest is identical to the training dataset digest, and the
1,144 evaluated examples match the DEV split size declared by the training job. The
evaluation is therefore a genuine held-out split of the same corpus, not a re-run of
training rows.

---

## Results

### Quality

| Metric | Score |
|---|---|
| JSON validity | **98.25%** |
| Exact 18-field schema | **87.24%** |
| Transaction type | 80.86% |
| Parties | 92.31% |
| Amounts | 88.90% |
| Payment method | 90.03% |
| **Grounding** | **62.67%** |
| Suggested status | 90.38% |
| Leakage-free | 99.91% |
| **Accounting leakage count** | **1** |

### Performance

| Metric | Value |
|---|---|
| Mean latency | 1.9448 s/example |
| P50 | 1.9277 s |
| P95 | 1.9807 s |
| Throughput | 0.5142 examples/s |

---

## Interpretation

**The evaluation is complete. Phase H is not promoted.**

### The three concerns

**1. Grounding is only 62.67% — the blocking concern.**
This is the lowest quality metric by a wide margin: 36.33 points below the next-worst
metric (transaction type, 80.86%). Grounding is a fail-closed gate in the production
path, so this number, not the aggregate, governs any promotion decision. It must be
understood before Phase H is considered for anything.

**2. Exact 18-field output is 87.24%, with JSON validity at 98.25%.**
The ~11-point gap between "parses as JSON" and "carries exactly the 18 contract fields"
means roughly **1 in 8** otherwise-valid outputs is missing fields or carries extra
ones. The production schema verifier would reject these. The 1.75% that are not even
valid JSON is a smaller, separate problem.

**3. Exactly 1 accounting-leakage case must be investigated.**
Leakage-free is otherwise 99.91% (1143/1144). A single leak is still a real occurrence
that must be understood — it is a required investigation item, not a rounding error.
Platrixa's financial-truth guarantees are never traded for availability.

### What is acceptable for now

- **Performance is usable** at ~1.94 s/example on a T4 (P95 1.98 s, throughput 0.5142
  ex/s). Runtime is not the constraint.
- **Parties (92.31%), amounts (88.90%) and payment method (90.03%)** are comparatively
  strong.

### Explicit non-conclusions

- **Do NOT retrain yet.**
- **Do NOT treat the old Phase 6C / FYJC-focused Hugging Face Space as the Phase H
  production artifact.** It is a different, earlier, FYJC-focused model.
- **Do NOT interpret this as evidence that Platrixa is FYJC-only.** Phase H was
  explicitly trained broader than FYJC.
- **Do NOT read the 0.00065822 final eval loss as a quality signal.** The 62.67%
  grounding result directly contradicts any inference that the model is
  production-ready. The very low eval loss reflects fit on a distributionally narrow
  split, not production readiness.

### Next step

Failure analysis of the grounding failures and the single accounting-leakage case,
followed by the Phase 24 breadth/generalization evaluation.

---

## Artifacts

Produced in the evaluation environment (Colab):

- `phase_h_dev_predictions.jsonl` — 1,144 predictions
- `phase_h_dev_final_report.json`
- `phase_h_dev_checkpoint.json`
- `phase_h_v01_dev_evaluate.py`
- `phase_h_v01_dev_evaluate_resumable.py`

**These are not in this repository.** They must be retrieved before the grounding
failure analysis and the leakage investigation can proceed — that work is
prediction-level and cannot be done from this summary report.

### Deliberately excluded from this report

`training_data/eval_20260828_092036.jsonl` was **independently verified to be a
zero-byte file** (SHA256 `e3b0c442…852b855`, the digest of empty input; 0 records). It
is **not** the 1,144-example Phase H DEV evaluation and was not used as evidence. It is
left untouched.

---

## Canonical 18-field contract (unchanged by Phase H)

18 fields, in canonical repository order (`backend.maths.fyjc_contract.ALL_VALID_FIELDS`,
verified to contain exactly 18 entries):

`parties` · `ambiguity_flags` · `overall_confidence` · `referenced_transaction_index` ·
`referenced_amount` · `suggested_status` · `safety_flags` · `references` ·
`field_confidences` · `payment_method_enum` · `transaction_type_enum` ·
`referenced_party` · `transaction_type` · `amounts` · `ambiguities` · `grounding` ·
`payment_method` · `scope_flags`

---

## Provenance

**Metric source:** operator-reported results from the Colab GPU evaluation environment.
The evaluation was **not** re-executed or independently recomputed in this repository.
This report preserves the run's methodology, exact figures, artifact references, and
interpretation; it is a record, not a reproduction.

**Independently verified in this repository:**

- `training_data/phase_h_v01_8000.jsonl` — SHA256 and 8,000-row count match exactly
- `training_data/phase_h_v01_manifest.json` — declares the same dataset digest
- `training/phase_h_sft_job.py` — base model, base revision, epochs, LoRA r/alpha,
  batch/accumulation, fp16, and the 6,856 / 1,144 split
- `backend/maths/fyjc_contract.ALL_VALID_FIELDS` — exactly 18 entries
- `training_data/eval_20260828_092036.jsonl` — zero bytes, not this evaluation

**Not verifiable in this repository:** the loss values, all quality metrics, the
latency/throughput figures, and the existence and content of the 1,144 predictions and
the evaluator scripts.
