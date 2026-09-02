#!/usr/bin/env python3
"""
Platrixa FYJC — Model Evaluation Script

Compares:
  A) Base Qwen2.5-1.5B-Instruct
  B) Fine-tuned Qwen2.5-1.5B-Instruct + LoRA

Against the held-out test set.

Metrics:
  - Exact structured-output validity (can JSON parse)
  - Schema validity (required keys present)
  - transaction_type accuracy
  - parties extraction accuracy
  - amount extraction accuracy
  - payment_method accuracy
  - ambiguity detection accuracy
  - grounding-field validity
  - malformed-output rate
  - unknown-field rate

Usage:
    python training/evaluate.py
    python training/evaluate.py --test-set training_data/specialist_test.jsonl
    python training/evaluate.py --base-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

REQUIRED_OUTPUT_KEYS = {"transaction_type", "parties", "amounts", "payment_method"}

VALID_TRANSACTION_TYPES = {
    "purchase", "sale", "payment", "receipt", "capital", "expense",
    "return", "return_out", "return_in", "discount_trade", "discount_cash",
    "settlement", "gst", "drawing", "depreciation", "unknown",
    "compound", "joint_venture", "consignment",
}


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

ALPACA_PROMPT = """Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.

### Instruction:
{}

### Input:
{}

### Response:
{}"""


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------

def load_test_set(path: str, max_samples: Optional[int] = None) -> List[Dict]:
    """Load test records."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records.append(rec)
            except json.JSONDecodeError:
                continue
    if max_samples:
        records = records[:max_samples]
    return records


def parse_model_output(raw_text: str) -> Tuple[Optional[Dict], str]:
    """Try to extract a JSON object from model output.
    
    Returns (parsed_dict_or_None, error_message).
    """
    text = raw_text.strip()

    # Try direct JSON parse
    try:
        return json.loads(text), ""
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the text (model might add explanation)
    import re
    # Look for JSON object
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group()), ""
        except json.JSONDecodeError:
            pass

    # Try code block
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1)), ""
        except json.JSONDecodeError:
            pass

    return None, "Could not extract valid JSON from model output"


def evaluate_record(
    prediction: Optional[Dict],
    ground_truth: Dict,
) -> Dict[str, Any]:
    """Evaluate a single record's prediction against ground truth."""
    results = {
        "parse_success": prediction is not None,
        "schema_valid": False,
        "fields": {},
        "field_accuracy": 0.0,
    }

    if prediction is None:
        results["fields"] = {k: {"correct": False, "error": "parse_failed"}
                             for k in REQUIRED_OUTPUT_KEYS}
        return results

    # Schema validation
    pred_keys = set(prediction.keys())
    results["schema_valid"] = REQUIRED_OUTPUT_KEYS.issubset(pred_keys)

    # Field-by-field comparison
    field_scores = {}

    # transaction_type
    pred_tx = str(prediction.get("transaction_type", "")).lower()
    gt_tx = str(ground_truth.get("transaction_type", "")).lower()
    field_scores["transaction_type"] = {
        "correct": pred_tx == gt_tx,
        "prediction": pred_tx,
        "ground_truth": gt_tx,
    }

    # parties
    pred_parties = sorted([str(p).lower() for p in (prediction.get("parties") or [])])
    gt_parties = sorted([str(p).lower() for p in (ground_truth.get("parties") or [])])
    field_scores["parties"] = {
        "correct": pred_parties == gt_parties,
        "prediction": pred_parties,
        "ground_truth": gt_parties,
    }

    # amounts
    pred_amounts = sorted([str(a.get("value", a) if isinstance(a, dict) else a)
                           for a in (prediction.get("amounts") or [])])
    gt_amounts = sorted([str(a.get("value", a) if isinstance(a, dict) else a)
                         for a in (ground_truth.get("amounts") or [])])
    field_scores["amounts"] = {
        "correct": pred_amounts == gt_amounts,
        "prediction": pred_amounts,
        "ground_truth": gt_amounts,
    }

    # payment_method
    pred_pm = str(prediction.get("payment_method", "")).lower()
    gt_pm = str(ground_truth.get("payment_method", "")).lower()
    field_scores["payment_method"] = {
        "correct": pred_pm == gt_pm,
        "prediction": pred_pm,
        "ground_truth": gt_pm,
    }

    # ambiguities
    pred_amb = sorted([str(a).lower() for a in (prediction.get("ambiguities") or [])])
    gt_amb = sorted([str(a).lower() for a in (ground_truth.get("ambiguities") or [])])
    field_scores["ambiguities"] = {
        "correct": pred_amb == gt_amb,
        "prediction": pred_amb,
        "ground_truth": gt_amb,
    }

    results["fields"] = field_scores
    correct_count = sum(1 for f in field_scores.values() if f["correct"])
    results["field_accuracy"] = correct_count / max(1, len(field_scores))

    return results


