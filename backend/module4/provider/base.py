from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    """
    Base class for all external financial providers.

    Every provider MUST return data using the same internal schema.
    """

    @abstractmethod
    def fetch_company_profile(self, ticker: str) -> dict:
        pass

    @abstractmethod
    def fetch_financials(self, ticker: str) -> dict:
        pass

    @abstractmethod
    def fetch_market_price(self, ticker: str) -> dict:
        pass

    @abstractmethod
    def fetch_news(self, ticker: str) -> list[dict]:
        pass

    @abstractmethod
    def fetch_filings(self, ticker: str) -> list[dict]:
        pass
