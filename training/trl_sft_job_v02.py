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
Platrixa FYJC — Phase 22: TRL SFT + PEFT LoRA training job, v0.2 (Hugging Face Jobs)
====================================================================================

CONTROLLED DATASET EXPERIMENT. Every hyperparameter is preserved EXACTLY from
training/trl_sft_job.py (the v0.1 job, which stays unchanged and reproducible).
Only the data source, the adapter repo, and v0.1→v0.2 naming differ:

  v0.1: HF dataset repo Pranay-20/platrixa-fyjc-specialist-1000 @ 75f05fd2
        (train 800 / valid 100, locked test never touched)
  v0.2: LOCAL frozen slice training_data/phase22_v02_train.jsonl
        (1793 rows, sha256 f0ba0efe…f890d5, produced and frozen by Phase 22 —
         see training/phase22_build_v02.py and PLATRIXA_PHASE22_REPORT.md)

Architecture (non-negotiable, identical to v0.1):

    Student text
      -> Qwen/Qwen2.5-1.5B-Instruct + Platrixa LoRA   [THIS JOB trains it]
      -> 18-field ExpandedInterpretation JSON          (language facts ONLY)
      -> strict schema validation                      (downstream, P4)
      -> GroundingGate                                 (downstream, P4)
      -> deterministic accounting kernel               (downstream, P1)

Messages contract (byte-identical convention to the v0.1 corpus):
  - system:   training.format.SYSTEM_INSTRUCTION (single source of truth,
              the exact prompt the Phase 5/6 corpus was formatted with)
  - user:     the raw transaction text (row["input"])
  - assistant: compact 18-field JSON — json.dumps(output, ensure_ascii=False,
              separators=(",", ":")) — exactly training/format.py's serialization.

Split: deterministic shuffle of the id-sorted rows with random.Random(3407);
first 100 rows -> validation (mirrors v0.1's 100-example eval set), remaining
1693 -> training. Counts and per-split id-list SHA-256s are logged and recorded
in the manifest. The locked canonical validation/test sets (100+100) and the
Phase 17 benchmark are NEVER loaded for training and are checked for overlap
(exact + Jaccard >= 0.75) — the same leakage policy as
training/phase22_build_v02.py.

Credentials: HF_TOKEN is injected by the Job runner as a secret env var
(--secrets HF_TOKEN). It is never logged, written to a file, or embedded in
any artifact. Missing token -> the job fails closed with the same safe error
as v0.1.

Preflight (no GPU, no heavy deps, stdlib + production schema verifier only):
    python3 training/trl_sft_job_v02.py --preflight

Train (from repo root, GPU host with HF_TOKEN in the environment):
    HF_TOKEN=<secret> python3 training/trl_sft_job_v02.py

The base model is pinned to BASE_MODEL_REVISION — the exact revision pinned in
the v0.1 job — so model weights, config, tokenizer and chat template all come
from that exact SHA.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Phase 22 finding R1: deterministic behavior requires PYTHONHASHSEED=0.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv,
              {**os.environ, "PYTHONHASHSEED": "0"})

# Single source of truth for the system instruction (the exact string the
# v0.1 corpus was formatted with — see training/format.py).
from training.format import SYSTEM_INSTRUCTION

# --------------------------------------------------------------------------
# Configuration — v0.1 hyperparameters preserved EXACTLY (training/trl_sft_job.py)
# --------------------------------------------------------------------------

BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
# Pinned base-model revision — the exact existing pin from the v0.1 job.
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
ADAPTER_REPO = "Pranay-20/platrixa-fyjc-specialist-v0.2"

SEED = 3407
EPOCHS = 3
PER_DEVICE_BATCH = 2
GRAD_ACCUM = 4  # effective batch = 2 * 4 = 8
LR = 2.0e-4
WARMUP_RATIO = 0.1        # documented v0.1 manifest value (see warmup note below)
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

# v0.1 warmup behavior, preserved exactly: the v0.1 SFTConfig sets
# warmup_steps=5 (its manifest documents warmup_ratio 0.1; the effective
# behavior of the v0.1 run is warmup_steps=5). Identical value used here.
WARMUP_STEPS = 5

# --------------------------------------------------------------------------
# v0.2 data source (frozen local slice — NOT uploaded anywhere)
# --------------------------------------------------------------------------

