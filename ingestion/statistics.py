"""
Financial Timeline Engine
Module 2 - Document Statistics

Provides detailed analytics for uploaded documents.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Dict


def document_statistics(results: List[Dict]) -> Dict:
    """
    Generate overall statistics for all uploaded documents.
    """

    if not results:
        return {}

    total_documents = len(results)

    total_pages = 0
    total_tables = 0
    total_chunks = 0
    total_characters = 0
    total_tokens = 0

    cached_documents = 0

    file_types = []

    for item in results:

        stats = item["statistics"]

        total_pages += stats.get("pages", 0)
        total_tables += stats.get("tables", 0)
        total_chunks += stats.get("chunk_count", 0)
        total_characters += stats.get("characters", 0)
        total_tokens += stats.get("estimated_tokens", 0)

        if stats.get("cached"):
            cached_documents += 1

        file_types.append(
            stats.get("document_type", "Unknown")
        )

    return {
        "documents": total_documents,
        "pages": total_pages,
        "tables": total_tables,
        "chunks": total_chunks,
        "characters": total_characters,
        "estimated_tokens": total_tokens,
        "cached_documents": cached_documents,
        "average_pages": round(total_pages / total_documents, 2),
        "average_chunks": round(total_chunks / total_documents, 2),
        "average_characters": round(total_characters / total_documents, 2),
        "file_type_distribution": dict(
            Counter(file_types)
        ),
    }


def print_statistics(stats: Dict) -> str:
    """
    Returns formatted statistics for Streamlit.
    """

    if not stats:
        return "No statistics available."

    lines = [
        f"📄 Documents : {stats['documents']}",
        f"📑 Pages : {stats['pages']}",
        f"📊 Tables : {stats['tables']}",
        f"🧩 Chunks : {stats['chunks']}",
        f"🔤 Characters : {stats['characters']}",
        f"🧠 Estimated Tokens : {stats['estimated_tokens']}",
        f"⚡ Cached Documents : {stats['cached_documents']}",
    ]

    return "\n".join(lines)
