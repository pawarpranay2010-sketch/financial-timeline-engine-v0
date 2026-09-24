"""Platrixa — document/evidence representation.

Pure data. No I/O, no model, no financial reasoning, no status authority.

Design constraints enforced here:

* Every page of every input document is represented — including pages with
  no extractable text, image-only pages, and pages whose extraction raised.
* Evidence identity is **deterministic**: the same document bytes always
  produce the same ``document_id``, the same page numbers, and the same
  ``evidence_id`` values, so evidence references are stable across repeated
  processing (required for lineage and replay).
* Nothing here claims a financial fact, a confidence verdict, or VERIFIED.
  ``extraction_confidence`` is a *document-layer* optical confidence only
  and is never used as a financial-authority input.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Extraction status taxonomy (Phase 1 requirement).
# This is DOCUMENT-layer status only. It is deliberately distinct from the
# Kernel's financial status taxonomy and must never be mapped onto it.
# --------------------------------------------------------------------------

DIGITAL_TEXT = "DIGITAL_TEXT"
IMAGE_ONLY = "IMAGE_ONLY"
EXTRACTION_FAILED = "EXTRACTION_FAILED"

#: Pseudo source-type used when the caller submits plain text directly.
DOCUMENT_SOURCE_TEXT = "PLAIN_TEXT"

ExtractionStatus = str  # one of the three constants above

_WS_RE = re.compile(r"\s+")

#: Confidence below which an engine's output is considered unusable. This is
#: a conservative, fixed, deterministic constant — never learned, never
#: fabricated, never tuned per document.
MIN_USABLE_CONFIDENCE = 0.30


def _normalized(text: Optional[str]) -> str:
    return _WS_RE.sub(" ", (text or "")).strip()


def _document_id(source_name: str, data: bytes) -> str:
    """Content-addressed, deterministic document identity.

    Uses the SHA-256 of the actual bytes so the same file renamed still maps
    to the same content identity, while two different files never collide.
    The name is mixed in only as a short readable prefix.
    """
    digest = hashlib.sha256(data or b"").hexdigest()
    stem = re.sub(r"[^A-Za-z0-9]+", "", source_name or "doc")[:24] or "doc"
    return f"doc_{stem}_{digest[:16]}"


def _evidence_id(document_id: str, page: int, index: int) -> str:
    """Stable evidence reference: document + page + within-page ordinal."""
    return f"{document_id}:p{page}:e{index:04d}"


@dataclass(frozen=True)
class EvidenceRef:
    """One normalized, citable unit of source content.

    Deliberately minimal. There is no ``value`` field, no ``amount`` field
    and no ``interpretation`` field — an evidence ref points at *source
    text*, never at a financial conclusion.
    """

    evidence_id: str
    document_id: str
    page: int
    text: str
    #: (x0, y0, x1, y1) in the engine's coordinate space, or None when the
    #: engine does not provide boxes.
    bbox: Optional[Tuple[float, float, float, float]] = None
    #: Optical/recognition confidence in [0, 1], or None when the engine
    #: does not report one. Never invented: it is None, not a default 1.0.
    confidence: Optional[float] = None
    #: Where this text came from, e.g. "pypdf", "rapidocr", "tesseract",
    #: "plain_text", "mixed".
    source_type: str = ""
    #: Engine identity + version when the text came from an OCR engine.
    engine: Optional[str] = None
    engine_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "page": self.page,
            "text": self.text,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "extraction_confidence": self.confidence,
            "source_type": self.source_type,
            "engine": self.engine,
            "engine_version": self.engine_version,
        }


@dataclass(frozen=True)
class PageRepresentation:
    """Exactly one physical page. Created for EVERY page, always."""

    document_id: str
    page: int  # 1-based, matching the existing "========== PAGE N ==========" convention
    status: ExtractionStatus
    text: str = ""
    width: Optional[float] = None
    height: Optional[float] = None
    #: Reason a page is IMAGE_ONLY / EXTRACTION_FAILED — diagnostic only.
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "status": self.status,
            "text_chars": len(self.text or ""),
            "width": self.width,
            "height": self.height,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DocumentRepresentation:
    """The full deterministic document evidence envelope."""

    document_id: str
    source_name: str
    #: "pdf" | "image" | "text"
    source_kind: str
    pages: List[PageRepresentation] = field(default_factory=list)
    evidence: List[EvidenceRef] = field(default_factory=list)
    #: Engine actually used, and its version. None when no engine ran
    #: (pure text extraction) — never fabricated.
    engine: Optional[str] = None
    engine_version: Optional[str] = None
    #: Wall-clock milliseconds spent in the document layer, for audit only.
    processing_ms: Optional[float] = None
    #: Deterministic, non-authoritative notes: e.g. pages needing OCR.
    notes: List[str] = field(default_factory=list)

    # -- derived, read-only helpers ------------------------------------

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def pages_by_status(self) -> Dict[str, List[int]]:
        out: Dict[str, List[int]] = {
            DIGITAL_TEXT: [], IMAGE_ONLY: [], EXTRACTION_FAILED: [],
        }
        for p in self.pages:
            out.setdefault(p.status, []).append(p.page)
        return out

    @property
    def has_extractable_text(self) -> bool:
        return any((p.text or "").strip() for p in self.pages)

    @property
    def pages_needing_ocr(self) -> List[int]:
        """Pages with no usable text: the ONLY pages an OCR engine may run on."""
        return [p.page for p in self.pages if not (p.text or "").strip()]

    def text_for_interpretation(self) -> str:
        """Serialize to the EXISTING kernel text boundary.

        Emits the same ``========== PAGE N ==========`` markers the existing
        ``ingestion.parser.parse_pdf`` already produces, so downstream text
        handling — layout enrichment, page attribution, and the kernel's own
        text-only ``process(raw_input: str)`` — behaves exactly as it does
        for text submitted today.
        """
        chunks: List[str] = []
        for page in self.pages:
            text = page.text or ""
            # Every page emits its marker, INCLUDING pages with no text, so
            # page numbering is complete and downstream page attribution
            # can see that the page exists.
            chunks.append(f"\n========== PAGE {page.page} ==========\n")
            if text.strip():
                chunks.append(text)
            # A page with no text contributes NOTHING else. We deliberately
            # do not inject a placeholder sentence: that would be synthetic
            # text entering the semantic interpreter's input, and it would
            # mask a textless page from existing consumers that detect empty
            # pages by inspecting the lines under each marker. The page's
            # real status is carried structurally in ``pages``/``evidence``
            # and is exposed to callers through ``to_dict()``.
        return "\n".join(chunks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_name": self.source_name,
            "source_kind": self.source_kind,
            "page_count": self.page_count,
            "pages_by_status": self.pages_by_status,
            "pages_needing_ocr": self.pages_needing_ocr,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "processing_ms": self.processing_ms,
            "notes": list(self.notes),
            "pages": [p.to_dict() for p in self.pages],
            "evidence": [e.to_dict() for e in self.evidence],
        }
