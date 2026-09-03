#!/usr/bin/env python3
"""
Platrixa FYJC — Phase 6B free-GPU launcher (Google Colab / Kaggle)
==================================================================
Runs the EXACT Phase 6B TRL SFT + LoRA job (training/trl_sft_job.py) on a
FREE GPU notebook:

  - Kaggle          : free tier, ~30 h/week GPU quota (T4/P100 class)
  - Google Colab    : free tier, T4 16 GB, session up to ~12 h

Nothing about the training changes vs the Hugging Face Jobs run: same base
model (Qwen/Qwen2.5-1.5B-Instruct), same pinned dataset revision
(75f05fd...), same seed 3407 and hyperparameters, same private adapter
destination (Pranay-20/platrixa-fyjc-specialist-v0.1). The adapter is
pushed to the Hub by the job before it exits, so the notebook session can
end afterwards without losing the artifact.

Token handling (never typed into code, never printed, never logged):
  Colab : notebook 🔑 "Secrets" panel  -> add  HF_TOKEN = hf_...
  Kaggle: "Add-ons -> Secrets"          -> add  HF_TOKEN = hf_...
The launcher reads the token from the platform secret store and exports it
to the environment for huggingface_hub. If HF_TOKEN is already exported it
is used as-is.

Usage:
  1) Upload these two files to the notebook (or clone the repo):
       training/run_sft_free_gpu.py
       training/trl_sft_job.py
  2) Add the HF_TOKEN secret in the platform secret store (see above).
  3) Run in a notebook cell:
       !python training/run_sft_free_gpu.py
     or, to check the environment first without launching:
       !python training/run_sft_free_gpu.py --check

Requires-python >= 3.11 (matches the job script). GPU requirement: any
CUDA GPU with >= 12 GB VRAM (T4 class or better); the job fails cleanly
if CUDA is unavailable.
"""

from __future__ import annotations

import os
import subprocess
import sys

PINNED_DEPS = [
    "torch==2.6.0",
    "transformers==5.16.1",
    "trl==1.12.0",
    "peft==0.20.0",
    "datasets==5.0.1",
    "accelerate==1.14.0",
    "huggingface_hub==1.30.0",
]

JOB_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trl_sft_job.py")


def detect_platform() -> str:
    try:
        import google.colab  # noqa: F401

        return "google-colab"
    except Exception:
        pass
    try:
        import kaggle_secrets  # noqa: F401

        return "kaggle"
    except Exception:
        pass
    return "other"


def resolve_token() -> str:
    """Return HF_TOKEN from env, Colab secrets, or Kaggle secrets. Never prints it."""
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from google.colab import userdata

        tok = str(userdata.get("HF_TOKEN") or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    try:
        from kaggle_secrets import UserSecretsClient

        tok = str(UserSecretsClient().get_secret("HF_TOKEN") or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    return ""


def check_env() -> None:
    print(f"[free-gpu] platform        : {detect_platform()}")
    print(f"[free-gpu] python          : {sys.version.split()[0]}")
    try:
        import torch

        print(f"[free-gpu] torch           : {torch.__version__}")
        print(f"[free-gpu] cuda_available  : {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            print(f"[free-gpu] gpu             : {props.name} "
                  f"({round(props.total_memory / 1e9, 1)} GB)")
    except Exception:
        print("[free-gpu] torch           : NOT INSTALLED (install step will add it)")
    try:
        import trl

        print(f"[free-gpu] trl             : {trl.__version__}")
    except Exception:
        print("[free-gpu] trl             : NOT INSTALLED (install step will add it)")
    print(f"[free-gpu] HF_TOKEN        : {'PRESENT' if resolve_token() else 'MISSING'}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    if "--check" in args:
        check_env()
        return

    platform = detect_platform()
    print(f"[free-gpu] platform: {platform}")

    token = resolve_token()
    if not token:
        print("[free-gpu] FATAL: HF_TOKEN not found. Add it to the platform secret "
              "store (Colab: Secrets panel; Kaggle: Add-ons -> Secrets) or export "
              "HF_TOKEN in the environment. The value is never printed.")
        sys.exit(1)

    if "--no-install" not in args:
        print(f"[free-gpu] installing pinned deps: {', '.join(PINNED_DEPS)}")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + PINNED_DEPS
        )
        if r.returncode != 0:
            print("[free-gpu] FATAL: dependency install failed")
            sys.exit(1)

    env = dict(os.environ)
    env["HF_TOKEN"] = token  # exported to the child job only; never printed

    print(f"[free-gpu] launching job: python {JOB_SCRIPT}")
    r = subprocess.run([sys.executable, JOB_SCRIPT], env=env)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()