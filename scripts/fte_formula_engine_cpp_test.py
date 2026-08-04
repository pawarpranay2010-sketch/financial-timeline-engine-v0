#!/usr/bin/env python3
"""Sprint 7 - C++ Deterministic Formula Engine targeted tests.

Proves the hybrid architecture: the deterministic Formula Engine is
implemented in C++ (formula_engine/) and Python (backend/formula_engine_cpp.py)
feeds it already-verified facts over a minimal JSON interface.

Checks (numbered as in the sprint spec):
   1-9    ROE, ROA, Profit Margin, Operating Margin, Current Ratio,
          Debt/Equity, Revenue Growth, EPS Growth, CAGR (C++ binary)
  10-11   Formula metadata + required-input metadata (C++ --registry)
  12-17   Verified inputs, missing input, blocked input, zero denominator,
          period mismatch, unit/scale mismatch
  18-21   DOCUMENT / APPENDIX / REGULATORY_API inputs and EXTERNAL_DERIVED
  22-23   Complete lineage + provenance preservation
  24      Invalid metric rejection
  25      Demo calculation (no API key)
  26      Deterministic repeated calculation is identical

No network, no AI, no storage - the C++ binary is built on demand when
missing, and stdlib + existing backend code only.
"""
import importlib.util as _ilu
import json
import os
import subprocess
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

