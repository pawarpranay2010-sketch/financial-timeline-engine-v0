"""
Platrixa — ModelProvider boundary (Phase 7B)

This package is the ONE place where application-level model inference
responsibility lives for the FYJC specialist path.

Target contract:

    ModelProvider
        ↓
    generate / interpret structured model output (ExpandedInterpretation candidate)

The provider remains a candidate-output-only boundary:

    - produces structured semantic interpretation
    - does NOT decide accounting truth
    - does NOT create journal entries / debit lines / credit lines / ledger / balances
    - does NOT write to any persistent store

Downstream (Kernel / verification / grounding) decides what becomes trusted data.

Concrete implementation today:

    LocalHFModelProvider
        ↓
    LocalModelRunner (backend.maths.fyjc_local_model_runner)
        ↓
    Qwen2.5-1.5B-Instruct + Platrixa LoRA adapter

Model revisions are pinned. "Latest" is never the production behavior.

Importing this package does NOT download or load the model.
Model loading happens only on the first real inference call.
"""

from __future__ import annotations

from backend.model_provider.base import (
    ModelProvider,
    ModelProviderError,
    ModelUnavailableError,
    MalformedOutputError,
    ForbiddenAccountingFieldError,
    ProviderConfig,
    ProviderStatus,
)
from backend.model_provider.local_hf import LocalHFModelProvider

__all__ = [
    "ModelProvider",
    "ModelProviderError",
    "ModelUnavailableError",
    "MalformedOutputError",
    "ForbiddenAccountingFieldError",
    "ProviderConfig",
    "ProviderStatus",
    "LocalHFModelProvider",
]
