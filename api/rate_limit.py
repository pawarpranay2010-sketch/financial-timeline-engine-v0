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
The bucket key is a single client IP, selected in this order:

1. The SIGNED EDGE CLAIM (``backend.auth.client_ip_claim``). The Cloudflare
   Pages proxy knows the real address and asserts it with an HMAC bound to the
   visitor assertion of the SAME request. It is the only source that survives
   the current topology, where the proxy forwards an allowlist of headers and
   Railway rewrites ``X-Forwarded-For``/``X-Real-IP`` with the edge address.
   A direct caller cannot forge it (no secret) and cannot replay one captured
   from another request (the assertion it is bound to is single-use).
2. ``PLATRIXA_CLIENT_IP_HEADER`` (explicitly configured; only names in a
   fixed allowlist are honoured, e.g. ``cf-connecting-ip``). Cloudflare sets
   that header at its edge; Railway forwards it untouched.
3. ``PLATRIXA_TRUSTED_PROXY_COUNT`` > 0: the ``N``-th address from the RIGHT
   of ``X-Forwarded-For``. Each trusted hop APPENDS the address it saw, so the
   rightmost entries are the only ones written by infrastructure; anything
   further left is caller-supplied noise and is never used. A chain shorter
   than ``N``, or a malformed address, falls back to the transport peer.
4. The transport peer (``request.client.host``), which no header can forge.

Caveat: a DIRECTLY exposed backend would let a caller set
``cf-connecting-ip``, so only configure ``PLATRIXA_CLIENT_IP_HEADER`` when the
service is actually behind that proxy. ``X-Forwarded-For`` is never used when
no trusted proxy is configured (the previous code took the LEFTMOST entry,
which a caller could set freely). Neither header is ever trusted from a
request that carries no signed claim for it.

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


# Only these header names may be configured as the trusted client-IP source.
# An arbitrary name would let an operator accidentally trust a browser header.
TRUSTED_IP_HEADERS = frozenset(
    {"cf-connecting-ip", "x-real-ip", "true-client-ip", "fly-client-ip"}
)


def _valid_port(value: str) -> bool:
    """True for a decimal port in 1..65535, with no sign, space or padding."""
    if not value.isdigit() or len(value) > 5:
        return False
    return 0 < int(value) <= 65535


def _parse_ip(value: Optional[str]) -> Optional[str]:
    """Canonical IP string, or None when the value is not a single address.

    Accepts bare IPv4/IPv6, ``1.2.3.4:5678`` and ``[2001:db8::1]:443``;
    rejects lists, hostnames, ports on IPv6 without brackets, and anything
    a header could use to smuggle a second identity.
    """
    import ipaddress

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or len(raw) > 64:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            return None
        rest = raw[end + 1 :]
        if rest:
            # A port is allowed only where the grammar permits one, and only
            # when it is a port: "[::1]:99999" is not an address.
            if not rest.startswith(":") or not _valid_port(rest[1:]):
                return None
        raw = raw[1:end]
    elif raw.count(":") == 1:  # IPv4 with a port
        host, _, port = raw.partition(":")
        if not _valid_port(port):
            return None
        raw = host
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        return str(parsed.ipv4_mapped)
    return str(parsed)


def _trusted_ip_header() -> str:
    name = (os.getenv("PLATRIXA_CLIENT_IP_HEADER", "") or "").strip().lower()
    return name if name in TRUSTED_IP_HEADERS else ""


def _signed_edge_ip(request) -> Optional[str]:
    """The edge-asserted client address, or None when it is not provable.

    Verification is entirely in ``backend.auth.client_ip_claim``: the claim is
    trusted only when its HMAC (bound to this request's visitor assertion)
    matches. A malformed, unsigned, forged or replayed claim is simply absent.
    """
    from backend.auth import client_ip_claim

    raw = client_ip_claim.verified_client_ip(
        surface=request.headers.get(client_ip_claim.SURFACE_HEADER),
        assertion=request.headers.get(client_ip_claim.ASSERTION_HEADER),
        client_ip=request.headers.get(client_ip_claim.CLAIM_HEADER),
        signature=request.headers.get(client_ip_claim.CLAIM_SIGNATURE_HEADER),
    )
    if not raw:
        return None
    return _parse_ip(raw)


def _client_key(request) -> str:
    """Bucket key: one client IP from the trusted chain, or the peer."""
    signed = _signed_edge_ip(request)
    if signed:
        return signed

    configured = _trusted_ip_header()
    if configured:
        parsed = _parse_ip(request.headers.get(configured))
        if parsed:
            return parsed

    trusted = _env_int("PLATRIXA_TRUSTED_PROXY_COUNT", 0)
    if trusted > 0:
        forwarded = request.headers.get("x-forwarded-for", "") or ""
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= trusted:
            # RIGHTMOST-trusted: trusted hops append the address they saw, so
            # this entry is infrastructure-written, not caller-supplied.
            parsed = _parse_ip(parts[-trusted])
            if parsed:
                return parsed

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
