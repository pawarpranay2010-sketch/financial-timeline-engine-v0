#!/usr/bin/env python3
"""
Platrixa — Independent Raw-Text Grounding Verifier

This executable regression gate proves that grounding is derived independently
from the original text and that unsupported values are refused before they can
enter the deterministic Truth Kernel.

Exit code 0 = all checks pass.
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.grounding_verifier import (  # noqa: E402
    GroundingStatus,
    GroundingVerifier,
)

FAILURES = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    if not ok:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL [{name}] {detail}")
    else:
        print(f"OK [{name}]")


def test_explicit_and_normalized_values():
    raw = "Purchased goods from Raj for Rs.20000 by cheque."
    interpretation = {
        "transaction_type": "PURCHASE",
        "party": "Raj",
        "amount": 20000,
        "payment_method": "CHEQUE",
    }

    report = GroundingVerifier().verify(raw, interpretation)
    check("explicit-normalized accepted", report.accepted)
    check(
        "transaction normalized",
        report.fields["transaction_type"].status == GroundingStatus.NORMALIZED,
    )
    check(
        "party explicit",
        report.fields["party"].status == GroundingStatus.EXPLICIT,
    )
    check(
        "amount normalized",
        report.fields["amount"].status == GroundingStatus.NORMALIZED,
    )
    check(
        "payment method explicit",
        report.fields["payment_method"].status == GroundingStatus.EXPLICIT,
    )


def test_relative_amount_is_not_concrete():
    raw = "Paid Raj half the remaining balance by cheque,"
    interpretation = {
        "transaction_type": {
            "value": "PAYMENT",
            "grounding": "NORMALIZED",
        },
        "party": "Raj",
        "amount": {
            "value": 10000,
            "grounding": "EXPLICIT",
        },
        "amount_expression": "half the remaining balance",
        "payment_method": "CHEQUE",
    }

    report = GroundingVerifier().verify(raw, interpretation)
    amount = report.fields["amount"]
    check("relative amount is inferred", amount.status == GroundingStatus.INFERRED)
    check("inferred amount is ungrounded", not amount.grounded)
    check("unsupported amount rejected", not report.accepted)
    check(
        "incorrect AI explicit label reported",
        any("AI claimed EXPLICIT" in violation for violation in report.violations),
    )
    check(
        "relative expression explicit",
        report.fields["amount_expression"].status == GroundingStatus.EXPLICIT,
    )


def test_absent_and_expected_fields():
    verifier = GroundingVerifier()
    report = verifier.verify(
        "Paid Raj half the remaining balance by cheque,",
        {
            "transaction_type": "PAYMENT",
            "party": "Raj",
            "amount": None,
            "amount_expression": "half the remaining balance",
            "payment_method": "CHEQUE",
        },
    )
    check("missing value is absent", report.fields["amount"].status == GroundingStatus.ABSENT)
    check("absent value does not reject", report.accepted)

    missing = verifier.verify(
        "Paid Raj by cheque.",
        {"party": "Raj"},
        expected_fields=("amount", "payment_method"),
    )
    check("missing amount is absent", missing.fields["amount"].status == GroundingStatus.ABSENT)
    check(
        "missing payment method is absent",
        missing.fields["payment_method"].status == GroundingStatus.ABSENT,
    )


def test_ai_label_never_controls_derivation():
    report = GroundingVerifier().verify(
        "Paid Raj half the remaining balance by cheque,",
        {
            "amount": {
                "value": 10000,
                "grounding": "NORMALIZED",
            }
        },
    )
    check("AI label cannot upgrade amount", report.fields["amount"].status == GroundingStatus.INFERRED)
    check("AI-label mismatch rejects report", not report.accepted)


def main():
    test_explicit_and_normalized_values()
    test_relative_amount_is_not_concrete()
    test_absent_and_expected_fields()
    test_ai_label_never_controls_derivation()

    print(f"\n{TOTAL[0] - len(FAILURES)}/{TOTAL[0]} checks passed")
    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
