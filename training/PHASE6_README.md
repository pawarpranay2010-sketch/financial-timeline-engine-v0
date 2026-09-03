# Platrixa FYJC — Phase 6: AutoTrain / LoRA Training Workflow

Phase 6 makes the Phase 3/5 local-specialist plan real: fine-tune
**Qwen/Qwen2.5-1.5B-Instruct** with **LoRA (PEFT)** on the Phase 5
1,000-example dataset so the model reliably turns student natural-language
input into the 18-field `ExpandedInterpretation` JSON.

## Three separate states (do not conflate)

| State | Meaning | Where |
|---|---|---|
| **6A — audit + preparation** | Repo audit, dataset suitability, config + converter + eval tooling, preflight tests | ✅ COMPLETE (committed) |
| **6B — actual LoRA training** | TRL `SFTTrainer` + LoRA job on a CUDA host (Modal free-T4 route ready) | ⏳ NOT YET RUN — needs user Modal login/authorization |
| **6C — adapter evaluation + integration** | `training/evaluate_finetuned.py` on the untouched 100 test examples + LocalModelRunner check | ⏳ Requires 6B adapter |

## Architecture (non-negotiable, unchanged)

```
Student text → LOCAL Qwen2.5-1.5B-Instruct + Platrixa LoRA
            → 18-field structured interpretation JSON
            → strict schema validation
            → GroundingGate
            → deterministic accounting kernel
```

The fine-tuned model is a **language-understanding specialist only**. The
training targets contain **no journal entries, no debit/credit decisions, no
ledger postings, no balances, no accounting conclusions** — those stay in the
deterministic kernel.

## AutoTrain audit results (current, from hf.co/docs/autotrain + repo source)

