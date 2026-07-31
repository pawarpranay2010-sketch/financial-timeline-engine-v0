"""
Finnhub Provider

Free-tier: 60 API calls/minute. Uses Finnhub REST API.

Endpoints:
  - Company profile: GET /stock/profile2?symbol=AAPL&token=KEY
  - News: GET /company-news?symbol=AAPL&from=DATE&to=DATE&token=KEY
  - Financials, price, filings — not implemented (return empty/fake as needed)

Response formats (news):
  [{"category": "...", "datetime": 1700000000, "headline": "...",
    "id": 123, "image": "...", "related": "...", "source": "...",
    "summary": "...", "url": "..."}]

Env var: FINNHUB_API_KEY
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .base import ProviderAdapter

logger = logging.getLogger(__name__)


class FinnhubAdapter(ProviderAdapter):
    """
    Free financial data provider powered by Finnhub.

    Requires FINNHUB_API_KEY environment variable or constructor param.
    """

    def __init__(self, api_key=None):
        self.name = "finnhub"

        # Key loading: constructor → env var → Streamlit secrets
        self.api_key = api_key
        key_source = "constructor"
        if not self.api_key:
            self.api_key = os.getenv("FINNHUB_API_KEY")
            if self.api_key:
                key_source = "os.environ"
        if not self.api_key:
            try:
                import streamlit as st
                if hasattr(st, "secrets") and "FINNHUB_API_KEY" in st.secrets:
                    self.api_key = st.secrets["FINNHUB_API_KEY"]
                    key_source = "st.secrets"
            except Exception:
                pass

        if self.api_key:
            masked = f"{self.api_key[:4]}***{self.api_key[-4:]}" if len(self.api_key) >= 8 else "***"
            logger.info(f"[FinnhubAdapter] API key loaded from {key_source}: {masked}")
        else:
            logger.info("[FinnhubAdapter] Initialized (no API key — will report errors gracefully)")

        self.base_url = "https://finnhub.io/api/v1"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FinancialTimelineEngine/1.0"})

        retry_strategy = Retry(
            total=2, connect=2, read=2, backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET"}, raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.timeout = (5, 15)

    def _get(self, endpoint: str, params: Dict[str, Any] = None) -> Any:
        """Execute a GET request with API key injection and error handling."""
        if not self.api_key:
            logger.warning(f"[FinnhubAdapter] No API key configured — skipping {endpoint}")
            raise RuntimeError("FINNHUB_API_KEY not configured")

        url = f"{self.base_url}/{endpoint}"
        req_params = dict(params or {})
        req_params["token"] = self.api_key

        try:
            resp = self.session.get(url, params=req_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"])
            return data
        except requests.exceptions.Timeout:
            raise RuntimeError("Finnhub API timed out")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 429:
                raise RuntimeError("Finnhub rate limit exceeded (429)")
            raise RuntimeError(f"Finnhub HTTP {status}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Finnhub request failed: {e}")

    def _safe_get(self, endpoint: str, params: Dict[str, Any] = None) -> Any:
        """Safe wrapper — returns empty result on failure."""
        try:
            return self._get(endpoint, params)
        except Exception as e:
            logger.warning(f"[FinnhubAdapter] {endpoint} failed: {e}")
            return None

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    def fetch_company_profile(self, ticker: str) -> Dict[str, Any]:
        """Fetch company profile via Finnhub stock/profile2 endpoint."""
        symbol = ticker.strip().upper()
        data = self._safe_get("stock/profile2", {"symbol": symbol})

        if not data or not isinstance(data, dict) or not data.get("name"):
            logger.warning(f"[FinnhubAdapter] No profile for {symbol}")
            return {}

        return {
            "symbol": data.get("ticker", symbol),
            "company_name": data.get("name"),
            "exchange": data.get("exchange"),
            "industry": data.get("finnhubIndustry"),
            "sector": None,  # Finnhub doesn't provide sector separately
            "market_cap": data.get("marketCapitalization"),
            "currency": data.get("currency"),
            "isin": None,
            "website": data.get("weburl"),
            "description": None,
            "logo": data.get("logo"),
        }

    def fetch_financials(self, ticker: str) -> Dict[str, List]:
        """Not fully supported — Finnhub has basic financials but requires
        additional API calls. Return empty for now."""
        return {"income_statement": [], "balance_sheet": [], "cash_flow": []}

    def fetch_market_price(self, ticker: str) -> Dict[str, Any]:
        """Finnhub has /quote endpoint for price. Implement minimally."""
        symbol = ticker.strip().upper()
        data = self._safe_get("quote", {"symbol": symbol})

        if not data or not isinstance(data, dict):
            return {}

        return {
            "symbol": symbol,
            "price": data.get("c"),       # Current price
            "day_low": data.get("l"),
            "day_high": data.get("h"),
            "open_price": data.get("o"),
            "changes_percentage": None,
            "change": data.get("d"),
            "volume": None,
            "timestamp": data.get("t"),
        }

    def fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Fetch company news from Finnhub.

        Uses the last 7 days as the date range (free tier limit).
        Returns normalized format matching the existing schema.
        """
        symbol = ticker.strip().upper()
        today = datetime.utcnow()
        seven_days_ago = today - timedelta(days=7)

        data = self._safe_get("company-news", {
            "symbol": symbol,
            "from": seven_days_ago.strftime("%Y-%m-%d"),
            "to": today.strftime("%Y-%m-%d"),
        })

        if not data or not isinstance(data, list):
            logger.info(f"[FinnhubAdapter] No news for {symbol}")
            return []

        result = []
        for article in data[:20]:  # Cap at 20 articles
            if not isinstance(article, dict):
                continue

            pub_ts = article.get("datetime")
            if pub_ts:
                try:
                    pub_dt = datetime.fromtimestamp(int(pub_ts))
                    pub_str = pub_dt.isoformat()
                except (ValueError, TypeError, OSError):
                    pub_str = ""
            else:
                pub_str = ""

            result.append({
                "symbol": symbol,
                "published_date": pub_str,
                "title": article.get("headline", "") or "",
                "image": article.get("image", ""),
                "site": article.get("source", ""),
                "text": article.get("summary", "") or "",
                "url": article.get("url", ""),
            })

        logger.info(f"[FinnhubAdapter] Fetched {len(result)} news articles for {symbol}")
        return result

    def fetch_filings(self, ticker: str) -> List[Dict[str, Any]]:
        """Finnhub does not directly provide SEC filings through a simple endpoint.
        Return empty list — pipeline handles this gracefully."""
        return []
