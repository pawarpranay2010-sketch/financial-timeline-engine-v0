"""
=========================================================
Module 3.4 - Risk Classifier
=========================================================

Purpose:
Convert generic risks into structured institutional risks.

Categories

- Financial Risk
- Strategic Risk
- Governance Risk
- Regulatory Risk
- Operational Risk
- Market Risk

Also integrates Industry Context so normal practices
(e.g. e-voting, Basel III) are NOT flagged as risks.
"""

from copy import deepcopy
import re

from backend.industry_context import classify_context


# -------------------------------------------------------
# Risk Patterns
# -------------------------------------------------------

RISK_PATTERNS = {

    "Financial Risk": [

        r"debt",
        r"default",
        r"loss",
        r"liquidity",
        r"cash flow",
        r"impairment",
        r"write[- ]?off",
        r"provision",
        r"capital erosion",
        r"earnings decline",

    ],

    "Strategic Risk": [

        r"competition",
        r"market share",
        r"expansion",
        r"merger",
        r"acquisition",
        r"growth slowdown",
        r"pricing pressure",
        r"customer concentration",

    ],

    "Governance Risk": [

        r"fraud",
        r"board",
        r"ethics",
        r"conflict of interest",
        r"whistle",
        r"internal control",
        r"corporate governance",

    ],

    "Regulatory Risk": [

        r"sebi",
        r"rbi",
        r"regulation",
        r"compliance",
        r"penalty",
        r"lawsuit",
        r"legal",
        r"tax dispute",
        r"court",

    ],

    "Operational Risk": [

        r"supply chain",
        r"cyber",
        r"system outage",
        r"technology failure",
        r"manufacturing disruption",
        r"labour",
        r"strike",
        r"plant shutdown",

    ],

    "Market Risk": [

        r"inflation",
        r"interest rate",
        r"currency",
        r"commodity",
        r"oil price",
        r"exchange rate",
        r"volatility",
        r"recession",

    ]

}


# -------------------------------------------------------
# Compile
# -------------------------------------------------------

COMPILED = {}

for category, patterns in RISK_PATTERNS.items():

    COMPILED[category] = [

        re.compile(p, re.IGNORECASE)

        for p in patterns

    ]


# -------------------------------------------------------
# Detect Risk
# -------------------------------------------------------

def detect_risk(text: str):

    if not isinstance(text, str):

        return None

    # ---------------------------------------------
    # Industry Context Check
    # ---------------------------------------------

    context = classify_context(text)

    if context["is_standard_practice"]:

        return {

            "category": None,

            "risk_detected": False,

            "reason":
                "Standard industry practice.",

            "industry":
                context["industry"]

        }

    # ---------------------------------------------
    # Risk Detection
    # ---------------------------------------------

    detected = []

    for category, patterns in COMPILED.items():

        for pattern in patterns:

            if pattern.search(text):

                detected.append(category)

                break

    if len(detected) == 0:

        return {

            "category": None,

            "risk_detected": False,

            "reason": None,

            "industry":
                context["industry"]

        }

    return {

        "category": detected,

        "risk_detected": True,

        "reason":
            "Keyword based classification.",

        "industry":
            context["industry"]

    }


# -------------------------------------------------------
# Recursive
# -------------------------------------------------------

def _recursive(data):

    if isinstance(data, dict):

        result = {}

        for k, v in data.items():

            result[k] = _recursive(v)

        return result

    elif isinstance(data, list):

        return [_recursive(x) for x in data]

    elif isinstance(data, str):

        return {

            "text": data,

            "risk": detect_risk(data)

        }

    else:

        return data


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def classify_risks(module3_result: dict):

    """
    Add institutional risk categories to
    Module 3 output.
    """

    if not isinstance(module3_result, dict):

        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    result = _recursive(result)

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "paragraph1":
            "Company faces liquidity pressure due to debt.",

        "paragraph2":
            "30 minute e-voting was completed.",

        "paragraph3":
            "SEBI has initiated regulatory review.",

        "paragraph4":
            "Inflation may impact margins."

    }

    from pprint import pprint

    pprint(classify_risks(sample))
