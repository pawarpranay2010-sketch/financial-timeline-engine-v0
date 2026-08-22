"""
Platrixa
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
    chunk_document,
    chunk_text,
    chunk_statistics,
    needs_chunking,
)
from ingestion.cache import document_cache
from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2


# ---------------------------------------------------------------------------
# Extraction 2.0 wiring (additive)
# ---------------------------------------------------------------------------

_financial_extractor_v2 = FinancialExtractorV2()


def extract_financial_facts(parsed):
    """
    Run FinancialExtractorV2 over a parsed document.

    Structured-first: XBRL -> tables -> contextual text -> guarded regex.
    Never raises: any extraction failure returns an empty fact set so the
    existing ingestion pipeline is never blocked by the extraction layer.
    """
    try:
        return _financial_extractor_v2.extract_document(parsed)
    except Exception:
        return {
            "facts": [],
            "stats": {
                "facts_total": 0,
                "facts_unique": 0,
                "duplicates_suppressed": 0,
                "tables_detected": 0,
                "extraction_time_ms": 0.0,
                "document_type": parsed.get("type", ""),
                "error": True,
            },
        }


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

        parsed = cached["data"]

        chunks = chunk_document(parsed)

        facts_result = extract_financial_facts(parsed)

        return {
            "cached": True,
            "parsed_document": parsed,
            "chunks": chunks,
            "statistics": chunk_statistics(chunks),
            "financial_facts": facts_result["facts"],
            "extraction_stats": facts_result["stats"],
        }

    # -------------------------
    # Parse
    # -------------------------

    parsed = parse_document(uploaded_file)

    # -------------------------
    # Chunk (table-boundary preserving)
    # -------------------------

    if needs_chunking(parsed["text"]):

        chunks = chunk_document(parsed)

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

    facts_result = extract_financial_facts(parsed)

    return {
        "cached": False,
        "parsed_document": parsed,
        "chunks": chunks,
        "statistics": stats,
        "financial_facts": facts_result["facts"],
        "extraction_stats": facts_result["stats"],
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
