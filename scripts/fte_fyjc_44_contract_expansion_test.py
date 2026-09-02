#!/usr/bin/env python3
"""
Platrixa — Phase 1 Contract Expansion Tests (Sprint 44)
========================================================

Tests the expanded 18-field StructuredInterpretation contract:
  - Legacy 7-field records still pass validation
  - Expanded 18-field records pass validation
  - Malformed/invalid records are correctly rejected
  - Legacy → expanded normalization preserves semantics
  - Schema verifier supports both formats

Run:
    python3 scripts/fte_fyjc_44_contract_expansion_test.py
"""

import sys
import os
import json

sys.path.insert(0, os.getcwd())

from backend.maths.schema_verifier import (
    validate_structured_interpretation,
    ValidationStatus,
    CANONICAL_FIELDS,
    ALL_VALID_FIELDS,
)
from backend.maths.fyjc_contract import (
    ExpandedInterpretation,
    FieldConfidenceRecord,
    classify_record,
    normalize_legacy_to_expanded,
    normalize_expanded_to_legacy,
    TransactionTypeEnum,
    PaymentMethodEnum,
    AmbiguityTypeEnum,
    GroundingLevel,
    SafetyFlag,
    ScopeFlag,
    LEGACY_FIELDS,
    EXPANDED_FIELDS,
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


# ---------------------------------------------------------------------------
# LEGACY: Valid 7-field records
# ---------------------------------------------------------------------------
print("\n═══ LEGACY VALID CASES ═══")


def test_legacy_valid_complete():
    record = {
        "transaction_type": "purchase",
        "parties": ["Vendor Inc."],
        "amounts": [{"value": "10000", "currency": "INR"}],
        "payment_method": "cash",
        "references": ["order_123"],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    }
    report = validate_structured_interpretation(record)
    check("legacy_complete_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")
    check("legacy_complete_status", report.status == ValidationStatus.VALID)
    check("legacy_complete_has_parsed", report.parsed is not None)


def test_legacy_valid_minimal():
    record = {
        "transaction_type": "sale",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
    }
    report = validate_structured_interpretation(record)
    check("legacy_minimal_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


def test_legacy_valid_no_grounding():
    record = {
        "transaction_type": "expense",
        "parties": ["Landlord"],
        "amounts": [{"value": "5000"}],
        "payment_method": "bank_transfer",
    }
    report = validate_structured_interpretation(record)
    check("legacy_no_grounding_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


def test_legacy_valid_json_string():
    record = {
        "transaction_type": "settlement",
        "parties": ["Customer"],
        "amounts": [{"value": "2000"}],
        "payment_method": "credit",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    report = validate_structured_interpretation(json.dumps(record))
    check("legacy_json_string_valid", report.valid)


def test_legacy_real_dataset_format():
    """Simulate a real FYJC record from specialist_clean_training.jsonl."""
    record = {
        "transaction_type": "purchase",
        "parties": ["Raj Traders"],
        "amounts": [{"value": "20000", "currency": "INR", "source": "explicit"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": ["missing_payment_mode"],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]},
    }
    report = validate_structured_interpretation(record)
    check("legacy_real_format_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


test_legacy_valid_complete()
test_legacy_valid_minimal()
test_legacy_valid_no_grounding()
test_legacy_valid_json_string()
test_legacy_real_dataset_format()


# ---------------------------------------------------------------------------
# EXPANDED: Valid 18-field records
# ---------------------------------------------------------------------------
print("\n═══ EXPANDED VALID CASES ═══")


def test_expanded_valid_complete():
    record = {
        # Legacy 7
        "transaction_type": "purchase",
        "parties": ["Sharma Traders"],
        "amounts": [{"value": "50000", "currency": "INR", "source": "explicit"}],
        "payment_method": "cash",
        "references": ["ref_001"],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        # Expanded 11
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["NONE"],
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [
            {"field_name": "transaction_type", "value": "PURCHASE",
             "confidence": "0.95", "grounding": "GROUNDED", "source_text": "Purchased goods", "reasoning": "explicit keyword"},
            {"field_name": "parties", "value": "['Sharma Traders']",
             "confidence": "0.90", "grounding": "GROUNDED", "source_text": "from Sharma Traders", "reasoning": "from marker"},
        ],
        "overall_confidence": "0.93",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION", "SINGLE_AUTHORITY"],
    }
    report = validate_structured_interpretation(record)
    check("expanded_complete_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")
    check("expanded_complete_status", report.status == ValidationStatus.VALID)


def test_expanded_valid_minimal():
    """Expanded record with only some optional expanded fields."""
    record = {
        "transaction_type": "sale",
        "parties": ["Amit"],
        "amounts": [{"value": "15000"}],
        "payment_method": "cheque",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        # Only a few expanded fields
        "transaction_type_enum": "SALE",
        "payment_method_enum": "CHEQUE",
        "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    }
    report = validate_structured_interpretation(record)
    check("expanded_minimal_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


def test_expanded_valid_with_references():
    """Expanded record with reference fields populated."""
    record = {
        "transaction_type": "settlement",
        "parties": ["Patel Bros"],
        "amounts": [{"value": "40000"}],
        "payment_method": "bank_transfer",
        "references": ["txn_prev"],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "SETTLEMENT",
        "payment_method_enum": "BANK",
        "ambiguity_flags": ["NONE"],
        "referenced_transaction_index": 3,
        "referenced_party": "Patel Bros",
        "referenced_amount": "35000",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SETTLEMENT_CALCULATION"],
    }
    report = validate_structured_interpretation(record)
    check("expanded_with_refs_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


def test_expanded_valid_complex_confidences():
    """Expanded record with multiple field confidences and grounding states."""
    record = {
        "transaction_type": "purchase",
        "parties": ["Mehta Corp"],
        "amounts": [{"value": "25000"}],
        "payment_method": "",
        "references": [],
        "ambiguities": ["missing_payment_mode"],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "UNKNOWN",
        "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
        "field_confidences": [
            {"field_name": "transaction_type", "value": "PURCHASE", "confidence": "0.95", "grounding": "GROUNDED"},
            {"field_name": "payment_method", "value": "UNKNOWN", "confidence": "0.10", "grounding": "UNRESOLVED"},
        ],
        "overall_confidence": "0.53",
        "safety_flags": ["LOW_CONFIDENCE", "UNRESOLVED_FIELDS"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }
    report = validate_structured_interpretation(record)
    check("expanded_complex_valid", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


test_expanded_valid_complete()
test_expanded_valid_minimal()
test_expanded_valid_with_references()
test_expanded_valid_complex_confidences()


# ---------------------------------------------------------------------------
# REJECTION: Invalid records
# ---------------------------------------------------------------------------
print("\n═══ REJECTION CASES ═══")


def test_reject_malformed_json():
    report = validate_structured_interpretation("{invalid json!!!")
    check("reject_malformed_json", not report.valid)
    check("reject_malformed_json_status", report.status == ValidationStatus.MALFORMED_JSON)


def test_reject_null():
    report = validate_structured_interpretation(None)
    check("reject_null", not report.valid)
    check("reject_null_status", report.status == ValidationStatus.TYPE_ERROR)


def test_reject_list():
    report = validate_structured_interpretation([1, 2, 3])
    check("reject_list", not report.valid)
    check("reject_list_status", report.status == ValidationStatus.TYPE_ERROR)


def test_reject_unknown_field():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "receipt_number": "X123",  # UNKNOWN
    }
    report = validate_structured_interpretation(record)
    check("reject_unknown_field", not report.valid)
    check("reject_unknown_field_status", report.status == ValidationStatus.UNKNOWN_FIELD)


def test_reject_wrong_type_parties():
    record = {
        "transaction_type": "purchase",
        "parties": "not_a_list",  # WRONG: should be list
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    report = validate_structured_interpretation(record)
    check("reject_wrong_type_parties", not report.valid)


def test_reject_wrong_type_amounts_element():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": ["not_a_dict"],  # WRONG: should be list[dict]
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    report = validate_structured_interpretation(record)
    check("reject_wrong_type_amounts_element", not report.valid)


def test_reject_invalid_tx_enum():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "INVALID_TYPE",  # NOT A VALID ENUM
    }
    report = validate_structured_interpretation(record)
    check("reject_invalid_tx_enum", not report.valid)


def test_reject_invalid_pm_enum():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "payment_method_enum": "BITCOIN",  # NOT A VALID ENUM
    }
    report = validate_structured_interpretation(record)
    check("reject_invalid_pm_enum", not report.valid)


def test_reject_invalid_ambiguity_flag():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "ambiguity_flags": ["MADE_UP_FLAG"],  # NOT VALID
    }
    report = validate_structured_interpretation(record)
    check("reject_invalid_ambiguity_flag", not report.valid)


def test_reject_invalid_safety_flag():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "safety_flags": ["FAKE_SAFETY"],  # NOT VALID
    }
    report = validate_structured_interpretation(record)
    check("reject_invalid_safety_flag", not report.valid)


def test_reject_invalid_scope_flag():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "scope_flags": ["NONEXISTENT_SCOPE"],  # NOT VALID
    }
    report = validate_structured_interpretation(record)
    check("reject_invalid_scope_flag", not report.valid)


def test_reject_confidence_out_of_range():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "overall_confidence": "1.5",  # OUT OF RANGE
    }
    report = validate_structured_interpretation(record)
    check("reject_confidence_out_of_range", not report.valid)


def test_reject_confidence_invalid_decimal():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "overall_confidence": "not_a_number",  # INVALID DECIMAL
    }
    report = validate_structured_interpretation(record)
    check("reject_confidence_invalid_decimal", not report.valid)


def test_reject_field_confidence_missing_field_name():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "field_confidences": [
            {"confidence": "0.9", "grounding": "GROUNDED"},  # MISSING field_name
        ],
    }
    report = validate_structured_interpretation(record)
    check("reject_fc_missing_field_name", not report.valid)


def test_reject_field_confidence_bad_grounding():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "field_confidences": [
            {"field_name": "tx", "confidence": "0.9", "grounding": "MAGIC"},  # INVALID
        ],
    }
    report = validate_structured_interpretation(record)
    check("reject_fc_bad_grounding", not report.valid)


def test_reject_missing_all_legacy_fields():
    """Record with only expanded fields and no legacy fields."""
    record = {
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["NONE"],
    }
    report = validate_structured_interpretation(record)
    check("reject_no_legacy_fields", not report.valid)


def test_reject_expanded_when_legacy_only():
    """Reject expanded record when allow_expanded=False."""
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "PURCHASE",
    }
    report = validate_structured_interpretation(record, allow_expanded=False)
    check("reject_expanded_legacy_only_mode", not report.valid)


test_reject_malformed_json()
test_reject_null()
test_reject_list()
test_reject_unknown_field()
test_reject_wrong_type_parties()
test_reject_wrong_type_amounts_element()
test_reject_invalid_tx_enum()
test_reject_invalid_pm_enum()
test_reject_invalid_ambiguity_flag()
test_reject_invalid_safety_flag()
test_reject_invalid_scope_flag()
test_reject_confidence_out_of_range()
test_reject_confidence_invalid_decimal()
test_reject_field_confidence_missing_field_name()
test_reject_field_confidence_bad_grounding()
test_reject_missing_all_legacy_fields()
test_reject_expanded_when_legacy_only()


# ---------------------------------------------------------------------------
# COMPATIBILITY: Legacy → Expanded normalization
# ---------------------------------------------------------------------------
print("\n═══ COMPATIBILITY CASES ═══")


def test_classify_legacy():
    record = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    check("classify_legacy", classify_record(record) == "LEGACY")


def test_classify_expanded():
    record = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "PURCHASE",
    }
    check("classify_expanded", classify_record(record) == "EXPANDED")


def test_classify_invalid():
    check("classify_invalid_none", classify_record(None) == "INVALID")
    check("classify_invalid_list", classify_record([1]) == "INVALID")
    check("classify_invalid_missing_fields", classify_record({"tx": "x"}) == "INVALID")


def test_normalize_legacy_to_expanded():
    legacy = {
        "transaction_type": "purchase",
        "parties": ["Sharma Traders"],
        "amounts": [{"value": "50000", "currency": "INR"}],
        "payment_method": "cash",
        "references": ["ref_001"],
        "ambiguities": ["missing_payment_mode"],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]},
    }
    expanded = normalize_legacy_to_expanded(legacy)

    check("norm_tx_preserved", expanded.transaction_type == "purchase")
    check("norm_parties_preserved", expanded.parties == ["Sharma Traders"])
    check("norm_amounts_preserved", expanded.amounts == [{"value": "50000", "currency": "INR"}])
    check("norm_pm_preserved", expanded.payment_method == "cash")
    check("norm_refs_preserved", expanded.references == ["ref_001"])
    check("norm_ambig_preserved", expanded.ambiguities == ["missing_payment_mode"])
    check("norm_grounding_preserved", expanded.grounding == {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]})
    check("norm_tx_enum", expanded.transaction_type_enum == "PURCHASE")
    check("norm_pm_enum", expanded.payment_method_enum == "CASH")
    check("norm_ambig_flags", expanded.ambiguity_flags == ["MISSING_PAYMENT_MODE"])
    check("norm_status", expanded.suggested_status == "REVIEW_REQUIRED")
    check("norm_scope", expanded.scope_flags == ["SINGLE_TRANSACTION"])
    check("norm_no_fabricated_facts", expanded.referenced_party is None)
    check("norm_no_fabricated_amount", expanded.referenced_amount is None)


