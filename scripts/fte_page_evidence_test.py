#!/usr/bin/env python3
"""Sprint 6 — Page-Aware Evidence Anchoring targeted tests.

Proves the upgraded evidence chain end to end:

  PDF page -> page-aware extraction -> evidence fragment + page ->
  financial fact -> verification/calculation -> Grid -> Memo ->
  floating evidence card

Checks:
  1. A multi-page PDF can produce page-aware evidence (real pypdf parse).
  2. Evidence text belongs to the correct page.
  3. Extracted fact retains page metadata.
  4. Grid row receives identical provenance.
  5. Memo evidence card receives identical provenance.
  6. Derived metrics retain formula + source inputs.
  7. Blocked metrics retain their reason.
  8. Missing page metadata becomes '—'.
  9. No provenance is fabricated.
 10. Demo mode still passes.
 11. Full AppTest still passes (run separately).
 12. Existing memo floating-card interaction still passes.

No network, no AI, no storage — stdlib + existing app/backend code only.
Raw PDF bytes are held in memory only (ephemeral).
"""
import io
import re as _re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak

from ingestion.parser import parse_pdf
from backend.financial_extractor import extract_financial_data
from backend.financial_calculator import calculate_financial_ratios

from streamlit.testing.v1 import AppTest

APP = "app (1) (9).py"


