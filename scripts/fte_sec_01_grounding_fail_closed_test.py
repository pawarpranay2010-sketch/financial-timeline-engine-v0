#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: GROUNDING GATE FAIL-CLOSED (fte_sec_01)
=====================================================================

Captures two defects found in the 2026-09-29 security audit
(reports/security_audit_2026-09-29.md) BEFORE any fix is applied.

  M-01  backend/maths/fyjc_grounding_gate.py Rule 5 fails OPEN on
        transaction_type. The unverified branch appends a
        FieldGrounding(grounded=False) to ``field_results`` but NEVER
        appends to ``issues``. Aggregation is ``grounded = len(issues)==0``,
        so the gate can report:

            FieldGrounding(grounded == False)      <- the honest record
            overall  grounded == True             <- the aggregate
            safe_for_kernel == True

        i.e. the gate contradicts ITSELF and admits an unverifiable
        transaction_type to the deterministic kernel. Every other rule
        (parties, amounts, payment method, references) correctly appends
        to ``issues``; Rule 5 is the sole exception.

  M-02  ``_amount_in_text`` uses ``val in text_digits_only`` — pure
        substring containment on the digits of the source text. Against
        "Rs. 90,000" the candidate amounts "90" (1000x understatement),
        "0" and "9,000" (a plausible-looking 10x error) ALL ground clean,
        because each is a digit-substring of "90000". A smaller or
        different numeric substring must never be treated as proof that
        the candidate amount appears in the source.

THE SECURITY PROPERTY UNDER TEST
-------------------------------
  If ANY field is recorded as ungrounded, the aggregate MUST be
  ungrounded and ``safe_for_kernel`` MUST be false. The gate must never
  report safe_for_kernel=True while its own field_results contain an
  ungrounded field.

  Separately: a candidate amount may only ground when it denotes the same
  magnitude as a number actually present in the source.

NOTE ON FIXTURE DISCIPLINE
-------------------------
The baseline candidate below is verified schema-VALID
(StructuredInterpretationValidator -> valid=True) and grounds clean. It
uses ``suggested_status="REVIEW_REQUIRED"`` because a candidate claiming
VERIFIED trips grounding Rule 0 and would mask every other assertion.

These tests are EXPECTED TO FAIL on the current tree. They define what
"fixed" means for M-01 and M-02. Do not weaken them to match current
behaviour — that is the defect they exist to catch.

Run:  python3 scripts/fte_sec_01_grounding_fail_closed_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate  # noqa: E402

# ---------------------------------------------------------------------------
# Shared check machinery (house style, mirrors fte_fyjc_57 / fte_fyjc_59)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_MESSAGES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def expected_failure(name: str, detail: str) -> None:
    """Record a defect that is KNOWN to fail until the fix lands."""
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {name} — {detail}")
    _MESSAGES.append(f"{name} — {detail}")


def note(text: str) -> None:
    """Non-asserting observation, so a gap is documented, not invented."""
    print(f"  [NOTE] {text}")


# ---------------------------------------------------------------------------
# Fixtures — schema-valid and fully grounded against SOURCE
# ---------------------------------------------------------------------------

CANONICAL_18 = (
    "transaction_type", "parties", "amounts", "payment_method", "references",
    "ambiguities", "grounding", "transaction_type_enum", "payment_method_enum",
    "ambiguity_flags", "field_confidences", "overall_confidence",
    "referenced_transaction_index", "referenced_party", "referenced_amount",
    "safety_flags", "scope_flags", "suggested_status",
)

# Contains "Bought" (a PURCHASE keyword) and "cash", but no sale language.
SOURCE = "Bought machinery for Rs. 90,000 cash from Iyer and Co."


def grounded_baseline(**overrides: Any) -> Dict[str, Any]:
    """Schema-valid candidate whose every field is supported by SOURCE."""
    candidate: Dict[str, Any] = {
        "transaction_type": "Purchase of machinery",
        "transaction_type_enum": "PURCHASE",
        "parties": ["Iyer and Co"],
        "amounts": [{"value": "90000", "currency": "INR", "source": "explicit"}],
        "payment_method": "Cash",
        "payment_method_enum": "CASH",
        "references": [],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        "ambiguity_flags": [],
        "field_confidences": [],
        "overall_confidence": "0.50",
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
        # The model may never claim VERIFIED (grounding Rule 0).
        "suggested_status": "REVIEW_REQUIRED",
    }
    candidate.update(overrides)
    return candidate


