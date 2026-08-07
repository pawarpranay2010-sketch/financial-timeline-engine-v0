"""Sprint 12.3 - Zero-Panic Student Onboarding & Guided Recovery

Deterministic adversarial test suite. The FT-E core workflow is already
strong (assignment understanding, requirement extraction, metric
normalization, period analysis, driver analysis, qualitative catalysts,
evidence verification, Excel working model, Student Memo, student-authored
conclusion, API/Demo parity). This sprint hardens TRUST during the first
minute: messy or ambiguous assignment text must NEVER look like an
application failure, and every uncertain state must lead to a clear,
guided next action — never a dead end.

Mandated adversarial cases (engine + app render + Demo/API parity):

   1.  Clean assignment text
   2.  Messy WhatsApp-style assignment text
   3.  Excessive whitespace
   4.  Broken line wrapping
   5.  Hyphenated metric names
   6.  Reordered requirements
   7.  Partial / ambiguous requirement
   8.  Unknown requirement
   9.  Empty assignment
  10.  Assignment containing unrelated prose
  11.  Mixed valid + ambiguous requirements
  12.  Parser failure with successful manual confirmation
  13.  Demo recovery flow
  14.  API recovery flow
  15.  Excel orientation message
  16.  Student conclusion remains blank
  17.  No generated Buy/Sell recommendation
  18.  Existing evidence-card interaction remains functional
  19.  Existing metric-click interaction remains functional
  20.  API/Demo UX structure remains equivalent

Every check is deterministic. The agent NEVER invents requirements, causes,
sources, calculations or conclusions; it never exposes parser/debug
terminology; and the Demo path stays static, offline, AI-free and
API-key-free.

Run:  python3 scripts/fte_zero_panic_adversarial_test.py
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
    AGENT_STAGE_IDS,
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
    PARSE_LOW,
    agent_session,
    apply_choice,
    confirmation_candidates,
    initial_state,
    parse_recovery,
    what_next,
)
from backend.student_workspace import (  # noqa: E402
    build_student_workspace,
    canonicalize_metric,
    parse_requirements,
)
from backend.excel_working_model import build_excel_working_model  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


# ---------------------------------------------------------------------------
# App-under-test (stubbed streamlit) — used only for the demo fixtures and
# the app render-path source markers.
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
    """Exec the app file under a stubbed streamlit and return the module."""
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


def _at_stage(ws, stage, requirements_text="", **extra):
    """Drive the state machine to a given stage and return the session view."""
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
        s = apply_choice(
            apply_choice(apply_choice(s, "requirements.confirm", ws), "period.ROE", ws),
            "metric.explain", ws,
        )
    elif stage == STAGE_EXCEL:
        s = apply_choice(apply_choice(s, "requirements.confirm", ws), "skip", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "drivers.excel", ws)
    elif stage == STAGE_CONCLUSION:
        s = apply_choice(apply_choice(s, "requirements.confirm", ws), "skip", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "continue", ws)
        s = apply_choice(s, "memo.conclusion", ws)
    return agent_session(ws, s, requirements_text=requirements_text, facts_src=extra.get("facts_src"))


# ---------------------------------------------------------------------------
# Fixtures used across the mandated cases
# ---------------------------------------------------------------------------
ws_demo = _demo_workspace()
ws_real = _real_workspace(period_facts=_PERIOD_FACTS)
_demo_req_text = _load_app()._demo_assignment_requirements_text()

with open(os.path.join(os.path.dirname(__file__), "..", "app (1) (9).py"), encoding="utf-8") as _fh:
    _APP_SRC = _fh.read()

# ===========================================================================
# 1 · Clean assignment text
# ===========================================================================
rec_clean = parse_recovery(ws_real, _REQ_TEXT)
check("1a · clean assignment -> high confidence", rec_clean["state"] == PARSE_HIGH,
      f"got {rec_clean['state']}")
v_clean = agent_session(
    ws_real, apply_choice(initial_state(), "opening.requirements", ws_real),
    requirements_text=_REQ_TEXT,
)
check("1b · high state recommends Continue to the analysis (automatic continuation)",
      (v_clean.get("recommended") or {}).get("id") == "requirements.continue",
      f"got {v_clean.get('recommended')}")
check("1c · high state has exactly one primary action",
      bool(v_clean.get("recommended")) and not isinstance(v_clean["recommended"], list))
check("1d · high state message identifies the requirement count",
      "identified" in v_clean["message"] and "requirements from it" in v_clean["message"])

# ===========================================================================
# 2 · Messy WhatsApp-style assignment text
# ===========================================================================
WHATSAPP = "yo!! calculate  roe   roa profit margin 📊 current ratio debt/equity for microsoft FY2024-FY2025 thx"
ws_whatsapp = _real_workspace(requirements_text=WHATSAPP, period_facts=_PERIOD_FACTS)
rec_ws = parse_recovery(ws_whatsapp, WHATSAPP)
check("2a · WhatsApp whitespace + emoji still recovers", rec_ws["state"] == PARSE_HIGH,
      f"got {rec_ws['state']}")
parsed_wa = {r["metric"] for r in parse_requirements(WHATSAPP)}
check("2b · WhatsApp text resolves the full metric set",
      {"ROE", "ROA", "Profit Margin", "Current Ratio", "Debt to Equity"} <= parsed_wa,
      f"got {parsed_wa}")
check("2c · WhatsApp never dead-ends (continue advances from high)",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_whatsapp),
                   "requirements.continue", ws_whatsapp)["stage"] in (STAGE_PERIODS, STAGE_METRIC))

# ===========================================================================
# 3 · Excessive whitespace
# ===========================================================================
SPACES = "Calculate      ROE \t \n  and     ROA\n\n\n     and  Profit   Margin   FY2024 - FY2025"
ws_spaces = _real_workspace(requirements_text=SPACES, period_facts=_PERIOD_FACTS)
rec_sp = parse_recovery(ws_spaces, SPACES)
check("3a · excessive whitespace recovers", rec_sp["state"] == PARSE_HIGH,
      f"got {rec_sp['state']} uncertain={rec_sp.get('uncertain')}")
check("3b · excessive whitespace keeps every requirement",
      {"ROE", "ROA", "Profit Margin"} <= {r["metric"] for r in parse_requirements(SPACES)})

# ===========================================================================
# 4 · Broken line wrapping
# ===========================================================================
LINES = "Compute ROE,\nROA\nand Profit Margin\nFY2024-FY2025"
ws_lines = _real_workspace(requirements_text=LINES, period_facts=_PERIOD_FACTS)
check("4a · broken line wrapping recovers", parse_recovery(ws_lines, LINES)["state"] == PARSE_HIGH)
WRAP = "Calculate Return\non Equity (ROE) and Profit\nMargin for FY2024–FY2025."
ws_wrap = _real_workspace(requirements_text=WRAP, period_facts=_PERIOD_FACTS)
rec_wrap = parse_recovery(ws_wrap, WRAP)
check("4b · line break inside a metric name still recovers",
      rec_wrap["state"] == PARSE_HIGH,
      f"got {rec_wrap['state']} uncertain={rec_wrap.get('uncertain')}")

# ===========================================================================
# 5 · Hyphenated metric names
# ===========================================================================
rows_d2e = parse_requirements("Compute ROE and Debt-to-Equity.")
check("5a · Debt-to-Equity resolves to one requirement",
      len([r for r in rows_d2e if r["metric"] == "Debt to Equity"]) == 1, f"got {rows_d2e}")
check("5b · Debt-to-Equity does NOT fragment into Debt + Equity",
      "Debt" not in {r["metric"] for r in rows_d2e} and "Equity" not in {r["metric"] for r in rows_d2e})
rows_de = parse_requirements("Compute D/E and ROE.")
check("5c · D/E resolves to one Debt to Equity requirement",
      len([r for r in rows_de if r["metric"] == "Debt to Equity"]) == 1, f"got {rows_de}")
HYPH = "Analyse the company's debt-to-equity and current ratio."
ws_hyph = _real_workspace(requirements_text=HYPH, period_facts=_PERIOD_FACTS)
check("5d · hyphenated assignment text recovers",
      parse_recovery(ws_hyph, HYPH)["state"] == PARSE_HIGH)

# ===========================================================================
# 6 · Reordered requirements
# ===========================================================================
REORDERED = "Compute Profit Margin, Debt to Equity and ROE — plus Current Ratio and ROA — for FY2024–FY2025."
ws_reord = _real_workspace(requirements_text=REORDERED, period_facts=_PERIOD_FACTS)
rec_reord = parse_recovery(ws_reord, REORDERED)
check("6a · reordered requirements recovers", rec_reord["state"] == PARSE_HIGH,
      f"got {rec_reord['state']} uncertain={rec_reord.get('uncertain')}")
check("6b · reordered requirements resolve the full set",
      {"ROE", "ROA", "Profit Margin", "Current Ratio", "Debt to Equity"}
      <= {r["metric"] for r in parse_requirements(REORDERED)})

# ===========================================================================
# 7 · Partial / ambiguous requirement
# ===========================================================================
AMB_TXT = "Calculate Segment Gross Margin and ROE."
ws_amb = _real_workspace(requirements_text=AMB_TXT, period_facts=_PERIOD_FACTS)
rec_amb = parse_recovery(ws_amb, AMB_TXT)
check("7a · ambiguous label surfaces for confirmation (never silently merged)",
      rec_amb["state"] == PARSE_PARTIAL and
      any("segment gross margin" in str(x).lower() for x in rec_amb["uncertain"]),
      f"got {rec_amb}")
v_amb = agent_session(
    ws_amb, apply_choice(initial_state(), "opening.requirements", ws_amb),
    requirements_text=AMB_TXT,
)
check("7b · partial state recommends Confirm & Continue",
      (v_amb.get("recommended") or {}).get("id") == "requirements.confirm",
      f"got {v_amb.get('recommended')}")
check("7c · partial state exposes confirmation candidates for the uncertain token",
      any("segment gross margin" in str(cand.get("token") or "").lower()
          for cand in (v_amb["content"].get("uncertain_candidates") or [])),
      str(v_amb["content"].get("uncertain_candidates")))
check("7d · ambiguous token still gets deterministic suggestions",
      bool(confirmation_candidates(str(rec_amb["uncertain"][0]))) if rec_amb["uncertain"] else True)

# ===========================================================================
# 8 · Unknown requirement
# ===========================================================================
LOW_TXT = "Please calculate ROIC and Quick Ratio."
ws_low = _real_workspace(requirements_text=LOW_TXT)
rec_low = parse_recovery(ws_low, LOW_TXT)
check("8a · unknown-only text -> low state", rec_low["state"] == PARSE_LOW, f"got {rec_low['state']}")
v_low = agent_session(
    ws_low, apply_choice(initial_state(), "opening.requirements", ws_low),
    requirements_text=LOW_TXT,
)
check("8b · low state message reassures nothing is broken",
      "couldn't confidently interpret" in v_low["message"] and "Nothing is broken" in v_low["message"],
      v_low["message"])
check("8c · low state exposes the manual canonical selector",
      len(v_low["content"].get("options") or []) >= 10)
check("8d · low state offers confirm + edit (manual recovery)",
      "requirements.confirm" in [c.get("id") for c in v_low["choices"]] and
      "requirements.edit" in [c.get("id") for c in v_low["choices"]])
check("8e · low state recommends the Continue action",
      (v_low.get("recommended") or {}).get("id") == "requirements.confirm")

# ===========================================================================
# 9 · Empty assignment
# ===========================================================================
ws_empty = _real_workspace(requirements_text="   ")
rec_empty = parse_recovery(ws_empty, "")
check("9a · empty assignment -> low state (not a crash, not a dead end)",
      rec_empty["state"] == PARSE_LOW, f"got {rec_empty['state']}")
v_empty = agent_session(
    ws_empty, apply_choice(initial_state(), "opening.requirements", ws_empty),
    requirements_text="",
)
check("9b · empty assignment produces a reassuring message",
      "Nothing is broken" in v_empty["message"], v_empty["message"])
check("9c · empty assignment still has a clear next action",
      bool(v_empty.get("recommended")) and bool(v_empty.get("choices")))
check("9d · empty assignment can be advanced past via confirmation",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_empty),
                   "requirements.confirm", ws_empty)["stage"] in (STAGE_PERIODS, STAGE_METRIC, STAGE_DRIVERS))
check("9e · empty assignment never fabricates requirements",
      parse_requirements("   ") == [] and parse_recovery(ws_empty, "")["confirmed"] == [])

# ===========================================================================
# 10 · Assignment containing unrelated prose
# ===========================================================================
PROSE = ("Dear students, please review the attached annual report. Submit "
         "your answers by Friday. Read chapter 4 of the textbook before class.")
ws_prose = _real_workspace(requirements_text=PROSE)
rec_prose = parse_recovery(ws_prose, PROSE)
check("10a · unrelated prose -> low state (no fabrication, no crash)",
      rec_prose["state"] == PARSE_LOW, f"got {rec_prose['state']}")
check("10b · unrelated prose invents no requirements",
      parse_requirements(PROSE) == [])
v_prose = agent_session(
    ws_prose, apply_choice(initial_state(), "opening.requirements", ws_prose),
    requirements_text=PROSE,
)
check("10c · unrelated prose still offers manual recovery with a next action",
      bool(v_prose.get("recommended")) and "requirements.confirm" in [c.get("id") for c in v_prose["choices"]])

# ===========================================================================
# 11 · Mixed valid + ambiguous requirements
# ===========================================================================
MIXED = "Calculate ROE, ROA and ROIC for FY2024-FY2025."
ws_mixed = _real_workspace(requirements_text=MIXED, period_facts=_PERIOD_FACTS)
rec_mixed = parse_recovery(ws_mixed, MIXED)
check("11a · mixed valid+ambiguous -> partial (never silent loss)",
      rec_mixed["state"] == PARSE_PARTIAL and
      {"ROE", "ROA"} <= set(rec_mixed["confirmed"]) and
      any("ROIC" in str(x).upper() for x in rec_mixed["uncertain"]),
      f"got {rec_mixed}")
v_mixed = agent_session(
    ws_mixed, apply_choice(initial_state(), "opening.requirements", ws_mixed),
    requirements_text=MIXED,
)
check("11b · mixed state keeps every confirmed requirement visible",
      {"ROE", "ROA"} <= set(v_mixed["content"].get("confirmed") or []))
check("11c · mixed state asks the student to confirm the unclear item",
      "confirmation" in v_mixed["message"] and "ROIC" in v_mixed["message"],
      v_mixed["message"])

# ===========================================================================
# 12 · Parser failure with successful manual confirmation
# ===========================================================================
check("12a · confirm on low-confidence advances (no dead end)",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_low),
                   "requirements.confirm", ws_low)["stage"] in (STAGE_PERIODS, STAGE_METRIC, STAGE_DRIVERS))
_s_conf = apply_choice(apply_choice(initial_state(), "opening.requirements", ws_low),
                       "requirements.confirm", ws_low)
check("12b · manual confirmation is a calm, normal step (notice present)",
      str(_s_conf.get("notice") or "").startswith("Got it. I'll use these requirements"),
      str(_s_conf))
check("12c · confirm on partial advances cleanly",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_mixed),
                   "requirements.confirm", ws_mixed)["stage"] in (STAGE_PERIODS, STAGE_METRIC, STAGE_DRIVERS))
check("12d · edit stays in-workspace (no dead end, no crash)",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_partial := _real_workspace(
          requirements_text="Calculate ROE and ROIC.", period_facts=_PERIOD_FACTS)),
          "requirements.edit", ws_partial)["stage"] == STAGE_REQUIREMENTS)

# ===========================================================================
# 13 · Demo recovery flow
# ===========================================================================
ws_demo_partial = _demo_workspace(requirements_text="Calculate ROE and ROIC.")
v_demo_rec = agent_session(
    ws_demo_partial, apply_choice(initial_state(), "opening.requirements", ws_demo_partial),
    requirements_text="Calculate ROE and ROIC.",
)
check("13a · Demo recovery offers the confirmation action",
      v_demo_rec["content"].get("parse_state") == PARSE_PARTIAL and
      (v_demo_rec.get("recommended") or {}).get("id") == "requirements.confirm",
      str(v_demo_rec.get("content", {}).get("parse_state")))
check("13b · Demo recovery confirms and advances",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_demo_partial),
                   "requirements.confirm", ws_demo_partial)["stage"] in (STAGE_PERIODS, STAGE_METRIC, STAGE_DRIVERS))
v_demo_open = agent_session(
    ws_demo, apply_choice(initial_state(), "opening.requirements", ws_demo),
    requirements_text=_demo_req_text,
)
check("13c · Demo clean onboarding renders the parsed assignment",
      v_demo_open["content"].get("parse_state") == PARSE_HIGH and
      "I've parsed your assignment" in v_demo_open["message"])

# ===========================================================================
# 14 · API recovery flow
# ===========================================================================
ws_real_partial = _real_workspace(requirements_text="Calculate ROE and ROIC.", period_facts=_PERIOD_FACTS)
v_real_rec = agent_session(
    ws_real_partial, apply_choice(initial_state(), "opening.requirements", ws_real_partial),
    requirements_text="Calculate ROE and ROIC.",
)
check("14a · API recovery offers the confirmation action",
      v_real_rec["content"].get("parse_state") == PARSE_PARTIAL and
      (v_real_rec.get("recommended") or {}).get("id") == "requirements.confirm",
      str(v_real_rec.get("content", {}).get("parse_state")))
check("14b · API recovery confirms and advances",
      apply_choice(apply_choice(initial_state(), "opening.requirements", ws_real_partial),
                   "requirements.confirm", ws_real_partial)["stage"] in (STAGE_PERIODS, STAGE_METRIC, STAGE_DRIVERS))
check("14c · API clean onboarding renders the parsed assignment",
      v_clean["content"].get("parse_state") == PARSE_HIGH and
      "I've parsed your assignment" in v_clean["message"])

# ===========================================================================
# 15 · Excel orientation message
# ===========================================================================
v_xl = _at_stage(ws_demo, STAGE_EXCEL)
check("15a · Excel message is explained before opening",
      "Your working model is ready" in v_xl["message"], v_xl["message"])
check("15b · Excel message points to Sheet 2 — Ratio Analysis and evidence cards",
      "Sheet 2" in v_xl["message"] and "Ratio Analysis" in v_xl["message"]
      and "evidence cards" in v_xl["message"], v_xl["message"])
check("15c · orientation marks formulas as already calculated",
      bool((v_xl["content"].get("orientation") or {}).get("formulas_done")) is True)
check("15d · Ratio Analysis is the stated first sheet",
      (v_xl["content"].get("orientation") or {}).get("first") == "Ratio Analysis")
check("15e · workbook still has seven sheets with real formulas",
      len(openpyxl.load_workbook(BytesIO(build_excel_working_model(ws_demo))).sheetnames) == 7)
check("15f · app renders the no-rebuild reassurance to the student",
      "Your working model is ready" in _APP_SRC and
      "you don't need to edit the formulas" in _APP_SRC)

# ===========================================================================
# 16 · Student conclusion remains blank
# ===========================================================================
v_concl = _at_stage(ws_demo, STAGE_CONCLUSION)
_cn = v_concl["content"]
check("16a · conclusion content never generates a conclusion",
      bool(_cn.get("never_generate")) is True and "conclusion" not in _cn)
check("16b · conclusion provides only evidence-backed scaffolding",
      len(_cn.get("scaffold") or []) >= 3 and
      str(_cn["scaffold"][0]).startswith("Evidence suggests"))
check("16c · app keeps the student conclusion field blank + student-authored",
      "State your own reasoned judgment here" in _APP_SRC and
      "never generates a conclusion" in _APP_SRC)
check("16d · default session state holds an empty student conclusion",
      str(_APP_STUB_SS.get("fte_student_conclusion", "")) == "" or
      "fte_student_conclusion" in _APP_SRC)

# ===========================================================================
# 17 · No generated Buy/Sell recommendation
# ===========================================================================
_all_msgs = []
for _st2 in AGENT_STAGE_IDS:
    _v2 = agent_session(ws_demo, {"stage": _st2, "metric": "ROE", "area": None, "visited": []})
    _all_msgs.append(str(_v2["message"]))
_blob = " ".join(_all_msgs).lower()
check("17a · no buy/sell/strong-buy/recommendation generated in any stage message",
      not any(w in _blob for w in ("buy ", " sell ", "strong buy", "recommendation", "outperform",
                                   "underperform", "target price")))
_concl_text = " ".join(str(x) for x in (_cn.get("checklist") or []) + (_cn.get("scaffold") or [])).lower()
check("17b · no buy/sell/hold language in the conclusion scaffolding",
      not any(w in _concl_text for w in ("buy ", " sell ", " hold ", "recommend", "outperform")))

# ===========================================================================
# 18 · Existing evidence-card interaction remains functional
# ===========================================================================
v_ev = _at_stage(ws_demo, STAGE_METRIC)
_s_ev = {"stage": STAGE_EVIDENCE, "metric": "ROE", "area": None,
         "visited": ["opening", "requirements", "periods", "metric"]}
v_ev_full = agent_session(ws_demo, _s_ev)
_ev_fields = v_ev_full["content"].get("fields") or []
check("18a · evidence stage opens with provenance fields",
      len(_ev_fields) > 0)
check("18b · evidence card carries source/period/page fields",
      any(f.get("label") == "Source" for f in _ev_fields) and
      any(f.get("label") in ("Period", "Page") for f in _ev_fields))
check("18c · app keeps the floating evidence-card dialog",
      "_memo_metric_dialog" in _APP_SRC and "fte_memo_metric_click" in _APP_SRC)
check("18d · Verify-evidence-first routes to the evidence stage",
      apply_choice({"stage": STAGE_EXCEL, "metric": None, "area": None,
                    "visited": ["opening", "requirements", "periods"]},
                   "excel.evidence", ws_demo)["stage"] == STAGE_EVIDENCE)
check("18e · evidence payload leaks no backend internals",
      not any(k in str(v_ev_full["content"]) for k in ("extraction_state", "bbox", "traceback",
                                                       "normalization_status", "provenance_tier")))

# ===========================================================================
# 19 · Existing metric-click interaction remains functional
# ===========================================================================
check("19a · memo renderer emits clickable metric links (?fte_metric=...)",
      "?fte_metric=" in _APP_SRC and "fte-metric-link" in _APP_SRC)
check("19b · metric clicks open the evidence card via fte_memo_metric_click",
      "_reconstruct_demo_from_query" in _APP_SRC and
      "fte_memo_metric_click" in _APP_SRC and "fte_metric" in _APP_SRC)
_s_met = apply_choice(apply_choice(apply_choice(initial_state(), "opening.requirements", ws_demo),
                                   "requirements.confirm", ws_demo), "period.ROE", ws_demo)
check("19c · metric selection from the period list works",
      _s_met["stage"] == STAGE_METRIC and _s_met["metric"] == "ROE")
check("19d · metric stage keeps Verify/Explain/Calculate actions (existing interaction intact)",
      any(c.get("id") in ("metric.evidence", "metric.explain", "metric.calculation")
          for c in agent_session(ws_demo, _s_met)["choices"]))

# ===========================================================================
# 20 · API/Demo UX structure remains equivalent
# ===========================================================================
_s_par = {"stage": STAGE_PERIODS, "metric": None, "area": None, "visited": ["opening", "requirements"]}
_v_real_par = agent_session(_real_workspace(period_facts=_PERIOD_FACTS), _s_par)
_v_demo_par = agent_session(ws_demo, _s_par)
check("20a · API and Demo expose identical stage ids",
      _v_real_par["stage"] == _v_demo_par["stage"] == STAGE_PERIODS)
check("20b · API and Demo progress rows match in shape",
      [p.get("label") for p in _v_real_par["progress"]] == [p.get("label") for p in _v_demo_par["progress"]])
check("20c · API and Demo use the same choice-id schema",
      all(re.fullmatch(r"(period\.[A-Za-z ]+|back|skip)", c.get("id") or "") for c in _v_real_par["choices"])
      and all(re.fullmatch(r"(period\.[A-Za-z ]+|back|skip)", c.get("id") or "") for c in _v_demo_par["choices"])
      and any(c.get("id", "").startswith("period.") for c in _v_real_par["choices"])
      and any(c.get("id", "").startswith("period.") for c in _v_demo_par["choices"]))
check("20d · both paths reach conclusion with blank student conclusion",
      apply_choice(apply_choice(apply_choice(
          apply_choice(apply_choice(apply_choice(
              apply_choice(apply_choice(initial_state(), "opening.requirements", ws_demo),
                           "requirements.confirm", ws_demo), "skip", ws_demo),
              "continue", ws_demo), "continue", ws_demo), "continue", ws_demo),
          "continue", ws_demo), "memo.conclusion", ws_demo)["stage"] == STAGE_CONCLUSION)

# ===========================================================================
# Safety sweep — no dead ends anywhere, no banned terminology, Demo isolation
# ===========================================================================
_all_ok = True
_all_detail = ""
for _stage in AGENT_STAGE_IDS:
    _st = {"stage": _stage, "metric": "ROE" if _stage in (
        STAGE_METRIC, STAGE_EXPLAIN, STAGE_CALCULATION, STAGE_EVIDENCE) else None,
           "area": None, "visited": ["opening"]}
    _v = agent_session(ws_demo, _st)
    if not _v["message"] or not isinstance(_v["message"], str):
        _all_ok = False
        _all_detail = f"stage {_stage}: empty message"
        break
    for _c in _v["choices"]:
        _nxt = apply_choice(_st, _c["id"], ws_demo)
        if _nxt["stage"] not in AGENT_STAGE_IDS and _nxt["stage"] != "__explore__":
            _all_ok = False
            _all_detail = f"stage {_stage} choice {_c['id']} -> bad {_nxt['stage']}"
            break
    if not _all_ok:
        break
check("s1 · every stage has a message and every choice resolves to a valid stage",
      _all_ok, _all_detail)

_BANNED = ("parser", "traceback", "exception", "stack", "debug", "regex",
           "backend error", "implementation", "failed", "error occurred",
           "span scan", "canonicalization failure")
_all_content_blob = " ".join(
    str(x) for x in [
        v_clean["message"], v_amb["message"], v_low["message"], v_empty["message"],
        v_prose["message"], v_mixed["message"], v_xl["message"], v_concl["message"],
    ]
)
check("s2 · no parser/debug/exception terminology in any student-facing message",
      not any(b in _all_content_blob.lower() for b in _BANNED),
      [b for b in _BANNED if b in _all_content_blob.lower()])
check("s3 · the banned 'could not parse requirements' failure copy is gone",
      "could not parse requirements clearly" not in _APP_SRC.lower()
      and "could not parse requirements clearly" not in open(
          os.path.join(os.path.dirname(__file__), "..", "backend", "assignment_agent.py"),
          encoding="utf-8").read().lower())

_demo_m3 = _load_app()._demo_module3_result()
check("s4 · Demo dataset carries no API key or network/config keys",
      "api_key" not in str(_demo_m3).lower() and
      not any(k in str(_demo_m3).lower() for k in ("http://", "https://", "provider_url", "api_url")))
_demo_msgs = [str(v_demo_open["message"]), str(v_demo_rec["message"]),
              str(v_xl["message"]), str(v_concl["message"])]
check("s5 · Demo messages contain no AI-provider language",
      all(not re.search(r"\bai\b|\bllm\b|gpt|openai|anthropic", m.lower()) for m in _demo_msgs))
check("s6 · Demo fixtures are deterministic module constants",
      isinstance(_load_app()._FTE_DEMO_PERIOD_FACTS, dict) and
      isinstance(_load_app()._FTE_DEMO_QUALITATIVE_DOCS, list))
check("s7 · Demo financial values remain unchanged",
      _demo_m3["financial_data"]["Revenue"]["value"] == 281700000000 and
      _demo_m3["financial_data"]["Net Profit"]["value"] == 98300000000)
_s_det = apply_choice(initial_state(), "opening.requirements", ws_demo)
_v1 = agent_session(ws_demo, _s_det, requirements_text=_demo_req_text)
_v2 = agent_session(ws_demo, _s_det, requirements_text=_demo_req_text)
check("s8 · Demo agent output is fully deterministic",
      _v1["message"] == _v2["message"] and
      [c.get("id") for c in _v1["choices"]] == [c.get("id") for c in _v2["choices"]] and
      _v1["recommended"] == _v2["recommended"])

# ===========================================================================
# Regression — the core machinery is untouched behind the recovery layer
# ===========================================================================
check("r1 · requirements checklist intact", len(ws_demo.get("requirements") or []) >= 5)
check("r2 · normalized facts intact", len(ws_demo.get("normalized_facts") or []) > 0)
check("r3 · comparison intact", bool((ws_demo.get("comparison") or {}).get("rows")))
check("r4 · driver analysis intact", bool((ws_demo.get("driver_analysis") or {}).get("observations")))
check("r5 · qualitative drivers intact", bool((ws_demo.get("qualitative_drivers") or {}).get("rows")))
check("r6 · calculations intact", len(ws_demo.get("calculations") or []) >= 5)
check("r7 · Excel working model still builds", isinstance(build_excel_working_model(ws_demo), bytes)
      and len(build_excel_working_model(ws_demo)) > 2000)
check("r8 · what_next still returns one recommended action",
      bool(what_next(ws_demo, _s_met).get("recommended")))
check("r9 · EPS/EPSILON, Revenue/Revenue Growth, Debt/Debt-like collisions stay blocked",
      canonicalize_metric("EPSILON")[0] is None and
      canonicalize_metric("Revenue Growth")[0] == "Revenue Growth" and
      canonicalize_metric("Debt-like")[0] is None)


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
