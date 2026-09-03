#!/usr/bin/env python3
"""
Platrixa FYJC — Fine-Tuned Specialist Evaluation (Phase 6A tooling, run in 6C)
==============================================================================

Evaluates a Qwen2.5-1.5B-Instruct specialist — with and without the Platrixa
LoRA adapter — against the untouched Phase 5 100-example test set.

Two run modes:

1) Direct chat-harness mode (default) — replicates the training distribution:
       system: Phase 5 specialist instruction (training/format.py)
       user:   student natural-language input
       model : 18-field ExpandedInterpretation JSON
   Used for the base-model (A) vs fine-tuned (B) comparison on identical
   inputs with identical decoding.

2) Production-path mode (--production-path) — routes every test input through
   FYJCLLMSpecialist.interpret(), i.e. the real chain
       model → JSON extraction → strict validation → schema verifier →
       ExpandedGroundingGate → REVIEW_REQUIRED interpretation
   This verifies Phase 12's adapter-load requirement: the LoRA adapter is
   loadable by LocalModelRunner and nothing bypasses validation/grounding.

The test set is NEVER used for training; it exists solely for this comparison.

Metrics (mode 1):
  - valid JSON rate / 18-field schema validity / unknown-field rate
  - forbidden accounting field rate
  - transaction-type accuracy
  - party extraction accuracy (exact set + token F1)
  - amount extraction accuracy (numeric multiset)
  - payment-method accuracy
  - ambiguity detection agreement
  - suggested_status agreement
  - difficulty-bucket breakdown (clear / ambiguous / incomplete / adversarial)
  - grounding-compatibility (grounding dict + statuses present and valid)
  - accounting-conclusion leakage rate (forbidden fields in raw text)

Usage (on a GPU host with torch + transformers + the model/adapter present):
    python3 training/evaluate_finetuned.py --base-only
    python3 training/evaluate_finetuned.py --lora-path training_output/platrixa-fyjc-specialist/<adapter-dir>
    python3 training/evaluate_finetuned.py                          # both, compare
    python3 training/evaluate_finetuned.py --production-path        # through specialist
    python3 training/evaluate_finetuned.py --check-only             # no torch needed

Library/model configuration follows LocalModelRunner environment vars:
    PLATRIXA_FYJC_MODEL_ID, PLATRIXA_FYJC_ADAPTER, PLATRIXA_FYJC_DEVICE,
    PLATRIXA_FYJC_DTYPE, PLATRIXA_FYJC_MAX_TOKENS, PLATRIXA_FYJC_TEMPERATURE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from training.format import SYSTEM_INSTRUCTION
except ImportError:  # direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from format import SYSTEM_INSTRUCTION  # type: ignore

from backend.maths.fyjc_contract import ALL_VALID_FIELDS

DEFAULT_TEST_SET = "training_data/fyjc_specialist_test.jsonl"
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

# Fields the model must never emit (accounting truth lives in the kernel).
FORBIDDEN_FIELDS = {
    "journal", "debit_lines", "credit_lines", "ledger",
    "balances", "debit_account", "credit_account", "journal_entry",
}

# Keys the production specialist may add on top of the 18 contract fields.
ALLOWED_EXTRA_KEYS = {
    "raw_input", "interpretation_model", "_error", "_raw_response",
    "_raw_model_output", "_validation_errors", "_grounded",
    "_grounding_summary", "_grounding_issues",
}

MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
VERIFIED = "VERIFIED"


# ---------------------------------------------------------------------------
# Data loading (no heavy imports)
# ---------------------------------------------------------------------------

def load_test_records(path: str) -> List[Dict[str, Any]]:
    """Load canonical Phase 5 test records {id, input, output, metadata}."""
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno} not valid JSON: {e}")
    return records


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _amount_value(item: Any) -> str:
    """Extract a comparable numeric string from an amount entry."""
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


def _norm_tx(pred: Dict[str, Any]) -> str:
    return str(pred.get("transaction_type_enum") or pred.get("transaction_type") or "").upper()


def _norm_pm(pred: Dict[str, Any]) -> str:
    return str(pred.get("payment_method_enum") or pred.get("payment_method") or "").upper()


def _has_ambiguity(out: Dict[str, Any]) -> bool:
    flags = out.get("ambiguity_flags") or []
    return any(f != "NONE" for f in flags)


# ---------------------------------------------------------------------------
# Per-record scoring (mode 1)
# ---------------------------------------------------------------------------

def score_prediction(prediction: Optional[Dict[str, Any]], ground_truth: Dict[str, Any]) -> Dict[str, Any]:
    """Score one prediction against the ground-truth 18-field output."""
    s: Dict[str, Any] = {
        "parse_ok": False,
        "schema_valid": False,
        "unknown_fields": [],
        "forbidden_fields": [],
        "tx_correct": False,
        "parties_exact": False,
        "party_f1": 0.0,
        "amounts_exact": False,
        "payment_correct": False,
        "ambiguity_agree": False,
        "status_agree": False,
        "grounding_compatible": False,
    }

    if not isinstance(prediction, dict):
        return s
    # Production error sentinels must not count as parsed outputs.
    if prediction.get("suggested_status") == MODEL_NOT_AVAILABLE:
        return s

    s["parse_ok"] = True

    pred_keys = set(prediction.keys())
    extra = pred_keys - ALL_VALID_FIELDS - ALLOWED_EXTRA_KEYS
    s["unknown_fields"] = sorted(extra)
    missing = ALL_VALID_FIELDS - pred_keys
    s["schema_valid"] = len(missing) == 0 and not s["unknown_fields"]

    forbidden = FORBIDDEN_FIELDS & pred_keys
    s["forbidden_fields"] = sorted(forbidden)

    # --- semantic field comparisons ---
    gt = ground_truth

    s["tx_correct"] = _norm_tx(prediction) == _norm_tx(gt)

    pred_parties = _norm_names(prediction.get("parties"))
    gt_parties = _norm_names(gt.get("parties"))
    s["parties_exact"] = pred_parties == gt_parties
    if pred_parties or gt_parties:
        inter = len(pred_parties & gt_parties)
        prec = inter / max(1, len(pred_parties))
        rec = inter / max(1, len(gt_parties))
        s["party_f1"] = round(2 * prec * rec / max(1e-9, prec + rec), 4)
    else:
        s["party_f1"] = 1.0

    s["amounts_exact"] = _norm_amounts(prediction.get("amounts")) == _norm_amounts(gt.get("amounts"))

    s["payment_correct"] = _norm_pm(prediction) == _norm_pm(gt)

    expected_amb = _has_ambiguity(gt)
    predicted_amb = _has_ambiguity(prediction)
    s["ambiguity_agree"] = predicted_amb == expected_amb

    s["status_agree"] = (
        str(prediction.get("suggested_status", "")).upper()
        == str(gt.get("suggested_status", "")).upper()
    )

    grounding = prediction.get("grounding")
    s["grounding_compatible"] = (
        isinstance(grounding, dict)
        and isinstance(prediction.get("field_confidences"), list)
        and prediction.get("suggested_status") in {VERIFIED, REVIEW_REQUIRED}
    )

    return s


# ---------------------------------------------------------------------------
# Aggregation (mode 1)
# ---------------------------------------------------------------------------

def aggregate(predictions: List[Optional[Dict[str, Any]]], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-record scores into report metrics."""
    n = len(records)
    counts: Dict[str, Any] = {
        "total": n,
        "parse_ok": 0,
        "schema_valid": 0,
        "forbidden_fields": 0,
        "tx_correct": 0,
        "parties_exact": 0,
        "party_f1_sum": 0.0,
        "amounts_exact": 0,
        "payment_correct": 0,
        "ambiguity_agree": 0,
        "status_agree": 0,
        "grounding_compatible": 0,
        "unknown_field_records": 0,
        "leakage_records": 0,
        "by_difficulty": {},
    }

    for pred, rec in zip(predictions, records):
        s = score_prediction(pred, rec.get("output", {}))
        for key in (
            "parse_ok", "schema_valid", "forbidden_fields", "tx_correct",
            "parties_exact", "amounts_exact", "payment_correct",
            "ambiguity_agree", "status_agree", "grounding_compatible",
        ):
            if s.get(key):
                counts[key] += 1
        counts["party_f1_sum"] += s.get("party_f1", 0.0)
        if s.get("unknown_fields"):
            counts["unknown_field_records"] += 1
        if s.get("forbidden_fields"):
            counts["leakage_records"] += 1

        difficulty = rec.get("metadata", {}).get("difficulty", "other")
        bucket = counts["by_difficulty"].setdefault(
            difficulty, {"total": 0, "parse_ok": 0, "tx_correct": 0,
                         "amounts_exact": 0, "status_agree": 0,
                         "ambiguity_agree": 0}
        )
        bucket["total"] += 1
        for key in ("parse_ok", "tx_correct", "amounts_exact",
                    "status_agree", "ambiguity_agree"):
            if s.get(key):
                bucket[key] += 1

    rates = {
        "valid_json_rate": round(counts["parse_ok"] / n, 4),
        "schema_valid_rate": round(counts["schema_valid"] / n, 4),
        "unknown_field_rate": round(counts["unknown_field_records"] / n, 4),
        "forbidden_field_rate": round(counts["forbidden_fields"] / n, 4),
        "accounting_conclusion_leakage_rate": round(counts["leakage_records"] / n, 4),
        "transaction_type_accuracy": round(counts["tx_correct"] / n, 4),
        "party_exact_accuracy": round(counts["parties_exact"] / n, 4),
        "party_token_f1": round(counts["party_f1_sum"] / n, 4),
        "amount_extraction_accuracy": round(counts["amounts_exact"] / n, 4),
        "payment_method_accuracy": round(counts["payment_correct"] / n, 4),
        "ambiguity_detection_agreement": round(counts["ambiguity_agree"] / n, 4),
        "status_agreement": round(counts["status_agree"] / n, 4),
        "grounding_compatibility_rate": round(counts["grounding_compatible"] / n, 4),
    }
    return {"counts": counts, "rates": rates}


