#!/usr/bin/env python3
"""
PLATRIXA — TRANSACTION SEMANTIC COVERAGE, PHASE 3A (fte_sec_04)
================================================================

Closes the verified transaction-type grounding coverage gap without
weakening fail-closed admission.

Before this phase, six TransactionTypeEnum values could never ground:
DEPRECIATION, DISCOUNT_CASH, DISCOUNT_TRADE, GST, SETTLEMENT and
UNKNOWN (the last intentionally). Rule 5 relied on a hardcoded table
with a single "return" category that RETURN_IN and RETURN_OUT shared.

THE SECURITY PROPERTY UNDER TEST
--------------------------------
  1. Every non-UNKNOWN contract type resolves to an explicit, versioned
     semantic mapping — no type can be silently unverifiable.
  2. A type grounds ONLY on its own evidence vocabulary. A mention of
     tax is not a complete tax treatment; a settlement mention is not a
     grounded discharge; an unqualified "returned" is not a direction.
  3. Unsupported / unknown values still fail closed, and an undirected
     return stays AMBIGUOUS — never promoted to acceptance.
  4. Nothing here touches admission: safe_for_kernel is unchanged and
     VERIFIED is still produced only by the accounting kernel.

No model inference is performed; kernel checks use a stub provider.

Run:  python3 scripts/fte_sec_04_transaction_semantic_coverage_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.maths.fyjc_contract import TransactionTypeEnum  # noqa: E402
from backend.maths.fyjc_grounding_gate import (  # noqa: E402
    ADJUSTMENT,
    CLASSIFICATION,
    EVENT,
    SETTLEMENT,
    TAX_TREATMENT,
    TRANSACTION_TYPE_EVIDENCE,
    TRANSACTION_TYPE_SEMANTICS_VERSION,
    EvidenceClass,
    ExpandedGroundingGate,
    REJECTING_EVIDENCE_CLASSES,
    _transaction_type_semantics,
    _sentences,
)

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
        print(f"  [FAIL] {name} — {detail}")
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def candidate(**overrides: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "transaction_type": "Transaction",
        "transaction_type_enum": "PAYMENT",
        "parties": [],
        "amounts": [],
        "payment_method": "",
        "payment_method_enum": "UNKNOWN",
        "references": [],
        "ambiguities": [],
        "grounding": {},
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


def tx_record(tx_enum: str, text: str):
    gate = ExpandedGroundingGate()
    result = gate.ground(candidate(transaction_type_enum=tx_enum), text)
    for fr in result.field_results:
        if fr.field_name == "transaction_type":
            return result, fr
    return result, None


# ---------------------------------------------------------------------------
# 1. Coverage: every contract type has an explicit, versioned mapping
# ---------------------------------------------------------------------------

def test_coverage() -> None:
    check("S0a semantics table is versioned",
          TRANSACTION_TYPE_SEMANTICS_VERSION == "tx-semantics-1",
          f"version={TRANSACTION_TYPE_SEMANTICS_VERSION}")

    uncovered = [
        m.value for m in TransactionTypeEnum
        if m.value != "UNKNOWN" and m.value not in TRANSACTION_TYPE_EVIDENCE
    ]
    check("S0b every non-UNKNOWN contract type has an evidence mapping",
          not uncovered, f"uncovered={uncovered}")

    empty = [k for k, (role, terms) in TRANSACTION_TYPE_EVIDENCE.items() if not terms]
    check("S0c no mapping has an empty evidence vocabulary",
          not empty, f"empty={empty} — an empty vocabulary would ground vacuously")

    bad_role = [k for k, (role, terms) in TRANSACTION_TYPE_EVIDENCE.items()
                if role not in (EVENT, CLASSIFICATION, SETTLEMENT, ADJUSTMENT,
                                TAX_TREATMENT)]
    check("S0d every mapping declares a known semantic role",
          not bad_role, f"unknown roles={bad_role}")

    role, terms = _transaction_type_semantics("SOMETHING_ELSE")
    check("S0e an unrecognised claim resolves to no mapping",
          role == "" and terms == (), f"resolved={role!r}/{terms!r}")


# ---------------------------------------------------------------------------
# 2. Previously unreachable types: positive / negative / ambiguous / contradictory
# ---------------------------------------------------------------------------

NEW_TYPES = [
    # Phase 3A: the bare "settlement" substring was withdrawn from the
    # unguarded evidence table (it grounded document mentions). The span is
    # now the guarded, sentence-scoped pattern match "as part settlement of".
    # The assertion still requires exact span equality plus a real source
    # span, so its strength is unchanged — only the expected value moved.
    ("SETTLEMENT", "Paid Raj 5,000 as part settlement of the 60,000 account.",
     SETTLEMENT, "as part settlement of"),
    ("DEPRECIATION", "Recorded depreciation of 5,000 on the machinery.",
     ADJUSTMENT, "depreciation"),
    ("GST", "Sold goods to Amit for 1,000 with GST charged at 18%.",
     TAX_TREATMENT, "GST"),
    ("DISCOUNT_TRADE", "Purchased goods for 1,000 with a 10% trade discount.",
     CLASSIFICATION, "trade discount"),
    ("DISCOUNT_CASH", "Allowed a cash discount of 100 on the settlement.",
     CLASSIFICATION, "cash discount"),
]


def test_newly_supported_types() -> None:
    for tx, text, want_role, want_span in NEW_TYPES:
        result, fr = tx_record(tx, text)
        check(f"S1 {tx} grounds on its own evidence",
              fr is not None and fr.grounded is True
              and result.safe_for_kernel is True,
              f"grounded={fr.grounded if fr else None} issues={result.issues[:1]}")
        check(f"S1b {tx} records role {want_role} and a real span",
              fr is not None and fr.semantic_role == want_role
              and fr.span_text == want_span and fr.source_span is not None,
              f"role={fr.semantic_role if fr else None} span={fr.span_text if fr else None!r}")
        check(f"S1c {tx} stays SEMANTIC (never promoted above semantic support)",
              fr is not None and fr.evidence_class is EvidenceClass.SEMANTIC,
              f"class={fr.evidence_class.value if fr else None}")

        # Negative evidence: the same type claimed on unrelated text fails closed.
        _, neg = tx_record(tx, "Paid 500 to Raj for stationery.")
        check(f"S1d {tx} is unsupported on unrelated text",
              neg is not None and neg.grounded is False
              and neg.evidence_class in REJECTING_EVIDENCE_CLASSES,
              f"grounded={neg.grounded if neg else None}")

        # Contradictory evidence: the type asserted where its own vocabulary
        # is explicitly negated by different wording still must not ground.
        _, contra = tx_record(tx, "Nothing of the kind was recorded today.")
        check(f"S1e {tx} stays ungrounded on contradictory text",
              contra is not None and contra.grounded is False,
              f"grounded={contra.grounded if contra else None}")


def test_tax_and_settlement_are_not_treatments() -> None:
    _, gst = tx_record("GST", "Sold goods to Amit for 1,000 with GST charged at 18%.")
    check("S2a GST is labelled a tax treatment, not an event",
          gst is not None and gst.semantic_role == TAX_TREATMENT,
          f"role={gst.semantic_role if gst else None}")
    check("S2b GST note states it is not a complete treatment",
          gst is not None and "complete GST treatment" in gst.span_note,
          f"note={gst.span_note!r}" if gst else "no record")

    _, stl = tx_record("SETTLEMENT", "Paid Raj 5,000 as part settlement of 60,000.")
    check("S2c SETTLEMENT notes that amount and counter-account must still ground",
          stl is not None and "must still be grounded" in stl.span_note,
          f"note={stl.span_note!r}" if stl else "no record")

    # A bare mention of tax without the tax vocabulary must not ground.
    _, bare = tx_record("GST", "Paid tax of 500 to the department.")
    check("S2d a bare 'tax' mention does not ground GST",
          bare is not None and bare.grounded is False,
          f"grounded={bare.grounded if bare else None}")


# ---------------------------------------------------------------------------
# 3. Return direction: separated, and ambiguity preserved
# ---------------------------------------------------------------------------

def test_return_direction() -> None:
    _, out = tx_record("RETURN_OUT", "The supplier returned the goods worth 5,000.")
    check("S3a RETURN_OUT grounds on purchase-side wording",
          out is not None and out.grounded is True
          and out.span_text == "returned the goods",
          f"grounded={out.grounded if out else None} span={out.span_text if out else None!r}")

    _, inn = tx_record("RETURN_IN", "The customer returned the goods worth 5,000.")
    check("S3b RETURN_IN grounds on sales-side wording",
          inn is not None and inn.grounded is True
          and inn.span_text == "customer returned",
          f"grounded={inn.grounded if inn else None} span={inn.span_text if inn else None!r}")

    # The two directions no longer share one vocabulary entry.
    out_terms = set(TRANSACTION_TYPE_EVIDENCE["RETURN_OUT"][1])
    in_terms = set(TRANSACTION_TYPE_EVIDENCE["RETURN_IN"][1])
    check("S3c the return directions have disjoint evidence vocabularies",
          not (out_terms & in_terms), f"shared={sorted(out_terms & in_terms)}")

    # Cross-direction claims must fail closed.
    _, crossed = tx_record("RETURN_IN", "The supplier returned the goods worth 5,000.")
    check("S3d RETURN_IN claimed on purchase-side wording fails closed",
          crossed is not None and crossed.grounded is False,
          f"grounded={crossed.grounded if crossed else None}")

    # Undirected return: ambiguity preserved, never guessed.
    for tx in ("RETURN_OUT", "RETURN_IN"):
        _, amb = tx_record(tx, "Returned 5 units worth 5,000 today.")
        check(f"S3e {tx} on undirected wording is AMBIGUOUS and ungrounded",
              amb is not None and amb.grounded is False
              and amb.evidence_class is EvidenceClass.AMBIGUOUS
              and "direction is not established" in amb.span_note,
              f"class={amb.evidence_class.value if amb else None} "
              f"note={amb.span_note!r}" if amb else "no record")

    amb_result, _ = tx_record("RETURN_OUT", "Returned 5 units worth 5,000 today.")
    check("S3f an undirected return is not safe for kernel",
          amb_result.safe_for_kernel is False and amb_result.review_required is True,
          f"safe={amb_result.safe_for_kernel}")


def test_return_direction_adversarial() -> None:
    """Every defect found in the adversarial audit of the return patterns.

    The heuristic must ABSTAIN rather than guess. Each case below was a real
    false-acceptance vector before its guard was added.
    """
    # (text, claim, expected_grounded, why)
    cases = [
        ("A return was discussed after goods were supplied last year.",
         "RETURN_OUT", False, "mention of the word 'return', not a transaction"),
        ("Invoices mention that a return policy exists for supplied goods.",
         "RETURN_OUT", False, "policy mention, not a return event"),
        ("Goods returned were not supplied by Ravi.",
         "RETURN_OUT", False, "negation inside the matched window"),
        ("We did not return any goods which were delivered by Ravi.",
         "RETURN_OUT", False, "negated return event"),
        ("We sold 200 to Amit. Items were supplied last year and goods were returned.",
         "RETURN_OUT", False, "supply word belongs to a different sentence"),
        ("We returned 500 to Ram because we sold 200 to Amit which were supplied last year.",
         "RETURN_OUT", False, "named transferee — the return's own direction "
                              "depends on that party's unstated role"),
        ("A return policy was discussed and we sold 500 units.",
         "RETURN_IN", False, "mention plus an unrelated sale"),
        ("He drew 500 from the account that we supplied.",
         "RETURN_OUT", False, "no return event at all"),
        ("The supplier supplied 500 and we allowed a cash discount of 50.",
         "RETURN_OUT", False, "settlement/discount wording does not establish "
                              "return direction"),
        ("Anjali returned furniture of Rs.25,000 as the wrong items were supplied.",
         "RETURN_OUT", True, "return + purchase-side phrase in one sentence"),
        # CORRECTED in Phase 3A: this previously asserted True. "returned BY Lata"
        # means the goods came back TO the firm, so the event is inbound —
        # the purchase-side phrase did not make it a purchase return. It was
        # a real false acceptance, now covered by the S8 group below.
        ("Goods worth Rs. 20000 returned by Lata as excess goods were supplied.",
         "RETURN_OUT", False, "passive agent — direction is inbound, not RETURN_OUT"),
        ("Ravi returned the goods we sold him for 500.",
         "RETURN_IN", True, "return + sales-side phrase"),
    ]
    for text, tx, expected, why in cases:
        result, fr = tx_record(tx, text)
        check(f"S7 {tx} {'grounds' if expected else 'abstains'}: {text[:52]}",
              (fr is not None and fr.grounded is expected
               and result.safe_for_kernel is expected),
              f"{why}; grounded={fr.grounded if fr else None}")

    # Sentence scoping must not be defeated by decimal/currency points.
    sentences = _sentences("Anjali returned furniture of Rs.25,000 as the wrong "
                           "items were supplied. Also sold 500 to Ram.")
    check("S7h currency points do not split sentences",
          len(sentences) == 2 and "Rs.25,000" in sentences[0],
          f"sentences={sentences}")


# ---------------------------------------------------------------------------
# 4. Existing types must be unaffected
# ---------------------------------------------------------------------------

EXISTING = [
    ("PURCHASE", "Purchased furniture from Amit for 25,000."),
    ("SALE", "Sold goods to Ram for 50,000."),
    ("PAYMENT", "Paid rent of 5,000 to the landlord."),
    ("RECEIPT", "Received 5,000 from Amit."),
    ("CAPITAL", "Amit invested capital of 100,000."),
    ("EXPENSE", "Paid salary of 50,000 to staff."),
    ("DRAWING", "The owner withdrew 10,000 for personal use."),
]


def test_existing_types_unchanged() -> None:
    for tx, text in EXISTING:
        result, fr = tx_record(tx, text)
        check(f"S4 {tx} still grounds",
              fr is not None and fr.grounded is True and result.safe_for_kernel is True,
              f"issues={result.issues[:1]}")
    # Legacy free-text transaction_type still resolves.
    _, legacy = tx_record("Purchase of machinery", "Bought machinery for 90,000 cash.")
    check("S4h legacy free-text transaction_type still resolves",
          legacy is not None and legacy.grounded is True,
          f"grounded={legacy.grounded if legacy else None}")

    # "settled" alone no longer grounds PAYMENT: a settlement is not a payment.
    result, fr = tx_record("PAYMENT", "Settled the account with Raj for 5,000.")
    check("S4i 'settled' no longer grounds PAYMENT (settlement is distinct)",
          fr is not None and fr.grounded is False and result.safe_for_kernel is False,
          f"grounded={fr.grounded if fr else None}")

    # UNKNOWN stays acceptable as an explicit absence.
    result, fr = tx_record("UNKNOWN", "Paid 500 to Raj.")
    check("S4j UNKNOWN transaction type is still a non-fabrication",
          fr is not None and fr.grounded is True
          and fr.evidence_class is EvidenceClass.AMBIGUOUS,
          f"class={fr.evidence_class.value if fr else None}")

    # Unknown / unsupported values fail closed.
    for bogus in ("WEIRD_TYPE", "", "SUPER_PURCHASE_XL"):
        result, fr = tx_record(bogus, "Paid 500 to Raj.")
        check(f"S4k unsupported type {bogus!r} fails closed",
              result.safe_for_kernel is False,
              f"safe={result.safe_for_kernel} issues={result.issues[:1]}")


# ---------------------------------------------------------------------------
# 5. Admission integrity
# ---------------------------------------------------------------------------

def test_admission_unchanged() -> None:
    src = (_REPO_ROOT / "backend" / "maths" / "fyjc_grounding_gate.py").read_text(
        encoding="utf-8")
    check("S5a safe_for_kernel formula is untouched",
          "grounded\n            and not has_forbidden\n            and not has_claimed_verified"
          in src, "admission formula must remain exactly as audited")
    agg = src[src.index("        # --- Determine result ---"):]
    agg = agg[:agg.index("return GroundingResult(")]
    check("S5b admission aggregation never reads the semantic role",
          "semantic_role" not in agg and "TRANSACTION_TYPE_EVIDENCE" not in agg,
          "semantic mappings must be descriptive only")


def _stub_provider(cand: Dict[str, Any]):
    from backend.model_provider.base import InterpretationResult, ProviderStatus

    class _Stub:
        def __init__(self) -> None:
            self._candidate = cand

        def status(self) -> ProviderStatus:
            return ProviderStatus(
                available=True, model_id="stub", base_model_revision="stub",
                adapter_repo_id="stub", adapter_revision="stub",
                reason="phase 3a stub", loadable=True,
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


def test_kernel_authority() -> None:
    from backend.kernel.kernel import Kernel

    def run(cand: Dict[str, Any], text: str) -> str:
        kernel = Kernel()
        kernel.set_model_provider(_stub_provider(cand))
        try:
            return getattr(kernel.process(text), "status", "NO_STATUS")
        except Exception as exc:
            return f"RAISED:{type(exc).__name__}"

    ok = run(candidate(transaction_type_enum="PAYMENT",
                       amounts=[{"value": "500", "currency": "INR",
                                 "source": "explicit"}],
                       parties=["Raj"]),
             "Paid 500 to Raj for stationery.")
    check("S6a a fully grounded candidate still reaches VERIFIED",
          ok == "VERIFIED", f"status={ok}")

    ambiguous = run(candidate(transaction_type_enum="RETURN_OUT",
                              amounts=[{"value": "5000", "currency": "INR",
                                        "source": "explicit"}]),
                    "Returned 5 units worth 5,000 today.")
    check("S6b an undirected return never reaches VERIFIED",
          ambiguous != "VERIFIED", f"status={ambiguous}")

    claimed = run(candidate(transaction_type_enum="DEPRECIATION",
                            amounts=[{"value": "5000", "currency": "INR",
                                      "source": "explicit"}],
                            suggested_status="VERIFIED"),
                  "Recorded depreciation of 5,000 on the machinery.")
    check("S6c a model claiming VERIFIED still cannot verify",
          claimed != "VERIFIED", f"status={claimed}")


def test_false_acceptance_regressions() -> None:
    """Targeted coverage for five REAL false acceptances found in Phase 3A.

    Four passive-return rows were grounded as RETURN_OUT purely because a
    side-of-trade word ("were supplied") happened to sit in the same
    sentence, even though "returned BY <party>" means the goods came back
    TO the firm — an inbound event. One settlement document mention was
    grounded as SETTLEMENT because "part settlement" was an unguarded
    substring. Each case below was an accepted-but-wrong result before its
    guard; all must now ABSTAIN.
    """
    # (text, claim, why) — all must abstain
    cases = [
        ("Goods worth Rs.4,000 returned by Komal as excess goods were supplied.",
         "RETURN_OUT", "passive agent + supply word — direction is inbound"),
        ("Goods worth Rs.8,000 returned by Vikram as the wrong items were supplied.",
         "RETURN_OUT", "passive agent + supply word"),
        ("Goods worth 7,500 rupees returned by Prakash as the wrong items were supplied.",
         "RETURN_OUT", "passive agent + supply word"),
        ("Goods worth 9,000 rupees returned by Harish as excess goods were supplied.",
         "RETURN_OUT", "passive agent + supply word"),
        ("This is a part settlement policy document.",
         "SETTLEMENT", "document mention, not a settlement event"),
        ("The board approved a full settlement clause in the contract.",
         "SETTLEMENT", "clause mention, no settled amount"),
        ("Settlement policy documents were circulated to all departments.",
         "SETTLEMENT", "bare noun mention"),
    ]
    for text, tx, why in cases:
        result, fr = tx_record(tx, text)
        check(f"S8 {tx} abstains: {text[:48]}",
              fr is not None and fr.grounded is False
              and result.safe_for_kernel is False,
              f"{why}; grounded={fr.grounded if fr else None}")

    # The recipient rule must NOT leak into the passive form, and an ACTIVE
    # return to a named party in the same shape must still ground.
    for text in ("Returned school uniforms worth Rs.7,500 to Meena "
                 "as the wrong items were supplied.",):
        result, fr = tx_record("RETURN_OUT", text)
        check(f"S8b active recipient still grounds RETURN_OUT: {text[:44]}",
              fr is not None and fr.grounded is True
              and result.safe_for_kernel is True,
              f"grounded={fr.grounded if fr else None}")

    # A passive form must not be rescued by adding a recipient elsewhere.
    result, fr = tx_record(
        "RETURN_OUT",
        "Goods worth Rs.4,000 returned by Komal as excess goods were "
        "supplied. Payment received to Ravi.")
    check("S8c passive agent is not rescued by a later sentence",
          fr is not None and fr.grounded is False,
          f"grounded={fr.grounded if fr else None}")


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — TRANSACTION SEMANTIC COVERAGE (Phase 3A)")
    print("=" * 78)

    test_coverage()
    test_newly_supported_types()
    test_tax_and_settlement_are_not_treatments()
    test_return_direction()
    test_return_direction_adversarial()
    test_existing_types_unchanged()
    test_admission_unchanged()
    test_kernel_authority()
    test_false_acceptance_regressions()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed")
        for m in _MESSAGES:
            print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())