def test_normalize_expanded_to_legacy():
    expanded = ExpandedInterpretation(
        transaction_type="sale",
        parties=["Amit"],
        amounts=[{"value": "15000"}],
        payment_method="cheque",
        references=[],
        ambiguities=[],
        grounding={},
        transaction_type_enum="SALE",
        payment_method_enum="CHEQUE",
        ambiguity_flags=["NONE"],
        overall_confidence="0.95",
        suggested_status="REVIEW_REQUIRED",
        safety_flags=["NONE"],
        scope_flags=["SINGLE_TRANSACTION"],
    )
    legacy = expanded.to_legacy_dict()
    check("to_legacy_has_7_fields", set(legacy.keys()) == LEGACY_FIELDS)
    check("to_legacy_tx", legacy["transaction_type"] == "sale")
    check("to_legacy_parties", legacy["parties"] == ["Amit"])
    check("to_legacy_amounts", legacy["amounts"] == [{"value": "15000"}])
    check("to_legacy_pm", legacy["payment_method"] == "cheque")


def test_expanded_to_dict_has_18_fields():
    expanded = ExpandedInterpretation()
    d = expanded.to_dict()
    check("expanded_to_dict_18_fields", len(d) == 18, f"got {len(d)} fields")
    check("expanded_to_dict_has_all_keys", ALL_VALID_FIELDS == set(d.keys()))


