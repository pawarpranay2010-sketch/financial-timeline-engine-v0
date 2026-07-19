"""
Financial Timeline Engine - Ingestion Package
Exports the public ingestion API.
"""

from .parser import (
    SUPPORTED_TYPES,
    detect_file_type,
    parse_txt,
    parse_csv,
    parse_excel,
    parse_docx,
    parse_pdf,
    parse_document,
)
from .chunking import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    chunk_text,
    estimate_tokens,
    chunk_statistics,
    needs_chunking,
)
from .cache import DocumentCache, document_cache
from .statistics import document_statistics, print_statistics
from .extraction import (
    extract_document,
    extract_multiple,
    merge_document_text,
)

__all__ = [
    "SUPPORTED_TYPES",
    "detect_file_type",
    "parse_txt",
    "parse_csv",
    "parse_excel",
    "parse_docx",
    "parse_pdf",
    "parse_document",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_OVERLAP",
    "chunk_text",
    "estimate_tokens",
    "chunk_statistics",
    "needs_chunking",
    "DocumentCache",
    "document_cache",
    "document_statistics",
    "print_statistics",
    "extract_document",
    "extract_multiple",
    "merge_document_text",
]
