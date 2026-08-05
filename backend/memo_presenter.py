"""
Financial Timeline Engine
Sprint 8 - Module B: Student + Professional Adaptive Memo Presenter

A PURE presentation layer (no Streamlit import) that renders the SAME
verified memo content through a workspace-specific presentation profile.

Hard rules
----------
* NEVER performs financial calculations - the C++ Formula Engine (Sprint 7)
  is the deterministic authority; this module only REORGANIZES verified
  values and real memo prose into presentation blocks.
* NEVER invents content: tables/bullets/evidence refs are built ONLY from
  the supplied fact graph (`rows`) and the supplied memo text. Missing
  data renders the allowed qualifier "Information not disclosed in source
  filings." or is omitted entirely.
* The evidence-card interaction is untouched: metric names/values are
  returned as plain text blocks and the app marks them clickable with the
  existing span machinery before rendering.

Block model
-----------
render_memo(...) returns an ordered list of blocks:
    ("heading",  title)
    ("para",     plain text)
    ("bullets",  [item, ...])
    ("table",    {"title", "headers", "rows": [[cell, ...], ...],
                  "notes": [str]})
    ("evidence", [{"label", "value", "source", "period", "page", "evidence"}])
    ("note",     text)

Profiles
--------
student     - learning-first: explanatory paragraphs, clean metric table,
              bullets for trends/risks/takeaways, plain terminology.
professional - speed + density: comparison table, driver bullets,
              strategic implications, recommendations, evidence refs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Profile configuration
# ---------------------------------------------------------------------------

# Preferred display order for the key-metrics table (only rows present in the
# fact graph are used; missing ones are simply skipped - never invented).
_KEY_ORDER = [
    "Revenue", "Net Profit", "Operating Profit", "EBITDA", "EPS",
    "Operating Cash Flow", "Assets", "Equity", "Debt", "Liabilities",
    "Current Assets", "Current Liabilities", "ROE", "ROA", "Profit Margin",
    "Operating Margin", "Current Ratio", "Debt to Equity",
    "Revenue Growth", "EPS Growth", "CAGR",
]

# Known memo section headings (all-caps, own line) found in generated memos,
# mapped to the semantic sections each profile exposes. Any section that has
# no text in the memo is rendered as a "note" qualifier or omitted.
_KNOWN_HEADINGS = [
    "EXECUTIVE SUMMARY", "KEY FINANCIAL EVENTS", "FINANCIAL PERFORMANCE",
    "KEY TRENDS", "RISKS & OPPORTUNITIES", "KEY DRIVERS",
    "STRATEGIC IMPLICATIONS", "RECOMMENDATIONS", "KEY TAKEAWAYS",
    "SOURCES & EVIDENCE", "EVIDENCE / SOURCES",
]

PROFILES: Dict[str, Dict[str, Any]] = {
    "student": {
        "label": "Student",
        "sections": [
            ("Executive Summary", {"source": "EXECUTIVE SUMMARY", "kind": "para"}),
            ("Key Financial Metrics", {"source": None, "kind": "table"}),
            ("Financial Performance", {"source": "FINANCIAL PERFORMANCE", "kind": "para"}),
            ("Key Trends", {"source": "KEY FINANCIAL EVENTS", "kind": "bullets"}),
            ("Risks & Opportunities", {"source": "RISKS & OPPORTUNITIES", "kind": "bullets"}),
            ("Key Takeaways", {"source": "RECOMMENDATIONS", "kind": "bullets"}),
            ("Sources & Evidence", {"source": None, "kind": "evidence"}),
        ],
    },
    "professional": {
        "label": "Professional",
        "sections": [
            ("Executive Summary", {"source": "EXECUTIVE SUMMARY", "kind": "para"}),
            ("Key Financials", {"source": None, "kind": "table"}),
            ("Financial Performance", {"source": "FINANCIAL PERFORMANCE", "kind": "para"}),
            ("Key Drivers", {"source": "KEY FINANCIAL EVENTS", "kind": "bullets"}),
            ("Risks & Opportunities", {"source": "RISKS & OPPORTUNITIES", "kind": "bullets"}),
            ("Strategic Implications", {"source": "STRATEGIC IMPLICATIONS", "kind": "para"}),
            ("Recommendations", {"source": "RECOMMENDATIONS", "kind": "bullets"}),
            ("Evidence / Sources", {"source": None, "kind": "evidence"}),
        ],
    },
}

_SUPPORTED = {"student", "professional"}


# ---------------------------------------------------------------------------
# Memo text -> sections
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> List[str]:
    """Split a paragraph into sentence bullets (real text only, never
    invented). Short fragments and empty pieces are dropped."""
    out = []
    for part in (text or "").split(". "):
        part = part.strip().rstrip(".")
        if len(part) >= 25:
            out.append(part + ".")
    return out


def parse_memo_sections(memo_text: str) -> Dict[str, str]:
    """Split the memo into {HEADING: body text}. A heading is a known
    ALL-CAPS line; everything before the first heading is attached to
    EXECUTIVE SUMMARY. Unknown lines stay attached to the current heading.
    Deterministic; never invents text."""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in (memo_text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper in _KNOWN_HEADINGS and len(line) <= 60:
            current = upper
            sections.setdefault(current, [])
            continue
        if current is None:
            current = "EXECUTIVE SUMMARY"
            sections.setdefault(current, [])
        sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items() if v}


def get_section(sections: Dict[str, str], heading: Optional[str]) -> Optional[str]:
    if not heading:
        return None
    return sections.get(heading)


# ---------------------------------------------------------------------------
# Fact-graph helpers
# ---------------------------------------------------------------------------


def select_key_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Key metrics for the profile table, in canonical display order,
    using ONLY rows present in the fact graph. Rows without a usable
    value are excluded (they render as '—' elsewhere if at all)."""
    by_name = {}
    for r in rows or []:
        name = str(r.get("metric") or r.get("Metric") or "").strip()
        if not name:
            continue
        by_name.setdefault(name, r)
    ordered = []
    for name in _KEY_ORDER:
        if name in by_name:
            ordered.append(by_name.pop(name))
    for name in sorted(by_name.keys()):
        ordered.append(by_name[name])
    return ordered


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------


