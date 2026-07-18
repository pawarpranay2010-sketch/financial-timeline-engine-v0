"""
Financial Timeline Engine
Module 2 - Document Cache

Prevents reprocessing identical files.
"""

from __future__ import annotations

import hashlib
from datetime import datetime


class DocumentCache:
    """
    Simple in-memory cache.

    Stores processed document outputs
    using SHA-256 hashes.
    """

    def __init__(self):
        self._cache = {}

    def fingerprint(self, uploaded_file) -> str:
        """
        Generates a unique fingerprint
        from file bytes.
        """

        uploaded_file.seek(0)

        data = uploaded_file.read()

        uploaded_file.seek(0)

        return hashlib.sha256(data).hexdigest()

    def exists(self, fingerprint: str) -> bool:
        return fingerprint in self._cache

    def get(self, fingerprint: str):
        return self._cache.get(fingerprint)

    def save(
        self,
        fingerprint: str,
        parsed_document: dict,
    ) -> None:

        self._cache[fingerprint] = {
            "timestamp": datetime.utcnow(),
            "data": parsed_document,
        }

    def clear(self):
        self._cache.clear()

    def size(self):
        return len(self._cache)

    def statistics(self):

        return {
            "cached_documents": len(self._cache),
            "hashes": list(self._cache.keys()),
        }


# Global singleton

document_cache = DocumentCache()
