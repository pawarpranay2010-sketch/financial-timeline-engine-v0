from abc import ABC, abstractmethod
from typing import Dict, List, Any


class ProviderAdapter(ABC):
    """
    Base interface for all financial data providers.

    Every provider must implement these methods and return data
    using Financial Prodigy's internal normalized schema.
    """

    @abstractmethod
    def fetch_company_profile(self, ticker: str) -> Dict[str, Any]:
        """Return normalized company profile."""
        pass

    @abstractmethod
    def fetch_financials(self, ticker: str) -> Dict[str, Any]:
        """Return normalized income statement, balance sheet and cash flow."""
        pass

    @abstractmethod
    def fetch_market_price(self, ticker: str) -> Dict[str, Any]:
        """Return latest market quote."""
        pass

    @abstractmethod
    def fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        """Return latest company news."""
        pass

    @abstractmethod
    def fetch_filings(self, ticker: str) -> List[Dict[str, Any]]:
        """Return regulatory filings."""
        pass
