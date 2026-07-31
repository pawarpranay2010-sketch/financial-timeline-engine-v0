"""
Financial Timeline Engine
Extraction 2.0 - Document Type Detector

Deterministically classifies an uploaded document so the extraction
pipeline can choose the correct strategy priority:

    SEC_XBRL       -> XBRL structured extraction (highest)
    SEC_HTML       -> Inline XBRL / HTML table extraction
    PDF            -> PDF table + layout-aware text extraction
    PDF_SCANNED    -> OCR required (no reliable text)
    DOCX           -> native table extraction (python-docx)
    XLSX           -> native table extraction (pandas/openpyxl)
    CSV            -> native table extraction (pandas)
    TXT            -> layout-aware text / contextual regex
    UNKNOWN        -> fail closed with no extraction strategy

Detection is based on extension + magic-byte/content sniffing only.
No heuristic guessing about the *content* of the document.
"""

from __future__ import annotations

import os
import re
import zipfile
from typing import Optional

# ---------------------------------------------------------------------------
# Document type constants
# ---------------------------------------------------------------------------

DOC_TYPE_SEC_XBRL = "SEC_XBRL"
DOC_TYPE_SEC_HTML = "SEC_HTML"
DOC_TYPE_PDF = "PDF"
DOC_TYPE_PDF_SCANNED = "PDF_SCANNED"
DOC_TYPE_DOCX = "DOCX"
DOC_TYPE_XLSX = "XLSX"
DOC_TYPE_CSV = "CSV"
DOC_TYPE_TXT = "TXT"
DOC_TYPE_HTML = "HTML"
DOC_TYPE_UNKNOWN = "UNKNOWN"

_ALL_TYPES = [
    DOC_TYPE_SEC_XBRL,
    DOC_TYPE_SEC_HTML,
    DOC_TYPE_PDF,
    DOC_TYPE_PDF_SCANNED,
    DOC_TYPE_DOCX,
    DOC_TYPE_XLSX,
    DOC_TYPE_CSV,
    DOC_TYPE_TXT,
    DOC_TYPE_HTML,
    DOC_TYPE_UNKNOWN,
]

_XBRL_MARKERS = [
    b"<xbrl",
    b":xbrl",
    b"www.xbrl.org",
    b"http://www.xbrl.org",
]

_INLINE_XBRL_MARKERS = [
    b"ix:nonfraction",
    b"ix:nonnumeric",
    b"http://www.xbrl.org/2013/inlinexbrl",
    b"inlinexbrl",
]


