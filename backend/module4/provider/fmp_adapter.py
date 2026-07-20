from .base import ProviderAdapter


class FMPAdapter(ProviderAdapter):
    """
    FinancialModelingPrep Provider

    TODO:
    Connect FinancialModelingPrep REST API.
    """

    def fetch_company_profile(self, ticker):
        raise NotImplementedError

    def fetch_financials(self, ticker):
        raise NotImplementedError

    def fetch_market_price(self, ticker):
        raise NotImplementedError

    def fetch_news(self, ticker):
        raise NotImplementedError

    def fetch_filings(self, ticker):
        raise NotImplementedError
