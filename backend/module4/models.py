"""
Common Internal Models

Every provider must return these models.

Never expose provider-specific formats internally.

"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class CompanyProfile:

    company_id: str

    ticker: str

    company_name: str

    exchange: str

    sector: Optional[str]

    industry: Optional[str]

    isin: Optional[str]

    market_cap: Optional[float]

    updated_at: datetime


@dataclass
class FinancialStatement:

    company_id: str

    financial_year: str

    statement_type: str

    metric_name: str

    metric_value: float

    reporting_source: str

    extracted_at: datetime

    is_latest: bool = True

    confidence_score: float = 1.0


@dataclass
class MarketPrice:

    company_id: str

    price: float

    volume: float

    timestamp: datetime


@dataclass
class NewsItem:

    company_id: str

    headline: str

    url: str

    source: str

    published_at: datetime
