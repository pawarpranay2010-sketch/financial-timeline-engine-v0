# Phase 6C — Base vs Fine-Tuned Evaluation Report

**Date:** 2026-09-04  
**Status:** ⚠️ BLOCKED BY SANDBOX RAM — Script verified, inference requires ≥4 GB RAM

---

## Configuration

| Item | Value |
|------|-------|
| **Base model** | `Qwen/Qwen2.5-1.5B-Instruct` |
| **Base revision** | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| **Adapter** | `Pranay-20/platrixa-fyjc-specialist-v0.1` |
| **Adapter revision** | `b5c0a37cebc00e93144150dbbcaa7b28cadb259e` |
| **Test set** | `training_data/fyjc_specialist_test.jsonl` — 100 examples |
| **Test SHA-256** | `c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0` |
| **Decoding** | temperature=0.0, top_p=1.0, max_new_tokens=1024 |
| **System prompt** | Same as training (`SYSTEM_INSTRUCTION` from `training/format.py`) |

---

## What Was Verified (Pre-Inference)

| Check | Result |
|-------|--------|
| Test set count | ✅ Exactly 100 records |
| Test set SHA-256 matches Phase 5 manifest | ✅ `c124372...` matches `phase6_manifest.json` |
| All outputs are dicts with 18 fields | ✅ Verified |
| Base model accessible (not gated) | ✅ Downloaded (2.89 GB) |
| Adapter accessible at pinned revision | ✅ `adapter_model.safetensors` (70.5 MB) |
| Adapter has correct `base_model_name_or_path` | ✅ Points to `Qwen/Qwen2.5-1.5B-Instruct` |
| Evaluation script compiles | ✅ `py_compile` passes |
| Check-only mode passes | ✅ All 100 records valid, adapter accessible |
| Test set protection verified | ✅ Read-only access, SHA-256 before/after check built in |

---

## Blocker: Sandbox RAM

| Resource | Available | Required |
|----------|----------|----------|
| **RAM** | 2.9 GB | ≥3 GB (float16), ≥6 GB (float32) |
| **Disk** | 3.1 GB (after cleanup) | 2.89 GB (model) + 70 MB (adapter) ✅ |
| **GPU** | None (CPU only) | N/A (CPU inference planned) |

The 1.5B parameter model in float16 needs ~3 GB minimum for weights alone, plus tokenizer overhead, Python runtime, and inference activations. The sandbox's 2.9 GB total RAM causes the OOM killer to terminate the process during model loading.

**Three loading attempts tried:**
1. `dtype=torch.float32, device_map=None` — OOM killed at 39% weights loaded
2. `dtype=torch.float16, device_map=None` — OOM killed at 39% weights loaded  
3. `dtype=torch.float16, low_cpu_mem_usage=True` — OOM killed at 37% weights loaded

---

## How to Complete Phase 6C

The evaluation script (`training/phase6c_evaluate.py`) is fully implemented and verified. Run it on any machine with ≥4 GB RAM (or a GPU):

### Option 1: Any machine with ≥4 GB RAM + internet
```bash
pip install torch transformers peft huggingface_hub
export HF_TOKEN=hf_YOUR_TOKEN_HERE
python training/phase6c_evaluate.py
```

### Option 2: Kaggle/Colab (free GPU)
```python
!pip install transformers peft huggingface_hub
import os; os.environ['HF_TOKEN'] = 'hf_YOUR_TOKEN_HERE'
!python training/phase6c_evaluate.py
```

### Option 3: Modal T4 (Phase 6B route)
The existing `training/run_modal.py` can be adapted to run evaluation by passing `--skip-inference` after base predictions are saved, or by modifying the Modal function to run `phase6c_evaluate.py`.

### What the script does:
1. Verifies test set SHA-256 (integrity gate)
2. Loads base Qwen on CPU, runs 100 examples (temperature=0, deterministic)
3. Saves base predictions → `training_data/phase6c_base_predictions.jsonl`
4. Loads fine-tuned Qwen + adapter, runs same 100 examples
5. Saves fine-tuned predictions → `training_data/phase6c_finetuned_predictions.jsonl`
6. Computes all metrics (schema, semantic, grounding, leakage, hallucination)
7. Generates comparison report → `training/PHASE6C_EVALUATION_REPORT.md`
8. Saves machine-readable results → `training_data/phase6c_evaluation_results.json`
9. Verifies test set integrity post-evaluation (SHA-256 must match)

### Estimated runtime:
- CPU: ~50-100 minutes per model (100 examples × 30-60s each)
- GPU (T4): ~5-10 minutes per model

---

## Metrics Computed (When Run)

The script calculates all required Phase 6C metrics:

### A. Output Safety / Contract
1. Valid JSON rate
2. Valid 18-field schema rate  
3. Unknown-field rate
4. Forbidden-field rate
5. Accounting leakage rate (conservative classifier)
6. suggested_status distribution

### B. Semantic Understanding
7. Transaction-type accuracy
8. Party exact-set accuracy + token F1 + precision + recall
9. Amount extraction accuracy (numeric multiset)
10. Payment-method accuracy
11. Ambiguity detection agreement
12. suggested_status agreement
13. Full semantic exact-match rate

### C. Grounding
14. Grounding dict presence rate
15. field_confidences presence rate
16. Valid suggested_status rate

### D. Hallucination Audit
17. Invented parties/amounts/references/payment methods/currencies
18. Unsupported certainty claims
19. Failed ambiguity preservation

### E. Accounting Leakage Audit (Conservative)
20. Forbidden key detection (journal, debit_lines, etc.)
21. Generated conclusion detection (vs. legitimate input echo)
22. Per-case classification: true_leakage / input_echo / clean

### F. Difficulty Breakdown
23. By difficulty: clear, ambiguous, incomplete, contradictory, adversarial
24. By category: single, reference, distractor, adversarial, multi
25. By language style: standard, noisy, conversational

---

## Final Verdict (Pending Execution)

| Possible Verdict | Condition |
|-----------------|-----------|
| **IMPROVED** | Semantic metrics improve + no safety regression |
| **PASS** | Marginal improvement, no regression |
| **NO_SIGNIFICANT_CHANGE** | Differences too small |
| **REGRESSED** | Semantic metrics decrease materially |
| **UNSAFE_REGRESSION** | Improvement but accounting leakage increases |

**Cannot determine verdict until inference runs on a ≥4 GB RAM machine.**

---

## Test Set Protection

| Check | Result |
|-------|--------|
| SHA-256 before evaluation | `c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0` |
| Record count before | 100 |
| Test set modified during evaluation | **NO** (script enforces SHA-256 after check) |
| Test set uploaded | **NO** |
| Test set used for training | **NO** |
| Test set used for prompt tuning | **NO** |

---

## Files

| File | Status |
|------|--------|
| `training/phase6c_evaluate.py` | **CREATED** — evaluation script |
| `training/PHASE6C_EVALUATION_REPORT.md` | **CREATED** — this report |
| `training_data/fyjc_specialist_test.jsonl` | **UNTOUCHED** |
| `backend/maths/*` | **UNTOUCHED** |
| `training/trl_sft_job.py` | **UNTOUCHED** |

---

## Git

- **Committed:** NO
- **Pushed:** NO
- **Training started:** NO
- **Paid compute used:** NO
- **Test set uploaded:** NO

---

Generated by `training/phase6c_evaluate.py`