# ---------------------------------------------------------------------------
# A. Baseline sanity — the control must ground clean and be schema-valid.
#    Without this, a failing M-01 test could be blamed on a broken fixture.
# ---------------------------------------------------------------------------


def test_baseline_is_grounded() -> None:
    print("\n--- A. Baseline control (must PASS today) ---")
    from backend.maths.schema_verifier import StructuredInterpretationValidator

    gate = ExpandedGroundingGate()
    candidate = grounded_baseline()

    report = StructuredInterpretationValidator().validate(candidate)
    check("A0 baseline candidate is schema-VALID",
          bool(getattr(report, "valid", False)),
          f"status={getattr(report, 'status', 'n/a')}")

    result = gate.ground(candidate, SOURCE)
    check("A1 baseline candidate grounds clean", result.grounded is True,
          f"issues={result.issues}")
    check("A2 baseline is safe_for_kernel", result.safe_for_kernel is True)
    check("A3 baseline reports no issues", not result.issues,
          f"issues={result.issues}")

    ungrounded = [f.field_name for f in result.field_results if not f.grounded]
    check("A4 no baseline field is ungrounded", not ungrounded,
          f"ungrounded={ungrounded}")


# ---------------------------------------------------------------------------
# B. M-01 — fabricated transaction_type must fail closed
# ---------------------------------------------------------------------------


def test_m01_transaction_type_fails_closed() -> None:
    print("\n--- B. M-01: fabricated transaction_type (EXPECTED TO FAIL today) ---")
    gate = ExpandedGroundingGate()

    # "SALE" is a legal enum but conflicts with "Bought" in SOURCE. Every
    # other field is genuinely grounded, so transaction_type is the ONLY
    # possible source of a grounding issue.
    candidate = grounded_baseline(
        transaction_type="Sale of goods",
        transaction_type_enum="SALE",
    )
    result = gate.ground(candidate, SOURCE)

    tx_records = [f for f in result.field_results if f.field_name == "transaction_type"]
    recorded_ungrounded = bool(tx_records) and not tx_records[0].grounded

    # (1) transaction_type IS recorded as ungrounded.
    check("B1 transaction_type recorded ungrounded", recorded_ungrounded,
          f"field_results grounded={tx_records[0].grounded if tx_records else 'n/a'}")

    # (2) Overall grounding must NOT be considered grounded.
    if result.grounded is True:
        expected_failure(
            "B2 overall grounded must be False",
            "M-01 FAIL-OPEN: gate reports grounded=True while its own "
            f"field_results marks transaction_type ungrounded; issues={result.issues}",
        )
    else:
        check("B2 overall grounded must be False", True)

    # (3) safe_for_kernel must be false.
    if result.safe_for_kernel is True:
        expected_failure(
            "B3 safe_for_kernel must be False",
            "M-01 FAIL-OPEN: unverified transaction_type admitted to the "
            "deterministic kernel",
        )
    else:
        check("B3 safe_for_kernel must be False", True)

    # The exact contradiction that IS the bug.
    if recorded_ungrounded and result.grounded is True:
        expected_failure(
            "B4 no FieldGrounding(False) may coexist with grounded=True",
            "M-01 FAIL-OPEN: self-contradictory grounding result",
        )
    else:
        check("B4 no FieldGrounding(False) may coexist with grounded=True", True)

    # (4) It must not become VERIFIED on the strength of other fields.
    check("B5 review_required is True", result.review_required is True,
          f"review_required={result.review_required}")
    if result.suggested_status == "VERIFIED":
        expected_failure("B6 suggested_status must not be VERIFIED",
                         "unverified transaction_type retained a VERIFIED suggestion")
    else:
        check("B6 suggested_status must not be VERIFIED", True,
              f"suggested_status={result.suggested_status}")


