"""
Financial Timeline Engine
Sprint 12 - Student Assignment Agent (Progressive Guided Workspace)

A DETERMINISTIC orchestration/presentation layer that guides a student
through the Student Assignment Workspace one small step at a time, instead
of exposing every analytical capability at once.

Hard rules
----------
* NO Streamlit, NO AI, NO network, NO randomness, NO time-dependent logic.
  Identical inputs always produce identical agent states and messages.
* The agent NEVER invents financial facts, causes, sources, calculations
  or conclusions. Every message and value comes from the already-built
  deterministic workspace dict (verified fact graph + Formula Engine +
  qualitative catalyst layer).
* The agent NEVER writes the student's conclusion. The conclusion stage
  only provides a fact checklist; the final judgment stays student-owned.
* Fail-closed: a metric that is BLOCKED / REVIEW_REQUIRED / conflicted
  produces guidance that names the gap and offers a useful next action —
  it is never presented as verified, and the student is never trapped.
* The agent has no dead ends: every stage exposes Back / Skip / Explore /
  Continue style controls through its deterministic choice set.

The same agent session is used by BOTH the API (real-document) path and
the Demo path — only the underlying workspace differs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.qualitative_catalyst import RELATIONSHIP_LABELS

# ---------------------------------------------------------------------------
# Stage vocabulary
# ---------------------------------------------------------------------------

STAGE_OPENING = "opening"
STAGE_REQUIREMENTS = "requirements"
STAGE_PERIODS = "periods"
STAGE_METRIC = "metric"
STAGE_EXPLAIN = "explain"
STAGE_CALCULATION = "calculation"
STAGE_EVIDENCE = "evidence"
STAGE_DRIVERS = "drivers"
STAGE_QUALITATIVE = "qualitative"
STAGE_COMPARISON = "comparison"
STAGE_EXTERNAL = "external"
STAGE_EXCEL = "excel"
STAGE_MEMO = "memo"
STAGE_CONCLUSION = "conclusion"

# Internal stage used while the student explores the full workspace.
STAGE_EXPLORE_UI = "__explore__"

AGENT_STAGE_IDS: List[str] = [
    STAGE_OPENING, STAGE_REQUIREMENTS, STAGE_PERIODS, STAGE_METRIC,
    STAGE_EXPLAIN, STAGE_CALCULATION, STAGE_EVIDENCE, STAGE_DRIVERS,
    STAGE_QUALITATIVE, STAGE_COMPARISON, STAGE_EXTERNAL, STAGE_EXCEL,
    STAGE_MEMO, STAGE_CONCLUSION,
]

# Progress indicator rows (✓ done / → current / ○ todo). Deterministic.
PROGRESS_STAGES: List[Dict[str, str]] = [
    {"id": "requirements", "label": "Requirements"},
    {"id": "financials", "label": "Financial data"},
    {"id": "calculations", "label": "Calculations"},
    {"id": "periods", "label": "Period analysis"},
    {"id": "comparison", "label": "Peer comparison"},
    {"id": "drivers", "label": "Driver investigation"},
    {"id": "conclusion", "label": "Student conclusion"},
]

# Which progress row a stage advances / occupies.
_STAGE_PROGRESS: Dict[str, str] = {
    STAGE_OPENING: "requirements",
    STAGE_REQUIREMENTS: "requirements",
    STAGE_PERIODS: "periods",
    STAGE_METRIC: "calculations",
    STAGE_EXPLAIN: "calculations",
    STAGE_CALCULATION: "calculations",
    STAGE_EVIDENCE: "calculations",
    STAGE_DRIVERS: "drivers",
    STAGE_QUALITATIVE: "drivers",
    STAGE_COMPARISON: "comparison",
    STAGE_EXTERNAL: "calculations",
    STAGE_EXCEL: None,  # deliverable — everything before conclusion is done
    STAGE_MEMO: None,
    STAGE_CONCLUSION: "conclusion",
}

# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


def initial_state() -> Dict[str, Any]:
    """Deterministic starting state of the Assignment Agent session."""
    return {
        "stage": STAGE_OPENING,
        "metric": None,
        "area": None,
        "visited": [],  # stage ids the student has already seen
    }


def _mark_visited(state: Dict[str, Any], stage: str) -> List[str]:
    visited = list(state.get("visited") or [])
    if stage and stage not in visited:
        visited.append(stage)
    return visited


# ---------------------------------------------------------------------------
# Workspace helpers (all fail-closed, never guessing)
# ---------------------------------------------------------------------------


def _req_rows(workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((workspace or {}).get("requirements") or [])


def _norm_facts(workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((workspace or {}).get("normalized_facts") or [])


def _driver(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return (workspace or {}).get("driver_analysis") or {}


def _qual(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return (workspace or {}).get("qualitative_drivers") or {}


def _comparison(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return (workspace or {}).get("comparison") or {}


def _calcs(workspace: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Dict[str, Any]] = {}
    for c in (workspace or {}).get("calculations") or []:
        out.setdefault(str(c.get("metric") or c.get("name") or ""), c)
    return out


def _period_values(workspace: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    driver = _driver(workspace)
    for obs in driver.get("observations") or []:
        metric = str(obs.get("metric") or "")
        if not metric:
            continue
        out.setdefault(metric, {})[str(obs.get("from") or "")] = str(obs.get("from_value") or "—")
        out.setdefault(metric, {})[str(obs.get("to") or "")] = str(obs.get("to_value") or "—")
    return out


def _metric_status(workspace: Dict[str, Any], metric: str) -> str:
    for r in _req_rows(workspace):
        if str(r.get("requirement")) == metric:
            return str(r.get("status") or "")
    calc = _calcs(workspace).get(metric)
    if calc:
        return str(calc.get("workspace_status") or calc.get("status") or "")
    return ""


def _metric_result(workspace: Dict[str, Any], metric: str) -> str:
    for r in _req_rows(workspace):
        if str(r.get("requirement")) == metric:
            res = r.get("result")
            if res not in (None, "", "—"):
                return str(res)
    calc = _calcs(workspace).get(metric)
    if calc and calc.get("display_value") not in (None, "", "—"):
        return str(calc.get("display_value"))
    for f in _norm_facts(workspace):
        if str(f.get("metric")) == metric and f.get("display_value") not in (None, "", "—"):
            return str(f.get("display_value"))
    return "—"


def _metric_change(workspace: Dict[str, Any], metric: str) -> Optional[Dict[str, Any]]:
    for obs in (_driver(workspace).get("observations") or []):
        if str(obs.get("metric")) == metric:
            return obs
    return None


def _strongest_changes(workspace: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    """Deterministic: period-over-period changes sorted by |% change|
    descending, ties broken alphabetically by metric name."""
    obs = list(_driver(workspace).get("observations") or [])
    return sorted(
        obs,
        key=lambda o: (
            -abs(float(o.get("change_pct") or 0.0)),
            str(o.get("metric") or ""),
            str(o.get("from") or ""),
        ),
    )[:limit]


def _metric_choices(workspace: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    """Choice buttons for the strongest verified changes (metrics the
    student can investigate). Never shows a metric with no change."""
    out: List[Dict[str, Any]] = []
    for obs in _strongest_changes(workspace, limit):
        metric = str(obs.get("metric") or "")
        if not metric:
            continue
        out.append({
            "id": f"period.{metric}",
            "label": f"{metric} ({obs.get('change_display') or '—'})",
            "hint": f"Investigate the {obs.get('change_display') or 'change'} in {metric}.",
        })
    return out


def _period_list(workspace: Dict[str, Any]) -> List[str]:
    return sorted({p for m in (_period_values(workspace) or {}).values() for p in m})


def _calc_metric(workspace: Dict[str, Any], metric: str) -> Optional[Dict[str, Any]]:
    return _calcs(workspace).get(metric)


def _qual_row(workspace: Dict[str, Any], metric: str) -> Optional[Dict[str, Any]]:
    for q in (_qual(workspace).get("rows") or []):
        if str(q.get("metric")) == metric:
            return q
    return None


def _norm_evidence_fields(workspace: Dict[str, Any], metric: str) -> List[Dict[str, str]]:
    """Deterministic provenance fields for a metric from the normalized
    fact graph. Only real fields are emitted (never invented)."""
    fields: List[Dict[str, str]] = []
    for f in _norm_facts(workspace):
        if str(f.get("metric")) != metric:
            continue
        for label, key in (
            ("Source", "source"), ("Period", "period"), ("Page", "page"),
            ("Evidence", "evidence"), ("Currency", "currency"),
            ("Unit", "unit"), ("Provenance", "provenance_tier"),
        ):
            v = f.get(key)
            if v in (None, "", "—"):
                continue
            fields.append({"label": label, "value": str(v)})
        break
    if not fields:
        calc = _calc_metric(workspace, metric)
        if calc:
            for label, key in (
                ("Formula", "formula"), ("Status", "workspace_status_label"),
                ("Note", "workspace_note"), ("Lineage", "lineage"),
            ):
                v = calc.get(key)
                if v in (None, "", "—"):
                    continue
                fields.append({"label": label, "value": str(v)})
    return fields


def _conflict_metrics(workspace: Dict[str, Any], facts_src: Optional[Dict[str, Any]] = None) -> List[str]:
    """Deterministic conflict detection: a fact whose extraction state is
    'conflict' (cross-document verification surfaced it). Never guesses —
    only explicit conflict markers count."""
    out: List[str] = []
    seen: List[str] = []
    sources = [facts_src]
    if facts_src is None:
        # normalized facts carry the extraction state when the pipeline
        # surfaced it into the workspace.
        for f in _norm_facts(workspace):
            if str(f.get("metric")) in seen:
                continue
            reason = str(f.get("normalization_reason") or "")
            if "conflict" in reason.lower():
                out.append(str(f.get("metric")))
                seen.append(str(f.get("metric")))
        return sorted(out)
    for section in ("financial_data", "ratios"):
        for key, fact in ((facts_src or {}).get(section) or {}).items():
            if not isinstance(fact, dict):
                continue
            if str(fact.get("extraction_state")) == "conflict":
                if str(key) not in seen:
                    out.append(str(key))
                    seen.append(str(key))
    return sorted(out)


def _review_required_metrics(workspace: Dict[str, Any]) -> List[str]:
    out = []
    for r in _req_rows(workspace):
        if r.get("status") == "REVIEW_REQUIRED":
            out.append(str(r.get("requirement")))
    for f in _norm_facts(workspace):
        if f.get("normalization_status") == "REVIEW_REQUIRED":
            m = str(f.get("metric"))
            if m not in out:
                out.append(m)
    return sorted(out)


def _blocked_metrics(workspace: Dict[str, Any]) -> List[str]:
    return sorted(
        str(r.get("requirement")) for r in _req_rows(workspace)
        if r.get("status") == "BLOCKED"
    )


# ---------------------------------------------------------------------------
# Stage content builders (message + structured content)
# ---------------------------------------------------------------------------

_COMPARISON_AREAS = [
    ("profitability", "Profitability", ["ROE", "ROA", "Profit Margin", "Operating Margin"]),
    ("leverage", "Leverage", ["Debt to Equity", "Debt", "Equity", "Liabilities"]),
    ("liquidity", "Liquidity", ["Current Ratio", "Current Assets", "Current Liabilities"]),
    ("size", "Size", ["Revenue", "Net Profit", "Operating Profit", "Assets"]),
]


def _comparison_area_map() -> Dict[str, List[str]]:
    return {area_id: metrics for area_id, _label, metrics in _COMPARISON_AREAS}


def _comparison_rows(workspace: Dict[str, Any], area: Optional[str] = None) -> List[Dict[str, Any]]:
    comp = _comparison(workspace)
    rows = list(comp.get("rows") or [])
    if area:
        keep = set(_comparison_area_map().get(area, []))
        rows = [r for r in rows if str(r.get("canonical")) in keep]
    return rows


def _content_opening(workspace: Dict[str, Any]) -> Dict[str, Any]:
    reqs = _req_rows(workspace)
    company = str((workspace or {}).get("company") or "Company A")
    req_names = [str(r.get("requirement")) for r in reqs]
    periods = _period_list(workspace)
    comp = _comparison(workspace)
    return {
        "company": company,
        "assignment_type": str((workspace or {}).get("assignment_type") or "—"),
        "requirement_count": len(req_names),
        "requirements": req_names,
        "periods": periods,
        "has_periods": bool(periods),
        "comparison_active": bool(comp.get("active")),
        "review_count": len(_review_required_metrics(workspace)),
        "blocked_count": len(_blocked_metrics(workspace)),
        "conflict_count": len(_conflict_metrics(workspace)),
    }


def _content_requirements(workspace: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for r in _req_rows(workspace):
        rows.append({
            "requirement": str(r.get("requirement") or "—"),
            "status": str(r.get("status") or "—"),
            "status_label": str(r.get("status_label") or r.get("status") or "—"),
            "result": str(r.get("result") or "—"),
            "evidence": str(r.get("evidence") or r.get("detail") or ""),
        })
    return {
        "rows": rows,
        "total": len(rows),
        "review_count": len(_review_required_metrics(workspace)),
        "blocked_count": len(_blocked_metrics(workspace)),
    }


def _content_periods(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "periods": _period_list(workspace),
        "changes": _strongest_changes(workspace, 4),
        "metric_choices": _metric_choices(workspace, 4),
        "has_periods": bool(_period_list(workspace)),
    }


def _metric_payload(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    change = _metric_change(workspace, metric)
    status = _metric_status(workspace, metric)
    calc = _calc_metric(workspace, metric)
    qual_row = _qual_row(workspace, metric)
    is_blocked = status == "BLOCKED" or bool(calc and calc.get("status") == "blocked")
    is_review = status == "REVIEW_REQUIRED"
    facts_evidence = _norm_evidence_fields(workspace, metric)
    has_explain = bool(
        (change and qual_row)
        or (change and (calc or facts_evidence))
    )
    return {
        "metric": metric,
        "value": _metric_result(workspace, metric),
        "status": status or "—",
        "change": change,
        "is_blocked": is_blocked,
        "is_review": is_review,
        "has_explain": has_explain,
        "has_calculation": calc is not None,
        "has_evidence": bool(facts_evidence),
        "qualitative": qual_row,
        "status_label": str((calc or {}).get("workspace_status_label") or status or "—"),
    }


def _content_metric(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    return _metric_payload(workspace, metric)


def _content_explain(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    payload = _metric_payload(workspace, metric)
    change = payload.get("change")
    qual_row = payload.get("qualitative") or {}
    numerical = "—"
    if change:
        metric_name = str(change.get("metric") or metric)
        driver_name = str(qual_row.get("numerical_driver") or "—")
        driver_change = str(qual_row.get("driver_change") or "—")
        if driver_name and driver_name != "—":
            numerical = (
                f"The main observed contribution was {driver_name} "
                f"({driver_change})."
            )
        else:
            numerical = (
                f"{metric_name} moved {change.get('change_display') or '—'} "
                f"from {change.get('from') or '—'} to {change.get('to') or '—'}."
            )
    return {
        "metric": metric,
        "numerical": numerical,
        "change": change,
        "catalyst": str(qual_row.get("catalyst") or "—"),
        "relationship": str(qual_row.get("relationship_label") or "—"),
        "relationship_code": str(qual_row.get("relationship") or "—"),
        "causality_note": str(qual_row.get("causality_note") or "—"),
        "student_explanation": str(qual_row.get("student_explanation") or ""),
        "evidence": str(qual_row.get("evidence") or ""),
        "has_qualitative": bool(qual_row),
        "is_blocked": payload.get("is_blocked"),
        "is_review": payload.get("is_review"),
    }


def _content_calculation(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    calc = _calc_metric(workspace, metric)
    if calc is None:
        return {
            "metric": metric,
            "available": False,
            "message": f"No deterministic calculation is available for {metric}.",
        }
    inputs = []
    for i in calc.get("inputs") or []:
        inputs.append({
            "metric": str(i.get("metric") or i.get("key") or "—"),
            "value": str(i.get("value") if i.get("value") is not None else (i.get("display_value") or "—")),
            "provenance": str(i.get("provenance_tier") or ""),
        })
    return {
        "metric": metric,
        "available": True,
        "formula": str(calc.get("formula") or "—"),
        "result": str(calc.get("display_value") or "—"),
        "status": str(calc.get("workspace_status_label") or calc.get("status") or "—"),
        "inputs": inputs,
        "note": str(calc.get("workspace_note") or ""),
        "reported_fact_value": str(calc.get("reported_fact_value") or ""),
        "reported_fact_source": str(calc.get("reported_fact_source") or ""),
        "student_input_used": bool(calc.get("student_input_used")),
    }


def _content_evidence(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    fields = _norm_evidence_fields(workspace, metric)
    qual_row = _qual_row(workspace, metric)
    if qual_row:
        for label, key in (
            ("Catalyst", "catalyst"), ("Relationship", "relationship_label"),
            ("Section", "section"), ("Confidence", "confidence"),
            ("Source", "source"), ("Page", "page"),
            ("Reporting period", "reporting_period"),
        ):
            v = qual_row.get(key)
            if v in (None, "", "—"):
                continue
            fields.append({"label": label, "value": str(v)})
        evidence_full = qual_row.get("evidence_full") or qual_row.get("evidence") or ""
        if evidence_full not in (None, "", "—"):
            fields.append({"label": "Evidence text", "value": str(evidence_full)})
    return {"metric": metric, "fields": fields, "has_evidence": bool(fields)}


def _content_drivers(workspace: Dict[str, Any]) -> Dict[str, Any]:
    driver = _driver(workspace)
    return {
        "observations": list(driver.get("observations") or []),
        "causes": [c for c in (driver.get("causes") or []) if c.get("target") != "—"],
        "metric_choices": _metric_choices(workspace, 4),
        "active": bool(driver.get("observations")),
        "qualitative_active": bool(_qual(workspace).get("rows")),
    }


def _content_qualitative(workspace: Dict[str, Any], metric: Optional[str]) -> Dict[str, Any]:
    rows = list(_qual(workspace).get("rows") or [])
    if metric:
        rows = [q for q in rows if str(q.get("metric")) == metric]
    return {
        "rows": rows,
        "metric": metric,
        "documents": sorted(_qual(workspace).get("documents") or []),
        "sections": sorted(_qual(workspace).get("sections") or []),
    }


def _content_comparison(workspace: Dict[str, Any], area: Optional[str]) -> Dict[str, Any]:
    comp = _comparison(workspace)
    rows = _comparison_rows(workspace, area)
    return {
        "active": bool(comp.get("active")),
        "company_a": str(comp.get("company_a") or "Company A"),
        "company_b": str(comp.get("company_b") or "—"),
        "rows": rows,
        "area": area,
        "review_rows": len(comp.get("review_rows") or []),
        "areas": [
            {"id": aid, "label": label}
            for aid, label, _metrics in _COMPARISON_AREAS
        ],
    }


def _content_external(workspace: Dict[str, Any]) -> Dict[str, Any]:
    variables = list((workspace or {}).get("external_variables") or [])
    return {
        "variables": variables,
        "count": len(variables),
        "note": (
            "Student-entered values are always labeled STUDENT_INPUT (🟡) and "
            "are passed explicitly into the Formula Engine — they are never "
            "treated as document-verified evidence."
        ),
    }


def _content_excel(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sheets": [
            "Financial Data", "Ratio Analysis", "External Variables",
            "Comparison", "Driver Analysis", "Assignment Requirements",
            "Qualitative Drivers",
        ],
        "ready": True,
    }


def _content_memo(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company": str((workspace or {}).get("company") or "Company A"),
        "hint": (
            "The memo is a deterministic rendering of the verified workspace — "
            "every figure stays clickable so you can verify its source."
        ),
    }


def _content_conclusion(workspace: Dict[str, Any]) -> Dict[str, Any]:
    checklist: List[str] = []
    obs = _strongest_changes(workspace, 4)
    if obs:
        for o in obs:
            checklist.append(
                f"Profitability / trend: {o.get('metric')} {o.get('change_display')} "
                f"({o.get('from')} to {o.get('to')})."
            )
    else:
        checklist.append(
            "Profitability / trend: no multi-period evidence in the current set."
        )
    req_by = {str(r.get("requirement")): r for r in _req_rows(workspace)}
    for label, key in (
        ("Liquidity", "Current Ratio"),
        ("Leverage", "Debt to Equity"),
        ("Cash flow", "Operating Cash Flow"),
    ):
        r = req_by.get(key)
        if r and r.get("result") not in (None, "", "—"):
            checklist.append(f"{label}: {key} {r.get('result')}.")
        else:
            checklist.append(f"{label}: {key} not established from available evidence.")
    comp = _comparison(workspace)
    if comp.get("active") and (comp.get("rows") or []):
        checklist.append(
            f"Peer comparison: {len(comp['rows'])} canonical metric(s) compared "
            f"vs {comp.get('company_b')}."
        )
    blocked = _blocked_metrics(workspace) + _review_required_metrics(workspace)
    if blocked:
        checklist.append(
            f"Risks / gaps: {len(blocked)} item(s) blocked or review-required "
            f"({', '.join(blocked[:3])})."
        )
    qual_rows = list(_qual(workspace).get("rows") or [])
    if qual_rows:
        est = sum(1 for q in qual_rows if q.get("relationship") in (
            "EXPLICITLY_DISCLOSED", "EVIDENCE_SUPPORTED"))
        checklist.append(
            f"Qualitative catalysts: {len(qual_rows)} evidence-classified "
            f"driver-catalyst relationship(s) ({est} evidence-backed)."
        )
    return {"checklist": checklist, "never_generate": True}


# ---------------------------------------------------------------------------
# Stage messages
# ---------------------------------------------------------------------------


def _message_for(stage: str, workspace: Dict[str, Any], metric: Optional[str], area: Optional[str]) -> str:
    if stage == STAGE_OPENING:
        c = _content_opening(workspace)
        req_names = ", ".join(c["requirements"]) if c["requirements"] else "no parsed requirements"
        lines = [
            "I've reviewed your assignment.",
            f"You need to work with: {req_names}.",
        ]
        if c["periods"]:
            lines.append(
                f"I found the periods {', '.join(c['periods'])} in the verified evidence."
            )
        if c["comparison_active"]:
            lines.append("A peer comparison is available for this assignment.")
        if c["review_count"] or c["blocked_count"] or c["conflict_count"]:
            flags = []
            if c["review_count"]:
                flags.append(f"{c['review_count']} item(s) flagged review-required")
            if c["blocked_count"]:
                flags.append(f"{c['blocked_count']} item(s) blocked")
            if c["conflict_count"]:
                flags.append(f"{c['conflict_count']} item(s) with conflicting evidence")
            lines.append(
                "Heads up: " + "; ".join(flags) +
                " — I'll show you those when they matter instead of burying them."
            )
        lines.append("Let's start with what you need to do. What would you like to work on first?")
        return " ".join(lines)
    if stage == STAGE_REQUIREMENTS:
        c = _content_requirements(workspace)
        if c["total"] == 0:
            return (
                "I couldn't parse any requirement from the assignment brief yet. "
                "Add the requirements above and I'll build the checklist."
            )
        flags = []
        if c["review_count"]:
            flags.append(f"{c['review_count']} needs review")
        if c["blocked_count"]:
            flags.append(f"{c['blocked_count']} is blocked")
        tail = f" 🟠 {c['review_count']} item(s) need review and 🔴 {c['blocked_count']} item(s) are blocked." if flags else (
            " Every requirement resolves to a verified or derived result."
        )
        return f"Here is your assignment checklist — {c['total']} requirement(s).{tail}"
    if stage == STAGE_PERIODS:
        c = _content_periods(workspace)
        if not c["has_periods"]:
            return (
                "Your assignment doesn't need a period comparison, or the current "
                "evidence set has no multi-period data. The strongest verified "
                "results are still available."
            )
        return (
            "Let's work with the years. I found the period data for "
            + ", ".join(c["periods"])
            + ". The strongest verified changes are below — which would you like to investigate?"
        )
    if stage == STAGE_METRIC:
        p = _metric_payload(workspace, metric or "")
        if p["is_blocked"]:
            return (
                f"{metric} cannot currently be calculated. Required inputs are "
                "missing from the verified evidence, so I won't invent a value. "
                "You can review what's missing or continue with the items that work."
            )
        if p["is_review"]:
            return (
                f"I found an accounting label related to {metric} that I couldn't "
                "normalize safely. I won't merge it automatically because doing so "
                "could change your analysis."
            )
        change_txt = ""
        change = p.get("change")
        if change:
            change_txt = (
                f" It moved {change.get('change_display') or '—'} from "
                f"{change.get('from') or '—'} to {change.get('to') or '—'}."
            )
        return (
            f"{metric} is {p.get('value')} ({p.get('status_label')}).{change_txt} "
            "What would you like to do with it?"
        )
    if stage == STAGE_EXPLAIN:
        c = _content_explain(workspace, metric or "")
        if c["is_blocked"] or c["is_review"]:
            return (
                f"{metric} has no verified numerical foundation, so I can't offer "
                "a reliable explanation. Review the evidence or move on."
            )
        if c["numerical"] != "—" and c["numerical"]:
            msg = c["numerical"] + " "
        else:
            msg = ""
        if c["relationship_code"] in ("EXPLICITLY_DISCLOSED", "EVIDENCE_SUPPORTED"):
            msg += (
                f"The filing evidence discusses {c['catalyst'] or 'relevant factors'}. "
                f"{c['causality_note']}"
            )
        elif c["relationship_code"] in ("POSSIBLE_RELATIONSHIP", "INSUFFICIENT_EVIDENCE"):
            msg += (
                f"The evidence relevant to {c['catalyst'] or 'possible drivers'} is "
                f"{c['relationship'].lower()}. {c['causality_note']}"
            )
        else:
            msg += "Cause not established from permitted evidence."
        msg += " The explanation is evidence-first — I never invent a cause."
        return msg
    if stage == STAGE_CALCULATION:
        c = _content_calculation(workspace, metric or "")
        if not c["available"]:
            return c["message"]
        return (
            f"{metric} is calculated deterministically by the Formula Engine "
            f"(C++ when available): {c['formula']} = {c['result']}. "
            "The engine, not the UI, is the calculation authority."
        )
    if stage == STAGE_EVIDENCE:
        c = _content_evidence(workspace, metric or "")
        if not c["has_evidence"]:
            return (
                f"No provenance fields are available for {metric} from the "
                "verified evidence set — I don't fabricate sources."
            )
        return (
            f"Here is the verified evidence for {metric}. Every field comes from "
            "the extraction/verification pipeline — nothing is invented."
        )
    if stage == STAGE_DRIVERS:
        c = _content_drivers(workspace)
        if not c["active"]:
            return (
                "There's no period-over-period data to drive a driver analysis. "
                "Cause not established from available evidence."
            )
        msg = (
            "These are the verified period-over-period movements. A cause is only "
            "stated when the components are present and internally consistent — "
            "otherwise it stays 'cause not established'."
        )
        if c["qualitative_active"]:
            msg += " I also found narrative evidence that may explain some of these moves."
        return msg
    if stage == STAGE_QUALITATIVE:
        c = _content_qualitative(workspace, metric)
        if not c["rows"]:
            return (
                "No qualitative catalyst evidence is available for the current "
                "metric — cause not established from permitted evidence."
            )
        return (
            "Here is the evidence-classified driver-catalyst analysis. "
            "🟡/🟠/🔴 relationships are never presented as established facts — "
            "your judgment is required."
        )
    if stage == STAGE_COMPARISON:
        c = _content_comparison(workspace, area)
        if not c["active"]:
            return (
                "Peer comparison is not applicable right now — no comparable "
                "second-company evidence is available, so I won't force one."
            )
        if not c["rows"]:
            return (
                f"No comparable canonical metrics could be aligned for "
                f"{c['company_b']} in that area. Missing inputs stay blocked — "
                "the comparison is never forced."
            )
        return (
            f"Here's the {c['company_a']} vs {c['company_b']} comparison"
            + (f" ({area})" if area else "")
            + ". Rows where one side can't be normalized safely are excluded."
        )
    if stage == STAGE_EXTERNAL:
        c = _content_external(workspace)
        if c["count"] == 0:
            return (
                "No external variables have been entered. If a calculation needs "
                "values that aren't in the filings (e.g. risk-free rate, beta), "
                "enter them here — they'll be marked STUDENT_INPUT and passed "
                "explicitly into the Formula Engine."
            )
        return (
            f"{c['count']} external variable(s) are in play. They are "
            "student-entered data (🟡 STUDENT_INPUT), never document evidence."
        )
    if stage == STAGE_EXCEL:
        return (
            "Your working model is ready. It contains the professional 7-sheet "
            "workbook with real Excel formulas — Financial Data, Ratio Analysis, "
            "External Variables, Comparison, Driver Analysis, Assignment "
            "Requirements and Qualitative Drivers. Download it, review it, or "
            "continue to the memo."
        )
    if stage == STAGE_MEMO:
        return (
            "Here is your Student Memo — a deterministic rendering of the verified "
            "workspace. Click any metric to open its evidence card."
        )
    if stage == STAGE_CONCLUSION:
        return (
            "You've completed the evidence and analysis stages. Now write your "
            "conclusion. I provide a checklist of facts to consider, but I never "
            "write the conclusion — the judgment is yours."
        )
    return "What would you like to do next?"


# ---------------------------------------------------------------------------
# Choices per stage
# ---------------------------------------------------------------------------


def _back_choice(stage: str) -> Dict[str, Any]:
    return {"id": "back", "label": "← Back", "hint": "Return to the previous step."}


def _continue_choice() -> Dict[str, Any]:
    return {"id": "continue", "label": "Continue", "hint": "Move to the next useful step."}


def _skip_choice() -> Dict[str, Any]:
    return {"id": "skip", "label": "Skip", "hint": "Skip this step for now."}


def _explore_choice() -> Dict[str, Any]:
    return {"id": "explore", "label": "Explore workspace", "hint": "Open the full workspace when you want it."}


def _choices_for(stage: str, workspace: Dict[str, Any], metric: Optional[str], area: Optional[str]) -> List[Dict[str, Any]]:
    choices: List[Dict[str, Any]] = []
    if stage == STAGE_OPENING:
        choices.append({
            "id": "opening.requirements", "label": "Show me what the assignment requires",
            "hint": "Review the parsed requirement checklist.",
        })
        c = _content_opening(workspace)
        if c["has_periods"]:
            choices.append({
                "id": "opening.periods", "label": "Start with FY analysis",
                "hint": "Review the period data and strongest changes.",
            })
        if c["comparison_active"]:
            choices.append({
                "id": "opening.comparison", "label": "Start with company comparison",
                "hint": "Compare with the peer company.",
            })
    elif stage == STAGE_REQUIREMENTS:
        choices.append({
            "id": "requirements.continue", "label": "Continue to results",
            "hint": "See the strongest verified results.",
        })
        if _review_required_metrics(workspace) or _blocked_metrics(workspace):
            choices.append({
                "id": "requirements.review", "label": "Review details",
                "hint": "Open the full checklist with evidence details.",
            })
        choices.append(_back_choice(stage))
    elif stage == STAGE_PERIODS:
        for mc in _metric_choices(workspace, 4):
            choices.append(mc)
        if not _period_list(workspace):
            choices.append(_continue_choice())
        choices.append(_back_choice(stage))
        choices.append(_skip_choice())
    elif stage == STAGE_METRIC:
        p = _metric_payload(workspace, metric or "")
        if p["is_blocked"]:
            choices.append({
                "id": "metric.review", "label": "View missing evidence",
                "hint": "See what's missing for this metric.",
            })
        elif p["is_review"]:
            choices.append({
                "id": "metric.review", "label": "Review it",
                "hint": "See why the label couldn't be normalized safely.",
            })
        if p["has_explain"] and not p["is_blocked"]:
            choices.append({
                "id": "metric.explain", "label": "Explain this",
                "hint": "Evidence-first explanation of the change.",
            })
        if p["has_calculation"] and not p["is_blocked"]:
            choices.append({
                "id": "metric.calculation", "label": "Show calculation",
                "hint": "Formula-Engine formula and inputs.",
            })
        if p["has_evidence"]:
            choices.append({
                "id": "metric.evidence", "label": "Verify the evidence",
                "hint": "Provenance fields for this metric.",
            })
        if _comparison(workspace).get("active"):
            choices.append({
                "id": "metric.comparison", "label": "Compare with peer",
                "hint": "Compare this metric with the peer company.",
            })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_EXPLAIN:
        c = _content_explain(workspace, metric or "")
        choices.append({
            "id": "explain.evidence", "label": "Show evidence",
            "hint": "Open the provenance fields.",
        })
        if not c["is_blocked"]:
            choices.append({
                "id": "explain.calculation", "label": "Show calculation",
                "hint": "Formula and inputs from the Formula Engine.",
            })
        if c["has_qualitative"] and not c["is_blocked"] and c["relationship_code"] not in (
            "CAUSE_NOT_ESTABLISHED", "INSUFFICIENT_EVIDENCE",
        ):
            choices.append({
                "id": "explain.qualitative", "label": "Investigate the driver",
                "hint": "Open the catalyst analysis for this metric.",
            })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_CALCULATION:
        choices.append({
            "id": "calculation.evidence", "label": "Show source evidence",
            "hint": "Where the inputs come from.",
        })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_EVIDENCE:
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_DRIVERS:
        c = _content_drivers(workspace)
        if c["qualitative_active"]:
            choices.append({
                "id": "drivers.qualitative", "label": "Investigate why",
                "hint": "Evidence-classified qualitative catalysts.",
            })
            choices.append({
                "id": "drivers.numerical", "label": "Use the numerical explanation only",
                "hint": "Component-based driver statement, no narrative claims.",
            })
        if _comparison(workspace).get("active"):
            choices.append({
                "id": "drivers.comparison", "label": "Compare with peer",
                "hint": "Move to the peer comparison.",
            })
        choices.append({
            "id": "drivers.excel", "label": "Open the working model",
            "hint": "Jump to the Excel deliverable.",
        })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_QUALITATIVE:
        choices.append({
            "id": "qualitative.evidence", "label": "View evidence",
            "hint": "Open the source snippet for the catalyst.",
        })
        if _comparison(workspace).get("active"):
            choices.append({
                "id": "qualitative.comparison", "label": "Compare with peer",
                "hint": "Move to the peer comparison.",
            })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_COMPARISON:
        c = _content_comparison(workspace, area)
        if c["rows"]:
            for a in c["areas"]:
                choices.append({
                    "id": f"comparison.area.{a['id']}", "label": f"Compare {a['label']}",
                    "hint": f"Filter the comparison to {a['label'].lower()} metrics.",
                })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_EXTERNAL:
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_EXCEL:
        choices.append({
            "id": "excel.download", "label": "Download Excel",
            "hint": "Export the 7-sheet working model.",
        })
        choices.append(_continue_choice())
        choices.append(_back_choice(stage))
    elif stage == STAGE_MEMO:
        choices.append({
            "id": "memo.conclusion", "label": "Go to the conclusion",
            "hint": "Write your own conclusion next.",
        })
        choices.append(_back_choice(stage))
    elif stage == STAGE_CONCLUSION:
        choices.append(_explore_choice())
    else:
        choices.append(_continue_choice())
    return choices


# ---------------------------------------------------------------------------
# Next-step recommendation (the central interaction)
# ---------------------------------------------------------------------------


def _recommended_choice(state: Dict[str, Any], workspace: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    stage = state.get("stage")
    metric = state.get("metric")
    if stage == STAGE_OPENING:
        if _period_list(workspace):
            return {"id": "opening.periods", "label": "Start with the period analysis", "hint": "Review FY changes first — your assignment asks for them."}
        return {"id": "opening.requirements", "label": "Review the assignment requirements", "hint": "See what the assignment asks for."}
    if stage == STAGE_REQUIREMENTS:
        return {"id": "requirements.continue", "label": "Continue to the results", "hint": "See the strongest verified results next."}
    if stage == STAGE_PERIODS:
        mc = _metric_choices(workspace, 1)
        if mc:
            return {"id": mc[0]["id"], "label": f"Investigate {mc[0]['label']}", "hint": mc[0]["hint"]}
        return {"id": "continue", "label": "Continue", "hint": "Move on to the results."}
    if stage == STAGE_METRIC:
        p = _metric_payload(workspace, metric or "")
        if p["is_blocked"] or p["is_review"]:
            return {"id": "metric.review", "label": "Review what's missing", "hint": "See why this metric can't be finalized yet."}
        if p["has_explain"]:
            return {"id": "metric.explain", "label": "Explain this change", "hint": "Evidence-first explanation of the move."}
        if p["has_calculation"]:
            return {"id": "metric.calculation", "label": "Show the calculation", "hint": "Formula and inputs."}
        return {"id": "metric.evidence", "label": "Verify the evidence", "hint": "Provenance fields."}
    if stage == STAGE_EXPLAIN:
        return {"id": "explain.evidence", "label": "Show the evidence", "hint": "Check the source fields."}
    if stage == STAGE_CALCULATION:
        return {"id": "calculation.evidence", "label": "Show source evidence", "hint": "Where the inputs come from."}
    if stage == STAGE_EVIDENCE:
        return {"id": "continue", "label": "Continue", "hint": "Move to the driver analysis."}
    if stage == STAGE_DRIVERS:
        if bool(_qual(workspace).get("rows")):
            return {"id": "drivers.qualitative", "label": "Investigate why the numbers moved", "hint": "Evidence-classified catalysts."}
        return {"id": "continue", "label": "Continue", "hint": "Move to the next step."}
    if stage == STAGE_QUALITATIVE:
        return {"id": "continue", "label": "Continue", "hint": "Move to the comparison or Excel."}
    if stage == STAGE_COMPARISON:
        return {"id": "continue", "label": "Continue", "hint": "Move to the Excel working model."}
    if stage == STAGE_EXTERNAL:
        return {"id": "continue", "label": "Continue", "hint": "Proceed with the analysis."}
    if stage == STAGE_EXCEL:
        return {"id": "excel.download", "label": "Download the Excel model", "hint": "Export the 7-sheet working model."}
    if stage == STAGE_MEMO:
        return {"id": "memo.conclusion", "label": "Go to your conclusion", "hint": "Write the final judgment yourself."}
    if stage == STAGE_CONCLUSION:
        return None
    return {"id": "continue", "label": "Continue", "hint": "Proceed."}


def _alternative_choices(state: Dict[str, Any], workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage = state.get("stage")
    if stage == STAGE_CONCLUSION:
        return []
    all_choices = _choices_for(stage, workspace, state.get("metric"), state.get("area"))
    rec = _recommended_choice(state, workspace)
    rec_id = rec["id"] if rec else None
    alts = [c for c in all_choices if c["id"] != rec_id]
    # Prefer meaningful actions over Back/Skip for the alternatives slot.
    meaningful = [c for c in alts if c["id"] not in ("back", "skip", "continue", "explore")]
    rest = [c for c in alts if c not in meaningful]
    return (meaningful + rest)[:2]


# ---------------------------------------------------------------------------
# Deterministic transitions
# ---------------------------------------------------------------------------

_BACK_TARGET: Dict[str, str] = {
    STAGE_REQUIREMENTS: STAGE_OPENING,
    STAGE_PERIODS: STAGE_REQUIREMENTS,
    STAGE_METRIC: STAGE_PERIODS,
    STAGE_EXPLAIN: STAGE_METRIC,
    STAGE_CALCULATION: STAGE_METRIC,
    STAGE_EVIDENCE: STAGE_METRIC,
    STAGE_DRIVERS: STAGE_PERIODS,
    STAGE_QUALITATIVE: STAGE_DRIVERS,
    STAGE_COMPARISON: STAGE_PERIODS,
    STAGE_EXTERNAL: STAGE_METRIC,
    STAGE_EXCEL: STAGE_DRIVERS,
    STAGE_MEMO: STAGE_EXCEL,
    STAGE_CONCLUSION: STAGE_MEMO,
}

_CONTINUE_TARGET: Dict[str, str] = {
    STAGE_OPENING: STAGE_REQUIREMENTS,
    STAGE_REQUIREMENTS: STAGE_PERIODS,
    STAGE_PERIODS: STAGE_DRIVERS,
    STAGE_METRIC: STAGE_DRIVERS,
    STAGE_EXPLAIN: STAGE_DRIVERS,
    STAGE_CALCULATION: STAGE_EVIDENCE,
    STAGE_EVIDENCE: STAGE_DRIVERS,
    STAGE_DRIVERS: STAGE_EXCEL,
    STAGE_QUALITATIVE: STAGE_COMPARISON,
    STAGE_COMPARISON: STAGE_EXCEL,
    STAGE_EXTERNAL: STAGE_EXCEL,
    STAGE_EXCEL: STAGE_MEMO,
    STAGE_MEMO: STAGE_CONCLUSION,
    STAGE_CONCLUSION: STAGE_CONCLUSION,
}


def apply_choice(
    state: Dict[str, Any],
    choice_id: str,
    workspace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic state transition for a choice id. Unknown ids are
    ignored (fail-closed — the session never crashes or dead-ends)."""
    choice_id = str(choice_id or "")
    stage = state.get("stage")
    metric = state.get("metric")
    area = state.get("area")
    visited = _mark_visited(state, stage)

    def go(new_stage: str, new_metric=None, new_area=None) -> Dict[str, Any]:
        return {
            "stage": new_stage,
            "metric": new_metric,
            "area": new_area,
            "visited": _mark_visited({"visited": visited}, new_stage),
        }

    # Universal controls.
    if choice_id == "back":
        target = _BACK_TARGET.get(stage, STAGE_OPENING)
        return go(target, None if target in (STAGE_OPENING, STAGE_REQUIREMENTS, STAGE_PERIODS) else metric, None)
    if choice_id == "skip":
        target = _CONTINUE_TARGET.get(stage, STAGE_DRIVERS)
        return go(target, None if target in (STAGE_OPENING, STAGE_REQUIREMENTS, STAGE_PERIODS) else metric, None)
    if choice_id == "explore":
        return {**state, "stage": STAGE_EXPLORE_UI, "visited": visited}
    if choice_id == "continue":
        target = _CONTINUE_TARGET.get(stage, STAGE_DRIVERS)
        return go(target, metric if target in (STAGE_METRIC, STAGE_EXPLAIN, STAGE_CALCULATION, STAGE_EVIDENCE, STAGE_QUALITATIVE) else None, None)
    if choice_id == "explore.back":
        target = visited[-1] if visited else STAGE_OPENING
        return {**state, "stage": target, "visited": visited}

    # Stage-specific transitions.
    if choice_id.startswith("opening."):
        sub = choice_id.split(".", 1)[1]
        if sub == "requirements":
            return go(STAGE_REQUIREMENTS)
        if sub == "periods":
            return go(STAGE_PERIODS)
        if sub == "comparison":
            return go(STAGE_COMPARISON)
    if choice_id.startswith("requirements."):
        sub = choice_id.split(".", 1)[1]
        if sub == "continue":
            return go(STAGE_PERIODS if _period_list(workspace or {}) else STAGE_METRIC)
        if sub == "review":
            return {**state, "stage": STAGE_EXPLORE_UI, "visited": visited}
    if choice_id.startswith("period."):
        sub = choice_id.split(".", 1)[1]
        if sub == "continue":
            return go(STAGE_DRIVERS)
        return go(STAGE_METRIC, new_metric=sub)
    if choice_id.startswith("metric."):
        sub = choice_id.split(".", 1)[1]
        if sub == "explain":
            return go(STAGE_EXPLAIN, metric)
        if sub == "calculation":
            return go(STAGE_CALCULATION, metric)
        if sub == "evidence":
            return go(STAGE_EVIDENCE, metric)
        if sub == "comparison":
            return go(STAGE_COMPARISON, None, None)
        if sub == "review":
            return go(STAGE_EVIDENCE, metric)
    if choice_id.startswith("explain."):
        sub = choice_id.split(".", 1)[1]
        if sub == "evidence":
            return go(STAGE_EVIDENCE, metric)
        if sub == "calculation":
            return go(STAGE_CALCULATION, metric)
        if sub == "qualitative":
            return go(STAGE_QUALITATIVE, metric)
    if choice_id.startswith("calculation."):
        sub = choice_id.split(".", 1)[1]
        if sub == "evidence":
            return go(STAGE_EVIDENCE, metric)
    if choice_id.startswith("drivers."):
        sub = choice_id.split(".", 1)[1]
        if sub == "qualitative":
            return go(STAGE_QUALITATIVE, None)
        if sub == "numerical":
            return go(STAGE_DRIVERS, metric, None)
        if sub == "comparison":
            return go(STAGE_COMPARISON, None, None)
        if sub == "excel":
            return go(STAGE_EXCEL)
    if choice_id.startswith("qualitative."):
        sub = choice_id.split(".", 1)[1]
        if sub == "evidence":
            return go(STAGE_EVIDENCE, metric)
        if sub == "comparison":
            return go(STAGE_COMPARISON, None, None)
    if choice_id.startswith("comparison.area."):
        return go(STAGE_COMPARISON, None, choice_id.split(".", 2)[2])
    if choice_id == "excel.download":
        return go(STAGE_EXCEL)
    if choice_id == "memo.conclusion":
        return go(STAGE_CONCLUSION)

    return dict(state)


