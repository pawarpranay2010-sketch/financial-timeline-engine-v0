"""
Financial Timeline Engine
Module 2 - Intelligent Document Chunking
"""

from __future__ import annotations

from typing import List


DEFAULT_CHUNK_SIZE = 10000
DEFAULT_OVERLAP = 500


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
