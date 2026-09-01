#!/usr/bin/env python3
"""
Platrixa — Schema Verifier Integration Test
=============================================

Quick validation that the schema_verifier module compiles and basic imports work.
For comprehensive testing, see:
  - scripts/fte_fyjc_41_contract_unit_tests.py (synthetic tests)
  - scripts/fte_fyjc_41_real_dataset_contract_test.py (real FYJC dataset regression)
"""

import sys
import os

sys.path.insert(0, os.getcwd())

try:
    from backend.maths.schema_verifier import (
        validate_structured_interpretation,
        StructuredInterpretationValidator,
        ValidationStatus,
        CANONICAL_FIELDS,
    )
    print("✓ Module imports successful")
    print(f"✓ Canonical fields: {CANONICAL_FIELDS}")
    
    # Quick smoke test
    valid_output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
    }
    
    report = validate_structured_interpretation(valid_output)
    assert report.valid, f"Valid output should pass: {report.errors}"
    print("✓ Valid output accepted")
    
    # Quick rejection test
    invalid_output = {
        "transaction_type": "purchase",
        "parties": ["Vendor"],
        "amounts": [{"value": "1000"}],
        "payment_method": "cash",
        "references": [],
        "ambiguities": [],
        "grounding": {},
        "receipt_number": "UNKNOWN_FIELD",
    }
    
    report = validate_structured_interpretation(invalid_output)
    assert not report.valid, "Invalid output should fail"
    assert report.status == ValidationStatus.UNKNOWN_FIELD
    print("✓ Unknown fields rejected correctly")
    
    print("\n✅ Integration test PASSED")
    sys.exit(0)
    
except Exception as e:
    print(f"✗ Integration test FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
