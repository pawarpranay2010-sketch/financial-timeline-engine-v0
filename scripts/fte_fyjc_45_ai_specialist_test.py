#!/usr/bin/env python3
"""
Platrixa — Phase 2 AI Specialist Tests (Sprint 45)
====================================================

Tests the FYJCAISpecialist deterministic NL→18-field parser.

Categories:
  A. Clear transactions
  B. Cash transaction
  C. Credit transaction
  D. Ambiguous input (no payment method)
  E. Missing party
  F. Reference/pronoun handling
  G. Invalid AI output (schema verifier rejection)
  H. Unknown field rejection
  I. Confidence validation
  J. Enum validation
  K. Safety/scope handling

Run:
    python3 scripts/fte_fyjc_45_ai_specialist_test.py
"""

import sys
import os
import json

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_ai_specialist import FYJCAISpecialist, parse_accounting_text
from backend.maths.schema_verifier import validate_structured_interpretation, ValidationStatus
from backend.maths.fyjc_contract import (
    ALL_VALID_FIELDS, LEGACY_FIELDS, EXPANDED_FIELDS,
    VALID_TRANSACTION_TYPES as VALID_TX_ENUM,
    VALID_PAYMENT_METHODS as VALID_PM_ENUM,
    VALID_AMBIGUITY_TYPES as VALID_AMBIG_FLAGS,
    VALID_SAFETY_FLAGS, VALID_SCOPE_FLAGS,
)

# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------
PASSED = 0
FAILED = 0
TOTAL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASSED, FAILED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        msg = f"  ✗ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def validate_and_check(name: str, result: dict) -> bool:
    """Validate a specialist output against the schema verifier."""
    report = validate_structured_interpretation(result)
    check(f"{name}_schema_valid", report.valid,
          f"errors={[e.to_dict() for e in report.errors]}")
    return report.valid


specialist = FYJCAISpecialist()


# ---------------------------------------------------------------------------
# A. CLEAR TRANSACTIONS
# ---------------------------------------------------------------------------
print("\n═══ A. CLEAR TRANSACTIONS ═══")


def test_clear_purchase():
    text = "Purchased furniture worth Rs.25,000 from Amit on credit."
    result = specialist.parse(text)
    validate_and_check("clear_purchase", result)
    check("clear_purchase_tx_type", result["transaction_type_enum"] == "PURCHASE")
    check("clear_purchase_parties", "Amit" in result["parties"])
    check("clear_purchase_amounts", any(a["value"] == "25000.0" for a in result["amounts"]))
    check("clear_purchase_pm", result["payment_method_enum"] == "CREDIT")
    check("clear_purchase_scope", "SINGLE_TRANSACTION" in result["scope_flags"])
    check("clear_purchase_no_fabrication", result.get("referenced_party") is None)


def test_clear_sale():
    text = "Sold goods to Sharma Traders for Rs.15,000 by cheque."
    result = specialist.parse(text)
    validate_and_check("clear_sale", result)
    check("clear_sale_tx_type", result["transaction_type_enum"] == "SALE")
    check("clear_sale_parties", "Sharma Traders" in result["parties"])
    check("clear_sale_pm", result["payment_method_enum"] == "CHEQUE")


def test_clear_expense():
    text = "Paid rent Rs.8,000 to Landlord."
    result = specialist.parse(text)
    validate_and_check("clear_expense", result)
    check("clear_expense_tx_type", result["transaction_type_enum"] == "EXPENSE")
    check("clear_expense_parties", "Landlord" in result["parties"])
    check("clear_expense_amounts", any(a["value"] == "8000.0" for a in result["amounts"]))


test_clear_purchase()
test_clear_sale()
test_clear_expense()


# ---------------------------------------------------------------------------
# B. CASH TRANSACTION
# ---------------------------------------------------------------------------
print("\n═══ B. CASH TRANSACTION ═══")


def test_cash_transaction():
    text = "Purchased stationery for cash Rs.5,000."
    result = specialist.parse(text)
    validate_and_check("cash_tx", result)
    check("cash_tx_pm", result["payment_method_enum"] == "CASH")
    check("cash_tx_pm_legacy", result["payment_method"] == "cash")
    check("cash_tx_amount", any(a["value"] == "5000.0" for a in result["amounts"]))
    # "Purchased stationery" has no party → MISSING_PARTY is correct
    check("cash_tx_has_flags", isinstance(result["ambiguity_flags"], list))
    check("cash_tx_no_payment_ambiguity", "MISSING_PAYMENT_MODE" not in result["ambiguity_flags"])


