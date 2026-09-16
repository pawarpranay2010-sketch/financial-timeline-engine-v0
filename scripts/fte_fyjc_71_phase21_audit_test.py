#!/usr/bin/env python3
"""fte_fyjc_71 — Phase 21: R2/R4/R5 audit evidence + hardcore dataset freeze gate.

Read-only audit of production behaviour (no runtime modification) plus a
freeze gate over the Phase 20 corpus.

Sections:
  R2  — substring keyword false positives: minimal reproduction, matcher
        mechanism identification, collision scope, fix specification.
  R4  — RECEIPT unreachable from the specialist although the kernel owns
        full receipt semantics (INCOME_RECEIVED golden case) — VOCABULARY_GAP.
  R5  — generic on-account payments: genuine evidence-weak inputs correctly
        unresolved — INTENTIONAL_UNKNOWN; settlement wording does resolve.
  E   — dataset integrity: sha256, rows, IDs, statuses, manifest, original.
  L   — leakage boundary vs every existing independent evaluation set,
        with the exact Phase 22 exclusion list.
  I   — runtime integrity hashes (kernel/grounding/schema/provider untouched).

Phase 20 artifacts are asserted byte-identical, never modified.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_checks: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _checks.append((name, bool(cond), detail))


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def tok(t: str) -> frozenset:
    return frozenset(norm(t).split())


def jac(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


HC = ROOT / "training_data" / "fyjc_hardcore_1000.jsonl"
HC_SHA = "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd"
ORIG = ROOT / "training_data" / "fyjc_specialist_1000.jsonl"
ORIG_SHA = "feb7bfe5c1f415228d3ab9ccdc43beaa2df1498af8f4e66fc5856c441df61e24"

RUNTIME_HASHES = {
    "backend/maths/fyjc_ai_specialist.py":
        "bc722a9d3ed3757386f0507b40a4bc72b0753306972930733b05c3f29dc132d1",
    "backend/maths/fyjc_contract.py":
        "3535a6cfc872a8a061a51ba414ac494e689804eabc3daace518ab47e8c054afa",
    "backend/maths/fyjc_grounding_gate.py":
        "3909af0ff3f17d3b347a3281e05362a4ccf2304bfa5e9042aa46a963d0dfeb3e",
    "backend/maths/schema_verifier.py":
        "539010bbb918bc5e8f5cf97d00a2cc1e417eaaaa99df9367817080f2b0b1c783",
    "backend/kernel/kernel.py":
        "ee524adc651950dfdafe6420e0b6e83b185e37c445e1e6b0764771c1fe01b17e",
    "backend/model_provider/base.py":
        "bf1e828305198f978c40b34401dab408d8f6a57cb9a7593e1d75a943da6a4fc2",
}


def main() -> int:
    from backend.maths.fyjc_ai_specialist import FYJCAISpecialist
    from backend.maths.schema_verifier import validate_structured_interpretation
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

    sp = FYJCAISpecialist()
    gate = ExpandedGroundingGate()

    # ================= R2: substring false positives =========================
    def tx_of(text: str):
        o = sp.parse(text)
        src = next((fc["source_text"] for fc in o["field_confidences"]
                    if fc["field_name"] == "transaction_type"), "")
        return o["transaction_type_enum"], src

    tx, src = tx_of("paid off")
    check("R2.paid_off_matches", tx == "SETTLEMENT" and src == "paid off", f"{tx}/{src!r}")
    tx, src = tx_of("paid office")
    check("R2.paid_office_false_positive",
          tx == "SETTLEMENT" and src == "paid off",
          f"{tx}/{src!r}")
    tx, _ = tx_of("paid off today")
    check("R2.paid_off_today_true_positive", tx == "SETTLEMENT", tx)
    tx, _ = tx_of("was paid off")
    check("R2.was_paid_off_true_positive", tx == "SETTLEMENT", tx)
    tx, _ = tx_of("office was paid")
    check("R2.office_was_paid_no_false_SETTLEMENT", tx != "SETTLEMENT", tx)
    tx, src = tx_of("The payment for the dues is forgotten.")
    check("R2.got_inside_forgotten_false_positive", tx == "PURCHASE" and src == "got",
          f"{tx}/{src!r}")
    tx, _ = tx_of("Sold goods to a salesgirl.")
    check("R2.sales_inside_salesgirl_false_positive", tx == "SALE", tx)
    tx, _ = tx_of("A cashless transaction was made.")
    check("R2.cash_inside_cashless_false_positive",
          sp.parse("A cashless transaction was made.")["payment_method_enum"] == "CASH",
          sp.parse("A cashless transaction was made.")["payment_method_enum"])

    # mechanism identification: raw substring (`if w in lower`), not token/regex
    matcher_lines = (ROOT / "backend" / "maths" / "fyjc_ai_specialist.py").read_text()
    check("R2.matcher_is_raw_substring",
          "for w in _SETTLEMENT_WORDS:\n        if w in lower:" in matcher_lines,
          "raw `in` matching confirmed at _detect_transaction_type/_detect_payment_method")
    check("R2.all_12_vocab_loops_use_substring",
          len(re.findall(r"if w in lower:", matcher_lines)) == 12,
          str(len(re.findall(r"if w in lower:", matcher_lines))))

    # legitimate inflections that any fix MUST preserve (spec requirement)
    for keep, expect in [("Returned goods worth Rs.500.", "RETURN_OUT"),
                         ("Two cheques were received.", None),
                         ("The full settlements were recorded.", None)]:
        tx, _ = tx_of(keep)
        if expect:
            check(f"R2.must_keep_inflection:{keep[:18]}", tx == expect, tx)
    tx, _ = tx_of("Paid office expenses of Rs.12,000 by cheque.")
    check("R2.paid_office_expenses_currently_misclassified", tx == "SETTLEMENT", tx)

    # ================= R4: RECEIPT unreachable ===============================
    for label, text in [
        ("generic", "Received Rs.5,000 cash from Rahul against our bill."),
        ("bank_mode", "Received a cheque of Rs.25,000 from Sharma Traders."),
        ("sent_wording", "Sharma Traders sent Rs. 60000 by NEFT towards dues."),
        ("commission", "Received commission Rs.2,000."),
    ]:
        o = sp.parse(text)
        rep = validate_structured_interpretation(o, allow_expanded=True)
        g = gate.ground(o, text)
        check(f"R4.tx_UNKNOWN:{label}",
              o["transaction_type_enum"] == "UNKNOWN"
              and "MULTIPLE_INTERPRETATIONS" in o["ambiguity_flags"],
              f"{o['transaction_type_enum']}/{o['ambiguity_flags']}")
        check(f"R4.schema_valid:{label}", rep.valid)
        # gate failure is ONLY the R3 amount-format artifact, not a new gap
        o2 = json.loads(json.dumps(o))
        for a in o2["amounts"]:
            if a["value"].endswith(".0"):
                a["value"] = a["value"][:-2]
        g2 = gate.ground(o2, text)
        check(f"R4.gate_clean_after_convention_fix:{label}", g2.safe_for_kernel,
              str(g2.issues)[:80])

    # specialist has no RECEIPT/PAYMENT emission path at all
    check("R4.no_RECEIPT_return_in_specialist",
          'return "RECEIPT"' not in matcher_lines,
          "grep-level: emission path absent")
    check("R4.no_PAYMENT_return_in_specialist",
          'return "PAYMENT"' not in matcher_lines)
    # downstream kernel owns the semantics (golden case A09, INCOME_RECEIVED)
    ds = (ROOT / "backend" / "maths" / "fyjc_dataset.py").read_text()
    check("R4.kernel_has_INCOME_RECEIVED_semantics",
          "INCOME_RECEIVED" in ds and "Received commission" in ds,
          "fyjc_dataset.py A09 golden case")

    # ================= R5: on-account payments ===============================
    for label, text, want_tx, want_flags in [
        ("generic_no_mode", "Paid Mohan Rs.5,000.", "UNKNOWN",
         {"MISSING_PAYMENT_MODE", "MULTIPLE_INTERPRETATIONS"}),
        ("cash_explicit", "Paid Mohan Rs.5,000 in cash.", "UNKNOWN",
         {"MULTIPLE_INTERPRETATIONS"}),
        ("settlement_wording", "Paid Mohan Rs.5,000 in full settlement.",
         "SETTLEMENT", {"MISSING_PAYMENT_MODE"}),
    ]:
        o = sp.parse(text)
        check(f"R5.tx:{label}",
              o["transaction_type_enum"] == want_tx
              and want_flags <= set(o["ambiguity_flags"]),
              f"{o['transaction_type_enum']}/{o['ambiguity_flags']}")
    # partial settlement: two amounts, dues explicitly present, still unresolved
    text = "Paid Mohan Rs.5,000 against the dues of Rs.10,000; balance still payable."
    o = sp.parse(text)
    check("R5.partial_settlement_both_amounts_found",
          len(o["amounts"]) == 2 and "MULTIPLE_INTERPRETATIONS" in o["ambiguity_flags"],
          f"{len(o['amounts'])}/{o['ambiguity_flags']}")
    # downstream supports settlement semantics (bk_reasoning: full/partial,
    # D4 anti-invented-discount guard)
    br = (ROOT / "backend" / "maths" / "fyjc_bk_reasoning.py").read_text()
    check("R5.kernel_supports_settlement_semantics",
          "_full_immediate_settlement" in br and br.lower().count("settlement") >= 50,
          f"mentions={br.lower().count('settlement')}")

    # ================= 21E: dataset integrity =================================
    raw = HC.read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    check("E.sha256_unchanged", h == HC_SHA, h)
    recs = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    check("E.row_count_1000", len(recs) == 1000, str(len(recs)))
    ids = [r["id"] for r in recs]
    check("E.ids_exact_range", ids == [f"hc_{i:05d}" for i in range(1, 1001)])
    check("E.no_dup_inputs", len({r["input"] for r in recs}) == 1000)
    statuses = {r["output"]["suggested_status"] for r in recs}
    check("E.all_REVIEW_REQUIRED", statuses == {"REVIEW_REQUIRED"}, str(statuses))
    check("E.no_VERIFIED_anywhere",
          all("VERIFIED" not in json.dumps(r) for r in recs))
    man = json.loads((ROOT / "training_data" / "fyjc_hardcore_1000.manifest.json")
                     .read_text())
    check("E.manifest_matches", man["candidate_dataset_sha256"] == h
          and man["row_count"] == 1000)
    check("E.original_byte_identical",
          hashlib.sha256(ORIG.read_bytes()).hexdigest() == ORIG_SHA)

    # ================= 21F: leakage boundary ==================================
    hc_norm = {norm(r["input"]) for r in recs}
    hc_tok = [tok(r["input"]) for r in recs]

    def scan(label, path):
        rows = [json.loads(l) for l in (ROOT / path).read_text().splitlines() if l.strip()]
        exact = sum(1 for r in rows if norm(r["input"]) in hc_norm)
        near = [(r["id"], hcid) for r in rows
                for hcid, htok in zip(ids, hc_tok)
                if tok(r["input"]) and jac(tok(r["input"]), htok) >= 0.75]
        return rows, exact, near

    for label, path in [
        ("p5a_ambiguity", "training_data/specialist_ambiguity_eval.jsonl"),
        ("p5a_unsupported", "training_data/specialist_unsupported_eval.jsonl"),
        ("p5a_robustness", "training_data/specialist_robustness_eval.jsonl"),
        ("clean_training", "training_data/specialist_clean_training.jsonl"),
        ("specialist_1000", "training_data/fyjc_specialist_1000.jsonl"),
    ]:
        rows, exact, near = scan(label, path)
        check(f"L.zero_overlap:{label}", exact == 0 and len(near) == 0,
              f"exact={exact} near={near[:2]}")
    rows17, exact17, near17 = scan("phase17_benchmark", "training/phase17_benchmark.jsonl")
    check("L.benchmark_exact_overlap_zero", exact17 == 0, str(exact17))
    check("L.benchmark_near_dups_enumerated", len(near17) == 2
          and {b for b, _ in near17} == {"PB-0005", "PB-0016"},
          str(near17))
    print("Phase 22 exclusion list (benchmark side):", sorted(b for b, _ in near17))

    # ================= runtime integrity ======================================
    for rel, want in RUNTIME_HASHES.items():
        got = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        check(f"I.untouched:{Path(rel).name}", got == want, got[:16] + "…")

    # ==========================================================================
    passed = sum(1 for _, ok, _ in _checks if ok)
    print(f"\nfte_fyjc_71 (phase21 audit + freeze gate): {passed}/{len(_checks)} checks passed")
    for name, ok, detail in _checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f"  — {detail}" if (detail and not ok) else ""))
    return 0 if passed == len(_checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
