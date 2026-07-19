"""
Financial Timeline Engine
Module 3

Timeline Builder

Purpose
-------
Convert extracted business events into
a chronological timeline.

Input:
Event List

Output:
Sorted Timeline
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Dict


class TimelineBuilder:

    def __init__(self):
        pass

    # -------------------------------------------------

    def build(self, events: List[Dict]) -> List[Dict]:

        timeline = []

        for event in events:

            date = self._extract_date(
                event["description"]
            )

            timeline.append({

                "date": date,

                "event": event["event"],

                "description": event["description"],

                "confidence": event.get("confidence", 100)

            })

        timeline.sort(
            key=lambda x: x["date"]
        )

        return timeline

    # -------------------------------------------------

    def _extract_date(self, text: str):

        patterns = [

            r"\d{1,2}/\d{1,2}/\d{4}",

            r"\d{4}-\d{2}-\d{2}",

            r"\d{4}"

        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text
            )

            if match:

                value = match.group()

                try:

                    if len(value) == 4:

                        return datetime(
                            int(value),
                            1,
                            1
                        )

                    if "/" in value:

                        return datetime.strptime(
                            value,
                            "%d/%m/%Y"
                        )

                    return datetime.strptime(
                        value,
                        "%Y-%m-%d"
                    )

                except Exception:

                    pass

        return datetime.max


# ------------------------------------------------------


def build_timeline(events):

    builder = TimelineBuilder()

    return builder.build(events)
