"""
=========================================================
Module 3.6 - Timeline Engine V2
=========================================================

Purpose:
Upgrade the existing timeline by enriching every event
with metadata useful for institutional users.

Each event contains

- Date
- Event
- Source
- Confidence
- Category
- Importance
- Temporal Tag
    Historical
    Current
    Future

Author:
Finance Intelligence Engine
"""

from copy import deepcopy
from datetime import datetime


# -------------------------------------------------------
# Configuration
# -------------------------------------------------------

CURRENT_YEAR = datetime.now().year


HIGH_IMPORTANCE = [

    "ipo",
    "acquisition",
    "merger",
    "earnings",
    "results",
    "guidance",
    "dividend",
    "buyback",
    "rights issue",
    "bonus",

]

MEDIUM_IMPORTANCE = [

    "agm",
    "conference",
    "board meeting",
    "investor meeting",

]


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def _determine_importance(event_name):

    if not event_name:
        return "Low"

    text = event_name.lower()

    for word in HIGH_IMPORTANCE:

        if word in text:
            return "High"

    for word in MEDIUM_IMPORTANCE:

        if word in text:
            return "Medium"

    return "Low"


def _temporal_tag(date_value):

    if not date_value:
        return "Unknown"

    try:

        year = int(str(date_value)[:4])

    except Exception:

        return "Unknown"

    if year < CURRENT_YEAR:
        return "Historical"

    elif year == CURRENT_YEAR:
        return "Current"

    else:
        return "Future"


def _event_category(event_name):

    if not event_name:
        return "General"

    text = event_name.lower()

    if "ipo" in text:
        return "IPO"

    if "earnings" in text or "results" in text:
        return "Financial"

    if "dividend" in text:
        return "Corporate Action"

    if "acquisition" in text:
        return "M&A"

    if "guidance" in text:
        return "Management"

    if "agm" in text:
        return "Governance"

    return "General"


# -------------------------------------------------------
# Timeline Builder
# -------------------------------------------------------

def enrich_timeline(timeline):

    if not isinstance(timeline, list):
        return []

    enriched = []

    for event in timeline:

        if not isinstance(event, dict):
            continue

        enriched_event = {

            "date":
                event.get("date"),

            "event":
                event.get("event"),

            "source":
                event.get("source", "Uploaded Document"),

            "confidence":
                event.get("confidence", 95.0),

            "importance":
                _determine_importance(
                    event.get("event")
                ),

            "category":
                _event_category(
                    event.get("event")
                ),

            "temporal_tag":
                _temporal_tag(
                    event.get("date")
                )

        }

        enriched.append(enriched_event)

    return enriched


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def generate_timeline_v2(module3_result):

    """
    Replace old timeline
    with enriched institutional timeline.
    """

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    timeline = result.get("timeline", [])

    result["timeline"] = enrich_timeline(timeline)

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "timeline": [

            {
                "date": "2023-07-15",
                "event": "AGM"
            },

            {
                "date": "2025-07-02",
                "event": "IPO Listing"
            },

            {
                "date": "2027-01-01",
                "event": "Expansion Plan"
            }

        ]

    }

    from pprint import pprint

    pprint(generate_timeline_v2(sample))
