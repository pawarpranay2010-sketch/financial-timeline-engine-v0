"""
Platrixa — Local HF ModelProvider (Phase 7B)

Concrete ModelProvider implementation around the existing local Hugging Face
model runner.

Architecture:

    LocalHFModelProvider
        ↓
    LocalModelRunner (backend.maths.fyjc_local_model_runner)
        ↓
    Qwen2.5-1.5B-Instruct + Platrixa LoRA adapter

This module does NOT:
  - create a second model
  - retrain
  - modify the LoRA adapter
  - touch Phase 6C artifacts
  - create external-API inference paths
  - own accounting truth
  - persist anything

It DOES:
  - expose the ModelProvider contract
  - pin the intended base model + adapter revisions
  - fail closed when the model is unavailable
  - reject forbidden accounting fields
  - never print or store HF_TOKEN
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from backend.model_provider.base import (
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    ForbiddenAccountingFieldError,
    GenerationError,
    InterpretationResult,
    MalformedOutputError,
    ModelProvider,
    ModelProviderError,
    ModelUnavailableError,
    ProviderConfig,
    ProviderStatus,
    contains_forbidden_accounting_fields,
    extract_json_candidate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def resolve_provider_config(
    *,
    model_id: Optional[str] = None,
    base_model_revision: Optional[str] = None,
    adapter_repo_id: Optional[str] = None,
    adapter_revision: Optional[str] = None,
    adapter_path: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    do_sample: Optional[bool] = None,
) -> ProviderConfig:
    """
    Resolve provider configuration from explicit arguments and environment.

    Priority:
      1. explicit arguments
      2. environment variables
      3. pinned defaults

    This preserves the existing env-driven configuration while making the
    production pinned artifacts visible and overridable in a controlled way.
    """

    return ProviderConfig(
        model_id=model_id or _env("PLATRIXA_FYJC_MODEL_ID", BASE_MODEL_ID),
        base_model_revision=(
            base_model_revision
            or _env("PLATRIXA_FYJC_BASE_REVISION", BASE_MODEL_REVISION)
        ),
        adapter_repo_id=(
            adapter_repo_id or _env("PLATRIXA_FYJC_ADAPTER_REPO", ADAPTER_REPO_ID)
        ),
        adapter_revision=(
            adapter_revision
            or _env("PLATRIXA_FYJC_ADAPTER_REVISION", ADAPTER_REVISION)
        ),
        adapter_path=adapter_path or _env("PLATRIXA_FYJC_ADAPTER", ""),
        max_new_tokens=max_new_tokens
        or int(_env("PLATRIXA_FYJC_MAX_TOKENS", "512")),
        temperature=temperature
        if temperature is not None
        else float(_env("PLATRIXA_FYJC_TEMPERATURE", "0.0")),
        top_p=top_p if top_p is not None else float(_env("PLATRIXA_FYJC_TOP_P", "1.0")),
        do_sample=do_sample
        if do_sample is not None
        else bool(_env("PLATRIXA_FYJC_DO_SAMPLE", "0")) is True,
    )


# ---------------------------------------------------------------------------
# LocalHFModelProvider
# ---------------------------------------------------------------------------

class LocalHFModelProvider:
    """
    ModelProvider around the existing local Hugging Face runner.

    This is the production-oriented model boundary for the FYJC Qwen path.

    Important boundaries:
      - It wraps LocalModelRunner; it does not duplicate model-loading logic.
      - It does not load the model at import time.
      - It does not expose HF_TOKEN anywhere.
      - It does not make accounting decisions.
      - It does not silently fall back to the deterministic keyword specialist
        in the production path.
    """

    def __init__(
        self,
        *,
        config: Optional[ProviderConfig] = None,
        model_runner: Optional[Any] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        self._config = config or resolve_provider_config()

        # Allow test injection of a model runner. In production, the provider
        # uses the existing LocalModelRunner singleton.
        self._model_runner = model_runner
        self._system_prompt = system_prompt

    # ------------------------------------------------------------------
    # ModelProvider contract
    # ------------------------------------------------------------------

    def status(self) -> ProviderStatus:
        runner = self._get_runner()
        st = runner.status()
        available = runner.is_available()

        return ProviderStatus(
            available=available,
            model_id=self._config.model_id,
            base_model_revision=self._config.base_model_revision,
            adapter_repo_id=self._config.adapter_repo_id,
            adapter_revision=self._config.adapter_revision,
            reason=st.get("error", "") or ("model loaded" if available else "model not loaded"),
            error=st.get("error", "") or "",
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        if not raw_input or not raw_input.strip():
            raise MalformedOutputError("Empty input")

        runner = self._get_runner()

        if not runner.is_available():
            raise ModelUnavailableError(
                runner.status().get("error") or "model not available"
            )

        generated, error = runner.generate(
            prompt=raw_input,
            system_prompt=self._system_prompt,
            max_new_tokens=self._config.max_new_tokens,
            temperature=self._config.temperature,
            top_p=self._config.top_p,
        )

        if generated is None:
            raise GenerationError(error or "generation failed")

        candidate = extract_json_candidate(generated)
        if candidate is None:
            raise MalformedOutputError("model response contains no valid JSON candidate")

        forbidden = contains_forbidden_accounting_fields(candidate)
        if forbidden:
            raise ForbiddenAccountingFieldError(
                "forbidden accounting fields present: " + ", ".join(forbidden)
            )

        return InterpretationResult(
            raw_input=raw_input,
            candidate=candidate,
            model_id=self._config.model_id,
            provider_revision=self._config.adapter_revision,
            generated_profile={
                "max_new_tokens": self._config.max_new_tokens,
                "temperature": self._config.temperature,
                "top_p": self._config.top_p,
                "do_sample": self._config.do_sample,
                "model_id": self._config.model_id,
                "adapter_repo_id": self._config.adapter_repo_id,
                "adapter_revision": self._config.adapter_revision,
            },
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_runner(self):
        if self._model_runner is not None:
            return self._model_runner
        from backend.maths.fyjc_local_model_runner import LocalModelRunner

        return LocalModelRunner()

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def model_identity(self) -> Dict[str, Any]:
        return {
            "model_id": self._config.model_id,
            "base_model_revision": self._config.base_model_revision,
            "adapter_repo_id": self._config.adapter_repo_id,
            "adapter_revision": self._config.adapter_revision,
        }