test_cash_transaction()


# ---------------------------------------------------------------------------
# C. CREDIT TRANSACTION
# ---------------------------------------------------------------------------
print("\n═══ C. CREDIT TRANSACTION ═══")


def test_credit_transaction():
    text = "Sold goods worth Rs.12,000 to Ravi on credit."
    result = specialist.parse(text)
    validate_and_check("credit_tx", result)
    check("credit_tx_pm", result["payment_method_enum"] == "CREDIT")
    check("credit_tx_parties", "Ravi" in result["parties"])
    check("credit_tx_tx_type", result["transaction_type_enum"] == "SALE")


test_credit_transaction()


# ---------------------------------------------------------------------------
# D. AMBIGUOUS INPUT (no payment method)
# ---------------------------------------------------------------------------
print("\n═══ D. AMBIGUOUS INPUT ═══")


def test_ambiguous_no_pm():
    text = "Purchased goods for Rs.10,000."
    result = specialist.parse(text)
    validate_and_check("ambiguous_no_pm", result)
    check("ambiguous_pm_unknown", result["payment_method_enum"] == "UNKNOWN")
    check("ambiguous_pm_legacy_empty", result["payment_method"] == "")
    check("ambiguous_has_flag", "MISSING_PAYMENT_MODE" in result["ambiguity_flags"])
    check("ambiguous_no_fabrication", result["payment_method"] != "cash")
    check("ambiguous_no_fabrication2", result["payment_method"] != "cheque")
    check("ambiguous_grounding_unresolved",
          any(fc["field_name"] == "payment_method" and fc["grounding"] == "UNRESOLVED"
              for fc in result["field_confidences"]))
    check("ambiguous_low_pm_conf",
          any(fc["field_name"] == "payment_method" and float(fc["confidence"]) < 0.5
              for fc in result["field_confidences"]))


test_ambiguous_no_pm()


# ---------------------------------------------------------------------------
# E. MISSING PARTY
# ---------------------------------------------------------------------------
print("\n═══ E. MISSING PARTY ═══")


def test_missing_party():
    text = "Sold goods on credit for Rs.15,000."
    result = specialist.parse(text)
    validate_and_check("missing_party", result)
    check("missing_party_empty", result["parties"] == [])
    check("missing_party_flag", "MISSING_PARTY" in result["ambiguity_flags"])
    check("missing_party_unresolved",
          any(fc["field_name"] == "parties" and fc["grounding"] == "UNRESOLVED"
              for fc in result["field_confidences"]))
    check("missing_party_low_conf",
          any(fc["field_name"] == "parties" and float(fc["confidence"]) < 0.5
              for fc in result["field_confidences"]))
    check("missing_party_not_invented",
          "Ravi" not in result["parties"] and "Amit" not in result["parties"])


test_missing_party()


# ---------------------------------------------------------------------------
# F. REFERENCE / PRONOUN HANDLING
# ---------------------------------------------------------------------------
print("\n═══ F. REFERENCE / PRONOUN HANDLING ═══")


def test_pronoun_detection():
    text = "He paid Rs.5,000 cash to Raj."
    result = specialist.parse(text)
    validate_and_check("pronoun_tx", result)
    check("pronoun_has_flag", "UNRESOLVED_PRONOUN" in result["ambiguity_flags"])
    check("pronoun_parties", "Raj" in result["parties"])
    check("pronoun_amount", any(a["value"] == "5000.0" for a in result["amounts"]))


def test_explicit_reference():
    text = "Paid ref:ORD-123 Rs.3,000 to Mehta."
    result = specialist.parse(text)
    validate_and_check("explicit_ref", result)
    check("explicit_ref_has_ref", "ORD-123" in result["references"])


test_pronoun_detection()
test_explicit_reference()


# ---------------------------------------------------------------------------
# G. INVALID AI OUTPUT (schema verifier rejection)
# ---------------------------------------------------------------------------
print("\n═══ G. INVALID AI OUTPUT ═══")


def test_reject_malformed_json():
    report = validate_structured_interpretation("{bad json!!!")
    check("reject_malformed_json", not report.valid)
    check("reject_malformed_json_status", report.status == ValidationStatus.MALFORMED_JSON)


def test_reject_null():
    report = validate_structured_interpretation(None)
    check("reject_null", not report.valid)


def test_reject_list():
    report = validate_structured_interpretation([1, 2, 3])
    check("reject_list", not report.valid)


