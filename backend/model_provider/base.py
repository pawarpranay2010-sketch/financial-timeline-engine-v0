"""
Platrixa — ModelProvider contract (Phase 7B)

Defines the minimal application-facing contract for AI/model inference in the
FYJC specialist path.

Design notes
------------
- The interface is deliberately small. It exposes only what downstream code
  (Kernel / verification / grounding / API) genuinely needs from the model path.
- The contract is model-agnostic on purpose, so the implementation can later be
  swapped (another local HF model, another inference backend) without changing
  Kernel/API/UI code.
- The provider is NOT the accounting authority. It does not own journal entries,
  debit/credit decisions, ledger balances, or any deterministic accounting truth.
- The provider does NOT persist anything. Persistence is reachable only after
  verification/grounding, outside this package.
- Importing this module does not load any model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

# ---------------------------------------------------------------------------
# Pinned production model artifacts
# ---------------------------------------------------------------------------
# These are the exact artifacts selected by Phase 6C evidence.
# They MUST NOT silently drift to "latest" in production code.
#
# Base model:
#   Qwen/Qwen2.5-1.5B-Instruct
#   revision: 989aa7980e4cf806f80c7fef2b1adb7bc71aa306
#
# LoRA adapter:
#   Pranay-20/platrixa-fyjc-specialist-v0.1
#   revision: b5c0a37cebc00e93144150dbbcaa7b28cadb259e
#
# The values below are the *intended* production artifacts. They are stored
# here so the boundary can assert them at runtime without reaching out to
# Hugging Face to ask "what is latest?".
#
# IMPORTANT:
#   - We do not call the Hugging Face API to verify the revision at import
#     time. That would require network + auth and would violate "fail closed,
#     no surprises".
#   - The LocalHF provider accepts these as configuration and includes them in
#     its status/identity information. The actual revision pinning is enforced
#     by configuration discipline + Phase 6C evidence, not by a live API call.
#   - HF_TOKEN is NEVER stored here. Authentication/access is handled at the
#     point of model loading through env/secrets, and is never printed.
#

BASE_MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"

ADAPTER_REPO_ID = "Pranay-20/platrixa-fyjc-specialist-v0.1"
ADAPTER_REVISION = "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderConfig:
    """
    Configuration for a model provider.

    This is intentionally separate from EngineSettings so the model boundary
    can be reasoned about independently from the existing gateway/executive
    configuration. In future phases, EngineSettings (or a dedicated model
    config) may host these values, but the boundary itself owns the shape.
    """

    # Model identity
    model_id: str = BASE_MODEL_ID
    base_model_revision: str = BASE_MODEL_REVISION

    # LoRA adapter identity
    adapter_repo_id: str = ADAPTER_REPO_ID
    adapter_revision: str = ADAPTER_REVISION

    # Optional adapter artifact path used by the local HF provider.
    # If set, it must point at a local PEFT adapter directory that matches the
    # pinned adapter revision. If empty, the provider may attempt HF-based
    # adapter resolution (subject to auth + the pinned revision).
    adapter_path: str = ""

    # Generation parameters used by the local HF provider.
    max_new_tokens: int = 512
    temperature: float = 0.0
    top_p: float = 1.0
    do_sample: bool = False

    # Runtime environment hints (device/dtype are read from env by the
    # LocalModelRunner; this config only records intent for documentation).
    device: str = "auto"
    dtype: str = "auto"

    def expected_model_identity(self) -> Dict[str, Any]:
        """Return the exact identity the provider is configured to use."""
        return {
            "model_id": self.model_id,
            "base_model_revision": self.base_model_revision,
            "adapter_repo_id": self.adapter_repo_id,
            "adapter_revision": self.adapter_revision,
            "adapter_path": self.adapter_path,
        }


# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderStatus:
    """
    Lightweight status returned by a provider without forcing model loading.

    This lets downstream code (Kernel / API) distinguish:
      - model available
      - model not available (MODEL_NOT_AVAILABLE)
      - why it is not available (without leaking secrets)
    """

    available: bool
    model_id: str
    base_model_revision: str
    adapter_repo_id: str
    adapter_revision: str
    reason: str = ""
    error: str = ""

    @property
    def model_unavailable(self) -> bool:
        return not self.available


# ---------------------------------------------------------------------------
# Structured candidate output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterpretationResult:
    """
    The model candidate output produced by the provider.

    This is a *semantic interpretation candidate*, not accounting truth.

    The downstream kernel/verification/grounding boundary owns:
      - schema validation
      - grounding compatibility
      - whether this becomes trusted data
      - what status is ultimately returned to the API/UI
    """

    raw_input: str
    candidate: Dict[str, Any]
    model_id: str
    provider_revision: str
    generated_profile: Dict[str, Any] = field(default_factory=dict)

    # Convenience accessors for the 18-field contract
    @property
    def transaction_type(self) -> Any:
        return self.candidate.get("transaction_type")

    @property
    def parties(self) -> Any:
        return self.candidate.get("parties")

    @property
    def amounts(self) -> Any:
        return self.candidate.get("amounts")

    @property
    def payment_method(self) -> Any:
        return self.candidate.get("payment_method")

    @property
    def references(self) -> Any:
        return self.candidate.get("references")

    @property
    def ambiguities(self) -> Any:
        return self.candidate.get("ambiguities")

    @property
    def grounding(self) -> Any:
        return self.candidate.get("grounding")

    @property
    def suggested_status(self) -> Any:
        return self.candidate.get("suggested_status")

    def snapshot(self) -> Dict[str, Any]:
        """Safe serialization for logging/metrics. Does not include secrets."""
        return {
            "raw_input": self.raw_input,
            "candidate": self.candidate,
            "model_id": self.model_id,
            "provider_revision": self.provider_revision,
            "generated_profile": self.generated_profile,
        }


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------

class ModelProvider(Protocol):
    """
    Application-facing contract for FYJC model inference.

    Implementations:
      - LocalHFModelProvider (current local Hugging Face + LoRA implementation)

    Future implementations may add other local HF backends, but this boundary
    must remain candidate-output-only and must not leak into accounting truth.
    """

    def status(self) -> ProviderStatus:
        """
        Return provider status WITHOUT triggering model loading.

        Used by downstream code to decide whether to attempt inference or
        to return MODEL_NOT_AVAILABLE.
        """

    def interpret(self, raw_input: str) -> InterpretationResult:
        """
        Produce a structured semantic interpretation candidate for the given
        student input text.

        Returns:
          - InterpretationResult on success (including cases where the model
            produces a structurally valid but semantically incomplete candidate)
          - Raises ModelProviderError subclasses on provider/infrastructure
            failures or on outputs that the provider must reject

        This method does NOT:
          - decide accounting truth
          - persist anything
          - claim VERIFIED
          - fall back silently to a non-model path in the production provider
        """


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class ModelProviderError(Exception):
    """
    Base exception for ModelProvider failures.

    Downstream code should catch this and map it to the appropriate terminal
    state (MODEL_NOT_AVAILABLE or MALFORMED), rather than treating all
    provider failures identically.
    """


class ModelUnavailableError(ModelProviderError):
    """
    The provider/model is unavailable.

    This is an infrastructure/availability failure. It is NOT the same as
    REVIEW_REQUIRED (ambiguous-but-interpretable output) and NOT the same as
    MALFORMED (output that cannot satisfy the required structure).
    """


class MalformedOutputError(ModelProviderError):
    """
    The model output cannot satisfy the required structure/format.

    Examples:
      - not valid JSON
      - missing the 18-field structure
      - invalid enum values that the provider must reject

    This is NOT the same as model unavailability and NOT the same as intentional
    ambiguity (REVIEW_REQUIRED).
    """


class ForbiddenAccountingFieldError(ModelProviderError):
    """
    The model output includes forbidden accounting-truth fields.

    The provider should reject these and never forward them to accounting logic.
    """


class GenerationError(ModelProviderError):
    """
    The model failed to generate output (tokenization, generation, decoding,
    device, adapter loading, etc.).
    """


class ValidationError(ModelProviderError):
    """
    The provider performed internal validation and rejected the output.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_ACCOUNTING_FIELDS = frozenset({
    "journal",
    "journal_entry",
    "debit_lines",
    "credit_lines",
    "ledger",
    "balances",
    "debit_account",
    "credit_account",
})


def contains_forbidden_accounting_fields(candidate: Dict[str, Any]) -> List[str]:
    """Return any forbidden accounting-truth keys present in the candidate."""
    keys = set(candidate.keys())
    hits = sorted(_FORBIDDEN_ACCOUNTING_FIELDS & keys)
    return hits


def extract_json_candidate(raw_response: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to extract a JSON object from a raw model response.

    Returns None when no candidate JSON object can be extracted.
    This helper is intentionally small and does not perform accounting-style
    inference or repair.
    """
    if not raw_response or not raw_response.strip():
        return None

    text = raw_response.strip()

    # Strip markdown fences
    import re

    fence = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    match = fence.search(text)
    if match:
        text = match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            parsed = json.loads(text[first:last + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None
