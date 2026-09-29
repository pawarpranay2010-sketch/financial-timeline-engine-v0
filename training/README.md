# Platrixa Training Pipeline (historical FYJC slice + current Phase H dataset)

A provider-independent pipeline for building the Platrixa specialist
accounting-language model. The original pipeline was trained on FYJC
(Grade 11) single-entry bookkeeping and Indian accounting terminology —
an early development/evaluation slice of Platrixa's broader
financial-semantic scope, not the product's overall scope. Later phases
(notably Phase H) extend the dataset across the full capability surface
already implemented by Platrixa's three deterministic authorities.

## Architecture

```
Candidate cases (JSONL / PostgreSQL)
        ↓
Candidate Case Generator (template-based)
        ↓
Kernel Verification (deterministic accounting engine)
        ↓
Schema Verification (AIInterpretation contract)
        ↓
Quality Validation (schema + dedup + field checks)
        ↓
Dataset Splitting (train / val / test)
        ↓
Alpaca Format (Qwen2.5 training format)
        ↓
[OPTIONAL GPU] Training (LoRA / QLoRA)
        ↓
Evaluation (field-level accuracy)
        ↓
Platrixa AI → Kernel pipeline
```

## Phone-First Development Workflow

The developer's primary device is an **Android phone** (Realme P4 Power).
No GPU is required for most of the pipeline.

### What runs on the phone / CPU

| Step | CPU? | Time |
|------|:----:|:----:|
| Dataset generation | ✅ | ~30s for 500 cases |
| Quality validation | ✅ | ~5s |
| Dataset splitting | ✅ | <1s |
| Format conversion | ✅ | <1s |
| Pipeline orchestration | ✅ | ~1 min |
| Evaluation (logic only) | ✅ | ~5s |

### What requires a GPU

| Step | GPU? | Notes |
|------|:----:|-------|
| Model training | ✅ | Any CUDA GPU (T4, A10G, A100, etc.) |
| Model evaluation | ✅ | Need GPU to run inference |
| Model export (GGUF) | ✅ | Uses llama.cpp |

### Development flow

```
Phone (CPU)                    External GPU
    │                              │
    ├─ Generate dataset            │
    ├─ Validate quality            │
    ├─ Split data                  │
    ├─ Format for training         │
    │                              │
    └── git push ──────────────────┤
                                   ├─ Pull repo
                                   ├─ python training/train.py
                                   ├─ python training/evaluate.py
                                   ├─ git push adapter + report
                                   │
    ◄── git pull adapter ──────────┘
    │
    └─ Connect to Platrixa pipeline
```

## Quick Start

### 1. Generate the dataset (CPU)

```bash
# From project root
python training/pipeline.py

# Or step by step:
python training/generate.py --max-new 500 --seed 42
python training/validate.py training_data/generated_training_raw.jsonl
python training/split.py training_data/generated_training_raw.jsonl
python training/format.py training_data/specialist_train.jsonl
python training/format.py training_data/specialist_val.jsonl
python training/format.py training_data/specialist_test.jsonl
```

### 2. Validate the dataset (CPU)

```bash
python training/validate.py training_data/specialist_train.jsonl
python training/validate.py training_data/specialist_val.jsonl
python training/validate.py training_data/specialist_test.jsonl
```

### 3. Train when a GPU is available

```bash
# On a GPU machine:
pip install torch transformers peft trl datasets accelerate pyyaml

# Validate first (works on CPU)
python training/train.py --dry-run

# Train (requires GPU)
python training/train.py

# Or with overrides:
python training/train.py --epochs 5 --lr 1e-4 --batch-size 4
```

### 4. Evaluate

```bash
# On a GPU machine:
python training/evaluate.py --base-only
python training/evaluate.py --lora-path training_output/lora_adapter
```

## Files

| File | Purpose | CPU? |
|------|---------|:----:|
| `training/config.yaml` | Provider-independent configuration | ✅ |
| `training/generate.py` | Dataset generation from templates | ✅ |
| `training/validate.py` | Data quality validation | ✅ |
| `training/split.py` | Train/val/test splitting | ✅ |
| `training/format.py` | Alpaca format conversion | ✅ |
| `training/pipeline.py` | Unified pipeline | ✅ |
| `training/train.py` | Model training | ❌ GPU |
| `training/evaluate.py` | Model evaluation | ❌ GPU |
| `training/README.md` | This file | ✅ |

## Configuration

Edit `training/config.yaml` to change:

- **Model**: base model name, max sequence length
- **LoRA**: rank, alpha, target modules
- **Data**: paths, split ratios
- **Generation**: max cases, categories, seed
- **Training**: epochs, learning rate, batch size
- **Evaluation**: test sets, max samples

