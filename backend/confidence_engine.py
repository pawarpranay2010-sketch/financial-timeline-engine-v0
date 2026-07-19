"""
Financial Timeline Engine
Module 3

Confidence Engine

Purpose
-------
Assign confidence scores to every extracted
financial value based on verification quality.
"""

from __future__ import annotations

from typing import Dict, Any


class ConfidenceEngine:

    def __init__(self):
        pass

    # -----------------------------------------------------

    def evaluate(
        self,
        ocr_results: Dict[str, Any],
        cross_results: Dict[str, Any]
    ) -> Dict[str, Any]:

        confidence = {}

        fields = set()

        fields.update(ocr_results.keys())
        fields.update(cross_results.keys())

        for field in fields:

            confidence[field] = self._score(
                field,
                ocr_results.get(field),
                cross_results.get(field)
            )

        return confidence

    # -----------------------------------------------------

    def _score(
        self,
        field,
        ocr,
        cross
    ):

        score = 100

        reasons = []

        # -----------------------------
        # OCR Check
        # -----------------------------

        if ocr:

            if ocr["status"] == "Conflict":

                score -= 40

                reasons.append(
                    "OCR Conflict"
                )

        # -----------------------------
        # Cross Document Check
        # -----------------------------

        if cross:

            if cross["status"] == "Conflict":

                score -= 50

                reasons.append(
                    "Cross Document Conflict"
                )

            elif cross["status"] == "Verified":

                score += 0

        # -----------------------------

        if score < 0:

            score = 0

        # -----------------------------

        if score >= 95:

            level = "Very High"

        elif score >= 80:

            level = "High"

        elif score >= 60:

            level = "Medium"

        elif score >= 40:

            level = "Low"

        else:

            level = "Very Low"

        return {

            "score": score,

            "level": level,

            "reasons": reasons

        }


# ---------------------------------------------------------


def calculate_confidence(
    ocr_results,
    cross_results
):

    engine = ConfidenceEngine()

    return engine.evaluate(
        ocr_results,
        cross_results
    )
