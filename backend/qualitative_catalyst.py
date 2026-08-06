"""
Financial Timeline Engine
Sprint 11 - Evidence-Backed Qualitative Catalyst & Driver Analysis

A DETERMINISTIC qualitative layer above the verified fact graph.

The goal is NOT to generate generic AI explanations. The goal is:

    Financial Change
    -> Numerical Driver
    -> Candidate Qualitative Catalyst
    -> Source Evidence
    -> Evidence/Relationship Classification
    -> Student-Facing Explanation
    -> Student Judgment

Hard rules
----------
* NO Streamlit, NO AI, NO network, NO randomness, NO time-dependent logic.
  The module is pure and fully deterministic: identical inputs always
  produce byte-identical outputs.
* The C++ Formula Engine (Sprint 7) remains the calculation authority.
  This module NEVER performs financial arithmetic for derived metrics and
  NEVER moves qualitative reasoning into C++.
* NEVER fabricates narrative evidence. Every extracted qualitative item
  retains real provenance (document, page, section, snippet, reporting
  period, extraction method, confidence, source location). Where
  provenance is unavailable it stays "—"; it is never invented.
* Causality safeguards: the system distinguishes OBSERVED CHANGE from
  DISCLOSED CAUSE from POSSIBLE EXPLANATION. A relationship is only
  EXPLICITLY_DISCLOSED when the source itself states it. Nothing here
  ever writes "X caused the entire decline" unless the filing states it.
* Fail-closed: a review-required numerical fact is never used as a
  verified qualitative foundation; a blocked metric never gets an
  invented numerical change; missing evidence => CAUSE_NOT_ESTABLISHED.
* The student's final conclusion is NEVER generated here.

Relationship states
-------------------
  EXPLICITLY_DISCLOSED   🟢 the filing directly states the relationship
  EVIDENCE_SUPPORTED     🔵 evidence strongly supports, causality not explicit
  POSSIBLE_RELATIONSHIP  🟡 evidence may be relevant, judgment required
  INSUFFICIENT_EVIDENCE  🟠 relevant evidence could not be established confidently
  CAUSE_NOT_ESTABLISHED  🔴 the source does not establish the cause
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Relationship states
# ---------------------------------------------------------------------------

REL_EXPLICIT = "EXPLICITLY_DISCLOSED"
REL_SUPPORTED = "EVIDENCE_SUPPORTED"
REL_POSSIBLE = "POSSIBLE_RELATIONSHIP"
REL_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
REL_CAUSE_NOT_ESTABLISHED = "CAUSE_NOT_ESTABLISHED"

RELATIONSHIP_LABELS: Dict[str, str] = {
    REL_EXPLICIT: "🟢 EXPLICITLY_DISCLOSED",
    REL_SUPPORTED: "🔵 EVIDENCE_SUPPORTED",
    REL_POSSIBLE: "🟡 POSSIBLE_RELATIONSHIP",
    REL_INSUFFICIENT: "🟠 INSUFFICIENT_EVIDENCE",
    REL_CAUSE_NOT_ESTABLISHED: "🔴 CAUSE_NOT_ESTABLISHED",
}

_REL_RANK: Dict[str, int] = {
    REL_EXPLICIT: 0,
    REL_SUPPORTED: 1,
    REL_POSSIBLE: 2,
    REL_INSUFFICIENT: 3,
    REL_CAUSE_NOT_ESTABLISHED: 4,
}

_CAUSALITY_NOTES: Dict[str, str] = {
    REL_EXPLICIT: (
        "The filing explicitly states this relationship; the filing does "
        "not establish that it was the sole cause."
    ),
    REL_SUPPORTED: (
        "The evidence is consistent with the relationship, but the filing "
        "does not explicitly establish causality."
    ),
    REL_POSSIBLE: (
        "The evidence may be relevant; student judgment is required before "
        "treating it as a driver."
    ),
    REL_INSUFFICIENT: (
        "Relevant evidence could not be established confidently from the "
        "permitted sources."
    ),
    REL_CAUSE_NOT_ESTABLISHED: "Cause not established from permitted evidence.",
}

# ---------------------------------------------------------------------------
# Catalyst taxonomy — a deterministic controlled vocabulary. Each category
# carries explicit keyword patterns. Ambiguous / unmatched narrative stays
# UNCLASSIFIED (REVIEW REQUIRED) rather than being forced into a category.
# ---------------------------------------------------------------------------

CATALYST_TAXONOMY: List[Tuple[str, str, List[str]]] = [
    ("REVENUE_GROWTH", "Revenue growth", [
        r"revenue\s+growth", r"revenue\s+increased", r"revenue\s+grew",
        r"sales\s+growth", r"sales\s+increased", r"higher\s+revenue",
        r"increased\s+revenue", r"growth\s+in\s+revenue", r"top[- ]line\s+growth",
    ]),
    ("REVENUE_DECLINE", "Revenue decline", [
        r"revenue\s+declined", r"revenue\s+decreased", r"sales\s+declined",
        r"lower\s+revenue", r"decline\s+in\s+revenue", r"decreased\s+revenue",
        r"revenue\s+fell",
    ]),
    ("PRICING", "Pricing", [
        r"pricing\s+power", r"price\s+increases?", r"price\s+decreases?",
        r"higher\s+prices", r"price\s+cuts?", r"price\s+reductions?",
        r"prices\s+rose", r"prices\s+declined", r"favorable\s+pricing",
    ]),
    ("VOLUME", "Volume", [
        r"volume", r"volumes", r"unit\s+volume", r"higher\s+volume",
        r"lower\s+volume", r"volume\s+growth", r"shipments", r"units\s+sold",
        r"sales\s+volume",
    ]),
    ("PRODUCT_MIX", "Product/service mix", [
        r"product\s+mix", r"service\s+mix", r"sales\s+mix", r"mix\s+shift",
        r"favorable\s+mix", r"unfavorable\s+mix", r"product\s+portfolio",
        r"segment\s+mix", r"mix\s+of\s+products",
    ]),
    ("INPUT_COSTS", "Input costs", [
        r"input\s+costs?", r"cost\s+of\s+goods", r"raw\s+material",
        r"raw\s+materials", r"material\s+costs?", r"commodity\s+prices?",
        r"cost\s+inflation", r"supply\s+chain", r"costs?\s+of\s+inputs",
        r"higher\s+input",
    ]),
    ("OPERATING_EXPENSES", "Operating expenses", [
        r"operating\s+expenses?", r"operating\s+costs?", r"sg&a",
        r"selling,\s*general\s+and\s+administrative", r"overhead",
        r"operating\s+expenditure", r"operating\s+cost", r"expense\s+discipline",
    ]),
    ("EMPLOYEE_COSTS", "Employee costs", [
        r"employee", r"compensation", r"salaries", r"headcount",
        r"labor\s+costs?", r"labour\s+costs?", r"staff\s+costs?",
        r"workforce", r"payroll", r"employee\s+costs?",
    ]),
    ("INTEREST_EXPENSE", "Interest expense", [
        r"interest\s+expense", r"interest\s+costs?", r"net\s+interest",
        r"borrowing\s+costs?", r"finance\s+costs?", r"interest\s+expense\s+increased",
        r"interest\s+expense\s+decreased",
    ]),
    ("TAXES", "Taxes", [
        r"\btax(?:es)?\b", r"tax\s+rate", r"effective\s+tax", r"tax\s+provision",
        r"income\s+tax", r"tax\s+expense", r"tax\s+legislation", r"tax\s+reform",
    ]),
    ("FOREIGN_EXCHANGE", "Foreign exchange", [
        r"foreign\s+exchange", r"\bcurrency\b", r"\bfx\b", r"exchange\s+rate",
        r"translation", r"forex", r"currency\s+movements?", r"currency\s+impact",
    ]),
    ("ACQUISITIONS", "Acquisitions", [
        r"acquisition", r"acquired", r"merger", r"acquisitions",
        r"acquisition\s+of", r"acquiring",
    ]),
    ("DIVESTITURES", "Divestitures", [
        r"divestiture", r"divested", r"sale\s+of", r"discontinued\s+operations",
        r"spin[- ]off", r"divest",
    ]),
    ("RESTRUCTURING", "Restructuring", [
        r"restructuring", r"reorganization", r"reorganisation", r"severance",
        r"cost\s+reduction\s+program", r"efficiency\s+program", r"workforce\s+reduction",
    ]),
    ("ONE_TIME", "One-time items", [
        r"one[- ]time", r"non-recurring", r"special\s+item", r"one[- ]off",
        r"unusual\s+item",
    ]),
    ("IMPAIRMENTS", "Impairments", [
        r"impairment", r"impairments", r"write[- ]down", r"write[- ]downs",
        r"write[- ]off", r"writedown", r"goodwill\s+impairment",
    ]),
    ("DEBT_CHANGES", "Debt changes", [
        r"\bdebt\b", r"borrowings", r"repayment", r"refinancing",
        r"debt\s+issuance", r"net\s+debt", r"long[- ]term\s+debt",
        r"debt\s+reduction", r"debt\s+increased", r"debt\s+decreased",
    ]),
    ("CAPITAL_STRUCTURE", "Capital structure", [
        r"capital\s+structure", r"capital\s+allocation", r"share\s+buyback",
        r"buyback", r"dividend", r"share\s+repurchase", r"capital\s+returns",
    ]),
    ("REGULATORY", "Regulatory events", [
        r"regulatory", r"regulation", r"regulations", r"regulatory\s+approval",
        r"compliance", r"regulatory\s+change", r"regulatory\s+requirements",
        r"sanctions",
    ]),
    ("LEGAL", "Legal/penalty events", [
        r"legal", r"litigation", r"lawsuit", r"penalty", r"penalties",
        r"\bfine\b", r"\bfines\b", r"settlement", r"proceeding", r"proceedings",
    ]),
    ("SEGMENT", "Segment performance", [
        r"\bsegment\b", r"segments", r"segment\s+performance",
        r"business\s+segment", r"operating\s+segment", r"division",
        r"geographic\s+segment",
    ]),
    ("OTHER", "Other disclosed operating factors", [
        r"market\s+conditions", r"competition", r"competitive", r"industry",
        r"macroeconomic", r"economic\s+conditions", r"customer\s+demand",
        r"\bdemand\b", r"operating\s+factors",
    ]),
]

_CATALYST_KEYWORDS: List[Tuple[str, List[re.Pattern]]] = [
    (cat_id, [re.compile(kw, re.IGNORECASE) for kw in kws])
    for cat_id, _label, kws in CATALYST_TAXONOMY
]

_CATALYST_LABEL: Dict[str, str] = {
    cat_id: label for cat_id, label, _kws in CATALYST_TAXONOMY
}

# ---------------------------------------------------------------------------
# Narrative section headings (already-ingested documents, no web search)
# ---------------------------------------------------------------------------

_SECTION_HEADINGS: List[Tuple[str, List[re.Pattern]]] = [
    ("Management Discussion & Analysis", [
        re.compile(r"management'?s?\s+discussion\s+and\s+analysis", re.IGNORECASE),
        re.compile(r"\bmda\b", re.IGNORECASE),
    ]),
    ("Financial Statement Notes", [
        re.compile(r"notes\s+to\s+(?:consolidated\s+)?financial\s+statements", re.IGNORECASE),
        re.compile(r"notes\s+to\s+the\s+financial\s+statements", re.IGNORECASE),
    ]),
    ("Risk Factors", [
        re.compile(r"risk\s+factors", re.IGNORECASE),
    ]),
    ("Business / Segment Discussion", [
        re.compile(r"segment\s+information", re.IGNORECASE),
        re.compile(r"business\s+segments", re.IGNORECASE),
        re.compile(r"results\s+of\s+operations", re.IGNORECASE),
        re.compile(r"business\s+overview", re.IGNORECASE),
        re.compile(r"segments?\s+and\s+geographic", re.IGNORECASE),
    ]),
    ("Liquidity & Capital Resources", [
        re.compile(r"liquidity\s+and\s+capital\s+resources", re.IGNORECASE),
    ]),
    ("Regulatory / Disclosure", [
        re.compile(r"regulatory\s+and\s+compliance", re.IGNORECASE),
        re.compile(r"government\s+regulation", re.IGNORECASE),
        re.compile(r"regulatory\s+matters", re.IGNORECASE),
    ]),
    ("Legal Proceedings", [
        re.compile(r"legal\s+proceedings", re.IGNORECASE),
        re.compile(r"litigation", re.IGNORECASE),
    ]),
    ("Critical Accounting Estimates", [
        re.compile(r"critical\s+accounting", re.IGNORECASE),
    ]),
]

_PAGE_RE = re.compile(r"==========\s*PAGE\s+(\d+)\s*==========", re.IGNORECASE)
_FY_RE = re.compile(r"\bFY\s?20\d\d\b", re.IGNORECASE)

_MAX_ITEM_CHARS = 900
_MIN_ITEM_CHARS = 30


# ---------------------------------------------------------------------------
# Narrative extraction
# ---------------------------------------------------------------------------


def _looks_narrative(line: str) -> bool:
    """A line counts as narrative when it contains real words (>= 3) and is
    not dominated by numbers (financial-table noise)."""
    words = re.findall(r"[A-Za-z]{2,}", line)
    if len(words) < 3:
        return False
    tokens = line.split()
    numeric = sum(1 for t in tokens if re.search(r"\d", t))
    if tokens and (numeric / len(tokens)) > 0.55:
        return False
    return True


def _match_heading(line: str) -> Optional[str]:
    if len(line.strip()) > 90:
        return None
    for section_name, patterns in _SECTION_HEADINGS:
        for p in patterns:
            if p.search(line):
                return section_name
    return None


def _reporting_period(text: str) -> str:
    m = _FY_RE.search(text or "")
    return m.group(0) if m else "—"


def extract_narrative_items(
    qualitative_documents: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Deterministic heading-based narrative extraction from already-ingested
    document text. Page markers ('========== PAGE N ==========') carry page
    provenance when present; missing provenance stays '—' (never invented).

    Returns a sorted list of evidence items:
        {document, page, section, text, reporting_period, extraction_method,
         extraction_confidence, source_location}
    """
    items: List[Dict[str, Any]] = []
    for doc in qualitative_documents or []:
        name = str(doc.get("document_name") or doc.get("file_name") or "—")
        text = str(doc.get("text") or "")
        period = _reporting_period(name + " " + text)

        # Split into pages by markers; keep running page number for items
        # that span pages (page = where the section heading appeared).
        pages: List[Tuple[Optional[int], str]] = []
        current_page: Optional[int] = None
        for raw_line in text.splitlines():
            pm = _PAGE_RE.search(raw_line)
            if pm:
                current_page = int(pm.group(1))
                continue
            pages.append((current_page, raw_line))

        current_section: Optional[str] = None
        section_page: Optional[int] = None
        buf: List[str] = []
        total_chars = 0

        def flush() -> None:
            nonlocal current_section, section_page, buf, total_chars
            if current_section is None:
                return
            body = " ".join(buf).strip()
            body = re.sub(r"\s+", " ", body)
            if len(body) >= _MIN_ITEM_CHARS:
                confidence = (
                    "HIGH" if len(buf) <= 1 else "MEDIUM"
                )
                items.append({
                    "document": name,
                    "page": section_page,
                    "section": current_section,
                    "text": body[:_MAX_ITEM_CHARS],
                    "reporting_period": period,
                    "extraction_method": "heading-based narrative extraction",
                    "extraction_confidence": confidence,
                    "source_location": (
                        f"p. {section_page}" if section_page is not None else ""
                    ),
                })
            current_section = None
            section_page = None
            buf = []
            total_chars = 0

        for page_no, line in pages:
            stripped = line.strip()
            if not stripped:
                continue
            heading = _match_heading(stripped)
            if heading is not None:
                flush()
                current_section = heading
                section_page = page_no
                continue
            if current_section is not None and _looks_narrative(stripped):
                if total_chars < _MAX_ITEM_CHARS:
                    buf.append(stripped)
                    total_chars += len(stripped) + 1
        flush()

    return sorted(items, key=lambda i: (
        i["document"], i["page"] if i["page"] is not None else -1,
        i["section"], i["text"],
    ))


