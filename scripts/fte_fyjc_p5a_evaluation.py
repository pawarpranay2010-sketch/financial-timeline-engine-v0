#!/usr/bin/env python3
"""
Platrixa Sprint P5a — Automated Specialist Model Evaluation

Evaluates the specialist finance/accounting model against held-out evaluation
datasets. Produces field-level accuracy, grounding-safety analysis, and a
reproducible evaluation report.

This script operates in EVAL_MODE only. It must NOT:
- Connect to production routing
- Modify the Truth Kernel
- Write to databases
- Promote evaluation cases into training data
- Retrain the model

Architecture:
    Student Input → Specialist AI → Structured Interpretation
         ↓
    Evaluation Harness (this script) compares against ground truth
         ↓
    Kernel Verification (optional, evaluation-only path)
         ↓
    Report: field-level accuracy, grounding safety, failure analysis

Pure module: no Streamlit, no production state mutation.
"""

from __future__ import annotations

import json
import os
import sys
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# EVAL MODE — Hard constant. This script NEVER operates in production mode.
# ---------------------------------------------------------------------------
EVAL_MODE = True

# Safety: prevent any accidental production writes
if not EVAL_MODE:
    raise RuntimeError("P5a evaluation must run with EVAL_MODE = True")


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Import project modules (evaluation-only imports)
# ---------------------------------------------------------------------------
try:
    from backend.maths.fyjc_ai_adapter import (
        AIAdapter,
        AIInterpretation,
        AmbiguityFlag,
        FieldConfidence,
        GroundingGate,
        GroundingStatus,
        MockAIAdapter,
        PaymentMethod,
        TransactionType,
    )
    _ADAPTER_AVAILABLE = True
except ImportError as e:
    _ADAPTER_AVAILABLE = False
    _IMPORT_ERROR = str(e)


# ---------------------------------------------------------------------------
# Model artifact detection
# ---------------------------------------------------------------------------
_MODEL_SEARCH_PATHS = [
    _PROJECT_ROOT / "models",
    _PROJECT_ROOT / "checkpoints",
    _PROJECT_ROOT / "lora_adapter",
    _PROJECT_ROOT / "output",
    Path.home() / ".cache" / "huggingface" / "hub",
    Path("/tmp") / "platrixa_model",
]

_LORA_PATTERNS = [
    "adapter_config.json",
    "adapter_model.bin",
    "adapter_model.safetensors",
    "lora_adapter",
]

_QWEN_BASE = "Qwen/Qwen2.5-1.5B-Instruct"


def _find_model_artifact() -> Optional[Dict[str, Any]]:
    """Search for actual model artifacts on disk."""
    results = {
        "base_model_available": False,
        "lora_adapter_path": None,
        "lora_adapter_available": False,
        "searched_paths": [],
        "found_artifacts": [],
    }

    # Check if transformers/peft are installed
    try:
        import transformers  # noqa: F401
        results["transformers_installed"] = True
    except ImportError:
        results["transformers_installed"] = False
        return results

    try:
        import peft  # noqa: F401
        results["peft_installed"] = True
    except ImportError:
        results["peft_installed"] = False

    # Search for LoRA adapter files
    for search_path in _MODEL_SEARCH_PATHS:
        results["searched_paths"].append(str(search_path))
        if not search_path.exists():
            continue
        for pattern in _LORA_PATTERNS:
            for match in search_path.rglob(pattern):
                results["found_artifacts"].append(str(match))
                if "adapter_config" in pattern:
                    results["lora_adapter_path"] = str(match.parent)
                    results["lora_adapter_available"] = True

    return results


# ---------------------------------------------------------------------------
# Load evaluation datasets
# ---------------------------------------------------------------------------
_EVAL_TIERS = {
    "ambiguity": "training_data/specialist_ambiguity_eval.jsonl",
    "unsupported": "training_data/specialist_unsupported_eval.jsonl",
    "robustness": "training_data/specialist_robustness_eval.jsonl",
}


