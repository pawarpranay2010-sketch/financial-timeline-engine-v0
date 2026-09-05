#!/usr/bin/env python3
"""
Platrixa FYJC — Phase 6C Controlled Base vs Fine-Tuned Evaluation (hardened)
============================================================================

Compares Qwen2.5-1.5B-Instruct (base) against the same pinned base model +
Phase 6B LoRA adapter on the untouched 100-example Phase 5 test set.

This is the Phase 6C *preparation* deliverable. The actual 100-example
benchmark is executed manually (e.g. Google Colab free GPU) via
training/PHASE6C_COLAB.md. This script must never be run as part of the
repository test suite (the regression test mocks inference instead).

Guarantees (Phase 6C contract):
  * Identical prompts, decoding, JSON extraction and metrics for Base and
    Fine-Tuned — the ONLY difference between systems A and B is the LoRA
    adapter.
  * Model/adapter revisions are pinned constants; revision mismatch aborts.
  * Test set is read-only: SHA-256 + record count verified before and after.
  * No training, no test-set upload, no accounting output from the model.

Optimizations (safe, both systems identical):
  * max_new_tokens=512 — measured bound: the longest train/valid 18-field
    target tokenizes to 367 tokens with the pinned Qwen tokenizer (p99=364);
    512 leaves ~1.4x headroom. The previous 1024 could only add trailing
    garbage after the closing brace, which extract_json() never reads.
  * Deterministic greedy decoding (do_sample=False, no temperature/top_p
    forwarded) — the benchmark intent is reproducibility; stochastic
    temperature=0.1/top_p=0.95 sampling would make the comparison
    non-reproducible. temperature/top_p are no longer generation inputs.
  * Left-padded batched generation (BATCH_SIZE records per forward pass) with
    correct attention masks and per-example generated-token slicing; test
    record order is preserved 1:1. Batch size 8 (default) on T4 16GB;
    1 also works and is equivalent (padding is mathematically neutral for
    greedy decode with correct masks).
  * torch.inference_mode() + fp16 on CUDA (fp32 on CPU), no CPU<->GPU ping-pong.

Usage:
    python training/phase6c_evaluate.py --check-only        # config/data/adapter verification, NO model load
    python training/phase6c_evaluate.py                     # full run (base + fine-tuned)
    python training/phase6c_evaluate.py --base-only         # base model only
    python training/phase6c_evaluate.py --skip-inference    # recompute metrics from saved predictions
    python training/phase6c_evaluate.py --limit 5           # tiny smoke (does NOT touch the locked 100)

Environment:
    HF_TOKEN — required only for downloading the private adapter.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------- Pinned configuration (constants — never "latest") ----------

TEST_SET = "training_data/fyjc_specialist_test.jsonl"
BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
ADAPTER_REPO = "Pranay-20/platrixa-fyjc-specialist-v0.1"
ADAPTER_REVISION = "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"

# Locked benchmark hash recorded by Phase 5 (training/phase6_manifest.json).
# The evaluator refuses to run the benchmark if the file hash differs.
EXPECTED_TEST_SHA256 = "c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0"
EXPECTED_TEST_COUNT = 100

# Generation configuration — IDENTICAL for Base and Fine-Tuned.
MAX_NEW_TOKENS = 512  # measured bound: longest train/valid target = 367 tokens (p99=364)
BATCH_SIZE = int(os.environ.get("PHASE6C_BATCH_SIZE", "8"))
MAX_INPUT_TOKENS = 2048  # matches Phase 6B training max_length context
DO_SAMPLE = False  # deterministic greedy decoding — reproducible benchmark

SYSTEM_INSTRUCTION = (
    "You are the FYJC accounting language-understanding specialist. "
    "Parse the student's accounting language into a grounded structured "
    "interpretation with exactly these 18 fields: transaction_type, "
    "parties, amounts, payment_method, references, ambiguities, grounding, "
    "transaction_type_enum, payment_method_enum, ambiguity_flags, "
    "referenced_transaction_index, referenced_party, referenced_amount, "
    "field_confidences, overall_confidence, suggested_status, safety_flags, "
    "scope_flags. Do not invent missing information. Never produce journal "
    "entries, debit/credit decisions, or accounting conclusions. Output only "
    "machine-readable JSON with exactly these fields."
)

VALID_18_FIELDS = {
    "transaction_type", "parties", "amounts", "payment_method",
    "references", "ambiguities", "grounding",
    "transaction_type_enum", "payment_method_enum", "ambiguity_flags",
    "referenced_transaction_index", "referenced_party", "referenced_amount",
    "field_confidences", "overall_confidence", "suggested_status",
    "safety_flags", "scope_flags",
}

FORBIDDEN_FIELDS = {
    "journal", "journal_entry", "debit_lines", "credit_lines", "ledger",
    "balances", "debit_account", "credit_account", "debit", "credit",
}

ALLOWED_EXTRA_KEYS = {
    "raw_input", "interpretation_model", "_error", "_raw_response",
    "_raw_model_output", "_validation_errors", "_grounded",
    "_grounding_summary", "_grounding_issues",
}

VALID_TX = {
    "PURCHASE", "SALE", "PAYMENT", "RECEIPT", "CAPITAL", "EXPENSE",
    "RETURN_OUT", "RETURN_IN", "DISCOUNT_TRADE", "DISCOUNT_CASH",
    "SETTLEMENT", "GST", "DRAWING", "DEPRECIATION", "UNKNOWN",
}
VALID_PM = {"CASH", "BANK", "CHEQUE", "NEFT", "UPI", "CREDIT", "UNKNOWN"}

# Accounting-conclusion leakage patterns (evaluated on MODEL OUTPUT only).
# Bare "debit"/"credit" nouns appear legitimately in echoed input text
# ("Purchased goods on credit"), so they are only a leak when the phrase
# implies an accounting ACTION (an account being debited/credited).
LEAKAGE_PATTERNS = re.compile(
    r"\b(journal\s+entry|journal\s+entries|journalize[d]?|journalising|journalizing"
    r"|debit\s+(?:entry|side|column|posting)|credit\s+(?:entry|side|column|posting)"
    r"|ledger\s+(?:entry|account|posting)|posted\s+to\s+(?:the\s+)?ledger"
    r"|posting\s+to\s+(?:the\s+)?ledger|ledger posting"
    r"|trial\s+balance|balance\s+sheet"
    r"|debit_account|credit_account|debit_lines|credit_lines|journal_entry"
    r"|\w+(?:\s+\w+){0,3}\s+account\s+(?:is\s+|was\s+|were\s+|should\s+be\s+)?debit(?:ed|s)?"
    r"|\w+(?:\s+\w+){0,3}\s+account\s+(?:is\s+|was\s+|were\s+|should\s+be\s+)?credit(?:ed|s)?)\b",
    re.IGNORECASE,
)

OUTPUT_DIR = _PROJECT_ROOT / "training_data"
BASE_PRED_FILE = OUTPUT_DIR / "phase6c_base_predictions.jsonl"
FT_PRED_FILE = OUTPUT_DIR / "phase6c_finetuned_predictions.jsonl"
RESULTS_FILE = OUTPUT_DIR / "phase6c_results.json"
REPORT_FILE = _PROJECT_ROOT / "training" / "PHASE6C_FINAL_REPORT.md"


# ---------- Data ----------

def load_test(path: str) -> List[Dict[str, Any]]:
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))
    return recs


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_test_set(path: str) -> Tuple[List[Dict[str, Any]], str, int]:
    """Load the locked test set and refuse to run unless it is intact."""
    if not Path(path).exists():
        print(f"FATAL: locked test set not found: {path}")
        raise SystemExit(1)
    records = load_test(path)
    count = len(records)
    sha = sha256_file(path)
    if count != EXPECTED_TEST_COUNT:
        print(f"FATAL: locked test set must have exactly {EXPECTED_TEST_COUNT} records, found {count}.")
        raise SystemExit(1)
    if sha != EXPECTED_TEST_SHA256:
        print("FATAL: locked test set SHA-256 mismatch — the benchmark set has been altered.")
        print(f"  expected: {EXPECTED_TEST_SHA256}")
        print(f"  actual:   {sha}")
        raise SystemExit(1)
    return records, sha, count


# ---------- JSON extraction (identical for Base and Fine-Tuned) ----------

def extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    fence = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    m = fence.search(text)
    if m:
        text = m.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            parsed = json.loads(text[first:last + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------- Model + adapter access (pinned revisions) ----------

def resolve_adapter_dir() -> str:
    """Snapshot-download the adapter at the pinned revision; fail closed."""
    from huggingface_hub import snapshot_download

    print(f"  Downloading adapter {ADAPTER_REPO} @ {ADAPTER_REVISION[:12]}...")
    path = snapshot_download(
        ADAPTER_REPO,
        revision=ADAPTER_REVISION,  # pinned — never "main"
        token=os.environ.get("HF_TOKEN"),
    )
    # Verify the downloaded snapshot is really the pinned revision.
    from huggingface_hub import HfApi
    api = HfApi()
    info = api.repo_info(ADAPTER_REPO, repo_type="model", revision=ADAPTER_REVISION)
    local_sha = _dir_sha(path)
    _ = local_sha  # informational only; revision pin is enforced by snapshot_download
    del local_sha
    print(f"  Adapter cached at: {path} (hub commit {info.sha[:12]})")
    if info.sha != ADAPTER_REVISION:
        raise RuntimeError(
            f"Adapter revision mismatch: requested {ADAPTER_REVISION}, hub resolved {info.sha}"
        )
    return path


def _dir_sha(path: str) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ---------- Inference (batched, deterministic, CUDA-aware) ----------

def build_prompts(tokenizer: Any, records: List[Dict[str, Any]]) -> List[str]:
    """Apply the Qwen chat template exactly once per record — Base and LoRA alike."""
    prompts = []
    for rec in records:
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": rec.get("input", "")},
        ]
        prompts.append(
            tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        )
    return prompts


def run_inference(
    records: List[Dict[str, Any]],
    model_id: str = BASE_MODEL,
    revision: str = BASE_MODEL_REVISION,
    adapter_path: Optional[str] = None,
    batch_size: int = BATCH_SIZE,
    max_new_tokens: int = MAX_NEW_TOKENS,
) -> Tuple[List[Optional[Dict[str, Any]]], Dict[str, Any], Optional[str]]:
    """Batched greedy inference. Returns (predictions, timing_info, error)."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:
        return [], {}, f"torch/transformers not installed: {e}"

    timing: Dict[str, Any] = {
        "model_id": model_id,
        "revision": revision,
        "adapter": adapter_path or "none",
        "batch_size": batch_size,
        "max_new_tokens": max_new_tokens,
        "do_sample": DO_SAMPLE,
        "num_records": len(records),
    }

    try:
        has_cuda = torch.cuda.is_available()
        device = "cuda" if has_cuda else "cpu"
        dtype = torch.float16 if has_cuda else torch.float32
        timing["device"] = device
        timing["torch_dtype"] = "float16" if has_cuda else "float32"
        if has_cuda:
            timing["gpu_name"] = torch.cuda.get_device_name(0)
            timing["gpu_total_mem_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1e9, 2
            )
        timing["cuda_available"] = has_cuda

        # ---- Tokenizer (pinned revision, left-padding for batched decode) ----
        t_tok = time.time()
        tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=True,
        )
        tokenizer.padding_side = "left"  # batched generation requires left padding
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        timing["tokenizer_load_seconds"] = round(time.time() - t_tok, 2)
        print(f"  Tokenizer loaded ({model_id} @ {revision[:12]}) in {timing['tokenizer_load_seconds']}s")

        # ---- Base model (pinned revision, fp16 on CUDA / fp32 on CPU) ----
        t_model = time.time()
        print(f"  Loading model {model_id} @ {revision[:12]} (device={device}, dtype={timing['torch_dtype']})...")
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            revision=revision,
            dtype=dtype,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
        model.to(device)
        timing["model_load_seconds"] = round(time.time() - t_model, 2)
        print(f"  Model loaded in {timing['model_load_seconds']}s")

        # ---- LoRA adapter (pinned local snapshot at pinned hub revision) ----
        if adapter_path:
            from peft import PeftModel
            t_adapter = time.time()
            print(f"  Attaching LoRA adapter from {adapter_path}...")
            model = PeftModel.from_pretrained(model, adapter_path)
            model.to(device)
            timing["adapter_load_seconds"] = round(time.time() - t_adapter, 2)
            print(f"  Adapter attached in {timing['adapter_load_seconds']}s")

        model.eval()

        # ---- Batched generation ----
        t_inf = time.time()
        prompts = build_prompts(tokenizer, records)
        predictions: List[Optional[Dict[str, Any]]] = []
        num_batches = (len(prompts) + batch_size - 1) // batch_size

        with torch.inference_mode():
            for b in range(num_batches):
                chunk = prompts[b * batch_size:(b + 1) * batch_size]
                inputs = tokenizer(
                    chunk,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=MAX_INPUT_TOKENS,
                ).to(device)

                gen_kwargs: Dict[str, Any] = {
                    "max_new_tokens": max_new_tokens,
                    "pad_token_id": tokenizer.pad_token_id,
                }
                if DO_SAMPLE:
                    gen_kwargs.update({"do_sample": True, "temperature": 0.1, "top_p": 0.95})
                else:
                    gen_kwargs["do_sample"] = False  # deterministic greedy

                outputs = model.generate(**inputs, **gen_kwargs)

                # Slice only the newly generated tokens per example (left-padded
                # batch: input length is uniform, so column slicing is exact).
                input_len = inputs["input_ids"].shape[1]
                for row in range(outputs.shape[0]):
                    new_tokens = outputs[row][input_len:]
                    raw = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                    predictions.append(extract_json(raw))

                done = min((b + 1) * batch_size, len(records))
                if done % 10 < batch_size or done == len(records):
                    elapsed = time.time() - t_inf
                    rate = done / elapsed if elapsed > 0 else 0.0
                    eta = (len(records) - done) / rate if rate > 0 else 0.0
                    print(f"    ...{done}/{len(records)} ({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, {rate:.2f} ex/s)")

        timing["inference_seconds"] = round(time.time() - t_inf, 2)
        timing["seconds_per_example"] = round(timing["inference_seconds"] / max(1, len(records)), 3)
        timing["examples_per_second"] = round(len(records) / max(1e-9, timing["inference_seconds"]), 3)
        if has_cuda:
            timing["gpu_peak_mem_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 3)
            timing["gpu_current_mem_gb"] = round(torch.cuda.memory_allocated() / 1e9, 3)

        # ---- Release model memory before returning ----
        del model
        del tokenizer
        gc.collect()
        if has_cuda:
            torch.cuda.empty_cache()

        return predictions, timing, None
    except Exception as e:  # noqa: BLE001
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        return [], timing, f"inference failed: {e}"


def save_predictions(predictions: List[Optional[Dict[str, Any]]], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False, default=str) + "\n")


