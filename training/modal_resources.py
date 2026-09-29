"""
Platrixa — Modal resource profiles (CPU inference / GPU training)
================================================================

WHY THIS MODULE EXISTS
----------------------
`training/modal_inference.py` historically hard-coded a single GPU class:

    _GPU = os.environ.get("PLATRIXA_MODAL_GPU", "T4")
    @app.cls(image=image, gpu=_GPU, ...)

That made *every* normal production interpretation run on a T4, even though
the model is small enough to serve on CPU. This module separates the two
concerns explicitly:

    NORMAL INFERENCE      -> CPU   (default, no GPU allocated)
    TRAINING / GPU EVAL   -> T4    (explicit, never inferred)

DESIGN RULES
------------
1. EXPLICIT, NOT AUTOMATIC. There is no CPU -> GPU failover anywhere. A CPU
   profile is a CPU profile; if it cannot serve a request it fails closed
   (503 MODEL_UNAVAILABLE) exactly as the adapter-load failure path already
   does. Falling back to GPU would make cost and capacity non-deterministic
   and would silently move a production workload onto a billable accelerator.

2. NO MODAL IMPORT. This module deliberately does not import `modal`, so the
   resolution logic is unit-testable in any environment, including CI images
   without the Modal SDK. `modal_inference.py` imports it; it does not own
   the rules.

3. NO SECRETS. Nothing here reads or stores a credential. Only the resource
   *shape* is resolved; endpoint tokens and HF tokens stay in Modal secrets /
   the Render environment and are never part of a resource profile.

4. INFRASTRUCTURE ONLY. A profile selects CPU count, memory, GPU class and
   compute dtype. It does not touch the base model revision, the adapter
   revision, the prompt format, the 18-field CandidateSemanticIR contract,
   grounding, authority routing, or VERIFIED semantics.

ENVIRONMENT VARIABLES
---------------------
    PLATRIXA_MODEL_RESOURCE   "cpu" (default) | "gpu"
    PLATRIXA_MODEL_GPU        GPU class, used ONLY when resource == "gpu".
                              Default "T4".
    PLATRIXA_MODAL_GPU        DEPRECATED alias for PLATRIXA_MODEL_GPU,
                              retained for backwards compatibility.

    Anything else is a hard configuration error. There is no defaulting of
    an unrecognised value to GPU and no warning-and-continue.

BACKWARDS COMPATIBILITY CAVEAT (deliberate, and the intended behaviour change)
-------------------------------------------------------------------------------
Before this change, a deployment that set only PLATRIXA_MODAL_GPU=T4 got a
GPU. After this change, PLATRIXA_MODEL_RESOURCE defaults to "cpu", so that
same deployment gets a CPU. A GPU inference deployment must now ALSO set
PLATRIXA_MODEL_RESOURCE=gpu. This is the objective of the change (normal
inference must not consume a GPU), not an accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

# ---------------------------------------------------------------------------
# Environment variable names
# ---------------------------------------------------------------------------

RESOURCE_ENV = "PLATRIXA_MODEL_RESOURCE"
GPU_ENV = "PLATRIXA_MODEL_GPU"
LEGACY_GPU_ENV = "PLATRIXA_MODAL_GPU"

VALID_RESOURCES = ("cpu", "gpu")
DEFAULT_RESOURCE = "cpu"
DEFAULT_GPU = "T4"

# ---------------------------------------------------------------------------
# CPU profile capacity — DERIVED, NOT BENCHMARKED
# ---------------------------------------------------------------------------
#
# These numbers are an engineering estimate from parameter-count arithmetic,
# NOT a measurement. No CPU Modal runtime was available in this environment,
# so no latency or throughput figure is claimed anywhere.
#
#   Qwen2.5-1.5B-Instruct  ~= 1.54e9 parameters
#     fp32 weights          ~= 6.2 GB   (CPU path: fp32, see note below)
#     bf16 weights          ~= 3.1 GB   (GPU path: unchanged from before)
#   KV cache, 2048 in / 512 out tokens, 1.5B model  ~= well under 1 GB
#   torch + transformers + PEFT runtime overhead   ~= 1.5-2 GB
#   ---------------------------------------------------------------
#   fp32 CPU working set  ~= 8-10 GB  -> 12 GiB requested for headroom
#
# WHY fp32 ON CPU: torch bfloat16 on CPU is either emulated (very slow) or
# requires AVX512-BF16 silicon. It is not portable, so the CPU path loads
# fp32. This is a *runtime numeric detail only* — same weights, same adapter,
# same greedy decoding, same prompt. It is called out explicitly because CPU
# output parity against the Phase 6C GPU evaluation has NOT been measured;
# see reports/ note in the module docstring of modal_inference.py.
CPU_COUNT = 4.0
CPU_MEMORY_MB = 12288  # 12 GiB
CPU_DTYPE = "float32"

# GPU path: unchanged from the pre-existing implementation. cpu/memory are
# left as None so that NO cpu/memory argument is passed to @app.cls, which
# preserves the existing GPU deployment exactly.
GPU_DTYPE = "bfloat16"


class ResourceConfigurationError(ValueError):
    """Raised for an invalid or contradictory resource configuration.

    Fails closed and loudly at import/deploy time. Never downgraded to a
    default, and never resolved to "use a GPU".
    """


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceProfile:
    """A fully-resolved Modal resource profile for one workload class."""

    workload: str            # "inference" | "training"
    resource: str            # "cpu" | "gpu"
    gpu: Optional[str]       # None on CPU; the requested class on GPU
    cpu: Optional[float]     # None => do not pass cpu= to Modal
    memory_mb: Optional[int]  # None => do not pass memory= to Modal
    dtype: str
    gpu_source_env: str = ""  # which env var supplied the GPU class

    @property
    def is_cpu(self) -> bool:
        return self.resource == "cpu"

    @property
    def is_gpu(self) -> bool:
        return self.resource == "gpu"

    def modal_gpu_arg(self):
        """Value for @app.cls/@app.function ``gpu=``. None means no GPU."""
        return self.gpu

    def as_public_dict(self) -> dict:
        """Non-secret description safe to expose on a health endpoint.

        Contains resource shape only — no endpoints, tokens, or credentials.
        """
        return {
            "workload": self.workload,
            "resource": self.resource,
            "gpu": self.gpu,
            "cpu": self.cpu,
            "memory_mb": self.memory_mb,
            "dtype": self.dtype,
        }

    def __str__(self) -> str:  # pragma: no cover - operator sugar
        if self.is_cpu:
            return f"cpu (cpu={self.cpu}, memory={self.memory_mb}MiB, dtype={self.dtype})"
        return f"gpu:{self.gpu} (dtype={self.dtype})"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    return str(value).strip()


def _resolve_gpu_class(env: Mapping[str, str]) -> tuple:
    """Return (gpu_class, source_env_var). Legacy alias honoured."""
    value = _get(env, GPU_ENV)
    if value:
        return value, GPU_ENV
    legacy = _get(env, LEGACY_GPU_ENV)
    if legacy:
        return legacy, LEGACY_GPU_ENV
    return DEFAULT_GPU, "default"


def resolve_resource(env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the resource selector. Default 'cpu'. Fails closed otherwise."""
    env = os.environ if env is None else env
    raw = _get(env, RESOURCE_ENV, DEFAULT_RESOURCE)
    if not raw:
        # An explicitly empty value is treated as unset -> CPU default.
        return DEFAULT_RESOURCE
    value = raw.lower()
    if value not in VALID_RESOURCES:
        raise ResourceConfigurationError(
            f"{RESOURCE_ENV}={raw!r} is not a valid resource. "
            f"Allowed values: {', '.join(VALID_RESOURCES)}. "
            f"Got {raw!r}."
        )
    return value


