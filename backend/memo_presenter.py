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
                refs = _build_evidence(rows)
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
    Metric | Value | Period | Source (professional adds Status)."""
    if not key_rows:
        return None
    use_status = profile == "professional"
    headers = ["Metric", "Value", "Period", "Source"]
    if use_status:
        headers.insert(1, "Status")
    rows_out = []
    for r in key_rows:
        metric = str(r.get("metric") or r.get("Metric") or "—")
        value = str(r.get("Value") or r.get("value") or "—")
        status = str(r.get("Status") or r.get("status") or "—")
        period = str(r.get("Period") or r.get("reporting_period") or "—")
        source = str(r.get("Source") or r.get("source") or "—")
        if value in ("", "None"):
            value = "—"
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


def _build_evidence(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Evidence references from the fact graph - only real fields are used;
    missing provenance fields render as '—'. Never fabricates."""
    refs: List[Dict[str, str]] = []
    seen = set()
    for r in rows or []:
        metric = str(r.get("metric") or r.get("Metric") or "")
        if not metric or metric in seen:
            continue
        seen.add(metric)
        page = r.get("page")
        page_s = f"p. {page}" if isinstance(page, int) else (str(page or "—"))
        doc = str(r.get("document_name") or r.get("source_ref") or "—")
        evidence = str(r.get("evidence") or "—")
        refs.append({
            "label": metric,
            "value": str(r.get("Value") or r.get("value") or "—"),
            "source": doc,
            "period": str(r.get("reporting_period") or r.get("Period") or "—"),
            "page": page_s,
            "evidence": evidence,
        })
    return refs


# ---------------------------------------------------------------------------
# Small helpers used by tests
# ---------------------------------------------------------------------------

SUPPORTED_PROFILES = sorted(_SUPPORTED)
PROFILE_LABELS = {k: v["label"] for k, v in PROFILES.items()}