def render_memo(
    memo_text: str,
    rows: List[Dict[str, Any]],
    profile: str = "professional",
    include_evidence: bool = True,
) -> List[Tuple[str, Any]]:
    """Render the memo through the requested profile as structured blocks.

    Args:
        memo_text: the continuous memo draft (same text used today).
        rows: the verified Financial Fact Graph rows (grid rows).
        profile: "student" | "professional".
        include_evidence: False omits the Sources & Evidence section.

    Returns:
        Ordered list of ("heading", ...) / ("para", ...) / ("bullets", ...)
        / ("table", ...) / ("evidence", ...) / ("note", ...) blocks.
    """
    profile = (profile or "professional").lower()
    if profile not in _SUPPORTED:
        profile = "professional"
    cfg = PROFILES[profile]
    sections = parse_memo_sections(memo_text)
    key_rows = select_key_rows(rows)
    blocks: List[Tuple[str, Any]] = []

    for title, spec in cfg["sections"]:
        kind = spec["kind"]
        src = spec.get("source")
        text = get_section(sections, src) if src else None

        if kind == "table":
            table = _build_table(key_rows, profile)
            if table:
                blocks.append(("heading", title))
                blocks.append(("table", table))
            continue

        if kind == "evidence":
            if include_evidence:
                refs = _build_evidence(rows, profile)
                blocks.append(("heading", title))
                if refs:
                    blocks.append(("evidence", refs))
                else:
                    blocks.append(("note", "No evidence references available."))
            continue

        if not text:
            # Section has no text in the source memo -> allowed qualifier.
            blocks.append(("heading", title))
            blocks.append(("note", "Information not disclosed in source filings."))
            continue

        if kind == "para":
            blocks.append(("heading", title))
            blocks.append(("para", text))
        elif kind == "bullets":
            items = _split_sentences(text)
            blocks.append(("heading", title))
            if items:
                blocks.append(("bullets", items))
            else:
                blocks.append(("para", text))

    return blocks


def _build_table(
    key_rows: List[Dict[str, Any]], profile: str
) -> Optional[Dict[str, Any]]:
    """Metric comparison table from the fact graph. Columns:
    Metric | Value | Period | Source (professional adds Status).
    Missing cells render EMPTY (never '—'): unavailable data is omitted
    from the visible document, exactly like a finished financial table."""
    if not key_rows:
        return None
    use_status = profile == "professional"
    headers = ["Metric", "Value", "Period", "Source"]
    if use_status:
        headers.insert(1, "Status")
    rows_out = []
    for r in key_rows:
        metric = str(r.get("metric") or r.get("Metric") or "")
        value = _clean_field(r.get("Value") or r.get("value"))
        status = _clean_field(r.get("Status") or r.get("status"))
        period = _clean_field(r.get("Period") or r.get("reporting_period"))
        source = _clean_field(r.get("Source") or r.get("source"))
        if use_status:
            rows_out.append([metric, status, value, period, source])
        else:
            rows_out.append([metric, value, period, source])
    return {
        "title": "Key Financial Metrics",
        "headers": headers,
        "rows": rows_out,
        "notes": ["Values shown as reported/derived by the verification pipeline."],
    }


_CURRENCY_SYMBOLS = {
    "USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥",
    "CAD": "$", "AUD": "$", "SGD": "$", "CNY": "¥", "CHF": "CHF ",
}


def _currency_prefix(unit: str, value: str) -> str:
    """Presentation-only currency prefix derived from REAL unit metadata.
    Applied only when the unit is known and the value does not already
    carry a currency symbol. Never modifies the underlying value."""
    if not unit or not value:
        return ""
    if any(sym in value for sym in ("$", "₹", "€", "£", "¥")):
        return ""
    return _CURRENCY_SYMBOLS.get(str(unit).strip().upper(), "")


def _fact_of(row: Dict[str, Any]) -> Dict[str, Any]:
    f = row.get("_fact")
    return f if isinstance(f, dict) else {}


