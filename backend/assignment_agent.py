"""
Platrixa
Sprint 12.1 - Zero-Panic Assignment Onboarding & Excel Guidance

* The Assignment Agent NEVER presents an ambiguous assignment parse as a
  system failure. Parsing confidence is classified deterministically into
  three recovery states:
      high    -> clean assignment, direct "Continue to analysis"
      partial -> most requirements confirmed, one item needs confirmation
      low     -> nothing reliably identified, manual requirement selector
* Unknown metric-like tokens in the assignment text (ROIC, Quick Ratio,
  EPSILON...) are SURFACED for student confirmation, never silently
  invented and never silently discarded.
* The Excel working model is introduced with orientation guidance:
  "Start with Ratio Analysis, then Financial Data" — the model itself is
  unchanged (7 sheets, real formulas, professional formatting).
* The conclusion remains student-authored; the agent may only offer
  evidence-backed scaffolding, never a generated conclusion.

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

import re
from typing import Any, Dict, List, Optional

from backend.qualitative_catalyst import RELATIONSHIP_LABELS
from backend.student_workspace import CANONICAL_METRICS, canonicalize_metric

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
STAGE_EXTERNAL = "external"  # probe-top
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

# Sprint 13 - seven tutor steps matching the guided student journey:
# Assignment -> Financial data -> Trends -> Why it changed -> Evidence ->
# Working model -> Student conclusion.
AGENT_STEPS: List[Dict[str, str]] = [
    {"id": "assignment", "label": "Assignment"},
    {"id": "financials", "label": "Financial data"},
    {"id": "trends", "label": "Trends"},
    {"id": "why", "label": "Why it changed"},
    {"id": "evidence", "label": "Evidence"},
    {"id": "model", "label": "Working model"},
    {"id": "conclusion", "label": "Student conclusion"},
]

_STAGE_STEP: Dict[str, int] = {
    STAGE_OPENING: 0,
    STAGE_REQUIREMENTS: 0,
    STAGE_PERIODS: 1,
    STAGE_EXTERNAL: 1,
    STAGE_METRIC: 2,
    STAGE_EXPLAIN: 3,
    STAGE_CALCULATION: 3,
    STAGE_DRIVERS: 3,
    STAGE_QUALITATIVE: 3,
    STAGE_COMPARISON: 3,
    STAGE_EVIDENCE: 4,
    STAGE_EXCEL: 5,
    STAGE_MEMO: 5,
    STAGE_CONCLUSION: 6,
}


def agent_step(stage: Optional[str]) -> Dict[str, Any]:
    """Deterministic muted 'Step N of 7' tutor indicator for the current stage."""
    idx = _STAGE_STEP.get(stage, 0)
    step = AGENT_STEPS[idx]
    return {
        "number": idx + 1,
        "label": step["label"],
        "total": len(AGENT_STEPS),
        "id": step["id"],
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
# Sprint 13 - student-facing confidence & status language
# ---------------------------------------------------------------------------

CONFIDENCE_CLEAR = "clear"
CONFIDENCE_CONFIRM = "needs_confirmation"
CONFIDENCE_UNKNOWN = "cannot_determine"

# Sprint 13 - every requirement carries an internal confidence/state; the
# student only ever sees the friendly one-liner, never parser internals.
_STATUS_STUDENT_LANGUAGE: Dict[str, str] = {
    "VERIFIED": "Directly supported by the evidence.",
    "DERIVED": "Calculated from verified figures.",
    "EXTERNAL_DERIVED": "Calculated using a student-entered external value.",
    "STUDENT_INPUT": "You entered this value yourself.",
    "REVIEW_REQUIRED": "I found a possible figure, but the accounting label or structure is ambiguous. Please verify it before using it.",
    "BLOCKED": "I don't have enough verified information to calculate this safely.",
    "UNANALYZED": "No verified information is available for this item yet.",
}


def _status_student_language(status: str) -> str:
    return _STATUS_STUDENT_LANGUAGE.get(str(status or ""), "")


def _missing_facts_for(workspace: Dict[str, Any], metric: str) -> List[str]:
    """Deterministic names of the missing/unreliable inputs for one metric,
    used by the tutor-style blocked explanation. Never guesses."""
    out: List[str] = []
    calc = _calcs(workspace).get(metric)
    if calc:
        for i in (calc.get("inputs") or []):
            v = i.get("value")
            if v in (None, "", "—"):
                name = str(i.get("metric") or i.get("key") or "")
                if name and name not in out:
                    out.append(name)
    if len(out) < 3:
        m_l = str(metric).lower()
        for section in ("financial_data", "ratios"):
            for item in ((workspace or {}).get("missing_data") or {}).get(section) or []:
                s = str(item or "")
                if s and (s.lower() in m_l or m_l in s.lower()):
                    if s not in out:
                        out.append(s)
    return out[:3]


# ---------------------------------------------------------------------------
# Sprint 12.1 - Zero-panic parse recovery (high / partial / low)
# ---------------------------------------------------------------------------

PARSE_HIGH = "high"
PARSE_PARTIAL = "partial"
PARSE_LOW = "low"

# Tokens that commonly appear in assignment prose but are NEVER treated as
# metric requirements (professor filler, document words, units...).
_NOISE_TOKENS: set = {
    "analysis", "analyst", "assignment", "calculate", "compute", "please",
    "explain", "compare", "comparison", "review", "report", "memo",
    "worksheet", "worksheet", "excel", "model", "the", "and", "for",
    "with", "from", "into", "across", "using", "between", "under",
    "over", "during", "based", "assuming", "include", "including",
    "must", "should", "need", "needs", "required", "required", "show",
    "find", "derive", "determine", "evaluate", "assess", "company",
    "fiscal", "year", "years", "period", "periods", "statement",
    "statements", "annual", "quarterly", "quarter", "quarters",
    "professor", "course", "class", "peer", "peerco", "inc", "ltd",
    "llc", "corp", "group", "holdings", "plc", "fy", "usd", "inr",
    "crore", "lakh", "million", "billion", "thousand", "bn", "mm",
    "figures", "numbers", "data", "net", "total", "gross", "operating",
}

# Metric-like suffix phrases: "quick ratio", "inventory turnover"...
_METRIC_SUFFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9 .'&/()]{1,28}?)\s+"
    r"(ratio|margin|turnover|coverage|yield|gearing|multiple|per share)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Standalone uppercase acronyms: ROIC, D/E, WACC... (never FY/ROE if already
# confirmed, and never noise words).
_ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6}(?:/[A-Z]{1,4})?)(?![A-Za-z0-9])")


# Common assignment instruction words that can precede a metric phrase.
_INSTRUCTION_WORDS = frozenset({
    "calculate", "compute", "find", "determine", "estimate", "assess",
    "analyze", "analyse", "evaluate", "show", "give", "provide", "derive",
    "identify", "consider", "explain", "state", "include", "cover", "review",
    "perform", "produce", "use", "apply", "measure", "check", "verify",
    "please", "the", "also", "then", "and", "over", "for", "of",
})

# Words that introduce a company name in assignment prose ("Analyze company
# XYZ...", "the firm MSFT..."). An uppercase token directly after one of
# these is the company the assignment is about — never a metric requirement
# (Sprint 12.1: a WhatsApp-style brief that names the company must produce a
# clean requirement confirmation, not an "is XYZ a requirement?" prompt).
_COMPANY_WORDS = frozenset({
    "company", "firm", "corporation", "corp", "inc", "ltd", "llc", "plc",
    "limited", "holdings", "holding", "group", "conglomerate",
    "enterprise", "enterprises", "industries", "partners", "solutions",
    "technologies", "co", "business", "businesses",
})


def _follows_company_word(text: str, pos: int) -> bool:
    """True when the word immediately before `pos` in `text` is a
    company-name introducer (word-boundary safe: "company's ROE" is not
    treated as a company reference)."""
    m = re.search(r"([A-Za-z]+)[^A-Za-z]*$", text[:pos])
    if not m:
        return False
    return m.group(1).lower() in _COMPANY_WORDS


def _uncertain_tokens(requirements_text: str, confirmed: List[str]) -> List[str]:
    """Deterministic detection of metric-like assignment tokens that were
    NOT parsed into a confirmed requirement. Never guesses: only uppercase
    acronyms, metric-suffix phrases, or known ambiguous labels are surfaced
    for the student to confirm/correct. Max 4, sorted, deduplicated."""
    text = requirements_text or ""
    if not text.strip():
        return []
    confirmed_l = {str(c).lower() for c in confirmed}
    out: List[str] = []

    def add(tok: str) -> None:
        tok = tok.strip()
        t = tok.lower()
        if not tok or len(tok) > 40:
            return
        if t in confirmed_l or tok in confirmed:
            return
        if t in _NOISE_TOKENS:
            return
        # A token that canonicalizes to a supported metric is a confirmed
        # requirement (or one the parser already resolved) — not uncertain.
        canonical, _conf, _reason = canonicalize_metric(tok)
        if canonical:
            if canonical.lower() in confirmed_l:
                return
        # Uppercase fragments that belong to an already-confirmed metric
        # label ("PROFIT"/"MARGIN" inside "PROFIT MARGIN") are not new
        # requirements — never surface them for confirmation.
        if tok.isupper() and any(
            t in set(re.findall(r"[a-z0-9]{2,}", cl))
            for cl in confirmed_l
        ):
            return
        if any(x.lower() == t for x in out):
            return
        out.append(tok)

    # 1) uppercase acronyms in the original (case-preserved) text
    for m in _ACRONYM_RE.finditer(text):
        # "Analyze company XYZ ..." — an acronym that directly follows a
        # company-name word is the company, not a metric requirement. It is
        # never surfaced for confirmation (Sprint 12.1 WhatsApp copy).
        if _follows_company_word(text, m.start()):
            continue
        add(m.group(1))
    # 2) metric-suffix phrases (e.g. "quick ratio", "inventory turnover").
    #    The regex can capture leading instruction words ("Calculate
    #    Operating Margin") or earlier metrics joined by "and" ("ROE and
    #    Quick Ratio"), so: drop instruction words, keep the trailing
    #    conjunction segment, and prefer the longest trailing sub-phrase
    #    that is itself a supported metric (already confirmed). Anything
    #    metric-like that remains is surfaced for the student to confirm.
    for m in _METRIC_SUFFIX_RE.finditer(text):
        phrase = " ".join((m.group(1) + " " + m.group(2)).strip().split())
        words = phrase.split()
        while len(words) > 2 and words[0].lower() in _INSTRUCTION_WORDS:
            words = words[1:]
        phrase = " ".join(words)
        segments = re.split(
            r"\s+(?:and|&|or|of|for|then|plus|with)\s+", phrase, flags=re.IGNORECASE
        )
        phrase = segments[-1].strip()
        words = phrase.split()
        best = phrase
        for i in range(len(words) - 1):
            tail = " ".join(words[i:])
            if canonicalize_metric(tail)[0]:
                best = tail
                break
        add(best)
    # 3) known ambiguous accounting labels (never silently merged); keep
    #    only the most specific labels so "segment gross margin" does not
    #    also emit its substring "gross margin".
    from backend.student_workspace import _AMBIGUOUS_LABELS
    present = [lbl for lbl in sorted(_AMBIGUOUS_LABELS, key=len, reverse=True)
               if re.search(rf"(?<![a-z0-9]){re.escape(lbl)}(?![a-z0-9])", text.lower())]
    for lbl in present:
        if not any(lbl in other for other in present if other != lbl):
            add(lbl)
    return sorted(out)[:4]


def confirmation_candidates(token: str) -> List[str]:
    """Deterministic canonical-metric suggestions for one uncertain
    assignment token (e.g. 'D/E' -> ['Debt to Equity'], 'Quick Ratio' ->
    ['Current Ratio']). Never guesses: only canonical metrics whose label
    shares a meaningful word with the token are offered, max 3."""
    tok = str(token or "").strip().lower()
    if not tok:
        return []
    canonical, _conf, _reason = canonicalize_metric(tok)
    if canonical:
        return [canonical]
    words = set(re.findall(r"[a-z0-9]{2,}", tok))
    if not words:
        return []
    out: List[str] = []
    for name in sorted(CANONICAL_METRICS.keys()):
        name_words = set(re.findall(r"[a-z0-9]{2,}", name.lower()))
        if name_words and (words & name_words):
            out.append(name)
        if len(out) >= 3:
            break
    return out


def parse_recovery(
    workspace: Dict[str, Any],
    requirements_text: str = "",
) -> Dict[str, Any]:
    """Deterministic classification of assignment-parse confidence.

    state
      high    every detected requirement is confirmed (clean assignment)
      partial some requirements confirmed, one item needs confirmation
      low     nothing was reliably identified -> manual selector
    """
    confirmed = [str(r.get("requirement") or "") for r in _req_rows(workspace)]
    confirmed = [c for c in confirmed if c]
    if not confirmed:
        return {
            "state": PARSE_LOW,
            "confirmed": [],
            "uncertain": [],
            "review_required": [],
        }
    confirmed_l = {c.lower() for c in confirmed}
    # Only review flags that belong to a confirmed assignment requirement make
    # the parse uncertain. An unrelated review-required fact (e.g. a flagged
    # Cash Flow line the professor never asked about) stays visible elsewhere
    # but does not block a clean assignment.
    review_required = [
        m for m in _review_required_metrics(workspace) if m.lower() in confirmed_l
    ]
    uncertain = _uncertain_tokens(requirements_text, confirmed)
    if uncertain or review_required:
        return {
            "state": PARSE_PARTIAL,
            "confirmed": confirmed,
            "uncertain": uncertain,
            "review_required": review_required,
        }
    return {
        "state": PARSE_HIGH,
        "confirmed": confirmed,
        "uncertain": [],
        "review_required": [],
    }


def _parse_summary(workspace: Dict[str, Any], requirements_text: str) -> str:
    """Deterministic one-line summary of the confirmed requirements for the
    high-confidence opening (e.g. "ROE, ROA and Profit Margin")."""
    confirmed = [str(r.get("requirement") or "") for r in _req_rows(workspace)]
    confirmed = [c for c in confirmed if c]
    if not confirmed:
        return "the required metrics"
    if len(confirmed) == 1:
        return confirmed[0]
    return ", ".join(confirmed[:-1]) + " and " + confirmed[-1]


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


def _content_requirements(workspace: Dict[str, Any], requirements_text: str = "") -> Dict[str, Any]:
    rec = parse_recovery(workspace, requirements_text)
    uncertain_set = {str(t) for t in rec["uncertain"]}
    review_set = {str(m) for m in rec["review_required"]}
    blocked_set = set(_blocked_metrics(workspace))
    rows = []
    for r in _req_rows(workspace):
        name = str(r.get("requirement") or "—")
        status = str(r.get("status") or "—")
        if name in uncertain_set or name in review_set:
            confidence, emoji = CONFIDENCE_CONFIRM, "🟡"
        elif name in blocked_set or status == "BLOCKED":
            confidence, emoji = CONFIDENCE_UNKNOWN, "🔴"
        else:
            confidence, emoji = CONFIDENCE_CLEAR, "🟢"
        rows.append({
            "requirement": name,
            "status": status,
            "status_label": str(r.get("status_label") or r.get("status") or "—"),
            "confidence": confidence,
            "confidence_emoji": emoji,
            "status_language": _status_student_language(status),
            "result": str(r.get("result") or "—"),
            "evidence": str(r.get("evidence") or r.get("detail") or ""),
        })
    return {
        "rows": rows,
        "total": len(rows),
        "clear_count": sum(1 for row in rows if row["confidence"] == CONFIDENCE_CLEAR),
        "confirm_count": sum(1 for row in rows if row["confidence"] == CONFIDENCE_CONFIRM),
        "unknown_count": sum(1 for row in rows if row["confidence"] == CONFIDENCE_UNKNOWN),
        "review_count": len(_review_required_metrics(workspace)),
        "blocked_count": len(_blocked_metrics(workspace)),
        # Sprint 12.1 - zero-panic parse recovery state.
        "parse_state": rec["state"],
        "confirmed": rec["confirmed"],
        "uncertain": rec["uncertain"],
        # Sprint 12.1 UX - per-item 'did your professor mean' candidates.
        "uncertain_candidates": [
            {"token": str(t), "candidates": confirmation_candidates(t)}
            for t in rec["uncertain"]
        ],
        "review_required": rec["review_required"],
        # Manual requirement selector options for the low-confidence state.
        "options": sorted(CANONICAL_METRICS.keys()),
    }


def _content_periods(workspace: Dict[str, Any]) -> Dict[str, Any]:
    changes = _strongest_changes(workspace, 4)
    return {
        "periods": _period_list(workspace),
        "changes": changes,
        "strongest": changes[0] if changes else None,
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
        "status_language": _status_student_language(status),
        "missing_facts": _missing_facts_for(workspace, metric),
    }


def _content_metric(workspace: Dict[str, Any], metric: str) -> Dict[str, Any]:
    payload = _metric_payload(workspace, metric)
    payload["excel_where"] = _excel_where(workspace, metric)
    return payload


# Metrics whose primary location in the Excel working model is the Ratio
# Analysis sheet (everything else lives in Financial Data).
_RATIO_SHEET_METRICS: set = {
    "ROE", "ROA", "Profit Margin", "Operating Margin", "Current Ratio",
    "Debt to Equity", "Revenue Growth", "EPS Growth", "CAGR",
}


def _excel_where(workspace: Dict[str, Any], metric: str) -> str:
    """Deterministic 'where do I look?' guidance for one metric in the Excel
    model. Never claims a sheet that does not exist — ratio metrics map to
    Ratio Analysis, everything else to Financial Data; underlying inputs are
    named from the Formula-Engine calculation when available."""
    sheet = "Ratio Analysis" if metric in _RATIO_SHEET_METRICS else "Financial Data"
    calc = _calc_metric(workspace, metric)
    inputs = [str(i.get("metric") or i.get("key") or "") for i in (calc or {}).get("inputs") or []]
    inputs = [i for i in inputs if i][:2]
    msg = f"Checking {metric}? Start with {sheet} → {metric}."
    if inputs:
        msg += f" The underlying {' and '.join(inputs)} values are linked from Financial Data."
    return msg


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
        "excel_where": _excel_where(workspace, metric),
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
    # Compact preview: prefer the most assignment-relevant metrics, then the
    # strongest remaining rows, capped at 3. The full table stays accessible.
    preview = []
    for preferred in ("Revenue", "Net Profit"):
        for r in rows:
            if str(r.get("canonical")) == preferred:
                preview.append(r)
    for r in rows:
        if r not in preview:
            preview.append(r)
    preview = preview[:3]
    return {
        "active": bool(comp.get("active")),
        "company_a": str(comp.get("company_a") or "Company A"),
        "company_b": str(comp.get("company_b") or "—"),
        "rows": rows,
        "preview": preview,
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
        # Sprint 12.1 - orientation: where to look first, without dumping the
        # whole workbook on the student.
        "orientation": {
            "formulas_done": True,
            "first": "Ratio Analysis",
            "then": ["Financial Data"],
            "optional": ["Comparison", "Driver Analysis"],
            # Sprint 12.1 - numbered 'start here' orientation steps.
            "steps": [
                {"n": 1, "text": "Sheet 2 — Ratio Analysis"},
                {"n": 2, "text": "Check the calculated metrics"},
                {"n": 3, "text": "Compare them with the evidence cards"},
                {"n": 4, "text": "Explore other sheets only if needed"},
            ],
            # Sprint 12.2 - contextual one-line guidance per sheet. Rendered
            # only when the student asks ('Understand the model'), never all
            # at once.
            "sheet_notes": {
                "Financial Data": "Raw verified financial inputs used by the model.",
                "Ratio Analysis": "Your main working sheet. Start here.",
                "External Variables": "Values you manually provide, such as risk-free rate or beta. These are explicitly marked as student inputs.",
                "Comparison": "Compare the company with the selected peer where sufficient evidence exists.",
                "Driver Analysis": "See what changed between periods and which numerical components contributed.",
                "Assignment Requirements": "Verify that every assignment requirement has been addressed.",
                "Qualitative Drivers": "Review evidence-backed explanations and their confidence/status.",
            },
        },
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
    # Sprint 12.1 - evidence-backed scaffolding. The agent NEVER writes the
    # conclusion; it only points at what the evidence suggests and leaves
    # the judgment (and the writing) to the student.
    scaffold: List[str] = []
    obs_top = _strongest_changes(workspace, 1)
    if obs_top:
        o = obs_top[0]
        scaffold.append(
            f"Evidence suggests: {o.get('metric')} {o.get('change_display') or 'moved'} "
            f"between {o.get('from')} and {o.get('to')}."
        )
    else:
        scaffold.append(
            "Evidence suggests: no multi-period trend is established from the "
            "current evidence set."
        )
    scaffold.append(
        "Think about: was the improvement related to pricing, volume, costs, "
        "or another factor disclosed in the evidence?"
    )
    scaffold.append(
        "Your task: decide which explanation is best supported and write your "
        "interpretation in your own words."
    )
    return {"checklist": checklist, "never_generate": True, "scaffold": scaffold}


# ---------------------------------------------------------------------------
# Stage messages
# ---------------------------------------------------------------------------


def _message_for(stage: str, workspace: Dict[str, Any], metric: Optional[str], area: Optional[str], requirements_text: str = "") -> str:
    if stage == STAGE_OPENING:
        c = _content_opening(workspace)
        req_names = (
            ", ".join(c["requirements"])
            if c["requirements"] else "no parsed requirements yet"
        )
        return (
            f"I've reviewed your assignment. You need to work with: {req_names}."
            " What would you like to work on first?"
        )
    if stage == STAGE_REQUIREMENTS:
        rec = parse_recovery(workspace, requirements_text)
        c = _content_requirements(workspace)
        if rec["state"] == PARSE_HIGH:
            summary = _parse_summary(workspace, requirements_text)
            periods = _period_list(workspace)
            period_txt = ""
            if len(periods) >= 2:
                period_txt = f" across {', '.join(periods)}"
            flags = []
            if c["review_count"]:
                flags.append(f"{c['review_count']} item(s) flagged for review")
            if c["blocked_count"]:
                flags.append(f"{c['blocked_count']} item(s) blocked")
            tail = (
                f" Heads up: {'; '.join(flags)} — I'll flag them when they matter."
                if flags else ""
            )
            n = len(rec["confirmed"])
            # Sprint 12.2 - high confidence is stated with a count, never
            # followed by an unnecessary interruption.
            return (
                f"I've parsed your assignment and identified {n} requirements from it. "
                f"You need to calculate {summary}{period_txt}.{tail} "
                "Continue to the analysis when you're ready."
            )
        if rec["state"] == PARSE_PARTIAL:
            items = rec["uncertain"] + rec["review_required"]
            item_txt = ", ".join(f"'{i}'" for i in items[:2]) or "one item"
            n_clear = len(rec["confirmed"])
            n_unc = len(items)
            word = "item" if n_unc == 1 else "items"
            verb = "needs" if n_unc == 1 else "need"
            # Sprint 13 - tutor-style: what was understood, what is unclear,
            # and the single decision the student needs to make.
            return (
                f"I understood most of your assignment. I found {n_clear} clear "
                f"requirements and {n_unc} {word} that {verb} confirmation: "
                f"{item_txt}. Should I include it in your analysis?"
            )
        # Sprint 12.2 - low confidence: reassure, then ask, never a technical
        # failure message.
        return (
            "I couldn't confidently interpret the assignment wording. Nothing is "
            "broken — let's confirm what the professor asked you to calculate "
            "before I continue."
        )
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
            missing_txt = ""
            if p.get("missing_facts"):
                missing_txt = (
                    " The figures I couldn't confirm: "
                    + ", ".join(str(m) for m in p["missing_facts"]) + "."
                )
            return (
                f"I couldn't safely calculate {metric} — the required inputs are "
                "missing or not reliable enough, so I don't have enough verified "
                "information to work with safely."
                f"{missing_txt} Next: verify the relevant figures, add an "
                "external variable, or continue with the metrics that are supported."
            )
        if p["is_review"]:
            return (
                f"I found a possible figure for {metric}, but the accounting "
                "label or structure is ambiguous. Please verify it before using "
                "it. Next: review the source evidence or continue with the "
                "metrics that are supported."
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
        if c["has_qualitative"] and not c["is_blocked"] and not c["is_review"]:
            # Sprint 13 - learning layer: the agent teaches the reasoning,
            # never writes the student's judgment.
            msg += (
                " Think about: what drove this — pricing, volume, operating "
                "costs, or another factor disclosed in the evidence? "
                "Your task: decide which explanation the report supports best."
            )
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
        # Sprint 12.2 - explain the model before it is opened: where to start
        # and what to check.
        return (
            "Your working model is ready. The calculations are already completed "
            "— you don't need to rebuild them. For this assignment, start with "
            "Sheet 2 — Ratio Analysis and verify the results against the evidence "
            "cards."
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


def _choices_for(stage: str, workspace: Dict[str, Any], metric: Optional[str], area: Optional[str], requirements_text: str = "") -> List[Dict[str, Any]]:
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
        rec = parse_recovery(workspace, requirements_text)
        if rec["state"] == PARSE_HIGH:
            choices.append({
                "id": "requirements.continue", "label": "Continue to analysis",
                "hint": "Move to the results.",
            })
            if _review_required_metrics(workspace) or _blocked_metrics(workspace):
                choices.append({
                    "id": "requirements.review", "label": "Review details",
                    "hint": "Open the full checklist with evidence details.",
                })
        elif rec["state"] == PARSE_PARTIAL:
            choices.append({
                "id": "requirements.confirm", "label": "Confirm & Continue",
                "hint": "Confirm the detected requirements and move on.",
            })
            items = rec["uncertain"] + rec["review_required"]
            for i, tok in enumerate(items[:2]):
                choices.append({
                    "id": f"requirements.include.{i}",
                    "label": f"Yes, include “{tok}”",
                    "hint": "Confirm this item and continue.",
                })
                choices.append({
                    "id": f"requirements.exclude.{i}",
                    "label": f"No, continue without “{tok}”",
                    "hint": "Leave this item out and continue.",
                })
            choices.append({
                "id": "requirements.edit", "label": "Edit requirements",
                "hint": "Correct the requirement wording.",
            })
        else:
            choices.append({
                "id": "requirements.confirm", "label": "Continue",
                "hint": "Continue once the requirements are set.",
            })
            choices.append({
                "id": "requirements.edit", "label": "Edit requirements",
                "hint": "Type or select the required metrics manually.",
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
            "id": "excel.download", "label": "Open Excel Working Model",
            "hint": "Download the 7-sheet working model.",
        })
        choices.append({
            "id": "excel.evidence", "label": "Verify evidence first",
            "hint": "Check the source evidence before reviewing the workbook.",
        })
        choices.append({
            "id": "excel.understand", "label": "Understand the model",
            "hint": "What each of the seven sheets is for.",
        })
        choices.append({
            "id": "continue", "label": "Continue in Platrixa",
            "hint": "Keep working in the workspace without downloading.",
        })
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


def _recommended_choice(state: Dict[str, Any], workspace: Dict[str, Any], requirements_text: str = "") -> Optional[Dict[str, Any]]:
    stage = state.get("stage")
    metric = state.get("metric")
    if stage == STAGE_OPENING:
        if _period_list(workspace):
            return {"id": "opening.periods", "label": "Start with the period analysis", "hint": "Review FY changes first — your assignment asks for them."}
        return {"id": "opening.requirements", "label": "Review the assignment requirements", "hint": "See what the assignment asks for."}
    if stage == STAGE_REQUIREMENTS:
        rec = parse_recovery(workspace, requirements_text)
        if rec["state"] == PARSE_HIGH:
            return {"id": "requirements.continue", "label": "Continue to the analysis", "hint": "See the strongest verified results next."}
        if rec["state"] == PARSE_PARTIAL:
            return {"id": "requirements.confirm", "label": "Confirm & Continue", "hint": "Confirm the detected requirements and move on."}
        return {"id": "requirements.confirm", "label": "Continue", "hint": "Continue once the requirements are set."}
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
            return {"id": "metric.explain", "label": "Investigate why", "hint": "Evidence-first explanation of the move."}
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


def _comparison_biggest(workspace: Dict[str, Any]) -> Optional[str]:
    """Canonical metric with the largest |value difference| in the active
    comparison (ties broken alphabetically). None when not comparable."""
    best = None
    best_abs = -1.0
    for r in _comparison_rows(workspace):
        try:
            va = float(str(r.get("value_a") or "0").replace(",", ""))
            vb = float(str(r.get("value_b") or "0").replace(",", ""))
        except (TypeError, ValueError):
            continue
        d = abs(va - vb)
        if d > best_abs or (d == best_abs and str(r.get("canonical") or "") < (best or "")):
            best_abs = d
            best = str(r.get("canonical") or "")
    return best or None


def _suggested_questions(
    stage: str,
    workspace: Dict[str, Any],
    metric: Optional[str] = None,
    area: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Sprint 13 - contextual suggested questions. Deterministic, rotate with
    the stage, capped so the workspace never becomes a button wall."""
    out: List[Dict[str, Any]] = []

    def add(cid: str, label: str, hint: str = "") -> None:
        if cid and label and all(c["id"] != cid for c in out):
            out.append({"id": cid, "label": label, "hint": hint})

    top = _strongest_changes(workspace, 1)
    top_m = str(top[0].get("metric")) if top else None

    if stage == STAGE_OPENING:
        add("opening.requirements", "Show me what the assignment requires")
        if _comparison(workspace).get("active"):
            add("opening.comparison", "Start with the company comparison")
    elif stage in (STAGE_PERIODS, STAGE_METRIC):
        if top_m and _metric_change(workspace, top_m):
            add(f"suggest.explain.{top_m}", f"Why did {top_m} change?")
        if _comparison(workspace).get("active"):
            add("suggest.comparison", "Compare with the peer company")
        if top_m:
            add(f"suggest.evidence.{top_m}", "Verify the source")
    elif stage in (STAGE_EXPLAIN, STAGE_DRIVERS, STAGE_QUALITATIVE):
        if top_m:
            add(f"suggest.evidence.{top_m}", "Show the evidence")
            add(f"suggest.calculation.{top_m}", "Open the calculation")
        else:
            add("continue", "Continue")
    elif stage == STAGE_EVIDENCE:
        add("continue", "Continue to the driver analysis")
        if top_m and _metric_change(workspace, top_m):
            add(f"suggest.explain.{top_m}", "Explain why it changed")
    elif stage == STAGE_COMPARISON:
        biggest = _comparison_biggest(workspace)
        if biggest:
            add(f"suggest.explain.{biggest}", "Explain the biggest difference")
            add(f"suggest.evidence.{biggest}", "Check the underlying figures")
        add("suggest.conclusion", "Continue to the conclusion")
    elif stage == STAGE_EXCEL:
        add("excel.evidence", "Verify evidence first")
        add("excel.understand", "Understand the model")
        add("continue", "Continue in Platrixa")
    elif stage == STAGE_MEMO:
        add("memo.conclusion", "Go to your conclusion")
    elif stage == STAGE_CONCLUSION:
        pass
    return out[:3]


