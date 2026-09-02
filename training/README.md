# Platrixa FYJC Accounting AI — Training Pipeline

A provider-independent pipeline for building a specialist accounting AI model
trained on FYJC (Grade 11) single-entry bookkeeping and Indian accounting terminology.

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
