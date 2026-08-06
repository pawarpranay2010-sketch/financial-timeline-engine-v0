"""
Financial Timeline Engine
Sprint 10 - University Finance Assignment Workspace + Working Model

A DETERMINISTIC workspace layer on top of the existing verified Fact
Graph. It removes repetitive financial-analysis work (extraction,
normalization, calculations, comparison, evidence collection, working
model construction) while leaving genuine academic judgment — the final
conclusion — to the student.

Hard rules
----------
* NO Streamlit, NO AI, NO network. This module is pure and deterministic.
* NEVER performs arithmetic itself for derived metrics: every calculation
  goes through the Sprint 7 Formula Engine (backend.formula_engine), which
  delegates to the C++ engine when available. This module only resolves
  inputs and orchestrates.
* NEVER invents a missing requirement or a missing value. A requirement
  whose metric is absent from the fact graph is reported as
  BLOCKED / UNANALYZED, never guessed.
* Student-entered data (external variables) is ALWAYS labeled
  STUDENT_INPUT (🟡). It can never appear as a document-verified fact.
* Canonical normalization only auto-merges HIGH-confidence label matches.
  Ambiguous or unknown labels become REVIEW_REQUIRED (🟠) — accounting
  concepts are never silently merged.
* The final student conclusion is NEVER generated here.

Status model (shared with the UI)
---------------------------------
  VERIFIED          🟢  reported + verified in a source document
  DERIVED           🔵  calculated by the Formula Engine from verified inputs
  EXTERNAL_DERIVED  🟣  calculated with one or more Sprint 6.5 Tier-3 inputs
  STUDENT_INPUT     🟡  value was entered by the student (never document data)
  REVIEW_REQUIRED   🟠  normalization/extraction is uncertain
  BLOCKED           🔴  required input unavailable / invalid / incompatible
  UNANALYZED        ⚪  no supported deterministic formula exists
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.formula_engine import (
    FORMULA_REGISTRY,
    calculate_metric,
    SUPPORTED_FORMULAS,
)
from backend.evidence_resolver import PROVENANCE_TIER
from backend.qualitative_catalyst import build_qualitative_drivers

# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------

ST_VERIFIED = "VERIFIED"
ST_DERIVED = "DERIVED"
ST_EXTERNAL_DERIVED = "EXTERNAL_DERIVED"
ST_STUDENT_INPUT = "STUDENT_INPUT"
ST_REVIEW_REQUIRED = "REVIEW_REQUIRED"
ST_BLOCKED = "BLOCKED"
ST_UNANALYZED = "UNANALYZED"

STATUS_LABELS: Dict[str, str] = {
    ST_VERIFIED: "🟢 VERIFIED",
    ST_DERIVED: "🔵 DERIVED",
    ST_EXTERNAL_DERIVED: "🟣 EXTERNAL_DERIVED",
    ST_STUDENT_INPUT: "🟡 STUDENT_INPUT",
    ST_REVIEW_REQUIRED: "🟠 REVIEW_REQUIRED",
    ST_BLOCKED: "🔴 BLOCKED",
    ST_UNANALYZED: "⚪ UNANALYZED",
}

# ---------------------------------------------------------------------------
# Assignment types (extensible; only the approved five for Sprint 10)
# ---------------------------------------------------------------------------

ASSIGNMENT_TYPES: List[str] = [
    "Financial Ratio Analysis",
    "Financial Statement Analysis",
    "Annual Report Analysis",
    "3-Year Trend Analysis",
    "Company Comparison",
]

# ---------------------------------------------------------------------------
# Canonical metric normalization
# ---------------------------------------------------------------------------

# canonical concept -> ordered list of accepted labels (lower-cased).
# Order matters: more specific labels come first so the exact-match pass
# prefers them. Only concepts already supported by the pipeline/formulas
# are listed — nothing is invented.
CANONICAL_METRICS: Dict[str, List[str]] = {
    "Revenue": [
        "revenue", "net sales", "net revenue", "sales", "sales revenue",
        "revenue from operations", "revenue from operations (net)",
        "operating revenue", "total revenue", "revenues",
    ],
    "Net Profit": [
        "net profit", "net income", "profit after tax", "pat",
        "net earnings", "profit for the year", "earnings",
        "income from continuing operations", "net profit after tax",
    ],
    "Operating Profit": [
        "operating profit", "operating income", "income from operations",
        "operating earnings", "profit from operations", "ebit",
    ],
    "EBITDA": [
        "ebitda", "earnings before interest, taxes, depreciation and amortisation",
        "earnings before interest, taxes, depreciation and amortization",
    ],
    "EPS": [
        "eps", "earnings per share", "diluted eps", "basic eps",
        "earnings per share (diluted)", "earnings per share (basic)",
    ],
    "Operating Cash Flow": [
        "operating cash flow", "cash flow from operations",
        "cash from operations", "net cash from operating activities",
        "cash generated from operations", "cash flow from operating activities",
    ],
    "Assets": [
        "assets", "total assets",
    ],
    "Equity": [
        "equity", "shareholders' equity", "shareholder equity",
        "stockholders' equity", "total equity", "shareholders equity",
        "owner's equity", "owners' equity", "total shareholders' equity",
    ],
    "Debt": [
        "debt", "total debt", "borrowings", "total borrowings",
    ],
    "Liabilities": [
        "liabilities", "total liabilities",
    ],
    "Current Assets": [
        "current assets", "total current assets",
    ],
    "Current Liabilities": [
        "current liabilities", "total current liabilities",
    ],
    "ROE": ["roe", "return on equity"],
    "ROA": ["roa", "return on assets"],
    "Profit Margin": ["profit margin", "net margin", "net profit margin"],
    "Operating Margin": ["operating margin"],
    "Current Ratio": ["current ratio"],
    "Debt to Equity": ["debt to equity", "debt/equity", "debt / equity",
                       "debt-to-equity", "d/e"],
    "Revenue Growth": ["revenue growth", "sales growth", "revenue yoy"],
    "EPS Growth": ["eps growth", "earnings per share growth"],
    "CAGR": ["cagr", "compound annual growth rate"],
}

# Labels that should NEVER auto-merge into a canonical concept because the
# accounting meaning is genuinely different (REVIEW_REQUIRED instead).
_AMBIGUOUS_LABELS = {
    "gross profit", "gross margin", "segment gross margin", "operating margin",
    "net working capital", "working capital", "book value", "market cap",
    "interest expense", "tax expense", "exceptional items", "other income",
}

# Formula input keys (Sprint 7 registry) -> canonical concept key.
_FORMULA_INPUT_CANONICAL = {
    "Revenue": "Revenue",
    "Previous Revenue": "Revenue",
    "Net Profit": "Net Profit",
    "Equity": "Equity",
    "Assets": "Assets",
    "Operating Profit": "Operating Profit",
    "Debt": "Debt",
    "Current Assets": "Current Assets",
    "Current Liabilities": "Current Liabilities",
    "EPS": "EPS",
    "Previous EPS": "EPS",
    "CAGR Beginning Value": "Revenue",
    "CAGR Ending Value": "Revenue",
}


# Hyphen-like punctuation that separates compound metric labels (hyphen,
# non-breaking hyphen, figure dash, en dash, em dash, minus). Normalized to
# a space so "Debt-to-Equity" / "Debt–to–Equity" resolve identically to
# "Debt to Equity" (Sprint 11.1). Word boundaries still protect EPSILON,
# "Debt-like" etc. from partial-word matching.
_HYPHEN_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212-]")


def _norm_label(label: Any) -> str:
    """Lower-case, strip and collapse whitespace for matching. Hyphen-like
    punctuation is normalized to a space so compound labels match
    canonically (Sprint 11.1)."""
    if label is None:
        return ""
    s = _HYPHEN_RE.sub(" ", str(label))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def canonicalize_metric(label: Any) -> Tuple[Optional[str], str, str]:
    """Map a raw metric label to a canonical concept.

    Returns (canonical, confidence, reason):
      canonical   - canonical concept key, or None
      confidence  - "high" | "medium" | "none"
      reason      - human-readable explanation ("" when exact match)

    Rules
    -----
    * exact alias match           -> high confidence, auto-normalized
    * contains-match (word-safe)  -> medium confidence, auto-normalized
    * multiple competing matches  -> REVIEW_REQUIRED (never guessed)
    * known ambiguous label       -> REVIEW_REQUIRED (never merged)
    * no match                    -> REVIEW_REQUIRED / unknown
    """
    n = _norm_label(label)
    if not n:
        return None, "none", "No label to normalize."
    if n in _AMBIGUOUS_LABELS:
        return None, "none", (
            f"'{label}' has a distinct accounting meaning and is not "
            "silently merged into a canonical metric."
        )

    # Pass 1 — exact alias match.
    for canonical, aliases in CANONICAL_METRICS.items():
        for alias in aliases:
            if n == alias:
                return canonical, "high", ""

    # Pass 2 — word-safe contains match (only if exactly ONE canonical
    # concept matches; otherwise the label is ambiguous).
    matched: List[str] = []
    for canonical, aliases in CANONICAL_METRICS.items():
        for alias in aliases:
            if len(alias) >= 5 and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", n):
                matched.append(canonical)
                break
    unique = list(dict.fromkeys(matched))
    if len(unique) == 1:
        return unique[0], "medium", (
            f"'{label}' matched canonical '{unique[0]}' by embedded label."
        )
    if len(unique) > 1:
        return None, "none", (
            f"'{label}' could match multiple canonical concepts "
            f"({', '.join(unique)}) — review required, not auto-merged."
        )
    return None, "none", f"'{label}' has no supported canonical mapping."


# ---------------------------------------------------------------------------
# Requirement parsing (deterministic; no LLM)
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r"(?:FY\s*)?(20\d{2})|(?:FY\s*)?(19\d{2})", re.IGNORECASE
)
_RANGE_RE = re.compile(
    r"(?:FY\s*)?(20\d{2})\s*(?:-|–|—|to)\s*(?:FY\s*)?(20\d{2})", re.IGNORECASE
)


def parse_requirements(text: str) -> List[Dict[str, Any]]:
    """Parse an assignment requirement sentence into structured items.

    Example:
      "Analyze Microsoft FY2023-FY2025 and calculate ROE, ROA, Profit
       Margin, Current Ratio and Debt/Equity."
      -> [
           {"metric": "ROE", "periods": ["FY2023","FY2024","FY2025"], ...},
           {"metric": "ROA", ...}, ...
         ]

    Deterministic: metrics are found by canonical alias matching in the
    text; periods are found by FY-range / single-year tokens. If the text
    mentions a metric the pipeline supports via a formula, it is included;
    unknown metric tokens are ignored (never guessed).
    """
    text = text or ""
    n = _norm_label(text)

    # Periods: expand ranges (FY2023-FY2025 -> each year) plus singles.
    periods: List[str] = []
    for m in _RANGE_RE.finditer(text):
        start, end = int(m.group(1)), int(m.group(2))
        if start <= end <= 2100 and end - start <= 15:
            periods.extend(f"FY{y}" for y in range(start, end + 1))
    for m in _PERIOD_RE.finditer(text):
        year = int(m.group(1) or m.group(2))
        fy = f"FY{year}"
        if fy not in periods and 1950 <= year <= 2100:
            periods.append(fy)

    # Metrics: span-based canonical alias scan with longest-match
    # priority. Collect every (canonical, start, end) match, then keep the
    # LONGEST alias at each position and drop shorter overlaps — so
    # "Debt/Equity" yields one "Debt to Equity" requirement, never the
    # shorter fragments "Debt" and "Equity" as separate items.
    spans: List[Tuple[str, int, int]] = []
    for canonical, aliases in CANONICAL_METRICS.items():
        for alias in aliases:
            if len(alias) < 3:
                continue
            for m in re.finditer(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", n
            ):
                spans.append((canonical, m.start(), m.end()))
    spans.sort(key=lambda t: (t[1], -(t[2] - t[1])))
    metrics: List[str] = []
    last_end = -1
    for canonical, start, end in spans:
        if start < last_end:
            continue  # shorter alias inside a longer match — drop it
        if canonical not in metrics:
            metrics.append(canonical)
        last_end = end

    items: List[Dict[str, Any]] = []
    for metric in metrics:
        items.append({
            "requirement": metric,
            "metric": metric,
            "canonical": metric,
            "periods": list(periods),
        })
    return items


# ---------------------------------------------------------------------------
# Fact-graph accessors (pipeline shape: {metric: {value, source, ...}})
# ---------------------------------------------------------------------------


def _fact_value(fact: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(fact, dict):
        return None
    v = fact.get("value")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fact_tier(fact: Optional[Dict[str, Any]]) -> str:
    if not isinstance(fact, dict):
        return PROVENANCE_TIER.UNANALYZED
    tier = fact.get("provenance_tier")
    if tier:
        return str(tier)
    if str(fact.get("source")) == "Calculated":
        return PROVENANCE_TIER.DERIVED
    if _fact_value(fact) is not None:
        return PROVENANCE_TIER.DOCUMENT
    return PROVENANCE_TIER.UNANALYZED


def collect_facts(module3_result: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge financial_data + ratios into one fact map (pipeline shape)."""
    facts: Dict[str, Dict[str, Any]] = {}
    for section in ("financial_data", "ratios"):
        for key, fact in ((module3_result or {}).get(section) or {}).items():
            if isinstance(fact, dict):
                facts[str(key)] = fact
    return facts


