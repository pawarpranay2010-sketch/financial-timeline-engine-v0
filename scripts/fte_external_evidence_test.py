#!/usr/bin/env python3
"""Sprint 6.5 - Deterministic External Evidence Recovery targeted tests.

Proves the strict resolver hierarchy:

  Tier 1 (primary uploaded document)
    -> Tier 2 (user-uploaded workspace appendices)
    -> Tier 3 (approved structured/regulatory providers + EXTERNAL_DERIVED)
    -> BLOCKED

and the hard rule that random web/search/blog/scraped sources can NEVER
enter the financial fact pipeline.

Checks (numbered as in the sprint spec):
  1-5   Tier hierarchy + stop-at-first-valid-tier
  6-10  Strict identity / period / metric / currency / scale matching
  11-14 Provider behavior (unconfigured / unavailable / invalid / valid)
  15-18 External-derived metrics
  19-21 Forbidden web/blog/scraper sources
  22-25 Sprint 5 + Sprint 6 evidence chain intact
  26-29 Grid / evidence-card / memo-card / blocked integration
  30    Demo mode unchanged

No network, no AI, no storage - stdlib + existing app/backend code only.
"""
import importlib.util as _ilu
import io
import re as _re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from backend.financial_extractor import extract_financial_data
from backend.financial_calculator import calculate_financial_ratios
from backend.evidence_resolver import (
    PROVENANCE_TIER,
    BLOCKED_REASON,
    DEFAULT_PROVIDERS,
    RATIO_INPUT_SETS,
    _normalize_period,
    _validate_external_result,
    resolve_metric,
    recover_missing_metrics,
)

from streamlit.testing.v1 import AppTest

APP = "app (1) (9).py"

CIK = "0000789019"
PERIOD = "FY2025"
CURRENCY = "USD"

SAMPLE_DOC = (
    "Microsoft Corporation Annual Report FY2025.\n"
    "Total Revenue for the year was 281,700,000,000, up strongly from a year ago.\n"
    "Net Profit reached 98,300,000,000 during the same period.\n"
    "Total Assets stood at 512,200,000,000 and Total Liabilities at 243,700,000,000.\n"
    "Shareholders' Equity was 268,500,000,000 at year end.\n"
    "Total Debt was 96,600,000,000 during the year.\n"
)

COMPANY = {
    "cik": CIK,
    "company_name": "Microsoft Corporation",
    "currency": CURRENCY,
}


# ---------------------------------------------------------------
# Fake approved providers (deterministic, in-memory)
# ---------------------------------------------------------------

class FakeValidProvider:
    name = "Fake SEC"
    configured = True

    def __init__(self, metric, value, cik=CIK, period=PERIOD, currency=CURRENCY,
                 scale="millions", field=None):
        self.metric = metric
        self.value = value
        self.cik = cik
        self.period = period
        self.currency = currency
        self.scale = scale
        self.field = field or metric

    def is_configured(self):
        return True

    def resolve_metric(self, company_identifier, metric, reporting_period):
        if metric != self.metric:
            return None  # UNAVAILABLE for this metric
        return {
            "value": self.value,
            "metric": self.field,
            "cik": self.cik,
            "reporting_period": self.period,
            "currency": self.currency,
            "scale": self.scale,
            "provider_identifier": f"CIK-{self.cik}",
            "source_ref": f"Fake SEC / CIK {self.cik} / {self.period}",
            "evidence": f"Fake SEC / CIK {self.cik} / {self.period}",
        }


class SpyWebProvider:
    """A random-web source that MUST never be consulted by the resolver."""

    name = "Random Web Search"

    def is_configured(self):
        return True

    def resolve_metric(self, company_identifier, metric, reporting_period):
        raise AssertionError("FORBIDDEN: random web source was queried!")


