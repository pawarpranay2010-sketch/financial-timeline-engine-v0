"""
Example: RulePack (declarative YAML rules) through the public interface.

Run from the repository root:

    python3 examples/developer_interface/rulepack_usage.py

The rule pack is the Phase 10 example pack (examples/rules/platrixa_rules.yaml).
Rules can only DOWNGRADE a deterministic success state — they can never
manufacture VERIFIED. Rule evidence is visible on the result.
"""

from platrixa import Platrixa, PlatrixaConfig

TRANSACTION = "Purchased furniture for cash ₹15,000"


def main() -> None:
    config = PlatrixaConfig(
        rule_pack="examples/rules/platrixa_rules.yaml",
    )
    client = Platrixa(config=config)

    print("rule pack summary:", client.rule_pack_summary())

    result = client.process(TRANSACTION)
    print(f"state: {result.status} ({result.status_label})")
    print("rule evidence:")
    for record in result.rule_evidence:
        print(f"  - {record.get('rule_id')}: {record.get('result')} — {record.get('message')}")


if __name__ == "__main__":
    main()