# ---------------------------------------------------------------------------
# Progress indicator
# ---------------------------------------------------------------------------


def agent_progress(workspace: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, str]]:
    """Progress rows: ✓ Requirements, ✓ Financial data, ... → current, ○ todo."""
    visited = set(state.get("visited") or [])
    stage = state.get("stage")
    current_progress = _STAGE_PROGRESS.get(stage)

    done_ids: List[str] = []
    if stage == STAGE_EXPLORE_UI:
        # Exploring the workspace: everything previously visited stays done.
        pass
    for visited_stage in visited:
        pid = _STAGE_PROGRESS.get(visited_stage)
        if pid and pid not in done_ids:
            done_ids.append(pid)

    rows: List[Dict[str, str]] = []
    for p in PROGRESS_STAGES:
        pid = p["id"]
        if current_progress == pid:
            rows.append({"id": pid, "label": p["label"], "state": "current"})
        elif pid in done_ids or (stage in (STAGE_EXCEL, STAGE_MEMO) and pid != "conclusion"):
            rows.append({"id": pid, "label": p["label"], "state": "done"})
        else:
            rows.append({"id": pid, "label": p["label"], "state": "todo"})
    return rows


# ---------------------------------------------------------------------------
# Public session view
# ---------------------------------------------------------------------------


