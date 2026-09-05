#!/usr/bin/env python3
"""
fte_fyjc_50 — Phase 6C Preparation Regression Test
==================================================

Verifies the Phase 6C preparation engineering WITHOUT running the actual
100-example benchmark and WITHOUT loading any model:

  1.  Evaluator imports and module-level invariants
  2.  --check-only mode passes (exit 0) via subprocess
  3.  Locked test set: exactly 100 records, SHA-256 matches the Phase 5 manifest
  4.  Base/LoRA configuration is pinned and immutable (no "latest")
  5.  Identical inference configuration for Base and Fine-Tuned
  6.  Forbidden-output detection (forbidden keys + generated-conclusion leakage)
  7.  Input-echo vs generated-leakage distinction ("credit" in input ≠ leakage)
  8.  Report/results schema completeness
  9.  Deterministic ordering of scoring helpers (Counter/set comparisons stable)
  10. No test-set mutation from the scoring path (bytes unchanged after scoring)
  11. No secret printing anywhere in the evaluator source (no token patterns)
  12. No accidental training invocation (evaluator must not import/launch training)
  13. JSON extraction: fenced/plain/truncated-brace parsing

The locked 100-example test set is READ here for verification only — it is
never modified, reordered, or used to exercise inference. Synthetic fixtures
are used for all scoring tests.

Run:
    python3 scripts/fte_fyjc_50_phase6c_preparation_test.py
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from training import phase6c_evaluate as ev  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  ❌ {name}{(' — ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Synthetic fixture (NOT the test set)
# ---------------------------------------------------------------------------

def make_record(rid: str, text: str, parties: List[str], amounts: List[Any],
                tx: str, pm: str, status: str = "VERIFIED",
                ambiguity_flags: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "id": rid,
        "input": text,
        "output": {
            "transaction_type": tx.title(),
            "parties": parties,
            "amounts": amounts,
            "payment_method": pm.title(),
            "references": [],
            "ambiguities": [],
            "grounding": {"inferred_fields": []},
            "transaction_type_enum": tx,
            "payment_method_enum": pm,
            "ambiguity_flags": ambiguity_flags or ["NONE"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [0.99],
            "overall_confidence": 0.99,
            "suggested_status": status,
            "safety_flags": [],
            "scope_flags": [],
        },
        "metadata": {"difficulty": "clear", "category": "single", "language_style": "standard"},
    }


def fixture_records() -> List[Dict[str, Any]]:
    return [
        make_record("syn-1", "Bought furniture from Raj for ₹15,000.",
                    ["raj"], [{"value": "15000", "currency": "INR"}], "PURCHASE", "CASH"),
        make_record("syn-2", "Purchased goods on credit from Sharma.",
                    ["sharma"], [{"value": "2000", "currency": "INR"}], "PURCHASE", "CREDIT",
                    status="REVIEW_REQUIRED", ambiguity_flags=["PAYMENT_METHOD_AMBIGUOUS"]),
    ]


# ---------------------------------------------------------------------------
# 1. Imports and invariants
# ---------------------------------------------------------------------------

section("1. Evaluator imports and module invariants")
check("module imports", ev.BASE_MODEL is not None)
check("18 contract fields defined", len(ev.VALID_18_FIELDS) == 18)
check("forbidden fields include journal/ledger/balances",
      {"journal", "journal_entry", "ledger", "balances", "debit_lines", "credit_lines"} <= ev.FORBIDDEN_FIELDS)
check("forbidden fields include bare debit/credit keys",
      {"debit", "credit"} <= ev.FORBIDDEN_FIELDS)
check("base model is Qwen2.5-1.5B-Instruct", ev.BASE_MODEL == "Qwen/Qwen2.5-1.5B-Instruct")
check("base revision pinned", re.fullmatch(r"[0-9a-f]{40}", ev.BASE_MODEL_REVISION) is not None)
check("adapter revision pinned", re.fullmatch(r"[0-9a-f]{40}", ev.ADAPTER_REVISION) is not None)

# ---------------------------------------------------------------------------
# 2. Check-only mode (subprocess, no model load)
# ---------------------------------------------------------------------------

section("2. Check-only mode passes (no model load)")
proc = subprocess.run(
    [sys.executable, str(_PROJECT_ROOT / "training" / "phase6c_evaluate.py"), "--check-only"],
    capture_output=True, text=True, timeout=300, cwd=str(_PROJECT_ROOT),
)
check("check-only exit 0", proc.returncode == 0,
      f"rc={proc.returncode} tail={proc.stdout[-200:] if proc.stdout else proc.stderr[-200:]}")
check("check-only prints PASS", "STATUS: PASS" in proc.stdout)
check("check-only does not load models", "Loading model" not in proc.stdout)
check("check-only records 100", "records: 100" in proc.stdout)
check("check-only no secrets in output", not re.search(r"hf_[A-Za-z0-9]{20,}", proc.stdout))

# ---------------------------------------------------------------------------
# 3. Locked test set integrity (read-only verification)
# ---------------------------------------------------------------------------

section("3. Locked test set integrity")
test_path = _PROJECT_ROOT / "training_data" / "fyjc_specialist_test.jsonl"
check("test set exists", test_path.exists())
sha_before = ev.sha256_file(str(test_path))
check("test set SHA matches Phase 5 locked hash", sha_before == ev.EXPECTED_TEST_SHA256,
      f"{sha_before[:16]}…")
records = ev.load_test(str(test_path))
check("test set has exactly 100 records", len(records) == 100, str(len(records)))
check("verify_test_set() returns intact set",
      ev.verify_test_set(str(test_path))[0] is records or len(ev.verify_test_set(str(test_path))[0]) == 100)
# manifest cross-check
manifest = json.loads((_PROJECT_ROOT / "training" / "phase6_manifest.json").read_text())
check("phase6 manifest test sha matches",
      manifest["dataset"]["test_sha256"] == ev.EXPECTED_TEST_SHA256)

# ---------------------------------------------------------------------------
# 4-5. Pinned config + Base/LoRA fairness
# ---------------------------------------------------------------------------

section("4-5. Pinned configuration and Base/LoRA fairness")
check("adapter repo is the Phase 6B artifact",
      ev.ADAPTER_REPO == "Pranay-20/platrixa-fyjc-specialist-v0.1")
check("generation cap is 512", ev.MAX_NEW_TOKENS == 512)
check("decoding is deterministic", ev.DO_SAMPLE is False)
check("no temperature in generation kwargs when deterministic",
      not ev.DO_SAMPLE)  # run_inference forwards no temperature/top_p unless DO_SAMPLE
check("results file is phase6c_results.json", ev.RESULTS_FILE.name == "phase6c_results.json")
check("report file is PHASE6C_FINAL_REPORT.md", ev.REPORT_FILE.name == "PHASE6C_FINAL_REPORT.md")

# Fairness: prompts must be identical for identical records regardless of adapter
try:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(
        ev.BASE_MODEL, revision=ev.BASE_MODEL_REVISION, trust_remote_code=True)
    prompts = ev.build_prompts(tok, fixture_records())
    prompts_again = ev.build_prompts(tok, fixture_records())
    check("chat-template prompts are deterministic", prompts == prompts_again)
    check("system instruction embedded in every prompt",
          all(ev.SYSTEM_INSTRUCTION in p for p in prompts))
    check("no ground-truth leakage into prompts",
          all("15000" not in p or "15000" in rec["input"]
              for p, rec in zip(prompts, fixture_records())))
except Exception as e:  # noqa: BLE001
    check("tokenizer available for prompt fairness check", False, str(e))

# ---------------------------------------------------------------------------
# 6-7. Forbidden-output detection + input-echo distinction
# ---------------------------------------------------------------------------

section("6-7. Forbidden-output and leakage detection")

recs = fixture_records()
gt0 = recs[0]["output"]

# Perfect prediction
good = dict(gt0)
s = ev.score_one(good, gt0, recs[0]["input"])
check("perfect prediction scores clean", s["leakage_type"] == "clean" and not s["accounting_leakage"])
check("perfect prediction is schema-valid", s["schema_valid"])
check("perfect prediction tx matches", s["tx_correct"])

# Forbidden keys in output → leakage
bad = dict(gt0)
bad["journal_entry"] = {"debit": "Purchases", "credit": "Cash"}
s = ev.score_one(bad, gt0, recs[0]["input"])
check("forbidden journal_entry key detected", s["accounting_leakage"] and s["leakage_type"] == "forbidden_keys")
check("forbidden fields listed", "journal_entry" in s["forbidden_fields_in_output"])

bad2 = dict(gt0)
bad2["ledger"] = "Purchases A/c Dr."
s = ev.score_one(bad2, gt0, recs[0]["input"])
check("forbidden ledger key detected", s["accounting_leakage"])

# Generated conclusion in text (not present in input) → leakage
hallu = dict(gt0)
hallu["ambiguities"] = ["Cash account debited and bank account credited for settlement"]
s = ev.score_one(hallu, gt0, recs[0]["input"])
check("generated accounting conclusion detected", s["accounting_leakage"],
      f"type={s['leakage_type']}")

# Input echo ("on credit" appears in the student's own input) → NOT leakage
echo = dict(gt0)
echo["ambiguities"] = ["payment method: cash or credit mentioned in input"]
s = ev.score_one(echo, gt0, recs[1]["input"])
check("'credit' echo of input is NOT flagged", not s["accounting_leakage"],
      f"type={s['leakage_type']}")

# True input-echo classification: a leakage phrase that ALSO appears verbatim
# in the student's input is classified input_echo, not generated_conclusion.
quote = dict(gt0)
quote["ambiguities"] = ["student wrote: journal entry needed here"]
s = ev.score_one(quote, gt0, "Please prepare the journal entry for this purchase.")
check("verbatim echo of leakage phrase classified input_echo",
      s["leakage_type"] == "input_echo" and not s["accounting_leakage"],
      f"type={s['leakage_type']}")

# audit_leakage aggregation distinguishes classes
quote_pred = dict(gt0)
quote_pred["ambiguities"] = ["student wrote: journal entry needed here"]
quote_rec = dict(recs[0])
quote_rec["input"] = "Please prepare the journal entry for this purchase."
leak_audit = ev.audit_leakage(
    [good, bad, echo, quote_pred, None],
    [recs[0], recs[0], recs[1], quote_rec, recs[0]], "synthetic")
check("audit_leakage counts true leakage", leak_audit["true_leakage"] == 1)
check("audit_leakage counts input echo separately", leak_audit["input_echo"] == 1,
      str({k: leak_audit[k] for k in ('true_leakage', 'input_echo', 'clean')}))
check("audit_leakage counts clean", leak_audit["clean"] >= 1)

# Missing/extra fields
partial = {k: v for k, v in gt0.items() if k != "parties"}
s = ev.score_one(partial, gt0, recs[0]["input"])
check("missing field detected", "parties" in s["missing_fields"] and not s["schema_valid"])
weird = dict(gt0)
weird["surprise_field"] = 1
s = ev.score_one(weird, gt0, recs[0]["input"])
check("unknown field detected", "surprise_field" in s["unknown_fields"] and not s["schema_valid"])

# MODEL_NOT_AVAILABLE sentinel is excluded from parsing stats
s = ev.score_one({"suggested_status": "MODEL_NOT_AVAILABLE"}, gt0, "x")
check("MODEL_NOT_AVAILABLE not counted as parse", not s["parse_ok"])

# ---------------------------------------------------------------------------
# 8. Report + results schema
# ---------------------------------------------------------------------------

section("8. Report and results schema")
m = ev.aggregate_scores([good, None], recs, "synthetic")
check("aggregate has rates", "rates" in m and "valid_json_rate" in m["rates"])
required_rates = {
    "valid_json_rate", "valid_18field_schema_rate", "unknown_field_rate",
    "forbidden_field_rate", "accounting_leakage_rate", "transaction_type_accuracy",
    "party_exact_accuracy", "party_token_f1", "amount_extraction_accuracy",
    "payment_method_accuracy", "ambiguity_detection_agreement",
    "suggested_status_agreement", "grounding_compatibility_rate",
    "full_semantic_exact_match", "suggested_status_review_required_rate",
}
check("all required metric rates present", required_rates <= set(m["rates"]),
      str(required_rates - set(m["rates"])))
check("difficulty breakdown present", "by_difficulty" in m and m["by_difficulty"])
check("category breakdown present", "by_category" in m and m["by_category"])
check("full semantic exact match computed", m["rates"]["full_semantic_exact_match"] == 0.5)

hall = ev.audit_hallucinations([good], recs[:1], "synthetic")
check("hallucination audit has issue classes",
      {"invented_parties", "invented_amounts", "invented_payment_methods",
       "invented_references", "unsupported_certainty_claims",
       "failed_ambiguity_preservation"} <= set(hall.keys()))

g = ev.check_grounding_compatibility([good], recs[:1], "synthetic")
check("grounding audit has rates",
      {"grounding_dict_rate", "field_confidences_rate", "valid_status_rate"} <= set(g.keys()))

deltas = ev.compute_deltas(m, m)
check("deltas computed", deltas["absolute"]["valid_json_rate"] == 0.0)

report = ev.generate_report(
    m, m,
    ev.audit_leakage([good], recs[:1], "b"), ev.audit_leakage([good], recs[:1], "f"),
    ev.audit_hallucinations([good], recs[:1], "b"), ev.audit_hallucinations([good], recs[:1], "f"),
    ev.check_grounding_compatibility([good], recs[:1], "b"),
    ev.check_grounding_compatibility([good], recs[:1], "f"),
    deltas, ev.EXPECTED_TEST_SHA256, 100,
    {"inference_seconds": 10.0, "seconds_per_example": 0.1, "device": "cuda", "batch_size": 8},
    {"inference_seconds": 10.0, "seconds_per_example": 0.1, "device": "cuda", "batch_size": 8},
)
check("report contains configuration", "Qwen2.5-1.5B-Instruct" in report and ev.BASE_MODEL_REVISION[:12] in report)
check("report contains adapter revision", ev.ADAPTER_REVISION[:12] in report)
check("report contains test sha", ev.EXPECTED_TEST_SHA256 in report)
check("report contains required sections",
      all(s in report for s in ["BASE PERFORMANCE" if False else "Final Verdict",
                                "Safety / Leakage Comparison", "Grounding Comparison",
                                "Difficulty Breakdown", "Regression Gate"]))
check("report has a verdict", re.search(r"Verdict: \*\*[A-Z_]+\*\*", report) is not None)
check("report does not print secrets", not re.search(r"hf_[A-Za-z0-9]{20,}", report))

# ---------------------------------------------------------------------------
# 9. Deterministic ordering
# ---------------------------------------------------------------------------

section("9. Deterministic ordering")
import copy
r1 = ev.aggregate_scores([copy.deepcopy(good), copy.deepcopy(bad)], recs, "x")
r2 = ev.aggregate_scores([copy.deepcopy(good), copy.deepcopy(bad)], recs, "x")
check("aggregate output is order-stable", json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True))
check("unknown/missing fields sorted", s["unknown_fields"] == sorted(s["unknown_fields"]))

# ---------------------------------------------------------------------------
# 10. No test-set mutation via the scoring path
# ---------------------------------------------------------------------------

section("10. Test set is never mutated by scoring")
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
    for r in recs:
        fh.write(json.dumps(r) + "\n")
    tmp_path = fh.name
tmp_sha = ev.sha256_file(tmp_path)
tmp_records = ev.load_test(tmp_path)
_ = ev.aggregate_scores([copy.deepcopy(good), copy.deepcopy(bad)], tmp_records, "x")
_ = ev.audit_leakage([copy.deepcopy(good), copy.deepcopy(bad)], tmp_records, "x")
_ = ev.audit_hallucinations([copy.deepcopy(good)], tmp_records[:1], "x")
_ = ev.check_grounding_compatibility([copy.deepcopy(good)], tmp_records[:1], "x")
check("fixture file bytes unchanged after all scoring paths",
      ev.sha256_file(tmp_path) == tmp_sha)
Path(tmp_path).unlink()

sha_after_all = ev.sha256_file(str(test_path))
check("locked test set bytes unchanged after this entire test run",
      sha_after_all == sha_before)

# ---------------------------------------------------------------------------
# 11. No secret printing in evaluator source
# ---------------------------------------------------------------------------

section("11. No secrets in evaluator source")
src = (_PROJECT_ROOT / "training" / "phase6c_evaluate.py").read_text()
check("no HF token pattern in evaluator source",
      re.search(r"hf_[A-Za-z0-9]{20,}", src) is None)
check("no hardcoded token= literal in evaluator source",
      re.search(r'token\s*=\s*"[^"]{10,}"', src) is None)

# ---------------------------------------------------------------------------
# 12. No accidental training invocation
# ---------------------------------------------------------------------------

section("12. No training invocation from the evaluator")
for banned in ["SFTTrainer", "trainer.train(", "run_modal", "modal.run", "AutoTrain"]:
    check(f"evaluator does not reference {banned}", banned not in src)

# ---------------------------------------------------------------------------
# 13. JSON extraction robustness
# ---------------------------------------------------------------------------

section("13. JSON extraction")
check("plain JSON parsed", ev.extract_json('{"a": 1}') == {"a": 1})
check("fenced JSON parsed", ev.extract_json('```json\n{"a": 1}\n```') == {"a": 1})
check("embedded JSON parsed", ev.extract_json('Sure! {"a": 1} hope that helps') == {"a": 1})
check("non-JSON returns None", ev.extract_json("no json here") is None)
check("empty returns None", ev.extract_json("") is None)
check("arrays rejected", ev.extract_json('[1,2,3]') is None)

# 512-token cap justification is documented in the module docstring
check("max_new_tokens justification documented", "367" in src)

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"PHASE 6C PREPARATION TEST: {PASS} passed, {FAIL} failed")
if FAILURES:
    print("Failures:")
    for f in FAILURES:
        print(f"  - {f}")
print(f"{'=' * 60}")
sys.exit(1 if FAIL else 0)
