# core/config.py
"""
Central configuration for the Financial Timeline Engine.

Two concerns live here, kept deliberately separate (Single Responsibility):

1. `SecretsProvider` -- an abstraction over "where do API keys come from".
   Today that's `st.secrets` (Streamlit). The backend/ package described in
   the target architecture will eventually run outside Streamlit (a REST
   API, a worker process), where secrets come from environment variables
   or a secrets manager instead. Every other module asks *this* module for
   secrets rather than importing `streamlit` directly, so that day the only
   file that changes is this one.

2. `EngineSettings` -- a single dataclass holding every tunable constant
   that used to be a bare module-level variable in app.py (model IDs,
   timeouts, chunking sizes, retry policy, feature flags). Grouping them in
   one typed object makes it possible to inject a different configuration
   in tests or in a future multi-tenant backend, instead of monkeypatching
   module globals.

Module-level singletons (`SETTINGS`, `get_secret`) are provided so existing
call sites can migrate with a one-line import change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


# =============================================================================
# Secrets abstraction
# =============================================================================
class SecretsProvider(Protocol):
    """Anything that can answer "give me the value for this secret key"."""

    def get(self, key: str, default: Any = "") -> Any:
        ...


class StreamlitSecretsProvider:
    """Reads secrets from `st.secrets`. This is the provider used by the
    current Streamlit frontend, and remains the default so existing
    behavior is unchanged."""

    def get(self, key: str, default: Any = "") -> Any:
        import streamlit as st  # local import: keeps `core` importable
        # in non-Streamlit contexts (tests, future backend) as long as
        # this specific provider isn't the one instantiated.
        return st.secrets.get(key, default)


class EnvSecretsProvider:
    """Reads secrets from process environment variables. Intended for the
    future REST backend, background workers, and unit tests, none of which
    run inside a Streamlit session."""

    def get(self, key: str, default: Any = "") -> Any:
        return os.environ.get(key, default)


class CompositeSecretsProvider:
    """Tries each provider in order and returns the first truthy value.
    Lets a deployment define secrets in `st.secrets` OR the environment
    without the rest of the app caring which one was used."""

    def __init__(self, providers: list[SecretsProvider]) -> None:
        self._providers = providers

    def get(self, key: str, default: Any = "") -> Any:
        for provider in self._providers:
            try:
                value = provider.get(key, "")
            except Exception:
                continue
            if value:
                return value
        return default


def get_default_secrets_provider() -> SecretsProvider:
    """Builds the default provider chain: Streamlit secrets first (current
    production path), then environment variables (future backend / local
    dev / tests). Streamlit failures (e.g. no script run context, as
    happens in a plain `python` or pytest process) are swallowed by
    CompositeSecretsProvider, so this is safe to call from anywhere."""
    return CompositeSecretsProvider([StreamlitSecretsProvider(), EnvSecretsProvider()])


# =============================================================================
# Engine settings
# =============================================================================
@dataclass(frozen=True)
class EngineSettings:
    """Typed, immutable configuration for the whole engine.

    Defaults reproduce the exact values previously hard-coded as module
    globals in app.py, so constructing `EngineSettings()` changes no
    behavior. A future backend can construct a different instance (e.g.
    per-tenant model choices) without touching business logic.
    """

    # --- AI Gateway: model selection -----------------------------------
    primary_model: str = "google/gemini-2.0-flash-exp:free"
    fallback_model: str = "meta-llama/llama-3.1-8b-instruct:free"
    google_model: str = "gemini-2.5-flash"
    openrouter_models: tuple = field(default=None)  # set in __post_init__
    groq_models: tuple = (
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "llama-3.1-8b-instant",
    )

    # --- AI Gateway: timeouts & retry -----------------------------------
    groq_timeout_seconds: int = 30
    openrouter_timeout_seconds: int = 45
    provider_retry_attempts: int = 2
    provider_retry_delay_seconds: float = 1.5

    # --- AI Gateway: circuit breaker -------------------------------------
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 60.0

    # --- Document processing: chunking & hierarchical merge -------------
    chunk_size: int = 10000
    chunk_overlap: int = 500
    merge_batch_size: int = 8  # max summaries merged per AI call; recurses above this

    # --- Live market intelligence (Phase 6 scaffold) --------------------
    live_intelligence_api_key_name: str = "MARKET_INTEL_API_KEY"

    # --- Auth (temporary hard-coded credentials; see backend/auth later) -
    default_admin_username: str = "admin"
    default_admin_password: str = "financial_terminal_2026"

    def __post_init__(self) -> None:
        if self.openrouter_models is None:
            object.__setattr__(self, "openrouter_models", (self.primary_model, self.fallback_model))


# Roadmap of planned-but-not-yet-implemented modules (Phase 11 scaffold).
# Kept as a plain module-level constant (rather than on EngineSettings)
# since it's static product roadmap data, not a runtime tunable.
FUTURE_MODULES: dict = {
    "valuation": "Valuation",
    "dcf": "Discounted Cash Flow (DCF) Model",
    "comparable_analysis": "Comparable Company Analysis",
    "forecasting": "Financial Forecasting",
    "earnings_model": "Earnings Model",
    "financial_modeling": "Financial Modeling",
    "portfolio_analysis": "Portfolio Analysis",
    "watchlist": "Watchlist",
    "company_comparison": "Company Comparison",
    "stock_scoring": "Stock Scoring",
    "screening_engine": "Screening Engine",
}


# --------------------------------------------------------------------------
# Module-level singletons -- import these from the rest of the codebase.
# --------------------------------------------------------------------------
SETTINGS = EngineSettings()
_secrets_provider: SecretsProvider = get_default_secrets_provider()


def get_secret(key: str, default: Any = "") -> Any:
    """Fetch a secret/API key using the engine's configured secrets
    provider chain (Streamlit secrets, then environment variables)."""
    return _secrets_provider.get(key, default)


def set_secrets_provider(provider: SecretsProvider) -> None:
    """Override the secrets provider (used by tests and by the future
    backend to inject a vault/secrets-manager-backed provider)."""
    global _secrets_provider
    _secrets_provider = provider
