"""
Financial Timeline Engine
Module 3

Module 3 Controller

Purpose
-------
Runs the complete intelligence pipeline.

Flow

Extract
↓

Calculate

↓

Ratio Detection

↓

OCR Verification

↓

Cross Verification

↓

Confidence

↓

Duplicate Removal

↓

Event Extraction

↓

Timeline Builder

↓

Context Optimizer
"""

from backend.module3.financial_extractor import extract_financial_data
from backend.module3.financial_calculator import calculate_financial_ratios
from backend.module3.ratio_detector import (
    detect_document_ratios,
    merge_ratios,
)
from backend.module3.ocr_verifier import verify_ocr_results
from backend.module3.cross_document_verifier import verify_documents
from backend.module3.confidence_engine import calculate_confidence
from backend.module3.duplicate_filter import filter_duplicate_information
from backend.module3.event_extractor import extract_events
from backend.module3.timeline_builder import build_timeline
from backend.module3.context_optimizer import optimize_context


class Module3Controller:

    def process(
        self,
        text,
        extracted_documents,
    ):

        # ------------------------------

        financial_data = extract_financial_data(text)

        # ------------------------------

        calculated_ratios = calculate_financial_ratios(
            financial_data
        )

        # ------------------------------

        document_ratios = detect_document_ratios(
            text
        )

        final_ratios = merge_ratios(
            document_ratios,
            calculated_ratios,
        )

        # ------------------------------

        ocr_results = verify_ocr_results(
            extracted_documents
        )

        # ------------------------------

        cross_results = verify_documents(
            extracted_documents
        )

        # ------------------------------

        confidence = calculate_confidence(
            ocr_results,
            cross_results,
        )

        # ------------------------------

        cleaned_financials = filter_duplicate_information(
            financial_data
        )

        # ------------------------------

        events = extract_events(text)

        # ------------------------------

        timeline = build_timeline(events)

        # ------------------------------

        optimized_context = optimize_context(
            cleaned_financials,
            final_ratios,
            timeline,
            events,
        )

        # ------------------------------

        return {

            "financial_data": cleaned_financials,

            "ratios": final_ratios,

            "ocr_verification": ocr_results,

            "cross_document_verification": cross_results,

            "confidence": confidence,

            "events": events,

            "timeline": timeline,

            "optimized_context": optimized_context,

        }


# ----------------------------------------------------------


def run_module3(
    text,
    extracted_documents,
):

    controller = Module3Controller()

    return controller.process(
        text,
        extracted_documents,
    )
