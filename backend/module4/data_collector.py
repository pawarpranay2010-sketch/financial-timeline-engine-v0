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

from .provider_manager import ProviderManager
from .validator import Validator
from .normalizer import Normalizer
from .database_manager import DatabaseManager
from .cache_manager import CacheManager


class DataCollector:

    def __init__(self):

        from .provider_manager import provider_manager

        self.providers = provider_manager

        self.validator = Validator()

        self.normalizer = Normalizer()

        self.database = DatabaseManager()

        self.cache = CacheManager()

    def collect_company(self, provider_name, ticker):

        provider = self.providers.get_provider(provider_name)

        if provider is None:
            raise Exception("Provider not registered")

        raw = provider.company_profile(ticker)

        self.validator.validate(raw)

        normalized = self.normalizer.company(raw)

        self.database.save_company(normalized)

        self.cache.set(
            "company",
            ticker,
            normalized,
            86400
        )

        return normalized


"""
Future Methods

collect_prices()

collect_financials()

collect_news()

collect_filings()

collect_corporate_actions()

collect_ratios()

"""
