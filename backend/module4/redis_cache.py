"""
Redis Cache Module

In-memory cache layer using Redis, sitting in front of the PostgreSQL
DBCache and external provider adapters.

Cache lookup order:
    Redis
    ↓ (miss)
    PostgreSQL (DBCache)
    ↓ (miss)
    External Provider
    ↓
    Save to PostgreSQL
    ↓
    Save to Redis
    ↓
    Return Response

TTL Strategy (all configurable):
    Company Profile    → 24 hours   (profile_ttl)
    Financials         → 24 hours   (financials_ttl)
    Market Price       → 5 minutes  (price_ttl)
    News               → 30 minutes (news_ttl)
    Filings            → 24 hours   (filings_ttl)
    Provider Health    → 5 minutes  (health_ttl)

Graceful degradation:
  - If Redis is unavailable → log warning, return None (caller falls through)
  - If Redis connection drops mid-operation → log warning, return None
  - If Redis URL is not configured → log info, skip Redis entirely

Serialization:
  - All values are JSON-serialized
  - Keys follow the pattern: fte:{data_type}:{ticker}
  - Market prices use: fte:price:{ticker}
  - Company profiles use: fte:profile:{ticker}
  - Financials use: fte:financials:{ticker}
  - News uses: fte:news:{ticker}
  - Filings use: fte:filings:{ticker}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fte.redis_cache")


# ---------------------------------------------------------------------------
# Default TTLs (seconds)
# ---------------------------------------------------------------------------

DEFAULT_TTLS: Dict[str, int] = {
    "profile":    86400,  # 24 hours
    "financials": 86400,  # 24 hours
    "price":        300,  # 5 minutes
    "news":        1800,  # 30 minutes
    "filings":    86400,  # 24 hours
    "health":       300,  # 5 minutes
}

_KEY_PREFIX = "fte"


# ---------------------------------------------------------------------------
# Redis Cache
# ---------------------------------------------------------------------------

class RedisCache:
    """
    Redis-backed cache for Module 4 data.

    All cache methods return None on:
      - Redis unavailable
      - Key not found (cache miss)
      - Serialization error

    This allows the caller to fall through to the next cache layer
    or provider without branching logic.
    """

    def __init__(self, redis_url: Optional[str] = None, ttls: Optional[Dict[str, int]] = None):
        self._client = None
        self._connected = False
        self._hits = 0
        self._misses = 0
        self._errors = 0

        # TTL configuration — merge defaults with overrides
        self._ttls = dict(DEFAULT_TTLS)
        if ttls:
            self._ttls.update(ttls)

        # Determine Redis URL: constructor arg → env var → config default
        url = redis_url or os.getenv("REDIS_URL") or ""
        if not url:
            logger.info("[RedisCache] No REDIS_URL configured — Redis cache disabled")
            return

        self._connect(url)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self, url: str) -> None:
        """Attempt Redis connection. Logs warning on failure — never crashes."""
        try:
            import redis as redis_module
            self._client = redis_module.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Ping to verify connection
            self._client.ping()
            self._connected = True
            logger.info(f"[RedisCache] Connected to Redis at {url[:url.rfind('@')+1] if '@' in url else url}***")
        except Exception as exc:
            self._connected = False
            self._client = None
            logger.warning(f"[RedisCache] Redis unavailable: {exc}. Running without Redis cache.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, data_type: str, ticker: str) -> str:
        """Generate a namespaced Redis key."""
        return f"{_KEY_PREFIX}:{data_type}:{ticker.lower()}"

    def _ttl(self, data_type: str) -> int:
        """Return TTL in seconds for the given data type."""
        return self._ttls.get(data_type, DEFAULT_TTLS.get(data_type, 3600))

    def _is_ready(self) -> bool:
        """Check if Redis is connected and usable."""
        if not self._connected or self._client is None:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            self._connected = False
            logger.warning("[RedisCache] Connection lost — falling through to PostgreSQL")
            return False

    def _hit(self, data_type: str, ticker: str) -> None:
        self._hits += 1
        logger.info(f"[RedisCache] HIT  {data_type:<12} ticker={ticker}")

    def _miss(self, data_type: str, ticker: str) -> None:
        self._misses += 1
        logger.info(f"[RedisCache] MISS {data_type:<12} ticker={ticker}")

    def _error(self, data_type: str, ticker: str, exc: str) -> None:
        self._errors += 1
        logger.warning(f"[RedisCache] ERROR {data_type:<12} ticker={ticker} | {exc}")

    # ------------------------------------------------------------------
    # Generic get / set
    # ------------------------------------------------------------------

    def get(self, data_type: str, ticker: str) -> Optional[Any]:
        """Generic cache lookup. Returns deserialized value or None."""
        if not self._is_ready():
            return None
        key = self._key(data_type, ticker)
        try:
            raw = self._client.get(key)
            if raw is not None:
                self._hit(data_type, ticker)
                return json.loads(raw)
            self._miss(data_type, ticker)
            return None
        except Exception as exc:
            self._error(data_type, ticker, str(exc))
            return None

    def set(self, data_type: str, ticker: str, value: Any) -> bool:
        """Generic cache write with TTL. Returns True on success."""
        if not self._is_ready() or value is None:
            return False
        key = self._key(data_type, ticker)
        ttl = self._ttl(data_type)
        try:
            serialized = json.dumps(value, default=str)
            self._client.setex(key, ttl, serialized)
            logger.info(f"[RedisCache] SET  {data_type:<12} ticker={ticker} TTL={ttl}s")
            return True
        except Exception as exc:
            self._error(data_type, ticker, str(exc))
            return False

    def delete(self, data_type: str, ticker: str) -> bool:
        """Delete a cached entry."""
        if not self._is_ready():
            return False
        key = self._key(data_type, ticker)
        try:
            self._client.delete(key)
            logger.info(f"[RedisCache] DEL  {data_type:<12} ticker={ticker}")
            return True
        except Exception as exc:
            self._error(data_type, ticker, str(exc))
            return False

    # ------------------------------------------------------------------
    # Typed convenience methods (mirror DBCache interface)
    # ------------------------------------------------------------------

    def get_profile(self, ticker: str) -> Optional[Dict]:
        return self.get("profile", ticker)

    def set_profile(self, ticker: str, profile: Dict) -> bool:
        return self.set("profile", ticker, profile)

    def get_financials(self, ticker: str) -> Optional[List[Dict]]:
        return self.get("financials", ticker)

    def set_financials(self, ticker: str, financials: List[Dict]) -> bool:
        return self.set("financials", ticker, financials)

    def get_price(self, ticker: str) -> Optional[Dict]:
        return self.get("price", ticker)

    def set_price(self, ticker: str, price: Dict) -> bool:
        return self.set("price", ticker, price)

    def get_news(self, ticker: str) -> Optional[List[Dict]]:
        return self.get("news", ticker)

    def set_news(self, ticker: str, news: List[Dict]) -> bool:
        return self.set("news", ticker, news)

    def get_filings(self, ticker: str) -> Optional[List[Dict]]:
        return self.get("filings", ticker)

    def set_filings(self, ticker: str, filings: List[Dict]) -> bool:
        return self.set("filings", ticker, filings)

    # ------------------------------------------------------------------
    # Provider health — cached in Redis for cross-process visibility
    # ------------------------------------------------------------------

    def get_health_status(self, provider: str) -> Optional[Dict]:
        return self.get("health", provider)

    def set_health_status(self, provider: str, status: Dict) -> bool:
        return self.set("health", provider, status)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        """Return hit/miss/error counters for diagnostics."""
        total = self._hits + self._misses
        return {
            "hits":          self._hits,
            "misses":        self._misses,
            "errors":        self._errors,
            "total":         total,
            "connected":     self._connected,
            "hit_rate_pct":  round(self._hits / total * 100, 2) if total else 0.0,
            "miss_rate_pct": round(self._misses / total * 100, 2) if total else 0.0,
            "error_rate_pct": round(self._errors / total * 100, 2) if total else 0.0,
        }

    def clear(self) -> bool:
        """Flush all fte-prefixed keys from Redis."""
        if not self._is_ready():
            return False
        try:
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = self._client.scan(cursor=cursor, match=f"{_KEY_PREFIX}:*", count=100)
                if keys:
                    self._client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            logger.info(f"[RedisCache] Cleared {deleted} fte-prefixed keys")
            return True
        except Exception as exc:
            logger.warning(f"[RedisCache] Clear failed: {exc}")
            return False

    def close(self) -> None:
        """Close the Redis connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._connected = False
        logger.info("[RedisCache] Connection closed")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

redis_cache = RedisCache()