def test_reject_wrong_type():
    report = validate_structured_interpretation("just a string")
    check("reject_string", not report.valid)


test_reject_malformed_json()
test_reject_null()
test_reject_list()
test_reject_wrong_type()


# ---------------------------------------------------------------------------
# H. UNKNOWN FIELD REJECTION
# ---------------------------------------------------------------------------
print("\n═══ H. UNKNOWN FIELD REJECTION ═══")


def test_reject_unknown_field():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    result["fake_field"] = "should not exist"
    report = validate_structured_interpretation(result)
    check("reject_unknown_field", not report.valid)
    check("reject_unknown_field_status", report.status == ValidationStatus.UNKNOWN_FIELD)


def test_reject_injected_journal():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    result["journal"] = {"debit": "Purchases", "credit": "Cash"}
    report = validate_structured_interpretation(result)
    check("reject_injected_journal", not report.valid)


test_reject_unknown_field()
test_reject_injected_journal()


# ---------------------------------------------------------------------------
# I. CONFIDENCE VALIDATION
# ---------------------------------------------------------------------------
print("\n═══ I. CONFIDENCE VALIDATION ═══")


def test_confidence_in_range():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    oc = result["overall_confidence"]
    check("confidence_is_string", isinstance(oc, str))
    check("confidence_in_range", 0.0 <= float(oc) <= 1.0,
          f"got {oc}")
    for fc in result["field_confidences"]:
        c = float(fc["confidence"])
        check(f"fc_{fc['field_name']}_in_range", 0.0 <= c <= 1.0,
              f"got {c}")


def test_high_confidence_for_clear_input():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("high_confidence_clear", float(result["overall_confidence"]) >= 0.70,
          f"got {result['overall_confidence']}")


def test_low_confidence_for_ambiguous():
    result = specialist.parse("Something happened.")
    check("low_confidence_ambiguous", float(result["overall_confidence"]) < 0.50,
          f"got {result['overall_confidence']}")


test_confidence_in_range()
test_high_confidence_for_clear_input()
test_low_confidence_for_ambiguous()


# ---------------------------------------------------------------------------
# J. ENUM VALIDATION
# ---------------------------------------------------------------------------
print("\n═══ J. ENUM VALIDATION ═══")


def test_all_enums_valid():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("tx_enum_valid", result["transaction_type_enum"] in VALID_TX_ENUM)
    check("pm_enum_valid", result["payment_method_enum"] in VALID_PM_ENUM)
    for flag in result["ambiguity_flags"]:
        check(f"ambig_flag_{flag}_valid", flag in VALID_AMBIG_FLAGS)
    for flag in result["safety_flags"]:
        check(f"safety_flag_{flag}_valid", flag in VALID_SAFETY_FLAGS)
    for flag in result["scope_flags"]:
        check(f"scope_flag_{flag}_valid", flag in VALID_SCOPE_FLAGS)


def test_expanded_field_count():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("has_18_fields", len(result) == 18, f"got {len(result)}")
    check("has_all_valid_fields", set(result.keys()) == ALL_VALID_FIELDS)


def test_suggested_status_never_verified():
    """AI must never claim VERIFIED."""
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("never_verified", result["suggested_status"] != "VERIFIED")
    check("always_review", result["suggested_status"] == "REVIEW_REQUIRED")


test_all_enums_valid()
test_expanded_field_count()
test_suggested_status_never_verified()


# ---------------------------------------------------------------------------
# K. SAFETY / SCOPE HANDLING
# ---------------------------------------------------------------------------
print("\n═══ K. SAFETY / SCOPE HANDLING ═══")


def test_safety_flags_clear_input():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("safety_none_clear", "NONE" in result["safety_flags"] or
          not any(f != "NONE" for f in result["safety_flags"]))


def test_safety_flags_low_confidence():
    result = specialist.parse("Something vague.")
    check("safety_low_conf", "LOW_CONFIDENCE" in result["safety_flags"] or
          "MISSING_REQUIRED_FIELDS" in result["safety_flags"])


def test_scope_single():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("scope_single", "SINGLE_TRANSACTION" in result["scope_flags"])


def test_scope_gst():
    result = specialist.parse("Paid CGST Rs.500 and SGST Rs.500 to Govt.")
    check("scope_gst", "GST_SPECIFIC" in result["scope_flags"])


