"""
Financial Timeline Engine
Module 3

Duplicate Intelligence Filter

Purpose
-------
Remove duplicate financial facts before
sending data to AI.

This reduces:

- Token usage
- Repeated reasoning
- Prompt size
"""

from __future__ import annotations

from typing import Dict, Any


class DuplicateFilter:

    def __init__(self):
        pass

    # ----------------------------------------------------

    def remove_duplicates(
        self,
        structured_data: Dict[str, Any]
    ) -> Dict[str, Any]:

        cleaned = {}

        seen = set()

        for key, value in structured_data.items():

            fingerprint = (
                key,
                str(value.get("value"))
            )

            if fingerprint in seen:
                continue

            seen.add(fingerprint)

            cleaned[key] = value

        return cleaned


# -----------------------------------------------------


def filter_duplicate_information(data):

    engine = DuplicateFilter()

    return engine.remove_duplicates(data)
