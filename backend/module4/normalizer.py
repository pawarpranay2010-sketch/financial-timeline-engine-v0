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
    Canonical financial metric mapping with semantic identity.

    Expand this dictionary over time.

    Now supports:
    - Basic alias resolution (backward compatible)
    - Semantic definition preservation (GAAP vs non-GAAP vs adjusted)
    - Metric definition qualifiers for semantic identity comparisons
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

    # ------------------------------------------------------------------
    # Semantic Definition Qualifiers
    # ------------------------------------------------------------------
    #
    # These qualifiers are DETECTED in the raw metric name and preserved
    # alongside the canonical name so the system can distinguish
    # semantically different definitions of the same metric.
    #
    # Example:
    #   "GAAP Revenue"       → canonical="Revenue", definition="GAAP"
    #   "non-GAAP Revenue"   → canonical="Revenue", definition="non-GAAP"
    #   "Gross Revenue"      → canonical="Revenue", definition="gross"
    #   "Net Revenue"        → canonical="Revenue", definition="net"
    #   "Adjusted Revenue"   → canonical="Revenue", definition="adjusted"

    DEFINITION_QUALIFIERS = {
        "gaap": "GAAP",
        "non-gaap": "non-GAAP",
        "adjusted": "adjusted",
        "unadjusted": "unadjusted",
        "gross": "gross",
        "net": "net",
        "operating": "operating",
        "pro forma": "pro_forma",
        "normalized": "normalized",
        "underlying": "underlying",
        "recurring": "recurring",
        "core": "core",
        "headline": "headline",
        "reported": "reported",
        "statutory": "statutory",
        "ifrs": "IFRS",
        "us gaap": "GAAP",
        "ind as": "Ind_AS",
    }

    @classmethod
    def resolve(cls, raw_metric: str):

        if raw_metric is None:
            return None

        key = raw_metric.strip().lower()

        return cls.METRIC_ALIASES.get(key)

    @classmethod
    def resolve_with_definition(cls, raw_metric: str) -> tuple:
        """
        Resolve a metric name to (canonical_name, definition_qualifier).

        Recursively strips ALL detected definition qualifiers from the
        metric name, then resolves the remaining base metric name.

        Returns:
            tuple of (canonical_name: str, definition: str)
            definition is empty string if no qualifier detected.

        Examples:
            "GAAP Revenue"                → ("Revenue", "GAAP")
            "non-GAAP Revenue"            → ("Revenue", "non-GAAP")
            "non-GAAP Adjusted Revenue"   → ("Revenue", "non-GAAP")
            "Adjusted EBITDA"             → ("EBITDA", "adjusted")
            "Revenue"                     → ("Revenue", "")
        """
        if raw_metric is None:
            return (None, "")

        # Sort qualifiers by length (longest first) for greedy matching
        sorted_keywords = sorted(
            cls.DEFINITION_QUALIFIERS.items(),
            key=lambda x: -len(x[0]),
        )

        found_definition = ""
        stripped = raw_metric.strip()

        # Loop: keep stripping qualifiers until no more are found
        # This handles cases like "non-GAAP Adjusted Revenue" where
        # both "non-GAAP" and "Adjusted" are qualifiers.
        changed = True
        while changed:
            changed = False
            current_lower = stripped.lower()
            for keyword, def_name in sorted_keywords:
                if keyword in current_lower:
                    if not found_definition:
                        found_definition = def_name
                    # Strip this qualifier from the metric name
                    start = current_lower.find(keyword)
                    end = start + len(keyword)
                    stripped = stripped[:start] + stripped[end:]
                    stripped = stripped.strip().strip("-").strip()
                    changed = True
                    break  # restart loop to check for remaining qualifiers

        # Resolve the fully stripped metric name
        canonical = cls.resolve(stripped) or stripped
        return (canonical, found_definition)

    @classmethod
    def definitions_match(cls, def_a: str, def_b: str) -> bool:
        """
        Check if two metric definitions are semantically compatible.

        Returns True if:
        - Both are empty (no definition detected)
        - Both are the same definition
        - One is empty and the other is a common default (e.g., 'reported')

        Returns False if they are explicitly different definitions
        (e.g., 'GAAP' vs 'non-GAAP').
        """
        a = def_a.lower().strip() if def_a else ""
        b = def_b.lower().strip() if def_b else ""

        if not a and not b:
            return True
        if not a or not b:
            # One is empty, other is not — assume compatible unless
            # the explicit one is a specific accounting basis
            explicit = a or b
            if explicit in ("gaap", "ifrs", "ind_as", "statutory"):
                return False
            return True
        return a == b


class Normalizer:

    def normalize_company(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        return {

            "company_id": raw.get("company_id"),

            "ticker": raw.get("ticker") or raw.get("symbol"),

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

            "headline": raw.get("headline") or raw.get("title"),

            "url": raw.get("url") or raw.get("link"),

            "source": raw.get("source") or raw.get("site") or raw.get("publisher"),

            "published_at": raw.get("published_at") or raw.get("published_date")

        }


normalizer = Normalizer()