# ---------------------------------------------------------------------------
# Inference (lazy heavy imports — only on a host with torch/transformers)
# ---------------------------------------------------------------------------

def run_direct_inference(
    records: List[Dict[str, Any]],
    base_model: str,
    adapter_path: Optional[str] = None,
    device: str = "auto",
    max_new_tokens: int = 1024,
    temperature: float = 0.1,
    top_p: float = 0.95,
) -> Tuple[List[Optional[Dict[str, Any]]], Optional[str]]:
    """Chat-harness inference. Returns (predictions, error)."""
    try:
        import torch  # noqa: PLC0415
        from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415
    except ImportError as e:
        return [], f"torch/transformers not installed on this host: {e}"

    try:
        tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
        load_kwargs: Dict[str, Any] = {"trust_remote_code": True}
        if device == "cpu":
            load_kwargs["device_map"] = None
        else:
            load_kwargs["device_map"] = "auto"
        if torch.cuda.is_available():
            load_kwargs["torch_dtype"] = torch.float16
        model = AutoModelForCausalLM.from_pretrained(base_model, **load_kwargs)

        if adapter_path:
            try:
                from peft import PeftModel  # noqa: PLC0415
            except ImportError as e:
                return [], f"peft not installed but --lora-path given: {e}"
            model = PeftModel.from_pretrained(model, adapter_path)

        model.eval()

        pad = tokenizer.eos_token_id
        predictions: List[Optional[Dict[str, Any]]] = []
        for i, rec in enumerate(records):
            messages = [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": rec.get("input", "")},
            ]
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = tokenizer(prompt, return_tensors="pt", truncation=True,
                               max_length=2048)
            device_of_model = next(model.parameters()).device
            inputs = {k: v.to(device_of_model) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=temperature > 0,
                    temperature=temperature if temperature > 0 else 1.0,
                    top_p=top_p,
                    pad_token_id=pad,
                )
            new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
            raw = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

            parsed = _extract_json(raw)
            predictions.append(parsed)
            if (i + 1) % 10 == 0:
                print(f"    ...{i + 1}/{len(records)}")
        return predictions, None
    except Exception as e:  # noqa: BLE001
        return [], f"inference failed: {e}"


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Tolerant JSON-object extraction (mirrors the production specialist)."""
    if not text:
        return None
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


def run_production_inference(records: List[Dict[str, Any]]) -> Tuple[List[Optional[Dict[str, Any]]], Optional[str]]:
    """Route inputs through FYJCLLMSpecialist (model→schema→grounding gate)."""
    try:
        from backend.maths.fyjc_llm_specialist import FYJCLLMSpecialist  # noqa: PLC0415
        from backend.maths.fyjc_local_model_runner import LocalModelRunner  # noqa: PLC0415
    except ImportError as e:
        return [], f"backend modules import failed: {e}"

    LocalModelRunner.reset()  # re-read PLATRIXA_FYJC_* env vars
    specialist = FYJCLLMSpecialist()

    predictions: List[Optional[Dict[str, Any]]] = []
    for rec in records:
        result = specialist.interpret(rec.get("input", ""))
        predictions.append(result)
    return predictions, None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(
    base_metrics: Optional[Dict[str, Any]],
    ft_metrics: Optional[Dict[str, Any]],
    test_set: str,
) -> None:
    print("\n" + "=" * 74)
    print(f"EVALUATION REPORT — {test_set}")
    print("=" * 74)

    if base_metrics and "error" in base_metrics:
        print(f"  BASE MODEL ERROR: {base_metrics['error']}")
    if ft_metrics and "error" in ft_metrics:
        print(f"  FT MODEL ERROR:   {ft_metrics['error']}")

    labels = {
        "valid_json_rate": "Valid JSON rate",
        "schema_valid_rate": "18-field schema validity",
        "unknown_field_rate": "Unknown-field rate",
        "forbidden_field_rate": "Forbidden-field rate",
        "accounting_conclusion_leakage_rate": "Accounting-conclusion leakage",
        "transaction_type_accuracy": "Transaction-type accuracy",
        "party_exact_accuracy": "Party exact-set accuracy",
        "party_token_f1": "Party token F1",
        "amount_extraction_accuracy": "Amount extraction accuracy",
        "payment_method_accuracy": "Payment-method accuracy",
        "ambiguity_detection_agreement": "Ambiguity detection agreement",
        "status_agreement": "suggested_status agreement",
        "grounding_compatibility_rate": "Grounding compatibility",
    }
    print(f"\n  {'Metric':34s} {'Base':>9s} {'LoRA':>9s} {'Δ':>9s}")
    print(f"  {'-'*34} {'-'*9} {'-'*9} {'-'*9}")
    for key, label in labels.items():
        b = base_metrics["rates"].get(key) if base_metrics and "rates" in base_metrics else None
        f = ft_metrics["rates"].get(key) if ft_metrics and "rates" in ft_metrics else None
        b_s = f"{b:.1%}" if isinstance(b, float) else ("—" if b is None else b)
        f_s = f"{f:.1%}" if isinstance(f, float) else ("—" if f is None else f)
        d_s = ""
        if isinstance(b, float) and isinstance(f, float):
            delta = f - b
            d_s = f"{delta:+.1%}"
        print(f"  {label:34s} {b_s:>9s} {f_s:>9s} {d_s:>9s}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate FYJC specialist (base vs LoRA)")
    parser.add_argument("--test-set", default=DEFAULT_TEST_SET)
    parser.add_argument("--base-model", default=None,
                        help="default: env PLATRIXA_FYJC_MODEL_ID or Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--lora-path", default=None, help="LoRA adapter directory")
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--production-path", action="store_true",
                        help="run through FYJCLLMSpecialist instead of the chat harness")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--output", default=None, help="JSON report output path")
    parser.add_argument("--check-only", action="store_true",
                        help="validate data loading only (no torch/model needed)")
    args = parser.parse_args()

    root = _PROJECT_ROOT
    test_path = args.test_set if Path(args.test_set).is_absolute() else str(root / args.test_set)
    if not Path(test_path).exists():
        print(f"ERROR: test set not found: {test_path}")
        sys.exit(1)

    records = load_test_records(test_path)
    if args.max_samples:
        records = records[: args.max_samples]
    print(f"Test set: {test_path} — {len(records)} records")

    if args.check_only:
        n = len(records)
        ok_outputs = all(isinstance(r.get("output"), dict) for r in records)
        ok_18 = all(set(r["output"]) >= ALL_VALID_FIELDS for r in records)
        print(f"Check-only: {n} records load; outputs dicts={ok_outputs}; "
              f"all 18 fields={ok_18}")
        sys.exit(0 if (ok_outputs and ok_18) else 1)

    base_model = (
        args.base_model
        or os.environ.get("PLATRIXA_FYJC_MODEL_ID")
        or DEFAULT_BASE_MODEL
    )
    adapter = args.lora_path or os.environ.get("PLATRIXA_FYJC_ADAPTER")
    device = os.environ.get("PLATRIXA_FYJC_DEVICE", "auto")
    max_tokens = int(os.environ.get("PLATRIXA_FYJC_MAX_TOKENS", "1024"))
    temperature = float(os.environ.get("PLATRIXA_FYJC_TEMPERATURE", "0.1"))
    top_p = float(os.environ.get("PLATRIXA_FYJC_TOP_P", "0.95"))

    # Probe availability first: never fake a run.
    try:
        import torch  # noqa: PLC0415
        import transformers  # noqa: PLC0415
        has_cuda = torch.cuda.is_available()
        print(f"torch={torch.__version__} transformers={transformers.__version__} "
              f"cuda={has_cuda} device={torch.cuda.get_device_name(0) if has_cuda else 'cpu'}")
    except ImportError as e:
        print(f"\nBLOCKED: this host cannot run inference — {e}")
        print("Phase 6C (training + adapter evaluation) requires a CUDA host "
              "with torch/transformers and the downloaded base model.")
        sys.exit(2)

    base_metrics = ft_metrics = None
    run_fn = run_production_inference if args.production_path else run_direct_inference

    if args.production_path:
        print("\nRunning production path (FYJCLLMSpecialist → schema → GroundingGate)...")
        preds, err = run_fn(records)
        if err:
            print(f"ERROR: {err}")
            sys.exit(2)
        available = [p for p in preds if p and p.get("suggested_status") != MODEL_NOT_AVAILABLE]
        gated = [p for p in preds if p and p.get("suggested_status") == REVIEW_REQUIRED]
        print(f"  records={len(records)} available={len(available)} "
              f"REVIEW_REQUIRED(gated)={len(gated)}")
        report = {
            "mode": "production-path",
            "test_set": test_path,
            "base_model": base_model,
            "adapter": adapter,
            "records": len(records),
            "model_available": len(available),
            "review_required": len(gated),
            "blocked": len(records) - len(available),
            "env": {
                "PLATRIXA_FYJC_MODEL_ID": base_model,
                "PLATRIXA_FYJC_ADAPTER": adapter,
                "PLATRIXA_FYJC_DEVICE": device,
            },
        }
        out = args.output or str(root / "training_data" / "phase6_production_check.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved: {out}")
        if len(available) < len(records):
            print("\nBLOCKED: adapter/base model not fully available. "
                  "Set PLATRIXA_FYJC_MODEL_ID / PLATRIXA_FYJC_ADAPTER.")
            sys.exit(2)
        sys.exit(0)

    # Mode 1: base (A) vs fine-tuned (B) on the SAME untouched test set.
    if adapter and not args.base_only:
        print("\nEvaluating BASE model (no adapter)...")
        base_preds, err = run_direct_inference(
            records, base_model, None, device, max_tokens, temperature, top_p
        )
        if err:
            print(f"  ERROR: {err}")
            base_metrics = {"error": err}
        else:
            base_metrics = aggregate(base_preds, records)

        print(f"\nEvaluating FINE-TUNED model (adapter: {adapter})...")
        ft_preds, err = run_direct_inference(
            records, base_model, adapter, device, max_tokens, temperature, top_p
        )
        if err:
            print(f"  ERROR: {err}")
            ft_metrics = {"error": err}
        else:
            ft_metrics = aggregate(ft_preds, records)
    else:
        # Base-only run (either --base-only, or no adapter supplied yet).
        if adapter and args.base_only:
            print("NOTE: --base-only given with --lora-path; adapter ignored.")
        print("\nEvaluating BASE model (no adapter)...")
        base_preds, err = run_direct_inference(
            records, base_model, None, device, max_tokens, temperature, top_p
        )
        if err:
            print(f"  ERROR: {err}")
            base_metrics = {"error": err}
        else:
            base_metrics = aggregate(base_preds, records)
        if not adapter:
            print("\n(Hint: pass --lora-path <adapter-dir> to compare against the "
                  "fine-tuned model.)")

    print_report(base_metrics, ft_metrics, Path(test_path).name)

    report = {
        "mode": "direct-chat",
        "test_set": str(test_path),
        "records": len(records),
        "base_model": base_model,
        "adapter": adapter,
        "base_metrics": base_metrics,
        "finetuned_metrics": ft_metrics,
    }
    out = args.output or str(root / "training_data" / "phase6_evaluation_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved: {out}")


if __name__ == "__main__":
    main()
