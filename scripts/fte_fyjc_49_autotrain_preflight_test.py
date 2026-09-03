#!/usr/bin/env python3
"""
Platrixa — Phase 6A: AutoTrain/LoRA Preflight Tests
=====================================================

Tests for the Phase 6A preparation work (NO GPU / NO torch required):

  A. Phase 5 dataset artifacts exist with exact counts
  B. Canonical split records are well-formed (id/input/output/metadata,
     18-field contract, no forbidden fields, no overlap)
  C. Phase 5 formatted files carry valid JSON targets (no Python reprs)
  D. AutoTrain preparation (training/prepare_autotrain.py):
       - produces train.jsonl (800) / valid.jsonl (100) in the documented
         AutoTrain llm-sft "messages" format (chat_template=tokenizer)
       - assistant content == Phase 5 formatted target (no data drift)
       - fully deterministic (byte-identical across runs)
  E. training/autotrain_config.yaml references Qwen2.5-1.5B-Instruct with a
     valid current llm-sft / LoRA configuration and no embedded credentials
  F. training/evaluate_finetuned.py imports without torch and loads the test set
  G. Protected backend files are unchanged (contract / grounding / specialist)
  H. Regression: Phase 1-5 + legacy suites still pass
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set

# Repo root on path so backend + training packages import
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.maths.fyjc_contract import ALL_VALID_FIELDS

from training.prepare_autotrain import prepare as run_prepare

passed = 0
failed = 0
skipped = 0
results: List[tuple] = []


def _check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        results.append(("PASS", name, detail))
    else:
        failed += 1
        results.append(("FAIL", name, detail))


def _skip(name: str, reason: str):
    global skipped
    skipped += 1
    results.append(("SKIP", name, reason))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append(None)
    return rows


FORBIDDEN = {"journal", "debit_lines", "credit_lines", "ledger",
             "balances", "debit_account", "credit_account", "journal_entry"}
TRAIN_DATA = _PROJECT_ROOT / "training_data"
CONFIG_PATH = _PROJECT_ROOT / "training" / "autotrain_config.yaml"
PREPARE_PATH = _PROJECT_ROOT / "training" / "prepare_autotrain.py"
EVAL_PATH = _PROJECT_ROOT / "training" / "evaluate_finetuned.py"

print(f"\nPhase 6A preflight — dataset root: {TRAIN_DATA}")
print(f"Config: {CONFIG_PATH}")

# =========================================================================
# A. ARTIFACTS + COUNTS
# =========================================================================
print("\n" + "=" * 70)
print("A. PHASE 5 ARTIFACTS AND COUNTS")
print("=" * 70)

canonical_files = {
    "main": TRAIN_DATA / "fyjc_specialist_1000.jsonl",
    "train": TRAIN_DATA / "fyjc_specialist_train.jsonl",
    "val": TRAIN_DATA / "fyjc_specialist_validation.jsonl",
    "test": TRAIN_DATA / "fyjc_specialist_test.jsonl",
    "train_fmt": TRAIN_DATA / "fyjc_specialist_train_formatted.jsonl",
    "val_fmt": TRAIN_DATA / "fyjc_specialist_validation_formatted.jsonl",
    "test_fmt": TRAIN_DATA / "fyjc_specialist_test_formatted.jsonl",
}
for key, path in canonical_files.items():
    _check(f"A1.{key}: exists", path.exists(), str(path))

expected_counts = {"main": 1000, "train": 800, "val": 100, "test": 100}
for key, expected in expected_counts.items():
    n = sum(1 for _ in open(canonical_files[key]) if _.strip()) if canonical_files[key].exists() else -1
    _check(f"A2.{key}: {expected} records", n == expected, f"count={n}")

for key in ("train_fmt", "val_fmt", "test_fmt"):
    n = sum(1 for _ in open(canonical_files[key]) if _.strip()) if canonical_files[key].exists() else -1
    _check(f"A3.{key}: non-empty", n in (800, 100), f"count={n}")

# =========================================================================
# B. CANONICAL SPLIT QUALITY
# =========================================================================
print("\n" + "=" * 70)
print("B. CANONICAL SPLIT QUALITY")
print("=" * 70)

all_rows: Dict[str, List[Dict[str, Any]]] = {}
for key in ("train", "val", "test"):
    rows = load_jsonl(canonical_files[key])
    all_rows[key] = [r for r in rows if r is not None]
    _check(f"B1.{key}: all lines parse as JSON",
           len(rows) == len(all_rows[key]),
           f"parsed={len(all_rows[key])}/{len(rows)}")
    _check(f"B2.{key}: every record has id/input/output/metadata",
           all(all(k in r for k in ("id", "input", "output", "metadata"))
               for r in all_rows[key]))

for key in ("train", "val", "test"):
    rows = all_rows[key]
    bad_18 = [r["id"] for r in rows
              if not isinstance(r.get("output"), dict)
              or set(r["output"]) != ALL_VALID_FIELDS]
    _check(f"B3.{key}: outputs have exactly the 18 contract fields",
           not bad_18, f"bad={bad_18[:3]}")
    forbidden_hits = [r["id"] for r in rows
                      if isinstance(r.get("output"), dict)
                      and (FORBIDDEN & set(r["output"]))]
    _check(f"B4.{key}: no forbidden accounting fields", not forbidden_hits,
           f"bad={forbidden_hits[:3]}")


def _ids_inputs(rows: List[Dict[str, Any]]):
    ids: Set[str] = set()
    inputs: Set[str] = set()
    for r in rows:
        ids.add(r["id"])
        inputs.add(r["input"].strip().lower())
    return ids, inputs


tr_ids, tr_in = _ids_inputs(all_rows["train"])
va_ids, va_in = _ids_inputs(all_rows["val"])
te_ids, te_in = _ids_inputs(all_rows["test"])
_check("B5: train/val/test IDs disjoint",
       not (tr_ids & va_ids) and not (tr_ids & te_ids) and not (va_ids & te_ids))
_check("B6: train/val/test inputs disjoint",
       not (tr_in & va_in) and not (tr_in & te_in) and not (va_in & te_in))
union_main = load_jsonl(canonical_files["main"])
main_ids = {r["id"] for r in union_main if r}
_check("B7: split union == main dataset (1,000 ids)",
       len(main_ids) == 1000 and main_ids == tr_ids | va_ids | te_ids)

# =========================================================================
# C. FORMATTED FILES — VALID JSON TARGETS
# =========================================================================
print("\n" + "=" * 70)
print("C. FORMATTED FILES CARRY VALID JSON TARGETS")
print("=" * 70)

for key in ("train_fmt", "val_fmt", "test_fmt"):
    rows = load_jsonl(canonical_files[key])
    parsed_rows = [r for r in rows if r]
    _check(f"C1.{key}: every line parses", len(rows) == len(parsed_rows),
           f"parsed={len(parsed_rows)}/{len(rows)}")
    bad_marker = 0
    bad_json = 0
    for r in parsed_rows:
        text = r.get("text", "")
        if "### Instruction:" not in text or "### Response:" not in text:
            bad_marker += 1
            continue
        resp = text.split("### Response:\n", 1)[1].strip()
        try:
            parsed = json.loads(resp)
            if not isinstance(parsed, dict) or set(parsed) != ALL_VALID_FIELDS:
                bad_json += 1
        except json.JSONDecodeError:
            bad_json += 1
    _check(f"C2.{key}: all have instruction/response markers", bad_marker == 0,
           f"bad={bad_marker}")
    _check(f"C3.{key}: all targets are valid 18-field JSON (no reprs)",
           bad_json == 0, f"bad={bad_json}")

# =========================================================================
# D. AUTOTRAIN PREPARATION
# =========================================================================
print("\n" + "=" * 70)
print("D. AUTOTRAIN PREPARATION (messages format, parity, determinism)")
print("=" * 70)

_check("D0: prepare_autotrain.py exists", PREPARE_PATH.exists(), str(PREPARE_PATH))

tmp_a = Path(tempfile.mkdtemp(prefix="at_prep_a_"))
tmp_b = Path(tempfile.mkdtemp(prefix="at_prep_b_"))
try:
    run_prepare(out_dir=str(tmp_a / "autotrain_fyjc"),
                manifest_path=str(tmp_a / "phase6_manifest.json"),
                config_path=str(CONFIG_PATH))
    run_prepare(out_dir=str(tmp_b / "autotrain_fyjc"),
                manifest_path=str(tmp_b / "phase6_manifest.json"),
                config_path=str(CONFIG_PATH))

    for split, expected in (("train", 800), ("valid", 100)):
        fa = tmp_a / "autotrain_fyjc" / f"{split}.jsonl"
        fb = tmp_b / "autotrain_fyjc" / f"{split}.jsonl"
        rows_a = load_jsonl(fa)
        _check(f"D1.{split}: produced with {expected} rows",
               fa.exists() and len(rows_a) == expected, f"count={len(rows_a) if fa.exists() else -1}")

        _check(f"D2.{split}: deterministic (byte-identical)",
               fa.exists() and fb.exists() and fa.read_bytes() == fb.read_bytes())

        bad_shape = 0
        for r in rows_a:
            msgs = r.get("messages")
            if not isinstance(msgs, list) or len(msgs) != 3:
                bad_shape += 1
                continue
            roles = [m.get("role") for m in msgs]
            if roles != ["system", "user", "assistant"]:
                bad_shape += 1
                continue
            if any(not isinstance(m.get("content"), str) or not m["content"].strip()
                   for m in msgs):
                bad_shape += 1
        _check(f"D3.{split}: messages rows are [system,user,assistant]",
               bad_shape == 0, f"bad={bad_shape}")

        bad_target = 0
        for r in rows_a:
            content = r["messages"][2]["content"]
            try:
                out = json.loads(content)
                if not isinstance(out, dict) or set(out) != ALL_VALID_FIELDS:
                    bad_target += 1
            except json.JSONDecodeError:
                bad_target += 1
        _check(f"D4.{split}: assistant targets are valid 18-field JSON",
               bad_target == 0, f"bad={bad_target}")

    # Parity spot check vs the formatted twin for the first rows of each split.
    parity_bad = 0
    for split, fmt_key in (("train", "train_fmt"), ("valid", "val_fmt")):
        rows_a = load_jsonl(tmp_a / "autotrain_fyjc" / f"{split}.jsonl")
        fmt_rows = load_jsonl(canonical_files[fmt_key])
        for r, fr in zip(rows_a, fmt_rows):
            text = fr.get("text", "")
            idx = text.rfind("### Response:\n")
            embedded = text[idx + len("### Response:\n"):].strip() if idx >= 0 else ""
            if embedded != r["messages"][2]["content"]:
                parity_bad += 1
                break
    _check("D5: assistant JSON byte-parity with Phase 5 formatted targets",
           parity_bad == 0, f"drift_rows={parity_bad}")
finally:
    shutil.rmtree(tmp_a, ignore_errors=True)
    shutil.rmtree(tmp_b, ignore_errors=True)

# =========================================================================
# E. AUTOTRAIN CONFIG
# =========================================================================
print("\n" + "=" * 70)
print("E. AUTOTRAIN CONFIG SANITY (no credentials)")
print("=" * 70)

_check("E0: autotrain_config.yaml exists", CONFIG_PATH.exists(), str(CONFIG_PATH))

cfg_text = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else ""
try:
    import yaml  # type: ignore
    cfg = yaml.safe_load(cfg_text) if CONFIG_PATH.exists() else {}
    cfg_ok = isinstance(cfg, dict)
except Exception:  # pragma: no cover
    cfg = {}
    cfg_ok = False
_check("E1: config parses as YAML", cfg_ok)

_check("E2: task is llm-sft", cfg.get("task") == "llm-sft", str(cfg.get("task")))
_check("E3: base_model is Qwen/Qwen2.5-1.5B-Instruct",
       cfg.get("base_model") == "Qwen/Qwen2.5-1.5B-Instruct",
       str(cfg.get("base_model")))
_check("E4: backend is local", cfg.get("backend") == "local", str(cfg.get("backend")))

data_cfg = cfg.get("data") or {}
_check("E5: data.chat_template is tokenizer",
       data_cfg.get("chat_template") == "tokenizer",
       str(data_cfg.get("chat_template")))
cm = data_cfg.get("column_mapping") or {}
_check("E6: column_mapping.text_column is messages",
       cm.get("text_column") == "messages", str(cm.get("text_column")))

params = cfg.get("params") or {}
_check("E7: peft enabled", params.get("peft") is True, str(params.get("peft")))
_check("E8: quantization is a verified value (int4/int8/null)",
       params.get("quantization") in ("int4", "int8", None),
       str(params.get("quantization")))
_check("E9: target_modules is all-linear",
       params.get("target_modules") == "all-linear",
       str(params.get("target_modules")))
_check("E10: lora_r/lora_alpha/lora_dropout present and numeric",
       isinstance(params.get("lora_r"), int)
       and isinstance(params.get("lora_alpha"), int)
       and isinstance(params.get("lora_dropout"), float),
       f"r={params.get('lora_r')} a={params.get('lora_alpha')} d={params.get('lora_dropout')}")
_check("E11: epochs/lr/batch_size/gradient_accumulation present",
       params.get("epochs") is not None and params.get("lr") is not None
       and params.get("batch_size") is not None
       and params.get("gradient_accumulation") is not None)
_check("E12: seed is a fixed integer", isinstance(params.get("seed"), int),
       str(params.get("seed")))

hub = cfg.get("hub") or {}
_check("E13: push_to_hub is false", hub.get("push_to_hub") is False,
       str(hub.get("push_to_hub")))
secret_pattern = re.compile(
    r"(hf_[A-Za-z0-9]{10,}|api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9]{12,}|"
    r"token\s*[:=]\s*['\"][^'\"$]{8,}['\"])",
    re.IGNORECASE,
)
_check("E14: no embedded secrets/tokens in config",
       not secret_pattern.search(cfg_text), "no secret-looking literals found")

# =========================================================================
# F. EVALUATION SCRIPT (imports without torch, loads test data)
# =========================================================================
print("\n" + "=" * 70)
print("F. EVALUATION SCRIPT LOADABILITY")
print("=" * 70)

_check("F0: evaluate_finetuned.py exists", EVAL_PATH.exists(), str(EVAL_PATH))

if EVAL_PATH.exists():
    torch_before = "torch" in sys.modules
    spec_before = set(sys.modules)
    try:
        import training.evaluate_finetuned as ev  # type: ignore
        import_ok = True
    except Exception as e:  # pragma: no cover
        import_ok = False
        import_err = str(e)
    new_imports = set(sys.modules) - spec_before
    _check("F1: module imports without torch/transformers",
           import_ok and not any(
               m.startswith(("torch", "transformers", "peft"))
               for m in new_imports),
           ("" if import_ok else import_err) or f"heavy={sorted(m for m in new_imports if m.startswith('torch'))}")
    try:
        recs = ev.load_test_records(str(canonical_files["test"]))
        _check("F2: loads the 100-record test set",
               len(recs) == 100, f"count={len(recs)}")
        _check("F3: all test outputs are 18-field dicts",
               all(isinstance(r.get("output"), dict)
                   and set(r["output"]) == ALL_VALID_FIELDS for r in recs))
    except Exception as e:  # pragma: no cover
        _check("F2: loads the 100-record test set", False, str(e))
        _check("F3: all test outputs are 18-field dicts", False, str(e))

# =========================================================================
# G. PROTECTED FILES UNCHANGED
# =========================================================================
print("\n" + "=" * 70)
print("G. PROTECTED BACKEND FILES UNCHANGED")
print("=" * 70)

protected = [
    "backend/maths/fyjc_contract.py",
    "backend/maths/fyjc_grounding_gate.py",
    "backend/maths/fyjc_llm_specialist.py",
    "backend/maths/fyjc_local_model_runner.py",
    "backend/maths/fyjc_ai_specialist.py",
]
try:
    out = subprocess.run(
        ["git", "diff", "--quiet", "--"] + protected,
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
    )
    _check("G1: contract/grounding/specialist files unchanged",
           out.returncode == 0, f"git-exit={out.returncode}")
except Exception as e:  # pragma: no cover
    _skip("G1: contract/grounding/specialist files unchanged", f"git unavailable: {e}")

# =========================================================================
# H. REGRESSION (Phase 1-5 + legacy)
# =========================================================================
print("\n" + "=" * 70)
print("H. REGRESSION SUITES")
print("=" * 70)

regression = {
    "H1: Phase 5 dataset tests": "scripts/fte_fyjc_48_training_dataset_test.py",
    "H2: Phase 4 grounding tests": "scripts/fte_fyjc_47_grounding_migration_test.py",
    "H3: Phase 3 specialist tests": "scripts/fte_fyjc_46_real_ai_specialist_test.py",
    "H4: Phase 2 specialist tests": "scripts/fte_fyjc_45_ai_specialist_test.py",
    "H5: Phase 1 contract tests": "scripts/fte_fyjc_44_contract_expansion_test.py",
    "H6: Legacy unit tests": "scripts/fte_fyjc_41_contract_unit_tests.py",
    "H7: Legacy integration tests": "scripts/fte_fyjc_41_contract_integration_test.py",
}
for name, script in regression.items():
    rc = os.system(f"cd {_PROJECT_ROOT} && python3 {script} > /dev/null 2>&1")
    _check(f"{name}", rc == 0, f"exit={rc}")

# =========================================================================
# RESULTS
# =========================================================================
print("\n" + "=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)
for icon, name, detail in results:
    marker = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[icon]
    print(f"  {marker} {name}" + (f" — {detail}" if detail else ""))

print(f"\n{'=' * 70}")
print(f"  PASSED:  {passed}")
print(f"  FAILED:  {failed}")
print(f"  SKIPPED: {skipped}")
print(f"  TOTAL:   {passed + failed + skipped}")
print(f"{'=' * 70}")

if failed > 0:
    print("\n⚠️  FAILURES DETECTED")
    sys.exit(1)
else:
    print("\n✅ ALL PREFLIGHT TESTS PASSED")
    sys.exit(0)
