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
"""

from backend.module4.provider_manager import provider_manager
from backend.module4.validator import Validator
from backend.module4.normalizer import Normalizer
from backend.module4.database_manager import DatabaseManager
from backend.module4.cache_manager import CacheManager
from backend.module4.logger import logger


class IngestionService:

    def __init__(self):

        self.validator = Validator()
        self.normalizer = Normalizer()

        self.database = DatabaseManager()
        self.cache = CacheManager()

    def ingest_company(self, provider_name: str, ticker: str):

        logger.info(f"[Ingestion] Starting ingestion for {ticker} ({provider_name})")

        try:

            provider = provider_manager.get_provider(provider_name)

            profile = provider.fetch_company_profile(ticker)
            financials = provider.fetch_financials(ticker)
            price = provider.fetch_market_price(ticker)
            news = provider.fetch_news(ticker)

            logger.info("[Ingestion] Provider fetch completed")

            profile = self.validator.validate(profile)
            financials = self.validator.validate(financials)
            price = self.validator.validate(price)
            news = self.validator.validate(news)

            logger.info("[Ingestion] Validation completed")

            profile = self.normalizer.normalize_company(profile)
            financials = self.normalizer.normalize_financials(financials)
            price = self.normalizer.normalize_price(price)
            news = self.normalizer.normalize_news(news)

            logger.info("[Ingestion] Normalization completed")

            self.database.begin_transaction()

            self.database.save_company(profile)
            self.database.save_financials(financials)
            self.database.save_market_price(price)
            self.database.save_news(news)

            self.database.commit()

            logger.info("[Ingestion] Database updated successfully")

            self.cache.cache_company(profile)
            self.cache.cache_price(price)
            self.cache.cache_news(news)

            logger.info("[Ingestion] Redis cache updated")

            return {
                "status": "success",
                "ticker": ticker,
            }

        except Exception as e:

            logger.error(f"[Ingestion] Failed for {ticker}: {e}")

            self.database.rollback()

            return {
                "status": "failed",
                "ticker": ticker,
                "error": str(e),
            }

        finally:

            self.database.close()


ingestion_service = IngestionService()
