"""Platrixa — canonical developer result contract (Phase 5D).

One canonical, typed, machine-consumable result envelope for the /v1
developer surface. This module is TRANSPORT-layer only: it serializes
existing engine truth, adds zero financial semantics, and can never
create, upgrade, or soften a status.

Design rules (Phase 5D contract):

  * EXPOSE, don't invent: every field is derived from structures the
    pipeline already produced (KernelResult projection via the public
    facade, document representation, rule evidence). Fields the engine
    does not provide are omitted or explicit null/unavailable markers —
    never fabricated values.
  * The six-state public vocabulary (api/status.py) is the ONLY public
    status vocabulary; ``status`` is the deterministic transport mapping
    of the verbatim engine state, which is carried beside it.
  * ``reason_codes`` reuse the existing central vocabulary
    (api.status.reason_code_for_engine / transport codes). No dozens of
    redundant codes.
  * Evidence is a thin serialization adapter over the existing
    document-understanding representation (EvidenceRef.to_dict) — never
    fabricated: bbox/confidence stay null when the engine did not
    provide them, and evidence is never manufactured for fields that
    have none.
  * Derived vs extracted distinction: amounts interpretation is
    annotated with a value_origin marker so a model-extracted amount is
    never blurred with a deterministic (accounting) value.
  * Deterministic, JSON-serializable output: same inputs → same bytes
    (sorted keys, explicit null handling), so Phase 5C idempotent replay
    of the envelope is stable.

Architecture:

    existing processing facade
        ↓
    existing schema/grounding/authority pipeline
        ↓
    engine terminal state           (authoritative, never changed)
        ↓
    build_process_result(...)       ← this module (serialization only)
        ↓
    canonical v1 result envelope    (sync /v1/process AND async results)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from api.status import (
    LABEL_BY_PUBLIC_STATUS,
    RETRYABLE_BY_PUBLIC_STATUS,
    engine_status_verbatim,
    public_status_for_engine,
    reason_code_for_engine,
    STATUS_FAILED,
)

# Document-layer evidence field names the contract exposes verbatim from
# EvidenceRef.to_dict(). Kept here so the adapter has exactly one place
# that names the field mapping.
_EVIDENCE_FIELDS = (
    "evidence_id",
    "document_id",
    "page",
    "text",
    "bbox",
    "extraction_confidence",
    "source_type",
    "engine",
    "engine_version",
)

# Reason codes recorded on the envelope when evidence was recorded for a
# FAILED mapping (mirrors the existing single-code behavior, pluralized in
# the codes list; the singular field remains for back-compat).
_EVIDENCE_RECORDED = "EVIDENCE_RECORDED"


def _jsonable(value: Any) -> Any:
    """JSON-safe conversion for engine structures (mirror of the facade's
    deterministic conversion table; local copy so the API layer does not
    import the model-facing facade internals)."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)  # unknown objects → deterministic string form