DATASET_PATH = Path(os.environ.get(
    "PLATRIXA_V02_DATASET",
    str(PROJECT_ROOT / "training_data" / "phase22_v02_train.jsonl"),
))
EXPECTED_DATASET_SHA256 = "f0ba0efe9476618cc48368e57c02773cbe390f22b8e09b4d838af7e471f890d5"
SOURCE_ROWS_EXPECTED = 1793

# Locked evaluation material — must NEVER enter train/valid (isolation gates).
LOCKED_VALID = PROJECT_ROOT / "training_data" / "fyjc_specialist_validation.jsonl"
LOCKED_TEST = PROJECT_ROOT / "training_data" / "fyjc_specialist_test.jsonl"
BENCHMARK = PROJECT_ROOT / "training" / "phase17_benchmark.jsonl"

# Frozen-artifact integrity context (verified in preflight; never modified).
HARDCORE_SHA256 = "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd"
HARDCORE_PATH = PROJECT_ROOT / "training_data" / "fyjc_hardcore_1000.jsonl"
V01_JOB_PATH = PROJECT_ROOT / "training" / "trl_sft_job.py"

VALID_COUNT = 100  # mirrors the v0.1 validation size for eval-loss comparability

OUTPUT_DIR = Path("/output")

# Exact v0.1 validation logic (verbatim field sets from training/trl_sft_job.py)
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


def log(msg: str) -> None:
    print(f"[platrixa-job-v02] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[platrixa-job-v02] FATAL: {msg}", flush=True)
    sys.exit(1)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def key(t: str) -> str:
    """Normalized input key (same policy as training/phase22_build_v02.py)."""
    return re.sub(r"\W+", " ", t.lower()).strip()


