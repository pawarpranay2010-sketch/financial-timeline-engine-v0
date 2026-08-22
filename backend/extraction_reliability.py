"""
Platrixa
Sprint 9 - Extraction Reliability & Real-World PDF Stress Testing

A fail-safe reliability layer over the Sprint 8 layout-aware extraction
(backend/layout_extractor.py). Governing rule:

    «A wrong financial value is worse than a missing value.»

What this module does (and only this)
-------------------------------------
1. Column identity protection — a value is attributed to a column/period
   ONLY when structure proves it (header exists, cell aligned to the
   header, period evidence, grid consistency across the table). A value
   is NEVER attributed merely because it is numerically close to a cell.
2. Separate confidence dimensions — layout_confidence, table_confidence,
   row_confidence, column_confidence, extraction_method and layout_flag
   are computed independently; never collapsed into one vague AI score.
3. Extraction conflict detection — when independent extraction paths
   (extractor fact vs table cells, or two tables) yield conflicting
   values for the same metric/period, a structured conflict record is
   created (metric, competing values, paths, source locations, reason)
   and kept visible to downstream verification. No value is silently
   chosen.
4. Fail-safe extraction states — verified / review_required / conflict /
   blocked / derived / unanalyzed. Structurally uncertain, conflicting
   or low-confidence-OCR facts are NEVER promoted to "Verified".
5. Evidence integrity — every metadata field is only populated when the
   source actually supports it; unknown values stay absent ("—" renders
   downstream); evidence fragments are real source spans, never invented.

Hard boundaries
---------------
- Financial VALUES are never modified.
- The Sprint 6.5 hierarchy (DOCUMENT -> APPENDIX -> REGULATORY_API ->
  BLOCKED) and the Sprint 7 C++ Formula Engine are untouched. Missing
  metrics still flow to backend.evidence_resolver.
- No new external providers, no API keys, no network, no OCR engine, no
  document persistence, no new dependencies.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.layout_extractor import (
    layout_aware_annotate,
    _match_metric_to_row,
    _find_numeric_cell,
    _parse_number,
    _is_number,
)

# ---------------------------------------------------------------------------
# Extraction state vocabulary (presentation semantics live in the UI layer)
# ---------------------------------------------------------------------------

STATE_VERIFIED = "verified"
STATE_REVIEW_REQUIRED = "review_required"
STATE_CONFLICT = "conflict"
STATE_BLOCKED = "blocked"
STATE_DERIVED = "derived"
STATE_UNANALYZED = "unanalyzed"

STATE_LABELS = {
    STATE_VERIFIED: "Verified",
    STATE_REVIEW_REQUIRED: "Review Required",
    STATE_CONFLICT: "Extraction Conflict",
    STATE_BLOCKED: "Blocked",
    STATE_DERIVED: "Derived",
    STATE_UNANALYZED: "Unanalyzed",
}

# A fact whose OCR confidence is below this threshold can NEVER be
# classified "verified" by this layer (it must stay review-required).
_OCR_VERIFIED_THRESHOLD = 0.6

# Two values are "the same" only when they agree to near-exact precision.
# A 0.5% tolerance is far too loose: digit transpositions like 281.70 vs
# 281.07 (0.22%) MUST surface as a conflict, never as equality.
_VALUE_TOLERANCE = 1e-6

# If two numbers differ by >= this factor they are treated as a scale
# difference (e.g. thousands vs millions), NOT an extraction conflict.
_SCALE_JUMP_RATIO = 1000.0

_PERIOD_NORM_RE = re.compile(r"[^0-9a-z]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def _coerce_number(value: Any) -> Optional[float]:
    """Coerce a value to float. Facts arrive as numeric floats/ints while
    table cells arrive as formatted strings ('281.70*', '(2,130)', ...).
    Both must be handled without crashing the reliability pass."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_number(str(value))


def _values_equal(a: float, b: float) -> bool:
    """Same value within 0.5% relative tolerance (never pick by closeness
    alone — this only decides whether two occurrences AGREE)."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if a == b:
        return True
    denom = max(abs(a), abs(b))
    if denom == 0:
        return a == b
    return abs(a - b) / denom < _VALUE_TOLERANCE


def _plausibly_same_scale(a: float, b: float) -> bool:
    """True when the two magnitudes are not an obvious scale-unit jump
    (1000x+). Used to avoid flagging genuine scale differences as
    conflicts while still catching real transcription conflicts."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if a == 0 or b == 0:
        return True
    ratio = max(abs(a), abs(b)) / min(abs(a), abs(b))
    return ratio < _SCALE_JUMP_RATIO