def _fmt_compact(v: Optional[float]) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1e9:
        return f"{f / 1e9:,.2f}B"
    if abs(f) >= 1e6:
        return f"{f / 1e6:,.2f}M"
    if abs(f) >= 1e3:
        return f"{f / 1e3:,.2f}K"
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Requirement checklist resolution
# ---------------------------------------------------------------------------


def resolve_metric_status(
    metric: str,
    facts: Dict[str, Dict[str, Any]],
    external_variables: Optional[List[Dict[str, Any]]] = None,
    missing: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Resolve one metric's workspace status against the fact graph.

    Returns (status_code, detail). Never guesses: a metric that exists
    nowhere becomes UNANALYZED (or BLOCKED when the pipeline lists it as
    required-but-missing).
    """
    fact = facts.get(metric)
    if isinstance(fact, dict):
        if str(fact.get("extraction_state")) == "review_required":
            return ST_REVIEW_REQUIRED, "Extraction reliability flags this as uncertain."
        tier = _fact_tier(fact)
        if tier in (PROVENANCE_TIER.REGULATORY_API, PROVENANCE_TIER.APPENDIX):
            return ST_EXTERNAL_DERIVED, "Calculated with Sprint 6.5 external inputs."
        if tier == PROVENANCE_TIER.EXTERNAL_DERIVED:
            return ST_EXTERNAL_DERIVED, "Calculated with Sprint 6.5 external inputs."
        if tier == PROVENANCE_TIER.DERIVED or str(fact.get("source")) == "Calculated":
            return ST_DERIVED, "Calculated by the Formula Engine from verified inputs."
        if _fact_value(fact) is not None:
            return ST_VERIFIED, "Reported and verified in the source document."
    # Student-entered external variable with a matching name?
    for var in external_variables or []:
        if _norm_label(var.get("name")) == _norm_label(metric):
            return ST_STUDENT_INPUT, "Value entered by the student."
    if missing and metric in (missing.get("financial_data") or []):
        return ST_BLOCKED, "Required financial evidence is missing."
    # A supported formula whose inputs ALL exist in the fact graph is
    # calculable by the Formula Engine — report it as such (never guess).
    reg = FORMULA_REGISTRY.get(metric)
    if reg is not None:
        if all(
            isinstance(facts.get(inp), dict) and _fact_value(facts.get(inp)) is not None
            for inp in reg.required_inputs
        ):
            return ST_DERIVED, "Calculable by the Formula Engine from verified inputs."
        return ST_BLOCKED, "Required inputs are unavailable from permitted evidence sources."
    if metric in CANONICAL_METRICS:
        return ST_BLOCKED, "Required evidence is unavailable from permitted sources."
    return ST_UNANALYZED, "No supported deterministic formula exists."


def build_requirements_checklist(
    requirements_text: str,
    facts: Dict[str, Dict[str, Any]],
    external_variables: Optional[List[Dict[str, Any]]] = None,
    missing: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Parse the requirements sentence and resolve each into a checklist
    row: Requirement | Status | Result | Evidence.

    For supported formulas the checklist consults the Sprint 7 Formula
    Engine (deterministic, no AI) so a requirement like "calculate ROA"
    is reported as DERIVED when its verified inputs exist, BLOCKED when
    they do not — never guessed, never silently pasted. Reported facts
    are returned as VERIFIED without recalculation (engine rule)."""
    rows: List[Dict[str, Any]] = []
    for item in parse_requirements(requirements_text):
        metric = item["metric"]
        # Sprint 9 reliability: a structurally uncertain extraction is
        # surfaced as REVIEW_REQUIRED before any engine/reported resolution
        # - it must never be promoted to VERIFIED by the workspace.
        fact = facts.get(metric)
        if isinstance(fact, dict) and str(fact.get("extraction_state")) == "review_required":
            rows.append({
                "requirement": metric,
                "metric": metric,
                "status": ST_REVIEW_REQUIRED,
                "status_label": STATUS_LABELS[ST_REVIEW_REQUIRED],
                "result": (_fmt_compact(_fact_value(fact)) if _fact_value(fact) is not None else "-"),
                "detail": str(fact.get("extraction_state_reason") or "Extraction reliability flags this as uncertain."),
                "evidence": str(fact.get("evidence") or fact.get("document_name") or ""),
                "periods": item.get("periods") or [],
            })
            continue
        engine_row = None
        if metric in SUPPORTED_FORMULAS or metric in CANONICAL_METRICS:
            try:
                res = calculate_metric_with_variables(
                    metric, facts, external_variables,
                    context={"recover": False},
                )
            except Exception:
                res = None
            if res is not None and res.get("workspace_status") in (
                ST_VERIFIED, ST_DERIVED, ST_EXTERNAL_DERIVED, ST_STUDENT_INPUT,
            ):
                engine_row = {
                    "requirement": metric,
                    "metric": metric,
                    "status": res["workspace_status"],
                    "status_label": res["workspace_status_label"],
                    "result": res.get("display_value") or "—",
                    "detail": res.get("workspace_note") or "",
                    "evidence": res.get("lineage") or "",
                    "periods": item.get("periods") or [],
                }
        if engine_row is not None:
            rows.append(engine_row)
            continue
        status, detail = resolve_metric_status(metric, facts, external_variables, missing)
        fact = facts.get(metric)
        result = "—"
        evidence = ""
        if isinstance(fact, dict) and _fact_value(fact) is not None:
            result = _fmt_compact(_fact_value(fact))
            evidence = (
                str(fact.get("evidence") or "")
                or str(fact.get("document_name") or "")
            )
        rows.append({
            "requirement": metric,
            "metric": metric,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "result": result,
            "detail": detail,
            "evidence": evidence,
            "periods": item.get("periods") or [],
        })
    return rows


# ---------------------------------------------------------------------------
# Canonical normalization of the fact graph
# ---------------------------------------------------------------------------


def normalize_facts(
    facts: Dict[str, Dict[str, Any]],
    company: str = "Company A",
) -> List[Dict[str, Any]]:
    """Build the normalized fact list: canonical concept, original label,
    company, period, value, unit, currency, source, page, evidence,
    provenance, confidence and normalization status.

    Facts whose label is ambiguous or unmapped get status
    REVIEW_REQUIRED — they are carried but never merged into a canonical
    comparison bucket.
    """
    out: List[Dict[str, Any]] = []
    for metric, fact in facts.items():
        canonical, conf, reason = canonicalize_metric(metric)
        tier = _fact_tier(fact)
        norm_status = ST_VERIFIED if conf in ("high", "medium") else ST_REVIEW_REQUIRED
        # Sprint 9 reliability: a structurally uncertain extraction stays
        # REVIEW_REQUIRED even when the label itself normalizes cleanly.
        if isinstance(fact, dict) and str(fact.get("extraction_state")) == "review_required":
            norm_status = ST_REVIEW_REQUIRED
            reason = str(fact.get("extraction_state_reason") or "Extraction reliability flags this as uncertain.")
        value = _fact_value(fact)
        page = fact.get("page")
        page_s = ""
        if isinstance(page, (int, float)):
            page_s = f"p. {int(page)}"
        elif page:
            page_s = str(page)
        out.append({
            "metric": str(metric),
            "canonical": canonical or "—",
            "original_label": str(metric),
            "company": company,
            "period": fact.get("reporting_period") or fact.get("period") or "—",
            "value": value,
            "display_value": _fmt_compact(value) if value is not None else "—",
            "unit": fact.get("unit") or "—",
            "scale": fact.get("scale") or "—",
            "currency": (
                fact.get("currency_code")
                or fact.get("currency")
                or fact.get("unit")
                or "—"
            ),
            "source": fact.get("source") or fact.get("document_name") or "—",
            "page": page_s,
            "evidence": fact.get("evidence") or "",
            "provenance_tier": tier,
            "confidence": conf,
            "normalization_status": norm_status,
            "normalization_reason": reason,
        })
    return out


# ---------------------------------------------------------------------------
# Multi-company comparison (two companies, Sprint 10)
# ---------------------------------------------------------------------------


def _value_map(company_facts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index normalized facts by canonical concept (only auto-normalized
    facts; ambiguous ones are excluded from automatic comparison)."""
    by_canonical: Dict[str, Dict[str, Any]] = {}
    for f in company_facts:
        canon = f.get("canonical")
        if not canon or canon == "—":
            continue
        if f.get("confidence") not in ("high", "medium"):
            continue
        # Prefer the fact with a real value.
        cur = by_canonical.get(canon)
        if cur is None or (cur.get("value") is None and f.get("value") is not None):
            by_canonical[canon] = f
    return by_canonical


def build_comparison(
    company_a: str,
    facts_a: Dict[str, Dict[str, Any]],
    company_b: str,
    facts_b: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Two-company comparison aligned on canonical metrics.

    Every row retains company identity, canonical metric, original label,
    period, unit, currency, provenance, evidence and normalization status.
    If one side cannot be normalized safely, the row is flagged
    REVIEW_REQUIRED — the comparison is never forced.
    """
    norm_a = normalize_facts(facts_a, company=company_a)
    norm_b = normalize_facts(facts_b, company=company_b)
    map_a = _value_map(norm_a)
    map_b = _value_map(norm_b)

    # Facts that failed normalization on either side -> review rows.
    review_rows: List[Dict[str, Any]] = []
    for side, facts in ((company_a, norm_a), (company_b, norm_b)):
        for f in facts:
            if f.get("confidence") not in ("high", "medium"):
                review_rows.append({
                    "canonical": f.get("canonical") or f.get("original_label"),
                    "metric": f.get("original_label"),
                    "company": f.get("company"),
                    "period": f.get("period"),
                    "status": ST_REVIEW_REQUIRED,
                    "status_label": STATUS_LABELS[ST_REVIEW_REQUIRED],
                    "reason": f.get("normalization_reason") or "Cannot normalize safely.",
                    "side_value": f.get("display_value"),
                })

    rows: List[Dict[str, Any]] = []
    for canonical in sorted(set(map_a) | set(map_b)):
        fa = map_a.get(canonical)
        fb = map_b.get(canonical)
        va = fa.get("value") if fa else None
        vb = fb.get("value") if fb else None
        if va is not None and vb is not None and vb != 0:
            diff = (vb - va) / abs(vb) * 100.0
            diff_s = f"{diff:+.1f}%"
        else:
            diff = None
            diff_s = "—"
        if fa and fb:
            status, reason = ST_VERIFIED, "Both sides normalized and comparable."
        elif fa is None:
            status, reason = ST_BLOCKED, f"{company_b} has no comparable '{canonical}' fact."
        else:
            status, reason = ST_BLOCKED, f"{company_a} has no comparable '{canonical}' fact."
        rows.append({
            "canonical": canonical,
            "metric": canonical,
            "company_a": company_a,
            "company_b": company_b,
            "label_a": (fa or {}).get("original_label") or "—",
            "label_b": (fb or {}).get("original_label") or "—",
            "period": (fa or fb or {}).get("period") or "—",
            "value_a": _fmt_compact(va) if va is not None else "Not disclosed",
            "value_b": _fmt_compact(vb) if vb is not None else "Not disclosed",
            "difference": diff_s,
            "difference_pct": diff,
            "unit": (fa or fb or {}).get("unit") or "—",
            "currency": (fa or fb or {}).get("currency") or "—",
            "status": status,
            "status_label": STATUS_LABELS[status],
            "reason": reason,
            "evidence_a": (fa or {}).get("evidence") or "",
            "evidence_b": (fb or {}).get("evidence") or "",
        })

    return {
        "active": bool(rows) or bool(review_rows),
        "company_a": company_a,
        "company_b": company_b,
        "rows": rows,
        "review_rows": review_rows,
    }


# ---------------------------------------------------------------------------
# Period-over-period driver analysis (evidence-backed only)
# ---------------------------------------------------------------------------

_DRIVER_TARGETS = {
    "ROE": {"formula": "Net Profit / Equity", "components": ["Net Profit", "Equity"]},
    "ROA": {"formula": "Net Profit / Assets", "components": ["Net Profit", "Assets"]},
    "Profit Margin": {"formula": "Net Profit / Revenue", "components": ["Net Profit", "Revenue"]},
}


def build_driver_analysis(
    period_facts: Dict[str, Dict[str, str]],
    company: str = "Company A",
) -> Dict[str, Any]:
    """Period-over-period driver analysis.

    Input: {metric: {period: value_string}} — a deterministic map of
    verified period values (from the fact graph, a fixture, or student
    input labeled as such upstream).

    Output: observations (deterministic % changes for metrics with >= 2
    periods) and causes. A cause is only stated when ALL components of a
    known driver relationship are present for the two periods AND the
    observed direction is internally consistent; otherwise the exact
    qualifier "Cause not established from available evidence." is used.
    """
    observations: List[Dict[str, Any]] = []
    for metric, periods in sorted(period_facts.items()):
        pairs = sorted(periods.items())
        for i in range(1, len(pairs)):
            prev_p, prev_v = pairs[i - 1]
            cur_p, cur_v = pairs[i]
            try:
                prev_f = float(str(prev_v).replace(",", ""))
                cur_f = float(str(cur_v).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if prev_f == 0:
                continue
            change = (cur_f - prev_f) / abs(prev_f) * 100.0
            observations.append({
                "metric": metric,
                "from": prev_p,
                "to": cur_p,
                "from_value": _fmt_compact(prev_f),
                "to_value": _fmt_compact(cur_f),
                "change_pct": change,
                "change_display": f"{change:+.1f}%",
                "direction": "increase" if change > 0 else ("decrease" if change < 0 else "flat"),
            })

    causes: List[Dict[str, Any]] = []
    for target, cfg in _DRIVER_TARGETS.items():
        comps = cfg["components"]
        if target not in period_facts or not all(comp in period_facts for comp in comps):
            continue
        try:
            target_pairs = sorted(period_facts[target].items())
            if len(target_pairs) < 2:
                continue
            t_prev, t_cur = target_pairs[-2], target_pairs[-1]
            delta = {}
            for comp in comps:
                p = sorted(period_facts[comp].items())
                if len(p) < 2:
                    raise ValueError
                delta[comp] = float(str(p[-1][1]).replace(",", "")) - float(str(p[-2][1]).replace(",", ""))
            t_delta = float(str(t_cur[1]).replace(",", "")) - float(str(t_prev[1]).replace(",", ""))
        except (TypeError, ValueError, IndexError):
            continue
        if t_delta == 0:
            continue
        direction = "increased" if t_delta > 0 else "decreased"
        direction_noun = "increase" if t_delta > 0 else "decrease"
        # Deterministic contribution statement built ONLY from verified
        # component movements; never a causal claim.
        parts = []
        for comp in comps:
            d = delta.get(comp, 0.0)
            if d > 0:
                parts.append(f"higher {comp}")
            elif d < 0:
                parts.append(f"lower {comp}")
            else:
                parts.append(f"flat {comp}")
        if parts:
            causes.append({
                "target": target,
                "period_from": t_prev[0],
                "period_to": t_cur[0],
                "statement": (
                    f"{target} {direction} from {t_prev[0]} to {t_cur[0]}. "
                    f"Observed contribution: {' combined with '.join(parts)} "
                    f"contributed to the {direction_noun} in {target}."
                ),
                "evidence": f"Component values are verified {company} period data.",
            })
        else:
            causes.append({
                "target": target,
                "period_from": t_prev[0],
                "period_to": t_cur[0],
                "statement": "Cause not established from available evidence.",
                "evidence": "",
            })

    if not causes:
        causes.append({
            "target": "—",
            "period_from": "—",
            "period_to": "—",
            "statement": "Cause not established from available evidence.",
            "evidence": "",
        })

    return {
        "active": bool(observations),
        "company": company,
        "observations": observations,
        "causes": causes,
        "periods": sorted({p for m in period_facts.values() for p in m}),
    }


# ---------------------------------------------------------------------------
# External variables (student-entered, never document data)
# ---------------------------------------------------------------------------


def add_external_variable(
    variables: List[Dict[str, Any]],
    name: str,
    value: Any,
    unit: str = "",
    period: str = "",
    origin: str = "Student entered",
    source: str = "",
    currency: str = "",
) -> List[Dict[str, Any]]:
    """Append one student-entered external variable. Always labeled
    STUDENT_INPUT (🟡); can never be presented as document-verified."""
    variables = list(variables or [])
    variables.append({
        "name": str(name).strip(),
        "value": value,
        "unit": str(unit or "").strip(),
        "currency": str(currency or "").strip(),
        "period": str(period or "").strip(),
        "origin": str(origin or "Student entered").strip(),
        "source": str(source or "").strip(),
        "verification_status": "student_entered",
        "student_entered": True,
        "status": ST_STUDENT_INPUT,
        "status_label": STATUS_LABELS[ST_STUDENT_INPUT],
    })
    return variables


def _external_fact(var: Dict[str, Any]) -> Dict[str, Any]:
    """Shape an external variable as a fact for the Formula Engine, with
    explicit STUDENT_INPUT provenance metadata. Never fabricates a source."""
    return {
        "value": var.get("value"),
        "source": "Student input",
        "provenance_tier": "STUDENT_INPUT",
        "unit": var.get("unit") or "",
        "reporting_period": var.get("period") or "",
        "currency_code": var.get("currency") or "",
    }


# ---------------------------------------------------------------------------
# Deterministic calculation (Sprint 7 Formula Engine, C++ when available)
# ---------------------------------------------------------------------------


def calculate_metric_with_variables(
    metric_key: str,
    facts: Dict[str, Dict[str, Any]],
    external_variables: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one metric through the Formula Engine.

    External variables are injected ONLY as primary facts carrying
    STUDENT_INPUT provenance — the engine's own rules mean document facts
    always win over primary facts (never a silent substitution). The
    returned record keeps the engine's full lineage; when any used input
    was student-entered, the workspace status is relabeled STUDENT_INPUT
    so the student-input nature is never hidden.
    """
    ctx = dict(context or {})
    primary = dict(ctx.get("primary_facts") or {})
    for var in external_variables or []:
        name = str(var.get("name") or "").strip()
        if name and var.get("value") is not None:
            primary[name] = _external_fact(var)
    ctx["primary_facts"] = primary
    res = calculate_metric(metric_key, dict(facts), ctx)

    # Detect student-input usage from the engine's per-input provenance.
    used_student = False
    for inp in res.get("inputs") or []:
        if str(inp.get("provenance_tier")) == "STUDENT_INPUT":
            used_student = True
            break
    if used_student and res.get("status") in (ST_DERIVED, ST_EXTERNAL_DERIVED, "derived", "external_derived"):
        res = dict(res)
        res["student_input_used"] = True
        res["workspace_status"] = ST_STUDENT_INPUT
        res["workspace_status_label"] = STATUS_LABELS[ST_STUDENT_INPUT]
        res["workspace_note"] = (
            "Calculated by the Formula Engine using a student-entered "
            "input — not derived solely from document-verified facts."
        )
    else:
        res["workspace_status"] = {
            "reported": ST_VERIFIED,
            "derived": ST_DERIVED,
            "external_derived": ST_EXTERNAL_DERIVED,
            "blocked": ST_BLOCKED,
            "unanalyzed": ST_UNANALYZED,
        }.get(res.get("status"), ST_UNANALYZED)
        res["workspace_status_label"] = STATUS_LABELS[res["workspace_status"]]
        res["workspace_note"] = res.get("reason") or res.get("error")
    res["metric"] = metric_key
    return res


# ---------------------------------------------------------------------------
# Workspace assembly
# ---------------------------------------------------------------------------

DEFAULT_ASSIGNMENT = "Financial Ratio Analysis"
DEFAULT_COMPANY = "Company A"


def build_student_workspace(
    module3_result: Optional[Dict[str, Any]],
    assignment_type: str = DEFAULT_ASSIGNMENT,
    requirements_text: str = "",
    external_variables: Optional[List[Dict[str, Any]]] = None,
    company_a: str = DEFAULT_COMPANY,
    peer_company: Optional[str] = None,
    peer_facts: Optional[Dict[str, Dict[str, Any]]] = None,
    period_facts: Optional[Dict[str, Dict[str, str]]] = None,
    calc_metrics: Optional[List[str]] = None,
    missing: Optional[Dict[str, Any]] = None,
    workspace_documents: Optional[List[Dict[str, Any]]] = None,
    qualitative_documents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the complete deterministic student workspace dict consumed
    by the UI, the memo presenter and the Excel working model.

    Never generates a conclusion, never guesses a missing value.
    """
    facts = collect_facts(module3_result)
    external_variables = external_variables or []
    missing = missing if missing is not None else (
        (module3_result or {}).get("missing_data") or {}
    )

    requirements = build_requirements_checklist(
        requirements_text, facts, external_variables, missing
    )
    normalized = normalize_facts(facts, company=company_a)

    comparison: Dict[str, Any] = {
        "active": False, "company_a": company_a, "company_b": None,
        "rows": [], "review_rows": [],
    }
    if peer_company and peer_facts:
        comparison = build_comparison(company_a, facts, peer_company, peer_facts)

    driver = build_driver_analysis(period_facts or {}, company=company_a)

    # Sprint 11 - Evidence-backed qualitative catalyst & driver analysis.
    # Deterministic layer over the verified fact graph: numerical change
    # -> numerical driver -> candidate catalyst -> source evidence ->
    # evidence/relationship classification -> student-facing explanation.
    # Never uses a review-required fact as a verified foundation and never
    # invents a numerical change for a blocked metric (fail closed).
    qualitative = build_qualitative_drivers(
        driver.get("observations") or [],
        facts=facts,
        period_facts=period_facts or {},
        qualitative_documents=qualitative_documents or [],
        requirements=requirements,
        company=company_a,
    )

    # Deterministic calculations for the requested/available metrics.
    calcs: List[Dict[str, Any]] = []
    for metric in calc_metrics or []:
        res = calculate_metric_with_variables(
            metric, facts, external_variables,
            context={
                "company_context": {"name": company_a},
                "reporting_period": None,
                "workspace_documents": workspace_documents or [],
                "recover": False,
            },
        )
        # Honest annotation: when the engine blocks a metric whose value
        # is ALREADY reported in the fact graph, say so instead of only
        # showing the blocked reason (the data exists; recomputation is
        # what is unavailable).
        if res.get("status") == "blocked":
            fact = facts.get(metric)
            if isinstance(fact, dict) and _fact_value(fact) is not None:
                res = dict(res)
                res["reported_fact_value"] = _fmt_compact(_fact_value(fact))
                res["reported_fact_source"] = str(fact.get("source") or "")
        calcs.append(res)

    return {
        "assignment_type": assignment_type,
        "company": company_a,
        "requirements": requirements,
        "normalized_facts": normalized,
        "comparison": comparison,
        "driver_analysis": driver,
        "qualitative_drivers": qualitative,
        "external_variables": external_variables,
        "calculations": calcs,
        "canonical_count": len(normalized),
    }
