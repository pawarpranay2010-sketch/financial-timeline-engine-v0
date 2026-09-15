#!/usr/bin/env python3
"""
PLATRIXA — PHASE 17: RULES.MD + ML BENCHMARK TEST (fte_fyjc_68)
===============================================================

Rule-authoring contract (tests 11–15):
  11. simple rules are representable declaratively (rules.md examples load)
  12. YAML rules produce expected decisions
  13. malformed rules fail closed
  14. hook requesting VERIFIED remains impossible
  15. complex hook still produces a normal RuleDecision

ML benchmark contract (tests 16–21):
  16. benchmark dataset is locked (deterministic IDs, version, hash)
  17. dataset hash is deterministic
  18. train/eval overlap check works (flags real overlap; passes clean data)
  19. whole-transaction metric is deterministic
  20. counterfactual pairs are evaluated correctly
  21. field accuracy cannot substitute for compositional accuracy

Run:  python3 scripts/fte_fyjc_68_phase17_rules_benchmark_test.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# Rule tests (11–15)
# ---------------------------------------------------------------------------

def test_11_declarative_rules() -> None:
    print("\n== 11. Simple rules are declarative ==")
    import yaml

    from backend.rules.loader import rule_pack_from_mapping

    # rules.md examples (verbatim primitives)
    pack_text = """
rules:
  - id: credit_requires_counterparty
    type: required
    field: parties
    decision: REVIEW_REQUIRED
    message: "Credit transactions must name a counterparty."
  - id: approved_payment_channels
    type: allowed_values
    field: payment_method_enum
    values: [CASH, CREDIT, BANK, CHEQUE, UPI, UNKNOWN]
    decision: REVIEW_REQUIRED
  - id: high_value_review
    type: threshold
    field: amounts.0.value
    operator: ">"
    value: 100000
    decision: REVIEW_REQUIRED
