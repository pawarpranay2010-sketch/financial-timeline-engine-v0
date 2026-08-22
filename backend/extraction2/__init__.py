"""
Platrixa
Extraction 2.0

Structured, table-aware, source-grounded financial extraction.

Pipeline:
    Document
      -> DocumentTypeDetector
      -> XBRL / Structured (highest priority)
      -> Table extraction
      -> Layout-aware text
      -> Contextual regex (LAST RESORT)
      -> FinancialExtractorV2
      -> ExtractedFact-shaped facts
      -> Agentic RAG / verification / calculation

The old regex-first FinancialExtractor (backend/financial_extractor.py) is
left in place for backward compatibility; FinancialExtractorV2 is the new
primary extractor.
"""

from backend.extraction2.document_type_detector import (
    DocumentTypeDetector,
    DOC_TYPE_SEC_XBRL,
    DOC_TYPE_SEC_HTML,
    DOC_TYPE_PDF,
    DOC_TYPE_PDF_SCANNED,
    DOC_TYPE_DOCX,
    DOC_TYPE_XLSX,
    DOC_TYPE_CSV,
    DOC_TYPE_TXT,
    DOC_TYPE_HTML,
    DOC_TYPE_UNKNOWN,
)
from backend.extraction2.confidence_scorer import (
    ConfidenceScorer,
    METHOD_XBRL,
    METHOD_HTML_TABLE,
    METHOD_PDF_TABLE,
    METHOD_LAYOUT_AWARE,
    METHOD_CONTEXTUAL_REGEX,
    METHOD_UNANCHORED_REGEX,
)
from backend.extraction2.negative_detector import (
    NegativeDetector,
    parse_parenthesized_value,
)
from backend.extraction2.table_extractor import (
    TableExtractor,
    Table,
)
from backend.extraction2.xbrl_extractor import (
    XbrlExtractor,
    XbrlFact,
)
from backend.extraction2.financial_extractor_v2 import (
    FinancialExtractorV2,
)

__all__ = [
    "DocumentTypeDetector",
    "ConfidenceScorer",
    "NegativeDetector",
    "parse_parenthesized_value",
    "TableExtractor",
    "Table",
    "XbrlExtractor",
    "XbrlFact",
    "FinancialExtractorV2",
    "DOC_TYPE_SEC_XBRL",
    "DOC_TYPE_SEC_HTML",
    "DOC_TYPE_PDF",
    "DOC_TYPE_PDF_SCANNED",
    "DOC_TYPE_DOCX",
    "DOC_TYPE_XLSX",
    "DOC_TYPE_CSV",
    "DOC_TYPE_TXT",
    "DOC_TYPE_HTML",
    "DOC_TYPE_UNKNOWN",
    "METHOD_XBRL",
    "METHOD_HTML_TABLE",
    "METHOD_PDF_TABLE",
    "METHOD_LAYOUT_AWARE",
    "METHOD_CONTEXTUAL_REGEX",
    "METHOD_UNANCHORED_REGEX",
]