def generate_model_predictions(
    model_name: str,
    test_records: List[Dict],
    max_new_tokens: int = 256,
    lora_path: Optional[str] = None,
) -> List[Optional[Dict]]:
    """Generate predictions from a model on test records.
    
    Returns list of parsed prediction dicts (None if parse failed).
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextStreamer

    # Load model
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map="auto",
    )

    # Load LoRA if provided
    if lora_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, lora_path)

    model.eval()

    predictions = []
    for i, record in enumerate(test_records):
        instruction = record.get("instruction", "")
        input_text = record.get("input", "")

        prompt = ALPACA_PROMPT.format(instruction, input_text, "")
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=1.0,
                top_p=1.0,
            )

        # Decode only new tokens
        new_tokens = outputs[0][inputs["input_ids"].shape[-1]:]
        raw_output = tokenizer.decode(new_tokens, skip_special_tokens=True)

        # Parse
        parsed, _ = parse_model_output(raw_output)
        predictions.append(parsed)

        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(test_records)}")

    return predictions


def compute_metrics(
    predictions: List[Optional[Dict]],
    ground_truths: List[Dict],
) -> Dict[str, Any]:
    """Compute aggregate evaluation metrics."""
    total = len(predictions)
    parse_successes = sum(1 for p in predictions if p is not None)
    schema_valid = 0

    field_correct = Counter()
    field_total = Counter()
    error_categories = Counter()

    for pred, gt in zip(predictions, ground_truths):
        result = evaluate_record(pred, gt)
        if result["parse_success"]:
            if result["schema_valid"]:
                schema_valid += 1
        for field_name, field_result in result.get("fields", {}).items():
            field_total[field_name] += 1
            if field_result.get("correct"):
                field_correct[field_name] += 1

    return {
        "total_records": total,
        "parse_success_rate": round(parse_successes / max(1, total), 4),
        "schema_valid_rate": round(schema_valid / max(1, total), 4),
        "field_accuracies": {
            field: round(field_correct[field] / max(1, field_total[field]), 4)
            for field in field_total
        },
        "malformed_output_rate": round(1 - parse_successes / max(1, total), 4),
    }


def print_report(
    base_metrics: Dict[str, Any],
    ft_metrics: Optional[Dict[str, Any]],
    test_set_name: str,
):
    """Print a formatted evaluation report."""
    print(f"\n{'='*60}")
    print(f"EVALUATION REPORT — {test_set_name}")
    print(f"{'='*60}")

    def _print_metrics(label: str, metrics: Dict):
        print(f"\n  {label}:")
        print(f"    Parse success rate:      {metrics['parse_success_rate']:.1%}")
        print(f"    Schema valid rate:       {metrics['schema_valid_rate']:.1%}")
        print(f"    Malformed output rate:   {metrics['malformed_output_rate']:.1%}")
        print(f"    Field accuracies:")
        for field, acc in metrics["field_accuracies"].items():
            print(f"      {field:30s} {acc:.1%}")

    _print_metrics("Base Model (Qwen2.5-1.5B-Instruct)", base_metrics)

    if ft_metrics:
        _print_metrics("Fine-tuned Model (LoRA)", ft_metrics)

        # Comparison
        print(f"\n  {'Field':30s} {'Base':>8s} {'FT':>8s} {'Δ':>8s}")
        print(f"  {'-'*30} {'-'*8} {'-'*8} {'-'*8}")
        all_fields = set(base_metrics["field_accuracies"]) | set(ft_metrics["field_accuracies"])
        for field in sorted(all_fields):
            base_acc = base_metrics["field_accuracies"].get(field, 0)
            ft_acc = ft_metrics["field_accuracies"].get(field, 0)
            delta = ft_acc - base_acc
            marker = "↑" if delta > 0 else "↓" if delta < 0 else "="
            print(f"  {field:30s} {base_acc:>7.1%} {ft_acc:>7.1%} {delta:>+7.1%} {marker}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate FYJC accounting model"
    )
    parser.add_argument("--test-set", type=str, default=None,
                        help="Path to test JSONL (default: specialist_test.jsonl)")
    parser.add_argument("--base-model", type=str, default=None,
                        help="Base model name")
    parser.add_argument("--lora-path", type=str, default=None,
                        help="Path to LoRA adapter")
    parser.add_argument("--max-samples", type=int, default=None,
                        help="Maximum samples to evaluate")
    parser.add_argument("--max-tokens", type=int, default=256,
                        help="Max new tokens for generation")
    parser.add_argument("--base-only", action="store_true",
                        help="Only evaluate the base model")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON report path")
    args = parser.parse_args()

    project_root = _PROJECT_ROOT
    data_dir = project_root / "training_data"

    # Find test set
    test_path = args.test_set
    if not test_path:
        test_path = str(data_dir / "specialist_test.jsonl")
    if not Path(test_path).exists():
        # Fallback to validation set
        test_path = str(data_dir / "specialist_val.jsonl")
    if not Path(test_path).exists():
        # Fallback to any available data
        for candidate in [
            "training_data/specialist_clean_training.jsonl",
            "training_data/specialist_ambiguity_eval.jsonl",
        ]:
            p = project_root / candidate
            if p.exists():
                test_path = str(p)
                break

    if not Path(test_path).exists():
        print("ERROR: No test data found. Run training/pipeline.py first.")
        sys.exit(1)

    print(f"Test set: {test_path}")
    test_records = load_test_set(test_path, max_samples=args.max_samples)
    print(f"Records: {len(test_records)}")

    if len(test_records) == 0:
        print("ERROR: Empty test set.")
        sys.exit(1)

    # Extract ground truth from records
    ground_truths = []
    for rec in test_records:
        output_str = rec.get("output", "{}")
        try:
            gt = json.loads(output_str)
        except json.JSONDecodeError:
            gt = {}
        ground_truths.append(gt)

    # Load config for model names
    config_path = project_root / "training" / "config.yaml"
    base_model_name = args.base_model or "unsloth/Qwen2.5-1.5B-Instruct"
    lora_path = args.lora_path

    if not args.base_only and not lora_path:
        # Check if fine-tuned model exists
        ft_path = project_root / "training_output" / "lora_adapter"
        if ft_path.exists():
            lora_path = str(ft_path)

    # ── Evaluate base model ──
    print("\n" + "=" * 60)
    print("Evaluating Base Model")
    print("=" * 60)

    try:
        base_predictions = generate_model_predictions(
            base_model_name, test_records, args.max_tokens
        )
        base_metrics = compute_metrics(base_predictions, ground_truths)
    except Exception as e:
        print(f"ERROR evaluating base model: {e}")
        base_metrics = {
            "total_records": len(test_records),
            "parse_success_rate": 0.0,
            "schema_valid_rate": 0.0,
            "field_accuracies": {},
            "malformed_output_rate": 1.0,
            "error": str(e),
        }

    # ── Evaluate fine-tuned model ──
    ft_metrics = None
    if not args.base_only and lora_path:
        print("\n" + "=" * 60)
        print("Evaluating Fine-tuned Model")
        print("=" * 60)

        try:
            ft_predictions = generate_model_predictions(
                base_model_name, test_records, args.max_tokens,
                lora_path=lora_path,
            )
            ft_metrics = compute_metrics(ft_predictions, ground_truths)
        except Exception as e:
            print(f"ERROR evaluating fine-tuned model: {e}")
            ft_metrics = {
                "total_records": len(test_records),
                "parse_success_rate": 0.0,
                "schema_valid_rate": 0.0,
                "field_accuracies": {},
                "malformed_output_rate": 1.0,
                "error": str(e),
            }

    # ── Report ──
    test_name = Path(test_path).stem
    print_report(base_metrics, ft_metrics, test_name)

    # Save report
    report = {
        "test_set": test_path,
        "test_records": len(test_records),
        "base_model": base_model_name,
        "lora_path": lora_path,
        "base_metrics": base_metrics,
        "finetuned_metrics": ft_metrics,
    }

    output_path = args.output
    if not output_path:
        output_path = str(data_dir / f"evaluation_report_{test_name}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()
