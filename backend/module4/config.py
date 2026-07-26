"""
Module 4 Configuration

Central configuration for the Financial Intelligence Database.

No API keys should ever be hardcoded here.
Everything should be loaded from environment variables.

Future providers:
- NSE
- BSE
- SEBI
- SEC
- FinancialModelingPrep
- Finnhub
- Polygon
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Module4Settings:
    DATABASE_URL: str
    REDIS_URL: str
    CACHE_DEFAULT_TTL: int
    PRICE_REFRESH_SECONDS: int
    NEWS_REFRESH_MINUTES: int
    FILINGS_REFRESH_MINUTES: int
    FINANCIALS_REFRESH_HOURS: int
    COMPANY_METADATA_REFRESH_DAYS: int

    @classmethod
    def from_env(cls) -> "Module4Settings":
        return cls(
            DATABASE_URL=os.getenv(
                "DATABASE_URL",
                "postgresql://user:password@localhost:5432/finance",
            ),
            REDIS_URL=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            CACHE_DEFAULT_TTL=_get_int("CACHE_DEFAULT_TTL", 3600),
            PRICE_REFRESH_SECONDS=_get_int("PRICE_REFRESH_SECONDS", 1),
            NEWS_REFRESH_MINUTES=_get_int("NEWS_REFRESH_MINUTES", 5),
            FILINGS_REFRESH_MINUTES=_get_int("FILINGS_REFRESH_MINUTES", 30),
            FINANCIALS_REFRESH_HOURS=_get_int("FINANCIALS_REFRESH_HOURS", 24),
            COMPANY_METADATA_REFRESH_DAYS=_get_int(
                "COMPANY_METADATA_REFRESH_DAYS",
                7,
            ),
        )


settings = Module4Settings.from_env()

# Keep the original module-level names so existing imports continue to work.
DATABASE_URL = settings.DATABASE_URL
REDIS_URL = settings.REDIS_URL
CACHE_DEFAULT_TTL = settings.CACHE_DEFAULT_TTL
PRICE_REFRESH_SECONDS = settings.PRICE_REFRESH_SECONDS
NEWS_REFRESH_MINUTES = settings.NEWS_REFRESH_MINUTES
FILINGS_REFRESH_MINUTES = settings.FILINGS_REFRESH_MINUTES
FINANCIALS_REFRESH_HOURS = settings.FINANCIALS_REFRESH_HOURS
COMPANY_METADATA_REFRESH_DAYS = settings.COMPANY_METADATA_REFRESH_DAYS
