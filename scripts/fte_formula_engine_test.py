#!/usr/bin/env python3
"""Sprint 7 - Deterministic Formula Doer / Formula Engine targeted tests.

Proves the engine computes deterministically (Decimal arithmetic, no LLM),
resolves missing inputs ONLY through the Sprint 6.5 hierarchy, blocks any
unverifiable calculation, and retains a full auditable lineage.

Checks (numbered as in the sprint spec):
   1-9    ROE, ROA, Profit Margin, Operating Margin, Current Ratio,
          Debt/Equity, Revenue Growth, EPS Growth, CAGR
  10-11   Formula metadata + required-input metadata
  12-18   Derived status, missing/blocked/zero/incompatible/mismatched
          inputs, EXTERNAL_DERIVED provenance
  19-20   Input provenance retained + complete calculation lineage
  21      No LLM/provider call is required for arithmetic
  22      Demo Mode works without an API key
  23-28   Grid distinction, demo unchanged, evidence overlay/card
          integration, page metadata intact, external-evidence intact

No network, no AI, no storage - stdlib + existing app/backend code only.
"""
import importlib.util as _ilu
import re as _re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from backend.formula_engine import (
    FORMULA_REGISTRY,
    SUPPORTED_FORMULAS,
    STATUS_BLOCKED,
    STATUS_DERIVED,
    STATUS_EXTERNAL_DERIVED,
    calculate_metric,
    registry_lookup,
)
from backend.evidence_resolver import (
    PROVENANCE_TIER,
    ExternalEvidenceProvider,
    resolve_metric,
)
from backend.financial_extractor import extract_financial_data

APP = "app (1) (9).py"
_spec = _ilu.spec_from_file_location("fte_app_mod", APP)
_app_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_app_mod)

_PASS = []
_FAIL = []


def check(num, label, ok):
    line = f"{num}. {label} {'OK' if ok else 'FAIL'}"
    print(line)
    ([] if ok else _FAIL).append(line)


def F(**kw):
    """One pipeline-shaped fact."""
    f = {"reporting_period": "FY2025", "unit": "USD", "scale": "B"}
    f.update(kw)
    return f


DEMO_FD = _app_mod._demo_module3_result()["financial_data"]

# ---------------------------------------------------------------- 1-9
fd = {
    "Revenue": F(value=281700000000, source="Doc", page="26", evidence="Consolidated Statements of Income"),
    "Net Profit": F(value=98300000000, source="Doc", page="26", evidence="Consolidated Statements of Income"),
    "Operating Profit": F(value=125500000000, source="Doc", page="26"),
    "Equity": F(value=268500000000, source="Doc", page="27", evidence="Consolidated Balance Sheets"),
    "Assets": F(value=512200000000, source="Doc", page="27"),
    "Debt": F(value=96600000000, source="Doc", page="27"),
    "Current Assets": F(value=21500000000, page="27"),
    "Current Liabilities": F(value=15400000000, page="27"),
    "EPS": F(value=13.05, page="26"),
    "Previous EPS": F(value=11.79, reporting_period="FY2024", page="26"),
    "Previous Revenue": F(value=245100000000, reporting_period="FY2024", page="25"),
}

r = calculate_metric("ROE", fd)
check(1, "ROE calculation (98.30/268.50 -> 36.61%)", r["status"] == STATUS_DERIVED and r["display_value"] == "36.61%")
r = calculate_metric("ROA", fd)
check(2, "ROA calculation (98.30/512.20 -> 19.19%)", r["display_value"] == "19.19%")
r = calculate_metric("Profit Margin", fd)
check(3, "Profit Margin (98.30/281.70 -> 34.90%)", r["display_value"] == "34.90%")
r = calculate_metric("Operating Margin", fd)
check(4, "Operating Margin (125.50/281.70 -> 44.55%)", r["display_value"] == "44.55%")
r = calculate_metric("Current Ratio", fd)
check(5, "Current Ratio (21.50/15.40 -> 1.40)", r["display_value"] == "1.40")
r = calculate_metric("Debt to Equity", fd)
check(6, "Debt/Equity (96.60/268.50 -> 0.36)", r["display_value"] == "0.36")
r = calculate_metric("Revenue Growth", fd)
check(7, "Revenue Growth ((281.70-245.10)/245.10 -> 14.93%)", r["display_value"] == "14.93%")
r = calculate_metric("EPS Growth", fd)
check(8, "EPS Growth ((13.05-11.79)/11.79 -> 10.69%)", r["display_value"] == "10.69%")
fd2 = dict(fd)
fd2["CAGR Beginning Value"] = F(value=200000000000, reporting_period="FY2023")
fd2["CAGR Ending Value"] = F(value=281700000000, reporting_period="FY2025")
r = calculate_metric("CAGR", fd2)
check(9, "CAGR ((281.7/200)^(1/2)-1 -> 18.68%)", r["display_value"] == "18.68%")

