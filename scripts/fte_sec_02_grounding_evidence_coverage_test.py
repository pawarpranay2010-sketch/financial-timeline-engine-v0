#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: GROUNDING EVIDENCE COVERAGE (fte_sec_02)
======================================================================

Companion to fte_sec_01 (fail-closed aggregation, audit M-01/M-02).

M-03  backend/maths/fyjc_grounding_gate.py Rule 4 (payment method) fails OPEN
      on any claimed method that is missing from its evidence vocabulary.

      The rule looked the claim up in an inline table and then guarded with

          keywords = pm_keywords.get(pm_lower, [])
          if keywords and not any(kw in text_lower for kw in keywords):
              ... append issue ...

      A MISSING table entry therefore produced ``keywords == []``, the guard
      short-circuited, and the branch fell through to ``grounded=True`` — the
      field was reported as "supported by text" without a single word of
      evidence ever being looked for. ``NEFT`` was the live instance:
      PaymentMethodEnum/VALID_PM_ENUM both list NEFT
      (backend/maths/fyjc_contract.py:77, backend/maths/schema_verifier.py:164)
      but the table had no "neft" key, so:

          source : "Paid Rs. 1250 cash for office stationery."
          claim  : payment_method_enum = "NEFT"

      produced grounded=True, safe_for_kernel=True, issues=[] — a candidate
      contradicting the source text about the instrument of payment was
      admitted to the deterministic kernel.

THE SECURITY PROPERTY UNDER TEST
-------------------------------
  A claimed payment method may only ground when its evidence vocabulary is
  KNOWN (exhaustive coverage of the contract enum) AND at least one of its
  evidence terms actually occurs in the source text. An unrecognised claim is
  UNVERIFIABLE and must fail closed — never vacuously ground.

  This suite also pins the over-tightening side: every valid, evidenced method
  must still ground, so the fix cannot be "reject every payment method".

NOTE ON SCOPE
-------------
  These assertions were written against the pre-fix behaviour of the gate and
  are EXPECTED TO FAIL until the evidence-vocabulary coverage lands. They
  define what "fixed" means for M-03. Do not weaken them to match current
  behaviour — that is the defect they exist to catch.

  No model inference is performed; the kernel checks use a stub provider.

Run:  python3 scripts/fte_sec_02_grounding_evidence_coverage_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.maths.fyjc_contract import PaymentMethodEnum  # noqa: E402
from backend.maths.fyjc_grounding_gate import (  # noqa: E402
    PAYMENT_METHOD_EVIDENCE,
    ExpandedGroundingGate,
    _resolve_payment_method_evidence,
)

# ---------------------------------------------------------------------------
# Shared check machinery (house style, mirrors fte_sec_01)
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


def note(text: str) -> None:
    """Non-asserting observation, so a gap is documented, not invented."""
    print(f"  [NOTE] {text}")


# ---------------------------------------------------------------------------
# Fixtures — schema-valid candidate over a fixed source text
# ---------------------------------------------------------------------------

# Diagnostic case from the grounding-engine audit.
SOURCE = "Paid Rs. 1,250 cash for office stationery."

# A clean, fully evidenced source per payment method (E5 / no over-tightening).
EVIDENCED_SOURCES = {
    "CASH": "Paid Rs. 1,250 in cash for office stationery.",
    "BANK": "Paid Rs. 1,250 by bank transfer for office stationery.",
    "CHEQUE": "Paid Rs. 1,250 by cheque for office stationery.",
    "NEFT": "Paid Rs. 1,250 by NEFT for office stationery.",
    "UPI": "Paid Rs. 1,250 via UPI for office stationery.",
    "CREDIT": "Paid Rs. 1,250 on credit for office stationery.",
}


def candidate(**overrides: Any) -> Dict[str, Any]:
    """Schema-valid 18-field candidate grounded against SOURCE."""
    out: Dict[str, Any] = {
        "transaction_type": "Payment of office stationery",
        "transaction_type_enum": "PAYMENT",
        "parties": [],
        "amounts": [{"value": "1250", "currency": "INR", "source": "explicit"}],
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
        "safety_flags": [],
        "scope_flags": [],
        "suggested_status": "REVIEW_REQUIRED",
    }
    out.update(overrides)
    return out


def _payment_field(result) -> Any:
    for fr in result.field_results:
        if fr.field_name == "payment_method":
            return fr
    return None