def _normalize_period_short(period: Any) -> str:
    """Normalize 'FY2025' / '2025' / 'FY 2025' for same-period comparison."""
    s = str(period or "").strip().lower()
    s = _PERIOD_NORM_RE.sub("", s)
    s = s.replace("fy", "")
    return s


# ---------------------------------------------------------------------------
# 1. Column identity protection (structural, never by numeric closeness)
# ---------------------------------------------------------------------------


def verify_column_identity(table: Dict[str, Any], row: Dict[str, Any], idx: Any) -> Dict[str, Any]:
    """Prove, structurally, that the cell at `idx` in `row` belongs to the
    column at header index `idx + 1` of `table`.

    Returns {"ok": bool, "reasons": [str], "column_confidence": float}.

    The value is attributed to a column ONLY when all of the following hold:
      - a numeric cell actually exists at that index in the row,
      - a header exists for that column and is non-empty,
      - the column carries a period token OR the table has no period
        columns at all (single-column layout — weaker, lower confidence),
      - the table's numeric structure is consistent enough (the row's
        numeric count matches the table's dominant shape) that the
        alignment is real rather than a coincidence.
    """
    headers = table.get("headers") or []
    periods = table.get("column_periods") or []
    cells = row.get("cells") or []
    reasons: List[str] = []

    ok = False
    if not isinstance(idx, int) or idx < 0:
        reasons.append("no matched numeric cell index")
    elif idx >= len(cells):
        reasons.append("cell index beyond the row's cells")
    elif idx + 1 >= len(headers):
        reasons.append("cell index beyond the header range")
    elif not str(headers[idx + 1] or "").strip():
        reasons.append("empty column header")
    else:
        period = periods[idx + 1] if idx + 1 < len(periods) else ""
        has_period = bool(str(period or "").strip())
        any_period_col = any(str(p or "").strip() for p in periods[1:])
        if any_period_col and not has_period:
            # The table IS period-columnized but this column carries no
            # period token — attributing a value here would be a guess.
            ok = False
            reasons.append("matched column carries no period token")
        elif not any_period_col:
            # Single-column / unlabeled-period layout: the header is real
            # (column identity holds) but no period is claimed. Weaker.
            reasons.append("table has no period columns — column identity weaker")
            ok = True
        else:
            ok = True

    # Grid consistency: the column structure must repeat across the table.
    if ok:
        counts = [
            sum(1 for c in (r.get("cells") or []) if _is_number(c))
            for r in (table.get("rows") or [])
        ]
        if counts:
            typical = max(set(counts), key=counts.count)
            coverage = counts.count(typical) / len(counts)
            row_count = sum(1 for c in cells if _is_number(c))
            if coverage < 0.5 and row_count != typical:
                ok = False
                reasons.append(
                    "row numeric structure inconsistent with table columns"
                )

    any_period_col = any(str(p or "").strip() for p in periods[1:])
    if ok:
        confidence = 1.0 if any_period_col else 0.7
    else:
        confidence = 0.2
    return {
        "ok": ok,
        "reasons": reasons,
        "column_confidence": round(confidence, 2),
    }


# ---------------------------------------------------------------------------
# 2. Extraction method + structured confidence dimensions
# ---------------------------------------------------------------------------


def extraction_method_for_table(table: Dict[str, Any]) -> str:
    """Which deterministic extraction path produced this table."""
    if table.get("ocr"):
        return "ocr"
    table_id = str(table.get("table_id") or "")
    if table_id.startswith("stacked_table"):
        return "stacked_table"
    if any(
        table_id.startswith(p)
        for p in ("native_", "sheet_", "csv_", "html_", "docx_")
    ):
        return "native_table"
    if table_id.startswith("text_table"):
        return "text_table"
    return "table"


def _row_confidence(label: Any) -> float:
    """Structural row-match confidence: short specific labels (a canonical
    alias verbatim) score higher than long wrapped captions."""
    norm = re.sub(r"[^a-z0-9]", " ", str(label or "").lower()).strip()
    if not norm:
        return 0.0
    words = len(norm.split())
    if words <= 3:
        return 0.95
    if words <= 6:
        return 0.85
    return 0.7


