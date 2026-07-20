"""
=========================================================
Module 3.2 - Corporate Boilerplate Filter
=========================================================

Purpose:
Remove procedural corporate meeting text that adds little
analytical value while preserving finance-relevant content.

Examples Removed:
- Scrutinizers
- Attendance
- Voting procedures
- Welcome speeches
- Meeting formalities
- Poll instructions

Examples Preserved:
- Financial performance
- Strategy
- Guidance
- Risks
- Capital allocation
- Acquisitions
"""

import re
from copy import deepcopy


# -------------------------------------------------------
# Boilerplate patterns
# -------------------------------------------------------

BOILERPLATE_PATTERNS = [

    r"scrutinizer",
    r"scrutinizers",

    r"attendance",

    r"welcome address",

    r"chairman welcomed",

    r"quorum",

    r"e[\-\s]?voting",

    r"polling process",

    r"voting results",

    r"proxy form",

    r"notice of meeting",

    r"ordinary resolution",

    r"special resolution",

    r"appointment of scrutinizer",

    r"authorized representative",

    r"members present",

    r"meeting commenced",

    r"meeting concluded",

    r"vote of thanks",

    r"company secretary informed",

]


# Compile regex once
COMPILED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in BOILERPLATE_PATTERNS
]


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def _contains_boilerplate(text: str) -> bool:

    for pattern in COMPILED_PATTERNS:

        if pattern.search(text):
            return True

    return False


# -------------------------------------------------------
# Text Cleaning
# -------------------------------------------------------

def filter_text(text: str) -> str:
    """
    Remove boilerplate paragraphs from plain text.
    """

    if not isinstance(text, str):
        return text

    paragraphs = text.split("\n")

    cleaned = []

    for para in paragraphs:

        if not para.strip():
            continue

        if _contains_boilerplate(para):
            continue

        cleaned.append(para)

    return "\n".join(cleaned)


# -------------------------------------------------------
# Recursive Cleaning
# -------------------------------------------------------

def _recursive_filter(data):

    if isinstance(data, dict):

        result = {}

        for key, value in data.items():

            result[key] = _recursive_filter(value)

        return result

    elif isinstance(data, list):

        cleaned = []

        for item in data:

            filtered = _recursive_filter(item)

            if filtered is None:
                continue

            cleaned.append(filtered)

        return cleaned

    elif isinstance(data, str):

        filtered = filter_text(data)

        if filtered.strip() == "":
            return None

        return filtered

    else:
        return data


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def remove_boilerplate(module3_result: dict):
    """
    Remove procedural content from Module 3 output.
    """

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    data = deepcopy(module3_result)

    data = _recursive_filter(data)

    return data


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "memo": """
Chairman welcomed all members.

Revenue increased by 14%.

Scrutinizer was appointed.

Management expects loan growth to remain strong.

Vote of thanks was proposed.
""",

        "events": [
            "Appointment of Scrutinizer",
            "Revenue increased 14%",
            "E-voting concluded"
        ]
    }

    cleaned = remove_boilerplate(sample)

    from pprint import pprint

    pprint(cleaned)
