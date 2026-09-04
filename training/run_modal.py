#!/usr/bin/env python3
"""
Platrixa FYJC — Phase 6B Modal launcher (free $30/month Starter tier)
=====================================================================
Runs the EXACT Phase 6B TRL SFT + LoRA job (training/trl_sft_job.py)
inside a Modal container with a T4 16 GB GPU.

Why Modal: the free Starter plan includes $30/month of compute credits
(no credit card required, no payment method on file — verified against
modal.com/pricing and current 2026 sources). A full baseline run
(~30-50 min on a T4 at ~$0.59/hr) costs ~$0.30-0.50, i.e. ~1-2% of the
monthly free credit. The credit resets monthly.

Compatibility: written against the Modal >= 1.0 SDK (verified with the
modal client 1.5.5 installed locally). The pre-1.0 modal.Mount API was
removed in 1.0, so the job script is attached to the container Image with
Image.add_local_file(copy=True) instead of a Mount.

Nothing about the training changes vs the HF Jobs run: same base model
(Qwen/Qwen2.5-1.5B-Instruct pinned to BASE_MODEL_REVISION inside the job),
same pinned dataset revision (75f05fd...), same seed 3407 and
hyperparameters, same private adapter destination
(Pranay-20/platrixa-fyjc-specialist-v0.1). The job pushes the adapter to
the Hub before exiting, so nothing is lost when the container dies.

Token handling (never in source, never printed):
  1) One-time, from any terminal:
       modal secret create hf-token HF_TOKEN=<your hf_... token>
  2) This launcher declares secrets=[modal.Secret.from_name("hf-token")]
     and the job script reads the HF_TOKEN env var injected by Modal.

Run (from the repo root, after `pip install "modal>=1.0"`):
    modal run training/run_modal.py

Requires: a Modal account (signup.modal.com, GitHub login — free Starter
plan, no card). Container python 3.11 with pinned deps that match
training/trl_sft_job.py exactly. Age/terms: standard Modal ToS applies —
users under the age of majority should review eligibility with a parent or
guardian before creating an account.
"""

from __future__ import annotations

import os
import subprocess
import sys

import modal

PINNED_DEPS = [
    "torch==2.6.0",
    "transformers==5.16.1",
    "trl==1.12.0",
    "peft==0.20.0",
    "datasets==5.0.1",
    "accelerate==1.14.0",
    "huggingface_hub==1.30.0",
]

app = modal.App("platrixa-fyjc-specialist-v0.1")

# The job script is fully self-contained, so only it needs to be in the
# container. copy=True bakes the file into the image at build time (the
# pre-1.0 Mount mechanism is gone; add_local_file is the current API).
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(*PINNED_DEPS)
    .add_local_file(
        "training/trl_sft_job.py",
        remote_path="/platrixa/training/trl_sft_job.py",
        copy=True,
    )
)


@app.function(
    image=image,
    gpu="T4",                 # 16 GB VRAM — fp16 LoRA needs ~5-6 GB
    timeout=7200,             # 2h cap; Modal allows up to 24h
    ephemeral_disk=524288,        # MiB — model weights + HF cache + outputs
    secrets=[modal.Secret.from_name("hf-token")],
)
def run_job() -> None:
    """Run training/trl_sft_job.py unchanged inside the Modal container."""
    job = "/platrixa/training/trl_sft_job.py"
    env = dict(os.environ)  # Modal injects HF_TOKEN from the named secret
    result = subprocess.run(
        [sys.executable, job], cwd="/", env=env, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"job exited with code {result.returncode}")
    print("[modal-launcher] job completed successfully")


@app.local_entrypoint()
def main() -> None:
    run_job.remote()
