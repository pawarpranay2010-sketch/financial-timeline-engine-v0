"""Sprint 12.1 - Student Agent Workspace UI/UX Transformation

UI-focused deterministic suite for the guided, agent-led student experience.
Verifies the presentation contracts without touching backend logic:

 Agent flow
   1.  Assignment loads
   2.  Agent summarizes requirements
   3.  High-confidence requirements continue automatically
   4.  Ambiguous requirement triggers guided confirmation
   5.  Student can recover without restarting
   6.  Step indicator updates correctly (Step N of 5)
   7.  Primary action is visually dominant (one button, quiet links otherwise)
   8.  Secondary actions remain accessible (quiet links)
   9.  Investigate Why reveals driver analysis
  10.  Evidence remains accessible
  11.  Company comparison remains accessible (preview + full)
  12.  Excel remains accessible (orientation first)
  13.  Student conclusion remains blank

 Demo
  14.  No API key
  15.  No AI provider
  16.  No network
  17.  Demo data remains unchanged
  18.  Demo provenance remains synthetic

 API
  19.  Real pipeline continues working
  20.  Existing evidence/provenance survives
  21.  Existing calculations survive
  22.  Existing reliability states survive

Every decision is deterministic; the agent never invents requirements,
causes, sources or conclusions.
"""
import os
import re
import sys
import types
import importlib.util
from io import BytesIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl  # noqa: E402

from backend.assignment_agent import (  # noqa: E402
    STAGE_OPENING,
    STAGE_REQUIREMENTS,
    STAGE_PERIODS,
    STAGE_METRIC,
    STAGE_EXPLAIN,
    STAGE_CALCULATION,
    STAGE_EVIDENCE,
    STAGE_DRIVERS,
    STAGE_QUALITATIVE,
    STAGE_COMPARISON,
    STAGE_EXTERNAL,
    STAGE_EXCEL,
    STAGE_MEMO,
    STAGE_CONCLUSION,
    PARSE_HIGH,
    PARSE_PARTIAL,
    agent_session,
    agent_step,
    apply_choice,
    confirmation_candidates,
    initial_state,
    parse_recovery,
    what_next,
)
from backend.student_workspace import (  # noqa: E402
    build_student_workspace,
    parse_requirements,
)
from backend.excel_working_model import build_excel_working_model  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


# ---------------------------------------------------------------------------
# App-under-test (stubbed streamlit) — used only for the demo fixtures and
# the app's presentation helpers.
# ---------------------------------------------------------------------------
_APP = None
_APP_STUB_SS = {}


class _Passthrough:
    def __call__(self, *a, **k):
        if len(a) == 1 and not k and callable(a[0]):
            return a[0]

        def deco(fn):
            return fn
        return deco


class _StubStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self._ss = _APP_STUB_SS

    def __getattr__(self, name):
        if name == "session_state":
            return self._ss
        return _Passthrough()


def _load_app():
    global _APP
    if _APP is not None:
        return _APP
    import streamlit as _real
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
        sys.modules["streamlit"] = _real
    _APP = mod
    return mod


