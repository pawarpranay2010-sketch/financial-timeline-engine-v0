"""
Platrixa — Persistence boundary contract (Phase 7E)

This module defines the application-facing persistence contract for
KernelResult. It is intentionally free of any PostgreSQL, SQLAlchemy, or
database implementation details.

Target flow (Phase 7E):

    Student input
        ↓
    ModelProvider
        ↓
    18-field schema validation
        ↓
    GroundingGate
        ↓
    Deterministic Kernel
        ↓
    KernelResult
        ↓
    ResultPersistence        ← this boundary
        ↓
    PostgreSQL

Architectural rules enforced here:

  * The database stores already-determined results. It never calculates,
    reinterprets, infers, or overrides accounting truth.
  * The Kernel's terminal status vocabulary is authoritative and is stored
    verbatim. Terminal states are never collapsed into a generic error.
  * Persistence failures are reported as distinct `PersistenceFailure`
    values. A database failure never becomes a successful accounting
    result, and accounting truth is never changed because persistence
    failed.
  * The Kernel depends on this contract, never on the concrete PostgreSQL
    implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Persisted result shapes
# ---------------------------------------------------------------------------


@dataclass
class PersistedResult:
    """
    Immutable view of what was actually stored, read back from the store.

    This is a *deserialized* projection of the persisted record. It exists so
    callers can verify round-trip fidelity without touching SQLAlchemy models.
    """

    interaction_id: Optional[int] = None
    interpretation_id: Optional[int] = None
    stored_status: str = ""
    stored_raw_input: str = ""
    stored_model_id: str = ""
    stored_provider_revision: str = ""
    stored_adapter_revision: str = ""
    stored_verification_status: str = ""
    stored_grounding_issues: List[str] = field(default_factory=list)
    stored_issues: List[str] = field(default_factory=list)
    stored_accounting_result: Optional[Dict[str, Any]] = None
    stored_interpretation_candidate: Optional[Dict[str, Any]] = None
    persisted_at: Optional[str] = None


@dataclass
class PersistenceFailure:
    """
    A persistence-layer failure.

    Distinct from Kernel terminal states: this is a *storage* failure, not a
    semantic/accounting one. It is returned, never silently swallowed, and it
    never mutates the KernelResult it tried to persist.
    """

    kind: str  # PERSISTENCE_UNAVAILABLE | PERSISTENCE_WRITE_FAILED
    reason: str
    detail: Optional[str] = None


# Failure kinds (storage-side, do not collide with Kernel terminal states)
PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
PERSISTENCE_WRITE_FAILED = "PERSISTENCE_WRITE_FAILED"


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@runtime_checkable
class ResultPersistence(Protocol):
    """
    Storage-side contract for KernelResult persistence.

    Implementations must:
      * accept already-determined KernelResults without recomputing anything
      * store the authoritative terminal status verbatim
      * preserve raw student input, model identity, verification state, and
        the accounting result exactly as produced by the Kernel
      * fail closed: return a PersistenceFailure rather than pretending a
        write succeeded
    """

    def persist(self, result: Any) -> "PersistedResult | PersistenceFailure":
        """
        Persist one KernelResult.

        Returns PersistedResult on success, PersistenceFailure on failure.
        Implementations must not raise for ordinary failure modes.
        """
        ...


# ---------------------------------------------------------------------------
# KernelResult extraction (shared by all implementations)
# ---------------------------------------------------------------------------
#
# This is the single place where a KernelResult is flattened into the
# storage-side record shape. Implementations call this so there is exactly
# one mapping between Kernel results and persistence records — and so no
# implementation is tempted to recompute accounting fields.


def build_persistence_record(result: Any) -> Dict[str, Any]:
    """
    Flatten a KernelResult into a plain storage record.

    Accepts any object carrying the KernelResult attributes. Performs no
    validation, recalculation, or reinterpretation: values are copied
    verbatim from the result object.

    Raises TypeError if the object does not look like a KernelResult, so a
    wrong object type fails loudly instead of persisting garbage.
    """
    required = ("status", "raw_input")
    for attr in required:
        if not hasattr(result, attr):
            raise TypeError(
                "persistence requires a KernelResult-like object "
                f"(missing attribute: {attr})"
            )

    accounting = getattr(result, "accounting_result", None)
    candidate = getattr(result, "interpretation_candidate", None)

    metadata = getattr(result, "metadata", None) or {}

    interpretation = getattr(result, "interpretation", None)
    if interpretation is not None:
        model_id = (
            getattr(interpretation, "model_id", None)
            or metadata.get("model_id")
            or ""
        )
    else:
        model_id = metadata.get("model_id") or ""

    grounding_issues = getattr(result, "grounding_issues", None) or []

    return {
        # identity
        "request_id": getattr(result, "request_id", None),
        # original student input
        "raw_input": getattr(result, "raw_input", ""),
        # authoritative terminal state, stored verbatim
        "status": getattr(result, "status", ""),
        "status_label": getattr(result, "status_label", ""),
        # verification / grounding state
        "verification_status": getattr(result, "verification_status", None),
        "grounding_issues": list(grounding_issues),
        "issues": list(getattr(result, "issues", None) or []),
        "next_action": getattr(result, "next_action", None),
        # model identity (Phase 6C pins survive through the boundary)
        "model_id": model_id,
        "provider_revision": metadata.get("provider_revision", ""),
        "adapter_revision": metadata.get("adapter_revision", ""),
        # deterministic accounting output, verbatim
        "accounting_result": accounting,
        # semantic interpretation candidate (18-field contract), verbatim
        "interpretation_candidate": candidate,
    }


def roundtrip_snapshot(record: Dict[str, Any]) -> PersistedResult:
    """
    Build a PersistedResult view from a stored record dict.

    Used by implementations to produce the read-back projection from the
    values they actually stored. No field is recomputed.
    """
    return PersistedResult(
        interaction_id=record.get("interaction_id"),
        interpretation_id=record.get("interpretation_id"),
        stored_status=record.get("status", ""),
        stored_raw_input=record.get("raw_input", ""),
        stored_model_id=record.get("model_id", "") or "",
        stored_provider_revision=record.get("provider_revision", "") or "",
        stored_adapter_revision=record.get("adapter_revision", "") or "",
        stored_verification_status=record.get("verification_status", "") or "",
        stored_grounding_issues=list(record.get("grounding_issues") or []),
        stored_issues=list(record.get("issues") or []),
        stored_accounting_result=record.get("accounting_result"),
        stored_interpretation_candidate=record.get("interpretation_candidate"),
        persisted_at=record.get("persisted_at"),
    )


# ---------------------------------------------------------------------------
# Reference in-memory implementation (tests / local development)
# ---------------------------------------------------------------------------


class InMemoryResultPersistence:
    """
    Faithful in-memory implementation of the ResultPersistence contract.

    Mirrors the PostgreSQL implementation's fail-closed semantics:
      * stores records verbatim
      * returns PersistenceFailure on storage errors (injected via
        `fail_with`)
      * never mutates the result it was given
    """

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self._fail_with: Optional[PersistenceFailure] = None

    def fail_with(self, failure: PersistenceFailure) -> None:
        """Inject the next persistence failure (for fail-closed testing)."""
        self._fail_with = failure

    def persist(self, result: Any) -> "PersistedResult | PersistenceFailure":
        if self._fail_with is not None:
            failure = self._fail_with
            self._fail_with = None
            return failure
        try:
            record = build_persistence_record(result)
            record["interaction_id"] = len(self.records) + 1
            record["interpretation_id"] = len(self.records) + 1
            record["persisted_at"] = "in-memory"
            self.records.append(record)
            return roundtrip_snapshot(record)
        except Exception as exc:  # pragma: no cover - defensive
            return PersistenceFailure(
                kind=PERSISTENCE_WRITE_FAILED,
                reason="in-memory write failed",
                detail=str(exc),
            )


__all__ = [
    "PersistedResult",
    "PersistenceFailure",
    "ResultPersistence",
    "PERSISTENCE_UNAVAILABLE",
    "PERSISTENCE_WRITE_FAILED",
    "build_persistence_record",
    "roundtrip_snapshot",
    "InMemoryResultPersistence",
]
