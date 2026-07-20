"""
Provider Manager

Acts as the abstraction layer between Module 4 and external APIs.

Future providers plug into this interface.

Nothing else in the application should directly call an API.

Architecture

Module5
↓

Module4 API

↓

ProviderManager

↓

Provider Adapter

↓

External API

"""

from abc import ABC
from abc import abstractmethod


class ProviderAdapter(ABC):

    @abstractmethod
    def fetch_company_profile(self, ticker: str):
        pass

    @abstractmethod
    def fetch_financials(self, ticker: str):
        pass

    @abstractmethod
    def fetch_market_price(self, ticker: str):
        pass

    @abstractmethod
    def fetch_news(self, ticker: str):
        pass


class ProviderManager:

    def __init__(self):

        self.providers = {}

    def register_provider(self, name, adapter):

        self.providers[name] = adapter

    def get_provider(self, name):

        if name not in self.providers:
            raise ValueError(f"Provider {name} not registered")

        return self.providers[name]


provider_manager = ProviderManager()

"""
TODO

Register adapters here

Examples

provider_manager.register_provider(
    "fmp",
    FinancialModelingPrepAdapter()
)

provider_manager.register_provider(
    "nse",
    NSEAdapter()
)

"""
