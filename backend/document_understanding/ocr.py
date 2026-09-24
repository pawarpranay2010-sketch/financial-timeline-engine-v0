"""Platrixa — OCR provider interface.

OCR is an IMPLEMENTATION behind a replaceable interface, never something
the semantic interpreter is wired to. The chain is:

    Document -> DocumentExtractor -> OCRProvider -> EvidenceAdapter

This module defines only the seam and two safe implementations:

* ``NullOCRProvider``   — the default. Never recognizes anything. Used when
  no OCR engine is installed, so a lightweight text-only install stays
  lightweight and still behaves exactly as before.
* ``FixtureOCRProvider`` — deterministic, test-only. Returns canned regions
  for a named fixture so the evidence pipeline can be tested without any
  OCR dependency or any accuracy claim.

Real engines (RapidOCR/PP-OCRv5, Tesseract) are registered in Phase 2 and
must satisfy the same contract:

    * return recognized text with page + bbox + confidence when available
    * report engine name and version
    * fail loudly / return nothing rather than guess

No provider in this package may compute, infer or validate a financial
value. An OCR engine reports glyphs; nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class OCRRegion:
    """One recognized region of source content."""

    text: str
    page: int
    bbox: Optional[Any] = None  # (x0, y0, x1, y1)
    confidence: Optional[float] = None
    source_type: str = "ocr"


@dataclass(frozen=True)
class OCRResult:
    """The result of one OCR pass over a document."""

    regions: List[OCRRegion]
    engine: str
    version: str

    @property
    def usable(self) -> bool:
        return any((r.text or "").strip() for r in self.regions)


@runtime_checkable
class OCRProvider(Protocol):
    """Interface every OCR engine must satisfy."""

    name: str
    version: str

    def is_available(self) -> bool:
        """True only when the engine can actually run right now."""
        ...

    def recognize(self, data: bytes, source_kind: str = "pdf") -> OCRResult:
        """Recognize ``data``.

        Must return an :class:`OCRResult`. On failure it may raise or return
        an empty result; the EvidenceAdapter treats both identically and
        fails closed. It must never return invented text.
        """
        ...


class NullOCRProvider:
    """The default: no OCR available, no OCR attempted.

    Keeps the core runtime dependency-free and guarantees that a system
    without an OCR engine degrades to today's exact text-only behavior.
    """

    name = "none"
    version = "0"

    def is_available(self) -> bool:
        return False

    def recognize(self, data: bytes, source_kind: str = "pdf") -> OCRResult:
        return OCRResult(regions=[], engine=self.name, version=self.version)


class FixtureOCRProvider:
    """Deterministic provider backed by a fixed in-memory fixture.

    TEST USE ONLY. It exists so the evidence pipeline (evidence IDs, page
    attribution, confidence handling, fail-closed behavior) can be verified
    without installing an OCR engine. It makes NO accuracy claim: it returns
    exactly the canned regions it was constructed with.
    """

    def __init__(
        self,
        regions: Optional[List[OCRRegion]] = None,
        available: bool = True,
        raise_on_use: bool = False,
    ) -> None:
        self.name = "fixture"
        self.version = "test-1"
        self._regions = regions or []
        self._available = available
        self._raise = raise_on_use
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def recognize(self, data: bytes, source_kind: str = "pdf") -> OCRResult:
        self.calls += 1
        if self._raise:
            raise RuntimeError("fixture OCR failure")
        return OCRResult(
            regions=list(self._regions),
            engine=self.name,
            version=self.version,
        )
