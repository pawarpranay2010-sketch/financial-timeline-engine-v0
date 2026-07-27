"""
DB Cache

Checks PostgreSQL for fresh data before making external API calls,
implementing the first layer of the source priority stack:

    1. PostgreSQL Cache  ← this module
    2. Official Filings (NSE / BSE / SEBI)
    3. Financial APIs (FMP …)

Freshness windows (aligned with config.py TTL settings and the spec):
    Company Profile : 7 days
    Financials      : 24 hours
    Market Price    : 5 minutes   (uses created_at, not trading_date,
                                   because trading_date is Date-only and
                                   cannot distinguish intra-day freshness)
    News            : 15 minutes  (uses published_at of most recent article)

All public methods return None on a cache miss so the caller falls
through to the provider layer without branching logic.

Hit/miss counters are accumulated in-process and surfaced via get_stats()
for the DiagnosticsService.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from backend.module4.database_manager import DatabaseManager
from backend.module4.logger import logger


# ---------------------------------------------------------------------------
# Freshness Windows
# ---------------------------------------------------------------------------

FRESHNESS_WINDOWS: Dict[str, timedelta] = {
    "profile":    timedelta(days=7),
    "financials": timedelta(hours=24),
    "price":      timedelta(minutes=5),
    "news":       timedelta(minutes=15),
}


def _strip_tz(dt: datetime) -> datetime:
    """Normalise to a naive UTC datetime for safe arithmetic."""
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def _is_fresh(dt: Optional[datetime], data_type: str) -> bool:
    """Return True if dt is non-None and within the freshness window."""
    if dt is None:
        return False
    age = datetime.utcnow() - _strip_tz(dt)
    return age <= FRESHNESS_WINDOWS[data_type]


# ---------------------------------------------------------------------------
# DB Cache
# ---------------------------------------------------------------------------

class DBCache:
    """
    Read-only PostgreSQL freshness cache.

    Returns data as plain dicts (same shape contract that provider adapters
    use downstream) with an extra ``_cache_source: "postgresql"`` marker so
    callers can distinguish cached from live data in logs and diagnostics.
    """

    def __init__(self, database: DatabaseManager):
        self._db = database
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _hit(self, data_type: str, ticker: str) -> None:
        self._hits += 1
        logger.info(f"[DBCache] HIT  {data_type:<12} ticker={ticker}")

    def _miss(self, data_type: str, ticker: str) -> None:
        self._misses += 1
        logger.info(f"[DBCache] MISS {data_type:<12} ticker={ticker}")

    # ------------------------------------------------------------------
    # Company Profile  — freshness window: 7 days
    # ------------------------------------------------------------------

    def get_fresh_profile(self, ticker: str) -> Optional[Dict]:
        """
        Returns a company profile dict if a record exists and was last
        updated within 7 days. Returns None on miss.

        Freshness field: Company.updated_at (DateTime, server-managed).
        """
        try:
            company = self._db.get_latest_company(ticker)
            if company and _is_fresh(company.updated_at, "profile"):
                self._hit("profile", ticker)
                return {
                    "ticker":        company.ticker,
                    "company_name":  company.company_name,
                    "exchange":      company.exchange,
                    "sector":        company.sector,
                    "industry":      company.industry,
                    "isin":          company.isin,
                    "market_cap":    company.market_cap,
                    "currency":      company.currency,
                    "_cache_source": "postgresql",
                }
        except Exception as exc:
            logger.warning(f"[DBCache] profile lookup error for {ticker}: {exc}")

        self._miss("profile", ticker)
        return None

    # ------------------------------------------------------------------
    # Financials  — freshness window: 24 hours
    # ------------------------------------------------------------------

    def get_fresh_financials(self, ticker: str) -> Optional[List[Dict]]:
        """
        Returns a list of financial statement dicts if records exist and
        the most recently ingested row is within 24 hours. Returns None on miss.

        Freshness field: max(Financial.created_at) across is_latest rows.
        """
        try:
            company = self._db.get_latest_company(ticker)
            if not company:
                self._miss("financials", ticker)
                return None

            records = self._db.get_latest_financials(company.id)
            if records:
                latest_dt = max(
                    (r.created_at for r in records if r.created_at),
                    default=None,
                )
                if _is_fresh(latest_dt, "financials"):
                    self._hit("financials", ticker)
                    return [
                        {
                            "company_id":          r.company_id,
                            "statement_type":      r.statement_type,
                            "fiscal_year":         r.fiscal_year,
                            "fiscal_quarter":      r.fiscal_quarter,
                            "revenue":             r.revenue,
                            "ebitda":              r.ebitda,
                            "ebit":                r.ebit,
                            "net_income":          r.net_income,
                            "eps":                 r.eps,
                            "total_assets":        r.total_assets,
                            "total_liabilities":   r.total_liabilities,
                            "shareholders_equity": r.shareholders_equity,
                            "operating_cash_flow": r.operating_cash_flow,
                            "free_cash_flow":      r.free_cash_flow,
                            "is_latest":           r.is_latest,
                            "source":              r.source,
                            "_cache_source":       "postgresql",
                        }
                        for r in records
                    ]
        except Exception as exc:
            logger.warning(f"[DBCache] financials lookup error for {ticker}: {exc}")

        self._miss("financials", ticker)
        return None

    # ------------------------------------------------------------------
    # Market Price  — freshness window: 5 minutes
    # ------------------------------------------------------------------

    def get_fresh_price(self, ticker: str) -> Optional[Dict]:
        """
        Returns a market price dict if the most recent price record was
        created within the last 5 minutes. Returns None on miss.

        Freshness field: MarketPrice.created_at (DateTime).

        Note: MarketPrice.trading_date is a Date column (day-resolution only)
        and cannot determine intra-day freshness. created_at is used instead.
        """
        try:
            price = self._db.get_latest_price(ticker)
            if price and _is_fresh(price.created_at, "price"):
                self._hit("price", ticker)
                return {
                    "price":          price.close_price,
                    "close_price":    price.close_price,
                    "open_price":     price.open_price,
                    "high_price":     price.high_price,
                    "low_price":      price.low_price,
                    "adjusted_close": price.adjusted_close,
                    "volume":         price.volume,
                    "trading_date":   str(price.trading_date),
                    "_cache_source":  "postgresql",
                }
        except Exception as exc:
            logger.warning(f"[DBCache] price lookup error for {ticker}: {exc}")

        self._miss("price", ticker)
        return None

    # ------------------------------------------------------------------
    # News  — freshness window: 15 minutes
    # ------------------------------------------------------------------

    def get_fresh_news(self, ticker: str) -> Optional[List[Dict]]:
        """
        Returns a list of news dicts if the most recently published article
        is within the last 15 minutes. Returns None on miss.

        Freshness field: max(News.published_at) across all returned rows.
        """
        try:
            items = self._db.get_latest_news(ticker)
            if items:
                latest_dt = max(
                    (n.published_at for n in items if n.published_at),
                    default=None,
                )
                if _is_fresh(latest_dt, "news"):
                    self._hit("news", ticker)
                    return [
                        {
                            "headline":      n.headline,
                            "url":           n.url,
                            "source":        n.source,
                            "published_at": (
                                str(n.published_at) if n.published_at else None
                            ),
                            "_cache_source": "postgresql",
                        }
                        for n in items
                    ]
        except Exception as exc:
            logger.warning(f"[DBCache] news lookup error for {ticker}: {exc}")

        self._miss("news", ticker)
        return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        """Return hit/miss counters and rates for the diagnostics endpoint."""
        total = self._hits + self._misses
        return {
            "hits":          self._hits,
            "misses":        self._misses,
            "total":         total,
            "hit_rate_pct":  round(self._hits  / total * 100, 2) if total else 0.0,
            "miss_rate_pct": round(self._misses / total * 100, 2) if total else 0.0,
        }
