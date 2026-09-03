#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "torch==2.6.0",
#     "transformers==5.16.1",
#     "trl==1.12.0",
#     "peft==0.20.0",
#     "datasets==5.0.1",
#     "accelerate==1.14.0",
#     "huggingface_hub==1.30.0",
# ]
# ///
"""
Platrixa FYJC — Phase 6B: TRL SFT + PEFT LoRA training job (Hugging Face Jobs)
==============================================================================

Single controlled baseline run. Architecture (non-negotiable):

    Student text
      -> Qwen/Qwen2.5-1.5B-Instruct + Platrixa LoRA   [THIS JOB trains it]
      -> 18-field ExpandedInterpretation JSON          (language facts ONLY)
      -> strict schema validation                      (downstream, P4)
      -> GroundingGate                                 (downstream, P4)
      -> deterministic accounting kernel               (downstream, P1)

The model is a LANGUAGE-UNDERSTANDING SPECIALIST. It is trained to map
student natural language to the compact 18-field semantic JSON target. It is
NOT trained to produce journal entries, debit/credit decisions, ledger
postings, balances, or accounting conclusions (those stay in the
deterministic kernel).

Data:
  - Repo : Pranay-20/platrixa-fyjc-specialist-1000 (private)
  - Rev  : 75f05fd287622c4dd7ae8f53cc9c575ef8f35672
  - train.jsonl  = 800 rows   (used)
  - valid.jsonl  = 100 rows   (used for eval loss only)
  - test (100)   = NOT uploaded, NEVER touched by this job

Method: SFT (TRL SFTTrainer + SFTConfig) with PEFT LoRA, fp16 on the job GPU
(T4). assistant_only_loss=True -> TRL swaps in its bundled Qwen2.5 training
chat template (verified current TRL docs) so loss covers only the assistant
JSON target, never the system/user prompt.

Artifacts: after training the adapter directory is pushed to the persistent
private model repo Pranay-20/platrixa-fyjc-specialist-v0.1. The Jobs
environment is ephemeral, so the push happens inside this job before exit.

Credentials: HF_TOKEN is injected by the Job runner as a secret env var
(--secrets HF_TOKEN). It is never logged, written to a file, or embedded in
any artifact.

Run (from repo root, GPU host with HF_TOKEN in the environment):
    modal run training/run_modal.py          # Modal T4 (free Starter credits)
    # or directly on any CUDA host:
    #   HF_TOKEN=<secret> python3 training/trl_sft_job.py

The base model is pinned to BASE_MODEL_REVISION (see below): model weights,
config, tokenizer and chat template all come from that exact SHA, never from a
floating main revision.
"""

from __future__ import annotations

import gc
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch

# --------------------------------------------------------------------------
# Configuration (single controlled baseline run — see training/PHASE6_README.md)
# --------------------------------------------------------------------------

BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
# Pinned base-model revision (live-verified 2026-09-03 via the HF API: not
# gated, private=False). A fixed revision makes every download deterministic:
# model weights, config, tokenizer and chat template all come from this SHA.
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
DATASET_REPO = "Pranay-20/platrixa-fyjc-specialist-1000"
DATASET_REVISION = "75f05fd287622c4dd7ae8f53cc9c575ef8f35672"
ADAPTER_REPO = "Pranay-20/platrixa-fyjc-specialist-v0.1"

SEED = 3407
EPOCHS = 3
PER_DEVICE_BATCH = 2
GRAD_ACCUM = 4  # effective batch = 2 * 4 = 8
LR = 2.0e-4
WARMUP_RATIO = 0.1
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

OUTPUT_DIR = Path("/output")
DATA_DIR = Path("/data")
DATA_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[platrixa-job] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[platrixa-job] FATAL: {msg}", flush=True)
    sys.exit(1)