def test_m01_invented_enum_fails_closed() -> None:
    """An enum outside the keyword table is even more clearly ungrounded."""
    print("\n--- B'. M-01: unverifiable transaction_type ---")
    gate = ExpandedGroundingGate()
    candidate = grounded_baseline(
        transaction_type="Consignment winding",
        transaction_type_enum="CONSIGNMENT_WINDING",
    )
    result = gate.ground(candidate, SOURCE)

    if result.grounded is True:
        expected_failure(
            "B7 unverifiable transaction_type must not ground",
            f"M-01 FAIL-OPEN: grounded=True, issues={result.issues}",
        )
    else:
        check("B7 unverifiable transaction_type must not ground", True)


# ---------------------------------------------------------------------------
# C. M-01 end-to-end — no VERIFIED, no authority action
# ---------------------------------------------------------------------------


def _stub_provider(candidate: Dict[str, Any]):
    from backend.model_provider.base import InterpretationResult, ProviderStatus

    class _Stub:
        def __init__(self) -> None:
            self._candidate = candidate

        def status(self) -> ProviderStatus:
            return ProviderStatus(
                available=True, model_id="stub", base_model_revision="stub",
                adapter_repo_id="stub", adapter_revision="stub",
                reason="security regression stub", loadable=True,
            )

        def interpret(self, raw_input: str) -> InterpretationResult:
            return InterpretationResult(
                raw_input=raw_input, candidate=dict(self._candidate),
                model_id="stub", provider_revision="stub",
            )

        @property
        def config(self) -> Optional[Any]:
            return None

    return _Stub()


def test_m01_kernel_never_verifies_fabricated_type() -> None:
    """
    (4)+(5): a fabricated transaction_type must not produce a VERIFIED
    result through the real Kernel, and must not cause any deterministic
    authority action to be taken on its basis.

    Uses a stub ModelProvider, so no model weights and no network needed.
    """
    print("\n--- C. M-01: Kernel must not VERIFY a fabricated type ---")
    from backend.kernel.kernel import Kernel

    def run(candidate: Dict[str, Any]):
        kernel = Kernel()
        kernel.set_model_provider(_stub_provider(candidate))
        try:
            return kernel.process(SOURCE)
        except Exception as exc:  # a raised boundary error is also "not VERIFIED"
            return exc

    control = run(grounded_baseline())
    control_status = getattr(control, "status", f"RAISED:{type(control).__name__}")
    check("C1 control (correct PURCHASE) reaches a real status",
          control_status == "VERIFIED", f"status={control_status}")

    fabricated = run(grounded_baseline(
        transaction_type="Sale of goods",
        transaction_type_enum="SALE",
    ))
    fab_status = getattr(fabricated, "status", f"RAISED:{type(fabricated).__name__}")

    if fab_status == "VERIFIED":
        expected_failure(
            "C2 fabricated transaction_type must not yield VERIFIED",
            "M-01 FAIL-OPEN end-to-end: a transaction_type conflicting with "
            f"the raw input produced a VERIFIED result: status={fab_status}",
        )
    else:
        check("C2 fabricated transaction_type must not yield VERIFIED", True,
              f"status={fab_status}")

    # (5) No authority action may be taken on the fabricated type.
    accounting = getattr(fabricated, "accounting_result", None)
    if fab_status == "VERIFIED" and accounting:
        expected_failure(
            "C3 no accounting authority action on a fabricated type",
            "M-01: debit/credit lines were produced from a candidate whose "
            f"transaction_type is ungrounded: {str(accounting)[:120]}",
        )
    else:
        check("C3 no accounting authority action on a fabricated type", True,
              f"accounting={'none' if not accounting else 'present'}")


# ---------------------------------------------------------------------------
# D. M-02 — amount substring must not count as grounding
# ---------------------------------------------------------------------------


