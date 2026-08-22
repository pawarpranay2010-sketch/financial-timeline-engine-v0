"""
Platrixa
Sprint 12E - Production Integration, Agentic Evidence Retrieval & Audit Loop
backend/audit_trail.py

Audit moat: a deterministic, machine-readable audit trail for every
metric the user selects, plus a pure HTML renderer for the UI.

The trail exposes:
    metric / final value / status / formula / dependencies / source
    document / page / evidence quote / source tier / provenance verdict /
    reconciliation status / anomalies / calculation lineage / next action.

Bounding-box honesty: if physical bounding-box coordinates are available
from the extraction layer they are exposed; otherwise the renderer shows
"bounding box unavailable" - it never fabricates coordinates.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from backend.maths.decision_graph import DecisionNode
from backend.maths.status import BLOCKED, REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Machine-readable audit trail
# ---------------------------------------------------------------------------


def build_audit_trail(node: DecisionNode,
                      bounding_boxes: Optional[Dict[str, Dict[str, Any]]] = None,
                      ) -> Dict[str, Any]:
    """Build the structured audit trail for one DecisionNode.

    `bounding_boxes`: optional {concept: {"x0":..., "y0":..., "x1":...,
    "y1":..., "page":...}} supplied by the extraction layer. When a
    concept has no coordinates the trail records "unavailable" - never a
    fabricated box.
    """
    leaves = node.evidence.leaves if node.evidence else []
    chain = list(node.evidence.chain) if node.evidence else []
    evidence_rows = []
    for leaf in leaves:
        box = (bounding_boxes or {}).get(leaf.concept) or {}
        evidence_rows.append({
            "concept": leaf.concept,
            "value": float(leaf.value) if leaf.value is not None else None,
            "status": leaf.status,
            "source": leaf.source,
            "document": leaf.document_name,
            "page": leaf.page,
            "evidence_quote": leaf.evidence,
            "source_tier": leaf.tier,
            "provider": leaf.provider,
            "identifier": leaf.identifier,
            "period": leaf.period,
            "currency": leaf.currency,
            "unit": leaf.unit,
            "excel_coordinate": leaf.excel_coordinate,
            "bounding_box": box if box else "unavailable",
        })
    return {
        "metric": node.target,
        "value": float(node.value) if node.value is not None else None,
        "display_value": node.display_value,
        "status": node.status,
        "confidence_state": node.confidence_state,
        "decision": node.decision,
        "formula": node.formula,
        "formula_id": node.formula_id,
        "dependencies": list(node.dependencies),
        "missing": list(node.missing or []),
        "source_tier": node.source_tier,
        "provenance": node.provenance_verdict.to_dict()
        if node.provenance_verdict else None,
        "reconciliation": list(node.reconciliation),
        "anomalies": list(node.anomalies),
        "lineage_chain": chain,
        "evidence": evidence_rows,
        "next_action": node.next_action,
        "reason": node.reason,
        "blocking_reason": node.blocking_reason,
    }


# ---------------------------------------------------------------------------
# Pure HTML rendering (no Streamlit)
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape("—" if value in (None, "") else str(value))


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def render_audit_trail_html(payload: Dict[str, Any],
                            bounding_boxes: Optional[Dict[str, Any]] = None,
                            ) -> str:
    """Pure-HTML audit trail for one metric (Sprint 12E section 6).

    Never fabricates: missing provenance renders '—'; missing bounding
    boxes render 'bounding box unavailable'.
    """
    metric = _esc(payload.get("metric") or payload.get("target") or "Metric")
    value = _fmt(payload.get("value"))
    status = _esc(payload.get("status") or "—")
    confidence = _esc(payload.get("confidence_state") or "—")
    formula = _esc(payload.get("formula") or "—")
    decision = _esc(payload.get("decision") or "—")
    next_action = _esc(payload.get("next_action") or "none")
    source_tier = _esc(payload.get("source_tier") or "—")

    deps = payload.get("dependencies") or []
    deps_html = " · ".join(_esc(d) for d in deps) if deps else "—"

    provenance = payload.get("provenance") or {}
    prov_verdict = _esc(provenance.get("verdict") or "—")
    prov_reasons = provenance.get("reasons") or []
    prov_html = "".join(
        f'<div class="fte-audit-reason">{_esc(r)}</div>' for r in prov_reasons[:4]
    )

    recon = payload.get("reconciliation") or []
    recon_html = "—"
    if recon:
        recon_html = "".join(
            f'<div class="fte-audit-reason">{_esc(r.get("reason") or r.get("status") or "review")}</div>'
            for r in recon[:4]
        )

    anomalies = payload.get("anomalies") or []
    anomaly_html = "—"
    if anomalies:
        anomaly_html = "".join(
            f'<div class="fte-audit-reason">{_esc(a.get("kind") or "anomaly")}: {_esc(a.get("reason") or "")}</div>'
            for a in anomalies[:4]
        )

    evidence_rows = payload.get("evidence") or []
    evidence_html = "—"
    if evidence_rows:
        cells = []
        for e in evidence_rows:
            box = (bounding_boxes or {}).get(e.get("concept")) or \
                e.get("bounding_box")
            box_text = "bounding box unavailable"
            if isinstance(box, dict) and box:
                box_text = (
                    f"bbox x0={box.get('x0')} y0={box.get('y0')} "
                    f"x1={box.get('x1')} y1={box.get('y1')}"
                    + (f" p.{box.get('page')}" if box.get("page") else "")
                )
            doc = e.get("document") or e.get("source") or "—"
            page = e.get("page") or "—"
            quote = e.get("evidence_quote") or "—"
            cells.append(
                f'<tr>'
                f'<td>{_esc(e.get("concept"))}</td>'
                f'<td>{_fmt(e.get("value"))}</td>'
                f'<td>{_esc(e.get("status"))}</td>'
                f'<td>{_esc(doc)}</td>'
                f'<td>{_esc(page)}</td>'
                f'<td class="fte-audit-quote">{_esc(quote)}</td>'
                f'<td>{_esc(e.get("source_tier"))}</td>'
                f'<td>{_esc(box_text)}</td>'
                f'</tr>'
            )
        evidence_html = (
            '<table class="fte-audit-table"><thead><tr>'
            '<th>Concept</th><th>Value</th><th>Status</th><th>Document</th>'
            '<th>Page</th><th>Evidence quote</th><th>Tier</th><th>Location</th>'
            '</tr></thead><tbody>' + "".join(cells) + "</tbody></table>"
        )

    lineage_html = "—"
    chain = payload.get("lineage_chain") or payload.get("lineage") or []
    if chain:
        steps = []
        for s in chain:
            formula_tag = s.get("formula_id") or s.get("formula") or s.get("kind") or "step"
            steps.append(
                f'<li class="fte-audit-step">{_esc(s.get("concept"))} '
                f'= {_esc(s.get("display_value") or _fmt(s.get("value")))} '
                f'[{_esc(s.get("status"))} · {_esc(formula_tag)}]</li>'
            )
        lineage_html = '<ul class="fte-audit-lineage">' + "".join(steps) + "</ul>"

    missing = payload.get("missing") or []
    missing_html = "—"
    if missing:
        missing_html = " · ".join(_esc(m) for m in sorted(missing))

    blocked_note = ""
    if status == BLOCKED or payload.get("blocking_reason"):
        blocked_note = (
            '<div class="fte-audit-blocked">'
            f'No fabricated value. Reason: {_esc(payload.get("blocking_reason") or payload.get("reason") or "required evidence unavailable")}'
            "</div>"
        )

    return f"""
