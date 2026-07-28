"""
Financial Intelligence Ingestion Service

Pipeline

Provider Orchestrator
        ↓
PostgreSQL Cache
        ↓
Provider Fetch (if needed)
        ↓
Validation
        ↓
Normalization
        ↓
Database
        ↓
Redis Cache
"""

from backend.module4.provider_orchestrator import ProviderOrchestrator
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

        self.provider = ProviderOrchestrator()

    def ingest_company(self, provider_name: str, ticker: str):

        logger.info(f"[Ingestion] Starting ingestion for {ticker}")

        try:

            # --------------------------------------------------
            # Provider Orchestrator
            # --------------------------------------------------

            data = self.provider.fetch_company(provider_name, ticker)

            logger.info(
                f"[Provider] Cache Status: {data.get('cache_status', 'unknown')}"
            )

            logger.info(
                f"[Provider] Provider Used: {data.get('provider_used', provider_name)}"
            )

            if data.get("fallback_provider"):
                logger.info(
                    f"[Provider] Fallback Used: {data['fallback_provider']}"
                )

            profile = data["profile"]
            financials = data["financials"]
            price = data["price"]
            news = data["news"]

            logger.info("[Ingestion] Provider fetch completed")

            # --------------------------------------------------
            # Validation
            # --------------------------------------------------

            profile_validation = self.validator.validate_company(profile)

            if not profile_validation.valid:
                raise ValueError(
                    f"Profile validation failed: {'; '.join(profile_validation.errors)}"
                )

            logger.info("[Ingestion] Validation completed")

            # --------------------------------------------------
            # Normalization
            # --------------------------------------------------

            profile = self.normalizer.normalize_company(profile)

            if isinstance(financials, dict):
                financials = {
                    statement_type: [
                        self.normalizer.normalize_financial(item)
                        for item in rows
                    ]
                    for statement_type, rows in financials.items()
                    if isinstance(rows, list)
                }

            price = self.normalizer.normalize_price(price)

            if isinstance(news, list):
                news = [
                    self.normalizer.normalize_news(item)
                    for item in news
                ]
            else:
                news = [
                    self.normalizer.normalize_news(news)
                ]

            logger.info("[Ingestion] Normalization completed")

            # --------------------------------------------------
            # Database
            # --------------------------------------------------

            self.database.begin_transaction()

            # Save Company
            self.database.save_company(profile)

            # Flush so PostgreSQL generates company ID before
            # dependent inserts.
            self.database.connection.flush()

            logger.info("[Ingestion] Company saved and flushed")

            # Save Financial Statements
            self.database.save_financials(financials)

            logger.info("[Ingestion] Financial statements saved")

            # Save Market Price
            self.database.save_market_price(price)

            logger.info("[Ingestion] Market price saved")

            # Save News
            for item in news:
                self.database.save_news(item)

            logger.info("[Ingestion] News saved")

            self.database.commit()

            logger.info("[Ingestion] Database updated")

            # --------------------------------------------------
            # Redis Cache
            # --------------------------------------------------

            self.cache.cache_company(profile)
            self.cache.cache_price(price)
            self.cache.cache_news(news)

            logger.info("[Ingestion] Cache updated")

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