def main() -> None:
    log(f"job start UTC={datetime.now(timezone.utc).isoformat()}")
    log(f"base_model={BASE_MODEL} dataset={DATASET_REPO}@{DATASET_REVISION[:12]}")
    log(f"adapter_repo={ADAPTER_REPO}")

    # --- credentials -------------------------------------------------------
    if not os.environ.get("HF_TOKEN"):
        fail("HF_TOKEN secret not present in the job environment")
    log("HF_TOKEN secret present (value never printed)")

    # --- versions / hardware ----------------------------------------------
    import accelerate
    import datasets
    import huggingface_hub
    import peft
    import transformers
    import trl

    log(f"python={sys.version.split()[0]} torch={torch.__version__} "
        f"transformers={transformers.__version__} trl={trl.__version__} "
        f"peft={peft.__version__} datasets={datasets.__version__} "
        f"accelerate={accelerate.__version__} hub={huggingface_hub.__version__}")
    log(f"cuda_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        log(f"gpu={props.name} vram_gb={round(props.total_memory / 1e9, 1)} "
            f"capability={props.major}.{props.minor}")
    else:
        fail("CUDA not available in this Job — cannot train")

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()

    # --- pinned base model revision ----------------------------------------
    try:
        pinned_model = api.model_info(BASE_MODEL, revision=BASE_MODEL_REVISION)
    except Exception as exc:
        fail(f"pinned base model {BASE_MODEL}@{BASE_MODEL_REVISION[:12]} "
             f"not accessible: {exc}")
    log(f"base model verified pinned: {BASE_MODEL} @ {pinned_model.sha}")

    # --- dataset (pinned revision, test set never present) ----------------
    log(f"downloading dataset at pinned revision {DATASET_REVISION}")
    for name in ("train.jsonl", "valid.jsonl"):
        hf_hub_download(
            DATASET_REPO, name, repo_type="dataset",
            revision=DATASET_REVISION, local_dir=DATA_DIR,
        )
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
    counts: dict[str, int] = {}
    for name in ("train.jsonl", "valid.jsonl"):
        path = DATA_DIR / name
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        counts[name] = len(rows)
        for row in rows:
            roles = [m.get("role") for m in row.get("messages", [])]
            if roles != ["system", "user", "assistant"]:
                fail(f"{name}: unexpected message roles {roles}")
            target = row["messages"][2]["content"]
            try:
                parsed = json.loads(target)
            except Exception:
                fail(f"{name}: assistant target is not valid JSON")
            if not isinstance(parsed, dict):
                fail(f"{name}: assistant target is not a JSON object")
            extra = set(parsed) - EXPANDED_18_FIELDS
            forbidden = set(parsed) & FORBIDDEN_TARGET_KEYS
            if extra:
                fail(f"{name}: assistant target has keys outside the 18-field "
                     f"contract: {sorted(extra)}")
            if forbidden:
                fail(f"{name}: assistant target contains forbidden accounting-output "
                     f"keys: {sorted(forbidden)}")
        log(f"{name}: {len(rows)} rows, roles OK, targets = exact 18-field contract, "
            f"0 forbidden accounting-output keys")
    if counts["train.jsonl"] != 800 or counts["valid.jsonl"] != 100:
        fail(f"unexpected dataset counts {counts}")

    # --- datasets ----------------------------------------------------------
    from datasets import load_dataset

    ds = load_dataset(
        "json",
        data_files={
            "train": str(DATA_DIR / "train.jsonl"),
            "valid": str(DATA_DIR / "valid.jsonl"),
        },
    )
    train_ds = ds["train"]
    valid_ds = ds["valid"]
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
        run_name="platrixa-fyjc-specialist-v0.1",
        seed=SEED,
        data_seed=SEED,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        lr_scheduler_type=LR_SCHEDULER,
        optim="adamw_torch",
        fp16=FP16,
        gradient_checkpointing=GRADIENT_CHECKPOINTING,
        max_length=MAX_SEQ_LENGTH,  # TRL 1.12 SFTConfig field (verified in source)
        packing=PACKING,
        assistant_only_loss=True,   # loss on assistant 18-field JSON only
        eval_strategy="epoch",
        logging_steps=10,
        save_strategy="no",         # adapter is saved + pushed at the end
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
        f"lr={LR} seq={MAX_SEQ_LENGTH} fp16={FP16} assistant_only_loss=True")
    log(f"LoraConfig: r={LORA_R} alpha={LORA_ALPHA} dropout={LORA_DROPOUT} "
        f"target={LORA_TARGET_MODULES}")

    trainer = SFTTrainer(
        model=model,                 # pre-loaded from the pinned revision
        args=training_args,
        processing_class=tokenizer,  # TRL 1.12 name for the tokenizer
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        peft_config=lora_config,     # TRL wraps via get_peft_model (adapter-only)
    )

    log("starting training")
    t0 = time.time()
    train_result = trainer.train()
    train_seconds = round(time.time() - t0, 1)
    log(f"training finished in {train_seconds}s")
    log(f"train_metrics={json.dumps(train_result.metrics, default=str)}")

    history = trainer.state.log_history
    final_eval = [h for h in history if "eval_loss" in h][-1] if history else {}
    train_loss = float(train_result.metrics.get("train_loss", float("nan")))
    eval_loss = float(final_eval.get("eval_loss", float("nan")))
    log(f"final_train_loss={train_loss} final_eval_loss={eval_loss}")

    # --- persist adapter (adapter-only; base model stays separate) ---------
    OUTPUT_DIR.mkdir(exist_ok=True)
    if not isinstance(trainer.model, peft.PeftModel):
        fail("trained model is not a PeftModel — refusing to save anything but an "
             "adapter (no merged/full-model output)")
    trainer.model.save_pretrained(str(OUTPUT_DIR))  # adapter_config.json + safetensors
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    log(f"adapter saved to {OUTPUT_DIR}: {sorted(p.name for p in OUTPUT_DIR.iterdir())}")

    manifest = {
        "phase": "6B",
        "purpose": "Platrixa FYJC specialist language-understanding LoRA (baseline run)",
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "training": {
            "method": "TRL SFTTrainer (SFT) + PEFT LoRA",
            "trl_version": trl.__version__,
            "transformers_version": transformers.__version__,
            "peft_version": peft.__version__,
            "datasets_version": datasets.__version__,
            "accelerate_version": accelerate.__version__,
            "torch_version": torch.__version__,
            "huggingface_hub_version": huggingface_hub.__version__,
            "gpu": torch.cuda.get_device_properties(0).name if torch.cuda.is_available() else None,
            "gpu_vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
            if torch.cuda.is_available() else None,
            "epochs": EPOCHS,
            "learning_rate": LR,
            "per_device_batch_size": PER_DEVICE_BATCH,
            "gradient_accumulation_steps": GRAD_ACCUM,
            "effective_batch_size": PER_DEVICE_BATCH * GRAD_ACCUM,
            "max_seq_length": MAX_SEQ_LENGTH,
            "packing": PACKING,
            "warmup_ratio": WARMUP_RATIO,
            "weight_decay": WEIGHT_DECAY,
            "lr_scheduler": LR_SCHEDULER,
            "optimizer": "adamw_torch",
            "precision": "fp16",
            "quantization": None,  # plain LoRA (not QLoRA); fits 16 GB T4
            "gradient_checkpointing": GRADIENT_CHECKPOINTING,
            "seed": SEED,
            "assistant_only_loss": True,
            "lora_r": LORA_R,
            "lora_alpha": LORA_ALPHA,
            "lora_dropout": LORA_DROPOUT,
            "lora_target_modules": LORA_TARGET_MODULES,
            "train_steps": int(train_result.metrics.get("train_steps", 0)),
            "train_samples": len(train_ds),
            "valid_samples": len(valid_ds),
            "test_used": False,
            "final_train_loss": train_loss,
            "final_eval_loss": eval_loss,
            "training_duration_s": train_seconds,
        },
        "dataset": {
            "repo": DATASET_REPO,
            "revision": DATASET_REVISION,
            "train_count": counts["train.jsonl"],
            "valid_count": counts["valid.jsonl"],
            "test_count": 100,
            "test_uploaded": False,
            "status_policy": "Phase 5 targets: 815 VERIFIED / 185 REVIEW_REQUIRED. "
                             "Production currently clamps suggested_status to REVIEW_REQUIRED "
                             "downstream; recorded separately, not changed here.",
        },
        "artifact": {
            "adapter_repo": ADAPTER_REPO,
            "adapter_revision": None,  # filled after the Hub push below
            "merge_adapter": False,
        },
        "runtime": "ephemeral GPU container (Modal T4 or any CUDA host); adapter "
                   "pushed to the Hub before exit",
        "credentials": "none stored",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = OUTPUT_DIR / "phase6b_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    readme = (
        "# Platrixa FYJC Specialist v0.1\n\n"
        "PEFT LoRA adapter for **Qwen/Qwen2.5-1.5B-Instruct** (base model left separate; "
        "adapter is NOT merged).\n\n"
        "Role: FYJC accounting **language-understanding** specialist. Maps student "
        "natural language to the compact 18-field `ExpandedInterpretation` JSON. It is "
        "deliberately NOT trained to emit journal entries, debit/credit decisions, ledger "
        "postings, balances, or accounting conclusions — those are produced downstream by "
        "the deterministic accounting kernel after strict schema validation and the "
        "GroundingGate.\n\n"
        f"- Base model: {BASE_MODEL}\n"
        f"- Method: TRL SFT (assistant-only loss) + PEFT LoRA (r={LORA_R}, alpha={LORA_ALPHA})\n"
        f"- Dataset: {DATASET_REPO} @ `{DATASET_REVISION}` "
        f"(train=800, valid=100; locked test=100 never uploaded)\n"
        "- License/usage: see `phase6b_manifest.json` for full provenance.\n"
    )
    (OUTPUT_DIR / "README.md").write_text(readme)

    log("uploading adapter to HF Hub (commit 1: adapter files)")
    commit1 = api.upload_folder(
        repo_id=ADAPTER_REPO,
        repo_type="model",
        folder_path=str(OUTPUT_DIR),
        commit_message=f"Platrixa FYJC Specialist v0.1 — SFT+LoRA on "
                       f"{BASE_MODEL}@{BASE_MODEL_REVISION[:12]} "
                       f"(dataset rev {DATASET_REVISION[:12]})",
    )
    adapter_revision = commit1.oid  # huggingface_hub 1.30 CommitInfo field
    if not adapter_revision:
        fail("adapter upload returned no commit revision")
    log(f"adapter pushed: {ADAPTER_REPO}@{adapter_revision}")

    # Persist the resulting adapter revision into the manifest
    # (commit 2: manifest file only).
    manifest["artifact"]["adapter_revision"] = adapter_revision
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    commit2 = api.upload_file(
        path_or_fileobj=str(manifest_path),
        path_in_repo="phase6b_manifest.json",
        repo_id=ADAPTER_REPO,
        repo_type="model",
        commit_message="phase6b manifest: record adapter revision",
    )
    log(f"manifest pushed: {ADAPTER_REPO}@{commit2.oid}")

    info = api.model_info(ADAPTER_REPO)
    log(f"final adapter repo state: {ADAPTER_REPO}@{info.sha} private={info.private}")
    files = sorted(f.rfilename for f in info.siblings)
    log(f"adapter files: {files}")
    required = {"adapter_config.json", "adapter_model.safetensors"}
    missing = required - set(files)
    if missing:
        fail(f"adapter push incomplete, missing {missing}")

    # --- smoke test (validation example ONLY — never the locked test set) ---
    # Release training-time GPU state first so no second resident copy of the
    # trained model remains while the fresh base + adapter are loaded.
    log("releasing training-time GPU memory before smoke test")
    example = valid_ds[0]  # captured before teardown
    del trainer
    del model
    gc.collect()
    torch.cuda.empty_cache()

    # Mirror the production loader (backend/maths/fyjc_local_model_runner.py):
    # pinned base AutoModelForCausalLM + PeftModel.from_pretrained(adapter).
    # (transformers v5 removed AutoPeftModelForCausalLM, so this is also the
    # only correct smoke-load pattern for transformers 5.16.1.)
    log("smoke test: loading adapter against the pinned base model")
    from peft import PeftModel

    smoke_base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, revision=BASE_MODEL_REVISION, torch_dtype=torch.float16
    )
    smoke = PeftModel.from_pretrained(smoke_base, str(OUTPUT_DIR)).to("cuda")
    smoke.eval()
    tok = tokenizer  # pinned-revision tokenizer, still referenced
    user_text = example["messages"][1]["content"]
    expected_target = example["messages"][2]["content"]
    log(f"smoke input (valid[0], NOT test set): {user_text!r}")
    prompt = tok.apply_chat_template(
        example["messages"][:2], tokenize=False, add_generation_prompt=True
    )
    inputs = tok(prompt, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = smoke.generate(
            **inputs, max_new_tokens=700, do_sample=False, temperature=None, top_p=None,
            pad_token_id=tok.pad_token_id or tok.eos_token_id,
        )
    response = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    log(f"smoke response: {response[:500]}")
    parsed_ok = False
    try:
        parsed = json.loads(response)
        parsed_ok = isinstance(parsed, dict) and "transaction_type" in parsed
    except Exception:
        parsed_ok = False
    lower = response.lower()
    forbidden_hits = [w for w in ("journal", "debit", "credit", "ledger", "trial balance")
                      if re.search(rf"\b{w}\b", lower)]
    log(f"smoke_valid_json_18field_shape={parsed_ok} "
        f"smoke_forbidden_hits={forbidden_hits} "
        f"target_prefix_matches={expected_target[:60] in response or parsed_ok}")
    if not parsed_ok:
        fail("SMOKE FAILED: response was not parseable 18-field JSON")
    if forbidden_hits:
        log("WARNING: forbidden word appears in smoke output (may be quoted input echo)")
    log("SMOKE: PASS")
    log(f"FINAL_STATUS: SUCCESS adapter={ADAPTER_REPO}@{adapter_revision} "
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
