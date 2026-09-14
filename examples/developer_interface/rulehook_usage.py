"""
Example: RuleHook (programmatic Python rule) through the public interface.

Run from the repository root:

    python3 examples/developer_interface/rulehook_usage.py

Reuses the Phase 10 example hook (HolidayBudgetRule) from
examples/rules/custom_hooks.py. Hooks receive a controlled read-only
RuleContext and may only downgrade states — a hook requesting VERIFIED is
sanitized by the runtime (never honored).
"""

from platrixa import Platrixa, PlatrixaConfig

from examples.rules.custom_hooks import HolidayBudgetRule

TRANSACTION = "Purchased furniture for cash ₹15,000"


def main() -> None:
    config = PlatrixaConfig(
        rule_hooks=[HolidayBudgetRule()],
    )
    client = Platrixa(config=config)

    result = client.process(TRANSACTION)
    print(f"state: {result.status} ({result.status_label})")
    for record in result.rule_evidence:
        print(f"  hook {record.get('rule_id')}: {record.get('result')} — {record.get('message')}")


if __name__ == "__main__":
    main()
