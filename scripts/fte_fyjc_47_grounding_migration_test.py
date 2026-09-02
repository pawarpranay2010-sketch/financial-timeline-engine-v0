"""
Platrixa — Phase 4: Grounding Gate + Migration Tests
======================================================

Test categories:
  A. GroundingGate direct behavior
  B. Specialist → GroundingGate integration
  C. Grounding rejection (fabricated data)
  D. Grounded acceptance (supported data)
  E. Fabricated party detection
  F. Fabricated amount detection
  G. Fabricated payment method detection
  H. Unresolved pronoun handling
  I. Invalid reference detection
  J. Forbidden accounting fields rejection
  K. Model confidence cannot bypass grounding
  L. Validated interpretation flow
  M. Deterministic kernel remains authoritative
  N. Regression against Phase 1-3 + legacy
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_grounding_gate import (
    ExpandedGroundingGate,
    GroundingResult,
    FieldGrounding,
    _text_contains,
    _amount_in_text,
)
from backend.maths.fyjc_llm_specialist import (
    FYJCLLMSpecialist,
    _extract_json_from_response,
    _validate_strict,
    SYSTEM_PROMPT,
)
from backend.maths.fyjc_local_model_runner import MockModelRunner
from backend.maths.fyjc_contract import ALL_VALID_FIELDS
from backend.maths.schema_verifier import validate_structured_interpretation

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
# HELPER: Build a valid 18-field interpretation
# =========================================================================

def _make_interp(**overrides) -> dict:
    """Build a valid 18-field interpretation with overrides."""
    base = {
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
             "grounding": "GROUNDED", "source_text": "purchased", "reasoning": "keyword"},
            {"field_name": "parties", "value": "['Amit']", "confidence": "0.90",
             "grounding": "GROUNDED", "source_text": "from Amit", "reasoning": "extracted"},
        ],
        "overall_confidence": "0.92",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }
    base.update(overrides)
    return base


# =========================================================================
# A. GROUNDING GATE DIRECT BEHAVIOR
# =========================================================================

print("\n" + "=" * 70)
print("A. GROUNDING GATE DIRECT BEHAVIOR")
print("=" * 70)

gate = ExpandedGroundingGate()

# A1: Fully grounded interpretation
interp_a1 = _make_interp()
r_a1 = gate.ground(interp_a1, "Purchased furniture worth Rs.25000 from Amit on credit.")
_check("A1: Fully grounded interpretation passes", r_a1.grounded, r_a1.summary)
_check("A2: safe_for_kernel=True for grounded", r_a1.safe_for_kernel)

# A3: Empty source text
r_a3 = gate.ground(interp_a1, "")
_check("A3: Empty source text → not grounded", not r_a3.grounded)

# A4: Completely different text
r_a4 = gate.ground(interp_a1, "The weather is nice today.")
_check("A4: Unrelated text → not grounded", not r_a4.grounded)

# A5: GroundingResult has field_results
_check("A5: field_results populated", len(r_a1.field_results) > 0)

# A6: summary property
_check("A6: summary property works", isinstance(r_a1.summary, str))


# =========================================================================
# B. SPECIALIST → GROUNDING GATE INTEGRATION
# =========================================================================

print("\n" + "=" * 70)
print("B. SPECIALIST → GROUNDING GATE INTEGRATION")
print("=" * 70)

# B1: Mock specialist with grounded response
grounded_response = json.dumps(_make_interp())
mock_runner_g = MockModelRunner(responses={"purchased furniture from amit": grounded_response})
spec_g = FYJCLLMSpecialist(model_runner=mock_runner_g)

r_b1 = spec_g.interpret("Purchased furniture from Amit for Rs.25000 on credit.")
_check("B1: Grounded interpretation passes specialist", r_b1.get("_grounded") is True,
       f"grounded={r_b1.get('_grounded')}")
_check("B2: _grounding_summary present", "_grounding_summary" in r_b1)

# B3: Mock specialist with fabricated party
fabricated_interp = _make_interp(parties=["Zara"])
mock_runner_f = MockModelRunner(responses={"test": json.dumps(fabricated_interp)})
spec_f = FYJCLLMSpecialist(model_runner=mock_runner_f)

r_b3 = spec_f.interpret("test")
_check("B3: Fabricated party → _grounded=False", r_b3.get("_grounded") is False,
       f"grounded={r_b3.get('_grounded')}")
_check("B4: _grounding_issues present", "_grounding_issues" in r_b3)


# =========================================================================
# C. GROUNDING REJECTION — FABRICATED DATA
# =========================================================================

print("\n" + "=" * 70)
print("C. GROUNDING REJECTION — FABRICATED DATA")
print("=" * 70)

# C1: Fabricated party
interp_c1 = _make_interp(parties=["Zara"])
r_c1 = gate.ground(interp_c1, "Purchased furniture worth Rs.25000.")
_check("C1: Fabricated party rejected", not r_c1.grounded, r_c1.summary)

# C2: Fabricated amount
interp_c2 = _make_interp(amounts=[{"value": "99999", "currency": "INR", "source": "explicit"}])
r_c2 = gate.ground(interp_c2, "Purchased furniture from Amit.")
_check("C2: Fabricated amount rejected", not r_c2.grounded, r_c2.summary)

# C3: Fabricated payment method
interp_c3 = _make_interp(payment_method_enum="CREDIT", payment_method="CREDIT")
r_c3 = gate.ground(interp_c3, "Purchased furniture from Amit for Rs.25000.")
_check("C3: Fabricated credit rejected (not in text)", not r_c3.grounded, r_c3.summary)

# C4: Multiple fabrications
interp_c4 = _make_interp(parties=["Zara"], amounts=[{"value": "99999", "currency": "INR", "source": "explicit"}])
r_c4 = gate.ground(interp_c4, "Bought something.")
_check("C4: Multiple fabrications rejected", not r_c4.grounded)


# =========================================================================
# D. GROUNDED ACCEPTANCE — SUPPORTED DATA
# =========================================================================

print("\n" + "=" * 70)
print("D. GROUNDED ACCEPTANCE — SUPPORTED DATA")
print("=" * 70)

# D1: Party in text (payment method not stated → UNKNOWN)
interp_d1 = _make_interp(parties=["Amit"], payment_method_enum="UNKNOWN", payment_method="UNKNOWN")
r_d1 = gate.ground(interp_d1, "Purchased furniture from Amit for Rs.25000.")
_check("D1: Party in text → grounded", r_d1.grounded, r_d1.summary)

# D2: Amount in text (payment method not stated → UNKNOWN)
interp_d2 = _make_interp(amounts=[{"value": "25000", "currency": "INR", "source": "explicit"}], payment_method_enum="UNKNOWN", payment_method="UNKNOWN")
r_d2 = gate.ground(interp_d2, "Purchased furniture for Rs.25000 from Amit.")
_check("D2: Amount in text → grounded", r_d2.grounded, r_d2.summary)

# D3: Cash payment in text
interp_d3 = _make_interp(payment_method_enum="CASH", payment_method="CASH")
r_d3 = gate.ground(interp_d3, "Purchased furniture from Amit for Rs.25000 cash.")
_check("D3: Cash payment in text → grounded", r_d3.grounded)

# D4: Credit payment in text
interp_d4 = _make_interp(payment_method_enum="CREDIT", payment_method="CREDIT")
r_d4 = gate.ground(interp_d4, "Purchased furniture from Amit for Rs.25000 on credit.")
_check("D4: Credit payment in text → grounded", r_d4.grounded)

# D5: UNKNOWN payment → grounded (not fabricated)
interp_d5 = _make_interp(payment_method_enum="UNKNOWN", payment_method="UNKNOWN")
r_d5 = gate.ground(interp_d5, "Purchased furniture from Amit for Rs.25000.")
_check("D5: UNKNOWN payment → grounded (correctly not fabricated)", r_d5.grounded)


# =========================================================================
# E. FABRICATED PARTY DETECTION
# =========================================================================

print("\n" + "=" * 70)
print("E. FABRICATED PARTY DETECTION")
print("=" * 70)

interp_e = _make_interp(parties=["Zara International Ltd"])
r_e = gate.ground(interp_e, "Purchased furniture from Amit for Rs.25000.")
_check("E1: Fabricated party 'Zara International Ltd' rejected", not r_e.grounded)
party_issues = [i for i in r_e.issues if "Zara" in i]
_check("E2: Issue mentions fabricated party", len(party_issues) > 0)


# =========================================================================
# F. FABRICATED AMOUNT DETECTION
# =========================================================================

print("\n" + "=" * 70)
print("F. FABRICATED AMOUNT DETECTION")
print("=" * 70)

interp_f = _make_interp(amounts=[{"value": "500000", "currency": "INR", "source": "explicit"}])
r_f = gate.ground(interp_f, "Purchased furniture from Amit for Rs.25000.")
_check("F1: Fabricated amount 500000 rejected", not r_f.grounded)
amt_issues = [i for i in r_f.issues if "500000" in i]
_check("F2: Issue mentions fabricated amount", len(amt_issues) > 0)


# =========================================================================
# G. FABRICATED PAYMENT METHOD DETECTION
# =========================================================================

print("\n" + "=" * 70)
print("G. FABRICATED PAYMENT METHOD DETECTION")
print("=" * 70)

interp_g = _make_interp(payment_method_enum="UPI", payment_method="UPI")
r_g = gate.ground(interp_g, "Purchased furniture from Amit for Rs.25000.")
_check("G1: Fabricated UPI rejected (not in text)", not r_g.grounded)

# G2: Valid UPI in text
interp_g2 = _make_interp(payment_method_enum="UPI", payment_method="UPI")
r_g2 = gate.ground(interp_g2, "Purchased furniture from Amit for Rs.25000 by UPI.")
_check("G2: UPI in text → grounded", r_g2.grounded)


# =========================================================================
# H. UNRESOLVED PRONOUN HANDLING
# =========================================================================

print("\n" + "=" * 70)
print("H. UNRESOLVED PRONOUN HANDLING")
print("=" * 70)

# H1: Pronoun reference with no party → ambiguity flag preserved
interp_h1 = _make_interp(
    parties=[],
    amounts=[],
    payment_method_enum="UNKNOWN",
    payment_method="UNKNOWN",
    ambiguity_flags=["UNRESOLVED_PRONOUN", "MISSING_PARTY"],
)
r_h1 = gate.ground(interp_h1, "He purchased furniture.")
_check("H1: Empty parties with UNRESOLVED_PRONOUN → grounded (no fabrication)", r_h1.grounded, r_h1.summary)

# H2: Pronoun with fabricated party → rejected
interp_h2 = _make_interp(parties=["Raj"], ambiguity_flags=[])
r_h2 = gate.ground(interp_h2, "He purchased furniture.")
_check("H2: Fabricated party from pronoun → rejected", not r_h2.grounded)


# =========================================================================
# I. INVALID REFERENCE DETECTION
# =========================================================================

print("\n" + "=" * 70)
print("I. INVALID REFERENCE DETECTION")
print("=" * 70)

# I1: Negative reference index
interp_i1 = _make_interp(referenced_transaction_index=-1)
r_i1 = gate.ground(interp_i1, "Test input.")
_check("I1: Negative reference index rejected", not r_i1.grounded)

# I2: Non-integer reference index
interp_i2 = _make_interp(referenced_transaction_index="abc")
r_i2 = gate.ground(interp_i2, "Test input.")
_check("I2: Non-integer reference rejected", not r_i2.grounded)

# I3: Valid reference index (with grounded data)
interp_i3 = _make_interp(
    parties=["Amit"],
    amounts=[{"value": "10000", "currency": "INR", "source": "explicit"}],
    payment_method_enum="CASH", payment_method="CASH",
    referenced_transaction_index=0,
)
r_i3 = gate.ground(interp_i3, "Paid Amit Rs.10000 cash.")
_check("I3: Valid reference index accepted", r_i3.grounded, r_i3.summary)


# =========================================================================
# J. FORBIDDEN ACCOUNTING FIELDS REJECTION
# =========================================================================

print("\n" + "=" * 70)
print("J. FORBIDDEN ACCOUNTING FIELDS REJECTION")
print("=" * 70)

for field in ["journal", "debit_account", "credit_account", "ledger"]:
    interp_j = _make_interp()
    interp_j[field] = "test"
    r_j = gate.ground(interp_j, "Purchased furniture from Amit for Rs.25000.")
    _check(f"J: {field} → not safe_for_kernel", not r_j.safe_for_kernel)


# =========================================================================
# K. MODEL CONFIDENCE CANNOT BYPASS GROUNDING
# =========================================================================

print("\n" + "=" * 70)
print("K. MODEL CONFIDENCE CANNOT BYPASS GROUNDING")
print("=" * 70)

# K1: High confidence + fabricated party → still not grounded
interp_k1 = _make_interp(
    parties=["Zara"],
    overall_confidence="0.99",
)
r_k1 = gate.ground(interp_k1, "Purchased furniture from Amit.")
_check("K1: High confidence (0.99) + fabricated party → not grounded", not r_k1.grounded)
_check("K2: Confidence override detected in issues",
       any("confidence" in i.lower() for i in r_k1.issues))


# =========================================================================
# L. VALIDATED INTERPRETATION FLOW
# =========================================================================

print("\n" + "=" * 70)
print("L. VALIDATED INTERPRETATION FLOW")
print("=" * 70)

# L1: Full pipeline with mock specialist
full_interp = json.dumps(_make_interp())
mock_full = MockModelRunner(responses={"purchased furniture from amit": full_interp})
spec_full = FYJCLLMSpecialist(model_runner=mock_full)
result_full = spec_full.interpret("Purchased furniture from Amit for Rs.25000 on credit.")

_check("L1: Full pipeline produces valid contract",
       all(f in result_full for f in ALL_VALID_FIELDS))
_check("L2: Grounded flag set", result_full.get("_grounded") is True)
_check("L3: suggested_status = REVIEW_REQUIRED",
       result_full.get("suggested_status") == "REVIEW_REQUIRED")
_check("L4: No forbidden accounting fields",
       not any(k in result_full for k in {"journal", "ledger", "debit_account", "credit_account"}))


# =========================================================================
# M. DETERMINISTIC KERNEL REMAINS AUTHORITATIVE
# =========================================================================

print("\n" + "=" * 70)
print("M. DETERMINISTIC KERNEL REMAINS AUTHORITATIVE")
print("=" * 70)

# M1: Specialist output never contains journal
result_m1 = spec_full.interpret("Purchased furniture from Amit for Rs.25000 on credit.")
_check("M1: Specialist output has no 'journal' field", "journal" not in result_m1)
_check("M2: Specialist output has no 'debit_account' field", "debit_account" not in result_m1)
_check("M3: Specialist output has no 'credit_account' field", "credit_account" not in result_m1)
_check("M4: Specialist output has no 'ledger' field", "ledger" not in result_m1)

# M5: Strict validation rejects journal
interp_with_journal = _make_interp()
interp_with_journal["journal"] = [{"debit": "Furniture", "credit": "Amit", "amount": 25000}]
valid_j, errors_j, _ = _validate_strict(interp_with_journal, "test")
_check("M5: Strict validation rejects journal field", not valid_j)


# =========================================================================
# N. HELPER FUNCTION TESTS
# =========================================================================

print("\n" + "=" * 70)
print("N. HELPER FUNCTION TESTS")
print("=" * 70)

# _text_contains
_check("N1: _text_contains match", _text_contains("Hello Amit", "Amit"))
_check("N2: _text_contains case-insensitive", _text_contains("hello AMIT", "amit"))
_check("N3: _text_contains no match", not _text_contains("Hello World", "Amit"))
_check("N4: _text_contains empty", not _text_contains("", "Amit"))
_check("N5: _text_contains with Rs.", _text_contains("Rs.25000", "25000"))
_check("N6: _text_contains with ₹", _text_contains("₹25,000", "25000"))

# _amount_in_text
_check("N7: _amount_in_text exact", _amount_in_text("Rs.25000", "25000"))
_check("N8: _amount_in_text with comma", _amount_in_text("Rs.25,000", "25000"))
_check("N9: _amount_in_text with ₹", _amount_in_text("₹25000", "25000"))
_check("N10: _amount_in_text no match", not _amount_in_text("Rs.25000", "99999"))
_check("N11: _amount_in_text thousand format", _amount_in_text("25 thousand", "25000"))
_check("N12: _amount_in_text k format", _amount_in_text("25k", "25000"))


# =========================================================================
# O. PHASE 4 SYSTEM PROMPT VERIFICATION
# =========================================================================

print("\n" + "=" * 70)
print("O. SYSTEM PROMPT VERIFICATION")
print("=" * 70)

_check("O1: Prompt mentions grounding/uncertainty", "uncertain" in SYSTEM_PROMPT.lower() or "ambig" in SYSTEM_PROMPT.lower())
_check("O2: Prompt forbids inventing", "invent" in SYSTEM_PROMPT.lower() or "fabricat" in SYSTEM_PROMPT.lower())
_check("O3: Prompt enforces REVIEW_REQUIRED", "REVIEW_REQUIRED" in SYSTEM_PROMPT)


# =========================================================================
# REGRESSION: PHASE 1-3 + LEGACY
# =========================================================================

print("\n" + "=" * 70)
print("REGRESSION: PHASE 1-3 + LEGACY")
print("=" * 70)

r_p3 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_46_real_ai_specialist_test.py > /dev/null 2>&1")
_check("R1: Phase 3 tests pass", r_p3 == 0, f"exit={r_p3}")

r_p1 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_44_contract_expansion_test.py > /dev/null 2>&1")
_check("R2: Phase 1 tests pass", r_p1 == 0, f"exit={r_p1}")

r_p2 = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_45_ai_specialist_test.py > /dev/null 2>&1")
_check("R3: Phase 2 tests pass", r_p2 == 0, f"exit={r_p2}")

r_lu = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_41_contract_unit_tests.py > /dev/null 2>&1")
_check("R4: Legacy unit tests pass", r_lu == 0, f"exit={r_lu}")

r_li = os.system(f"cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && python3 scripts/fte_fyjc_41_contract_integration_test.py > /dev/null 2>&1")
_check("R5: Legacy integration tests pass", r_li == 0, f"exit={r_li}")


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
