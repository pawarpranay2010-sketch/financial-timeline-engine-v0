"""
Financial Timeline Engine
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening
backend/maths/normalization.py

Hardened real-filing normalization (Sprint 12D section B).

The pipeline must survive realistic financial-document problems without
ever silently resolving ambiguity:

* parentheses negatives           (1,234)  -> -1234
* minus / unicode minus           -1,234 / -1,234
* currency symbols                $1,234.5 / 500 Cr (-> INR crores)
* percentages                     20%  -> value 20, percent kind
* per-share values                unit "per share" / "$/share"
* scale suffixes                  K / M / B / T / Cr / L
* thousands separators            1,234.56
* fiscal-year / quarter labels    FY2024 / Q1 FY25  (label parsing only)

Rules
-----
* The ORIGINAL text is always preserved; parsing only builds the
  analytical representation.
* Unparseable input -> value None (BLOCKED downstream). Never guessed,
  never interpolated, never coerced to zero.
* Ambiguous forms that cannot be resolved deterministically (e.g. a
  European 1.234,56 where the separator role is not explicit) produce an
  ambiguity note and value None - never a silent interpretation.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from backend.maths.units import PERCENT, SHARES, CURRENCY, classify_quantity

# ---------------------------------------------------------------------------
# Parsing tables (deterministic)
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS: Dict[str, str] = {
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "₹": "INR",
    "inr": "INR",
    "¥": "JPY",
    "jpy": "JPY",
    "¥c": "CNY",
    "cny": "CNY",
    "chf": "CHF",
    "a$": "AUD",
    "aud": "AUD",
    "c$": "CAD",
    "cad": "CAD",
}

_SCALE_SUFFIXES: Dict[str, str] = {
    "k": "thousands",
    "m": "millions",
    "b": "billions",
    "t": "trillions",
    "cr": "crores",
    "l": "lakhs",
}

_PER_SHARE_HINTS = (
    "per share", "per-share", "$/share", "usd/share", "inr/share",
    "share", "shares outstanding", "shares",
)

# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Deterministic result of parsing one raw cell value."""

    raw: Any = None
    value: Optional[Decimal] = None
    unit: Optional[str] = None
    scale: Optional[str] = None
    currency: Optional[str] = None
    kind: str = "unclassified"          # currency | shares | percent | count
    ambiguity: Optional[str] = None     # why parsing failed / was refused
    per_share: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "value": float(self.value) if self.value is not None else None,
            "unit": self.unit,
            "scale": self.scale,
            "currency": self.currency,
            "kind": self.kind,
            "ambiguity": self.ambiguity,
            "per_share": self.per_share,
        }


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[0-9][0-9,\.]*")
_PAREN_NEG = re.compile(r"\(\s*([0-9][0-9,\.]*)\s*\)")
_PERMISSIVE_NUM = re.compile(
    r"^-?(?:[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|"
    r"\.\d+)$"
)


def _strip_currency_symbol(text: str) -> tuple:
    """Return (rest, currency_code). Deterministic: longest symbol first."""
    s = text.strip()
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if s.lower().startswith(sym):
            return s[len(sym):].strip(), _CURRENCY_SYMBOLS[sym]
        if s.lower().endswith(sym):
            return s[:-len(sym)].strip(), _CURRENCY_SYMBOLS[sym]
    return s, None


def _extract_scale_suffix(text: str) -> tuple:
    """Return (rest, scale_label). Handles '1.2B', '500 Cr', '123M'."""
    s = text.strip()
    m = re.match(r"^(.*?)\s*([KMBCrTLl]{1,2})$", s, re.IGNORECASE)
    if not m:
        # bare two-letter codes that look like scale words
        m = re.match(r"^(.*?)\s*(million|millions|billion|billions|"
                     r"thousand|thousands|crore|crores|lakh|lakhs|trillion|"
                     r"trillions)$", s, re.IGNORECASE)
        if not m:
            return s, None
        rest, word = m.group(1), m.group(2).lower()
        word_map = {
            "million": "millions", "millions": "millions",
            "billion": "billions", "billions": "billions",
            "thousand": "thousands", "thousands": "thousands",
            "trillion": "trillions", "trillions": "trillions",
            "crore": "crores", "crores": "crores",
            "lakh": "lakhs", "lakhs": "lakhs",
        }
        return rest.strip(), word_map[word]
    rest, suffix = m.group(1).strip(), m.group(2).lower()
    scale = _SCALE_SUFFIXES.get(suffix)
    if scale is None:
        return s, None
    return rest, scale


def _clean_number_token(token: str) -> Optional[str]:
    """Normalize a numeric token to a Decimal-safe plain string, or None
    when the separators are ambiguous (European style is never guessed).
    A leading minus sign is carried through (unicode minus variants are
    normalized to '-' by the caller)."""
    t = token.strip().replace(" ", "")
    if not t:
        return None
    neg = False
    if t.startswith("-"):
        neg = True
        t = t[1:]
        if not t:
            return None
    cleaned = None
    # standard: 1,234.56 -> 1234.56
    if re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", t):
        cleaned = t.replace(",", "")
    # no grouping: 1234.56 / .5 / 0.5
    elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?|\.\d+", t):
        cleaned = t
    # 1.234,56 (European): both separators, non-standard role
    elif "," in t and "." in t:
        return None  # ambiguous - never guessed
    # single decimal comma: 1234,56 (ambiguous)
    elif "," in t:
        return None
    if cleaned is None:
        return None
    return ("-" + cleaned) if neg else cleaned