All configuration is provider-independent. No Colab, Kaggle, or
Google Drive paths.

## Modal resource split: CPU inference, GPU training

Normal production interpretation runs on a **CPU** container. A **T4 GPU** is
reserved for training and GPU-heavy evaluation. This split is explicit — there
is no automatic CPU→GPU failover, and normal inference never silently
escalates to a billable accelerator.

| Workload | Entry point | Resource | Selected by |
|---|---|---|---|
| Normal inference | `training/modal_inference.py` | **CPU (default)** | `PLATRIXA_MODEL_RESOURCE` unset or `cpu` |
| Training / GPU eval | `training/run_modal.py` | **T4** | always GPU; refuses `cpu` |

### Environment variables

```bash
# Normal inference (default) — CPU, 4 vCPU / 12 GiB, scales to zero
modal deploy training/modal_inference.py

# Inference on a GPU (must be explicit)
PLATRIXA_MODEL_RESOURCE=gpu modal deploy training/modal_inference.py

# Optional GPU class override (default T4)
PLATRIXA_MODEL_RESOURCE=gpu PLATRIXA_MODEL_GPU=A10G modal deploy training/modal_inference.py

# Training always runs on a GPU
modal run training/run_modal.py
```

- `PLATRIXA_MODEL_RESOURCE` — `cpu` (default) or `gpu`. Anything else is a hard
  configuration error at deploy time; it never falls back to a GPU.
- `PLATRIXA_MODEL_GPU` — GPU class, used **only** when the resource is `gpu`.
  Default `T4`.
- `PLATRIXA_MODAL_GPU` — deprecated alias for `PLATRIXA_MODEL_GPU`, still read.

**Behaviour change to be aware of:** before this split, a deployment that set
only `PLATRIXA_MODAL_GPU=T4` got a GPU. A GPU *inference* deployment must now
also set `PLATRIXA_MODEL_RESOURCE=gpu`. Training is unaffected.

Resolution rules live in `training/modal_resources.py` (no Modal import, unit
testable) and are covered by `scripts/fte_modal_resource_profile_test.py`.

### What this does and does not change

The resource profile is **infrastructure only**. It changes *where* the model
runs. It does not change the base model revision, the adapter revision, the
Alpaca prompt, the 18-field `CandidateSemanticIR` contract, grounding,
authority routing, or `VERIFIED` semantics. The GPU path is byte-identical to
the previous behaviour, including its dtype and resource arguments.

The CPU profile's CPU/memory values are derived from parameter-count arithmetic,
**not measured** — no CPU Modal runtime was benchmarked, so no latency or
throughput figure is claimed. The CPU path loads in fp32 (torch bfloat16 on
CPU is emulated unless the host has AVX512-BF16); that is a runtime numeric
detail, and CPU-vs-GPU output parity against the Phase 6C evaluation has not
been measured.

## Connecting to Platrixa

After training:

1. The LoRA adapter is saved to `training_output/lora_adapter/`
2. For GGUF export (Ollama/llama.cpp):
   ```bash
   # On GPU machine with llama.cpp:
   python -m llama_cpp.llama_export \
       --model training_output/lora_adapter \
       --outfile platrixa_fyjc.gguf \
       --outtype q4_k_m
   ```
3. The adapter connects to Platrixa via `FinanceModelAdapter.load_model()`
4. The kernel remains the source of truth for accounting correctness

## Safety Rules

- The **deterministic accounting kernel** is the source of truth
- This pipeline **never** generates training labels using an LLM
- Every generated case passes through the kernel for verification
- The model learns **interpretation/structured extraction**, not accounting truth
- Kernel verification happens at inference time, not training time
- No production code is modified by the training pipeline

## Data Format

Training records use the Alpaca format:

```json
{
  "instruction": "Parse the student's accounting language...",
  "input": "Purchased goods from Raj for Rs.20000",
  "output": "{\"transaction_type\": \"purchase\", \"parties\": [\"Raj\"], ...}",
  "_p4_metadata": {
    "problem_id": "C0000",
    "category": "cash_credit",
    "kernel_status": "VERIFIED"
  }
}
```

The model learns to produce structured JSON from natural-language
accounting transactions. The output format matches the `AIInterpretation`
schema defined in `backend/maths/fyjc_ai_adapter.py`.

## Dependencies

### For dataset preparation (CPU)
- Python 3.9+
- No ML dependencies required

### For training (GPU)
- Python 3.9+
- PyTorch with CUDA
- transformers
- peft
- trl
- datasets
- accelerate
- pyyaml (optional, for config.yaml)

### For the existing kernel (CPU)
- SQLAlchemy
- psycopg2-binary
- (other backend dependencies as needed)
