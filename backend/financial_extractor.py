"""
Financial Timeline Engine
Module 3

Financial Extractor

Purpose:
---------
Extract structured financial facts from
documents before sending anything to the LLM.

The extractor NEVER performs calculations.

It only identifies financial values.
"""

from __future__ import annotations

import re
from typing import Dict, Any


class FinancialExtractor:

    def __init__(self):

        self.patterns = {

            "Revenue": r"(Revenue|Sales|Total Revenue)\D+([\d,\.]+)",

            "Net Profit": r"(Net Profit|Profit After Tax|PAT)\D+([\d,\.]+)",

            "EBITDA": r"(EBITDA)\D+([\d,\.]+)",

            "Operating Profit": r"(Operating Profit|Operating Income)\D+([\d,\.]+)",

            "EPS": r"(EPS|Earnings Per Share)\D+([\d,\.]+)",

            "Debt": r"(Total Debt|Debt)\D+([\d,\.]+)",

            "Assets": r"(Total Assets)\D+([\d,\.]+)",

            "Liabilities": r"(Total Liabilities)\D+([\d,\.]+)",

            "Equity": r"(Shareholders'? Equity|Total Equity)\D+([\d,\.]+)",

            "Cash Flow": r"(Cash Flow from Operations|Operating Cash Flow)\D+([\d,\.]+)",

        }

    # -------------------------------------------------------

    def extract(self, text: str) -> Dict[str, Any]:

        result = {}

        for field, pattern in self.patterns.items():

            match = re.search(pattern, text, re.IGNORECASE)

            if match:

                value = match.group(2)

                value = value.replace(",", "")

                try:

                    value = float(value)

                except Exception:

                    pass

                # Sprint 5 — Evidence-Proof Layer: attach the exact
                # supporting text fragment from the source document.
                # This is the real matched sentence (or the matched span
                # itself when sentence boundaries cannot be resolved) —
                # genuine provenance, never invented. It is only present
                # when the extractor actually matched text.
                evidence = self._evidence_fragment(text, match)

                result[field] = {

                    "value": value,

                    "source": "Document",

                    "evidence": evidence

                }

        return result

    # -------------------------------------------------------
    # Fix #S5 — supporting-text fragment capture
    # -------------------------------------------------------

    @staticmethod
    def _evidence_fragment(text, match, limit=280):
        """The real sentence in `text` containing the matched value —
        exact supporting evidence for the extracted fact. Returns None
        when the text is unusable; the caller then omits the key so the
        UI renders '—' instead of fabricating anything."""
        try:
            if not isinstance(text, str) or not text or match is None:
                return None
            start, end = match.start(), match.end()
            if start < 0:
                return None
            # sentence start: after the last '.', '!', '?' or newline
            s = max(
                text.rfind(".", 0, start),
                text.rfind("!", 0, start),
                text.rfind("?", 0, start),
                text.rfind("\n", 0, start),
            )
            s = 0 if s < 0 else s + 1
            # sentence end: at the next '.', '!', '?' or newline
            candidates = [
                text.find(".", end),
                text.find("!", end),
                text.find("?", end),
                text.find("\n", end),
            ]
            candidates = [c for c in candidates if c >= 0]
            e = min(candidates) if candidates else len(text)
            frag = text[s:e].strip()
            if len(frag) > limit:
                # keep the window around the matched value
                mid = start - s
                frag = frag[max(0, mid - limit // 3): min(len(frag), mid + 2 * limit // 3)].strip()
            return frag or None
        except Exception:
            return None


# -----------------------------------------------------------

def extract_financial_data(text: str) -> Dict[str, Any]:

    extractor = FinancialExtractor()

    return extractor.extract(text)
