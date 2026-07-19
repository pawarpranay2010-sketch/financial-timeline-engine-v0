"""
Financial Timeline Engine
Module 3

Financial Extractor

Purpose:
---------
Extract structured financial facts from
documents before sending anything to the LLM.

The extractor NEVER performs calculations.

It only identifies financial values.
"""

from __future__ import annotations

import re
from typing import Dict, Any


class FinancialExtractor:

    def __init__(self):

        self.patterns = {

            "Revenue": r"(Revenue|Sales|Total Revenue)\D+([\d,\.]+)",

            "Net Profit": r"(Net Profit|Profit After Tax|PAT)\D+([\d,\.]+)",

            "EBITDA": r"(EBITDA)\D+([\d,\.]+)",

            "Operating Profit": r"(Operating Profit|Operating Income)\D+([\d,\.]+)",

            "EPS": r"(EPS|Earnings Per Share)\D+([\d,\.]+)",

            "Debt": r"(Total Debt|Debt)\D+([\d,\.]+)",

            "Assets": r"(Total Assets)\D+([\d,\.]+)",

            "Liabilities": r"(Total Liabilities)\D+([\d,\.]+)",

            "Equity": r"(Shareholders'? Equity|Total Equity)\D+([\d,\.]+)",

            "Cash Flow": r"(Cash Flow from Operations|Operating Cash Flow)\D+([\d,\.]+)",

        }

    # -------------------------------------------------------

    def extract(self, text: str) -> Dict[str, Any]:

        result = {}

        for field, pattern in self.patterns.items():

            match = re.search(pattern, text, re.IGNORECASE)

            if match:

                value = match.group(2)

                value = value.replace(",", "")

                try:

                    value = float(value)

                except Exception:

                    pass

                result[field] = {

                    "value": value,

                    "source": "Document"

                }

        return result


# -----------------------------------------------------------

def extract_financial_data(text: str) -> Dict[str, Any]:

    extractor = FinancialExtractor()

    return extractor.extract(text)
