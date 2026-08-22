"""
Platrixa
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/units.py

Unit / scale / currency / period normalization.

Rules
-----
* The ORIGINAL representation is always preserved; normalization only
  builds the value the engine computes with.
* Scales are normalized to absolute units: 125.4 with scale "millions"
  -> 125400000, while original_value=125.4 / original_unit="USD millions"
  are retained.
* Incompatible quantities (currency vs shares) are NEVER normalized into
  the same unit system - that is a UNIT_MISMATCH.
* Incompatible currencies are NEVER silently converted - that is a
  CURRENCY_MISMATCH (an approved conversion relationship would be
  required, and none exists in this engine).
* Incompatible periods block same-period relationships (PERIOD_MISMATCH).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Tuple

from backend.maths.exceptions import (
    CurrencyMismatchError,
    PeriodMismatchError,
    ScaleMismatchError,
    UnitMismatchError,
)

# ---------------------------------------------------------------------------
# Quantity kinds
# ---------------------------------------------------------------------------

CURRENCY = "currency"
SHARES = "shares"
PERCENT = "percent"
RATIO = "ratio"
COUNT = "count"
UNCLASSIFIED = "unclassified"

# Common currency codes (upper-case). Anything else that looks like a
# 3-letter uppercase code is also treated as currency.
_CURRENCY_CODES = {
    "USD", "INR", "EUR", "GBP", "JPY", "CNY", "HKD", "SGD", "AUD", "CAD",
    "CHF", "NZD", "SEK", "NOK", "DKK", "MXN", "BRL", "ZAR", "KRW", "TWD",
    "THB", "MYR", "IDR", "PHP", "VND", "AED", "SAR", "TRY", "RUB", "PLN",
    "EGP", "NGN", "KES", "GHS", "ILS", "CLP", "COP", "PEN", "ARS",
}

_SCALE_MULTIPLIERS: Dict[str, Decimal] = {
    "unit": Decimal(1),
    "units": Decimal(1),
    "absolute": Decimal(1),
    "none": Decimal(1),
    "thousand": Decimal("1000"),
    "thousands": Decimal("1000"),
    "k": Decimal("1000"),
    "million": Decimal("1000000"),
    "millions": Decimal("1000000"),
    "m": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "billions": Decimal("1000000000"),
    "b": Decimal("1000000000"),
    "crore": Decimal("10000000"),
    "crores": Decimal("10000000"),
    "lakh": Decimal("100000"),
    "lakhs": Decimal("100000"),
}

_QUANTITY_HINTS = {
    "share": SHARES,
    "shares": SHARES,
    "per share": SHARES,
    "usd/shares": SHARES,
    "$/share": SHARES,
    "usd per share": SHARES,
    "inr/shares": SHARES,
    "%": PERCENT,
    "percent": PERCENT,
    "percentage": PERCENT,
    "bps": PERCENT,
    "basis points": PERCENT,
}


def classify_quantity(unit: Optional[str]) -> str:
    """Deterministic quantity-kind classification for a unit string.

    * empty / unknown            -> unclassified (compatible with anything)
    * known currency code / 3-uppercase-letter code -> currency
    * share-like hints           -> shares
    * percentage hints           -> percent
    * anything else              -> count
    """
    if unit is None:
        return UNCLASSIFIED
    s = str(unit).strip()
    if not s:
        return UNCLASSIFIED
    low = s.lower()
    for hint, kind in _QUANTITY_HINTS.items():
        if hint in low:
            return kind
    upper = s.upper().replace(" ", "")
    if upper in _CURRENCY_CODES:
        return CURRENCY
    if len(upper) == 3 and upper.isalpha():
        return CURRENCY
    # Currency-prefixed unit strings (e.g. "USD millions", "INR crore",
    # "USD thousands") are still currency quantities - the scale suffix
    # is normalized separately, never conflated with a different kind.
    for code in sorted(_CURRENCY_CODES, key=len, reverse=True):
        if upper.startswith(code):
            return CURRENCY
    return COUNT


def scale_multiplier(scale: Optional[str]) -> Optional[Decimal]:
    """Multiplier for a scale label; None when the label is unknown
    (unknown scales are never guessed - callers decide how to fail)."""
    if scale is None:
        return None
    return _SCALE_MULTIPLIERS.get(str(scale).strip().lower())


def normalize_value(value: Decimal, scale: Optional[str],
                    unit: Optional[str] = None) -> Decimal:
    """Normalize a raw-scaled magnitude to absolute units.

    Example: 125.4 with scale "millions" -> 125400000.
    The scale factor is applied only when the scale label is known.
    """
    mult = scale_multiplier(scale)
    if mult is None:
        return value
    return value * mult


# ---------------------------------------------------------------------------
# Compatibility checks
# ---------------------------------------------------------------------------


def quantities_compatible_for_add_sub(
    facts_kinds: Tuple[Optional[str], Optional[str]],
) -> Optional[str]:
    """Return None when the two quantity kinds may be added/subtracted,
    else a human reason (UNIT_MISMATCH). Unclassified sides are tolerated;
    two different CLASSIFIED kinds (currency vs shares) are rejected."""
    a, b = facts_kinds
    if a == UNCLASSIFIED or b == UNCLASSIFIED or a == b:
        return None
    return (
        "Incompatible quantities: cannot combine "
        f"{a or 'unknown'} and {b or 'unknown'} in the same equation."
    )


def quantities_compatible_for_divide(
    num_kind: Optional[str], den_kind: Optional[str],
) -> Optional[str]:
    """Division (ratio-building) requires both sides to be the same
    quantity kind (usually currency), or at least one unclassified.
    Mixing currency and shares in a ratio is not permitted by default."""
    if num_kind == UNCLASSIFIED or den_kind == UNCLASSIFIED or num_kind == den_kind:
        return None
    return (
        "Incompatible quantities: cannot divide "
        f"{num_kind or 'unknown'} by {den_kind or 'unknown'}."
    )


def currencies_compatible(cur_a: Optional[str], cur_b: Optional[str]) -> Optional[str]:
    """Return None when compatible, else a CURRENCY_MISMATCH reason.
    Unknown/one-sided currency is tolerated (cannot prove a mismatch);
    two known and different currencies are rejected - never converted."""
    ca = (cur_a or "").strip().upper()
    cb = (cur_b or "").strip().upper()
    if not ca or not cb:
        return None
    if ca != cb:
        return f"Currency mismatch between inputs ({ca} vs {cb})."
    return None


def scales_compatible(
    scale_a: Optional[str], scale_b: Optional[str],
) -> Optional[str]:
    """Scales are always normalized to absolute before arithmetic, so any
    two KNOWN scales are compatible. An unknown scale on either side is a
    SCALE_MISMATCH (never guess a factor)."""
    ma = scale_multiplier(scale_a)
    mb = scale_multiplier(scale_b)
    if (scale_a not in (None, "") and ma is None) or \
       (scale_b not in (None, "") and mb is None):
        return (
            "Scale mismatch between inputs: an unknown scale "
            "cannot be normalized (never guessed)."
        )
    return None


def periods_compatible(
    period_a: Optional[str], period_b: Optional[str], mode: str = "same",
) -> Optional[str]:
    """Period compatibility for a relationship.

    mode "same"      -> both non-empty periods must be identical
    mode "different" -> both non-empty periods must differ
    One-sided empty periods are tolerated (cannot prove a mismatch).
    """
    pa = (period_a or "").strip()
    pb = (period_b or "").strip()
    if not pa or not pb:
        return None
    if mode == "same" and pa != pb:
        return (
            f"Incompatible reporting periods for this relationship "
            f"({pa} vs {pb})."
        )
    if mode == "different" and pa == pb:
        return (
            f"This relationship requires two different reporting periods "
            f"(both are {pa})."
        )
    return None


def normalize_fact_for_formula(
    node,
    formula,
) -> Tuple[Decimal, str, str, str, str]:
    """Normalize one fact for use in a formula step.

    Returns (working_value, unit_label, currency, period, scale_label).
    Raises ScaleMismatchError for unknown scales when normalization is
    required (apply_scale facts).
    """
    value = node.value
    if value is None:
        raise ValueError(f"{node.node_id} has no numeric value")
    if node.apply_scale:
        if scale_multiplier(node.original_scale) is None and \
           node.original_scale not in (None, ""):
            raise ScaleMismatchError(
                f"Scale '{node.original_scale}' for {node.node_id} is unknown; "
                "normalization cannot proceed (never guessed)."
            )
        value = normalize_value(value, node.original_scale, node.original_unit)
    unit = node.original_unit or node.normalized_unit
    currency = node.currency
    period = node.period
    scale = node.original_scale
    return value, str(unit or ""), str(currency or ""), str(period or ""), str(scale or "")
