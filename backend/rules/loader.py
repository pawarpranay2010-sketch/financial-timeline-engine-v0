"""
Platrixa — Declarative YAML rule pack loader (Phase 10C)
=========================================================

Smallest useful declarative format. Three primitives only:

    - required:       a field must be present and non-empty
    - allowed_values: a field's value must be one of an allowed set
    - threshold:      a numeric field must satisfy a comparison

A rule may optionally carry `decision: REVIEW_REQUIRED | BLOCKED`, the state
the runtime downgrades a deterministic VERIFIED to when the rule FAILS. There
is deliberately NO way to express VERIFIED (or any upgrade) in YAML: unknown
decision values, unknown rule types, and malformed structures fail closed at
LOAD time, before any transaction is processed.

No arbitrary code execution: YAML is parsed with yaml.safe_load and every
accepted construct maps to a deterministic built-in evaluator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from backend.rules.contract import (
    RuleContext,
    RuleDecision,
    RulePackError,
    OUTCOME_FAIL,
    OUTCOME_PASS,
)

# Decision hints a YAML rule may request on failure. Deliberately excludes
# VERIFIED and every other terminal state: the runtime owns final states.
ALLOWED_DECISIONS = ("REVIEW_REQUIRED", "BLOCKED")

VALID_RULE_TYPES = ("required", "allowed_values", "threshold")

_THRESHOLD_OPERATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


@dataclass(frozen=True)
class _DeclarativeRule:
    """One validated YAML rule (internal)."""

    rule_id: str
    type: str
    field: str
    decision: str
    values: tuple = ()
    operator: str = ""
    threshold_value: Any = None
    message: str = ""

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """Deterministically evaluate against the controlled context."""
        interpretation = context.interpretation
        field_value = _lookup_field(interpretation, self.field)

        if self.type == "required":
            ok = field_value is not None and (
                not isinstance(field_value, (str, list, dict, tuple)) or len(field_value) > 0
            )
            outcome = OUTCOME_PASS if ok else OUTCOME_FAIL
            message = "" if ok else (self.message or f"required field missing/empty: {self.field}")

        elif self.type == "allowed_values":
            if field_value is None:
                outcome, message = OUTCOME_PASS, ""  # absent fields are not this rule's concern
            else:
                ok = field_value in self.values
                outcome = OUTCOME_PASS if ok else OUTCOME_FAIL
                message = (
                    "" if ok
                    else (self.message or f"{self.field}={field_value!r} not in allowed values {list(self.values)}")
                )

        elif self.type == "threshold":
            if field_value is None:
                outcome, message = OUTCOME_PASS, ""  # absent fields are not this rule's concern
            else:
                number = _as_number(field_value)
                if number is None:
                    outcome, message = OUTCOME_FAIL, (
                        self.message or f"{self.field} is not numeric: {field_value!r}"
                    )
                else:
                    op = _THRESHOLD_OPERATORS[self.operator]
                    ok = op(number, self.threshold_value)
                    outcome = OUTCOME_PASS if ok else OUTCOME_FAIL
                    message = (
                        "" if ok
                        else (self.message or f"{self.field}={number} violates {self.field} {self.operator} {self.threshold_value}")
                    )
        else:  # pragma: no cover — load-time validation prevents this
            outcome, message = OUTCOME_FAIL, f"unknown rule type {self.type!r}"

        return RuleDecision(
            rule_id=self.rule_id,
            outcome=outcome,
            message=message,
            metadata={"rule_type": self.type, "field": self.field, "decision_hint": self.decision},
        )


def _lookup_field(interpretation: Mapping[str, Any], field_path: str) -> Any:
    """Dotted-path lookup into the interpretation mapping.

    Numeric path segments index into lists (e.g. ``amounts.0.value``);
    non-integer segments on lists resolve to None (fail-closed: an absent
    field is treated as not-this-rule's-concern by required/allowed_values/
    threshold semantics).
    """
    current: Any = interpretation
    for part in str(field_path).split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                index = int(part)
            except ValueError:
                return None
            if 0 <= index < len(current):
                current = current[index]
            else:
                return None
        else:
            return None
    return current


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def load_yaml_rule_pack(path: str | Path) -> List[_DeclarativeRule]:
    """
    Load and validate a YAML rule pack. Fails closed.

    Raises RulePackError on: unreadable file, non-mapping root, missing/empty
    rules list, non-mapping rule, missing/blank id/type/field, unknown rule
    type, unknown decision hint, unknown threshold operator, non-numeric
    threshold value, malformed allowed_values.
    """
    import yaml  # deferred: engine/contract remain importable without pyyaml

    p = Path(path)
    if not p.is_file():
        raise RulePackError(f"rule pack file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulePackError(f"rule pack is not valid YAML: {exc}") from exc
    return rule_pack_from_mapping(raw, source=str(p))


def rule_pack_from_mapping(raw: Any, source: str = "<mapping>") -> List[_DeclarativeRule]:
    """Validate an already-parsed mapping into rules (shared by tests)."""
    if not isinstance(raw, Mapping):
        raise RulePackError(f"{source}: rule pack root must be a mapping with a 'rules' list")
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RulePackError(f"{source}: 'rules' must be a non-empty list")

    rules: List[_DeclarativeRule] = []
    seen_ids = set()
    for index, item in enumerate(rules_raw):
        where = f"{source} rule[{index}]"
        if not isinstance(item, Mapping):
            raise RulePackError(f"{where}: each rule must be a mapping")

        rule_id = item.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise RulePackError(f"{where}: 'id' must be a non-empty string")
        if rule_id in seen_ids:
            raise RulePackError(f"{where}: duplicate rule id {rule_id!r}")
        seen_ids.add(rule_id)

        rtype = item.get("type")
        if rtype not in VALID_RULE_TYPES:
            raise RulePackError(
                f"{where} ({rule_id!r}): unknown rule type {rtype!r}; expected one of {VALID_RULE_TYPES}"
            )

        field_name = item.get("field")
        if not isinstance(field_name, str) or not field_name.strip():
            raise RulePackError(f"{where} ({rule_id!r}): 'field' must be a non-empty string")

        decision = item.get("decision", "REVIEW_REQUIRED")
        if decision not in ALLOWED_DECISIONS:
            raise RulePackError(
                f"{where} ({rule_id!r}): decision {decision!r} not allowed; "
                f"rules may only downgrade to {ALLOWED_DECISIONS} (final VERIFIED belongs to the runtime)"
            )

        values: tuple = ()
        operator = ""
        threshold_value: Any = None
        if rtype == "allowed_values":
            vals = item.get("values")
            if not isinstance(vals, list) or not vals:
                raise RulePackError(f"{where} ({rule_id!r}): allowed_values requires a non-empty 'values' list")
            values = tuple(vals)
        elif rtype == "threshold":
            operator = str(item.get("operator", ""))
            if operator not in _THRESHOLD_OPERATORS:
                raise RulePackError(
                    f"{where} ({rule_id!r}): threshold operator must be one of {sorted(_THRESHOLD_OPERATORS)}"
                )
            threshold_value = item.get("value")
            if _as_number(threshold_value) is None:
                raise RulePackError(f"{where} ({rule_id!r}): threshold 'value' must be numeric")

        message = item.get("message", "")
        if not isinstance(message, str):
            raise RulePackError(f"{where} ({rule_id!r}): 'message' must be a string")

        rules.append(
            _DeclarativeRule(
                rule_id=rule_id,
                type=rtype,
                field=field_name,
                decision=decision,
                values=values,
                operator=operator,
                threshold_value=threshold_value,
                message=message,
            )
        )
    return rules


__all__ = [
    "ALLOWED_DECISIONS",
    "VALID_RULE_TYPES",
    "load_yaml_rule_pack",
    "rule_pack_from_mapping",
]
