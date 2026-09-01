#!/usr/bin/env python3
"""
Platrixa — StructuredInterpretation Contract Unit Tests (Sprint 41)
===================================================================

Synthetic unit tests for the schema_verifier validator.
Tests all valid StructuredInterpretation variants + malformed cases.

These are FAST unit tests. Real dataset regression is in:
  scripts/fte_fyjc_41_real_dataset_contract_test.py
"""

import sys
import os

sys.path.insert(0, os.getcwd())

from backend.maths.schema_verifier import (  # noqa: E402
    validate_structured_interpretation,
    ValidationStatus,
)

PASSED = 0
FAILED = 0
TOTAL = 0


def test_case(name: str, output: dict, expect_valid: bool, expect_status: str = None):
    """Run one test case."""
    global PASSED, FAILED, TOTAL
    TOTAL += 1

    report = validate_structured_interpretation(output)
    passed = report.valid == expect_valid
    if expect_status and report.status.value != expect_status:
        passed = False

    status_str = "✓" if passed else "✗"
    print(f"{status_str} {name}")
    if not passed:
        print(f"    Expected: valid={expect_valid}, status={expect_status}")
        print(f"    Got:      valid={report.valid}, status={report.status.value}")
        if report.errors:
            for err in report.errors[:3]:
                print(f"      {err.field}: {err.issue}")
        FAILED += 1
    else:
        PASSED += 1


# =============================================================================
# VALID CASES (all 7 fields, empty, partial)
# =============================================================================

def test_valid_complete():
    """All 7 fields present and valid."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor Inc."],
        "amounts": [{"value": "10000", "currency": "INR"}],
        "payment_method": "cash",
        "references": ["order_123"],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    }
    test_case("VALID_all_7_fields", output, expect_valid=True, expect_status="VALID")


def test_valid_minimal():
    """Only transaction_type, rest empty."""
    output = {
        "transaction_type": "sale",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "references": [],
        "ambiguities": [],
    }
    test_case("VALID_minimal_fields", output, expect_valid=True, expect_status="VALID")


def test_valid_no_optional():
    """Only required-looking fields, no grounding."""
    output = {
        "transaction_type": "expense",
        "parties": ["Landlord"],
        "amounts": [{"value": "5000"}],
        "payment_method": "bank_transfer",
    }
    test_case("VALID_no_grounding", output, expect_valid=True, expect_status="VALID")


def test_valid_complex_amounts():
    """Multiple amounts with various keys."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Supplier A", "Supplier B"],
        "amounts": [
            {"value": "1000", "currency": "INR", "source": "explicit"},
            {"value": "500", "currency": "INR", "source": "inferred"},
        ],
        "payment_method": "cheque",
        "references": ["prev_transaction", "balance_due"],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": ["payment_method"]},
    }
    test_case("VALID_complex_amounts", output, expect_valid=True, expect_status="VALID")


def test_valid_json_string():
    """Input as JSON string (common in datasets)."""
    import json
    output_dict = {
        "transaction_type": "settlement",
        "parties": ["Customer"],
        "amounts": [{"value": "2000"}],
        "payment_method": "credit",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    output_str = json.dumps(output_dict)
    test_case("VALID_json_string_input", output_str, expect_valid=True, expect_status="VALID")


# =============================================================================
# UNKNOWN FIELD CASES (CRITICAL: must reject)
# =============================================================================

def test_unknown_field_receipt_number():
    """Unknown field: receipt_number."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "receipt_number": "RCP-12345",  # ← NOT IN CONTRACT
    }
    test_case("REJECT_unknown_receipt_number", output, expect_valid=False, expect_status="UNKNOWN_FIELD")


def test_unknown_field_vendor_id():
    """Unknown field: vendor_id."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "vendor_id": "V-001",  # ← NOT IN CONTRACT
    }
    test_case("REJECT_unknown_vendor_id", output, expect_valid=False, expect_status="UNKNOWN_FIELD")


