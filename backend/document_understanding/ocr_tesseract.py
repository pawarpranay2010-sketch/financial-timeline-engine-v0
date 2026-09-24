"""Platrixa — Tesseract provider (fallback OCR engine).

Tesseract 5 (Apache-2.0) is kept as a *fallback* only. It is mature, tiny
and CPU-only, which makes it a useful safety net, but it has no document
layout model: on dense financial tables it loses column structure, and its
per-word output is noisier than PP-OCRv5. It is never preferred when
RapidOCR is available.

Like the RapidOCR provider:
  * imports lazily and cleanly when the binary/module is absent,
  * returns recognized text with page + bbox + confidence,
  * fails closed rather than guessing.
"""

from __future__ import annotations

import io
import shutil
from typing import Any, List, Optional

from backend.document_understanding.ocr import OCRProvider, OCRRegion, OCRResult


class TesseractProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, render_dpi: int = 300, min_confidence: float = 0.40,
                 lang: str = "eng") -> None:
        self.render_dpi = int(render_dpi)
        self.min_confidence = float(min_confidence)
        self.lang = lang
        self._version: Optional[str] = None

    def is_available(self) -> bool:
        # Tesseract is a system binary; python bindings alone are not enough.
        if shutil.which("tesseract") is None:
            return False
        try:
            import pytesseract  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def version(self) -> str:
        if self._version is None:
            try:
                import pytesseract

                self._version = str(pytesseract.get_tesseract_version())
            except Exception:
                self._version = "unavailable"
        return self._version or "unknown"

    def recognize(self, data: bytes, source_kind: str = "pdf") -> OCRResult:
        import pytesseract
        from PIL import Image

        if source_kind == "image":
            images = [Image.open(io.BytesIO(data))]
        else:
            images = self._render_pdf_pages(data)

        regions: List[OCRRegion] = []
        for page_number, image in enumerate(images, start=1):
            if image is None:
                continue
            try:
                data_frame = pytesseract.image_to_data(
                    image,
                    lang=self.lang,
                    output_type=pytesseract.Output.DICT,
                )
            except Exception:
                continue

            for i, word in enumerate(data_frame.get("text", [])):
                word = (word or "").strip()
                if not word:
                    continue
                try:
                    score = float(data_frame["conf"][i])
                except Exception:
                    score = -1.0
                # Tesseract reports -1 for non-text detections.
                if score < 0:
                    continue
                score = score / 100.0
                if score < self.min_confidence:
                    continue
                regions.append(OCRRegion(
                    text=word,
                    page=page_number,
                    bbox=(
                        float(data_frame["left"][i]),
                        float(data_frame["top"][i]),
                        float(data_frame["left"][i] + data_frame["width"][i]),
                        float(data_frame["top"][i] + data_frame["height"][i]),
                    ),
                    confidence=score,
                    source_type=f"tesseract:p{page_number}",
                ))

        return OCRResult(regions=regions, engine=self.name, version=self.version)

    def _render_pdf_pages(self, data: bytes) -> List[Optional[Any]]:
        """PDF -> page images. Any failure yields None (fail closed)."""
        out: List[Optional[Any]] = []
        try:
            import fitz
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
                    out.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                except Exception:
                    out.append(None)
        finally:
            try:
                doc.close()
            except Exception:
                pass
        return out
