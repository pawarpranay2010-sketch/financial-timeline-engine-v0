"""
=========================================================
Module 3.7 - Missing Data Detector
=========================================================

Purpose:
Detect important financial information that is missing
from Module 3 results.

NOTE:
This module DOES NOT fetch data.
It only reports what is missing.

Module 4 will later retrieve the missing information.

Author:
Finance Intelligence Engine
"""

from copy import deepcopy


# -------------------------------------------------------
# Required Fields
# -------------------------------------------------------

REQUIRED_FINANCIAL_FIELDS = [

    "Revenue",
    "Net Profit",
    "PAT",
    "EPS",
    "Total Assets",
    "Total Liabilities",

]

REQUIRED_RATIO_FIELDS = [

    "ROE",
    "ROCE",
    "Debt to Equity",
    "Current Ratio",

]

REQUIRED_EVENT_FIELDS = [

    "AGM",
    "Dividend",
    "Results",
]

OPTIONAL_MARKET_FIELDS = [

    "Market Cap",
    "Share Price",
    "52 Week High",
    "52 Week Low",
    "Ticker",

]


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def _check_dictionary(required_fields, data):

    missing = []

    if not isinstance(data, dict):

        return required_fields.copy()

    for field in required_fields:

        value = data.get(field)

        if value is None:
            missing.append(field)
            continue

        if isinstance(value, str):

            if value.strip() == "":
                missing.append(field)

    return missing


def _check_events(events):

    found = set()

    if not isinstance(events, list):
        return REQUIRED_EVENT_FIELDS.copy()

    for event in events:

        if not isinstance(event, dict):
            continue

        text = str(
            event.get("event", "")
        ).lower()

        for required in REQUIRED_EVENT_FIELDS:

            if required.lower() in text:
                found.add(required)

    return [

        x for x in REQUIRED_EVENT_FIELDS

        if x not in found

    ]


# -------------------------------------------------------
# Detector
# -------------------------------------------------------

def detect_missing_data(module3_result):

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    financial_data = result.get(
        "financial_data", {}
    )

    ratios = result.get(
        "ratios", {}
    )

    events = result.get(
        "events", []
    )

    report = {

        "financial_data":

            _check_dictionary(
                REQUIRED_FINANCIAL_FIELDS,
                financial_data
            ),

        "ratios":

            _check_dictionary(
                REQUIRED_RATIO_FIELDS,
                ratios
            ),

        "events":

            _check_events(events),

        "market_data":

            OPTIONAL_MARKET_FIELDS.copy()

    }

    total_missing = sum(
        len(v)
        for v in report.values()
    )

    report["summary"] = {

        "missing_items":
            total_missing,

        "status":

            "Complete"

            if total_missing == 0

            else "Incomplete"

    }

    result["missing_data"] = report

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "financial_data": {

            "Revenue": 1000,
            "PAT": 200

        },

        "ratios": {

            "ROE": 18

        },

        "events": [

            {

                "event":
                    "Quarterly Results"

            }

        ]

    }

    from pprint import pprint

    pprint(
        detect_missing_data(sample)
    )