# ---------------------------------------------------------------------------
# Catalyst classification (controlled vocabulary)
# ---------------------------------------------------------------------------


def classify_catalysts(text: str) -> List[str]:
    """Return the deterministic, sorted list of catalyst category ids whose
    keywords appear in the text. Empty list => UNCLASSIFIED / REVIEW REQUIRED
    (never force a category)."""
    low = (text or "").lower()
    found: List[str] = []
    for cat_id, patterns in _CATALYST_KEYWORDS:
        for p in patterns:
            if p.search(low):
                found.append(cat_id)
                break
    return sorted(found)


def catalyst_label(cat_id: str) -> str:
    return _CATALYST_LABEL.get(cat_id, cat_id)


# ---------------------------------------------------------------------------
# Numerical driver mapping (metric -> underlying financial driver)
# ---------------------------------------------------------------------------

_DRIVER_COMPONENTS: Dict[str, List[str]] = {
    "ROE": ["Net Profit", "Equity"],
    "ROA": ["Net Profit", "Assets"],
    "Profit Margin": ["Net Profit", "Revenue"],
    "Operating Margin": ["Operating Profit", "Revenue"],
    "Current Ratio": ["Current Assets", "Current Liabilities"],
    "Debt to Equity": ["Debt", "Equity"],
    "Revenue Growth": ["Revenue"],
    "EPS Growth": ["EPS"],
    "CAGR": ["Revenue"],
}

