"""Platrixa — document processing orchestration (Phase 3).

    PDF / image / text
        ↓  DocumentExtractor   (page accounting — every page represented)
        ↓  OCRProvider          (optional, only for pages with no text layer)
        ↓  EvidenceAdapter      (deterministic evidence normalization)
        ↓  Financial Semantic Interpreter
        ↓  EXACT existing 18-field CandidateSemanticIR
        ↓  existing Schema Verification
        ↓  existing ExpandedGroundingGate
        ↓  existing Authority Routing
        ↓  VERIFIED / REVIEW_REQUIRED / UNSUPPORTED

The critical property of this module: **it owns no part of that chain
except the first three arrows.** Everything from the semantic interpreter
onward is the existing runtime, reached through its existing text-only
entry point. This module:

  * does NOT define, extend, or modify CandidateSemanticIR (18 fields stay 18)
  * does NOT call the schema verifier, grounding gate, or authorities itself
  * does NOT compute a financial value
  * does NOT map a document-layer status onto a Kernel status
  * does NOT decide, upgrade, or soften any verdict

It builds source evidence, serializes it into the existing text boundary,
and delegates. The Kernel remains the only authority that can produce
VERIFIED.

Lineage: for every semantic field the caller can recover which page (and,
when the engine provides one, which bounding box) the supporting text came
from, via :meth:`DocumentProcessorResult.evidence_for_field`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.document_understanding.adapter import EvidenceAdapter
from backend.document_understanding.representation import (
    EXTRACTION_FAILED,
    DocumentRepresentation,
)


@dataclass
class DocumentProcessorResult:
    """Outcome of routing one document through the existing pipeline.

    ``kernel_result`` is whatever the existing Kernel returned, untouched.
    This wrapper adds provenance and timing; it adds no authority.
    """

    document: DocumentRepresentation
    kernel_result: Any
    #: Text actually handed to the existing Kernel boundary.
    raw_input: str = ""
    timings_ms: Dict[str, float] = field(default_factory=dict)
    #: Field -> evidence ids that support it. Populated post-kernel by
    #: matching the interpretation's source strings against the evidence set.
    lineage: Dict[str, List[str]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Verbatim Kernel status. Never derived, never mapped."""
        return str(getattr(self.kernel_result, "status", ""))

    @property
    def success(self) -> bool:
        return bool(getattr(self.kernel_result, "success", False))

    def evidence_for_field(self, field_name: str) -> List[Dict[str, Any]]:
        """Which source regions support a semantic field.

        Returns the full evidence dicts (page, bbox, text, confidence) so a
        developer can answer "which page/region caused this fact?".
        """
        ids = set(self.lineage.get(field_name, []))
        return [e.to_dict() for e in self.document.evidence if e.evidence_id in ids]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "raw_input": self.raw_input,
            "status": self.status,
            "success": self.success,
            "timings_ms": self.timings_ms,
            "lineage": self.lineage,
            "notes": self.notes,
        }


