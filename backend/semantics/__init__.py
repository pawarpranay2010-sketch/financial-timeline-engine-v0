"""
Platrixa — Semantic IR boundary (Phase 17)
==========================================

This package defines the typed boundary between model output and
deterministic accounting, plus the versioned execution-evidence chain.

    Model
      ↓
    CandidateSemanticIR        (what the model BELIEVES the input means)
      ↓
    deterministic grounding    (ExpandedGroundingGate — the ONLY constructor
      ↓                         of GroundedSemanticIR)
    GroundedSemanticIR         (ONLY claims grounding verified in the source)
      ↓
    deterministic accounting   (accepts ONLY GroundedSemanticIR)
      ↓
    RuleEngine → Kernel        (Kernel owns final state)

Authority invariants enforced structurally:

  * CandidateSemanticIR has no authority: it is a frozen, validated view of
    the model's proposal. It cannot be passed to accounting — only the
    grounding gate can convert it into GroundedSemanticIR, and that
    conversion happens ONLY after deterministic grounding succeeds.

  * GroundedSemanticIR can only be constructed through
    ``GroundedSemanticIR.from_candidate(...)``, which requires a PASSING
    ``GroundingResult`` (safe_for_kernel=True). There is no public
    constructor path from raw dicts or from a failing grounding result.

  * ``GroundedSemanticIR.for_accounting()`` is the ONLY accounting
    admission contract: accounting consumes exactly this, never the
    candidate.

Pure module: no model, no network, no accounting logic. Deterministic.
"""

from __future__ import annotations

from .ir import (
    CandidateSemanticIR,
    GroundedSemanticIR,
    SemanticIRError,
    SCHEMA_VERSION,
)
from .evidence import (
    ExecutionEvidence,
    EVIDENCE_SCHEMA_VERSION,
    sha256_of_json,
)

__all__ = [
    "CandidateSemanticIR",
    "GroundedSemanticIR",
    "SemanticIRError",
    "SCHEMA_VERSION",
    "ExecutionEvidence",
    "EVIDENCE_SCHEMA_VERSION",
    "sha256_of_json",
]