_METRIC_ALIASES: Dict[str, List[str]] = {
    "ROE": ["return on equity", "roe"],
    "ROA": ["return on assets", "roa"],
    "Profit Margin": ["profit margin", "net margin", "net profit margin"],
    "Operating Margin": ["operating margin"],
    "Revenue": ["revenue", "sales", "net sales", "revenues"],
    "Net Profit": ["net profit", "net income", "net earnings", "profitability"],
    "Operating Profit": ["operating profit", "operating income", "ebit"],
    "Operating Cash Flow": ["operating cash flow", "cash from operations"],
    "Assets": ["total assets", "assets"],
    "Equity": ["shareholders' equity", "stockholders' equity", "equity"],
    "Debt": ["total debt", "long-term debt", "debt"],
    "Liabilities": ["total liabilities", "liabilities"],
    "Current Assets": ["current assets"],
    "Current Liabilities": ["current liabilities"],
    "EPS": ["earnings per share", "eps"],
    "EBITDA": ["ebitda"],
    "Revenue Growth": ["revenue growth", "revenue"],
    "EPS Growth": ["earnings per share growth", "eps growth"],
    "CAGR": ["cagr", "compound annual growth"],
    "Current Ratio": ["current ratio", "liquidity position"],
    "Debt to Equity": ["debt-to-equity", "debt to equity", "leverage", "gearing"],
}