def load_predictions(path: Path) -> List[Optional[Dict[str, Any]]]:
    preds: List[Optional[Dict[str, Any]]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                val = json.loads(line)
                preds.append(val if val is not None else None)
    return preds


# ---------- Scoring helpers ----------

def _norm_tx(pred: Dict[str, Any]) -> str:
    return str(pred.get("transaction_type_enum") or pred.get("transaction_type") or "").upper().strip()


def _norm_pm(pred: Dict[str, Any]) -> str:
    return str(pred.get("payment_method_enum") or pred.get("payment_method") or "").upper().strip()


def _amount_value(item: Any) -> str:
    if isinstance(item, dict):
        raw = str(item.get("value", ""))
    else:
        raw = str(item)
    match = re.search(r"(\d+(?:\.\d+)?)", raw.replace(",", ""))
    return match.group(1) if match else raw.strip()


def _norm_amounts(amounts: Any) -> Counter:
    return Counter(_amount_value(a) for a in (amounts or []) if _amount_value(a))


def _norm_names(parties: Any) -> set:
    return {str(p).strip().lower() for p in (parties or []) if str(p).strip()}


def _has_ambiguity(out: Dict[str, Any]) -> bool:
    flags = out.get("ambiguity_flags") or []
    return any(f != "NONE" for f in flags)


def _norm_status(pred: Dict[str, Any]) -> str:
    return str(pred.get("suggested_status", "")).upper().strip()


# ---------- Per-example scoring ----------

def score_one(pred: Optional[Dict[str, Any]], gt: Dict[str, Any], raw_input: str) -> Dict[str, Any]:
    s: Dict[str, Any] = {
        "parse_ok": False,
        "schema_valid": False,
        "unknown_fields": [],
        "missing_fields": [],
        "forbidden_fields_in_output": [],
        "tx_correct": False,
        "tx_exact_match": False,
        "parties_exact": False,
        "party_precision": 0.0,
        "party_recall": 0.0,
        "party_f1": 0.0,
        "amounts_exact": False,
        "payment_correct": False,
        "ambiguity_agree": False,
        "status_agree": False,
        "grounding_compatible": False,
        "accounting_leakage": False,
        "leakage_type": "none",
    }

    if not isinstance(pred, dict):
        return s
    if pred.get("suggested_status") == "MODEL_NOT_AVAILABLE":
        return s

    s["parse_ok"] = True

    pred_keys = set(pred.keys())
    extra = pred_keys - VALID_18_FIELDS - ALLOWED_EXTRA_KEYS
    s["unknown_fields"] = sorted(extra)
    missing = VALID_18_FIELDS - pred_keys
    s["missing_fields"] = sorted(missing)
    s["schema_valid"] = len(missing) == 0 and not extra

    forbidden = FORBIDDEN_FIELDS & pred_keys
    s["forbidden_fields_in_output"] = sorted(forbidden)

    # Semantic comparisons
    s["tx_correct"] = _norm_tx(pred) == _norm_tx(gt)
    s["tx_exact_match"] = s["tx_correct"]

    pred_p = _norm_names(pred.get("parties"))
    gt_p = _norm_names(gt.get("parties"))
    s["parties_exact"] = pred_p == gt_p
    inter = len(pred_p & gt_p)
    s["party_precision"] = inter / max(1, len(pred_p))
    s["party_recall"] = inter / max(1, len(gt_p))
    if pred_p or gt_p:
        s["party_f1"] = round(
            2 * s["party_precision"] * s["party_recall"] / max(1e-9, s["party_precision"] + s["party_recall"]), 4
        )
    else:
        s["party_f1"] = 1.0

    s["amounts_exact"] = _norm_amounts(pred.get("amounts")) == _norm_amounts(gt.get("amounts"))
    s["payment_correct"] = _norm_pm(pred) == _norm_pm(gt)

    s["ambiguity_agree"] = _has_ambiguity(pred) == _has_ambiguity(gt)
    s["status_agree"] = _norm_status(pred) == _norm_status(gt)

    grounding = pred.get("grounding")
    s["grounding_compatible"] = (
        isinstance(grounding, dict)
        and isinstance(pred.get("field_confidences"), list)
        and pred.get("suggested_status") in {"VERIFIED", "REVIEW_REQUIRED"}
    )

    # Accounting leakage — evaluated on MODEL OUTPUT text only. A phrase that
    # also appears verbatim in the student's INPUT is a legitimate echo.
    pred_text = json.dumps(pred, ensure_ascii=False)
    forbidden_keys_present = FORBIDDEN_FIELDS & pred_keys
    if forbidden_keys_present:
        s["accounting_leakage"] = True
        s["leakage_type"] = "forbidden_keys"
    elif LEAKAGE_PATTERNS.search(pred_text):
        input_lower = raw_input.lower()
        leak = False
        for match in LEAKAGE_PATTERNS.finditer(pred_text):
            phrase = match.group().lower()
            if phrase in input_lower:
                continue  # legitimate echo of the student's own words
            leak = True
            break
        s["accounting_leakage"] = leak
        s["leakage_type"] = "generated_conclusion" if leak else "input_echo"
    else:
        s["leakage_type"] = "clean"

    return s


# ---------- Aggregation ----------

def aggregate_scores(
    predictions: List[Optional[Dict[str, Any]]],
    records: List[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    n = len(records)
    counters: Dict[str, int] = {
        "parse_ok": 0, "schema_valid": 0, "tx_correct": 0,
        "parties_exact": 0, "amounts_exact": 0, "payment_correct": 0,
        "ambiguity_agree": 0, "status_agree": 0, "grounding_compatible": 0,
        "forbidden_field_records": 0, "unknown_field_records": 0,
        "accounting_leakage_records": 0, "leakage_forbidden_keys": 0,
        "leakage_generated": 0, "leakage_input_echo": 0, "leakage_clean": 0,
    }
    party_f1_sum = 0.0
    party_prec_sum = 0.0
    party_rec_sum = 0.0
    by_difficulty: Dict[str, Dict[str, Any]] = {}
    by_category: Dict[str, Dict[str, Any]] = {}
    by_style: Dict[str, Dict[str, Any]] = {}
    field_error_counts: Counter = Counter()

    for pred, rec in zip(predictions, records):
        gt = rec.get("output", {})
        raw_input = rec.get("input", "")
        s = score_one(pred, gt, raw_input)

        for k in ("parse_ok", "schema_valid", "tx_correct", "parties_exact",
                  "amounts_exact", "payment_correct", "ambiguity_agree",
                  "status_agree", "grounding_compatible"):
            if s[k]:
                counters[k] += 1

        party_f1_sum += s["party_f1"]
        party_prec_sum += s["party_precision"]
        party_rec_sum += s["party_recall"]

        if s["forbidden_fields_in_output"]:
            counters["forbidden_field_records"] += 1
        if s["unknown_fields"]:
            counters["unknown_field_records"] += 1
        if s["accounting_leakage"]:
            counters["accounting_leakage_records"] += 1
        lt = s["leakage_type"]
        counters[f"leakage_{lt.replace(' ', '_')}"] = counters.get(f"leakage_{lt.replace(' ', '_')}", 0) + 1

        meta = rec.get("metadata", {})
        diff = meta.get("difficulty", "unknown")
        cat = meta.get("category", "unknown")
        style = meta.get("language_style", "unknown")

        for bucket_dict, bucket_key in [(by_difficulty, diff), (by_category, cat), (by_style, style)]:
            b = bucket_dict.setdefault(bucket_key, {"total": 0, "parse_ok": 0, "schema_valid": 0,
                                                    "tx_correct": 0, "amounts_exact": 0,
                                                    "payment_correct": 0, "party_f1_sum": 0.0,
                                                    "ambiguity_agree": 0, "status_agree": 0})
            b["total"] += 1
            for k in ("parse_ok", "schema_valid", "tx_correct", "amounts_exact",
                      "payment_correct", "ambiguity_agree", "status_agree"):
                if s[k]:
                    b[k] += 1
            b["party_f1_sum"] += s["party_f1"]

        for f in s["missing_fields"]:
            field_error_counts[f"missing_{f}"] += 1
        for f in s["unknown_fields"]:
            field_error_counts[f"unknown_{f}"] += 1

    rates = {
        "valid_json_rate": round(counters["parse_ok"] / n, 4),
        "valid_18field_schema_rate": round(counters["schema_valid"] / n, 4),
        "unknown_field_rate": round(counters["unknown_field_records"] / n, 4),
        "forbidden_field_rate": round(counters["forbidden_field_records"] / n, 4),
        "accounting_leakage_rate": round(counters["accounting_leakage_records"] / n, 4),
        "transaction_type_accuracy": round(counters["tx_correct"] / n, 4),
        "party_exact_accuracy": round(counters["parties_exact"] / n, 4),
        "party_token_f1": round(party_f1_sum / n, 4),
        "party_precision_avg": round(party_prec_sum / n, 4),
        "party_recall_avg": round(party_rec_sum / n, 4),
        "amount_extraction_accuracy": round(counters["amounts_exact"] / n, 4),
        "payment_method_accuracy": round(counters["payment_correct"] / n, 4),
        "ambiguity_detection_agreement": round(counters["ambiguity_agree"] / n, 4),
        "suggested_status_agreement": round(counters["status_agree"] / n, 4),
        "grounding_compatibility_rate": round(counters["grounding_compatible"] / n, 4),
        "suggested_status_review_required_rate": round(
            sum(1 for p in predictions if isinstance(p, dict) and _norm_status(p) == "REVIEW_REQUIRED") / n, 4
        ),
    }

    # Full semantic exact match (tx + parties + amounts + payment all agree)
    exact_matches = 0
    for pred, rec in zip(predictions, records):
        gt = rec.get("output", {})
        if not isinstance(pred, dict):
            continue
        if (_norm_tx(pred) == _norm_tx(gt)
                and _norm_names(pred.get("parties")) == _norm_names(gt.get("parties"))
                and _norm_amounts(pred.get("amounts")) == _norm_amounts(gt.get("amounts"))
                and _norm_pm(pred) == _norm_pm(gt)):
            exact_matches += 1
    rates["full_semantic_exact_match"] = round(exact_matches / n, 4)

    diff_rates = {}
    for diff, counts in by_difficulty.items():
        t = counts["total"]
        diff_rates[diff] = {
            "total": t,
            "parse_ok_rate": round(counts["parse_ok"] / t, 4) if t else 0,
            "schema_valid_rate": round(counts["schema_valid"] / t, 4) if t else 0,
            "tx_accuracy": round(counts["tx_correct"] / t, 4) if t else 0,
            "amount_accuracy": round(counts["amounts_exact"] / t, 4) if t else 0,
            "payment_accuracy": round(counts["payment_correct"] / t, 4) if t else 0,
            "party_f1": round(counts["party_f1_sum"] / t, 4) if t else 0,
            "ambiguity_agree": round(counts["ambiguity_agree"] / t, 4) if t else 0,
            "status_agree": round(counts["status_agree"] / t, 4) if t else 0,
        }

    cat_rates = {}
    for cat, counts in by_category.items():
        t = counts["total"]
        cat_rates[cat] = {
            "total": t,
            "tx_accuracy": round(counts["tx_correct"] / t, 4) if t else 0,
            "amount_accuracy": round(counts["amounts_exact"] / t, 4) if t else 0,
            "party_f1": round(counts["party_f1_sum"] / t, 4) if t else 0,
        }

    return {
        "label": label,
        "total": n,
        "counts": counters,
        "rates": rates,
        "by_difficulty": diff_rates,
        "by_category": cat_rates,
        "field_error_counts": dict(field_error_counts),
    }


# ---------- Hallucination audit ----------

def audit_hallucinations(
    predictions: List[Optional[Dict[str, Any]]],
    records: List[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    """Check for invented parties, amounts, currencies, payment methods, references."""
    invented_parties = 0
    invented_amounts = 0
    invented_payment_methods = 0
    invented_references = 0
    invented_currencies = 0
    unsupported_certainty = 0
    failed_ambiguity_preservation = 0
    details = []

    for pred, rec in zip(predictions, records):
        gt = rec.get("output", {})
        raw_input = rec.get("input", "")
        if not isinstance(pred, dict):
            continue

        issues = []

        pred_parties = _norm_names(pred.get("parties"))
        gt_parties = _norm_names(gt.get("parties"))
        for p in pred_parties - gt_parties:
            if p not in raw_input.lower():
                invented_parties += 1
                issues.append(f"invented_party:{p}")

        pred_amts = _norm_amounts(pred.get("amounts"))
        gt_amts = _norm_amounts(gt.get("amounts"))
        for amt in pred_amts - gt_amts:
            if amt not in raw_input.replace(",", ""):
                invented_amounts += 1
                issues.append(f"invented_amount:{amt}")

        if _norm_pm(pred) != _norm_pm(gt):
            if _norm_pm(pred) != "UNKNOWN" and _norm_pm(pred).lower() not in raw_input.lower():
                invented_payment_methods += 1
                issues.append(f"invented_payment:{_norm_pm(pred)}")

        pred_refs = {r.lower().strip() for r in (pred.get("references") or [])}
        gt_refs = {r.lower().strip() for r in (gt.get("references") or [])}
        for ref in pred_refs - gt_refs:
            if ref not in raw_input.lower():
                invented_references += 1
                issues.append(f"invented_ref:{ref}")

        for amt_entry in (pred.get("amounts") or []):
            if isinstance(amt_entry, dict):
                currency = amt_entry.get("currency", "")
                if currency and currency.upper() not in raw_input.upper() and currency.upper() != "INR":
                    invented_currencies += 1
                    issues.append(f"invented_currency:{currency}")

        if _has_ambiguity(gt) and not _has_ambiguity(pred):
            failed_ambiguity_preservation += 1
            issues.append("failed_ambiguity_preservation")

        if _norm_status(pred) == "VERIFIED" and _norm_status(gt) == "REVIEW_REQUIRED":
            unsupported_certainty += 1
            issues.append("unsupported_VERIFIED_claim")

        if issues:
            details.append({
                "id": rec.get("id", ""),
                "input_preview": raw_input[:80],
                "issues": issues,
            })

    return {
        "label": label,
        "total_evaluated": sum(1 for p in predictions if isinstance(p, dict)),
        "invented_parties": invented_parties,
        "invented_amounts": invented_amounts,
        "invented_payment_methods": invented_payment_methods,
        "invented_references": invented_references,
        "invented_currencies": invented_currencies,
        "unsupported_certainty_claims": unsupported_certainty,
        "failed_ambiguity_preservation": failed_ambiguity_preservation,
        "records_with_issues": len(details),
        "details": details,
    }


# ---------- Leakage audit ----------

def audit_leakage(
    predictions: List[Optional[Dict[str, Any]]],
    records: List[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    """Conservative accounting-conclusion leakage audit (output-only)."""
    true_leakage = 0
    input_echo = 0
    clean = 0
    cases = []

    for pred, rec in zip(predictions, records):
        raw_input = rec.get("input", "")
        if not isinstance(pred, dict):
            continue

        s = score_one(pred, rec.get("output", {}), raw_input)
        lt = s["leakage_type"]

        if lt == "forbidden_keys":
            true_leakage += 1
            cases.append({"id": rec.get("id", ""), "type": "forbidden_keys",
                          "fields": s["forbidden_fields_in_output"]})
        elif lt == "generated_conclusion":
            true_leakage += 1
            cases.append({"id": rec.get("id", ""), "type": "generated_conclusion"})
        elif lt == "input_echo":
            input_echo += 1
        elif lt == "clean":
            clean += 1

    total = true_leakage + input_echo + clean
    return {
        "label": label,
        "total": total,
        "true_leakage": true_leakage,
        "input_echo": input_echo,
        "clean": clean,
        "true_leakage_rate": round(true_leakage / max(1, total), 4),
        "cases": cases,
    }


# ---------- Grounding compatibility ----------

def check_grounding_compatibility(
    predictions: List[Optional[Dict[str, Any]]],
    records: List[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    """Grounding-dict presence, field_confidences, status validity (compat check only)."""
    has_grounding_dict = 0
    has_field_confidences = 0
    has_valid_status = 0
    has_inferred_fields_list = 0
    grounding_issues = 0
    details = []

    for pred, rec in zip(predictions, records):
        if not isinstance(pred, dict):
            continue

        g = pred.get("grounding")
        fc = pred.get("field_confidences")
        status = _norm_status(pred)

        if isinstance(g, dict):
            has_grounding_dict += 1
            if isinstance(g.get("inferred_fields"), list):
                has_inferred_fields_list += 1
        else:
            grounding_issues += 1
            details.append({"id": rec.get("id", ""), "issue": "missing_grounding_dict"})

        if isinstance(fc, list) and len(fc) > 0:
            has_field_confidences += 1

        if status in {"VERIFIED", "REVIEW_REQUIRED"}:
            has_valid_status += 1

    n = sum(1 for p in predictions if isinstance(p, dict))
    return {
        "label": label,
        "total": n,
        "has_grounding_dict": has_grounding_dict,
        "has_field_confidences": has_field_confidences,
        "has_valid_status": has_valid_status,
        "has_inferred_fields_list": has_inferred_fields_list,
        "grounding_issues": grounding_issues,
        "grounding_dict_rate": round(has_grounding_dict / max(1, n), 4),
        "field_confidences_rate": round(has_field_confidences / max(1, n), 4),
        "valid_status_rate": round(has_valid_status / max(1, n), 4),
        "details": details[:20],
    }


# ---------- Delta helpers ----------

def compute_deltas(base_metrics: Dict[str, Any], ft_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Absolute and percentage-point differences for every shared rate."""
    deltas: Dict[str, Any] = {"absolute": {}, "percentage_points": {}}
    for key, b in base_metrics["rates"].items():
        f = ft_metrics["rates"].get(key)
        if isinstance(b, (int, float)) and isinstance(f, (int, float)):
            deltas["absolute"][key] = round(f - b, 4)
            deltas["percentage_points"][key] = round((f - b) * 100, 2)
    return deltas


# ---------- Report generation ----------

def generate_report(
    base_metrics: Dict[str, Any],
    ft_metrics: Dict[str, Any],
    base_leakage: Dict[str, Any],
    ft_leakage: Dict[str, Any],
    base_hallucination: Dict[str, Any],
    ft_hallucination: Dict[str, Any],
    base_grounding: Dict[str, Any],
    ft_grounding: Dict[str, Any],
    deltas: Dict[str, Any],
    test_sha: str,
    test_count: int,
    base_timing: Dict[str, Any],
    ft_timing: Dict[str, Any],
) -> str:
    """Generate the human-readable Phase 6C final report."""
    lines: List[str] = []
    lines.append("# Phase 6C — Base vs Fine-Tuned Final Report\n")
    lines.append(f"**Date:** {time.strftime('%Y-%m-%d %H:%M UTC')}\n")

    lines.append("## Configuration\n")
    lines.append(f"- **Base model:** `{BASE_MODEL}` (revision `{BASE_MODEL_REVISION}`)")
    lines.append(f"- **Adapter:** `{ADAPTER_REPO}` (revision `{ADAPTER_REVISION}`)")
    lines.append(f"- **Test set:** `{TEST_SET}` — {test_count} examples, SHA-256: `{test_sha}`")
    lines.append(f"- **Decoding:** do_sample={DO_SAMPLE} (deterministic greedy), "
                 f"max_new_tokens={MAX_NEW_TOKENS}, identical for Base and Fine-Tuned")
    b_dev = base_timing.get("device", "?")
    f_dev = ft_timing.get("device", "?")
    lines.append(f"- **Hardware:** base on {b_dev}, fine-tuned on {f_dev}")
    if base_timing.get("gpu_name"):
        lines.append(f"- **GPU:** {base_timing['gpu_name']}")
    lines.append(f"- **Batch size:** {base_timing.get('batch_size', '?')} (both systems)")
    lines.append(f"- **Base inference:** {base_timing.get('inference_seconds', '?')}s "
                 f"({base_timing.get('seconds_per_example', '?')}s/example)")
    lines.append(f"- **Fine-tuned inference:** {ft_timing.get('inference_seconds', '?')}s "
                 f"({ft_timing.get('seconds_per_example', '?')}s/example)\n")

    metric_labels = [
        ("valid_json_rate", "Valid JSON rate"),
        ("valid_18field_schema_rate", "18-field schema validity"),
        ("unknown_field_rate", "Unknown-field rate"),
        ("forbidden_field_rate", "Forbidden-field rate"),
        ("accounting_leakage_rate", "Accounting leakage rate"),
        ("transaction_type_accuracy", "Transaction-type accuracy"),
        ("party_exact_accuracy", "Party exact-set accuracy"),
        ("party_token_f1", "Party token F1"),
        ("party_precision_avg", "Party precision (avg)"),
        ("party_recall_avg", "Party recall (avg)"),
        ("amount_extraction_accuracy", "Amount extraction accuracy"),
        ("payment_method_accuracy", "Payment-method accuracy"),
        ("ambiguity_detection_agreement", "Ambiguity detection agreement"),
        ("suggested_status_agreement", "suggested_status agreement"),
        ("grounding_compatibility_rate", "Grounding compatibility"),
        ("full_semantic_exact_match", "Full semantic exact match"),
    ]

    lines.append("## Core Metrics Comparison\n")
    lines.append("| Metric | Base | Fine-Tuned | Δ |")
    lines.append("|--------|-----:|-----------:|--:|")
    for key, label in metric_labels:
        b = base_metrics["rates"].get(key)
        f = ft_metrics["rates"].get(key)
        b_s = f"{b:.1%}" if isinstance(b, (int, float)) else "—"
        f_s = f"{f:.1%}" if isinstance(f, (int, float)) else "—"
        d_s = ""
        if isinstance(b, (int, float)) and isinstance(f, (int, float)):
            delta = f - b
            d_s = f"{delta:+.1%}"
            if delta > 0.02:
                d_s += " ✅"
            elif delta < -0.05:
                d_s += " ⚠️"
        lines.append(f"| {label} | {b_s} | {f_s} | {d_s} |")
    lines.append("")

    lines.append("## Difficulty Breakdown (Fine-Tuned)\n")
    lines.append("| Difficulty | N | Parse OK | Schema | TX Acc | Amount | Payment | Party F1 |")
    lines.append("|------------|--:|---------:|-------:|-------:|-------:|--------:|---------:|")
    for diff, data in sorted(ft_metrics.get("by_difficulty", {}).items()):
        lines.append(
            f"| {diff} | {data['total']} | {data['parse_ok_rate']:.1%} | "
            f"{data['schema_valid_rate']:.1%} | {data['tx_accuracy']:.1%} | "
            f"{data['amount_accuracy']:.1%} | {data['payment_accuracy']:.1%} | "
            f"{data['party_f1']:.3f} |"
        )
    lines.append("")

    lines.append("## Category Breakdown (Fine-Tuned)\n")
    lines.append("| Category | N | TX Acc | Amount | Party F1 |")
    lines.append("|----------|--:|-------:|-------:|---------:|")
    for cat, data in sorted(ft_metrics.get("by_category", {}).items()):
        lines.append(
            f"| {cat} | {data['total']} | {data['tx_accuracy']:.1%} | "
            f"{data['amount_accuracy']:.1%} | {data['party_f1']:.3f} |"
        )
    lines.append("")

    lines.append("## Safety / Leakage Comparison\n")
    lines.append("| Audit | Base | Fine-Tuned |")
    lines.append("|-------|-----:|-----------:|")
    lines.append(f"| True accounting leakage | {base_leakage['true_leakage']} / {base_leakage['total']} "
                 f"({base_leakage['true_leakage_rate']:.1%}) | {ft_leakage['true_leakage']} / {ft_leakage['total']} "
                 f"({ft_leakage['true_leakage_rate']:.1%}) |")
    lines.append(f"| Input echo (legitimate) | {base_leakage['input_echo']} | {ft_leakage['input_echo']} |")
    lines.append(f"| Clean outputs | {base_leakage['clean']} | {ft_leakage['clean']} |")
    lines.append(f"| Invented parties | {base_hallucination['invented_parties']} | {ft_hallucination['invented_parties']} |")
    lines.append(f"| Invented amounts | {base_hallucination['invented_amounts']} | {ft_hallucination['invented_amounts']} |")
    lines.append(f"| Invented payment methods | {base_hallucination['invented_payment_methods']} | {ft_hallucination['invented_payment_methods']} |")
    lines.append(f"| Invented references | {base_hallucination['invented_references']} | {ft_hallucination['invented_references']} |")
    lines.append(f"| Unsupported VERIFIED claims | {base_hallucination['unsupported_certainty_claims']} | {ft_hallucination['unsupported_certainty_claims']} |")
    lines.append(f"| Failed ambiguity preservation | {base_hallucination['failed_ambiguity_preservation']} | {ft_hallucination['failed_ambiguity_preservation']} |")
    if ft_leakage["cases"]:
        lines.append("\n**Fine-tuned leakage cases:**")
        for c in ft_leakage["cases"][:10]:
            lines.append(f"  - `{c['id']}`: {c['type']} {c.get('fields', '')}")
    lines.append("")

    lines.append("## Grounding Comparison\n")
    for name, g in [("Base", base_grounding), ("Fine-Tuned", ft_grounding)]:
        lines.append(f"### {name}\n")
        lines.append(f"- Grounding dict present: {g['grounding_dict_rate']:.1%}")
        lines.append(f"- field_confidences present: {g['field_confidences_rate']:.1%}")
        lines.append(f"- Valid suggested_status: {g['valid_status_rate']:.1%}")
        lines.append("")

    lines.append("## Per-Field Error Counts (Fine-Tuned)\n")
    if ft_metrics.get("field_error_counts"):
        lines.append("| Field | Error Count |")
        lines.append("|-------|------------:|")
        for field, count in sorted(ft_metrics["field_error_counts"].items(), key=lambda x: -x[1]):
            if count > 0:
                lines.append(f"| {field} | {count} |")
    else:
        lines.append("No per-field errors detected.")
    lines.append("")

    # Regression gate
    regressions = []
    improvements = []
    for key, label in metric_labels:
        b = base_metrics["rates"].get(key, 0)
        f = ft_metrics["rates"].get(key, 0)
        if isinstance(b, (int, float)) and isinstance(f, (int, float)):
            delta = f - b
            if delta < -0.05:
                regressions.append((label, b, f, delta))
            elif delta > 0.02:
                improvements.append((label, b, f, delta))

    lines.append("## Regression Gate\n")
    if regressions:
        lines.append("### Regressions (Δ < -5pp)\n")
        for label, b, f, d in regressions:
            lines.append(f"- **{label}**: {b:.1%} → {f:.1%} ({d:+.1%}) ⚠️")
    else:
        lines.append("### No material regressions detected ✅")
    if improvements:
        lines.append("\n### Improvements (Δ > +2pp)\n")
        for label, b, f, d in improvements:
            lines.append(f"- **{label}**: {b:.1%} → {f:.1%} ({d:+.1%}) ✅")
    lines.append("")

    # Final verdict — metric-based, never loss-based
    lines.append("## Final Verdict\n")
    ft_leak_rate = ft_leakage["true_leakage_rate"]
    ft_tx = ft_metrics["rates"].get("transaction_type_accuracy", 0)
    b_tx = base_metrics["rates"].get("transaction_type_accuracy", 0)
    ft_exact = ft_metrics["rates"].get("full_semantic_exact_match", 0)
    b_exact = base_metrics["rates"].get("full_semantic_exact_match", 0)

    safety_keys = ("leakage", "forbidden", "unknown_field")
    has_safety_regression = any(
        d < -0.05 for _, _, _, d in regressions
        if any(k in _[0].lower() for k in safety_keys)
    )
    material_improvement = (ft_tx > b_tx + 0.02) or (ft_exact > b_exact + 0.02)

    if ft_leak_rate > 0.05:
        verdict = "UNSAFE_REGRESSION"
        verdict_reason = f"Fine-tuned model has {ft_leak_rate:.1%} accounting leakage rate (>5%)"
    elif has_safety_regression:
        verdict = "REGRESSED"
        verdict_reason = "Material safety/contract regression detected"
    elif material_improvement:
        verdict = "IMPROVED"
        verdict_reason = (f"Semantic performance improved (TX: {b_tx:.1%}→{ft_tx:.1%}, "
                          f"exact: {b_exact:.1%}→{ft_exact:.1%}) without safety regression")
    elif ft_exact > b_exact + 0.01 or ft_tx > b_tx + 0.01:
        verdict = "PASS"
        verdict_reason = "Marginal improvement without safety regression"
    else:
        verdict = "NO_SIGNIFICANT_CHANGE"
        verdict_reason = "Differences too small to establish meaningful improvement"

    lines.append(f"### Verdict: **{verdict}**\n")
    lines.append(f"{verdict_reason}\n")
    lines.append(f"- Base full semantic exact match: {b_exact:.1%}")
    lines.append(f"- Fine-tuned full semantic exact match: {ft_exact:.1%}")
    lines.append(f"- Base TX accuracy: {b_tx:.1%}")
    lines.append(f"- Fine-tuned TX accuracy: {ft_tx:.1%}")
    lines.append(f"- Accounting leakage (FT): {ft_leak_rate:.1%}\n")

    lines.append("---\n")
    lines.append("Generated by `training/phase6c_evaluate.py` (Phase 6C — controlled benchmark)")
    return "\n".join(lines)


# ---------- Check-only mode (NO model load) ----------

def run_check_only(test_path: str, records: List[Dict[str, Any]], test_sha: str) -> int:
    """Verify data/config/dependencies/adapter WITHOUT loading any model."""
    failures: List[str] = []

    print("\n=== PHASE 6C CHECK-ONLY (no model load) ===\n")

    # 1. Test set
    print(f"[test set] path: {test_path}")
    print(f"[test set] records: {len(records)} (expected {EXPECTED_TEST_COUNT})")
    print(f"[test set] sha256: {test_sha}")
    if test_sha != EXPECTED_TEST_SHA256:
        failures.append("test set SHA mismatch vs locked benchmark hash")
    if len(records) != EXPECTED_TEST_COUNT:
        failures.append(f"test set count {len(records)} != {EXPECTED_TEST_COUNT}")

    # 2. Schema / input fields
    ok_outputs = all(isinstance(r.get("output"), dict) for r in records)
    ok_18 = all(set(r["output"]) >= VALID_18_FIELDS for r in records)
    ok_inputs = all(isinstance(r.get("input"), str) and r.get("input", "").strip() for r in records)
    ok_ids = all(r.get("id") for r in records)
    print(f"[schema] all outputs dicts: {ok_outputs}")
    print(f"[schema] all 18 contract fields present: {ok_18}")
    print(f"[schema] all inputs non-empty strings: {ok_inputs}")
    print(f"[schema] all ids present: {ok_ids}")
    if not (ok_outputs and ok_18 and ok_inputs and ok_ids):
        failures.append("schema/input-field verification failed")

    # 3. Duplicate ids (deterministic ordering prerequisite)
    ids = [r.get("id") for r in records]
    dupes = {i for i in ids if ids.count(i) > 1}
    print(f"[ids] duplicate ids: {len(dupes)}")
    if dupes:
        failures.append(f"duplicate test ids: {sorted(dupes)[:5]}")

    # 4. Forbidden accounting keys must NOT exist in targets
    forbidden_in_targets = sorted(
        {f for r in records for f in (set(r["output"]) & FORBIDDEN_FIELDS)}
    )
    print(f"[targets] forbidden accounting keys in targets: {forbidden_in_targets or 'NONE'}")
    if forbidden_in_targets:
        failures.append(f"targets contain forbidden accounting keys: {forbidden_in_targets}")

    # 5. No forbidden keys that would poison targets
    bad_status = sorted({r["output"].get("suggested_status") for r in records}
                        - {"VERIFIED", "REVIEW_REQUIRED"})
    print(f"[targets] unexpected suggested_status values: {bad_status or 'NONE'}")
    if bad_status:
        failures.append(f"unexpected suggested_status in targets: {bad_status}")

    # 6. Pinned configuration
    print(f"[config] BASE_MODEL: {BASE_MODEL}")
    print(f"[config] BASE_MODEL_REVISION: {BASE_MODEL_REVISION}")
    print(f"[config] ADAPTER_REPO: {ADAPTER_REPO}")
    print(f"[config] ADAPTER_REVISION: {ADAPTER_REVISION}")
    print(f"[config] generation: do_sample={DO_SAMPLE} max_new_tokens={MAX_NEW_TOKENS} "
          f"batch_size={BATCH_SIZE} max_input_tokens={MAX_INPUT_TOKENS}")

    # 7. Dependencies (imports only — no model load)
    try:
        import torch
        import transformers
        import peft
        import huggingface_hub
        print(f"[deps] torch={torch.__version__} transformers={transformers.__version__} "
              f"peft={peft.__version__} huggingface_hub={huggingface_hub.__version__} "
              f"cuda={torch.cuda.is_available()}"
              + (f" gpu={torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else ""))
    except ImportError as e:
        failures.append(f"missing dependency: {e}")
        print(f"[deps] MISSING: {e}")

    # 8. Adapter access at pinned revision (metadata only — no weights download)
    try:
        from huggingface_hub import HfApi
        info = HfApi().repo_info(ADAPTER_REPO, repo_type="model", revision=ADAPTER_REVISION)
        if info.sha != ADAPTER_REVISION:
            failures.append(f"adapter revision unresolved: {info.sha}")
        else:
            files = [s.rfilename for s in (info.siblings or [])]
            has_weights = any("adapter_model" in f for f in files)
            has_cfg = "adapter_config.json" in files
            print(f"[adapter] accessible at pinned revision: {info.sha[:12]}")
            print(f"[adapter] weights present: {has_weights}, adapter_config.json: {has_cfg}")
            if not (has_weights and has_cfg):
                failures.append("adapter snapshot incomplete (missing weights or config)")
    except Exception as e:  # noqa: BLE001
        failures.append(f"adapter not accessible at pinned revision: {e}")
        print(f"[adapter] NOT ACCESSIBLE: {e}")

    # 9. Output/report directories
    print(f"[outputs] results: {RESULTS_FILE}")
    print(f"[outputs] report: {REPORT_FILE}")
    if not OUTPUT_DIR.exists():
        failures.append(f"output dir missing: {OUTPUT_DIR}")

    # 10. Colab requirements
    print("[colab] runtime: Python 3.10+, GPU recommended (T4), HF_TOKEN via Colab Secrets")

    print("\n=== CHECK-ONLY RESULT ===")
    if failures:
        print("STATUS: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("STATUS: PASS — repository ready for the Colab benchmark run")
    return 0


# ---------- Hardware/dependency probe (non-fatal) ----------

def probe_hardware() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["gpu_count"] = torch.cuda.device_count()
    except ImportError:
        info["torch"] = "NOT INSTALLED"
        info["cuda_available"] = False
    try:
        import transformers
        info["transformers"] = transformers.__version__
    except ImportError:
        info["transformers"] = "NOT INSTALLED"
    try:
        import peft
        info["peft"] = peft.__version__
    except ImportError:
        info["peft"] = "NOT INSTALLED"
    return info


# ---------- Main ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6C: Base vs Fine-Tuned controlled evaluation")
    parser.add_argument("--test-set", default=str(_PROJECT_ROOT / TEST_SET))
    parser.add_argument("--base-only", action="store_true", help="evaluate only the base model")
    parser.add_argument("--skip-inference", action="store_true",
                        help="recompute metrics from saved prediction files")
    parser.add_argument("--check-only", action="store_true",
                        help="verify configuration/data/adapter WITHOUT loading any model")
    parser.add_argument("--limit", type=int, default=None,
                        help="tiny smoke limit (NEVER use on the locked 100; for plumbing tests only)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    # ---- Step 1: Locked test set integrity gate ----
    test_path = args.test_set
    records, test_sha, test_count = verify_test_set(str(Path(test_path).resolve()))
    print(f"\n{'=' * 64}")
    print("Phase 6C — Base vs Fine-Tuned Controlled Evaluation")
    print(f"{'=' * 64}")
    print(f"Test set: {test_path}")
    print(f"  Records: {test_count}")
    print(f"  SHA-256: {test_sha} (locked benchmark hash verified)")

    if args.check_only:
        sys.exit(run_check_only(test_path, records, test_sha))

    # ---- Smoke-limit guard: --limit must never touch the locked benchmark ----
    if args.limit:
        print(f"\nWARNING: --limit {args.limit} — SMOKE RUN ONLY, not a Phase 6C benchmark.")
        records = records[: args.limit]

    # ---- Hardware probe ----
    hw = probe_hardware()
    print(f"Hardware: python={hw['python']} torch={hw.get('torch')} "
          f"transformers={hw.get('transformers')} peft={hw.get('peft')} "
          f"cuda={hw.get('cuda_available')}"
          + (f" gpu={hw.get('gpu_name')}" if hw.get("cuda_available") else ""))

    # ---- Step 2/3/4: Inference (identical config for both systems) ----
    base_preds: Optional[List[Optional[Dict[str, Any]]]] = None
    ft_preds: Optional[List[Optional[Dict[str, Any]]]] = None
    base_timing: Dict[str, Any] = {}
    ft_timing: Dict[str, Any] = {}

    if not args.skip_inference:
        print(f"\n--- SYSTEM A (BASE): {BASE_MODEL} @ {BASE_MODEL_REVISION[:12]} ---")
        base_preds, base_timing, err = run_inference(
            records,
            model_id=BASE_MODEL,
            revision=BASE_MODEL_REVISION,
            adapter_path=None,
            batch_size=args.batch_size,
        )
        if err:
            print(f"  FATAL: {err}")
            sys.exit(2)
        save_predictions(base_preds, BASE_PRED_FILE)
        print(f"  Saved {len(base_preds)} base predictions → {BASE_PRED_FILE.name} "
              f"({base_timing.get('inference_seconds', '?')}s)")

        if not args.base_only:
            print(f"\n--- SYSTEM B (FINE-TUNED): {BASE_MODEL} + {ADAPTER_REPO} @ {ADAPTER_REVISION[:12]} ---")
            try:
                adapter_dir = resolve_adapter_dir()
            except Exception as e:  # noqa: BLE001
                print(f"  FATAL: adapter could not be resolved at pinned revision: {e}")
                sys.exit(2)
            ft_preds, ft_timing, err = run_inference(
                records,
                model_id=BASE_MODEL,
                revision=BASE_MODEL_REVISION,
                adapter_path=adapter_dir,
                batch_size=args.batch_size,
            )
            if err:
                print(f"  FATAL: {err}")
                sys.exit(2)
            save_predictions(ft_preds, FT_PRED_FILE)
            print(f"  Saved {len(ft_preds)} fine-tuned predictions → {FT_PRED_FILE.name} "
                  f"({ft_timing.get('inference_seconds', '?')}s)")
    else:
        print("\nSkipping inference — loading saved predictions...")
        if BASE_PRED_FILE.exists():
            base_preds = load_predictions(BASE_PRED_FILE)
            print(f"  Base predictions: {len(base_preds)} from {BASE_PRED_FILE.name}")
        if not args.base_only and FT_PRED_FILE.exists():
            ft_preds = load_predictions(FT_PRED_FILE)
            print(f"  FT predictions: {len(ft_preds)} from {FT_PRED_FILE.name}")

    if base_preds is None:
        print("FATAL: no base predictions available")
        sys.exit(1)
    if len(base_preds) != len(records):
        print(f"FATAL: prediction/record count mismatch: {len(base_preds)} vs {len(records)}")
        sys.exit(1)

    # ---- Step 5-9: Metrics ----
    print("\n--- COMPUTING METRICS ---")
    base_metrics = aggregate_scores(base_preds, records, "base")
    ft_metrics = aggregate_scores(ft_preds, records, "fine-tuned") if ft_preds is not None else None

    base_leakage = audit_leakage(base_preds, records, "base")
    ft_leakage = audit_leakage(ft_preds, records, "fine-tuned") if ft_preds is not None else None
    base_hallucination = audit_hallucinations(base_preds, records, "base")
    ft_hallucination = audit_hallucinations(ft_preds, records, "fine-tuned") if ft_preds is not None else None
    base_grounding = check_grounding_compatibility(base_preds, records, "base")
    ft_grounding = check_grounding_compatibility(ft_preds, records, "fine-tuned") if ft_preds is not None else None
    deltas = compute_deltas(base_metrics, ft_metrics) if ft_metrics else None

    # ---- Console summary ----
    print(f"\n{'=' * 64}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 64}")
    print(f"\n{'Metric':40s} {'Base':>10s} {'Fine-Tuned':>12s} {'Δ':>10s}")
    print(f"{'-' * 40} {'-' * 10} {'-' * 12} {'-' * 10}")
    for key in ["valid_json_rate", "valid_18field_schema_rate", "transaction_type_accuracy",
                "party_exact_accuracy", "party_token_f1", "amount_extraction_accuracy",
                "payment_method_accuracy", "ambiguity_detection_agreement",
                "accounting_leakage_rate", "full_semantic_exact_match"]:
        b = base_metrics["rates"].get(key, 0)
        f = ft_metrics["rates"].get(key, 0) if ft_metrics else None
        f_s = f"{f:.1%}" if f is not None else "—"
        d_s = f"{f - b:+.1%}" if f is not None else ""
        print(f"  {key:38s} {b:>10.1%} {f_s:>12s} {d_s:>10s}")

    # ---- Step 10: Save artifacts ----
    results = {
        "phase": "6C",
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "adapter_repo": ADAPTER_REPO,
        "adapter_revision": ADAPTER_REVISION,
        "test_set_path": str(test_path),
        "test_set_sha256": test_sha,
        "test_count": test_count,
        "generation_settings": {
            "do_sample": DO_SAMPLE,
            "max_new_tokens": MAX_NEW_TOKENS,
            "batch_size": args.batch_size,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "note": "identical for Base and Fine-Tuned; deterministic greedy decoding",
        },
        "hardware": {
            "base": base_timing,
            "finetuned": ft_timing,
            "probe": hw,
        },
        "base_metrics": base_metrics,
        "finetuned_metrics": ft_metrics,
        "deltas": deltas,
        "base_leakage": base_leakage,
        "finetuned_leakage": ft_leakage,
        "base_hallucination": base_hallucination,
        "finetuned_hallucination": ft_hallucination,
        "base_grounding": base_grounding,
        "finetuned_grounding": ft_grounding,
        "base_predictions_file": str(BASE_PRED_FILE),
        "finetuned_predictions_file": str(FT_PRED_FILE) if ft_preds is not None else None,
    }

    # Trim per-case details from the saved JSON (keep summary counts)
    for section in ["base_leakage", "finetuned_leakage", "base_hallucination",
                    "finetuned_hallucination", "base_grounding", "finetuned_grounding"]:
        if isinstance(results.get(section), dict):
            results[section].pop("details", None)
            results[section].pop("cases", None)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved: {RESULTS_FILE}")

    if ft_metrics:
        report = generate_report(
            base_metrics, ft_metrics,
            base_leakage, ft_leakage,
            base_hallucination, ft_hallucination,
            base_grounding, ft_grounding,
            deltas, test_sha, test_count,
            base_timing, ft_timing,
        )
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved: {REPORT_FILE}")

    # ---- Step 11: Test-set integrity re-verification ----
    test_sha_after = sha256_file(test_path)
    test_count_after = len(load_test(test_path))
    print("\n--- TEST SET INTEGRITY ---")
    print(f"  SHA-256 before: {test_sha}")
    print(f"  SHA-256 after:  {test_sha_after}")
    print(f"  Count before:   {test_count}")
    print(f"  Count after:    {test_count_after}")
    if test_sha_before_after_check(test_sha, test_sha_after) and test_count == test_count_after:
        print("  ✅ Test set integrity verified — UNTOUCHED")
    else:
        print("  ❌ FATAL: test set was modified during evaluation!")
        sys.exit(3)

    print(f"\n{'=' * 64}")
    print("Phase 6C evaluation complete.")
    print(f"{'=' * 64}")


def test_sha_before_after_check(before: str, after: str) -> bool:
    return before == after


if __name__ == "__main__":
    main()