def parse_numeric_text(raw: Any) -> ParseResult:
    """Deterministic hard-parser for a raw financial cell value.

    Understands parentheses negatives, unicode minus signs, currency
    symbols, '%', scale suffixes (K/M/B/Cr), and thousands separators.
    Never guesses: unparseable or ambiguous input -> value None.
    """
    result = ParseResult(raw=raw)
    if raw is None or isinstance(raw, bool):
        result.ambiguity = "not a numeric value"
        return result
    if isinstance(raw, Decimal):
        result.value = raw
        return result
    if isinstance(raw, (int, float)):
        result.value = Decimal(str(raw))
        return result

    text = str(raw).strip()
    if not text:
        result.ambiguity = "empty value"
        return result

    # per-share hint detection (unit-level, not numeric)
    low = text.lower()
    result.per_share = any(h in low for h in _PER_SHARE_HINTS)
    if result.per_share:
        result.kind = SHARES
        # strip the per-share phrase so the numeric core can parse
        # ("12.5 per share" -> "12.5")
        for phrase in ("per share", "per-share", "/share", "share"):
            text = text.replace(phrase, " ")
        text = text.strip()

    # percent
    if text.endswith("%") or low.endswith("percent"):
        percent_text = text[:-1].strip() if text.endswith("%") else \
            text[: -len("percent")].strip()
        inner = parse_numeric_text(percent_text)
        if inner.value is None:
            result.ambiguity = inner.ambiguity or "unparseable percentage"
            return result
        result.value = inner.value
        result.unit = "%"
        result.kind = PERCENT
        return result

    # parentheses negative: (1,234)
    paren = _PAREN_NEG.search(text)
    if paren:
        token = paren.group(1)
        cleaned = _clean_number_token(token)
        if cleaned is None:
            result.ambiguity = (
                f"ambiguous number inside parentheses: {token!r}"
            )
            return result
        try:
            result.value = -Decimal(cleaned)
        except InvalidOperation:
            result.ambiguity = f"unparseable number: {token!r}"
            return result
        # detect trailing scale/currency in the surrounding text
        around = text[:paren.start()] + " " + text[paren.end():]
        rest, scale = _extract_scale_suffix(around)
        result.scale = scale
        rest2, currency = _strip_currency_symbol(rest)
        result.currency = currency
        if currency:
            result.kind = CURRENCY
        return result

    # currency symbol (prefix or suffix)
    rest, currency = _strip_currency_symbol(text)
    if currency:
        result.currency = currency
        result.kind = CURRENCY

    # unicode minus variants
    rest = rest.replace("−", "-").replace("–", "-").replace("—", "-")

    # scale suffix (after currency strip)
    rest, scale = _extract_scale_suffix(rest)
    result.scale = scale

    # numeric core
    core = rest.strip()
    if not core:
        result.ambiguity = "no numeric token found"
        return result
    cleaned = _clean_number_token(core)
    if cleaned is None:
        result.ambiguity = (
            f"ambiguous numeric formatting: {core!r} (never guessed)"
        )
        return result
    try:
        result.value = Decimal(cleaned)
    except InvalidOperation:
        result.ambiguity = f"unparseable number: {core!r}"
        return result
    if result.kind == "unclassified" and currency:
        result.kind = CURRENCY
    return result


def normalize_value_text(raw: Any) -> ParseResult:
    """Convenience alias (Sprint 12D public entry point)."""
    return parse_numeric_text(raw)


# ---------------------------------------------------------------------------
# Full pipeline-fact hardening: apply parse to a pipeline fact dict
# ---------------------------------------------------------------------------


def harden_fact_text(metric: str, fact: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically harden one pipeline fact dict.

    When the fact's value is text (or its unit/scale hint is present),
    re-parse it with the hardened parser. The original value is preserved
    in original_value; parsed results populate value/unit/scale/currency.
    A fact that refuses to parse keeps its original value and carries
    status_reason so the gate/solver fail closed.
    """
    from backend.maths.fact_model import to_decimal

    out = dict(fact)
    raw = out.get("value")
    existing = to_decimal(out.get("normalized_value", raw))
    if existing is not None:
        # already numeric - leave the pipeline normalization untouched
        return out
    parsed = parse_numeric_text(raw)
    out["original_value"] = raw
    if parsed.value is not None:
        out["value"] = parsed.value
        out["normalized_value"] = parsed.value
        if parsed.unit:
            out["unit"] = parsed.unit
        if parsed.scale:
            out["scale"] = parsed.scale
        if parsed.currency:
            out["currency"] = parsed.currency
        out["parse_kind"] = parsed.kind
        out["per_share"] = parsed.per_share
    else:
        out["status_reason"] = (
            f"could not parse value {raw!r}: {parsed.ambiguity or 'unknown'}"
        )
    return out
