"""Platrixa — Phase 1 document/page accounting + evidence adapter tests.

Covers every case the phase requires:
  - normal text PDF
  - image-only PDF
  - mixed text/image PDF
  - empty page
  - extraction failure
  - multi-page document
  - page numbering correctness
  - evidence identity/stability
  - deterministic repeated processing
plus regression-safety of the existing text pipeline.

Run:  PYTHONPATH=. python3 scripts/fte_document_understanding_phase1.py
"""
from __future__ import annotations

import io
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.document_understanding.adapter import EvidenceAdapter
from backend.document_understanding.ocr import (
    FixtureOCRProvider,
    NullOCRProvider,
    OCRRegion,
)
from backend.document_understanding.page_accounting import detect_source_kind
from backend.document_understanding.representation import (
    DIGITAL_TEXT,
    EXTRACTION_FAILED,
    IMAGE_ONLY,
)

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not condition else ""))


def build_pdf(pages):
    """pages: list of ('text', str) or ('image', seed)"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from PIL import Image as PILImage, ImageDraw, ImageFont

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    story = []
    for idx, (kind, payload) in enumerate(pages):
        if kind == "text":
            story.append(__import__("reportlab.platypus", fromlist=["Paragraph"]).Paragraph(payload, styles["BodyText"]))
        else:
            img = PILImage.new("RGB", (1000, 1414), "white")
            d = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
            except OSError:
                font = ImageFont.load_default()
            d.text((60, 60), f"SCANNED PAGE {idx + 1} seed={payload}", fill="black", font=font)
            d.text((60, 140), "Total amount payable: Rs. 17,080.70", fill="black", font=font)
            b = io.BytesIO()
            img.save(b, format="JPEG", quality=70)
            b.seek(0)
            story.append(Image(b, width=A4[0] - 40 * mm, height=A4[1] - 60 * mm))
        if idx < len(pages) - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def main():
    print("=" * 70)
    print("PLATRIXA PHASE 1 — PAGE ACCOUNTING + EVIDENCE ADAPTER")
    print("=" * 70)

    adapter = EvidenceAdapter()  # no OCR: lightweight text-only install

    # ------------------------------------------------------------------
    print("\n-- normal text PDF --")
    pdf = build_pdf([("text", "TAX INVOICE INV-2026-0412 Total 17080.70")])
    r = adapter.interpret(pdf, "invoice.pdf")
    check("text PDF: single page represented", r.page_count == 1, str(r.page_count))
    check("text PDF: status DIGITAL_TEXT", r.pages[0].status == DIGITAL_TEXT, r.pages[0].status)
    check("text PDF: has evidence", len(r.evidence) > 0, str(len(r.evidence)))
    check("text PDF: no page needs OCR", r.pages_needing_ocr == [], str(r.pages_needing_ocr))
    check("text PDF: text carries PAGE marker", "PAGE 1" in r.text_for_interpretation())

    # ------------------------------------------------------------------
    print("\n-- image-only PDF (the Phase 0 bug) --")
    scan = build_pdf([("image", 1)])
    r = adapter.interpret(scan, "scan.pdf")
    check("image-only PDF: page represented", r.page_count == 1, str(r.page_count))
    check("image-only PDF: status IMAGE_ONLY", r.pages[0].status == IMAGE_ONLY, r.pages[0].status)
    check("image-only PDF: listed as needing OCR",
          r.pages_needing_ocr == [1], str(r.pages_needing_ocr))
    check("image-only PDF: no text fabricated", not r.has_extractable_text)
    check("image-only PDF: no evidence fabricated", len(r.evidence) == 0, str(len(r.evidence)))
    check("image-only PDF: fail-closed note recorded",
          any("IMAGE_ONLY" in n or "no OCR" in n for n in r.notes), str(r.notes))
    check("image-only PDF: serialized text declares the page marker",
          "PAGE 1" in r.text_for_interpretation())
    check("image-only PDF: no synthetic placeholder text injected into input",
          "NO EXTRACTABLE TEXT" not in r.text_for_interpretation(),
          r.text_for_interpretation()[:120])

    # THE regression the audit found: old parser returned pages_without_text=[]
    from backend.layout_extractor import layout_aware_annotate
    parsed_like = {"type": "pdf", "text": r.text_for_interpretation()}
    lay = layout_aware_annotate(parsed_like, "scan.pdf")
    check("REGRESSION FIXED: pages_without_text now fires for a scan",
          lay.get("pages_without_text") == [1], str(lay.get("pages_without_text")))

    # ------------------------------------------------------------------
    print("\n-- mixed text/image PDF --")
    mixed = build_pdf([("text", "Statement page one balance 250000.00"),
                       ("image", 2),
                       ("text", "Statement page three balance 252150.00")])
    r = adapter.interpret(mixed, "mixed.pdf")
    check("mixed PDF: all 3 pages represented", r.page_count == 3, str(r.page_count))
    check("mixed PDF: page statuses", [p.status for p in r.pages] ==
          [DIGITAL_TEXT, IMAGE_ONLY, DIGITAL_TEXT], str([p.status for p in r.pages]))
    check("mixed PDF: only the image page needs OCR",
          r.pages_needing_ocr == [2], str(r.pages_needing_ocr))

    # ------------------------------------------------------------------
    print("\n-- empty page --")
    empty = build_pdf([("text", "Page one has content"), ("text", " "), ("text", "Page three")])
    # a whitespace-only reportlab page may still carry no glyphs
    r = adapter.interpret(empty, "empty.pdf")
    check("empty-page PDF: all pages represented", r.page_count == 3, str(r.page_count))
    check("empty-page PDF: page 2 accounted as non-digital or empty-text",
          r.pages[1].status in (IMAGE_ONLY, DIGITAL_TEXT), r.pages[1].status)
    check("empty-page PDF: if IMAGE_ONLY it is queued for OCR",
          r.pages[1].status != IMAGE_ONLY or 2 in r.pages_needing_ocr)

    # ------------------------------------------------------------------
    print("\n-- extraction failure (corrupt PDF) --")
    r = adapter.interpret(b"this is definitely not a pdf at all", "corrupt.pdf")
    check("corrupt PDF: fails closed with a page", r.page_count >= 1, str(r.page_count))
    check("corrupt PDF: status EXTRACTION_FAILED",
          r.pages[0].status == EXTRACTION_FAILED, r.pages[0].status)
    check("corrupt PDF: no text", not r.has_extractable_text)
    check("corrupt PDF: reason recorded", bool(r.pages[0].reason), str(r.pages[0].reason))
    check("corrupt PDF: never raises", True)

    # ------------------------------------------------------------------
    print("\n-- image input --")
    from PIL import Image as PILImage
    b = io.BytesIO()
    PILImage.new("RGB", (200, 100), "white").save(b, format="PNG")
    r = adapter.interpret(b.getvalue(), "receipt.png")
    check("image: one page IMAGE_ONLY", r.page_count == 1 and r.pages[0].status == IMAGE_ONLY)
    check("image: source_kind image", r.source_kind == "image", r.source_kind)
    r2 = adapter.interpret(b"not an image", "receipt.png")
    check("corrupt image: EXTRACTION_FAILED", r2.pages[0].status == EXTRACTION_FAILED,
          r2.pages[0].status)

    # ------------------------------------------------------------------
    print("\n-- multi-page numbering correctness --")
    ten = build_pdf([("text", f"page {i}") for i in range(1, 11)])
    r = adapter.interpret(ten, "ten.pdf")
    check("10-page: count", r.page_count == 10, str(r.page_count))
    check("10-page: numbers are 1..10", [p.page for p in r.pages] == list(range(1, 11)),
          str([p.page for p in r.pages]))
    check("10-page: every marker present",
          all(f"PAGE {i}" in r.text_for_interpretation() for i in range(1, 11)))

    # ------------------------------------------------------------------
    print("\n-- evidence identity / stability / determinism --")
    r1 = adapter.interpret(pdf, "invoice.pdf")
    r2 = adapter.interpret(pdf, "invoice.pdf")
    check("document_id stable", r1.document_id == r2.document_id)
    check("evidence ids stable",
          [e.evidence_id for e in r1.evidence] == [e.evidence_id for e in r2.evidence])
    check("evidence text stable",
          [e.text for e in r1.evidence] == [e.text for e in r2.evidence])
    check("serialized text stable",
          r1.text_for_interpretation() == r2.text_for_interpretation())
    check("evidence ids unique", len({e.evidence_id for e in r1.evidence}) == len(r1.evidence))
    check("document_id content-addressed and distinct for different bytes",
          r1.document_id != adapter.interpret(pdf + b"x", "invoice.pdf").document_id)
    ev = r1.evidence[0]
    check("evidence carries document id", ev.document_id == r1.document_id)
    check("evidence carries page", ev.page == 1)
    check("evidence confidence is None (never invented)", ev.confidence is None, str(ev.confidence))
    check("evidence source_type set", ev.source_type == "pypdf", ev.source_type)

    # ------------------------------------------------------------------
    print("\n-- OCR seam: null provider is the default, no dependency --")
    check("default adapter has no OCR", EvidenceAdapter().ocr_provider is None)
    null = NullOCRProvider()
    check("NullOCRProvider unavailable", null.is_available() is False)
    r = adapter.interpret(scan, "scan.pdf")
    check("no OCR provider => scan stays IMAGE_ONLY", r.pages[0].status == IMAGE_ONLY)

    print("\n-- OCR seam: fixture provider (deterministic, test-only) --")
    fx = FixtureOCRProvider(regions=[
        OCRRegion(text="TAX INVOICE", page=1, bbox=(10, 10, 200, 30), confidence=0.98),
        OCRRegion(text="Total 17080.70", page=1, bbox=(10, 40, 220, 60), confidence=0.91),
    ])
    a = EvidenceAdapter(ocr_provider=fx)
    r = a.interpret(scan, "scan.pdf")
    check("fixture OCR: page becomes DIGITAL_TEXT", r.pages[0].status == DIGITAL_TEXT,
          r.pages[0].status)
    check("fixture OCR: evidence produced", len(r.evidence) == 2, str(len(r.evidence)))
    check("fixture OCR: bbox preserved", r.evidence[0].bbox == (10.0, 10.0, 200.0, 30.0),
          str(r.evidence[0].bbox))
    check("fixture OCR: confidence preserved", r.evidence[0].confidence == 0.98)
    check("fixture OCR: engine recorded", r.engine == "fixture", str(r.engine))
    check("fixture OCR: OCR never touches the digital page (no unnecessary OCR)",
          a.interpret(pdf, "invoice.pdf").engine is None)
    check("fixture OCR: no financial fields on evidence",
          not any(k in ev0.to_dict() for ev0 in r.evidence
                  for k in ("amount", "value", "debit", "credit", "verified")))

    print("\n-- OCR fail-closed paths --")
    r = EvidenceAdapter(ocr_provider=FixtureOCRProvider(available=False)).interpret(scan, "scan.pdf")
    check("OCR unavailable => stays IMAGE_ONLY", r.pages[0].status == IMAGE_ONLY)
    check("OCR unavailable => no text fabricated", not r.has_extractable_text)
    r = EvidenceAdapter(ocr_provider=FixtureOCRProvider(regions=[])).interpret(scan, "scan.pdf")
    check("OCR empty result => stays IMAGE_ONLY", r.pages[0].status == IMAGE_ONLY)
    r = EvidenceAdapter(ocr_provider=FixtureOCRProvider(raise_on_use=True)).interpret(scan, "scan.pdf")
    check("OCR exception => stays IMAGE_ONLY (no crash)", r.pages[0].status == IMAGE_ONLY)
    fx_low = FixtureOCRProvider(regions=[
        OCRRegion(text="0.0.0.0", page=1, confidence=0.01),  # below floor
    ])
    r = EvidenceAdapter(ocr_provider=fx_low, min_confidence=0.30).interpret(scan, "scan.pdf")
    check("below-confidence region dropped, not trusted", len(r.evidence) == 0, str(len(r.evidence)))
    check("below-confidence page stays IMAGE_ONLY", r.pages[0].status == IMAGE_ONLY)

    # ------------------------------------------------------------------
    print("\n-- regression: existing text path unchanged --")
    from ingestion.parser import parse_document

    class NamedFile:
        def __init__(self, fh, name):
            self._fh, self.name = fh, name

        def seek(self, *a, **k):
            return self._fh.seek(*a, **k)

        def read(self, *a, **k):
            return self._fh.read(*a, **k)

        def __getattr__(self, i):
            return getattr(self._fh, i)

    path = "/tmp/_phase1_regression.pdf"
    with open(path, "wb") as f:
        f.write(pdf)
    with open(path, "rb") as fh:
        existing = parse_document(NamedFile(fh, path))
    check("existing parser still works on a text PDF", existing["type"] == "pdf", str(existing))
    check("existing parser output unchanged (text present)", len(existing["text"]) > 0)
    check("new adapter agrees with parser on digital text presence",
          bool(r1.has_extractable_text) == bool(existing["text"]))

    # plain text passthrough
    r = adapter.interpret(b"Paid 100 to Raj by cash.", "note.txt")
    check("plain text: one DIGITAL_TEXT page", r.page_count == 1 and r.pages[0].status == DIGITAL_TEXT)
    check("plain text: text preserved",
          "Paid 100 to Raj by cash." in r.text_for_interpretation())
    check("source kind detection",
          detect_source_kind("a.pdf") == "pdf" and detect_source_kind("a.png") == "image"
          and detect_source_kind("a.txt") == "text")

    os.remove(path)

    # ------------------------------------------------------------------
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"PHASE 1: {passed}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:")
        for f in failed:
            print("  -", f)
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