def serialize_evidence(
    evidence_refs: Optional[List[Any]],
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Thin serialization adapter over the document evidence refs.

    The input is a list of objects exposing ``to_dict()`` (the existing
    ``EvidenceRef`` boundary). This adapter adds NOTHING: bbox and
    confidence stay null when the engine did not provide them; evidence is
    never manufactured, filtered, or reordered beyond the deterministic
    order already present. ``limit`` bounds the response size only.
    """
    if not evidence_refs:
        return []
    out: List[Dict[str, Any]] = []
    for ref in evidence_refs[:limit]:
        try:
            raw = ref.to_dict()
        except Exception:
            continue  # never let one malformed ref poison the envelope
        item: Dict[str, Any] = {}
        for name in _EVIDENCE_FIELDS:
            if name in raw:
                item[name] = _jsonable(raw[name])
        # Drop keys the engine did not provide at all (absent ≠ null here:
        # EvidenceRef.to_dict always emits all keys, so null means the
        # engine genuinely had no bbox/confidence — keep the null).
        out.append(item)
    return out


def annotate_amounts_origin(interpretation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Annotate interpretation.amounts with a value_origin marker.

    The 18-field interpretation carries model-suggested amounts. The
    accounting result is the deterministic output. To never blur a
    model-extracted amount with a deterministic value, each amount entry
    is annotated with ``value_origin``:

        EXTRACTED   — value came from the model's interpretation
                      (model-suggested, NOT deterministic)
        UNKNOWN     — entry shape not recognized (fail-closed labeling)

    This annotation is transport-layer metadata: it changes nothing about
    the interpretation the engine produced and adds no financial
    semantics.
    """
    if not isinstance(interpretation, dict):
        return interpretation
    amounts = interpretation.get("amounts")
    if not isinstance(amounts, list):
        return interpretation
    out = dict(interpretation)
    annotated: List[Any] = []
    for entry in amounts:
        if isinstance(entry, dict) and "value" in entry and "value_origin" not in entry:
            annotated.append({**entry, "value_origin": "EXTRACTED"})
        elif isinstance(entry, dict):
            annotated.append(dict(entry))
        else:
            annotated.append({"value_origin": "UNKNOWN", "raw": _jsonable(entry)})
    out["amounts"] = annotated
    return out


def build_process_result(
    *,
    request_id: Optional[str],
    engine_status: str,
    engine_status_label: Optional[str] = None,
    next_action: Optional[str] = None,
    issues: Optional[List[str]] = None,
    grounding_issues: Optional[List[str]] = None,
    rule_evidence: Optional[List[Dict[str, Any]]] = None,
    interpretation: Optional[Dict[str, Any]] = None,
    accounting: Optional[Dict[str, Any]] = None,
    document: Optional[Dict[str, Any]] = None,
    evidence_refs: Optional[List[Any]] = None,
    lineage: Optional[Dict[str, List[str]]] = None,
    timings_ms: Optional[Dict[str, Any]] = None,
    notes: Optional[List[str]] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the canonical 5D result envelope from engine truth.

    Pure serialization of the structures the pipeline already produced.
    Every argument is existing engine truth (or None); nothing here calls
    the engine, the model, or any authority, and nothing upgrades a
    status. The same engine state always produces the same envelope
    (deterministic serialization for idempotent replay).

    Status semantics (identical to the Phase 5A sync contract):

      * ``status``        — the ENGINE terminal state, verbatim
                            (authoritative; e.g. ``UNSUPPORTED_TRANSACTION``).
      * ``api_status``    — the deterministic six-state public mapping
                            (e.g. ``UNSUPPORTED``), label beside it.
      * ``engine_status`` — the verbatim engine state again, explicit.

    ``accounting`` (established contract name) and ``accounting_result``
    (canonical 5D name) carry the SAME value so both generations of
    consumers read one envelope.
    """
    api_status = public_status_for_engine(engine_status)
    reason_codes: List[str] = []
    reason_code: Optional[str] = reason_code_for_engine(engine_status)
    if reason_code:
        reason_codes.append(reason_code)
    if api_status == STATUS_FAILED and (issues or grounding_issues):
        reason_codes.append(_EVIDENCE_RECORDED)

    accounting_out = _jsonable(accounting) if accounting is not None else None
    envelope: Dict[str, Any] = {
        # --- status block (engine verbatim + six-state mapping) ---------
        "api_version": "v1",
        "request_id": request_id,
        "status": engine_status,
        "status_label": engine_status_label or engine_status,
        "api_status": api_status,
        "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
        "success": api_status == "VERIFIED",
        "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False),
        "engine_status": engine_status_verbatim(engine_status),
        "next_action": next_action,
        "reason_code": reason_codes[0] if reason_codes else None,
        "reason_codes": reason_codes,
        # --- substance (verbatim engine truth) --------------------------
        "interpretation": annotate_amounts_origin(
            _jsonable(interpretation) if interpretation is not None else None
        ),
        "accounting": accounting_out,
        "accounting_result": accounting_out,
        "issues": list(issues or []),
        "grounding_issues": list(grounding_issues or []),
        "rule_evidence": _jsonable(list(rule_evidence or [])),
        # --- evidence (thin adapter; never fabricated) ------------------
        "evidence": serialize_evidence(evidence_refs),
        # --- document provenance when the input was a document ----------
        "document": _jsonable(document) if document is not None else None,
        "lineage": _jsonable(lineage) if lineage is not None else None,
        # --- metadata ----------------------------------------------------
        "metadata": {
            "engine_status": engine_status_verbatim(engine_status),
            "processing_time_ms": int(duration_ms) if duration_ms is not None else None,
            "timings_ms": _jsonable(timings_ms) if timings_ms is not None else None,
            "notes": list(notes or []),
        },
    }
    return envelope


def next_action_for(status: str) -> Optional[str]:
    """Deterministic developer-handling hint per public status.

    Transport-layer guidance only — never a financial instruction. None
    for VERIFIED (no action needed).
    """
    return {
        "VERIFIED": None,
        "REVIEW_REQUIRED": "queue for human review; do not treat as success",
        "UNSUPPORTED": "capability outside the supported implementation boundary",
        "INVALID_INPUT": "fix the request; retrying unchanged will fail again",
        "FAILED": "see reason_codes; retry only if the code is retryable",
        "PROCESSING": "poll the job or retry the request later",
    }.get(status)


def unavailable(reason: str) -> Any:
    """Explicit 'unavailable' marker for metadata fields.

    Distinguishes absent (key omitted / null) from unavailable (the
    engine would have provided it but the dependency is down right now).
    """
    return {"available": False, "reason": reason, "at": round(time.time(), 3)}
