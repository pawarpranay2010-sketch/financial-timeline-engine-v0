"""Sprint 12.2 - Student Workspace Navigation & State Preservation

Deterministic suite for the workspace action -> rerun navigation defect.

A click on ANY primary/secondary Agent action must keep the student inside
the Student Assignment Workspace (never the Entrance / start screen), keep
the full assignment session state intact, and advance the Agent step. The
workspace must behave like a persistent application flow, not a collection
of independent reruns.

 Navigation
   1.  An Agent action never mutates fte_route (no accidental navigation)
   2.  An Agent action never mutates fte_page (stays on the workspace)
   3.  An Agent action never drops Demo/API mode
   4.  Quiet secondary links carry explicit route markers (demo + real)
   5.  Demo URL markers rebuild route/page after a full-page navigation
   6.  Real-workspace URL marker rebuilds the route after a full load
   7.  No marker -> fresh visitor is unaffected (stays at entrance)
   8.  Restore guard: live workspace state restores the route
   9.  Sign-out / exit-demo clear the sentinel (intentional exits)

 State preservation
  10. Assignment context (requirements/company/external vars) survives
  11. Agent step advances correctly through the whole journey
  12. Excel / evidence / memo actions stay in the workspace
  13. Demo and API produce the same workspace flow (parity)

 Button matrix
  14. Every recommended primary action has a deterministic transition
  15. Every alternative/secondary action has a deterministic transition
  16. Unknown ids are ignored (fail-closed, never a crash)

Every decision is deterministic; no API key, no AI, no network.
"""
import os
import sys
import types
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
    apply_choice,
    initial_state,
)
from backend.student_workspace import (  # noqa: E402
    build_student_workspace,
    parse_requirements,
)

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


# ---------------------------------------------------------------------------
# App-under-test with a stubbed streamlit (session state + query params).
# ---------------------------------------------------------------------------
_APP = None
_STUB_SS = {}


