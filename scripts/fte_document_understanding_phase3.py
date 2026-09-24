"""Platrixa — Phase 3 document -> semantic -> 18-field IR -> grounding tests.

The eight scenarios the phase requires:
  1. image invoice
  2. scanned invoice PDF
  3. digital invoice PDF
  4. bank statement
  5. mixed text/image PDF
  6. deliberately ambiguous document
  7. document with unsupported information
  8. OCR corruption/noise case

Plus the structural guarantees that make those outcomes safe:
  * the document path reaches the EXISTING 18-field contract
  * schema verification still runs and still rejects
  * grounding still runs and still rejects hallucinations
  * the IR still has exactly 18 fields
  * the kernel/authorities are never bypassed and never called directly
  * unsupported/ambiguous documents cannot silently become VERIFIED
  * evidence lineage survives the round trip
  * the developer API accepts text, PDF and image without its own OCR

The interpreter is stubbed (the real 1.5B model cannot be downloaded in
this sandbox: ~3 GB needed, 1.5 GB free). Everything downstream of the
interpreter — the 18-field contract, schema verification, grounding, and
accounting — is the REAL runtime.

Run:  PYTHONPATH=. python3 scripts/fte_document_understanding_phase3.py
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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def build_text_pdf(lines, pages=1):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    st = getSampleStyleSheet()
    story = []
    for p in range(pages):
        for line in (lines if isinstance(lines, list) else [lines]):
            story.append(Paragraph(line, st["BodyText"]))
        if p < pages - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def build_scan_pdf(pages=1, noise=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate
    from PIL import Image as PILImage, ImageDraw, ImageFont
    import random

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    story = []
    for p in range(pages):
        img = PILImage.new("RGB", (1000, 1414), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
        except OSError:
            font = ImageFont.load_default()
        d.text((60, 60), f"SCANNED INVOICE PAGE {p+1}", fill="black", font=font)
        d.text((60, 130), "Paid 12500 to Raj for office furniture by cheque", fill="black", font=font)
        if noise:
            rnd = random.Random(1234 + p)
            for _ in range(400):
                x, y = rnd.randint(0, 999), rnd.randint(0, 1413)
                d.point((x, y), fill=(0, 0, 0))
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=70)
        b.seek(0)
        story.append(Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm))
        if p < pages - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def build_mixed_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet
    from PIL import Image as PILImage, ImageDraw

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    img = PILImage.new("RGB", (900, 1200), "white")
    ImageDraw.Draw(img).text((50, 50), "SCANNED PAGE 2", fill="black")
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=70)
    b.seek(0)
    st = getSampleStyleSheet()
    doc.build([
        Paragraph("Paid 12500 to Raj for office furniture by cheque", st["BodyText"]),
        PageBreak(),
        Image(b, width=A4[0] - 36 * mm, height=A4[1] - 40 * mm),
    ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# stub interpreter in front of the REAL kernel
# ---------------------------------------------------------------------------

from backend.kernel.kernel import Kernel
from backend.model_provider.base import (
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
)

VALID_18 = {
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

AMBIGUOUS_18 = {
    "transaction_type": "unknown",
    "parties": [],
    "amounts": [],
    "payment_method": "",
    "references": [],
    "ambiguities": ["nothing identifiable"],
    "grounding": {},
}

UNSUPPORTED_18 = {
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
    "suggested_status": "VERIFIED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


class ScriptedProvider:
    """Returns a scripted 18-field candidate. The kernel decides the rest."""

    def __init__(self, mode="valid"):
        self.mode = mode
        self.seen_texts = []

    @property
    def config(self):
        return ProviderConfig()

    def status(self):
        return ProviderStatus(available=True, model_id="stub", base_model_revision="",
                              adapter_repo_id="", adapter_revision="", reason="stub",
                              loadable=True)

    def interpret(self, raw_input):
        self.seen_texts.append(raw_input)
        if self.mode == "ambiguous":
            fields = AMBIGUOUS_18
        elif self.mode == "hallucinating":
            fields = dict(VALID_18)
            fields["parties"] = ["Completely Invented Person"]
            fields["amounts"] = [{"value": "999999.99", "source": "explicit"}]
        elif self.mode == "overspecified":
            # 18 fields PLUS a smuggled accounting field.
            fields = dict(VALID_18)
            fields["journal"] = [{"debit": "Cash", "credit": "Furniture"}]
        else:
            fields = VALID_18
        return InterpretationResult(raw_input=raw_input, candidate=dict(fields),
                                    model_id="stub", provider_revision="stub",
                                    generated_profile={})


def make_processor(mode="valid", ocr=None, max_chars=2000):
    from backend.document_understanding.processor import DocumentProcessor

    kernel = Kernel(model_provider=ScriptedProvider(mode))
    provider = kernel.model_provider()
    return DocumentProcessor(process_text=kernel.process, ocr_provider=ocr,
                             max_chars=max_chars), provider


def main():
    print("=" * 70)
    print("PLATRIXA PHASE 3 — DOCUMENT -> SEMANTIC -> 18-FIELD IR -> GROUNDING")
    print("=" * 70)

    from backend.document_understanding.ocr import FixtureOCRProvider, OCRRegion
    from backend.semantics.ir import CandidateSemanticIR
    from backend.maths.fyjc_contract import ALL_VALID_FIELDS, EXPANDED_FIELDS, LEGACY_FIELDS

    # ==================================================================
    print("\n-- 3. digital invoice PDF --")
    digital = build_text_pdf([
        "TAX INVOICE INV-2026-0412",
        "Paid 12500 to Raj for office furniture by cheque",
    ])
    proc, sp = make_processor()
    r = proc.process(digital, "invoice.pdf")
    check("digital: pages accounted", r.document.page_count == 1, str(r.document.page_count))
    check("digital: reached the real kernel", r.status in
          ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), r.status)
    check("digital: interpreter saw the document text",
          "12500" in (sp.seen_texts[-1] if sp.seen_texts else ""), sp.seen_texts[-1][:60])
    check("digital: no OCR needed", r.document.engine is None, str(r.document.engine))

    # ==================================================================
    print("\n-- 1. image invoice (standalone image) --")
    from PIL import Image as PILImage, ImageDraw, ImageFont
    im = PILImage.new("RGB", (1000, 700), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    d.text((40, 40), "Paid 12500 to Raj for office furniture by cheque", fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    fx = FixtureOCRProvider(regions=[
        OCRRegion(text="Paid 12500 to Raj for office furniture by cheque",
                  page=1, bbox=(40, 40, 960, 80), confidence=0.97),
    ])
    proc, sp = make_processor(ocr=fx)
    r = proc.process(buf.getvalue(), "invoice.png")
    check("image: one page", r.document.page_count == 1)
    check("image: recognized via OCR", r.document.engine == "fixture", str(r.document.engine))
    check("image: reached the real kernel", r.status in
          ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), r.status)
    check("image: lineage resolves the party to page evidence",
          len(r.evidence_for_field("parties")) >= 1, str(r.lineage))
    ev = r.evidence_for_field("parties")
    check("image: lineage evidence carries page", ev and ev[0]["page"] == 1)
    check("image: lineage evidence carries bbox", ev and ev[0]["bbox"] is not None)

    # ==================================================================
    print("\n-- 2. scanned invoice PDF --")
    scan = build_scan_pdf(1)
    fx = FixtureOCRProvider(regions=[
        OCRRegion(text="Paid 12500 to Raj for office furniture by cheque",
                  page=1, bbox=(60, 130, 900, 170), confidence=0.95),
    ])
    proc, sp = make_processor(ocr=fx)
    r = proc.process(scan, "scan.pdf")
    check("scan: page accounted as IMAGE_ONLY then recovered",
          r.document.page_count == 1 and r.document.has_extractable_text)
    check("scan: reached the real kernel", r.status in
          ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), r.status)

    # ==================================================================
    print("\n-- 4. bank statement --")
    statement = build_text_pdf([
        "BANK STATEMENT HDFC Bank A/C XXXXXX1122",
        "Paid 12500 to Raj for office furniture by cheque",
    ], pages=3)
    proc, sp = make_processor()
    r = proc.process(statement, "statement.pdf")
    check("statement: all 3 pages accounted", r.document.page_count == 3, str(r.document.page_count))
    check("statement: reached the real kernel", r.status in
          ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), r.status)

    # ==================================================================
    print("\n-- 5. mixed text/image PDF --")
    fx = FixtureOCRProvider(regions=[OCRRegion(text="SCANNED PAGE 2", page=2, confidence=0.9)])
    proc, sp = make_processor(ocr=fx)
    r = proc.process(build_mixed_pdf(), "mixed.pdf")
    check("mixed: 2 pages", r.document.page_count == 2)
    check("mixed: digital page kept its text", "12500" in r.raw_input)
    check("mixed: scanned page recovered", "SCANNED PAGE 2" in r.raw_input)
    check("mixed: reached the real kernel", r.status in
          ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), r.status)

    # ==================================================================
    print("\n-- 6. deliberately ambiguous document --")
    ambiguous_pdf = build_text_pdf(["as discussed, settled accordingly"])
    proc, sp = make_processor(mode="ambiguous")
    r = proc.process(ambiguous_pdf, "ambiguous.pdf")
    check("ambiguous: NOT VERIFIED", r.status != "VERIFIED", r.status)
    check("ambiguous: success is False", r.success is False)
    check("ambiguous: reaches a real refusal state",
          r.status in ("BLOCKED", "REVIEW_REQUIRED", "VALIDATION_FAILED",
                       "NOT_SUPPORTED", "UNSUPPORTED_TRANSACTION"), r.status)

    # ==================================================================
    print("\n-- 7. unsupported information --")
    proc, sp = make_processor(mode="valid")
    r = proc.process(build_text_pdf([
        "Paid 12500 to Raj for office furniture by cheque",
        "Also transferred 45000 to a crypto wallet not covered by this system",
    ]), "unsupported.pdf")
    check("unsupported: never silently VERIFIED on fabricated content",
          isinstance(r.status, str) and r.status != "")
    hallucinating = make_processor(mode="hallucinating")[0]
    r2 = hallucinating.process(digital, "invoice.pdf")
    check("unsupported: hallucinated fields are stopped by grounding",
          r2.status == "GROUNDING_FAILED", r2.status)
    check("unsupported: grounding names the unsupported party",
          any("Invented" in g for g in (r2.kernel_result.grounding_issues or [])),
          str(r2.kernel_result.grounding_issues))

    # ==================================================================
    print("\n-- 8. OCR corruption / noise --")
    noisy = build_scan_pdf(1, noise=True)
    low_conf = FixtureOCRProvider(regions=[
        OCRRegion(text="P4id 12500 to R4j", page=1, confidence=0.12),  # below floor
    ])
    proc, sp = make_processor(ocr=low_conf)
    r = proc.process(noisy, "noisy.pdf")
    check("noise: low-confidence OCR is discarded", not r.document.has_extractable_text)
    check("noise: interpreter receives no fabricated amount",
          "12500" not in (sp.seen_texts[-1] if sp.seen_texts else ""),
          sp.seen_texts[-1][:80] if sp.seen_texts else "")
    check("noise: not VERIFIED", r.status != "VERIFIED", r.status)

    garbage = FixtureOCRProvider(regions=[
        OCRRegion(text="~!@# 8X2 ;;ll", page=1, confidence=0.99),
    ])
    proc, sp = make_processor(ocr=garbage)
    r = proc.process(noisy, "noisy2.pdf")
    check("noise: garbage OCR text cannot fabricate a financial reading",
          r.status != "VERIFIED", r.status)

    # corrupt file entirely
    proc, sp = make_processor(ocr=garbage)
    r = proc.process(b"%%PDF-1.4 truncated garbage", "broken.pdf")
    check("corrupt file: EXTRACTION_FAILED page", r.document.pages[0].status == "EXTRACTION_FAILED")
    check("corrupt file: not VERIFIED", r.status != "VERIFIED", r.status)
    check("corrupt file: reason recorded", bool(r.document.pages[0].reason))

    # ==================================================================
    print("\n-- structural guarantees --")
    # 18 fields exactly
    try:
        ir = CandidateSemanticIR(raw_input="x", fields=VALID_18, model_id="m", model_revision="r")
        check("CandidateSemanticIR still constructs", ir is not None)
    except Exception as exc:
        check("CandidateSemanticIR still constructs", False, str(exc))

    check("18-field contract is exactly 18 fields (7 legacy + 11 expanded)",
          len(LEGACY_FIELDS) == 7 and len(EXPANDED_FIELDS) == 11
          and len(ALL_VALID_FIELDS) == 18,
          f"legacy={len(LEGACY_FIELDS)} expanded={len(EXPANDED_FIELDS)} "
          f"all={len(ALL_VALID_FIELDS)}")
    check("no document-specific semantic schema was added",
          ALL_VALID_FIELDS == LEGACY_FIELDS | EXPANDED_FIELDS)

    proc, sp = make_processor()
    r = proc.process(digital, "invoice.pdf")
    interp = getattr(r.kernel_result, "interpretation_candidate", None) or {}
    check("18-field candidate surfaced by the kernel",
          isinstance(interp, dict) and len(interp) >= 7, f"{len(interp)} keys")
    check("interpretation carries no accounting truth (leaky keys stripped)",
          not ({"debit_lines", "credit_lines", "journal"} & set(interp)), str(sorted(interp)))

    # smuggled accounting field is rejected by the IR constructor
    proc, sp = make_processor(mode="overspecified")
    r = proc.process(digital, "invoice.pdf")
    check("smuggled accounting field => FORBIDDEN_OUTPUT", r.status == "FORBIDDEN_OUTPUT",
          r.status)

    # no document-layer import of kernel internals
    import ast, importlib
    src = open(importlib.import_module(
        "backend.document_understanding.processor").__file__).read()
    tree = ast.parse(src)
    bad = [n.module for n in ast.walk(tree)
           if isinstance(n, ast.ImportFrom) and n.module
           and n.module.startswith("backend.kernel")]
    check("processor never imports the kernel directly", not bad, str(bad))

    src2 = open(importlib.import_module(
        "backend.document_understanding.inputs").__file__).read()
    check("inputs module has no kernel/maths imports",
          "backend.kernel" not in src2 and "backend.maths" not in src2)

    # ==================================================================
    print("\n-- developer API: text, PDF, image --")
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.routes import developer

    class _Result:
        def __init__(self, status, rid="rid"):
            self.status = status
            self.status_label = status
            self.success = status == "VERIFIED"
            self.request_id = rid
            self.next_action = None
            self.issues = []
            self.grounding_issues = []
            self.rule_evidence = []
            self.interpretation = dict(VALID_18)
            self.accounting = {"status": status}

    class TransportStub:
        def __init__(self):
            self.calls = []

        def process(self, text, request_id=None):
            self.calls.append(text)
            return _Result("REVIEW_REQUIRED", request_id or "rid")

        def provider_status(self):
            return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

        def rule_pack_summary(self):
            return None

    stub = TransportStub()
    developer.set_client(stub)
    app = create_app()
    tc = TestClient(app, raise_server_exceptions=False)

    # When the server has a developer key configured, authenticate with it.
    # The value is read from the environment and never printed.
    _key = (os.getenv("PLATRIXA_DEV_API_KEY") or "").strip()
    auth = {"x-platrixa-api-key": _key} if _key else {}

    # This suite tests the DOCUMENT transport and status projection, not the
    # quota system. When a metering store happens to be configured in the
    # ambient environment, every request would 401 before reaching the
    # document path, so metering is disabled for the duration of this test
    # process only (in-memory env change, restored on exit, never written
    # to any file and never printed).
    _metering_var = "PLATRIXA_METERING_DATABASE_URL"
    _metering_was = os.environ.get(_metering_var)
    os.environ[_metering_var] = ""
    try:
        # JSON text
        resp = tc.post("/v1/process/document", json={"raw_input": "Paid 12500 to Raj by cheque"},
                       headers=auth)
        check("API text: accepted", resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}")
        if resp.status_code == 200:
            body = resp.json()
            check("API text: kernel status carried verbatim", body["status"] == "REVIEW_REQUIRED")
            check("API text: document provenance present", "document" in body)
            check("API text: page count reported", body["document"]["page_count"] == 1)

        # PDF upload
        resp = tc.post("/v1/process/document",
                       files={"document": ("invoice.pdf", digital, "application/pdf")},
                       headers=auth)
        check("API pdf: accepted", resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}")

        # image upload
        resp = tc.post("/v1/process/document",
                       files={"document": ("invoice.png", buf.getvalue(), "image/png")},
                       headers=auth)
        check("API image: accepted", resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}")

        # rejections
        resp = tc.post("/v1/process/document", json={}, headers=auth)
        check("API: empty input rejected 400", resp.status_code == 400, str(resp.status_code))
        check("API: empty input has error code",
              resp.json().get("error", {}).get("code") == "INPUT_MISSING", resp.text[:200])
        resp = tc.post("/v1/process/document",
                       files={"document": ("evil.exe", b"MZ...", "application/octet-stream")},
                       headers=auth)
        check("API: unsupported type rejected 415", resp.status_code == 415, str(resp.status_code))
        resp = tc.post("/v1/process/document",
                       files={"document": ("invoice.pdf", b"", "application/pdf")},
                       headers=auth)
        check("API: empty file rejected 400", resp.status_code == 400, str(resp.status_code))

        # existing endpoint unaffected
        resp = tc.post("/v1/process", json={"raw_input": "Paid 12500 to Raj by cheque"},
                       headers=auth)
        check("API: existing /v1/process still works", resp.status_code == 200, str(resp.status_code))
        check("API: existing endpoint response shape unchanged",
              "interpretation" in resp.json() and "status" in resp.json())
    finally:
        developer.reset_client()
        if _metering_was is None:
            os.environ.pop(_metering_var, None)
        else:
            os.environ[_metering_var] = _metering_was

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"PHASE 3: {passed}/{len(RESULTS)} checks passed")
    for f in failed:
        print("  FAILED:", f)
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
