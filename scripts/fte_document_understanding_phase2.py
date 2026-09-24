"""Platrixa — Phase 2 OCR integration tests.

Verifies the INTEGRATION properties, deliberately without claiming OCR
accuracy (accuracy requires a benchmark corpus and is a Phase 4 deliverable):

  - OCR is optional: the core runtime imports with no OCR engine present
  - OCR is behind a replaceable provider interface
  - no engine is imported unless explicitly requested
  - registry resolution and fail-closed selection
  - OCR runs ONLY on pages that need it (never on digital pages)
  - every failure mode fails closed with no fabricated text
  - evidence carries engine, version, bbox, confidence
  - OCR output can never carry financial truth or VERIFIED

Run:  PYTHONPATH=. python3 scripts/fte_document_understanding_phase2.py
"""
from __future__ import annotations

import io
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not condition else ""))


def build_scan_pdf(pages=1):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate
    from PIL import Image as PILImage, ImageDraw, ImageFont

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    story = []
    for p in range(pages):
        img = PILImage.new("RGB", (1000, 1414), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
        d.text((60, 60), f"SCANNED PAGE {p+1}", fill="black", font=font)
        d.text((60, 130), "Total amount payable: Rs. 17,080.70", fill="black", font=font)
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=70)
        b.seek(0)
        story.append(Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm))
        if p < pages - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def build_text_pdf(text="TAX INVOICE INV-2026-0412 total 17080.70"):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    doc.build([Paragraph(text, getSampleStyleSheet()["BodyText"])])
    return buf.getvalue()


