"""
Platrixa
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

        # Sprint 6 - Page-Aware Evidence Anchoring. parse_pdf() embeds
        # "========== PAGE N ==========" markers in the extracted text and
        # merge_document_text() wraps each file with
        # "--- Start of File: <name> ---". These markers are the ONLY source
        # of page / document provenance: a fact is attributed to a page (or
        # a file) only when the corresponding marker is provably present in
        # the text before the matched value. No marker -> no key -> the UI
        # renders '-'. Nothing is ever invented.
        self._PAGE_MARKER_RE = re.compile(r"========== PAGE (\d+) ==========")
        self._FILE_MARKER_RE = re.compile(r"--- Start of File: (.+?) ---")

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

                # Sprint 6 - page / document anchoring from the ingestion
                # markers (only when reliably present; see __init__).
                page = self._page_of(text, match)
                document_name = self._document_of(text, match)

                fact = {

                    "value": value,

                    "source": "Document",

                    "evidence": evidence

                }

                if page is not None:

                    fact["page"] = page

                if document_name:

                    fact["document_name"] = document_name

                result[field] = fact

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

    # -------------------------------------------------------
    # Sprint 6 - page / document locators (marker-backed only)
    # -------------------------------------------------------

    def _page_of(self, text, match):
        """The page number whose '========== PAGE N ==========' marker
        immediately precedes the match, or None when no marker is present
        (plain text / non-PDF input). Never inferred - only read from the
        actual marker text."""
        try:
            if not isinstance(text, str) or match is None:
                return None
            prefix = text[:match.start()]
            found = list(self._PAGE_MARKER_RE.finditer(prefix))
            if not found:
                return None
            return int(found[-1].group(1))
        except Exception:
            return None

    def _document_of(self, text, match):
        """The file name whose '--- Start of File: <name> ---' header
        immediately precedes the match, or None when no header is present.
        Single-document corpora therefore carry the real file name; a
        multi-document fact is attributed only to the file whose header
        provably precedes it. Never inferred."""
        try:
            if not isinstance(text, str) or match is None:
                return None
            prefix = text[:match.start()]
            found = list(self._FILE_MARKER_RE.finditer(prefix))
            if not found:
                return None
            name = found[-1].group(1).strip()
            return name or None
        except Exception:
            return None


# -----------------------------------------------------------

def extract_financial_data(text: str) -> Dict[str, Any]:

    extractor = FinancialExtractor()

    return extractor.extract(text)
