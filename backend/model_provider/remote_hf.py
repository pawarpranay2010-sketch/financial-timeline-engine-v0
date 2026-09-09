"""
Platrixa — Remote HF ModelProvider (Phase 7R)
=============================================

Concrete ModelProvider implementation that calls the Modal inference service
(training/modal_inference.py) over HTTP instead of loading a model in-process.

Architecture position (Kernel processing untouched):

    Kernel
      → model_provider()          (selection: PLATRIXA_MODEL_ENDPOINT_URL set?)
          → RemoteHFModelProvider → Modal /interpret
          → LocalHFModelProvider  → in-process runner (unchanged behavior)

Selection lives ONLY in the factory below (get_model_provider()); the Kernel's
model_provider() method delegates to it when no explicit provider is injected.
This keeps one selection point while leaving Kernel.process() byte-identical.

Contract compliance (backend/model_provider/base.py):
  - status() is non-blocking, makes no network calls, and never triggers
    model loading. available=True is reported only after a proven successful
    interpret() in this process; the remote service keeps its own fail-closed
    /health (503 unless base+adapter are loaded).
  - interpret() maps failures fail-closed onto the existing taxonomy:
      Modal unreachable / timeout / HTTP 5xx → ModelUnavailableError
      invalid JSON / missing required fields → MalformedOutputError
      forbidden accounting fields            → ForbiddenAccountingFieldError
  - NEVER falls back to the local runner, keyword parsing, or a base-only model.
  - Never invents missing interpretation fields — required 18-field presence
    is enforced here (structure only); semantics remain downstream's job.
  - Performs no accounting and persists nothing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

from backend.model_provider.base import (
    ForbiddenAccountingFieldError,
    InterpretationResult,
    MalformedOutputError,
    ModelProvider,
    ModelUnavailableError,
    ProviderConfig,
    ProviderStatus,
    contains_forbidden_accounting_fields,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Endpoint URL of the Modal inference service (no trailing slash).
# Empty/absent → the local provider is selected (unchanged local behavior).
ENDPOINT_URL_ENV = "PLATRIXA_MODEL_ENDPOINT_URL"
# Optional bearer token for Modal proxy-auth-protected endpoints.
TOKEN_ENV = "PLATRIXA_MODEL_ENDPOINT_TOKEN"
# HTTP timeout for interpret calls (seconds).
TIMEOUT_ENV = "PLATRIXA_MODEL_TIMEOUT"
DEFAULT_TIMEOUT = 60.0

# The 18-field interpretation contract (names only — semantics are enforced
# downstream by schema validation, grounding, and the deterministic kernel).
# Asserted against backend/maths/fyjc_contract.ALL_VALID_FIELDS by
# scripts/fte_fyjc_57_remote_provider_test.py.
REQUIRED_FIELDS_18 = (
    "transaction_type",
    "parties",
    "amounts",
    "payment_method",
    "references",
    "ambiguities",
    "grounding",
    "transaction_type_enum",
    "payment_method_enum",
    "ambiguity_flags",
    "referenced_transaction_index",
    "referenced_party",
    "referenced_amount",
    "field_confidences",
    "overall_confidence",
    "suggested_status",
    "safety_flags",
    "scope_flags",
)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def endpoint_url() -> str:
    """Configured Modal endpoint URL ('' when not configured)."""
    return _env(ENDPOINT_URL_ENV)


def endpoint_token() -> str:
    """Optional bearer token string ('' when unset)."""
    return _env(TOKEN_ENV)


def endpoint_timeout() -> float:
    env = _env(TIMEOUT_ENV)
    try:
        return float(env) if env else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# RemoteHFModelProvider
# ---------------------------------------------------------------------------

class RemoteHFModelProvider:
    """
    ModelProvider over the Modal inference endpoint.

    Important boundaries:
      - status() makes no network calls and never triggers model loading.
      - interpret() fails closed on any HTTP/JSON/structure failure.
      - No fallback to LocalHFModelProvider, keyword parsing, or base model.
      - No accounting decisions, no persistence.
    """

    def __init__(
        self,
        *,
        config: Optional[ProviderConfig] = None,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._config = config or ProviderConfig()
        self._url = (url if url is not None else endpoint_url()).rstrip("/")
        self._token = token if token is not None else endpoint_token()
        self._timeout = timeout if timeout is not None else endpoint_timeout()
        self._last_call_ok: bool = False

    # ------------------------------------------------------------------
    # ModelProvider contract
    # ------------------------------------------------------------------

    def status(self) -> ProviderStatus:
        url_configured = bool(self._url)
        return ProviderStatus(
            available=self._last_call_ok,
            model_id=self._config.model_id,
            base_model_revision=self._config.base_model_revision,
            adapter_repo_id=self._config.adapter_repo_id,
            adapter_revision=self._config.adapter_revision,
            reason=(
                "remote endpoint configured"
                if url_configured
                else "no endpoint URL configured"
            ),
            error="" if url_configured else f"{ENDPOINT_URL_ENV} is not set",
            # A configured endpoint is loadable: interpret() may attempt the
            # remote call. Unavailable-but-configured is NOT a hard failure.
            loadable=url_configured,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        self._last_call_ok = False

        if not raw_input or not raw_input.strip():
            raise MalformedOutputError("Empty input")

        if not self._url:
            # Fail closed: without an endpoint there is no model path.
            raise ModelUnavailableError(f"{ENDPOINT_URL_ENV} is not set")

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            resp = requests.post(
                f"{self._url}/interpret",
                json={"text": raw_input},
                headers=headers,
                timeout=self._timeout,
            )
        except requests.Timeout:
            raise ModelUnavailableError("remote model endpoint timeout")
        except requests.RequestException as exc:
            raise ModelUnavailableError(f"remote model endpoint unreachable: {exc}")

        if resp.status_code == 200:
            pass
        elif resp.status_code == 422:
            # Remote service rejected the request shape or its own output;
            # maps to malformed output (VALIDATION_FAILED downstream).
            raise MalformedOutputError(f"remote 422: {_detail_of(resp)}")
        elif resp.status_code == 503:
            # Remote service is up but the model/adapter failed to load
            # (fail-closed /health semantics) — infrastructure failure.
            raise ModelUnavailableError(f"remote 503: {_detail_of(resp)}")
        else:
            raise ModelUnavailableError(
                f"remote model endpoint error {resp.status_code}: {_detail_of(resp)}"
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise MalformedOutputError("remote response is not valid JSON") from exc

        candidate = body.get("interpretation") if isinstance(body, dict) else None
        model_info = body.get("model", {}) if isinstance(body, dict) else {}
        if not isinstance(candidate, dict):
            raise MalformedOutputError(
                "remote response missing interpretation object"
            )
        if not isinstance(model_info, dict):
            model_info = {}

        _require_18_fields(candidate)

        forbidden = contains_forbidden_accounting_fields(candidate)
        if forbidden:
            raise ForbiddenAccountingFieldError(
                "forbidden accounting fields present: " + ", ".join(forbidden)
            )

        self._last_call_ok = True

        return InterpretationResult(
            raw_input=raw_input,
            candidate=candidate,
            model_id=self._config.model_id,
            provider_revision=self._config.adapter_revision,
            generated_profile={
                "endpoint_url": self._url,
                "remote_base_revision": model_info.get("base_revision", ""),
                "remote_adapter_revision": model_info.get("adapter_revision", ""),
                "remote_adapter_loaded": model_info.get("adapter_loaded", None),
                "timeout_s": self._timeout,
            },
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _require_18_fields(candidate: Dict[str, Any]) -> None:
        _require_18_fields(candidate)

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def model_identity(self) -> Dict[str, Any]:
        return {
            "endpoint_url": self._url,
            "model_id": self._config.model_id,
            "base_model_revision": self._config.base_model_revision,
            "adapter_repo_id": self._config.adapter_repo_id,
            "adapter_revision": self._config.adapter_revision,
        }


def _require_18_fields(candidate: Dict[str, Any]) -> None:
    """Fail closed when any of the 18 contract fields is absent."""
    missing = [f for f in REQUIRED_FIELDS_18 if f not in (candidate or {})]
    if missing:
        raise MalformedOutputError(
            "remote interpretation missing required fields: " + ", ".join(missing)
        )


def _detail_of(resp: requests.Response) -> str:
    """Best-effort error detail extraction; never raises."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:200]
    if isinstance(body, dict):
        for key in ("detail", "error"):
            if key in body:
                return str(body[key])[:200]
    return str(body)[:200]


# ---------------------------------------------------------------------------
# Single selection point for the ModelProvider implementation
# ---------------------------------------------------------------------------

def get_model_provider(
    *,
    provider: Optional[ModelProvider] = None,
    config: Optional[ProviderConfig] = None,
) -> ModelProvider:
    """
    Single selection point for the ModelProvider implementation.

    Selection (Phase 7R Part 6):
        PLATRIXA_MODEL_ENDPOINT_URL set → RemoteHFModelProvider
        otherwise                       → LocalHFModelProvider (unchanged)

    Explicit `provider` argument wins (tests / advanced wiring).
    """
    if provider is not None:
        return provider

    if endpoint_url():
        return RemoteHFModelProvider(config=config)

    from backend.model_provider.local_hf import LocalHFModelProvider

    return LocalHFModelProvider(config=config)


__all__ = [
    "RemoteHFModelProvider",
    "get_model_provider",
    "REQUIRED_FIELDS_18",
    "ENDPOINT_URL_ENV",
]