def _table_confidence(table: Dict[str, Any], best: Dict[str, Any]) -> float:
    """Header/period detection quality for the table."""
    headers = table.get("headers") or []
    periods = table.get("column_periods") or []
    score = 0.4
    if sum(1 for h in headers if str(h or "").strip()) >= 2:
        score += 0.2
    if any(str(p or "").strip() for p in periods[1:]):
        score += 0.2
    if len(headers) >= 2 and best.get("idx", -1) + 1 < len(headers):
        score += 0.1
    if best.get("flagged"):
        score -= 0.3
    return round(max(0.1, min(1.0, score)), 2)


def _structured_confidence(best: Optional[Dict[str, Any]], fact: Dict[str, Any]) -> Dict[str, Any]:
    """Separate confidence dimensions — never one vague score. Only real,
    structurally-derived values; unknown dims stay 0.0 with the existing
    layout_flag preserved when present."""
    if best is None:
        return {
            "layout_confidence": 0.0,
            "table_confidence": 0.0,
            "row_confidence": 0.0,
            "column_confidence": 0.0,
            "layout_flag": str(fact.get("layout_flag") or "unanalyzed"),
        }
    table = best["table"]
    identity = best["identity"]
    flag = "ambiguous" if (best.get("flagged") or not identity["ok"]) else "ok"
    if fact.get("ocr") or best["method"] == "ocr":
        flag = "review_required"
    return {
        "layout_confidence": round(1.0 if table.get("page") else 0.5, 2),
        "table_confidence": _table_confidence(table, best),
        "row_confidence": round(_row_confidence(best["row"].get("label")), 2),
        "column_confidence": identity["column_confidence"],
        "layout_flag": flag,
    }


# ---------------------------------------------------------------------------
# 3. Best-match resolution (reuses the Sprint 8 matching vocabulary)
# ---------------------------------------------------------------------------


