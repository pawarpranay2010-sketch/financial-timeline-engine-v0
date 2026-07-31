"""
Source Resolver — Deterministic source-resolution engine.

NEVER lets the LLM decide which conflicting financial fact is canonical.

Source Hierarchy (Tier 3 = highest authority):
    Tier 3: Authoritative filings / official sources
        - SEC filings (10-K, 10-Q, 8-K, etc.)
        - NSE/BSE official disclosures
        - SEBI orders and circulars
        - Official statistical sources (RBI, Bureau of Economic Analysis, etc.)

    Tier 2: Verified structured financial providers
        - Financial Modeling Prep (FMP)
        - Yahoo Finance (yfinance)
        - Finnhub
        - Alpha Vantage
        - Other structured API providers

    Tier 1: Public/speculative sources
        - News articles
        - Scraped web pages
        - Aggregator sites
        - Social media / forums

Filing Precedence:
    - 10-K/A supersedes 10-K for the same reporting period
    - 10-Q/A supersedes 10-Q for the same reporting period
    - Later amendments supersede earlier ones for affected facts
    - If precedence cannot be established safely → UNRESOLVED_CONFLICT
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from backend.database.models import Filing

logger = logging.getLogger("fte.rag.source_resolver")

# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

# Filing types that qualify as Tier 3 authoritative
_AUTHORITATIVE_FILING_TYPES = {
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
    "20-F", "20-F/A", "6-K", "6-K/A",
    "DEF 14A", "DEF 14A/A",
    "S-1", "S-1/A",
    # Indian market filings
    "NSE_ANNOUNCEMENT", "BSE_ANNOUNCEMENT",
    "SEBI_ORDER", "SEBI_CIRCULAR",
}

# Provider names that qualify as Tier 2 verified
_VERIFIED_PROVIDERS = {
    "fmp", "yfinance", "yahoo finance",
    "finnhub", "alpha_vantage", "alpha vantage",
    "intrinio", "polygon", "tiingo",
    "morningstar", "bloomberg", "reuters",
    "s&p", "moody's", "fitch",
}


def classify_tier(source: str, source_type: str = "", filing_type: str = "") -> int:
    """
    Determine the source tier for a given source string.

    Args:
        source: Provider name, document source, or filing source
        source_type: "filing", "provider", "news", or "document"
        filing_type: SEC filing type (10-K, 10-Q, etc.)

    Returns:
        3 = authoritative, 2 = verified, 1 = public/speculative
    """
    source_lower = (source or "").strip().lower()

    # Tier 3: Authoritative filings and official sources
    if filing_type and filing_type.upper() in _AUTHORITATIVE_FILING_TYPES:
        return 3
    if source_type == "filing":
        return 3
    if any(official in source_lower for official in
           ["sec", "nse", "bse", "sebi", "rbi", "federal reserve",
            "bureau of", "treasury", "central bank", "regulatory",
            "exchange commission"]):
        return 3

    # Tier 2: Verified structured providers
    if source_lower in _VERIFIED_PROVIDERS:
        return 2
    if source_type == "provider":
        return 2

    # Tier 1: Everything else
    return 1


# ---------------------------------------------------------------------------
# Filing Precedence
# ---------------------------------------------------------------------------

# Maps amendment filing types to their base type
_AMENDMENT_MAP = {
    "10-K/A": ("10-K", True),
    "10-Q/A": ("10-Q", True),
    "8-K/A": ("8-K", True),
    "20-F/A": ("20-F", True),
    "6-K/A": ("6-K", True),
    "DEF 14A/A": ("DEF 14A", True),
    "S-1/A": ("S-1", True),
}

# Filing types ordered by precedence (higher index = more authoritative for same period)
_FILING_TYPE_PRECEDENCE = {
    "10-K": 10,
    "10-K/A": 15,  # Amendment supersedes original
    "10-Q": 5,
    "10-Q/A": 8,   # Amendment supersedes original
    "8-K": 3,
    "8-K/A": 7,
    "20-F": 10,
    "20-F/A": 15,
    "6-K": 3,
    "6-K/A": 7,
}


def check_filing_precedence(filing_a: Filing, filing_b: Filing) -> Optional[int]:
    """
    Determine which filing takes precedence for the same reporting period.

    Args:
        filing_a: First filing record
        filing_b: Second filing record

    Returns:
        1 if filing_a supersedes filing_b
        -1 if filing_b supersedes filing_a
        None if precedence cannot be established (unresolved conflict)
    """
    # Same filing — equal
    if filing_a.id == filing_b.id:
        return 0

    # Check if one is an amendment of the other
    a_type = filing_a.filing_type or ""
    b_type = filing_b.filing_type or ""

    a_base, a_is_amendment = _AMENDMENT_MAP.get(a_type.upper(), (a_type, False))
    b_base, b_is_amendment = _AMENDMENT_MAP.get(b_type.upper(), (b_type, False))

    # Direct amendment relationship
    if a_is_amendment and a_base == b_type.upper():
        # filing_a is amendment of filing_b's type
        if _same_period(filing_a, filing_b):
            return 1  # amendment supersedes original
    if b_is_amendment and b_base == a_type.upper():
        if _same_period(filing_a, filing_b):
            return -1  # amendment supersedes original

    # Compare by filing type precedence
    a_prec = _FILING_TYPE_PRECEDENCE.get(a_type.upper(), 0)
    b_prec = _FILING_TYPE_PRECEDENCE.get(b_type.upper(), 0)

    if a_prec > b_prec and _same_period(filing_a, filing_b):
        return 1
    if b_prec > a_prec and _same_period(filing_a, filing_b):
        return -1

    # Compare by filing date (later filing for same period wins)
    if _same_period(filing_a, filing_b):
        a_date = filing_a.filing_date
        b_date = filing_b.filing_date
        if a_date and b_date:
            if a_date > b_date:
                return 1
            if b_date > a_date:
                return -1

    # Cannot determine precedence
    return None


def _same_period(filing_a: Filing, filing_b: Filing) -> bool:
    """Check if two filings relate to the same fiscal period."""
    a_period = filing_a.fiscal_period or ""
    b_period = filing_b.fiscal_period or ""
    if a_period and b_period and a_period == b_period:
        return True
    a_year = filing_a.fiscal_year
    b_year = filing_b.fiscal_year
    if a_year and b_year and a_year == b_year:
        return True
    return False


# ---------------------------------------------------------------------------
# Source Resolver
# ---------------------------------------------------------------------------


class SourceResolver:
    """
    Deterministic source-resolution engine.

    Resolves conflicting evidence by applying the tier hierarchy and
    filing precedence rules. Never uses an LLM to decide which fact
    is canonical.
    """

    @staticmethod
    def resolve_filing_precedence(filing_a: Filing, filing_b: Filing) -> Optional[int]:
        """Delegate to standalone function."""
        return check_filing_precedence(filing_a, filing_b)

    @staticmethod
    def get_filing_type_tier(filing_type: str) -> int:
        """Get the tier for a filing type string."""
        return classify_tier(
            source="",
            source_type="filing",
            filing_type=filing_type,
        )

    @staticmethod
    def is_amendment(filing_type: str) -> bool:
        """Check if a filing type is an amendment."""
        return filing_type.upper() in _AMENDMENT_MAP

    @staticmethod
    def get_base_filing_type(filing_type: str) -> str:
        """Get the base filing type (e.g., '10-K' from '10-K/A')."""
        upper = filing_type.upper()
        if upper in _AMENDMENT_MAP:
            return _AMENDMENT_MAP[upper][0]
        return upper

    def resolve_conflict(
        self,
        evidence_items: List[Dict],
    ) -> Tuple[str, Optional[Dict]]:
        """
        Resolve conflicting evidence items deterministically.

        Args:
            evidence_items: List of evidence dicts, each with at minimum:
                - source_tier: int
                - source: str
                - value: Optional[float]
                - filing_type: str (optional)
                - filing_date: str (optional)

        Returns:
            Tuple of (resolution_status, resolved_item_or_None)
            resolution_status: "RESOLVED", "UNRESOLVED_CONFLICT", or "INSUFFICIENT"
        """
        if not evidence_items:
            return ("INSUFFICIENT", None)

        if len(evidence_items) == 1:
            return ("RESOLVED", evidence_items[0])

        # Sort by tier (highest first), then by confidence (highest first)
        sorted_items = sorted(
            evidence_items,
            key=lambda x: (
                -x.get("source_tier", 1),
                -x.get("confidence", 0.0),
            ),
        )

        # Check if highest-tier item clearly dominates
        highest_tier = sorted_items[0].get("source_tier", 1)

        # Find all items at the highest tier
        tier_items = [it for it in sorted_items if it.get("source_tier", 1) == highest_tier]

        if len(tier_items) == 1:
            # Single highest-tier item — it wins
            return ("RESOLVED", tier_items[0])

        # Multiple items at the same highest tier — check for agreement
        tier_values = set()
        for it in tier_items:
            val = it.get("value")
            if val is not None:
                tier_values.add(val)

        if len(tier_values) <= 1:
            # All agree on the same value
            winner = max(tier_items, key=lambda x: x.get("confidence", 0.0))
            return ("RESOLVED", winner)

        # Same tier, different values — check filing precedence
        resolved_filing = self._resolve_by_filing_precedence(tier_items)
        if resolved_filing:
            return ("RESOLVED", resolved_filing)

        # Cannot resolve — return conflict
        logger.warning(
            f"[SourceResolver] Unresolved conflict: {len(tier_items)} items "
            f"at tier {highest_tier} with different values"
        )
        return ("UNRESOLVED_CONFLICT", None)

    def _resolve_by_filing_precedence(
        self,
        items: List[Dict],
    ) -> Optional[Dict]:
        """Try to resolve by filing precedence rules."""
        # Check if we have filing type information
        filings_info = []
        for item in items:
            f_type = item.get("filing_type", "")
            f_date = item.get("filing_date", "")
            if f_type:
                filings_info.append((item, f_type, f_date))

        if len(filings_info) < 2:
            return None

        # Check for amendment relationships
        for i, (item_a, type_a, date_a) in enumerate(filings_info):
            a_base, a_is_amendment = _AMENDMENT_MAP.get(type_a.upper(), (type_a, False))
            for j, (item_b, type_b, date_b) in enumerate(filings_info):
                if i >= j:
                    continue
                b_base, b_is_amendment = _AMENDMENT_MAP.get(type_b.upper(), (type_b, False))

                if a_is_amendment and a_base == type_b.upper():
                    return item_a  # Amendment wins
                if b_is_amendment and b_base == type_a.upper():
                    return item_b  # Amendment wins

        return None

    def is_authoritative(
        self,
        source: str,
        source_type: str = "",
        filing_type: str = "",
    ) -> bool:
        """Check if a source is Tier 3 authoritative."""
        return classify_tier(source, source_type, filing_type) == 3
