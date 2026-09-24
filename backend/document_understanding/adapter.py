"""Platrixa — deterministic evidence adapter.

Normalizes a document (and, when enabled, OCR output from an
``OCRProvider``) into a ``DocumentRepresentation``: pages, text, bounding
boxes, confidence, stable evidence IDs and explicit extraction status.

This adapter is the ONLY place document content is turned into a citable
structure, and it is deliberately the dumbest possible component:

* NO financial reasoning. No arithmetic, no totals, no classification of
  what a number *means*.
* NO status authority. It cannot emit VERIFIED and does not know the
  Kernel's status taxonomy.
* NO fabrication. Missing confidence stays ``None``; unreadable pages
  become ``EXTRACTION_FAILED`` with a reason; nothing is invented to fill
  a gap.
* NO kernel import. This module never imports ``backend.maths`` or the
  kernel, and no kernel/authority module imports this one.

Its single output that matters downstream is
``DocumentRepresentation.text_for_interpretation()``, which serializes to
the EXISTING text boundary consumed by ``Kernel.process(raw_input: str)``.
Everything after that point is the existing, unmodified pipeline.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from backend.document_understanding.page_accounting import extract_pages
from backend.document_understanding.representation import (
    DIGITAL_TEXT,
    EXTRACTION_FAILED,
    IMAGE_ONLY,
    DocumentRepresentation,
    EvidenceRef,
    PageRepresentation,
    _document_id,
    _evidence_id,
)


def _blocks_from_page_text(
    page: PageRepresentation,
    document_id: str,
) -> List[EvidenceRef]:
    """Split a digitally-extracted page into stable, citable blocks.

    One block per non-empty line. Line granularity is chosen deliberately:
    it is the smallest unit the existing grounding gate can actually verify
    (it does substring matching against the whole source text), so finer
    boxes would claim precision the downstream layer cannot use.
    """
    refs: List[EvidenceRef] = []
    index = 0
    for line in (page.text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        refs.append(EvidenceRef(
            evidence_id=_evidence_id(document_id, page.page, index),
            document_id=document_id,
            page=page.page,
            text=stripped,
            bbox=None,          # pypdf does not expose per-line geometry here
            confidence=None,    # NEVER invented: digital text has no OCR score
            source_type="pypdf",
        ))
        index += 1
    return refs


class EvidenceAdapter:
    """Deterministic document -> evidence normalizer.

    Parameters
    ----------
    ocr_provider:
        Optional :class:`OCRProvider`. When ``None`` (the default), no OCR
        is ever attempted and image-only pages simply remain image-only —
        which is the correct fail-closed behavior, not a silent success.
    min_confidence:
        Regions below this confidence are dropped from the evidence set
        rather than being passed on as if they were reliable.
    """

    def __init__(self, ocr_provider: Any = None, min_confidence: float = 0.30) -> None:
        self.ocr_provider = ocr_provider
        self.min_confidence = min_confidence

    # -- public API -----------------------------------------------------

    def interpret(
        self,
        data: bytes,
        source_name: str,
    ) -> DocumentRepresentation:
        """Build the evidence representation for one document.

        Never raises for bad content; unreadable input becomes an explicit
        ``EXTRACTION_FAILED`` page so the caller can fail closed.
        """
        started = time.perf_counter()

        document_id, kind, pages, notes = extract_pages(data, source_name)

        engine_name: Optional[str] = None
        engine_version: Optional[str] = None
        evidence: List[EvidenceRef] = []

        if kind == "text":
            # Plain text: already digital, no OCR, no extra accounting.
            evidence = self._blocks_for_pages(pages, document_id)
        else:
            # Digital text first. OCR runs ONLY on pages that yielded no
            # usable text — never unnecessarily.
            evidence = self._blocks_for_pages(pages, document_id)

            needs_ocr = [p.page for p in pages if not (p.text or "").strip()]
            if needs_ocr and self.ocr_provider is not None:
                pages, ocr_evidence, engine_name, engine_version = self._run_ocr(
                    pages, document_id, data, needs_ocr, notes
                )
                evidence.extend(ocr_evidence)
            elif needs_ocr:
                notes.append(
                    f"pages {needs_ocr} have no extractable text and no OCR "
                    f"provider is configured; they remain IMAGE_ONLY"
                )

        elapsed_ms = (time.perf_counter() - started) * 1000.0

        representation = DocumentRepresentation(
            document_id=document_id,
            source_name=source_name,
            source_kind=kind,
            pages=pages,
            evidence=evidence,
            engine=engine_name,
            engine_version=engine_version,
            processing_ms=round(elapsed_ms, 3),
            notes=notes,
        )
        return representation

    # -- internals ------------------------------------------------------

    def _blocks_for_pages(
        self,
        pages: List[PageRepresentation],
        document_id: str,
    ) -> List[EvidenceRef]:
        refs: List[EvidenceRef] = []
        for page in pages:
            if page.status != DIGITAL_TEXT:
                continue
            refs.extend(_blocks_from_page_text(page, document_id))
        return refs

    def _run_ocr(
        self,
        pages: List[PageRepresentation],
        document_id: str,
        data: bytes,
        needs_ocr: List[int],
        notes: List[str],
    ):
        """Invoke the OCR provider on the pages that need it.

        Any failure — provider unavailable, exception, or unusable output —
        leaves those pages as IMAGE_ONLY / EXTRACTION_FAILED. It NEVER
        fabricates text and never downgrades to a guessed result.
        """
        provider = self.ocr_provider
        engine_name = getattr(provider, "name", None)
        engine_version = getattr(provider, "version", None)

        try:
            available = provider.is_available()
        except Exception as exc:
            available = False
            notes.append(f"ocr provider availability check failed: {type(exc).__name__}")

        if not available:
            notes.append(
                f"OCR unavailable; pages {needs_ocr} remain IMAGE_ONLY "
                f"(fail closed: no text is fabricated)"
            )
            return pages, [], engine_name, engine_version

        try:
            result = provider.recognize(data, source_kind=_source_kind_of(pages))
        except Exception as exc:
            notes.append(f"ocr failed closed: {type(exc).__name__}")
            return pages, [], engine_name, engine_version

        regions = list(getattr(result, "regions", None) or [])
        if not regions:
            notes.append(
                f"OCR returned no usable text for pages {needs_ocr}; "
                f"they remain IMAGE_ONLY (fail closed)"
            )
            return pages, [], engine_name, engine_version

        # Re-index regions per page, in engine order, with a stable ordinal.
        by_page = {}
        for region in regions:
            page_no = int(getattr(region, "page", 1) or 1)
            by_page.setdefault(page_no, []).append(region)

        # Pages whose extraction FAILED are never eligible for OCR.
        # OCR exists to read a page that has no text layer; it must never
        # resurrect a page the reader could not even open (a corrupt or
        # truncated file). Such a page stays EXTRACTION_FAILED and fail
        # closed, no matter what an engine claims to have found.
        failed_pages = {p.page for p in pages if p.status == EXTRACTION_FAILED}
        if failed_pages:
            notes.append(
                f"pages {sorted(failed_pages)} failed extraction and are not "
                f"eligible for OCR; they remain EXTRACTION_FAILED"
            )

        updated: List[PageRepresentation] = []
        ocr_evidence: List[EvidenceRef] = []

        for page in pages:
            if page.status == EXTRACTION_FAILED:
                updated.append(page)
                continue
            regions_for_page = by_page.get(page.page, [])
            usable: List[EvidenceRef] = []
            ordinal = 0
            page_lines: List[str] = []

            for region in regions_for_page:
                text = (getattr(region, "text", "") or "").strip()
                if not text:
                    continue
                confidence = getattr(region, "confidence", None)
                if confidence is not None and float(confidence) < self.min_confidence:
                    # Below the confidence floor: excluded from evidence
                    # entirely. It is NOT silently accepted and NOT
                    # replaced with a guess.
                    continue
                usable.append(EvidenceRef(
                    evidence_id=_evidence_id(document_id, page.page, ordinal),
                    document_id=document_id,
                    page=page.page,
                    text=text,
                    bbox=_bbox_of(region),
                    confidence=(float(confidence) if confidence is not None else None),
                    source_type=getattr(region, "source_type", "ocr") or "ocr",
                    engine=engine_name,
                    engine_version=engine_version,
                ))
                page_lines.append(text)
                ordinal += 1

            if usable:
                # The page now has recognized text: its status becomes
                # digital-equivalent, but its provenance is recorded as OCR.
                updated.append(PageRepresentation(
                    document_id=document_id,
                    page=page.page,
                    status=DIGITAL_TEXT,
                    text="\n".join(page_lines),
                    width=page.width,
                    height=page.height,
                    reason="ocr_recognized",
                ))
                ocr_evidence.extend(usable)
            else:
                updated.append(page)

        return updated, ocr_evidence, engine_name, engine_version


def _bbox_of(region: Any):
    raw = getattr(region, "bbox", None)
    if raw is None:
        return None
    try:
        x0, y0, x1, y1 = raw
        return (float(x0), float(y0), float(x1), float(y1))
    except Exception:
        return None


def _source_kind_of(pages: List[PageRepresentation]) -> str:
    return "image" if len(pages) == 1 and pages[0].status == IMAGE_ONLY else "pdf"