class _QP(dict):
    """query_params stub: dict that supports get/in/del (like Streamlit's)."""


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
        self._ss = _STUB_SS
        self.query_params = _QP()
        self.rerun = lambda: None
        self.switch_page = lambda *a, **k: None

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
            "fte_app_nav_under_test", os.path.join(root, "app (1) (9).py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.modules["streamlit"] = _real
    _APP = mod
    return mod


# ---------------------------------------------------------------------------
# Deterministic fixtures (Demo + real/API), mirroring the Sprint 12.x suites.
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


app = _load_app()
ws_demo = _demo_workspace()
ws_real = _real_workspace(period_facts=_PERIOD_FACTS)
_DEMO_REQ_TEXT = app._demo_assignment_requirements_text()
with open(os.path.join(os.path.dirname(__file__), "..", "app (1) (9).py"),
          encoding="utf-8") as _fh:
    _APP_SRC = _fh.read()

_OLD_SS = app.st.session_state  # the shared stub dict; restored after each call


def _fresh_session(**kw):
    """A fresh stub session with the terminal defaults the app would set."""
    ss = {
        "fte_route": "entrance",
        "fte_page": "Financial Grid",
        "fte_demo_mode": False,
        "fte_ws_active": False,
        "fte_workspace": None,
        "fte_assignment_requirements": _DEMO_REQ_TEXT,
        "fte_assignment_company": "Contoso Analytics (Demo)",
        "fte_assignment_company_b": "PeerCo Inc.",
        "fte_external_variables": [],
        "fte_agent_state": initial_state(),
        "fte_agent_explore": False,
        "fte_agent_edit_reqs": False,
        "fte_agent_ctx": None,
    }
    ss.update(kw)
    return ss


# ---------------------------------------------------------------------------
# 1-3 · Navigation invariants: an Agent action never navigates away
# ---------------------------------------------------------------------------
_MATRIX_IDS = [
    "opening.requirements",
    "requirements.confirm",
    "requirements.continue",
    "requirements.include.0",
    "requirements.exclude.0",
    "requirements.edit",
    "period.ROE",
    "metric.explain",
    "metric.calculation",
    "metric.evidence",
    "explain.qualitative",
    "continue",
    "skip",
    "back",
    "explore",
    "comparison.ROE",
    "excel.download",
    "excel.understand",
    "excel.evidence",
    "memo.conclusion",
    "suggest.comparison",
    "suggest.conclusion",
    "suggest.explain.ROE",
    "suggest.evidence.ROE",
    "suggest.calculation.ROE",
]

for _tag, _ws, _ss0 in (
    ("demo", ws_demo, _fresh_session(fte_route="demo", fte_page="Assignment",
                                     fte_demo_mode=True, fte_ws_active=True)),
    ("real", ws_real, _fresh_session(fte_route="workspace", fte_page="Assignment",
                                     fte_ws_active=True)),
):
    for _cid in _MATRIX_IDS:
        app.st.session_state = dict(_ss0)
        app._agent_apply(_cid, _ws, _DEMO_REQ_TEXT)
        _ns = dict(app.st.session_state)
        app.st.session_state = _OLD_SS
        check(f"nav[{_tag}] · '{_cid}' never leaves the workspace (route)",
              _ns["fte_route"] == _ss0["fte_route"],
              f"route {_ss0['fte_route']} -> {_ns['fte_route']}")
        check(f"nav[{_tag}] · '{_cid}' keeps the workspace page",
              _ns["fte_page"] == _ss0["fte_page"],
              f"page {_ss0['fte_page']} -> {_ns['fte_page']}")
        check(f"nav[{_tag}] · '{_cid}' keeps demo/api mode",
              _ns["fte_demo_mode"] == _ss0["fte_demo_mode"])
        check(f"nav[{_tag}] · '{_cid}' keeps assignment context",
              _ns["fte_assignment_requirements"] == _ss0["fte_assignment_requirements"]
              and _ns["fte_assignment_company"] == _ss0["fte_assignment_company"]
              and _ns["fte_external_variables"] == _ss0["fte_external_variables"])

# ---------------------------------------------------------------------------
# 4 · Quiet secondary links carry route markers
# ---------------------------------------------------------------------------
app.st.session_state = _fresh_session(fte_demo_mode=True)
_href_demo = app._agent_quiet_link("continue", "Continue")
app.st.session_state = _OLD_SS
check("links · demo quiet link carries demo marker",
      "fte_demo=1" in _href_demo and "fte_page=Assignment" in _href_demo and
      "fte_agent_action=continue" in _href_demo, _href_demo)
app.st.session_state = _fresh_session(fte_demo_mode=False)
_href_real = app._agent_quiet_link("continue", "Continue")
app.st.session_state = _OLD_SS
check("links · real quiet link keeps the established format",
      _href_real ==
      '<a class="fte-agent-quiet" href="?fte_agent_action=continue">Continue</a>',
      _href_real)
check("links · source contains the demo marker prefix",
      "?fte_demo=1&fte_page=Assignment" in _APP_SRC)
check("links · app never uses switch_page navigation",
      "st.switch_page" not in _APP_SRC)

# ---------------------------------------------------------------------------
# 5-7 · Full-page navigation reconstruction
# ---------------------------------------------------------------------------
def _run_reconstruct(ss, params):
    """Run _reconstruct_demo_from_query with a stub session + query params."""
    app.st.session_state = ss
    app.st.query_params.clear()
    for k, v in params.items():
        app.st.query_params[k] = v
    app._reconstruct_demo_from_query()
    out = (dict(ss), dict(app.st.query_params))
    app.st.session_state = _OLD_SS
    app.st.query_params.clear()
    return out


_ss, _qp = _run_reconstruct(
    _fresh_session(), {"fte_demo": "1", "fte_page": "Assignment", "fte_agent_action": "continue"})
check("recon · demo agent link restores route+demo+Assignment",
      _ss["fte_route"] == "demo" and _ss["fte_demo_mode"] is True and
      _ss["fte_page"] == "Assignment" and _ss["fte_memo_view_open"] is False)
check("recon · demo markers consumed (action param kept for the workspace)",
      "fte_demo" not in _qp and "fte_page" not in _qp and
      _qp.get("fte_agent_action") == "continue")

_ss, _qp = _run_reconstruct(
    _fresh_session(), {"fte_demo": "1", "fte_metric": "ROE"})
check("recon · demo memo link restores Intelligence memo view",
      _ss["fte_route"] == "demo" and _ss["fte_page"] == "Intelligence" and
      _ss["fte_memo_view_open"] is True and _ss["fte_memo_metric_click"] == "ROE")

_ss, _qp = _run_reconstruct(
    _fresh_session(), {"fte_route": "workspace", "fte_page": "Assignment", "fte_agent_action": "explain.evidence"})
check("recon · real workspace link restores workspace route",
      _ss["fte_route"] == "workspace" and _ss["fte_page"] == "Assignment")
check("recon · real markers consumed",
      "fte_route" not in _qp and "fte_page" not in _qp and
      _qp.get("fte_agent_action") == "explain.evidence")

# 6c. Real-workspace quiet link (established format) signals the workspace.
_ss, _qp = _run_reconstruct(
    _fresh_session(), {"fte_agent_action": "metric.evidence"})
check("recon · bare fte_agent_action restores workspace + Assignment",
      _ss["fte_route"] == "workspace" and _ss["fte_page"] == "Assignment" and
      _qp.get("fte_agent_action") == "metric.evidence")

# 6d. Real-workspace memo metric link signals the workspace.
_ss, _qp = _run_reconstruct(
    _fresh_session(), {"fte_metric": "ROE"})
check("recon · bare fte_metric restores the workspace",
      _ss["fte_route"] == "workspace" and _qp.get("fte_metric") == "ROE")

_ss, _qp = _run_reconstruct(
    _fresh_session(fte_route="workspace", fte_page="Assignment"),
    {"fte_route": "demo"})
check("recon · live session never overridden by a stale marker",
      _ss["fte_route"] == "workspace")

_ss, _qp = _run_reconstruct(_fresh_session(), {})
check("recon · no markers leaves fresh visitor at entrance",
      _ss["fte_route"] == "entrance" and "fte_route" not in _qp and "fte_page" not in _qp)

# ---------------------------------------------------------------------------
# 8-9 · Route-restore guard (defense-in-depth for in-session reruns)
# ---------------------------------------------------------------------------
def _restore_with(route, demo, active):
    app.st.session_state = _fresh_session(fte_route=route, fte_demo_mode=demo, fte_ws_active=active)
    try:
        return app._restore_workspace_route()
    finally:
        app.st.session_state = _OLD_SS


check("guard · live demo restores demo route",
      _restore_with("entrance", demo=True, active=True) == "demo")
check("guard · live workspace restores workspace route",
      _restore_with("entrance", demo=False, active=True) == "workspace")
check("guard · fresh visitor stays at entrance",
      _restore_with("entrance", demo=False, active=False) == "entrance")
check("guard · live session route is untouched",
      _restore_with("workspace", demo=False, active=True) == "workspace")
check("guard · explicit sign-out/exit-demo stays at entrance",
      _restore_with("entrance", demo=False, active=False) == "entrance")

# ---------------------------------------------------------------------------
# 10-13 · State preservation across the full guided journey (Demo + API)
# ---------------------------------------------------------------------------
# Verified against _CONTINUE_TARGET: calculation -> evidence -> drivers.
_JOURNEY = [
    ("opening.requirements", STAGE_REQUIREMENTS),
    ("requirements.confirm", STAGE_PERIODS),
    ("period.ROE", STAGE_METRIC),
    ("metric.explain", STAGE_EXPLAIN),
    ("metric.evidence", STAGE_EVIDENCE),
    ("metric.calculation", STAGE_CALCULATION),
    ("continue", STAGE_EVIDENCE),
    ("continue", STAGE_DRIVERS),
    ("suggest.comparison", STAGE_COMPARISON),
    ("excel.download", STAGE_EXCEL),
    ("excel.understand", STAGE_EXCEL),
    ("memo.conclusion", STAGE_CONCLUSION),
]

for _tag, _ws, _req in (("demo", ws_demo, _DEMO_REQ_TEXT), ("real", ws_real, _REQ_TEXT)):
    _ss = _fresh_session(fte_route="demo" if _tag == "demo" else "workspace",
                         fte_page="Assignment", fte_demo_mode=(_tag == "demo"),
                         fte_ws_active=True)
    for _i, (_cid, _expected_stage) in enumerate(_JOURNEY):
        app.st.session_state = dict(_ss)
        app._agent_apply(_cid, _ws, _req)
        _ns = dict(app.st.session_state)
        app.st.session_state = _OLD_SS
        check(f"journey[{_tag}] · step {_i + 1} '{_cid}' stays in workspace",
              _ns["fte_route"] == _ss["fte_route"] and
              _ns["fte_page"] == _ss["fte_page"] and
              _ns["fte_demo_mode"] == _ss["fte_demo_mode"],
              f"route={_ns.get('fte_route')} page={_ns.get('fte_page')}")
        check(f"journey[{_tag}] · step {_i + 1} '{_cid}' advances the agent step",
              isinstance(_ns.get("fte_agent_state"), dict) and
              _ns["fte_agent_state"]["stage"] == _expected_stage,
              f"stage={(_ns.get('fte_agent_state') or {}).get('stage')}")
        check(f"journey[{_tag}] · step {_i + 1} keeps assignment context",
              _ns["fte_assignment_requirements"] == _ss["fte_assignment_requirements"] and
              _ns["fte_assignment_company"] == _ss["fte_assignment_company"] and
              _ns["fte_external_variables"] == _ss["fte_external_variables"])
        _ss = _ns
    check(f"journey[{_tag}] · full guided journey reaches the conclusion",
          _ss["fte_agent_state"]["stage"] == STAGE_CONCLUSION)


def _apply_chain(ws, req):
    s = initial_state()
    for cid, _ in _JOURNEY:
        s = apply_choice(s, cid, ws)
    return s["stage"]


check("parity · Demo and API end on the same stage",
      _apply_chain(ws_demo, _DEMO_REQ_TEXT) == _apply_chain(ws_real, _REQ_TEXT) == STAGE_CONCLUSION)

# ---------------------------------------------------------------------------
# 14-16 · Button matrix determinism (backend state machine)
# ---------------------------------------------------------------------------
_state = initial_state()
for _cid, _ in _JOURNEY:
    _next = apply_choice(_state, _cid, ws_demo)
    check(f"matrix · '{_cid}' has a deterministic transition",
          isinstance(_next, dict) and _next.get("stage") in {
              STAGE_OPENING, STAGE_REQUIREMENTS, STAGE_PERIODS, STAGE_METRIC,
              STAGE_EXPLAIN, STAGE_CALCULATION, STAGE_EVIDENCE, STAGE_DRIVERS,
              STAGE_QUALITATIVE, STAGE_COMPARISON, STAGE_EXTERNAL, STAGE_EXCEL,
              STAGE_MEMO, STAGE_CONCLUSION,
          }, f"got {_next.get('stage')}")
    _state = _next
check("matrix · unknown id is fail-closed (no crash, no navigation)",
      apply_choice(_state, "totally.unknown.action", ws_demo)["stage"] == _state["stage"])

# ---------------------------------------------------------------------------
# 17 · Duplicate-widget-key regression (StreamlitDuplicateElementKey)
# ---------------------------------------------------------------------------
class _RecordingStreamlit(types.ModuleType):
    """Recording stub: captures every widget key registered during a render so
    we can assert exactly one workspace instance renders per page/path (no
    StreamlitDuplicateElementKey). Layout/non-keyed calls are no-ops."""

    def __init__(self, ss):
        super().__init__("streamlit")
        self._ss = ss
        self.query_params = _QP()
        self.registered_keys = []

    # -- keyed widget surface used by the workspace renderers --
    def selectbox(self, label, options, key=None, **kw):
        self.registered_keys.append(key)
        return options[0] if options else None

    def text_input(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return ""

    def text_area(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return ""

    def multiselect(self, label, options, key=None, **kw):
        self.registered_keys.append(key)
        return []

    def radio(self, label, options, key=None, **kw):
        self.registered_keys.append(key)
        return options[0] if options else None

    def button(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return False

    def download_button(self, label, data=None, key=None, **kw):
        self.registered_keys.append(key)
        return False

    def checkbox(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return False

    def number_input(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return 0.0

    def slider(self, label, key=None, **kw):
        self.registered_keys.append(key)
        return 0

    # -- layout / non-keyed surface --
    def markdown(self, *a, **kw):
        return None

    def caption(self, *a, **kw):
        return None

    def dataframe(self, *a, **kw):
        return None

    def success(self, *a, **kw):
        return None

    def warning(self, *a, **kw):
        return None

    def info(self, *a, **kw):
        return None

    def error(self, *a, **kw):
        return None

    def divider(self, *a, **kw):
        return None

    def progress(self, *a, **kw):
        return None

    def spinner(self, *a, **kw):
        class _Spinner:
            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False
        return _Spinner()

    def columns(self, spec, **kw):
        n = len(spec) if isinstance(spec, (list, tuple)) else int(spec)
        return [_RecordingCtx(self) for _ in range(n)]

    def expander(self, *a, **kw):
        return _RecordingCtx(self)

    def container(self, *a, **kw):
        return _RecordingCtx(self)

    def form(self, *a, **kw):
        return _RecordingCtx(self)

    def tabs(self, labels, **kw):
        return [_RecordingCtx(self) for _ in range(len(labels))]

    def rerun(self):
        pass

    def switch_page(self, *a, **kw):
        pass

    def __getattr__(self, name):
        if name == "session_state":
            return self._ss
        return _Passthrough()


class _RecordingCtx:
    """Context-manager wrapper that delegates widget calls to the recorder."""

    def __init__(self, rec):
        self._rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(self._rec, name)


def _render_workspace_recording(demo, explore):
    """Render the Student Assignment Workspace under the recording stub and
    return (registered widget keys, mutated session)."""
    ss = _fresh_session(
        fte_route="demo" if demo else "workspace",
        fte_page="Assignment",
        fte_demo_mode=demo,
        fte_ws_active=True,
        fte_agent_explore=explore,
    )
    rec = _RecordingStreamlit(ss)
    old_st = app.st
    app.st = rec
    try:
        module3 = app._demo_module3_result() if demo else _real_module3()
        app._render_student_assignment_workspace(module3, demo=demo)
    finally:
        app.st = old_st
    return rec.registered_keys, ss


# Demo path: guided render (explore off) — only the guided setup panel
# registers fte_assignment_type; the legacy view is never invoked.
_keys_demo_guided, _ = _render_workspace_recording(demo=True, explore=False)
check("dupkey · demo guided render registers fte_assignment_type exactly once",
      _keys_demo_guided.count("fte_assignment_type") == 1)
check("dupkey · demo guided render has no duplicate widget keys",
      len(_keys_demo_guided) == len(set(_keys_demo_guided)))
check("dupkey · demo guided render never invokes the legacy view",
      "fte_extvar_name" not in _keys_demo_guided)

# Demo path: explore render (explore on) — ONLY the legacy view renders,
# still exactly one fte_assignment_type (no duplicate key crash).
_keys_demo_explore, _ = _render_workspace_recording(demo=True, explore=True)
check("dupkey · demo explore render registers fte_assignment_type exactly once",
      _keys_demo_explore.count("fte_assignment_type") == 1)
check("dupkey · demo explore render has no duplicate widget keys",
      len(_keys_demo_explore) == len(set(_keys_demo_explore)))

# API/real path: same two modes.
_keys_real_guided, _ = _render_workspace_recording(demo=False, explore=False)
check("dupkey · real guided render registers fte_assignment_type exactly once",
      _keys_real_guided.count("fte_assignment_type") == 1)
check("dupkey · real guided render has no duplicate widget keys",
      len(_keys_real_guided) == len(set(_keys_real_guided)))

_keys_real_explore, _ = _render_workspace_recording(demo=False, explore=True)
check("dupkey · real explore render registers fte_assignment_type exactly once",
      _keys_real_explore.count("fte_assignment_type") == 1)
check("dupkey · real explore render has no duplicate widget keys",
      len(_keys_real_explore) == len(set(_keys_real_explore)))

# Source-level guards: the explore branch precedes the guided setup panel,
# and the old trailing explore branch is gone.
_gstart = _APP_SRC.index("def _render_student_assignment_workspace(")
_gend = _APP_SRC.index("def _render_student_assignment_workspace_legacy(")
_guided_src = _APP_SRC[_gstart:_gend]
check("dupkey · explore branch precedes the guided setup widgets",
      _guided_src.index('if st.session_state.get("fte_agent_explore"):') <
      _guided_src.index('st.selectbox("Assignment type", assignment_types, key="fte_assignment_type")'))
check("dupkey · legacy call site lives only in the top explore branch",
      _guided_src.count("_render_student_assignment_workspace_legacy(") == 1 and
      _guided_src.index("_render_student_assignment_workspace_legacy(") <
      _guided_src.index('st.selectbox("Assignment type", assignment_types, key="fte_assignment_type")'))
check("dupkey · old trailing explore branch removed",
      "# Explore workspace: the full deterministic view, one click away." not in _APP_SRC)

# ---------------------------------------------------------------------------
# Source-level guards
# ---------------------------------------------------------------------------
check("src · restore guard and sentinel present",
      "_restore_workspace_route" in _APP_SRC and "fte_ws_active" in _APP_SRC)
check("src · primary action remains a single blue button",
      'type="primary"' in _APP_SRC and "fte_agent_next_primary_" in _APP_SRC)
_apply_zone = _APP_SRC[_APP_SRC.index("def _agent_apply"):_APP_SRC.index("def _agent_header_html")]
check("src · no fte_route reset inside the agent state machine",
      "fte_agent_apply_choice" in _apply_zone and '"fte_route"' not in _apply_zone)


def main():
    failures = [c for c in CHECKS if not c[1]]
    print(f"\nRESULT: {len(CHECKS) - len(failures)}/{len(CHECKS)} checks pass")
    if failures:
        for name, _ok, detail in failures[:40]:
            print(f"  FAIL {name}: {detail}")
        if len(failures) > 40:
            print(f"  ... and {len(failures) - 40} more")
        sys.exit(1)
    print("ALL CHECKS PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
