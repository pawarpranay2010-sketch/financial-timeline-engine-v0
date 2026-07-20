"""
=========================================================
Module 3.1 - Placeholder Cleaner
=========================================================

Purpose:
Clean Module 3 outputs before they reach the AI or UI.

Responsibilities:
- Remove placeholder values
- Replace invalid values with readable text
- Remove empty dictionaries/lists
- Prevent ugly outputs such as:
    YYYY-MM
    None
    N/A
    Unknown
    []
    {}

Author:
Finance Intelligence Engine
"""

from copy import deepcopy
import re


# -------------------------------------------------------
# Configuration
# -------------------------------------------------------

PLACEHOLDER_STRINGS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "unknown",
    "not available",
    "not found",
    "yyyy-mm",
    "yyyy",
    "-",
}


DEFAULT_MESSAGE = "Not disclosed in uploaded document."


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def _is_placeholder(value):
    """
    Check whether a value is a placeholder.
    """

    if value is None:
        return True

    if isinstance(value, str):
        text = value.strip().lower()

        if text in PLACEHOLDER_STRINGS:
            return True

        # YYYY-MM
        if re.fullmatch(r"\d{4}-\d{2}", text):
            return True

        # YYYY/MM
        if re.fullmatch(r"\d{4}/\d{2}", text):
            return True

        # YYYY
        if re.fullmatch(r"\d{4}", text):
            return False

    return False


# -------------------------------------------------------
# Recursive Cleaner
# -------------------------------------------------------

def _clean(data):
    """
    Recursively clean any object.
    """

    # Dictionary
    if isinstance(data, dict):

        cleaned = {}

        for key, value in data.items():

            value = _clean(value)

            # Ignore empty dict/list
            if value == {}:
                continue

            if value == []:
                continue

            cleaned[key] = value

        return cleaned

    # List
    elif isinstance(data, list):

        cleaned = []

        for item in data:

            item = _clean(item)

            if item == {}:
                continue

            if item == []:
                continue

            cleaned.append(item)

        return cleaned

    # Primitive
    else:

        if _is_placeholder(data):
            return DEFAULT_MESSAGE

        return data


# -------------------------------------------------------
# Public Function
# -------------------------------------------------------

def clean_module3_output(module3_result):
    """
    Clean Module 3 result.

    Parameters
    ----------
    module3_result : dict

    Returns
    -------
    dict
        Cleaned result.
    """

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    result = _clean(result)

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "financial_data": {
            "Revenue": None,
            "PAT": "Unknown",
            "Year": "YYYY-MM",
            "EPS": 12.6,
        },

        "timeline": [

            {
                "date": "YYYY-MM",
                "event": "AGM"
            },

            {
                "date": "2025",
                "event": "IPO"
            }

        ],

        "ratios": {},

        "events": [],

    }

    cleaned = clean_module3_output(sample)

    from pprint import pprint

    pprint(cleaned)
