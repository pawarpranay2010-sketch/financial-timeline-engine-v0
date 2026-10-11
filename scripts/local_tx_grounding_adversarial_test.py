#!/usr/bin/env python3
"""Adversarial transaction grounding tests: negation, chronology and event status."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fte_sec_01_grounding_fail_closed_test import grounded_baseline
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate


def evaluate(source, transaction_type="Payment", transaction_type_enum="PAYMENT"):
    candidate = grounded_baseline(
        transaction_type=transaction_type,
        transaction_type_enum=transaction_type_enum,
        parties=[],
        amounts=[],
        payment_method="UNKNOWN",
        payment_method_enum="UNKNOWN",
    )
    return ExpandedGroundingGate().ground(candidate, source)


CASES = [
    # Explicit negation of transaction verbs
    ("purchase: not purchased", "The machinery was not purchased.", "Purchase", "PURCHASE", False),
    ("purchase: never bought", "The machinery was never bought.", "Purchase", "PURCHASE", False),
    ("purchase: not procured", "The equipment was not procured.", "Purchase", "PURCHASE", False),
    ("purchase: not acquired", "The equipment was not acquired.", "Purchase", "PURCHASE", False),
    ("sale: not sold", "The goods were not sold.", "Sale", "SALE", False),
    ("sale: never supplied", "The goods were never supplied.", "Sale", "SALE", False),
    ("sale: not delivered", "The goods were not delivered.", "Sale", "SALE", False),
    ("receipt: not received", "The customer payment was not received.", "Receipt", "RECEIPT", False),
    ("receipt: no receipt", "No receipt was recorded.", "Receipt", "RECEIPT", False),
    ("return: not returned", "The goods were not returned.", "Return", "RETURN", False),
    ("return: never executed", "The purchase return was never executed.", "Return", "RETURN", False),
    ("capital: not invested", "Capital was not invested.", "Capital", "CAPITAL", False),
    ("drawing: did not withdraw", "The proprietor did not withdraw funds.", "Drawing", "DRAWING", False),

    # Positive evidence followed by a later negative statement
    ("purchase then cancellation", "The machinery was purchased, but the purchase was later cancelled.", "Purchase", "PURCHASE", True),
    ("sale then cancellation", "The goods were sold, but the sale was later cancelled.", "Sale", "SALE", True),
    ("payment then reversal", "The supplier was paid, but the payment was later reversed.", "Payment", "PAYMENT", True),

    # Negative first, then clear later completion
    ("purchase later completed", "The machinery was not purchased initially, but it was purchased later.", "Purchase", "PURCHASE", True),
    ("sale later completed", "The goods were not sold initially, but they were sold later.", "Sale", "SALE", True),
    ("receipt later received", "The payment was not received initially, but it was received later.", "Receipt", "RECEIPT", True),
    ("payment later completed", "The supplier was not paid initially, but payment was completed later.", "Payment", "PAYMENT", True),

    # Cancellation and proposed transactions without completion
    ("cancelled purchase only", "The proposed machinery purchase was cancelled before completion.", "Purchase", "PURCHASE", False),
    ("proposed sale only", "The proposed equipment sale has not been completed.", "Sale", "SALE", False),
    ("scheduled payment only", "The supplier payment is scheduled for tomorrow and has not been made.", "Payment", "PAYMENT", False),
    ("expected receipt only", "A customer receipt is expected but has not been received.", "Receipt", "RECEIPT", False),
    ("return discussed only", "A purchase return was discussed but never executed.", "Return", "RETURN", False),

    # Positive controls
    ("positive purchase", "The machinery was purchased for business use.", "Purchase", "PURCHASE", True),
    ("positive sale", "The goods were sold to the customer.", "Sale", "SALE", True),
    ("positive supply", "The goods were supplied to the customer.", "Sale", "SALE", True),
    ("positive receipt", "The customer payment was received.", "Receipt", "RECEIPT", True),
    ("positive return", "The goods were returned by the customer.", "Return", "RETURN", True),
    ("positive capital", "The proprietor invested capital in the business.", "Capital", "CAPITAL", True),
    ("positive drawing", "The proprietor withdrew funds for personal use.", "Drawing", "DRAWING", True),
    ("positive payment", "Paid Rs. 1,250 in cash for office stationery.", "Payment", "PAYMENT", True),
]


def main():
    failures = 0

    for name, source, tx_type, tx_enum, expected in CASES:
        result = evaluate(source, tx_type, tx_enum)
        actual = result.grounded and result.safe_for_kernel
        passed = actual == expected

        print(
            f"[{'PASS' if passed else 'FAIL'}] {name} "
            f"| expected={expected} actual={actual} "
            f"| issues={result.issues}"
        )

        if not passed:
            failures += 1

    print(f"\nResult: {len(CASES) - failures}/{len(CASES)} passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
