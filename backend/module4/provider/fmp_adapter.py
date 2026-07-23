import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from .base import ProviderAdapter

logger = logging.getLogger(__name__)


class FMPAdapter(ProviderAdapter):
    """
    FinancialModelingPrep Provider

    TODO:
    Connect FinancialModelingPrep REST API.
    """

    def __init__(self, api_key=None):
        # Secrets Compatibility: Support both Streamlit Cloud secrets and local environment variables
        self.api_key = api_key
        if not self.api_key:
            try:
                import streamlit as st
                if hasattr(st, "secrets") and "FMP_API_KEY" in st.secrets:
                    self.api_key = st.secrets["FMP_API_KEY"]
            except ImportError:
                pass

        if not self.api_key:
            self.api_key = os.getenv("FMP_API_KEY")

        if not self.api_key:
            logger.error("FMP_API_KEY environment variable or Streamlit secret is missing.")
            raise ValueError("FMP_API_KEY must be provided, set in os.getenv, or configured in st.secrets.")

        # Base URL: Set clean endpoint target structure
        self.base_url = "https://financialmodelingprep.com/api/v3"
        self.session = requests.Session()
        
        # User-Agent Configuration
        self.session.headers.update({
            "User-Agent": "FinancialProdigy/1.0"
        })
        
        # Enhanced production-ready retry strategy with rate limit tracking (429)
        retry_strategy = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET"},
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.timeout = (5, 15)

    def _execute_request(self, endpoint, params=None):
        """Internal helper to securely route requests with standard exception handling."""
        url = f"{self.base_url}/{endpoint}"
        req_params = params.copy() if params else {}
        req_params["apikey"] = self.api_key

        try:
            response = self.session.get(url, params=req_params, timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            # Detect FMP API error payloads
            if isinstance(data, dict):
            if data.get("Error Message") or data.get("error"):
            raise RuntimeError(
            data.get("Error Message") or data.get("error")
            )
            if data is None:
                raise RuntimeError("Empty JSON returned by FMP.")
            return data
            
        except requests.exceptions.Timeout as e:
            logger.error(f"FMP API timeout on {endpoint}: {e}")
            raise RuntimeError("FMP API connection timed out.") from e
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            logger.error(f"HTTP {status} on {endpoint}")
            raise RuntimeError(f"FMP HTTP Error {status}") from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Network subsystem exception on {endpoint}: {e}")
            raise RuntimeError("Failed to communicate with FMP API.") from e
        except ValueError as e:
            logger.error(f"JSON decoding error on {endpoint}: {e}")
            raise RuntimeError("Malformed response payload received from FMP API.") from e

    def _safe_fetch(self, endpoint, params=None):
        """Safe execution boundary wrapper preventing isolated endpoint drops from killing pipelines."""
        try:
            return self._execute_request(endpoint, params)
        except Exception as e:
            logger.warning(f"{endpoint} failed: {e}")
            return []

    def fetch_company_profile(self, ticker):
        symbol = ticker.strip().upper()
        data = self._execute_request(f"profile/{symbol}")
        
        if not data or not isinstance(data, list):
            logger.warning(f"No profile payload for: {symbol}")
            return {}
            
        profile = data[0]
        return {
            "symbol": profile.get("symbol", symbol),
            "company_name": profile.get("companyName"),
            "price": profile.get("price"),
            "beta": profile.get("beta"),
            "vol_avg": profile.get("volAvg"),
            "mkt_cap": profile.get("mktCap"),
            "last_div": profile.get("lastDiv"),
            "range": profile.get("range"),
            "changes": profile.get("changes"),
            "exchange": profile.get("exchange"),
            "industry": profile.get("industry"),
            "sector": profile.get("sector"),
            "ceo": profile.get("ceo"),
            "website": profile.get("website"),
            "description": profile.get("description"),
            "image": profile.get("image")
        }

    def fetch_financials(self, ticker):
        """Fetches and builds isolation-bounded financial statements."""
        symbol = ticker.strip().upper()
        params = {"limit": 5}

        income = self._safe_fetch(f"income-statement/{symbol}", params)
        balance = self._safe_fetch(f"balance-sheet-statement/{symbol}", params)
        cash = self._safe_fetch(f"cash-flow-statement/{symbol}", params)

        return {
            "income_statement": income if isinstance(income, list) else [],
            "balance_sheet": balance if isinstance(balance, list) else [],
            "cash_flow": cash if isinstance(cash, list) else [],
        }

    def fetch_market_price(self, ticker):
        symbol = ticker.strip().upper()
        data = self._execute_request(f"quote/{symbol}")
        
        if not data or not isinstance(data, list):
            logger.warning(f"No price quote payload for: {symbol}")
            return {}

        quote = data[0]
        return {
            "symbol": quote.get("symbol", symbol),
            "name": quote.get("name"),
            "price": quote.get("price"),
            "changes_percentage": quote.get("changesPercentage"),
            "change": quote.get("change"),
            "day_low": quote.get("dayLow"),
            "day_high": quote.get("dayHigh"),
            "year_high": quote.get("yearHigh"),
            "year_low": quote.get("yearLow"),
            "market_cap": quote.get("marketCap"),
            "volume": quote.get("volume"),
            "timestamp": quote.get("timestamp")
        }

    def fetch_news(self, ticker):
        symbol = ticker.strip().upper()
        data = self._execute_request("stock_news", params={"tickers": symbol, "limit": 10})
        
        if not data or not isinstance(data, list):
            return []

        return [
            {
                "symbol": article.get("symbol"),
                "published_date": article.get("publishedDate"),
                "title": article.get("title"),
                "image": article.get("image"),
                "site": article.get("site"),
                "text": article.get("text"),
                "url": article.get("url")
            }
            for article in data
        ]

    def fetch_filings(self, ticker):
        symbol = ticker.strip().upper()
        data = self._execute_request(f"sec-filings/{symbol}", params={"limit": 5})
        
        if not data or not isinstance(data, list):
            logger.warning(f"No filings payload for: {symbol}")
            return []

        return [
            {
                "symbol": filing.get("symbol", symbol),
                "filling_date": filing.get("fillingDate"),
                "accepted_date": filing.get("acceptedDate"),
                "form": filing.get("type"),
                "link": filing.get("link"),
                "final_link": filing.get("finalLink")
            }
            for filing in data
        ]

    def __del__(self):
    try:
        self.session.close()
    except Exception:
        pass
        
