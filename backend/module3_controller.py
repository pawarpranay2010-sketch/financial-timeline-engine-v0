"""
Financial Timeline Engine

Module 3 Controller
"""

from __future__ import annotations

from typing import Any, Dict, List

from backend.financial_extractor import extract_financial_data

from backend.financial_calculator import (
    calculate_financial_ratios,
)

from backend.ratio_detector import (
    detect_document_ratios,
    merge_ratios,
)

from backend.ocr_verifier import (
    verify_ocr_results,
)

from backend.cross_document_verifier import (
    verify_documents,
)

from backend.confidence_engine import (
    calculate_confidence,
)

from backend.duplicate_filter import (
    filter_duplicate_information,
)

from backend.event_extraction import (
    extract_events,
)

from backend.Timeline_builder import (
    build_timeline,
)

from backend.context_optimizer import (
    optimize_context,
)


def _resolve_document_text(document: Dict[str, Any]) -> str:
    """extracted_documents may come from Module 2's extract_multiple()
    (each item shaped {"file_name", "parsed_document": {"text": ...}, ...})
    or already be in a simpler {"source"/"document_name", "text"} shape.
    This resolves either without assuming one specific caller shape."""
    if "text" in document:
        return document.get("text") or ""
    parsed_document = document.get("parsed_document") or {}
    return parsed_document.get("text", "")


def _resolve_document_name(document: Dict[str, Any]) -> str:
    return (
        document.get("file_name")
        or document.get("document_name")
        or document.get("source")
        or "Unknown Document"
    )


def _build_per_document_financial_data(
    extracted_documents: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Runs the financial extractor on each document's own text,
    producing the per-document {"source", "document_name", "financial_data"}
    entries that ocr_verifier.verify_ocr_results() (expects "source") and
    cross_document_verifier.verify_documents() (expects "document_name")
    both require -- both keys are included on each entry so one pass
    serves both verifiers."""
    per_document = []
    for document in extracted_documents or []:
        name = _resolve_document_name(document)
        text = _resolve_document_text(document)
        per_document.append({
            "source": name,
            "document_name": name,
            "financial_data": extract_financial_data(text),
        })
    return per_document


def run_module3(text: str, extracted_documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Module 3 pipeline orchestrator.

    Runs the complete deterministic financial-intelligence pass --
    extraction, ratio detection/calculation, OCR verification,
    cross-document verification, confidence scoring, event extraction,
    timeline building, duplicate removal, and context compression -- and
    returns the 8-key result dict app.py's Module 3 integration expects.

    Args:
        text: combined raw extracted text across all uploaded documents.
            Used for the unified financial_data/ratios/events/timeline
            extraction (a single, whole-corpus view).
        extracted_documents: per-document results (e.g. Module 2's
            extract_multiple() output). Used specifically for OCR and
            cross-document verification, which need each document's own
            figures to compare against each other -- something a single
            merged text blob can't provide.

    Returns:
        {
            "financial_data": dict,
            "ratios": dict,
            "ocr_verification": dict,
            "cross_document_verification": dict,
            "confidence": dict,
            "events": list,
            "timeline": list,
            "optimized_context": dict,
        }
    """
    # --- Unified extraction over the combined text ---
    financial_data = extract_financial_data(text)

    document_ratios = detect_document_ratios(text)
    # NOTE: financial_calculator.py was not provided, so
    # calculate_financial_ratios()'s exact signature could not be verified
    # against real code (unlike every other call in this file, which was
    # checked against your actual uploaded files). This call follows the
    # same pattern every other Module 3 engine uses -- take the
    # already-extracted financial_data dict, return a dict shaped like
    # {ratio_name: {"value": ..., ...}} so it merges cleanly with
    # document_ratios via merge_ratios(). Please confirm or correct this
    # one line once financial_calculator.py is available.
    calculated_ratios = calculate_financial_ratios(financial_data)
    ratios = merge_ratios(document_ratios, calculated_ratios)

    # --- Per-document extraction, for verification across documents ---
    per_document_financial_data = _build_per_document_financial_data(extracted_documents)
    ocr_verification = verify_ocr_results(per_document_financial_data)
    cross_document_verification = verify_documents(per_document_financial_data)

    confidence = calculate_confidence(ocr_verification, cross_document_verification)

    events = extract_events(text)
    timeline = build_timeline(events)

    deduplicated_financial_data = filter_duplicate_information(financial_data)

    optimized_context = optimize_context(
        deduplicated_financial_data,
        ratios,
        timeline,
        events,
    )

    return {
        "financial_data": deduplicated_financial_data,
        "ratios": ratios,
        "ocr_verification": ocr_verification,
        "cross_document_verification": cross_document_verification,
        "confidence": confidence,
        "events": events,
        "timeline": timeline,
        "optimized_context": optimized_context,
    }
