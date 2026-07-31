"""
MemoGenerator

Generates professional investment memos using consolidated evidence
from documents, Module 3 financial intelligence, and Module 4 live data.

The prompt instructs the AI to:
  - Cite every numerical claim to its source
  - NOT recalculate deterministic metrics (use the Python-calculated values)
  - Generate structured sections: Investment Score, Risk Analysis,
    Scenario Analysis, Peer Comparison, Source Citations, Action Checklist

Pipeline:
  EvidenceConsolidator.context_text
         ↓
  MemoGenerator.build_prompt()
         ↓
  call_ai_with_fallback()
         ↓
  Structured Investment Memo
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.constants import GROUNDING_RULE

logger = logging.getLogger("fte.intelligence.memo_generator")

# ---------------------------------------------------------------------------
# System prompt for the investment memo AI
# ---------------------------------------------------------------------------

MEMO_SYSTEM_PROMPT = """You are an elite institutional investment research analyst at a top-tier fund.

Your task is to write a professional, multi-section investment memo grounded
EXCLUSIVELY in the evidence provided below.

## CRITICAL RULES

1. **CITE EVERY CLAIM**: Every numerical claim must include its source in
   brackets, e.g., [Source: Live Market Data], [Source: Uploaded Documents],
   [Source: Module 3 Financial Intelligence]. If the evidence doesn't support
   a claim, do not make it.

2. **USE DETERMINISTIC METRICS AS-IS**: Financial ratios, extracted metrics,
   and calculated values from the "Module 3" and "Live Market Data" sections
   are already computed by the Python engine. Do NOT recalculate or round
   them — cite them verbatim with their source.

3. **NO GENERIC FILLER**: If the evidence does not contain information for a
   requested section, explicitly state "The provided evidence does not contain
   sufficient information for this section." Do not invent industry averages
   or generic commentary.

4. **GROUNDING RULE**: """ + GROUNDING_RULE + """

## REQUIRED MEMO SECTIONS

Generate the following sections in order:

### SECTION A: Executive Summary
A concise 2-3 paragraph overview covering the company, its sector,
investment thesis, and key findings from all evidence sources.

### SECTION B: Company Overview
Key facts about the company from the evidence: business description,
sector, industry, market position.

### SECTION C: Financial Analysis
Analysis of financial performance using the evidence provided.
Cover revenue trends, profitability, margins, balance sheet strength,
and cash flow. For every figure, cite [Source: Uploaded Documents]
or [Source: Live Market Data — Financial Statements].

### SECTION D: Financial Ratios (if available)
Key ratios from the Module 3 evidence. Use the exact values provided.

### SECTION E: Market Performance
Current market price, trading range, volume, market cap from the evidence.
[Source: Live Market Data — Market Price]

### SECTION F: Investment Score
Score the investment opportunity (1-100) based on the evidence provided.
Break down the score into: Financial Health (0-25), Market Position (0-25),
Growth Prospects (0-25), Risk Assessment (0-25). Explain each component
with source citations.

### SECTION G: Risk Analysis
Identify and analyze specific risks mentioned in the evidence:
- Financial risks from the financial statements
- Market risks from news and price data
- Operational risks from documents
- Regulatory or controversy flags
Rate each risk (Low / Medium / High / Critical) with source citations.

### SECTION H: Scenario Analysis
Based on the evidence, outline:
- Bull Case (key catalysts and supporting evidence)
- Base Case (most likely outcome with reasoning)
- Bear Case (key risks and downside triggers)

### SECTION I: Peer Comparison
If the evidence provides sector/industry context, note the company's
position. If no peer data is available, state that explicitly.

### SECTION J: Recent News & Developments
Summarize key recent news from [Source: Live Market Data — Recent News].
Highlight material events that could impact the investment thesis.

### SECTION K: Evidence & Source Citations
A numbered list of every source used in this memo:
1. Uploaded Financial Documents
2. Module 3 Financial Intelligence (deterministic extraction)
3. Live Market Data — Company Profile (Yahoo Finance)
4. Live Market Data — Financial Statements (Yahoo Finance)
5. Live Market Data — Market Price (Yahoo Finance)
6. Live Market Data — Recent News (Yahoo Finance)

For each source, note whether it was available or unavailable.

### SECTION L: Action Checklist
Based on the analysis, provide an actionable checklist:
- [ ] Items to monitor
- [ ] Due diligence steps
- [ ] Catalyst dates to watch
- [ ] Risk triggers to track

### SECTION M: Recommendation
Clear investment recommendation (Buy / Hold / Sell / Watch) with
supporting evidence and key conviction level (High / Medium / Low).

---

