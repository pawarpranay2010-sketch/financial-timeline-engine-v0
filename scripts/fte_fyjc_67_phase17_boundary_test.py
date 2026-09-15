#!/usr/bin/env python3
"""
PLATRIXA — PHASE 17: ARCHITECTURE BOUNDARY TEST (fte_fyjc_67)
=============================================================

Proves the Phase 17 authority boundaries with live execution, not prose:

  1.  model output is bound as CandidateSemanticIR
  2.  grounding creates GroundedSemanticIR
  3.  accounting accepts ONLY GroundedSemanticIR
  4.  ungrounded IR cannot reach accounting (fail-closed manufacture)
  5.  RuleEngine can downgrade
  6.  RuleEngine cannot upgrade to VERIFIED
  7.  Kernel owns final state (model VERIFIED suggestion neutralized)
  8.  evidence binds actual execution (hashes, identity, final state)
  9.  RulePack hash is deterministic
  10. version identifiers are correctly captured

Run:  python3 scripts/fte_fyjc_67_phase17_boundary_test.py
"""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PASS = 0
_FAIL = 0


def _check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
    return ok


GOOD_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "transaction_type_enum": "PURCHASE",
    "parties": ["Raj"],
    "amounts": [{"value": "20000", "currency": "INR", "source": "explicit"}],
    "payment_method": "CASH",
    "payment_method_enum": "CASH",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    "ambiguity_flags": ["NONE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.90",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

GOOD_INPUT = "Purchased goods from Raj for Rs.20000 in cash"

GROUNDING_INPUT = "Purchased goods from Raj for Rs.20000 in cash"


class StubProvider:
    """Configurable provider stub for Kernel-boundary tests."""

    def __init__(self, candidate: Dict[str, Any], suggested_status: Any = None,
                 model_id: str = "stub-model", revision: str = "rev-17"):
        self._candidate = copy.deepcopy(candidate)
        if suggested_status is not None:
            self._candidate["suggested_status"] = suggested_status
        self._model_id = model_id
        self._revision = revision

    def status(self):
        from backend.model_provider.base import ProviderStatus

        return ProviderStatus(
            available=True, model_id=self._model_id,
            base_model_revision="base-rev", adapter_repo_id="adapter-repo",
            adapter_revision="adapter-rev", loadable=True,
        )

    def interpret(self, raw_input: str):
        from backend.model_provider.base import InterpretationResult

        return InterpretationResult(
            raw_input=raw_input,
            candidate=copy.deepcopy(self._candidate),
            model_id=self._model_id,
            provider_revision=self._revision,
        )


def _kernel_with(candidate: Dict[str, Any], **kwargs: Any):
    from backend.kernel.kernel import Kernel

    suggested = kwargs.pop("suggested_status", None)
    return Kernel(model_provider=StubProvider(candidate, suggested_status=suggested, **kwargs))


def section_1_candidate_ir() -> None:
    print("\n== 1. Model output is Candidate IR ==")
    from backend.semantics import CandidateSemanticIR, SemanticIRError

    c = CandidateSemanticIR(raw_input=GOOD_INPUT, fields=GOOD_CANDIDATE,
                            model_id="m", model_revision="r")
    _check("candidate constructed from model output", c is not None)
    _check("candidate exposes no accounting payload method",
           not hasattr(c, "for_accounting"))

    bad = dict(GOOD_CANDIDATE)
    bad["debit_lines"] = [{"account": "Purchases", "amount": 20000}]
    try:
        CandidateSemanticIR(raw_input=GOOD_INPUT, fields=bad)
        _check("candidate rejects accounting-truth fields", False)
    except SemanticIRError:
        _check("candidate rejects accounting-truth fields", True)


def section_2_grounding_creates_grounded() -> None:
    print("\n== 2. Grounding creates Grounded IR ==")
    from backend.semantics import CandidateSemanticIR, GroundedSemanticIR, SemanticIRError
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

    c = CandidateSemanticIR(raw_input=GOOD_INPUT, fields=GOOD_CANDIDATE)
    result = ExpandedGroundingGate().ground(GOOD_CANDIDATE, GOOD_INPUT)
    _check("deterministic gate passes the grounded candidate",
           result.safe_for_kernel and result.grounded)
    g = GroundedSemanticIR.from_candidate(c, result)
    _check("grounded IR fields are the groundable semantic fields",
           set(g.fields.keys()) == {
               "transaction_type", "parties", "amounts",
               "payment_method", "references", "ambiguities"})
    _check("for_accounting exposes raw_input + semantic fields only",
           set(g.for_accounting().keys()) == {
               "raw_input", "transaction_type", "parties", "amounts",
               "payment_method", "references", "ambiguities"})

    try:
        GroundedSemanticIR(raw_input="x", fields={})
        _check("direct construction is blocked", False)
    except SemanticIRError:
        _check("direct construction is blocked", True)

    try:
        GroundedSemanticIR.from_candidate(c, {"safe_for_kernel": False})
        _check("failing grounding result cannot manufacture grounded IR", False)
    except SemanticIRError:
        _check("failing grounding result cannot manufacture grounded IR", True)


def section_3_accounting_only_grounded() -> None:
    print("\n== 3. Accounting accepts only Grounded IR ==")
    from backend.kernel.kernel import Kernel
    from backend.semantics import CandidateSemanticIR, SemanticIRError

    k = _kernel_with(GOOD_CANDIDATE)
    c = CandidateSemanticIR(raw_input=GOOD_INPUT, fields=GOOD_CANDIDATE)
    try:
        k.process_accounting(c)  # type: ignore[arg-type]
        _check("accounting(candidate_ir) rejected", False)
    except SemanticIRError:
        _check("accounting(candidate_ir) rejected", True)
    try:
        k.process_accounting({"parties": []})  # type: ignore[arg-type]
        _check("accounting(raw dict) rejected", False)
    except SemanticIRError:
        _check("accounting(raw dict) rejected", True)


def section_4_ungrounded_cannot_reach_accounting() -> None:
    print("\n== 4. Ungrounded IR cannot reach accounting ==")
    from backend.kernel.kernel import Kernel

    k = _kernel_with(GOOD_CANDIDATE)
    r = k.process("Purchased goods from Raj for Rs.20000 in cash", request_id="u1")
    _check("grounded path reaches accounting (VERIFIED)",
           r.status == "VERIFIED" and r.grounded_ir is not None)

    # Fabricated party → grounding fails → no grounded IR, no accounting
    fabricated = dict(GOOD_CANDIDATE)
    fabricated["parties"] = ["Fabricated Person XYZ"]
    k2 = _kernel_with(fabricated)
    r2 = k2.process("Purchased goods from Raj for Rs.20000 in cash", request_id="u2")
    _check("fabricated party → GROUNDING_FAILED", r2.status == "GROUNDING_FAILED",
           detail=r2.status)
    _check("grounding failure carries candidate IR, never grounded IR",
           r2.candidate_ir is not None and r2.grounded_ir is None)


def section_5_ruleengine_downgrades() -> None:
    print("\n== 5. RuleEngine can downgrade ==")
    import tempfile

    from backend.rules.engine import RuleEngine
    from backend.rules.contract import RuleContext
    from backend.rules.loader import rule_pack_from_mapping

    pack = {"rules": [{"id": "always_review", "type": "required",
                       "field": "suggested_status", "decision": "REVIEW_REQUIRED"}]}
    # field must exist and be non-empty for PASS — use a rule that FAILS:
    pack_fail = {"rules": [{"id": "force_review", "type": "required",
                            "field": "nonexistent_field", "decision": "REVIEW_REQUIRED"}]}
    engine = RuleEngine(rules=rule_pack_from_mapping(pack_fail))
    ctx = RuleContext(request_id="x", raw_input=GOOD_INPUT, interpretation=GOOD_CANDIDATE)
    status, label, evidence = engine.evaluate(ctx, status="VERIFIED", status_label="Verified")
    _check("failing rule downgrades VERIFIED → REVIEW_REQUIRED",
           status == "REVIEW_REQUIRED" and len(evidence) == 1)

    pack_pass = {"rules": [{"id": "exists", "type": "required",
                            "field": "parties", "decision": "REVIEW_REQUIRED"}]}
    engine2 = RuleEngine(rules=rule_pack_from_mapping(pack_pass))
    status2, _, _ = engine2.evaluate(ctx, status="REVIEW_REQUIRED", status_label="Review Required")
    _check("PASS rule leaves a downgraded state untouched", status2 == "REVIEW_REQUIRED")


def section_6_ruleengine_cannot_upgrade() -> None:
    print("\n== 6. RuleEngine cannot upgrade to VERIFIED ==")
    from backend.rules.engine import RuleEngine
    from backend.rules.contract import RuleContext, RuleDecision, RuleHook
    from backend.rules.loader import rule_pack_from_mapping

    ctx = RuleContext(request_id="x", raw_input=GOOD_INPUT, interpretation=GOOD_CANDIDATE)

    class UpgradeAttemptHook(RuleHook):
        rule_id = "smuggled_upgrade"

        def validate(self, context: RuleContext) -> RuleDecision:
            return RuleDecision(
                rule_id=self.rule_id, outcome="PASS",
                message="attempting upgrade via metadata",
                metadata={"decision_hint": "VERIFIED"},
            )

    engine = RuleEngine(hooks=[UpgradeAttemptHook()])
    status, _, evidence = engine.evaluate(ctx, status="REVIEW_REQUIRED",
                                          status_label="Review Required")
    _check("PASS-hook with VERIFIED hint does not upgrade REVIEW_REQUIRED",
           status == "REVIEW_REQUIRED")
    evidence_dicts = [e.to_dict() for e in evidence]
    _check("smuggled VERIFIED hint neutralized in evidence decision_hint",
           all("VERIFIED" not in str(e.get("metadata", {}).get("decision_hint", ""))
               for e in evidence_dicts))

    class FailUpgradeHook(RuleHook):
        rule_id = "fail_upgrade"

        def validate(self, context: RuleContext) -> RuleDecision:
            return RuleDecision(rule_id=self.rule_id, outcome="PASS",
                                metadata={"decision_hint": "VERIFIED"})

    engine2 = RuleEngine(hooks=[FailUpgradeHook()])
    status2, _, _ = engine2.evaluate(ctx, status="VERIFIED", status_label="Verified")
    _check("PASS outcome never upgrades any state", status2 == "VERIFIED")

    # Malformed pack: decision VERIFIED rejected at load time
    import tempfile, yaml  # noqa: E401
    try:
        rule_pack_from_mapping({"rules": [{"id": "bad", "type": "required",
                                           "field": "parties",
                                           "decision": "VERIFIED"}]})
        _check("VERIFIED decision rejected at pack load", False)
    except Exception:
        _check("VERIFIED decision rejected at pack load", True)


def section_7_kernel_owns_final_state() -> None:
    print("\n== 7. Kernel owns final state ==")
    from backend.kernel.kernel import Kernel

    # A. model suggests VERIFIED → Kernel still controls state.
    # The deterministic grounding gate treats a VERIFIED claim as a safety
    # violation and FAILS CLOSED (GROUNDING_FAILED) — the pipeline never
    # continues with the model's claimed state, so the model can never
    # establish VERIFIED through its own output.
    k = _kernel_with(GOOD_CANDIDATE, suggested_status="VERIFIED")
    r = k.process(GROUNDING_INPUT, request_id="a1")
    _check("model-suggested VERIFIED is fail-closed (never reaches VERIFIED)",
           r.status == "GROUNDING_FAILED", detail=f"status={r.status}")
    _check("VERIFIED claim recorded as a grounding issue",
           any("VERIFIED" in issue for issue in (r.issues or [])))

    # E. accounting cannot directly emit final VERIFIED authority — the
    # accounting dict carries a flow status; the Kernel assembles the result
    # and the rule engine + Kernel own the final state. Prove accounting
    # output alone cannot be a final state: KernelResult.success is defined
    # ONLY by Kernel.status.
    from backend.kernel.kernel import KernelResult

    kr = KernelResult(status="REVIEW_REQUIRED")
    _check("KernelResult.success derives from Kernel status only",
           kr.success is False and kr.accounting_result is None)

    # F. grounding failure cannot silently become VERIFIED
    fabricated = dict(GOOD_CANDIDATE)
    fabricated["parties"] = ["Ghost Counterparty"]
    fabricated["suggested_status"] = "VERIFIED"
    k2 = _kernel_with(fabricated)
    r2 = k2.process(GROUNDING_INPUT, request_id="f1")
    _check("grounding failure + VERIFIED suggestion ≠ VERIFIED",
           r2.status != "VERIFIED", detail=r2.status)


def section_8_evidence_binds_execution() -> None:
    print("\n== 8. Evidence binds actual execution ==")
    from backend.kernel.kernel import Kernel

    k = _kernel_with(GOOD_CANDIDATE, model_id="model-A", revision="rev-X")
    r = k.process(GROUNDING_INPUT, request_id="ev1")
    d = r.evidence.to_dict()
    _check("evidence present on success result", r.evidence is not None)
    _check("input_hash matches sha256(raw_input)",
           d["input_hash"] == hashlib.sha256(GROUNDING_INPUT.encode()).hexdigest())
    _check("candidate hash equals candidate IR digest",
           d["candidate_interpretation_hash"] == r.candidate_ir.content_digest)
    _check("grounded hash equals grounded IR digest",
           d["grounded_interpretation_hash"] == r.grounded_ir.content_digest)
    _check("model identity records the ACTUAL provider identity",
           d["model_identity"] == {"model_id": "model-A", "provider_revision": "rev-X"})
    _check("final_state records the ACTUAL state", d["final_state"] == r.status)
    _check("schema_version captured", d["schema_version"] == "sem-ir-1")
    _check("grounding_version captured", d["grounding_version"] == "expanded-gate-1")
    _check("evidence carries no secrets",
           "token" not in json_lower(d) and "hf_token" not in json_lower(d))

    # Determinism: identical execution → identical evidence hashes
    r2 = _kernel_with(GOOD_CANDIDATE, model_id="model-A", revision="rev-X").process(
        GROUNDING_INPUT, request_id="ev2")
    _check("equivalent executions produce identical hashes",
           d["grounded_interpretation_hash"] == r2.evidence.to_dict()["grounded_interpretation_hash"])

    # Different input → different hash (success path with a MATCHING candidate)
    iyer_candidate = dict(GOOD_CANDIDATE)
    iyer_candidate["parties"] = ["Iyer"]
    iyer_candidate["payment_method"] = "CHEQUE"
    iyer_candidate["payment_method_enum"] = "CHEQUE"
    r3 = _kernel_with(iyer_candidate, model_id="model-A", revision="rev-X").process(
        "Purchased goods from Iyer for Rs.20000 by cheque", request_id="ev3")
    _check("different input → different input_hash",
           d["input_hash"] != r3.evidence.to_dict()["input_hash"])
    _check("different input → different grounded hash",
           d["grounded_interpretation_hash"] != r3.evidence.to_dict()["grounded_interpretation_hash"])

    # Rule pack hash binds the ACTUAL pack executed
    import tempfile
    pack_text = (
        "rules:\n  - id: parties_required\n    type: required\n"
        "    field: parties\n    decision: REVIEW_REQUIRED\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(pack_text)
        pack_path = fh.name
    from platrixa import PlatrixaConfig  # noqa: F401 — config surface exists
    k2 = _kernel_with(GOOD_CANDIDATE)
    k2._rule_pack_path = pack_path
    k2._rule_engine = None  # force lazy load of the real pack
    r4 = k2.process(GROUNDING_INPUT, request_id="ev4")
    d4 = r4.evidence.to_dict()
    _check("rule_pack_hash == sha256 of pack file bytes",
           d4["rule_pack_hash"] == hashlib.sha256(Path(pack_path).read_bytes()).hexdigest())
    _check("rule evidence recorded from actual rule run",
           len(d4["rule_evidence"]) == 1
           and d4["rule_evidence"][0]["rule_id"] == "parties_required")


def json_lower(obj: Any) -> str:
    import json as _json

    return _json.dumps(obj, default=str).lower()


def section_9_rulepack_hash_deterministic() -> None:
    print("\n== 9. RulePack hash is deterministic ==")
    from backend.semantics.evidence import rule_pack_identity

    import tempfile
    text = "rules:\n  - id: r1\n    type: required\n    field: parties\n"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(text)
        p = fh.name
    h1 = rule_pack_identity(p)
    h2 = rule_pack_identity(p)
    _check("same file bytes → same hash", h1 == h2 and len(h1) == 64)
    _check("hash equals sha256 of file bytes",
           h1 == hashlib.sha256(Path(p).read_bytes()).hexdigest())
    _check("no pack configured → empty identity (never invented)",
           rule_pack_identity(None) == "")
    _check("unreadable pack → empty identity (never invented)",
           rule_pack_identity("/nonexistent/pack.yaml") == "")


def section_10_version_identifiers() -> None:
    print("\n== 10. Version identifiers correctly captured ==")
    from backend.semantics import SCHEMA_VERSION
    from backend.semantics.evidence import (
        ACCOUNTING_VERSION,
        EVIDENCE_SCHEMA_VERSION,
        GROUNDING_VERSION,
        prompt_identity,
    )

    _check("evidence schema version present", bool(EVIDENCE_SCHEMA_VERSION))
    _check("grounding version is the actual gate identity",
           GROUNDING_VERSION == "expanded-gate-1")
    _check("accounting version is the actual flow identity",
           ACCOUNTING_VERSION == "hardened-bk-15i")
    _check("IR schema version present", bool(SCHEMA_VERSION))
    _check("prompt identity of a known text is deterministic",
           prompt_identity("abc") == prompt_identity("abc")
           and prompt_identity("abc").startswith("prompt:sha256:"))
    _check("empty prompt → empty identity (never invented)",
           prompt_identity("") == "")
    _check("prompt identity differs for different prompts",
           prompt_identity("abc") != prompt_identity("abd"))


def main() -> int:
    print("=" * 78)
    print("PLATRIXA PHASE 17 — ARCHITECTURE BOUNDARY TEST (fte_fyjc_67)")
    print("=" * 78)

    section_1_candidate_ir()
    section_2_grounding_creates_grounded()
    section_3_accounting_only_grounded()
    section_4_ungrounded_cannot_reach_accounting()
    section_5_ruleengine_downgrades()
    section_6_ruleengine_cannot_upgrade()
    section_7_kernel_owns_final_state()
    section_8_evidence_binds_execution()
    section_9_rulepack_hash_deterministic()
    section_10_version_identifiers()

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
