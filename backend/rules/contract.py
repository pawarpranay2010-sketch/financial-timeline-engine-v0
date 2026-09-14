"""
Platrixa — Rule Pack contract (Phase 10)
=========================================

Minimal, frozen contract objects for the developer-facing rule boundary.

Ownership (Phase 10K):

    AI/model            interprets student language
    Grounding           checks interpretation against the input text
    Rule Pack           developer-specific CONSTRAINTS (this module)
    Rule Engine         evaluates constraints deterministically
    Accounting kernel   deterministic accounting truth (untouched)
    Runtime/state       final status authority (untouched)
    Persistence         stores results

SECURITY INVARIANT (Phase 10F):

    A developer rule produces a RuleDecision that may only CONSTRAIN the
    outcome (downgrade VERIFIED to REVIEW_REQUIRED/BLOCKED). There is no
    field, channel, or method by which a rule can request, suggest, or
    manufacture the final VERIFIED state. RuleDecision is frozen and
    validated at construction: malformed decisions cannot exist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Rule outcomes (Phase 10B). External dependency failure is NEVER success.
# ---------------------------------------------------------------------------

OUTCOME_PASS = "PASS"
OUTCOME_FAIL = "FAIL"
OUTCOME_UNAVAILABLE = "UNAVAILABLE"
OUTCOME_ERROR = "ERROR"

VALID_OUTCOMES = (OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_UNAVAILABLE, OUTCOME_ERROR)

# Outcomes that block a deterministic VERIFIED produced by the runtime.
BLOCKING_OUTCOMES = (OUTCOME_FAIL, OUTCOME_UNAVAILABLE, OUTCOME_ERROR)


class RulePackError(Exception):
    """Raised when a rule pack (YAML or hook registry) is malformed."""


class RuleContractError(Exception):
    """Raised when a RuleDecision/RuleContext would violate the contract."""


# ---------------------------------------------------------------------------
# RuleDecision (Phase 10B) — frozen, validated, authority-free by design
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleDecision:
    """
    The ONLY thing a developer rule may return.

    Deliberately contains NO status field: there is no way to express
    "make the final state VERIFIED" (or any final state) through this
    contract. The runtime translates decisions into final states.
    """

    rule_id: str
    outcome: str
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise RuleContractError("rule_id must be a non-empty string")
        if self.outcome not in VALID_OUTCOMES:
            raise RuleContractError(
                f"outcome must be one of {VALID_OUTCOMES}, got {self.outcome!r}"
            )
        if not isinstance(self.message, str):
            raise RuleContractError("message must be a string")
        # Normalize metadata into a read-only mapping so a decision cannot be
        # mutated after the fact and cannot smuggle mutable state.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))

    @property
    def is_blocking(self) -> bool:
        """True when this decision must prevent a final VERIFIED."""
        return self.outcome in BLOCKING_OUTCOMES


# ---------------------------------------------------------------------------
# RuleContext (Phase 10D) — controlled read-only view for hooks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleContext:
    """
    What a custom hook may see.

    `interpretation` is exposed as a read-only mapping: hooks can inspect the
    validated 18-field candidate but cannot mutate kernel state through the
    context (mutation attempts raise TypeError, which the engine maps to a
    fail-closed ERROR decision).
    """

    request_id: str
    raw_input: str
    interpretation: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str):
            raise RuleContractError("request_id must be a string")
        if not isinstance(self.raw_input, str):
            raise RuleContractError("raw_input must be a string")
        object.__setattr__(
            self, "interpretation", MappingProxyType(dict(self.interpretation or {}))
        )


# ---------------------------------------------------------------------------
# Custom rule hook protocol (Phase 10D)
# ---------------------------------------------------------------------------


class RuleHook(ABC):
    """
    Public extension protocol for complex enterprise logic.

    Subclass, set `rule_id`, and implement `validate()`. Return a
    RuleDecision — never a final status. The engine invokes `validate` inside
    a fail-closed guard: an exception or a non-RuleDecision return becomes an
    ERROR decision (never success).
    """

    rule_id: str = ""

    @abstractmethod
    def validate(self, context: RuleContext) -> RuleDecision:
        """Evaluate this rule against the controlled context."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Evidence record (Phase 10G)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    """Structured audit evidence for one executed rule."""

    rule_id: str
    source: str  # "yaml" | "python_hook"
    outcome: str  # PASS | FAIL | UNAVAILABLE | ERROR
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source": self.source,
            "result": self.outcome,
            "message": self.message,
            "metadata": dict(self.metadata),
        }


def decision_to_result(decision: RuleDecision, source: str) -> RuleResult:
    """Convert a developer decision into runtime evidence."""
    return RuleResult(
        rule_id=decision.rule_id,
        source=source,
        outcome=decision.outcome,
        message=decision.message,
        metadata=dict(decision.metadata),
    )


__all__ = [
    "OUTCOME_PASS",
    "OUTCOME_FAIL",
    "OUTCOME_UNAVAILABLE",
    "OUTCOME_ERROR",
    "VALID_OUTCOMES",
    "BLOCKING_OUTCOMES",
    "RuleDecision",
    "RuleContext",
    "RuleHook",
    "RuleResult",
    "RulePackError",
    "RuleContractError",
    "decision_to_result",
]