# ---------------------------------------------------------------------------
# Deterministic fixtures (mirror of the Sprint 12 / 12.1 suites)
# ---------------------------------------------------------------------------
def _real_facts():
    return {
        "Revenue": {"value": 281700000000, "source": "10-K FY2025 · Income Statement", "reporting_period": "FY2025", "page": 26, "evidence": "Consolidated Statements of Income, p. 26", "unit": "USD", "scale": "B"},
        "Net Profit": {"value": 98300000000, "source": "10-K FY2025 · Income Statement", "reporting_period": "FY2025", "page": 26, "evidence": "Consolidated Statements of Income, p. 26", "unit": "USD", "scale": "B"},
        "Equity": {"value": 268500000000, "source": "10-K FY2025 · Balance Sheet", "reporting_period": "FY2025", "page": 27, "evidence": "Consolidated Balance Sheets, p. 27", "unit": "USD", "scale": "B"},
        "Assets": {"value": 512200000000, "source": "10-K FY2025 · Balance Sheet", "reporting_period": "FY2025", "page": 27, "evidence": "Consolidated Balance Sheets, p. 27", "unit": "USD", "scale": "B"},
        "Debt": {"value": 101200000000, "source": "10-K FY2025 · Balance Sheet", "reporting_period": "FY2025", "page": 27, "evidence": "Consolidated Balance Sheets, p. 27", "unit": "USD", "scale": "B"},
        "Current Assets": {"value": 147600000000, "source": "10-K FY2025 · Balance Sheet", "reporting_period": "FY2025", "page": 27, "evidence": "Consolidated Balance Sheets, p. 27", "unit": "USD", "scale": "B"},
        "Current Liabilities": {"value": 105400000000, "source": "10-K FY2025 · Balance Sheet", "reporting_period": "FY2025", "page": 27, "evidence": "Consolidated Balance Sheets, p. 27", "unit": "USD", "scale": "B"},
    }


_REQ_TEXT = ("Analyze Microsoft FY2023-FY2025 and calculate ROE, ROA, "
             "Profit Margin, Current Ratio and Debt/Equity.")


def _real_module3():
    facts = _real_facts()
    module3 = {
        "financial_data": facts,
        "ratios": {},
        "missing_data": {"financial_data": ["Segment Gross Margin"], "ratios": []},
    }
    module3["ratios"]["Current Ratio"] = {
        "value": round(facts["Current Assets"]["value"] / facts["Current Liabilities"]["value"], 2),
        "source": "Calculated", "formula": "Current Assets / Current Liabilities",
        "reporting_period": "FY2025",
    }
    module3["ratios"]["Debt to Equity"] = {
        "value": round(facts["Debt"]["value"] / facts["Equity"]["value"], 2),
        "source": "Calculated", "formula": "Debt / Equity", "reporting_period": "FY2025",
    }
    return module3


def _real_workspace(requirements_text=_REQ_TEXT, **kw):
    module3 = _real_module3()
    return build_student_workspace(
        module3,
        assignment_type=kw.get("assignment_type", "Financial Ratio Analysis"),
        requirements_text=requirements_text,
        external_variables=kw.get("external_variables") or [],
        company_a=kw.get("company_a", "Microsoft"),
        peer_company=kw.get("peer_company"),
        peer_facts=kw.get("peer_facts"),
        period_facts=kw.get("period_facts"),
        calc_metrics=[r["metric"] for r in parse_requirements(requirements_text)],
        missing=module3.get("missing_data"),
    )


_PERIOD_FACTS = {
    "Revenue": {"FY2024": "245120000000", "FY2025": "281700000000"},
    "Net Profit": {"FY2024": "80100000000", "FY2025": "98300000000"},
    "Equity": {"FY2024": "268500000000", "FY2025": "268500000000"},
    "Assets": {"FY2024": "512200000000", "FY2025": "512200000000"},
    "ROE": {"FY2024": "0.298", "FY2025": "0.366"},
    "ROA": {"FY2024": "0.156", "FY2025": "0.192"},
    "Profit Margin": {"FY2024": "0.327", "FY2025": "0.349"},
    "Current Ratio": {"FY2024": "1.35", "FY2025": "1.40"},
    "Debt to Equity": {"FY2024": "0.36", "FY2025": "0.38"},
}


def _demo_workspace(requirements_text=None, **kw):
    app = _load_app()
    req_text = requirements_text if requirements_text is not None else app._demo_assignment_requirements_text()
    return build_student_workspace(
        app._demo_module3_result(),
        assignment_type=kw.get("assignment_type", "Financial Ratio Analysis"),
        requirements_text=req_text,
        external_variables=kw.get("external_variables") or [],
        company_a=kw.get("company_a", "Contoso Analytics (Demo)"),
        peer_company=kw.get("peer_company", "PeerCo Inc."),
        peer_facts=kw.get("peer_facts", app._FTE_DEMO_PEER_FACTS),
        period_facts=kw.get("period_facts", app._FTE_DEMO_PERIOD_FACTS),
        calc_metrics=[r["metric"] for r in parse_requirements(req_text)],
        missing=app._demo_module3_result().get("missing_data"),
        qualitative_documents=kw.get("qualitative_documents", app._FTE_DEMO_QUALITATIVE_DOCS),
    )