Generate the memo in professional institutional format.
Use markdown headings (##) for each section.
Be concise but thorough — quality over length.
"""


class MemoGenerator:
    """
    Generates an investment memo from consolidated evidence.

    Usage:
        generator = MemoGenerator()
        result = generator.generate(consolidated_evidence, call_ai_fn)
    """

    def __init__(self):
        self._ai_fn = None  # Set to call_ai_with_fallback or a mock

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def build_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build the user prompt from consolidated evidence context.

        Args:
            context: Output of EvidenceConsolidator.consolidate()

        Returns:
            Formatted prompt string for the AI
        """
        context_text = context.get("context_text", "")
        ticker = context.get("ticker", "UNKNOWN")
        current_date = context.get("current_date", "N/A")

        prompt = f"""Analyze the consolidated evidence package below for {ticker}
and generate a comprehensive professional investment memo following the
system prompt's required sections.

EVIDENCE PACKAGE:
{context_text}

---
Generate the investment memo below. Remember:
1. Cite every claim with [Source: ...]
2. Use the exact deterministic values — do not recalculate
3. If evidence is missing for a section, explicitly say so
4. Follow the required sections exactly
"""

        logger.info(
            f"[MemoGenerator] Built prompt for {ticker} "
            f"({len(prompt)} chars, {context.get('source_count', 0)} sources)"
        )
        return prompt

    # ------------------------------------------------------------------
    # AI call wrapper
    # ------------------------------------------------------------------

    def _call_ai(self, prompt: str, system_prompt: str = None) -> str:
        """
        Call the AI with fallback. Uses the injected AI function or
        the default call_ai_with_fallback from the app.
        """
        if self._ai_fn is not None:
            return self._ai_fn(prompt, system_prompt=system_prompt, temperature=0.3)

        # Default: try to import and use the app's fallback chain
        try:
            from app import call_ai_with_fallback as _app_ai_fn
            return _app_ai_fn(prompt, system_prompt=system_prompt, temperature=0.3)
        except ImportError:
            # Fallback for testing outside Streamlit
            try:
                # Try importing from the module directly
                import sys
                sys.path.insert(0, ".")
                # Re-import with the test harness path
                from tests.test_ai_harness import call_ai_test
                return call_ai_test(prompt, system_prompt=system_prompt)
            except ImportError:
                logger.error(
                    "[MemoGenerator] No AI provider available — "
                    "no API keys configured"
                )
                return (
                    "❌ AI memo generation skipped: No AI provider "
                    "API keys are configured. "
                    "Set GOOGLE_API_KEY, GROQ_API_KEY, or "
                    "OPENROUTER_API_KEY to enable AI generation."
                )

    # ------------------------------------------------------------------
    # Public generate method
    # ------------------------------------------------------------------

    def generate(
        self,
        context: Dict[str, Any],
        system_prompt: Optional[str] = None,
        call_ai_fn: Optional[callable] = None,
    ) -> Dict[str, Any]:
        """
        Generate the complete investment memo.

        Args:
            context: Output of EvidenceConsolidator.consolidate()
            system_prompt: Optional override for the system prompt
            call_ai_fn: Optional callable to use instead of default AI

        Returns:
            Dict with:
              - "success": bool
              - "memo_text": str (the generated memo or error message)
              - "prompt": str (the prompt that was sent)
              - "source_count": int
              - "ticker": str
              - "error": str (if failed)
        """
        self._ai_fn = call_ai_fn
        ticker = context.get("ticker", "UNKNOWN")
        source_count = context.get("source_count", 0)

        prompt = self.build_prompt(context)
        sys_prompt = system_prompt or MEMO_SYSTEM_PROMPT

        logger.info(
            f"[MemoGenerator] Generating memo for {ticker} "
            f"({source_count} sources available)"
        )

        try:
            memo_text = self._call_ai(prompt, system_prompt=sys_prompt)

            is_error = (
                not memo_text
                or "❌" in memo_text
                or "🔴" in memo_text
            )

            if is_error:
                logger.warning(
                    f"[MemoGenerator] AI generated an error response: "
                    f"{memo_text[:100]}"
                )
                return {
                    "success": False,
                    "memo_text": memo_text,
                    "prompt": prompt,
                    "source_count": source_count,
                    "ticker": ticker,
                    "error": memo_text,
                }

            logger.info(
                f"[MemoGenerator] Memo generated successfully "
                f"({len(memo_text)} chars)"
            )
            return {
                "success": True,
                "memo_text": memo_text,
                "prompt": prompt,
                "source_count": source_count,
                "ticker": ticker,
                "error": None,
            }

        except Exception as e:
            logger.error(f"[MemoGenerator] Generation failed: {e}")
            return {
                "success": False,
                "memo_text": None,
                "prompt": prompt,
                "source_count": source_count,
                "ticker": ticker,
                "error": str(e),
            }
