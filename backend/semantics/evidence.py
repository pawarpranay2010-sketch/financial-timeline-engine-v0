"""
Platrixa — Execution evidence chain (Phase 17 §7–§8)
====================================================

A versioned, immutable record binding one execution to the actual
configuration that produced it. Every digest has an EXACT hash definition
(what bytes are hashed) documented on the producing property/field — no
invented cryptographic identities.

Fields are populated from live runtime values wherever the runtime actually
measures them:

    input_hash                     sha256(raw_input UTF-8)
    candidate_interpretation_hash  CandidateSemanticIR.content_digest
                                   (canonical-JSON sha256 of
                                   {raw_input, fields})
    grounded_interpretation_hash   GroundedSemanticIR.content_digest
                                   (canonical-JSON sha256 of
                                   {raw_input, fields, grounding_version})
    model_identity                 provider's reported model/adapter identity
                                   (model_id, base/adapter revisions)
    adapter_identity               adapter repo/revision (may be empty when
                                   the provider reports none)
    schema_version                 semantic IR schema version
    prompt_version                 identity of the exact system-prompt
                                   template the runtime used (sha256 of the
                                   prompt text, truncated label)
    grounding_version              grounding gate implementation version
    accounting_version             accounting implementation identity
                                   (the module path + status vocabulary the
                                   runtime actually executed)
    rule_pack_hash                 sha256 of the YAML rule pack file bytes
                                   ('' when no pack configured)
    rule_evidence                  the rule results the runtime produced
    final_state                    the state the Kernel actually returned

Evidence MUST reflect actual execution (§8): values are captured from the
runtime objects after processing — never asserted independently. No secrets
are recorded.

Pure module: no model, no network. Deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

EVIDENCE_SCHEMA_VERSION = "exec-evidence-1"

GROUNDING_VERSION = "expanded-gate-1"      # backend/maths/fyjc_grounding_gate.py
ACCOUNTING_VERSION = "hardened-bk-15i"     # backend/maths/fyjc_accounting.py flow


def sha256_of_json(value: Any) -> str:
    """Deterministic sha256 of the canonical JSON of ``value``.

    Hash definition: UTF-8 bytes of ``json.dumps(value, sort_keys=True,
    separators=(",", ":"), default=str)``.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def sha256_of_text(text: str) -> str:
    """sha256 of the UTF-8 bytes of ``text`` ('' for empty)."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_identity(system_prompt: Optional[str]) -> str:
    """
    Identity of the exact system-prompt template used for interpretation.

    Hash definition: ``"prompt:sha256:<first 16 hex of sha256(prompt UTF-8)>``
    for a non-empty prompt, ``""`` when the runtime ran without a prompt
    template. The full prompt text is deliberately NOT recorded (it is
    large and its bytes are hashed, not stored).
    """
    if not system_prompt:
        return ""
    return "prompt:sha256:" + hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]


def rule_pack_identity(pack_path: Optional[str]) -> str:
    """
    sha256 of the rule pack FILE BYTES (exact content executed), '' when no
    pack is configured. This is measured from the file the runtime loaded —
    not from a summary.
    """
    if not pack_path:
        return ""
    try:
        from pathlib import Path

        data = Path(pack_path).read_bytes()
    except OSError:
        return ""  # unreadable pack cannot be attested — recorded as empty,
        # and the runtime itself fails closed on unreadable packs at load.
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ExecutionEvidence:
    """
    Immutable, versioned binding of one execution to its actual inputs.

    Constructed by the Kernel AFTER processing completes, from the runtime
    objects that actually executed (§8: recorded evidence == actual
    execution configuration/result).
    """

    request_id: str = ""
    input_hash: str = ""
    candidate_interpretation_hash: str = ""
    grounded_interpretation_hash: str = ""
    model_identity: Mapping[str, Any] = field(default_factory=dict)
    adapter_identity: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ""
    prompt_version: str = ""
    grounding_version: str = GROUNDING_VERSION
    accounting_version: str = ACCOUNTING_VERSION
    rule_pack_hash: str = ""
    rule_evidence: List[Dict[str, Any]] = field(default_factory=list)
    final_state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "request_id": self.request_id,
            "input_hash": self.input_hash,
            "candidate_interpretation_hash": self.candidate_interpretation_hash,
            "grounded_interpretation_hash": self.grounded_interpretation_hash,
            "model_identity": dict(self.model_identity),
            "adapter_identity": dict(self.adapter_identity),
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "grounding_version": self.grounding_version,
            "accounting_version": self.accounting_version,
            "rule_pack_hash": self.rule_pack_hash,
            "rule_evidence": list(self.rule_evidence),
            "final_state": self.final_state,
        }


__all__ = [
    "ExecutionEvidence",
    "EVIDENCE_SCHEMA_VERSION",
    "GROUNDING_VERSION",
    "ACCOUNTING_VERSION",
    "sha256_of_json",
    "sha256_of_text",
    "prompt_identity",
    "rule_pack_identity",
]