def test_no_fabricated_facts_in_normalization():
    """Normalization must never invent financial facts."""
    minimal_legacy = {
        "transaction_type": "",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    expanded = normalize_legacy_to_expanded(minimal_legacy)
    check("no_fabricated_parties", expanded.parties == [])
    check("no_fabricated_amounts", expanded.amounts == [])
    check("no_fabricated_tx_enum", expanded.transaction_type_enum == "UNKNOWN")
    check("no_fabricated_pm_enum", expanded.payment_method_enum == "UNKNOWN")
    check("no_fabricated_refs", expanded.referenced_party is None)
    check("no_fabricated_amount_val", expanded.referenced_amount is None)


def test_normalize_then_validate():
    """Legacy record → normalize → validate as expanded."""
    legacy = {
        "transaction_type": "purchase",
        "parties": ["Raj"],
        "amounts": [{"value": "20000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    expanded = normalize_legacy_to_expanded(legacy)
    report = validate_structured_interpretation(expanded.to_dict())
    check("normalize_then_validate", report.valid, f"errors={[e.to_dict() for e in report.errors]}")


test_classify_legacy()
test_classify_expanded()
test_classify_invalid()
test_normalize_legacy_to_expanded()
test_normalize_expanded_to_legacy()
test_expanded_to_dict_has_18_fields()
test_no_fabricated_facts_in_normalization()
test_normalize_then_validate()


# ---------------------------------------------------------------------------
# ENUM DEFINITIONS
# ---------------------------------------------------------------------------
print("\n═══ ENUM DEFINITIONS ═══")


def test_enum_completeness():
    check("tx_enum_15_values", len(TransactionTypeEnum) == 15)
    check("pm_enum_7_values", len(PaymentMethodEnum) == 7)
    check("ambig_enum_9_values", len(AmbiguityTypeEnum) == 9)
    check("grounding_enum_4_values", len(GroundingLevel) == 4)
    check("safety_enum_9_values", len(SafetyFlag) == 9)
    check("scope_enum_10_values", len(ScopeFlag) == 10)
    check("legacy_fields_7", len(LEGACY_FIELDS) == 7)
    check("expanded_fields_11", len(EXPANDED_FIELDS) == 11)
    check("all_valid_fields_18", len(ALL_VALID_FIELDS) == 18)


test_enum_completeness()


# ---------------------------------------------------------------------------
# LEGACY SCHEMA_VERIFIER COMPATIBILITY
# ---------------------------------------------------------------------------
print("\n═══ SCHEMA_VERIFIER COMPATIBILITY ═══")


def test_verifier_legacy_still_accepted():
    record = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    report = validate_structured_interpretation(record)
    check("verifier_legacy_accepted", report.valid)


def test_verifier_expanded_still_accepted():
    record = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["NONE"],
    }
    report = validate_structured_interpretation(record)
    check("verifier_expanded_accepted", report.valid)


def test_verifier_legacy_only_mode():
    record = {
        "transaction_type": "purchase",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "transaction_type_enum": "PURCHASE",
    }
    report_legacy_only = validate_structured_interpretation(record, allow_expanded=False)
    report_both = validate_structured_interpretation(record, allow_expanded=True)
    check("verifier_legacy_only_rejects_expanded", not report_legacy_only.valid)
    check("verifier_both_accepts_expanded", report_both.valid)


def test_verifier_canonical_fields_unchanged():
    check("canonical_fields_unchanged", CANONICAL_FIELDS == {
        "transaction_type", "parties", "amounts", "payment_method",
        "references", "ambiguities", "grounding",
    })


test_verifier_legacy_still_accepted()
test_verifier_expanded_still_accepted()
test_verifier_legacy_only_mode()
test_verifier_canonical_fields_unchanged()


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
