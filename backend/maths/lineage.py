"""
Platrixa
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/lineage.py

Deterministic lineage.

Every derived result exposes:
    * target             the quantity that was derived
    * formula            the registered relationship used (final step)
    * inputs used        each input: concept, value, status, provenance,
                         source evidence (page/document when available)
    * traversal path     ordered node ids visited (dependencies first)
    * intermediate results  every step value produced along the path
    * status             six-tier status of the result
    * reason             explicit reason for BLOCKED / REVIEW_REQUIRED

No derived result may exist without lineage.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class LineageInput:
    """One input consumed by a formula step."""

    concept: str
    value: Optional[Decimal] = None
    display_value: str = "—"
    status: str = "—"
    provenance_tier: str = "—"
    source: str = "—"
    page: str = "—"
    evidence: str = "—"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "status": self.status,
            "provenance_tier": self.provenance_tier,
            "source": self.source,
            "page": self.page,
            "evidence": self.evidence,
        }


@dataclass
class LineageStep:
    """One step in the traversal (intermediate result)."""

    concept: str
    formula_id: str
    formula: str
    value: Optional[Decimal] = None
    display_value: str = "—"
    status: str = "—"
    inputs: List[LineageInput] = field(default_factory=list)
    kind: str = "forward"  # forward | reverse | direct

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "formula_id": self.formula_id,
            "formula": self.formula,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "status": self.status,
            "kind": self.kind,
            "inputs": [i.to_dict() for i in self.inputs],
        }


@dataclass
class LineageRecord:
    """Complete deterministic lineage for one result."""

    target: str
    status: str
    formula_id: Optional[str] = None
    formula: str = "—"
    value: Optional[Decimal] = None
    display_value: str = "—"
    reason: Optional[str] = None
    steps: List[LineageStep] = field(default_factory=list)
    traversal_path: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "formula_id": self.formula_id,
            "formula": self.formula,
            "value": float(self.value) if self.value is not None else None,
            "display_value": self.display_value,
            "reason": self.reason,
            "traversal_path": list(self.traversal_path),
            "steps": [s.to_dict() for s in self.steps],
        }

    def render_text(self) -> str:
        """Human-readable lineage tree (auditable, mirrors the Platrixa
        lineage convention)."""
        lines = [f"{self.target}"]
        if self.formula_id:
            lines.append(f"├── Formula: {self.formula_id} = {self.formula}")
        for s in self.steps:
            if s.kind == "direct":
                lines.append(f"├── {s.concept} [direct fact, {s.status}]")
                continue
            lines.append(
                f"├── {s.concept} = {s.display_value} "
                f"[{s.formula_id}, {s.status}]"
            )
            for i in s.inputs:
                ev = f" ({i.evidence})" if i.evidence not in ("", "—") else ""
                pg = f" p.{i.page}" if i.page not in ("", "—") else ""
                lines.append(
                    f"│   ├── {i.concept} = {i.display_value} "
                    f"[{i.status}, {i.provenance_tier}]{pg}{ev}"
                )
        lines.append(f"└── Result: {self.display_value} ({self.status})")
        if self.reason:
            lines.append(f"    Reason: {self.reason}")
        return "\n".join(lines)
