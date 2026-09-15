"""
Platrixa — Semantic IR types (Phase 17 §1–§4)
=============================================

Two typed representations with deliberately different authority:

CandidateSemanticIR
    What the model BELIEVES the input means. Frozen, validated view over the
    model's 18-field candidate dict. May be incomplete, ambiguous, or wrong.
    Has NO authority over financial truth and CANNOT reach accounting.

GroundedSemanticIR
    Only claims that deterministic grounding established as supported by the
    source input. The ONLY semantic representation permitted into
    deterministic accounting.

The grounding gate (backend/maths/fyjc_grounding_gate.py) remains the single
deterministic grounding implementation — this module does NOT reimplement
grounding. It makes the gate's decision a structural requirement: a
GroundedSemanticIR cannot exist without a passing GroundingResult.

Pure module: no model, no network, no accounting logic. Deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

# Semantic IR schema version. Bump when the grounded contract changes in a
# way that changes hashed bytes or accounting admission semantics.
SCHEMA_VERSION = "sem-ir-1"

# Forbidden accounting-truth fields — mirrored from the model provider
# boundary so the IR itself can never carry accounting truth.
_FORBIDDEN_ACCOUNTING_FIELDS = frozenset({
    "journal",
    "journal_entry",
    "debit_lines",
    "credit_lines",
    "ledger",
    "balances",
    "debit_account",
    "credit_account",
})

# The semantic fields accounting may consume from the grounded IR.
GROUNDABLE_FIELDS = (
    "transaction_type",
    "parties",
    "amounts",
    "payment_method",
    "references",
    "ambiguities",
)


class SemanticIRError(Exception):
    """Raised when a semantic IR would violate its authority contract."""


def _canonical_json(value: Any) -> str:
    """Deterministic JSON serialization for hashing (sorted keys, compact)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _field_digest(payload: Any) -> str:
    """sha256 of the canonical JSON of one field's value ('' when absent).

    Hash definition (contract, used by evidence): the canonical JSON bytes
    of the field's parsed value — sorted keys, compact separators, strings
    as-is. Equivalent inputs produce identical digests.
    """
    if payload is None:
        return ""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateSemanticIR:
    """
    The model's PROPOSED meaning of the input. Candidate, never truth.

    Constructed from the provider's validated 18-field candidate dict.
    Frozen and validated at construction: forbidden accounting fields are
    structurally impossible to carry.

    There is deliberately no path from this type to accounting: passing a
    candidate where a grounded IR is required fails at runtime (and fails
    loudly, by AttributeError/type check) rather than silently.
    """

    raw_input: str
    fields: Mapping[str, Any]
    model_id: str = ""
    model_revision: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.raw_input, str):
            raise SemanticIRError("raw_input must be a string")
        if not isinstance(self.fields, Mapping):
            raise SemanticIRError("fields must be a mapping (the 18-field candidate)")
        forbidden = _FORBIDDEN_ACCOUNTING_FIELDS & set(self.fields.keys())
        if forbidden:
            raise SemanticIRError(
                "candidate IR cannot carry accounting-truth fields: "
                + ", ".join(sorted(forbidden))
            )
        # Freeze the view: shallow-immutable mapping over a private copy.
        object.__setattr__(self, "fields", dict(self.fields))

    # ------------------------------------------------------------------
    # Accessors (read-only views over the candidate)
    # ------------------------------------------------------------------

    def get(self, field_name: str, default: Any = None) -> Any:
        return self.fields.get(field_name, default)

    def transaction_type(self) -> Any:
        return self.fields.get("transaction_type_enum") or self.fields.get("transaction_type")

    def parties(self) -> Any:
        return self.fields.get("parties")

    def amounts(self) -> Any:
        return self.fields.get("amounts")

    def payment_method(self) -> Any:
        return self.fields.get("payment_method_enum") or self.fields.get("payment_method")

    def references(self) -> Any:
        return self.fields.get("references")

    def ambiguities(self) -> Any:
        return self.fields.get("ambiguities")

    def suggested_status(self) -> Any:
        """The model's suggestion — NEVER authority. Exposed for evidence and
        for the grounding gate's fail-closed VERIFIED check only."""
        return self.fields.get("suggested_status")

    @property
    def content_digest(self) -> str:
        """Deterministic digest of the candidate interpretation content.

        Hash definition: sha256 over the canonical JSON of
        ``{"raw_input": <raw_input>, "fields": <fields>}``. The model
        identity/revision are deliberately EXCLUDED so this digest binds the
        *interpretation content*; identity is recorded separately in
        ExecutionEvidence.model_identity.
        """
        payload = {"raw_input": self.raw_input, "fields": dict(self.fields)}
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ir_type": "candidate",
            "raw_input": self.raw_input,
            "fields": dict(self.fields),
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class GroundedSemanticIR:
    """
    ONLY grounding-verified claims. The sole accounting admission contract.

    Construction is deliberately restricted: the only public constructor is
    :meth:`GroundedSemanticIR.from_candidate`, which REQUIRES a passing
    ``GroundingResult`` (safe_for_kernel=True) produced by the deterministic
    grounding gate. Every other construction path raises SemanticIRError.

    Accounting code that accepts only this type makes
    ``accounting(candidate_ir)`` structurally impossible in the normal
    execution path.
    """

    raw_input: str
    fields: Mapping[str, Any]
    grounding_issues: tuple = ()
    grounding_version: str = "expanded-gate-1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise SemanticIRError(
            "GroundedSemanticIR cannot be constructed directly; "
            "use GroundedSemanticIR.from_candidate(candidate, grounding_result) "
            "with a PASSING deterministic grounding result"
        )

    # ------------------------------------------------------------------
    # The ONLY construction path
    # ------------------------------------------------------------------

    @classmethod
    def from_candidate(
        cls,
        candidate: CandidateSemanticIR,
        grounding_result: Any,
    ) -> "GroundedSemanticIR":
        """
        Convert a CandidateSemanticIR into a GroundedSemanticIR.

        ``grounding_result`` must be a PASSING result from the deterministic
        grounding gate (``safe_for_kernel`` truthy, ``grounded`` truthy).
        Anything else raises SemanticIRError — fail closed, no manufactured
        support, no silent field-filling.

        The grounded fields are the candidate's semantic fields (already
        gate-verified against the source text). Non-semantic bookkeeping
        keys (``_grounding_issues``, ``_grounded``, confidence bookkeeping,
        model metadata) are NOT forwarded to accounting.
        """
        if not isinstance(candidate, CandidateSemanticIR):
            raise SemanticIRError(
                "from_candidate requires a CandidateSemanticIR, got "
                f"{type(candidate).__name__}"
            )
        safe = bool(getattr(grounding_result, "safe_for_kernel", False))
        grounded_flag = bool(getattr(grounding_result, "grounded", False))
        if not safe or not grounded_flag:
            raise SemanticIRError(
                "grounding did not pass: refusing to manufacture a "
                "GroundedSemanticIR from ungrounded input"
            )
        fields = {name: candidate.fields.get(name) for name in GROUNDABLE_FIELDS}
        issues = tuple(getattr(grounding_result, "issues", []) or ())
        obj = object.__new__(cls)
        object.__setattr__(obj, "raw_input", candidate.raw_input)
        object.__setattr__(obj, "fields", dict(fields))
        object.__setattr__(obj, "grounding_issues", issues)
        object.__setattr__(obj, "grounding_version", "expanded-gate-1")
        return obj

    # ------------------------------------------------------------------
    # The ONLY accounting admission contract
    # ------------------------------------------------------------------

    def for_accounting(self) -> Dict[str, Any]:
        """
        The payload deterministic accounting may consume.

        Contains ONLY grounded semantic fields plus the source text (which
        the existing deterministic flow is built around). Accounting code
        that accepts only ``GroundedSemanticIR.for_accounting()`` output can
        never consume an ungrounded candidate.
        """
        return {
            "raw_input": self.raw_input,
            **{name: self.fields.get(name) for name in GROUNDABLE_FIELDS},
        }

    # ------------------------------------------------------------------
    # Views / digests
    # ------------------------------------------------------------------

    def get(self, field_name: str, default: Any = None) -> Any:
        return self.fields.get(field_name, default)

    def transaction_type(self) -> Any:
        return self.fields.get("transaction_type")

    def parties(self) -> Any:
        return self.fields.get("parties")

    def amounts(self) -> Any:
        return self.fields.get("amounts")

    def payment_method(self) -> Any:
        return self.fields.get("payment_method")

    @property
    def content_digest(self) -> str:
        """Deterministic digest of the grounded interpretation content.

        Hash definition: sha256 over the canonical JSON of
        ``{"raw_input": <raw_input>, "fields": <grounded fields>,
        "grounding_version": <version>}``.
        """
        payload = {
            "raw_input": self.raw_input,
            "fields": dict(self.fields),
            "grounding_version": self.grounding_version,
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def field_digest(self, field_name: str) -> str:
        """Deterministic per-field digest (canonical-JSON sha256, '' if absent)."""
        return _field_digest(self.fields.get(field_name))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ir_type": "grounded",
            "raw_input": self.raw_input,
            "fields": dict(self.fields),
            "grounding_issues": list(self.grounding_issues),
            "grounding_version": self.grounding_version,
            "content_digest": self.content_digest,
        }


__all__ = [
    "CandidateSemanticIR",
    "GroundedSemanticIR",
    "SemanticIRError",
    "SCHEMA_VERSION",
    "GROUNDABLE_FIELDS",
]