# ---------------------------------------------------------------------------
# E1 — coverage is exhaustive by construction
# ---------------------------------------------------------------------------

def test_every_contract_method_has_evidence_vocabulary() -> None:
    """Every non-UNKNOWN PaymentMethodEnum value must be verifiable.

    This is the structural half of M-03: the defect existed because the table
    and the contract enum could drift apart silently.
    """
    for member in PaymentMethodEnum:
        value = member.value
        if value == "UNKNOWN":
            resolved = _resolve_payment_method_evidence(value)
            note(
                "UNKNOWN is intentionally absent from the evidence vocabulary "
                f"(resolve -> {resolved!r}); it is handled as 'not fabricated' "
                "by Rule 4 before the vocabulary is consulted."
            )
            continue
        resolved = _resolve_payment_method_evidence(value)
        check(
            f"E1 contract value {value} has evidence vocabulary",
            resolved is not None and bool(resolved[1]),
            f"resolve={resolved!r} — a missing entry is what let M-03 pass silently",
        )

    # The vocabulary table itself must cover the contract enum, so a future
    # enum addition is caught by this test rather than by production.
    uncovered = [
        m.value for m in PaymentMethodEnum
        if m.value != "UNKNOWN" and m.value.lower() not in PAYMENT_METHOD_EVIDENCE
    ]
    check(
        "E1b PAYMENT_METHOD_EVIDENCE covers every non-UNKNOWN contract value",
        not uncovered,
        f"uncovered={uncovered}",
    )


# ---------------------------------------------------------------------------
# E2 — the concrete defect: NEFT against a cash source
# ---------------------------------------------------------------------------

def test_neft_claim_against_cash_source_fails_closed() -> None:
    gate = ExpandedGroundingGate()

    res = gate.ground(candidate(payment_method_enum="NEFT", payment_method="NEFT"), SOURCE)
    check(
        "E2 NEFT claimed against a cash source must not ground",
        res.grounded is False,
        f"M-03 VACUOUS PASS: grounded=True issues={res.issues}",
    )
    check(
        "E2b NEFT contradiction is not safe_for_kernel",
        res.safe_for_kernel is False,
        f"safe_for_kernel={res.safe_for_kernel} issues={res.issues}",
    )
    fr = _payment_field(res)
    check(
        "E2c payment_method field record is ungrounded",
        fr is not None and fr.grounded is False,
        f"field_record={fr}",
    )
    check(
        "E2d the issue names the payment method",
        any("NEFT" in i for i in res.issues),
        f"issues={res.issues}",
    )


# ---------------------------------------------------------------------------
# E3 — unrecognised claims fail closed
# ---------------------------------------------------------------------------

def test_unrecognised_method_claims_fail_closed() -> None:
    gate = ExpandedGroundingGate()
    for bogus, why in (
        ("DOGECOIN", "not a payment method at all"),
        ("CHEQUE_BY_POST", "unmapped spelling"),
        ("CRYPTO", "plausible-sounding but unrecognised"),
    ):
        res = gate.ground(
            candidate(payment_method_enum=bogus, payment_method=bogus), SOURCE
        )
        check(
            f"E3 unrecognised method {bogus!r} must not ground",
            res.grounded is False and res.safe_for_kernel is False,
            f"{why}; grounded={res.grounded} safe={res.safe_for_kernel} "
            f"issues={res.issues}",
        )


# ---------------------------------------------------------------------------
# E4 — UNKNOWN is not a fabrication and must still ground
# ---------------------------------------------------------------------------

def test_unknown_method_still_grounds() -> None:
    """The fix must not turn 'no instrument claimed' into a rejection."""
    gate = ExpandedGroundingGate()
    for claimed in ("UNKNOWN", "unknown", ""):
        overrides: Dict[str, Any] = {"payment_method_enum": claimed}
        if claimed != "":
            overrides["payment_method"] = claimed
        else:
            overrides["payment_method"] = ""
        res = gate.ground(candidate(**overrides), SOURCE)
        check(
            f"E4 unclaimed payment method ({claimed!r}) still grounds",
            res.grounded is True,
            f"over-tightening; issues={res.issues}",
        )


# ---------------------------------------------------------------------------
# E5 — every evidenced method still grounds (no blanket tightening)
# ---------------------------------------------------------------------------