def _metric_keywords(metric: str) -> List[str]:
    """Metric aliases plus its underlying component names — so evidence about
    the numerical driver (e.g. Net Profit for ROE) counts as relevant."""
    kws = list(_METRIC_ALIASES.get(metric, [str(metric).lower()]))
    for comp in _DRIVER_COMPONENTS.get(metric, []):
        for a in _METRIC_ALIASES.get(comp, [str(comp).lower()]):
            if a not in kws:
                kws.append(a)
    return kws


def primary_numerical_driver(
    metric: str,
    period_facts: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[str, str]:
    """Return (driver_name, driver_change_display) for the metric's period
    change. The primary numerical driver is the component with the largest
    |% change| across the periods; ties resolve alphabetically. When no
    component breakdown exists, the driver is the metric itself (its own
    change). Never invents values — only period_facts are used."""
    period_facts = period_facts or {}
    components = _DRIVER_COMPONENTS.get(metric, [])
    if not components:
        return metric, "—"

    best_comp: Optional[str] = None
    best_pct = -1.0
    for comp in sorted(components):
        pairs = sorted((period_facts.get(comp) or {}).items())
        if len(pairs) < 2:
            continue
        try:
            prev = float(str(pairs[-2][1]).replace(",", ""))
            cur = float(str(pairs[-1][1]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if prev == 0:
            continue
        pct = abs((cur - prev) / abs(prev) * 100.0)
        if pct > best_pct:
            best_pct = pct
            best_comp = comp
    if best_comp is None:
        return metric, "—"
    pairs = sorted((period_facts.get(best_comp) or {}).items())
    try:
        prev = float(str(pairs[-2][1]).replace(",", ""))
        cur = float(str(pairs[-1][1]).replace(",", ""))
        pct = (cur - prev) / abs(prev) * 100.0 if prev else 0.0
        return best_comp, f"{pct:+.1f}%"
    except (TypeError, ValueError, IndexError):
        return best_comp, "—"


# ---------------------------------------------------------------------------
# Evidence -> driver matching
# ---------------------------------------------------------------------------

_CAUSALITY_MARKERS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"due\s+to", r"driven\s+by", r"resulting\s+from", r"as\s+a\s+result\s+of",
        r"because\s+of", r"attributable\s+to", r"reflecting", r"reflected",
        r"contributed\s+to", r"led\s+to", r"was\s+driven", r"primarily\s+due",
        r"impact\s+of", r"result\s+of", r"arising\s+from", r"caused\s+by",
    )
]

# Which catalyst categories are plausibly relevant to each metric's
# domain. An evidence item only becomes a candidate when it mentions the
# metric (or one of its numerical-driver components) OR carries at least one
# relevant catalyst. This prevents unrelated narrative (e.g. a revenue-growth
# paragraph) from being reported as "possible" evidence for every metric.
_RELEVANT_CATALYSTS: Dict[str, set] = {
    "Revenue": {
        "REVENUE_GROWTH", "REVENUE_DECLINE", "PRICING", "VOLUME",
        "PRODUCT_MIX", "FOREIGN_EXCHANGE", "ACQUISITIONS", "DIVESTITURES",
        "SEGMENT",
    },
    "Net Profit": {
        "INPUT_COSTS", "OPERATING_EXPENSES", "EMPLOYEE_COSTS", "TAXES",
        "ONE_TIME", "IMPAIRMENTS", "REVENUE_GROWTH", "REVENUE_DECLINE",
        "RESTRUCTURING", "FOREIGN_EXCHANGE", "DIVESTITURES", "ACQUISITIONS",
        "PRICING", "VOLUME",
    },
    "Operating Profit": {
        "INPUT_COSTS", "OPERATING_EXPENSES", "EMPLOYEE_COSTS", "PRICING",
        "VOLUME", "RESTRUCTURING", "ONE_TIME", "IMPAIRMENTS",
        "FOREIGN_EXCHANGE", "REVENUE_GROWTH", "REVENUE_DECLINE", "SEGMENT",
    },
    "Operating Cash Flow": {
        "INPUT_COSTS", "OPERATING_EXPENSES", "EMPLOYEE_COSTS", "VOLUME",
        "FOREIGN_EXCHANGE",
    },
    "Equity": {"CAPITAL_STRUCTURE", "DEBT_CHANGES"},
    "Assets": {"IMPAIRMENTS", "ACQUISITIONS", "DIVESTITURES", "FOREIGN_EXCHANGE", "ONE_TIME"},
    "Debt": {"DEBT_CHANGES", "INTEREST_EXPENSE", "CAPITAL_STRUCTURE"},
    "Liabilities": {"DEBT_CHANGES", "INTEREST_EXPENSE"},
    "Current Assets": set(),
    "Current Liabilities": set(),
    "Current Ratio": set(),
    "Debt to Equity": {"DEBT_CHANGES", "INTEREST_EXPENSE", "CAPITAL_STRUCTURE"},
    "EPS": {"PRICING", "VOLUME", "PRODUCT_MIX", "INPUT_COSTS", "TAXES", "ONE_TIME"},
    "EBITDA": {"INPUT_COSTS", "OPERATING_EXPENSES", "EMPLOYEE_COSTS", "REVENUE_GROWTH", "REVENUE_DECLINE"},
    "Revenue Growth": {"REVENUE_GROWTH", "REVENUE_DECLINE", "PRICING", "VOLUME", "PRODUCT_MIX", "SEGMENT"},
    "EPS Growth": {"PRICING", "VOLUME", "PRODUCT_MIX", "INPUT_COSTS"},
    "CAGR": {"REVENUE_GROWTH", "REVENUE_DECLINE", "PRICING", "VOLUME", "SEGMENT"},
}

_AMBIGUITY_MARKERS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"may\s+affect", r"could\s+affect", r"might\s+affect",
        r"may\s+be\s+affected", r"could\s+be\s+affected",
        r"potential", r"possibly", r"uncertain", r"subject\s+to",
        r"may\s+increase", r"may\s+decrease", r"could\s+increase",
        r"could\s+decrease",
    )
]