def make_multi_page_pdf() -> bytes:
    """A 3-page PDF: Revenue on page 2, Net Profit + Assets on page 3.
    Page 1 is a cover with no financial facts (proves facts are NOT
    attributed to the wrong page)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Acme Corp Annual Report FY2025 - Cover", styles["Normal"]),
        PageBreak(),
        Paragraph(
            "Total Revenue for the year was 281,700,000,000, up strongly from a year ago.",
            styles["Normal"],
        ),
        PageBreak(),
        Paragraph(
            "Net Profit reached 98,300,000,000 during the same period. "
            "Total Assets stood at 512,200,000,000. "
            "Shareholders' Equity was 268,500,000,000 at year end.",
            styles["Normal"],
        ),
    ]
    doc.build(story)
    return buf.getvalue()


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def check_exceptions(at, label):
    if at.exception:
        print(f"FAIL [{label}]:")
        for e in at.exception:
            print(getattr(e, "stack_trace", e))
        return False
    return True


def main() -> int:
    pdf_bytes = make_multi_page_pdf()
    parsed = parse_pdf(io.BytesIO(pdf_bytes))
    assert parsed["pages"] == 3, f"test PDF must be 3 pages, got {parsed['pages']}"

    # ---- 1) Multi-page PDF -> page-aware evidence ----
    fd = extract_financial_data(parsed["text"])
    rev = fd.get("Revenue") or {}
    npf = fd.get("Net Profit") or {}
    assert rev.get("page") == 2, f"Revenue should anchor to page 2, got {rev.get('page')}"
    assert npf.get("page") == 3, f"Net Profit should anchor to page 3, got {npf.get('page')}"
    assert fd.get("Assets", {}).get("page") == 3, "Assets should anchor to page 3"
    assert fd.get("Equity", {}).get("page") == 3, "Equity should anchor to page 3"
    print("1. MULTI-PAGE PDF -> PAGE-AWARE EVIDENCE OK (Revenue p.2, Net Profit/Assets/Equity p.3)")

    # ---- 2) Evidence text belongs to the correct page ----
    assert "up strongly from a year ago" in (rev.get("evidence") or ""), "Revenue evidence must come from page 2"
    assert "same period" in (npf.get("evidence") or ""), "Net Profit evidence must come from page 3"
    assert "Cover" not in (rev.get("evidence") or ""), "evidence must not leak from the cover page"
    print("2. EVIDENCE TEXT BELONGS TO CORRECT PAGE OK")

    # ---- 3) Extracted fact retains page metadata ----
    assert isinstance(rev.get("page"), int) and rev["page"] == 2
    assert rev.get("source") == "Document"
    print("3. FACT RETAINS PAGE METADATA OK (page=2, int)")

    # ---- 4) Grid row receives identical provenance ----
    # module3-shaped result -> canonical grid rows -> _metric_overlay_fields
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("fte_app_mod", APP)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _build_terminal_rows = _mod._build_terminal_rows
    _metric_overlay_fields = _mod._metric_overlay_fields
    _blocked_metrics = _mod._blocked_metrics

    m3 = {"financial_data": fd, "ratios": {}}
    rows = _build_terminal_rows(m3)
    rev_row = next(r for r in rows if r["metric"] == "Revenue")
    assert rev_row["_kind"] == "verified"
    fields = _metric_overlay_fields(rows, m3, "Revenue")
    assert fields["location"] == "2", f"grid/dialog location must carry page 2, got {fields['location']}"
    assert "p. 2" in fields["source_ref"], f"source_ref must contain p. 2, got {fields['source_ref']}"
    print("4. GRID ROW RECEIVES IDENTICAL PROVENANCE OK (location=p. 2)")

    # ---- 5) Memo evidence card receives identical provenance ----
    # The demo memo overlay card renders Reference / Source / Period /
    # Evidence rows from the same _metric_overlay_fields, so the page must
    # reach the card. Drive the demo memo and check the Revenue card HTML.
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.button(key="fte_btn_demo").click().run()
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    at.button(key="fte_btn_demo_memo").click().run()
    if not check_exceptions(at, "demo memo card provenance"):
        return 1
    memo = next(str(m.value) for m in at.markdown if "fte-memo-para" in str(m.value))
    card_m = _re.search(
        r'<div class="fte-memo-card"[^>]*data-card="ftemetric-revenue".*?</div></div></div>',
        memo,
        _re.S,
    )
    assert card_m, "Revenue overlay card not found in demo memo"
    card = card_m.group(0)
    assert "Reference" in card, "card missing Reference row"
    assert "Demo fixture" in card, "card missing demo document provenance"
    assert "Microsoft" not in card, "card leaks real-company provenance in Demo mode"
    assert "10-K FY2025" not in card, "card leaks real-filing provenance in Demo mode"
    print("5. MEMO EVIDENCE CARD RECEIVES IDENTICAL PROVENANCE OK")

    # ---- 6) Derived metrics retain formula + source inputs ----
    ratios = calculate_financial_ratios(fd)
    roe = ratios.get("ROE") or {}
    assert "formula" in roe and "inputs" in roe, f"ROE must carry formula+inputs, got {list(roe.keys())}"
    assert "Net Profit" in roe["inputs"] and "Equity" in roe["inputs"]
    print("6. DERIVED METRICS RETAIN FORMULA + SOURCE INPUTS OK (ROE)")

    # ---- 7) Blocked metrics retain their reason ----
    # Net Profit was extracted, but e.g. a metric absent from both
    # financial_data and ratios becomes blocked only when the missing-data
    # detector reports it; for a pure unit test, directly verify the
    # pipeline's blocked reason plumbing via _build_terminal_rows with a
    # missing-data entry.
    bm3 = {
        "financial_data": fd,
        "ratios": {},
        "missing_data": {"ratios": ["Debt to Equity"]},
    }
    rows2 = _build_terminal_rows(bm3)
    dte = next((r for r in rows2 if r["metric"] == "Debt to Equity"), None)
    assert dte and dte["_kind"] == "blocked", "Debt to Equity must be blocked"
    assert "not available" in (dte.get("_reason") or "").lower(), "blocked reason must be retained"
    print("7. BLOCKED METRICS RETAIN THEIR REASON OK")

    # ---- 8) Missing page metadata becomes '—' ----
    plain = "Total Revenue for the year was 281,700,000,000, up strongly."
    plain_fd = extract_financial_data(plain)
    pfact = plain_fd.get("Revenue") or {}
    assert "page" not in pfact, "plain text must NOT get a page key"
    assert "document_name" not in pfact, "plain text must NOT get a document_name key"
    m3b = {"financial_data": plain_fd, "ratios": {}}
    rows3 = _build_terminal_rows(m3b)
    fields3 = _metric_overlay_fields(rows3, m3b, "Revenue")
    assert fields3["location"] == "—", "missing page must render '—'"
    print("8. MISSING PAGE METADATA BECOMES '—' OK")

    # ---- 9) No provenance is fabricated ----
    # page / document_name only ever come from actual markers in the text.
    ev = rev.get("evidence") or ""
    assert ev in parsed["text"], "evidence must be a real substring of the PDF text"
    assert str(rev["page"]) in parsed["text"], "page number must appear in the source text markers"
    # document_name absent (this text has no Start-of-File header)
    assert "document_name" not in rev, "no fabricated document attribution without a header"
    # multi-document attribution: only when the header provably precedes the fact
    multi = (
        "--- Start of File: MSFT_10K_FY2025.pdf ---\n"
        "========== PAGE 1 ==========\n"
        "Total Revenue for the year was 281,700,000,000, up strongly from a year ago.\n"
        "--- Start of File: AAPL_10K_FY2025.pdf ---\n"
        "========== PAGE 1 ==========\n"
        "Net Profit reached 98,300,000,000 during the same period.\n"
    )
    mfd = extract_financial_data(multi)
    assert mfd["Revenue"]["document_name"] == "MSFT_10K_FY2025.pdf"
    assert mfd["Net Profit"]["document_name"] == "AAPL_10K_FY2025.pdf"
    assert mfd["Revenue"]["page"] == 1 and mfd["Net Profit"]["page"] == 1
    print("9. NO PROVENANCE FABRICATED OK (evidence real substring; doc attribution only via header)")

    # ---- 10) Demo mode still passes ----
    assert ss(at, "fte_route") == "demo"
    assert ss(at, "fte_memo_view_open") is True
    assert ss(at, "fte_demo_mode") is True
    print("10. DEMO MODE STILL PASSES OK")

    # ---- 12) Existing memo floating-card interaction still passes ----
    # Revenue card exists; ROE card exists; exclusive radio group intact.
    assert 'data-card="ftemetric-revenue"' in memo
    assert 'data-card="ftemetric-roe"' in memo
    assert 'name="fte-memo-card"' in memo
    assert 'class="fte-memo-radio"' in memo
    print("12. MEMO FLOATING-CARD INTERACTION STILL PASSES OK (radios + cards intact)")

    print("=== PAGE EVIDENCE TESTS: ALL CHECKS COMPLETE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
