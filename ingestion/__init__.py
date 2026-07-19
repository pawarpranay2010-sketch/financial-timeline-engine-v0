"""
Financial Timeline Engine - Ingestion Package
Exports the public ingestion API.
"""

from .parser import extract_text
from .chunking import chunk_text
from .cache import _cached_call
from .statistics import calculate_statistics
from .extraction import (
    extract_document,
    extract_multiple,
    merge_document_text,
)

__all__ = [
    "extract_text",
    "chunk_text",
    "_cached_call",
    "calculate_statistics",
    "extract_document",
    "extract_multiple",
    "merge_document_text",
]
