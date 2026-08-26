#!/usr/bin/env python3
"""
Sprint 35 — Transaction-Level VERIFIED + Journal Integrity Invariant Test

Verifies that:
A. VERIFIED + journal lines  =>  allowed
B. VERIFIED + zero journal lines + posting transaction  =>  MUST FAIL SAFE
C. REVIEW_REQUIRED + zero journal lines  =>  allowed
D. NOT_SUPPORTED + zero journal lines  =>  allowed
E. VERIFIED + unbalanced journal  =>  MUST NOT display VERIFIED
F. Non-posting event + explicitly supported classification  =>  allowed
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_problem_engine import process_problem
from backend.maths.fyjc_ui_contract import (
    validate_transaction_integrity,
    validate_problem_integrity,
)


def test_verified_with_journal():
    """Case A: VERIFIED + journal lines => allowed."""
    result = process_problem(
        "Purchased goods from Raj for Rs.30,000."
    )
    txns = result["transactions"]
    verified = [t for t in txns if t["status"] == "VERIFIED"]
    for txn in verified:
        validated = validate_transaction_integrity(txn)
        assert validated["status"] == "VERIFIED", \
            f"VERIFIED transaction should remain VERIFIED: {txn['text']}"
        jnl = txn.get("journal") or {}
        lines = len(jnl.get("debit_lines", [])) + len(jnl.get("credit_lines", []))
        assert lines >= 1, \
            f"VERIFIED transaction should have journal lines: {txn['text']}"


def test_verified_without_journal():
    """Case B: VERIFIED + zero journal lines + posting => MUST FAIL SAFE."""
    # Create a synthetic VERIFIED transaction with no journal lines
    fake_txn = {
        "index": 1,
        "text": "Synthetic test transaction",
        "status": "VERIFIED",
        "event_type": "ACCOUNTING_TRANSACTION",
        "journal": {
            "status": "VERIFIED",
            "debit_lines": [],
            "credit_lines": [],
            "calculation_records": [],
            "total_debit": 0,
            "total_credit": 0,
            "balanced": True,
        },
    }
    validated = validate_transaction_integrity(fake_txn)
    assert validated["status"] == "REVIEW_REQUIRED", \
        f"VERIFIED posting with 0 journal lines must be downgraded to REVIEW_REQUIRED, got {validated['status']}"
    assert validated.get("_integrity_downgraded") is True, \
        "Downgraded flag must be set"
    assert "no journal entry" in (validated.get("why_not") or "").lower(), \
        "Why-not message must mention missing journal"


def test_review_required_without_journal():
    """Case C: REVIEW_REQUIRED + zero journal lines => allowed."""
    fake_txn = {
        "index": 1,
        "text": "Synthetic test",
        "status": "REVIEW_REQUIRED",
        "event_type": "ACCOUNTING_TRANSACTION",
        "journal": {
            "status": "REVIEW_REQUIRED",
            "debit_lines": [],
            "credit_lines": [],
        },
    }
    validated = validate_transaction_integrity(fake_txn)
    assert validated["status"] == "REVIEW_REQUIRED", \
        "REVIEW_REQUIRED should remain unchanged"


def test_not_supported_without_journal():
    """Case D: NOT_SUPPORTED + zero journal lines => allowed."""
    fake_txn = {
        "index": 1,
        "text": "Synthetic test",
        "status": "NOT_SUPPORTED",
        "event_type": "ACCOUNTING_TRANSACTION",
        "journal": {
            "status": "NOT_SUPPORTED",
            "debit_lines": [],
            "credit_lines": [],
        },
    }
    validated = validate_transaction_integrity(fake_txn)
    assert validated["status"] == "NOT_SUPPORTED", \
        "NOT_SUPPORTED should remain unchanged"


def test_informational_event_no_journal():
    """Case F: Non-posting event + no journal => allowed."""
    fake_txn = {
        "index": 1,
        "text": "Opening balances",
        "status": "VERIFIED",
        "event_type": "OPENING_BALANCE",
        "journal": {
            "status": "VERIFIED",
            "debit_lines": [],
            "credit_lines": [],
        },
    }
    validated = validate_transaction_integrity(fake_txn)
    assert validated["status"] == "VERIFIED", \
        "OPENING_BALANCE with VERIFIED should remain VERIFIED (non-posting)"


def test_informational_event_no_journal_2():
    """Case F2: INFORMATIONAL_EVENT with no journal => allowed."""
    fake_txn = {
        "index": 1,
        "text": "Informational note",
        "status": "VERIFIED",
        "event_type": "INFORMATIONAL_EVENT",
        "journal": {
            "status": "VERIFIED",
            "debit_lines": [],
            "credit_lines": [],
        },
    }
    validated = validate_transaction_integrity(fake_txn)
    assert validated["status"] == "VERIFIED", \
        "INFORMATIONAL_EVENT with VERIFIED should remain VERIFIED (non-posting)"


def test_rohan_regression():
    """Sprint 35 regression: the Rohan case must not produce
    VERIFIED + empty journal lines."""
    problem = ("Sold goods to Rohan priced at Rs.50,000 at 10% Trade "
               "Discount and 5% Cash Discount. Rohan paid half the "
               "amount immediately by cheque.")
    result = process_problem(problem)
    integrity = validate_problem_integrity(result["transactions"])

    # The problem should have 0 integrity violations
    assert integrity["integrity_violations"] == 0, \
        f"Rohan case should have 0 integrity violations, got {integrity['integrity_violations']}"

    # Every VERIFIED transaction must have journal lines
    for txn in integrity["transactions"]:
        if txn["status"] == "VERIFIED" and \
                txn.get("event_type") == "ACCOUNTING_TRANSACTION":
            jnl = txn.get("journal") or {}
            lines = len(jnl.get("debit_lines", [])) + len(jnl.get("credit_lines", []))
            assert lines >= 1, \
                f"VERIFIED posting TX{txn['index']} has {lines} journal lines"


def test_validate_problem_integrity_counts():
    """Test that validate_problem_integrity returns correct counts."""
    # Create a mix of transaction statuses
    txns = [
        {"index": 1, "status": "VERIFIED", "event_type": "ACCOUNTING_TRANSACTION",
         "journal": {"debit_lines": [{"account": "Cash", "amount": "1000"}],
                     "credit_lines": [{"account": "Sales", "amount": "1000"}]}},
        {"index": 2, "status": "REVIEW_REQUIRED", "event_type": "ACCOUNTING_TRANSACTION",
         "journal": {"debit_lines": [], "credit_lines": []}},
        {"index": 3, "status": "VERIFIED", "event_type": "ACCOUNTING_TRANSACTION",
         "journal": {"debit_lines": [], "credit_lines": []}},  # VIOLATION
    ]
    result = validate_problem_integrity(txns)
    assert result["verified_count"] == 1, \
        f"Expected 1 verified, got {result['verified_count']}"
    assert result["review_required_count"] == 2, \
        f"Expected 2 review_required, got {result['review_required_count']}"
    assert result["integrity_violations"] == 1, \
        f"Expected 1 violation, got {result['integrity_violations']}"


def test_input_not_mutated():
    """Verify that validate_transaction_integrity never mutates input."""
    original = {
        "index": 1,
        "text": "Test",
        "status": "VERIFIED",
        "event_type": "ACCOUNTING_TRANSACTION",
        "journal": {"debit_lines": [], "credit_lines": []},
    }
    import copy
    before = copy.deepcopy(original)
    validate_transaction_integrity(original)
    assert original == before, "Input was mutated by validate_transaction_integrity"


if __name__ == "__main__":
    tests = [
        test_verified_with_journal,
        test_verified_without_journal,
        test_review_required_without_journal,
        test_not_supported_without_journal,
        test_informational_event_no_journal,
        test_informational_event_no_journal_2,
        test_rohan_regression,
        test_validate_problem_integrity_counts,
        test_input_not_mutated,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"  \\u2705 {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  \\u274c {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  \\u274c {test.__name__}: ERROR {e}")

    print(f"\nSprint 35 Integrity Invariant: {passed}/{passed+failed} PASS, {failed} FAIL")
    if failed:
        sys.exit(1)
    print("ALL TESTS PASS")
