# Phase 6C — Google Colab Execution Guide
## Base Qwen2.5-1.5B vs LoRA Fine-Tuned Qwen — Controlled Benchmark

This is the **execution manual** for the Phase 6C benchmark prepared by
`training/phase6c_evaluate.py`. The actual 100-example evaluation is run
**manually in Google Colab** (free T4 GPU). This document contains every step.

**The locked test set (`training_data/fyjc_specialist_test.jsonl`, SHA-256
`c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0`) must never
be modified, uploaded anywhere new, or used for anything other than this
evaluation's read-only scoring.**

---

## Pinned artifacts

| Item | Value |
|------|-------|
| Base model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Base revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Adapter repo | `Pranay-20/platrixa-fyjc-specialist-v0.1` (private) |
| Adapter revision | `b5c0a37cebc00e93144150dbbcaa7b28cadb259e` |
| Test set | `training_data/fyjc_specialist_test.jsonl` — 100 examples |
| Decoding | `do_sample=False` (deterministic greedy), `max_new_tokens=512`, identical for Base and Fine-Tuned |

Why `max_new_tokens=512`: the longest 18-field target in train+valid
(900 examples, test set excluded from sizing) tokenizes to 367 tokens with the
pinned Qwen tokenizer (p99 = 364). 512 leaves ~1.4× headroom and cuts ~50% of
the wasted generation the previous 1024-token cap produced.

---

## Step-by-step cells

### Cell 1 — Check GPU

```python
!nvidia-smi
```

Runtime → Change runtime type → **T4 GPU**. If no GPU is attached, stop and
reconnect with a GPU runtime.

### Cell 2 — Clone the repository

```python
from getpass import getpass
import os

# A fine-grained GitHub token with repo read access (NOT your HF token).
# Paste at the prompt — it is never printed or stored in a cell.
GH_TOKEN = getpass("GitHub token (read access to financial-timeline-engine-v0): ")
os.environ["GH_TOKEN"] = GH_TOKEN

!git clone https://x-access-token:$GH_TOKEN@github.com/pawarpranay2010-sketch/financial-timeline-engine-v0.git
%cd financial-timeline-engine-v0
```

### Cell 3 — Install exact dependencies (pinned to the verified stack)

```python
# Pinned to the versions Phase 6B trained with (training/phase6b_manifest.json)
!pip install -q "torch==2.6.0" "transformers==4.49.0" "peft==0.15.2" "accelerate==1.14.0" "huggingface_hub==0.30.1"

import torch, transformers, peft
print("torch:", torch.__version__)
print("transformers:", transformers.__version__)
print("peft:", peft.__version__)
print("cuda available:", torch.cuda.is_available())
assert torch.cuda.is_available(), "A CUDA runtime is required for the benchmark."
```

> These pins match the Phase 6B training container so the adapter loads with
> exactly the library versions it was produced with. If a pin is unavailable
> for the Colab runtime, keep transformers/peft aligned with Phase 6B and do
> NOT mix major versions.

### Cell 4 — Authenticate to Hugging Face safely

```python
from google.colab import userdata  # Colab Secrets — NEVER hardcode the token

# Add HF_TOKEN in Colab: Secrets (🔑) → "HF_TOKEN" → enable notebook access.
from huggingface_hub import login
login(token=userdata.get("HF_TOKEN"))
```

Add the secret in Colab UI first: click the **🔑 Secrets** icon in the left
sidebar → *Add a new secret* → name `HF_TOKEN` → value = your Hugging Face
token (read scope) → toggle *Notebook access* ON. The token is never printed,
never written to a file, never committed.

### Cell 5 — Verify test-set integrity + configuration (check-only mode)

```python
!python training/phase6c_evaluate.py --check-only
```

This verifies: 100 records, locked SHA-256, 18-field schema on every target,
no duplicate ids, no forbidden accounting keys in targets, pinned model and
adapter revisions resolvable on the Hub, dependency versions, and output
paths — **without loading any model weights**. It must print
`STATUS: PASS` before continuing. If it fails, STOP — do not run the benchmark.

### Cell 6 — Run Base evaluation (System A)

```python
!python training/phase6c_evaluate.py --base-only
```

Loads the pinned base Qwen model and evaluates all 100 examples with
deterministic greedy decoding. Progress prints every batch. Predictions are
saved to `training_data/phase6c_base_predictions.jsonl`.

### Cell 7 — Run Fine-Tuned (Base + LoRA) evaluation (System B)

```python
!python training/phase6c_evaluate.py
```

This is the full run: it downloads the adapter at the pinned revision, then
evaluates the SAME 100 test records with the SAME prompts and decoding. It
recomputes both metric sets, generates `training/PHASE6C_FINAL_REPORT.md` and
`training_data/phase6c_results.json`, and re-verifies the test-set SHA-256.

> Note: this command re-runs the base pass as well (needed because the base
> predictions from Cell 6 must exist at `training_data/phase6c_base_predictions.jsonl`
> for the final comparison; the script detects the cached file only with
> `--skip-inference`). If you want to skip re-running the base pass after
> Cell 6, verify `training_data/phase6c_base_predictions.jsonl` exists and add
> `--skip-inference` — but the default full run is the safest reproducible path.

### Cell 8 — Display the final report

```python
from pathlib import Path
print(Path("training/PHASE6C_FINAL_REPORT.md").read_text())
```

### Cell 9 — Save artifacts locally

```python
from google.colab import files

files.download("training_data/phase6c_results.json")
files.download("training/PHASE6C_FINAL_REPORT.md")
# Optional raw predictions for audit:
# files.download("training_data/phase6c_base_predictions.jsonl")
# files.download("training_data/phase6c_finetuned_predictions.jsonl")
```

Download these artifacts back into the repository working tree before
committing results. **Do not commit the locked test set anywhere, and do not
upload predictions to Hugging Face.**

---

## Rules enforced by the script

- Test set: read-only. SHA-256 is verified before and after; the run aborts on
  any mismatch or record-count change.
- Pinned revisions: the script fails closed if the adapter revision cannot be
  resolved — no "latest" fallback.
- Identical configuration: same chat template, same prompts, same
  `max_new_tokens`, same decoding, same batch size, same JSON extraction, and
  the same metric implementation for Base and Fine-Tuned.
- No secrets printed: the script reads `HF_TOKEN` from the environment only.
- No training: this is evaluation only.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `FATAL: locked test set SHA-256 mismatch` | The repo's test file changed — stop, do not evaluate, report. |
| Adapter 401/403 | HF token missing read access to `Pranay-20/platrixa-fyjc-specialist-v0.1`. |
| OOM during batched generation | Set `PHASE6C_BATCH_SIZE=4` (env var) or even `1`; results are identical, only slower. |
| `torch_dtype is deprecated` warning | Cosmetic in transformers ≥5; the script already passes the modern `dtype` argument where supported. |
| Run interrupted mid-way | Re-run the same command; intermediate predictions from the previous pass can be reused via `--skip-inference` once both prediction files exist. |
