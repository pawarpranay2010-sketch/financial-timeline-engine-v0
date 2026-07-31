"""
RetrievalAgent

Retrieves stored company data from PostgreSQL and DBCache.

Responsibilities:
  - Query DatabaseManager for stored company records
  - Check DBCache for fresh data before fetching live
  - Retrieve stored financials, prices, news from PostgreSQL
  - Provide structured data for the EvidenceConsolidator
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fte.intelligence.retrieval_agent")


class RetrievalAgent:
    """
    Retrieves company intelligence from PostgreSQL/DBCache.

    Used by EvidenceConsolidator to supplement live DataAgent fetches
    with persisted data (e.g., historical financials already stored).
    """

    def __init__(self, ticker: str):
        self.ticker = ticker.strip().upper()
        self._dbm = None  # lazy init
        self._cache = None  # lazy init

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _get_dbm(self):
        if self._dbm is None:
            from backend.module4.database_manager import DatabaseManager
            self._dbm = DatabaseManager()
            logger.info(
                f"[RetrievalAgent] DatabaseManager initialized (ticker={self.ticker})"
            )
        return self._dbm

    def _get_cache(self):
        if self._cache is None:
            from backend.module4.db_cache import DBCache
            if self._dbm is None:
                self._get_dbm()
            self._cache = DBCache(self._dbm)
            logger.info(
                f"[RetrievalAgent] DBCache initialized (ticker={self.ticker})"
            )
        return self._cache

    # ------------------------------------------------------------------
    # Retrieval methods
    # ------------------------------------------------------------------

    def get_company(self) -> Optional[Dict[str, Any]]:
        """Retrieve company record from PostgreSQL."""
        try:
            dbm = self._get_dbm()
            company = dbm.get_latest_company(self.ticker)
            if company:
                return {
                    "ticker": company.ticker,
                    "company_name": company.company_name,
                    "exchange": company.exchange,
                    "sector": company.sector,
                    "industry": company.industry,
                    "market_cap": company.market_cap,
                    "currency": company.currency,
                    "source": "postgresql",
                }
        except Exception as e:
            logger.warning(f"[RetrievalAgent] get_company failed: {e}")
        return None

    def get_financials(self) -> List[Dict[str, Any]]:
        """Retrieve latest financial statements from PostgreSQL."""
        try:
            dbm = self._get_dbm()
            company = dbm.get_latest_company(self.ticker)
            if company:
                return dbm.get_latest_financials(company.id)
        except Exception as e:
            logger.warning(f"[RetrievalAgent] get_financials failed: {e}")
        return []

    def get_market_price(self) -> Optional[Dict[str, Any]]:
        """Retrieve latest market price from PostgreSQL."""
        try:
            dbm = self._get_dbm()
            price = dbm.get_latest_price(self.ticker)
            if price:
                return {
                    "price": price.close_price,
                    "open_price": price.open_price,
                    "high_price": price.high_price,
                    "low_price": price.low_price,
                    "volume": price.volume,
                    "trading_date": str(price.trading_date),
                    "source": "postgresql",
                }
        except Exception as e:
            logger.warning(f"[RetrievalAgent] get_market_price failed: {e}")
        return None

    def get_news(self) -> List[Dict[str, Any]]:
        """Retrieve latest news articles from PostgreSQL."""
        try:
            dbm = self._get_dbm()
            news_items = dbm.get_latest_news(self.ticker)
            return [
                {
                    "headline": n.headline,
                    "source": n.source,
                    "url": n.url,
                    "published_at": str(n.published_at) if n.published_at else None,
                }
                for n in news_items
            ]
        except Exception as e:
            logger.warning(f"[RetrievalAgent] get_news failed: {e}")
        return []

    def cache_company_profile(self) -> Optional[Dict[str, Any]]:
        """Check DBCache for a fresh company profile (avoids live API call)."""
        try:
            cache = self._get_cache()
            profile = cache.get_fresh_profile(self.ticker)
            if profile:
                logger.info(
                    f"[RetrievalAgent] DBCache HIT for {self.ticker} profile"
                )
                return profile
        except Exception as e:
            logger.warning(f"[RetrievalAgent] cache_company_profile failed: {e}")
        return None

    # ------------------------------------------------------------------
    # Full retrieval
    # ------------------------------------------------------------------

    def retrieve_all(self) -> Dict[str, Any]:
        """
        Retrieve all available stored data for this ticker.

        Returns a dict keyed by data type with retrieved values or
        empty placeholders.
        """
        results = {
            "ticker": self.ticker,
            "company": self.get_company(),
            "financials": self.get_financials(),
            "market_price": self.get_market_price(),
            "news": self.get_news(),
            "cache_profile": self.cache_company_profile(),
        }

        populated = sum(
            1 for v in results.values()
            if isinstance(v, (dict, list)) and v
        )
        logger.info(
            f"[RetrievalAgent] retrieve_all: {populated} data sources "
            f"available (ticker={self.ticker})"
        )
        return results