<style>
.fte-audit-trail{{font-size:.8rem;line-height:1.5;color:var(--fte-text,#e8e6e3)}}
.fte-audit-head{{font-weight:600;margin:.2rem 0 .35rem;padding-bottom:.25rem;border-bottom:1px solid rgba(255,255,255,.12)}}
.fte-audit-meta{{width:100%;border-collapse:collapse;margin-bottom:.4rem}}
.fte-audit-meta td{{padding:.18rem .45rem;border:1px solid rgba(255,255,255,.08);vertical-align:top}}
.fte-audit-section{{font-weight:600;margin:.45rem 0 .15rem}}
.fte-audit-reason{{font-size:.76rem;opacity:.85;margin:.1rem 0}}
.fte-audit-quote{{font-style:italic;opacity:.9}}
.fte-audit-table{{width:100%;border-collapse:collapse;font-size:.74rem}}
.fte-audit-table th,.fte-audit-table td{{border:1px solid rgba(255,255,255,.08);padding:.2rem .35rem;text-align:left}}
.fte-audit-lineage{{margin:.2rem 0;padding-left:1.1rem}}
.fte-audit-step{{margin:.12rem 0}}
.fte-audit-blocked{{margin-top:.4rem;padding:.3rem .5rem;border-left:2px solid #e05252;background:rgba(224,82,82,.08);border-radius:4px}}
</style>
<div class="fte-audit-trail">
  <div class="fte-audit-head">Audit Trail — {metric}</div>
  <table class="fte-audit-meta">
    <tr><td>Final value</td><td>{value}</td>
        <td>Status</td><td>{status}</td>
        <td>Confidence</td><td>{confidence}</td></tr>
    <tr><td>Formula</td><td colspan="3"><code>{formula}</code></td>
        <td>Source tier</td><td>{source_tier}</td></tr>
    <tr><td>Dependencies</td><td colspan="3">{deps_html}</td>
        <td>Missing</td><td>{missing_html}</td></tr>
    <tr><td>Decision</td><td colspan="3">{decision}</td>
        <td>Next action</td><td>{next_action}</td></tr>
  </table>
  <div class="fte-audit-section">Provenance verdict: {prov_verdict}</div>
  {prov_html}
  <div class="fte-audit-section">Reconciliation</div>{recon_html}
  <div class="fte-audit-section">Anomalies</div>{anomaly_html}
  <div class="fte-audit-section">Evidence lineage</div>
  {lineage_html}
  <div class="fte-audit-section">Source evidence</div>
  {evidence_html}
  {blocked_note}
</div>
"""
