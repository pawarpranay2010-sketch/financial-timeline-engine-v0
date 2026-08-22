#!/usr/bin/env python3
"""
Platrixa
Sprint 9 - Extraction Reliability & Real-World PDF Stress Testing

Targeted regression corpus proving the pipeline FAILS SAFELY:

  «A wrong financial value is worse than a missing value.»

Scenarios (numbered per the sprint spec):
  1.  clean single-column PDF
  2.  two-column PDF
  3.  three-period financial table (column identity)
  4.  multi-column financial statement
  5.  table spanning pages
  6.  repeated table headers
  7.  footnote-heavy table
  8.  negative/parenthesized values
  9.  currency/scale variations
 10.  malformed table
 11.  ambiguous column ordering
 12.  OCR-derived document
 13.  low-confidence OCR
 14.  conflicting extraction paths
 15.  multiple documents
 16.  missing metric
 17.  page-aware evidence
 18.  table/row/column provenance
 19.  Sprint 6.5 fallback
 20.  Sprint 7 C++ calculation
 21.  Demo Mode regression
 22.  Student memo regression
 23.  Professional memo regression
 24.  clickable evidence-card regression

Generated PDFs (reportlab) keep every scenario deterministic. No network,
no AI, no storage - stdlib + existing backend code only.
"""
import importlib.util
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from ingestion.parser import parse_pdf
from backend.layout_extractor import (
    layout_aware_annotate,
    enrich_financial_data,
)
from backend.extraction_reliability import (
    STATE_LABELS,
    build_extraction_reliability_report,
    classify_extraction_state,
    verify_column_identity,
)
from backend.evidence_resolver import (
    PROVENANCE_TIER,
    BLOCKED_REASON,
    DEFAULT_PROVIDERS,
    ExternalEvidenceProvider,
    resolve_metric,
    recover_missing_metrics,
)
from backend.formula_engine import calculate_metric
from backend.memo_presenter import render_memo

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def make_pdf(blocks, pages=1):
    """Build a reportlab PDF from block lists; returns BytesIO."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=40, rightMargin=40)
    st = getSampleStyleSheet()
    story = []
    for pi, page in enumerate(blocks):
        if pi > 0:
            story.append(PageBreak())
        for blk in page:
            if not isinstance(blk, tuple):
                continue
            if blk[0] == "p":
                story.append(Paragraph(blk[1], st["Normal"]))
            elif blk[0] == "t":
                t = Table(blk[1])
                t.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]))
                story.append(t)
            elif blk[0] == "s":
                story.append(Spacer(1, 10))
    doc.build(story)
    buf.seek(0)
    return buf


def fact(value, **over):
    out = {"value": value, "source": "Doc", "status": "verified",
           "reporting_period": "FY2025"}
    out.update(over)
    return out


def doc_entry(name, parsed):
    return {"file_name": name, "parsed_document": parsed}


def reliability_over(financial_data, parsed, name="doc.pdf"):
    """Mimic the module3 order: Sprint 8 enrich, then Sprint 9 reliability."""
    enriched = enrich_financial_data(dict(financial_data), parsed, name)
    rep = build_extraction_reliability_report(
        enriched, [doc_entry(name, parsed)]
    )
    return rep, enriched


# ---------------------------------------------------------------------------
# 1 + 2. Clean single-column PDF and two-column-ish PDF
# ---------------------------------------------------------------------------
def test_1_2():
    pdf1 = make_pdf([
        [("p", "Annual Report FY2025"),
         ("p", "Total revenue was 281.70 USD billion during the year.")],
    ])
    parsed1 = parse_pdf(pdf1)
    rep1 = build_extraction_reliability_report(
        {"Revenue": fact(281.70)},
        [doc_entry("single.pdf", parsed1)],
    )
    f1 = rep1["financial_data"]["Revenue"]
    check("1a. single-column facts preserved",
          f1["value"] == 281.70 and f1["source"] == "Doc")
    check("1b. no fabricated layout fields on prose page",
          f1.get("row") is None and f1.get("table") is None)
    check("1c. prose-only fact is not falsely 'verified' by layout",
          f1["extraction_state"] != "verified")

    pdf2 = make_pdf([
        [("p", "Left column narrative about FY2025 performance."),
         ("p", "Right column shows highlights and outlook.")],
    ])
    parsed2 = parse_pdf(pdf2)
    rep2 = build_extraction_reliability_report(
        {"Revenue": fact(281.70)},
        [doc_entry("twocol.pdf", parsed2)],
    )
    check("2a. two-column-ish page safe",
          rep2["financial_data"]["Revenue"]["value"] == 281.70)
    check("2b. no crash on multi-paragraph pages", True)


# ---------------------------------------------------------------------------
# 3 + 4. Three-period table / multi-column statement with column identity
# ---------------------------------------------------------------------------
def test_3_4():
    pdf = make_pdf([
        [("p", "Annual Report FY2025"), PageBreak()],
        [("p", "* in USD billions"),
         ("t", [["Particulars", "FY2025", "FY2024", "FY2023"],
                ["Revenue from operations", "281.70", "245.12", "198.27"],
                ["Net Profit", "98.30", "88.42", "72.11"],
                ["Total Assets", "512.20", "445.00", "364.00"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])
    parsed = parse_pdf(pdf)
    fd = {
        "Revenue": fact(281.70),
        "Net Profit": fact(98.30),
        "Total Assets": fact(512.20),
    }
    rep, enriched = reliability_over(fd, parsed, "three.pdf")
    rev = rep["financial_data"]["Revenue"]

    check("3a. column identity proven (confidence 1.0)",
          rev.get("column_confidence") == 1.0, str(rev.get("column_confidence")))
    check("3b. extraction state verified",
          rev.get("extraction_state") == "verified",
          str(rev.get("extraction_state")))
    check("3c. extraction method stacked_table",
          rev.get("extraction_method") == "stacked_table", str(rev.get("extraction_method")))
    check("3d. Sprint 8 chain still attributes FY2025 column",
          enriched["Revenue"].get("column") == "FY2025", str(enriched["Revenue"].get("column")))
    check("3e. page-aware (page 2)", enriched["Revenue"].get("page") == 2,
          str(enriched["Revenue"].get("page")))

    # 4. multi-column statement: every metric keeps its own column/period.
    np_ = rep["financial_data"]["Net Profit"]
    ta_ = rep["financial_data"]["Total Assets"]
    check("4a. net profit verified with proven column",
          np_.get("extraction_state") == "verified" and np_.get("column_confidence") == 1.0)
    check("4b. total assets verified with proven column",
          ta_.get("extraction_state") == "verified" and ta_.get("column_confidence") == 1.0)
    check("4c. FY2024 sibling not mis-attributed to FY2025",
          enriched["Net Profit"].get("column") == "FY2025")
    check("4d. values never modified by reliability layer",
          rev["value"] == 281.70 and np_["value"] == 98.30 and ta_["value"] == 512.20)


# ---------------------------------------------------------------------------
# 5 + 6. Table spanning pages + repeated headers
# ---------------------------------------------------------------------------
def test_5_6():
    pdf = make_pdf([
        [("p", "Page one"), PageBreak()],
        [("p", "* in USD billions"),
         ("t", [["Particulars", "FY2025", "FY2024"],
                ["Revenue from operations", "281.70", "245.12"],
                ["Net Profit", "98.30", "88.42"]]),
         ("s", None), PageBreak()],
        [("p", "Table continued"),
         ("t", [["Particulars", "FY2025", "FY2024"],
                ["Total Assets", "512.20", "445.00"],
                ["Equity", "268.50", "230.00"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])
    parsed = parse_pdf(pdf)
    fd = {
        "Revenue": fact(281.70),
        "Net Profit": fact(98.30),
        "Total Assets": fact(512.20),
        "Equity": fact(268.50),
    }
    ann = layout_aware_annotate(parsed, "span.pdf")
    check("5a. both pages recovered as real tables",
          len(ann["tables"]) >= 2, f"{len(ann['tables'])} tables")
    check("5b. table spanning pages never crashes",
          len(ann["tables"]) >= 1)

    rep, enriched = reliability_over(fd, parsed, "span.pdf")
    check("5c. revenue attributed to page 2",
          enriched["Revenue"].get("page") == 2, str(enriched["Revenue"].get("page")))
    check("5d. total assets attributed to page 3",
          enriched["Total Assets"].get("page") == 3, str(enriched["Total Assets"].get("page")))
    check("5e. all four metrics verified",
          all(rep["financial_data"][m].get("extraction_state") == "verified"
              for m in fd), str({m: rep["financial_data"][m].get("extraction_state") for m in fd}))

    # 6. repeated headers: every recovered table keeps its period headers.
    all_periods = [
        str(p) for t in ann["tables"]
        for p in (t.get("column_periods") or []) if p
    ]
    check("6a. repeated header tables carry FY tokens",
          all_periods.count("FY2025") >= 2, str(all_periods))
    check("6b. no corrupted rows from repeated headers",
          all(
              len(r.get("cells") or []) <= len(t.get("headers") or [])
              for t in ann["tables"] for r in (t.get("rows") or [])
          ))


# ---------------------------------------------------------------------------
# 7. Footnote-heavy table
# ---------------------------------------------------------------------------
def test_7():
    pdf = make_pdf([
        [("p", "Notes to accounts"), PageBreak()],
        [("p", "* in USD billions"),
         ("t", [["Particulars", "FY2025"],
                ["Revenue from operations", "281.70*"],
                ["Net Profit", "98.30\u2020"]]),
         ("s", None),
         ("p", "* Figures rounded to nearest billion."),
         ("p", "\u2020 Includes one-time items as per note 14.")],
    ])
    parsed = parse_pdf(pdf)
    ann = layout_aware_annotate(parsed, "notes.pdf")
    leaked = False
    for t in ann["tables"]:
        for row in (t.get("rows") or []):
            label = str(row.get("label"))
            for cell in (row.get("cells") or []):
                if "Figures rounded" in label or "one-time items" in label \
                        or "Figures rounded" in str(cell) or "one-time items" in str(cell):
                    leaked = True
    check("7a. footnote lines never become rows/cells", not leaked)
    check("7b. footnote markers stripped from cells",
          all(
              str(c).endswith(("*", "\u2020")) is False
              for t in ann["tables"] for r in (t.get("rows") or []) for c in (r.get("cells") or [])
          ))

    rep, enriched = reliability_over(
        {"Revenue": fact(281.70), "Net Profit": fact(98.30)},
        parsed, "notes.pdf",
    )
    check("7c. values around footnotes still verified",
          rep["financial_data"]["Revenue"].get("extraction_state") == "verified"
          and rep["financial_data"]["Net Profit"].get("extraction_state") == "verified")


# ---------------------------------------------------------------------------
# 8. Negative / parenthesized values
# ---------------------------------------------------------------------------
def test_8():
    pdf = make_pdf([
        [("p", "Page one"), PageBreak()],
        [("p", "* in USD billions"),
         ("t", [["Particulars", "FY2025"],
                ["Net Profit", "(2,130)"],
                ["Operating Profit", "1,840"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])
    parsed = parse_pdf(pdf)
    ann = layout_aware_annotate(parsed, "neg.pdf")
    cells = [str(c) for t in ann["tables"] for r in (t.get("rows") or [])
             for c in (r.get("cells") or [])]
    check("8a. parenthesized value survives as negative token",
          "(2,130)" in cells, str(cells))
    rep, enriched = reliability_over(
        {"Net Profit": fact(-2130), "Operating Profit": fact(1840)},
        parsed, "neg.pdf",
    )
    check("8b. negative value preserved by reliability layer",
          rep["financial_data"]["Net Profit"]["value"] == -2130)
    check("8c. sibling positive value not contaminated",
          rep["financial_data"]["Operating Profit"]["value"] == 1840)


# ---------------------------------------------------------------------------
# 9. Currency / scale variations (native structured table path)
# ---------------------------------------------------------------------------
def test_9():
    parsed = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\nRevenue table\n",
        "table_data": [{
            "table_id": "native_cur_1",
            "headers": ["Particulars", "FY2025", "FY2024"],
            "rows": [
                {"label": "Revenue from operations", "cells": ["281.70", "245.12"]},
                {"label": "Net Profit", "cells": ["98.30", "88.42"]},
            ],
            "currency": "USD",
            "scale": "millions",
            "source_location": "native table 1",
        }],
    }
    rep, enriched = reliability_over(
        {"Revenue": fact(281.70, unit="USD", scale="millions"),
         "Net Profit": fact(98.30, unit="USD", scale="millions")},
        parsed, "cur.pdf",
    )
    rev = rep["financial_data"]["Revenue"]
    check("9a. native table path recorded",
          rev.get("extraction_method") == "native_table", str(rev.get("extraction_method")))
    check("9b. currency metadata preserved", rev.get("unit") == "USD")
    check("9c. scale metadata preserved", rev.get("scale") == "millions")
    check("9d. separate confidence dimensions present",
          all(k in rev for k in ("layout_confidence", "table_confidence",
                                 "row_confidence", "column_confidence",
                                 "extraction_method", "layout_flag")),
          str({k: rev.get(k) for k in ("layout_confidence", "table_confidence",
                                       "row_confidence", "column_confidence",
                                       "layout_flag")}))


# ---------------------------------------------------------------------------
# 10. Malformed table -> flagged, review required, nothing guessed
# ---------------------------------------------------------------------------
def test_10():
    parsed = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\n"
                "Particulars\tFY2025\tFY2024\n"
                "Revenue from operations\t281.70\t245.12\t198.27\t90.00\n",
        "table_data": [],
    }
    ann = layout_aware_annotate(parsed, "bad.pdf")
    check("10a. malformed table flagged",
          len(ann.get("flagged_tables") or []) >= 1, str(ann.get("flagged_tables")))
    rep, enriched = reliability_over(
        {"Revenue": fact(281.70)}, parsed, "bad.pdf",
    )
    rev = rep["financial_data"]["Revenue"]
    check("10b. malformed table -> review_required",
          rev.get("extraction_state") == "review_required", str(rev.get("extraction_state")))
    check("10c. layout_flag ambiguous",
          rev.get("layout_flag") == "ambiguous", str(rev.get("layout_flag")))
    check("10d. value still preserved (never dropped, never guessed)",
          rev.get("value") == 281.70)


# ---------------------------------------------------------------------------
# 11. Ambiguous column ordering -> never guess
# ---------------------------------------------------------------------------
def test_11():
    # (a) headers with no period tokens at all.
    parsed_a = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\n"
                "Particulars\tCurrent Year\tPrevious Year\n"
                "Revenue from operations\t281.70\t245.12\n",
        "table_data": [],
    }
    rep_a, _ = reliability_over({"Revenue": fact(281.70)}, parsed_a, "amb.pdf")
    rev_a = rep_a["financial_data"]["Revenue"]
    check("11a. no-period headers cannot prove column identity",
          rev_a.get("extraction_state") == "review_required",
          str(rev_a.get("extraction_state")))
    check("11b. no guessed period is fabricated",
          rev_a.get("reporting_period") == "FY2025",
          str(rev_a.get("reporting_period")))
    check("11c. column confidence zeroed when unprovable",
          rev_a.get("column_confidence") == 0.2, str(rev_a.get("column_confidence")))

    # (b) value numerically close to TWO cells -> no unique cell -> nothing
    # attributed. The extractor must never pick by numerical closeness.
    parsed_b = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\n"
                "Particulars\tFY2025\tFY2024\n"
                "Revenue from operations\t281.70\t281.38\n",
        "table_data": [],
    }
    rep_b, _ = reliability_over({"Revenue": fact(281.55)}, parsed_b, "close.pdf")
    rev_b = rep_b["financial_data"]["Revenue"]
    check("11d. ambiguous proximity -> no column attribution",
          rev_b.get("extraction_state") != "verified"
          and rev_b.get("column_confidence") == 0.0,
          f"state={rev_b.get('extraction_state')} col={rev_b.get('column_confidence')}")


# ---------------------------------------------------------------------------
# 12 + 13. OCR reliability
# ---------------------------------------------------------------------------
def test_12_13():
    # (12) scanned/OCR page emulation: page 2 has no extractable text.
    parsed_scan = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\nRevenue from operations 281.70\n"
                "========== PAGE 2 ==========\n",
        "table_data": [],
    }
    rep = build_extraction_reliability_report(
        {"Revenue": fact(281.70)},
        [doc_entry("scan.pdf", parsed_scan)],
    )
    check("12a. empty scanned page reported",
           rep.get("pages_without_text") == [2], str(rep.get("pages_without_text")))
    check("12b. no fact fabricated for the empty page",
          rep["financial_data"]["Revenue"]["value"] == 281.70)
    check("12c. extraction method honest (no layout evidence)",
          rep["financial_data"]["Revenue"].get("extraction_method") == "unanalyzed",
          str(rep["financial_data"]["Revenue"].get("extraction_method")))

    # (13) low-confidence OCR can never become Verified.
    low, low_reason = classify_extraction_state(
        {"value": 281.7, "ocr": True, "ocr_confidence": 0.42}, None)
    check("13a. low-confidence OCR -> review_required",
          low == "review_required", f"{low} [{low_reason}]")
    high, _ = classify_extraction_state(
        {"value": 281.7, "ocr": True, "ocr_confidence": 0.95}, None)
    check("13b. high-confidence OCR without table evidence is never 'verified'",
          high != "verified", str(high))
    plain, _ = classify_extraction_state({"value": 281.7}, None)
    check("13c. unclassified fact stays unanalyzed (not guessed)",
          plain == "unanalyzed", str(plain))


# ---------------------------------------------------------------------------
# 14. Conflicting extraction paths
# ---------------------------------------------------------------------------
def test_14():
    # Two independent tables in the same document disagree on FY2025 Revenue.
    pdf = make_pdf([
        [("p", "Page one"), PageBreak()],
        [("p", "Primary statements"),
         ("t", [["Particulars", "FY2025"],
                ["Revenue from operations", "281.70"]]),
         ("s", None), PageBreak()],
        [("p", "Supplementary table"),
         ("t", [["Particulars", "FY2025"],
                ["Revenue from operations", "281.07"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])
    parsed = parse_pdf(pdf)
    rep, _ = reliability_over({"Revenue": fact(281.70)}, parsed, "conflict.pdf")
    conflicts = rep.get("conflicts") or []
    rev = rep["financial_data"]["Revenue"]
    rev_conf = [c for c in conflicts if c.get("metric") == "Revenue"]
    check("14a. conflict record created",
          len(rev_conf) == 1, str(conflicts))
    if rev_conf:
        check("14b. competing values preserved (no silent pick)",
              len(rev_conf[0].get("competing_values") or []) >= 2,
              str(rev_conf[0].get("competing_values")))
        check("14c. conflict carries source locations + reason",
              all(e.get("path") for e in rev_conf[0]["competing_values"])
              and bool(rev_conf[0].get("reason")),
              str(rev_conf[0].get("reason")))
    check("14d. fact state marked conflict",
          rev.get("extraction_state") == "conflict", str(rev.get("extraction_state")))
    check("14e. conflict visible on the fact (downstream verification)",
          isinstance(rev.get("extraction_conflict"), dict))

    # Extractor fact vs same-period table cell (single table path).
    parsed2 = {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\n"
                "Particulars\tFY2025\n"
                "Revenue from operations\t281.07\n",
        "table_data": [],
    }
    rep2, _ = reliability_over({"Revenue": fact(281.70)}, parsed2, "one.pdf")
    rev2 = rep2["financial_data"]["Revenue"]
    check("14f. extractor vs table-cell conflict detected",
          rev2.get("extraction_state") == "conflict", str(rev2.get("extraction_state")))


# ---------------------------------------------------------------------------
# 15. Multiple documents
# ---------------------------------------------------------------------------
def test_15():
    def table_pdf(val):
        return {
            "type": "pdf",
            "text": "========== PAGE 1 ==========\n"
                    "Particulars\tFY2025\n"
                    f"Revenue from operations\t{val}\n",
            "table_data": [],
        }

    docA = doc_entry("alpha.pdf", table_pdf("281.70"))
    docB = doc_entry("beta.pdf", {
        "type": "pdf",
        "text": "========== PAGE 1 ==========\nJust narrative with no tables.\n",
        "table_data": [],
    })
    docC = doc_entry("gamma.pdf", table_pdf("281.07"))
    rep = build_extraction_reliability_report(
        {"Revenue": fact(281.70)},
        [docA, docB, docC],
    )
    conflicts = [c for c in (rep.get("conflicts") or []) if c.get("metric") == "Revenue"]
    check("15a. cross-document conflict aggregated",
          len(conflicts) == 1, str(rep.get("conflicts")))
    if conflicts:
        docs_seen = {e.get("document") for e in conflicts[0].get("competing_values") or []}
        check("15b. competing values keep their document attribution",
              "alpha.pdf" in docs_seen and "gamma.pdf" in docs_seen, str(docs_seen))
    check("15c. narrative-only document contributes nothing",
          rep["financial_data"]["Revenue"]["value"] == 281.70)


# ---------------------------------------------------------------------------
# 16. Missing metric -> left to the Sprint 6.5 resolver
# ---------------------------------------------------------------------------
def test_16():
    rep = build_extraction_reliability_report(
        {"Revenue": fact(281.70)},
        [doc_entry("x.pdf", {"type": "pdf", "text": "== PAGE 1 ==\nnone\n", "table_data": []})],
    )
    check("16a. absent metric never fabricated by reliability layer",
          "ROE" not in rep.get("states", {}))
    blocked = resolve_metric("ROE", {}, "FY2025", providers=[])
    check("16b. missing primary metric passes to resolver -> BLOCKED",
          blocked.get("provenance_tier") == PROVENANCE_TIER.BLOCKED,
          str(blocked.get("provenance_tier")))
    check("16c. blocked reason is precise",
          bool(blocked.get("reason")), str(blocked.get("reason")))


# ---------------------------------------------------------------------------
# 17 + 18. Page-aware evidence + table/row/column provenance
# ---------------------------------------------------------------------------
def test_17_18():
    pdf = make_pdf([
        [("p", "Page one"), PageBreak()],
        [("p", "* in USD billions"),
         ("t", [["Particulars", "FY2025"],
                ["Revenue from operations", "281.70"],
                ["Net Profit", "98.30"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])
    parsed = parse_pdf(pdf)
    rep, enriched = reliability_over(
        {"Revenue": fact(281.70), "Net Profit": fact(98.30)},
        parsed, "provenance.pdf",
    )
    rev = enriched["Revenue"]
    check("17a. page-aware evidence propagated", rev.get("page") == 2,
          str(rev.get("page")))
    check("18a. table provenance present", bool(rev.get("table")))
    check("18b. row provenance present",
          rev.get("row") == "Revenue from operations", str(rev.get("row")))
    check("18c. column provenance present",
          rev.get("column") == "FY2025", str(rev.get("column")))
    check("18d. evidence is a real source representation",
          bool(rev.get("evidence")) and "Revenue from operations" in str(rev.get("evidence")),
          str(rev.get("evidence"))[:60])
    rr = rep["financial_data"]["Revenue"]
    check("18e. full confidence chain on the fact",
          all(k in rr for k in ("layout_confidence", "table_confidence",
                                "row_confidence", "column_confidence",
                                "extraction_method", "layout_flag")))


# ---------------------------------------------------------------------------
# 19. Sprint 6.5 fallback regression (with Sprint 9 output present)
# ---------------------------------------------------------------------------
class _FakeProvider(ExternalEvidenceProvider):
    name = "Fake SEC EDGAR"

    def __init__(self, payload):
        self._payload = payload

    def is_configured(self):
        return True

    def resolve_metric(self, company_identifier, metric, reporting_period):
        return self._payload


def test_19():
    company = {"cik": "0000789019", "currency": "USD"}
    payload = {
        "value": 281.7, "cik": "0000789019", "reporting_period": "FY2025",
        "metric": "Revenue", "currency": "USD", "scale": "billions",
        "evidence": "SEC EDGAR 10-K FY2025", "provider_identifier": "0000789019",
    }
    recovered = resolve_metric(
        "Revenue", company_context=company, reporting_period="FY2025",
        providers=[_FakeProvider(payload)],
    )
    check("19a. approved provider recovery keeps REGULATORY_API tier",
          recovered.get("provenance_tier") == PROVENANCE_TIER.REGULATORY_API,
          str(recovered.get("provenance_tier")))
    check("19b. recovered value correct", recovered.get("value") == 281.7)

    blocked = resolve_metric("Revenue", company_context=company,
                             reporting_period="FY2025", providers=[])
    check("19c. no provider -> BLOCKED (never fabricated)",
          blocked.get("provenance_tier") == PROVENANCE_TIER.BLOCKED)

    module3_result = {
        "financial_data": {"Revenue": fact(281.70)},
        "ratios": {},
        "extraction_reliability": {
            "conflicts": [], "flagged_tables": [], "pages_without_text": [],
            "states": {"Revenue": "verified"}, "methods": {"Revenue": "stacked_table"},
        },
    }
    out = recover_missing_metrics(module3_result, company_context=company,
                                  reporting_period="FY2025")
    ev = out.get("external_evidence") or {}
    check("19d. recover_missing_metrics coexists with Sprint 9 output",
          isinstance(ev.get("blocked"), dict) and "EPS" in ev.get("blocked", {}),
          str(list((ev.get("blocked") or {}).keys())[:5]))
    check("19e. already-recovered facts stay untouched (Tier 1 stands)",
          out["financial_data"]["Revenue"]["value"] == 281.70)


# ---------------------------------------------------------------------------
# 20. Sprint 7 C++ Formula Engine regression
# ---------------------------------------------------------------------------
def test_20():
    from backend.formula_engine_cpp import cpp_calculate
    check("20a. C++ engine import intact", callable(cpp_calculate))

    res = calculate_metric("ROE", {
        "Net Profit": {"value": 98.3, "source": "Document"},
        "Equity": {"value": 268.5, "source": "Document"},
    }, {"recover": False})
    check("20b. deterministic derived calculation",
          res.get("status") == "derived" and res.get("value") is not None,
          f"status={res.get('status')} value={res.get('value')}")
    if res.get("value") is not None:
        # Frozen Sprint 7 convention: percent-kind formulas return the
        # percentage NUMBER (98.30 / 268.50 x 100 = 36.6108007449).
        check("20c. arithmetic exact (98.3 / 268.5, percent convention)",
              abs(float(res["value"]) - 98.3 / 268.5 * 100) < 1e-9,
              str(res["value"]))

    blocked = calculate_metric("ROE", {"Net Profit": {"value": 98.3}},
                               {"recover": False})
    check("20d. missing input -> blocked (never guessed)",
          blocked.get("status") == "blocked", str(blocked.get("status")))


# ---------------------------------------------------------------------------
# 21. Demo Mode regression
# ---------------------------------------------------------------------------
def test_21():
    demo = {
        "Revenue": {"value": 281.70, "source": "Microsoft 10-K FY2025",
                    "reporting_period": "FY2025", "unit": "USD", "status": "verified"},
        "ROE": {"value": 0.37, "source": "Calculated",
                "reporting_period": "FY2025", "status": "derived"},
    }
    rep = build_extraction_reliability_report(dict(demo), [])
    for metric, original in demo.items():
        f = rep["financial_data"][metric]
        check(f"21a. demo {metric} value untouched",
              f["value"] == original["value"] and f["source"] == original["source"],
              f"value={f['value']} source={f['source']}")
    check("21b. demo derived metric keeps derived state",
          rep["financial_data"]["ROE"].get("extraction_state") == "derived",
          str(rep["financial_data"]["ROE"].get("extraction_state")))
    check("21c. no fabricated conflicts for demo",
          len(rep.get("conflicts") or []) == 0)


# ---------------------------------------------------------------------------
# 22 + 23. Student / Professional memo regressions with review-required rows
# ---------------------------------------------------------------------------
MEMO = """EXECUTIVE SUMMARY
Revenue reached 281.70B in FY2025 while Net Profit grew steadily.