class DocumentProcessor:
    """Routes a document into the existing semantic + validation pipeline.

    Parameters
    ----------
    process_text:
        Callable taking ``raw_input: str`` and returning the existing
        pipeline's result. In production this is the ``Platrixa`` facade
        (``client.process``) or ``Kernel.process``. It is injected, so this
        module is not coupled to any particular caller and cannot bypass it.
    ocr_provider:
        Optional OCR engine (see ``registry.get_ocr_provider``).
    max_chars:
        Hard cap on the text handed to the interpreter, mirroring the
        existing developer-API input limit. Content beyond the cap is
        truncated at a line boundary and the truncation is recorded — the
        document is never silently shortened.
    """

    def __init__(
        self,
        process_text: Callable[[str], Any],
        ocr_provider: Any = None,
        max_chars: int = 2000,
    ) -> None:
        self._process_text = process_text
        self._adapter = EvidenceAdapter(ocr_provider=ocr_provider)
        self.max_chars = int(max_chars)

    def process(
        self,
        data: bytes,
        source_name: str,
        request_id: Optional[str] = None,
    ) -> DocumentProcessorResult:
        timings: Dict[str, float] = {}
        notes: List[str] = []

        # 1-3. Document understanding (deterministic evidence only).
        document = self._adapter.interpret(data, source_name)
        timings["document_understanding_ms"] = float(document.processing_ms or 0.0)

        # A document we could not read at all must fail closed BEFORE the
        # interpreter sees anything. We do not send an empty or placeholder
        # payload and hope the model refuses.
        failed_pages = [p.page for p in document.pages if p.status == EXTRACTION_FAILED]
        raw_input = self._serialize(document, notes)

        if failed_pages and not document.has_extractable_text:
            # Every page failed: nothing may be interpreted. Return a result
            # whose status is produced by the EXISTING runtime, not by us.
            kernel_result = self._run_existing_pipeline(raw_input, request_id, timings)
            notes.append(
                f"every page failed extraction ({failed_pages}); no financial "
                f"interpretation may be produced from this document"
            )
            return DocumentProcessorResult(
                document=document,
                kernel_result=kernel_result,
                raw_input=raw_input,
                timings_ms=timings,
                lineage={},
                notes=notes,
            )

        if document.pages_needing_ocr and not document.has_extractable_text:
            notes.append(
                f"pages {document.pages_needing_ocr} remain IMAGE_ONLY; the "
                f"interpreter receives page markers only and cannot invent content"
            )

        # 4+. The EXISTING pipeline, unchanged, via its text-only boundary.
        kernel_result = self._run_existing_pipeline(raw_input, request_id, timings)

        result = DocumentProcessorResult(
            document=document,
            kernel_result=kernel_result,
            raw_input=raw_input,
            timings_ms=timings,
            notes=notes,
        )
        result.lineage = self._build_lineage(document, kernel_result)
        return result

    # -- internals ------------------------------------------------------

    def _serialize(self, document: DocumentRepresentation, notes: List[str]) -> str:
        text = document.text_for_interpretation()
        if len(text) <= self.max_chars:
            return text
        truncated = text[: self.max_chars]
        cut = truncated.rfind("\n")
        if cut > 0:
            truncated = truncated[:cut]
        notes.append(
            f"document text truncated from {len(text)} to {len(truncated)} "
            f"characters for the interpreter; remaining pages are not interpreted"
        )
        return truncated

    def _run_existing_pipeline(
        self,
        raw_input: str,
        request_id: Optional[str],
        timings: Dict[str, float],
    ) -> Any:
        started = time.perf_counter()
        try:
            if request_id:
                return self._process_text(raw_input, request_id=request_id)
            return self._process_text(raw_input)
        finally:
            timings["semantic_validation_pipeline_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 3
            )

    def _build_lineage(
        self,
        document: DocumentRepresentation,
        kernel_result: Any,
    ) -> Dict[str, List[str]]:
        """Map semantic fields back to the evidence that supports them.

        Purely a citation index: it resolves each interpretation string to
        the evidence region containing it. It has no effect on the verdict,
        which has already been decided by the existing runtime.
        """
        interpretation = getattr(kernel_result, "interpretation_candidate", None)
        interpretation = interpretation if isinstance(interpretation, dict) else {}

        lineage: Dict[str, List[str]] = {}
        if not interpretation:
            return lineage

        normalized_evidence = [
            (e, e.text.strip().lower()) for e in document.evidence if (e.text or "").strip()
        ]

        for field_name in ("parties", "references"):
            values = interpretation.get(field_name) or []
            if not isinstance(values, list):
                continue
            ids: List[str] = []
            for value in values:
                needle = str(value).strip().lower()
                if not needle:
                    continue
                for evidence, haystack in normalized_evidence:
                    if needle in haystack:
                        ids.append(evidence.evidence_id)
            if ids:
                lineage[field_name] = sorted(set(ids))

        amounts = interpretation.get("amounts") or []
        if isinstance(amounts, list):
            ids = []
            for amount in amounts:
                if not isinstance(amount, dict):
                    continue
                needle = str(amount.get("value", "")).strip().lower()
                if not needle:
                    continue
                variants = {needle, needle.replace(",", "")}
                for evidence, haystack in normalized_evidence:
                    if any(v and v in haystack for v in variants):
                        ids.append(evidence.evidence_id)
                        break
            if ids:
                lineage["amounts"] = sorted(set(ids))

        return lineage