def resolve_inference_profile(
    env: Optional[Mapping[str, str]] = None,
) -> ResourceProfile:
    """Resolve the production INFERENCE profile. CPU unless GPU is asked for."""
    env = os.environ if env is None else env
    resource = resolve_resource(env)

    if resource == "cpu":
        # Hard guarantee: a CPU profile can never carry a GPU class, even if
        # PLATRIXA_MODEL_GPU is set. This is the "CPU path does not request a
        # GPU" invariant, enforced in code rather than by convention.
        return ResourceProfile(
            workload="inference",
            resource="cpu",
            gpu=None,
            cpu=CPU_COUNT,
            memory_mb=CPU_MEMORY_MB,
            dtype=CPU_DTYPE,
        )

    gpu_class, source = _resolve_gpu_class(env)
    if not gpu_class:
        raise ResourceConfigurationError(
            f"{RESOURCE_ENV}=gpu requires a GPU class via {GPU_ENV} "
            f"(or the deprecated {LEGACY_GPU_ENV})."
        )
    return ResourceProfile(
        workload="inference",
        resource="gpu",
        gpu=gpu_class,
        # None => not passed to Modal, preserving the previous GPU deployment.
        cpu=None,
        memory_mb=None,
        dtype=GPU_DTYPE,
        gpu_source_env=source,
    )


def resolve_training_profile(
    env: Optional[Mapping[str, str]] = None,
) -> ResourceProfile:
    """Resolve the TRAINING / GPU-EVALUATION profile. Always GPU.

    Training is not auto-detected: it is explicitly GPU-backed and keeps the
    pre-existing T4 requirement. There is no CPU training path and no
    inference of the requirement from the inference selector.
    """
    env = os.environ if env is None else env
    requested = _get(env, RESOURCE_ENV, "").lower()
    if requested == "cpu":
        # Explicitly refuse rather than silently training on CPU. The
        # hyperparameters, dataset and memory envelope in the SFT job were
        # established against a T4; running them on CPU is not a supported
        # configuration and must be a loud failure, not a slow surprise.
        raise ResourceConfigurationError(
            f"{RESOURCE_ENV}=cpu is not valid for training/evaluation. "
            f"The SFT job and its hyperparameters require a GPU. Unset "
            f"{RESOURCE_ENV} or set it to 'gpu'."
        )

    gpu_class, source = _resolve_gpu_class(env)
    if not gpu_class:
        raise ResourceConfigurationError(
            f"training/evaluation requires a GPU class via {GPU_ENV} "
            f"(or the deprecated {LEGACY_GPU_ENV})."
        )
    return ResourceProfile(
        workload="training",
        resource="gpu",
        gpu=gpu_class,
        cpu=None,
        memory_mb=None,
        dtype=GPU_DTYPE,
        gpu_source_env=source,
    )


__all__ = [
    "ResourceConfigurationError",
    "ResourceProfile",
    "resolve_resource",
    "resolve_inference_profile",
    "resolve_training_profile",
    "RESOURCE_ENV",
    "GPU_ENV",
    "LEGACY_GPU_ENV",
    "VALID_RESOURCES",
    "DEFAULT_RESOURCE",
    "DEFAULT_GPU",
    "CPU_COUNT",
    "CPU_MEMORY_MB",
    "CPU_DTYPE",
    "GPU_DTYPE",
]
