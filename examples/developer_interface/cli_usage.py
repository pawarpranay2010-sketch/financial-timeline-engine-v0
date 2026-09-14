"""
Example: CLI usage producing machine-readable JSON.

Run from the repository root:

    python3 -m platrixa process --text "Purchased furniture for cash ₹15,000" --pretty
    python3 -m platrixa process examples/developer_interface/transaction.json
    python3 -m platrixa process transaction.json --rules examples/rules/platrixa_rules.yaml

Exit codes (deterministic view of the Kernel state):
    0 VERIFIED · 1 REVIEW_REQUIRED · 2 BLOCKED · 3 failure/error

This file is the JSON input used by the file-based form:
    python3 -m platrixa process examples/developer_interface/transaction.json
"""
