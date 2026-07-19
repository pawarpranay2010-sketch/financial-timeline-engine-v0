"""
Financial Timeline Engine
Module 3

Ratio Detector

Purpose
-------
Detect ratios already present in the document.

Never calculate duplicate ratios.

Priority

1. Document Ratio
2. Calculated Ratio
"""

from __future__ import annotations

import re
from typing import Dict, Any


class RatioDetector:

    def __init__(self):

        self.patterns = {

            "ROE": r"(ROE|Return on Equity)\D+([\d\.]+)%",

            "ROA": r"(ROA|Return on Assets)\D+([\d\.]+)%",

            "Profit Margin": r"(Profit Margin|Net Margin)\D+([\d\.]+)%",

            "Debt to Equity": r"(Debt[\s\-\/]*to[\s\-\/]*Equity)\D+([\d\.]+)",

            "Current Ratio": r"(Current Ratio)\D+([\d\.]+)",

            "Quick Ratio": r"(Quick Ratio)\D+([\d\.]+)",

            "Operating Margin": r"(Operating Margin)\D+([\d\.]+)%",

            "EBITDA Margin": r"(EBITDA Margin)\D+([\d\.]+)%"

        }

    # -----------------------------------------------------

    def detect(self, text: str) -> Dict[str, Any]:

        detected = {}

        for ratio, pattern in self.patterns.items():

            match = re.search(pattern, text, re.IGNORECASE)

            if match:

                try:

                    value = float(match.group(2))

                except Exception:

                    value = match.group(2)

                detected[ratio] = {

                    "value": value,

                    "source": "Document"

                }

        return detected


# -----------------------------------------------------------

def merge_ratios(
    document_ratios: Dict[str, Any],
    calculated_ratios: Dict[str, Any]
) -> Dict[str, Any]:

    """
    Merge ratios.

    Document ratios always win.

    Missing ratios come from Python.
    """

    final = {}

    # Document first

    final.update(document_ratios)

    # Add calculated only if missing

    for ratio, value in calculated_ratios.items():

        if ratio not in final:

            final[ratio] = value

    return final


# -----------------------------------------------------------

def detect_document_ratios(text: str):

    detector = RatioDetector()

    return detector.detect(text)
