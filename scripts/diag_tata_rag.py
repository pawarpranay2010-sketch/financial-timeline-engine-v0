"""Capture TEST 10-12 detail: RAG terminal state, conflicts, calculation."""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Silence ALL application logging so only our prints remain
logging.disable(logging.CRITICAL)
import warnings
warnings.filterwarnings("ignore")

from ingestion.extraction import extract_document
from backend.extraction2.financial_extractor_v2 import FinancialExtractorV2
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState, EvidenceItem, InformationRequirement,
)
from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator
from backend.financial_calculator import FinancialCalculator
from backend.intelligence.currency_validator import CurrencyValidator


class _FileLike:
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


DOC = os.path.join(os.path.dirname(__file__), "..", "tests", "test_data", "tata_motors_20f.html")

r = extract_document(_FileLike(DOC))
facts = r["financial_facts"]


class DocBackedOrchestrator(AgenticRAGOrchestrator):
    def __init__(self, items, **kw):
        super().__init__(**kw)
        self._pool = items
        self.retrieval_calls = 0

    def _retrieve_evidence(self, query):
        self.retrieval_calls += 1
        return [EvidenceItem(**d) for d in self._pool]


pool = [FinancialExtractorV2.to_evidence_item_dict(f) for f in facts]
orch = DocBackedOrchestrator(pool, ticker="TTM", max_iterations=3,
                             timeout_seconds=60, max_evidence_items=5000)
canon = orch.execute("Analyze Tata Motors FY2023 revenue, net income, EBITDA and total assets")
st = canon.state
print("=== TEST 10 — AGENTIC RAG (document-backed) ===")
print(f"  terminal        : {st.terminal_state}")
print(f"  reason          : {st.terminal_reason}")
print(f"  iterations      : {st.iterations_used}/{st.max_iterations}  (retrieval_calls={orch.retrieval_calls})")
print(f"  evidence        : {st.evidence_count} unique")
print(f"  requirements    : {len(st.requirements)}  satisfied={st.satisfied_count} missing={st.missing_count} conflicts={st.conflict_count}")
print(f"  resolved_facts  : {canon.resolved_count}")
print(f"  compact_context : {len(st.to_dict() and orch.state_to_str()) if False else ''}")

req_status = [(r.metric, r.period, r.status) for r in st.requirements]
print(f"  requirements    : {req_status}")

print("\n=== TEST 12 — CALCULATION SAFETY ===")
# canonical gate: what does the orchestrator allow?
print(f"  calculation allowed? terminal={st.terminal_state} "
      f"-> {'YES (COMPLETE)' if st.terminal_state == 'COMPLETE' else 'NO (blocked)'}")
if st.terminal_state == "COMPLETE":
    data = {}
    for item in canon.to_dict()["resolved_facts"]:
        k = {"Revenue": "Revenue", "NetIncome": "Net Profit", "TotalAssets": "Assets",
             "TotalLiabilities": "Liabilities", "ShareholdersEquity": "Equity",
             "TotalDebt": "Debt"}.get(item.get("metric"))
        if k and item.get("value") is not None:
            data.setdefault(k, {"value": item["value"]})
    ratios = FinancialCalculator().calculate(data)
    print(f"  ratios computed: {list(ratios.keys())}")
    for k, v in ratios.items():
        print(f"    {k} = {v['value']}")
else:
    print("  ratios NOT computed (evidence gate blocked)")

# currency mismatch block demonstration with real extracted facts
inr_facts = [f for f in facts if f.get("currency_code") == "INR"]
usd_facts = [f for f in facts if f.get("currency_code") == "USD"]
print(f"\n  real facts: INR={len(inr_facts)} USD={len(usd_facts)}")
if inr_facts and usd_facts:
    ok, err = CurrencyValidator.check_operation_currency(
        {"currency_code": "INR", "currency_role": "REPORTING"},
        {"currency_code": "USD", "currency_role": "REPORTING"}, "divide")
    print(f"  INR/USD operation blocked: {not ok} | {err}")

print("\n=== TEST 11 — CONFLICT RESOLUTION ===")
from backend.intelligence.source_resolver import SourceResolver
sr = SourceResolver()
t3 = {"metric": "Revenue", "value": 3457999.0, "source": "SEC 20-F", "source_tier": 3,
      "filing_type": "20-F", "confidence": 0.99}
t1 = {"metric": "Revenue", "value": 3400000.0, "source": "news", "source_tier": 1, "confidence": 0.6}
s, w = sr.resolve_conflict([t1, t3])
print(f"  tier3 vs tier1 -> {s}, winner_tier={w.get('source_tier') if w else None}")
am = {"metric": "Revenue", "value": 3458000.0, "source": "SEC 20-F/A", "source_tier": 3,
      "filing_type": "20-F/A", "confidence": 0.99}
s3, w3 = sr.resolve_conflict([t3, am])
print(f"  20-F vs 20-F/A -> {s3}, winner={w3.get('filing_type') if w3 else None}")
