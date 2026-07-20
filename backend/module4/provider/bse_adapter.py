from .base import ProviderAdapter


class BSEAdapter(ProviderAdapter):
    """
    Bombay Stock Exchange Provider

    TODO:
    Connect BSE APIs.
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
