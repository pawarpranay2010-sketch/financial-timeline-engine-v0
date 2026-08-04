#!/usr/bin/env python3
"""Sprint 5 — Evidence-Proof Layer targeted tests.

Proves the evidence chain end to end:
  Document -> extraction -> structured metric -> provenance metadata ->
  verification/calculation -> terminal/grid row -> memo evidence card

Checks:
  1. Verified metric retains its source metadata (real extractor).
  2. Page / source / evidence propagate into the grid row (demo dataset).
  3. The same metadata reaches the memo evidence card (demo overlay).
  4. Derived metrics identify their source inputs + basis.
  5. Blocked metrics retain their reason.
  6. Missing provenance becomes '—' (and absent facts are not fabricated).
  7. No provenance is fabricated (evidence is a real substring of input).
  8. Existing demo still works (demo workspace + memo open).
  9. Grid / Intelligence / System navigation still intact.

No network, no AI, no storage — stdlib + existing app/backend code only.
"""
import html as _html
import re as _re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from backend.financial_extractor import extract_financial_data
from backend.financial_calculator import calculate_financial_ratios

from streamlit.testing.v1 import AppTest

APP = "app (1) (9).py"

SAMPLE_DOC = (
    "Microsoft Corporation Annual Report FY2025.\n"
    "Total Revenue for the year was 281,700,000,000, up strongly from a year ago.\n"
    "Net Profit reached 98,300,000,000 during the same period.\n"
    "Total Assets stood at 512,200,000,000 and Total Liabilities at 243,700,000,000.\n"
    "Shareholders' Equity was 268,500,000,000 at year end.\n"
    "Total Debt was 96,600,000,000 during the year.\n"
)


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


