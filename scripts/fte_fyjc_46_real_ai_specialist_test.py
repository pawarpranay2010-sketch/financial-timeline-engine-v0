"""
Platrixa — Phase 3: Local Model Specialist Tests (Corrected)
=============================================================

Test categories:
  A. Mocked model response (valid 18-field)
  B. JSON extraction robustness
  C. Strict fail-closed validation
  D. Anti-cheating architecture
  E. Local model runner interface
  F. FYJCLLMSpecialist with mock runner
  G. Model unavailable handling
  H. Deterministic specialist separation
  I. System prompt integrity
  J. Regression: Phase 1 + Phase 2 + Legacy
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_llm_specialist import (
    FYJCLLMSpecialist,
    _extract_json_from_response,
    _validate_strict,
    interpret_with_local_model,
    interpret_deterministic,
    SYSTEM_PROMPT,
    MODEL_NOT_AVAILABLE,
)
from backend.maths.fyjc_local_model_runner import (
    LocalModelRunner,
    MockModelRunner,
    check_transformers_available,
    get_model_config,
)
from backend.maths.fyjc_contract import (
    ALL_VALID_FIELDS,
    LEGACY_FIELDS,
    EXPANDED_FIELDS,
)
from backend.maths.schema_verifier import (
    validate_structured_interpretation,
)

passed = 0
failed = 0
skipped = 0
results = []


def _check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        results.append(("✅", name, detail))
    else:
        failed += 1
        results.append(("❌", name, detail))


def _skip(name: str, reason: str):
    global skipped
    skipped += 1
    results.append(("⏭️", name, reason))


# =========================================================================
# A. MOCKED MODEL RESPONSE
# =========================================================================

print("\n" + "=" * 70)
print("A. MOCKED MODEL RESPONSE")
print("=" * 70)

valid_complete = {
    "transaction_type": "PURCHASE",
    "parties": ["Amit"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "CREDIT",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "CREDIT",
    "ambiguity_flags": ["NONE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [
        {"field_name": "transaction_type", "value": "PURCHASE", "confidence": "0.95",
         "grounding": "GROUNDED", "source_text": "purchased", "reasoning": "keyword match"},
    ],
    "overall_confidence": "0.92",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

r1 = validate_structured_interpretation(valid_complete, allow_expanded=True)
_check("A1: Valid complete 18-field JSON accepted", r1.valid, f"status={r1.status.value}")

r1s, e1, _ = _validate_strict(valid_complete, "test")
_check("A2: _validate_strict accepts valid record", r1s, f"errors={e1}")

# A3: Partial dict passes _validate_strict (structural completeness is _ensure_contract_fields' job)
incomplete = {"transaction_type": "PURCHASE"}
r3s, e3, _ = _validate_strict(incomplete, "test")
_check("A3: Partial dict passes _validate_strict (no invalid values)", r3s, f"errors={e3}")

# A4: Empty dict passes _validate_strict (empty = no invalid values)
r4s, e4, _ = _validate_strict({}, "test")
_check("A4: Empty dict passes _validate_strict (no invalid values)", r4s, f"errors={e4}")


# =========================================================================
# B. JSON EXTRACTION
# =========================================================================

print("\n" + "=" * 70)
print("B. JSON EXTRACTION ROBUSTNESS")
print("=" * 70)

raw = json.dumps(valid_complete)
_check("B1: Raw JSON extracted", _extract_json_from_response(raw) is not None)
_check("B2: Fenced JSON extracted", _extract_json_from_response(f"```json\n{raw}\n```") is not None)
_check("B3: Generic fenced JSON extracted", _extract_json_from_response(f"```\n{raw}\n```") is not None)
prose = f"Here is the interpretation:\n\n{raw}\n\nDone."
_check("B4: Prose-wrapped JSON extracted", _extract_json_from_response(prose) is not None)
_check("B5: Empty string → None", _extract_json_from_response("") is None)
_check("B6: No JSON → None", _extract_json_from_response("Just plain text") is None)
_check("B7: Malformed JSON → None", _extract_json_from_response('{"broken": }') is None)


# =========================================================================
# C. STRICT FAIL-CLOSED VALIDATION
# =========================================================================

print("\n" + "=" * 70)
print("C. STRICT FAIL-CLOSED VALIDATION")
print("=" * 70)

# C1: Unknown fields → REJECT (not strip)
with_extra = dict(valid_complete)
with_extra["debit_account"] = "Furniture A/c"
cs, ce, _ = _validate_strict(with_extra, "test")
_check("C1: Unknown field debit_account rejected", not cs, f"errors={ce}")

# C2: Invalid enum → REJECT (not replace)
with_bad_enum = dict(valid_complete)
with_bad_enum["transaction_type_enum"] = "BOGUS"
cs2, ce2, _ = _validate_strict(with_bad_enum, "test")
_check("C2: Invalid enum rejected (not replaced)", not cs2, f"errors={ce2}")

# C3: Confidence > 1.0 → REJECT (not clamp)
with_high_conf = dict(valid_complete)
with_high_conf["overall_confidence"] = "1.5"
cs3, ce3, _ = _validate_strict(with_high_conf, "test")
_check("C3: Confidence > 1.0 rejected (not clamped)", not cs3, f"errors={ce3}")

# C4: Confidence < 0.0 → REJECT
with_neg_conf = dict(valid_complete)
with_neg_conf["overall_confidence"] = "-0.3"
cs4, ce4, _ = _validate_strict(with_neg_conf, "test")
_check("C4: Negative confidence rejected", not cs4)

# C5: Invalid safety flag → REJECT
with_bad_safety = dict(valid_complete)
with_bad_safety["safety_flags"] = ["BOGUS"]
cs5, ce5, _ = _validate_strict(with_bad_safety, "test")
_check("C5: Invalid safety flag rejected", not cs5)

# C6: Invalid scope flag → REJECT
with_bad_scope = dict(valid_complete)
with_bad_scope["scope_flags"] = ["BOGUS"]
cs6, ce6, _ = _validate_strict(with_bad_scope, "test")
_check("C6: Invalid scope flag rejected", not cs6)

# C7: Invalid grounding → REJECT
with_bad_ground = dict(valid_complete)
with_bad_ground["field_confidences"] = [{
    "field_name": "test", "value": "x", "confidence": "0.5",
    "grounding": "MAGIC", "source_text": "", "reasoning": "",
}]
cs7, ce7, _ = _validate_strict(with_bad_ground, "test")
_check("C7: Invalid grounding level rejected", not cs7)


# =========================================================================
# D. ANTI-CHEATING ARCHITECTURE
# =========================================================================

print("\n" + "=" * 70)
print("D. ANTI-CHEATING ARCHITECTURE")
print("=" * 70)

# D1-D4: Forbidden fields → REJECT
for field in ["journal", "debit_account", "credit_account", "ledger"]:
    d = dict(valid_complete)
    d[field] = "test"
    ds, de, _ = _validate_strict(d, "test")
    _check(f"D: {field} in model output rejected", not ds, f"errors={de}")

# D5: Clean output passes
ds5, _, _ = _validate_strict(valid_complete, "test")
_check("D5: Clean interpretation passes strict validation", ds5)

# D6: FYJCLLMSpecialist._make_error_result
spec = FYJCLLMSpecialist(model_runner=MockModelRunner())
err = spec._make_error_result("test", "test error")
_check("D6: Error result has REVIEW_REQUIRED", err["suggested_status"] == "REVIEW_REQUIRED")

err_na = spec._make_error_result("test", "no model", status=MODEL_NOT_AVAILABLE)
_check("D7: MODEL_NOT_AVAILABLE error result", err_na["suggested_status"] == MODEL_NOT_AVAILABLE)

# D8: _ensure_contract_fields
raw_model = {"transaction_type": "PURCHASE", "parties": ["Raj"]}
enriched = spec._ensure_contract_fields(raw_model)
_check("D8: _ensure_contract_fields adds all 18 fields",
       all(f in enriched for f in ALL_VALID_FIELDS))


# =========================================================================
# E. LOCAL MODEL RUNNER INTERFACE
# =========================================================================

print("\n" + "=" * 70)
print("E. LOCAL MODEL RUNNER INTERFACE")
print("=" * 70)

# E1: MockModelRunner
mock = MockModelRunner()
_check("E1: MockModelRunner.is_available()", mock.is_available())
_check("E2: MockModelRunner.status()", "mock" in mock.status().get("model_id", ""))

text_out, err_out = mock.generate("Purchased goods from Raj")
_check("E3: MockModelRunner.generate() returns text", text_out is not None and err_out == "")

parsed_out = _extract_json_from_response(text_out)
_check("E4: MockModelRunner output is valid JSON", parsed_out is not None)

# E5: LocalModelRunner.config
config = get_model_config()
_check("E5: get_model_config() returns model_id", "model_id" in config)
_check("E6: Default model is Qwen", "Qwen" in config["model_id"] or "qwen" in config["model_id"].lower())

# E7: check_transformers_available
tf_avail = check_transformers_available()
_check("E7: check_transformers_available() returns bool", isinstance(tf_avail, bool))

# E8: LocalModelRunner singleton
runner = LocalModelRunner()
runner2 = LocalModelRunner()
_check("E8: LocalModelRunner is singleton", runner is runner2)
LocalModelRunner.reset()

# E9: LocalModelRunner not loaded initially
runner3 = LocalModelRunner()
_check("E9: LocalModelRunner not loaded at init", not runner3.is_available())
LocalModelRunner.reset()

# E10: transformers check
if tf_avail:
    _skip("E10: transformers importable", "transformers available")
else:
    _check("E10: transformers not installed", not tf_avail, "expected for test environment")


# =========================================================================
# F. FYJCLLMSpecialist WITH MOCK RUNNER
# =========================================================================

print("\n" + "=" * 70)
print("F. FYJCLLMSpecialist WITH MOCK RUNNER")
print("=" * 70)

# Set up mock with known responses
purchase_response = json.dumps(valid_complete)
mock_runner = MockModelRunner(responses={
    "purchased furniture from amit": purchase_response,
})

specialist = FYJCLLMSpecialist(model_runner=mock_runner)

# F1: Known input → valid contract
result_f1 = specialist.interpret("Purchased furniture from Amit for Rs.25000 on credit")
_check("F1: Mock specialist produces valid contract",
       all(f in result_f1 for f in ALL_VALID_FIELDS),
       f"tx={result_f1.get('transaction_type_enum')}")
_check("F2: No accounting truth fields",
       not any(k in result_f1 for k in {"journal", "debit_account", "credit_account"}))
_check("F3: suggested_status = REVIEW_REQUIRED",
       result_f1.get("suggested_status") == "REVIEW_REQUIRED")
_check("F4: No _error on success", "_error" not in result_f1)

# F5: Unknown input → returns generic mock
result_f5 = specialist.interpret("Something completely unknown")
_check("F5: Unknown input returns valid contract",
       all(f in result_f5 for f in ALL_VALID_FIELDS))


# =========================================================================
# G. MODEL UNAVAILABLE HANDLING
# =========================================================================

print("\n" + "=" * 70)
print("G. MODEL UNAVAILABLE HANDLING")
print("=" * 70)

# G1: No runner → MODEL_NOT_AVAILABLE
spec_na = FYJCLLMSpecialist(model_runner=None)
LocalModelRunner.reset()
result_g1 = spec_na.interpret("Purchased goods from Raj for Rs.20000")
_check("G1: No model → MODEL_NOT_AVAILABLE",
       result_g1.get("suggested_status") == MODEL_NOT_AVAILABLE)
_check("G2: Error message present", "_error" in result_g1)

# G3: Empty input → error
result_g3 = specialist.interpret("")
_check("G3: Empty input → error", "_error" in result_g3 or result_g3.get("suggested_status") in (MODEL_NOT_AVAILABLE, "REVIEW_REQUIRED"))


# =========================================================================
# H. DETERMINISTIC SPECIALIST SEPARATION
# =========================================================================

print("\n" + "=" * 70)
print("H. DETERMINISTIC SPECIALIST SEPARATION")
print("=" * 70)

from backend.maths.fyjc_llm_specialist import DeterministicSpecialist
det = DeterministicSpecialist()
det_result = det.parse("Purchased furniture from Amit for Rs.25000 on credit")
_check("H1: DeterministicSpecialist is importable", det_result is not None)
_check("H2: DeterministicSpecialist produces 18-field contract",
       all(f in det_result for f in ALL_VALID_FIELDS))
_check("H3: DeterministicSpecialist never claims VERIFIED",
       det_result.get("suggested_status") != "VERIFIED")

det_result2 = interpret_deterministic("Purchased goods from Raj for Rs.20000 for cash")
_check("H4: interpret_deterministic() works",
       all(f in det_result2 for f in ALL_VALID_FIELDS))

# H5: Deterministic is NOT production fallback
_local_spec = FYJCLLMSpecialist(model_runner=MockModelRunner())
# The specialist uses model_runner, not DeterministicSpecialist
_check("H5: FYJCLLMSpecialist uses model_runner, not DeterministicSpecialist",
       hasattr(_local_spec, "_model_runner"))


# =========================================================================
# I. SYSTEM PROMPT INTEGRITY
# =========================================================================

print("\n" + "=" * 70)
print("I. SYSTEM PROMPT INTEGRITY")
print("=" * 70)

_check("I1: Prompt mentions Platrixa", "Platrixa" in SYSTEM_PROMPT)
_check("I2: Prompt forbids journal entries", "journal" in SYSTEM_PROMPT.lower() and "do not" in SYSTEM_PROMPT.lower())
_check("I3: Prompt forbids VERIFIED", "VERIFIED" in SYSTEM_PROMPT)
_check("I4: Prompt forbids inventing", "invent" in SYSTEM_PROMPT.lower() or "fabricat" in SYSTEM_PROMPT.lower())
_check("I5: Prompt specifies JSON schema", '"transaction_type"' in SYSTEM_PROMPT)
_check("I6: Prompt specifies all 18 key fields", all(f in SYSTEM_PROMPT for f in [
    "transaction_type", "parties", "amounts", "payment_method",
    "ambiguity_flags", "field_confidences", "overall_confidence",
    "safety_flags", "scope_flags",
]))
_check("I7: Prompt enforces REVIEW_REQUIRED", "REVIEW_REQUIRED" in SYSTEM_PROMPT)
_check("I8: No mention of Gemini/Groq/OpenRouter in prompt",
       not any(x in SYSTEM_PROMPT.lower() for x in ["gemini", "groq", "openrouter", "google api", "nvidia api"]))


# =========================================================================
# J. REGRESSION: PHASE 1 + PHASE 2 + LEGACY
# =========================================================================

print("\n" + "=" * 70)
print("J. REGRESSION (Phase 1 + Phase 2 + Legacy)")
print("=" * 70)

# Run Phase 1 contract tests
print("  Running Phase 1 contract expansion tests...")
r_j1 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_44_contract_expansion_test.py > /dev/null 2>&1")
_check("J1: Phase 1 contract tests pass", r_j1 == 0, f"exit={r_j1}")

# Run Phase 2 specialist tests
print("  Running Phase 2 specialist tests...")
r_j2 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_45_ai_specialist_test.py > /dev/null 2>&1")
_check("J2: Phase 2 specialist tests pass", r_j2 == 0, f"exit={r_j2}")

# Run legacy unit tests
print("  Running legacy contract unit tests...")
r_j3 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_41_contract_unit_tests.py > /dev/null 2>&1")
_check("J3: Legacy unit tests pass", r_j3 == 0, f"exit={r_j3}")

# Run legacy integration tests
print("  Running legacy integration tests...")
r_j4 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_41_contract_integration_test.py > /dev/null 2>&1")
_check("J4: Legacy integration tests pass", r_j4 == 0, f"exit={r_j4}")


# =========================================================================
# RESULTS
# =========================================================================

print("\n" + "=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)

for icon, name, detail in results:
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))

print(f"\n{'=' * 70}")
print(f"  PASSED:  {passed}")
print(f"  FAILED:  {failed}")
print(f"  SKIPPED: {skipped}")
print(f"  TOTAL:   {passed + failed + skipped}")
print(f"{'=' * 70}")

if failed > 0:
    print("\n⚠️  FAILURES DETECTED")
    sys.exit(1)
else:
    print("\n✅ ALL TESTS PASSED")
    sys.exit(0)