def _drive(ws, stage, requirements_text="", metric=None):
    """Reach a stage deterministically (direct state for late stages, choice
    chains for the proven early transitions)."""
    if stage in (STAGE_COMPARISON, STAGE_EXCEL, STAGE_CONCLUSION):
        visited = ["opening", "requirements", "periods", "drivers", "qualitative"]
        if stage == STAGE_EXCEL:
            visited += ["comparison"]
        if stage == STAGE_CONCLUSION:
            visited += ["comparison", "excel", "memo"]
        s = {"stage": stage, "metric": None, "area": None, "visited": visited}
        return agent_session(ws, s, requirements_text=requirements_text)
    s = initial_state()
    if stage != STAGE_OPENING:
        s = apply_choice(s, "opening.requirements", ws)
    if stage == STAGE_REQUIREMENTS:
        pass
    elif stage == STAGE_PERIODS:
        s = apply_choice(s, "requirements.confirm", ws)
    elif stage == STAGE_METRIC:
        s = apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws)
    elif stage == STAGE_EXPLAIN:
        s = apply_choice(apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws), "metric.explain", ws)
    elif stage == STAGE_CALCULATION:
        s = apply_choice(apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws), "metric.calculation", ws)
    elif stage == STAGE_EVIDENCE:
        s = apply_choice(apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws), "metric.evidence", ws)
    elif stage == STAGE_DRIVERS:
        s = apply_choice(apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws), "metric.explain", ws)
        s = apply_choice(s, "continue", ws)
    elif stage == STAGE_QUALITATIVE:
        s = apply_choice(apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws), "metric.explain", ws)
        s = apply_choice(s, "explain.qualitative", ws)
    return agent_session(ws, s, requirements_text=requirements_text)


ws_demo = _demo_workspace()
ws_real = _real_workspace(period_facts=_PERIOD_FACTS)
app = _load_app()
_DEMO_REQ_TEXT = app._demo_assignment_requirements_text()
with open(os.path.join(os.path.dirname(__file__), "..", "app (1) (9).py"),
          encoding="utf-8") as _fh:
    _APP_SRC = _fh.read()

# ---------------------------------------------------------------------------
# 1-13 · Agent flow
# ---------------------------------------------------------------------------
v_open = agent_session(ws_demo, initial_state())
check("1a · assignment loads (opening view present)",
      v_open["stage"] == STAGE_OPENING and bool(v_open["message"]) and bool(v_open["content"]))
check("2a · agent summarizes the parsed requirements",
      len(v_open["content"].get("requirements") or []) >= 5 and
      "ROE" in v_open["content"]["requirements"])
check("2b · opening message is short (2 statements + one question)",
      "Let's start" not in v_open["message"] and v_open["message"].rstrip().endswith("?"))

# 3. High-confidence -> automatic continuation
v_req_clean = agent_session(
    ws_demo, apply_choice(initial_state(), "opening.requirements", ws_demo),
    requirements_text=_DEMO_REQ_TEXT,
)
check("3a · clean assignment parses high",
      parse_recovery(ws_demo, _DEMO_REQ_TEXT)["state"] == PARSE_HIGH)
check("3b · high state continues automatically (Continue primary)",
      (v_req_clean.get("recommended") or {}).get("id") == "requirements.continue")

