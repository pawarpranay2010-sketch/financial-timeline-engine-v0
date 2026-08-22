#!/usr/bin/env python3
"""
Platrixa
Sprint 9.1 - Mandatory Memo Path Coverage

Sprint 9.1 MUST support and verify BOTH memo paths:

PART A - API / REAL MEMO (full reliability verification)
  PDF/document input
  -> extraction
  -> Sprint 9 reliability analysis
  -> Module 3 (run_module3)
  -> _build_terminal_rows()
  -> Classic / Student / Professional memo
  -> clickable metric
  -> floating evidence card
  Prove the "review_required" AND extraction-conflict states end-to-end
  through the REAL deterministic pipeline (no AI, no API keys).

PART B - DEMO MEMO (full interaction + regression verification)
  - Demo Memo opens normally and remains ONE continuous document.
  - Demo metric tokens stay inline-clickable; clicking a metric opens
    the floating evidence card; clicking another metric replaces the
    same card; the × control AND the backdrop close the card.
  - Student and Professional adaptive formats continue working with
    correct evidence/source sections.
  - No AI / API key is required and no random external source is
    introduced.
  - Existing demo financial values remain unchanged and NO Sprint 9
    reliability metadata is fabricated into the production demo facts.
  - The synthetic "review_required" demo fixture is isolated to this
    test only -- the production demo dataset (_demo_module3_result) is
    never modified (verified by a before/after deep-equality check).

The AI narrative generation step (which needs provider keys) is OUT of
scope: the memo TEXT is a deterministic stand-in, and every rendering
path the app uses (memo view -> rows -> Classic/adaptive HTML -> metric
links -> evidence card fields) is exercised exactly as the app calls it.
"""

import importlib.util
import io
import json
import os
import re
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from streamlit.testing.v1 import AppTest

