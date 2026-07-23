"""
Provider Manager

Acts as the abstraction layer between Module 4 and all external
financial data providers.

Future modules (Module 5, Workspace, Portfolio, etc.) should NEVER
call external APIs directly.

Architecture

Module 5
    │
    ▼
Module 4 API
    │
    ▼
Provider Manager
    │
    ▼
Provider Adapter
    │
    ▼
External API

All providers must return data using the same internal schema.
"""



from providers.nse_adapter import NSEAdapter
from providers.bse_adapter import BSEAdapter
from providers.sebi_adapter import SEBIAdapter
from providers.fmp_adapter import FMPAdapter


class ProviderAdapter(ABC):
    """
    Abstract interface every provider must implement.
    """

    @abstractmethod
    def fetch_company_profile(self, provider_name: str, ticker: str) -> dict:
        pass

    @abstractmethod
    def fetch_financials(self, provider_name: str, ticker: str) -> dict:
        pass

    @abstractmethod
    def fetch_market_price(self, ticker: str):
        pass

    @abstractmethod
    def fetch_news(self, provider_name: str, ticker: str) -> list:
        pass

    @abstractmethod
    def fetch_filings(self, ticker: str):
        pass


class ProviderManager:
    """
    Central registry for every financial provider.

    This allows Module 4 to switch providers without
    changing business logic.
    """

    def __init__(self):
        self.providers = {}

    def register_provider(self, name: str, adapter: ProviderAdapter):
        """
        Register a provider.

        Example:
            provider_manager.register_provider(
                "nse",
                NSEAdapter()
            )
        """
        self.providers[name.lower()] = adapter

    def unregister_provider(self, name: str):
        """
        Remove provider.
        """
        self.providers.pop(name.lower(), None)

    def get_provider(self, name: str) -> ProviderAdapter:
        """
        Return provider instance.
        """

        provider = self.providers.get(name.lower())

        if provider is None:
            raise ValueError(
                f"Provider '{name}' is not registered."
            )

        return provider

    def list_providers(self):
        """
        Return registered providers.
        """
        return list(self.providers.keys())

    def has_provider(self, name: str):
        """
        Check provider availability.
        """
        return name.lower() in self.providers


# ----------------------------------------------------
# Singleton Provider Manager
# ----------------------------------------------------

provider_manager = ProviderManager()


# ----------------------------------------------------
# Register Default Providers
# ----------------------------------------------------

provider_manager.register_provider(
    "nse",
    NSEAdapter()
)

provider_manager.register_provider(
    "bse",
    BSEAdapter()
)

provider_manager.register_provider(
    "sebi",
    SEBIAdapter()
)

provider_manager.register_provider(
    "fmp",
    FMPAdapter()
)


# ----------------------------------------------------
# Future Providers (TODO)
# ----------------------------------------------------

"""
Examples

provider_manager.register_provider(
    "polygon",
    PolygonAdapter()
)

provider_manager.register_provider(
    "finnhub",
    FinnhubAdapter()
)

provider_manager.register_provider(
    "alpha_vantage",
    AlphaVantageAdapter()
)

provider_manager.register_provider(
    "twelve_data",
    TwelveDataAdapter()
)
"""