# ---------------------------------------------------------------- 10-11
roe_def = registry_lookup("ROE")
check(10, "Correct formula metadata (name/formula/kind/precision)",
      roe_def.display_name == "ROE" and "Net Profit" in roe_def.formula
      and roe_def.kind == "percent" and roe_def.precision == 2)
check(11, "Correct required-input metadata (ROE -> Net Profit + Equity)",
      roe_def.required_inputs == ["Net Profit", "Equity"]
      and registry_lookup("CAGR").required_inputs == ["CAGR Beginning Value", "CAGR Ending Value"]
      and "roe" in roe_def.aliases)

# ---------------------------------------------------------------- 12-18
check(12, "Verified inputs produce a derived result",
      r["status"] == STATUS_DERIVED and r["provenance"] == PROVENANCE_TIER.DERIVED)
fd_miss = dict(fd)
del fd_miss["Equity"]
r = calculate_metric("ROE", fd_miss, context={"recover": False})
check(13, "Missing input produces Blocked with precise reason",
      r["status"] == STATUS_BLOCKED and "Equity" in (r.get("reason") or ""))
fd_blocked = dict(fd)
fd_blocked["Equity"] = {"value": 268500000000, "provenance_tier": PROVENANCE_TIER.BLOCKED}
r = calculate_metric("ROE", fd_blocked, context={"recover": False})
check(14, "Blocked input cannot be used in a calculation",
      r["status"] == STATUS_BLOCKED and "Equity" in (r.get("reason") or ""))
fd_zero = dict(fd)
fd_zero["Equity"] = F(value=0)
r = calculate_metric("ROE", fd_zero, context={"recover": False})
check(15, "Zero denominator produces Blocked",
      r["status"] == STATUS_BLOCKED and "zero" in (r.get("reason") or "").lower())
fd_samep = dict(fd)
fd_samep["Previous Revenue"] = F(value=245100000000, reporting_period="FY2025")
r = calculate_metric("Revenue Growth", fd_samep, context={"recover": False})
check(16, "Incompatible periods produce Blocked",
      r["status"] == STATUS_BLOCKED and "period" in (r.get("reason") or "").lower())
fd_scale = dict(fd)
fd_scale["Equity"] = F(value=268500, scale="M")
r = calculate_metric("ROE", fd_scale, context={"recover": False})
check(17, "Unit/scale mismatch is rejected deterministically",
      r["status"] == STATUS_BLOCKED and "scale" in (r.get("reason") or "").lower())


class _FakeSecProvider(ExternalEvidenceProvider):
    name = "Fake SEC EDGAR"

    def is_configured(self):
        return True

    def resolve_metric(self, company_identifier, metric, reporting_period):
        if metric != "Equity":
            return None
        return {
            "value": 268500000000,
            "cik": company_identifier,
            "company_identifier": company_identifier,
            "reporting_period": reporting_period,
            "metric": "Equity",
            "currency": "USD",
            "scale": "billions",
            "evidence": "SEC EDGAR 10-K cover, FY2025",
            "provider_identifier": "eq-0000789019",
        }


ctx_ext = {
    "company_context": {"cik": "0000789019", "company_name": "Microsoft Corporation", "currency": "USD"},
    "reporting_period": "FY2025",
    "providers": [_FakeSecProvider()],
}
r = calculate_metric("ROE", fd_miss, context=ctx_ext)
check(18, "External-derived input produces EXTERNAL_DERIVED provenance",
      r["status"] == STATUS_EXTERNAL_DERIVED and r["provenance"] == PROVENANCE_TIER.EXTERNAL_DERIVED
      and any(i["provenance_tier"] == PROVENANCE_TIER.REGULATORY_API for i in r["inputs"]))

