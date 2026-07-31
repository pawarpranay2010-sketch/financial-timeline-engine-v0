"""
Redis Cache Manager

Delegates to the RedisCache module. Maintains backward compatibility
with the existing CacheManager API used by IngestionService and other
modules.

Responsibilities
----------------
- Store frequently requested data in Redis
- Reduce PostgreSQL queries
- Reduce external API usage
- Graceful degradation when Redis is unavailable
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.module4.redis_cache import RedisCache

logger = logging.getLogger("fte.cache_manager")


class CacheManager:
    """
    Cache manager that delegates to the RedisCache module.

    Maintains the same public API as the original stub for
    backward compatibility with IngestionService.
    """

    def __init__(self):
        self._redis = RedisCache()
        # Check if Redis is actually connected
        self._available = self._redis._connected if hasattr(self._redis, '_connected') else False
        if self._available:
            logger.info("[CACHE] Cache Manager initialized (Redis connected)")
        else:
            logger.info("[CACHE] Cache Manager initialized (Redis unavailable — running without cache)")

    # --------------------------------------------------
    # Company
    # --------------------------------------------------

    def cache_company(self, company: Dict) -> None:
        ticker = company.get("ticker", "unknown")
        logger.info(f"[CACHE] Company cached: {ticker}")
        self._redis.set_profile(ticker, company)

    def get_company(self, ticker: str) -> Optional[Dict]:
        logger.info(f"[CACHE] Company lookup: {ticker}")
        return self._redis.get_profile(ticker)

    # --------------------------------------------------
    # Price
    # --------------------------------------------------

    def cache_price(self, price: Dict) -> None:
        logger.info("[CACHE] Latest price cached")
        ticker = price.get("symbol", price.get("ticker", "unknown"))
        self._redis.set_price(ticker, price)

    def get_price(self, ticker: str) -> Optional[Dict]:
        logger.info(f"[CACHE] Price lookup: {ticker}")
        return self._redis.get_price(ticker)

    # --------------------------------------------------
    # Financials
    # --------------------------------------------------

    def cache_financials(self, financials) -> None:
        logger.info("[CACHE] Financials cached")
        # Financials may be a dict or list — store as-is
        self._redis.set("financials", "latest", financials)

    def get_financials(self, ticker: str):
        logger.info(f"[CACHE] Financial lookup: {ticker}")
        return self._redis.get_financials(ticker)

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def cache_news(self, news) -> None:
        logger.info("[CACHE] News cached")
        self._redis.set("news", "latest", news)

    def get_news(self, ticker: str):
        logger.info(f"[CACHE] News lookup: {ticker}")
        return self._redis.get_news(ticker)

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def cache_filings(self, filings) -> None:
        logger.info("[CACHE] Filings cached")
        self._redis.set("filings", "latest", filings)

    def get_filings(self, ticker: str):
        logger.info(f"[CACHE] Filing lookup: {ticker}")
        return self._redis.get_filings(ticker)

    # --------------------------------------------------
    # Generic Helpers
    # --------------------------------------------------

    def exists(self, key: str) -> bool:
        logger.info(f"[CACHE] Exists check: {key}")
        return False  # Redis key format differs; caller should use RedisCache directly

    def delete(self, key: str) -> None:
        logger.warning(f"[CACHE] Delete cache: {key}")
        self._redis.delete("generic", key)

    def clear(self) -> None:
        logger.warning("[CACHE] Clear all cache")
        self._redis.clear()

    # --------------------------------------------------
    # TTL Support
    # --------------------------------------------------

    def set_with_ttl(self, key: str, value: Any, ttl: int) -> None:
        logger.info(f"[CACHE] Setting key={key} with TTL={ttl}")
        # Parse data_type from key prefix if possible
        parts = key.split(":")
        data_type = parts[1] if len(parts) >= 2 else "generic"
        self._redis.set(data_type, key, value)

    # --------------------------------------------------
    # Close
    # --------------------------------------------------

    def close(self) -> None:
        logger.info("[CACHE] Cache Manager closed")
        self._redis.close()