- **Status:** AutoTrain Advanced is officially **no longer maintained**
  ("No new features will be added and bugs will not be fixed. We recommend
  using Axolotl, TRL, or transformers.Trainer"). Latest stable pip release is
  **v0.8.24**. It remains installable and usable for this small SFT job.
- **Install:** `pip install autotrain-advanced` (Python ≥ 3.10; PyTorch +
  `git-lfs` required on the training host). Hosted option: the AutoTrain
  Docker image on a private Hugging Face Space with a GPU.
- **Workflow selected:** `autotrain --config training/autotrain_config.yaml`
  with `task: llm-sft`, `backend: local` — the current documented CLI/config
  workflow (per-task CLI flags are the legacy interface).
- **Qwen2.5 compatibility:** Qwen2.5-1.5B-Instruct is a standard Llama-3.1
  architecture causal LM supported by transformers/peft/trl; `chat_template:
  tokenizer` applies the model's own chat template at load time. Verified in
  AutoTrain's llm-sft trainer: it calls
  `tokenizer.apply_chat_template(messages, add_generation_prompt=False)` and
  trains with TRL `SFTTrainer` (`dataset_text_field = messages`,
  `packing=True`, `max_seq_length = block_size`).
- **Dataset format:** JSONL, one row per example:
  `{"messages": [{"role":"system","content": ...},
                 {"role":"user","content": <student text>},
                 {"role":"assistant","content": <18-field JSON>}]}`
  with `column_mapping.text_column: messages`.
- **Model access:** the Qwen2.5 Hub repos are access-controlled — the
  training host must be logged in (`HF_TOKEN`) and the license accepted once.
  Alternative mirror if ever needed: `unsloth/Qwen2.5-1.5B-Instruct` (used by
  the legacy `training/config.yaml`), but the standard stays
  `Qwen/Qwen2.5-1.5B-Instruct` (also `LocalModelRunner`'s default).
- **Hardware:** Qwen2.5-1.5B + LoRA needs a CUDA GPU; ~6 GB VRAM is the
  practical floor with int4 QLoRA + gradient checkpointing (12 GB+ is
  comfortable). CPU-only training is not practical and is not attempted.
  AutoTrain's hosted option (HF Space GPU) is the fallback if no local GPU.

## Files

| File | Purpose |
|---|---|
| `training/autotrain_config.yaml` | Reference AutoTrain llm-sft LoRA config (superseded for 6B — see below) |
| `training/prepare_autotrain.py` | Converts Phase 5 splits → `messages` JSONL + writes `training/phase6_manifest.json` |
| `training/trl_sft_job.py` | **Active 6B job**: self-contained TRL `SFTTrainer` + PEFT LoRA (dataset/model pinned, adapter pushed to Hub, smoke test) |
| `training/run_modal.py` | Launches `trl_sft_job.py` on Modal (free T4 credits) — client pinned in `training/requirements_modal.txt` |
| `training/run_sft_free_gpu.py` | Alternate launcher for free notebook hosts (Colab/Kaggle) — same job, zero code changes |
| `training/phase6_manifest.json` | Phase 6A reproducibility manifest (dataset prep) |
| `training/phase6b_manifest.json` | Phase 6B provenance manifest (pre-run config record; updated by the job with adapter revision) |
| `training/evaluate_finetuned.py` | Base vs LoRA evaluation on the untouched test set; production-path gate check |
| `training/PHASE6_README.md` | This document |
| `training/requirements_modal.txt` | Pinned Modal client dependency for the launcher |
| `scripts/fte_fyjc_49_autotrain_preflight_test.py` | Phase 6A preflight tests (CPU-only) |

Created/generated: `training_data/autotrain_fyjc/{train,valid}.jsonl` and
`training/phase6_manifest.json` (runtime artifacts; regenerate with the
command below).

## Exact workflow

### 6A — prepare (CPU, reproducible)

```bash
python3 training/prepare_autotrain.py          # → autotrain_fyjc/{train,valid}.jsonl + manifest
python3 scripts/fte_fyjc_49_autotrain_preflight_test.py
```

Regeneration is byte-identical (deterministic).

### 6B — train

**Active implementation:** TRL `SFTTrainer` + PEFT LoRA in
`training/trl_sft_job.py` (see the Modal route below). The AutoTrain config
above is retained only as a reference — the AutoTrain Advanced approach was
dropped (officially unmaintained) in favour of HF Jobs / Modal + TRL.

#### Modal free-T4 route (current; no credit card, ~$30/mo free credits)

```bash
# one-time workspace/host setup
pip install -r training/requirements_modal.txt   # modal==1.5.5 (pinned)
python3 -m modal setup                           # INTERACTIVE browser flow — run it yourself
modal secret create hf-token HF_TOKEN=<hf_... token>   # one-time; never commit the token

# launch the single baseline run (expects HF_TOKEN secret + Qwen access on the account)
modal run training/run_modal.py
```

Expected: ~30–50 min on a T4, cost ≈ $0.30–0.50 of the free monthly
credits (hard-capped by the container timeout). The job downloads only
train (800) + valid (100) at dataset revision `75f05fd…`, pushes the LoRA
adapter to `Pranay-20/platrixa-fyjc-specialist-v0.1` before exit, and writes
the adapter revision into `training/phase6b_manifest.json`.

#### Alternative: any CUDA host (≥ 16 GB VRAM, Python ≥ 3.10)

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121  # GPU build
# then install the job's pinned deps (see the PEP-723 header of trl_sft_job.py,
# or use training/run_sft_free_gpu.py which resolves them automatically)
export HF_TOKEN=<hf_... token>            # never put the token in source/git
python3 training/trl_sft_job.py
```

Run parameters are pinned in the job script + `training/phase6b_manifest.json`
(base model & dataset revisions, LoRA r/alpha/dropout, epochs, LR, batch,
seed, adapter repository). Never put credentials in the manifest.

### 6C — evaluate + integrate

```bash
# A vs B on the same 100 untouched test examples (both need the base model)
python3 training/evaluate_finetuned.py --lora-path platrixa-fyjc-specialist/<adapter-dir>

# production-path gate check (model → schema → GroundingGate)
PLATRIXA_FYJC_MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct \
PLATRIXA_FYJC_ADAPTER=platrixa-fyjc-specialist/<adapter-dir> \
python3 training/evaluate_finetuned.py --production-path

# or drive the real component directly:
#   python3 -c "from backend.maths.fyjc_llm_specialist import FYJCLLMSpecialist; ..."
```

`LocalModelRunner` (env: `PLATRIXA_FYJC_MODEL_ID`, `PLATRIXA_FYJC_ADAPTER`,
`PLATRIXA_FYJC_DEVICE`, `PLATRIXA_FYJC_DTYPE`, ...) already loads a LoRA
adapter via `PeftModel.from_pretrained` — no architecture change was needed
and none is planned. Do **not** connect the model to the accounting kernel
directly; schema validation and the GroundingGate stay in the path.

## Safety / dataset rules (inherited from Phase 5)

- 815 clear/grounded examples may train toward `VERIFIED`; the 185
  ambiguous/incomplete/contradictory/adversarial/unsupported examples train
  the model to say `REVIEW_REQUIRED` instead of inventing facts.
- No prompt-injection, hallucination-bait, or contradiction example was
  removed to make training "easier".
- The 100 test examples are never in the training data dir
  (`autotrain_fyjc/` holds train + valid only).

## This workspace audit snapshot (Phase 6A)

- Python 3.10.12 (meets AutoTrain's ≥ 3.10 floor) — but no PyTorch,
  transformers, peft, datasets, accelerate, bitsandbytes, trl, or AutoTrain
  installed; no `nvidia-smi`/CUDA device; ~2.9 GiB RAM; ~4 GB disk. Actual
  training is **blocked here by design**; nothing in 6A fakes a run.
- Dataset: 1,000 = 800 train / 100 validation / 100 test, 0 overlap, all
  18-field outputs, all targets valid compact JSON (see test 49).
