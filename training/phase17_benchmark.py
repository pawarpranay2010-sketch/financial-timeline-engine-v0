#!/usr/bin/env python3
"""
Platrixa — Phase 17 Independent ML Benchmark Harness
====================================================

Measures whether the model composes the COMPLETE semantic interpretation
Platrixa actually needs — transaction-level (compositional) correctness is
the headline metric; field accuracy is reported separately as diagnostic.

Dataset: training/phase17_benchmark.jsonl (LOCKED — hand-authored,
deterministic IDs PB-*, dataset version + sha256 recorded in every result).
Independence from training data is enforced by an overlap check against
every known training/eval corpus in the repository (ID-level AND
normalized-input-text level). A non-zero overlap FAILS the run.

Evaluation path per case (the production semantics, nothing benchmark-only):

    input text
      → adapter (pluggable; production = LocalHFModelProvider.interpret)
      → candidate dict (18-field contract)
      → StructuredInterpretationValidator   (schema validity metric)
      → ExpandedGroundingGate               (grounding compatibility metric)
      → deterministic field comparison vs ground truth
      → compositional whole-transaction verdict (ALL required fields correct)

Counterfactual pairs additionally measure:
  - counterfactual sensitivity (did the model change the intended field?)
  - unrelated-field stability (did it preserve the fields that must not move?)

Usage:
    python3 training/phase17_benchmark.py                 # harness self-test
    python3 training/phase17_benchmark.py --adapter production
        # runs the real Qwen2.5-1.5B + LoRA production provider (needs RAM)

Pure evaluation: no training, no dataset mutation, no production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DATASET_PATH = _PROJECT_ROOT / "training" / "phase17_benchmark.jsonl"
DATASET_VERSION = "phase17-benchmark-v1"
RESULTS_PATH = _PROJECT_ROOT / "training" / "phase17_benchmark_results.jsonl"

# Every training/eval corpus the repository has ever produced. The benchmark
# must not overlap ANY of them (the 53-case P5A tiers included).
_TRAINING_CORPORA = [
    "training_data/specialist_clean_training.jsonl",
    "training_data/specialist_train.jsonl",
    "training_data/specialist_val.jsonl",
    "training_data/specialist_test.jsonl",
    "training_data/specialist_ambiguity_eval.jsonl",
    "training_data/specialist_unsupported_eval.jsonl",
    "training_data/specialist_robustness_eval.jsonl",
    "training_data/fyjc_specialist_train.jsonl",
    "training_data/fyjc_specialist_validation.jsonl",
    "training_data/fyjc_specialist_test.jsonl",
    "training_data/training_20260828_072626.jsonl",
    "training_data/training_20260828_092036.jsonl",
    "training_data/eval_20260828_092036.jsonl",
    "platrixa_ai_candidate_cases.jsonl",
]

_FIELD_COMPARATORS = ("transaction_type", "parties", "amounts",
                      "payment_method", "ambiguities")


# ---------------------------------------------------------------------------
# Dataset loading + integrity
# ---------------------------------------------------------------------------

def dataset_sha256() -> str:
    """sha256 of the exact dataset file bytes (the lock fingerprint)."""
    return hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()


def load_dataset() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line in DATASET_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _norm_text(s: str) -> str:
    """Normalization for overlap checking (aggressive, deterministic)."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def check_independence(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify the benchmark overlaps NO training/eval corpus.

    Compares both deterministic IDs and normalized input texts.
    """
    bench_ids = {r["id"] for r in records}
    bench_inputs = {_norm_text(r["input"]) for r in records}
    id_overlaps: Dict[str, List[str]] = {}
    text_overlaps: Dict[str, int] = {}
    for rel in _TRAINING_CORPORA:
        path = _PROJECT_ROOT / rel
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = (rec.get("_p4_metadata") or {}).get("problem_id") or rec.get("id")
            if pid and pid in bench_ids:
                id_overlaps.setdefault(rel, []).append(pid)
            text = rec.get("input") or rec.get("raw_input") or ""
            if text and _norm_text(text) in bench_inputs:
                text_overlaps[rel] = text_overlaps.get(rel, 0) + 1
    return {
        "independent": not id_overlaps and not text_overlaps,
        "id_overlaps": id_overlaps,
        "text_overlaps": text_overlaps,
        "corpora_checked": [rel for rel in _TRAINING_CORPORA
                            if (_PROJECT_ROOT / rel).exists()],
    }


# ---------------------------------------------------------------------------
# Deterministic field comparison
# ---------------------------------------------------------------------------

def _amount_set(values: Any) -> set:
    out = set()
    if isinstance(values, list):
        for v in values:
            if isinstance(v, dict):
                v = v.get("value")
            try:
                out.add(str(Decimal(str(v))))
            except (InvalidOperation, ValueError, TypeError):
                out.add(str(v).strip().lower())
    return out


def _party_set(values: Any) -> set:
    if isinstance(values, list):
        return {str(p).strip().lower() for p in values if str(p).strip()}
    return set()


def _ambiguity_set(values: Any) -> set:
    if not isinstance(values, list):
        values = []
    normalized = {str(a).strip().upper() for a in values if str(a).strip()}
    normalized.discard("")
    if not normalized or normalized == {"NONE"}:
        return set()  # "no ambiguity" — canonical form
    return normalized


def _enum(value: Any) -> str:
    return str(value or "").strip().upper()


def compare_field(field_name: str, prediction: Any, expected: Any) -> bool:
    """Deterministic per-field correctness for the benchmark contract."""
    if field_name == "transaction_type":
        return _enum(prediction) == _enum(expected)
    if field_name == "payment_method":
        return _enum(prediction) == _enum(expected)
    if field_name == "parties":
        return _party_set(prediction) == _party_set(expected)
    if field_name == "amounts":
        return _amount_set(prediction) == _amount_set(expected)
    if field_name == "ambiguities":
        return _ambiguity_set(prediction) == _ambiguity_set(expected)
    # Unknown field: strict equality (fail-closed — never silently pass)
    return prediction == expected


def _get_candidate_field(candidate: Dict[str, Any], field_name: str) -> Any:
    if field_name == "transaction_type":
        return candidate.get("transaction_type_enum") or candidate.get("transaction_type")
    if field_name == "payment_method":
        return candidate.get("payment_method_enum") or candidate.get("payment_method")
    if field_name == "ambiguities":
        return candidate.get("ambiguities")
    return candidate.get(field_name)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------

class DeterministicTestAdapter:
    """Tiny deterministic adapter for METRIC PLUMBING TESTS ONLY.

    Returns hand-mapped outputs for a handful of inputs and an explicit
    UNKNOWN interpretation otherwise. This is NOT the model and NOT an
    interpretation engine — it exists so the metric layer can be tested
    without any model. Dataset-adapter overlap is deliberately tiny.
    """

    _MAP = {
        "purchased goods from raj for rs20000 in cash": {
            "transaction_type": "PURCHASE",
            "parties": ["Raj"],
            "amounts": [{"value": "20000", "currency": "INR", "source": "explicit"}],
            "payment_method": "CASH",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
            "transaction_type_enum": "PURCHASE",
            "payment_method_enum": "CASH",
            "ambiguity_flags": ["NONE"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.90",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        },
    }

    def interpret(self, text: str) -> Dict[str, Any]:
        base = self._MAP.get(_norm_text_key(text))
        if base is None:
            return {
                "transaction_type": "UNKNOWN", "transaction_type_enum": "UNKNOWN",
                "parties": [], "amounts": [],
                "payment_method": "UNKNOWN", "payment_method_enum": "UNKNOWN",
                "references": [], "ambiguities": [],
                "ambiguity_flags": ["NONE"],
                "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
                "field_confidences": [], "overall_confidence": "0.10",
                "suggested_status": "REVIEW_REQUIRED", "safety_flags": ["NONE"],
                "scope_flags": ["SINGLE_TRANSACTION"],
                "referenced_transaction_index": None,
                "referenced_party": None, "referenced_amount": None,
            }
        return dict(base)


def _norm_text_key(text: str) -> str:
    lowered = " ".join(str(text).lower().split())
    return lowered.replace(",", "").replace(".", "")


class ProductionAdapter:
    """The EXACT production inference path: LocalHFModelProvider.interpret.

    Raises RuntimeError with the underlying reason when the model cannot
    run (missing deps, OOM, load failure) — the benchmark records that as
    an infrastructure-blocked run, never as model failure.
    """

    def __init__(self) -> None:
        from backend.model_provider.local_hf import LocalHFModelProvider

        self._provider = LocalHFModelProvider()

    def model_identity(self) -> Dict[str, Any]:
        st = self._provider.status()
        return {
            "model_id": st.model_id,
            "base_model_revision": st.base_model_revision,
            "adapter_repo_id": st.adapter_repo_id,
            "adapter_revision": st.adapter_revision,
            "available": st.available,
            "loadable": st.loadable,
            "reason": st.reason,
        }

    def interpret(self, text: str) -> Dict[str, Any]:
        result = self._provider.interpret(text)  # may raise ModelProviderError
        return dict(result.candidate)


# ---------------------------------------------------------------------------
# Case evaluation
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    category: str
    input_text: str
    schema_valid: bool = False
    schema_errors: List[str] = dc_field(default_factory=list)
    grounding_safe: bool = False
    grounding_issues: List[str] = dc_field(default_factory=list)
    field_correct: Dict[str, bool] = dc_field(default_factory=dict)
    transaction_correct: bool = False
    error: str = ""
    latency_ms: float = 0.0
    raw_candidate_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "input": self.input_text,
            "schema_valid": self.schema_valid,
            "schema_errors": self.schema_errors,
            "grounding_safe": self.grounding_safe,
            "grounding_issues": self.grounding_issues,
            "field_correct": dict(self.field_correct),
            "transaction_correct": self.transaction_correct,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "raw_candidate_digest": self.raw_candidate_digest,
        }


def evaluate_case(record: Dict[str, Any], adapter: Any) -> CaseResult:
    """Evaluate ONE benchmark case through the production semantics."""
    result = CaseResult(
        case_id=record["id"],
        category=record["category"],
        input_text=record["input"],
    )
    started = time.perf_counter()
    try:
        candidate = adapter.interpret(record["input"])
    except Exception as exc:  # infrastructure/adapter failure — recorded, never retried
        result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
    result.latency_ms = (time.perf_counter() - started) * 1000
    result.raw_candidate_digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    # Schema validity (candidate IR schema validity metric, §14.I)
    from backend.maths.schema_verifier import validate_structured_interpretation

    report = validate_structured_interpretation(candidate, allow_expanded=True)
    result.schema_valid = bool(report.valid)
    result.schema_errors = [e.issue for e in report.errors][:5]

    # Grounding compatibility (§14.J) — deterministic gate, never a second LLM
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

    gate = ExpandedGroundingGate()
    grounding = gate.ground(candidate, record["input"])
    result.grounding_safe = bool(grounding.safe_for_kernel)
    result.grounding_issues = list(grounding.issues)

    # Field + compositional correctness (§14–§15)
    gt = record["ground_truth"]
    all_correct = True
    for fname in _FIELD_COMPARATORS:
        required = fname in record.get("required_fields", [])
        ok = compare_field(fname, _get_candidate_field(candidate, fname), gt.get(fname))
        if required or ok:
            result.field_correct[fname] = ok
        if required and not ok:
            all_correct = False
    result.transaction_correct = all_correct
    return result


# ---------------------------------------------------------------------------
# Counterfactual metrics (§17)
# ---------------------------------------------------------------------------

def evaluate_counterfactuals(records: List[Dict[str, Any]],
                             case_results: Dict[str, CaseResult],
                             candidates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Counterfactual sensitivity + unrelated-field stability over pairs.

    sensitivity: the model's interpretation CHANGED in the intended fields
                 between the original and the counterfactual.
    stability:   the model's interpretation stayed EQUAL in the fields that
                 must not change.
    """
    pairs: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        if r["category"] == "counterfactual":
            pairs.setdefault(r["pair_id"], []).append(r)

    sensitivity_hits = 0
    stability_hits = 0
    stability_total = 0
    detail: List[Dict[str, Any]] = []

    for pair_id, members in sorted(pairs.items()):
        if len(members) != 2:
            continue
        a, b = members
        ca = candidates.get(a["id"])
        cb = candidates.get(b["id"])
        if ca is None or cb is None:
            continue
        changed_expected = set(a.get("expect_change") or [])
        stable_expected = set(a.get("expect_stable") or [])

        changed = {f for f in changed_expected
                   if not compare_field(f, _get_candidate_field(ca, f),
                                        _get_candidate_field(cb, f))}
        stable = {f for f in stable_expected
                  if compare_field(f, _get_candidate_field(ca, f),
                                   _get_candidate_field(cb, f))}
        sensitivity_hits += 1 if changed == changed_expected and changed else 0
        stability_hits += len(stable)
        stability_total += len(stable_expected)
        detail.append({
            "pair_id": pair_id,
            "sensitivity_ok": changed == changed_expected and bool(changed),
            "stable_ok": stable == stable_expected,
        })

    return {
        "pairs": len(pairs),
        "counterfactual_sensitivity": (
            sensitivity_hits / len(pairs) if pairs else 0.0
        ),
        "unrelated_field_stability": (
            stability_hits / stability_total if stability_total else 0.0
        ),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_benchmark(adapter: Any, collect_candidates: bool = False
                  ) -> Dict[str, Any]:
    records = load_dataset()
    sha = dataset_sha256()
    independence = check_independence(records)

    if not independence["independent"]:
        return {
            "status": "FAIL_INDEPENDENCE",
            "dataset_version": DATASET_VERSION,
            "dataset_sha256": sha,
            "independence": independence,
        }

    results: List[CaseResult] = []
    candidates: Dict[str, Dict[str, Any]] = {}
    for record in records:
        r = evaluate_case(record, adapter)
        results.append(r)
        if collect_candidates and not r.error:
            try:
                candidates[record["id"]] = dict(adapter.interpret(record["input"]))
            except Exception:
                pass

    total = len(results)
    infra_failures = [r for r in results if r.error]
    valid = [r for r in results if not r.error]
    tx_correct = [r for r in valid if r.transaction_correct]

    field_totals: Dict[str, Tuple[int, int]] = {}
    for r in valid:
        for fname, ok in r.field_correct.items():
            c, t = field_totals.get(fname, (0, 0))
            field_totals[fname] = (c + (1 if ok else 0), t + 1)

    by_category: Dict[str, Dict[str, Any]] = {}
    for r in valid:
        cat = by_category.setdefault(r.category, {"total": 0, "transaction_correct": 0})
        cat["total"] += 1
        if r.transaction_correct:
            cat["transaction_correct"] += 1

    cf = evaluate_counterfactuals(records, {r.case_id: r for r in results},
                                  candidates) if collect_candidates else {}

    summary = {
        "status": "OK" if not infra_failures else "INFRASTRUCTURE_BLOCKED",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": sha,
        "dataset_size": total,
        "independence": independence,
        "schema_validity_rate": (
            sum(1 for r in valid if r.schema_valid) / len(valid) if valid else 0.0
        ),
        "grounding_compatibility_rate": (
            sum(1 for r in valid if r.grounding_safe) / len(valid) if valid else 0.0
        ),
        "whole_transaction_accuracy": (
            len(tx_correct) / len(valid) if valid else 0.0
        ),
        "field_accuracy": {
            fname: (correct / tot if tot else 0.0)
            for fname, (correct, tot) in sorted(field_totals.items())
        },
        "by_category": by_category,
        "counterfactual": cf,
        "infrastructure_failures": [r.to_dict() for r in infra_failures],
        "latency_ms_avg": (
            sum(r.latency_ms for r in valid) / len(valid) if valid else 0.0
        ),
        "results": [r.to_dict() for r in results],
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 17 independent benchmark")
    parser.add_argument("--adapter", choices=["test", "production"], default="test")
    args = parser.parse_args()

    if args.adapter == "production":
        try:
            adapter = ProductionAdapter()
        except Exception as exc:
            print(f"PRODUCTION ADAPTER UNAVAILABLE: {type(exc).__name__}: {exc}")
            return 2
    else:
        adapter = DeterministicTestAdapter()

    summary = run_benchmark(adapter, collect_candidates=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"},
                     indent=2, default=str)[:4000])
    RESULTS_PATH.write_text(
        "\n".join(json.dumps(r, default=str) for r in summary["results"]) + "\n",
        encoding="utf-8",
    )
    print(f"\nresults written: {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