# ---------------------------------------------------------------- 19-21
r = calculate_metric("ROE", fd)
check(19, "Every derived result retains input provenance",
      len(r["inputs"]) == 2
      and all(i.get("provenance_tier") for i in r["inputs"])
      and r["inputs"][0]["metric"] == "Net Profit")
check(20, "Calculation lineage is complete",
      len(r["calculation_steps"]) >= 4
      and "Formula" in (r.get("lineage") or "")
      and r["formula"] == "Net Profit \u00f7 Equity \u00d7 100")
check(21, "No LLM/provider call required for arithmetic",
      calculate_metric("ROE", fd, context={"providers": []})["status"] == STATUS_DERIVED
      and calculate_metric("EPS Growth", fd, context={"providers": []})["status"] == STATUS_DERIVED)

# ---------------------------------------------------------------- 22
r = calculate_metric("ROE", DEMO_FD, context={})
check(22, "Demo Mode works without an API key (ROE -> 36.61%)",
      r["status"] == STATUS_DERIVED and r["display_value"] == "36.61%")

# ---------------------------------------------------------------- 23-28
m3 = {
    "financial_data": {"Net Profit": fd["Net Profit"], "Equity": fd["Equity"]},
    "ratios": {
        "ROE": {
            "value": 36.61, "source": "Calculated",
            "provenance_tier": PROVENANCE_TIER.EXTERNAL_DERIVED,
            "formula": "Net Profit \u00f7 Equity \u00d7 100",
            "inputs": ["Net Profit", "Equity"],
            "input_provenance": {"Net Profit": PROVENANCE_TIER.DOCUMENT,
                                 "Equity": PROVENANCE_TIER.REGULATORY_API},
        }
    },
    "missing_data": {"financial_data": [], "ratios": []},
}
rows = _app_mod._build_terminal_rows(m3)
roe_row = next((x for x in rows if x["metric"] == "ROE"), None)
check(23, "Financial Grid distinguishes externally-derived rows",
      roe_row is not None and "External + Derived" in roe_row["Status"]
      and roe_row["_kind"] == "derived")
fields = _app_mod._metric_overlay_fields(rows, m3, "ROE")
check(24, "Evidence overlay shows provenance tier + per-input rows",
      fields["tier"] == "External + Derived"
      and isinstance(fields.get("calc_inputs"), list)
      and len(fields["calc_inputs"]) == 2
      and fields["calc_inputs"][1]["tier"] == "Regulatory API")
detail = _app_mod._metric_detail_html(fields, "Return generated from verified inputs.")
demo_card = _app_mod._demo_memo_card_html(fields, "Return generated from verified inputs.")
check(25, "Detail dialog + memo card render calculation/input provenance",
      "Calculation" in detail and "Regulatory API" in detail
      and "Inputs" in demo_card and "Regulatory API" in demo_card)
check(26, "Sprint 6 page metadata remains intact",
      extract_financial_data("Annual Report\n========== PAGE 2 ==========\nTotal Revenue was 281,700,000,000.")
      .get("Revenue", {}).get("page") == 2)
check(27, "Sprint 6.5 external evidence remains fail-closed",
      resolve_metric("Equity", company_context={"cik": "0000789019"},
                     reporting_period="FY2025", providers=[])["provenance_tier"]
      == PROVENANCE_TIER.BLOCKED)
demo_m3 = _app_mod._demo_module3_result()
check(28, "Demo Mode unchanged (values + memo card mechanism intact)",
      demo_m3["financial_data"]["Net Profit"]["value"] == 98300000000
      and demo_m3["ratios"]["ROE"]["value"] == 0.366
      and "fte-memo-radio" in _app_mod._demo_memo_cards_html(rows, demo_m3, ["ROE"]))

print()
if _FAIL:
    print("=== FORMULA ENGINE TESTS: %d FAILED ===" % len(_FAIL))
    for f in _FAIL:
        print(" ", f)
    sys.exit(1)
print("=== FORMULA ENGINE TESTS: ALL CHECKS COMPLETE ===")
