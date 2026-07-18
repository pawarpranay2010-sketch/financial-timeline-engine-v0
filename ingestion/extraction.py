"""
Financial Timeline Engine
Module 2 - Document Extraction Pipeline

Coordinates:
- File Parsing
- Cache
- Chunking
- Statistics
"""

from __future__ import annotations

from ingestion.parser import parse_document
from ingestion.chunking import (
    chunk_text,
    chunk_statistics,
    needs_chunking,
)
from ingestion.cache import document_cache


def extract_document(uploaded_file):
    """
    Complete ingestion pipeline.

    Returns:
    {
        parsed_document,
        chunks,
        statistics
    }
    """

    fingerprint = document_cache.fingerprint(uploaded_file)

    # -------------------------
    # Cache hit
    # -------------------------

    if document_cache.exists(fingerprint):

        cached = document_cache.get(fingerprint)

        return {
            "cached": True,
            "parsed_document": cached["data"],
            "chunks": chunk_text(
                cached["data"]["text"]
            ),
            "statistics": chunk_statistics(
                chunk_text(
                    cached["data"]["text"]
                )
            ),
        }

    # -------------------------
    # Parse
    # -------------------------

    parsed = parse_document(uploaded_file)

    # -------------------------
    # Chunk
    # -------------------------

    if needs_chunking(parsed["text"]):

        chunks = chunk_text(parsed["text"])

    else:

        chunks = [parsed["text"]]

    # -------------------------
    # Stats
    # -------------------------

    stats = chunk_statistics(chunks)

    stats.update(
        {
            "pages": parsed["pages"],
            "tables": parsed["tables"],
            "document_type": parsed["type"],
            "cached": False,
        }
    )

    # -------------------------
    # Save Cache
    # -------------------------

    document_cache.save(
        fingerprint,
        parsed,
    )

    return {
        "cached": False,
        "parsed_document": parsed,
        "chunks": chunks,
        "statistics": stats,
    }


def extract_multiple(files):
    """
    Batch extraction.

    Returns list of extracted documents.
    """

    results = []
