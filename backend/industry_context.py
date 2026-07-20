"""
=========================================================
Module 3.3 - Industry Context Engine
=========================================================

Purpose:
Understand industry-specific practices so that normal
business operations are not incorrectly classified as risks.

Examples:
- 30-minute e-voting -> Standard Governance Practice
- Floating Provision -> Banking Practice
- Basel III Capital -> Banking Regulation
- Solvency Ratio -> Insurance Regulation

Supported Industries:
- Banking
- NBFC
- Insurance
- Manufacturing
- Technology
- Pharma (basic)
"""

from copy import deepcopy
import re


# -------------------------------------------------------
# Industry Knowledge Base
# -------------------------------------------------------

INDUSTRY_RULES = {

    "banking": {

        "standard_practices": [

            r"e[\-\s]?voting",
            r"floating provision",
            r"basel\s?iii",
            r"tier\s?1",
            r"capital adequacy",
            r"casa",
            r"gross npa",
            r"net npa",
            r"slr",
            r"crr",
            r"priority sector lending",

        ],

        "ignore_as_risk": [

            "e-voting",
            "floating provision",
            "basel iii",
            "tier 1",

        ]

    },

    "insurance": {

        "standard_practices": [

            r"solvency ratio",
            r"claim settlement",
            r"embedded value",
            r"persistency ratio",

        ]

    },

    "manufacturing": {

        "standard_practices": [

            r"capacity utilization",
            r"plant shutdown",
            r"maintenance shutdown",
            r"inventory turnover",

        ]

    },

    "technology": {

        "standard_practices": [

            r"cloud migration",
            r"saas",
            r"annual recurring revenue",
            r"customer churn",

        ]

    },

    "pharma": {

        "standard_practices": [

            r"usfda",
            r"clinical trial",
            r"drug approval",

        ]

    }

}


# -------------------------------------------------------
# Pattern Builder
# -------------------------------------------------------

COMPILED = {}

for industry, rules in INDUSTRY_RULES.items():

    COMPILED[industry] = []

    for pattern in rules["standard_practices"]:

        COMPILED[industry].append(
            re.compile(pattern, re.IGNORECASE)
        )


# -------------------------------------------------------
# Industry Detection
# -------------------------------------------------------

def detect_industry(text: str):

    if not isinstance(text, str):
        return "general"

    scores = {}

    for industry, patterns in COMPILED.items():

        count = 0

        for p in patterns:

            if p.search(text):
                count += 1

        scores[industry] = count

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return "general"

    return best


# -------------------------------------------------------
# Context Classification
# -------------------------------------------------------

def classify_context(text: str):

    industry = detect_industry(text)

    if industry == "general":

        return {
            "industry": "General",
            "is_standard_practice": False,
            "explanation": None
        }

    for pattern in COMPILED[industry]:

        if pattern.search(text):

            return {

                "industry": industry.title(),

                "is_standard_practice": True,

                "explanation":
                "Recognized as a normal industry practice. "
                "Should not automatically be treated as a risk."

            }

    return {

        "industry": industry.title(),

        "is_standard_practice": False,

        "explanation": None

    }


# -------------------------------------------------------
# Recursive Context Enhancement
# -------------------------------------------------------

def _enhance(data):

    if isinstance(data, dict):

        result = {}

        for k, v in data.items():

            result[k] = _enhance(v)

        return result

    elif isinstance(data, list):

        return [_enhance(x) for x in data]

    elif isinstance(data, str):

        context = classify_context(data)

        return {

            "text": data,

            "industry_context": context

        }

    else:

        return data


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def apply_industry_context(module3_result: dict):

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    result = _enhance(result)

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "management_commentary":

        "The company completed a 30 minute e-voting process "
        "and continues to maintain strong CASA ratio while "
        "following Basel III capital requirements."

    }

    from pprint import pprint

    pprint(apply_industry_context(sample))
