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

# --- New improvement layer (Module 3.1 - 3.9) ---
from backend.placeholder_cleaner import clean_module3_output
from backend.boilerplate_filter import remove_boilerplate
from backend.industry_context import apply_industry_context
from backend.risk_classifier import classify_risks
from backend.confidence_engine_v2 import generate_confidence_scores
from backend.timeline_engine_v2 import generate_timeline_v2
from backend.missing_data_detector import detect_missing_data
from backend.entity_resolver import resolve_entities
from backend.quality_score import generate_quality_score


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


def _apply_improvement_layer(result: Dict[str, Any], text: str) -> Dict[str, Any]:
    """
    Runs the completed Module 3.1-3.9 improvement layer over the existing
    8-key Module 3 result, in the specified order:

    Placeholder Cleaner -> Boilerplate Filter -> Industry Context ->
    Risk Classifier -> Confidence Engine V2 -> Timeline Engine V2 ->
    Missing Data Detector -> Entity Resolver -> Quality Score

    IMPORTANT -- Industry Context / Risk Classifier are deliberately NOT
    called on the full `result` dict, even though their own public
    functions accept one. Both are written to recursively walk every
    string anywhere in whatever dict they're given and replace each one
    with a nested object (e.g. "Document" -> {"text": "Document",
    "risk": {...}}). Calling them on the full result would reach into
    financial_data/ratios/ocr_verification/etc. and corrupt those
    already-consumed fields, which the existing schema must not do. So
    each is instead run on an isolated {"narrative": text} blob (the same
    combined document text run_module3() receives), and only their
    output is attached under the new "industry_context" / "risk_analysis"
    keys -- the real, unmodified functions are still used; only their
    input is scoped to protect the pre-existing fields.

    Every other stage adds a purely new top-level key (confidence_scores,
    missing_data, entities, entity_relationships, quality_score) except
    Timeline Engine V2, whose own docstring states it replaces the
    existing "timeline" key with an enriched version -- that in-place
    upgrade is intentional per this module's stated purpose.
    """
    # 1. Placeholder Cleaner -- operates on the full result as designed;
    #    only replaces placeholder-ish leaf values / drops empty
    #    dicts/lists. NOTE: this means it can drop one of the 8 original
    #    top-level keys entirely if that stage produced no data (e.g. no
    #    events found) -- since app.py's schema depends on those 8 keys
    #    always being present, they're restored below if cleaning removed
    #    them, using their pre-cleaning (empty) value. This does not
    #    change placeholder_cleaner.py's own behavior, only guarantees
    #    the contract app.py relies on.
    original_keys_snapshot = dict(result)
    result = clean_module3_output(result)
    for key, original_value in original_keys_snapshot.items():
        if key not in result:
            result[key] = original_value

    # 2. Boilerplate Filter -- same: strips procedural boilerplate
    #    sentences from string values, structure/keys unaffected.
    result = remove_boilerplate(result)

    # 3. Industry Context -- isolated call, see docstring above.
    industry_context_output = apply_industry_context({"narrative": text})
    result["industry_context"] = industry_context_output.get("narrative")

    # 4. Risk Classifier -- isolated call, see docstring above. (Its own
    #    internal `from backend.industry_context import classify_context`
    #    import is unaffected -- that's risk_classifier.py's own
    #    dependency, not something this controller needs to manage.)
    risk_analysis_output = classify_risks({"narrative": text})
    result["risk_analysis"] = risk_analysis_output.get("narrative")

    # 5. Confidence Engine V2 -- adds "confidence_scores".
    result = generate_confidence_scores(result)

    # 6. Timeline Engine V2 -- replaces "timeline" with an enriched
    #    version (intentional; see docstring above).
    result = generate_timeline_v2(result)

    # 7. Missing Data Detector -- adds "missing_data". Must run before
    #    Quality Score, which reads result["missing_data"].
    result = detect_missing_data(result)

    # 8. Entity Resolver -- adds "entities" and "entity_relationships".
    result = resolve_entities(result)

    # 9. Quality Score -- adds "quality_score". Runs last since it scores
    #    the completeness of everything produced above, including
    #    missing_data.
    result = generate_quality_score(result)

    return result


def run_module3(text: str, extracted_documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Module 3 pipeline orchestrator.

    Runs the complete deterministic financial-intelligence pass --
    extraction, ratio detection/calculation, OCR verification,
    cross-document verification, confidence scoring, event extraction,
    timeline building, duplicate removal, and context compression --
    followed by the improvement layer (placeholder cleaning, boilerplate
    filtering, industry context, risk classification, detailed
    confidence scoring, timeline enrichment, missing-data detection,
    entity resolution, and an overall quality score) -- and returns the
    result dict app.py's Module 3 integration expects.

    The original 8 keys (financial_data, ratios, ocr_verification,
    cross_document_verification, confidence, events, timeline,
    optimized_context) are unchanged in name and structure. The
    improvement layer only appends: confidence_scores, missing_data,
    entities, entity_relationships, quality_score, risk_analysis,
    industry_context.

    Args:
        text: combined raw extracted text across all uploaded documents.
            Used for the unified financial_data/ratios/events/timeline
            extraction (a single, whole-corpus view).
        extracted_documents: per-document results (e.g. Module 2's
            extract_multiple() output). Used specifically for OCR and
            cross-document verification, which need each document's own
            figures to compare against each other -- something a single
            merged text blob can't provide.
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

    result = {
        "financial_data": deduplicated_financial_data,
        "ratios": ratios,
        "ocr_verification": ocr_verification,
        "cross_document_verification": cross_document_verification,
        "confidence": confidence,
        "events": events,
        "timeline": timeline,
        "optimized_context": optimized_context,
    }

    # --- New improvement layer (Module 3.1 - 3.9) ---
    result = _apply_improvement_layer(result, text)

    return result
