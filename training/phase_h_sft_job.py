#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "torch==2.6.0",
#     "transformers==5.16.1",
#     "trl==1.13.0",
#     "peft==0.20.0",
#     "datasets==5.0.1",
#     "accelerate==1.14.0",
#     "huggingface_hub==1.30.0",
# ]
# ///
"""
Platrixa Phase H — TRL SFT + PEFT LoRA training job (Colab / Kaggle / CUDA host)
================================================================================

Trains the Phase H financial-semantic specialist on the frozen Phase H dataset
(training_data/phase_h_v01_8000.jsonl, sha256-pinned) with the exact pinned
hyperparameters of the previous 7-hour T4 run. Nothing about the training
method changed vs the v0.1/v0.2 jobs (same SFTConfig/LoraConfig fields, same
TRL API surface, verified for TRL 1.12/1.13):

    Student text
      -> Qwen/Qwen2.5-1.5B-Instruct @ pinned revision + Phase H LoRA
      -> exact 18-field ExpandedInterpretation JSON   (language facts ONLY)
      -> strict schema validation / grounding / kernel (downstream, unchanged)

RESTARTABLE TRAINING (the one behavioral change vs v0.1/v0.2):
  - SFTConfig uses save_strategy="steps" (save_steps=250, save_total_limit=2)
    through the STANDARD HF/TRL Trainer checkpoint mechanism — every checkpoint
    carries adapter weights PLUS optimizer/scheduler/RNG/trainer state, so a
    disconnected runtime resumes true training, not just weights.
  - On startup the job scans OUTPUT_DIR for the latest COMPLETE checkpoint
    (directory matching checkpoint-<step> that contains trainer_state.json and
    adapter_config.json). Incomplete/garbage directories are logged and
    skipped, never resumed. No checkpoint -> clean start from scratch.
  - Checkpoints live directly under OUTPUT_DIR (stable path; override with
    PLATRIXA_PHASE_H_OUTPUT_DIR for Colab Drive / Kaggle persistent mounts).
    Nothing in this job deletes checkpoints before resume; save_total_limit
    rotation is HF-managed and never removes the newest checkpoint.
  - The final adapter save to OUTPUT_DIR is unchanged (adapter_config.json +
    safetensors + tokenizer), and NOTHING is uploaded to Hugging Face.

Data (frozen, never regenerated here):
  - training_data/phase_h_v01_8000.jsonl — 8,000 rows {id, input, output,
    metadata}; sha256 verified before use; split read from the frozen
    metadata.split (md5(leakage_group) % 15 rule) and re-derived in-job as an
    integrity cross-check. Expected: train 6,856 / valid 1,144 — hard-fail on
    any mismatch. Locked evaluation artifacts are never touched.

Run (from repo root, CUDA host):
    HF_TOKEN not required — no Hub writes.
    PLATRIXA_PHASE_H_OUTPUT_DIR=/drive/ckpt python3 training/phase_h_sft_job.py

The base model is pinned to BASE_MODEL_REVISION: model weights, config,
tokenizer and chat template all come from that exact SHA, never from a
floating main revision.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Phase 22 finding R1: deterministic behavior requires PYTHONHASHSEED=0.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv,
              {**os.environ, "PYTHONHASHSEED": "0"})

# Single source of truth for the system instruction (same string the v0.1/v0.2
# corpora were formatted with — see training/format.py).
from training.format import SYSTEM_INSTRUCTION

import torch

# --------------------------------------------------------------------------
# Configuration — pinned Phase H run hyperparameters (do NOT change)
# --------------------------------------------------------------------------

BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"

SEED = 3407
EPOCHS = 3
PER_DEVICE_BATCH = 2
GRAD_ACCUM = 4  # effective batch = 2 * 4 = 8
LR = 2.0e-4
WARMUP_STEPS = 5
WEIGHT_DECAY = 0.001
LR_SCHEDULER = "linear"
MAX_SEQ_LENGTH = 2048
PACKING = False
FP16 = True  # T4 (Turing) has no bf16 support
GRADIENT_CHECKPOINTING = True
LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05
LORA_TARGET_MODULES = "all-linear"  # q/k/v/o + gate/up/down proj on Qwen2.5

# --------------------------------------------------------------------------
# Checkpointing (restartable training)
# --------------------------------------------------------------------------

SAVE_STRATEGY = "steps"
SAVE_STEPS = 250
SAVE_TOTAL_LIMIT = 2  # HF-managed rotation; never deletes the newest checkpoint

# Version gate: a checkpoint written by one library version may not resume on
# another. Fail closed on mismatch with the versions this job was built for.
EXPECTED_VERSIONS = {
    "transformers": "5.16.1",
    "trl": "1.13.0",
    "peft": "0.20.0",
    "accelerate": "1.14.0",
}

# Stable, explicit checkpoint/output location. Default follows the v0.1/v0.2
# job convention (/output on HF Jobs / Modal / container CUDA hosts). Override
# ONLY the location, never the mechanism, on Colab/Kaggle where checkpoints
# must survive runtime loss (e.g. a mounted Drive / persistent volume).
OUTPUT_DIR = Path(os.environ.get("PLATRIXA_PHASE_H_OUTPUT_DIR", "/output"))

# --------------------------------------------------------------------------
# Phase H data source (frozen local dataset — never regenerated or uploaded)
# --------------------------------------------------------------------------

DATASET_PATH = PROJECT_ROOT / "training_data" / "phase_h_v01_8000.jsonl"
EXPECTED_DATASET_SHA256 = "5c56b373537c982d3784452a67f01d173fa390e093978e46df4788e3048d1f29"
SOURCE_ROWS_EXPECTED = 8000
TRAIN_COUNT_EXPECTED = 6856
VALID_COUNT_EXPECTED = 1144


def log(msg: str) -> None:
    print(f"[platrixa-job] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[platrixa-job] FATAL: {msg}", flush=True)
    sys.exit(1)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def assign_split(leakage_group: str) -> str:
    """EXACT Phase H generator split rule (training/phase_h_generate.py):
    dev iff int(md5(leakage_group)[:8], 16) % 100 < 15, else train."""
    h = hashlib.md5(leakage_group.encode()).hexdigest()
    return "dev" if int(h[:8], 16) % 100 < 15 else "train"


# --------------------------------------------------------------------------
# Checkpoint discovery (standard HF/TRL checkpoint format only)
# --------------------------------------------------------------------------

def find_latest_checkpoint(output_dir: Path) -> Optional[Path]:
    """Return the latest COMPLETE Trainer checkpoint under output_dir, or None.

    A directory qualifies only if it matches checkpoint-<step> AND contains the
    standard resume artifacts (trainer_state.json + adapter_config.json for the
    PEFT adapter). Optimizer/scheduler/RNG state are part of the same standard
    checkpoint write, so a qualifying directory is a true training resume point.
    Anything else (partial write, renamed dir, stray folder) is logged and
    skipped — never resumed. No custom checkpoint format is used or needed.
    """
    if not output_dir.is_dir():
        return None
    candidates: list[tuple[int, Path]] = []
    for entry in sorted(output_dir.iterdir()):
        m = re.fullmatch(r"checkpoint-(\d+)", entry.name)
        if m is None or not entry.is_dir():
            continue
        if not (entry / "trainer_state.json").is_file():
            log(f"ignoring incomplete checkpoint (no trainer_state.json): {entry}")
            continue
        if not (entry / "adapter_config.json").is_file():
            log(f"ignoring incomplete checkpoint (no adapter_config.json): {entry}")
            continue
        candidates.append((int(m.group(1)), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


# --------------------------------------------------------------------------
# Frozen-row projection (same contract as the v0.2 job)
# --------------------------------------------------------------------------

EXPANDED_18_FIELDS = {
    "transaction_type", "parties", "amounts", "payment_method", "references",
    "ambiguities", "grounding", "transaction_type_enum", "payment_method_enum",
    "ambiguity_flags", "referenced_transaction_index", "referenced_party",
    "referenced_amount", "field_confidences", "overall_confidence",
    "suggested_status", "safety_flags", "scope_flags",
}
FORBIDDEN_TARGET_KEYS = {
    "journal", "journal_entry", "journal_entries", "ledger",
    "ledger_postings", "trial_balance", "balances", "debit_account",
    "credit_account", "debit_lines", "credit_lines", "debits", "credits",
    "accounts", "accounting_conclusion",
}


def to_messages_row(record: dict) -> dict:
    """Project a frozen {id, input, output, metadata} row into the established
    {messages: [system, user, assistant]} conversational contract.

    assistant = compact 18-field JSON (training/format.py serialization), so
    the assistant-only loss sees byte-identical targets to the v0.1/v0.2
    corpora. Dataset metadata NEVER enters the model target.
    """
    return {
        "id": record["id"],
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": record["input"]},
            {"role": "assistant", "content": json.dumps(
                record["output"], ensure_ascii=False, separators=(",", ":"))},
        ],
    }


def verify_contract(projected: list, label: str) -> None:
    """Roles, JSON validity, exact 18-field contract, forbidden output keys."""
    for row in projected:
        roles = [m.get("role") for m in row["messages"]]
        if roles != ["system", "user", "assistant"]:
            fail(f"{label} {row['id']}: unexpected message roles {roles}")
        target = row["messages"][2]["content"]
        try:
            parsed = json.loads(target)
        except Exception:
            fail(f"{label} {row['id']}: assistant target is not valid JSON")
        if not isinstance(parsed, dict):
            fail(f"{label} {row['id']}: assistant target is not a JSON object")
        extra = set(parsed) - EXPANDED_18_FIELDS
        forbidden = set(parsed) & FORBIDDEN_TARGET_KEYS
        if extra:
            fail(f"{label} {row['id']}: assistant target has keys outside the "
                 f"18-field contract: {sorted(extra)}")
        if forbidden:
            fail(f"{label} {row['id']}: assistant target contains forbidden "
                 f"accounting-output keys: {sorted(forbidden)}")
    log(f"{label}: {len(projected)} rows, roles OK, targets = exact 18-field "
        f"contract, 0 forbidden accounting-output keys")


def load_phase_h_split() -> tuple[list, list, dict]:
    """Verify the frozen Phase H dataset and return (train_rows, valid_rows,
    split_info). The split is read from the frozen metadata.split and
    independently re-derived from the generator's md5(leakage_group) rule;
    any disagreement is a fail-closed integrity error. Counts are asserted:
    8,000 source rows -> 6,856 train / 1,144 valid."""
    if not DATASET_PATH.is_file():
        fail(f"Phase H dataset not found: {DATASET_PATH}")
    sha = sha256_file(DATASET_PATH)
    if sha != EXPECTED_DATASET_SHA256:
        fail(f"frozen Phase H dataset SHA mismatch: {sha} != "
             f"{EXPECTED_DATASET_SHA256} — STOP")
    log(f"dataset sha256 verified: {sha}")

    rows = [json.loads(line) for line in DATASET_PATH.read_text().splitlines()
            if line.strip()]
    if len(rows) != SOURCE_ROWS_EXPECTED:
        fail(f"expected {SOURCE_ROWS_EXPECTED} rows, found {len(rows)}")
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        fail("duplicate row ids in frozen dataset")
    for r in rows:
        if set(r) != {"id", "input", "output", "metadata"}:
            fail(f"{r.get('id')}: unexpected frozen-row keys {sorted(r)}")
        if len(r["output"]) != len(EXPANDED_18_FIELDS):
            fail(f"{r['id']}: output is not the 18-field contract")
    log(f"row count verified: {len(rows)}")

    # Production schema verifier — same stack the dataset was generated with.
    from backend.maths.schema_verifier import validate_structured_interpretation
    bad_schema = []
    for r in rows:
        rep = validate_structured_interpretation(r["output"], allow_expanded=True)
        if not rep.valid:
            bad_schema.append(r["id"])
    if bad_schema:
        fail(f"rows failing the production schema verifier: {bad_schema[:5]}")
    log("production schema verifier: all rows PASS (allow_expanded=True)")

    train_rows, valid_rows, mismatches = [], [], 0
    for r in rows:
        stored = r["metadata"].get("split")
        derived = assign_split(r["metadata"]["leakage_group"])
        if stored != derived:
            mismatches += 1
            if mismatches <= 5:
                log(f"split mismatch for {r['id']}: stored={stored} derived={derived}")
        (valid_rows if derived == "dev" else train_rows).append(r)
    if mismatches:
        fail(f"{mismatches} rows disagree between frozen metadata.split and the "
             f"generator split rule — dataset integrity failure")
    log(f"split integrity verified: metadata.split == md5(leakage_group) rule "
        f"for all {len(rows)} rows")

    if len(train_rows) != TRAIN_COUNT_EXPECTED or len(valid_rows) != VALID_COUNT_EXPECTED:
        fail(f"unexpected split counts: train={len(train_rows)} "
             f"valid={len(valid_rows)} (expected "
             f"{TRAIN_COUNT_EXPECTED}/{VALID_COUNT_EXPECTED})")
    log(f"split: train={len(train_rows)} valid={len(valid_rows)}")
    return train_rows, valid_rows, {"source_rows": len(rows),
                                    "train": len(train_rows),
                                    "valid": len(valid_rows)}


def main() -> None:
    log(f"job start UTC={datetime.now(timezone.utc).isoformat()}")
    log(f"base_model={BASE_MODEL}@{BASE_MODEL_REVISION[:12]}")

    # --- versions (checkpoint-compatibility gate) --------------------------
    import accelerate
    import datasets
    import peft
    import transformers
    import trl

    actual = {"transformers": transformers.__version__, "trl": trl.__version__,
              "peft": peft.__version__, "accelerate": accelerate.__version__}
    mismatched = {k: (EXPECTED_VERSIONS[k], v) for k, v in actual.items()
                  if EXPECTED_VERSIONS[k] != v}
    if mismatched:
        fail(f"library version mismatch (expected, found): {mismatched} — "
             f"checkpoints are not guaranteed to resume across versions")
    log(f"python={sys.version.split()[0]} torch={torch.__version__} "
        f"transformers={actual['transformers']} trl={actual['trl']} "
        f"peft={actual['peft']} accelerate={actual['accelerate']} "
        f"datasets={datasets.__version__}")

    log(f"cuda_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        log(f"gpu={props.name} vram_gb={round(props.total_memory / 1e9, 1)} "
            f"capability={props.major}.{props.minor}")
    else:
        fail("CUDA not available — cannot train")

    # --- dataset (frozen, split-verified) ----------------------------------
    train_rows, valid_rows, split_info = load_phase_h_split()

    from datasets import Dataset

    train_proj = [to_messages_row(r) for r in train_rows]
    valid_proj = [to_messages_row(r) for r in valid_rows]
    verify_contract(train_proj, "train")
    verify_contract(valid_proj, "valid")

    train_ds = Dataset.from_list(
        [{"messages": r["messages"]} for r in train_proj])
    valid_ds = Dataset.from_list(
        [{"messages": r["messages"]} for r in valid_proj])
    log(f"datasets loaded: train={len(train_ds)} valid={len(valid_ds)}")

    # --- model + tokenizer (pinned revision, explicit objects) -------------
    from transformers import AutoModelForCausalLM, AutoTokenizer

    log(f"loading base model {BASE_MODEL} @ {BASE_MODEL_REVISION[:12]} (fp16)")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        revision=BASE_MODEL_REVISION,
        torch_dtype=torch.float16,
    )
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_MODEL_REVISION)
    if not tokenizer.chat_template:
        fail("tokenizer has no chat template — cannot apply TRL chat formatting")
    log(f"base model architecture={model.config.architectures} "
        f"num_params={model.num_parameters():,} chat_template=present")

    # --- training ----------------------------------------------------------
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    training_args = SFTConfig(
        output_dir=str(OUTPUT_DIR),
        run_name="platrixa-phase-h-sft",
        seed=SEED,
        data_seed=SEED,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        warmup_steps=WARMUP_STEPS,
        weight_decay=WEIGHT_DECAY,
        lr_scheduler_type=LR_SCHEDULER,
        optim="adamw_torch",
        fp16=FP16,
        gradient_checkpointing=GRADIENT_CHECKPOINTING,
        max_length=MAX_SEQ_LENGTH,  # TRL 1.12/1.13 SFTConfig field
        packing=PACKING,
        assistant_only_loss=True,   # loss on assistant 18-field JSON only
        eval_strategy="epoch",
        logging_steps=10,
        # Restartable training: standard step-based checkpoints carrying full
        # trainer state (adapter + optimizer + scheduler + RNG + trainer state).
        save_strategy=SAVE_STRATEGY,
        save_steps=SAVE_STEPS,
        save_total_limit=SAVE_TOTAL_LIMIT,
        report_to="none",
    )

    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=LORA_TARGET_MODULES,
    )

    log(f"SFTConfig: epochs={EPOCHS} eff_batch={PER_DEVICE_BATCH * GRAD_ACCUM} "
        f"lr={LR} seq={MAX_SEQ_LENGTH} fp16={FP16} assistant_only_loss=True "
        f"warmup_steps={WARMUP_STEPS}")
    log(f"LoraConfig: r={LORA_R} alpha={LORA_ALPHA} dropout={LORA_DROPOUT} "
        f"target={LORA_TARGET_MODULES}")
    log(f"checkpoint_strategy={SAVE_STRATEGY} save_steps={SAVE_STEPS} "
        f"save_total_limit={SAVE_TOTAL_LIMIT}")
    log(f"output_dir={OUTPUT_DIR}")

    # --- resume detection (standard HF/TRL checkpoints only) ---------------
    resume_ckpt = find_latest_checkpoint(OUTPUT_DIR)
    if resume_ckpt is None:
        log("no checkpoint found; starting from scratch")
    else:
        log(f"latest checkpoint found: {resume_ckpt}")
        log(f"resuming from checkpoint: {resume_ckpt}")

    trainer = SFTTrainer(
        model=model,                 # pre-loaded from the pinned revision
        args=training_args,
        processing_class=tokenizer,  # TRL 1.12/1.13 name for the tokenizer
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        peft_config=lora_config,     # TRL wraps via get_peft_model (adapter-only)
    )

    log("starting training")
    t0 = time.time()
    train_result = trainer.train(
        resume_from_checkpoint=str(resume_ckpt) if resume_ckpt else None)
    train_seconds = round(time.time() - t0, 1)
    log(f"training finished in {train_seconds}s")
    log(f"train_metrics={json.dumps(train_result.metrics, default=str)}")

    history = trainer.state.log_history
    final_eval = [h for h in history if "eval_loss" in h][-1] if history else {}
    train_loss = float(train_result.metrics.get("train_loss", float("nan")))
    eval_loss = float(final_eval.get("eval_loss", float("nan")))
    log(f"final_train_loss={train_loss} final_eval_loss={eval_loss}")

    # --- persist final adapter (adapter-only; base model stays separate) ---
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(trainer.model, peft.PeftModel):
        fail("trained model is not a PeftModel — refusing to save anything but "
             "an adapter (no merged/full-model output)")
    trainer.model.save_pretrained(str(OUTPUT_DIR))  # adapter_config.json + safetensors
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    log(f"adapter saved to {OUTPUT_DIR}: "
        f"{sorted(p.name for p in OUTPUT_DIR.iterdir())}")
    log("nothing uploaded to Hugging Face (explicit job policy)")

    manifest = {
        "phase": "H",
        "purpose": "Platrixa Phase H financial-semantic LoRA (restartable SFT)",
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "checkpointing": {
            "save_strategy": SAVE_STRATEGY,
            "save_steps": SAVE_STEPS,
            "save_total_limit": SAVE_TOTAL_LIMIT,
            "mechanism": "standard HF/TRL Trainer checkpoints under OUTPUT_DIR "
                         "(adapter + optimizer + scheduler + RNG + trainer state)",
            "output_dir": str(OUTPUT_DIR),
            "resumed_from": str(resume_ckpt) if resume_ckpt else None,
        },
        "training": {
            "method": "TRL SFTTrainer (SFT) + PEFT LoRA",
            "transformers_version": actual["transformers"],
            "trl_version": actual["trl"],
            "peft_version": actual["peft"],
            "accelerate_version": actual["accelerate"],
            "datasets_version": datasets.__version__,
            "torch_version": torch.__version__,
            "gpu": torch.cuda.get_device_properties(0).name if torch.cuda.is_available() else None,
            "epochs": EPOCHS,
            "learning_rate": LR,
            "per_device_batch_size": PER_DEVICE_BATCH,
            "gradient_accumulation_steps": GRAD_ACCUM,
            "effective_batch_size": PER_DEVICE_BATCH * GRAD_ACCUM,
            "max_seq_length": MAX_SEQ_LENGTH,
            "packing": PACKING,
            "warmup_steps": WARMUP_STEPS,
            "weight_decay": WEIGHT_DECAY,
            "lr_scheduler": LR_SCHEDULER,
            "optimizer": "adamw_torch",
            "precision": "fp16",
            "gradient_checkpointing": GRADIENT_CHECKPOINTING,
            "seed": SEED,
            "assistant_only_loss": True,
            "lora_r": LORA_R,
            "lora_alpha": LORA_ALPHA,
            "lora_dropout": LORA_DROPOUT,
            "lora_target_modules": LORA_TARGET_MODULES,
            "train_samples": len(train_ds),
            "valid_samples": len(valid_ds),
            "final_train_loss": train_loss,
            "final_eval_loss": eval_loss,
            "training_duration_s": train_seconds,
        },
        "dataset": {
            "path": str(DATASET_PATH),
            "sha256": sha256_file(DATASET_PATH),
            "source_rows": split_info["source_rows"],
            "train_count": split_info["train"],
            "valid_count": split_info["valid"],
            "split_method": "frozen metadata.split (md5(leakage_group) % 15 rule), "
                            "re-derived and cross-checked in-job",
            "uploaded_to_hf": False,
        },
        "artifact": {
            "adapter_path": str(OUTPUT_DIR),
            "merge_adapter": False,
            "uploaded_to_hf": False,
        },
        "runtime": "ephemeral GPU container (Colab/Kaggle/any CUDA host); "
                   "checkpoints + final adapter stay under OUTPUT_DIR",
        "credentials": "none stored",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = OUTPUT_DIR / "phase_h_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    log(f"manifest written: {manifest_path}")

    # Release training-time GPU state.
    del trainer
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    log("FINAL_STATUS: SUCCESS "
        f"adapter={OUTPUT_DIR} resumed_from={resume_ckpt or 'scratch'} "
        f"train_loss={train_loss} eval_loss={eval_loss} seconds={train_seconds}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail-closed: any uncaught error fails the run
        print(f"[platrixa-job] FATAL UNCAUGHT: {type(exc).__name__}: {exc}",
              flush=True)
        sys.exit(1)
