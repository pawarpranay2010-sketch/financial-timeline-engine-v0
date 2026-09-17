#!/usr/bin/env python3
"""
Platrixa FYJC — Phase 6C-v02 Controlled Three-System Evaluation
================================================================

Evaluates, on the IDENTICAL locked 100-example Phase 5 test set:

    SYSTEM BASE : Qwen/Qwen2.5-1.5B-Instruct @ 989aa7980e4cf806f80c7fef2b1adb7bc71aa306
    SYSTEM V01  : same base + Pranay-20/platrixa-fyjc-specialist-v0.1 @ b5c0a37cebc00e93144150dbbcaa7b28cadb259e
    SYSTEM V02  : same base + Pranay-20/platrixa-fyjc-specialist-v0.2 @ efa5075d16579d1e036779e69ecac5341d2c9d8e

Question answered: "Did v0.2 improve performance on Platrixa's task without
introducing hallucinated accounting conclusions or weakening the production
safety boundary?"

Protocol identity with historical Phase 6C (training/phase6c_evaluate.py):
  * The historical evaluator's pure scoring functions (extract_json,
    score_one, aggregate_scores, audit_hallucinations, audit_leakage,
    check_grounding_compatibility, compute_deltas) are IMPORTED — not forked —
    so every historical metric is computed with byte-identical semantics.
  * Inference reuses the historical run_inference(): identical prompts
    (SYSTEM_INSTRUCTION asserted equal to training/format.py), deterministic
    greedy decoding (do_sample=False), max_new_tokens=512, max_input 2048,
    identical for all three systems. The ONLY difference between systems is
    the LoRA adapter.
  * The historical evaluator file itself is untouched and must remain
    git-clean for this harness to run (protocol-identity gate).

New in v0.2 evaluation (explicitly additive, never replacing historical
metrics): every parsed model output is additionally passed through the
CURRENT production stack —

    validate_structured_interpretation(output, allow_expanded=True)  -> ValidationReport
    ExpandedGroundingGate().ground(interpretation, source_text)      -> GroundingResult

so schema validity, grounding validity and kernel safety are judged by the
same authoritative deterministic layer production uses. GroundingResult.summary
is a @property (never called).

This harness is evaluation-only. It must NEVER:
  - modify the locked test set, the original corpus, the hardcore dataset,
    the phase22 v0.2 training slice, or any production runtime file
  - write to training/, backend/, or any frozen artifact path
  - train, upload to Hugging Face, or push to git
  - feed test examples into training or tune anything on test labels

Outputs (all under reports/, evaluation-only):
    reports/phase6c_v02_base_predictions.jsonl
    reports/phase6c_v02_v01_predictions.jsonl
    reports/phase6c_v02_v02_predictions.jsonl
    reports/phase6c_v02_evaluation.json
    reports/phase6c_v02_evaluation.md

Usage:
    python3 scripts/fte_fyjc_74_phase6c_v02_evaluate.py --check-only   # no model load, no torch needed
    python3 scripts/fte_fyjc_74_phase6c_v02_evaluate.py                # full run: base + v0.1 + v0.2
    python3 scripts/fte_fyjc_74_phase6c_v02_evaluate.py --systems base,v02
    python3 scripts/fte_fyjc_74_phase6c_v02_evaluate.py --skip-inference   # rescore saved predictions
    python3 scripts/fte_fyjc_74_phase6c_v02_evaluate.py --limit 5      # plumbing smoke (artifacts suffixed _smoke)

Environment:
    HF_TOKEN — required on a full run to download the private adapters
    (read only via os.environ.get; never printed, logged, or written).
    PHASE6C_V02_BATCH_SIZE — optional batch size (default 8, historical default).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Historical Phase 6C evaluator — imported, never modified. Its pure scoring
# functions define the metric semantics for this evaluation.
from training import phase6c_evaluate as ev  # noqa: E402
from training.format import SYSTEM_INSTRUCTION as FORMAT_SYSTEM_INSTRUCTION  # noqa: E402

# Current production verification stack (read-only use).
from backend.maths.schema_verifier import validate_structured_interpretation  # noqa: E402
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate  # noqa: E402

# ---------------------------------------------------------------------------
# Pinned configuration — constants, never "latest"
# ---------------------------------------------------------------------------

TEST_SET_PATH = _PROJECT_ROOT / "training_data" / "fyjc_specialist_test.jsonl"
EXPECTED_TEST_SHA256 = "c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0"
EXPECTED_TEST_COUNT = 100

BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"

ADAPTER_V01_REPO = "Pranay-20/platrixa-fyjc-specialist-v0.1"
ADAPTER_V01_REVISION = "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"
ADAPTER_V02_REPO = "Pranay-20/platrixa-fyjc-specialist-v0.2"
ADAPTER_V02_REVISION = "efa5075d16579d1e036779e69ecac5341d2c9d8e"

# Generation configuration — IDENTICAL for all systems (historical Phase 6C).
MAX_NEW_TOKENS = 512
DO_SAMPLE = False
MAX_INPUT_TOKENS = 2048
BATCH_SIZE = int(os.environ.get("PHASE6C_V02_BATCH_SIZE", "8"))

# Protocol-identity: the historical evaluator's embedded system prompt must
# equal the training source of truth (training/format.py). Verified at import.
if ev.SYSTEM_INSTRUCTION != FORMAT_SYSTEM_INSTRUCTION:
    raise RuntimeError(
        "PROTOCOL IDENTITY FAILURE: training/phase6c_evaluate.py SYSTEM_INSTRUCTION "
        "no longer matches training/format.py SYSTEM_INSTRUCTION — the historical "
        "evaluation protocol has drifted. Refusing to run."
    )

# ---------------------------------------------------------------------------
# Frozen-artifact integrity targets (regression protection, section F)
# ---------------------------------------------------------------------------

FROZEN_DATASETS = {
    "locked_test": (TEST_SET_PATH, EXPECTED_TEST_SHA256),
    "original_corpus": (
        _PROJECT_ROOT / "training_data" / "fyjc_specialist_1000.jsonl",
        "feb7bfe5c1f415228d3ab9ccdc43beaa2df1498af8f4e66fc5856c441df61e24",
    ),
    "hardcore_dataset": (
        _PROJECT_ROOT / "training_data" / "fyjc_hardcore_1000.jsonl",
        "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd",
    ),
    "phase22_v02_train": (
        _PROJECT_ROOT / "training_data" / "phase22_v02_train.jsonl",
        "f0ba0efe9476618cc48368e57c02773cbe390f22b8e09b4d838af7e471f890d5",
    ),
}

# Production/protocol files that must remain byte-identical to git HEAD.
RUNTIME_FILES_VS_HEAD = [
    "backend/maths/fyjc_ai_specialist.py",
    "backend/maths/schema_verifier.py",
    "backend/maths/fyjc_grounding_gate.py",
    "training/phase6c_evaluate.py",   # historical evaluator — protocol identity
]

REPORTS_DIR = _PROJECT_ROOT / "reports"
PRED_FILES = {
    "base": REPORTS_DIR / "phase6c_v02_base_predictions.jsonl",
    "v01": REPORTS_DIR / "phase6c_v02_v01_predictions.jsonl",
    "v02": REPORTS_DIR / "phase6c_v02_v02_predictions.jsonl",
}
RESULTS_FILE = REPORTS_DIR / "phase6c_v02_evaluation.json"
REPORT_FILE = REPORTS_DIR / "phase6c_v02_evaluation.md"

SYSTEMS: Dict[str, Dict[str, Any]] = {
    "base": {"label": "base", "adapter_repo": None, "adapter_revision": None},
    "v01": {"label": "base+v0.1", "adapter_repo": ADAPTER_V01_REPO, "adapter_revision": ADAPTER_V01_REVISION},
    "v02": {"label": "base+v0.2", "adapter_repo": ADAPTER_V02_REPO, "adapter_revision": ADAPTER_V02_REVISION},
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(s: str) -> str:
    return " ".join(s.lower().split())


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(_PROJECT_ROOT), capture_output=True, text=True, check=True
    ).stdout.strip()


def working_tree_tracked_clean() -> Tuple[bool, str]:
    """True iff no tracked file is modified/staged (untracked files are the
    normal state of this repository and are ignored here)."""
    out = git("status", "--porcelain")
    tracked_dirty = [
        line for line in out.splitlines()
        if line.strip() and not line.startswith("??")
    ]
    return (len(tracked_dirty) == 0), "\n".join(tracked_dirty[:10])


def file_matches_head(repo_rel_path: str) -> Tuple[bool, str, str]:
    """Compare the working file's git blob hash with HEAD's blob hash."""
    head_blob = git("rev-parse", f"HEAD:{repo_rel_path}")
    work_blob = git("hash-object", str(_PROJECT_ROOT / repo_rel_path))
    return head_blob == work_blob, head_blob, work_blob


def load_test_records(path: Path) -> List[Dict[str, Any]]:
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))
    return recs


def save_predictions(predictions: List[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False, default=str) + "\n")


def load_predictions(path: Path) -> List[Any]:
    preds: List[Any] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                val = json.loads(line)
                preds.append(val if val is not None else None)
    return preds


# ---------------------------------------------------------------------------
# Integrity gates (section F — regression protection)
# ---------------------------------------------------------------------------


def run_integrity_gates() -> Tuple[bool, List[str]]:
    """All blocking gates. Returns (all_pass, failure_list)."""
    failures: List[str] = []

    # Gate 0: tracked working tree clean (protects "production unchanged").
    clean, dirty = working_tree_tracked_clean()
    if not clean:
        failures.append(f"tracked working tree is dirty:\n{dirty}")

    # Gate 1..4: frozen dataset SHAs (before the run).
    for name, (path, expected) in FROZEN_DATASETS.items():
        if not path.exists():
            failures.append(f"frozen artifact missing: {name} ({path})")
            continue
        actual = sha256_file(path)
        print(f"  [sha] {name}: {actual[:16]}… {'OK' if actual == expected else 'MISMATCH'}")
        if actual != expected:
            failures.append(f"{name} SHA-256 mismatch: expected {expected}, got {actual}")

    # Gate 5: production runtime + historical evaluator byte-identical to HEAD.
    for rel in RUNTIME_FILES_VS_HEAD:
        try:
            same, head_blob, work_blob = file_matches_head(rel)
        except subprocess.CalledProcessError as e:
            failures.append(f"git comparison failed for {rel}: {e}")
            continue
        print(f"  [head] {rel}: {'OK' if same else 'DIFFERS FROM HEAD'}")
        if not same:
            failures.append(f"{rel} differs from HEAD (head={head_blob[:12]}, work={work_blob[:12]})")

    # Gate 6: evaluation isolation — no locked-test input may appear in the
    # phase22 v0.2 training slice (exact, whitespace/case-normalized).
    train_inputs = set()
    with open(FROZEN_DATASETS["phase22_v02_train"][0], "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                train_inputs.add(norm_text(json.loads(line).get("input", "")))
    test_records = load_test_records(TEST_SET_PATH)
    contaminated = [r["id"] for r in test_records if norm_text(r.get("input", "")) in train_inputs]
    print(f"  [isolation] locked-test rows present in v0.2 training slice: {len(contaminated)}")
    if contaminated:
        failures.append(f"EVALUATION ISOLATION FAILURE: locked-test rows in training slice: {contaminated[:5]}")

    return (len(failures) == 0), failures


def verify_locked_test_schema(records: List[Dict[str, Any]]) -> List[str]:
    """Schema checks on the locked test set (read-only; mirrors historical checks)."""
    failures = []
    if len(records) != EXPECTED_TEST_COUNT:
        failures.append(f"locked test count {len(records)} != {EXPECTED_TEST_COUNT}")
    ids = [r.get("id") for r in records]
    if len(set(ids)) != len(ids):
        failures.append("duplicate ids in locked test set")
    for r in records:
        out = r.get("output")
        if not isinstance(out, dict) or set(out) < ev.VALID_18_FIELDS:
            failures.append(f"locked-test row {r.get('id')}: output missing 18-field contract")
            break
        if set(out) & ev.FORBIDDEN_FIELDS:
            failures.append(f"locked-test row {r.get('id')}: forbidden accounting keys in target")
            break
        if str(out.get("suggested_status", "")).upper() not in {"VERIFIED", "REVIEW_REQUIRED"}:
            failures.append(f"locked-test row {r.get('id')}: unexpected suggested_status")
            break
    return failures


# ---------------------------------------------------------------------------
# Adapter resolution (parameterized version of the historical resolver)
# ---------------------------------------------------------------------------


def resolve_adapter(repo: str, revision: str) -> str:
    """Snapshot-download the adapter at the pinned revision; fail closed."""
    from huggingface_hub import snapshot_download, HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "FATAL: HF_TOKEN environment variable is required to download the "
            "private adapter. The token is read from the environment only and is "
            "never printed, logged, or written. (No insecure fallback exists.)"
        )
    print(f"  Downloading adapter {repo} @ {revision[:12]}…")
    path = snapshot_download(repo, revision=revision, token=token)
    info = HfApi().repo_info(repo, repo_type="model", revision=revision)
    if info.sha != revision:
        raise RuntimeError(f"Adapter revision mismatch: requested {revision}, hub resolved {info.sha}")
    print(f"  Adapter cached at: {path} (hub commit {info.sha[:12]})")
    return path


# ---------------------------------------------------------------------------
# Production verification gates (NEW for v0.2 evaluation)
# ---------------------------------------------------------------------------


def production_gates_one(pred: Optional[Dict[str, Any]], raw_input: str) -> Dict[str, Any]:
    """Run the current production verifier + grounding gate on one model output.

    Read-only use of production code. GroundingResult.summary is a property.
    """
    result: Dict[str, Any] = {
        "verifier_valid": False,
        "verifier_status": "NOT_PARSED",
        "verifier_error_count": 0,
        "grounded": False,
        "safe_for_kernel": False,
        "review_required": False,
        "gate_suggested_status": None,
        "gate_issue_count": 0,
        "gate_top_issue": None,
    }
    if not isinstance(pred, dict):
        return result

    report = validate_structured_interpretation(pred, allow_expanded=True)
    result["verifier_valid"] = bool(report.valid)
    result["verifier_status"] = report.status.value
    result["verifier_error_count"] = len(report.errors)

    if report.valid and isinstance(report.parsed, dict):
        g = ExpandedGroundingGate().ground(report.parsed, raw_input)
        result["grounded"] = bool(g.grounded)
        result["safe_for_kernel"] = bool(g.safe_for_kernel)
        result["review_required"] = bool(g.review_required)
        result["gate_suggested_status"] = g.suggested_status
        result["gate_issue_count"] = len(g.issues)
        result["gate_top_issue"] = g.issues[0] if g.issues else None
    return result


def aggregate_production_gates(
    predictions: List[Any], records: List[Dict[str, Any]], label: str
) -> Dict[str, Any]:
    n = len(records)
    counters = Counter()
    status_counter: Counter = Counter()
    issue_counter: Counter = Counter()
    per_example: List[Dict[str, Any]] = []

    for pred, rec in zip(predictions, records):
        pg = production_gates_one(pred, rec.get("input", ""))
        status_counter[pg["verifier_status"]] += 1
        if pg["gate_top_issue"]:
            issue_counter[pg["gate_top_issue"].split(".")[0][:80]] += 1
        for k in ("verifier_valid", "grounded", "safe_for_kernel", "review_required"):
            if pg[k]:
                counters[k] += 1
        per_example.append({"id": rec.get("id", ""), **pg})

    return {
        "label": label,
        "total": n,
        "counts": dict(counters),
        "rates": {
            "verifier_valid_rate": round(counters["verifier_valid"] / n, 4),
            "grounding_pass_rate": round(counters["grounded"] / n, 4),
            "kernel_safe_rate": round(counters["safe_for_kernel"] / n, 4),
            "gate_review_required_rate": round(counters["review_required"] / n, 4),
        },
        "verifier_status_distribution": dict(status_counter),
        "top_gate_issues": dict(issue_counter.most_common(8)),
        "per_example": per_example,
    }


# ---------------------------------------------------------------------------
# Inference orchestration (reuses the historical run_inference verbatim)
# ---------------------------------------------------------------------------


def run_system(records: List[Dict[str, Any]], system_key: str) -> Tuple[List[Any], Dict[str, Any]]:
    sys_cfg = SYSTEMS[system_key]
    adapter_path = None
    if sys_cfg["adapter_repo"]:
        adapter_path = resolve_adapter(sys_cfg["adapter_repo"], sys_cfg["adapter_revision"])
    print(f"\n--- SYSTEM {system_key.upper()} ({sys_cfg['label']}) ---")
    preds, timing, err = ev.run_inference(
        records,
        model_id=BASE_MODEL,
        revision=BASE_MODEL_REVISION,
        adapter_path=adapter_path,
        batch_size=BATCH_SIZE,
        max_new_tokens=MAX_NEW_TOKENS,
    )
    if err:
        print(f"  FATAL: {err}")
        raise SystemExit(2)
    return preds, timing


# ---------------------------------------------------------------------------
# Scoring + report
# ---------------------------------------------------------------------------

CORE_METRIC_LABELS = [
    ("valid_json_rate", "Valid JSON rate"),
    ("valid_18field_schema_rate", "18-field schema (historical)"),
    ("transaction_type_accuracy", "Transaction-type accuracy"),
    ("party_exact_accuracy", "Party exact-set accuracy"),
    ("party_token_f1", "Party token F1"),
    ("amount_extraction_accuracy", "Amount extraction accuracy"),
    ("payment_method_accuracy", "Payment-method accuracy"),
    ("ambiguity_detection_agreement", "Ambiguity detection agreement"),
    ("suggested_status_agreement", "suggested_status agreement"),
    ("grounding_compatibility_rate", "Grounding compatibility (historical)"),
    ("accounting_leakage_rate", "Accounting leakage rate"),
    ("full_semantic_exact_match", "Full semantic exact match"),
]


def build_results(
    records: List[Dict[str, Any]],
    predictions: Dict[str, List[Any]],
    timings: Dict[str, Dict[str, Any]],
    test_sha: str,
    hw: Dict[str, Any],
) -> Dict[str, Any]:
    metrics, leakage, halluc, gcompat, pgates = {}, {}, {}, {}, {}
    for key, preds in predictions.items():
        metrics[key] = ev.aggregate_scores(preds, records, SYSTEMS[key]["label"])
        leakage[key] = ev.audit_leakage(preds, records, SYSTEMS[key]["label"])
        halluc[key] = ev.audit_hallucinations(preds, records, SYSTEMS[key]["label"])
        gcompat[key] = ev.check_grounding_compatibility(preds, records, SYSTEMS[key]["label"])
        pgates[key] = aggregate_production_gates(preds, records, SYSTEMS[key]["label"])

    deltas = {
        "base_to_v01": ev.compute_deltas(metrics["base"], metrics["v01"]),
        "base_to_v02": ev.compute_deltas(metrics["base"], metrics["v02"]),
        "v01_to_v02": ev.compute_deltas(metrics["v01"], metrics["v02"]),
    }

    # Per-example audit records: id + per-system key outcomes (no labels/inputs).
    audit_rows: List[Dict[str, Any]] = []
    parse_maps = {
        key: {rec.get("id", ""): isinstance(p, dict) for rec, p in zip(records, preds)}
        for key, preds in predictions.items()
    }
    gate_maps = {
        key: {p["id"]: p for p in pgates[key]["per_example"]}
        for key in predictions
    }
    score_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for key, preds in predictions.items():
        smap = {}
        for rec, p in zip(records, preds):
            s = ev.score_one(p, rec.get("output", {}), rec.get("input", ""))
            smap[rec.get("id", "")] = s
        score_maps[key] = smap
    for rec in records:
        rid = rec.get("id", "")
        row = {"id": rid}
        for key in predictions:
            s = score_maps[key][rid]
            g = gate_maps[key][rid]
            row[key] = {
                "parse_ok": parse_maps[key][rid],
                "schema_valid": s["schema_valid"],
                "tx_correct": s["tx_correct"],
                "parties_exact": s["parties_exact"],
                "amounts_exact": s["amounts_exact"],
                "payment_correct": s["payment_correct"],
                "ambiguity_agree": s["ambiguity_agree"],
                "status_agree": s["status_agree"],
                "accounting_leakage": s["accounting_leakage"],
                "verifier_valid": g["verifier_valid"],
                "verifier_status": g["verifier_status"],
                "grounded": g["grounded"],
                "safe_for_kernel": g["safe_for_kernel"],
                "gate_review_required": g["review_required"],
                "gate_issue_count": g["gate_issue_count"],
            }
        audit_rows.append(row)

    return {
        "phase": "6C-v02",
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": git("rev-parse", "HEAD"),
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "adapters": {
            "v01": {"repo": ADAPTER_V01_REPO, "revision": ADAPTER_V01_REVISION},
            "v02": {"repo": ADAPTER_V02_REPO, "revision": ADAPTER_V02_REVISION},
        },
        "test_set": {
            "path": str(TEST_SET_PATH),
            "sha256": test_sha,
            "count": len(records),
            "locked": True,
        },
        "generation_settings": {
            "do_sample": DO_SAMPLE,
            "max_new_tokens": MAX_NEW_TOKENS,
            "batch_size": BATCH_SIZE,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "note": "identical for all systems; deterministic greedy decoding (historical Phase 6C protocol)",
        },
        "evaluator": {
            "harness": "scripts/fte_fyjc_74_phase6c_v02_evaluate.py",
            "harness_sha256": sha256_file(Path(__file__).resolve()),
            "historical_evaluator": "training/phase6c_evaluate.py (imported, unmodified)",
            "historical_evaluator_sha256": sha256_file(_PROJECT_ROOT / "training" / "phase6c_evaluate.py"),
            "system_prompt_source": "training/format.py SYSTEM_INSTRUCTION (asserted == phase6c_evaluate.SYSTEM_INSTRUCTION)",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "probe": hw,
        },
        "integrity": {
            name: {"sha256": sha256_file(path), "expected_sha256": expected, "match": sha256_file(path) == expected}
            for name, (path, expected) in FROZEN_DATASETS.items()
        },
        "systems": {
            key: {
                "label": SYSTEMS[key]["label"],
                "adapter_repo": SYSTEMS[key]["adapter_repo"] or "none",
                "adapter_revision": SYSTEMS[key]["adapter_revision"] or "none",
                "metrics": metrics[key],
                "leakage": {k: v for k, v in leakage[key].items() if k != "cases"},
                "hallucination": {k: v for k, v in halluc[key].items() if k != "details"},
                "grounding_compat": gcompat[key],
                "production_gates": {
                    **{k: v for k, v in pgates[key].items() if k != "per_example"},
                },
                "timing": timings.get(key, {}),
            }
            for key in predictions
        },
        "deltas": deltas,
        "per_example_audit": audit_rows,
    }


def derive_verdict(results: Dict[str, Any]) -> Tuple[str, str]:
    """Mechanical, threshold-explicit verdict comparing v0.2 against v0.1."""
    m01 = results["systems"]["v01"]["metrics"]["rates"]
    m02 = results["systems"]["v02"]["metrics"]["rates"]
    l01 = results["systems"]["v01"]["leakage"]
    l02 = results["systems"]["v02"]["leakage"]
    g01 = results["systems"]["v01"]["production_gates"]["rates"]
    g02 = results["systems"]["v02"]["production_gates"]["rates"]
    h02 = results["systems"]["v02"]["hallucination"]

    if l02["true_leakage_rate"] > 0.05:
        return "UNSAFE_REGRESSION", f"v0.2 accounting leakage rate {l02['true_leakage_rate']:.1%} > 5%"
    if (m02["valid_18field_schema_rate"] < m01["valid_18field_schema_rate"] - 0.05
            or l02["true_leakage_rate"] > l01["true_leakage_rate"] + 0.05):
        return "REGRESSED", "Material safety/contract regression vs v0.1 (>5pp)"
    if g02["grounding_pass_rate"] < g01["grounding_pass_rate"] - 0.10:
        return "REGRESSED", (
            f"Production grounding pass rate regressed vs v0.1: "
            f"{g01['grounding_pass_rate']:.1%} -> {g02['grounding_pass_rate']:.1%} (>10pp)"
        )
    improved = (m02["transaction_type_accuracy"] > m01["transaction_type_accuracy"] + 0.02
                or m02["full_semantic_exact_match"] > m01["full_semantic_exact_match"] + 0.02)
    marginal = (m02["transaction_type_accuracy"] > m01["transaction_type_accuracy"] + 0.01
                or m02["full_semantic_exact_match"] > m01["full_semantic_exact_match"] + 0.01)
    note = f" (invented-entity issues on v0.2: {h02['records_with_issues']})"
    if improved:
        return "IMPROVED", "Semantic performance improved vs v0.1 (TX or exact-match >+2pp) without safety regression" + note
    if marginal:
        return "PASS", "Marginal improvement vs v0.1 without safety regression" + note
    return "NO_SIGNIFICANT_CHANGE", "Differences vs v0.1 too small to establish meaningful improvement" + note


def write_markdown(results: Dict[str, Any], verdict: Tuple[str, str]) -> str:
    lines: List[str] = []
    lines.append("# Phase 6C-v02 — Base vs v0.1 vs v0.2 Controlled Evaluation\n")
    lines.append(f"**Date:** {results['benchmark_timestamp']}  |  **git HEAD:** `{results['git_head'][:12]}`\n")
    lines.append("## Configuration\n")
    lines.append(f"- **Base model:** `{BASE_MODEL}` @ `{BASE_MODEL_REVISION}`")
    lines.append(f"- **v0.1 adapter:** `{ADAPTER_V01_REPO}` @ `{ADAPTER_V01_REVISION}`")
    lines.append(f"- **v0.2 adapter:** `{ADAPTER_V02_REPO}` @ `{ADAPTER_V02_REVISION}`")
    lines.append(f"- **Locked test set:** `{results['test_set']['path']}` — {results['test_set']['count']} examples, "
                 f"SHA-256 `{results['test_set']['sha256']}`")
    lines.append(f"- **Decoding:** do_sample={DO_SAMPLE} (greedy), max_new_tokens={MAX_NEW_TOKENS}, "
                 f"batch={BATCH_SIZE}, identical for all systems")
    lines.append(f"- **System prompt:** training/format.py SYSTEM_INSTRUCTION (protocol-identity asserted)\n")

    lines.append("## Core Metrics (historical Phase 6C semantics)\n")
    lines.append("| Metric | Base | v0.1 | v0.2 | v02−v01 |")
    lines.append("|--------|-----:|-----:|-----:|--------:|")
    for key, label in CORE_METRIC_LABELS:
        b = results["systems"]["base"]["metrics"]["rates"].get(key)
        m1 = results["systems"]["v01"]["metrics"]["rates"].get(key)
        m2 = results["systems"]["v02"]["metrics"]["rates"].get(key)
        fmt = lambda v: f"{v:.1%}" if isinstance(v, (int, float)) else "—"
        d = ""
        if isinstance(m1, (int, float)) and isinstance(m2, (int, float)):
            dd = m2 - m1
            d = f"{dd:+.1%}" + (" ✅" if dd > 0.02 else (" ⚠️" if dd < -0.05 else ""))
        lines.append(f"| {label} | {fmt(b)} | {fmt(m1)} | {fmt(m2)} | {d} |")
    lines.append("")

    lines.append("## Production Verification Gates (current stack — NEW)\n")
    lines.append("Every parsed output passed through `validate_structured_interpretation(..., allow_expanded=True)` "
                 "and, when valid, `ExpandedGroundingGate().ground()`.\n")
    lines.append("| Gate | Base | v0.1 | v0.2 |")
    lines.append("|------|-----:|-----:|-----:|")
    for key, label in [
        ("verifier_valid_rate", "Schema verifier valid"),
        ("grounding_pass_rate", "Grounding gate PASS"),
        ("kernel_safe_rate", "Safe for kernel"),
        ("gate_review_required_rate", "Gate REVIEW_REQUIRED"),
    ]:
        vals = [results["systems"][s]["production_gates"]["rates"].get(key) for s in ("base", "v01", "v02")]
        lines.append(f"| {label} | " + " | ".join(f"{v:.1%}" if isinstance(v, (int, float)) else "—" for v in vals) + " |")
    lines.append("")

    lines.append("## Safety / Hallucination\n")
    lines.append("| Audit | Base | v0.1 | v0.2 |")
    lines.append("|-------|-----:|-----:|-----:|")
    rows = [
        ("True accounting leakage", lambda s: f"{results['systems'][s]['leakage']['true_leakage']} ({results['systems'][s]['leakage']['true_leakage_rate']:.1%})"),
        ("Invented parties", lambda s: str(results['systems'][s]['hallucination']['invented_parties'])),
        ("Invented amounts", lambda s: str(results['systems'][s]['hallucination']['invented_amounts'])),
        ("Unsupported VERIFIED claims", lambda s: str(results['systems'][s]['hallucination']['unsupported_certainty_claims'])),
        ("Failed ambiguity preservation", lambda s: str(results['systems'][s]['hallucination']['failed_ambiguity_preservation'])),
        ("Forbidden fields in output", lambda s: str(results['systems'][s]['metrics']['counts'].get('forbidden_field_records', 0))),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(s) for s in ("base", "v01", "v02")) + " |")
    lines.append("")

    lines.append("## Verdict\n")
    lines.append(f"### **{verdict[0]}**\n")
    lines.append(f"{verdict[1]}\n")
    lines.append("Verdict thresholds (explicit): leakage >5% ⇒ UNSAFE_REGRESSION; safety/schema regression >5pp or "
                 "grounding-pass regression >10pp vs v0.1 ⇒ REGRESSED; TX or exact-match improvement >+2pp ⇒ IMPROVED; "
                 ">+1pp ⇒ PASS; else NO_SIGNIFICANT_CHANGE.\n")
    lines.append("---")
    lines.append("Generated by `scripts/fte_fyjc_74_phase6c_v02_evaluate.py` — evaluation-only, no training, no uploads.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Check-only mode (no model load, no torch requirement)
# ---------------------------------------------------------------------------


def run_check_only(records: List[Dict[str, Any]], test_sha: str) -> int:
    print("\n=== PHASE 6C-v02 CHECK-ONLY (no model load) ===\n")
    local_failures: List[str] = []

    print("[1/7] locked test set")
    print(f"  path: {TEST_SET_PATH}")
    print(f"  count: {len(records)} (expected {EXPECTED_TEST_COUNT})")
    print(f"  sha256: {test_sha} {'OK' if test_sha == EXPECTED_TEST_SHA256 else 'MISMATCH'}")
    if test_sha != EXPECTED_TEST_SHA256:
        local_failures.append("locked test SHA mismatch")

    print("[2/7] locked test schema (read-only)")
    schema_fail = verify_locked_test_schema(records)
    if schema_fail:
        local_failures.extend(schema_fail)
    else:
        print("  all rows: 18-field contract, no forbidden keys, valid statuses, unique ids")

    print("[3/7] frozen dataset integrity")
    ok, failures = run_integrity_gates()
    local_failures.extend(x for x in failures if not x.startswith("tracked working tree"))

    print("[4/7] production verification stack imports")
    try:
        from backend.maths.schema_verifier import ValidationStatus  # noqa: F401
        print("  backend.maths.schema_verifier: OK (validate_structured_interpretation)")
        print("  backend.maths.fyjc_grounding_gate: OK (ExpandedGroundingGate, GroundingResult.summary is a property)")
    except Exception as e:  # noqa: BLE001
        local_failures.append(f"production stack import failed: {e}")

    print("[5/7] protocol identity")
    print(f"  phase6c_evaluate.SYSTEM_INSTRUCTION == training.format.SYSTEM_INSTRUCTION: "
          f"{ev.SYSTEM_INSTRUCTION == FORMAT_SYSTEM_INSTRUCTION}")
    print(f"  historical evaluator MAX_NEW_TOKENS={ev.MAX_NEW_TOKENS} DO_SAMPLE={ev.DO_SAMPLE} "
          f"(harness: {MAX_NEW_TOKENS}/{DO_SAMPLE})")

    print("[6/7] pinned adapter references")
    print(f"  v0.1: {ADAPTER_V01_REPO} @ {ADAPTER_V01_REVISION}")
    print(f"  v0.2: {ADAPTER_V02_REPO} @ {ADAPTER_V02_REVISION}")

    print("[7/7] adapter accessibility (metadata only; requires network + HF_TOKEN for private repos)")
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("  HF_TOKEN not set in this environment — adapter access UNVERIFIED here.")
        print("  This is expected on a credential-less host; a full run fails closed without it.")
    else:
        from huggingface_hub import HfApi
        for repo, rev, name in [
            (ADAPTER_V01_REPO, ADAPTER_V01_REVISION, "v0.1"),
            (ADAPTER_V02_REPO, ADAPTER_V02_REVISION, "v0.2"),
        ]:
            try:
                info = HfApi().repo_info(repo, repo_type="model", revision=rev, token=token)
                files = [s.rfilename for s in (info.siblings or [])]
                has_weights = any("adapter_model" in f for f in files)
                ok_cfg = "adapter_config.json" in files
                print(f"  {name}: OK @ {info.sha[:12]} (weights={has_weights}, config={ok_cfg})")
                if info.sha != rev:
                    local_failures.append(f"{name} adapter revision unresolved: {info.sha}")
                if not (has_weights and ok_cfg):
                    local_failures.append(f"{name} adapter snapshot incomplete")
            except Exception as e:  # noqa: BLE001
                local_failures.append(f"{name} adapter not accessible: {e}")

    print("\n=== CHECK-ONLY RESULT ===")
    if local_failures:
        print("STATUS: FAIL")
        for f in local_failures:
            print(f"  - {f}")
        return 1
    print("STATUS: PASS — repository ready for the Phase 6C-v02 benchmark run")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6C-v02: base vs v0.1 vs v0.2 controlled evaluation")
    parser.add_argument("--systems", default="base,v01,v02",
                        help="comma-separated subset of base,v01,v02")
    parser.add_argument("--skip-inference", action="store_true",
                        help="rescore from saved reports/phase6c_v02_*_predictions.jsonl")
    parser.add_argument("--check-only", action="store_true",
                        help="verify integrity/config/adapters WITHOUT loading any model")
    parser.add_argument("--limit", type=int, default=None,
                        help="plumbing smoke only (artifacts suffixed _smoke; NEVER a benchmark)")
    args = parser.parse_args()

    requested = [s.strip() for s in args.systems.split(",") if s.strip()]
    for s in requested:
        if s not in SYSTEMS:
            parser.error(f"unknown system '{s}' (choose from base,v01,v02)")

    # ---- Blocking integrity gates BEFORE anything else ----
    print("=== PHASE 6C-v02 INTEGRITY GATES ===")
    ok, failures = run_integrity_gates()
    if not ok:
        print("\nFATAL: integrity gate failure — STOPPING (no files written)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(3)

    records = load_test_records(TEST_SET_PATH)
    test_sha = sha256_file(TEST_SET_PATH)
    schema_fail = verify_locked_test_schema(records)
    if schema_fail:
        print("\nFATAL: locked test schema verification failed — STOPPING")
        for f in schema_fail:
            print(f"  - {f}")
        sys.exit(3)
    print(f"\nLocked test set: {len(records)} examples, SHA-256 {test_sha} — verified")

    if args.check_only:
        sys.exit(run_check_only(records, test_sha))

    suffix = ""
    smoke = args.limit is not None
    if smoke:
        print(f"\nWARNING: --limit {args.limit} — SMOKE RUN ONLY (artifacts suffixed _smoke).")
        records = records[: args.limit]
        suffix = "_smoke"

    hw = ev.probe_hardware()
    print(f"Hardware: python={hw['python']} torch={hw.get('torch')} transformers={hw.get('transformers')} "
          f"peft={hw.get('peft')} cuda={hw.get('cuda_available')}"
          + (f" gpu={hw.get('gpu_name')}" if hw.get("cuda_available") else ""))

    predictions: Dict[str, List[Any]] = {}
    timings: Dict[str, Dict[str, Any]] = {}

    if not args.skip_inference:
        for key in requested:
            preds, timing = run_system(records, key)
            predictions[key] = preds
            timings[key] = timing
            out_path = PRED_FILES[key].with_name(PRED_FILES[key].stem + suffix + ".jsonl")
            save_predictions(preds, out_path)
            print(f"  Saved {len(preds)} predictions → {out_path.name}")
    else:
        for key in requested:
            path = PRED_FILES[key].with_name(PRED_FILES[key].stem + suffix + ".jsonl")
            if not path.exists():
                print(f"FATAL: --skip-inference requested but predictions missing: {path}")
                sys.exit(1)
            predictions[key] = load_predictions(path)
            print(f"  Loaded {len(predictions[key])} predictions from {path.name}")
        for key, preds in predictions.items():
            if len(preds) != len(records):
                print(f"FATAL: prediction/record count mismatch for {key}: {len(preds)} vs {len(records)}")
                sys.exit(1)

    print("\n--- COMPUTING METRICS (historical semantics + production gates) ---")
    results = build_results(records, predictions, timings, test_sha, hw)
    # For --limit runs the base-to-* deltas over a truncated set are meaningless.
    if smoke:
        results["deltas"] = {"note": "smoke run — deltas not meaningful"}

    verdict = derive_verdict(results) if set(requested) >= {"v01", "v02"} else ("INCOMPLETE", "run includes v01 and v02 to derive a verdict")
    results["verdict"] = {"status": verdict[0], "reason": verdict[1]}

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_FILE.with_name(RESULTS_FILE.stem + suffix + ".json")
    report_path = REPORT_FILE.with_name(REPORT_FILE.stem + suffix + ".md")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved: {results_path}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(write_markdown(results, verdict))
    print(f"Report saved: {report_path}")

    # ---- Post-run integrity re-verification ----
    print("\n--- POST-RUN INTEGRITY ---")
    test_sha_after = sha256_file(TEST_SET_PATH)
    count_after = len(load_test_records(TEST_SET_PATH))
    print(f"  locked test SHA-256 before: {test_sha}")
    print(f"  locked test SHA-256 after:  {test_sha_after}")
    print(f"  locked test count before/after: {len(records) if not smoke else EXPECTED_TEST_COUNT}/{count_after}")
    ok_after, failures_after = run_integrity_gates()
    if test_sha_after != test_sha or count_after != EXPECTED_TEST_COUNT or not ok_after:
        print("  ❌ FATAL: integrity changed during evaluation!")
        for f in failures_after:
            print(f"    - {f}")
        sys.exit(3)
    print("  ✅ All frozen artifacts and production runtime verified UNTOUCHED")

    print(f"\n{'=' * 64}")
    print(f"Phase 6C-v02 evaluation complete. Verdict: {verdict[0]}")
    print(f"{'=' * 64}")


if __name__ == "__main__":
    main()
