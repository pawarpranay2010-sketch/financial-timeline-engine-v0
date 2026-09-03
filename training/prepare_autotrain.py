#!/usr/bin/env python3
"""
Platrixa FYJC — AutoTrain Dataset Preparation (Phase 6A)
========================================================

Converts the Phase 5 canonical split files into the exact supervised
fine-tuning format consumed by AutoTrain Advanced llm-sft (v0.8.24):

    training_data/fyjc_specialist_train.jsonl        (800)
    training_data/fyjc_specialist_validation.jsonl   (100)
                    ↓
    training_data/autotrain_fyjc/train.jsonl         (800)
    training_data/autotrain_fyjc/valid.jsonl         (100)

Each row is the no_robots-style "messages" shape used with
`chat_template: tokenizer` and `text_column: messages` in the AutoTrain
config (verified against current AutoTrain docs and its llm-sft trainer):

    {"messages": [
        {"role": "system",    "content": <Phase 5 specialist instruction>},
        {"role": "user",      "content": <student natural-language input>},
        {"role": "assistant", "content": <compact 18-field JSON target>}
    ]}

The assistant content string is produced with the exact same serialization
used by training/format.py, so it is byte-identical to the JSON embedded in
the committed `*_formatted.jsonl` files (an integrity check verifies this).

Training target stays:  NATURAL LANGUAGE → 18-FIELD STRUCTURED JSON.
The model is NOT trained on journal entries / debit-credit / accounting truth.

Outputs are deterministic (stable ordering, fixed seed usage is inherited
from the Phase 5 split files; no timestamps in the data files).

Also writes training/phase6_manifest.json with dataset, library, hardware
and hyperparameter provenance (no credentials, ever).

Usage:
    python3 training/prepare_autotrain.py
    python3 training/prepare_autotrain.py --seed 3407
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Imports (repo-root aware so the script runs from anywhere in the repo)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from training.format import SYSTEM_INSTRUCTION  # module invocation
except ImportError:  # direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from format import SYSTEM_INSTRUCTION  # type: ignore

from backend.maths.fyjc_contract import ALL_VALID_FIELDS  # 18-field contract

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_ROLES = ("system", "user", "assistant")

# Fields the model must NEVER learn to emit (deterministic kernel territory).
FORBIDDEN_FIELDS = {
    "journal", "debit_lines", "credit_lines", "ledger",
    "balances", "debit_account", "credit_account", "journal_entry",
}

DEFAULT_SEED = 3407  # matches the repo's training/train.py seed


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load records from JSONL (skips blank lines, fails on malformed JSON)."""
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno} is not valid JSON: {e}")
    return rows


def write_jsonl(rows: List[Dict[str, Any]], path: str) -> None:
    """Write records to JSONL deterministically."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compact_json(value: Dict[str, Any]) -> str:
    """Serialize an output dict exactly like training/format.py does."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def build_messages(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build the AutoTrain 'messages' row for one canonical Phase 5 record."""
    output = record.get("output")
    if not isinstance(output, dict):
        raise ValueError(f"record {record.get('id', '?')}: output is not a dict")

    missing = ALL_VALID_FIELDS - set(output.keys())
    if missing:
        raise ValueError(
            f"record {record.get('id', '?')}: missing contract fields {sorted(missing)}"
        )
    forbidden = FORBIDDEN_FIELDS & set(output.keys())
    if forbidden:
        raise ValueError(
            f"record {record.get('id', '?')}: forbidden fields present {sorted(forbidden)}"
        )

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": record.get("input", "")},
            {"role": "assistant", "content": compact_json(output)},
        ]
    }


def prepare_split(
    canonical_path: str,
    formatted_path: str,
    out_path: str,
) -> Dict[str, Any]:
    """Convert one canonical split and verify byte-parity with the formatted file."""
    records = load_jsonl(canonical_path)
    messages_rows = [build_messages(r) for r in records]

    # Integrity check: assistant JSON must equal the JSON embedded in the
    # committed Phase 5 *_formatted.jsonl (same source order).
    formatted_rows = load_jsonl(formatted_path)
    if len(formatted_rows) != len(records):
        raise ValueError(
            f"{canonical_path} has {len(records)} rows but its formatted twin "
            f"has {len(formatted_rows)} rows"
        )
    drift = []
    for i, (row, fmt) in enumerate(zip(messages_rows, formatted_rows)):
        target = row["messages"][2]["content"]
        text = fmt.get("text", "")
        marker = "### Response:\n"
        idx = text.rfind(marker)
        embedded = text[idx + len(marker):].strip() if idx >= 0 else ""
        if embedded != target:
            drift.append(i)
            if len(drift) >= 5:
                break
    if drift:
        raise ValueError(
            f"{out_path}: assistant JSON differs from formatted target at "
            f"rows {drift} — dataset drift detected, aborting"
        )

    write_jsonl(messages_rows, out_path)
    return {
        "rows": len(messages_rows),
        "sha256": sha256_file(out_path),
        "file": str(out_path),
    }


# ---------------------------------------------------------------------------
# Environment / provenance probe (CPU-safe, no heavy imports)
# ---------------------------------------------------------------------------

def _probe_version(module_name: str) -> str:
    try:
        mod = importlib.import_module(module_name)
        return str(getattr(mod, "__version__", "installed (no __version__)"))
    except ImportError:
        return "NOT INSTALLED"


