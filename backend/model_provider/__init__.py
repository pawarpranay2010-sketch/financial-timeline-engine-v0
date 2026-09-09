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

Concrete implementations today:

    LocalHFModelProvider     (in-process HF runner; default when no endpoint is set)
    RemoteHFModelProvider    (Modal HTTP endpoint; selected when the env var
                              PLATRIXA_MODEL_ENDPOINT_URL is set)
    HFGradioModelProvider    (HF ZeroGPU Gradio Space named API; selected when
                              PLATRIXA_MODEL_ENDPOINT_URL is set AND
                              PLATRIXA_MODEL_TRANSPORT=gradio)

Selection lives in remote_hf.get_model_provider() — the single factory that
both the Kernel and API wiring consult. Importing this package does NOT
download or load any model and makes no network calls.
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
from backend.model_provider.remote_hf import RemoteHFModelProvider, get_model_provider
from backend.model_provider.hf_gradio import HFGradioModelProvider

__all__ = [
    "ModelProvider",
    "ModelProviderError",
    "ModelUnavailableError",
    "MalformedOutputError",
    "ForbiddenAccountingFieldError",
    "ProviderConfig",
    "ProviderStatus",
    "LocalHFModelProvider",
    "RemoteHFModelProvider",
    "HFGradioModelProvider",
    "get_model_provider",
]
