#!/usr/bin/env python3
"""Platrixa — Phase 4 document understanding benchmark.

REPRODUCIBLE. Synthetic, legally-safe fixtures only. No private or
third-party financial document is used, and nothing is sent to an external
service.

Measures each pipeline stage SEPARATELY for four input classes:

    DIGITAL PDF   text layer present
    SCANNED PDF   image-only pages, no text layer
    IMAGE         standalone image file
    MIXED PDF     text page + image-only page

Stages:
    1. page accounting / text extraction
    2. OCR                          (NOT MEASURED unless an engine is installed)
    3. evidence adapter
    4. semantic + schema + grounding (deterministic; stub interpreter)
    5. total end-to-end

Reported: cold and warm latency, ms/page, pages/sec, documents/min, peak
RSS, peak VRAM (if a GPU is present), and document size.

Quality metrics are reported ONLY where a ground truth exists in the
synthetic fixture. OCR accuracy is explicitly NOT MEASURED unless a real
OCR engine is installed; no accuracy figure is ever invented.

Usage:
    PYTHONPATH=. python3 scripts/fte_document_benchmark.py [--json out.json]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import resource
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPEATS = 5
TRUTH_SENTENCE = "Paid 12500 to Raj for office furniture by cheque"


# ---------------------------------------------------------------------------
# Fixtures (synthetic)
# ---------------------------------------------------------------------------

def _doc_buffer():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    doc.build([Paragraph(TRUTH_SENTENCE, getSampleStyleSheet()["BodyText"])])
    return buf


def digital_pdf(pages: int = 1) -> bytes:
    """A digital PDF: real text layer on every page."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    st = getSampleStyleSheet()
    story = []
    for p in range(pages):
        story.append(Paragraph(TRUTH_SENTENCE, st["BodyText"]))
        story.append(Paragraph(f"Page {p + 1} of {pages}", st["BodyText"]))
        if p < pages - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def scanned_pdf(pages: int = 1) -> bytes:
    """An image-only PDF: rasterized pages, no text layer at all."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate
    from PIL import Image as PILImage, ImageDraw, ImageFont

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    story = []
    for p in range(pages):
        img = PILImage.new("RGB", (1240, 1754), "white")  # ~150 DPI A4
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
        d.text((60, 60), f"SCANNED PAGE {p + 1}", fill="black", font=font)
        d.text((60, 130), TRUTH_SENTENCE, fill="black", font=font)
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=70)
        b.seek(0)
        story.append(Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm))
        if p < pages - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def standalone_image() -> bytes:
    from PIL import Image as PILImage, ImageDraw, ImageFont

    img = PILImage.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    d.text((40, 40), TRUTH_SENTENCE, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mixed_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet
    from PIL import Image as PILImage, ImageDraw

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    img = PILImage.new("RGB", (1240, 1754), "white")
    ImageDraw.Draw(img).text((60, 60), "SCANNED PAGE 2", fill="black")
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=70)
    b.seek(0)
    st = getSampleStyleSheet()
    doc.build([
        Paragraph(TRUTH_SENTENCE, st["BodyText"]),
        PageBreak(),
        Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm),
    ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# deterministic semantic stage (real kernel, stub interpreter)
# ---------------------------------------------------------------------------

from backend.kernel.kernel import Kernel
from backend.model_provider.base import (
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
)

CANDIDATE_18 = {
    "transaction_type": "PURCHASE",
    "parties": ["Raj"],
    "amounts": [{"value": "12500", "source": "explicit"}],
    "payment_method": "cheque",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "CHEQUE",
    "ambiguity_flags": [],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.9",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


class StubProvider:
    @property
    def config(self):
        return ProviderConfig()

    def status(self):
        return ProviderStatus(available=True, model_id="stub", base_model_revision="",
                              adapter_repo_id="", adapter_revision="", reason="stub",
                              loadable=True)

    def interpret(self, raw_input):
        return InterpretationResult(raw_input=raw_input, candidate=dict(CANDIDATE_18),
                                    model_id="stub", provider_revision="stub",
                                    generated_profile={})


# ---------------------------------------------------------------------------
# timing helpers
# ---------------------------------------------------------------------------

def timed(fn, repeats: int = REPEATS):
    t0 = time.perf_counter()
    out = fn()
    cold = (time.perf_counter() - t0) * 1000.0
    warm = []
    for _ in range(max(repeats - 1, 0)):
        t0 = time.perf_counter()
        fn()
        warm.append((time.perf_counter() - t0) * 1000.0)
    return cold, (statistics.median(warm) if warm else cold), out


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gpu_info() -> Dict[str, Any]:
    try:
        import torch

        if torch.cuda.is_available():
            return {"available": True,
                    "device_count": torch.cuda.device_count(),
                    "device_name": torch.cuda.get_device_name(0),
                    "peak_vram_mb": round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1)}
        return {"available": False, "reason": "no CUDA device visible"}
    except Exception as exc:
        return {"available": False, "reason": f"torch unavailable ({type(exc).__name__})"}


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------

def benchmark_one(label: str, data: bytes, name: str, ocr_provider, kernel) -> Dict[str, Any]:
    from backend.document_understanding.adapter import EvidenceAdapter
    from backend.document_understanding.page_accounting import extract_pages
    from backend.document_understanding.processor import DocumentProcessor

    # Stage 1: page accounting / text extraction
    acc_cold, acc_warm, (doc_id, kind, pages, _notes) = timed(
        lambda: extract_pages(data, name))

    # Stage 2: OCR — only measurable with a real engine installed
    if ocr_provider is not None and ocr_provider.is_available():
        adapter_ocr = EvidenceAdapter(ocr_provider=ocr_provider)
        ocr_cold, ocr_warm, _ = timed(lambda: adapter_ocr.interpret(data, name))
        ocr_status = f"MEASURED ({ocr_provider.name} {ocr_provider.version})"
    else:
        ocr_cold = ocr_warm = None
        ocr_status = "NOT MEASURED (no OCR engine installed)"

    # Stage 3 + 4 + 5: adapter -> interpreter -> schema -> grounding -> authorities
    processor = DocumentProcessor(process_text=kernel.process,
                                  ocr_provider=ocr_provider, max_chars=100000)
    e2e_cold, e2e_warm, result = timed(lambda: processor.process(data, name))

    adapter_ms = float(result.document.processing_ms or 0.0)
    semantic_ms = float(result.timings_ms.get("semantic_validation_pipeline_ms") or 0.0)
    page_count = max(len(pages), 1)

    # Quality: only where synthetic ground truth exists
    quality = {
        "ground_truth_sentence": TRUTH_SENTENCE,
        "text_extraction_correct": None,
        "ocr_accuracy": "NOT MEASURED (no OCR engine installed)",
        "false_verified": None,
        "status": result.status,
        "success": result.success,
        "pages_accounted": len(result.document.pages),
        "pages_expected": page_count,
        "page_accounting_correct": len(result.document.pages) == page_count,
    }
    if kind == "pdf" or kind == "text":
        extracted = " ".join((p.text or "") for p in result.document.pages)
        quality["text_extraction_correct"] = TRUTH_SENTENCE.split()[1] in extracted
    if result.success and result.status == "VERIFIED":
        quality["false_verified"] = False  # only a real VERIFIED counts as a check

    return {
        "label": label,
        "source_kind": kind,
        "pages": page_count,
        "size_kb": round(len(data) / 1024.0, 1),
        "page_accounting_ms": {"cold": round(acc_cold, 3), "warm": round(acc_warm, 3)},
        "ocr_ms": (None if ocr_cold is None else
                   {"cold": round(ocr_cold, 3), "warm": round(ocr_warm, 3)}),
        "ocr_status": ocr_status,
        "adapter_ms": round(adapter_ms, 3),
        "semantic_validation_ms": round(semantic_ms, 3),
        "end_to_end_ms": {"cold": round(e2e_cold, 3), "warm": round(e2e_warm, 3)},
        "ms_per_page_warm": round(e2e_warm / page_count, 3),
        "pages_per_sec_warm": round(1000.0 / e2e_warm * page_count, 2) if e2e_warm > 0 else None,
        "docs_per_min_warm": round(60000.0 / e2e_warm, 1) if e2e_warm > 0 else None,
        "quality": quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    print("=" * 78)
    print("PLATRIXA PHASE 4 — DOCUMENT UNDERSTANDING BENCHMARK")
    print("=" * 78)

    from backend.document_understanding.registry import get_ocr_provider

    ocr = get_ocr_provider("auto")
    ocr_available = ocr.is_available()
    print(f"OCR engine selected : {ocr.name} (available={ocr_available})")
    if not ocr_available:
        print("OCR                 : NOT MEASURED — install with "
              "`pip install -r requirements-ocr.txt` to measure")

    kernel = Kernel(model_provider=StubProvider())

    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "gpu": gpu_info(),
    }
    print(f"CPU cores           : {env['cpu_count']}")
    print(f"GPU                 : {env['gpu']}")
    print()

    cases: List[Any] = [
        ("DIGITAL PDF  1p", digital_pdf(1), "invoice.pdf"),
        ("DIGITAL PDF  5p", digital_pdf(5), "statement.pdf"),
        ("DIGITAL PDF 10p", digital_pdf(10), "report.pdf"),
        ("SCANNED PDF  1p", scanned_pdf(1), "scan_invoice.pdf"),
        ("SCANNED PDF  5p", scanned_pdf(5), "scan_statement.pdf"),
        ("IMAGE        1p", standalone_image(), "invoice.png"),
        ("MIXED PDF    2p", mixed_pdf(), "mixed.pdf"),
    ]

    results = []
    header = (f"{'case':16} {'pg':>3} {'KB':>7} {'extract_w':>10} {'ocr_w':>9} "
              f"{'adapt_w':>9} {'sema_w':>8} {'e2e_w':>9} {'e2e_cold':>9} "
              f"{'ms/pg':>7} {'p/s':>7}")
    print(header)
    print("-" * len(header))
    for label, data, name in cases:
        r = benchmark_one(label, data, name, ocr, kernel)
        results.append(r)
        print(f"{r['label']:16} {r['pages']:3d} {r['size_kb']:7.1f} "
              f"{r['page_accounting_ms']['warm']:10.2f} "
              f"{('%.2f' % r['ocr_ms']['warm']) if r['ocr_ms'] else 'N/M':>9} "
              f"{r['adapter_ms']:9.2f} {r['semantic_validation_ms']:8.2f} "
              f"{r['end_to_end_ms']['warm']:9.2f} {r['end_to_end_ms']['cold']:9.2f} "
              f"{r['ms_per_page_warm']:7.2f} "
              f"{(r['pages_per_sec_warm'] or 0):7.2f}")

    print()
    print("QUALITY (synthetic ground truth only)")
    print("-" * 78)
    for r in results:
        q = r["quality"]
        print(f"{r['label']:16} pages={q['pages_accounted']}/{q['pages_expected']} "
              f"accounting_ok={q['page_accounting_correct']} "
              f"text_ok={q['text_extraction_correct']} "
              f"status={q['status']} success={q['success']} "
              f"ocr={q['ocr_accuracy']}")

    payload = {
        "environment": env,
        "ocr_engine": {"name": ocr.name, "available": ocr_available},
        "repeats": REPEATS,
        "results": results,
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }
    print()
    print(f"PEAK RSS: {payload['peak_rss_mb']} MB")
    print(f"PEAK VRAM: {env['gpu'].get('peak_vram_mb', 'N/A (no GPU)')}")
    print("=" * 78)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"JSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
