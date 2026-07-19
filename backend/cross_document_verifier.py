"""
Financial Timeline Engine
Module 3

Cross Document Verification Engine

Purpose
-------
Compare multiple financial documents.

Detect

- matching values
- conflicting values
- missing values

Output

Verified Financial Truth
"""

from __future__ import annotations

from typing import Dict, List, Any


class CrossDocumentVerifier:

    def __init__(self):
        pass

    # --------------------------------------------------

    def verify(self, documents: List[Dict[str, Any]]):

        verification = {}

        fields = set()

        for doc in documents:

            fields.update(
                doc["financial_data"].keys()
            )

        # ---------------------------------------

        for field in fields:

            collected = []

            for doc in documents:

                if field in doc["financial_data"]:

                    collected.append({

                        "document": doc["document_name"],

                        "value": doc["financial_data"][field]["value"],

                        "source": doc["financial_data"][field]["source"]

                    })

            verification[field] = self._analyse(collected)

        return verification

    # --------------------------------------------------

    def _analyse(self, values):

        if len(values) == 0:

            return {

                "status": "Missing",

                "confidence": 0,

                "verified_value": None,

                "documents": []

            }

        unique = {

            item["value"]

            for item in values

        }

        # -----------------------------------

        if len(unique) == 1:

            return {

                "status": "Verified",

                "confidence": 100,

                "verified_value": values[0]["value"],

                "documents": values

            }

        # -----------------------------------

        return {

            "status": "Conflict",

            "confidence": 0,

            "verified_value": None,

            "documents": values

        }


# ------------------------------------------------------


def verify_documents(documents):

    verifier = CrossDocumentVerifier()

    return verifier.verify(documents)
