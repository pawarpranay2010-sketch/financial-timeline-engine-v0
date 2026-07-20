"""
Redis Cache Layer

Flow:

Check Redis
↓

Check PostgreSQL
↓

If missing:
Fetch Provider
↓

Validate
↓

Normalize
↓

Save PostgreSQL

↓

Save Redis

↓

Return
"""

from datetime import timedelta
import json

try:
    import redis
except ImportError:
    redis = None

from .config import Config


class CacheManager:

    def __init__(self):

        self.client = None

        if redis:

            self.client = redis.Redis(
                host=Config.REDIS_HOST,
                port=Config.REDIS_PORT,
                db=Config.REDIS_DB,
                decode_responses=True
            )

    def _key(self, category, identifier):
        return f"{category}:{identifier}"

    def get(self, category, identifier):

        if not self.client:
            return None

        key = self._key(category, identifier)

        value = self.client.get(key)

        if value:

            return json.loads(value)

        return None

    def set(self, category, identifier, data, ttl):

        if not self.client:
            return

        key = self._key(category, identifier)

        self.client.setex(
            key,
            ttl,
            json.dumps(data)
        )

    def invalidate(self, category, identifier):

        if not self.client:
            return

        self.client.delete(self._key(category, identifier))


class CacheTTL:

    COMPANY_PROFILE = timedelta(days=7)

    LATEST_PRICE = timedelta(seconds=5)

    RATIOS = timedelta(hours=6)

    NEWS = timedelta(minutes=15)

    FILINGS = timedelta(hours=24)

    HISTORICAL = timedelta(days=30)