def _alternative_choices(state: Dict[str, Any], workspace: Dict[str, Any], requirements_text: str = "") -> List[Dict[str, Any]]:
    """Sprint 13 - one primary plus at most 1-2 quiet secondaries. Recovery
    on the requirements stage keeps its yes/no/include/edit controls."""
    stage = state.get("stage")
    if stage == STAGE_CONCLUSION:
        return []
    if stage == STAGE_OPENING:
        rec = _recommended_choice(state, workspace, requirements_text)
        if rec and rec.get("id") == "opening.requirements":
            return ([{"id": "opening.comparison", "label": "Start with the company comparison", "hint": "Compare with the peer company."}]
                    if _comparison(workspace).get("active") else [])
        return [{"id": "opening.requirements", "label": "Show me what the assignment requires", "hint": "Review the parsed requirement checklist."}]
    if stage == STAGE_REQUIREMENTS:
        rec = parse_recovery(workspace, requirements_text)
        alts: List[Dict[str, Any]] = []
        if rec["state"] == PARSE_PARTIAL:
            items = rec["uncertain"] + rec["review_required"]
            if items:
                alts.append({
                    "id": "requirements.include.0",
                    "label": "Yes, include \u201c%s\u201d" % items[0],
                    "hint": "Confirm this item and continue.",
                })
                alts.append({
                    "id": "requirements.exclude.0",
                    "label": "No, continue without \u201c%s\u201d" % items[0],
                    "hint": "Leave this item out and continue.",
                })
            alts.append({
                "id": "requirements.edit", "label": "Edit requirements",
                "hint": "Correct the requirement wording.",
            })
        elif rec["state"] == PARSE_HIGH:
            if _review_required_metrics(workspace) or _blocked_metrics(workspace):
                alts.append({
                    "id": "requirements.review", "label": "Review details",
                    "hint": "Open the full checklist with evidence details.",
                })
            else:
                alts.append({
                    "id": "requirements.edit", "label": "Review requirements",
                    "hint": "Review the requirement checklist.",
                })
        else:
            alts.append({
                "id": "requirements.edit", "label": "Edit requirements",
                "hint": "Type or select the required metrics manually.",
            })
        return alts[:3]
    return _suggested_questions(stage, workspace, state.get("metric"), state.get("area"))[:2]


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
    requirements_text: str = "",
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
        if sub in ("continue", "confirm"):
            new = go(STAGE_PERIODS if _period_list(workspace or {}) else STAGE_METRIC)
            if sub == "confirm":
                # Sprint 12.2 - manual confirmation is a normal, calm step.
                new = {
                    **new,
                    "notice": "Got it. I'll use these requirements for the working model.",
                }
            return new
        if sub == "edit":
            # Stay on the requirements stage — the UI opens the setup editor
            # (the deterministic state machine never dead-ends).
            return dict(state)
        if sub == "review":
            return {**state, "stage": STAGE_EXPLORE_UI, "visited": visited}
        if sub.startswith("include.") or sub.startswith("exclude."):
            # Sprint 13 - per-item yes/no confirmation: the student decides
            # on the uncertain item; the decision is recorded calmly and the
            # agent continues with the verified workspace (never fabricates).
            rec = parse_recovery(workspace or {}, requirements_text)
            items = rec["uncertain"] + rec["review_required"]
            try:
                idx = int(sub.split(".", 1)[1])
                tok = str(items[idx] or "this item")
            except (IndexError, ValueError):
                tok = "this item"
            key = "included" if sub.startswith("include.") else "excluded"
            prior = list(state.get(key) or [])
            if tok not in prior:
                prior.append(tok)
            if sub.startswith("include."):
                notice = "Got it. I'll use \u201c%s\u201d for the working model." % tok
            else:
                notice = "Understood \u2014 I'll continue without \u201c%s\u201d." % tok
            new = go(STAGE_PERIODS if _period_list(workspace or {}) else STAGE_METRIC)
            return {**new, key: prior, "notice": notice}
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
    if choice_id == "excel.understand":
        # Sprint 12.2 - toggle contextual per-sheet guidance in place.
        return {
            **state,
            "stage": STAGE_EXCEL,
            "show_sheet_notes": not bool(state.get("show_sheet_notes")),
        }
    if choice_id == "excel.evidence":
        top = _strongest_changes(workspace or {}, 1)
        ev_metric = str(top[0].get("metric")) if top else None
        return go(STAGE_EVIDENCE, ev_metric)
    if choice_id == "memo.conclusion":
        return go(STAGE_CONCLUSION)

    # Sprint 13 - contextual suggested-question targets (deterministic).
    if choice_id.startswith("suggest."):
        sub = choice_id.split(".", 1)[1]
        if sub == "comparison":
            return go(STAGE_COMPARISON)
        if sub == "conclusion":
            return go(STAGE_CONCLUSION)
        if sub == "drivers":
            return go(STAGE_DRIVERS)
        if sub.startswith("explain."):
            return go(STAGE_EXPLAIN, sub.split(".", 1)[1])
        if sub.startswith("evidence."):
            return go(STAGE_EVIDENCE, sub.split(".", 1)[1])
        if sub.startswith("calculation."):
            return go(STAGE_CALCULATION, sub.split(".", 1)[1])

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
    requirements_text: str = "",
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
            "step": agent_step(stage),
            "explore": True,
            "message": "You're in the full workspace — everything remains available.",
            "content": {},
            "choices": [],
            "recommended": None,
            "alternatives": [],
            "suggested": [],
            "progress": agent_progress(workspace, state),
            "guidance": {},
        }

    content: Dict[str, Any] = {}
    if stage == STAGE_OPENING:
        content = _content_opening(workspace)
    elif stage == STAGE_REQUIREMENTS:
        content = _content_requirements(workspace, requirements_text)
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
                "title": f"\U0001f534 {metric} is blocked",
                "what": f"I couldn't safely calculate {metric}.",
                "why": (
                    "I don't have enough verified information — the required "
                    "figures are missing or not reliable enough to work with safely."
                ),
                "next": (
                    "Verify the relevant figures, add an external variable, or "
                    "continue with the metrics that are supported."
                ),
                "missing": _missing_facts_for(workspace, metric),
                "message": (
                    f"{metric} is blocked: required inputs are missing from the "
                    "verified evidence. I won't guess a value."
                ),
            }
        elif status == "REVIEW_REQUIRED":
            guidance = {
                "kind": "review",
                "metric": metric,
                "title": f"\U0001f7e0 {metric} needs your review",
                "what": (
                    f"I found a possible figure for {metric}, but the accounting "
                    "label or structure is ambiguous."
                ),
                "why": (
                    "I won't merge it automatically because doing so could "
                    "change your analysis."
                ),
                "next": (
                    "Verify the figure before using it, or continue with the "
                    "metrics that are supported."
                ),
                "missing": [],
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
        "step": agent_step(stage),
        "explore": False,
        "message": _message_for(stage, workspace, metric, area, requirements_text),
        "content": content,
        "choices": _choices_for(stage, workspace, metric, area, requirements_text),
        "recommended": _recommended_choice(state, workspace, requirements_text),
        "alternatives": _alternative_choices(state, workspace, requirements_text),
        "suggested": _suggested_questions(stage, workspace, metric, area),
        "progress": agent_progress(workspace, state),
        "guidance": guidance,
        "conflict_metrics": conflicts,
    }


# Convenience alias so UI/tests can ask "what should I do next?" explicitly.
def what_next(
    workspace: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    requirements_text: str = "",
) -> Dict[str, Any]:
    """The central interaction: ONE recommended action plus 1-2 alternatives."""
    view = agent_session(workspace, state, requirements_text=requirements_text)
    return {
        "recommended": view["recommended"],
        "alternatives": view["alternatives"],
        "stage": view["stage"],
    }
