"""
Platrixa
Module 2 - Intelligent Document Chunking
"""

from __future__ import annotations

import re
from typing import List


DEFAULT_CHUNK_SIZE = 10000
DEFAULT_OVERLAP = 500

# Marker-delimited table block produced by ingestion.parser
_TABLE_BLOCK_RE = re.compile(
    r"(=== TABLE .*? ===\n.*?=== END TABLE ===)",
    re.DOTALL,
)


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> List[str]:
    """
    Character-based chunking with overlap.
    Preserves ordering and prevents cutting too much context.
    """

    if not text:
        return []

    chunks = []

    start = 0
    length = len(text)

    while start < length:

        end = start + chunk_size

        chunks.append(text[start:end])

        if end >= length:
            break

        start = end - overlap

    return chunks


def chunk_document(
    parsed: dict,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> List[str]:
    """
    Chunk a parsed document while preserving table boundaries.

    Table blocks (marked `=== TABLE ... === ... === END TABLE ===`) are
    treated as atomic units and are NEVER split mid-table. Narrative text
    continues to use normal character-based chunking.

    Documents without marked tables fall back to chunk_text() unchanged.
    """

    text = parsed.get("text", "") or ""

    if "=== TABLE" not in text:
        return chunk_text(text, chunk_size=chunk_size, overlap=overlap)

    parts = _TABLE_BLOCK_RE.split(text)

    chunks: List[str] = []

    for part in parts:

        if not part:
            continue

        if part.startswith("=== TABLE"):
            chunks.append(part)
        else:
            chunks.extend(
                chunk_text(part, chunk_size=chunk_size, overlap=overlap)
            )

    return [c for c in chunks if c.strip()]


def estimate_tokens(text: str) -> int:
    """
    Rough token estimation.
    Average English token ≈ 4 characters.
    """

    if not text:
        return 0

    return max(1, len(text) // 4)


def chunk_statistics(chunks: List[str]) -> dict:
    """
    Returns statistics for debugging and UI.
    """

    total_chars = sum(len(c) for c in chunks)

    total_tokens = sum(estimate_tokens(c) for c in chunks)

    return {
        "chunk_count": len(chunks),
        "characters": total_chars,
        "estimated_tokens": total_tokens,
        "largest_chunk": max((len(c) for c in chunks), default=0),
        "smallest_chunk": min((len(c) for c in chunks), default=0),
        "average_chunk": (
            total_chars // len(chunks)
            if chunks
            else 0
        ),
    }


def needs_chunking(
    text: str,
    threshold: int = DEFAULT_CHUNK_SIZE,
) -> bool:
    """
    Determines whether a document should
    be chunked before AI processing.
    """

    return len(text) > threshold
