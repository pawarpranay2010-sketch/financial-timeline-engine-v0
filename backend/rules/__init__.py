"""
Platrixa — Rule Pack boundary (Phase 10)
=========================================

Public surface for the developer rule extension boundary:

    from backend.rules import (
        RuleContext,
        RuleDecision,
        RuleEngine,
        RuleHook,
        RuleResult,
        load_yaml_rule_pack,
    )

The rule system may only CONSTRAIN Platrixa outcomes (downgrade-only state
policy). Final states — including VERIFIED — remain owned by the Platrixa
runtime and its deterministic accounting kernel.
"""

from backend.rules.contract import (
    BLOCKING_OUTCOMES,
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNAVAILABLE,
    VALID_OUTCOMES,
    RuleContractError,
    RuleContext,
    RuleDecision,
    RuleHook,
    RulePackError,
    RuleResult,
    decision_to_result,
)
from backend.rules.engine import RuleEngine
from backend.rules.loader import ALLOWED_DECISIONS, VALID_RULE_TYPES, load_yaml_rule_pack

__all__ = [
    "BLOCKING_OUTCOMES",
    "OUTCOME_ERROR",
    "OUTCOME_FAIL",
    "OUTCOME_PASS",
    "OUTCOME_UNAVAILABLE",
    "VALID_OUTCOMES",
    "ALLOWED_DECISIONS",
    "VALID_RULE_TYPES",
    "RuleContractError",
    "RuleContext",
    "RuleDecision",
    "RuleEngine",
    "RuleHook",
    "RulePackError",
    "RuleResult",
    "decision_to_result",
    "load_yaml_rule_pack",
]