def _best_match(annotations: Dict[str, Any], metric: str, fact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Strongest defensible (table, row, cell-index) match for a metric,
    preferring higher structural column confidence. Never guesses."""
    flagged = set(annotations.get("flagged_tables") or [])
    best: Optional[Dict[str, Any]] = None
    for table in annotations.get("tables") or []:
        for row in table.get("rows") or []:
            if not _match_metric_to_row(metric, row.get("label")):
                continue
            idx = _find_numeric_cell(row.get("cells") or [], fact.get("value"))
            if idx is None:
                continue
            identity = verify_column_identity(table, row, idx)
            candidate = {
                "table": table,
                "row": row,
                "idx": idx,
                "identity": identity,
                "page": table.get("page"),
                "table_id": table.get("table_id") or table.get("source_location") or "",
                "method": extraction_method_for_table(table),
                "flagged": table.get("table_id") in flagged,
            }
            if best is None or identity["column_confidence"] > best["identity"]["column_confidence"]:
                best = candidate
    return best


# ---------------------------------------------------------------------------
# 4. Fail-safe extraction-state classification
# ---------------------------------------------------------------------------


def classify_extraction_state(
    fact: Dict[str, Any],
    best: Optional[Dict[str, Any]],
    in_conflict: bool = False,
) -> tuple:
    """Classify one fact's extraction state. Returns (state, reason).

    Proven          -> verified
    Structurally uncertain -> review_required
    Conflicting extraction -> conflict
    Insufficient evidence  -> blocked
    Missing primary metric -> left to the Sprint 6.5 resolver
    No supported path      -> unanalyzed

    Low-confidence OCR can NEVER be classified verified.
    """
    reason = ""
    if in_conflict:
        return STATE_CONFLICT, "conflicting values from independent extraction paths"
    ptier = str(fact.get("provenance_tier") or "")
    if ptier == "BLOCKED" or fact.get("value") is None:
        return STATE_BLOCKED, "required evidence is not available"
    if str(fact.get("source")) == "Calculated" or ptier in ("DERIVED", "EXTERNAL_DERIVED"):
        return STATE_DERIVED, ""
    if fact.get("ocr"):
        conf = fact.get("ocr_confidence")
        try:
            low = conf is None or float(conf) < _OCR_VERIFIED_THRESHOLD
        except (TypeError, ValueError):
            low = True
        if low:
            return STATE_REVIEW_REQUIRED, "low-confidence OCR — cannot be automatically verified"
    if best is None:
        return STATE_UNANALYZED, "no layout/table evidence analyzed for this metric"
    if best.get("flagged"):
        return STATE_REVIEW_REQUIRED, "table flagged as malformed/ambiguous"
    if not best["identity"]["ok"]:
        return (
            STATE_REVIEW_REQUIRED,
            "column identity could not be established: " + "; ".join(best["identity"]["reasons"]),
        )
    if best["method"] == "ocr" and fact.get("ocr_confidence") is None:
        return STATE_REVIEW_REQUIRED, "OCR-derived value without OCR confidence"
    return STATE_VERIFIED, ""


# ---------------------------------------------------------------------------
# 5. Extraction conflict detection (independent paths, never silent pick)
# ---------------------------------------------------------------------------


def _collect_occurrences(
    financial_data: Dict[str, Any],
    annotations: Dict[str, Any],
    document_name: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """Every numeric occurrence per metric across all tables of one
    document, with its path/method, column/period, page and document."""
    occurrences: Dict[str, List[Dict[str, Any]]] = {
        m: [] for m in (financial_data or {})
    }
    flagged = set(annotations.get("flagged_tables") or [])
    for table in annotations.get("tables") or []:
        headers = table.get("headers") or []
        periods = table.get("column_periods") or []
        table_id = table.get("table_id") or table.get("source_location") or ""
        is_flagged = table_id in flagged
        for row in table.get("rows") or []:
            for metric in (financial_data or {}):
                if not _match_metric_to_row(metric, row.get("label")):
                    continue
                cells = row.get("cells") or []
                for idx, cell in enumerate(cells):
                    v = _coerce_number(cell)
                    if v is None:
                        continue
                    column = headers[idx + 1] if idx + 1 < len(headers) else ""
                    period = periods[idx + 1] if idx + 1 < len(periods) else ""
                    occurrences.setdefault(metric, []).append({
                        "value": v,
                        "path": extraction_method_for_table(table),
                        "table": table_id,
                        "column": str(column or ""),
                        "period": str(period or ""),
                        "page": table.get("page"),
                        "document": document_name or "",
                        "flagged": is_flagged,
                    })
    return occurrences


def _detect_conflicts_from_occurrences(
    occurrences: Dict[str, List[Dict[str, Any]]],
    financial_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Group occurrences by metric+period/column and report when two
    independent paths disagree beyond tolerance. Also compares the
    extractor's own fact value against same-period table cells (scale
    jumps are not treated as conflicts)."""
    conflicts: List[Dict[str, Any]] = []

    # Pass 1: same column/period, two distinct values. Cells without any
    # column/period attribution (empty headers) are NEVER compared — a row
    # holding several unlabeled numeric columns is not a conflict. Flagged
    # (malformed) tables are excluded: they already surface as
    # review_required and cannot prove a conflict.
    for metric, entries in occurrences.items():
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries:
            if e.get("flagged"):
                continue
            key = e.get("period") or e.get("column")
            if not key:
                continue
            groups.setdefault(key, []).append(e)
        for col_key, col_entries in groups.items():
            distinct: List[Dict[str, Any]] = []
            for e in col_entries:
                if not any(_values_equal(e["value"], d["value"]) for d in distinct):
                    distinct.append(e)
            if len(distinct) >= 2:
                conflicts.append({
                    "metric": metric,
                    "column": col_key,
                    "competing_values": distinct,
                    "reason": (
                        f"independent extraction paths conflict for {metric} "
                        f"({col_key})"
                    ),
                    "status": "conflict",
                })

    # Pass 2: extractor fact vs same-period table cell (scale-guarded).
    # Skip metrics/columns already conflicted in Pass 1 so one structural
    # conflict record stays per metric+column.
    pass1_keys = {(c["metric"], c["column"]) for c in conflicts}
    for metric, fact in (financial_data or {}).items():
        fv = _coerce_number(fact.get("value"))
        if fv is None:
            continue
        fperiod = _normalize_period_short(fact.get("reporting_period"))
        if not fperiod:
            continue
        for e in occurrences.get(metric, []):
            if e.get("flagged"):
                continue
            eperiod = _normalize_period_short(e.get("period"))
            if not eperiod or eperiod != fperiod:
                continue
            key = (metric, e.get("column") or eperiod)
            if key in pass1_keys:
                continue
            if _values_equal(fv, e["value"]) or not _plausibly_same_scale(fv, e["value"]):
                continue
            pass1_keys.add(key)
            conflicts.append({
                "metric": metric,
                "column": e.get("column") or eperiod,
                "competing_values": [
                    {
                        "value": fv,
                        "path": "extractor",
                        "table": "",
                        "column": str(fact.get("reporting_period") or ""),
                        "period": str(fact.get("reporting_period") or ""),
                        "page": fact.get("page"),
                        "document": "",
                    },
                    e,
                ],
                "reason": (
                    f"extractor fact value conflicts with the table cell for "
                    f"the same period ({eperiod})"
                ),
                "status": "conflict",
            })
            break
    return conflicts


# ---------------------------------------------------------------------------
# 6. Sprint 9 integration entry point
# ---------------------------------------------------------------------------


def build_extraction_reliability_report(
    financial_data: Dict[str, Any],
    extracted_documents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the full reliability pass over the verified fact graph.

    Args:
        financial_data: the pipeline's verified financial_data dict
            (already layout-enriched by Sprint 8 where applicable).
        extracted_documents: per-document results shaped like Module 2's
            extract_multiple() output ({file_name, parsed_document}).

    Returns (never raises):
        {
          "financial_data": enriched fact dict (additive fields only),
          "conflicts": [structured conflict records],
          "flagged_tables": [table ids],
          "pages_without_text": [page numbers],
          "states": {metric: extraction_state},
          "methods": {metric: extraction_method},
        }
    """
    out: Dict[str, Any] = {
        "financial_data": dict(financial_data or {}),
        "conflicts": [],
        "flagged_tables": [],
        "pages_without_text": [],
        "states": {},
        "methods": {},
    }
    try:
        financial_data = financial_data or {}
        all_annotations: List[Dict[str, Any]] = []
        all_occurrences: Dict[str, List[Dict[str, Any]]] = {
            m: [] for m in financial_data
        }
        for doc in extracted_documents or []:
            if not isinstance(doc, dict):
                continue
            name = str(
                doc.get("file_name") or doc.get("document_name") or doc.get("source") or ""
            )
            parsed = doc.get("parsed_document") or {}
            if not isinstance(parsed, dict):
                continue
            ann = layout_aware_annotate(parsed, name)
            all_annotations.append(ann)
            out["flagged_tables"].extend(ann.get("flagged_tables") or [])
            out["pages_without_text"].extend(ann.get("pages_without_text") or [])
            occ = _collect_occurrences(financial_data, ann, name)
            for m, entries in occ.items():
                all_occurrences.setdefault(m, []).extend(entries)

        conflicts = _detect_conflicts_from_occurrences(all_occurrences, financial_data)
        out["conflicts"] = conflicts
        conflict_metrics = {c["metric"] for c in conflicts}

        enriched: Dict[str, Any] = {}
        for metric, fact in financial_data.items():
            if not isinstance(fact, dict):
                enriched[metric] = fact
                continue
            f = dict(fact)
            best: Optional[Dict[str, Any]] = None
            for ann in all_annotations:
                cand = _best_match(ann, metric, f)
                if cand is None:
                    continue
                if best is None or cand["identity"]["column_confidence"] > best["identity"]["column_confidence"]:
                    best = cand
            in_conflict = metric in conflict_metrics
            state, reason = classify_extraction_state(f, best, in_conflict)
            method = best["method"] if best else ("ocr" if f.get("ocr") else "unanalyzed")

            # Additive fields only — existing values/status/provenance are
            # never touched.
            f["extraction_method"] = method
            f["extraction_state"] = state
            f["extraction_state_reason"] = reason or ""
            for k, v in _structured_confidence(best, f).items():
                f[k] = v
            if in_conflict:
                conflict_record = next(
                    (c for c in conflicts if c["metric"] == metric), None
                )
                if conflict_record is not None:
                    f["extraction_conflict"] = conflict_record
            enriched[metric] = f
            out["states"][metric] = state
            out["methods"][metric] = method
        out["financial_data"] = enriched
    except Exception:
        # Fail-safe: the reliability pass must never break the pipeline.
        out["financial_data"] = dict(financial_data or {})
    return out
