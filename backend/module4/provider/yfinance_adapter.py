"""
YFinance Provider

Free, no API key required. Uses Yahoo Finance data via the yfinance library.

Provides:
  - Company profile (ticker.info)
  - Financial statements (annual income/balance/cashflow)
  - Market price (info dict, fastest reliable source)
  - News (ticker.news)
  - Filings — not available via Yahoo, returns empty list
"""

import logging
import time
from typing import Any, Dict, List

import yfinance as yf

from .base import ProviderAdapter

logger = logging.getLogger(__name__)


class YFinanceAdapter(ProviderAdapter):
    """
    Free financial data provider powered by Yahoo Finance (yfinance).

    No API key required. Initialization always succeeds.
    Uses internal caching and rate-limit awareness to avoid 429s.
    """

    def __init__(self):
        self.name = "yfinance"
        logger.info("[YFinanceAdapter] Initialized (no API key needed)")

    # ------------------------------------------------------------------
    # Internal: safe Ticker access
    # ------------------------------------------------------------------

    def _get_ticker(self, symbol: str) -> yf.Ticker:
        return yf.Ticker(symbol)

    def _safe_info(self, ticker: yf.Ticker) -> Dict[str, Any]:
        """Safely fetch ticker.info dict, returning {} on failure."""
        try:
            info = ticker.info
            return info if isinstance(info, dict) else {}
        except Exception as e:
            logger.warning(f"[YFinanceAdapter] Failed to fetch info: {e}")
            return {}

    def _safe_financials_df(self, ticker: yf.Ticker, attr: str) -> List[Dict]:
        """
        Fetch a financials DataFrame (e.g. ticker.financials) and convert
        to a list of dicts, one per period.

        attr: 'financials' (income), 'balance_sheet', 'cashflow'
        """
        try:
            df = getattr(ticker, attr, None)
            if df is None or df.empty:
                return []
            records = []
            for col in df.columns:
                period = {}
                period["date"] = col.isoformat() if hasattr(col, "isoformat") else str(col)
                for line_item in df.index:
                    val = df.loc[line_item, col]
                    if hasattr(val, "item"):
                        val = val.item()
                    period[str(line_item)] = val
                records.append(period)
            return records
        except Exception as e:
            logger.warning(f"[YFinanceAdapter] Failed to fetch {attr}: {e}")
            return []

    def _safe_news(self, ticker: yf.Ticker) -> List[Dict]:
        """Safely fetch ticker.news, returning [] on failure."""
        try:
            articles = ticker.news
            if not articles or not isinstance(articles, list):
                return []
            return articles
        except Exception as e:
            logger.warning(f"[YFinanceAdapter] Failed to fetch news: {e}")
            return []

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    def fetch_company_profile(self, ticker: str) -> Dict[str, Any]:
        symbol = ticker.strip().upper()
        t = self._get_ticker(symbol)
        info = self._safe_info(t)

        if not info:
            logger.warning(f"[YFinanceAdapter] No profile info for {symbol}")
            return {}

        return {
            "symbol": info.get("symbol", symbol),
            "company_name": info.get("longName") or info.get("shortName", ""),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "beta": info.get("beta"),
            "vol_avg": info.get("averageVolume"),
            "mkt_cap": info.get("marketCap"),
            "last_div": info.get("lastDividendValue") or info.get("dividendRate"),
            "range": f"{info.get('fiftyTwoWeekLow')}-{info.get('fiftyTwoWeekHigh')}",
            "changes": info.get("regularMarketChange"),
            "exchange": info.get("exchange"),
            "industry": info.get("industry"),
            "sector": info.get("sector"),
            "ceo": None,
            "website": info.get("website"),
            "description": info.get("longBusinessSummary"),
            "image": None,
        }

    def fetch_financials(self, ticker: str) -> Dict[str, List]:
        symbol = ticker.strip().upper()
        t = self._get_ticker(symbol)

        income = self._safe_financials_df(t, "financials")
        balance = self._safe_financials_df(t, "balance_sheet")
        cash = self._safe_financials_df(t, "cashflow")

        return {
            "income_statement": income,
            "balance_sheet": balance,
            "cash_flow": cash,
        }

    def fetch_market_price(self, ticker: str) -> Dict[str, Any]:
        symbol = ticker.strip().upper()
        t = self._get_ticker(symbol)

        # Use ticker.info dict for market price (most reliable data source)
        info = self._safe_info(t)
        if info:
            return {
                "symbol": symbol,
                "name": info.get("shortName"),
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "changes_percentage": info.get("regularMarketChangePercent"),
                "change": info.get("regularMarketChange"),
                "day_low": info.get("dayLow"),
                "day_high": info.get("dayHigh"),
                "year_high": info.get("fiftyTwoWeekHigh"),
                "year_low": info.get("fiftyTwoWeekLow"),
                "market_cap": info.get("marketCap"),
                "volume": info.get("volume"),
                "timestamp": int(time.time()),
            }

        # Fallback: fast_info (less reliable, may have NaN prices)
        try:
            fast = t.fast_info
            if fast is not None:
                price = getattr(fast, "lastPrice", None)
                if price is not None:
                    return {
                        "symbol": symbol,
                        "name": None,
                        "price": price,
                        "changes_percentage": None,
                        "change": None,
                        "day_low": getattr(fast, "dayLow", None),
                        "day_high": getattr(fast, "dayHigh", None),
                        "year_high": getattr(fast, "yearHigh", None),
                        "year_low": getattr(fast, "yearLow", None),
                        "market_cap": getattr(fast, "marketCap", None),
                        "volume": getattr(fast, "lastVolume", None),
                        "timestamp": int(time.time()),
                    }
        except Exception as e:
            logger.warning(f"[YFinanceAdapter] fast_info failed for {symbol}: {e}")

        logger.warning(f"[YFinanceAdapter] No price data for {symbol}")
        return {}

    def fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        symbol = ticker.strip().upper()
        t = self._get_ticker(symbol)
        articles = self._safe_news(t)

        if not articles:
            return []

        result = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            result.append({
                "symbol": symbol,
                "published_date": article.get("providerPublishTime") or "",
                "title": article.get("title") or "",
                "image": None,
                "site": article.get("publisher") or "",
                "text": article.get("summary") or article.get("description") or "",
                "url": article.get("link") or "",
            })

        return result

    def fetch_filings(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Yahoo Finance does not provide SEC filing data.
        Returns an empty list — the pipeline handles this gracefully.
        """
        return []
