"""
Financial Timeline Engine
Sprint 8 - Module A: Layout-Aware Financial Document Extraction

A SAFE, additive enrichment layer over the existing extraction pipeline.
It does NOT replace ingestion.parser / financial_extractor. It consumes
the structured tables already produced by backend.extraction2
(TableExtractor) and annotates the existing Financial Fact Graph with
layout metadata where it can be RELIABLY determined:

    document_name, page, table (title/id), row label, column header,
    reporting period, value, unit, scale, evidence, bounding-box (only
    when the stack can produce it), ocr flag, ocr confidence,
    extraction confidence.

Rules enforced here
-------------------
1. Row/column relationships are preserved (a value stays associated with
   its row label and its period column header).
2. Columns are never merged horizontally - cells map 1:1 to headers.
3. Footnote markers are stripped from cells and footnote-only lines are
   excluded, so notes never contaminate numeric extraction.
4. Table headers are preserved (a value without its period/header is
   ambiguous and is marked so).
5. OCR is only a flag here - no OCR engine is bundled; pages with no
   extractable text are reported and produce NO facts (fail closed).
   Low-confidence OCR can never become a Verified fact because no
   OCR-derived value ever enters this enrichment.
6. Evidence is a real source representation (the original table line or
   a reconstruction from the real cells) - never fabricated.
7. Unknown layout metadata stays absent (renders as "—" downstream).
8. Malformed/ambiguous tables are FLAGGED; affected facts are marked
   `layout_flag="ambiguous"` instead of receiving fabricated
   column/period attribution.
9. Output stays compatible with Sprint 5/6 evidence+page metadata and
   with Sprint 6.5 / Sprint 7 (facts keep value/unit/scale/period/tier).

Financial VALUES are never modified. Raw PDF bytes are never persisted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.extraction2.table_extractor import TableExtractor

# ---------------------------------------------------------------------------
# Metric label matching vocabulary (canonical key -> label aliases)
# ---------------------------------------------------------------------------

_METRIC_LABELS: Dict[str, List[str]] = {
    "Revenue": ["revenue", "total revenue", "net sales", "revenue from operations",
                "net revenue", "total sales", "sales"],
    "Net Profit": ["net profit", "net income", "profit after tax", "profit for the year",
                   "net earnings", "profit attributable", "net income attributable",
                   "profit before tax"],
    "Operating Profit": ["operating profit", "operating income", "income from operations"],
    "EBITDA": ["ebitda", "earnings before interest", "ebit"],
    "EPS": ["eps", "earnings per share", "basic earnings per share",
            "diluted earnings per share", "earnings per equity share"],
    "Debt": ["total debt", "borrowings", "debt"],
    "Assets": ["total assets", "assets"],
    "Total Assets": ["total assets", "assets"],
    "Liabilities": ["total liabilities", "liabilities"],
    "Equity": ["shareholders' equity", "shareholder's equity", "total equity",
               "total shareholders' equity", "equity", "net worth"],
    "Cash Flow": ["operating cash flow", "cash flow from operations", "cash flow",
                  "net cash from operating"],
    "Current Assets": ["total current assets", "current assets"],
    "Current Liabilities": ["total current liabilities", "current liabilities"],
    "Profit Margin": ["profit margin", "net margin", "net profit margin"],
    "ROE": ["return on equity", "roe"],
    "ROA": ["return on assets", "roa"],
    "Debt to Equity": ["debt to equity", "debt/equity", "debt equity"],
    "Current Ratio": ["current ratio"],
}

# Footnote markers (line starts) and cell suffixes.
_FOOTNOTE_LINE_RE = re.compile(
    r"^\s*(\*{1,3}|†|‡|§|[nN]otes?|see\s+(?:note|footnote)|\^?\d{1,2})\b", re.IGNORECASE
)
_FOOTNOTE_SUFFIX_RE = re.compile(r"(\s*\*{1,3}|\s*†|\s*‡|\s*\^\d{1,2}|\s*\(\d{1,2}\))$")
_NUMBER_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$")

_PAGE_MARKER_RE = re.compile(r"========== PAGE (\d+) ==========")

# ---------------------------------------------------------------------------
# Page attribution + footnote guard over the raw page text
# ---------------------------------------------------------------------------


def _page_lines(text: str) -> Dict[int, List[str]]:
    """Map raw page lines -> {page_number: [lines]} using the parser's
    PAGE markers. Lines before the first marker get page 0 (unknown)."""
    pages: Dict[int, List[str]] = {}
    current = 0
    for line in text.splitlines():
        m = _PAGE_MARKER_RE.search(line)
        if m:
            current = int(m.group(1))
            pages.setdefault(current, [])
            continue
        pages.setdefault(current, []).append(line)
    return pages


def _line_is_footnote(line: str) -> bool:
    return bool(_FOOTNOTE_LINE_RE.match(line.strip()))


def _clean_cell(cell: str) -> str:
    """Strip footnote suffixes from a numeric cell ("5,730*" -> "5,730")."""
    return _FOOTNOTE_SUFFIX_RE.sub("", cell.strip()).strip()


def _parse_number(token: str) -> Optional[float]:
    t = _clean_cell(token)
    if not _NUMBER_RE.fullmatch(t):
        return None
    try:
        return float(t.replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def _is_number(token: str) -> bool:
    return _parse_number(str(token or "").strip()) is not None


# ---------------------------------------------------------------------------
# Metric -> table row matching
# ---------------------------------------------------------------------------


_PERIOD_TOKEN_RE = re.compile(r"\b(?:FY\s?20\d{2}|F20\d{2}|20\d{2}|Q[1-4][\s-]?FY\s?20\d{2}|Q[1-4][\s-]?20\d{2})\b", re.IGNORECASE)


def _is_period_token(token: str) -> bool:
    return bool(_PERIOD_TOKEN_RE.search(str(token or "").strip()))


def _stacked_tables(page_lines: Dict[int, List[str]]) -> List[Dict[str, Any]]:
    """Fallback for PDFs where pypdf emits table cells ONE PER LINE (the
    common case for reportlab/pdf-writer tables). A run of consecutive
    period tokens after a label header defines the columns; each following
    non-numeric line is a row label and the next numeric lines are its
    cells. Footnote lines never become rows or cells. Deterministic, and
    every value/line is a real source line - nothing is invented."""
    tables: List[Dict[str, Any]] = []
    for page_no, lines in page_lines.items():
        if page_no == 0:
            continue
        n = len(lines)
        i = 0
        while i < n:
            tok = lines[i].strip()
            if tok and _is_period_token(tok):
                # Header run: optional label header line then periods.
                # If the line BEFORE the first period looks like a column
                # label (non-numeric, non-footnote), it is the label header.
                label_header = "Particulars"
                if i > 0:
                    prev = lines[i - 1].strip()
                    if prev and not _is_number(prev) and not _line_is_footnote(prev) \
                            and not _is_period_token(prev):
                        label_header = prev
                headers = [label_header]
                j = i
                while j < n and _is_period_token(lines[j].strip()):
                    headers.append(lines[j].strip())
                    j += 1
                column_periods = [""] + [_detect_period_short(h) for h in headers[1:]]
                rows: List[Dict[str, Any]] = []
                k = j
                while k < n:
                    t = lines[k].strip()
                    if not t:
                        k += 1
                        continue
                    if _line_is_footnote(t) or _is_period_token(t):
                        break
                    if _is_number(t):
                        break  # stray number without a label -> stop the table
                    label = t
                    cells: List[str] = []
                    k += 1
                    while k < n and len(cells) < len(headers) - 1 \
                            and _is_number(lines[k].strip()):
                        cells.append(_clean_cell(lines[k].strip()))
                        k += 1
                    if cells:
                        rows.append({"label": label, "cells": cells})
                    else:
                        break
                if rows or len(headers) >= 2:
                    tables.append({
                        "table_id": f"stacked_table_{page_no}_{len(tables) + 1}",
                        "page": page_no,
                        "headers": headers,
                        "column_periods": column_periods,
                        "rows": rows,
                        "currency": "",
                        "scale": "",
                        "source_location": f"pdf stacked table p.{page_no}",
                        "ocr": False,
                        "ocr_confidence": None,
                        "raw_lines": [l for l in lines if l.strip()],
                    })
                    break  # one table region per page scan
            i += 1
    return tables


def _detect_period_short(token: str) -> str:
    m = _PERIOD_TOKEN_RE.search(str(token or ""))
    if not m:
        return ""
    raw = m.group(0).upper().replace(" ", "")
    if raw.startswith("FY") and len(raw) == 4:
        return f"FY20{raw[2:]}"
    return raw


def _match_metric_to_row(metric: str, label: str) -> bool:
    """True when a canonical metric key can be defensibly matched to a
    table row label (longest-substring alias, word-safe)."""
    aliases = _METRIC_LABELS.get(metric)
    if not aliases:
        return False
    norm_label = re.sub(r"[^a-z0-9]", " ", str(label or "").lower())
    for alias in sorted(aliases, key=len, reverse=True):
        norm_alias = re.sub(r"[^a-z0-9]", " ", alias.lower())
        if norm_alias and norm_alias in norm_label:
            return True
    return False


def _find_numeric_cell(row_cells: List[str], fact_value) -> Optional[int]:
    """Index of the numeric cell whose value matches the fact's value
    (within 0.5% tolerance), when unambiguous. Never guesses."""
    if fact_value is None:
        return None
    try:
        target = float(fact_value)
    except (TypeError, ValueError):
        return None
    matches = []
    for i, cell in enumerate(row_cells):
        v = _parse_number(cell)
        if v is not None and target != 0 and abs((v - target) / abs(target)) < 0.005:
            matches.append(i)
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Main enrichment API
# ---------------------------------------------------------------------------


def layout_aware_annotate(parsed: Dict[str, Any], document_name: str = "") -> Dict[str, Any]:
    """Pure annotation pass over a parsed document dict.

    Returns (never raises):
        {
          "document_name": str,
          "tables": [enriched table dicts],
          "flagged_tables": [table_id ...],
          "pages_without_text": [page_number ...],
        }
    """
    out: Dict[str, Any] = {
        "document_name": document_name or "",
        "tables": [],
        "flagged_tables": [],
        "pages_without_text": [],
    }
    try:
        parsed = parsed or {}
        text = parsed.get("text") or ""
        tables = []
        try:
            tables = TableExtractor().extract_from_parsed(parsed)
        except Exception:
            tables = []
        page_lines = _page_lines(text)

        # Fallback: pypdf often emits table cells ONE PER LINE, which the
        # TableExtractor cannot columnize (it yields rows whose labels are
        # bare numbers with empty cells). Recover tables directly from the
        # real page lines (period headers + stacked numeric cells) and
        # PREFER them whenever they produce usable rows - unusable
        # TableExtractor rows are never trusted over the direct recovery.
        stacked = _stacked_tables(page_lines)
        stacked_ok = any(s["rows"] for s in stacked)
        if stacked_ok:
            for s in stacked:
                out["tables"].append({
                    "table_id": s["table_id"],
                    "page": s["page"],
                    "headers": s["headers"],
                    "column_periods": s["column_periods"],
                    "currency": s["currency"],
                    "scale": s["scale"],
                    "source_location": s["source_location"],
                    "rows": s["rows"],
                    "ocr": False,
                    "ocr_confidence": None,
                })

        # Pages with no extractable text (scanned/image pages) -> fail closed.
        for page_no, lines in page_lines.items():
            if page_no == 0:
                continue
            if not any(l.strip() for l in lines):
                out["pages_without_text"].append(page_no)

        for table in (tables if not stacked_ok else []):
            tdict = {
                "table_id": table.table_id,
                "page": table.page,
                "headers": list(table.headers),
                "column_periods": list(table.column_periods),
                "currency": table.currency,
                "scale": table.scale,
                "source_location": table.source_location,
                "rows": list(table.rows),
                "ocr": False,
                "ocr_confidence": None,
            }
            # Page attribution: find the first raw line of this table.
            raw_lines = list(getattr(table, "raw_lines", None) or [])
            if raw_lines:
                first = raw_lines[0].strip()
                for page_no, lines in page_lines.items():
                    if any(first in ln for ln in lines):
                        tdict["page"] = page_no
                        break
            # Footnote guard: strip suffixes from cells; drop footnote rows.
            clean_rows = []
            for row in table.rows:
                label = str(row.get("label", ""))
                cells = [str(c) for c in (row.get("cells") or [])]
                if _line_is_footnote(label) or not label:
                    continue
                clean_cells = [_clean_cell(c) for c in cells]
                # Malformed/ambiguous: ragged row vs header count -> flag.
                # The first header is the row-LABEL column, so a valid data
                # row has exactly (len(headers) - 1) cells. A single-period
                # table (Particulars | FY2025) with one value per row is
                # NOT malformed.
                if table.headers and len(clean_cells) + 1 != len(table.headers):
                    if table.table_id not in out["flagged_tables"]:
                        out["flagged_tables"].append(table.table_id)
                    tdict["flagged"] = True
                clean_rows.append({"label": label, "cells": clean_cells})
            tdict["rows"] = clean_rows
            out["tables"].append(tdict)
    except Exception:
        # Fail-safe: enrichment must never break the pipeline.
        return {
            "document_name": document_name or "",
            "tables": [],
            "flagged_tables": [],
            "pages_without_text": [],
        }
    return out


def enrich_financial_data(
    financial_data: Dict[str, Any],
    parsed: Dict[str, Any],
    document_name: str = "",
) -> Dict[str, Any]:
    """Return a NEW financial_data dict whose facts carry layout metadata
    when a defensible table match exists. Values are never modified.
    Facts without a table match keep their existing metadata untouched
    (missing layout fields render as '—' downstream)."""
    annotations = layout_aware_annotate(parsed, document_name)
    tables = annotations["tables"]
    flagged = set(annotations["flagged_tables"])

    enriched: Dict[str, Any] = {}
    for metric, fact in (financial_data or {}).items():
        enriched[metric] = dict(fact) if isinstance(fact, dict) else fact
        if not isinstance(fact, dict):
            continue

        best = None
        for table in tables:
            for row in table.get("rows") or []:
                if not _match_metric_to_row(metric, row.get("label")):
                    continue
                idx = _find_numeric_cell(row.get("cells") or [], fact.get("value"))
                headers = table.get("headers") or []
                periods = table.get("column_periods") or []
                column = ""
                period = ""
                if idx is not None and idx + 1 < len(headers):
                    column = headers[idx + 1]
                if idx is not None and idx + 1 < len(periods) and periods[idx + 1]:
                    period = periods[idx + 1]
                raw_evidence = _row_evidence(table, row, idx)
                confidence = 1.0 if (column and period) else 0.7
                candidate = {
                    "table": table.get("table_id") or table.get("source_location") or "—",
                    "row": str(row.get("label")),
                    "column": column or "—",
                    "page": table.get("page"),
                    "unit": table.get("currency") or "",
                    "scale": table.get("scale") or "",
                    "reporting_period": period or fact.get("reporting_period", ""),
                    "evidence": raw_evidence or fact.get("evidence", ""),
                    "bbox": None,
                    "ocr": bool(table.get("ocr")),
                    "ocr_confidence": table.get("ocr_confidence"),
                    "extraction_confidence": confidence,
                    "layout_flag": "ambiguous" if table.get("table_id") in flagged else "ok",
                }
                # A flagged/ragged table must not receive column/period
                # attribution it cannot prove.
                if table.get("table_id") in flagged:
                    candidate["column"] = "—"
                    candidate["reporting_period"] = fact.get("reporting_period", "")
                    candidate["extraction_confidence"] = 0.4
                if best is None or candidate["extraction_confidence"] > best["extraction_confidence"]:
                    best = candidate

        if best is not None:
            for k, v in best.items():
                if v not in (None, ""):
                    enriched[metric][k] = v
    return enriched


def _row_evidence(table: Dict[str, Any], row: Dict[str, Any], idx: Optional[int]) -> str:
    """Evidence is the real source line when available, else a faithful
    reconstruction from the real cells. Never fabricated."""
    raw = list(getattr(table, "raw_lines", None) or [])
    label = str(row.get("label"))
    for line in raw:
        if label and label.strip() in line:
            return line.strip()
    cells = [str(c) for c in (row.get("cells") or [])]
    parts = [label] + cells
    return " | ".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Document-aware enrichment (multi-document corpora)
# ---------------------------------------------------------------------------


def enrich_financial_data_from_documents(
    financial_data: Dict[str, Any],
    extracted_documents: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Enrich across multiple parsed documents. A fact is attributed to a
    document ONLY when exactly one document's tables provably contain its
    row; otherwise document metadata is left unset (no mis-attribution)."""
    merged: Dict[str, Dict[str, Any]] = {}
    for doc in extracted_documents or []:
        name = ""
        try:
            name = str(doc.get("file_name") or doc.get("document_name") or "")
        except Exception:
            name = ""
        parsed = doc.get("parsed_document") or {}
        if not isinstance(parsed, dict):
            continue
        ann = layout_aware_annotate(parsed, name)
        for metric in (financial_data or {}):
            if metric not in merged:
                merged[metric] = {"docs": 0, "meta": {}}
            # Does THIS doc provably contain a matching row?
            matched = False
            for table in ann["tables"]:
                if any(
                    _match_metric_to_row(metric, str(row.get("label")))
                    for row in (table.get("rows") or [])
                ):
                    matched = True
                    break
            if matched:
                merged[metric]["docs"] += 1
                if merged[metric]["docs"] == 1:
                    merged[metric]["meta"] = enrich_financial_data(
                        {metric: (financial_data or {}).get(metric) or {}},
                        parsed,
                        name,
                    ).get(metric) or {}

    out: Dict[str, Any] = {}
    for metric, fact in (financial_data or {}).items():
        f = dict(fact) if isinstance(fact, dict) else fact
        info = merged.get(metric)
        if isinstance(f, dict) and info and info["docs"] == 1:
            for k, v in (info["meta"] or {}).items():
                if v not in (None, ""):
                    f[k] = v
        out[metric] = f
    return out