def probe_environment() -> Dict[str, Any]:
    """Best-effort environment probe. Never imports ML libs eagerly here."""
    info: Dict[str, Any] = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "gpu_available": False,
        "gpu_name": None,
        "gpu_vram_gb": None,
        "packages": {
            name: _probe_version(name)
            for name in [
                "torch", "transformers", "peft", "datasets", "accelerate",
                "bitsandbytes", "trl", "autotrain",
                "huggingface_hub",
            ]
        },
    }
    try:
        import torch  # noqa: PLC0415
        if torch.cuda.is_available():
            info["gpu_available"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["gpu_vram_gb"] = round(props.total_mem / (1024 ** 3), 1)
    except ImportError:
        pass
    return info


def load_hyperparams(config_path: Path) -> Dict[str, Any]:
    """Pull the params block from the AutoTrain config (best effort)."""
    if not config_path.exists():
        return {"error": f"config not found: {config_path}"}
    try:
        import yaml  # noqa: PLC0415
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            return {"error": "config did not parse to a mapping"}
        params = cfg.get("params") or {}
        data = cfg.get("data") or {}
        return {
            "task": cfg.get("task"),
            "base_model": cfg.get("base_model"),
            "project_name": cfg.get("project_name"),
            "backend": cfg.get("backend"),
            "data": data,
            "params": params,
        }
    except ImportError:
        return {"error": "pyyaml not installed; hyperparameters not read"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to read config: {e}"}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _relative_or_abs(path: Path, root: Path) -> str:
    """Path relative to the project root, or absolute when outside of it."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def prepare(
    out_dir: str = "training_data/autotrain_fyjc",
    manifest_path: str = "training/phase6_manifest.json",
    config_path: str = "training/autotrain_config.yaml",
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """Run the full Phase 6A dataset preparation. Returns the manifest."""
    root = _PROJECT_ROOT
    data_dir = root / "training_data"
    out = root / out_dir
    out.mkdir(parents=True, exist_ok=True)

    splits = [
        {
            "name": "train",
            "canonical": data_dir / "fyjc_specialist_train.jsonl",
            "formatted": data_dir / "fyjc_specialist_train_formatted.jsonl",
            "expected": 800,
        },
        {
            "name": "valid",
            "canonical": data_dir / "fyjc_specialist_validation.jsonl",
            "formatted": data_dir / "fyjc_specialist_validation_formatted.jsonl",
            "expected": 100,
        },
    ]

    prepared = {}
    total = 0
    for split in splits:
        for key in ("canonical", "formatted"):
            if not split[key].exists():
                raise FileNotFoundError(f"missing dataset file: {split[key]}")
        result = prepare_split(
            str(split["canonical"]),
            str(split["formatted"]),
            str(out / f"{split['name']}.jsonl"),
        )
        if result["rows"] != split["expected"]:
            raise ValueError(
                f"{split['name']} split: expected {split['expected']} rows, "
                f"got {result['rows']}"
            )
        total += result["rows"]
        prepared[split["name"]] = result

    # Optional: also convert the untouched test split for future evaluation
    # harness use (never part of the AutoTrain data dir).
    test_info = None
    test_canonical = data_dir / "fyjc_specialist_test.jsonl"
    test_formatted = data_dir / "fyjc_specialist_test_formatted.jsonl"
    if test_canonical.exists() and test_formatted.exists():
        rows = load_jsonl(str(test_canonical))
        if len(rows) != 100:
            raise ValueError(f"test split expected 100 rows, got {len(rows)}")
        test_info = {
            "rows": len(rows),
            "sha256": sha256_file(str(test_canonical)),
        }

    manifest = {
        "phase": "6A",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
        "training_method": "LoRA (PEFT) via AutoTrain Advanced llm-sft",
        "dataset": {
            "canonical_root": "training_data/fyjc_specialist_{train,validation,test}.jsonl",
            "phase5_commit": "e315b56",
            "train_count": prepared["train"]["rows"],
            "valid_count": prepared["valid"]["rows"],
            "test_count": (test_info or {}).get("rows"),
            "train_sha256": prepared["train"]["sha256"],
            "valid_sha256": prepared["valid"]["sha256"],
            "test_sha256": (test_info or {}).get("sha256"),
            "seed": seed,
            "train_target_format": "messages[{system,user,assistant}] JSONL for AutoTrain chat_template=tokenizer",
            "assistant_content": "compact 18-field ExpandedInterpretation JSON (byte-identical to Phase 5 formatted targets)",
            "forbidden_content": ["journal entries", "debit/credit decisions", "ledger postings", "balances", "accounting conclusions"],
        },
        "output": {
            "data_dir": _relative_or_abs(out, root),
            "train_file": prepared["train"]["file"],
            "valid_file": prepared["valid"]["file"],
        },
        "hyperparameters": load_hyperparams(root / config_path),
        "environment": probe_environment(),
        "credentials": "none stored in this manifest or any Phase 6 file",
    }

    manifest_path = root / manifest_path
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Prepared {total} rows (train + valid) in {out}")
    print(f"  train.jsonl : {prepared['train']['rows']} rows  "
          f"sha256={prepared['train']['sha256'][:16]}…")
    print(f"  valid.jsonl : {prepared['valid']['rows']} rows  "
          f"sha256={prepared['valid']['sha256'][:16]}…")
    print(f"Manifest written: {manifest_path}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare Phase 5 data for AutoTrain llm-sft"
    )
    parser.add_argument("--out-dir", default="training_data/autotrain_fyjc")
    parser.add_argument("--manifest", default="training/phase6_manifest.json")
    parser.add_argument("--config", default="training/autotrain_config.yaml")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    prepare(
        out_dir=args.out_dir,
        manifest_path=args.manifest,
        config_path=args.config,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
