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

            "ticker": raw.get("ticker", raw.get("symbol")),

            "company_name": raw.get("company_name", raw.get("companyName")),

            "exchange": raw.get("exchange"),

            "sector": raw.get("sector"),

            "industry": raw.get("industry"),

            "isin": raw.get("isin"),

            "market_cap": raw.get("market_cap", raw.get("mkt_cap")),

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

    def normalize_financials(self, raw: Dict[str, Any]):
        normalized = []

        statement_type_map = {
            "income_statement": "income_statement",
            "balance_sheet": "balance_sheet",
            "cash_flow": "cash_flow",
        }

        for statement_key, statement_type in statement_type_map.items():
            for item in raw.get(statement_key, []) or []:
                period = item.get("calendarYear") or item.get("date")
                financial_year = None

                if isinstance(period, str) and period:
                    financial_year = period.split("-")[0]

                metric_fields = {
                    "revenue": item.get("revenue"),
                    "ebitda": item.get("ebitda"),
                    "ebit": item.get("operatingIncome"),
                    "net income": item.get("netIncome"),
                    "eps": item.get("eps"),
                    "total assets": item.get("totalAssets"),
                    "total liabilities": item.get("totalLiabilities"),
                    "operating cash flow": item.get("operatingCashFlow"),
                    "free cash flow": item.get("freeCashFlow"),
                }

                for metric_name, metric_value in metric_fields.items():
                    if metric_value is None:
                        continue
                    normalized.append(
                        self.normalize_financial(
                            {
                                "company_id": raw.get("company_id"),
                                "financial_year": financial_year,
                                "statement_type": statement_type,
                                "metric_name": metric_name,
                                "metric_value": metric_value,
                                "currency": item.get("reportedCurrency"),
                                "source_provider": raw.get("source_provider", "fmp"),
                                "source_document": item.get("link"),
                                "is_latest": item.get("is_latest", True),
                            }
                        )
                    )

        return normalized

    def normalize_price(self, raw: Dict[str, Any]):

        return {

            "company_id": raw.get("company_id"),

            "price": raw.get("price", raw.get("close_price")),

            "volume": raw.get("volume"),

            "timestamp": raw.get("timestamp"),

            "high_price": raw.get("high_price", raw.get("day_high")),

            "low_price": raw.get("low_price", raw.get("day_low")),

            "open_price": raw.get("open_price"),

            "close_price": raw.get("close_price", raw.get("price"))

        }

    def normalize_news(self, raw):
        if isinstance(raw, list):
            return [self.normalize_news(item) for item in raw]

        return {

            "company_id": raw.get("company_id"),

            "headline": raw.get("headline", raw.get("title")),

            "url": raw.get("url"),

            "source": raw.get("source"),

            "published_at": raw.get("published_at", raw.get("published_date")),

            "summary": raw.get("summary", raw.get("text"))

        }


normalizer = Normalizer()