def main():
    failures = 0

    # --- 1) Verified metric retains source metadata (real extractor) ---
    fd = extract_financial_data(SAMPLE_DOC)
    assert "Revenue" in fd, "extractor missed Revenue"
    rev = fd["Revenue"]
    assert rev.get("source") == "Document", rev
    assert rev.get("value") == 281700000000, rev.get("value")
    assert isinstance(rev.get("evidence"), str) and rev["evidence"].strip(), "no evidence fragment"
    assert "281,700,000,000" in rev["evidence"], f"evidence not from the document: {rev['evidence']!r}"
    assert "EPS" not in fd, "extractor fabricated an absent metric"
    print("1. VERIFIED METRIC RETAINS SOURCE METADATA OK (value + source + evidence fragment)")

    # --- 7) No provenance fabricated (backend) ---
    for key, fact in fd.items():
        if isinstance(fact.get("evidence"), str) and fact["evidence"]:
            assert fact["evidence"] in SAMPLE_DOC, f"evidence not a real substring: {fact['evidence']!r}"
    print("7a. NO FABRICATED EVIDENCE OK (every evidence fragment is a real substring of the document)")

    # --- 4) Derived metrics identify source inputs + basis (calculator) ---
    ratios = calculate_financial_ratios(fd)
    assert ratios["Profit Margin"]["inputs"] == ["Net Profit", "Revenue"], ratios["Profit Margin"]
    assert ratios["ROE"]["inputs"] == ["Net Profit", "Equity"], ratios["ROE"]
    assert ratios["Debt to Equity"]["inputs"] == ["Debt", "Equity"], ratios["Debt to Equity"]
    for name, fact in ratios.items():
        assert fact["source"] == "Calculated"
        assert fact.get("formula"), f"derived {name} has no formula"
        for inp in fact.get("inputs") or []:
            assert inp in fd, f"derived {name} lists untracked input {inp}"
    assert "Current Ratio" not in ratios, "Current Ratio must stay absent when inputs are missing"
    print("4. DERIVED METRICS IDENTIFY SOURCE INPUTS + BASIS OK (inputs ⊆ extracted facts, no fabrication)")

    # --- 2/5/6/8/9: demo app-level checks ---
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    if not check_exceptions(at, "entrance"):
        return 1
    at.button(key="fte_btn_demo").click().run()
    if not check_exceptions(at, "demo workspace"):
        return 1
    assert ss(at, "fte_route") == "demo"

    # --- 2) page/source/evidence propagate into the grid row ---
    rows = ss(at, "fte_grid_rows") or []
    rev_row = next(r for r in rows if r["metric"] == "Revenue")
    assert rev_row["_kind"] == "verified"
    fact = rev_row["_fact"]
    assert fact.get("page") == "26", fact
    assert fact.get("evidence") == "Consolidated Statements of Income, p. 26", fact
    assert fact.get("source") == "10-K FY2025 · Income Statement", fact
    roe_row = next(r for r in rows if r["metric"] == "ROE")
    assert roe_row["_kind"] == "derived"
    assert roe_row["_fact"].get("inputs") == ["Net Profit", "Equity"], roe_row["_fact"]
    assert roe_row["_fact"].get("formula") == "Net income ÷ shareholders' equity"
    print("2. PAGE/SOURCE/EVIDENCE PROPAGATED INTO GRID ROW OK (Revenue fact + ROE inputs/formula)")

    # --- 5) Blocked metrics retain their reason ---
    blocked_row = next(r for r in rows if r["metric"] == "Segment Gross Margin")
    assert blocked_row["_kind"] == "blocked"
    assert blocked_row.get("_reason"), blocked_row
    assert "missing" in blocked_row["_reason"].lower() or "blocked" in blocked_row["_reason"].lower()
    print("5. BLOCKED METRIC RETAINS ITS REASON OK:", blocked_row["_reason"])

    # --- 6) Missing provenance becomes '—' (blocked card has no source/page) ---
    unanalyzed_rows = [r for r in rows if r["_kind"] == "unanalyzed"]
    assert unanalyzed_rows, "expected an unanalyzed demo row"
    assert unanalyzed_rows[0].get("_fact") in (None, {}), "unanalyzed rows must have no fact"
    print("6. MISSING PROVENANCE → '—' OK (unanalyzed row has no fact; blocked shows no source)")

    # --- 3) Metadata reaches the memo evidence card ---
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    at.button(key="fte_btn_demo_memo").click().run()
    if not check_exceptions(at, "demo memo view"):
        return 1
    assert ss(at, "fte_memo_view_open") is True
    memo_html = _html.unescape(
        next(str(m.value) for m in at.markdown if "fte-memo-para" in str(m.value))
    )
    # verified card: concise source reference "doc · p. 26" + evidence
    assert "10-K FY2025 · Income Statement · p. 26" in memo_html, "memo card missing source reference"
    assert "Consolidated Statements of Income, p. 26" in memo_html, "memo card missing evidence fragment"
    # derived card: calculation basis + inputs
    assert "Net income ÷ shareholders' equity" in memo_html, "memo card missing calc basis"
    assert "Inputs: Net Profit, Equity" in memo_html, "memo card missing source inputs"
    # blocked card: analysis-limited note retained; missing reference → '—'
    assert 'data-card="ftemetric-segment-gross-margin"' in memo_html, "blocked metric card not embedded"
    assert "Analysis limited" in memo_html, "blocked card lost its limitation note"
    assert '<div class="k">Reference</div><div class="v">—</div>' in memo_html, \
        "blocked card must show '—' for the missing source reference"
    print("3. METADATA REACHES THE MEMO EVIDENCE CARD OK (reference · p.26, evidence, calc basis, inputs, blocked note)")

    # --- 9) Grid / Intelligence / System navigation intact ---
    at.button(key="fte_btn_memo_back").click().run()
    assert ss(at, "fte_page") == "Intelligence"
    at.segmented_control(key="fte_page").set_value("Financial Grid").run()
    if not check_exceptions(at, "demo grid"):
        return 1
    at.segmented_control(key="fte_page").set_value("System").run()
    if not check_exceptions(at, "demo system"):
        return 1
    print("9. GRID / INTELLIGENCE / SYSTEM NAV INTACT OK")

    if failures:
        print(f"=== EVIDENCE TESTS: {failures} FAILURE(S) ===")
        return 1
    print("=== EVIDENCE TESTS: ALL CHECKS COMPLETE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
