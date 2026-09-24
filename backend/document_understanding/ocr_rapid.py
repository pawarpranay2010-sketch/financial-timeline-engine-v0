"""Platrixa — RapidOCR / PP-OCRv5 provider.

An EXISTING open-source OCR engine (Apache-2.0) integrated behind the
``OCRProvider`` interface. Platrixa does not build, train or modify an OCR
model; it calls one and normalizes what comes back.

Chosen in the Phase 0 audit on three grounds, not popularity:
  * Apache-2.0 for both code and weights — no copyleft, no non-commercial
    restriction on a hosted service (unlike MinerU / Marker / Surya).
  * CPU-viable, with strong multilingual coverage (80+ languages).
  * Returns bounding boxes and per-region confidence natively, which is what
    the evidence adapter needs.

Everything about the dependency is OPTIONAL:
  * ``rapidocr_onnxruntime`` is imported lazily, inside ``_ensure_engine``.
  * The module imports cleanly with the engine absent.
  * Nothing in the core runtime imports this module unless a caller
    explicitly asks for OCR.

Fail-closed contract: any problem — import failure, engine crash, empty
output, or unusable confidence — yields no recognized text. It NEVER
fabricates, guesses, or falls back to a lower-statement "best effort" text.
"""

from __future__ import annotations

import io
import threading
from typing import Any, List, Optional

from backend.document_understanding.ocr import OCRProvider, OCRRegion, OCRResult

_LOCK = threading.Lock()
_ENGINE_CACHE: dict = {}


class RapidOCRProvider(OCRProvider):
    """PP-OCRv5 via RapidOCR (ONNXRuntime build).

    Parameters
    ----------
    render_dpi:
        DPI used when rasterizing PDF pages. 200 is a reasonable accuracy /
        speed default for A4 financial documents.
    min_confidence:
        Engine-level floor. Regions below it are dropped here AND again in
        the adapter — defense in depth, never a substitute for the gate.
    """

    name = "rapidocr"

    def __init__(self, render_dpi: int = 200, min_confidence: float = 0.30) -> None:
        self.render_dpi = int(render_dpi)
        self.min_confidence = float(min_confidence)
        self._version: Optional[str] = None

    # -- availability ---------------------------------------------------

    def is_available(self) -> bool:
        try:
            self._ensure_engine()
            return True
        except Exception:
            return False

    @property
    def version(self) -> str:
        if self._version is None:
            try:
                self._ensure_engine()
            except Exception:
                self._version = "unavailable"
        return self._version or "unknown"

    # -- engine lifecycle -----------------------------------------------

    def _ensure_engine(self):
        """Lazily construct and cache the engine. Raises if unavailable."""
        with _LOCK:
            if "engine" in _ENGINE_CACHE:
                return _ENGINE_CACHE["engine"]

            # Lazy import: absent engine is a normal, supported state.
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            engine = RapidOCR()
            _ENGINE_CACHE["engine"] = engine
            try:
                self._version = getattr(
                    __import__("rapidocr_onnxruntime", fromlist=["__version__"]),
                    "__version__", "unknown",
                )
            except Exception:
                self._version = "unknown"
            return engine

    # -- recognition ----------------------------------------------------

    def recognize(self, data: bytes, source_kind: str = "pdf") -> OCRResult:
        """Recognize a PDF or image and return normalized regions.

        Raises on genuine engine failure; the EvidenceAdapter converts that
        into a fail-closed IMAGE_ONLY page.
        """
        engine = self._ensure_engine()

        if source_kind == "image":
            pages = [self._image_bytes(data)]
            if pages[0] is None:
                return OCRResult(regions=[], engine=self.name, version=self.version)
        else:
            pages = self._render_pdf_pages(data)

        regions: List[OCRRegion] = []
        for page_number, image_bytes in enumerate(pages, start=1):
            if image_bytes is None:
                continue
            for region in self._recognize_page(engine, image_bytes, page_number):
                regions.append(region)

        return OCRResult(regions=regions, engine=self.name, version=self.version)

    def _recognize_page(
        self, engine: Any, image_bytes: bytes, page_number: int
    ) -> List[OCRRegion]:
        import numpy as np  # provided by rapidocr's dependency set

        array = np.asarray(_pil_open(image_bytes))
        raw = engine(array)

        # RapidOCR returns [result, elapse] where result is a list of
        # [box, text, score] or None.
        if not raw:
            return []
        result = raw[0] if isinstance(raw, (list, tuple)) else raw
        if not result:
            return []

        regions: List[OCRRegion] = []
        for item in result:
            try:
                box, text, score = item[0], item[1], item[2]
            except Exception:
                continue
            text = (text or "").strip()
            if not text:
                continue
            try:
                score = float(score)
            except Exception:
                score = None
            if score is not None and score < self.min_confidence:
                continue
            regions.append(OCRRegion(
                text=text,
                page=page_number,
                bbox=_box_to_bbox(box),
                confidence=score,
                source_type=f"rapidocr:p{page_number}",
            ))
        return regions

    # -- rasterization ---------------------------------------------------

    def _render_pdf_pages(self, data: bytes) -> List[Optional[bytes]]:
        """Rasterize each PDF page to JPEG bytes.

        A page that fails to rasterize yields None and is simply skipped —
        it stays IMAGE_ONLY, which is the correct fail-closed outcome.
        """
        out: List[Optional[bytes]] = []
        try:
            import fitz  # PyMuPDF, an OCR-extra dependency
        except Exception:
            return [None]

        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception:
            return [None]

        zoom = self.render_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        try:
            for page in doc:
                try:
                    pix = page.get_pixmap(matrix=matrix)
                    out.append(pix.tobytes("jpeg"))
                except Exception:
                    out.append(None)
        finally:
            try:
                doc.close()
            except Exception:
                pass
        return out


def _pil_open(image_bytes: bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return image


def _box_to_bbox(box: Any):
    """RapidOCR returns a 4-point polygon; reduce to (x0, y0, x1, y1)."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        return None
