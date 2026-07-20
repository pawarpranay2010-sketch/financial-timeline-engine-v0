"""
Financial Intelligence Ingestion Service

Pipeline

Provider
    ↓
Validation
    ↓
Normalization
    ↓
Database
    ↓
Redis Cache

Future:

Scheduler
    ↓
IngestionService
"""

from module4.provider_manager import provider_manager
from module4.validator import Validator
from module4.normalizer import Normalizer
from module4.database_manager import DatabaseManager
from module4.cache_manager import CacheManager


class IngestionService:

    def __init__(self):

        self.validator = Validator()
        self.normalizer = Normalizer()

        self.database = DatabaseManager()
        self.cache = CacheManager()

    def ingest_company(self, provider_name: str, ticker: str):

        provider = provider_manager.get_provider(provider_name)

        profile = provider.fetch_company_profile(ticker)

        financials = provider.fetch_financials(ticker)

        price = provider.fetch_market_price(ticker)

        news = provider.fetch_news(ticker)

        profile = self.validator.validate(profile)

        financials = self.validator.validate(financials)

        price = self.validator.validate(price)

        news = self.validator.validate(news)

        profile = self.normalizer.normalize_company(profile)

        financials = self.normalizer.normalize_financials(financials)

        price = self.normalizer.normalize_price(price)

        news = self.normalizer.normalize_news(news)

        self.database.save_company(profile)

        self.database.save_financials(financials)

        self.database.save_price(price)

        self.database.save_news(news)

        self.cache.cache_company(profile)

        self.cache.cache_price(price)

        self.cache.cache_news(news)

        return {
            "status": "success",
            "ticker": ticker
        }


ingestion_service = IngestionService()