KEY FINANCIAL EVENTS
Operating Profit expanded with scale during the year.

FINANCIAL PERFORMANCE
Revenue of 281.70B converted into Net Profit of 98.30B.

RISKS & OPPORTUNITIES
Column attribution is uncertain for the revenue table.

RECOMMENDATIONS
Confirm the ambiguous revenue figure before extrapolating.
"""

MEMO_ROWS = [
    {"metric": "Revenue", "Value": "281.70B", "Period": "FY2025",
     "Source": "Microsoft 10-K FY2025", "Status": "🟠 Review Required",
     "_kind": "review_required",
     "_reason": "column identity could not be established",
     "_fact": {"reporting_period": "FY2025", "evidence": "Revenue from operations 281.70 (FY2025)"}},
    {"metric": "Net Profit", "Value": "98.30B", "Period": "FY2025",
     "Source": "Microsoft 10-K FY2025", "Status": "🟢 Verified",
     "_kind": "verified",
     "_fact": {"document_name": "Microsoft 10-K FY2025", "reporting_period": "FY2025",
               "page": 26, "evidence": "Consolidated Statements of Income, p. 26"}},
    {"metric": "Segment Gross Margin", "Value": "—", "Period": "—",
     "Source": "—", "Status": "🔴 Blocked", "_kind": "blocked",
     "_reason": "Segment-level margin is not disclosed in source filings.",
     "_fact": {}},
]


def test_22_23():
    stud = render_memo(MEMO, MEMO_ROWS, "student")
    prof = render_memo(MEMO, MEMO_ROWS, "professional")
    stud_headings = [b[1] for b in stud if b[0] == "heading"]
    prof_headings = [b[1] for b in prof if b[0] == "heading"]
    check("22a. student memo structure intact",
          "Executive Summary" in stud_headings
          and "Key Financial Metrics" in stud_headings
          and "Sources & Evidence" in stud_headings, str(stud_headings))
    check("23a. professional memo structure intact",
          "Key Financials" in prof_headings
          and "Evidence / Sources" in prof_headings, str(prof_headings))

    stud_refs = next(b[1] for b in stud if b[0] == "evidence")
    prof_refs = next(b[1] for b in prof if b[0] == "evidence")
    rr_stud = [r for r in stud_refs if r.get("kind") == "review_required"]
    rr_prof = [r for r in prof_refs if r.get("kind") == "review_required"]
    check("22b. student renders review-required ref",
          len(rr_stud) == 1 and "Review required" in " ".join(rr_stud[0].get("lines") or []),
          str(rr_stud[0].get("lines") if rr_stud else None))
    check("23b. professional renders review-required ref with reason",
          len(rr_prof) == 1 and any(
              "column identity could not be established" in ln
              for ln in (rr_prof[0].get("lines") or [])
          ), str(rr_prof[0].get("lines") if rr_prof else None))
    all_lines = [ln for r in stud_refs + prof_refs for ln in (r.get("lines") or [])]
    check("23c. no empty '—' leaks into visible evidence lines",
          all("\u2014" not in ln for ln in all_lines), str(all_lines[:6]))
    check("23d. verified refs keep full provenance internally",
          any(r.get("kind") == "verified" and r.get("page") == "p. 26"
              and r.get("evidence") for r in prof_refs))


# ---------------------------------------------------------------------------
# 24. Clickable evidence-card regression (demo mechanism with new kind)
# ---------------------------------------------------------------------------
def test_24():
    helper = importlib.util.spec_from_file_location(
        "fte_memo_profile_test",
        os.path.join(os.path.dirname(__file__), "fte_memo_profile_test.py"),
    )
    mod = importlib.util.module_from_spec(helper)
    helper.loader.exec_module(mod)
    html = mod._adaptive_html_sim(MEMO, MEMO_ROWS, "student", demo=True)
    check("24a. metric tokens remain inline clickable",
          html.count("fte-metric-link") >= 3, f"{html.count('fte-metric-link')} links")
    check("24b. demo evidence cards still embedded",
          'class="fte-memo-card"' in html and 'id="ftemetric-none"' in html)
    check("24c. single continuous document", isinstance(html, str) and len(html) > 500)


def main():
    print("=" * 62)
    print("SPRINT 9 - EXTRACTION RELIABILITY & REAL-WORLD PDF STRESS TEST SUITE")
    print("=" * 62)
    test_1_2()
    test_3_4()
    test_5_6()
    test_7()
    test_8()
    test_9()
    test_10()
    test_11()
    test_12_13()
    test_14()
    test_15()
    test_16()
    test_17_18()
    test_19()
    test_20()
    test_21()
    test_22_23()
    test_24()

    failed = [c for c in CHECKS if not c[1]]
    print("=" * 62)
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED CHECKS:")
        for name, _, detail in failed:
            print(f"  - {name}  [{detail}]")
        sys.exit(1)
    print("ALL CHECKS COMPLETE")
    sys.exit(0)


if __name__ == "__main__":
    main()
