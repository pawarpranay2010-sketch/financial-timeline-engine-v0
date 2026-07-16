# core/validation.py
"""
Validation helpers for AI provider responses.

Two responsibilities, both reused across the timeline/, intelligence/, and
memo/ modules (as well as the frontend):

1. `is_error_response` -- centralizes the "is this string actually a
   rendered user-facing error message rather than real model output?"
   check (previously duplicated ad hoc, e.g. as
   `("❌" in x) or ("🔴" in x) or ("⚠️" in x)` inline in the UI code).

2. `extract_json` -- robustly parses a JSON object/array out of a raw AI
   text response, tolerating markdown code fences and prose wrapped around
   the JSON, and falling back to an empty instance of the expected type
   (never raises) so downstream rendering/export code never breaks on a
   malformed or missing response.
"""
from __future__ import annotations

import json
from typing import Type, TypeVar

from core.constants import ERROR_RESPONSE_MARKERS
from core.logging import get_logger

_logger = get_logger("core.validation")

T = TypeVar("T", dict, list)


def is_error_response(text: str | None) -> bool:
    """True if `text` looks like one of the engine's own rendered error
    messages (see `core.constants.ERROR_RESPONSE_MARKERS`) rather than
    genuine AI-generated content."""
    if not text:
        return False
    return any(text.startswith(marker) for marker in ERROR_RESPONSE_MARKERS)


def contains_error_marker(text: str | None) -> bool:
    """Looser variant of `is_error_response`: true if an error marker
    appears *anywhere* in the text, not just at the start. Matches the
    original app's inline check used right before timeline extraction."""
    if not text:
        return False
    return any(marker in text for marker in ERROR_RESPONSE_MARKERS)


def extract_json(result: str | None, expected_type: Type[T] = dict) -> T:
    """Parses a JSON object/array out of a raw AI response.

    Handles three shapes of input, in order:
      1. Already-clean JSON.
      2. JSON wrapped in a markdown code fence (```json ... ``` or ``` ... ```).
      3. JSON embedded in surrounding prose ("Here is the JSON:\n{...}\n
         Let me know...") with no code fence, by locating the outermost
         matching bracket pair.

    Returns an empty instance of `expected_type` (i.e. `{}` or `[]`) on any
    provider error, parse failure, or type mismatch -- this function never
    raises, since callers (timeline extraction, intelligence extraction)
    need rendering/export to keep working even when the model returns
    unusable output.
    """
    empty = expected_type()
    if is_error_response(result):
        return empty
    if not result:
        return empty

    cleaned = result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, expected_type) else empty
    except Exception:
        pass

    # Fallback: locate the outermost {...} or [...] block by bracket
    # position and retry, instead of silently discarding a response that
    # actually contained valid, usable JSON surrounded by prose.
    open_char = "{" if expected_type is dict else "["
    close_char = "}" if expected_type is dict else "]"
    start = cleaned.find(open_char)
    end = cleaned.rfind(close_char)
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            return parsed if isinstance(parsed, expected_type) else empty
        except Exception as exc:
            _logger.warning("JSON parse failed after fallback extraction: %s", exc)
            return empty

    _logger.warning("No JSON object/array found in AI response.")
    return empty