def _relevant_catalysts(metric: str) -> set:
    """Union of the metric's own relevant catalyst categories plus those of
    its numerical-driver components (e.g. ROE inherits Net Profit's)."""
    relevant = set(_RELEVANT_CATALYSTS.get(metric, set()))
    for comp in _DRIVER_COMPONENTS.get(metric, []):
        relevant |= _RELEVANT_CATALYSTS.get(comp, set())
    return relevant


def _match_evidence(
    metric: str,
    items: List[Dict[str, Any]],
) -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
    """Deterministically match the metric to the best narrative item and
    classify the relationship. Returns (relationship, catalysts, best_item).

    Relevance gate: an item is only a candidate when it mentions the metric
    (or a numerical-driver component) OR carries at least one catalyst that
    is plausibly relevant to the metric's domain. This keeps unrelated
    narrative from being reported as evidence for every metric (fail-closed)."""
    keywords = [re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in _metric_keywords(metric)]
    relevant = _relevant_catalysts(metric)

    scored: List[Tuple[int, Dict[str, Any], List[str], bool, bool]] = []
    for item in items:
        text = item.get("text") or ""
        cats = classify_catalysts(text)
        relevant_cats = [c for c in cats if c in relevant]
        metric_hit = any(p.search(text) for p in keywords)
        causality = any(p.search(text) for p in _CAUSALITY_MARKERS)
        ambiguous = any(p.search(text) for p in _AMBIGUITY_MARKERS)
        if not metric_hit and not relevant_cats:
            continue
        # Metric mention dominates; catalysts are secondary evidence weight.
        score = (100 if metric_hit else 0) + (2 * len(relevant_cats)) + (20 if causality else 0) - (5 if ambiguous else 0)
        scored.append((score, item, relevant_cats, causality, ambiguous))

    if not scored:
        return REL_CAUSE_NOT_ESTABLISHED, [], None

    # Deterministic tie-break: highest score, then document/page/section/text.
    scored.sort(key=lambda s: (
        s[0],
        s[1].get("document", ""),
        s[1].get("page") if s[1].get("page") is not None else -1,
        s[1].get("section", ""),
        s[1].get("text", ""),
    ), reverse=True)
    _score, best, relevant_cats, causality, ambiguous = scored[0]

    metric_hit = any(p.search(best.get("text") or "") for p in keywords)
    cats = relevant_cats

    if metric_hit and cats and causality and not ambiguous:
        rel = REL_EXPLICIT
    elif metric_hit and cats:
        rel = REL_SUPPORTED
    elif cats and not metric_hit:
        rel = REL_POSSIBLE
    else:
        # The source mentions the metric but discloses no catalyst: relevant
        # evidence could not be established confidently.
        rel = REL_INSUFFICIENT

    # Ambiguity hedges downgrade one level (deterministic).
    if ambiguous and rel == REL_EXPLICIT:
        rel = REL_SUPPORTED
    elif ambiguous and rel == REL_SUPPORTED:
        rel = REL_POSSIBLE
    elif ambiguous and rel == REL_POSSIBLE:
        rel = REL_INSUFFICIENT

    return rel, cats, best


