"""
Redis Cache Manager

Responsibilities
----------------
- Store frequently requested data
- Reduce PostgreSQL queries
- Reduce external API usage
"""

import json

from backend.module4.logger import logger


class CacheManager:

    def __init__(self):
        """
        TODO

        Connect Redis here.
        """
        self.redis = None
        logger.info("[CACHE] Cache Manager initialized")

    # --------------------------------------------------
    # Company
    # --------------------------------------------------

    def cache_company(self, company):

        logger.info(f"[CACHE] Company cached: {company.get('ticker')}")

        # TODO
        # self.redis.set(...)

    def get_company(self, ticker):

        logger.info(f"[CACHE] Company lookup: {ticker}")

        return None

    # --------------------------------------------------
    # Price
    # --------------------------------------------------

    def cache_price(self, price):

        logger.info("[CACHE] Latest price cached")

    def get_price(self, ticker):

        logger.info(f"[CACHE] Price lookup: {ticker}")

        return None

    # --------------------------------------------------
    # Financials
    # --------------------------------------------------

    def cache_financials(self, financials):

        logger.info("[CACHE] Financials cached")

    def get_financials(self, ticker):

        logger.info(f"[CACHE] Financial lookup: {ticker}")

        return None

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def cache_news(self, news):

        logger.info("[CACHE] News cached")

    def get_news(self, ticker):

        logger.info(f"[CACHE] News lookup: {ticker}")

        return None

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def cache_filings(self, filings):

        logger.info("[CACHE] Filings cached")

    def get_filings(self, ticker):

        logger.info(f"[CACHE] Filing lookup: {ticker}")

        return None

    # --------------------------------------------------
    # Generic Helpers
    # --------------------------------------------------

    def exists(self, key):

        logger.info(f"[CACHE] Exists check: {key}")

        return False

    def delete(self, key):

        logger.warning(f"[CACHE] Delete cache: {key}")

        # TODO
        # self.redis.delete(key)

    def clear(self):

        logger.warning("[CACHE] Clear all cache")

        # TODO
        # self.redis.flushdb()

    # --------------------------------------------------
    # Future TTL Support
    # --------------------------------------------------

    def set_with_ttl(self, key, value, ttl):

        logger.info(f"[CACHE] Setting key={key} with TTL={ttl}")

        # TODO
        # self.redis.setex(
        #     key,
        #     ttl,
        #     json.dumps(value)
        # )

    # --------------------------------------------------
    # Close
    # --------------------------------------------------

    def close(self):

        logger.info("[CACHE] Cache Manager closed")