def _load_tier(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL evaluation tier."""
    full_path = _PROJECT_ROOT / path
    if not full_path.exists():
        return []
    records = []
    with open(full_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Field-level comparison
# ---------------------------------------------------------------------------

@dataclass
class FieldResult:
    """Result of comparing one field between prediction and ground truth."""
    field_name: str
    correct: bool
    prediction_value: Any = None
    ground_truth_value: Any = None
    failure_type: str = ""  # "match", "missing", "fabricated", "mismatch", "inferred_labeled_explicit"


@dataclass
class RecordResult:
    """Evaluation result for a single record."""
    problem_id: str
    source_tier: str
    input_text: str
    raw_model_output: str = ""
    parse_success: bool = False
    field_results: List[FieldResult] = field(default_factory=list)
    grounding_correct: bool = False
    ambiguity_correct: bool = False
    reference_correct: bool = False
    kernel_status: str = ""
    kernel_verdict: str = ""
    failure_categories: List[str] = field(default_factory=list)
    total_fields: int = 0
    correct_fields: int = 0

    @property
    def field_accuracy(self) -> float:
        return self.correct_fields / self.total_fields if self.total_fields > 0 else 0.0


def _normalize_amount(val: Any) -> Optional[str]:
    """Normalize an amount for comparison."""
    if val is None:
        return None
    if isinstance(val, dict):
        v = val.get("value", "")
    else:
        v = str(val)
    v = v.strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    try:
        return str(Decimal(v))
    except (InvalidOperation, ValueError):
        return v.lower().strip()


def _compare_amounts(pred: Any, gt: Any) -> Tuple[bool, str]:
    """Compare amounts semantically."""
    if isinstance(pred, list) and isinstance(gt, list):
        pred_vals = sorted([_normalize_amount(a) for a in pred if _normalize_amount(a)])
        gt_vals = sorted([_normalize_amount(a) for a in gt if _normalize_amount(a)])
        if pred_vals == gt_vals:
            return True, "match"
        # Check for missing/fabricated
        gt_set = set(gt_vals)
        pred_set = set(pred_vals)
        missing = gt_set - pred_set
        fabricated = pred_set - gt_set
        if missing and not fabricated:
            return False, "missing"
        if fabricated and not missing:
            return False, "fabricated"
        return False, "mismatch"
    elif isinstance(pred, list) and not isinstance(gt, list):
        return False, "type_mismatch"
    elif not isinstance(pred, list) and isinstance(gt, list):
        return False, "type_mismatch"
    return str(pred).strip() == str(gt).strip(), "match" if str(pred).strip() == str(gt).strip() else "mismatch"


def _compare_field(pred: Any, gt: Any, field_name: str) -> Tuple[bool, str]:
    """Generic field comparison."""
    if field_name == "amounts":
        return _compare_amounts(pred, gt)
    if field_name == "parties":
        if isinstance(pred, list) and isinstance(gt, list):
            pred_set = {str(p).strip().lower() for p in pred}
            gt_set = {str(p).strip().lower() for p in gt}
            if pred_set == gt_set:
                return True, "match"
            missing = gt_set - pred_set
            fabricated = pred_set - gt_set
            if missing and not fabricated:
                return False, "missing"
            if fabricated and not missing:
                return False, "fabricated"
            return False, "mismatch"
        return False, "type_mismatch"
    if field_name in ("references", "ambiguities"):
        if isinstance(pred, list) and isinstance(gt, list):
            pred_set = {str(p).strip().lower() for p in pred}
            gt_set = {str(p).strip().lower() for p in gt}
            if pred_set == gt_set:
                return True, "match"
            return False, "mismatch"
        return str(pred).strip().lower() == str(gt).strip().lower(), "match" if str(pred).strip().lower() == str(gt).strip().lower() else "mismatch"
    if isinstance(pred, str) and isinstance(gt, str):
        p = pred.strip().lower()
        g = gt.strip().lower()
        return p == g, "match" if p == g else "mismatch"
    return str(pred).strip() == str(gt).strip(), "match" if str(pred).strip() == str(gt).strip() else "mismatch"


# ---------------------------------------------------------------------------
# Grounding safety checks
# ---------------------------------------------------------------------------

def _check_grounding_safety(prediction: Dict[str, Any], ground_truth: Dict[str, Any]) -> List[str]:
    """Detect grounding safety violations in model prediction."""
    violations = []

    # Check if prediction marks inferred as explicit
    pred_grounding = prediction.get("grounding", {})
    gt_grounding = ground_truth.get("grounding", {})
    if isinstance(pred_grounding, dict) and isinstance(gt_grounding, dict):
        pred_inferred = set(pred_grounding.get("inferred_fields", []))
        gt_inferred = set(gt_grounding.get("inferred_fields", []))
        # Fields the GT says are inferred but prediction says are explicit
        incorrectly_explicit = gt_inferred - pred_inferred
        if incorrectly_explicit:
            violations.append(f"inferred_marked_explicit: {incorrectly_explicit}")

    # Check for fabricated amounts
    pred_amounts = prediction.get("amounts", [])
    gt_amounts = ground_truth.get("amounts", [])
    if isinstance(pred_amounts, list) and isinstance(gt_amounts, list):
        pred_vals = {_normalize_amount(a) for a in pred_amounts if _normalize_amount(a)}
        gt_vals = {_normalize_amount(a) for a in gt_amounts if _normalize_amount(a)}
        fabricated = pred_vals - gt_vals
        if fabricated and gt_vals:  # Only flag if GT has amounts
            violations.append(f"fabricated_amounts: {fabricated}")

    # Check for fabricated parties
    pred_parties = {str(p).strip().lower() for p in prediction.get("parties", [])}
    gt_parties = {str(p).strip().lower() for p in ground_truth.get("parties", [])}
    fabricated_parties = pred_parties - gt_parties
    if fabricated_parties and gt_parties:
        violations.append(f"fabricated_parties: {fabricated_parties}")

    # Check if model produced journal/accounting truth (should be kernel-only)
    for key in ("journal", "debit_lines", "credit_lines", "ledger", "balances",
                "trial_balance", "debit", "credit"):
        if key in prediction:
            violations.append(f"produced_kernel_output: {key}")

    # Check if unresolved ambiguities were silently resolved
    gt_ambiguities = set(str(a).strip().lower() for a in ground_truth.get("ambiguities", []))
    pred_ambiguities = set(str(a).strip().lower() for a in prediction.get("ambiguities", []))
    missed_ambiguities = gt_ambiguities - pred_ambiguities
    if missed_ambiguities:
        violations.append(f"missed_ambiguity: {missed_ambiguities}")

    return violations


# ---------------------------------------------------------------------------
# Mock model (used when real model is unavailable)
# ---------------------------------------------------------------------------

class _DeterministicFallbackAdapter:
    """Fallback adapter using simple heuristic rules for evaluation.

    This is NOT the trained model. It exists solely so the evaluation
    harness can run end-to-end when the LoRA artifact is unavailable,
    producing a baseline for comparison.
    """

    def __init__(self):
        self._name = "deterministic-fallback"
        self._version = "0.0.0-baseline"

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def model_version(self) -> str:
        return self._version

    def understand_transaction(self, text: str) -> Dict[str, Any]:
        """Heuristic interpretation — NOT the trained model."""
        low = text.lower().strip()

        # Transaction type detection (simple keyword matching)
        tx_type = "unknown"
        if any(w in low for w in ("purchased", "bought", "purchase")):
            tx_type = "purchase"
        elif any(w in low for w in ("sold", "sale", "selling")):
            tx_type = "sale"
        elif any(w in low for w in ("paid", "payment")):
            tx_type = "expense" if any(e in low for e in ("rent", "salary", "wages", "electricity", "insurance")) else "payment"
        elif any(w in low for w in ("received", "receipt")):
            tx_type = "receipt"
        elif any(w in low for w in ("started business", "capital")):
            tx_type = "capital"
        elif any(w in low for w in ("withdrew", "drawings", "personal use")):
            tx_type = "drawing"
        elif any(w in low for w in ("returned", "return")):
            tx_type = "return"
        elif any(w in low for w in ("depreciation", "provision")):
            tx_type = "depreciation"
        elif any(w in low for w in ("consignment", "consign")):
            tx_type = "consignment"
        elif any(w in low for w in ("joint venture")):
            tx_type = "joint_venture"

        # Amount extraction
        import re
        amounts = []
        for m in re.finditer(r'(?:rs\.?|₹|inr)\s*(\d[\d,]*(?:\.\d+)?)', low):
            try:
                amounts.append({"value": m.group(1).replace(",", ""), "currency": "INR", "source": "explicit"})
            except Exception:
                pass

        # Party extraction (capitalized words after "from"/"to"/"on")
        parties = []
        for m in re.finditer(r'(?:from|to|on)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', text):
            parties.append(m.group(1))

        # Payment method
        payment = "unknown"
        if any(w in low for w in ("by cheque", "cheque", "check")):
            payment = "cheque"
        elif any(w in low for w in ("by bank", "bank transfer", "neft", "rtgs")):
            payment = "bank"
        elif any(w in low for w in ("cash",)):
            payment = "cash"
        elif "credit" in low:
            payment = "credit"

        # Ambiguities
        ambiguities = []
        if payment == "unknown" and tx_type in ("purchase", "sale"):
            ambiguities.append("payment_method_ambiguous")
        if not amounts:
            ambiguities.append("missing_amount")
        if "half" in low or "remaining" in low or "balance" in low:
            ambiguities.append("relative_amount")

        # Confidence
        conf = 0.6 if tx_type != "unknown" else 0.2
        if parties:
            conf += 0.1
        if amounts:
            conf += 0.1

        return {
            "transaction_type": tx_type,
            "parties": parties,
            "amounts": amounts,
            "payment_method": payment,
            "references": [],
            "ambiguities": ambiguities,
            "grounding": {
                "all_fields_explicitly_grounded": len(ambiguities) == 0,
                "inferred_fields": ["payment_method"] if payment == "unknown" else [],
            },
        }


# ---------------------------------------------------------------------------
# Kernel evaluation (evaluation-only path)
# ---------------------------------------------------------------------------

def _kernel_evaluate(input_text: str) -> Dict[str, Any]:
    """Run the deterministic kernel on input text (evaluation-only).

    This is NOT a production path. It imports the orchestration module
    and runs it in isolation without writing to any database.
    """
    try:
        from backend.maths.fyjc_orchestration import orchestrate
        result = orchestrate(input_text)
        return {
            "status": result.get("status", "UNKNOWN"),
            "has_journal": bool(result.get("journal")),
            "journal_line_count": len(
                (result.get("journal") or {}).get("debit_lines", [])
            ) + len(
                (result.get("journal") or {}).get("credit_lines", [])
            ),
        }
    except Exception as e:
        return {"status": "EXCEPTION", "error": str(e), "has_journal": False, "journal_line_count": 0}


# ---------------------------------------------------------------------------
# Main evaluation engine
# ---------------------------------------------------------------------------

def evaluate_record(
    record: Dict[str, Any],
    source_tier: str,
    adapter: Any,
    use_kernel: bool = True,
) -> RecordResult:
    """Evaluate a single record against model prediction."""
    input_text = record.get("input", "")
    ground_truth = record.get("output", {})
    if isinstance(ground_truth, str):
        ground_truth = json.loads(ground_truth)
    metadata = record.get("_p4_metadata", {})
    problem_id = metadata.get("problem_id", hashlib.sha256(input_text.encode()).hexdigest()[:8])

    result = RecordResult(
        problem_id=problem_id,
        source_tier=source_tier,
        input_text=input_text,
    )

    # Get model prediction
    try:
        prediction = adapter.understand_transaction(input_text)
        # Normalize: adapter might return AIInterpretation or dict
        if isinstance(prediction, AIInterpretation):
            pred_dict = prediction.snapshot()
            # Flatten for comparison
            pred_compare = {
                "transaction_type": pred_dict.get("transaction_type", "unknown"),
                "parties": pred_dict.get("parties", []),
                "amounts": pred_dict.get("amounts", []),
                "payment_method": pred_dict.get("payment_method", "unknown"),
                "references": pred_dict.get("referenced_party", None),
                "ambiguities": [f.get("field_name", "") for f in pred_dict.get("ambiguity_flags", [])] if isinstance(pred_dict.get("ambiguity_flags"), list) else [],
                "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
            }
        elif isinstance(prediction, dict):
            pred_compare = prediction
        else:
            pred_compare = {"transaction_type": "unknown"}
        result.raw_model_output = json.dumps(pred_compare, default=str)
        result.parse_success = True
    except Exception as e:
        result.raw_model_output = f"PARSE_ERROR: {e}"
        result.parse_success = False
        result.failure_categories.append("parse_failure")
        return result

    # Field-level comparison
    fields_to_compare = ["transaction_type", "parties", "amounts", "payment_method", "references", "ambiguities"]
    for field_name in fields_to_compare:
        pred_val = pred_compare.get(field_name)
        gt_val = ground_truth.get(field_name)
        correct, ftype = _compare_field(pred_val, gt_val, field_name)
        result.field_results.append(FieldResult(
            field_name=field_name,
            correct=correct,
            prediction_value=pred_val,
            ground_truth_value=gt_val,
            failure_type=ftype,
        ))
        result.total_fields += 1
        if correct:
            result.correct_fields += 1

    # Grounding comparison
    pred_grounding = pred_compare.get("grounding", {})
    gt_grounding = ground_truth.get("grounding", {})
    if isinstance(pred_grounding, dict) and isinstance(gt_grounding, dict):
        result.grounding_correct = (
            pred_grounding.get("all_fields_explicitly_grounded")
            == gt_grounding.get("all_fields_explicitly_grounded")
            and set(pred_grounding.get("inferred_fields", []))
            == set(gt_grounding.get("inferred_fields", []))
        )
    else:
        result.grounding_correct = False

    # Ambiguity handling
    pred_amb = set(str(a).strip().lower() for a in pred_compare.get("ambiguities", []))
    gt_amb = set(str(a).strip().lower() for a in ground_truth.get("ambiguities", []))
    result.ambiguity_correct = pred_amb == gt_amb

    # Reference handling
    pred_ref = pred_compare.get("references", [])
    gt_ref = ground_truth.get("references", [])
    if isinstance(pred_ref, list) and isinstance(gt_ref, list):
        result.reference_correct = (
            {str(r).strip().lower() for r in pred_ref}
            == {str(r).strip().lower() for r in gt_ref}
        )
    else:
        result.reference_correct = str(pred_ref).strip() == str(gt_ref).strip()

    # Grounding safety checks
    violations = _check_grounding_safety(pred_compare, ground_truth)
    result.failure_categories.extend(violations)

    # Kernel evaluation (evaluation-only)
    if use_kernel:
        kernel_result = _kernel_evaluate(input_text)
        result.kernel_status = kernel_result.get("status", "UNKNOWN")
        result.kernel_verdict = json.dumps(kernel_result, default=str)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    all_results: Dict[str, List[RecordResult]],
    model_info: Dict[str, Any],
) -> str:
    """Generate the P5a evaluation report."""
    total = sum(len(v) for v in all_results.values())
    total_parse_ok = sum(1 for v in all_results.values() for r in v if r.parse_success)
    total_parse_fail = total - total_parse_ok
    total_correct_fields = sum(r.correct_fields for v in all_results.values() for r in v)
    total_fields = sum(r.total_fields for v in all_results.values() for r in v)
    total_full_match = sum(1 for v in all_results.values() for r in v if r.field_accuracy == 1.0)
    grounding_correct = sum(1 for v in all_results.values() for r in v if r.grounding_correct)
    ambiguity_correct = sum(1 for v in all_results.values() for r in v if r.ambiguity_correct)
    reference_correct = sum(1 for v in all_results.values() for r in v if r.reference_correct)

    # Failure categories
    all_violations = {}
    for v in all_results.values():
        for r in v:
            for fc in r.failure_categories:
                cat = fc.split(":")[0] if ":" in fc else fc
                all_violations[cat] = all_violations.get(cat, 0) + 1

    # Relative amount failures
    relative_amount_failures = []
    for tier_name, results in all_results.items():
        for r in results:
            for fr in r.field_results:
                if "remaining" in r.input_text.lower() or "half" in r.input_text.lower():
                    if not fr.correct and fr.field_name == "amounts":
                        relative_amount_failures.append(r)

    lines = []
    lines.append("# P5a — Specialist Model Evaluation Report\n")
    lines.append("## 1. Model Artifact\n")
    lines.append(f"| Field | Value |")
    lines.append(f"|-------|-------|")
    lines.append(f"| Base model | {model_info.get('base_model', 'Qwen/Qwen2.5-1.5B-Instruct')} |")
    lines.append(f"| Adapter | {model_info.get('adapter', 'NOT FOUND')} |")
    lines.append(f"| Adapter loaded | {model_info.get('adapter_loaded', False)} |")
    lines.append(f"| Evaluation adapter | {model_info.get('eval_adapter', 'deterministic-fallback')} |")
    lines.append(f"| Note | {model_info.get('note', 'No trained LoRA artifact found')} |")
    lines.append("")

    lines.append("## 2. Dataset Counts\n")
    lines.append("| Tier | Records |")
    lines.append("|------|--------:|")
    for tier_name, results in all_results.items():
        lines.append(f"| {tier_name} | {len(results)} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")

    lines.append("## 3. Overall Results\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total evaluated | {total} |")
    lines.append(f"| Valid JSON outputs | {total_parse_ok} |")
    lines.append(f"| Parse failures | {total_parse_fail} |")
    lines.append(f"| Complete interpretation matches | {total_full_match} |")
    lines.append(f"| Field-level accuracy | {total_correct_fields}/{total_fields} ({total_correct_fields/total_fields*100:.1f}%) |" if total_fields > 0 else "| Field-level accuracy | N/A |")
    lines.append(f"| Grounding correctness | {grounding_correct}/{total} ({grounding_correct/total*100:.1f}%) |" if total > 0 else "| Grounding correctness | N/A |")
    lines.append(f"| Ambiguity handling | {ambiguity_correct}/{total} ({ambiguity_correct/total*100:.1f}%) |" if total > 0 else "| Ambiguity handling | N/A |")
    lines.append(f"| Reference resolution | {reference_correct}/{total} ({reference_correct/total*100:.1f}%) |" if total > 0 else "| Reference resolution | N/A |")
    lines.append("")

    lines.append("## 4. Per-Tier Results\n")
    for tier_name, results in all_results.items():
        n = len(results)
        if n == 0:
            lines.append(f"### {tier_name.title()}\nNo records.\n")
            continue
        tier_correct = sum(r.correct_fields for r in results)
        tier_fields = sum(r.total_fields for r in results)
        tier_full = sum(1 for r in results if r.field_accuracy == 1.0)
        tier_grounding = sum(1 for r in results if r.grounding_correct)
        tier_ambiguity = sum(1 for r in results if r.ambiguity_correct)
        tier_parse = sum(1 for r in results if r.parse_success)

        lines.append(f"### {tier_name.title()} ({n} records)\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Parse success | {tier_parse}/{n} |")
        lines.append(f"| Complete matches | {tier_full}/{n} ({tier_full/n*100:.1f}%) |")
        lines.append(f"| Field accuracy | {tier_correct}/{tier_fields} ({tier_correct/tier_fields*100:.1f}%) |" if tier_fields > 0 else "| Field accuracy | N/A |")
        lines.append(f"| Grounding correct | {tier_grounding}/{n} ({tier_grounding/n*100:.1f}%) |")
        lines.append(f"| Ambiguity correct | {tier_ambiguity}/{n} ({tier_ambiguity/n*100:.1f}%) |")

        if n < 5:
            lines.append(f"\n> ⚠️ **Low sample size** ({n} records) — results are observational, not generalizable.\n")

        # Per-field breakdown
        field_stats = {}
        for r in results:
            for fr in r.field_results:
                if fr.field_name not in field_stats:
                    field_stats[fr.field_name] = {"correct": 0, "total": 0, "failures": {}}
                field_stats[fr.field_name]["total"] += 1
                if fr.correct:
                    field_stats[fr.field_name]["correct"] += 1
                else:
                    ft = fr.failure_type or "unknown"
                    field_stats[fr.field_name]["failures"][ft] = field_stats[fr.field_name]["failures"].get(ft, 0) + 1

        lines.append(f"\n**Field-level breakdown:**\n")
        lines.append(f"| Field | Correct | Total | Accuracy | Top Failure |")
        lines.append(f"|-------|--------:|------:|:--------:|-------------|")
        for fname, stats in field_stats.items():
            acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
            top_fail = max(stats["failures"].items(), key=lambda x: x[1]) if stats["failures"] else ("—", 0)
            lines.append(f"| {fname} | {stats['correct']} | {stats['total']} | {acc:.0f}% | {top_fail[0]} ({top_fail[1]}) |")
        lines.append("")

    lines.append("## 5. Failure Analysis\n")
    if all_violations:
        lines.append("| Failure Category | Count |")
        lines.append("|-----------------|------:|")
        for cat, count in sorted(all_violations.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {count} |")
    else:
        lines.append("No grounding safety violations detected.\n")
    lines.append("")

    # Specific failed cases
    lines.append("### Failed Records\n")
    lines.append("| Problem ID | Tier | Input (truncated) | Failed Fields |")
    lines.append("|-----------|------|-------------------|---------------|")
    for tier_name, results in all_results.items():
        for r in results:
            if r.field_accuracy < 1.0 and r.parse_success:
                failed_fields = [fr.field_name for fr in r.field_results if not fr.correct]
                input_short = r.input_text[:50] + ("..." if len(r.input_text) > 50 else "")
                lines.append(f"| {r.problem_id} | {tier_name} | {input_short} | {', '.join(failed_fields)} |")
    lines.append("")

    lines.append("## 6. Relative-Amount Test\n")
    if relative_amount_failures:
        lines.append(f"**The relative-amount failure REPRODUCED in {len(relative_amount_failures)} case(s):**\n")
        for r in relative_amount_failures:
            lines.append(f"- `{r.problem_id}`: input=`{r.input_text[:60]}...`")
    else:
        lines.append("**No relative-amount failures detected** in the evaluated records.\n")
        lines.append("> Note: the ambiguity and unsupported tiers contain few relative-amount examples.")
    lines.append("")

    lines.append("## 7. Kernel vs AI Separation\n")
    lines.append("| Source | Correct | Total | Accuracy |")
    lines.append("|--------|--------:|------:|:--------:|")
    lines.append(f"| AI interpretation | {total_correct_fields} | {total_fields} | {total_correct_fields/total_fields*100:.1f}% |" if total_fields > 0 else "| AI interpretation | N/A |")
    lines.append(f"| Kernel accounting truth | (not counted as AI evidence) | — | — |")
    lines.append("")
    lines.append("> The Kernel's deterministic result is NOT counted as evidence that the AI interpretation was correct.")
    lines.append("> The Kernel remains the sole authority for accounting truth.\n")

    lines.append("## 8. Decision\n")
    accuracy_pct = total_correct_fields / total_fields * 100 if total_fields > 0 else 0
    if accuracy_pct >= 80 and grounding_correct / max(total, 1) >= 0.7:
        verdict = "**PROMISING** — proceed to next gated integration design"
    elif accuracy_pct >= 50:
        verdict = "**INCONCLUSIVE** — more evaluation/training data required"
    else:
        verdict = "**NOT READY** — specialist model fails required grounding/interpretation criteria"
    lines.append(f"{verdict}\n")
    lines.append(f"Criteria: field accuracy {accuracy_pct:.1f}% (threshold: 80%), grounding {grounding_correct}/{total} (threshold: 70%)\n")

    lines.append("---\n*Generated by P5a evaluation harness. EVAL_MODE=True. No production state was modified.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_p5a_evaluation(use_kernel: bool = True) -> Dict[str, Any]:
    """Run the complete P5a evaluation."""
    print("=" * 70)
    print("Platrixa P5a — Specialist Model Evaluation")
    print("EVAL_MODE = True (no production state mutation)")
    print("=" * 70)

    # Step 1: Model artifact detection
    print("\n[1/5] Detecting model artifacts...")
    model_artifact = _find_model_artifact()
    model_info = {
        "base_model": _QWEN_BASE,
        "adapter": model_artifact.get("lora_adapter_path", "NOT FOUND"),
        "adapter_loaded": False,
        "eval_adapter": "deterministic-fallback",
        "note": "No trained LoRA artifact found on disk. Using deterministic fallback baseline.",
        "artifacts_found": model_artifact.get("found_artifacts", []),
        "transformers_installed": model_artifact.get("transformers_installed", False),
        "peft_installed": model_artifact.get("peft_installed", False),
    }

    if model_artifact.get("lora_adapter_available"):
        print(f"  LoRA adapter found at: {model_artifact['lora_adapter_path']}")
        print(f"  Attempting to load...")
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel

            print(f"  Loading base model: {_QWEN_BASE}")
            base_model = AutoModelForCausalLM.from_pretrained(
                _QWEN_BASE,
                torch_dtype="auto",
                device_map="auto",
                load_in_4bit=False,
            )
            tokenizer_load = AutoTokenizer.from_pretrained(_QWEN_BASE)

            print(f"  Loading LoRA adapter: {model_artifact['lora_adapter_path']}")
            lora_model = PeftModel.from_pretrained(base_model, model_artifact["lora_adapter_path"])
            lora_model.eval()

            # Create adapter wrapper for evaluation
            class _TrainedModelAdapter:
                def __init__(self, model, tok):
                    self._model = model
                    self._tok = tok
                    self._name = "trained-qwen-lora"
                    self._version = "p5a"

                @property
                def model_name(self):
                    return self._name

                @property
                def model_version(self):
                    return self._version

                def understand_transaction(self, text):
                    prompt = f"Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.\n\n### Instruction:\nParse the student's accounting language into a grounded structured interpretation. Do not invent missing information.\n\n### Input:\n{text}\n\n### Response:\n"
                    inputs = self._tok(prompt, return_tensors="pt").to(self._model.device)
                    with __import__("torch").no_grad():
                        outputs = self._model.generate(**inputs, max_new_tokens=256, use_cache=True)
                    response = self._tok.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
                    # Try to parse JSON from response
                    import re
                    json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group())
                    # Try parsing the full response
                    return json.loads(response)

            adapter = _TrainedModelAdapter(lora_model, tokenizer_load)
            model_info["eval_adapter"] = "trained-qwen-lora"
            model_info["note"] = "Trained LoRA adapter loaded successfully."
            model_info["adapter_loaded"] = True
            print(f"  ✅ Trained model loaded and ready for evaluation.")
        except Exception as e:
            print(f"  ⚠️  Failed to load trained model: {e}")
            print(f"  Falling back to deterministic-fallback baseline.")
            adapter = _DeterministicFallbackAdapter()
            model_info["note"] = f"LoRA adapter found but failed to load: {e}. Using fallback."
    else:
        print(f"  No LoRA adapter found.")
        print(f"  Searched: {model_artifact.get('searched_paths', [])}")
        print(f"  Transformers installed: {model_artifact.get('transformers_installed', False)}")
        print(f"  PEFT installed: {model_artifact.get('peft_installed', False)}")
        print(f"  Using deterministic-fallback baseline for evaluation.")

    # adapter was set above if model loaded successfully; default to fallback
    if not model_info["adapter_loaded"]:
        adapter = _DeterministicFallbackAdapter()

    # Step 2: Load evaluation tiers
    print("\n[2/5] Loading evaluation tiers...")
    tiers = {}
    for tier_name, tier_path in _EVAL_TIERS.items():
        records = _load_tier(tier_path)
        tiers[tier_name] = records
        print(f"  {tier_name}: {len(records)} records")

    total_records = sum(len(v) for v in tiers.values())
    print(f"  Total: {total_records} records")

    # Step 3: Evaluate all tiers
    print("\n[3/5] Evaluating all tiers...")
    all_results: Dict[str, List[RecordResult]] = {}
    for tier_name, records in tiers.items():
        print(f"\n  Evaluating {tier_name} ({len(records)} records)...")
        results = []
        for i, record in enumerate(records):
            r = evaluate_record(record, tier_name, adapter, use_kernel=use_kernel)
            results.append(r)
            status = "✓" if r.field_accuracy == 1.0 else f"✗ ({r.correct_fields}/{r.total_fields})"
            print(f"    [{i+1}/{len(records)}] {r.problem_id}: {status}")
        all_results[tier_name] = results

    # Step 4: Generate report
    print("\n[4/5] Generating evaluation report...")
    report = generate_report(all_results, model_info)
    report_path = _PROJECT_ROOT / "PLATRIXA_P5A_MODEL_EVALUATION_REPORT.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"  Report written to: {report_path}")

    # Step 5: Export machine-readable results
    print("\n[5/5] Exporting machine-readable results...")
    export_records = []
    for tier_name, results in all_results.items():
        for r in results:
            export_records.append({
                "problem_id": r.problem_id,
                "source_tier": r.source_tier,
                "input": r.input_text,
                "raw_model_output": r.raw_model_output,
                "parse_success": r.parse_success,
                "field_results": [
                    {
                        "field": fr.field_name,
                        "correct": fr.correct,
                        "prediction": str(fr.prediction_value)[:200],
                        "ground_truth": str(fr.ground_truth_value)[:200],
                        "failure_type": fr.failure_type,
                    }
                    for fr in r.field_results
                ],
                "field_accuracy": r.field_accuracy,
                "grounding_correct": r.grounding_correct,
                "ambiguity_correct": r.ambiguity_correct,
                "failure_categories": r.failure_categories,
                "kernel_status": r.kernel_status,
            })

    export_path = _PROJECT_ROOT / "training_data" / "p5a_evaluation_results.jsonl"
    with open(export_path, "w") as f:
        for rec in export_records:
            f.write(json.dumps(rec, default=str) + "\n")
    print(f"  Results written to: {export_path}")

    # Summary
    total_correct = sum(r.correct_fields for v in all_results.values() for r in v)
    total_fields = sum(r.total_fields for v in all_results.values() for r in v)
    total_full = sum(1 for v in all_results.values() for r in v if r.field_accuracy == 1.0)
    accuracy = total_correct / total_fields * 100 if total_fields > 0 else 0

    print(f"\n{'=' * 70}")
    print(f"RESULTS: {total_correct}/{total_fields} fields correct ({accuracy:.1f}%)")
    print(f"Complete matches: {total_full}/{total_records}")
    print(f"{'=' * 70}")

    return {
        "total_evaluated": total_records,
        "total_fields": total_fields,
        "correct_fields": total_correct,
        "accuracy_pct": accuracy,
        "complete_matches": total_full,
        "report_path": str(report_path),
        "results_path": str(export_path),
        "model_info": model_info,
        "all_results": all_results,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result = run_p5a_evaluation(use_kernel=True)

    # Print summary for test runner
    print(f"\nP5a evaluation complete.")
    print(f"Accuracy: {result['accuracy_pct']:.1f}%")
    print(f"Report: {result['report_path']}")
    sys.exit(0 if result["accuracy_pct"] > 0 else 1)