def main():
    print("=" * 70)
    print("PLATRIXA PHASE 2 — OCR INTEGRATION")
    print("=" * 70)

    scan = build_scan_pdf(1)
    text_pdf = build_text_pdf()

    # ------------------------------------------------------------------
    print("\n-- OCR is OPTIONAL: core runtime unaffected --")
    import backend.document_understanding as du

    check("document_understanding imports with no OCR engine", du is not None)
    from backend.document_understanding.adapter import EvidenceAdapter

    r = EvidenceAdapter().interpret(scan, "scan.pdf")
    check("no OCR configured => scan stays IMAGE_ONLY", r.pages[0].status == "IMAGE_ONLY",
          r.pages[0].status)
    check("no OCR configured => no text fabricated", not r.has_extractable_text)

    # ------------------------------------------------------------------
    print("\n-- no engine module imported unless requested --")
    import sys as _sys

    for mod in ("backend.document_understanding.ocr_rapid",
                "backend.document_understanding.ocr_tesseract"):
        check(f"{mod.rsplit('.',1)[-1]} not imported on the default path", mod not in _sys.modules)

    from backend.document_understanding.registry import get_ocr_provider

    p = get_ocr_provider("none")
    check("registry 'none' => NullOCRProvider", p.name == "none", p.name)
    check("NullOCRProvider unavailable", p.is_available() is False)

    explicit = get_ocr_provider("rapidocr")
    check("registry rapidocr resolves without raising", explicit is not None)
    check("registry rapidocr reports availability as a bool",
          isinstance(explicit.is_available(), bool))
    if not explicit.is_available():
        check("unavailable rapidocr falls back to null (fail closed)", explicit.name == "none",
              explicit.name)
    else:
        check("available rapidocr is returned", explicit.name == "rapidocr", explicit.name)

    auto = get_ocr_provider("auto")
    check("registry auto resolves", auto is not None)
    check("auto provider is an OCRProvider",
          hasattr(auto, "recognize") and hasattr(auto, "is_available"))

    # ------------------------------------------------------------------
    print("\n-- interface replaceability --")
    from backend.document_understanding.ocr import (
        FixtureOCRProvider, NullOCRProvider, OCRProvider, OCRRegion, OCRResult,
    )

    check("RapidOCRProvider satisfies OCRProvider",
          isinstance(get_ocr_provider("rapidocr"), OCRProvider))
    check("FixtureOCRProvider satisfies OCRProvider",
          isinstance(FixtureOCRProvider(), OCRProvider))
    check("adapter accepts ANY provider (not hard-wired)",
          EvidenceAdapter(ocr_provider=FixtureOCRProvider()).ocr_provider is not None)

    # ------------------------------------------------------------------
    print("\n-- OCR runs ONLY where needed --")
    fx = FixtureOCRProvider(regions=[OCRRegion(text="Total 17080.70", page=1, bbox=(5, 6, 7, 8), confidence=0.95)])
    a = EvidenceAdapter(ocr_provider=fx)
    r = a.interpret(text_pdf, "digital.pdf")
    check("digital PDF: OCR not invoked", fx.calls == 0, f"calls={fx.calls}")
    check("digital PDF: text still extracted", r.has_extractable_text)
    check("digital PDF: engine None (pure text path)", r.engine is None, str(r.engine))

    fx2 = FixtureOCRProvider(regions=[OCRRegion(text="scanned total 17080.70", page=1, bbox=(5, 6, 7, 8), confidence=0.95)])
    a2 = EvidenceAdapter(ocr_provider=fx2)
    r = a2.interpret(scan, "scan.pdf")
    check("scanned PDF: OCR invoked", fx2.calls == 1, f"calls={fx2.calls}")
    check("scanned PDF: text now present", r.has_extractable_text)
    check("scanned PDF: engine recorded", r.engine == "fixture", str(r.engine))
    check("scanned PDF: engine version recorded", r.engine_version == "test-1", str(r.engine_version))

    # ------------------------------------------------------------------
    print("\n-- evidence provenance recorded --")
    e = r.evidence[0]
    check("evidence carries page", e.page == 1)
    check("evidence carries source_type", e.source_type.startswith("rapidocr") or
          e.source_type == "ocr", e.source_type)
    check("evidence carries engine", e.engine == "fixture", str(e.engine))
    check("evidence carries engine version", e.engine_version == "test-1")
    check("evidence carries confidence", e.confidence == 0.95, str(e.confidence))
    check("evidence carries bbox", e.bbox == (5.0, 6.0, 7.0, 8.0), str(e.bbox))

    fx3 = FixtureOCRProvider(regions=[OCRRegion(text="boxed", page=1, bbox=(1, 2, 3, 4), confidence=0.9)])
    r3 = EvidenceAdapter(ocr_provider=fx3).interpret(scan, "s.pdf")
    check("bbox normalized to tuple", r3.evidence[0].bbox == (1.0, 2.0, 3.0, 4.0),
          str(r3.evidence[0].bbox))

    # ------------------------------------------------------------------
    print("\n-- failure modes fail closed (no fabricated interpretation) --")
    cases = {
        "OCR unavailable": FixtureOCRProvider(available=False),
        "OCR returns nothing": FixtureOCRProvider(regions=[]),
        "OCR raises": FixtureOCRProvider(raise_on_use=True),
        "OCR all whitespace": FixtureOCRProvider(regions=[OCRRegion(text="   ", page=1, confidence=0.99)]),
        "OCR below confidence floor": FixtureOCRProvider(
            regions=[OCRRegion(text="0.00", page=1, confidence=0.01)]),
    }
    for label, provider in cases.items():
        res = EvidenceAdapter(ocr_provider=provider, min_confidence=0.30).interpret(scan, "s.pdf")
        ok = (res.pages[0].status == "IMAGE_ONLY" and not res.has_extractable_text
              and len(res.evidence) == 0)
        check(f"{label} => fail closed", ok,
              f"status={res.pages[0].status} text={res.has_extractable_text} ev={len(res.evidence)}")

    # corrupted image with an OCR provider present
    res = EvidenceAdapter(ocr_provider=FixtureOCRProvider(
        regions=[OCRRegion(text="x", page=1, confidence=0.99)])).interpret(b"not-an-image", "x.png")
    check("corrupted image + OCR => EXTRACTION_FAILED",
          res.pages[0].status == "EXTRACTION_FAILED", res.pages[0].status)
    check("corrupted image + OCR => no text", not res.has_extractable_text)

    # ------------------------------------------------------------------
    print("\n-- OCR output is never financial truth --")
    fx4 = FixtureOCRProvider(regions=[OCRRegion(
        text="Revenue 12480.60 | Net profit 1487.66 | Dr 1200 | Cr 1200",
        page=1, confidence=0.99)])
    res4 = EvidenceAdapter(ocr_provider=fx4).interpret(scan, "s.pdf")
    check("raw OCR text preserved verbatim (no interpretation)",
          res4.pages[0].text.startswith("Revenue 12480.60"))
    check("evidence carries no status/verdict field",
          all(k not in res4.evidence[0].to_dict()
              for k in ("status", "verified", "verdict", "accounting", "journal")))
    check("document layer never emits VERIFIED",
          "VERIFIED" not in res4.to_dict().__repr__())
    check("document layer has NO real import of backend.maths",
          not _imports_maths("backend.document_understanding.adapter")
          and not _imports_maths("backend.document_understanding.representation")
          and not _imports_maths("backend.document_understanding.page_accounting")
          and not _imports_maths("backend.document_understanding.ocr_rapid")
          and not _imports_maths("backend.document_understanding.ocr_tesseract")
          and not _imports_maths("backend.document_understanding.registry"))

    # ------------------------------------------------------------------
    print("\n-- mixed document: per-page routing --")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet
    from PIL import Image as PILImage, ImageDraw

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    img = PILImage.new("RGB", (900, 1200), "white")
    d = ImageDraw.Draw(img)
    d.text((50, 50), "SCANNED PAGE 2", fill="black")
    b = io.BytesIO(); img.save(b, format="JPEG", quality=70); b.seek(0)
    st = getSampleStyleSheet()
    doc.build([
        Paragraph("Digital page one balance 250000.00", st["BodyText"]),
        PageBreak(),
        Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm),
    ])
    mixed = buf.getvalue()

    fxm = FixtureOCRProvider(regions=[OCRRegion(text="OCR page two text", page=2, confidence=0.95)])
    am = EvidenceAdapter(ocr_provider=fxm)
    rm = am.interpret(mixed, "mixed.pdf")
    check("mixed: 2 pages", rm.page_count == 2, str(rm.page_count))
    check("mixed: page 1 digital text kept", "Digital page one" in rm.pages[0].text)
    check("mixed: page 2 recovered by OCR", "OCR page two" in rm.pages[1].text,
          rm.pages[1].text[:50])
    check("mixed: OCR-sourced evidence only from the scanned page",
          all(e.page == 2 for e in rm.evidence if e.source_type != "pypdf"),
          str([(e.page, e.source_type) for e in rm.evidence]))
    check("mixed: digital page still yields its own citable evidence",
          any(e.page == 1 and e.source_type == "pypdf" for e in rm.evidence))
    check("mixed: pypdf source_type preserved for digital lines",
          any(e.source_type == "pypdf" for e in rm.evidence))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"PHASE 2: {passed}/{len(RESULTS)} checks passed")
    for f in failed:
        print("  FAILED:", f)
    print("=" * 70)
    return 0 if not failed else 1


def _imports_maths(module_name):
    """True only if the module has a REAL import of backend.maths.

    Parses the AST rather than grepping the source, so a docstring that
    merely *mentions* backend.maths (the boundary rule is documented there)
    is not mistaken for a dependency.
    """
    import ast
    import importlib

    mod = importlib.import_module(module_name)
    tree = ast.parse(open(mod.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend.maths"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("backend.maths"):
                return True
    return False


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
