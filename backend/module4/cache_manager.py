"""
Redis Cache Manager

Responsibilities
----------------
- Store frequently requested data
- Reduce PostgreSQL queries
- Reduce external API usage

Cache Flow

Redis
↓

PostgreSQL
↓

External APIs

"""

import json


class CacheManager:

    def __init__(self):
        """
        TODO

        Connect Redis here.
        """

        self.redis = None

    # --------------------------------------------------
    # Company
    # --------------------------------------------------

    def cache_company(self, company):

        print(f"[CACHE] Company cached: {company.get('ticker')}")

        # TODO
        # redis.set(...)

    def get_company(self, ticker):

        print(f"[CACHE] Company lookup: {ticker}")

        return None

    # --------------------------------------------------
    # Price
    # --------------------------------------------------

    def cache_price(self, price):

        print("[CACHE] Latest price cached")

    def get_price(self, ticker):

        print("[CACHE] Price lookup")

        return None

    # --------------------------------------------------
    # Financials
    # --------------------------------------------------

    def cache_financials(self, financials):

        print("[CACHE] Financials cached")

    def get_financials(self, ticker):

        print("[CACHE] Financial lookup")

        return None

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def cache_news(self, news):

        print("[CACHE] News cached")

    def get_news(self, ticker):

        print("[CACHE] News lookup")

        return None

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def cache_filings(self, filings):

        print("[CACHE] Filings cached")

    def get_filings(self, ticker):

        print("[CACHE] Filing lookup")

        return None

    # --------------------------------------------------
    # Generic Helpers
    # --------------------------------------------------

    def exists(self, key):

        print(f"[CACHE] Exists check: {key}")

        return False

    def delete(self, key):

        print(f"[CACHE] Delete cache: {key}")

    def clear(self):

        print("[CACHE] Clear all cache")

    # --------------------------------------------------
    # Future TTL Support
    # --------------------------------------------------

    def set_with_ttl(self, key, value, ttl):

        print(f"[CACHE] Setting TTL={ttl}")

        # TODO
        # redis.setex(...)