def test_m02_amount_substring_does_not_ground() -> None:
    print("\n--- D. M-02: amount substring defect (EXPECTED TO FAIL today) ---")
    gate = ExpandedGroundingGate()

    def with_amount(value: str) -> Dict[str, Any]:
        return grounded_baseline(
            amounts=[{"value": value, "currency": "INR", "source": "explicit"}],
        )

    # --- Contract that MUST be preserved by any fix -----------------------
    for good, label in (("90000", "plain"), ("90,000", "comma-grouped")):
        res = gate.ground(with_amount(good), SOURCE)
        if res.grounded is not True:
            expected_failure(
                f"D1 true amount {good!r} must keep grounding ({label})",
                f"fix would over-tighten the existing normalization contract: "
                f"issues={res.issues}",
            )
        else:
            check(f"D1 true amount {good!r} keeps grounding ({label})", True)

    # Documented gap, NOT an assertion — the current contract does not
    # normalize these. Recorded so a future fix does not silently invent
    # new semantics.
    from backend.maths.fyjc_grounding_gate import _amount_in_text
    for gap in ("Rs. 90,000", "90000.00", "90,000.00"):
        note(f"current _amount_in_text({SOURCE!r}, {gap!r}) = "
             f"{_amount_in_text(SOURCE, gap)} (not asserted; existing contract)")

    # --- The defect ------------------------------------------------------
    for bogus, why in (
        ("90", "1000x understatement — digit-substring of 90000"),
        ("0", "digit-substring of 90000"),
        ("9", "digit-substring of 90000"),
        ("000", "digit-substring of 90000"),
        ("9,000", "10x error that even LOOKS like a well-formed amount"),
    ):
        res = gate.ground(with_amount(bogus), SOURCE)
        if res.grounded is True:
            expected_failure(
                f"D2 amount {bogus!r} must not ground",
                f"M-02 SUBSTRING DEFECT: {why}; gate reports grounded=True "
                f"against source {SOURCE!r}",
            )
        else:
            check(f"D2 amount {bogus!r} must not ground", True,
                  f"status issues={res.issues}")

        # Belt and braces: the field-level record must agree with the
        # aggregate, or we have the M-01 contradiction again.
        recorded_ungrounded = any(
            f.field_name == f"amount:{bogus}" and not f.grounded
            for f in res.field_results
        )
        if res.grounded is True and recorded_ungrounded:
            expected_failure(
                f"D3 amount {bogus!r} field record must not contradict aggregate",
                "self-contradictory grounding result (same shape as M-01)",
            )


def test_m02_kernel_rejects_substring_amount() -> None:
    print("\n--- D'. M-02: Kernel-level consequence ---")
    from backend.kernel.kernel import Kernel

    kernel = Kernel()
    kernel.set_model_provider(_stub_provider(grounded_baseline(
        amounts=[{"value": "9,000", "currency": "INR", "source": "explicit"}],
    )))
    try:
        result = kernel.process(SOURCE)
        status = result.status
        accounting = result.accounting_result or {}
        debit = accounting.get("debit_lines") or []
    except Exception as exc:
        status = f"RAISED:{type(exc).__name__}"
        debit = []

    # The deterministic accounting re-derives the amount from raw_input, so
    # today a bogus model amount cannot change the posting. This assertion
    # targets the GROUNDING outcome, which is what M-02 is about.
    if status == "VERIFIED":
        expected_failure(
            "D4 substring amount must not survive as VERIFIED",
            "M-02: candidate amount '9,000' against source 'Rs. 90,000' was "
            "admitted through grounding. The posting is unaffected only "
            "because accounting re-derives from raw_input — the grounding "
            f"gate itself did not catch it. debit={str(debit)[:100]}",
        )
    else:
        check("D4 substring amount must not survive as VERIFIED", True,
              f"status={status}")

    # A true amount must still verify — proves the fix need not be blanket-tight.
    ok_kernel = Kernel()
    ok_kernel.set_model_provider(_stub_provider(grounded_baseline()))
    try:
        ok_status = ok_kernel.process(SOURCE).status
    except Exception as exc:
        ok_status = f"RAISED:{type(exc).__name__}"
    check("D5 true amount still reaches VERIFIED (no over-tightening)",
          ok_status == "VERIFIED", f"status={ok_status}")


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: GROUNDING GATE FAIL-CLOSED")
    print("Captures audit findings M-01 and M-02 (pre-fix).")
    print("=" * 78)

    test_baseline_is_grounded()
    test_m01_transaction_type_fails_closed()
    test_m01_invented_enum_fails_closed()
    test_m01_kernel_never_verifies_fabricated_type()
    test_m02_amount_substring_does_not_ground()
    test_m02_kernel_rejects_substring_amount()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed (expected pre-fix)")
        print("\nOutstanding defects (audit findings this suite locks in):")
        for m in _MESSAGES:
            print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
