"""
Financial Timeline Engine
Module 3

OCR Verification Engine

Purpose
-------
Verify extracted financial values.

Detect conflicting values extracted
from OCR across pages/documents.

Never silently overwrite data.
"""

from __future__ import annotations

from typing import Dict, List, Any


class OCRVerifier:

    def __init__(self):
        pass

    # -------------------------------------------------

    def verify(self, extracted_documents: List[Dict[str, Any]]):

        """
        extracted_documents

        [
            {
                "source":"Annual Report",
                "financial_data": {...}
            },
            ...
        ]
        """

        verification = {}

        all_fields = set()

        for doc in extracted_documents:

            all_fields.update(
                doc["financial_data"].keys()
            )

        # -----------------------------------------

        for field in all_fields:

            values = []

            for doc in extracted_documents:

                financial_data = doc["financial_data"]

                if field in financial_data:

                    values.append({

                        "source": doc["source"],

                        "value": financial_data[field]["value"]

                    })

            if len(values) == 1:

                verification[field] = {

                    "status": "Verified",

                    "confidence": 100,

                    "value": values[0]["value"],

                    "sources": values

                }

            else:

                unique_values = {

                    v["value"]

                    for v in values

                }

                if len(unique_values) == 1:

                    verification[field] = {

                        "status": "Verified",

                        "confidence": 100,

                        "value": values[0]["value"],

                        "sources": values

                    }

                else:

                    verification[field] = {

                        "status": "Conflict",

                        "confidence": 0,

                        "value": None,

                        "sources": values

                    }

        return verification


# -------------------------------------------------


def verify_ocr_results(extracted_documents):

    verifier = OCRVerifier()

    return verifier.verify(extracted_documents)
