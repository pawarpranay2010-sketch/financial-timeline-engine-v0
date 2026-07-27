"""
Retry Policy

Classifies exceptions as retryable (transient) or non-retryable (permanent)
and wraps callables with exponential backoff + jitter.

Retryable  (transient — safe to retry):
    Timeout, connection reset, network errors
    HTTP 500, 502, 503, 504

Non-retryable  (permanent — re-raise immediately):
    HTTP 400 Bad Request
    HTTP 401 Unauthorized / invalid API key
    HTTP 403 Forbidden
    HTTP 404 Not Found
    HTTP 422 Unprocessable Entity

Backoff formula:
    delay = min(base_delay × 2^attempt + jitter, max_delay)
    jitter = uniform(0.0, 1.0)   →   avoids thundering herd
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable

from backend.module4.logger import logger


# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------

# Substrings that identify a permanent (non-retryable) error.
# Checked first — these take precedence over retryable patterns.
_NON_RETRYABLE_FRAGMENTS = [
    "invalid api key",
    "unauthorized",
    "authentication failed",
    "authentication",
    "forbidden",
    "bad request",
    "unprocessable",
    "not found",
    "http 400",
    "http error 400",
    " 400 ",
    "http 401",
    "http error 401",
    " 401 ",
    "http 403",
    "http error 403",
    " 403 ",
    "http 404",
    "http error 404",
    " 404 ",
    "http 422",
    "http error 422",
    " 422 ",
]

# Substrings that identify a transient (retryable) error.
_RETRYABLE_FRAGMENTS = [
    "timeout",
    "timed out",
    "connection",
    "network",
    "reset by peer",
    "broken pipe",
    "http 500",
    "http error 500",
    " 500 ",
    "http 502",
    "http error 502",
    " 502 ",
    "http 503",
    "http error 503",
    " 503 ",
    "http 504",
    "http error 504",
    " 504 ",
]


# ---------------------------------------------------------------------------
# Classification function
# ---------------------------------------------------------------------------

def is_retryable(error: Exception) -> bool:
    """
    Returns True if the error is transient and safe to retry.

    Non-retryable patterns are evaluated first. If neither set matches,
    the error is treated as retryable (conservative default — better to
    retry an unknown error once than to silently drop it).
    """
    msg = str(error).lower()

    if any(fragment in msg for fragment in _NON_RETRYABLE_FRAGMENTS):
        return False

    return True  # includes explicit retryable matches and unknown errors


# ---------------------------------------------------------------------------
# Execution with retry
# ---------------------------------------------------------------------------

def execute_with_retry(
    fn: Callable,
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> Any:
    """
    Call ``fn(*args, **kwargs)`` with exponential backoff.

    Behaviour:
        - Non-retryable error on any attempt  →  re-raised immediately.
        - Retryable error on attempt < max_attempts  →  wait and retry.
        - Retryable error on the final attempt  →  re-raised.

    Parameters
    ----------
    fn          : Callable to invoke.
    *args       : Positional arguments forwarded to fn.
    max_attempts: Maximum total call attempts (default 3).
    base_delay  : Initial backoff in seconds (default 1.0).
    max_delay   : Ceiling for the computed delay (default 30.0 s).
    **kwargs    : Keyword arguments forwarded to fn.

    Returns
    -------
    The return value of fn on success.

    Raises
    ------
    The last exception if all attempts are exhausted, or any
    non-retryable exception immediately.
    """
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)

        except Exception as exc:
            last_error = exc

            if not is_retryable(exc):
                logger.warning(
                    f"[Retry] Non-retryable error on attempt {attempt + 1} "
                    f"— re-raising immediately: {exc}"
                )
                raise  # No backoff for permanent failures

            if attempt < max_attempts - 1:
                delay = min(
                    base_delay * (2 ** attempt) + random.uniform(0.0, 1.0),
                    max_delay,
                )
                logger.warning(
                    f"[Retry] Attempt {attempt + 1}/{max_attempts} failed "
                    f"({exc}). Retrying in {delay:.1f}s ..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"[Retry] All {max_attempts} attempts exhausted: {exc}"
                )

    raise last_error
