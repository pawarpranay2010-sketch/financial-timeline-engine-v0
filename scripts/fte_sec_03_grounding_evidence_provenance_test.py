#!/usr/bin/env python3
"""
PLATRIXA — GROUNDING EVIDENCE PROVENANCE (fte_sec_03)
=====================================================

Phase 2 of the grounding-engine work: an ADDITIVE provenance layer over the
existing gate. Nothing here changes admission.

WHAT THIS SUITE LOCKS IN
------------------------
P1  Public contract preserved. ``FieldGrounding`` / ``GroundingResult`` keep
    their original positional construction, their original attribute values
    (``grounded``, ``reason``, ``source_evidence``) and their original
    aggregate verdicts. New provenance attributes are additive and defaulted.

P2  Evidence classification. Every field verdict carries one of
    DIRECT / DERIVED / SEMANTIC / AMBIGUOUS / UNSUPPORTED / CONTRADICTED plus
    the resolution method and the uncertainty state.

P3  Spans are real or absent. A reported source span must slice back to the
    reported span text out of the source, and a record without a span must
    say WHY in ``span_note``. Offsets are never invented, and a derived
    ("90 thousand") amount must not be reported as a literal token match.

P4  Entity matching is token-boundary safe (audit D6). "Ravi" must not match
    "Ravikesh"; a real party must still match across case, punctuation and
    spacing; a currency marker is not an entity.

P5  Admission policy. Evidence metadata never grants VERIFIED: an asserted
    field that is unsupported or contradicted can never be ``grounded=True``,
    and the aggregation block must not read any provenance attribute.

No model inference is performed; kernel checks use a stub provider.

Run:  python3 scripts/fte_sec_03_grounding_evidence_provenance_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.maths.fyjc_grounding_gate import (  # noqa: E402
    ACCEPTING_EVIDENCE_CLASSES,
    EVIDENCE_CONTRACT_VERSION,
    REJECTING_EVIDENCE_CLASSES,
    EvidenceClass,
    FieldGrounding,
    GroundingResult,
    Uncertainty,
    ExpandedGroundingGate,
    _entity_in_text,
    _locate_entity,
    _text_contains,
)

# ---------------------------------------------------------------------------
# Shared check machinery (house style, mirrors fte_sec_01 / fte_sec_02)
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
        print(f"  [FAIL] {name} — {detail}")
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def note(text: str) -> None:
    print(f"  [NOTE] {text}")


SOURCE = "Paid Rs. 1,250 cash for office stationery to Acme Corp."


def candidate(**overrides: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "transaction_type": "Payment for office stationery",
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


def by_name(result: GroundingResult, name: str) -> Optional[FieldGrounding]:
    for fr in result.field_results:
        if fr.field_name == name:
            return fr
    return None


# ---------------------------------------------------------------------------
# P1 — additive contract
# ---------------------------------------------------------------------------

def test_p1_public_contract_preserved() -> None:
    # Original positional construction must still work unchanged.
    legacy = FieldGrounding("party:X", True, "legacy reason")
    check("P1a legacy positional FieldGrounding still constructs",
          legacy.field_name == "party:X" and legacy.grounded is True
          and legacy.reason == "legacy reason" and legacy.source_evidence == "",
          f"{legacy}")

    legacy_result = GroundingResult(True, True, False)
    check("P1b legacy positional GroundingResult still constructs",
          legacy_result.grounded is True and legacy_result.safe_for_kernel is True
          and legacy_result.issues == [] and legacy_result.field_results == []
          and legacy_result.suggested_status == "REVIEW_REQUIRED",
          f"{legacy_result}")

    check("P1c summary property text unchanged",
          legacy_result.summary == "All grounding checks passed"
          and GroundingResult(False, False, True, issues=["a", "b"]).summary == "a; b")

    gate = ExpandedGroundingGate()
    res = gate.ground(candidate(parties=["Acme Corp"]), SOURCE)
    check("P1d evidence_version is set and independent of GROUNDING_VERSION",
          res.evidence_version == EVIDENCE_CONTRACT_VERSION
          == "grounding-evidence-2",
          f"version={res.evidence_version}")

    # Booleans/issues of representative candidates must match the pre-Phase-2
    # contract exactly (regression lock on admission, not on metadata).
    cases = [
        ("clean payment", candidate(parties=["Acme Corp"]), True, True),
        ("fabricated party", candidate(parties=["Ravikesh"]), False, False),
        ("unevidenced tx type",
         candidate(transaction_type="Expense on stationery",
                   transaction_type_enum="EXPENSE"), False, False),
        ("claimed VERIFIED", candidate(suggested_status="VERIFIED"), False, False),
        ("forbidden field", candidate(journal="x"), False, False),
        ("neft vs cash", candidate(payment_method_enum="NEFT",
                                   payment_method="NEFT"), False, False),
    ]
    for label, cand, want_grounded, want_safe in cases:
        got = gate.ground(cand, SOURCE)
        check(f"P1e verdict preserved: {label}",
              got.grounded is want_grounded and got.safe_for_kernel is want_safe,
              f"grounded={got.grounded} safe={got.safe_for_kernel} "
              f"issues={got.issues[:1]}")


def test_p1f_serialization_round_trip() -> None:
    gate = ExpandedGroundingGate()
    res = gate.ground(candidate(parties=["Acme Corp"]), SOURCE)
    payload = res.to_dict()
    try:
        json.dumps(payload)
        serializable = True
    except TypeError as exc:
        serializable = False
        note(f"to_dict() not JSON serializable: {exc}")
    check("P1f GroundingResult.to_dict is JSON serializable", serializable,
          "provenance must be exportable without custom encoders")
    check("P1g to_dict carries provenance keys",
          {"evidence_version", "evidence_counts", "uncertain_fields", "field_results"}
          <= set(payload.keys())
          and {"evidence_class", "resolution", "span_text", "source_span",
               "span_note", "uncertainty"} <= set(payload["field_results"][0].keys()),
          f"keys={sorted(payload.keys())}")
    # Restoring the dict must reproduce the same classification.
    restored = payload["field_results"][0]["evidence_class"]
    check("P1h evidence_class round-trips as its string value",
          restored == EvidenceClass(payload["field_results"][0]["evidence_class"]).value,
          f"value={restored}")


# ---------------------------------------------------------------------------
# P2 — classification correctness
# ---------------------------------------------------------------------------

def test_p2_classification_per_rule() -> None:
    gate = ExpandedGroundingGate()

    party = by_name(gate.ground(candidate(parties=["Acme Corp"]), SOURCE),
                    "party:Acme Corp")
    check("P2a grounded party is DIRECT via token_match",
          party is not None and party.evidence_class is EvidenceClass.DIRECT
          and party.resolution == "token_match" and party.uncertainty is Uncertainty.NONE,
          f"{party}")

    bad = by_name(gate.ground(candidate(parties=["Ravikesh"]), SOURCE),
                  "party:Ravikesh")
    check("P2b unsupported party is UNSUPPORTED and ungrounded",
          bad is not None and bad.evidence_class is EvidenceClass.UNSUPPORTED
          and bad.grounded is False,
          f"{bad}")

    empty = gate.ground(candidate(parties=[], amounts=[], payment_method_enum="UNKNOWN",
                                  payment_method="", ambiguity_flags=[]), SOURCE)
    classes = {fr.field_name: fr.evidence_class for fr in empty.field_results}
    check("P2c no-claim fields are AMBIGUOUS with NO_CLAIM uncertainty",
          classes.get("parties") is EvidenceClass.AMBIGUOUS
          and classes.get("amounts") is EvidenceClass.AMBIGUOUS
          and classes.get("ambiguity_flags") is EvidenceClass.AMBIGUOUS
          and by_name(empty, "parties").uncertainty is Uncertainty.NO_CLAIM,
          f"classes={ {k: v.value for k, v in classes.items()} }")

    pm = by_name(gate.ground(candidate(), SOURCE), "payment_method")
    check("P2d evidenced payment method is SEMANTIC via keyword_table",
          pm is not None and pm.evidence_class is EvidenceClass.SEMANTIC
          and pm.resolution == "keyword_table",
          f"{pm}")

    tx = by_name(gate.ground(candidate(), SOURCE), "transaction_type")
    check("P2e evidenced transaction type is SEMANTIC via keyword_table",
          tx is not None and tx.evidence_class is EvidenceClass.SEMANTIC,
          f"{tx}")

    thousands = by_name(
        gate.ground(candidate(amounts=[{"value": "90000", "currency": "INR",
                                       "source": "explicit"}]),
                    "Paid Rs. 90 thousand cash."),
        "amount:90000")
    check("P2f derived-form amount is DERIVED, not DIRECT",
          thousands is not None and thousands.evidence_class is EvidenceClass.DERIVED
          and thousands.resolution == "derived_form",
          f"{thousands}")

    ref = by_name(gate.ground(candidate(referenced_transaction_index=2), SOURCE),
                  "referenced_transaction_index")
    check("P2g structural reference check is SEMANTIC without a span",
          ref is not None and ref.evidence_class is EvidenceClass.SEMANTIC
          and ref.resolution == "structural_check" and ref.source_span is None,
          f"{ref}")

    bad_ref = by_name(gate.ground(candidate(referenced_transaction_index=-1), SOURCE),
                      "referenced_transaction_index")
    check("P2h contradicted reference index is CONTRADICTED and ungrounded",
          bad_ref is not None
          and bad_ref.evidence_class is EvidenceClass.CONTRADICTED
          and bad_ref.grounded is False,
          f"{bad_ref}")

    res = gate.ground(candidate(parties=["Acme Corp"]), SOURCE)
    counted = sum(res.evidence_counts.values())
    check("P2i evidence_counts covers every field record exactly once",
          counted == len(res.field_results) and len(res.field_results) > 0,
          f"counts={res.evidence_counts} records={len(res.field_results)}")
    check("P2j uncertain_fields lists exactly the AMBIGUOUS records",
          set(res.uncertain_fields)
          == {fr.field_name for fr in res.field_results
              if fr.evidence_class is EvidenceClass.AMBIGUOUS},
          f"uncertain={res.uncertain_fields}")
    check("P2k every class is a declared EvidenceClass member",
          all(isinstance(fr.evidence_class, EvidenceClass)
              for fr in res.field_results),
          "provenance must use the declared vocabulary only")


# ---------------------------------------------------------------------------
# P3 — span integrity: real or explicitly absent, never invented
# ---------------------------------------------------------------------------

def test_p3_span_integrity() -> None:
    gate = ExpandedGroundingGate()

    corpus = [
        (candidate(parties=["Acme Corp"]), SOURCE),
        (candidate(amounts=[{"value": "1,250", "currency": "INR",
                             "source": "explicit"}]), SOURCE),
        (candidate(parties=["Ravikesh"]), SOURCE),
        (candidate(payment_method_enum="NEFT", payment_method="NEFT"), SOURCE),
        (candidate(amounts=[{"value": "90000", "currency": "INR",
                             "source": "explicit"}]), "Paid Rs. 90 thousand cash."),
        (candidate(payment_method_enum="UNKNOWN", payment_method=""), SOURCE),
        (candidate(referenced_transaction_index=1), SOURCE),
    ]
    span_records = 0
    absent_records = 0
    problems: List[str] = []
    for cand, text in corpus:
        for fr in gate.ground(cand, text).field_results:
            if fr.source_span is not None:
                span_records += 1
                start, end = fr.source_span
                ok = (isinstance(start, int) and isinstance(end, int)
                      and 0 <= start < end <= len(text)
                      and text[start:end] == fr.span_text
                      and fr.span_text != "")
                if not ok:
                    problems.append(f"{fr.field_name} span={fr.source_span} "
                                    f"text={fr.span_text!r} slice={text[start:end]!r}")
            else:
                absent_records += 1
                if not fr.span_note:
                    problems.append(f"{fr.field_name} has no span and no span_note")
    check("P3a every reported span slices back out of the source",
          not problems, "; ".join(problems[:3]))
    check("P3b spans were actually produced (not silently disabled)",
          span_records > 0 and absent_records > 0,
          f"spans={span_records} without_span={absent_records}")

    # A derived amount must NOT claim a literal numeric-token span.
    derived = by_name(
        gate.ground(candidate(amounts=[{"value": "90000", "currency": "INR",
                                       "source": "explicit"}]),
                    "Paid Rs. 90 thousand cash."),
        "amount:90000")
    check("P3c derived-form amount claims no numeric span and explains why",
          derived is not None and derived.source_span is None
          and "no single numeric token" in derived.span_note,
          f"span={derived.source_span} note={derived.span_note!r}")


# ---------------------------------------------------------------------------
# P4 — token-boundary entity matching (audit D6)
# ---------------------------------------------------------------------------

def test_p4_entity_matching() -> None:
    cases = [
        # (text, candidate, expected, why)
        ("Ravikesh paid me 500", "Ravi", False, "prefix of a longer name"),
        ("Paid Ravi Kumar 500", "Ravi", True, "real whole-token party"),
        ("Paid Acme Corp 500", "Acme", True, "legitimate short form, exact token"),
        ("Paid acme corp ltd", "Acme Corp", True, "case normalization"),
        ("Bought from Iyer and Co.", "Iyer and Co", True, "trailing period"),
        ("Bought from Iyer  and   Co", "Iyer and Co", True, "spacing"),
        ("Bought from Iyer & Co", "Iyer and Co", False, "ampersand is not a separator match"),
        ("Paid Rs. 500 to supplier", "Rs", False, "currency marker is not an entity"),
        ("Paid Rs. 500 to supplier", "INR", False, "currency marker is not an entity"),
        ("Paid 500 to Ramesh", "Ravi", False, "different name"),
        ("", "Ravi", False, "empty source"),
        ("Paid Ravi", "", False, "empty candidate"),
    ]
    for text, entity, expected, why in cases:
        got = _entity_in_text(text, entity)
        check(f"P4 {'match' if expected else 'no-match'}: {entity!r} in {text!r} ({why})",
              got is expected, f"got={got} expected={expected}")

    # Regression evidence for the semantic change: on real datasets the two
    # matchers must agree, so the D6 fix closes a gap without reclassifying
    # legitimate parties.
    data = _REPO_ROOT / "training_data" / "fyjc_specialist_1000.jsonl"
    if data.exists():
        rows = [json.loads(line) for line in
                data.read_text(encoding="utf-8").splitlines() if line.strip()]
        disagreements = []
        checked = 0
        for row in rows:
            text = row.get("input", "")
            for party in (row.get("output") or {}).get("parties") or []:
                checked += 1
                if _text_contains(text, party) != _entity_in_text(text, party):
                    disagreements.append((row.get("id"), party))
        check("P4b no verdict change on the committed dataset (1000 rows)",
              not disagreements,
              f"checked={checked} disagreements={disagreements[:5]}")
    else:
        note(f"dataset not present at {data}; P4b not asserted")

    located = _locate_entity(SOURCE, "Acme Corp")
    check("P4c located span is exact and in-bounds",
          located is not None and SOURCE[located[0]:located[1]] == located[2]
          == "Acme Corp",
          f"located={located}")

    # Gate-level effect of the D6 fix.
    gate = ExpandedGroundingGate()
    res = gate.ground(candidate(parties=["Ravi"]),
                      "Ravikesh paid me 500 cash for stationery.")
    check("P4d a prefix-only party no longer grounds (D6 closed)",
          res.grounded is False
          and any("Ravi" in i for i in res.issues),
          f"grounded={res.grounded} issues={res.issues}")
    # The honest control: the full name still grounds.
    ctrl = gate.ground(
        candidate(parties=["Ravikesh"],
                  amounts=[{"value": "500", "currency": "INR", "source": "explicit"}]),
        "Ravikesh paid me 500 cash for stationery.")
    check("P4e the full name still grounds (no over-tightening)",
          ctrl.grounded is True, f"issues={ctrl.issues}")


# ---------------------------------------------------------------------------
# P5 — admission policy: provenance never grants admission
# ---------------------------------------------------------------------------

def test_p5_metadata_cannot_grant_admission() -> None:
    gate = ExpandedGroundingGate()
    corpus = [
        candidate(),
        candidate(parties=["Ravikesh"]),
        candidate(parties=["Acme Corp"]),
        candidate(transaction_type="Expense on stationery",
                  transaction_type_enum="EXPENSE"),
        candidate(payment_method_enum="NEFT", payment_method="NEFT"),
        candidate(payment_method_enum="DOGECOIN", payment_method="DOGECOIN"),
        candidate(suggested_status="VERIFIED"),
        candidate(journal="x"),
        candidate(referenced_transaction_index=-1),
        candidate(amounts=[{"value": "999999", "currency": "INR",
                            "source": "explicit"}]),
    ]
    promoted: List[str] = []
    for cand in corpus:
        res = gate.ground(cand, SOURCE)
        for fr in res.field_results:
            if fr.evidence_class in REJECTING_EVIDENCE_CLASSES and fr.grounded:
                promoted.append(f"{fr.field_name}={fr.evidence_class.value}")
            if fr.grounded and fr.evidence_class not in ACCEPTING_EVIDENCE_CLASSES \
                    and fr.evidence_class is not EvidenceClass.AMBIGUOUS:
                promoted.append(f"{fr.field_name} unaccepted-but-grounded")
    check("P5a unsupported/contradicted evidence is never promoted to accepted",
          not promoted, f"promoted={promoted[:5]}")

    # An asserted-but-unevidenced field must always be ungrounded.
    asserted_cases = [
        (candidate(parties=["Ravikesh"]), "party:Ravikesh"),
        (candidate(amounts=[{"value": "999999", "currency": "INR",
                             "source": "explicit"}]), "amount:999999"),
        (candidate(payment_method_enum="NEFT", payment_method="NEFT"),
         "payment_method"),
        (candidate(transaction_type="Expense on stationery",
                   transaction_type_enum="EXPENSE"), "transaction_type"),
    ]
    bad = []
    for cand, name in asserted_cases:
        fr = by_name(gate.ground(cand, SOURCE), name)
        if fr is None or fr.grounded or fr.uncertainty is not Uncertainty.REJECTED:
            bad.append(f"{name} -> {fr}")
    check("P5b every asserted-but-unevidenced field is ungrounded/REJECTED",
          not bad, f"problems={bad}")

    # The aggregation block must not read any provenance attribute.
    src = (_REPO_ROOT / "backend" / "maths" / "fyjc_grounding_gate.py").read_text(
        encoding="utf-8")
    agg = src[src.index("        # --- Determine result ---"):]
    agg = agg[:agg.index("return GroundingResult(")]
    leaked = [name for name in ("evidence_class", "evidence_counts",
                                "source_span", "span_text", "uncertainty",
                                "EvidenceClass") if name in agg]
    check("P5c admission aggregation does not read any provenance attribute",
          not leaked, f"leaked={leaked}")
    check("P5d safe_for_kernel formula is unchanged",
          "grounded\n            and not has_forbidden\n            and not has_claimed_verified"
          in src, "admission formula must stay exactly as audited")


def _stub_provider(cand: Dict[str, Any]):
    from backend.model_provider.base import InterpretationResult, ProviderStatus

    class _Stub:
        def __init__(self) -> None:
            self._candidate = cand

        def status(self) -> ProviderStatus:
            return ProviderStatus(
                available=True, model_id="stub", base_model_revision="stub",
                adapter_repo_id="stub", adapter_revision="stub",
                reason="evidence provenance stub", loadable=True,
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


def test_p5e_kernel_admission_unchanged() -> None:
    from backend.kernel.kernel import Kernel

    def run(cand: Dict[str, Any], source: str) -> str:
        kernel = Kernel()
        kernel.set_model_provider(_stub_provider(cand))
        try:
            return getattr(kernel.process(source), "status", "NO_STATUS")
        except Exception as exc:  # a raised boundary error is also "not VERIFIED"
            return f"RAISED:{type(exc).__name__}"

    check("P5e clean candidate still reaches kernel VERIFIED",
          run(candidate(parties=["Acme Corp"]), SOURCE) == "VERIFIED",
          "provenance must not change admission either way")
    check("P5f unsupported party still stops at GROUNDING_FAILED",
          run(candidate(parties=["Ravikesh"]), SOURCE) == "GROUNDING_FAILED",
          "D6 fix must block, not merely annotate")
    check("P5g claimed VERIFIED still cannot verify",
          run(candidate(suggested_status="VERIFIED"), SOURCE) != "VERIFIED",
          "only the accounting kernel may produce VERIFIED")


# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("PLATRIXA — GROUNDING EVIDENCE PROVENANCE (Phase 2, additive)")
    print("=" * 78)

    test_p1_public_contract_preserved()
    test_p1f_serialization_round_trip()
    test_p2_classification_per_rule()
    test_p3_span_integrity()
    test_p4_entity_matching()
    test_p5_metadata_cannot_grant_admission()
    test_p5e_kernel_admission_unchanged()

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