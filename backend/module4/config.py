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

from pydantic import BaseSettings


class Module4Settings(BaseSettings):

    DATABASE_URL: str = "postgresql://user:password@localhost:5432/finance"

    REDIS_URL: str = "redis://localhost:6379/0"

    CACHE_DEFAULT_TTL = 3600

    PRICE_REFRESH_SECONDS = 1

    NEWS_REFRESH_MINUTES = 5

    FILINGS_REFRESH_MINUTES = 30

    FINANCIALS_REFRESH_HOURS = 24

    COMPANY_METADATA_REFRESH_DAYS = 7

    class Config:
        env_file = ".env"


settings = Module4Settings()
