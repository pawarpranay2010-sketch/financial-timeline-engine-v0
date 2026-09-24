"""Platrixa — per-page document accounting.

Fixes the Phase 0 audit finding: the existing ``ingestion.parser.parse_pdf``
emits a ``========== PAGE N ==========`` marker only when
``page.extract_text()`` returns truthy text, so a scanned/image-only page
leaves no trace at all. The downstream ``pages_without_text`` signal can
therefore never fire for a real scan.

This module performs its OWN per-page read and classifies every physical
page. It deliberately does NOT modify ``ingestion.parser`` so that every
existing caller, test and fixture keeps byte-identical behavior.

Classification (document layer only — never a financial status):

    DIGITAL_TEXT     the page yielded usable text
    IMAGE_ONLY       the page yielded no text but is a readable page
                     (an image/scanned page: OCR may be attempted)
    EXTRACTION_FAILED reading the page raised

Fail-closed guarantees:

* A page is NEVER invented: page count comes from the reader.
* A page is NEVER dropped: an exception becomes an EXTRACTION_FAILED page,
  never a silently shorter document.
* Confidence is NEVER invented: it is None when the engine reports none.
* Nothing here computes or interprets any financial value.
"""

from __future__ import annotations

import io
from typing import Any, List, Optional, Tuple

from backend.document_understanding.representation import (
    DIGITAL_TEXT,
    EXTRACTION_FAILED,
    IMAGE_ONLY,
    PageRepresentation,
    _document_id,
)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")


def detect_source_kind(source_name: str) -> str:
    """Classify the input by name. Never by content sniffing."""
    name = (source_name or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(IMAGE_SUFFIXES):
        return "image"
    return "text"


def _read_pdf_pages(
    data: bytes,
    document_id: str,
) -> Tuple[List[PageRepresentation], List[str]]:
    """Read a PDF page-by-page, representing every page exactly once."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    notes: List[str] = []
    pages: List[PageRepresentation] = []

    try:
        reader = PdfReader(io.BytesIO(data))
        raw_pages = list(reader.pages)
    except Exception as exc:  # unreadable/corrupt/encrypted container
        # Fail closed as a single EXTRACTION_FAILED page rather than
        # pretending the document was empty.
        return [
            PageRepresentation(
                document_id=document_id,
                page=1,
                status=EXTRACTION_FAILED,
                text="",
                reason=f"pdf_read_failed:{type(exc).__name__}",
            )
        ], [f"pdf could not be read: {type(exc).__name__}"]

    if not raw_pages:
        return [], ["pdf contains zero pages"]

    for index, page in enumerate(raw_pages, start=1):
        width = height = None
        try:
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
        except Exception:
            # Geometry is optional metadata; its absence must never fail
            # the page.
            pass

        try:
            text = page.extract_text() or ""
        except Exception as exc:
            pages.append(PageRepresentation(
                document_id=document_id,
                page=index,
                status=EXTRACTION_FAILED,
                text="",
                width=width,
                height=height,
                reason=f"page_extract_failed:{type(exc).__name__}",
            ))
            continue

        stripped = (text or "").strip()
        if stripped:
            pages.append(PageRepresentation(
                document_id=document_id,
                page=index,
                status=DIGITAL_TEXT,
                text=text,
                width=width,
                height=height,
            ))
        else:
            # A real page with no text layer = image-only page. This is the
            # signal the OCR adapter keys off, and the signal the old parser
            # could not emit.
            pages.append(PageRepresentation(
                document_id=document_id,
                page=index,
                status=IMAGE_ONLY,
                text="",
                width=width,
                height=height,
                reason="no_extractable_text_layer",
            ))

    return pages, notes


def _read_image_page(data: bytes, document_id: str) -> List[PageRepresentation]:
    """A standalone image is a single IMAGE_ONLY page.

    The image is validated as decodable here; OCR, if enabled, does the
    actual recognition later.
    """
    reason = "image_input"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.verify()  # structural check only; no recognition happens here
    except Exception as exc:
        return [PageRepresentation(
            document_id=document_id,
            page=1,
            status=EXTRACTION_FAILED,
            text="",
            reason=f"image_decode_failed:{type(exc).__name__}",
        )]
    except ImportError:
        # Pillow absent: the image is still a real page, we simply cannot
        # validate it. Fail closed on recognition, not on page accounting.
        reason = "image_input_unvalidated"

    return [PageRepresentation(
        document_id=document_id,
        page=1,
        status=IMAGE_ONLY,
        text="",
        reason=reason,
    )]


def extract_pages(
    data: bytes,
    source_name: str,
) -> Tuple[str, str, List[PageRepresentation], List[str]]:
    """Read a document and return every page, with an explicit status.

    Returns ``(document_id, source_kind, pages, notes)``.

    Never raises for malformed content: a document that cannot be read comes
    back as one EXTRACTION_FAILED page so the caller can fail closed with an
    explicit reason.
    """
    data = data or b""
    document_id = _document_id(source_name, data)
    kind = detect_source_kind(source_name)

    if kind == "pdf":
        pages, notes = _read_pdf_pages(data, document_id)
    elif kind == "image":
        pages = _read_image_page(data, document_id)
        notes = []
    else:
        # Plain text / unknown extension: treated as a single digital page.
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:
            pages = [PageRepresentation(
                document_id=document_id,
                page=1,
                status=EXTRACTION_FAILED,
                text="",
                reason=f"text_decode_failed:{type(exc).__name__}",
            )]
            return document_id, kind, pages, [f"text decode failed: {type(exc).__name__}"]

        pages = [PageRepresentation(
            document_id=document_id,
            page=1,
            status=DIGITAL_TEXT,
            text=text,
        )]
        notes = []

    return document_id, kind, pages, notes
