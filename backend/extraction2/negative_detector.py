"""
Platrixa
Extraction 2.0 - Negative Detector

Correctly distinguishes accounting negatives written in parentheses

    (₹500 million)  ->  -500000000

from footnote / cross-reference markers

    (1) (2) (4)     ->  NOT numbers at all

Context signals used (deterministic, no ML):
  - currency symbols ($, €, £, ₹, ¥, etc.)
  - scale words (million, billion, crore, lakh, thousand)
  - thousands separators (500,000)
  - decimal points with magnitude
  - nearby metric label / accounting context
"""

from __future__ import annotations

import re
from typing import Optional

_CURRENCY_SYMBOLS = set("$€£¥₹₽₩₺₫₱₴")
_CURRENCY_CODES = [
    "USD", "EUR", "GBP", "JPY", "INR", "CNY", "CAD", "AUD",
    "CHF", "HKD", "SGD", "NZD", "SEK", "NOK", "DKK", "MXN",
    "BRL", "ZAR", "RUB", "KRW", "IDR", "MYR", "THB", "PLN",
    "TRY", "AED", "SAR", "ILS", "VND", "PHP", "PKR", "BDT",
    "LKR", "NPR", "EGP", "NGN", "KES",
]

_SCALE_WORDS = [
    "million", "millions", "milion", "billion", "billions",
    "crore", "crores", "lakh", "lakhs", "thousand", "thousands",
    "trillion", "trillions",
]

# A pure footnote reference is a small integer, usually 1-2 digits,
# occasionally 3 digits, with NO currency/scale/separator context.
_FOOTNOTE_RE = re.compile(r"^\d{1,3}$")

_NUMERIC_RE = re.compile(
    r"^\(?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?$"
)

# Metric labels that indicate accounting context nearby
_ACCOUNTING_LABELS = re.compile(
    r"(revenue|income|profit|loss|earnings|expense|cost|margin|"
    r"asset|liabilit|equity|cash|debt|ebitda|ebit|tax|depreciation|"
    r"amorti[sz]ation|dividend|share|stock|inventory|receivable|"
    r"payable|sales|turnover|balance|reserve|surplus)",
    re.IGNORECASE,
)

_PAGE_NUMBER_CONTEXT = re.compile(
    r"(page\s+\d+|p\.\s?\d+|\bpage\b|\bpage[s]?\b)",
    re.IGNORECASE,
)


class NegativeDetector:
    """Deterministic detection of parenthesized negative values."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def is_footnote_reference(inner: str, context: str = "") -> bool:
        """
        True when a parenthesized token is a footnote/cross-reference
        marker rather than a financial value.

        Rules:
          - Pure small integer (1-3 digits) with NO currency symbol,
            scale word, or thousands separator inside.
          - If strong financial context is present in the surrounding
            text (currency + metric label), treat as negative instead.
        """
        token = inner.strip()

        if not _FOOTNOTE_RE.match(token):
            return False  # not a small pure integer => not a footnote

        # Strong financial context overrides footnote classification:
        # e.g. "(500)" inside "Revenue (500 million)" is a negative.
        if any(ch in token for ch in _CURRENCY_SYMBOLS):
            return False

        if NegativeDetector._has_financial_context(context, token):
            return False

        return True

    @staticmethod
    def parse_parenthesized(
        text: str,
        context: str = "",
    ) -> Optional[float]:
        """
        Parse a parenthesized token into a negative float, or return None
        when it is not an accounting negative.

        Examples:
            "(500)"            + no context -> None (footnote)
            "(500)"            + "Revenue (500 million)" -> -500.0
            "(500,000)"        -> -500000.0
            "(₹500 million)"   -> -500.0  (unit words stripped)
            "(1,250.5)"        -> -1250.5
            "($1,234 million)" -> -1234.0
        """
        token = text.strip()

        if not (token.startswith("(") and token.endswith(")")):
            return None

        inner = token[1:-1].strip()

        # Strip currency symbols and scale words inside the token
        cleaned = inner
        for sym in _CURRENCY_SYMBOLS:
            cleaned = cleaned.replace(sym, "")
        for word in _SCALE_WORDS:
            cleaned = re.sub(rf"\b{word}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        if not _NUMERIC_RE.match(cleaned):
            return None

        # Footnote reference check (small int, no separators, no context)
        if NegativeDetector.is_footnote_reference(inner, context):
            return None

        value = float(cleaned.replace(",", ""))

        # Bare 3-digit numbers with no financial context stay ambiguous
        if abs(value) < 1000 and "," not in inner and "." not in inner:
            if not NegativeDetector._has_financial_context(context, inner):
                return None

        return -value

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_financial_context(context: str, token: str) -> bool:
        """Does the surrounding text give financial meaning to `token`?"""
        if not context:
            return False

        window = context[-160:]
        has_currency = (
            any(ch in window for ch in _CURRENCY_SYMBOLS)
            or any(
                re.search(rf"\b{code}\b", window, re.IGNORECASE)
                for code in _CURRENCY_CODES
            )
        )
        has_scale = any(
            re.search(rf"\b{word}\b", window, re.IGNORECASE)
            for word in _SCALE_WORDS
        )
        has_label = bool(_ACCOUNTING_LABELS.search(window))

        # A pure page-number context must NOT turn "(1)" into -1
        if _PAGE_NUMBER_CONTEXT.search(window):
            return False

        return (has_currency or has_scale) and has_label


def parse_parenthesized_value(
    text: str,
    context: str = "",
) -> Optional[float]:
    """Module-level convenience wrapper."""
    return NegativeDetector.parse_parenthesized(text, context=context)
