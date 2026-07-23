import logging
# 1. Import ProviderAdapter directly from base to completely eliminate duplication
from providers.base import ProviderAdapter

from providers.nse_adapter import NSEAdapter
from providers.bse_adapter import BSEAdapter
from providers.sebi_adapter import SEBIAdapter
from providers.fmp_adapter import FMPAdapter

# 5. Missing logger: Configured correctly using the standard logging module
logger = logging.getLogger(__name__)


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

    # 3. Routing Methods: Keep rest of application from interacting directly with adapters
    def fetch_company_profile(self, provider_name: str, ticker: str):
        """Route tracking for company profile metadata payloads."""
        return self.get_provider(provider_name).fetch_company_profile(ticker)

    def fetch_financials(self, provider_name: str, ticker: str):
        """Route tracking for three-statement comprehensive compound dictionary objects."""
        return self.get_provider(provider_name).fetch_financials(ticker)

    def fetch_market_price(self, provider_name: str, ticker: str):
        """Route tracking for real-time spot quote metrics."""
        return self.get_provider(provider_name).fetch_market_price(ticker)

    def fetch_news(self, provider_name: str, ticker: str):
        """Route tracking for stock macro context streams."""
        return self.get_provider(provider_name).fetch_news(ticker)

    def fetch_filings(self, provider_name: str, ticker: str):
        """Route tracking for standard SEC regulatory filings summaries."""
        return self.get_provider(provider_name).fetch_filings(ticker)


# Singleton Provider Manager
provider_manager = ProviderManager()


# 4. Unsafe provider registration: Wrapped in try/except boundaries with logger fallbacks
# This prevents application boot crashes if API keys or dependencies fail to load.
try:
    provider_manager.register_provider("nse", NSEAdapter())
except Exception as e:
    logger.warning(f"Could not automatically register default provider 'nse': {e}")

try:
    provider_manager.register_provider("bse", BSEAdapter())
except Exception as e:
    logger.warning(f"Could not automatically register default provider 'bse': {e}")

try:
    provider_manager.register_provider("sebi", SEBIAdapter())
except Exception as e:
    logger.warning(f"Could not automatically register default provider 'sebi': {e}")

try:
    provider_manager.register_provider("fmp", FMPAdapter())
except Exception as e:
    logger.warning(f"Could not automatically register default provider 'fmp': {e}")
    
