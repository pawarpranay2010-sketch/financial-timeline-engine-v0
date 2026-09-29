"""
Platrixa — request-rate limiting for the anonymous API surface (audit H-02)
===========================================================================

WHY THIS EXISTS
---------------
The 2026-09-29 security audit found NO request-rate limiter anywhere in the
application. The only admission control was the per-tenant monthly quota in
``backend/auth/gate.py``, which by design does not apply to the unauthenticated
``/api/v1/*`` surface. An anonymous caller could therefore issue an unlimited
number of requests against state-changing and provider-backed routes.

Per-request cost was already bounded by request-schema constraints
(``max_iterations`` 1–5, ``raw_input`` <= 2000 chars) — see the audit's
CORRECTIONS 1 and 2. The exposure this module closes is unbounded request
COUNT.

WHAT THIS IS NOT
----------------
* It does NOT replace or weaken the tenant quota. Quota remains the
  authoritative per-tenant control; this is a per-client-IP ceiling that
  exists even where no tenant is authenticated.
* It is not a security boundary on its own. It bounds volume, not identity.

IDENTITY OF A CLIENT
--------------------
The bucket key is derived from the transport peer address
(``request.client.host``) — the socket-level source, which a client cannot
forge with a header, API key, or query parameter. Header-supplied forwarding
values are deliberately NOT used as the bucket key, because they are
attacker-controlled and would make the limiter trivially bypassable.

A forwarded-proxy deployment should run the proxy in front of this service so
the peer address is the real client. When a trusted proxy is configured via
``PLATRIXA_TRUSTED_PROXY_COUNT``, the leftmost address from
``X-Forwarded-For`` is used instead.

SCOPE AND DEFAULTS
------------------
Limits are per-path-class and deliberately generous for the metered ``/v1``
developer API (which already has quota) and tight for the anonymous
``/api/v1`` surface. Both are configurable by environment variable.

KNOWN LIMITATION (documented, not hidden)
-----------------------------------------
Counters are held in process memory. A multi-instance deployment enforces the
limit PER INSTANCE, not globally. A shared store (Redis) would be required for
a global ceiling. This is stated explicitly rather than implied.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger("platrixa.api")

# Defaults (requests per window, window seconds).
DEFAULT_API_V1_LIMIT = 30
DEFAULT_API_V1_WINDOW = 60.0
DEFAULT_V1_LIMIT = 600
DEFAULT_V1_WINDOW = 60.0

# Paths exempt from limiting: liveness must never be rate-limited, or a
# health probe could be throttled during an incident.
EXEMPT_EXACT = frozenset({"/api/v1/health", "/v1/health", "/v1/ready"})

_BUCKETS: Dict[Tuple[str, str], Deque[float]] = {}
_LOCK = threading.Lock()
_LAST_SWEEP = 0.0
_SWEEP_INTERVAL = 60.0


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using default %d", name, raw[:20], default)
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _client_key(request) -> str:
    """Bucket key: transport peer address, or a trusted forwarded address."""
    trusted = _env_int("PLATRIXA_TRUSTED_PROXY_COUNT", 0)
    if trusted > 0:
        forwarded = request.headers.get("x-forwarded-for", "") or ""
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= trusted:
            return parts[trusted - 1]
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host or "unknown"


def _sweep(now: float) -> None:
    """Drop buckets with no activity inside the window. Bounded work."""
    global _LAST_SWEEP
    if now - _LAST_SWEEP < _SWEEP_INTERVAL:
        return
    _LAST_SWEEP = now
    for key in list(_BUCKETS.keys()):
        bucket = _BUCKETS.get(key)
        if bucket is not None and bucket and (now - bucket[-1]) > 3600.0:
            _BUCKETS.pop(key, None)


def limits_for(path: str) -> Tuple[int, float]:
    """(max_requests, window_seconds) for a path."""
    if path.startswith("/v1/"):
        return (
            _env_int("PLATRIXA_V1_RATE_LIMIT", DEFAULT_V1_LIMIT),
            _env_float("PLATRIXA_V1_RATE_WINDOW", DEFAULT_V1_WINDOW),
        )
    if path.startswith("/api/v1/"):
        return (
            _env_int("PLATRIXA_API_V1_RATE_LIMIT", DEFAULT_API_V1_LIMIT),
            _env_float("PLATRIXA_API_V1_RATE_WINDOW", DEFAULT_API_V1_WINDOW),
        )
    return (0, 0.0)  # not rate limited


def check(path: str, client_key: str) -> Tuple[bool, int, int, float]:
    """
    Record one request against ``client_key`` for ``path``.

    Returns ``(allowed, remaining, retry_after_seconds, window_seconds)``.
    ``allowed`` is True when the request may proceed.

    Not rate limited paths return ``(True, -1, 0, 0.0)``.
    """
    max_requests, window = limits_for(path)
    if max_requests <= 0:
        return True, -1, 0.0, 0.0

    now = time.monotonic()
    bucket_key = (path.split("{")[0], client_key)
    with _LOCK:
        _sweep(now)
        bucket = _BUCKETS.get(bucket_key)
        if bucket is None:
            bucket = deque()
            _BUCKETS[bucket_key] = bucket
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            retry_after = max(0.0, bucket[0] + window - now)
            return False, 0, int(retry_after) + 1, window
        bucket.append(now)
        return True, max_requests - len(bucket), 0, window


def reset() -> None:
    """Test hook — clear all counters."""
    with _LOCK:
        _BUCKETS.clear()


__all__ = [
    "check",
    "limits_for",
    "reset",
    "EXEMPT_EXACT",
    "DEFAULT_API_V1_LIMIT",
    "DEFAULT_V1_LIMIT",
]