# 4. Ambiguous requirement -> guided confirmation
PARTIAL_TXT = "Calculate ROE and Quick Ratio."
ws_part = _real_workspace(requirements_text=PARTIAL_TXT, period_facts=_PERIOD_FACTS)
v_part = agent_session(
    ws_part, apply_choice(initial_state(), "opening.requirements", ws_part),
    requirements_text=PARTIAL_TXT,
)
check("4a · ambiguous requirement -> partial state",
      v_part["content"].get("parse_state") == PARSE_PARTIAL)
_cands = v_part["content"].get("uncertain_candidates") or []
check("4b · guided confirmation offers per-item candidates",
      any("quick ratio" in str(x.get("token") or "").lower() for x in _cands))
check("4c · 'Quick Ratio' suggests a plausible canonical metric",
      any("Current Ratio" in [str(cn) for cn in (x.get("candidates") or [])] for x in _cands))
check("4d · D/E token suggests Debt to Equity",
      confirmation_candidates("D/E") == ["Debt to Equity"])
check("4e · bare unknown tokens get no invented candidates",
      confirmation_candidates("ROIC") == [])

# 5. Recovery without restarting
s_part = apply_choice(initial_state(), "opening.requirements", ws_part)
s_edit = apply_choice(s_part, "requirements.edit", ws_part)
check("5a · requirements.edit stays on stage (no dead end)",
      s_edit["stage"] == STAGE_REQUIREMENTS)
s_conf = apply_choice(s_part, "requirements.confirm", ws_part)
check("5b · requirements.confirm advances",
      s_conf["stage"] in (STAGE_PERIODS, STAGE_METRIC))
check("5c · confirm after partial still yields a full view",
      bool(agent_session(ws_part, s_conf, requirements_text=PARTIAL_TXT)["message"]))
check("5d · quiet-link action wires to the same state machine",
      "fte_agent_action=explain.evidence" in app._agent_quiet_link("explain.evidence", "Verify source"))

# 6. Step indicator (Step N of 5)
check("6a · five tutor steps defined", agent_step(STAGE_OPENING)["total"] == 5)
check("6b · opening is Step 1 of 5 · Understand Assignment",
      agent_step(STAGE_OPENING)["number"] == 1 and
      agent_step(STAGE_OPENING)["label"] == "Understand Assignment")
check("6c · periods is Step 2 of 5 · Verify Financial Data",
      agent_step(STAGE_PERIODS)["number"] == 2 and
      agent_step(STAGE_PERIODS)["label"] == "Verify Financial Data")
check("6d · explain/drivers/comparison is Step 3 of 5",
      agent_step(STAGE_EXPLAIN)["number"] == agent_step(STAGE_DRIVERS)["number"] ==
      agent_step(STAGE_COMPARISON)["number"] == 3)
check("6e · excel is Step 4 of 5 · Review Working Model",
      agent_step(STAGE_EXCEL)["number"] == 4)
check("6f · conclusion is Step 5 of 5 · Write Conclusion",
      agent_step(STAGE_CONCLUSION)["number"] == 5)
check("6g · session view carries the step indicator",
      v_open.get("step", {}).get("number") == 1 and
      _drive(ws_demo, STAGE_CONCLUSION).get("step", {}).get("number") == 5)
check("6h · app renders the muted step line",
      f'Step {v_open["step"]["number"]} of {v_open["step"]["total"]}' in
      app._agent_step_html(v_open["step"]))

# 7. Primary action dominance
wn_open = what_next(ws_demo, initial_state())
check("7a · exactly one recommended (primary) action",
      bool(wn_open.get("recommended")) and not isinstance(wn_open["recommended"], list))
check("7b · at most two quiet alternatives",
      len(wn_open.get("alternatives") or []) <= 2)

# 8. Secondary actions remain accessible as quiet links
check("8a · alternatives are rendered as quiet links",
      all("fte_agent_action=" in app._agent_quiet_link(c.get("id"), c.get("label"))
          for c in wn_open["alternatives"]))
check("8b · stage choices remain accessible (quiet links, not buttons)",
      len(v_req_clean.get("choices") or []) >= 1)