"""
    parsed = yaml.safe_load(pack_text)
    rules = rule_pack_from_mapping(parsed)
    _check("rules.md examples load with existing primitives", len(rules) == 3)
    _check("declarative rules are short (5-20 lines each)",
           len(pack_text.strip().splitlines()) <= 21)

    # rules.md exists and documents the required sections (§12)
    rules_md = _REPO_ROOT / "rules.md"
    _check("rules.md exists", rules_md.exists())
    if rules_md.exists():
        content = rules_md.read_text(encoding="utf-8")
        required_sections = [
            "What a rule is", "What a rule is NOT", "How YAML rules work",
            "Python hooks", "cannot return VERIFIED", "downgrade",
            "UNAVAILABLE", "ERROR", "evidence", "Bad vs good",
            "complexity principle",
        ]
        missing = [s for s in required_sections if s.lower() not in content.lower()]
        _check("rules.md documents all mandated sections", not missing,
               detail=f"missing: {missing}")
        _check("rules.md contains bad-vs-good hook example",
               "class PaymentModeRule" in content and "type: required" in content)


def test_12_yaml_decisions() -> None:
    print("\n== 12. YAML rules produce expected decisions ==")
    from backend.rules.engine import RuleEngine
    from backend.rules.loader import rule_pack_from_mapping
    from backend.rules.contract import RuleContext

    interp = {"parties": ["Raj"], "payment_method_enum": "CASH",
              "amounts": [{"value": "20000"}]}

    def run(rules_mapping: Dict, interpretation: Dict, status: str = "VERIFIED"):
        engine = RuleEngine(rules=rule_pack_from_mapping(rules_mapping))
        return engine.evaluate(
            RuleContext(request_id="t", raw_input="x", interpretation=interpretation),
            status=status, status_label="Verified")

    # required: fail on empty, pass on present
    pack = {"rules": [{"id": "r", "type": "required", "field": "parties",
                       "decision": "REVIEW_REQUIRED"}]}
    status, _, ev = run(pack, {"parties": []})
    _check("required fails on empty field", status == "REVIEW_REQUIRED"
           and ev[0].to_dict()["result"] == "FAIL")
    status, _, ev = run(pack, interp)
    _check("required passes on present field", status == "VERIFIED"
           and ev[0].to_dict()["result"] == "PASS")

    # allowed_values: fail on disallowed value, pass otherwise
    pack = {"rules": [{"id": "r", "type": "allowed_values", "field": "payment_method_enum",
                       "values": ["CASH", "BANK"], "decision": "REVIEW_REQUIRED"}]}
    status, _, _ = run(pack, {"payment_method_enum": "BARTER"})
    _check("allowed_values fails on disallowed value", status == "REVIEW_REQUIRED")
    status, _, _ = run(pack, {"payment_method_enum": "BANK"})
    _check("allowed_values passes on allowed value", status == "VERIFIED")
    status, _, _ = run(pack, {})
    _check("allowed_values passes when field absent", status == "VERIFIED")

    # threshold: numeric comparison. Loader semantics: PASS iff the field
    # satisfies the predicate (number OP value); violation → FAIL → downgrade.
    # "amount must stay below 1 lakh" expresses the high-value-review policy.
    pack = {"rules": [{"id": "r", "type": "threshold", "field": "amounts.0.value",
                       "operator": "<", "value": 100000, "decision": "REVIEW_REQUIRED"}]}
    status, _, _ = run(pack, interp)
    _check("threshold passes when predicate satisfied", status == "VERIFIED")
    status, _, _ = run(pack, {"amounts": [{"value": "250000"}]})
    _check("threshold fails when predicate violated", status == "REVIEW_REQUIRED")


def test_13_malformed_fails_closed() -> None:
    print("\n== 13. Malformed rules fail closed ==")
    from backend.rules.loader import rule_pack_from_mapping, load_yaml_rule_pack
    from backend.rules.contract import RulePackError

    malformed_cases = [
        ("VERIFIED decision", {"rules": [{"id": "r", "type": "required",
                                          "field": "parties", "decision": "VERIFIED"}]}),
        ("unknown rule type", {"rules": [{"id": "r", "type": "explode",
                                          "field": "parties"}]}),
        ("duplicate ids", {"rules": [
            {"id": "r", "type": "required", "field": "parties"},
            {"id": "r", "type": "required", "field": "amounts"}]}),
        ("empty rules list", {"rules": []}),
        ("non-mapping root", ["not", "a", "mapping"]),
        ("bad threshold operator", {"rules": [{"id": "r", "type": "threshold",
                                               "field": "x", "operator": "~",
                                               "value": 5}]}),
        ("non-numeric threshold", {"rules": [{"id": "r", "type": "threshold",
                                              "field": "x", "operator": ">",
                                              "value": "big"}]}),
    ]
    all_rejected = True
    for name, mapping in malformed_cases:
        try:
            rule_pack_from_mapping(mapping)
            print(f"    (accepted malformed: {name})")
            all_rejected = False
        except RulePackError:
            pass
    _check("all malformed packs rejected at load", all_rejected)

    # Malformed YAML file fails closed
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write("rules: [ { id: r, type: required, field: parties, decision: VERIFIED } ]")
        p = fh.name
    try:
        load_yaml_rule_pack(p)
        _check("malformed YAML file fails closed", False)
    except RulePackError:
        _check("malformed YAML file fails closed", True)

    # Missing file fails closed
    try:
        load_yaml_rule_pack("/nonexistent/pack.yaml")
        _check("missing pack file fails closed", False)
    except RulePackError:
        _check("missing pack file fails closed", True)


def test_14_hook_verified_impossible() -> None:
    print("\n== 14. Hook requesting VERIFIED remains impossible ==")
    from backend.rules.engine import RuleEngine
    from backend.rules.contract import RuleContext, RuleDecision, RuleHook

    class VerifiedRequester(RuleHook):
        rule_id = "requests_verified"

        def validate(self, context: RuleContext) -> RuleDecision:
            # every conceivable smuggling vector, all at once
            return RuleDecision(
                rule_id=self.rule_id,
                outcome="PASS",
                message="VERIFIED please",
                metadata={"decision_hint": "VERIFIED", "status": "VERIFIED",
                          "final_state": "VERIFIED", "override": "VERIFIED"},
            )

    engine = RuleEngine(hooks=[VerifiedRequester()])
    ctx = RuleContext(request_id="v", raw_input="x", interpretation={"parties": []})
    for incoming in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"):
        status, label, evidence = engine.evaluate(ctx, status=incoming, status_label=incoming)
        ev = evidence[0].to_dict()
        hints = json.dumps(ev, default=str)
        if incoming == "VERIFIED":
            _check(f"start=VERIFIED stays VERIFIED (PASS cannot change it)",
                   status == "VERIFIED")
        else:
            _check(f"start={incoming} never upgraded to VERIFIED",
                   status != "VERIFIED")
        _check(f"start={incoming}: no VERIFIED in evidence decision_hint",
               "decision_hint" not in hints or "VERIFIED" not in
               str(ev["metadata"].get("decision_hint", "")))


def test_15_complex_hook_normal_decision() -> None:
    print("\n== 15. Complex hook still produces normal RuleDecision ==")
    from backend.rules.engine import RuleEngine
    from backend.rules.contract import (
        RuleContext, RuleDecision, RuleHook, OUTCOME_UNAVAILABLE,
    )

    class MultiStepExposureHook(RuleHook):
        """Deliberately complex: aggregation + external dependency."""
        rule_id = "group_exposure"

        def validate(self, context: RuleContext) -> RuleDecision:
            amounts = context.interpretation.get("amounts") or []
            total = 0
            for entry in amounts:
                try:
                    total += float(entry.get("value", 0))
                except (TypeError, ValueError, AttributeError):
                    return RuleDecision(self.rule_id, "ERROR",
                                        message="non-numeric exposure data")
            if total > 100000:
                return RuleDecision(self.rule_id, "FAIL",
                                    message=f"group exposure {total} exceeds cap",
                                    metadata={"decision_hint": "REVIEW_REQUIRED"})
            return RuleDecision(self.rule_id, "PASS")

    class UnavailableDependencyHook(RuleHook):
        rule_id = "erp_lookup"

        def validate(self, context: RuleContext) -> RuleDecision:
            return RuleDecision(self.rule_id, OUTCOME_UNAVAILABLE,
                                message="ERP unavailable",
                                metadata={"decision_hint": "REVIEW_REQUIRED"})

    class ExplodingHook(RuleHook):
        rule_id = "explodes"

        def validate(self, context: RuleContext) -> RuleDecision:
            raise RuntimeError("boom")

    class WrongTypeHook(RuleHook):
        rule_id = "wrong_type"

        def validate(self, context: RuleContext):
            return {"outcome": "PASS"}  # not a RuleDecision

    ctx = RuleContext(request_id="c", raw_input="x",
                      interpretation={"amounts": [{"value": "150000"}]})
    engine = RuleEngine(hooks=[MultiStepExposureHook(), UnavailableDependencyHook(),
                               ExplodingHook(), WrongTypeHook()])
    status, _, evidence = engine.evaluate(ctx, status="VERIFIED", status_label="Verified")
    results = [e.to_dict() for e in evidence]
    _check("complex hook produces a normal RuleDecision (FAIL)",
           results[0]["rule_id"] == "group_exposure" and results[0]["result"] == "FAIL")
    _check("UNAVAILABLE dependency blocks VERIFIED",
           results[1]["result"] == "UNAVAILABLE")
    _check("hook exception → ERROR (fail closed)",
           results[2]["result"] == "ERROR" and "boom" in results[2]["message"])
    _check("wrong return type → ERROR (fail closed)",
           results[3]["result"] == "ERROR")
    _check("composite run downgrades VERIFIED", status == "REVIEW_REQUIRED")


# ---------------------------------------------------------------------------
# Benchmark tests (16–21)
# ---------------------------------------------------------------------------

def _bench():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "phase17_benchmark", _REPO_ROOT / "training" / "phase17_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase17_benchmark"] = mod  # required for dataclasses
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_16_dataset_locked() -> None:
    print("\n== 16. Benchmark dataset is locked ==")
    b = _bench()
    records = b.load_dataset()
    _check("dataset loads", len(records) >= 80, detail=str(len(records)))
    ids = [r["id"] for r in records]
    _check("deterministic IDs, all unique", len(ids) == len(set(ids)))
    _check("IDs follow the PB- namespace", all(i.startswith("PB-") for i in ids))
    _check("dataset version declared", b.DATASET_VERSION == "phase17-benchmark-v1")
    _check("dataset sha256 recorded (lock fingerprint)",
           len(b.dataset_sha256()) == 64)
    categories = {r["category"] for r in records}
    _check("categories include core + counterfactual + adversarial",
           {"core", "counterfactual", "adversarial"} <= categories)
    _check("counterfactual pairs are complete (2 members each)",
           all(len(v) == 2 for v in
               _group(records, "pair_id", "counterfactual").values()))
    _check("every record has ground truth + required fields",
           all(isinstance(r.get("ground_truth"), dict)
               and r.get("required_fields") for r in records))


def _group(records, key, category):
    out: Dict[str, list] = {}
    for r in records:
        if r["category"] == category:
            out.setdefault(r[key], []).append(r)
    return out


def test_17_hash_deterministic() -> None:
    print("\n== 17. Dataset hash is deterministic ==")
    b = _bench()
    h1 = b.dataset_sha256()
    h2 = b.dataset_sha256()
    _check("repeated hashing identical", h1 == h2)
    _check("hash is sha256 of exact file bytes",
           h1 == __import__("hashlib").sha256(b.DATASET_PATH.read_bytes()).hexdigest())


def test_18_overlap_check() -> None:
    print("\n== 18. Train/eval overlap check works ==")
    b = _bench()
    records = b.load_dataset()
    report = b.check_independence(records)
    _check("benchmark is independent of ALL training corpora",
           report["independent"], detail=str(report["text_overlaps"]))
    _check("multiple corpora actually checked",
           len(report["corpora_checked"]) >= 10)

    # The check FLAGS a real overlap (self-test with a planted collision)
    planted = [dict(records[0])]
    planted[0] = copy.deepcopy(planted[0])
    planted[0]["input"] = "Purchased goods from Raj for Rs.20000 on credit"  # = C0004
    report2 = b.check_independence(planted)
    _check("planted overlap is detected", not report2["independent"])
    _check("overlap report names the offending corpus",
           any("specialist" in c for c in report2["text_overlaps"]))


def test_19_metric_deterministic() -> None:
    print("\n== 19. Whole-transaction metric is deterministic ==")
    b = _bench()
    records = b.load_dataset()
    adapter = b.DeterministicTestAdapter()
    s1 = b.run_benchmark(adapter)
    s2 = b.run_benchmark(adapter)
    _check("two runs → identical whole-transaction accuracy",
           s1["whole_transaction_accuracy"] == s2["whole_transaction_accuracy"])
    _check("two runs → identical field accuracy",
           s1["field_accuracy"] == s2["field_accuracy"])
    _check("deterministic adapter produces deterministic per-case digests",
           s1["results"][0]["raw_candidate_digest"]
           == s2["results"][0]["raw_candidate_digest"])
    _check("metrics computed over the full dataset",
           s1["dataset_size"] == len(records))


def test_20_counterfactual_evaluated() -> None:
    print("\n== 20. Counterfactual pairs evaluated correctly ==")
    b = _bench()
    records = b.load_dataset()
    cf_records = [r for r in records if r["category"] == "counterfactual"]
    _check("12 counterfactual pairs present", len(cf_records) == 24)

    # Ground-truth pairs must differ EXACTLY in the expected fields
    pairs = _group(records, "pair_id", "counterfactual")
    correct_pairs = 0
    for members in pairs.values():
        a, b2 = members
        gt_a, gt_b = a["ground_truth"], b2["ground_truth"]
        changed = {f for f in gt_a
                   if not b.compare_field(f, gt_a[f], gt_b[f])}
        expected = set(a.get("expect_change") or [])
        if changed == expected:
            correct_pairs += 1
    _check("every pair differs exactly in its declared expect_change fields",
           correct_pairs == len(pairs), detail=f"{correct_pairs}/{len(pairs)}")

    # Metric machinery: a stub adapter that responds correctly to changes
    class SensitiveAdapter:
        def interpret(self, text: str):
            low = text.lower()
            cand = {
                "transaction_type": "SALE" if "sold" in low or "bought" not in low and "purchased" not in low else "PURCHASE",
                "parties": [], "amounts": [],
                "payment_method": "CASH" if "cash" in low else ("CREDIT" if "credit" in low else "UNKNOWN"),
                "references": [], "ambiguities": [],
                "ambiguity_flags": ["NONE"], "overall_confidence": "0.9",
                "suggested_status": "REVIEW_REQUIRED",
            }
            return cand

    summary = b.run_benchmark(SensitiveAdapter(), collect_candidates=True)
    cf = summary["counterfactual"]
    _check("counterfactual sensitivity computed", "counterfactual_sensitivity" in cf)
    _check("unrelated-field stability computed", "unrelated_field_stability" in cf)
    _check("12 pairs measured", cf.get("pairs") == 12)


def test_21_compositional_vs_field() -> None:
    print("\n== 21. Field accuracy cannot substitute for compositional ==")
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate  # noqa: F401

    b = _bench()

    class AlmostRightAdapter:
        """Gets 4/5 fields right on EVERY case but the type always wrong."""
        def interpret(self, text: str):
            low = text.lower()
            is_sale = "sold" in low or "sales" in low
            return {
                "transaction_type": "SALE" if is_sale else "PURCHASE",
                "transaction_type_enum": "SALE" if is_sale else "PURCHASE",
                # deliberately WRONG: everything is a purchase unless 'sold'
                "parties": ["Raj"] if "raj" in low else [],
                "amounts": [{"value": "20000", "currency": "INR", "source": "explicit"}]
                if "20000" in low.replace(",", "") else [],
                "payment_method": "CASH" if "cash" in low else "UNKNOWN",
                "payment_method_enum": "CASH" if "cash" in low else "UNKNOWN",
                "references": [], "ambiguities": [],
                "ambiguity_flags": ["NONE"], "overall_confidence": "0.9",
                "suggested_status": "REVIEW_REQUIRED",
            }

    summary = b.run_benchmark(AlmostRightAdapter())
    field_avg = sum(summary["field_accuracy"].values()) / max(len(summary["field_accuracy"]), 1)
    tx = summary["whole_transaction_accuracy"]
    _check("high field accuracy does not imply high compositional accuracy",
           field_avg > 0.5 and tx < field_avg,
           detail=f"field_avg={field_avg:.2f} whole_tx={tx:.2f}")
    _check("both metrics reported separately (§15)",
           "field_accuracy" in summary and "whole_transaction_accuracy" in summary)


def main() -> int:
    print("=" * 78)
    print("PLATRIXA PHASE 17 — RULES + BENCHMARK TEST (fte_fyjc_68)")
    print("=" * 78)

    test_11_declarative_rules()
    test_12_yaml_decisions()
    test_13_malformed_fails_closed()
    test_14_hook_verified_impossible()
    test_15_complex_hook_normal_decision()
    test_16_dataset_locked()
    test_17_hash_deterministic()
    test_18_overlap_check()
    test_19_metric_deterministic()
    test_20_counterfactual_evaluated()
    test_21_compositional_vs_field()

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
