"""
DataAgent

Fetches live company intelligence from Module 4's ProviderOrchestrator.

Responsibilities:
  - Initialize and manage ProviderOrchestrator
  - Fetch company profiles, financials, market prices, news, and filings
  - Gracefully handle provider failures (yfinance fallback chain)
  - Provide structured data dicts ready for the EvidenceConsolidator
"""

from __future__ import annotations

import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fte.intelligence.data_agent")


class DataAgent:
    """
    Orchestrates Module 4 data collection for the investment memo pipeline.

    Uses the same ProviderOrchestrator instance that the rest of Module 4
    uses, so caching (DBCache) and provider failover are transparent.
    """

    def __init__(self, ticker: str):
        self.ticker = ticker.strip().upper()
        self._orch = None  # lazy init

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _get_orch(self):
        if self._orch is None:
            from backend.module4.provider_manager import initialize_default_providers
            from backend.module4.provider_orchestrator import ProviderOrchestrator

            initialize_default_providers()
            self._orch = ProviderOrchestrator()
            logger.info(
                "[DataAgent] ProviderOrchestrator initialized "
                f"(ticker={self.ticker})"
            )
        return self._orch

    # ------------------------------------------------------------------
    # Fetch methods — each returns a dict with metadata
    # ------------------------------------------------------------------

    def fetch_company_profile(self) -> Dict[str, Any]:
        """Fetch company profile via Module 4 failover chain."""
        orch = self._get_orch()
        start = time.monotonic()
        try:
            data = orch.fetch_company_profile(self.ticker)
            ms = round((time.monotonic() - start) * 1000)
            logger.info(f"[DataAgent] Company profile fetched: {ms}ms")
            return {
                "success": True,
                "data": data or {},
                "source": "module4",
                "latency_ms": ms,
            }
        except Exception as e:
            ms = round((time.monotonic() - start) * 1000)
            logger.warning(f"[DataAgent] Company profile failed: {e} [{ms}ms]")
            return {
                "success": False,
                "data": {},
                "source": "module4",
                "error": str(e),
                "latency_ms": ms,
            }

    def fetch_financials(self) -> Dict[str, Any]:
        """Fetch financial statements via Module 4 failover chain."""
        orch = self._get_orch()
        start = time.monotonic()
        try:
            data = orch.fetch_financials(self.ticker)
            ms = round((time.monotonic() - start) * 1000)
            logger.info(f"[DataAgent] Financials fetched: {ms}ms")
            return {
                "success": True,
                "data": data or {},
                "source": "module4",
                "latency_ms": ms,
            }
        except Exception as e:
            ms = round((time.monotonic() - start) * 1000)
            logger.warning(f"[DataAgent] Financials failed: {e} [{ms}ms]")
            return {
                "success": False,
                "data": {},
                "source": "module4",
                "error": str(e),
                "latency_ms": ms,
            }

    def fetch_market_price(self) -> Dict[str, Any]:
        """Fetch current market price via Module 4 failover chain."""
        orch = self._get_orch()
        start = time.monotonic()
        try:
            data = orch.fetch_market_price(self.ticker)
            ms = round((time.monotonic() - start) * 1000)
            logger.info(f"[DataAgent] Market price fetched: {ms}ms")
            return {
                "success": True,
                "data": data or {},
                "source": "module4",
                "latency_ms": ms,
            }
        except Exception as e:
            ms = round((time.monotonic() - start) * 1000)
            logger.warning(f"[DataAgent] Market price failed: {e} [{ms}ms]")
            return {
                "success": False,
                "data": {},
                "source": "module4",
                "error": str(e),
                "latency_ms": ms,
            }

    def fetch_news(self) -> Dict[str, Any]:
        """Fetch recent news articles via Module 4 failover chain."""
        orch = self._get_orch()
        start = time.monotonic()
        try:
            data = orch.fetch_news(self.ticker)
            ms = round((time.monotonic() - start) * 1000)
            count = len(data) if isinstance(data, list) else 0
            logger.info(f"[DataAgent] News fetched: {count} articles [{ms}ms]")
            return {
                "success": True,
                "data": data if isinstance(data, list) else [],
                "source": "module4",
                "latency_ms": ms,
                "article_count": count,
            }
        except Exception as e:
            ms = round((time.monotonic() - start) * 1000)
            logger.warning(f"[DataAgent] News failed: {e} [{ms}ms]")
            return {
                "success": False,
                "data": [],
                "source": "module4",
                "error": str(e),
                "latency_ms": ms,
                "article_count": 0,
            }

    def fetch_filings(self) -> Dict[str, Any]:
        """Fetch regulatory filings (if available)."""
        orch = self._get_orch()
        start = time.monotonic()
        try:
            data = orch.fetch_filings(self.ticker)
            ms = round((time.monotonic() - start) * 1000)
            count = len(data) if isinstance(data, list) else 0
            logger.info(f"[DataAgent] Filings fetched: {count} items [{ms}ms]")
            return {
                "success": True,
                "data": data if isinstance(data, list) else [],
                "source": "module4",
                "latency_ms": ms,
                "filing_count": count,
            }
        except Exception as e:
            ms = round((time.monotonic() - start) * 1000)
            logger.info(f"[DataAgent] Filings unavailable: {e} [{ms}ms]")
            return {
                "success": False,
                "data": [],
                "source": "module4",
                "error": str(e),
                "latency_ms": ms,
                "filing_count": 0,
            }

    # ------------------------------------------------------------------
    # Bulk fetch — all data types in one call
    # ------------------------------------------------------------------

    def fetch_all(self) -> Dict[str, Any]:
        """
        Fetch all company intelligence data types.

        Returns a structured dict keyed by data type, each with
        success/failure metadata so the EvidenceConsolidator can
        decide what to include.
        """
        results = {
            "ticker": self.ticker,
            "company_profile": self.fetch_company_profile(),
            "market_price": self.fetch_market_price(),
            "financials": self.fetch_financials(),
            "news": self.fetch_news(),
            "filings": self.fetch_filings(),
        }

        success_count = sum(
            1 for v in results.values() if isinstance(v, dict) and v.get("success")
        )
        total = sum(1 for k, v in results.items() if k != "ticker")
        logger.info(
            f"[DataAgent] fetch_all: {success_count}/{total} data types succeeded "
            f"(ticker={self.ticker})"
        )
        return results