def test_scope_settlement():
    result = specialist.parse("Settled account with Raj for Rs.10,000.")
    check("scope_settlement", "SETTLEMENT_CALCULATION" in result["scope_flags"])


def test_scope_return():
    result = specialist.parse("Returned goods worth Rs.3,000 to Amit.")
    check("scope_return", "RETURN_PROCESSING" in result["scope_flags"])


test_safety_flags_clear_input()
test_safety_flags_low_confidence()
test_scope_single()
test_scope_gst()
test_scope_settlement()
test_scope_return()


# ---------------------------------------------------------------------------
# LEGACY COMPATIBILITY
# ---------------------------------------------------------------------------
print("\n═══ LEGACY COMPATIBILITY ═══")


def test_legacy_fields_present():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("legacy_has_tx_type", "transaction_type" in result)
    check("legacy_has_parties", "parties" in result)
    check("legacy_has_amounts", "amounts" in result)
    check("legacy_has_pm", "payment_method" in result)
    check("legacy_has_refs", "references" in result)
    check("legacy_has_ambig", "ambiguities" in result)
    check("legacy_has_grounding", "grounding" in result)


def test_legacy_tx_type_is_string():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("legacy_tx_type_str", isinstance(result["transaction_type"], str))
    check("legacy_tx_type_lower", result["transaction_type"] == "purchase")


def test_legacy_amounts_are_dicts():
    result = specialist.parse("Purchased goods from Raj for Rs.5,000 cash.")
    check("legacy_amounts_list", isinstance(result["amounts"], list))
    if result["amounts"]:
        check("legacy_amounts_dict", isinstance(result["amounts"][0], dict))
        check("legacy_amounts_has_value", "value" in result["amounts"][0])


test_legacy_fields_present()
test_legacy_tx_type_is_string()
test_legacy_amounts_are_dicts()


# ---------------------------------------------------------------------------
# END-TO-END: parse → validate
# ---------------------------------------------------------------------------
print("\n═══ END-TO-END: PARSE → VALIDATE ═══")


def test_e2e_all_verdict_classes():
    """Parse representative text for each verdict class, validate schema."""
    cases = [
        ("VERIFIED_clear", "Purchased goods from Raj for Rs.5,000 cash."),
        ("REVIEW_REQUIRED_ambiguous", "Purchased goods for Rs.10,000."),
        ("NOT_SUPPORTED_nonsense", "The weather is nice today."),
        ("MISSING_PARTY", "Sold goods on credit for Rs.15,000."),
        ("CREDIT", "Sold goods to Amit on credit for Rs.12,000."),
        ("CASH", "Purchased stationery for cash Rs.5,000."),
        ("SETTLEMENT", "Settled full account with Raj for Rs.20,000."),
        ("RETURN", "Returned goods worth Rs.3,000 to Amit."),
        ("GST", "Paid CGST Rs.500 and SGST Rs.500 to Govt."),
        ("DRAWING", "Withdrew Rs.10,000 for personal use."),
    ]
    for label, text in cases:
        result = specialist.parse(text)
        valid = validate_and_check(f"e2e_{label}", result)
        if valid:
            check(f"e2e_{label}_18_fields", len(result) == 18)
            check(f"e2e_{label}_no_verified", result["suggested_status"] != "VERIFIED")


test_e2e_all_verdict_classes()


# ---------------------------------------------------------------------------
# REAL DATASET COMPATIBILITY
# ---------------------------------------------------------------------------
print("\n═══ REAL DATASET COMPATIBILITY ═══")


def test_real_training_format():
    """Parse text and verify output matches the training data contract."""
    text = "Purchased goods from Raj Traders for Rs.20,000 for cash."
    result = specialist.parse(text)

    # Verify it can be serialized as JSON (training data format)
    json_str = json.dumps(result, ensure_ascii=False, default=str)
    parsed_back = json.loads(json_str)
    check("real_json_roundtrip", parsed_back == result)

    # Verify legacy format is compatible with StructuredInterpretation.to_dict()
    legacy_keys = {"transaction_type", "parties", "amounts", "payment_method",
                   "references", "ambiguities", "grounding"}
    check("real_has_legacy_keys", legacy_keys.issubset(set(result.keys())))


test_real_training_format()


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
print(f"\n{'═' * 50}")
print(f"RESULTS: {PASSED}/{TOTAL} passed, {FAILED} failed")
if FAILED == 0:
    print("✅ ALL TESTS PASSED")
    sys.exit(0)
else:
    print("❌ SOME TESTS FAILED")
    sys.exit(1)
