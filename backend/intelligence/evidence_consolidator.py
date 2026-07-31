"""
EvidenceConsolidator

Merges all evidence sources into a structured context for the AI memo generator.

Sources:
  1. Document master summary (from uploaded financial documents)
  2. Module 3 financial intelligence (deterministic extraction from docs)
  3. Module 4 live company profile (from DataAgent/RetrievalAgent)
  4. Module 4 financial statements (income, balance sheet, cash flow)
  5. Module 4 market price (current price snapshot)
  6. Module 4 news articles (recent company news)
  7. Module 4 regulatory filings (if available)

The consolidator marks each source so the AI can cite it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fte.intelligence.evidence_consolidator")


class EvidenceConsolidator:
    """
    Combines document analysis and live market intelligence into one
    structured evidence context for AI memo generation.

    Usage:
        consolidator = EvidenceConsolidator(ticker="AAPL")
        context = consolidator.consolidate(
            master_summary=master_summary,
            module3_result=module3_result,
            module4_data=data_agent_result,
            stored_data=retrieval_result,
        )
    """

    def __init__(self, ticker: str):
        self.ticker = ticker.strip().upper()
        self._sources = []

    # ------------------------------------------------------------------
    # Section builders — each returns a formatted text block with source tag
    # ------------------------------------------------------------------

    def _build_document_section(self, master_summary: Optional[str]) -> str:
        """Section 1: Extracted document summary with source citations."""
        if not master_summary or not master_summary.strip():
            self._sources.append("documents: NONE")
            return (
                "\n[SOURCE: Uploaded Documents]\n"
                "No financial documents were uploaded for this analysis.\n"
            )

        self._sources.append("documents: UPLOADED")
        lines = master_summary.strip().split("\n")
        # Truncate if too long for the prompt (keep ~4000 chars)
        truncated = "\n".join(lines[:200])
        if len(truncated) > 4000:
            truncated = truncated[:4000] + "\n[...truncated...]"

        return (
            "\n[SOURCE: Uploaded Financial Documents]\n"
            f"The following was extracted from the user's uploaded financial documents:\n"
            f"{truncated}\n"
            "[END DOCUMENT SOURCE]\n"
        )

    def _build_module3_section(
        self, module3_result: Optional[Dict[str, Any]]
    ) -> str:
        """Section 2: Module 3 deterministic financial intelligence."""
        if not module3_result:
            self._sources.append("module3: NONE")
            return (
                "\n[SOURCE: Module 3 — Financial Intelligence]\n"
                "No Module 3 analysis was available.\n"
                "[END MODULE3 SOURCE]\n"
            )

        self._sources.append("module3: AVAILABLE")

        # Extract key subsections
        financial_data = module3_result.get("financial_data", {})
        ratios = module3_result.get("ratios", {})
        events = module3_result.get("events", [])
        confidence = module3_result.get("confidence", {})
        verified = module3_result.get("cross_document_verification", {})

        parts = [
            "\n[SOURCE: Module 3 — Deterministic Financial Intelligence]\n"
            "The following financial metrics were extracted deterministically "
            "from the uploaded documents (these are NOT AI-generated):"
        ]

        if financial_data:
            parts.append(
                "\n--- Extracted Financial Data ---\n"
                + json.dumps(financial_data, indent=2, default=str)
            )
        if ratios:
            parts.append(
                "\n--- Calculated Ratios ---\n"
                + json.dumps(ratios, indent=2, default=str)
            )
        if verified:
            parts.append(
                "\n--- Cross-Document Verification ---\n"
                + json.dumps(verified, indent=2, default=str)
            )
        if confidence:
            parts.append(
                "\n--- Confidence Scores ---\n"
                + json.dumps(confidence, indent=2, default=str)
            )
        if events:
            parts.append(
                "\n--- Key Events ---\n"
                + json.dumps(events[:20], indent=2, default=str)
            )

        parts.append("[END MODULE3 SOURCE]\n")
        return "\n".join(parts)

    def _build_company_profile_section(
        self, profile_data: Optional[Dict[str, Any]]
    ) -> str:
        """Section 3: Company profile from live market data."""
        if not profile_data or not profile_data.get("success"):
            self._sources.append("profile: UNAVAILABLE")
            return (
                "\n[SOURCE: Live Market Data — Company Profile]\n"
                "Live company profile was not available (all providers exhausted).\n"
                "[END PROFILE SOURCE]\n"
            )

        self._sources.append("profile: AVAILABLE")
        p = profile_data.get("data", {})

        fields = {
            "Company Name": p.get("company_name"),
            "Symbol": p.get("symbol") or p.get("ticker"),
            "Exchange": p.get("exchange"),
            "Sector": p.get("sector"),
            "Industry": p.get("industry"),
            "Market Capitalization": p.get("mkt_cap") or p.get("market_cap"),
            "Description": (
                (p.get("description") or "")[:300]
                if p.get("description")
                else None
            ),
            "Currency": p.get("currency"),
        }

        parts = [
            "\n[SOURCE: Live Market Data — Company Profile (Yahoo Finance)]\n"
            "The following company information was retrieved from live "
            "market data sources:"
        ]
        for label, value in fields.items():
            if value is not None:
                parts.append(f"  • {label}: {value}")

        parts.append("[END PROFILE SOURCE]\n")
        return "\n".join(parts)

    def _build_financials_section(
        self, financials_data: Optional[Dict[str, Any]]
    ) -> str:
        """Section 4: Live financial statements."""
        if not financials_data or not financials_data.get("success"):
            self._sources.append("financials: UNAVAILABLE")
            return (
                "\n[SOURCE: Live Market Data — Financial Statements]\n"
                "Live financial statements were not available.\n"
                "[END FINANCIALS SOURCE]\n"
            )

        self._sources.append("financials: AVAILABLE")
        f_data = financials_data.get("data", {})

        # Handle both live provider format (dict) and cached format (list)
        if isinstance(f_data, list):
            parts = [
                "\n[SOURCE: Live Market Data — Financial Statements (PostgreSQL Cache)]\n"
                "The following financial statements were retrieved from the database cache:\n"
            ]
            if f_data:
                for i, item in enumerate(f_data[:20], 1):
                    parts.append(f"  Record {i}: {json.dumps(item, indent=4, default=str)}")
            else:
                parts.append("  No cached financial records available.")
            parts.append("[END FINANCIALS SOURCE]\n")
            return "\n".join(parts)

        income = f_data.get("income_statement", [])
        balance = f_data.get("balance_sheet", [])
        cash_flow = f_data.get("cash_flow", [])

        parts = [
            "\n[SOURCE: Live Market Data — Financial Statements (Yahoo Finance)]\n"
            "The following financial statement data was retrieved from live sources:"
        ]

        if income:
            parts.append(
                "\n--- Income Statement (last 5 periods) ---\n"
                + json.dumps(income, indent=2, default=str)
            )
        if balance:
            parts.append(
                "\n--- Balance Sheet (last 5 periods) ---\n"
                + json.dumps(balance, indent=2, default=str)
            )
        if cash_flow:
            parts.append(
                "\n--- Cash Flow Statement (last 5 periods) ---\n"
                + json.dumps(cash_flow, indent=2, default=str)
            )

        if not income and not balance and not cash_flow:
            parts.append("  No financial statement data was returned.")

        parts.append("[END FINANCIALS SOURCE]\n")
        return "\n".join(parts)

    def _build_market_price_section(
        self, price_data: Optional[Dict[str, Any]]
    ) -> str:
        """Section 5: Current market price snapshot."""
        if not price_data or not price_data.get("success"):
            self._sources.append("market_price: UNAVAILABLE")
            return (
                "\n[SOURCE: Live Market Data — Market Price]\n"
                "Current market price was not available.\n"
                "[END PRICE SOURCE]\n"
            )

        self._sources.append("market_price: AVAILABLE")
        p = price_data.get("data", {})

        fields = [
            ("Current Price", p.get("price")),
            ("Day Open", p.get("open") or p.get("day_open")),
            ("Day High", p.get("day_high")),
            ("Day Low", p.get("day_low")),
            ("Volume", f"{p.get('volume'):,}" if p.get("volume") else None),
            ("Market Cap", p.get("market_cap")),
            ("Change (%)", p.get("change_pct")),
        ]

        parts = [
            "\n[SOURCE: Live Market Data — Market Price (Yahoo Finance)]\n"
            "Current market price snapshot:"
        ]
        for label, value in fields:
            if value is not None:
                parts.append(f"  • {label}: {value}")

        parts.append("\n[END PRICE SOURCE]\n")
        return "\n".join(parts)

    def _build_news_section(
        self, news_data: Optional[Dict[str, Any]]
    ) -> str:
        """Section 6: Recent news articles."""
        if not news_data or not news_data.get("success"):
            self._sources.append("news: UNAVAILABLE")
            return (
                "\n[SOURCE: Live Market Data — Recent News]\n"
                "Recent company news was not available.\n"
                "[END NEWS SOURCE]\n"
            )

        self._sources.append("news: AVAILABLE")
        articles = news_data.get("data", [])

        parts = [
            "\n[SOURCE: Live Market Data — Recent News (Yahoo Finance)]\n"
            f"Recent news articles for {self.ticker}:"
        ]

        if not articles:
            parts.append("  No recent news articles found.")
        else:
            for i, article in enumerate(articles[:10], 1):
                title = article.get("title", "") or ""
                site = article.get("site", "") or ""
                text = article.get("text", "") or ""
                url = article.get("url", "") or ""

                parts.append(f"\n  Article {i}:")
                parts.append(f"    Title: {title[:200]}")
                if site:
                    parts.append(f"    Source: {site}")
                if text:
                    parts.append(f"    Snippet: {text[:300]}")
                if url:
                    parts.append(f"    URL: {url}")

        parts.append("\n[END NEWS SOURCE]\n")
        return "\n".join(parts)

    def _build_filings_section(
        self, filings_data: Optional[Dict[str, Any]]
    ) -> str:
        """Section 7: Regulatory filings (if available)."""
        if not filings_data or not filings_data.get("success"):
            self._sources.append("filings: UNAVAILABLE")
            return ""

        filings = filings_data.get("data", [])
        count = len(filings) if isinstance(filings, list) else 0
        if count == 0:
            self._sources.append("filings: NONE")
            return ""

        self._sources.append("filings: AVAILABLE")
        parts = [
            "\n[SOURCE: Live Market Data — Regulatory Filings]\n"
            f"Recent filings for {self.ticker}:"
        ]
        for i, filing in enumerate(filings[:5], 1):
            parts.append(
                f"  Filing {i}: {filing.get('filing_type', 'N/A')} — "
                f"{filing.get('filing_date', 'N/A')}"
            )
        parts.append("[END FILINGS SOURCE]\n")
        return "\n".join(parts)

    def _get_available_sources_summary(self) -> str:
        """Build a summary of which sources were available."""
        lines = ["\n--- Evidence Sources Summary ---"]
        for s in self._sources:
            status = "✅" if "AVAILABLE" in s or "UPLOADED" in s else (
                "⚠️" if "NONE" in s else "❌"
            )
            label = s.split(":")[0].strip().upper()
            lines.append(f"  {status} {label}")
        return "\n".join(lines)

    def _build_agentic_rag_section(
        self, canonical_set: Optional[Dict[str, Any]]
    ) -> str:
        """Section 8: Agentic RAG Canonical Evidence Set."""
        if not canonical_set:
            self._sources.append("agentic_rag: NONE")
            return ""

        self._sources.append("agentic_rag: AVAILABLE")

        terminal_state = canonical_set.get("terminal_state", "")
        resolved_facts = canonical_set.get("resolved_facts", [])
        evidence_count = canonical_set.get("evidence_count", 0)
        iterations_used = canonical_set.get("iterations_used", 0)

        parts = [
            "\n[SOURCE: Agentic RAG — Canonical Evidence Set]\n"
            f"Status: {terminal_state}\n"
            f"Iterations: {iterations_used}\n"
            f"Evidence Items: {evidence_count}\n"
            f"Resolved Facts: {len(resolved_facts)}\n"
        ]

        if resolved_facts:
            parts.append("\n--- Resolved Financial Facts ---")
            for fact in resolved_facts:
                parts.append(
                    f"  • {fact.get('metric', '')} = {fact.get('value', '')} "
                    f"{fact.get('unit', '')} "
                    f"[{fact.get('currency_code', '')}] "
                    f"[{fact.get('reporting_period', '')}] "
                    f"[{fact.get('accounting_basis', '')}] "
                    f"Source: {fact.get('source', '')} (tier {fact.get('source_tier', 1)})"
                )

        parts.append("[END AGENTIC RAG SOURCE]\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Main consolidation method
    # ------------------------------------------------------------------

    def consolidate(
        self,
        master_summary: Optional[str] = None,
        module3_result: Optional[Dict[str, Any]] = None,
        module4_data: Optional[Dict[str, Any]] = None,
        stored_data: Optional[Dict[str, Any]] = None,
        agentic_rag_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Merge all evidence sources into a structured context.

        Args:
            master_summary: Merged document summary from ingestion pipeline
            module3_result: Output of run_module3()
            module4_data: Result of DataAgent.fetch_all()
            stored_data: Result of RetrievalAgent.retrieve_all()

        Returns:
            Dict with:
              - "context_text": Full formatted text for AI prompt
              - "sources": List of available source names
              - "source_count": Number of available sources
              - "current_date": Today's date string
              - "ticker": Target company ticker
        """
        self._sources = []
        sections = []

        # Header
        sections.append(
            f"=== EVIDENCE PACKAGE FOR INVESTMENT MEMO ===\n"
            f"Company Ticker: {self.ticker}\n"
            f"Date Prepared: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"{'=' * 50}\n"
        )

        # Section 1: Document Summary
        if module4_data:
            # If we have Module 4 data, prefer its actual company name
            profile = module4_data.get("company_profile", {})
            if profile.get("success"):
                p = profile.get("data", {})
                name = (
                    p.get("company_name")
                    or p.get("longName")
                    or self.ticker
                )
                sections.insert(
                    0,
                    f"Company: {name} ({self.ticker})\n"
                    f"Date: {datetime.now().strftime('%Y-%m-%d')}\n\n",
                )

        sections.append(self._build_document_section(master_summary))

        # Section 2: Module 3
        sections.append(self._build_module3_section(module3_result))

        # Section 3: Company Profile (live)
        if module4_data:
            sections.append(
                self._build_company_profile_section(
                    module4_data.get("company_profile")
                )
            )

        # Section 4: Financial Statements (live)
        if module4_data:
            sections.append(
                self._build_financials_section(
                    module4_data.get("financials")
                )
            )

        # Section 5: Market Price
        if module4_data:
            sections.append(
                self._build_market_price_section(
                    module4_data.get("market_price")
                )
            )

        # Section 6: News
        if module4_data:
            sections.append(
                self._build_news_section(module4_data.get("news"))
            )

        # Section 7: Filings
        if module4_data:
            sections.append(
                self._build_filings_section(module4_data.get("filings"))
            )

        # Section 8: Agentic RAG Canonical Evidence Set
        if agentic_rag_result:
            sections.append(
                self._build_agentic_rag_section(agentic_rag_result)
            )

        # Supplement with stored data if live wasn't available
        if stored_data:
            if not module4_data or not module4_data.get("company_profile", {}).get("success"):
                cached = stored_data.get("cache_profile")
                if cached:
                    sections.append(
                        "\n[SOURCE: PostgreSQL Cache — Company Profile]\n"
                        "The following profile was retrieved from the database cache:\n"
                        + json.dumps(cached, indent=2, default=str)
                        + "\n[END CACHE SOURCE]\n"
                    )

        # Source summary
        sections.append(self._get_available_sources_summary())

        context_text = "\n".join(sections)

        logger.info(
            f"[EvidenceConsolidator] Consolidated {len(self._sources)} evidence "
            f"sources for {self.ticker} ({len(context_text)} chars)"
        )

        return {
            "context_text": context_text,
            "sources": list(self._sources),
            "source_count": len(self._sources),
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "ticker": self.ticker,
        }
