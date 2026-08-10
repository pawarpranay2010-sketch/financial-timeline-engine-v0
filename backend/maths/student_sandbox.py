"""
Financial Timeline Engine
Sprint 12F - Student Maths Sandbox
backend/maths/student_sandbox.py

A controlled student-test path that exercises the REAL production
calculation path:

    Student input (facts / text / uploaded documents)
        -> normalization (12D)          (deterministic, never guessed)
        -> fact identity + evidence
        -> agentic orchestration (12E)
        -> C++ mathematical authority (Sprint 12F strict mode)
        -> decision graph (12C)
        -> explanation / refusal UX    (12F sections 9-10)
        -> Excel lineage (12C)
        -> audit trail (12E/12F)

The sandbox is NOT a fake demonstration calculator: it runs the same
production pipeline the product uses (strict Solver cpp_authority mode),
so a student who submits sufficient evidence receives a correct,
C++-computed, evidence-backed result, and a student who does not receives
a clear, deterministic explanation of what is missing - never a guessed
value, never a silent Python fallback.

Demo Mode is intentionally NOT routed here (it stays static and
deterministic per Sprint 12F section 13); this module is the production
student path.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.maths.agentic import AgenticOrchestrator
from backend.maths.authority import (
    AUTHORITY_CPP,
    AUTHORITY_UNAVAILABLE,
    AUTHORITY_UNSUPPORTED,
    ENGINE_UNAVAILABLE_REASON,
    engine_available,
    production_dupont,
)
from backend.maths.decision_graph import DecisionGraph
from backend.maths.extended_registry import EXTENDED_REGISTRY
from backend.maths.fact_model import build_fact_graph
from backend.maths.normalization import harden_fact_text, parse_numeric_text
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    STATUS_LABELS,
)

# ---------------------------------------------------------------------------
# Student-understandable vocabulary (section 9)
# ---------------------------------------------------------------------------

STATUS_WORDS = {
    VERIFIED: "Verified - reported in an approved source",
    DERIVED: "Derived - calculated from verified inputs",
    RECONCILED: "Reconciled - consistent across statements",
    STUDENT_INPUT: "Student input - provided directly by you",
    REVIEW_REQUIRED: "Review required - evidence conflicts",
    BLOCKED: "Blocked - cannot be calculated from the evidence provided",
}

# Human-level acceptance checklist (Sprint 12F section 15). Every entry is
# answerable from a sandbox outcome without any engine internals.
STUDENT_CHECKLIST: List[Dict[str, str]] = [
    {
        "question": "What did FT-E calculate?",
        "payload_field": "what",
        "explanation": "The metric name and its value (or a refusal).",
    },
    {
        "question": "What formula did it use?",
        "payload_field": "how",
        "explanation": "The registered accounting relationship, in plain text.",
    },
    {
        "question": "Which numbers were used?",
        "payload_field": "inputs",
        "explanation": "Each dependency with its value.",
    },
    {
        "question": "Where did those numbers come from?",
        "payload_field": "where",
        "explanation": "Document / page / source / evidence for each number.",
    },
    {
        "question": "Is the result verified or derived?",
        "payload_field": "status",
        "explanation": "The six-tier status and its plain-language label.",
    },
    {
        "question": "Why was something blocked?",
        "payload_field": "why_not",
        "explanation": "The exact missing / conflicting evidence, deterministically.",
    },
    {
        "question": "What do I need to provide to continue?",
        "payload_field": "next_action",
        "explanation": "The concrete next step (e.g. upload a source with both values).",
    },
    {
        "question": "Can I independently verify the answer?",
        "payload_field": "verification_hint",
        "explanation": "The registered formula lets the student recompute by hand.",
    },
    {
        "question": "Can I export/use the Excel model?",
        "payload_field": "excel_formula",
        "explanation": "A live Excel formula over the Financial Data sheet (or why not).",
    },
]


# ---------------------------------------------------------------------------
# Input ingestion (deterministic; never fabricates)
# ---------------------------------------------------------------------------


# One 'Concept: value' / 'Concept = value' pair. The concept name may
# NOT contain sentence-final punctuation, a comma or another colon, so
# 'Calculate the Profit. Revenue: 1,000 Expenses: 600' yields exactly
# Revenue and Expenses (never 'the Profit'). The negative lookahead
# rejects a number that continues into more digits/commas/periods
# (European '1.234,56' is ambiguous and NEVER read as 1.234).
_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9 &\-\/']{1,60}?)\s*[:=]\s*"
    r"(Rs\.?|₹)?\s*([\d,]+(?:\.[\d]+)?\s*%?)(?![.,\d])",
    re.IGNORECASE,
)


def _parse_text_facts(text: str, document_name: str = "Student document",
                      page: Optional[str] = None) -> Dict[str, Any]:
    """Parse 'Concept: value' / 'Concept = value' pairs from free text.

    Matches every unambiguous pair in the text - one per line OR several
    compact pairs on a single line (e.g. 'Calculate the Commission.
    Sales: 10,000 Commission Rate: 5'). A pair whose numeric right-hand
    side is ambiguous is silently skipped (the solver reports the missing
    dependency - never a fabricated value). Provenance is attached as
    Tier 1 (uploaded primary document).
    """
    out: Dict[str, Any] = {}
    if not text:
        return out
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        occupied: List[tuple] = []
        for m in _PAIR_RE.finditer(line):
            start, end = m.start(), m.end()
            if any(start < o_end and end > o_start
                   for o_start, o_end in occupied):
                continue  # a longer concept already claimed this span
            concept = m.group(1).strip()
            raw_value = m.group(3).strip()
            parsed = parse_numeric_text(raw_value)
            if parsed.value is None or parsed.ambiguity:
                # ambiguous number on this line - never guessed; the
                # whole line is skipped so a partial interpretation
                # cannot be mixed with an ambiguous one.
                occupied = ["ambiguous"]
                break
            occupied.append((start, end))
            out[concept] = harden_fact_text(concept, {
                "value": parsed.value,
                "unit": parsed.currency or parsed.unit,
                "scale": parsed.scale,
                "reporting_period": "FY2025",
                "provenance_tier": "DOCUMENT",
                "document_name": document_name,
                "page": page,
                "evidence": raw_line[start:end].strip()[:160],
                "source": document_name,
            })
    return out


_PROSE_LINK_RE = re.compile(
    r"\s+(?:is|are|was|were|equals?|amounts?\s+to|of|=|:)\s*"
    r"(?:Rs\.?|₹)?\s*(?P<val>[\d,]+(?:\.[\d]+)?\s*%?)",
    re.IGNORECASE,
)

_CONCEPT_WORDS_CACHE: Optional[List[str]] = None


def _known_concept_words() -> List[str]:
    """Registered concept names (formula targets + their dependencies)
    with their canonical registry spelling, longest first. Used ONLY to
    anchor deterministic prose extraction - a word that is not a
    registered concept is never extracted."""
    global _CONCEPT_WORDS_CACHE
    if _CONCEPT_WORDS_CACHE is None:
        words = set()
        registries = [EXTENDED_REGISTRY]
        try:  # Sprint 15D FYJC commercial arithmetic (additive)
            from backend.maths.fyjc_canonical import FYJC_FORMULA_REGISTRY
            registries.append(FYJC_FORMULA_REGISTRY)
        except Exception:  # pragma: no cover - defensive
            pass
        for registry in registries:
            for fid in registry.all_ids():
                d = registry.get(fid)
                if d is None:
                    continue
                if d.target:
                    words.add(str(d.target))
                words.update(str(x) for x in (d.dependencies or []))
        words = {w for w in words if len(w) >= 3}
        _CONCEPT_WORDS_CACHE = sorted(words, key=lambda w: (-len(w), w))
    return _CONCEPT_WORDS_CACHE


def extract_prose_facts(text: str,
                        document_name: str = "Student document",
                        page: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic extraction of narrative-prose facts.

    Handles the natural question wording that the strict 'Concept: value'
    normalizer cannot read, e.g. "Revenue is Rs.10,000 and its Expenses
    are Rs.6,000". ONLY a REGISTERED concept name immediately followed by
    a linking verb and an explicit numeric value becomes a fact - nothing
    is guessed, no arithmetic runs here, and a concept without an
    attached number is left for the solver to report as missing.

    Provenance is Tier 1 (uploaded primary document), matching
    _parse_text_facts. Longer concepts claim a span before shorter
    overlapping ones ('Net Profit' beats 'Profit'), so the canonical
    registry name is used as the fact key.
    """
    out: Dict[str, Any] = {}
    if not text:
        return out
    raw = str(text)
    occupied: List[tuple] = []
    for name in _known_concept_words():
        pat = re.compile(
            r"(?<![a-z])" + re.escape(name) + r"(?![a-z])"
            + _PROSE_LINK_RE.pattern,
            re.IGNORECASE,
        )
        for m in pat.finditer(raw):
            start, end = m.start(), m.end()
            if any(start < o_end and end > o_start
                   for o_start, o_end in occupied):
                continue  # a longer concept already claimed this span
            parsed = parse_numeric_text(m.group("val"))
            if parsed.value is None or parsed.ambiguity:
                continue  # ambiguous - never guessed
            occupied.append((start, end))
            out[name] = harden_fact_text(name, {
                "value": parsed.value,
                "unit": parsed.currency or parsed.unit,
                "scale": parsed.scale,
                "reporting_period": "FY2025",
                "provenance_tier": "DOCUMENT",
                "document_name": document_name,
                "page": page,
                "evidence": raw[start:end].strip()[:160],
                "source": document_name,
            })
    return out


def _attach_document_facts(facts: Dict[str, Any],
                           documents: Optional[List[Dict[str, Any]]]
                           ) -> Dict[str, Any]:
    """Merge document-derived facts (Tier 1/2) into the fact set.

    Each document entry: {document_name, page, tier: 'DOCUMENT'|'APPENDIX',
    facts: {concept: value_or_fact}}. Conflicting values are NOT resolved
    here - both are preserved so the deterministic machinery reports
    EVIDENCE_CONFLICT / REVIEW_REQUIRED.
    """
    merged = dict(facts or {})
    for doc in (documents or []):
        if not isinstance(doc, dict):
            continue
        doc_name = str(doc.get("document_name") or "Uploaded document")
        page = doc.get("page")
        tier = str(doc.get("tier") or "DOCUMENT")
        # An explicit non-approved source (Tier 4 / WEB / anything outside
        # DOCUMENT | APPENDIX) is NEVER silently upgraded to a primary
        # document: the tier is preserved so the provenance machinery
        # fails closed (BLOCKED) for facts that depend on it.
        for concept, value in (doc.get("facts") or {}).items():
            if isinstance(value, dict):
                fact = dict(value)
            else:
                fact = {"value": value}
            fact.setdefault("provenance_tier", tier)
            fact.setdefault("document_name", doc_name)
            if page is not None:
                fact.setdefault("page", str(page))
            fact.setdefault("reporting_period", "FY2025")
            fact.setdefault("source", doc_name)
            fact.setdefault("evidence", doc.get("evidence") or f"from {doc_name}")
            if concept not in merged:
                merged[concept] = fact
            else:
                if not isinstance(merged[concept], dict):
                    merged[concept] = {"value": merged[concept]}
                existing = merged[concept]
                incoming = fact
                # Conflicting values from approved sources are preserved
                # and flagged REVIEW_REQUIRED (never silently chosen; both
                # values stay separately traceable).
                if (tier in ("DOCUMENT", "APPENDIX")
                        and existing.get("value") != incoming.get("value")):
                    existing.setdefault("extraction_state", "conflict")
                    existing["conflict_with"] = {
                        "value": incoming.get("value"),
                        "document_name": doc_name,
                        "page": str(page) if page is not None else None,
                        "source": doc.get("source") or doc_name,
                        "evidence": incoming.get("evidence"),
                    }
    return merged


# ---------------------------------------------------------------------------
# Outcome shaping (student-understandable, section 9/10)
# ---------------------------------------------------------------------------


def _inputs_rows(analysis: Any) -> List[Dict[str, Any]]:
    deps = list(getattr(analysis, "dependencies", None) or [])
    evidence = getattr(analysis, "evidence", None) or {}
    leaves = {l.get("concept"): l for l in (evidence.get("leaves") or [])}
    rows: List[Dict[str, Any]] = []
    for concept in deps:
        leaf = leaves.get(concept) or {}
        rows.append({
            "concept": concept,
            "value": leaf.get("value"),
            "display_value": leaf.get("display_value"),
            "status": leaf.get("status"),
            "source": leaf.get("source"),
            "document": leaf.get("document_name"),
            "page": leaf.get("page"),
            "evidence": leaf.get("evidence"),
            "tier": leaf.get("tier"),
        })
    return rows


def _verification_hint(analysis: Any) -> str:
    formula = analysis.formula or "—"
    if analysis.status in (VERIFIED, DERIVED, RECONCILED, STUDENT_INPUT):
        return (
            f"Yes - recompute by hand using the registered formula "
            f"({formula}) and the numbers listed above."
        )
    return (
        "Not yet - first provide the missing/confirmed evidence listed "
        "under 'Next action'."
    )


def _authority_state(analysis: Any) -> str:
    if not engine_available():
        return AUTHORITY_UNAVAILABLE
    if getattr(analysis, "resolved", False) is False:
        return AUTHORITY_UNSUPPORTED
    if analysis.status == BLOCKED and (
        "UNSUPPORTED" in str(getattr(analysis, "termination_reason", ""))
        or "not covered by the C++" in str(getattr(analysis, "explanation", {}))
    ):
        return AUTHORITY_UNSUPPORTED
    return AUTHORITY_CPP


def _build_audit(analysis: Any) -> Dict[str, Any]:
    node = getattr(analysis, "_last_node", None)
    if node is None:
        node = getattr(analysis, "node", None)
    if node is None:
        return {
            "available": False,
            "reason": "Audit trail unavailable for this outcome "
                      "(unsupported or unresolved request).",
        }
    try:
        from backend.audit_trail import build_audit_trail
        return {"available": True, "payload": build_audit_trail(node)}
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "available": False,
            "reason": f"Audit trail could not be built: {exc}",
        }


