"""
Background Data Collector

Responsible for:

- Requesting provider data
- Passing through validator
- Passing through normalizer
- Saving database
- Updating Redis

NO business logic here.

Pipeline only.
"""

from .provider_manager import provider_manager
from .validator import Validator
from .normalizer import Normalizer
from .database_manager import DatabaseManager
from .cache_manager import CacheManager
from .logger import logger


class DataCollector:

    def __init__(self):

        # Singleton Provider Manager
        self.providers = provider_manager

        self.validator = Validator()

        self.normalizer = Normalizer()

        self.database = DatabaseManager()

        self.cache = CacheManager()

        logger.info("[Collector] Data Collector initialized")


    def collect_company(self, provider_name, ticker):

        logger.info(
            f"[Collector] Collecting company data: {ticker} using {provider_name}"
        )

        try:

            # Provider
            provider = self.providers.get_provider(provider_name)

            raw = provider.fetch_company_profile(ticker)

            logger.info("[Collector] Provider data received")


            # Validation
            validated = self.validator.validate(raw)

            logger.info("[Collector] Validation completed")


            # Normalization
            normalized = self.normalizer.normalize_company(validated)

            logger.info("[Collector] Normalization completed")


            # Database
            self.database.begin_transaction()

            self.database.save_company(normalized)

            self.database.commit()

            logger.info("[Collector] Database updated")


            # Cache
            self.cache.cache_company(normalized)

            logger.info("[Collector] Cache updated")


            return normalized


        except Exception as e:

            logger.error(
                f"[Collector] Failed collecting {ticker}: {e}"
            )

            self.database.rollback()

            raise


        finally:

            self.database.close()



"""
Future Methods

collect_prices()

collect_financials()

collect_news()

collect_filings()

collect_corporate_actions()

collect_ratios()

"""


data_collector = DataCollector()
