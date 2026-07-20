"""
=========================================================
Module 3.9 - Quality Score Engine
=========================================================

Purpose:
Evaluate the overall quality of Module 3 output.

This is NOT AI confidence.

It measures

- Extraction completeness
- Verification quality
- Timeline quality
- Ratio quality
- OCR quality
- Missing information

Output

quality_score
quality_breakdown
quality_status

Author:
Finance Intelligence Engine
"""

from copy import deepcopy


# -------------------------------------------------------
# Weight Configuration
# -------------------------------------------------------

WEIGHTS = {

    "financial_data": 20,

    "ratios": 20,

    "ocr": 15,

    "cross_document": 15,

    "timeline": 10,

    "events": 10,

    "missing_data": 10

}


# -------------------------------------------------------
# Utility
# -------------------------------------------------------

def _exists(value):

    if value is None:
        return False

    if isinstance(value, dict):
        return len(value) > 0

    if isinstance(value, list):
        return len(value) > 0

    if isinstance(value, str):
        return value.strip() != ""

    return True


# -------------------------------------------------------
# Score Calculation
# -------------------------------------------------------

def calculate_quality(module3_result):

    score = 0

    breakdown = {}

    # ---------------------------------------------

    if _exists(module3_result.get("financial_data")):

        score += WEIGHTS["financial_data"]

        breakdown["Financial Data"] = 100

    else:

        breakdown["Financial Data"] = 0

    # ---------------------------------------------

    if _exists(module3_result.get("ratios")):

        score += WEIGHTS["ratios"]

        breakdown["Ratios"] = 100

    else:

        breakdown["Ratios"] = 0

    # ---------------------------------------------

    if _exists(module3_result.get("ocr_verification")):

        score += WEIGHTS["ocr"]

        breakdown["OCR"] = 100

    else:

        breakdown["OCR"] = 0

    # ---------------------------------------------

    if _exists(module3_result.get("cross_document_verification")):

        score += WEIGHTS["cross_document"]

        breakdown["Cross Document"] = 100

    else:

        breakdown["Cross Document"] = 0

    # ---------------------------------------------

    if _exists(module3_result.get("timeline")):

        score += WEIGHTS["timeline"]

        breakdown["Timeline"] = 100

    else:

        breakdown["Timeline"] = 0

    # ---------------------------------------------

    if _exists(module3_result.get("events")):

        score += WEIGHTS["events"]

        breakdown["Events"] = 100

    else:

        breakdown["Events"] = 0

    # ---------------------------------------------
    # Missing Data Penalty
    # ---------------------------------------------

    penalty = 0

    missing = module3_result.get("missing_data")

    if isinstance(missing, dict):

        summary = missing.get("summary", {})

        count = summary.get("missing_items", 0)

        penalty = min(count, 10)

    score -= penalty

    score = max(0, min(score, 100))

    breakdown["Missing Data Penalty"] = penalty

    # ---------------------------------------------

    if score >= 90:

        status = "Excellent"

    elif score >= 75:

        status = "Good"

    elif score >= 60:

        status = "Fair"

    else:

        status = "Poor"

    return {

        "score": round(score, 2),

        "status": status,

        "breakdown": breakdown

    }


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def generate_quality_score(module3_result):

    if not isinstance(module3_result, dict):

        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    result["quality_score"] = calculate_quality(result)

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "financial_data": {
            "Revenue": 1000
        },

        "ratios": {
            "ROE": 18
        },

        "ocr_verification": {
            "confidence": 98
        },

        "cross_document_verification": {
            "confidence": 97
        },

        "timeline": [
            {
                "event": "IPO"
            }
        ],

        "events": [
            {
                "event": "IPO"
            }
        ],

        "missing_data": {

            "summary": {

                "missing_items": 2

            }

        }

    }

    from pprint import pprint

    pprint(
        generate_quality_score(sample)
    )