def run_student_metric(
    metric: str,
    facts: Optional[Dict[str, Any]] = None,
    text: Optional[str] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    reference: Optional[Dict[str, Any]] = None,
    coordinate_map: Optional[Dict[str, str]] = None,
    retrieval_timestamp: str = "",
) -> Dict[str, Any]:
    """Run the REAL production pipeline for one student request.

    Returns a student-understandable outcome: what/how/inputs/where/
    status/why-not/next-action, plus authority_state, Excel formula and
    the audit trail. A refusal (BLOCKED / REVIEW_REQUIRED / UNSUPPORTED)
    is a successful outcome when evidence is insufficient.
    """
    merged = _attach_document_facts(facts or {}, documents)
    merged.update(_parse_text_facts(text or ""))

    orchestrator = AgenticOrchestrator(cpp_authority=True)
    analysis = orchestrator.analyze_request(
        str(metric),
        existing_facts=merged,
        reference=reference,
        coordinate_map=coordinate_map,
        retrieval_timestamp=retrieval_timestamp,
    )
    # expose the node for the audit builder (deterministic hook)
    analysis._last_node = getattr(orchestrator, "_last_node", None)

    status = analysis.status or BLOCKED
    resolved = analysis.resolved and analysis.value is not None
    return {
        "metric": str(metric),
        "resolved": analysis.resolved,
        "workflow_state": analysis.workflow_state,
        "decision": analysis.decision,
        "authority_state": _authority_state(analysis),
        # ---- student-understandable fields (section 9) ----
        "what": (
            f"{analysis.target}: {analysis.display_value}"
            if resolved else f"{analysis.target} could not be calculated."
        ),
        "how": analysis.formula or "—",
        "inputs": _inputs_rows(analysis),
        "where": _inputs_rows(analysis),  # same rows carry source/page/evidence
        "status": status,
        "status_label": STATUS_WORDS.get(status, status),
        "why_not": (
            (analysis.termination_reason or "")
            or (getattr(analysis, "node_payload", None) or {})
            .get("reason")
            or (None if resolved else "Required evidence was not "
                "established from an approved source.")
        ),
        "next_action": analysis.next_action,
        "verification_hint": _verification_hint(analysis),
        "value": float(analysis.value) if analysis.value is not None else None,
        "display_value": analysis.display_value,
        # ---- production artifacts ----
        "excel_formula": analysis.excel_formula,
        "explanation": dict(analysis.explanation or {}),
        "audit": _build_audit(analysis),
    }


def run_student_dupont(
    facts_by_period: Dict[str, Dict[str, Any]],
    documents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Strict DuPont decomposition for a student (periods -> facts)."""
    merged = {
        period: _attach_document_facts(period_facts, documents)
        for period, period_facts in (facts_by_period or {}).items()
    }
    return {
        "authority_state": AUTHORITY_CPP if engine_available()
        else AUTHORITY_UNAVAILABLE,
        "analysis": production_dupont(merged).to_dict(),
    }


def student_checklist() -> List[Dict[str, str]]:
    """The human-level acceptance checklist (Sprint 12F section 15)."""
    return [dict(item) for item in STUDENT_CHECKLIST]
