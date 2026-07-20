"""
Module 4 - Normalization Engine

Purpose:
Convert every provider's output into ONE canonical format.

Provider Data
        ↓
Normalizer
        ↓
Canonical Schema
        ↓
Database

The normalizer is deterministic.

It NEVER calls an LLM.

Unknown metrics are flagged for review.
"""

from typing import Dict, Any


class MetricDictionary:
    """
    Canonical financial metric mapping.

    Expand this dictionary over time.
    """

    METRIC_ALIASES = {

        # Revenue
        "revenue": "Revenue",
        "total revenue": "Revenue",
        "revenue from operations": "Revenue",
        "net revenue": "Revenue",

        # PAT
        "pat": "PAT",
        "profit after tax": "PAT",
        "net profit": "PAT",
        "profit for the year": "PAT",

        # EBITDA
        "ebitda": "EBITDA",
        "operating profit": "EBITDA",

        # EBIT
        "ebit": "EBIT",

        # EPS
        "eps": "EPS",
        "earnings per share": "EPS",

        # Assets
        "assets under management": "AUM",
        "aum": "AUM",
        "total managed assets": "AUM",

        # Debt
        "total debt": "Debt",
        "borrowings": "Debt",

        # Cash Flow
        "cash flow": "CashFlow",
        "operating cash flow": "OperatingCashFlow",
    }

    @classmethod
    def resolve(cls, raw_metric: str):

        if raw_metric is None:
            return None

        key = raw_metric.strip().lower()

        return cls.METRIC_ALIASES.get(key)


class Normalizer:

    def normalize_company(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        return {

            "company_id": raw.get("company_id"),

            "ticker": raw.get("ticker"),

            "company_name": raw.get("company_name"),

            "exchange": raw.get("exchange"),

            "sector": raw.get("sector"),

            "industry": raw.get("industry"),

            "isin": raw.get("isin"),

            "market_cap": raw.get("market_cap"),

            "updated_at": raw.get("updated_at")

        }

    def normalize_financial(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        metric = MetricDictionary.resolve(
            raw.get("metric_name")
        )

        unknown_metric = False

        if metric is None:

            metric = raw.get("metric_name")

            unknown_metric = True

        return {

            "company_id": raw.get("company_id"),

            "financial_year": raw.get("financial_year"),

            "statement_type": raw.get("statement_type"),

            "metric_name": metric,

            "metric_value": raw.get("metric_value"),

            "currency": raw.get("currency"),

            "source_provider": raw.get("source_provider"),

            "source_document": raw.get("source_document"),

            "is_latest": True,

            "version": 1,

            "unknown_metric": unknown_metric

        }

    def normalize_price(self, raw: Dict[str, Any]):

        return {

            "company_id": raw.get("company_id"),

            "price": raw.get("price"),

            "volume": raw.get("volume"),

            "timestamp": raw.get("timestamp")

        }

    def normalize_news(self, raw: Dict[str, Any]):

        return {

            "company_id": raw.get("company_id"),

            "headline": raw.get("headline"),

            "url": raw.get("url"),

            "source": raw.get("source"),

            "published_at": raw.get("published_at")

        }


normalizer = Normalizer()
