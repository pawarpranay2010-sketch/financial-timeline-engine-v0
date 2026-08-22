"""
Platrixa
Sprint 12E - Production Integration, Agentic Evidence Retrieval & Audit Loop
backend/maths/agent_explainer.py

Deterministic agent explanation layer.

The Agent translates the existing Decision Graph payload into concise,
deterministic explanations (Sprint 12E section 7). It explains the
engine's result - it never alters the result, never claims more
certainty than the weakest dependency, and never generates free-form
financial conclusions.

Examples of the shape produced:

    "ROE is 36.61%.
     Status: DERIVED.
     It was calculated from verified Net Profit and Equity.
     Net Profit: 98,300 - FY2025 - Filing page 42.
     Equity: 268,500 - FY2025 - Filing page 51.
     No reconciliation conflict detected."

    "ROE cannot be calculated.
     Equity for FY2025 was not established from an approved source.
     Next action: Upload the relevant balance-sheet page or approved
     supporting document."

    "ROE requires review.
     Two approved sources report different Net Profit values.
     Variance: 400.
     No value was automatically selected."

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.maths.decision_graph import (
    ADJUSTMENT_REQUIRED,
    EVIDENCE_CONFLICT,
    INSUFFICIENT_EVIDENCE,
    METRIC_AVAILABLE,
    METRIC_BLOCKED,
    METRIC_DERIVED,
    METRIC_RECONCILED,
    METRIC_STUDENT_INPUT,
    RECONCILIATION_REQUIRED,
    DecisionNode,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

# ---------------------------------------------------------------------------
# Formatting helpers (deterministic)
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    """Deterministic number formatting for display."""
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(f) >= 1e9 or (abs(f) > 0 and abs(f) < 1e-4):
        return f"{f:.4g}"
    return f"{f:,.{digits}f}"


def _evidence_lines(node: DecisionNode) -> List[str]:
    """Deterministic 'Concept: value - period - source page.' lines."""
    lines: List[str] = []
    leaves = node.evidence.leaves if node.evidence else []
    for leaf in leaves:
        parts = [str(leaf.concept), _fmt(leaf.value)]
        if leaf.period not in ("", "—"):
            parts.append(str(leaf.period))
        if leaf.document_name not in ("", "—"):
            parts.append(str(leaf.document_name))
        if leaf.page not in ("", "—"):
            parts.append(f"page {leaf.page}")
        lines.append(": ".join(parts))
    return lines


def _currency_prefix(node: DecisionNode) -> str:
    """A deterministic currency hint for amounts (best-effort from leaves)."""
    leaves = node.evidence.leaves if node.evidence else []
    for leaf in leaves:
        if leaf.currency not in ("", "—"):
            return str(leaf.currency) + " "
    return ""


# ---------------------------------------------------------------------------
# Decision -> explanation (deterministic)
# ---------------------------------------------------------------------------


def explain_decision_node(node: DecisionNode) -> Dict[str, Any]:
    """Structured, deterministic explanation for one DecisionNode.

    Always includes: what / why / status / evidence / missing / conflict /
    next_action / human_text. The maths layer produces this payload; an
    LLM agent may translate it into natural language, but the payload
    itself is deterministic and never free-form.
    """
    decision = node.decision
    target = node.target
    display = node.display_value if node.display_value not in ("", "—") \
        else _fmt(node.value)
    evidence = _evidence_lines(node)
    conflicts = node.anomalies or []
    recon_review = any(
        r.get("status") in (REVIEW_REQUIRED, BLOCKED)
        for r in (node.reconciliation or [])
    )

    if decision == METRIC_AVAILABLE:
        status_line = (
            f"{target} is {display}. Status: {node.status}. "
            "It is directly supported by accepted source evidence."
        )
    elif decision == METRIC_DERIVED:
        status_line = (
            f"{target} is {display}. Status: {node.status}. "
            "It was calculated deterministically from verified dependencies."
        )
    elif decision == METRIC_RECONCILED:
        status_line = (
            f"{target} is {display}. Status: {node.status}. "
            "It was obtained through a documented reconciliation "
            "relationship."
        )
    elif decision == METRIC_STUDENT_INPUT:
        status_line = (
            f"{target} is {display}. Status: {node.status}. "
            "It is an explicit student-input analytical value with full "
            "lineage to the original facts and decision."
        )
    elif decision == EVIDENCE_CONFLICT:
        status_line = (
            f"{target} requires review. Approved sources report conflicting "
            "values. No value was automatically selected."
        )
    elif decision == RECONCILIATION_REQUIRED:
        status_line = (
            f"{target} requires reconciliation review. Cross-statement "
            "evidence does not tie out within tolerance."
        )
    elif decision == ADJUSTMENT_REQUIRED:
        status_line = (
            f"{target} has an adjustment candidate. An explicit student "
            "decision is required; the engine never auto-corrects."
        )
    elif decision == METRIC_BLOCKED:
        status_line = (
            f"{target} cannot be calculated. Required evidence was not "
            "established from an approved source."
        )
    elif decision == INSUFFICIENT_EVIDENCE:
        status_line = (
            f"{target} is unsupported: no registered relationship or "
            "known fact can produce it."
        )
    else:
        status_line = f"{target} is {display}. Status: {node.status}."

    missing = list(node.missing or [])
    missing_note = ""
    if missing:
        missing_note = (
            "Missing dependencies: " + ", ".join(sorted(missing)) + "."
        )

    conflict_note = ""
    if conflicts:
        kinds = sorted({str(c.get("kind") or "ANOMALY") for c in conflicts})
        conflict_note = "Anomalies: " + ", ".join(kinds) + "."

    recon_note = ""
    if recon_review:
        recon_note = "Reconciliation conflict detected."

    next_action = node.next_action if node.next_action not in ("", "none") \
        else "none"

    human_lines: List[str] = []
    if decision == METRIC_BLOCKED:
        human_lines.append(status_line)
        if missing:
            human_lines.append(
                f"{', '.join(sorted(missing))} for the relevant period was "
                "not established from an approved source."
            )
        else:
            human_lines.append(
                "A required dependency was not established from an "
                "approved source."
            )
        if next_action != "none":
            human_lines.append(
                f"Next action: {_next_action_text(next_action)}."
            )
    elif decision == EVIDENCE_CONFLICT:
        human_lines.append(status_line)
        variance = _conflict_variance(node)
        if variance is not None:
            human_lines.append(f"Variance: {_fmt(variance)}.")
        human_lines.append("No value was automatically selected.")
    else:
        human_lines.append(status_line)
        human_lines.extend(evidence)
        if missing_note:
            human_lines.append(missing_note)
        if conflict_note:
            human_lines.append(conflict_note)
        if recon_note:
            human_lines.append(recon_note)
        if not evidence and not missing and not conflicts and not recon_review:
            human_lines.append("No reconciliation conflict detected.")

    return {
        "what": f"{target} {display} ({node.status})",
        "status": node.status,
        "confidence_state": node.confidence_state,
        "why": node.reason or node.blocking_reason or "",
        "status_line": status_line,
        "evidence": evidence,
        "missing": missing,
        "conflicts": conflict_note,
        "reconciliation_review": recon_review,
        "next_action": next_action,
        "human_text": "\n".join(human_lines),
        "target": target,
        "value": float(node.value) if node.value is not None else None,
    }


def explain_unsupported(request: str) -> Dict[str, Any]:
    """Deterministic explanation for an unresolvable request."""
    return {
        "what": f"'{request}' is unsupported",
        "status": "UNSUPPORTED",
        "confidence_state": "insufficient",
        "why": (
            f"'{request}' could not be resolved to a registered metric or "
            "known fact; nothing was guessed."
        ),
        "status_line": (
            f"'{request}' is unsupported: no registered relationship or "
            "known fact can produce it."
        ),
        "evidence": [],
        "missing": [],
        "conflicts": "",
        "reconciliation_review": False,
        "next_action": "provide_evidence_or_register_relationship",
        "human_text": (
            f"'{request}' cannot be calculated. No registered relationship "
            "or known fact matches this request, and the engine never "
            "invents relationships. Next action: check the metric name, "
            "provide evidence, or register the relationship."
        ),
        "target": "",
        "value": None,
    }


def _conflict_variance(node: DecisionNode) -> Optional[float]:
    """Deterministic variance among conflicting source values, when
    mathematically valid (both values present)."""
    values: List[float] = []
    for leaf in (node.evidence.leaves if node.evidence else []):
        if leaf.value is not None:
            values.append(float(leaf.value))
    if len(values) >= 2:
        return abs(values[0] - values[1])
    for anomaly in node.anomalies:
        v = anomaly.get("variance")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _next_action_text(action: str) -> str:
    """Deterministic human-readable next-action text."""
    return {
        "none": "no action required",
        "review_conflicting_evidence": (
            "review the conflicting evidence and decide which value to use"
        ),
        "review_reconciliation": (
            "review the reconciliation variance and resolve it"
        ),
        "decide_adjustment": (
            "make an explicit adjustment decision (or accept the original)"
        ),
        "provide_missing_evidence": (
            "upload the relevant document page or an approved supporting "
            "document"
        ),
        "provide_evidence_or_register_relationship": (
            "provide approved evidence or register the relationship in the "
            "formula registry"
        ),
    }.get(action, action.replace("_", " "))
