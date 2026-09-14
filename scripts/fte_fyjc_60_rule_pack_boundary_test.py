#!/usr/bin/env python3
"""
PLATRIXA — PHASE 10: RULE PACK BOUNDARY TEST (fte_fyjc_60)
===========================================================

Proves the Phase 10 extensibility boundary:

  - YAML rule pack loads / validates / fails closed
  - required / allowed_values / threshold primitives pass and fail correctly
  - custom Python hooks (pass / fail / unavailable / exception / wrong type)
  - STATE AUTHORITY: no rule can manufacture or upgrade VERIFIED
  - multiple rules aggregate deterministically
  - structured rule evidence is produced
  - Kernel integration: default-off byte-identity; rules only downgrade
  - no accounting leakage, no grounding bypass

Run:  python3 scripts/fte_fyjc_60_rule_pack_boundary_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.rules.contract import (  # noqa: E402
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNAVAILABLE,
    RuleContractError,
    RuleContext,
    RuleDecision,
    RuleHook,
    RulePackError,
    RuleResult,
)
from backend.rules.loader import load_yaml_rule_pack, rule_pack_from_mapping  # noqa: E402
from backend.rules.engine import RuleEngine, _run_hook  # noqa: E402

from backend.model_provider.base import (  # noqa: E402
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
)
from backend.kernel.kernel import GROUNDING_FAILED, VERIFIED, Kernel  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} — {detail}")
    return ok


# ---------------------------------------------------------------------------
# Fixtures — candidate mirrors the Phase 7R/9 proven VERIFIED path
# ---------------------------------------------------------------------------

VALID_CANDIDATE: Dict[str, Any] = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

VALID_INPUT = "purchased furniture from raj for rs.25000"

FORBIDDEN_KEYS = {
    "journal", "journal_entry", "debit_lines", "credit_lines",
    "ledger", "balances", "debit_account", "credit_account",
}


class _StubProvider:
    """ModelProvider stub returning the fixed validated candidate."""

    def __init__(self, candidate: Optional[Dict[str, Any]] = None) -> None:
        self._candidate = dict(candidate or VALID_CANDIDATE)
        self._config = ProviderConfig()

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            available=True,
            model_id="stub-model",
            base_model_revision="stub-base",
            adapter_repo_id="stub-adapter",
            adapter_revision="stub-rev",
            reason="stub provider",
            loadable=True,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        return InterpretationResult(
            raw_input=raw_input,
            candidate=dict(self._candidate),
            model_id="stub-model",
            provider_revision="stub-rev",
            generated_profile={},
        )


def _kernel(
    candidate: Optional[Dict[str, Any]] = None,
    rule_pack: Optional[str] = None,
    hooks: Any = None,
) -> Kernel:
    return Kernel(
        model_provider=_StubProvider(candidate),
        rule_pack=rule_pack,
        rule_hooks=hooks,
    )


def _write_yaml(mapping: Any) -> str:
    import yaml

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(mapping, fh)
        return fh.name


def _write_yaml_text(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(text)
        return fh.name


def _ctx(interp: Optional[Dict[str, Any]] = None) -> RuleContext:
    return RuleContext(
        request_id="kernel:7",
        raw_input=VALID_INPUT,
        interpretation=interp if interp is not None else dict(VALID_CANDIDATE),
    )


# Hook fixtures -------------------------------------------------------------

class _PassHook(RuleHook):
    rule_id = "pass_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_PASS)


class _FailHook(RuleHook):
    rule_id = "fail_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_FAIL, message="policy says no")


class _UnavailHook(RuleHook):
    rule_id = "unavail_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_UNAVAILABLE, message="provider down")


class _BoomHook(RuleHook):
    rule_id = "boom_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        raise RuntimeError("dependency exploded")


class _WrongTypeHook(RuleHook):
    rule_id = "wrong_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return {"status": "VERIFIED"}  # type: ignore[return-value] — smuggle attempt


class _VerifiedRequestHook(RuleHook):
    """Fails AND attempts to steer the downgrade toward VERIFIED."""

    rule_id = "verified_request_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(
            rule_id=self.rule_id,
            outcome=OUTCOME_FAIL,
            message="policy violated; requesting VERIFIED anyway",
            metadata={"decision_hint": "VERIFIED"},
        )


# ---------------------------------------------------------------------------
# A. YAML rule pack loading / validation
# ---------------------------------------------------------------------------

GOOD_PACK = {
    "rules": [
        {"id": "invoice_reference_required", "type": "required", "field": "references",
         "decision": "REVIEW_REQUIRED"},
        {"id": "payment_method_allowed", "type": "allowed_values", "field": "payment_method_enum",
         "values": ["CASH", "CREDIT", "BANK", "UNKNOWN"], "decision": "REVIEW_REQUIRED"},
        {"id": "high_value_review", "type": "threshold", "field": "amounts.0.value",
         "operator": ">", "value": 100000, "decision": "REVIEW_REQUIRED"},
    ]
}


def test_yaml_loading() -> None:
    print("\n--- A. YAML rule pack loading and validation ---")

    path = _write_yaml(GOOD_PACK)
    rules = load_yaml_rule_pack(path)
    check("A1 valid YAML pack loads", len(rules) == 3, f"{len(rules)} rules")

    bad_yaml = _write_yaml_text("rules: [unclosed")
    try:
        load_yaml_rule_pack(bad_yaml)
        check("A2 malformed YAML rejected", False, "no exception")
    except RulePackError as exc:
        check("A2 malformed YAML rejected", "YAML" in str(exc) or "yaml" in str(exc), str(exc)[:60])

    try:
        rule_pack_from_mapping({"rules": [{"id": "x", "type": "crypto_lock", "field": "a"}]})
        check("A3 unknown rule type rejected", False, "no exception")
    except RulePackError as exc:
        check("A3 unknown rule type rejected", "unknown rule type" in str(exc), str(exc)[:60])

    try:
        rule_pack_from_mapping({"rules": [{"id": "x", "type": "required", "field": "parties",
                                           "decision": "VERIFIED"}]})
        check("A4 VERIFIED decision rejected at load time", False, "no exception")
    except RulePackError as exc:
        check("A4 VERIFIED decision rejected at load time",
              "VERIFIED" in str(exc), str(exc)[:80])

    try:
        rule_pack_from_mapping({"rules": [{"type": "required", "field": "parties"}]})
        check("A5 malformed rule (missing id) rejected", False, "no exception")
    except RulePackError:
        check("A5 malformed rule (missing id) rejected", True)


# ---------------------------------------------------------------------------
# B. Primitive evaluation (deterministic)
# ---------------------------------------------------------------------------

def test_primitives() -> None:
    print("\n--- B. Declarative primitives ---")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "req_ok", "type": "required", "field": "parties"}]}))
    status, label, ev = engine.evaluate(_ctx())
    check("B1 required passes (parties present)", status == "VERIFIED" and ev[0].outcome == OUTCOME_PASS,
          f"status={status}")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "req_fail", "type": "required", "field": "references"}]}))
    status, label, ev = engine.evaluate(_ctx())
    check("B2 required fails (references empty) → downgrade",
          status == "REVIEW_REQUIRED" and ev[0].outcome == OUTCOME_FAIL, f"status={status}")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "thr_ok", "type": "threshold", "field": "amounts.0.value",
         "operator": "<", "value": 100000}]}))
    status, _, ev = engine.evaluate(_ctx())
    check("B3 threshold passes (25000 < 100000)", status == "VERIFIED" and ev[0].outcome == OUTCOME_PASS)

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "thr_fail", "type": "threshold", "field": "amounts.0.value",
         "operator": "<", "value": 10000}]}))
    status, _, ev = engine.evaluate(_ctx())
    check("B4 threshold fails (25000 < 10000 false) → downgrade",
          status == "REVIEW_REQUIRED" and ev[0].outcome == OUTCOME_FAIL, f"status={status}")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "av_fail", "type": "allowed_values", "field": "payment_method_enum",
         "values": ["CASH", "CREDIT"]}]}))
    status, _, ev = engine.evaluate(_ctx())
    check("B5 allowed_values fails (UNKNOWN not allowed) → downgrade",
          status == "REVIEW_REQUIRED" and ev[0].outcome == OUTCOME_FAIL, f"status={status}")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "av_ok", "type": "allowed_values", "field": "payment_method_enum",
         "values": ["CASH", "CREDIT", "UNKNOWN"]}]}))
    status, _, ev = engine.evaluate(_ctx())
    check("B6 allowed_values passes (UNKNOWN allowed)", status == "VERIFIED")


# ---------------------------------------------------------------------------
# C. Custom hooks
# ---------------------------------------------------------------------------

def test_hooks() -> None:
    print("\n--- C. Custom Python hooks ---")

    decision = _run_hook(_PassHook(), _ctx())
    check("C1 pass hook returns PASS", decision.outcome == OUTCOME_PASS)

    decision = _run_hook(_FailHook(), _ctx())
    check("C2 fail hook returns FAIL", decision.outcome == OUTCOME_FAIL)

    decision = _run_hook(_UnavailHook(), _ctx())
    check("C3 unavailable hook returns UNAVAILABLE (never success)",
          decision.outcome == OUTCOME_UNAVAILABLE and decision.is_blocking)

    decision = _run_hook(_BoomHook(), _ctx())
    check("C4 hook exception → ERROR (fail closed, never success)",
          decision.outcome == OUTCOME_ERROR and decision.is_blocking,
          decision.message[:60])

    decision = _run_hook(_WrongTypeHook(), _ctx())
    check("C5 hook returning non-decision → ERROR",
          decision.outcome == OUTCOME_ERROR and decision.is_blocking,
          decision.message[:60])

    try:
        RuleDecision(rule_id="x", outcome="VERIFIED")  # type: ignore[arg-type]
        check("C6 contract rejects VERIFIED as an outcome", False, "no exception")
    except RuleContractError:
        check("C6 contract rejects VERIFIED as an outcome", True)


# ---------------------------------------------------------------------------
# D. State authority — the critical security invariant
# ---------------------------------------------------------------------------

def test_state_authority() -> None:
    print("\n--- D. State authority: rules can only downgrade ---")

    engine = RuleEngine(rules=(), hooks=())
    status, label, ev = engine.evaluate(_ctx())
    check("D1 empty pack leaves VERIFIED untouched", status == "VERIFIED" and ev == [])

    engine = RuleEngine(rules=(), hooks=(_PassHook(),))
    status, _, _ = engine.evaluate(_ctx())
    check("D2 passing custom hook keeps VERIFIED", status == "VERIFIED")

    engine = RuleEngine(rules=(), hooks=(_FailHook(),))
    status, label, _ = engine.evaluate(_ctx())
    check("D3 failing custom hook downgrades VERIFIED → REVIEW_REQUIRED",
          status == "REVIEW_REQUIRED" and label == "Review Required", f"status={status}")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "hard_block", "type": "required", "field": "references", "decision": "BLOCKED"}]}))
    status, label, _ = engine.evaluate(_ctx())
    check("D4 BLOCKED hint downgrades to BLOCKED",
          status == "BLOCKED" and label == "Blocked", f"status={status}")

    engine = RuleEngine(rules=(), hooks=(_VerifiedRequestHook(),))
    status, _, ev = engine.evaluate(_ctx())
    check("D5 rule requesting VERIFIED hint is sanitized → REVIEW_REQUIRED",
          status == "REVIEW_REQUIRED", f"status={status}")
    check("D6 sanitized evidence never carries VERIFIED",
          all("VERIFIED" not in str(r.metadata.get("decision_hint", "")) for r in ev),
          str(ev[-1].metadata.get("decision_hint")))

    # Downgrades never compose upwards
    engine = RuleEngine(rules=(), hooks=(_PassHook(),))
    status, _, _ = engine.evaluate(_ctx(), status="REVIEW_REQUIRED", status_label="Review Required")
    check("D7 PASS rule cannot upgrade REVIEW_REQUIRED back to VERIFIED", status == "REVIEW_REQUIRED")

    # Deterministic multi-rule aggregation: worst case wins
    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "r1", "type": "required", "field": "references", "decision": "REVIEW_REQUIRED"},
        {"id": "r2", "type": "required", "field": "nonexistent_field", "decision": "BLOCKED"},
    ]}))
    status1, _, ev1 = engine.evaluate(_ctx())
    status2, _, ev2 = engine.evaluate(_ctx())
    check("D8 multi-rule aggregation deterministic (run twice identical)",
          status1 == status2 and [e.to_dict() for e in ev1] == [e.to_dict() for e in ev2],
          f"status={status1}")
    check("D9 worst case wins (REVIEW_REQUIRED then BLOCKED → BLOCKED)", status1 == "BLOCKED")

    # Structural proof: no downgrade target is VERIFIED; decision cannot express it
    from backend.rules import engine as engine_mod
    check("D10 engine downgrade map excludes VERIFIED",
          "VERIFIED" not in engine_mod._DOWNGRADE_LABELS,
          str(sorted(engine_mod._DOWNGRADE_LABELS)))
    import dataclasses
    fields = {f.name for f in dataclasses.fields(RuleDecision)}
    check("D11 RuleDecision has no status/verdict field", not (fields & {"status", "verdict", "final_state"}),
          str(sorted(fields)))


# ---------------------------------------------------------------------------
# E. Evidence / auditability
# ---------------------------------------------------------------------------

def test_evidence() -> None:
    print("\n--- E. Rule evidence ---")

    engine = RuleEngine(rules=rule_pack_from_mapping({"rules": [
        {"id": "ev_rule", "type": "required", "field": "references"}]}), hooks=(_FailHook(),))
    _, _, ev = engine.evaluate(_ctx())
    check("E1 one evidence record per executed rule", len(ev) == 2)
    first = ev[0].to_dict()
    check("E2 evidence record shape",
          set(first.keys()) == {"rule_id", "source", "result", "message", "metadata"},
          str(sorted(first.keys())))
    check("E3 sources distinguish yaml vs python_hook",
          ev[0].source == "yaml" and ev[1].source == "python_hook")
    check("E4 'why rejected' answerable from evidence",
          any(r.outcome in ("FAIL", "UNAVAILABLE", "ERROR") and r.rule_id for r in ev),
          str([r.rule_id for r in ev]))


# ---------------------------------------------------------------------------
# F. Kernel integration
# ---------------------------------------------------------------------------

def test_kernel_integration() -> None:
    print("\n--- F. Kernel integration ---")

    # F1-F3: default OFF → byte-identical behavior
    r0 = _kernel().process(VALID_INPUT)
    check("F1 default-off kernel reaches VERIFIED", r0.status == VERIFIED, r0.status)
    check("F2 default-off rule_evidence empty", r0.rule_evidence == [])
    check("F3 default-off metadata unchanged",
          set(r0.metadata.keys()) == {"model_id", "provider_revision"}, str(sorted(r0.metadata.keys())))

    # F4: failing YAML pack downgrades the kernel VERIFIED
    pack_path = _write_yaml({"rules": [
        {"id": "cap_rule", "type": "threshold", "field": "amounts.0.value",
         "operator": "<", "value": 10000, "decision": "REVIEW_REQUIRED"}]})
    r1 = _kernel(rule_pack=pack_path).process(VALID_INPUT)
    check("F4 failing YAML rule downgrades kernel VERIFIED → REVIEW_REQUIRED",
          r1.status == "REVIEW_REQUIRED", r1.status)
    check("F5 kernel rule_evidence populated",
          len(r1.rule_evidence) == 1 and r1.rule_evidence[0]["result"] == "FAIL",
          str(r1.rule_evidence)[:100])
    check("F6 accounting result still present and authoritative",
          isinstance(r1.accounting_result, dict) and bool(r1.accounting_result.get("debit_lines")))

    # F7: custom hook alone downgrades without touching core runtime
    r2 = _kernel(hooks=[_FailHook()]).process(VALID_INPUT)
    check("F7 custom hook downgrades kernel VERIFIED", r2.status == "REVIEW_REQUIRED", r2.status)

    # F8: hook exception inside kernel → fail closed (REVIEW_REQUIRED, not crash/VERIFIED)
    r3 = _kernel(hooks=[_BoomHook()]).process(VALID_INPUT)
    check("F8 hook exception fails closed in kernel", r3.status == "REVIEW_REQUIRED", r3.status)
    check("F9 exception evidence records ERROR outcome",
          r3.rule_evidence and r3.rule_evidence[0]["result"] == "ERROR")

    # F10: no accounting leakage through the rule channel
    candidate_keys = set(VALID_CANDIDATE.keys())
    check("F10 candidate carries no forbidden accounting fields",
          not (candidate_keys & FORBIDDEN_KEYS))
    check("F11 rule evidence carries no accounting truth",
          all(not (set(rec.get("metadata", {}).keys()) & FORBIDDEN_KEYS) for rec in r1.rule_evidence))

    # F12: grounding gate still enforced even with rules configured
    bad = dict(VALID_CANDIDATE)
    bad["parties"] = ["zhongli"]  # party not present in input text
    r4 = _kernel(candidate=bad, rule_pack=pack_path).process(VALID_INPUT)
    check("F12 grounding failure not masked by rules",
          r4.status == GROUNDING_FAILED, f"status={r4.status} issues={r4.issues[:1]}")


# ---------------------------------------------------------------------------
# G. Construction-time fail-closed
# ---------------------------------------------------------------------------

def test_construction_failclosed() -> None:
    print("\n--- G. Malformed pack fails at Kernel construction ---")

    bad_path = _write_yaml_text("rules: [unclosed")
    try:
        _kernel(rule_pack=bad_path)
        check("G1 malformed pack rejects Kernel construction", False, "no exception")
    except RulePackError:
        check("G1 malformed pack rejects Kernel construction", True)

    try:
        Kernel(model_provider=_StubProvider(), rule_hooks=[object()])  # type: ignore[list-item]
        check("G2 non-RuleHook object rejected at construction", False, "no exception")
    except RulePackError:
        check("G2 non-RuleHook object rejected at construction", True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("PLATRIXA — PHASE 10: RULE PACK BOUNDARY TEST")
    print("=" * 78)

    test_yaml_loading()
    test_primitives()
    test_hooks()
    test_state_authority()
    test_evidence()
    test_kernel_integration()
    test_construction_failclosed()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
