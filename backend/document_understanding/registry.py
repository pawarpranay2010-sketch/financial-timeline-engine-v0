"""Platrixa — OCR provider registry.

The single place that decides WHICH OCR engine runs. Nothing else in
Platrixa knows engine names; in particular the semantic interpreter is
never wired to an engine, only to the :class:`OCRProvider` interface.

Resolution rules:
  * explicit name  -> that engine, or fail closed if unavailable
  * auto           -> first available of (rapidocr, tesseract)
  * unavailable    -> ``NullOCRProvider`` (no OCR attempted, fail closed)

The registry never imports an engine module unless it is actually needed,
so a text-only installation pays nothing.
"""

from __future__ import annotations

import os
from typing import Optional

from backend.document_understanding.ocr import (
    NullOCRProvider,
    OCRProvider,
)

#: Preference order. RapidOCR/PP-OCRv5 first (document-aware, bboxes,
#: confidence, strong multilingual); Tesseract as the light fallback.
ENGINE_ORDER = ("rapidocr", "tesseract")


def _build(name: str) -> Optional[OCRProvider]:
    if name == "rapidocr":
        from backend.document_understanding.ocr_rapid import RapidOCRProvider

        return RapidOCRProvider()
    if name == "tesseract":
        from backend.document_understanding.ocr_tesseract import TesseractProvider

        return TesseractProvider()
    return None


def get_ocr_provider(name: Optional[str] = None) -> OCRProvider:
    """Return a provider.

    ``name=None`` consults ``PLATRIXA_OCR_ENGINE``; the literal values
    ``"none"``/``"off"``/``"disabled"`` disable OCR entirely, which is the
    supported way to run with no OCR dependency at all.
    """
    requested = (name or os.getenv("PLATRIXA_OCR_ENGINE") or "auto").strip().lower()

    if requested in ("none", "off", "disabled"):
        return NullOCRProvider()

    if requested != "auto":
        provider = _build(requested)
        if provider is not None and provider.is_available():
            return provider
        # An explicitly requested but unavailable engine fails closed to the
        # null provider rather than silently substituting another engine.
        return NullOCRProvider()

    for engine in ENGINE_ORDER:
        try:
            provider = _build(engine)
        except Exception:
            continue
        if provider is not None and provider.is_available():
            return provider

    return NullOCRProvider()
