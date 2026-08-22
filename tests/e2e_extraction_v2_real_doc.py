"""
Platrixa
Extraction 2.0 - REAL-DOCUMENT E2E COMPARISON

Runs the REAL Apple 10-K SEC filing (Inline XBRL HTML, ~1.5 MB) through the
ACTUAL application ingestion pipeline (ingestion.extract_document), which
now wires in FinancialExtractorV2.

Compares:

  BEFORE (regex-first FinancialExtractor):
      Revenue -> 2025.0     (picked up the FISCAL YEAR)
      EPS     -> 8217.0     (picked up a cross-reference)
      Tables  -> flattened
      Scale   -> lost
      Period  -> unreliable

  AFTER (FinancialExtractorV2):
      Revenue -> ~391B (SEC XBRL-tagged fact) + period + unit + source
      EPS     -> ~6.08 with source
      Tables  -> preserved relationships
      Scale   -> preserved
      Evidence-> traceable (evidence_hash + anchor)

Run: python3 tests/e2e_extraction_v2_real_doc.py
"""

import sys
import os
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.ERROR)

from backend.financial_extractor import FinancialExtractor  # BEFORE (regex)
from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2  # AFTER
from ingestion.extraction import extract_document

DOC_PATH = os.path.join(os.path.dirname(__file__), "test_data", "apple_10k_2024.html")

# Known BEFORE values from the previous stress test run
BEFORE_BASELINE = {
    "Revenue": 2025.0,       # fiscal year mistaken for revenue
    "EPS": 8217.0,           # cross-reference mistaken for EPS
}


class _FileLike:
    """Minimal file-like wrapper so the real HTML can flow through the
    actual application ingestion pipeline (parse_document requires
    `.name` + `.read()`/`.seek()`)."""

    def __init__(self, path):
        self.name = os.path.basename(path)
        with open(path, "rb") as f:
            self._data = f.read()
        self._pos = 0

    def read(self, *args):
        if args:
            n = args[0]
            out = self._data[self._pos:self._pos + n]
            self._pos += len(out)
            return out
        out = self._data[self._pos:]
        self._pos = len(self._data)
        return out

    def seek(self, offset, whence=0):
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._data) + offset
        return self._pos


