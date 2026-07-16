# core/logging.py
"""
Structured logging for the Financial Timeline Engine.

Two things live here:

1. Standard Python `logging` setup (`configure_logging`), replacing the
   old ad hoc `print()` calls with real, leveled, server-side logging that
   works the same whether the process is Streamlit, a future REST API, or
   a background worker.

2. `ProviderEventLogger` -- a small, dependency-injected structured event
   log for AI provider activity (used today by the Provider Health
   Dashboard's "Activity Log" expander). The *sink* the log entries are
   written to is injected rather than hard-coded to `st.session_state`, so
   the same logger works in a Streamlit session, a unit test, or (later) a
   backend process that persists provider logs to a database.

NOTE on provider health: `get_provider_health` currently just checks which
provider API keys are configured. It lives here (rather than in a not-yet-
built `gateway/provider_manager.py`) because it's still purely a "does a
secret exist" health check consumed by the dashboard's logging/health UI.
When the `gateway/` package is built, provider health will be re-exported
from there and this function will simply delegate to it -- no call sites
need to change today.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Protocol

from core.config import get_secret

_LOGGER_NAME = "fte"
_configured = False


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Idempotently configures the engine's root logger. Safe to call from
    multiple modules/entry points -- only the first call attaches a
    handler, subsequent calls just return the existing logger."""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
        _configured = True
    return logger


def get_logger(component: str) -> logging.Logger:
    """Returns a child logger, e.g. `get_logger('gateway.router')` ->
    logger named 'fte.gateway.router'. Ensures the root 'fte' logger has
    been configured first."""
    configure_logging()
    return logging.getLogger(f"{_LOGGER_NAME}.{component}")


_module_logger = get_logger("core.logging")


# =============================================================================
# Provider event log (structured, injectable sink)
# =============================================================================
class LogSink(Protocol):
    """Anything capable of storing/retrieving a list of log entry dicts."""

    def append(self, entry: dict) -> None: ...
    def recent(self, limit: int) -> list: ...


class InMemoryLogSink:
    """Simple list-backed sink. Used for tests, scripts, and as the
    fallback when no Streamlit session is available."""

    def __init__(self) -> None:
        self._entries: list = []

    def append(self, entry: dict) -> None:
        self._entries.append(entry)

    def recent(self, limit: int) -> list:
        return self._entries[-limit:]


class StreamlitSessionLogSink:
    """Stores entries in `st.session_state['provider_log']`, matching the
    original app's behavior exactly so the existing Provider Health
    Dashboard expander keeps working unmodified."""

    def __init__(self, state_key: str = "provider_log") -> None:
        self._state_key = state_key

    def _list(self) -> list:
        import streamlit as st
        return st.session_state.setdefault(self._state_key, [])

    def append(self, entry: dict) -> None:
        self._list().append(entry)

    def recent(self, limit: int) -> list:
        return self._list()[-limit:]


class ProviderEventLogger:
    """Structured logger for AI provider call events (attempts, successes,
    failures). Each event is both:
      - appended to the injected `LogSink` (for UI display), and
      - emitted through standard Python logging (for server-side logs /
        future log aggregation), replacing the old bare `print()`.
    """

    def __init__(self, sink: LogSink | None = None) -> None:
        self._sink = sink if sink is not None else StreamlitSessionLogSink()
        self._logger = get_logger("provider")

    def log(self, stage: str, provider: str, status: str, detail: str = "") -> dict:
        """Record one provider event. Returns the recorded entry."""
        entry = {
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "stage": stage,
            "provider": provider,
            "status": status,
            "detail": detail,
        }
        self._sink.append(entry)
        log_fn = self._logger.info if status == "success" else self._logger.warning
        log_fn("stage=%s provider=%s status=%s detail=%s", stage, provider, status, detail)
        return entry

    def recent(self, limit: int = 20) -> list:
        return self._sink.recent(limit)


# Default, module-level instance -- existing call sites can do
# `from core.logging import provider_logger` and call
# `provider_logger.log(...)` as a drop-in replacement for the old
# `_log_provider_event(...)` free function.
provider_logger = ProviderEventLogger()


# =============================================================================
# Provider health
# =============================================================================
def get_provider_health() -> dict:
    """Which AI providers currently have a configured API key.

    Uses `core.config.get_secret`, so this works whether the key is set in
    Streamlit secrets (today) or an environment variable (future
    backend/tests), without this function needing to change.
    """
    return {
        "Google AI Studio": bool(get_secret("GOOGLE_API_KEY", "")),
        "Groq": bool(get_secret("GROQ_API_KEY", "")),
        "OpenRouter": bool(get_secret("OPENROUTER_API_KEY", "")),
    }