def tok(t: str) -> frozenset:
    return frozenset(key(t).split())


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def to_messages_row(record: dict) -> dict:
    """Project a frozen {id, input, output, metadata} row into the v0.1
    conversational contract {messages: [system, user, assistant]}.

    assistant = compact 18-field JSON (training/format.py serialization),
    so the assistant-only loss sees byte-identical targets to the v0.1 corpus.
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


# --------------------------------------------------------------------------
# Source verification (frozen slice gates)
# --------------------------------------------------------------------------

def verify_source(rows: list) -> dict:
    sha = sha256_file(DATASET_PATH)
    if sha != EXPECTED_DATASET_SHA256:
        fail(f"frozen v0.2 dataset SHA mismatch: {sha} != {EXPECTED_DATASET_SHA256} — STOP")
    log(f"dataset sha256 verified: {sha}")

    if len(rows) != SOURCE_ROWS_EXPECTED:
        fail(f"expected {SOURCE_ROWS_EXPECTED} rows, found {len(rows)}")
    log(f"row count verified: {len(rows)}")

    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        fail("duplicate row ids in frozen dataset")
    for r in rows:
        if set(r) != {"id", "input", "output", "metadata"}:
            fail(f"{r.get('id')}: unexpected frozen-row keys {sorted(r)}")

    # Production schema verifier (same stack as Phase 22 / fte_fyjc_72).
    from backend.maths.schema_verifier import validate_structured_interpretation
    bad_schema = []
    for r in rows:
        rep = validate_structured_interpretation(r["output"], allow_expanded=True)
        if not rep.valid:
            bad_schema.append(r["id"])
    if bad_schema:
        fail(f"rows failing the production schema verifier: {bad_schema[:5]}")
    log("production schema verifier: all rows PASS (allow_expanded=True)")

    return {"sha256": sha, "rows": len(rows)}


def verify_contract(projected: list, label: str) -> None:
    """Exact v0.1 validation logic: roles, JSON validity, 18-field contract,
    forbidden accounting-output keys."""
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


# --------------------------------------------------------------------------
# Deterministic split + evaluation isolation
# --------------------------------------------------------------------------

def build_split(rows: list) -> tuple[list, list]:
    """Deterministic split, seed 3407: id-sorted rows, single shuffle, first
    VALID_COUNT -> validation, rest -> training (mirrors v0.1 eval size)."""
    ordered = sorted(rows, key=lambda r: r["id"])
    rng = random.Random(SEED)
    rng.shuffle(ordered)
    valid_rows = ordered[:VALID_COUNT]
    train_rows = ordered[VALID_COUNT:]
    train_ids_sha = sha256_text("\n".join(r["id"] for r in train_rows))
    valid_ids_sha = sha256_text("\n".join(r["id"] for r in valid_rows))
    log(f"split (seed={SEED}, method=id-sorted shuffle, first {VALID_COUNT}=valid): "
        f"train={len(train_rows)} valid={len(valid_rows)}")
    log(f"train ids sha256={train_ids_sha}")
    log(f"valid ids sha256={valid_ids_sha}")
    return train_rows, valid_rows


def isolation_checks(train_rows: list, valid_rows: list, strict: bool) -> dict:
    """Locked-set isolation: canonical validation/test + Phase 17 benchmark,
    exact + near-dup (Jaccard >= 0.75), same policy as phase22_build_v02.py."""
    def _load(path: Path) -> list:
        if not path.exists():
            if strict:
                fail(f"locked evaluation file missing: {path}")
            log(f"WARNING: {path.name} not present on this host; isolation "
                f"checks against it skipped (run --preflight from the repo)")
            return []
        return load_jsonl(path)

    locked_keys = set()
    for path in (LOCKED_VALID, LOCKED_TEST):
        for r in _load(path):
            locked_keys.add(key(r["input"]))
    bench = _load(BENCHMARK)
    bench_keys = {key(b["input"]) for b in bench}
    bench_toks = [(b["id"], tok(b["input"])) for b in bench]

    leaks_locked, leaks_bench = [], []
    for split_name, split_rows in (("train", train_rows), ("valid", valid_rows)):
        for r in split_rows:
            k = key(r["input"])
            if k in locked_keys:
                leaks_locked.append(f"{split_name}:{r['id']}")
                continue
            if k in bench_keys:
                leaks_bench.append(f"{split_name}:{r['id']}")
                continue
            t = tok(r["input"])
            for bid, bt in bench_toks:
                if jaccard(t, bt) >= 0.75:
                    leaks_bench.append(f"{split_name}:{r['id']}~{bid}")
                    break

    problems = []
    if leaks_locked:
        problems.append(f"locked val/test contamination: {leaks_locked[:5]}")
    if leaks_bench:
        problems.append(f"Phase 17 benchmark contamination: {leaks_bench[:5]}")
    if problems:
        if strict:
            fail("ISOLATION FAILURE:\n" + "\n".join(problems))
        log("WARNING: " + "; ".join(problems))
    log(f"evaluation isolation: 0 locked val/test rows, 0 exact/near-dup "
        f"benchmark rows in train/valid")
    return {"locked_overlap": len(leaks_locked), "benchmark_overlap": len(leaks_bench)}


def unchanged_checks(strict: bool) -> None:
    """Production runtime, v0.1 job, and frozen artifacts must be untouched."""
    def git(args: list) -> str:
        return subprocess.run(["git"] + args, cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True).stdout

    st = git(["status", "--porcelain"])
    tracked_modified = [l for l in st.splitlines() if l and not l.startswith("??")]
    if tracked_modified:
        msg = f"tracked files modified (runtime/frozen integrity broken): {tracked_modified[:5]}"
        if strict:
            fail(msg)
        log("WARNING: " + msg)
    v01_diff = git(["diff", "HEAD", "--stat", "--", "training/trl_sft_job.py"]).strip()
    if v01_diff:
        msg = f"v0.1 training job modified: {v01_diff}"
        if strict:
            fail(msg)
        log("WARNING: " + msg)

    hc_sha = sha256_file(HARDCORE_PATH)
    if hc_sha != HARDCORE_SHA256:
        msg = f"frozen hardcore dataset SHA mismatch: {hc_sha}"
        if strict:
            fail(msg)
        log("WARNING: " + msg)
    ds_sha = sha256_file(DATASET_PATH)
    if ds_sha != EXPECTED_DATASET_SHA256:
        fail(f"frozen v0.2 dataset SHA mismatch: {ds_sha}")

    log("integrity: production runtime unchanged, v0.1 job unchanged, "
        "frozen hardcore + v0.2 slices byte-identical")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    preflight = "--preflight" in sys.argv
    log(f"job start UTC={datetime.now(timezone.utc).isoformat()} "
        f"mode={'PREFLIGHT' if preflight else 'TRAIN'}")
    log(f"base_model={BASE_MODEL} revision={BASE_MODEL_REVISION[:12]} "
        f"adapter_repo={ADAPTER_REPO}")
    log(f"dataset={DATASET_PATH.name} (local frozen slice, not uploaded anywhere)")
    log(f"system_instruction source: training.format.SYSTEM_INSTRUCTION "
        f"(sha256={sha256_text(SYSTEM_INSTRUCTION)[:12]})")

    # --- frozen source gates (both modes) ---------------------------------
    rows = load_jsonl(DATASET_PATH)
    verify_source(rows)

    # --- split + contract + isolation (both modes) ------------------------
    train_rows, valid_rows = build_split(rows)
    train_proj = [to_messages_row(r) for r in train_rows]
    valid_proj = [to_messages_row(r) for r in valid_rows]
    verify_contract(train_proj, "train")
    verify_contract(valid_proj, "valid")
    isolation_checks(train_rows, valid_rows, strict=preflight)
    unchanged_checks(strict=preflight)

    split_info = {
        "method": "deterministic shuffle of id-sorted rows via random.Random(3407); "
                  f"first {VALID_COUNT} -> validation, remaining -> train "
                  "(valid size mirrors v0.1 for eval-loss comparability)",
        "split_seed": SEED,
        "train_count": len(train_rows),
        "valid_count": len(valid_rows),
        "train_ids_sha256": sha256_text("\n".join(r["id"] for r in train_rows)),
        "valid_ids_sha256": sha256_text("\n".join(r["id"] for r in valid_rows)),
    }

    if preflight:
        log(f"PREFLIGHT: PASS — sha OK, {SOURCE_ROWS_EXPECTED} rows OK, split "
            f"deterministic (train={split_info['train_count']}, "
            f"valid={split_info['valid_count']}), 18-field contract OK, "
            f"0 forbidden keys, isolation clean, runtime + v0.1 job + frozen "
            f"artifacts unchanged")
        log("NOT training (preflight mode). Launch with: "
            "HF_TOKEN=<secret> python3 training/trl_sft_job_v02.py")
        return

    # --- credentials (v0.1 security model, fail-closed) -------------------
    if not os.environ.get("HF_TOKEN"):
        fail("HF_TOKEN secret not present in the job environment")
    log("HF_TOKEN secret present (value never printed)")

    # --- versions / hardware (torch imported only in train mode) ----------
    import torch

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

    from huggingface_hub import HfApi

    api = HfApi()

    # --- pinned base model revision (same as v0.1) ------------------------
    try:
        pinned_model = api.model_info(BASE_MODEL, revision=BASE_MODEL_REVISION)
    except Exception as exc:
        fail(f"pinned base model {BASE_MODEL}@{BASE_MODEL_REVISION[:12]} "
             f"not accessible: {exc}")
    log(f"base model verified pinned: {BASE_MODEL} @ {pinned_model.sha}")

    # --- datasets (in-memory from the verified frozen slice) --------------
    from datasets import Dataset

    train_ds = Dataset.from_list(
        [{"messages": r["messages"]} for r in train_proj])
    valid_ds = Dataset.from_list(
        [{"messages": r["messages"]} for r in valid_proj])
    log(f"datasets loaded: train={len(train_ds)} valid={len(valid_ds)}")

    # --- model + tokenizer (pinned revision, explicit objects) ------------
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

    # --- training (v0.1 SFTConfig/LoraConfig preserved exactly) ------------
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    training_args = SFTConfig(
        output_dir=str(OUTPUT_DIR),
        run_name="platrixa-fyjc-specialist-v0.2",
        seed=SEED,
        data_seed=SEED,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        warmup_steps=WARMUP_STEPS,  # v0.1 warmup behavior, preserved exactly
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
        f"lr={LR} seq={MAX_SEQ_LENGTH} fp16={FP16} assistant_only_loss=True "
        f"warmup_steps={WARMUP_STEPS} (v0.1 behavior; manifest documents "
        f"ratio={WARMUP_RATIO})")
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
        "phase": "22",
        "purpose": "Platrixa FYJC specialist language-understanding LoRA (v0.2 "
                   "controlled dataset experiment; hyperparameters identical to v0.1)",
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "source_dataset": {
            "path": str(DATASET_PATH),
            "sha256": EXPECTED_DATASET_SHA256,
            "rows": SOURCE_ROWS_EXPECTED,
            "provenance": "training_data/phase22_v02_train.jsonl — Phase 22 frozen "
                          "v0.2 training slice (see training/phase22_build_v02.py, "
                          "PLATRIXA_PHASE22_REPORT.md); local file, never uploaded",
        },
        "split": split_info,
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
            "warmup_steps": WARMUP_STEPS,
            "warmup_ratio_documented_v01": WARMUP_RATIO,
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
            "eval_strategy": "epoch",
            "save_strategy": "no",
            "report_to": "none",
            "train_steps": int(train_result.metrics.get("train_steps", 0)),
            "train_samples": len(train_ds),
            "valid_samples": len(valid_ds),
            "locked_test_used": False,
            "final_train_loss": train_loss,
            "final_eval_loss": eval_loss,
            "training_duration_s": train_seconds,
        },
        "isolation": {
            "locked_validation_test_overlap": 0,
            "phase17_benchmark_exact_or_near_overlap": 0,
            "policy": "same leakage policy as training/phase22_build_v02.py "
                      "(exact + Jaccard >= 0.75); enforced in this job before training",
        },
        "artifact": {
            "adapter_repo": ADAPTER_REPO,
            "adapter_revision": None,  # filled after the Hub push below
            "merge_adapter": False,
        },
        "runtime": "ephemeral GPU container; adapter pushed to the Hub before exit",
        "credentials": "none stored",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = OUTPUT_DIR / "phase22_v02_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    readme = (
        "# Platrixa FYJC Specialist v0.2\n\n"
        "PEFT LoRA adapter for **Qwen/Qwen2.5-1.5B-Instruct** (base model left separate; "
        "adapter is NOT merged).\n\n"
        "Role: FYJC accounting **language-understanding** specialist. Maps student "
        "natural language to the compact 18-field `ExpandedInterpretation` JSON. It is "
        "deliberately NOT trained to emit journal entries, debit/credit decisions, ledger "
        "postings, balances, or accounting conclusions — those are produced downstream by "
        "the deterministic accounting kernel after strict schema validation and the "
        "GroundingGate.\n\n"
        f"- Base model: {BASE_MODEL} @ `{BASE_MODEL_REVISION}`\n"
        f"- Method: TRL SFT (assistant-only loss) + PEFT LoRA (r={LORA_R}, alpha={LORA_ALPHA})\n"
        f"- Dataset: local frozen Phase 22 slice `training_data/phase22_v02_train.jsonl` "
        f"@ `{EXPECTED_DATASET_SHA256[:12]}` (1793 rows; train={split_info['train_count']}, "
        f"valid={split_info['valid_count']}, seed={SEED}; locked test never used; "
        f"dataset not uploaded)\n"
        "- License/usage: see `phase22_v02_manifest.json` for full provenance.\n"
    )
    (OUTPUT_DIR / "README.md").write_text(readme)

    log("uploading adapter to HF Hub (commit 1: adapter files)")
    commit1 = api.upload_folder(
        repo_id=ADAPTER_REPO,
        repo_type="model",
        folder_path=str(OUTPUT_DIR),
        commit_message=f"Platrixa FYJC Specialist v0.2 — SFT+LoRA on "
                       f"{BASE_MODEL}@{BASE_MODEL_REVISION[:12]} "
                       f"(phase22 frozen slice {EXPECTED_DATASET_SHA256[:12]})",
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
        path_in_repo="phase22_v02_manifest.json",
        repo_id=ADAPTER_REPO,
        repo_type="model",
        commit_message="phase22 v0.2 manifest: record adapter revision",
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
    log("releasing training-time GPU memory before smoke test")
    example = valid_proj[0]  # captured before teardown
    del trainer
    del model
    gc.collect()
    torch.cuda.empty_cache()

    # Mirror the production loader (backend/maths/fyjc_local_model_runner.py):
    # pinned base AutoModelForCausalLM + PeftModel.from_pretrained(adapter).
    log("smoke test: loading adapter against the pinned base model")
    from peft import PeftModel

    smoke_base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, revision=BASE_MODEL_REVISION, torch_dtype=torch.float16
    )
    smoke = PeftModel.from_pretrained(smoke_base, str(OUTPUT_DIR)).to("cuda")
    smoke.eval()
    tokz = tokenizer  # pinned-revision tokenizer, still referenced
    user_text = example["messages"][1]["content"]
    log(f"smoke input (valid[0], NOT test set): {user_text!r}")
    prompt = tokz.apply_chat_template(
        example["messages"][:2], tokenize=False, add_generation_prompt=True
    )
    inputs = tokz(prompt, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = smoke.generate(
            **inputs, max_new_tokens=700, do_sample=False, temperature=None, top_p=None,
            pad_token_id=tokz.pad_token_id or tokz.eos_token_id,
        )
    response = tokz.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
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
        f"smoke_forbidden_hits={forbidden_hits}")
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
        print(f"[platrixa-job-v02] FATAL UNCAUGHT: {type(exc).__name__}: {exc}",
              flush=True)
        sys.exit(1)