def main():
    print("=" * 70)
    print("EXTRACTION 2.0 — REAL APPLE 10-K E2E COMPARISON")
    print("=" * 70)

    if not os.path.exists(DOC_PATH):
        print(f"ERROR: {DOC_PATH} missing. Run scripts/download_sec_filing.py first.")
        return 1

    file_size = os.path.getsize(DOC_PATH)
    print(f"\nDocument: {os.path.basename(DOC_PATH)} ({file_size:,} bytes)")

    # =================================================================
    # 1. BEFORE — regex-first extractor on the raw stripped text
    # =================================================================
    print("\n--- 1. BEFORE (regex-first FinancialExtractor) ---")
    with open(DOC_PATH, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()
    import re
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = re.sub(r"\s+", " ", text)

    before = FinancialExtractor().extract(text)
    for field, data in sorted(before.items()):
        marker = "❌ WRONG" if field in BEFORE_BASELINE and data["value"] == BEFORE_BASELINE[field] else ""
        print(f"  {field:20s} = {data['value']:<22} {marker}")

    # =================================================================
    # 2. AFTER — actual application pipeline (V2 wired in)
    # =================================================================
    print("\n--- 2. AFTER (actual pipeline + FinancialExtractorV2) ---")

    start = time.time()
    result = extract_document(_FileLike(DOC_PATH))
    elapsed = time.time() - start

    parsed = result["parsed_document"]
    stats = result["extraction_stats"]
    facts = result["financial_facts"]

    print(f"Pipeline time (parse + extract): {elapsed:.2f}s")
    print(f"Document type: {parsed['type']} | pages={parsed['pages']} | "
          f"tables={parsed['tables']} | text chars={len(parsed.get('text', '')):,}")
    print(f"XBRL facts parsed: {len(parsed.get('xbrl_facts') or [])}")
    print(f"Structured tables: {len(parsed.get('table_data') or [])}")
    print(f"Extraction stats: {json.dumps(stats)}")

    print(f"\nExtracted facts ({len(facts)}):")
    by_metric = {}
    for f in facts:
        by_metric.setdefault(f["metric_id"], []).append(f)
        period = f.get("fiscal_period") or ""
        cur = f.get("currency_code") or ""
        src = f.get("source_type") or ""
        conf = f.get("confidence_score")
        print(f"  {f['metric_id']:22s} = {f['metric_value']:<22} "
              f"period={period:<10} cur={cur:<4} src={src:<6} conf={conf}")

    # =================================================================
    # 3. Assertions
    # =================================================================
    print("\n--- 3. CHECKS ---")

    checks = {}

    # Revenue: should be ~391B from XBRL, NOT 2025
    rev_facts = by_metric.get("Revenue", [])
    rev_values = {abs(f["metric_value"]) for f in rev_facts}
    checks["Revenue correct (~391B)"] = any(
        300e9 < v < 450e9 for v in rev_values
    )
    checks["Revenue NOT the fiscal year 2025"] = 2025.0 not in rev_values

    # EPS: should be ~6, NOT 8217
    eps_facts = by_metric.get("EPS", [])
    eps_values = {f["metric_value"] for f in eps_facts}
    checks["EPS correct (~6.08)"] = any(3 < v < 12 for v in eps_values)
    checks["EPS NOT a cross-reference (8217)"] = 8217.0 not in eps_values

    # XBRL structured facts took precedence
    checks["XBRL source present"] = any(f["source_type"] == "XBRL" for f in facts)

    # Evidence integrity
    checks["Evidence anchors present"] = all(f["evidence_text_anchor"] for f in facts)
    checks["SHA-256 evidence hashes"] = all(
        isinstance(f["evidence_hash"], str) and len(f["evidence_hash"]) == 64
        for f in facts
    )
    checks["Confidence scored (never fabricated)"] = all(
        0.0 < f["confidence_score"] < 1.0 for f in facts
    )
    checks["No fiscal-year / page-number poisoning"] = all(
        f["metric_value"] not in (2025.0, 2024.0)
        and abs(f["metric_value"]) not in (12.0, 101.0, 57.0, 58.0)
        for f in facts
    )

    # ExtractedFact compatibility
    from backend.database.models import ExtractedFact
    model_columns = {c.name for c in ExtractedFact.__table__.columns}
    checks["ExtractedFact schema compatible"] = all(
        set(f.keys()) - model_columns <= {
            "raw_value", "normalized_value", "page", "table_id",
            "extraction_method", "evidence_hash",
        }
        for f in facts
    )

    # Agentic RAG EvidenceItem compatibility
    from backend.intelligence.evidence_summary_state import EvidenceItem, EvidenceSummaryState
    items = [
        EvidenceItem(**FinancialExtractorV2.to_evidence_item_dict(f))
        for f in facts
    ]
    state = EvidenceSummaryState(max_iterations=3)
    added = state.add_evidence_batch(items)
    checks["Agentic RAG dedup compatible"] = state.state.evidence_count <= len(facts)

    pass_count = sum(1 for v in checks.values() if v)
    fail_count = sum(1 for v in checks.values() if not v)

    for name, passed in checks.items():
        print(f"  {'✅' if passed else '❌'} {name}")
    print(f"\nPASS: {pass_count}/{len(checks)} | FAIL: {fail_count}/{len(checks)}")

    # =================================================================
    # 4. Summary table BEFORE vs AFTER
    # =================================================================
    print("\n" + "=" * 70)
    print("BEFORE vs AFTER — EXTRACTION RESULTS")
    print("=" * 70)

    def fmt_metric(facts_list):
        if not facts_list:
            return "MISSING"
        best = sorted(facts_list, key=lambda f: -f["confidence_score"])[0]
        return (f"{best['metric_value']} "
                f"(period={best.get('fiscal_period') or '?'}, "
                f"cur={best.get('currency_code') or '?'}, "
                f"src={best.get('source_type')}, conf={best.get('confidence_score')})")

    rows = [
        ("Revenue", BEFORE_BASELINE.get("Revenue", "?"), fmt_metric(rev_facts)),
        ("EPS", BEFORE_BASELINE.get("EPS", "?"), fmt_metric(eps_facts)),
        ("NetIncome", "?", fmt_metric(by_metric.get("NetIncome", []))),
        ("TotalAssets", "?", fmt_metric(by_metric.get("TotalAssets", []))),
        ("TotalDebt", "?", fmt_metric(by_metric.get("TotalDebt", []))),
        ("OperatingCashFlow", "?", fmt_metric(by_metric.get("OperatingCashFlow", []))),
    ]
    print(f"{'Metric':20s} {'BEFORE (regex)':<28} AFTER (V2)")
    print("-" * 70)
    for metric, before_v, after_v in rows:
        print(f"{metric:20s} {str(before_v):<28} {after_v}")

    print(f"\n{'=' * 70}")
    if fail_count == 0:
        print("VERDICT: EXTRACTION 2.0 BEATS THE REGEX BASELINE ON THE REAL DOCUMENT")
    else:
        print(f"VERDICT: {fail_count} CHECKS FAILED")
    print("=" * 70)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
