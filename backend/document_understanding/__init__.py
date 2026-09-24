"""Platrixa — Document Understanding (deterministic evidence layer).

WHAT THIS PACKAGE IS
--------------------
A thin, deterministic adapter that turns a document (text / PDF / image)
into an evidence representation: pages, text, bounding boxes, extraction
status, confidence and stable evidence IDs.

WHAT THIS PACKAGE IS NOT
------------------------
It is NOT a financial authority and contains NO financial reasoning.

    * It never computes an amount, total, tax, balance or any arithmetic.
    * It never emits VERIFIED (or any Kernel status).
    * It never imports anything from ``backend.maths`` (kernel, formula
      authority, finance knowledge authority) and is never imported by
      them.
    * It never writes to the 18-field CandidateSemanticIR contract.

The division of responsibility is fixed:

    document understanding -> "What is present in the source?"
    semantic interpretation -> "What financial meaning does it represent?"
    grounding                -> "Is that meaning supported by the source?"
    authorities              -> "What deterministic operation is permitted?"

The output of this package is SOURCE EVIDENCE ONLY. It becomes financial
meaning exclusively by being serialized into the existing text boundary
(``Kernel.process(raw_input: str)``), where the existing schema verifier,
grounding gate and authority routing remain authoritative and unchanged.
"""

from backend.document_understanding.representation import (
    DOCUMENT_SOURCE_TEXT,
    EXTRACTION_FAILED,
    IMAGE_ONLY,
    DIGITAL_TEXT,
    DocumentRepresentation,
    EvidenceRef,
    ExtractionStatus,
    PageRepresentation,
)

__all__ = [
    "DIGITAL_TEXT",
    "IMAGE_ONLY",
    "EXTRACTION_FAILED",
    "DOCUMENT_SOURCE_TEXT",
    "ExtractionStatus",
    "EvidenceRef",
    "PageRepresentation",
    "DocumentRepresentation",
]
