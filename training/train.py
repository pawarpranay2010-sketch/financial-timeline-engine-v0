#!/usr/bin/env python3
"""
Platrixa FYJC — Provider-Independent Training Script

Trains Qwen2.5-1.5B-Instruct with LoRA on FYJC accounting data.
Works on any CUDA GPU without provider-specific dependencies.

CPU-safe startup: validates config, dataset, and environment before
attempting GPU training. Clear error if no GPU is available.

Usage:
    python training/train.py
    python training/train.py --config training/config.yaml
    python training/train.py --epochs 5 --lr 1e-4
    python training/train.py --dry-run   # validate only, don't train
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# CPU-safe environment validation
# ---------------------------------------------------------------------------

def validate_environment() -> dict:
    """Validate that the training environment is ready.
    
    Returns a dict with status and details.
    Does NOT import any heavy ML libraries.
    """
    info = {
        "python_version": sys.version.split()[0],
        "gpu_available": False,
        "gpu_name": None,
        "gpu_memory_gb": None,
        "errors": [],
        "warnings": [],
    }

    # Check Python version
    if sys.version_info < (3, 9):
        info["errors"].append(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old. "
            "Requires Python 3.9+"
        )

    # Check GPU
    try:
        import torch
        info["gpu_available"] = torch.cuda.is_available()
        if info["gpu_available"]:
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["gpu_memory_gb"] = round(
                torch.cuda.get_device_properties(0).total_mem / (1024**3), 1
            )
        else:
            info["warnings"].append(
                "No CUDA GPU detected. Training requires a GPU."
            )
    except ImportError:
        info["warnings"].append("PyTorch not installed. Install with: pip install torch")

    # Check required packages
    required = ["transformers", "peft", "trl", "datasets", "accelerate"]
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            info["errors"].append(f"Missing package: {pkg}")

    return info


def validate_dataset(train_path: str, val_path: str) -> dict:
    """Validate that training datasets exist and are well-formed.
    
    CPU-safe: only reads JSONL metadata, no ML imports.
    """
    info = {
        "train_exists": False,
        "train_count": 0,
        "val_exists": False,
        "val_count": 0,
        "errors": [],
    }

    for name, path in [("train", train_path), ("val", val_path)]:
        p = Path(path)
        if not p.exists():
            info["errors"].append(f"{name} dataset not found: {path}")
            continue

        count = 0
        has_text = False
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                if not has_text:
                    try:
                        rec = json.loads(line)
                        if "text" in rec:
                            has_text = True
                    except json.JSONDecodeError:
                        pass

        if name == "train":
            info["train_exists"] = True
            info["train_count"] = count
        else:
            info["val_exists"] = True
            info["val_count"] = count

        if count == 0:
            info["errors"].append(f"{name} dataset is empty: {path}")
        if not has_text:
            info["errors"].append(
                f"{name} dataset lacks 'text' field. "
                "Run training/format.py first."
            )

    return info


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(config: dict, dry_run: bool = False):
    """Run the training pipeline.
    
    Args:
        config: Training configuration dict.
        dry_run: If True, only validate without training.
    """
    project_root = _PROJECT_ROOT
    data_dir = project_root / "training_data"
    output_dir = project_root / config.get("training", {}).get("output_dir", "training_output")

    # ── Validate environment ──
    print("=" * 60)
    print("Environment Validation")
    print("=" * 60)
    env = validate_environment()
    print(f"  Python: {env['python_version']}")
    print(f"  GPU: {env['gpu_name'] or 'None'}")
    if env["gpu_memory_gb"]:
        print(f"  GPU Memory: {env['gpu_memory_gb']} GB")

    for warn in env["warnings"]:
        print(f"  ⚠ {warn}")
    for err in env["errors"]:
        print(f"  ✗ {err}")

    # ── Validate dataset ──
    print("\n" + "=" * 60)
    print("Dataset Validation")
    print("=" * 60)
    train_path = str(data_dir / "specialist_train_formatted.jsonl")
    val_path = str(data_dir / "specialist_val_formatted.jsonl")
    ds = validate_dataset(train_path, val_path)
    print(f"  Train: {ds['train_count']} records ({train_path})")
    print(f"  Val:   {ds['val_count']} records ({val_path})")

    for err in ds["errors"]:
        print(f"  ✗ {err}")

    # ── Check for blockers ──
    blockers = env["errors"] + ds["errors"]
    if not env["gpu_available"]:
        blockers.append(
            "No CUDA GPU available. Training requires a GPU.\n"
            "You can develop the entire pipeline on CPU.\n"
            "When ready to train, run this script on a GPU machine."
        )

    if blockers:
        print("\n" + "=" * 60)
        print("BLOCKERS — Cannot start training")
        print("=" * 60)
        for i, blocker in enumerate(blockers, 1):
            print(f"  {i}. {blocker}")

        if not env["gpu_available"]:
            print("\nTo train on an external GPU:")
            print("  1. Copy this repository to a GPU machine")
            print("  2. pip install torch transformers peft trl datasets accelerate")
            print("  3. python training/train.py")

        if dry_run:
            print("\n[Dry run] Validation complete.")
            return

        sys.exit(1)

    if dry_run:
        print("\n[Dry run] All validations passed. Ready to train.")
        return

    # ── Actually train ──
    print("\n" + "=" * 60)
    print("Starting Training")
    print("=" * 60)

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTConfig, SFTTrainer
    from datasets import load_dataset

    # Load model
    model_name = config["model"]["name"]
    max_seq_length = config["model"]["max_seq_length"]
    load_in_4bit = config["model"].get("load_in_4bit", False)

    print(f"  Loading model: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map="auto",
        load_in_4bit=load_in_4bit,
    )

    # LoRA config
    lora_config = LoraConfig(
        r=config["lora"]["r"],
        lora_alpha=config["lora"]["alpha"],
        lora_dropout=config["lora"]["dropout"],
        target_modules=config["lora"]["target_modules"],
        bias=config["lora"]["bias"],
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Load datasets
    train_dataset = load_dataset("json", data_files=train_path, split="train")
    val_dataset = load_dataset("json", data_files=val_path, split="train")

    # Training config
    tc = config["training"]
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(output_dir),
        per_device_train_batch_size=tc["per_device_train_batch_size"],
        gradient_accumulation_steps=tc["gradient_accumulation_steps"],
        warmup_steps=tc["warmup_steps"],
        num_train_epochs=tc["num_train_epochs"],
        learning_rate=tc["learning_rate"],
        weight_decay=tc["weight_decay"],
        lr_scheduler_type=tc["lr_scheduler_type"],
        logging_steps=tc["logging_steps"],
        save_steps=tc["save_steps"],
        save_total_limit=tc["save_total_limit"],
        optim=tc["optim"],
        seed=tc["seed"],
        report_to=tc["report_to"],
        max_seq_length=max_seq_length,
        dataset_text_field="text",
        packing=False,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        args=training_args,
    )

    # Resume from checkpoint
    resume = tc.get("resume_from_checkpoint")
    print(f"\n  Training for {tc['num_train_epochs']} epochs...")
    print(f"  Learning rate: {tc['learning_rate']}")
    print(f"  Batch size: {tc['per_device_train_batch_size']}")
    print(f"  Gradient accumulation: {tc['gradient_accumulation_steps']}")
    start = time.time()
    trainer_stats = trainer.train(resume_from_checkpoint=resume)
    elapsed = time.time() - start

    # Save
    model.save_pretrained(str(output_dir / "lora_adapter"))
    tokenizer.save_pretrained(str(output_dir / "lora_adapter"))

    print(f"\nTraining complete in {elapsed:.0f}s")
    print(f"LoRA adapter saved to: {output_dir / 'lora_adapter'}")
    print(f"Final loss: {trainer_stats.training_loss:.4f}")

    # Save training metadata
    meta = {
        "model": model_name,
        "lora_rank": config["lora"]["r"],
        "lora_alpha": config["lora"]["alpha"],
        "epochs": tc["num_train_epochs"],
        "learning_rate": tc["learning_rate"],
        "train_records": len(train_dataset),
        "val_records": len(val_dataset),
        "final_loss": round(trainer_stats.training_loss, 4),
        "training_time_seconds": round(elapsed, 1),
        "gpu": env["gpu_name"],
    }
    with open(output_dir / "training_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Train Qwen2.5-1.5B-Instruct on FYJC accounting data"
    )
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config.yaml")
    parser.add_argument("--epochs", type=int, default=None,
                        help="Override number of epochs")
    parser.add_argument("--lr", type=float, default=None,
                        help="Override learning rate")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Override batch size")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Override output directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate only, don't train")
    args = parser.parse_args()

    # Load config
    config = _load_config(args.config)

    # Apply overrides
    if args.epochs is not None:
        config["training"]["num_train_epochs"] = args.epochs
    if args.lr is not None:
        config["training"]["learning_rate"] = args.lr
    if args.batch_size is not None:
        config["training"]["per_device_train_batch_size"] = args.batch_size
    if args.output_dir is not None:
        config["training"]["output_dir"] = args.output_dir

    train(config, dry_run=args.dry_run)


def _load_config(path: Optional[str] = None) -> dict:
    """Load configuration from YAML or use defaults."""
    # Default config
    config = {
        "model": {
            "name": "unsloth/Qwen2.5-1.5B-Instruct",
            "max_seq_length": 2048,
            "dtype": None,
            "load_in_4bit": False,
        },
        "lora": {
            "r": 16,
            "alpha": 16,
            "dropout": 0.0,
            "target_modules": [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ],
            "bias": "none",
            "use_rslora": False,
        },
        "training": {
            "output_dir": "training_output",
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 4,
            "warmup_steps": 5,
            "num_train_epochs": 3,
            "learning_rate": 2e-4,
            "weight_decay": 0.001,
            "lr_scheduler_type": "linear",
            "logging_steps": 1,
            "save_steps": 50,
            "save_total_limit": 3,
            "optim": "adamw_8bit",
            "seed": 3407,
            "report_to": "none",
            "resume_from_checkpoint": None,
        },
    }

    config_path = Path(path) if path else _PROJECT_ROOT / "training" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                loaded = yaml.safe_load(f)
            if loaded:
                # Deep merge
                for key in loaded:
                    if isinstance(loaded[key], dict) and key in config:
                        config[key].update(loaded[key])
                    else:
                        config[key] = loaded[key]
        except ImportError:
            print(f"⚠ PyYAML not installed. Using default config. "
                  f"Install with: pip install pyyaml")
        except Exception as e:
            print(f"⚠ Error loading config: {e}. Using defaults.")

    return config


if __name__ == "__main__":
    main()