# Build the C++ binary on demand when missing.
BIN = os.path.join("formula_engine", "bin", "formula_engine")
if not os.path.exists(BIN):
    r = subprocess.run(["sh", "formula_engine/build.sh"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

from backend.formula_engine import calculate_metric  # noqa: E402
from backend.formula_engine_cpp import (  # noqa: E402
    cpp_available,
    cpp_calculate,
    binary_path,
)

_PASS = []
_FAIL = []


def check(num, label, ok):
    line = f"{num}. {label} {'OK' if ok else 'FAIL'}"
    print(line)
    ([] if ok else _FAIL).append(line)


def run_bin(payload):
    p = subprocess.run([BIN], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def fact(value, period="FY2025", unit="USD", scale="B", tier="DOCUMENT", **kw):
    f = {"value": value, "reporting_period": period, "unit": unit,
         "scale": scale, "provenance_tier": tier}
    f.update(kw)
    return f


BASE = {
    "Revenue": fact(281700000000, page="26"),
    "Net Profit": fact(98300000000, page="26"),
    "Operating Profit": fact(125500000000, page="26"),
    "Equity": fact(268500000000, page="27"),
    "Assets": fact(512200000000, page="27"),
    "Debt": fact(96600000000, page="27"),
    "Current Assets": fact(21500000000, page="27"),
    "Current Liabilities": fact(15400000000, page="27"),
    "EPS": fact(13.05, page="26"),
    "Previous EPS": fact(11.79, period="FY2024", page="26"),
    "Previous Revenue": fact(245100000000, period="FY2024", page="25"),
}

# ---------------------------------------------------------------- 1-9
out = run_bin({"metric": "ROE", "inputs": BASE})
check(1, "ROE (C++) -> 36.61%", out["display_value"] == "36.61%" and out["status"] == "DERIVED_VERIFIED")
out = run_bin({"metric": "ROA", "inputs": BASE})
check(2, "ROA (C++) -> 19.19%", out["display_value"] == "19.19%")
out = run_bin({"metric": "Profit Margin", "inputs": BASE})
check(3, "Profit Margin (C++) -> 34.90%", out["display_value"] == "34.90%")
out = run_bin({"metric": "Operating Margin", "inputs": BASE})
check(4, "Operating Margin (C++) -> 44.55%", out["display_value"] == "44.55%")
out = run_bin({"metric": "Current Ratio", "inputs": BASE})
check(5, "Current Ratio (C++) -> 1.40", out["display_value"] == "1.40")
out = run_bin({"metric": "Debt to Equity", "inputs": BASE})
check(6, "Debt/Equity (C++) -> 0.36", out["display_value"] == "0.36")
out = run_bin({"metric": "Revenue Growth", "inputs": BASE})
check(7, "Revenue Growth (C++) -> 14.93%", out["display_value"] == "14.93%")
out = run_bin({"metric": "EPS Growth", "inputs": BASE})
check(8, "EPS Growth (C++) -> 10.69%", out["display_value"] == "10.69%")
cagr_inputs = dict(BASE)
cagr_inputs["CAGR Beginning Value"] = fact(200000000000, period="FY2023")
cagr_inputs["CAGR Ending Value"] = fact(281700000000, period="FY2025")
out = run_bin({"metric": "CAGR", "inputs": cagr_inputs})
check(9, "CAGR (C++) -> 18.68%", out["display_value"] == "18.68%")

# ---------------------------------------------------------------- 10-11
reg = json.loads(subprocess.run([BIN, "--registry"], capture_output=True,
                                text=True, timeout=30).stdout)
keys = [r["metric_key"] for r in reg]
check(10, "Formula metadata (9 formulas, correct fields)",
      len(reg) == 9 and all({"metric_key", "display_name", "formula",
                             "required_inputs", "unit", "precision"} <= set(r)
                            for r in reg)
      and set(keys) == {"ROE", "ROA", "Profit Margin", "Operating Margin",
                        "Current Ratio", "Debt to Equity", "Revenue Growth",
                        "EPS Growth", "CAGR"})
roe_def = next(r for r in reg if r["metric_key"] == "ROE")
check(11, "Required-input metadata (ROE -> Net Profit + Equity)",
      roe_def["required_inputs"] == ["Net Profit", "Equity"]
      and "Net Profit" in roe_def["formula"] and roe_def["precision"] == 2)

# ---------------------------------------------------------------- 12-17
out = run_bin({"metric": "ROE", "inputs": BASE})
check(12, "Verified inputs produce DERIVED_VERIFIED", out["status"] == "DERIVED_VERIFIED")
no_eq = {k: v for k, v in BASE.items() if k != "Equity"}
out = run_bin({"metric": "ROE", "inputs": no_eq})
check(13, "Missing input -> BLOCKED", out["status"] == "BLOCKED" and "Equity" in (out.get("block_reason") or ""))
# A BLOCKED-provenance input can never be used (Python filters before C++).
fd_blocked = dict(BASE)
fd_blocked["Equity"] = {"value": 268500000000, "provenance_tier": "BLOCKED"}
r = calculate_metric("ROE", fd_blocked, context={"recover": False})
check(14, "Blocked input cannot calculate",
      r["status"] == "blocked" and "Equity" in (r.get("reason") or ""))
zero = dict(BASE)
zero["Equity"] = fact(0)
out = run_bin({"metric": "ROE", "inputs": zero})
check(15, "Zero denominator -> BLOCKED",
      out["status"] == "BLOCKED" and "zero" in (out.get("block_reason") or "").lower())
same_p = dict(BASE)
same_p["Previous Revenue"] = fact(245100000000, period="FY2025")
out = run_bin({"metric": "Revenue Growth", "inputs": same_p})
check(16, "Period mismatch -> BLOCKED",
      out["status"] == "BLOCKED" and "period" in (out.get("block_reason") or "").lower())
scale_m = dict(BASE)
scale_m["Equity"] = fact(268500, scale="M")
out = run_bin({"metric": "ROE", "inputs": scale_m})
check(17, "Unit/scale mismatch rejected",
      out["status"] == "BLOCKED" and "scale" in (out.get("block_reason") or "").lower())

# ---------------------------------------------------------------- 18-21
r = calculate_metric("ROE", BASE)
check(18, "DOCUMENT-derived result (DERIVED provenance, DOCUMENT inputs)",
      r["status"] == "derived" and r["provenance"] == "DERIVED"
      and r["display_value"] == "36.61%"
      and all(i["provenance_tier"] == "DOCUMENT" for i in r["inputs"]))
appendix = dict(BASE)
appendix["Equity"] = fact(268500000000, tier="APPENDIX", page="S-1")
r = cpp_calculate("ROE", appendix)
check(19, "APPENDIX-derived input -> EXTERNAL_DERIVED",
      r is not None and r["status"] == "external_derived")
regapi = dict(BASE)
regapi["Equity"] = fact(268500000000, tier="REGULATORY_API", provider="SEC EDGAR")
r = cpp_calculate("ROE", regapi)
check(20, "REGULATORY_API-derived input -> EXTERNAL_DERIVED",
      r is not None and r["status"] == "external_derived")
r = calculate_metric("ROE", regapi)
check(21, "EXTERNAL_DERIVED result status + provenance",
      r["status"] == "external_derived"
      and r["provenance"] == "EXTERNAL_DERIVED"
      and r["status_label"] == "🟣 External + Derived")

# ---------------------------------------------------------------- 22-23
out = run_bin({"metric": "ROE", "inputs": BASE})
check(22, "Complete calculation lineage",
      len(out.get("calculation_steps") or []) >= 2
      and "Formula" in (out.get("lineage") or "")
      and "Result" in (out.get("lineage") or ""))
r = calculate_metric("ROE", BASE)
check(23, "Provenance preservation (inputs carry tier/page/evidence)",
      len(r.get("inputs") or []) == 2
      and all(i.get("provenance_tier") for i in r["inputs"])
      and r["inputs"][0]["page"] == "26"
      and len(r.get("calculation_steps") or []) >= 4)

# ---------------------------------------------------------------- 24
out = run_bin({"metric": "DCF", "inputs": BASE})
check(24, "Invalid metric rejected (UNANALYZED)",
      out["status"] == "UNANALYZED" and out.get("value") is None)

# ---------------------------------------------------------------- 25
spec = _ilu.spec_from_file_location("fte_app_mod", "app (1) (9).py")
mod = _ilu.module_from_spec(spec)
spec.loader.exec_module(mod)
demo_fd = mod._demo_module3_result()["financial_data"]
r = calculate_metric("ROE", demo_fd, context={})
check(25, "Demo calculation works without an API key (ROE -> 36.61%)",
      r["status"] == "derived" and r["display_value"] == "36.61%")

# ---------------------------------------------------------------- 26
a1 = run_bin({"metric": "ROE", "inputs": BASE})
a2 = run_bin({"metric": "ROE", "inputs": BASE})
b1 = calculate_metric("ROE", BASE)
b2 = calculate_metric("ROE", BASE)
check(26, "Deterministic repeated calculation is identical",
      a1 == a2 and b1["display_value"] == b2["display_value"]
      and a1["display_value"] == b1["display_value"])

# ---------------------------------------------------------------- bridge
check(27, "C++ binary is preferred and reachable by the bridge",
      cpp_available() and binary_path() is not None)
os.environ["FTE_FORMULA_ENGINE_BIN"] = "/nonexistent/formula_engine"
fb_unavailable = (not cpp_available()) and cpp_calculate("ROE", BASE) is None
del os.environ["FTE_FORMULA_ENGINE_BIN"]
_fallback = calculate_metric("ROE", BASE)
check(28, "Fallback intact (missing binary -> Python Decimal path still works)",
      fb_unavailable and cpp_available() and _fallback["display_value"] == "36.61%")

print()
if _FAIL:
    print("=== C++ FORMULA ENGINE TESTS: %d FAILED ===" % len(_FAIL))
    for f in _FAIL:
        print(" ", f)
    sys.exit(1)
print("=== C++ FORMULA ENGINE TESTS: ALL CHECKS COMPLETE ===")
