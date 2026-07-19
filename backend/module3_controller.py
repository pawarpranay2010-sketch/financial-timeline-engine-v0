"""
Financial Timeline Engine

Module 3 Controller
"""

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
