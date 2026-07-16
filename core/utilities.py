# core/utilities.py
"""
Small, dependency-free helper utilities shared across the engine:
stable hashing (for cache keys), a generic cache-or-compute wrapper, and a
retry-with-backoff helper for flaky I/O (AI provider calls).

`CacheManager` is deliberately store-agnostic (Dependency Injection): it
takes anything that behaves like a `dict` in its constructor. Today the
Streamlit frontend injects `st.session_state`-backed dict views (matching
the original session-scoped cache exactly), but the same class works
against a plain `dict` in tests or, later, against a Redis/DB-backed
mapping in the backend package -- without any caller code changing,
directly serving the "Reduce duplicate AI calls / Improve caching"
performance goals called out in the architecture.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, MutableMapping

from core.logging import get_logger

_logger = get_logger("core.utilities")


def hash_text(text: str | None) -> str:
    """Stable SHA-256 hash of arbitrary text, used as a cache key so that
    re-uploading identical document content (or re-requesting an identical
    prompt) doesn't trigger a duplicate AI call."""
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


class CacheManager:
    """Generic get-or-compute cache wrapper.

    Wraps a single `MutableMapping` (e.g. one `st.session_state[cache_name]`
    dict). If `cache_key` is already present, the cached value is returned
    without invoking `compute_fn`; otherwise `compute_fn` is called, its
    result stored, and returned.

    This is a *cache-scoped-to-whatever-mapping-you-hand-it* wrapper -- for
    the current Streamlit frontend that means "scoped to this browser
    session", exactly matching the original app's behavior. A future
    backend can hand it a persistent mapping (e.g. a dict-like Redis
    client) to get cross-session/cross-user caching for free.
    """

    def __init__(self, store: MutableMapping[str, Any]) -> None:
        self._store = store

    def get_or_compute(self, cache_key: str, compute_fn: Callable[[], Any]) -> tuple[Any, bool]:
        """Returns (value, was_cache_hit)."""
        if cache_key in self._store:
            return self._store[cache_key], True
        result = compute_fn()
        self._store[cache_key] = result
        return result, False


def retry(
    fn: Callable[[], Any],
    attempts: int = 2,
    delay: float = 1.5,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Any:
    """Retry-with-backoff wrapper for a zero-arg callable.

    Retries `fn` up to `attempts` times, sleeping `delay` seconds between
    attempts, before re-raising the last exception. `on_retry`, if given,
    is called with (attempt_index, exception) before each sleep -- used by
    the AI Gateway to log each failed attempt without this generic helper
    needing to know about provider-specific logging.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - intentionally broad; re-raised below
            last_exc = exc
            if attempt < attempts - 1:
                if on_retry is not None:
                    on_retry(attempt, exc)
                else:
                    _logger.warning("Retry %d/%d after error: %s", attempt + 1, attempts, exc)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc
