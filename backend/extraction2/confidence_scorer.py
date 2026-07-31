"""
Financial Timeline Engine
Extraction 2.0 - Confidence Scorer

Explicit, never-fabricated confidence scores based on the extraction
method actually used, with small deterministic adjustments for verifiable
contextual signals (period, currency, unit, anchor).

Hierarchy (from the spec):

    XBRL structured fact        -> highest      (0.98)
    Structured HTML table       -> very high    (0.92)
    PDF table extraction        -> high         (0.85)
    Layout-aware text           -> medium/high  (0.70)
    Contextual regex            -> medium/low   (0.55)
    Unanchored regex            -> low          (0.35)
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Method constants
# ---------------------------------------------------------------------------

METHOD_XBRL = "XBRL"
METHOD_HTML_TABLE = "HTML_TABLE"
METHOD_PDF_TABLE = "PDF_TABLE"
METHOD_LAYOUT_AWARE = "LAYOUT_AWARE_TEXT"
METHOD_CONTEXTUAL_REGEX = "CONTEXTUAL_REGEX"
METHOD_UNANCHORED_REGEX = "UNANCHORED_REGEX"

# Base confidence per method (explicit, documented, never inflated)
BASE_CONFIDENCE = {
    METHOD_XBRL: 0.98,
    METHOD_HTML_TABLE: 0.92,
    METHOD_PDF_TABLE: 0.85,
    METHOD_LAYOUT_AWARE: 0.70,
    METHOD_CONTEXTUAL_REGEX: 0.55,
    METHOD_UNANCHORED_REGEX: 0.35,
}

# Deterministic adjustment deltas (small, bounded)
_ADJUST_PERIOD = 0.03       # a valid fiscal period is attached
_ADJUST_CURRENCY = 0.02     # currency code/symbol attached
_ADJUST_UNIT_SCALE = 0.02   # unit or scale metadata attached
_ADJUST_ANCHOR = 0.03       # traceable source anchor retained
_ADJUST_MISSING_PERIOD = -0.05
_ADJUST_MISSING_ANCHOR = -0.05


class ConfidenceScorer:
    """Assigns extraction confidence deterministically."""

    @staticmethod
    def base_for(method: str) -> float:
        """Base confidence for an extraction method."""
        return BASE_CONFIDENCE.get(method, 0.3)

    @staticmethod
    def score(
        method: str,
        has_period: bool = False,
        has_currency: bool = False,
        has_unit_scale: bool = False,
        has_anchor: bool = False,
    ) -> float:
        """
        Deterministically compute the confidence for a fact.

        Adjustments are additive, small, and clamped to [0.1, 0.99].
        A fact can never reach 1.0 -- perfect confidence is reserved
        for verified facts, never raw extraction.
        """
        base = ConfidenceScorer.base_for(method)

        if method == METHOD_UNANCHORED_REGEX:
            # Unanchored regex never gets contextual bonuses
            return round(max(0.1, min(0.99, base)), 4)

        score = base
        if has_period:
            score += _ADJUST_PERIOD
        else:
            score += _ADJUST_MISSING_PERIOD
        if has_currency:
            score += _ADJUST_CURRENCY
        if has_unit_scale:
            score += _ADJUST_UNIT_SCALE
        if has_anchor:
            score += _ADJUST_ANCHOR
        else:
            score += _ADJUST_MISSING_ANCHOR

        return round(max(0.1, min(0.99, score)), 4)

    @staticmethod
    def label(confidence: float) -> str:
        """Human label for a confidence value."""
        if confidence >= 0.9:
            return "VERY_HIGH"
        if confidence >= 0.8:
            return "HIGH"
        if confidence >= 0.6:
            return "MEDIUM"
        if confidence >= 0.4:
            return "LOW"
        return "VERY_LOW"
