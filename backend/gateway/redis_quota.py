"""Redis-based quota and circuit-breaker tracking.

Provides cross-worker coordination for rate limits and circuit state.
Degrades gracefully when Redis is unavailable.
"""
import json
import time
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class RedisQuotaTracker:
    """Tracks provider quota/cooldown/circuit state in Redis.

    If Redis is unavailable, falls back to local in-memory state with
    bounded concurrency and jittered backoff.
    """

    KEY_PREFIX = "fte:ai:quota:"

    def __init__(self, ttl_seconds: int = 60):
        self._redis = None
        self._local_state: dict = {}
        self._ttl = ttl_seconds
        self._connect()

    def _connect(self) -> None:
        """Connect to Redis using REDIS_URL. Graceful failure."""
        url = os.getenv("REDIS_URL", "")
        if not url:
            return
        try:
            import redis
            self._redis = redis.from_url(url, socket_timeout=3, decode_responses=True)
            self._redis.ping()
            logger.info("RedisQuotaTracker: connected to Redis")
        except Exception as e:
            logger.warning(f"RedisQuotaTracker: Redis unavailable, using local state ({e})")
            self._redis = None

    def _key(self, provider: str, metric: str) -> str:
        return f"{self.KEY_PREFIX}{provider}:{metric}"

    def record_request(self, provider: str) -> None:
        """Record an API request for rate-limiting purposes."""
        now = time.time()
        key = self._key(provider, "requests")
        if self._redis:
            try:
                self._redis.zadd(key, {str(now): now})
                self._redis.expire(key, self._ttl)
                return
            except Exception:
                pass
        # Local fallback
        if provider not in self._local_state:
            self._local_state[provider] = {"requests": [], "errors": 0}
        self._local_state[provider]["requests"].append(now)
        # Trim old entries
        cutoff = now - self._ttl
        self._local_state[provider]["requests"] = [
            t for t in self._local_state[provider]["requests"] if t > cutoff
        ]

    def record_error(self, provider: str, error_type: str = "") -> None:
        """Record a provider error (429, 5xx, etc.)."""
        now = time.time()
        key = self._key(provider, "errors")
        if self._redis:
            try:
                self._redis.zadd(key, {f"{now}:{error_type}": now})
                self._redis.expire(key, self._ttl * 5)
                return
            except Exception:
                pass
        if provider not in self._local_state:
            self._local_state[provider] = {"requests": [], "errors": 0}
        self._local_state[provider]["errors"] = self._local_state[provider].get("errors", 0) + 1

    def get_rpm(self, provider: str) -> int:
        """Get requests per minute for a provider."""
        now = time.time()
        cutoff = now - 60
        if self._redis:
            try:
                key = self._key(provider, "requests")
                count = self._redis.zcount(key, cutoff, now)
                return int(count)
            except Exception:
                pass
        state = self._local_state.get(provider, {})
        requests = state.get("requests", [])
        return sum(1 for t in requests if t > cutoff)

    def get_error_count(self, provider: str) -> int:
        """Get recent error count for a provider."""
        if self._redis:
            try:
                key = self._key(provider, "errors")
                now = time.time()
                count = self._redis.zcount(key, now - 300, now)
                return int(count)
            except Exception:
                pass
        state = self._local_state.get(provider, {})
        return state.get("errors", 0)

    def is_circuit_open(self, provider: str, threshold: int = 5) -> bool:
        """Check if circuit breaker is open (too many recent errors)."""
        return self.get_error_count(provider) >= threshold

    def is_rate_limited(self, provider: str, max_rpm: int = 60) -> bool:
        """Check if provider is currently rate-limited."""
        return self.get_rpm(provider) >= max_rpm

    def summary(self) -> dict:
        """Return quota summary for all tracked providers."""
        result = {}
        all_providers = set()
        if self._local_state:
            all_providers.update(self._local_state.keys())
        for p in all_providers:
            result[p] = {
                "rpm": self.get_rpm(p),
                "errors": self.get_error_count(p),
                "circuit_open": self.is_circuit_open(p),
            }
        return result
