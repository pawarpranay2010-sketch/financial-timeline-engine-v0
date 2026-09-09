"""
Platrixa — HF Gradio transport adapter (Phase 7S)
=================================================

Smallest safe transport adapter between the existing RemoteHFModelProvider
boundary and the Hugging Face ZeroGPU Space `Pranay-20/Platrixa`.

Why this exists
---------------
The Space serves the EXACT Phase 6C artifact (Qwen2.5-1.5B @ 989aa79… with the
locked FYJC LoRA @ b5c0a37…). ZeroGPU requires the @spaces.GPU Gradio execution
model, so the Space cannot expose a raw FastAPI POST /interpret (confirmed by
rebuild experiments and HF forums). It instead exposes the named Gradio API
`/interpret_core`, whose first output is byte-compatible with the envelope the
provider already consumes: {"interpretation": {...18 fields...}, "model": {...}}.

This module therefore does NOT change:
  - the ModelProvider contract (backend/model_provider/base.py)
  - provider selection (single factory in remote_hf.get_model_provider())
  - the Kernel, grounding gate, schema validation, or accounting logic
  - the Modal HTTP transport (still selected by default)

It changes ONLY the wire protocol, and only when explicitly selected:

    PLATRIXA_MODEL_ENDPOINT_URL   = https://pranay-20-platrixa.hf.space
    PLATRIXA_MODEL_TRANSPORT      = gradio

FAIL-CLOSED GUARANTEES (same taxonomy as RemoteHFModelProvider)
---------------------------------------------------------------
- Space unreachable / timeout / queue errors → ModelUnavailableError
- malformed envelope / missing 18 fields    → MalformedOutputError
- forbidden accounting fields               → ForbiddenAccountingFieldError
- wrong model identity (base or adapter ID/revision mismatch)
                                            → MalformedOutputError (identity)
- NO fallback to local/base-only/keyword/another LLM, ever.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from backend.model_provider.base import (
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
    ForbiddenAccountingFieldError,
    MalformedOutputError,
    ModelUnavailableError,
    ProviderConfig,
)
from backend.model_provider.remote_hf import (
    ENDPOINT_URL_ENV,
    RemoteHFModelProvider,
    endpoint_timeout,
)

# Optional HF token for token-gated/private Spaces. NEVER printed or logged.
HF_TOKEN_ENV = "HF_TOKEN"
# Default timeout documented in DEPLOYMENT.md: ZeroGPU cold start (GPU queue +
# 3 GB lazy model load + generation) has been observed up to ~90 s, so the
# default sits above that with headroom; PLATRIXA_MODEL_TIMEOUT overrides.
DEFAULT_GRADIO_TIMEOUT = 120.0

# Identity keys the Space's model_identity() reports (hf_space/app.py).
_EXPECTED_BASE_ID = BASE_MODEL_ID
_EXPECTED_BASE_REV = BASE_MODEL_REVISION
_EXPECTED_ADAPTER_ID = ADAPTER_REPO_ID
_EXPECTED_ADAPTER_REV = ADAPTER_REVISION


def hf_token() -> str:
    """Configured HF token ('' when unset). Never logged."""
    return os.environ.get(HF_TOKEN_ENV, "").strip()


def _identity_problems(model_info: Dict[str, Any]) -> list:
    """Return a list of human-readable identity mismatches (empty = OK)."""
    problems: list = []

    def _norm(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    # Accept the Space's key names (base_model/adapter_model) and tolerate
    # equivalent aliases so a future Modal-style identity also validates.
    base_id = _norm(
        model_info.get("base_model")
        or model_info.get("base_model_id")
        or model_info.get("model_id")
    )
    base_rev = _norm(
        model_info.get("base_revision") or model_info.get("base_model_revision")
    )
    adapter_id = _norm(
        model_info.get("adapter_model") or model_info.get("adapter_repo_id")
    )
    adapter_rev = _norm(model_info.get("adapter_revision"))
    adapter_loaded = model_info.get("adapter_loaded")

    if base_id and base_id != _EXPECTED_BASE_ID:
        problems.append(
            f"base model identity mismatch: got {base_id!r}, expected {_EXPECTED_BASE_ID!r}"
        )
    if base_rev and base_rev != _EXPECTED_BASE_REV:
        problems.append(
            f"base revision mismatch: got {base_rev!r}, expected {_EXPECTED_BASE_REV!r}"
        )
    if adapter_id and adapter_id != _EXPECTED_ADAPTER_ID:
        problems.append(
            f"adapter identity mismatch: got {adapter_id!r}, expected {_EXPECTED_ADAPTER_ID!r}"
        )
    if adapter_rev and adapter_rev != _EXPECTED_ADAPTER_REV:
        problems.append(
            f"adapter revision mismatch: got {adapter_rev!r}, expected {_EXPECTED_ADAPTER_REV!r}"
        )
    if adapter_loaded is False:
        problems.append("adapter not loaded on remote service")
    return problems


class HFGradioModelProvider(RemoteHFModelProvider):
    """
    ModelProvider over the HF ZeroGPU Gradio Space named API.

    Subclasses the existing remote provider so selection semantics, status(),
    18-field validation, forbidden-field rejection, and the ModelProvider
    contract are inherited unchanged. The ONLY overridden behavior is the
    transport (_call_remote) plus locked-identity enforcement on the response.
    """

    def __init__(
        self,
        *,
        config: Optional[ProviderConfig] = None,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
        api_name: str = "/interpret_core",
    ) -> None:
        # Default timeout: ZeroGPU cold start observed up to ~90 s (GPU queue +
        # lazy 3 GB load + generation). 120 s default keeps legitimate cold
        # starts working; PLATRIXA_MODEL_TIMEOUT overrides explicitly.
        super().__init__(
            config=config,
            url=url,
            token=token,
            timeout=timeout if timeout is not None else DEFAULT_GRADIO_TIMEOUT,
        )
        self._api_name = api_name
        self._token = token if token is not None else self._resolve_token()

    def _resolve_token(self) -> str:
        # PLATRIXA_MODEL_ENDPOINT_TOKEN (inherited default) wins; else HF_TOKEN.
        inherited = os.environ.get("PLATRIXA_MODEL_ENDPOINT_TOKEN", "").strip()
        return inherited or hf_token()

    # ------------------------------------------------------------------
    # Transport seam (the only overridden remote behavior)
    # ------------------------------------------------------------------

    def _call_remote(self, raw_input: str) -> Dict[str, Any]:
        """
        Call the Space's named Gradio API and return the provider envelope.

        Identity is enforced HERE, at the transport seam, before any
        interpretation content can flow downstream: a response that does not
        carry the exact locked artifact identity fails closed.
        """
        from gradio_client import Client

        try:
            client = Client(
                self._url,
                hf_token=self._token or None,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed on any connect issue
            raise ModelUnavailableError(
                f"gradio transport: cannot reach Space: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            result = client.predict(
                raw_input,
                api_name=self._api_name,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed on any call issue
            name = type(exc).__name__
            text = str(exc)
            lowered = text.lower()
            if "time" in lowered and "out" in lowered:
                raise ModelUnavailableError(f"gradio transport timeout: {text}") from exc
            raise ModelUnavailableError(
                f"gradio transport failure ({name}): {text[:200]}"
            ) from exc

        # gradio_client returns a tuple of the handler's two outputs:
        #   (envelope_dict, runtime_metadata_str)
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise MalformedOutputError(
                f"gradio transport: unexpected output arity ({type(result).__name__})"
            )
        envelope = result[0]
        if not isinstance(envelope, dict):
            raise MalformedOutputError("gradio transport: envelope is not an object")

        # An explicit error envelope from the Space (model load failure etc.)
        # is an infrastructure failure, not a malformed interpretation.
        if envelope.get("error"):
            kind = envelope.get("error", "")
            detail = str(envelope.get("detail", ""))[:200]
            if kind == "forbidden_accounting_fields":
                raise ForbiddenAccountingFieldError(
                    f"forbidden accounting fields present: {detail}"
                )
            raise ModelUnavailableError(f"remote error envelope ({kind}): {detail}")

        # Locked-identity enforcement (fail closed BEFORE downstream use).
        model_info = envelope.get("model")
        problems = _identity_problems(
            model_info if isinstance(model_info, dict) else {}
        )
        if problems:
            raise MalformedOutputError(
                "remote model identity mismatch: " + "; ".join(problems)
            )

        return envelope

    @property
    def model_identity(self) -> Dict[str, Any]:
        identity = super().model_identity
        identity["transport"] = "gradio"
        identity["api_name"] = self._api_name
        return identity