def agent_session(
    workspace: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    facts_src: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic full view of the Assignment Agent for the current
    state: {state, stage, message, content, choices, recommended,
    alternatives, progress, guidance}."""
    state = dict(state or initial_state())
    stage = state.get("stage")
    metric = state.get("metric")
    area = state.get("area")

    # Guard: a metric stage without a metric never crashes — it falls back
    # to the periods stage (fail-closed).
    if stage == STAGE_METRIC and not metric:
        stage = STAGE_PERIODS
        state = {**state, "stage": stage}

    if stage == STAGE_EXPLORE_UI:
        return {
            "state": state,
            "stage": stage,
            "explore": True,
            "message": "You're in the full workspace — everything remains available.",
            "content": {},
            "choices": [],
            "recommended": None,
            "alternatives": [],
            "progress": agent_progress(workspace, state),
            "guidance": {},
        }

    content: Dict[str, Any] = {}
    if stage == STAGE_OPENING:
        content = _content_opening(workspace)
    elif stage == STAGE_REQUIREMENTS:
        content = _content_requirements(workspace)
    elif stage == STAGE_PERIODS:
        content = _content_periods(workspace)
    elif stage == STAGE_METRIC:
        content = _content_metric(workspace, metric)
    elif stage == STAGE_EXPLAIN:
        content = _content_explain(workspace, metric)
    elif stage == STAGE_CALCULATION:
        content = _content_calculation(workspace, metric)
    elif stage == STAGE_EVIDENCE:
        content = _content_evidence(workspace, metric)
    elif stage == STAGE_DRIVERS:
        content = _content_drivers(workspace)
    elif stage == STAGE_QUALITATIVE:
        content = _content_qualitative(workspace, metric)
    elif stage == STAGE_COMPARISON:
        content = _content_comparison(workspace, area)
    elif stage == STAGE_EXTERNAL:
        content = _content_external(workspace)
    elif stage == STAGE_EXCEL:
        content = _content_excel(workspace)
    elif stage == STAGE_MEMO:
        content = _content_memo(workspace)
    elif stage == STAGE_CONCLUSION:
        content = _content_conclusion(workspace)

    guidance: Dict[str, Any] = {}
    if stage in (STAGE_METRIC, STAGE_EXPLAIN, STAGE_EVIDENCE, STAGE_CALCULATION) and metric:
        status = _metric_status(workspace, metric)
        if status == "BLOCKED":
            guidance = {
                "kind": "blocked",
                "metric": metric,
                "message": (
                    f"{metric} is blocked: required inputs are missing from the "
                    "verified evidence. I won't guess a value."
                ),
            }
        elif status == "REVIEW_REQUIRED":
            guidance = {
                "kind": "review",
                "metric": metric,
                "message": (
                    f"{metric} is flagged review-required — the label or "
                    "extraction couldn't be normalized safely."
                ),
            }
    conflicts = _conflict_metrics(workspace, facts_src)
    if conflicts:
        guidance.setdefault("conflicts", conflicts)
        guidance["conflict_message"] = (
            "I found conflicting values for "
            + ", ".join(conflicts[:3])
            + " — I won't silently choose one."
        )

    return {
        "state": state,
        "stage": stage,
        "explore": False,
        "message": _message_for(stage, workspace, metric, area),
        "content": content,
        "choices": _choices_for(stage, workspace, metric, area),
        "recommended": _recommended_choice(state, workspace),
        "alternatives": _alternative_choices(state, workspace),
        "progress": agent_progress(workspace, state),
        "guidance": guidance,
        "conflict_metrics": conflicts,
    }


# Convenience alias so UI/tests can ask "what should I do next?" explicitly.
def what_next(
    workspace: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The central interaction: ONE recommended action plus 1-2 alternatives."""
    view = agent_session(workspace, state)
    return {
        "recommended": view["recommended"],
        "alternatives": view["alternatives"],
        "stage": view["stage"],
    }
