import logging
from typing import Dict, List, Any

# Imported from central base; no duplicated definitions
from providers.base import ProviderAdapter

from providers.nse_adapter import NSEAdapter
from providers.bse_adapter import BSEAdapter
from providers.sebi_adapter import SEBIAdapter
from providers.fmp_adapter import FMPAdapter

logger = logging.getLogger(__name__)


class ProviderManager:
    """
    Central registry for every financial provider.

    This allows Module 4 to switch providers without    
    changing business logic.    
    """    

    def __init__(self):    
        self.providers: Dict[str, ProviderAdapter] = {}    

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

    def list_providers(self) -> List[str]:    
        """    
        Return registered providers.    
        """    
        return list(self.providers.keys())    

    def has_provider(self, name: str) -> bool:    
        """    
        Check provider availability.    
        """    
        return name.lower() in self.providers

    # 2 & 3. Added precise type hints and informative docstrings to routing methods
    def fetch_company_profile(self, provider_name: str, ticker: str) -> Dict[str, Any]:
        """
        Routes the profile request to the specified provider adapter.
        Returns a dictionary containing standardized company details.
        """
        return self.get_provider(provider_name).fetch_company_profile(ticker)

    def fetch_financials(self, provider_name: str, ticker: str) -> Dict[str, Any]:
        """
        Routes the financial data request to the specified provider adapter.
        Returns a structured dictionary with income_statement, balance_sheet, and cash_flow lists.
        """
        return self.get_provider(provider_name).fetch_financials(ticker)

    def fetch_market_price(self, provider_name: str, ticker: str) -> Dict[str, Any]:
        """
        Routes the market price request to the specified provider adapter.
        Returns a dictionary containing real-time price and quote variables.
        """
        return self.get_provider(provider_name).fetch_market_price(ticker)

    def fetch_news(self, provider_name: str, ticker: str) -> List[Dict[str, Any]]:
        """
        Routes the stock news request to the specified provider adapter.
        Returns a list of dictionaries with matching recent articles.
        """
        return self.get_provider(provider_name).fetch_news(ticker)

    def fetch_filings(self, provider_name: str, ticker: str) -> List[Dict[str, Any]]:
        """
        Routes the regulatory filing request to the specified provider adapter.
        Returns a list of dictionaries capturing tracking URLs and metadata.
        """
        return self.get_provider(provider_name).fetch_filings(ticker)


# Singleton Provider Manager Instance
provider_manager = ProviderManager()


# 1. Cleaner loop execution parsing registrations without repetitive try/except syntax noise
def initialize_default_providers():
    """Initializes and registers baseline data provider adapters with exception protection."""
    providers_map = {
        "nse": NSEAdapter,
        "bse": BSEAdapter,
        "sebi": SEBIAdapter,
        "fmp": FMPAdapter,
    }

    for name, adapter_class in providers_map.items():
        try:
            provider_manager.register_provider(name, adapter_class())
        except Exception as e:
            logger.warning(f"Failed to register provider '{name}': {e}")
            
