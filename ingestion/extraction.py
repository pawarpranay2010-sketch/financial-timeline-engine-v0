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

    Runs extract_document() over every uploaded file and tags each
    result with its source file_name (parse_document()/extract_document()
    never see the original filename once parsing is done, but downstream
    consumers -- statistics, merge_document_text, and the AI summarization
    pipeline in app.py -- need it to label/attribute each document), so
    every existing per-file feature (file-by-file summaries, per-file
    labels in exports, etc.) keeps working unchanged.

    Returns:
        List[dict]: one entry per input file, each shaped exactly like
        extract_document()'s return value, plus a "file_name" key:
        {
            "file_name": str,
            "cached": bool,
            "parsed_document": dict,
            "chunks": list[str],
            "statistics": dict,
        }
    """

    results = []

    for uploaded_file in files:

        result = extract_document(uploaded_file)

        result["file_name"] = getattr(uploaded_file, "name", "Unknown Document")

        results.append(result)

    return results


def merge_document_text(results):
    """
    Combines the raw extracted text of multiple documents (as returned by
    extract_multiple()) into a single delimited text blob.

    This mirrors the app's previous combined-text behavior (each source
    file clearly marked with a "--- Start of File: <name> ---" header) so
    that anything downstream expecting one combined raw-text string --
    e.g. the "Extracted Characters" metric, or feeding the full corpus to
    a single-pass process -- keeps working unchanged.

    NOTE: this merges raw extracted *text*, not AI-generated summaries.
    Merging per-document AI summaries into a master summary remains the
    job of merge_document_summaries()/_merge_summary_batch() in app.py,
    which is unaffected by and unrelated to this function.

    Args:
        results: the list returned by extract_multiple().

    Returns:
        str: the combined, per-file-delimited raw text. Returns an empty
        string if `results` is empty/falsy.
    """

    if not results:
        return ""

    merged_sections = []

    for result in results:

        file_name = result.get("file_name", "Unknown Document")

        parsed_document = result.get("parsed_document") or {}

        text = parsed_document.get("text", "")

        merged_sections.append(
            f"\n--- Start of File: {file_name} ---\n{text}"
        )

    return "\n".join(merged_sections)