def _clean_field(value) -> str:
    """Normalize a provenance field: real text passes through, '—'/None/
    empty become '' so missing provenance is OMITTED from the visible memo
    (never shown as a placeholder)."""
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s in ("", "—", "None", "none", "N/A", "n/a") else s


def _build_evidence(rows: List[Dict[str, Any]], profile: str = "professional") -> List[Dict[str, Any]]:
    """Evidence references from the fact graph (rows + their `_fact`). Each
    ref carries the FULL real provenance internally (empty string when the
    pipeline did not provide it — never '—') plus pre-computed per-profile
    display `lines` containing ONLY real fields. Never fabricates."""
    refs: List[Dict[str, Any]] = []
    seen = set()
    for r in rows or []:
        metric = str(r.get("metric") or r.get("Metric") or "")
        if not metric or metric in seen:
            continue
        seen.add(metric)
        fact = _fact_of(r)
        kind = str(r.get("_kind") or "")
        if kind not in ("verified", "derived", "blocked", "conflict", "unanalyzed"):
            kind = "verified"
        value = _clean_field(r.get("Value") or r.get("value"))
        period = _clean_field(fact.get("reporting_period") or r.get("Period") or r.get("period"))
        source = _clean_field(
            fact.get("document_name") or fact.get("source_ref")
            or fact.get("source") or r.get("Source") or r.get("source")
        )
        page = fact.get("page") or r.get("page")
        if isinstance(page, bool):
            page_s = ""
        elif isinstance(page, (int, float)):
            page_s = f"p. {int(page)}"
        else:
            page_s = _clean_field(page)
            if page_s and not page_s.startswith("p."):
                page_s = f"p. {page_s}"
        evidence = _clean_field(fact.get("evidence") or r.get("evidence"))
        formula = _clean_field(fact.get("formula") or r.get("formula"))
        unit = _clean_field(fact.get("unit") or r.get("unit"))
        inputs = fact.get("inputs") or r.get("inputs") or []
        if isinstance(inputs, str):
            inputs = [i.strip() for i in inputs.split(",") if i.strip()]
        inputs = [str(i).strip() for i in inputs if str(i).strip()]
        reason = _clean_field(
            fact.get("blocked_reason") or fact.get("reason")
            or r.get("_reason") or r.get("blocked_reason")
        )
        ref = {
            "label": metric, "value": value, "source": source,
            "period": period, "page": page_s, "evidence": evidence,
            "formula": formula, "inputs": inputs, "kind": kind,
            "unit": unit,
            "status": _clean_field(r.get("Status") or r.get("status")),
            "blocked_reason": reason,
        }
        ref["lines"] = _evidence_lines(ref, profile)
        refs.append(ref)
    return refs


def _evidence_lines(ref: Dict[str, Any], profile: str) -> List[str]:
    """Per-profile display lines for one evidence ref. ONLY real fields
    produce lines; missing provenance is omitted entirely (never '—')."""
    kind = ref.get("kind", "verified")
    lines: List[str] = []
    if kind == "blocked":
        lines.append("Blocked")
        if ref.get("blocked_reason"):
            lines.append(ref["blocked_reason"])
        return lines
    if kind == "conflict":
        lines.append("Cross-document verification conflict")
        if ref.get("evidence"):
            lines.append(ref["evidence"])
        return lines
    if kind == "derived":
        if profile == "student":
            inputs = ref.get("inputs") or []
            if inputs:
                if len(inputs) == 1:
                    lines.append(f"Calculated from {inputs[0]}")
                else:
                    lines.append(f"Calculated from {', '.join(inputs[:-1])} and {inputs[-1]}")
            elif ref.get("formula"):
                lines.append(f"Calculated using: {ref['formula']}")
            else:
                lines.append("Calculated")
        else:  # professional
            if ref.get("period"):
                lines.append(f"Calculated · {ref['period']}")
            else:
                lines.append("Calculated")
            if ref.get("formula"):
                lines.append(ref["formula"])
            if ref.get("inputs"):
                lines.append("Inputs: " + ", ".join(ref["inputs"]))
        return lines
    # verified / default
    if profile == "student":
        if ref.get("source"):
            lines.append(f"Source: {ref['source']}")
        if ref.get("period"):
            lines.append(f"Period: {ref['period']}")
        if ref.get("evidence"):
            lines.append(f"Evidence: {ref['evidence']}")
        elif ref.get("page"):
            lines.append(f"Evidence: {ref['page']}")
    else:  # professional
        src_period = " · ".join(p for p in (ref.get("source"), ref.get("period")) if p)
        if src_period:
            lines.append(src_period)
        if ref.get("evidence"):
            lines.append(ref["evidence"])
        elif ref.get("page"):
            lines.append(ref["page"])
    return lines


# ---------------------------------------------------------------------------
# Small helpers used by tests
# ---------------------------------------------------------------------------

SUPPORTED_PROFILES = sorted(_SUPPORTED)
PROFILE_LABELS = {k: v["label"] for k, v in PROFILES.items()}