# ---------------------------------------------------------------------------
# Student-facing explanations (causality-safe, deterministic)
# ---------------------------------------------------------------------------


def _direction_verb(direction: str) -> str:
    return {
        "increase": "increased",
        "decrease": "decreased",
        "flat": "remained flat",
    }.get(str(direction).lower(), "changed")


def student_explanation(
    metric: str,
    obs: Dict[str, Any],
    rel: str,
    cats: List[str],
    best: Optional[Dict[str, Any]],
) -> str:
    direction = _direction_verb(obs.get("direction") or "change")
    change = str(obs.get("change_display") or "—")
    from_p = str(obs.get("from") or "—")
    to_p = str(obs.get("to") or "—")
    catalysts = " and ".join(catalyst_label(c) for c in cats) if cats else "—"

    if rel == REL_EXPLICIT and best:
        snippet = re.sub(r"\s+", " ", (best.get("text") or ""))[:220]
        return (
            f"{metric} {direction} from {from_p} to {to_p} ({change}). "
            f"The filing explicitly discloses a relationship with {catalysts}: "
            f"\"{snippet}…\". This is a source-disclosed explanation; the filing "
            f"does not establish that this factor was the sole cause. Student "
            f"interpretation is required."
        )
    if rel == REL_SUPPORTED and best:
        section = best.get("section") or "narrative section"
        page = f", p. {best.get('page')}" if best.get("page") is not None else ""
        snippet = re.sub(r"\s+", " ", (best.get("text") or ""))[:180]
        return (
            f"{metric} {direction} from {from_p} to {to_p} ({change}). "
            f"Narrative evidence in {section}{page} mentions {catalysts}, "
            f"which is consistent with the observed change, but the filing "
            f"does not explicitly establish that this factor was the cause. "
            f"Evidence: \"{snippet}…\". Student interpretation is required."
        )
    if rel == REL_POSSIBLE:
        return (
            f"{metric} {direction} from {from_p} to {to_p} ({change}). "
            f"Evidence relevant to {catalysts} may exist, but the filing does "
            f"not directly connect it to {metric}. Student judgment is required "
            f"before treating it as a driver."
        )
    if rel == REL_INSUFFICIENT:
        return (
            f"{metric} {direction} from {from_p} to {to_p} ({change}). "
            f"Relevant evidence could not be established confidently from the "
            f"permitted sources. Student judgment is required."
        )
    return (
        f"{metric} {direction} from {from_p} to {to_p} ({change}). "
        f"Cause not established from permitted evidence."
    )


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_qualitative_drivers(
    observations: List[Dict[str, Any]],
    facts: Optional[Dict[str, Dict[str, Any]]] = None,
    period_facts: Optional[Dict[str, Dict[str, str]]] = None,
    qualitative_documents: Optional[List[Dict[str, Any]]] = None,
    requirements: Optional[List[Dict[str, Any]]] = None,
    company: str = "Company A",
) -> Dict[str, Any]:
    """Build the deterministic Qualitative Drivers & Catalysts section for
    the Student Assignment Workspace.

    Fail-closed gates (Sprint 9 reliability):
      * metric fact with extraction_state == 'review_required'
        => REVIEW_REQUIRED, never a verified qualitative foundation
      * metric listed BLOCKED in the requirements checklist
        => CAUSE_NOT_ESTABLISHED, no invented numerical change
      * no narrative corpus / no match => CAUSE_NOT_ESTABLISHED
    """
    facts = facts or {}
    period_facts = period_facts or {}
    req_status = {
        str(r.get("requirement")): r.get("status") for r in (requirements or [])
    }
    items = extract_narrative_items(qualitative_documents)

    rows: List[Dict[str, Any]] = []
    for obs in sorted(observations, key=lambda o: (str(o.get("metric") or ""), str(o.get("from") or ""))):
        metric = str(obs.get("metric") or "")
        if not metric:
            continue

        fact = facts.get(metric) if isinstance(facts, dict) else None
        review_required = (
            isinstance(fact, dict)
            and str(fact.get("extraction_state")) == "review_required"
        )
        blocked = req_status.get(metric) == "BLOCKED"

        if review_required:
            rows.append({
                "metric": metric,
                "period_from": str(obs.get("from") or "—"),
                "period_to": str(obs.get("to") or "—"),
                "from_value": str(obs.get("from_value") or "—"),
                "to_value": str(obs.get("to_value") or "—"),
                "change_display": str(obs.get("change_display") or "—"),
                "direction": str(obs.get("direction") or "change"),
                "numerical_driver": "—",
                "driver_change": "—",
                "catalyst": "—",
                "catalyst_categories": [],
                "relationship": REL_INSUFFICIENT,
                "relationship_label": "🟠 REVIEW_REQUIRED",
                "relationship_rank": 3,
                "evidence": "—",
                "evidence_full": "—",
                "source": "—",
                "page": "—",
                "section": "—",
                "confidence": "—",
                "reporting_period": "—",
                "extraction_method": "—",
                "source_location": "",
                "causality_note": (
                    "The numerical fact is flagged REVIEW_REQUIRED by extraction "
                    "reliability — it is not used as a verified qualitative "
                    "foundation. Student review is required."
                ),
                "student_explanation": (
                    f"{metric} is flagged REVIEW_REQUIRED — its numerical "
                    f"foundation is not verified, so no qualitative catalyst is "
                    f"claimed. Student review is required."
                ),
                "student_interpretation_required": True,
                "foundation": "REVIEW_REQUIRED",
                "foundation_note": str(fact.get("extraction_state_reason") or "Extraction reliability flags this as uncertain."),
            })
            continue

        if blocked:
            rows.append({
                "metric": metric,
                "period_from": str(obs.get("from") or "—"),
                "period_to": str(obs.get("to") or "—"),
                "from_value": str(obs.get("from_value") or "—"),
                "to_value": str(obs.get("to_value") or "—"),
                "change_display": str(obs.get("change_display") or "—"),
                "direction": str(obs.get("direction") or "change"),
                "numerical_driver": "—",
                "driver_change": "—",
                "catalyst": "—",
                "catalyst_categories": [],
                "relationship": REL_CAUSE_NOT_ESTABLISHED,
                "relationship_label": RELATIONSHIP_LABELS[REL_CAUSE_NOT_ESTABLISHED],
                "relationship_rank": _REL_RANK[REL_CAUSE_NOT_ESTABLISHED],
                "evidence": "—",
                "evidence_full": "—",
                "source": "—",
                "page": "—",
                "section": "—",
                "confidence": "—",
                "reporting_period": "—",
                "extraction_method": "—",
                "source_location": "",
                "causality_note": (
                    "Required numerical inputs are BLOCKED — the qualitative "
                    "layer does not invent a numerical change for a blocked metric."
                ),
                "student_explanation": (
                    f"{metric} is BLOCKED: required numerical inputs are "
                    f"unavailable, so no numerical change is analyzed and no "
                    f"qualitative catalyst is claimed."
                ),
                "student_interpretation_required": True,
                "foundation": "BLOCKED",
                "foundation_note": "Required evidence is unavailable from permitted sources.",
            })
            continue

        driver_name, driver_change = primary_numerical_driver(metric, period_facts)
        rel, cats, best = _match_evidence(metric, items)
        catalyst_display = (
            " and ".join(catalyst_label(c) for c in cats) if cats else "—"
        )

        rows.append({
            "metric": metric,
            "period_from": str(obs.get("from") or "—"),
            "period_to": str(obs.get("to") or "—"),
            "from_value": str(obs.get("from_value") or "—"),
            "to_value": str(obs.get("to_value") or "—"),
            "change_display": str(obs.get("change_display") or "—"),
            "direction": str(obs.get("direction") or "change"),
            "numerical_driver": driver_name,
            "driver_change": driver_change,
            "catalyst": catalyst_display,
            "catalyst_categories": cats,
            "relationship": rel,
            "relationship_label": RELATIONSHIP_LABELS[rel],
            "relationship_rank": _REL_RANK[rel],
            "evidence": (
                re.sub(r"\s+", " ", (best.get("text") or ""))[:220]
                if best else "—"
            ),
            "evidence_full": (best.get("text") or "—") if best else "—",
            "source": (best.get("document") or "—") if best else "—",
            "page": (best.get("page") if best and best.get("page") is not None else "—"),
            "section": (best.get("section") or "—") if best else "—",
            "confidence": (best.get("extraction_confidence") or "—") if best else "—",
            "reporting_period": (best.get("reporting_period") or "—") if best else "—",
            "extraction_method": (best.get("extraction_method") or "—") if best else "—",
            "source_location": (best.get("source_location") or "") if best else "",
            "causality_note": _CAUSALITY_NOTES[rel],
            "student_explanation": student_explanation(metric, obs, rel, cats, best),
            "student_interpretation_required": True,
            "foundation": "VERIFIED",
            "foundation_note": "",
        })

    rows.sort(key=lambda r: (
        r.get("relationship_rank", 9),
        str(r.get("metric") or ""),
        str(r.get("period_from") or ""),
    ))

    return {
        "active": bool(rows),
        "company": company,
        "rows": rows,
        "evidence_count": len(items),
        "documents": sorted({i.get("document") for i in items if i.get("document")}),
        "sections": sorted({i.get("section") for i in items if i.get("section")}),
    }