def _load_app():
    spec = _ilu.spec_from_file_location("fte_app_mod", APP)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_app()
    _build_terminal_rows = mod._build_terminal_rows
    _metric_overlay_fields = mod._metric_overlay_fields
    _metric_detail_html = mod._metric_detail_html

    # ---- 1) Tier 1 document fact wins (provider never consulted) ----
    web = SpyWebProvider()
    tier1_fact = {
        "value": 281700000000, "source": "Document", "evidence": "Total Revenue ...",
        "page": 2, "document_name": "MSFT_10K_FY2025.pdf",
    }
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={"Revenue": tier1_fact}, providers=[web],
    )
    assert res.get("value") == 281700000000
    assert res.get("provenance_tier") == PROVENANCE_TIER.DOCUMENT
    assert res.get("page") == 2 and res.get("document_name") == "MSFT_10K_FY2025.pdf"
    print("1. TIER 1 DOCUMENT FACT WINS OK (external provider never consulted)")

    # ---- 2) Tier 2 used only when Tier 1 unavailable ----
    appendix_doc = {
        "document_name": "MSFT_Notes_FY2025.pdf",
        "text": "========== PAGE 1 ==========\nRevenue grew to 281,700,000,000 during FY2025.",
    }
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        workspace_documents=[appendix_doc], primary_facts={},
        providers=[FakeValidProvider("Revenue", 999.0, cik="0000000000")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.APPENDIX
    assert res.get("value") == 281700000000
    print("2. TIER 2 USED ONLY WHEN TIER 1 UNAVAILABLE OK")

    # ---- 3) Tier 2 identifies the correct appendix document ----
    assert res.get("document_name") == "MSFT_Notes_FY2025.pdf"
    assert res.get("page") == 1
    assert (res.get("evidence") or "") in appendix_doc["text"], "appendix evidence must be a real substring"
    print("3. TIER 2 IDENTIFIES CORRECT APPENDIX DOCUMENT OK (name + page + real evidence)")

    # ---- 4) Tier 3 used only after Tiers 1 and 2 fail ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        workspace_documents=[], primary_facts={},
        providers=[FakeValidProvider("Revenue", 281.7, scale="billions")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.REGULATORY_API
    print("4. TIER 3 USED ONLY AFTER TIERS 1+2 FAIL OK")

    # ---- 5) Resolver stops after the first valid tier ----
    # Tier 1 has Revenue AND the appendix also has Revenue -> Tier 1 wins.
    both_appendix = {
        "document_name": "MSFT_Notes_FY2025.pdf",
        "text": "Revenue grew to 281,700,000,000 during FY2025.",
    }
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        workspace_documents=[both_appendix],
        primary_facts={"Revenue": dict(tier1_fact)},
        providers=[FakeValidProvider("Revenue", 999.0, scale="billions")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.DOCUMENT
    assert res.get("value") == 281700000000
    print("5. RESOLVER STOPS AFTER FIRST VALID TIER OK (Tier 1 beats Tier 2/3)")

    # ---- 6) Wrong company identifier -> reject ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, cik="0000000000")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert "identifier" in (res.get("reason") or "").lower()
    print("6. WRONG COMPANY IDENTIFIER REJECTED OK")

    # ---- 7) Wrong reporting period -> reject ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, period="FY2024")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert "period" in (res.get("reason") or "").lower()
    print("7. WRONG REPORTING PERIOD REJECTED OK (FY2024 != FY2025)")

    # ---- 8) Wrong metric definition -> reject (EBITDA payload != Revenue) ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, field="EBITDA")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert "definition" in (res.get("reason") or "").lower()
    print("8. WRONG METRIC DEFINITION REJECTED OK (EBITDA payload != Revenue request)")

    # ---- 9) Currency mismatch -> reject ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, currency="INR")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert "currency" in (res.get("reason") or "").lower()
    print("9. CURRENCY MISMATCH REJECTED OK (INR != USD, no silent conversion)")

    # ---- 10) Invalid scale -> reject ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, scale="galactic")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert "scale" in (res.get("reason") or "").lower()
    print("10. INVALID SCALE REJECTED OK")

    # ---- 11) Unconfigured provider -> no crash -> continue ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=DEFAULT_PROVIDERS,
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED  # all unconfigured
    assert all(not p.is_configured() for p in DEFAULT_PROVIDERS)
    print("11. UNCONFIGURED PROVIDERS OK (no crash; resolver continues, then BLOCKED)")

    # ---- 12) All providers unavailable -> BLOCKED ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert res.get("reason") == BLOCKED_REASON
    print("12. ALL PROVIDERS UNAVAILABLE -> BLOCKED OK")

    # ---- 13) Provider returning invalid data -> BLOCKED ----
    class GarbageProvider(FakeValidProvider):
        def resolve_metric(self, company_identifier, metric, reporting_period):
            return {"value": "not-a-number", "metric": "Revenue"}

    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[GarbageProvider("Revenue", 0)],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    print("13. PROVIDER INVALID DATA -> BLOCKED OK (garbage never enters pipeline)")

    # ---- 14) Provider returning valid data -> REGULATORY_API provenance ----
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[FakeValidProvider("Revenue", 281.7, scale="billions")],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.REGULATORY_API
    assert res.get("provider") == "Fake SEC"
    assert res.get("provider_identifier") == f"CIK-{CIK}"
    assert res.get("scale") == "billions" and res.get("currency") == CURRENCY
    print("14. VALID PROVIDER DATA -> REGULATORY_API OK (provider + identifier + scale preserved)")

    # ---- 15) External inputs produce EXTERNAL_DERIVED (Current Ratio) ----
    ca_provider = FakeValidProvider("Current Assets", 420.0, scale="billions")
    cl_provider = FakeValidProvider("Current Liabilities", 300.0, scale="billions")
    res = resolve_metric(
        "Current Ratio", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, workspace_documents=[],
        providers=[ca_provider, cl_provider],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.EXTERNAL_DERIVED
    assert res.get("value") == round(420.0 / 300.0, 2)
    print("15. EXTERNAL INPUTS -> EXTERNAL_DERIVED OK (Current Ratio = 1.4)")

    # ---- 16) Formula retained ----
    assert res.get("formula") == "Current Assets ÷ Current Liabilities"
    print("16. FORMULA RETAINED OK")

    # ---- 17) Actual source input metrics retained ----
    assert res.get("inputs") == ["Current Assets", "Current Liabilities"]
    assert res.get("input_provenance") == {
        "Current Assets": PROVENANCE_TIER.REGULATORY_API,
        "Current Liabilities": PROVENANCE_TIER.REGULATORY_API,
    }
    print("17. SOURCE INPUT METRICS RETAINED OK (+ per-input provenance)")

    # ---- 18) Derived value cannot use unverified inputs ----
    res = resolve_metric(
        "Current Ratio", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, workspace_documents=[],
        providers=[ca_provider],  # Current Liabilities never verified
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    assert res.get("value") is None
    print("18. UNVERIFIED INPUT BLOCKS DERIVATION OK (no calculation from missing input)")

    # ---- 19) Random web source is never queried ----
    web = SpyWebProvider()
    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={"Revenue": dict(tier1_fact)}, providers=[web],
    )
    assert res.get("value") == 281700000000  # web spy would have raised
    assert "web" not in " ".join(p.name.lower() for p in DEFAULT_PROVIDERS)
    print("19. RANDOM WEB SOURCE NEVER QUERIED OK")

    # ---- 20) Blog/search result cannot become a fact ----
    class BlogProvider(FakeValidProvider):
        def resolve_metric(self, company_identifier, metric, reporting_period):
            # A plausible-looking blog snippet: NO canonical identity, NO
            # reporting period, no scale -> cannot be defended.
            return {"value": 281.7, "metric": "Revenue", "currency": "USD",
                    "source": "https://blog.financeguru.example/revenue-2025"}

    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[BlogProvider("Revenue", 0)],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    print("20. BLOG/SEARCH RESULT CANNOT BECOME A FACT OK")

    # ---- 21) Generic scraper cannot become a fact ----
    class ScraperProvider(FakeValidProvider):
        def resolve_metric(self, company_identifier, metric, reporting_period):
            return {"value": "281.7", "metric": "Revenue", "scale": "billions",
                    "source": "scraped.example.net"}

    res = resolve_metric(
        "Revenue", company_context=COMPANY, reporting_period=PERIOD,
        primary_facts={}, providers=[ScraperProvider("Revenue", 0)],
    )
    assert res.get("provenance_tier") == PROVENANCE_TIER.BLOCKED
    print("21. GENERIC SCRAPER CANNOT BECOME A FACT OK (no identity/period -> rejected)")

    # ---- 22) Sprint 5 evidence intact ----
    fd = extract_financial_data(SAMPLE_DOC)
    rev = fd.get("Revenue") or {}
    assert isinstance(rev.get("evidence"), str) and "281,700,000,000" in rev["evidence"]
    print("22. SPRINT 5 EVIDENCE INTACT OK")

    # ---- 23) Sprint 6 page metadata intact ----
    multi = (
        "--- Start of File: MSFT_10K_FY2025.pdf ---\n"
        "========== PAGE 2 ==========\n"
        "Total Revenue for the year was 281,700,000,000, up strongly from a year ago.\n"
    )
    mfd = extract_financial_data(multi)
    assert mfd["Revenue"]["page"] == 2
    assert mfd["Revenue"]["document_name"] == "MSFT_10K_FY2025.pdf"
    print("23. SPRINT 6 PAGE METADATA INTACT OK")

    # ---- 24) Evidence fragments remain real source substrings ----
    for key, fact in fd.items():
        if isinstance(fact.get("evidence"), str) and fact["evidence"]:
            assert fact["evidence"] in SAMPLE_DOC, f"fabricated evidence: {fact['evidence']!r}"
    print("24. EVIDENCE FRAGMENTS REMAIN REAL SOURCE SUBSTRINGS OK")

    # ---- 25) Document attribution remains correct ----
    multi2 = (
        "--- Start of File: MSFT_10K_FY2025.pdf ---\n"
        "========== PAGE 1 ==========\n"
        "Total Revenue for the year was 281,700,000,000.\n"
        "--- Start of File: AAPL_10K_FY2025.pdf ---\n"
        "========== PAGE 1 ==========\n"
        "Net Profit reached 98,300,000,000.\n"
    )
    mfd2 = extract_financial_data(multi2)
    assert mfd2["Revenue"]["document_name"] == "MSFT_10K_FY2025.pdf"
    assert mfd2["Net Profit"]["document_name"] == "AAPL_10K_FY2025.pdf"
    print("25. DOCUMENT ATTRIBUTION REMAINS CORRECT OK (multi-document headers)")

    # ---- 26) Financial Grid shows the recovered metric correctly ----
    m3 = {"financial_data": {}, "ratios": {}}
    m3 = recover_missing_metrics(
        m3, company_context=COMPANY, reporting_period=PERIOD,
        workspace_documents=[],
        providers=[FakeValidProvider("Revenue", 281.7, scale="billions")],
    )
    assert m3["external_evidence"]["recovered"].get("Revenue") == PROVENANCE_TIER.REGULATORY_API
    rows = _build_terminal_rows(m3)
    rev_row = next(r for r in rows if r["metric"] == "Revenue")
    assert rev_row["_kind"] == "verified"
    assert rev_row["Value"] != "—"
    assert rev_row["Source"] == "Regulatory API"
    print("26. FINANCIAL GRID SHOWS RECOVERED METRIC OK (verified row, Regulatory API source)")

    # ---- 27) Evidence card shows the provenance tier ----
    fields = _metric_overlay_fields(rows, m3, "Revenue")
    assert fields["tier"] == "Regulatory API"
    assert fields["provider"] == "Fake SEC"
    assert fields["provenance_tier"] == PROVENANCE_TIER.REGULATORY_API
    assert "Fake SEC" in fields["source_ref"]
    print("27. EVIDENCE CARD SHOWS PROVENANCE TIER OK (tier + provider + source_ref)")

    # ---- 28) Memo evidence card shows identical fact/provenance ----
    detail_html = _metric_detail_html(fields, "")
    assert "Tier" in detail_html and "Regulatory API" in detail_html
    assert "Fake SEC" in detail_html
    assert "281.7" in detail_html or "281" in detail_html
    print("28. MEMO EVIDENCE CARD SHOWS IDENTICAL FACT/PROVENANCE OK")

    # ---- 29) Blocked metrics remain blocked with a reason ----
    m3b = {
        "financial_data": {},
        "ratios": {},
        "missing_data": {
            "financial_data": [
                "Revenue", "Net Profit", "PAT", "EPS",
                "Total Assets", "Total Liabilities",
            ],
            "ratios": ["ROE", "ROCE", "Debt to Equity", "Current Ratio"],
        },
    }
    m3b = recover_missing_metrics(
        m3b, company_context=COMPANY, reporting_period=PERIOD,
        workspace_documents=[], providers=[],
    )
    assert m3b["external_evidence"]["blocked"].get("Revenue") == BLOCKED_REASON
    rowsb = _build_terminal_rows(m3b)
    revb = next(r for r in rowsb if r["metric"] == "Revenue")
    assert revb["_kind"] == "blocked"
    fieldsb = _metric_overlay_fields(rowsb, m3b, "Revenue")
    assert fieldsb["tier"] == "Blocked"
    assert "permitted evidence sources" in (fieldsb["note"] or "")
    print("29. BLOCKED METRICS REMAIN BLOCKED WITH REASON OK")

    # ---- 30) Demo mode unchanged ----
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.button(key="fte_btn_demo").click().run()
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    at.button(key="fte_btn_demo_memo").click().run()
    if at.exception:
        for e in at.exception:
            print(getattr(e, "stack_trace", e))
        return 1
    assert at.session_state["fte_route"] == "demo"
    assert at.session_state["fte_memo_view_open"] is True
    memo = next(str(m.value) for m in at.markdown if "fte-memo-para" in str(m.value))
    assert 'name="fte-memo-card"' in memo
    assert 'data-card="ftemetric-revenue"' in memo
    # Demo cards must NOT show a provenance tier row (static data has none).
    assert "Regulatory API" not in memo and "Tier</div>" not in memo
    print("30. DEMO MODE UNCHANGED OK (no tier row, radios/cards intact)")

    print("=== EXTERNAL EVIDENCE TESTS: ALL CHECKS COMPLETE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
