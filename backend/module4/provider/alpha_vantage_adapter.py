"""
Alpha Vantage Provider

Free-tier: 5 API calls/minute, 500 calls/day. Uses Alpha Vantage REST API.

Endpoints:
  - Company overview: GET /query?function=OVERVIEW&symbol=AAPL&apikey=KEY
  - News: GET /query?function=NEWS_SENTIMENT&tickers=AAPL&apikey=KEY
  - Financials, price, filings — not implemented (return empty)

Response formats (news):
  {"items": "50", "feed": [{"title": "...", "url": "...", "time_published": "...",
    "authors": [...], "summary": "...", "source": "...", "category_within_source": "...",
    "source_domain": "...", "topics": [...], "overall_sentiment_score": 0.1, ...}]}

Response format (overview):
  {"Symbol": "AAPL", "Name": "Apple Inc", "Exchange": "NASDAQ", "Sector": "Technology",
   "Industry": "Consumer Electronics", "MarketCapitalization": "...", ...}

Env var: ALPHA_VANTAGE_API_KEY
"""

import logging
import os
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .base import ProviderAdapter

logger = logging.getLogger(__name__)


class AlphaVantageAdapter(ProviderAdapter):
    """
    Free financial data provider powered by Alpha Vantage.

    Requires ALPHA_VANTAGE_API_KEY environment variable or constructor param.
    Free tier: 5 calls/min, 500 calls/day.
    """

    def __init__(self, api_key=None):
        self.name = "alpha_vantage"

        # Key loading: constructor → env var → Streamlit secrets
        self.api_key = api_key
        key_source = "constructor"
        if not self.api_key:
            self.api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
            if self.api_key:
                key_source = "os.environ"
        if not self.api_key:
            try:
                import streamlit as st
                if hasattr(st, "secrets") and "ALPHA_VANTAGE_API_KEY" in st.secrets:
                    self.api_key = st.secrets["ALPHA_VANTAGE_API_KEY"]
                    key_source = "st.secrets"
            except Exception:
                pass

        if self.api_key:
            masked = f"{self.api_key[:4]}***{self.api_key[-4:]}" if len(self.api_key) >= 8 else "***"
            logger.info(f"[AlphaVantageAdapter] API key loaded from {key_source}: {masked}")
        else:
            logger.info("[AlphaVantageAdapter] Initialized (no API key — will report errors gracefully)")

        self.base_url = "https://www.alphavantage.co/query"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FinancialTimelineEngine/1.0"})

        retry_strategy = Retry(
            total=2, connect=2, read=2, backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET"}, raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.timeout = (5, 20)

    def _get(self, params: Dict[str, str]) -> Any:
        """Execute a GET request to Alpha Vantage with error handling."""
        if not self.api_key:
            raise RuntimeError("ALPHA_VANTAGE_API_KEY not configured")

        req_params = dict(params)
        req_params["apikey"] = self.api_key

        try:
            resp = self.session.get(self.base_url, params=req_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            # Detect Alpha Vantage error messages
            if isinstance(data, dict):
                if "Error Message" in data:
                    raise RuntimeError(data["Error Message"])
                if "Note" in data:
                    # Rate limit note
                    logger.warning(f"[AlphaVantageAdapter] Rate limit note: {data['Note'][:100]}")
                    raise RuntimeError("Alpha Vantage rate limit reached")

            return data
        except requests.exceptions.Timeout:
            raise RuntimeError("Alpha Vantage API timed out")
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Alpha Vantage HTTP {e.response.status_code}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Alpha Vantage request failed: {e}")

    def _safe_get(self, params: Dict[str, str]) -> Any:
        """Safe wrapper — returns None on failure."""
        try:
            return self._get(params)
        except Exception as e:
            logger.warning(f"[AlphaVantageAdapter] Request failed: {e}")
            return None

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    def fetch_company_profile(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch company overview from Alpha Vantage.

        Uses the OVERVIEW function endpoint.
        """
        symbol = ticker.strip().upper()
        data = self._safe_get({"function": "OVERVIEW", "symbol": symbol})

        if not data or not isinstance(data, dict) or not data.get("Name"):
            logger.warning(f"[AlphaVantageAdapter] No overview for {symbol}")
            return {}

        return {
            "symbol": data.get("Symbol", symbol),
            "company_name": data.get("Name"),
            "exchange": data.get("Exchange"),
            "sector": data.get("Sector"),
            "industry": data.get("Industry"),
            "market_cap": self._safe_float(data.get("MarketCapitalization")),
            "currency": None,
            "isin": None,
            "website": None,
            "description": data.get("Description"),
            "logo": None,
        }

    def _safe_float(self, val) -> float:
        """Safely convert a value to float."""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def fetch_financials(self, ticker: str) -> Dict[str, List]:
        """Not implemented via Alpha Vantage (requires INCOME_STATEMENT etc.).
        Return empty lists — pipeline handles this gracefully."""
        return {"income_statement": [], "balance_sheet": [], "cash_flow": []}

    def fetch_market_price(self, ticker: str) -> Dict[str, Any]:
        """Not implemented via Alpha Vantage (requires GLOBAL_QUOTE etc.).
        Return empty dict — pipeline handles this gracefully."""
        symbol = ticker.strip().upper()
        data = self._safe_get({"function": "GLOBAL_QUOTE", "symbol": symbol})

        if not data or "Global Quote" not in data:
            return {}

        quote = data["Global Quote"]
        return {
            "symbol": symbol,
            "price": self._safe_float(quote.get("05. price")),
            "day_low": self._safe_float(quote.get("04. low")),
            "day_high": self._safe_float(quote.get("03. high")),
            "open_price": self._safe_float(quote.get("02. open")),
            "changes_percentage": quote.get("10. change percent"),
            "change": self._safe_float(quote.get("09. change")),
            "volume": self._safe_float(quote.get("06. volume")),
            "timestamp": None,
        }

    def fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Fetch news articles from Alpha Vantage.

        Uses the NEWS_SENTIMENT endpoint.
        Returns normalized format matching the existing schema.
        """
        symbol = ticker.strip().upper()
        data = self._safe_get({
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "limit": "50",
        })

        if not data or not isinstance(data, dict):
            logger.info(f"[AlphaVantageAdapter] No news data for {symbol}")
            return []

        feed = data.get("feed", [])
        if not feed or not isinstance(feed, list):
            logger.info(f"[AlphaVantageAdapter] No news feed for {symbol}")
            return []

        result = []
        for article in feed[:20]:  # Cap at 20 articles
            if not isinstance(article, dict):
                continue

            # Convert Alpha Vantage timestamp format: "20240729T130000" → ISO
            pub_raw = article.get("time_published", "")
            pub_str = ""
            if pub_raw and len(pub_raw) >= 8:
                try:
                    pub_str = f"{pub_raw[:4]}-{pub_raw[4:6]}-{pub_raw[6:8]}T{pub_raw[9:11]}:{pub_raw[11:13]}:{pub_raw[13:15]}"
                except Exception:
                    pub_str = pub_raw

            result.append({
                "symbol": symbol,
                "published_date": pub_str,
                "title": article.get("title", "") or "",
                "image": None,
                "site": article.get("source", "") or "",
                "text": article.get("summary", "") or "",
                "url": article.get("url", "") or "",
            })

        logger.info(f"[AlphaVantageAdapter] Fetched {len(result)} news articles for {symbol}")
        return result

    def fetch_filings(self, ticker: str) -> List[Dict[str, Any]]:
        """Not available via Alpha Vantage. Return empty list."""
        return []
