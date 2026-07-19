"""
Financial Timeline Engine
Module 3

Event Extraction Engine

Purpose
-------
Extract meaningful business events from
financial documents.

Does NOT extract financial numbers.

Output:
Structured event list.
"""

from __future__ import annotations

import re
from typing import List, Dict


class EventExtractor:

    def __init__(self):

        self.event_patterns = {

            "Dividend": r"dividend",

            "Acquisition": r"acquisition|acquired|acquire",

            "Merger": r"merger|merged",

            "CEO Change": r"appointed.*CEO|new CEO|CEO resigned",

            "Auditor Change": r"auditor",

            "Debt Issue": r"debt issue|issued debt|borrowed",

            "Share Buyback": r"buyback|repurchase",

            "Stock Split": r"stock split|share split",

            "Fund Raising": r"rights issue|QIP|raised capital",

            "Legal Case": r"lawsuit|litigation|legal proceeding",

            "Partnership": r"partnership|strategic alliance",

            "Expansion": r"expansion|new plant|new facility"

        }

    # -------------------------------------------------

    def extract(self, text: str) -> List[Dict]:

        events = []

        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:

            for event_type, pattern in self.event_patterns.items():

                if re.search(pattern, sentence, re.IGNORECASE):

                    events.append({

                        "event": event_type,

                        "description": sentence.strip(),

                        "confidence": 90

                    })

        return events


# ---------------------------------------------------------


def extract_events(text: str):

    extractor = EventExtractor()

    return extractor.extract(text)
