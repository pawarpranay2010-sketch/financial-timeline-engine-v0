"""
Platrixa — Example custom rule hooks (Phase 10D/I)
===================================================

Demonstrates the programmatic extension protocol: complex enterprise logic
plugged into the runtime WITHOUT modifying Platrixa core code.

    from examples.rules.custom_hooks import HolidayBudgetRule

    kernel = Kernel(rule_hooks=[HolidayBudgetRule()])

HolidayBudgetRule illustrates the full pattern:
  1. inspect the transaction (date is taken deterministically from request_id
     so the example is reproducible without wall-clock flakiness),
  2. consult a deterministic mock budget provider (no real database needed),
  3. return PASS / FAIL / UNAVAILABLE through the RuleDecision boundary.

A hook NEVER returns a final status: there is no way to express VERIFIED
through RuleDecision — the runtime owns final states (Phase 10F).
"""

from __future__ import annotations

from typing import Any, Dict

from backend.rules.contract import (
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNAVAILABLE,
    RuleContext,
    RuleDecision,
    RuleHook,
)


class _MockBudgetProvider:
    """
    Deterministic fake enterprise dependency (Phase 10D: no real DB).

    Rules:
      - December transactions: budget exhausted (all spending blocked)
      - otherwise: budget available up to a fixed limit
    """

    MONTHLY_LIMIT = 25_000

    def remaining_budget(self, month: int) -> int:
        if month == 12:
            return 0  # holiday freeze
        return self.MONTHLY_LIMIT

    def is_available(self) -> bool:
        """Simulates dependency health (True for the mock)."""
        return True


class HolidayBudgetRule(RuleHook):
    """Blocks spending in the holiday month unless within remaining budget."""

    rule_id = "holiday_budget"

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider or _MockBudgetProvider()

    def validate(self, context: RuleContext) -> RuleDecision:
        # 1. Deterministic "date": derived from the request id so tests are
        #    reproducible (request_id embeds a stable hash of the input).
        month = int(str(context.request_id).split(":")[-1]) % 12 + 1

        # 2. Dependency health: a real integration would map connectivity
        #    failure to UNAVAILABLE — never to PASS (fail-closed).
        if not self._provider.is_available():
            return RuleDecision(
                rule_id=self.rule_id,
                outcome=OUTCOME_UNAVAILABLE,
                message="budget provider unavailable; cannot confirm budget",
            )

        # 3. Policy decision from the mock provider.
        remaining = self._provider.remaining_budget(month)
        amount = _first_amount(context.interpretation)
        if month == 12 and amount > 0:
            return RuleDecision(
                rule_id=self.rule_id,
                outcome=OUTCOME_FAIL,
                message=f"holiday budget freeze: spending blocked (requested {amount})",
                metadata={"month": month, "remaining_budget": remaining},
            )
        if amount > remaining:
            return RuleDecision(
                rule_id=self.rule_id,
                outcome=OUTCOME_FAIL,
                message=f"amount {amount} exceeds remaining budget {remaining}",
                metadata={"month": month, "remaining_budget": remaining},
            )
        return RuleDecision(
            rule_id=self.rule_id,
            outcome=OUTCOME_PASS,
            metadata={"month": month, "remaining_budget": remaining},
        )


def _first_amount(interpretation: Dict[str, Any]) -> float:
    amounts = interpretation.get("amounts") or []
    for item in amounts:
        try:
            return float(item.get("value", 0))
        except (TypeError, ValueError):
            continue
    return 0.0


__all__ = ["HolidayBudgetRule"]