from ingestion.parser import parse_pdf
from backend.module3_controller import run_module3

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _ss(at, key, default=None):
    """Safe AppTest session_state read (the proxy has no .get())."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


# ---------------------------------------------------------------------------
# App module loading (browser-free): exec 'app (1) (9).py' with a stubbed
# streamlit so the pure helper functions (_build_terminal_rows,
# _metric_overlay_fields, _provenance_tray_html, _memo_adaptive_html,
# _memo_metric_html, _demo_module3_result, ...) can be exercised without a
# running Streamlit runtime. The real streamlit module is restored
# immediately afterwards, so AppTest-based tests keep the real runtime.
# ---------------------------------------------------------------------------

_REAL_STREAMLIT = None
_APP = None
_APP_STUB_SS = {}


class _Passthrough:
    """Callable stand-in for any streamlit API: used as a plain call
    (widget) it returns a harmless function; used as a decorator or
    decorator factory (@st.dialog(...), @st.cache_resource(...)) it
    returns the decorated function UNCHANGED."""

    def __call__(self, *a, **k):
        if len(a) == 1 and not k and callable(a[0]):
            return a[0]

        def deco(fn):
            return fn
        return deco


class _StubStreamlit(types.ModuleType):
    """Minimal streamlit stand-in: session_state is a plain dict; every
    other API is a pass-through no-op."""

    def __init__(self):
        super().__init__("streamlit")
        self._ss = _APP_STUB_SS

    def __getattr__(self, name):
        if name == "session_state":
            return self._ss
        return _Passthrough()


def _load_app():
    """Exec the app file under a stubbed streamlit and return the module."""
    global _APP, _REAL_STREAMLIT
    if _APP is not None:
        return _APP
    import streamlit as _real  # real package must be importable
    _REAL_STREAMLIT = _real
    root = os.path.join(os.path.dirname(__file__), "..")
    stub = _StubStreamlit()
    sys.modules["streamlit"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "fte_app_under_test", os.path.join(root, "app (1) (9).py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.modules["streamlit"] = _REAL_STREAMLIT
    _APP = mod
    return mod


# ---------------------------------------------------------------------------
# Browser-free simulation of the demo memo's exclusive radio + label CSS
# (same semantics as scripts/fte_demo_test.py::simulate_card_visibility).
# ---------------------------------------------------------------------------

def simulate_card_visibility(memo_html, checked_id):
    """(radio_ids, card_ids, visible_card_ids) for a given checked radio."""
    rules = {}
    style_m = re.search(r"<style>(.*?)</style>", memo_html, re.S)
    if style_m:
        for m in re.finditer(
            r'#(ftemetric-[a-z0-9-]+):checked\s*~\s*\.fte-memo-card'
            r'\[data-card="(ftemetric-[a-z0-9-]+)"\]\s*\{\s*display:\s*block;\s*\}',
            style_m.group(1),
        ):
            rules[m.group(1)] = m.group(2)
    radios = set(re.findall(r'<input type="radio"[^>]*id="(ftemetric-[a-z0-9-]+)"[^>]*>', memo_html))
    cards = set(re.findall(r'class="fte-memo-card" role="dialog" data-card="(ftemetric-[a-z0-9-]+)"', memo_html))
    visible = {rules[checked_id]} if checked_id in rules else set()
    return radios, cards, visible


def make_pdf(blocks):
    """reportlab PDF from block lists; returns BytesIO."""
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


# ===========================================================================
# PART A - API / REAL MEMO: reliability states end-to-end
# ===========================================================================

# A1 fixture: malformed/ragged table -> flagged -> review_required. The
# parsed-dict shape is exactly what ingestion/parser.parse_pdf() returns
# (type/text/table_data), and this exact shape is proven to flag in
# scripts/fte_reliability_test.py (scenario 10).
PARSED_MALFORMED = {
    "type": "pdf",
    "text": (
        "========== PAGE 1 ==========\n"
        "Particulars\tFY2025\tFY2024\n"
        "Revenue from operations\t281.70\t245.12\t198.27\t90.00\n"
    ),
    "table_data": [],
}

MEMO_TEXT = (
    "EXECUTIVE SUMMARY\n"
    "Revenue reached 281.70B in FY2025 while Net Profit grew steadily.\n"
    "\n"
    "FINANCIAL PERFORMANCE\n"
    "Revenue of 281.70B converted into Net Profit of 98.30B.\n"
)


def test_a1_review_required_e2e():
    app = _load_app()
    _APP_STUB_SS["fte_demo_mode"] = False
    doc = {"file_name": "malformed.pdf", "parsed_document": PARSED_MALFORMED}
    result = run_module3(PARSED_MALFORMED["text"], [doc])

    # --- pipeline: reliability report attached, fact classified ---
    reliab = result.get("extraction_reliability") or {}
    check("a1a. module3 result carries the Sprint 9 reliability report",
          isinstance(reliab, dict) and "states" in reliab and "conflicts" in reliab)
    rev = (result.get("financial_data") or {}).get("Revenue") or {}
    check("a1b. Revenue classified review_required by the real pipeline",
          rev.get("extraction_state") == "review_required",
          str(rev.get("extraction_state")))
    check("a1c. structural reason attached to the fact",
          bool(rev.get("extraction_state_reason")),
          str(rev.get("extraction_state_reason"))[:80])
    check("a1d. value never modified by the reliability pass",
          rev.get("value") == 281.70, str(rev.get("value")))
    check("a1e. separate confidence dimensions present",
          all(k in rev for k in ("layout_confidence", "table_confidence",
                                 "row_confidence", "column_confidence",
                                 "extraction_method", "layout_flag")))

    # --- _build_terminal_rows -> review_required row ---
    rows = app._build_terminal_rows(result)
    rrow = next((r for r in rows if r["metric"] == "Revenue"), None)
    check("a1f. grid row kind review_required",
          rrow is not None and rrow.get("_kind") == "review_required",
          str(rrow.get("_kind") if rrow else None))
    check("a1g. grid row status 🟠 Review Required",
          rrow is not None and rrow.get("Status") == "🟠 Review Required",
          str(rrow.get("Status") if rrow else None))
    check("a1h. grid row carries the structural reason",
          rrow is not None and bool(rrow.get("_reason")),
          str((rrow or {}).get("_reason"))[:80])

    # --- floating evidence card fields ---
    fields = app._metric_overlay_fields(rows, result, "Revenue")
    check("a1i. evidence card origin = Extraction reliability",
          fields.get("origin") == "Extraction reliability", str(fields.get("origin")))
    check("a1j. evidence card carries the structural reason as note",
          bool(fields.get("note")) and fields.get("note") == (rrow or {}).get("_reason"),
          str(fields.get("note"))[:80])
    tray = app._provenance_tray_html(rows, result, "Revenue")
    check("a1k. provenance tray surfaces extraction reliability",
          "Extraction reliability" in tray and bool(fields.get("note")) and
          str(fields.get("note"))[:60] in tray)

    # --- Classic memo: clickable metric, one continuous document ---
    classic = app._memo_metric_html(rows, MEMO_TEXT, result)
    check("a1l. classic memo keeps inline clickable metric (real link)",
          '<a class="fte-metric-link"' in classic and "?fte_metric=" in classic,
          "link" if '<a class="fte-metric-link"' in classic else "no link")
    check("a1m. classic memo is one continuous document",
          classic.count('<div class="fte-memo-para">') >= 2
          and "fte-memo-cards" not in classic)

    # --- Student / Professional adaptive memos: evidence shows the reason ---
    for profile in ("student", "professional"):
        adaptive = app._memo_adaptive_html(rows, MEMO_TEXT, result, profile)
        check(f"a1n. {profile} memo renders review-required evidence line",
              "Review required" in adaptive, "found" if "Review required" in adaptive else "missing")
        check(f"a1o. {profile} memo shows the structural reason",
              str(fields.get("note"))[:60] in adaptive)
        check(f"a1p. {profile} memo keeps metric tokens clickable",
              'class="fte-metric-link"' in adaptive)
        evidence_lines = re.findall(
            r'<span class="fte-evidence-line">([^<]*)</span>', adaptive)
        check(f"a1q. {profile} memo evidence has no empty '—' leak",
              all("—" not in ln for ln in evidence_lines), str(evidence_lines[:4]))


# A2 fixture: one real PDF whose two independent tables disagree on the
# FY2025 Revenue figure (281.70 vs 281.07) -> extraction conflict. The
# narrative line guarantees the extractor finds Revenue=281.70 while the
# reliability layer sees both table cells.
def _conflict_pdf():
    return make_pdf([
        [("p", "Annual Report FY2025"),
         ("p", "Revenue was 281.70 billion dollars during fiscal 2025."),
         PageBreak()],
        [("p", "Primary statements"),
         ("t", [["Particulars", "FY2025"], ["Revenue from operations", "281.70"]]),
         ("s", None), PageBreak()],
        [("p", "Supplementary table"),
         ("t", [["Particulars", "FY2025"], ["Revenue from operations", "281.07"]]),
         ("s", None), ("p", "* Figures rounded.")],
    ])


def test_a2_conflict_e2e():
    app = _load_app()
    _APP_STUB_SS["fte_demo_mode"] = False
    parsed = parse_pdf(_conflict_pdf())
    doc = {"file_name": "conflict.pdf", "parsed_document": parsed}
    result = run_module3(parsed["text"], [doc])

    # --- pipeline: structured conflict record ---
    reliab = result.get("extraction_reliability") or {}
    conflicts = [c for c in (reliab.get("conflicts") or []) if c.get("metric") == "Revenue"]
    check("a2a. extraction conflict detected in the real pipeline",
          len(conflicts) >= 1, str(conflicts[:1])[:120])
    cfact = (result.get("financial_data") or {}).get("Revenue") or {}
    check("a2b. fact state = conflict",
          cfact.get("extraction_state") == "conflict", str(cfact.get("extraction_state")))
    check("a2c. conflict record attached to the fact (downstream visibility)",
          isinstance(cfact.get("extraction_conflict"), dict))
    check("a2d. no value silently chosen or modified",
          cfact.get("value") == 281.70, str(cfact.get("value")))

    # --- _build_terminal_rows -> conflict row ---
    rows = app._build_terminal_rows(result)
    crow = next((r for r in rows if r["metric"] == "Revenue"), None)
    check("a2e. grid row kind conflict",
          crow is not None and crow.get("_kind") == "conflict",
          str(crow.get("_kind") if crow else None))
    check("a2f. grid row status 🔵 Conflict",
          crow is not None and crow.get("Status") == "🔵 Conflict",
          str(crow.get("Status") if crow else None))

    # --- floating evidence card + memo evidence ---
    fields = app._metric_overlay_fields(rows, result, "Revenue")
    check("a2g. evidence card origin = Cross-document verification",
          fields.get("origin") == "Cross-document verification",
          str(fields.get("origin")))
    adaptive = app._memo_adaptive_html(rows, MEMO_TEXT, result, "student")
    check("a2h. student memo evidence flags the conflict",
          "Cross-document verification conflict" in adaptive,
          "found" if "Cross-document verification conflict" in adaptive else "missing")
    classic = app._memo_metric_html(rows, MEMO_TEXT, result)
    check("a2i. classic memo still renders with conflict rows present",
          'class="fte-metric-link"' in classic and "fte-memo-para" in classic)


# ===========================================================================
# PART B - DEMO MEMO: interaction + regression safety
# ===========================================================================

def _demo_expected_values():
    return {
        "281.70B", "98.30B", "125.50B", "13.05", "96.60B", "512.20B",
        "243.70B", "268.50B", "127.80B", "161.00B", "0.35", "0.37",
        "0.19", "0.36", "1.4", "—",
    }


def test_b1_demo_apptest():
    """Full demo-memo interaction + regression through the real app."""
    app = _load_app()
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app (1) (9).py"),
                           default_timeout=120)
    at.run()
    if at.exception:
        check("b1a. demo opens normally", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    at.button(key="fte_btn_demo").click().run()
    if at.exception:
        check("b1b. demo workspace opens", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    check("b1a. demo opens normally (no exception)", True)
    check("b1b. demo mode active without any API key",
          _ss(at, "fte_demo_mode") is True)

    # Demo grid: static values only, no Sprint 9 fabrication.
    rows = _ss(at, "fte_grid_rows") or []
    check("b1c. demo grid rows exist", bool(rows))
    row_values = {r["Value"] for r in rows}
    check("b1d. demo financial values unchanged (static-only)",
          row_values <= _demo_expected_values(),
          str(sorted(row_values - _demo_expected_values())))
    kinds = {r["_kind"] for r in rows}
    check("b1e. no Sprint 9 reliability states fabricated into demo grid",
          "review_required" not in kinds and "conflict" not in kinds,
          str(kinds))
    check("b1f. demo still AI-free on grid",
          not _ss(at, "provider_log"))

    # --- open the memo on the Intelligence page ---
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    if at.exception:
        check("b1g. demo Intelligence page opens", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    at.button(key="fte_btn_demo_memo").click().run()
    if at.exception:
        check("b1h. demo memo view opens", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    check("b1g. demo memo opens normally", _ss(at, "fte_memo_view_open") is True)
    draft = _ss(at, "fte_memo_draft") or ""
    check("b1h. demo memo is the byte-identical static sample (no AI path)",
          draft == app._FTE_DEMO_MEMO,
          "byte-identical" if draft == app._FTE_DEMO_MEMO else "DIFFERS")
    check("b1i. no AI provider called by demo memo",
          not _ss(at, "provider_log")
          and _ss(at, "ai_connected") is False)

    # --- continuous document + layout unchanged ---
    bodies = [str(m.value) for m in at.markdown]
    memo_bodies = [b for b in bodies if "fte-memo-para" in b]
    check("b1j. memo remains ONE continuous document",
          len(memo_bodies) == 1, f"{len(memo_bodies)} body blocks")
    joined = " ".join(bodies)
    check("b1k. memo layout structure unchanged (title/context/body)",
          "fte-memo-title" in joined and "fte-memo-context" in joined
          and "fte-memo-para" in joined)
    check("b1l. demo memo label caption still present",
          "Demo memo · Pre-analyzed sample · No AI generation used" in joined)

    # --- inline clickable metric tokens + floating cards machinery ---
    memo_html = memo_bodies[0]
    check("b1m. demo metric tokens remain inline-clickable (label toggles)",
          'for="ftemetric-revenue"' in memo_html)
    check("b1n. exclusive radio group embedded",
          'name="fte-memo-card"' in memo_html and 'id="ftemetric-none"' in memo_html)
    radios, cards, _ = simulate_card_visibility(memo_html, None)
    check("b1o. cards pre-rendered for every clickable metric",
          bool(cards) and "ftemetric-revenue" in cards and "ftemetric-roe" in cards,
          str(sorted(cards))[:80])

    # --- interaction: open / replace / close via × and backdrop ---
    radios_all, cards_all, vis_none = simulate_card_visibility(memo_html, None)
    check("b1p. no card visible initially", vis_none == set(), str(vis_none))
    _, _, vis_rev = simulate_card_visibility(memo_html, "ftemetric-revenue")
    check("b1q. clicking Revenue opens its floating evidence card",
          vis_rev == {"ftemetric-revenue"}, str(vis_rev))
    _, _, vis_roe = simulate_card_visibility(memo_html, "ftemetric-roe")
    check("b1r. clicking another metric replaces the same card",
          vis_roe == {"ftemetric-roe"} and "ftemetric-revenue" not in vis_roe, str(vis_roe))
    _, _, vis_close = simulate_card_visibility(memo_html, "ftemetric-none")
    check("b1s. × / backdrop close the card (none-radio hides everything)",
          vis_close == set(), str(vis_close))
    check("b1t. × close control wired to the none-radio",
          'class="fte-card-x"' in memo_html and 'for="ftemetric-none"' in memo_html)
    check("b1u. backdrop close wired to the none-radio",
          'class="fte-card-backdrop"' in memo_html and 'for="ftemetric-none"' in memo_html)

    # --- Student and Professional adaptive formats in the demo ---
    at.radio(key="fte_memo_profile_ctl").set_value("Student").run()
    if at.exception:
        check("b1v. student adaptive demo memo renders", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    stud_bodies = [str(m.value) for m in at.markdown]
    stud_joined = " ".join(stud_bodies)
    check("b1v. student adaptive demo memo renders",
          "Key Financial Metrics" in stud_joined
          and ("Sources & Evidence" in stud_joined or "Sources &amp; Evidence" in stud_joined))
    check("b1w. student demo memo keeps inline clickable metrics",
          'for="ftemetric-revenue"' in stud_joined)
    check("b1x. student demo memo keeps the floating cards",
          'id="ftemetric-none"' in stud_joined and 'class="fte-memo-card"' in stud_joined)
    stud_ev_lines = re.findall(
        r'<span class="fte-evidence-line">([^<]*)</span>', stud_joined)
    check("b1y. student evidence lines render without '—' leaks",
          bool(stud_ev_lines) and all("—" not in ln for ln in stud_ev_lines),
          str(stud_ev_lines[:4]))

    at.radio(key="fte_memo_profile_ctl").set_value("Professional").run()
    if at.exception:
        check("b1z. professional adaptive demo memo renders", False,
              str([getattr(e, "message", e) for e in at.exception]))
        return
    prof_joined = " ".join(str(m.value) for m in at.markdown)
    check("b1z. professional adaptive demo memo renders",
          "Key Financials" in prof_joined and "Evidence / Sources" in prof_joined)
    check("b1aa. professional demo memo keeps cards + clickable metrics",
          'id="ftemetric-none"' in prof_joined and 'for="ftemetric-revenue"' in prof_joined)
    prof_ev_lines = re.findall(
        r'<span class="fte-evidence-line">([^<]*)</span>', prof_joined)
    check("b1ab. professional evidence lines render without '—' leaks",
          bool(prof_ev_lines) and all("—" not in ln for ln in prof_ev_lines),
          str(prof_ev_lines[:4]))


def test_b2_demo_dataset_untouched():
    """The production demo dataset carries NO Sprint 9 reliability
    metadata and the demo rows never fabricate those states."""
    app = _load_app()
    demo = app._demo_module3_result()
    check("b2a. demo module3 result has no extraction_reliability report",
          "extraction_reliability" not in demo)
    bad = []
    for metric, f in (demo.get("financial_data") or {}).items():
        for key in ("extraction_state", "extraction_state_reason",
                    "extraction_conflict", "extraction_method"):
            if isinstance(f, dict) and key in f:
                bad.append(f"{metric}.{key}")
    check("b2b. no Sprint 9 reliability metadata on any demo fact",
          not bad, str(bad))
    rows = app._build_terminal_rows(demo)
    kinds = {r["_kind"] for r in rows}
    check("b2c. demo grid kinds exclude review_required/conflict",
          "review_required" not in kinds and "conflict" not in kinds, str(kinds))
    check("b2d. demo values unchanged through the rows builder",
          {r["Value"] for r in rows} <= _demo_expected_values())


def test_b3_synthetic_demo_fixture_isolated():
    """A review_required demo fixture works through the REAL demo memo
    machinery (labels, cards, exclusive radio), and the production demo
    dataset is provably not modified by the exercise."""
    app = _load_app()
    before = json.dumps(app._demo_module3_result(), default=str, sort_keys=True)

    synth_rows = [
        {"metric": "Revenue", "Value": "281.70B", "Period": "FY2025",
         "Source": "Microsoft 10-K FY2025", "Status": "🟠 Review Required",
         "_kind": "review_required",
         "_reason": "column identity could not be established",
         "_fact": {"reporting_period": "FY2025",
                   "document_name": "Microsoft 10-K FY2025", "page": 26,
                   "evidence": "Revenue from operations 281.70 (FY2025)"}},
        {"metric": "ROE", "Value": "0.37", "Period": "FY2025",
         "Source": "Calculated", "Status": "🟡 Derived", "_kind": "derived",
         "_fact": {"formula": "Net Profit / Equity",
                   "inputs": ["Net Profit", "Equity"], "reporting_period": "FY2025"}},
    ]
    synth_memo = "EXECUTIVE SUMMARY\nRevenue reached 281.70B in FY2025 while ROE improved.\n"
    _APP_STUB_SS["fte_demo_mode"] = True
    try:
        html = app._memo_adaptive_html(synth_rows, synth_memo, {}, "student")
    finally:
        _APP_STUB_SS["fte_demo_mode"] = False
    check("b3a. synthetic review-required metric stays inline-clickable",
          'for="ftemetric-revenue"' in html)
    check("b3b. review-required evidence renders in the demo memo",
          "Review required" in html and "column identity could not be established" in html)
    check("b3c. floating cards still embedded for the fixture",
          'class="fte-memo-card"' in html and 'id="ftemetric-none"' in html)
    _, _, vis = simulate_card_visibility(html, "ftemetric-revenue")
    check("b3d. synthetic fixture card opens on click",
          vis == {"ftemetric-revenue"}, str(vis))
    _, _, vis_none = simulate_card_visibility(html, "ftemetric-none")
    check("b3e. synthetic fixture card closes via none-radio",
          vis_none == set(), str(vis_none))

    after = json.dumps(app._demo_module3_result(), default=str, sort_keys=True)
    check("b3f. production demo dataset provably unchanged (fixture isolated)",
          before == after, "mutated!" if before != after else "deep-equal")


def main():
    print("=" * 62)
    print("SPRINT 9.1 - MANDATORY MEMO PATH COVERAGE")
    print("=" * 62)
    print("PART A - API / REAL MEMO (reliability end-to-end)")
    test_a1_review_required_e2e()
    test_a2_conflict_e2e()
    print("PART B - DEMO MEMO (interaction + regression)")
    test_b1_demo_apptest()
    test_b2_demo_dataset_untouched()
    test_b3_synthetic_demo_fixture_isolated()

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