class DocumentTypeDetector:
    """
    Deterministic document classifier.

    Usage:
        detector = DocumentTypeDetector()
        doc_type = detector.detect(file_name="report.html", content=bytes)
        strategy = detector.preferred_strategy(doc_type)
    """

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def detect(
        self,
        file_name: Optional[str] = None,
        content: Optional[bytes] = None,
        text: Optional[str] = None,
        parsed: Optional[dict] = None,
    ) -> str:
        """
        Classify the document.

        Priority:
          1. `parsed` dict from ingestion.parser (already has `type` + text)
          2. magic-byte / content sniffing
          3. extension fallback
        """

        # If we already have a parsed document, use its type directly
        if parsed is not None:
            parsed_type = parsed.get("type")
            if parsed_type == "pdf":
                return self._classify_pdf(parsed)
            if parsed_type == "html":
                return self._classify_html(parsed)
            if parsed_type in ("txt", "text"):
                return DOC_TYPE_TXT
            if parsed_type in ("docx", "xlsx", "csv"):
                return parsed_type.upper()
            if parsed_type == "xbrl":
                return DOC_TYPE_SEC_XBRL

        # Content sniffing first (most reliable)
        if content is not None:
            sniffed = self._sniff(content)
            if sniffed != DOC_TYPE_UNKNOWN:
                return sniffed

        # Then extension-based fallback
        if file_name:
            ext = os.path.splitext(str(file_name).lower())[1]
            return self._from_extension(ext, text=text)

        return DOC_TYPE_UNKNOWN

    # ------------------------------------------------------------------
    # Content sniffing
    # ------------------------------------------------------------------

    def _sniff(self, content: bytes) -> str:
        head = content[:4096].lower()

        # PDF magic
        if content.startswith(b"%PDF"):
            return DOC_TYPE_PDF

        # ZIP-based office formats (DOCX / XLSX)
        if content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06"):
            try:
                with zipfile.ZipFile(__import__("io").BytesIO(content)) as zf:
                    names = zf.namelist()
            except Exception:
                return DOC_TYPE_UNKNOWN
            if any(n.startswith("word/") for n in names):
                return DOC_TYPE_DOCX
            if any(n.startswith("xl/") for n in names):
                return DOC_TYPE_XLSX
            return DOC_TYPE_UNKNOWN

        # XBRL / Inline XBRL (usually inside HTML)
        if any(m in head for m in _XBRL_MARKERS):
            return DOC_TYPE_SEC_XBRL
        if any(m in head for m in _INLINE_XBRL_MARKERS):
            return DOC_TYPE_SEC_HTML

        # HTML
        stripped = head.lstrip()
        if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
            return DOC_TYPE_HTML

        # CSV: lines with consistent comma counts
        if self._looks_like_csv(content):
            return DOC_TYPE_CSV

        # Plain text
        if self._looks_like_text(content):
            return DOC_TYPE_TXT

        return DOC_TYPE_UNKNOWN

    @staticmethod
    def _looks_like_csv(content: bytes) -> bool:
        try:
            head = content[:8192].decode("utf-8", errors="ignore")
        except Exception:
            return False
        lines = [ln for ln in head.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        counts = {ln.count(",") for ln in lines[:10]}
        # Consistent comma structure across rows => tabular CSV
        return len(counts) == 1 and list(counts)[0] > 0

    @staticmethod
    def _looks_like_text(content: bytes) -> bool:
        if not content:
            return False
        # Reject binary-ish content
        sample = content[:8192]
        printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126)
        ratio = printable / max(1, len(sample))
        return ratio > 0.85

    # ------------------------------------------------------------------
    # Extension fallback
    # ------------------------------------------------------------------

    def _from_extension(self, ext: str, text: Optional[str] = None) -> str:
        ext = ext.lower()
        if ext in (".xbrl", ".xml"):
            # XML could be XBRL instance or taxonomy
            if text and ("xbrl" in text.lower()):
                return DOC_TYPE_SEC_XBRL
            return DOC_TYPE_UNKNOWN
        if ext in (".html", ".htm"):
            if text and "xbrl" in text.lower():
                return DOC_TYPE_SEC_HTML
            return DOC_TYPE_HTML
        if ext == ".pdf":
            return DOC_TYPE_PDF
        if ext == ".docx":
            return DOC_TYPE_DOCX
        if ext in (".xlsx", ".xls"):
            return DOC_TYPE_XLSX
        if ext == ".csv":
            return DOC_TYPE_CSV
        if ext in (".txt", ".text", ".md"):
            return DOC_TYPE_TXT
        return DOC_TYPE_UNKNOWN

    # ------------------------------------------------------------------
    # PDF / HTML refinement
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_pdf(parsed: dict) -> str:
        """
        A PDF is classified PDF_SCANNED when it has almost no extractable
        text relative to its page count (i.e. OCR would be required).
        """
        pages = int(parsed.get("pages", 0) or 0)
        text_len = len(parsed.get("text", "") or "")
        if pages >= 2 and text_len < pages * 50:
            return DOC_TYPE_PDF_SCANNED
        return DOC_TYPE_PDF

    @staticmethod
    def _classify_html(parsed: dict) -> str:
        text = parsed.get("text", "") or ""
        if "xbrl" in text.lower():
            return DOC_TYPE_SEC_HTML
        return DOC_TYPE_HTML

    # ------------------------------------------------------------------
    # Strategy mapping
    # ------------------------------------------------------------------

    def preferred_strategy(self, doc_type: str) -> str:
        """
        Return the highest-priority extraction strategy for a doc type.
        """
        strategy_map = {
            DOC_TYPE_SEC_XBRL: "XBRL",
            DOC_TYPE_SEC_HTML: "XBRL_AND_TABLES",
            DOC_TYPE_PDF: "TABLES_AND_TEXT",
            DOC_TYPE_PDF_SCANNED: "OCR_REQUIRED",
            DOC_TYPE_DOCX: "NATIVE_TABLES",
            DOC_TYPE_XLSX: "NATIVE_TABLES",
            DOC_TYPE_CSV: "NATIVE_TABLES",
            DOC_TYPE_TXT: "TEXT",
            DOC_TYPE_HTML: "TABLES_AND_TEXT",
            DOC_TYPE_UNKNOWN: "NONE",
        }
        return strategy_map.get(doc_type, "NONE")


def detect_document_type(
    file_name: Optional[str] = None,
    content: Optional[bytes] = None,
    text: Optional[str] = None,
    parsed: Optional[dict] = None,
) -> str:
    """Module-level convenience wrapper."""
    return DocumentTypeDetector().detect(
        file_name=file_name,
        content=content,
        text=text,
        parsed=parsed,
    )