def test_evidenced_methods_still_ground() -> None:
    gate = ExpandedGroundingGate()
    for value, source in EVIDENCED_SOURCES.items():
        res = gate.ground(
            candidate(payment_method_enum=value, payment_method=value), source
        )
        check(
            f"E5 {value} with its own evidence still grounds",
            res.grounded is True,
            f"over-tightening against {source!r}; issues={res.issues}",
        )


# ---------------------------------------------------------------------------
# E6 — legacy free-text spellings still resolve (no false rejection)
# ---------------------------------------------------------------------------

def test_legacy_spellings_resolve() -> None:
    gate = ExpandedGroundingGate()
    for value, source, label in (
        ("by cash", SOURCE, "prepositional phrase"),
        ("Cash", SOURCE, "mixed case"),
        ("bank_transfer", EVIDENCED_SOURCES["BANK"], "underscored legacy token"),
        ("Bank Transfer", EVIDENCED_SOURCES["BANK"], "spaced legacy token"),
    ):
        res = gate.ground(
            candidate(payment_method_enum="", payment_method=value), source
        )
        check(
            f"E6 legacy payment_method {value!r} ({label}) still grounds",
            res.grounded is True,
            f"false rejection; issues={res.issues}",
        )


# ---------------------------------------------------------------------------
# E7 — anti-vacuity sweep over every contract method
# ---------------------------------------------------------------------------

def test_no_contract_method_vacuously_grounds() -> None:
    """No method may be reported grounded against a source that lacks it."""
    gate = ExpandedGroundingGate()
    for member in PaymentMethodEnum:
        value = member.value
        resolved = _resolve_payment_method_evidence(value)
        if value == "UNKNOWN" or resolved is None:
            continue
        keywords = resolved[1]
        source = "Paid Rs. 1,250 cash for office stationery."
        if any(kw in source.lower() for kw in keywords):
            continue  # genuinely evidenced by this source; covered by E5
        res = gate.ground(
            candidate(payment_method_enum=value, payment_method=value), source
        )
        fr = _payment_field(res)
        check(
            f"E7 {value} absent from the source must not ground",
            res.grounded is False
            and res.safe_for_kernel is False
            and (fr is None or fr.grounded is False),
            f"M-03 VACUOUS PASS; grounded={res.grounded} safe="
            f"{res.safe_for_kernel} field={fr}",
        )


# ---------------------------------------------------------------------------
# E8 — the kernel never reaches VERIFIED on a contradicted instrument
# ---------------------------------------------------------------------------

def _stub_provider(cand: Dict[str, Any]):
    from backend.model_provider.base import InterpretationResult, ProviderStatus

    class _Stub:
        def __init__(self) -> None:
            self._candidate = cand

        def status(self) -> ProviderStatus:
            return ProviderStatus(
                available=True, model_id="stub", base_model_revision="stub",
                adapter_repo_id="stub", adapter_revision="stub",
                reason="grounding evidence coverage stub", loadable=True,
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


def test_kernel_rejects_contradicted_instrument() -> None:
    from backend.kernel.kernel import Kernel

    def run(cand: Dict[str, Any], source: str) -> str:
        kernel = Kernel()
        kernel.set_model_provider(_stub_provider(cand))
        try:
            return getattr(kernel.process(source), "status", "NO_STATUS")
        except Exception as exc:  # a raised boundary error is also "not VERIFIED"
            return f"RAISED:{type(exc).__name__}"

    control = run(candidate(), SOURCE)
    check(
        "E8a control (evidence matches source) still reaches VERIFIED",
        control == "VERIFIED",
        f"over-tightening; status={control}",
    )

    contradicted = run(
        candidate(payment_method_enum="NEFT", payment_method="NEFT"), SOURCE
    )
    check(
        "E8b NEFT-against-cash must not survive as VERIFIED",
        contradicted != "VERIFIED",
        f"M-03 reached the kernel; status={contradicted}",
    )


# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: GROUNDING EVIDENCE COVERAGE")
    print("Captures audit finding M-03 (payment-method evidence coverage).")
    print("=" * 78)

    test_every_contract_method_has_evidence_vocabulary()
    test_neft_claim_against_cash_source_fails_closed()
    test_unrecognised_method_claims_fail_closed()
    test_unknown_method_still_grounds()
    test_evidenced_methods_still_ground()
    test_legacy_spellings_resolve()
    test_no_contract_method_vacuously_grounds()
    test_kernel_rejects_contradicted_instrument()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed (expected pre-fix)")
        print("\nOutstanding defects (audit finding this suite locks in):")
        for m in _MESSAGES:
            print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())