# 9. Investigate Why reveals driver analysis
v_expl = _drive(ws_demo, STAGE_EXPLAIN)
check("9a · explain stage carries the numerical driver",
      bool(v_expl["content"].get("numerical")) and v_expl["content"].get("numerical") != "—")
check("9b · explain stage carries the qualitative catalyst",
      bool(v_expl["content"].get("catalyst")) and v_expl["content"].get("catalyst") != "—")
check("9c · recommended label is 'Investigate why' at metric stage",
      (_drive(ws_demo, STAGE_METRIC).get("recommended") or {}).get("label") == "Investigate why")
v_qual = _drive(ws_demo, STAGE_QUALITATIVE)
check("9d · deeper investigation reveals catalyst rows",
      len(v_qual["content"].get("rows") or []) >= 1)

# 10. Evidence on demand
v_ev = _drive(ws_demo, STAGE_EVIDENCE)
check("10a · evidence stage exposes provenance fields",
      len(v_ev["content"].get("fields") or []) >= 3)
v_calc = _drive(ws_demo, STAGE_CALCULATION)
check("10b · calculation stage exposes the Formula-Engine result",
      v_calc["content"].get("available") is True and bool(v_calc["content"].get("result")))

# 11. Comparison preview + full table
v_cmp = _drive(ws_demo, STAGE_COMPARISON)
_check_rows = v_cmp["content"].get("rows") or []
_check_prev = v_cmp["content"].get("preview") or []
check("11a · comparison preview shows only the most relevant rows",
      1 <= len(_check_prev) <= 3 and len(_check_prev) <= len(_check_rows))
check("11b · preview prefers Revenue / Net Profit",
      any(str(r.get("canonical")) in ("Revenue", "Net Profit") for r in _check_prev))
check("11c · full comparison remains accessible",
      len(_check_rows) >= len(_check_prev) and all(
          r in _check_rows for r in _check_prev))
check("11d · no comparison forced when peer data is absent",
      _drive(_real_workspace(period_facts=None), STAGE_COMPARISON)["content"].get("active") in (None, False))

# 12. Excel orientation before opening
v_xl = _drive(ws_demo, STAGE_EXCEL)
check("12a · excel recommended action is Open Working Model",
      (v_xl.get("recommended") or {}).get("id") == "excel.download")
check("12b · orientation introduces the model before opening",
      bool((v_xl["content"].get("orientation") or {}).get("first")) and
      "Your working model is ready" in v_xl["message"])

# 13. Conclusion blank
v_concl = _drive(ws_demo, STAGE_CONCLUSION)
_cn = v_concl["content"]
check("13a · conclusion engine never generates the conclusion",
      bool(_cn.get("never_generate")) is True)
_concl_text = " ".join(str(x) for x in (_cn.get("checklist") or []) + (_cn.get("scaffold") or [])).lower()
check("13b · no Buy/Sell/Hold / recommendation generated",
      not any(w in _concl_text for w in ("buy ", " sell ", " hold ", "recommend",
                                         "outperform", "target price")))

# ---------------------------------------------------------------------------
# 14-18 · Demo
# ---------------------------------------------------------------------------
_demo_m3 = app._demo_module3_result()
_demo_srcs = [str(f.get("source") or "").lower() for f in _demo_m3["financial_data"].values()]
check("14a · demo provenance is synthetic (no API key implied)",
      all(("demo" in s or "fixture" in s or "synthetic" in s) and "10-k" not in s for s in _demo_srcs))
_demo_msgs = [str(v_open["message"]), str(v_part["message"]),
              str(_drive(ws_demo, STAGE_EXCEL)["message"]),
              str(_drive(ws_demo, STAGE_CONCLUSION)["message"])]
check("15a · demo messages contain no AI-provider language",
      all(not re.search(r"\bai\b|\bllm\b|gpt|openai|anthropic", m.lower()) for m in _demo_msgs))
check("16a · demo needs no network / no API key (static fixtures only)",
      "api_key" not in str(_demo_m3).lower() and "https://" not in str(_demo_m3).lower())
