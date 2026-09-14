"""
Platrixa — Rule Engine (Phase 10E/F)
=====================================

Runtime execution boundary for developer rules.

    RulePack (YAML rules + custom hooks)
        ↓
    RuleEngine.evaluate(...)
        ↓
    RuleDecision[] → structured evidence (RuleResult[])
        ↓
    downgrade-only state policy
        ↓
    final KernelResult (unchanged construction site)

AUTHORITY INVARIANT (Phase 10F): rules evaluate AFTER deterministic
accounting has produced a success state. A rule can only downgrade that
state (VERIFIED → REVIEW_REQUIRED / BLOCKED per the decision hint). There is
no code path by which a rule outcome upgrades, sets, or manufactures a final
state — including VERIFIED.

Fail-closed rules:
    - hook exception            → ERROR decision (blocks VERIFIED)
    - hook returns non-decision → ERROR decision (blocks VERIFIED)
    - dependency unavailable    → UNAVAILABLE decision (blocks VERIFIED)
    - YAML pack malformed       → RulePackError at load time (never silently)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Tuple

from backend.rules.contract import (
    OUTCOME_ERROR,
    RuleContext,
    RuleDecision,
    RuleHook,
    RulePackError,
    RuleResult,
    decision_to_result,
)
from backend.rules.loader import _DeclarativeRule

logger = logging.getLogger(__name__)

# The only states a rule decision may downgrade a deterministic VERIFIED into.
# VERIFIED is deliberately absent from every downgrade target: rules never own it.
_DOWNGRADE_LABELS = {
    "REVIEW_REQUIRED": "Review Required",
    "BLOCKED": "Blocked",
}


def _sanitize_hint(decision: RuleDecision) -> RuleDecision:
    """
    Enforce the downgrade-only hint domain on any decision.

    A decision whose metadata carries a decision_hint outside the allowed
    set (e.g. a hook attempting to smuggle "VERIFIED") is rebuilt with the
    fail-safe REVIEW_REQUIRED hint, so neither the applied state change nor
    the recorded evidence can carry the smuggled value.
    """
    hint = str(decision.metadata.get("decision_hint", ""))
    if hint in _DOWNGRADE_LABELS:
        return decision
    metadata = dict(decision.metadata)
    metadata["decision_hint"] = "REVIEW_REQUIRED"
    if hint:
        metadata["decision_hint_rejected"] = hint
    return RuleDecision(
        rule_id=decision.rule_id,
        outcome=decision.outcome,
        message=decision.message,
        metadata=metadata,
    )


def _run_hook(hook: RuleHook, context: RuleContext) -> RuleDecision:
    """
    Invoke one custom hook inside the fail-closed guard.

    Every failure mode — exception, wrong return type, malformed decision —
    becomes an ERROR decision, which blocks a final VERIFIED. A hook can
    never turn its own failure into success.
    """
    try:
        decision = hook.validate(context)
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY hook failure
        logger.warning("rule hook %s raised: %s", getattr(hook, "rule_id", "<unknown>"), exc)
        return RuleDecision(
            rule_id=getattr(hook, "rule_id", "<unknown>"),
            outcome=OUTCOME_ERROR,
            message=f"rule hook raised {type(exc).__name__}: {exc}",
        )
    if not isinstance(decision, RuleDecision):
        return RuleDecision(
            rule_id=getattr(hook, "rule_id", "<unknown>"),
            outcome=OUTCOME_ERROR,
            message=f"rule hook returned {type(decision).__name__}, expected RuleDecision",
        )
    return decision


class RuleEngine:
    """
    Deterministic rule evaluation boundary.

    Rules run AFTER grounding and AFTER deterministic accounting have
    produced a success state. `evaluate()` receives the accounting-produced
    state and may only return the same state or a downgrade of it.
    """

    def __init__(
        self,
        rules: Sequence[_DeclarativeRule] = (),
        hooks: Sequence[RuleHook] = (),
    ) -> None:
        self._rules: Tuple[_DeclarativeRule, ...] = tuple(rules)
        self._hooks: Tuple[RuleHook, ...] = tuple(hooks)
        for hook in self._hooks:
            if not isinstance(hook, RuleHook):
                raise RulePackError(f"hook {hook!r} does not implement the RuleHook protocol")
            if not isinstance(getattr(hook, "rule_id", None), str) or not hook.rule_id.strip():
                raise RulePackError(f"hook {type(hook).__name__} must define a non-empty rule_id")

    def has_rules(self) -> bool:
        """True when any YAML rule or hook is configured."""
        return bool(self._rules or self._hooks)

    def describe(self) -> Dict[str, Any]:
        """Non-secret summary of the configured rule pack (for evidence)."""
        return {
            "yaml_rules": [r.rule_id for r in self._rules],
            "python_hooks": [h.rule_id for h in self._hooks],
        }

    def evaluate(
        self, context: RuleContext, *, status: str = "VERIFIED", status_label: str = "Verified"
    ) -> Tuple[str, str, List[RuleResult]]:
        """
        Evaluate all rules against `context` and return
        (status, status_label, evidence).

        The incoming state is the accounting-produced success state (default
        VERIFIED). Each blocking decision downgrades it per its decision
        hint; PASS decisions leave it untouched. Downgrades never compose
        upwards: the first REVIEW_REQUIRED hint downgrades VERIFIED, and a
        BLOCKED hint downgrades whichever downgraded state is current.
        """
        evidence: List[RuleResult] = []

        for rule in self._rules:
            try:
                decision = rule.evaluate(context)
            except Exception as exc:  # noqa: BLE001 — fail closed
                decision = RuleDecision(
                    rule_id=getattr(rule, "rule_id", "<unknown>"),
                    outcome=OUTCOME_ERROR,
                    message=f"rule evaluation error: {type(exc).__name__}: {exc}",
                )
            decision = _sanitize_hint(decision)
            evidence.append(decision_to_result(decision, source="yaml"))
            status, status_label = _apply_decision(status, status_label, decision)

        for hook in self._hooks:
            decision = _sanitize_hint(_run_hook(hook, context))
            evidence.append(decision_to_result(decision, source="python_hook"))
            status, status_label = _apply_decision(status, status_label, decision)

        return status, status_label, evidence


def _apply_decision(
    status: str, status_label: str, decision: RuleDecision
) -> Tuple[str, str]:
    """
    Translate one decision into a state change — downgrade-only.

    - PASS: no change.
    - FAIL/UNAVAILABLE/ERROR (blocking): downgrade the current state.
      VERIFIED → decision hint (REVIEW_REQUIRED/BLOCKED);
      an already-downgraded state only ever moves further down
      (REVIEW_REQUIRED → BLOCKED when a BLOCKED hint arrives).
    - A decision can never upgrade REVIEW_REQUIRED/BLOCKED back to VERIFIED.
    """
    if not decision.is_blocking:
        return status, status_label

    hint = str(decision.metadata.get("decision_hint", "")) or "REVIEW_REQUIRED"
    if hint not in _DOWNGRADE_LABELS:
        # Defensive: _sanitize_hint already guarantees a valid hint, but if a
        # raw decision ever reaches this point with a bad hint, fail closed.
        hint = "REVIEW_REQUIRED"

    if status == "VERIFIED":
        return hint, _DOWNGRADE_LABELS[hint]
    if status == "REVIEW_REQUIRED" and hint == "BLOCKED":
        return "BLOCKED", _DOWNGRADE_LABELS["BLOCKED"]
    return status, status_label


__all__ = ["RuleEngine"]
