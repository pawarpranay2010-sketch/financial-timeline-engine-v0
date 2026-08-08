#!/usr/bin/env python3
"""
Sprint 13 - Student Agent Intelligence: Guided Recovery & Adaptive
Assignment Flow (deterministic test suite).

Coverage (per the sprint brief):
  * Ingestion      - clean, messy WhatsApp text, punctuation, hyphenated
                     Debt-to-Equity, reordered requirements, ambiguous item,
                     missing requirement, unparseable assignment.
  * Agent recovery - parser failure -> confirmation state; ambiguous -> confirm;
                     missing data -> verification action; blocked / review
                     metrics -> safe tutor explanations; successful calc ->
                     next action; changed metric -> investigation; evidence ->
                     evidence action; Excel ready -> Excel action; complete ->
                     conclusion action.
  * Progressive disclosure - everything exists internally, only the relevant
                     subset is surfaced, no 20-button cockpit, actions change
                     after progress, secondary actions remain available.
  * Demo/API parity - identical agent state machine over the same fixture,
                     deterministic output, fixtures never mutated, no AI /
                     network / API key anywhere in the agent layer.
  * Conclusion     - stays blank/student-authored, no Buy/Sell wording.

Runs offline: no Streamlit, no network, no AI. Pure backend imports.
"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backend.assignment_agent as aa
from backend.student_workspace import (
    build_student_workspace,
    parse_requirements,
)

FAILS: list = []


def check(name: str, cond: bool, info: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  {status}  {name}  {info}" + ("" if cond else "  <-- FAILED"))
    if not cond:
        FAILS.append(name)


def _fact(value, source, evidence="", page="", scale=None,
          extraction_state=None, reason="", formula=None, inputs=None):
    f = {
        "value": value,
        "source": source,
        "reporting_period": "FY2025",
        "unit": "USD",
        "evidence": evidence,
        "page": page,
    }
    if scale:
        f["scale"] = scale
    if extraction_state:
        f["extraction_state"] = extraction_state
        f["extraction_state_reason"] = reason
    if formula:
        f["formula"] = formula
    if inputs:
        f["inputs"] = inputs
    return f


def _base_module3(review_revenue=False):
    """Synthetic but internally consistent module3-shaped fixture. Values
    mirror the Demo sample (deterministic, isolated, no I/O)."""
    fd = {
        "Revenue": _fact(281700000000, "Fixture · Income statement",
                         evidence="Fixture · Consolidated income statement, p. 26",
                         page="26", scale="B",
                         extraction_state="review_required" if review_revenue else None,
                         reason="Table flagged as malformed/ambiguous" if review_revenue else ""),
        "Net Profit": _fact(98300000000, "Fixture · Income statement",
                            evidence="Fixture · Consolidated income statement, p. 26",
                            page="26", scale="B"),
        "Operating Profit": _fact(125500000000, "Fixture · Income statement",
                                  evidence="Fixture · Consolidated income statement, p. 26",
                                  page="26", scale="B"),
        "Debt": _fact(96600000000, "Fixture · Balance sheet",
                      evidence="Fixture · Consolidated balance sheet, p. 27",
                      page="27", scale="B"),
        "Assets": _fact(512200000000, "Fixture · Balance sheet",
                        evidence="Fixture · Consolidated balance sheet, p. 27",
                        page="27", scale="B"),
        "Liabilities": _fact(243700000000, "Fixture · Balance sheet",
                             evidence="Fixture · Consolidated balance sheet, p. 27",
                             page="27", scale="B"),
        "Equity": _fact(268500000000, "Fixture · Balance sheet",
                        evidence="Fixture · Consolidated balance sheet, p. 27",
                        page="27", scale="B"),
        "Current Assets": _fact(140000000000, "Fixture · Balance sheet",
                                evidence="Fixture · Consolidated balance sheet, p. 27",
                                page="27", scale="B"),
        "Current Liabilities": _fact(100000000000, "Fixture · Balance sheet",
                                     evidence="Fixture · Consolidated balance sheet, p. 27",
                                     page="27", scale="B"),
        "Cash Flow": _fact(127800000000, "Fixture · Cash flow statement",
                           evidence="Fixture · Consolidated cash flow statement, p. 28",
                           page="28", scale="B"),
    }
    rt = {
        "ROE": _fact(0.366, "Calculated",
                     evidence="Net income ÷ shareholders' equity (fixture FY2025)",
                     formula="Net income ÷ shareholders' equity", inputs=["Net Profit", "Equity"]),
        "ROA": _fact(0.192, "Calculated",
                     evidence="Net income ÷ total assets (fixture FY2025)",
                     formula="Net income ÷ total assets", inputs=["Net Profit", "Assets"]),
        "Profit Margin": _fact(0.349, "Calculated",
                               evidence="Net income ÷ revenue (fixture FY2025)",
                               formula="Net income ÷ revenue", inputs=["Net Profit", "Revenue"]),
        "Debt to Equity": _fact(0.36, "Calculated",
                                evidence="Total debt ÷ shareholders' equity (fixture FY2025)",
                                formula="Total debt ÷ shareholders' equity", inputs=["Debt", "Equity"]),
        "Current Ratio": _fact(1.40, "Calculated",
                               evidence="Current assets ÷ current liabilities (fixture FY2025)",
                               formula="Current assets ÷ current liabilities",
                               inputs=["Current Assets", "Current Liabilities"]),
    }
    return {
        "financial_data": fd,
        "ratios": rt,
        "missing_data": {"financial_data": ["Segment Gross Margin"], "ratios": []},
        "demo": True,
    }


def _peer_facts():
    return {
        "Revenue": _fact(198400000000, "Peer fixture",
                         evidence="Peer fixture · Income statement, p. 12", page="12", scale="B"),
        "Net Profit": _fact(41200000000, "Peer fixture",
                            evidence="Peer fixture · Income statement, p. 12", page="12", scale="B"),
        "Equity": _fact(104000000000, "Peer fixture",
                        evidence="Peer fixture · Balance sheet, p. 13", page="13", scale="B"),
        "Assets": _fact(236000000000, "Peer fixture",
                        evidence="Peer fixture · Balance sheet, p. 13", page="13", scale="B"),
        "Debt": _fact(39000000000, "Peer fixture",
                      evidence="Peer fixture · Balance sheet, p. 13", page="13", scale="B"),
        "ROE": _fact(0.396, "Calculated", evidence="Peer fixture"),
        "ROA": _fact(0.175, "Calculated", evidence="Peer fixture"),
        "Profit Margin": _fact(0.208, "Calculated", evidence="Peer fixture"),
        "Debt to Equity": _fact(0.375, "Calculated", evidence="Peer fixture"),
        "Current Ratio": _fact(1.22, "Calculated", evidence="Peer fixture"),
    }


PERIOD_FACTS = {
    "ROE": {"FY2024": "30", "FY2025": "36.6"},
    "Net Profit": {"FY2024": "78000000000", "FY2025": "98300000000"},
    "Equity": {"FY2024": "260000000000", "FY2025": "268500000000"},
}


def _build(req_text, module3=None, period=None, peer=None, docs=None, missing=None):
    m3 = module3 if module3 is not None else _base_module3()
    reqs = parse_requirements(req_text)
    return build_student_workspace(
        m3,
        assignment_type="Financial Ratio Analysis",
        requirements_text=req_text,
        external_variables=[],
        company_a="Fixture Co.",
        peer_company="PeerCo Inc." if peer else None,
        peer_facts=_peer_facts() if peer else None,
        period_facts=period or {},
        calc_metrics=[r["metric"] for r in reqs],
        missing=missing,
        qualitative_documents=docs or [],
    )


def _state(**kw):
    s = {"stage": aa.STAGE_OPENING, "metric": None, "area": None, "visited": []}
    s.update(kw)
    return s


CLEAN_TEXT = "Analyze Fixture Co FY2024-FY2025 and calculate ROE, ROA, Profit Margin, Current Ratio and Debt/Equity."
MESSY_TEXT = "pls calclte ROE n profit margin nd current ratio for FY2024-FY2025 thx also debt/equity ratio ok?"
PUNCT_TEXT = "ROE, ROA; Profit Margin\u2014Current Ratio / Debt-to-Equity"
REORDERED_TEXT = "Current Ratio, Debt to Equity, ROE, Profit Margin, ROA"
AMBIG_TEXT = "Calculate ROE and quick ratio for FY2024-FY2025."
GIBBERISH = "xyz abc 123 !!! %&"

print("== Sprint 13 · Agent Workspace ==")

# ---------------------------------------------------------------------------
# 1. Ingestion
# ---------------------------------------------------------------------------
print("\n### I. Ingestion")

# I1 clean assignment -> 5 confirmed requirements, high confidence.
ws = _build(CLEAN_TEXT)
rec = aa.parse_recovery(ws, CLEAN_TEXT)
check("I1a · clean assignment parses 5 requirements",
      len(rec["confirmed"]) == 5, str(rec["confirmed"]))
check("I1b · clean assignment is high confidence", rec["state"] == "high", rec["state"])

# I2 messy WhatsApp-style text never crashes and still finds the metrics.
ws = _build(MESSY_TEXT)
rec = aa.parse_recovery(ws, MESSY_TEXT)
need = {"ROE", "Profit Margin", "Current Ratio", "Debt to Equity"}
check("I2a · messy WhatsApp text parses without crash", bool(rec["confirmed"]),
      str(rec["confirmed"]))
check("I2b · messy text recovers the required metrics",
      need.issubset(set(rec["confirmed"])), str(rec["confirmed"]))

# I3 punctuation/spacing variations.
ws = _build(PUNCT_TEXT)
rec = aa.parse_recovery(ws, PUNCT_TEXT)
check("I3 · punctuation variations parse all metrics",
      set(rec["confirmed"]) == {"ROE", "ROA", "Profit Margin", "Current Ratio", "Debt to Equity"},
      str(rec["confirmed"]))

# I4 hyphenated Debt-to-Equity resolves to the canonical requirement.
ws = _build("Just calculate Debt-to-Equity.")
rec = aa.parse_recovery(ws, "Just calculate Debt-to-Equity.")
check("I4 · hyphenated Debt-to-Equity canonicalizes",
      "Debt to Equity" in rec["confirmed"] and len(rec["confirmed"]) == 1,
      str(rec["confirmed"]))

# I5 reordered requirements keep the assignment order.
ws = _build(REORDERED_TEXT)
rec = aa.parse_recovery(ws, REORDERED_TEXT)
check("I5 · reordered requirements keep text order",
      rec["confirmed"] == ["Current Ratio", "Debt to Equity", "ROE", "Profit Margin", "ROA"],
      str(rec["confirmed"]))

# I6 ambiguous requirement -> partial confirmation state (never a crash).
ws = _build(AMBIG_TEXT)
rec = aa.parse_recovery(ws, AMBIG_TEXT)
check("I6a · ambiguous item drives partial state", rec["state"] == "partial", rec["state"])
check("I6b · ambiguous token surfaced for confirmation",
      any("quick" in str(t).lower() for t in rec["uncertain"]), str(rec["uncertain"]))

# I7 missing requirement -> blocked checklist row, high parse (honest).
ws = _build("Calculate Operating Cash Flow and ROE.")
rec = aa.parse_recovery(ws, "Calculate Operating Cash Flow and ROE.")
rows = {str(r["requirement"]): r for r in ws["requirements"]}
check("I7 · missing-data requirement is BLOCKED, never guessed",
      rows.get("Operating Cash Flow", {}).get("status") == "BLOCKED",
      str(rows.get("Operating Cash Flow", {}).get("status")))

# I8 completely unparseable -> low-confidence guided recovery.
ws = _build(GIBBERISH)
rec = aa.parse_recovery(ws, GIBBERISH)
check("I8a · gibberish assignment classifies as low", rec["state"] == "low", rec["state"])
view = aa.agent_session(ws, _state(stage=aa.STAGE_REQUIREMENTS), requirements_text=GIBBERISH)
check("I8b · low state offers a manual selector", bool(view["content"].get("options")))
check("I8c · low state reassures instead of failing",
      "Nothing is broken" in (view["message"] or ""), view["message"][:90])

# ---------------------------------------------------------------------------
# 2. Agent recovery
# ---------------------------------------------------------------------------
print("\n### R. Agent recovery")

# R1 parser failure -> confirmation state with a clear primary action.
v = aa.agent_session(ws, _state(stage=aa.STAGE_REQUIREMENTS), requirements_text=GIBBERISH)
check("R1a · parser failure always has a primary action",
      bool(v["recommended"] and v["recommended"].get("id")), str(v["recommended"]))
check("R1b · parser failure exposes a recovery secondary",
      any(a.get("id") == "requirements.edit" for a in v["alternatives"]),
      str([a.get("id") for a in v["alternatives"]]))

# R2 ambiguous requirement -> yes/no confirmation choices.
ws = _build(AMBIG_TEXT)
v = aa.agent_session(ws, _state(stage=aa.STAGE_REQUIREMENTS), requirements_text=AMBIG_TEXT)
alts_ids = [a.get("id") for a in v["alternatives"]]
check("R2a · ambiguous state message asks the student to decide",
      "Should I include it" in (v["message"] or ""), v["message"][:110])
check("R2b · 'Yes, include it' offered", "requirements.include.0" in alts_ids, str(alts_ids))
check("R2c · 'No, continue without it' offered", "requirements.exclude.0" in alts_ids, str(alts_ids))
check("R2d · Edit requirements offered", "requirements.edit" in alts_ids, str(alts_ids))

# R2e include -> calm notice + advance.
st = aa.apply_choice(_state(stage=aa.STAGE_REQUIREMENTS), "requirements.include.0", ws, AMBIG_TEXT)
# No period facts in this fixture: the confirm transition lands on the
# metric stage, which agent_session guards back to periods (fail-closed).
check("R2e · include advances calmly",
      st.get("stage") in ("periods", "metric") and "Got it." in (st.get("notice") or ""),
      str(st.get("stage")) + " | " + str(st.get("notice")))
# R2f exclude -> calm notice + advance, decision recorded.
st = aa.apply_choice(_state(stage=aa.STAGE_REQUIREMENTS), "requirements.exclude.0", ws, AMBIG_TEXT)
check("R2f · exclude advances calmly with recorded decision",
      st.get("stage") in ("periods", "metric") and "without" in (st.get("notice") or "") and st.get("excluded"),
      str(st.get("notice")))

# R3 missing financial data -> verification action (blocked metric).
ws = _build("Calculate Operating Cash Flow.")
st = _state(stage=aa.STAGE_METRIC, metric="Operating Cash Flow")
v = aa.agent_session(ws, st)
check("R3a · blocked metric surfaces a verification action",
      bool(v["recommended"]) and v["recommended"].get("id") == "metric.review",
      str(v["recommended"]))
check("R3b · blocked guidance is tutor-structured",
      v["guidance"].get("kind") == "blocked" and bool(v["guidance"].get("what"))
      and bool(v["guidance"].get("why")) and bool(v["guidance"].get("next")),
      str({k: v["guidance"].get(k) for k in ("kind", "title")}))

# R4 blocked metric -> safe explanation (no traceback language).
check("R4a · blocked message is a safe tutor explanation",
      "couldn't safely" in (v["message"] or "") and "verified information" in (v["message"] or ""),
      v["message"][:120])
for bad in ("traceback", "parser failure", "regex", "span scan", "backend exception",
            "canonicalization failure", "extraction stack"):
    check(f"R4b · no internal terminology leaked ('{bad}')", bad not in (v["message"] or "").lower())

# R5 review-required fact -> safe explanation + partial confirmation.
ws = _build("Revenue and ROE.", module3=_base_module3(review_revenue=True))
rec = aa.parse_recovery(ws, "Revenue and ROE.")
check("R5a · review-required fact flags partial recovery",
      rec["state"] == "partial" and "Revenue" in rec["review_required"],
      str(rec))
st = _state(stage=aa.STAGE_METRIC, metric="Revenue")
v = aa.agent_session(ws, st)
check("R5b · review guidance is tutor-structured",
      v["guidance"].get("kind") == "review" and "ambiguous" in (v["guidance"].get("what") or ""),
      str(v["guidance"].get("kind")))
check("R5c · review message asks the student to verify",
      "Please verify it" in (v["message"] or ""), v["message"][:120])

# R6 successful calculation -> next action.
ws = _build("ROE.")
st = _state(stage=aa.STAGE_METRIC, metric="ROE")
v = aa.agent_session(ws, st)
check("R6 · successful calculation yields a next action",
      bool(v["content"].get("has_calculation")) and bool(v["recommended"]),
      str(v["recommended"]))

# R7 changed metric -> investigation action.
ws = _build("ROE.", period=PERIOD_FACTS)
st = _state(stage=aa.STAGE_METRIC, metric="ROE")
v = aa.agent_session(ws, st)
check("R7a · changed metric recommends investigation",
      bool(v["recommended"]) and v["recommended"].get("id") == "metric.explain",
      str(v["recommended"]))
check("R7b · change surfaced in the metric card",
      bool(v["content"].get("change")) and "ROE" in str(v["content"].get("change")),
      str((v["content"].get("change") or {}).get("change_display")))

# R8 evidence available -> evidence action reachable.
ws = _build("Revenue.")
st = _state(stage=aa.STAGE_METRIC, metric="Revenue")
v = aa.agent_session(ws, st)
check("R8a · metric exposes evidence",
      bool(v["content"].get("has_evidence")), str(v["content"].get("has_evidence")))
check("R8b · evidence action exists in the internal choice set",
      any(c.get("id") == "metric.evidence" for c in v["choices"]),
      str([c.get("id") for c in v["choices"]]))

# R9 Excel ready -> Excel action.
st = _state(stage=aa.STAGE_EXCEL)
v = aa.agent_session(ws, st)
check("R9a · Excel stage recommends the working model",
      bool(v["recommended"]) and v["recommended"].get("id") == "excel.download",
      str(v["recommended"]))
check("R9b · Excel orientation is present",
      bool(v["content"].get("orientation")) and v["content"]["orientation"].get("formulas_done") is True)
check("R9c · Sheet 2 / Ratio Analysis is the stated starting point",
      any("Ratio Analysis" in str(s.get("text")) for s in v["content"]["orientation"].get("steps") or []))

# R10 completed workspace -> conclusion action.
st = _state(stage=aa.STAGE_MEMO)
v = aa.agent_session(ws, st)
check("R10a · memo stage recommends the conclusion",
      bool(v["recommended"]) and v["recommended"].get("id") == "memo.conclusion",
      str(v["recommended"]))
st = _state(stage=aa.STAGE_CONCLUSION)
v = aa.agent_session(ws, st)
check("R10b · conclusion stage has no generated action (student writes)",
      v["recommended"] is None and v["alternatives"] == [],
      str(v["recommended"]))
check("R10c · conclusion stage still guides the student",
      "write your conclusion" in (v["message"] or "").lower())

# ---------------------------------------------------------------------------
# 3. Progressive disclosure
# ---------------------------------------------------------------------------
print("\n### P. Progressive disclosure")

ws = _build(CLEAN_TEXT, period=PERIOD_FACTS, peer=True)
st = _state(stage=aa.STAGE_METRIC, metric="ROE")
v = aa.agent_session(ws, st)
check("P1 · internal choice set is complete (information exists)",
      any(c.get("id") == "metric.explain" for c in v["choices"])
      and any(c.get("id") == "metric.calculation" for c in v["choices"])
      and any(c.get("id") == "metric.evidence" for c in v["choices"]),
      str([c.get("id") for c in v["choices"]]))
check("P2 · one primary action only", bool(v["recommended"]) and isinstance(v["recommended"], dict))
check("P3 · at most 2 quiet secondaries at the metric stage",
      len(v["alternatives"]) <= 2, str([a.get("id") for a in v["alternatives"]]))
check("P4 · no 20-button cockpit (total surfaced actions <= 3)",
      (1 if v["recommended"] else 0) + len(v["alternatives"]) <= 3,
      str((1 if v["recommended"] else 0) + len(v["alternatives"])))

# P5 actions change after progress.
ids = {}
for stage, metric in ((aa.STAGE_PERIODS, None), (aa.STAGE_METRIC, "ROE"),
                      (aa.STAGE_EXPLAIN, "ROE"), (aa.STAGE_EVIDENCE, "ROE"),
                      (aa.STAGE_DRIVERS, None), (aa.STAGE_EXCEL, None)):
    vv = aa.agent_session(ws, _state(stage=stage, metric=metric))
    ids[stage] = (vv["recommended"] or {}).get("id")
check("P5 · recommended action changes across stages",
      len(set(x for x in ids.values() if x)) >= 4, str(ids))

# P6 secondary actions remain available on the main stages.
for stage, metric in ((aa.STAGE_OPENING, None), (aa.STAGE_PERIODS, None),
                      (aa.STAGE_METRIC, "ROE"), (aa.STAGE_EXPLAIN, "ROE"),
                      (aa.STAGE_COMPARISON, None), (aa.STAGE_EXCEL, None)):
    vv = aa.agent_session(ws, _state(stage=stage, metric=metric))
    check(f"P6 · alternatives available at {stage}", len(vv["alternatives"]) >= 1,
          str([a.get("id") for a in vv["alternatives"]]))
    check(f"P6b · suggested pool exposed at {stage}",
          "suggested" in vv and isinstance(vv.get("suggested"), list))

# P7 comparison stage offers contextual questions.
v = aa.agent_session(ws, _state(stage=aa.STAGE_COMPARISON))
sug = [s.get("id") for s in v.get("suggested") or []]
check("P7 · comparison suggests 'Explain the biggest difference'",
      any(s.startswith("suggest.explain.") for s in sug), str(sug))

# P8 the 7-step tutor journey maps stages to steps.
check("P8a · opening is step 1 of 7", aa.agent_step("opening")["number"] == 1
      and aa.agent_step("opening")["total"] == 7)
check("P8b · trends stage is step 3", aa.agent_step("metric")["number"] == 3)
check("P8c · conclusion is step 7", aa.agent_step("conclusion")["number"] == 7
      and aa.agent_step("conclusion")["label"] == "Student conclusion")

# ---------------------------------------------------------------------------
# 4. Demo / API parity & determinism
# ---------------------------------------------------------------------------
print("\n### D. Demo / API parity")

demo_m3 = _base_module3()                      # demo-shaped fixture (demo=True)
api_m3 = _base_module3()
api_m3.pop("demo", None)                       # API-shaped fixture
ws_demo = _build(CLEAN_TEXT, module3=demo_m3, period=PERIOD_FACTS, peer=True)
ws_api = _build(CLEAN_TEXT, module3=api_m3, period=PERIOD_FACTS, peer=True)

journey = ["opening.requirements", "requirements.continue", "period.ROE",
           "metric.explain", "explain.evidence", "continue", "continue",
           "continue", "continue", "memo.conclusion"]
st_demo = aa.initial_state()
st_api = aa.initial_state()
views_demo, views_api = [], []
for cid in journey:
    views_demo.append(aa.agent_session(ws_demo, st_demo, requirements_text=CLEAN_TEXT))
    views_api.append(aa.agent_session(ws_api, st_api, requirements_text=CLEAN_TEXT))
    st_demo = aa.apply_choice(st_demo, cid, ws_demo, CLEAN_TEXT)
    st_api = aa.apply_choice(st_api, cid, ws_api, CLEAN_TEXT)
check("D1 · Demo and API produce identical agent states",
      [v["stage"] for v in views_demo] == [v["stage"] for v in views_api]
      and st_demo["stage"] == st_api["stage"],
      str(st_demo["stage"]))
check("D1b · identical messages across paths",
      [v["message"] for v in views_demo] == [v["message"] for v in views_api])

v1 = aa.agent_session(ws_demo, aa.initial_state(), requirements_text=CLEAN_TEXT)
v2 = aa.agent_session(ws_demo, aa.initial_state(), requirements_text=CLEAN_TEXT)
check("D2 · agent output is deterministic (byte-identical)",
      copy.deepcopy(v1) == copy.deepcopy(v2))

m3_snapshot = copy.deepcopy(demo_m3)
aa.agent_session(ws_demo, aa.initial_state(), requirements_text=CLEAN_TEXT)
aa.apply_choice(aa.initial_state(), "opening.periods", ws_demo, CLEAN_TEXT)
check("D3 · agent never mutates the demo fixture",
      copy.deepcopy(demo_m3) == m3_snapshot)

with open(os.path.join(ROOT, "backend", "assignment_agent.py"), "r", encoding="utf-8") as f:
    AGENT_SRC = f.read().lower()
for lib in ("openai", "anthropic", "import requests", "import urllib", "aiohttp"):
    check(f"D4 · no {lib.strip().replace('import ', '')} in the agent layer",
          lib not in AGENT_SRC)

# ---------------------------------------------------------------------------
# 5. Conclusion safety
# ---------------------------------------------------------------------------
print("\n### C. Conclusion")

st = _state(stage=aa.STAGE_CONCLUSION)
v = aa.agent_session(ws_demo, st)
c = v["content"]
check("C1 · conclusion carries only scaffold + checklist (never a generated paragraph)",
      set(c.keys()) == {"checklist", "never_generate", "scaffold"}
      and c.get("never_generate") is True, str(sorted(c.keys())))
check("C2 · conclusion checklist is evidence-based, not a verdict",
      bool(c.get("checklist")) and bool(c.get("scaffold")))
check("C3 · scaffold hands the judgment to the student",
      any("Your task" in s for s in (c.get("scaffold") or [])))

banned = ("buy", "sell", "recommendation", "recommend you", "overweight",
          "underweight", "hold ", "outperform")
all_msgs = []
for stage in (aa.STAGE_OPENING, aa.STAGE_REQUIREMENTS, aa.STAGE_PERIODS,
              aa.STAGE_METRIC, aa.STAGE_EXPLAIN, aa.STAGE_CALCULATION,
              aa.STAGE_EVIDENCE, aa.STAGE_DRIVERS, aa.STAGE_QUALITATIVE,
              aa.STAGE_COMPARISON, aa.STAGE_EXTERNAL, aa.STAGE_EXCEL,
              aa.STAGE_MEMO, aa.STAGE_CONCLUSION):
    vv = aa.agent_session(ws_demo, _state(stage=stage, metric="ROE"), requirements_text=CLEAN_TEXT)
    all_msgs.append(str(vv.get("message") or "").lower())
for word in banned:
    check(f"C4 · no '{word.strip()}' wording in any agent message",
          not any(word in m for m in all_msgs))

# Full-journey sanity: a student can walk the whole flow with no dead ends.
st = aa.initial_state()
st = aa.apply_choice(st, "opening.periods", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "period.ROE", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "metric.explain", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "continue", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "continue", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "continue", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "continue", ws_demo, CLEAN_TEXT)
st = aa.apply_choice(st, "memo.conclusion", ws_demo, CLEAN_TEXT)
check("J1 · full guided journey reaches the conclusion without dead ends",
      st.get("stage") == "conclusion", str(st.get("stage")))
v_end = aa.agent_session(ws_demo, st, requirements_text=CLEAN_TEXT)
check("J2 · conclusion stage shows the writing prompt, not a verdict",
      "write your conclusion" in (v_end.get("message") or "").lower()
      and v_end.get("recommended") is None)

print("\n============================================================")
if FAILS:
    print(f"RESULT: {len(FAILS)} FAILED -> {FAILS}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASS")
print("ALL CHECKS PASS")
sys.exit(0)