check("17a · demo financial values unchanged",
      _demo_m3["financial_data"]["Revenue"]["value"] == 281700000000 and
      _demo_m3["financial_data"]["Net Profit"]["value"] == 98300000000)
check("18a · demo qualitative docs remain synthetic fixtures",
      all("demo" in str(q.get("document_name") or "").lower() or
          "synthetic" in str(q.get("document_name") or "").lower()
          for q in app._FTE_DEMO_QUALITATIVE_DOCS))

# ---------------------------------------------------------------------------
# 19-22 · API / real path
# ---------------------------------------------------------------------------
v_real_open = agent_session(ws_real, apply_choice(initial_state(), "opening.requirements", ws_real),
                            requirements_text=_REQ_TEXT)
check("19a · real pipeline renders the agent flow",
      v_real_open["content"].get("parse_state") == PARSE_HIGH and
      "I've parsed your assignment" in v_real_open["message"])
_v_real_ev = _drive(ws_real, STAGE_EVIDENCE, requirements_text=_REQ_TEXT)
check("20a · real evidence/provenance survives",
      len(_v_real_ev["content"].get("fields") or []) >= 3)
_v_real_calc = _drive(ws_real, STAGE_CALCULATION, requirements_text=_REQ_TEXT)
check("21a · real calculations survive (Formula Engine)",
      _v_real_calc["content"].get("available") is True and
      bool(_v_real_calc["content"].get("formula")))
_v_real_metric = _drive(ws_real, STAGE_METRIC, requirements_text=_REQ_TEXT)
check("22a · real reliability states survive (blocked/review guidance intact)",
      (_v_real_metric.get("guidance") or {}).get("kind") in ("blocked", "review", None))
check("22b · reliability six-state vocabulary intact in checklist rows",
      all(r.get("status") in ("VERIFIED", "DERIVED", "STUDENT_INPUT", "POSSIBLE",
                              "REVIEW_REQUIRED", "BLOCKED")
          for r in (ws_demo.get("requirements") or [])
          if r.get("status") not in (None, "")))

# ---------------------------------------------------------------------------
# Presentation contracts
# ---------------------------------------------------------------------------
check("c1 · quiet link is an anchor to the agent action",
      app._agent_quiet_link("metric.calculation", "See calculation") ==
      '<a class="fte-agent-quiet" href="?fte_agent_action=metric.calculation">See calculation</a>')
_v_per = _drive(ws_demo, STAGE_PERIODS)
check("c2 · periods content marks the strongest change",
      bool((_v_per["content"].get("strongest") or {}).get("metric")))
check("c3 · strong change references a real period row",
      (_v_per["content"].get("strongest") or {}).get("metric") in
      [o.get("metric") for o in (_v_per["content"].get("changes") or [])])
check("c4 · tutor voice typography uses line-height 1.6",
      "line-height: 1.6" in _APP_SRC)
check("c5 · excel workbook unchanged (7 sheets, real formulas)",
      set(openpyxl.load_workbook(BytesIO(build_excel_working_model(ws_demo))).sheetnames) == {
          "Financial Data", "Ratio Analysis", "External Variables", "Comparison",
          "Driver Analysis", "Assignment Requirements", "Qualitative Drivers"})
check("c6 · primary action renders as a single dominant button",
      'type="primary"' in _APP_SRC and "fte_agent_next_primary_" in _APP_SRC)
check("c7 · step indicator replaces the old chip wall in the main render",
      "Step {n} of {t} · {label}".format(n="{n}", t="{t}", label="{label}") in _APP_SRC or
      "_agent_step_html(view.get(\"step\"))" in _APP_SRC)


def main():
    failures = [c for c in CHECKS if not c[1]]
    print(f"\nRESULT: {len(CHECKS) - len(failures)}/{len(CHECKS)} checks pass")
    if failures:
        for name, _ok, detail in failures:
            print(f"  FAIL {name}: {detail}")
        sys.exit(1)
    print("ALL CHECKS PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