def test_unknown_field_customer_name():
    """Unknown field: customer_name."""
    output = {
        "transaction_type": "sale",
        "parties": ["Customer A"],
        "amounts": [{"value": "5000"}],
        "payment_method": "cheque",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "customer_name": "John Doe",  # ← NOT IN CONTRACT
    }
    test_case("REJECT_unknown_customer_name", output, expect_valid=False, expect_status="UNKNOWN_FIELD")


# =============================================================================
# TYPE ERRORS
# =============================================================================

def test_type_error_parties_string():
    """Type error: parties is string, not list."""
    output = {
        "transaction_type": "purchase",
        "parties": "Vendor",  # ← Should be list[str]
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
    }
    test_case("REJECT_parties_string", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_parties_numbers():
    """Type error: parties list contains numbers."""
    output = {
        "transaction_type": "purchase",
        "parties": [123, 456],  # ← Should be list[str]
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
    }
    test_case("REJECT_parties_numbers", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_amounts_string():
    """Type error: amounts is string, not list."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": "1000",  # ← Should be list[dict]
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
    }
    test_case("REJECT_amounts_string", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_amounts_list_strings():
    """Type error: amounts is list[str], not list[dict]."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": ["1000", "500"],  # ← Should be list[dict]
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
    }
    test_case("REJECT_amounts_list_strings", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_amounts_list_numbers():
    """Type error: amounts is list[number], not list[dict]."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [1000, 500],  # ← Should be list[dict]
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
    }
    test_case("REJECT_amounts_list_numbers", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_references_string():
    """Type error: references is string, not list."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": "order_123",  # ← Should be list[str]
        "ambiguities": [],
    }
    test_case("REJECT_references_string", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_ambiguities_string():
    """Type error: ambiguities is string, not list."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": "payment_ambiguous",  # ← Should be list[str]
    }
    test_case("REJECT_ambiguities_string", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_type_error_grounding_list():
    """Type error: grounding is list, not dict."""
    output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": ["explicit"],  # ← Should be dict
    }
    test_case("REJECT_grounding_list", output, expect_valid=False, expect_status="TYPE_ERROR")


# =============================================================================
# MALFORMED JSON
# =============================================================================

def test_malformed_json_syntax():
    """Malformed: invalid JSON string."""
    output = "{not valid json"
    test_case("REJECT_malformed_json", output, expect_valid=False, expect_status="MALFORMED_JSON")


def test_malformed_not_dict():
    """Malformed: output is list, not dict."""
    output = ["transaction_type", "purchase"]
    test_case("REJECT_not_dict_is_list", output, expect_valid=False, expect_status="TYPE_ERROR")


def test_malformed_null():
    """Malformed: output is null."""
    output = None
    test_case("REJECT_null_input", output, expect_valid=False, expect_status="TYPE_ERROR")


# =============================================================================
# Main
# =============================================================================

def main():
    """Run all tests."""
    print("=" * 70)
    print("StructuredInterpretation Contract Unit Tests (Sprint 41)")
    print("=" * 70)
    print()

    print("VALID CASES (should all PASS)")
    print("-" * 70)
    test_valid_complete()
    test_valid_minimal()
    test_valid_no_optional()
    test_valid_complex_amounts()
    test_valid_json_string()
    print()

    print("UNKNOWN FIELD CASES (should all FAIL)")
    print("-" * 70)
    test_unknown_field_receipt_number()
    test_unknown_field_vendor_id()
    test_unknown_field_customer_name()
    print()

    print("TYPE ERROR CASES (should all FAIL)")
    print("-" * 70)
    test_type_error_parties_string()
    test_type_error_parties_numbers()
    test_type_error_amounts_string()
    test_type_error_amounts_list_strings()
    test_type_error_amounts_list_numbers()
    test_type_error_references_string()
    test_type_error_ambiguities_string()
    test_type_error_grounding_list()
    print()

    print("MALFORMED JSON CASES (should all FAIL)")
    print("-" * 70)
    test_malformed_json_syntax()
    test_malformed_not_dict()
    test_malformed_null()
    print()

    # Summary
    print("=" * 70)
    print(f"RESULTS: {PASSED}/{TOTAL} passed, {FAILED}/{TOTAL} failed")
    print("=" * 70)

    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
