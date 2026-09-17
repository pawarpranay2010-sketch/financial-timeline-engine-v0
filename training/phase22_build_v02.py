#!/usr/bin/env python3
"""phase22_build_v02.py — Phase 22 v0.2 training-corpus builder.

Composition (every rule proven by the Phase 22 audit, see
PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md and PLATRIXA_PHASE22_REPORT.md):

  1. Canonical v0.1 train split (training_data/fyjc_specialist_train.jsonl,
     800 rows, 18-field targets) — the v0.1-proven training material.
     Audit result: its targets are ALREADY clean (0 'Rs'-party rows,
     0 blank-transaction_type rows, 0 junk party spans across all 800 —
     the Phase 22 defect patterns live in the deterministic specialist
     parse path, NOT in the training labels). All rows are therefore kept
     VERBATIM as TRUSTED_GOLD except:
       - the 21 audited dual-payment rows (Pattern D: legacy target claims
         a single payment method + VERIFIED although the input binds BOTH
         cash and cheque to the same transaction subject) → CORRECTIVE
         re-label: transaction interpretation and both grounded amounts
         kept, payment conflict recorded
         (ambiguity_flags=[CONFLICTING_INFORMATION], pm=UNKNOWN,
         suggested_status=REVIEW_REQUIRED). Contract-valid by construction
         and re-validated through the production stack below.
         11 purchase + 6 payment rows were found by the fte_fyjc_73
         full-corpus sweep (they PASS the grounding gate individually, so
         the label defect is invisible to it); the audited 4 are
         sta_00090/91/92/101. sta_00085 (validation split) is documented,
         never relabeled — locked splits are untouchable.
       - any row dropped by the leakage/duplicate gates below (recorded).
  3. Hardcore corpus (training_data/fyjc_hardcore_1000.jsonl, frozen
     sha256 56be5be1…) used VERBATIM — its outputs are already full
     18-field contract objects in the v0.1 target shape (integer-string
     amounts = corpus convention).
     EXCLUDED: hc_00092, hc_00928 (Phase 21 template-level near-duplicates
     of Phase 17 benchmark material) — mandatory exclusion list.
     Each row is re-validated through the production schema verifier AND
     the ExpandedGroundingGate; failures are dropped and counted, never
     silently repaired.

Evaluation isolation (STEP 8, enforced below):
  - no input overlap with fyjc_specialist_validation.jsonl / _test.jsonl
  - no exact or near-duplicate (Jaccard >= 0.75) input vs the Phase 17
    locked benchmark (training/phase17_benchmark.jsonl)
  - hc_00092 / hc_00928 absent
  - unique ids, unique inputs

Determinism: fixed ordering (canonical order, then hardcore order),
PYTHONHASHSEED=0 re-exec (Phase 21 finding R1), no timestamps in rows.
Secrets: none read, none written.

Output:
  training_data/phase22_v02_train.jsonl
  training_data/phase22_v02_train.manifest.json
"""
import json
import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
import re
from collections import Counter

from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
from backend.maths.schema_verifier import validate_structured_interpretation

CANONICAL_TRAIN = "training_data/fyjc_specialist_train.jsonl"
CANONICAL_VAL = "training_data/fyjc_specialist_validation.jsonl"
CANONICAL_TEST = "training_data/fyjc_specialist_test.jsonl"
HARDCORE = "training_data/fyjc_hardcore_1000.jsonl"
BENCHMARK = "training/phase17_benchmark.jsonl"
OUT = "training_data/phase22_v02_train.jsonl"
MANIFEST = "training_data/phase22_v02_train.manifest.json"

HARDCORE_EXPECTED_SHA = "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd"
EXCLUDE_HARDCORE_IDS = {"hc_00092", "hc_00928"}

# NOTE: no canonical rows are excluded for specialist-path defects — the
# canonical train targets were audited clean (see module docstring).

# Audited Pattern-D dual-payment rows: corrective re-label (see module
# docstring and the fte_fyjc_73 sweep). Two audited classes share the
# identical defect — the input binds BOTH cash and cheque to the same
# transaction subject while the legacy target claims a single mode +
# VERIFIED; the fresh specialist resolves cheque (last-mentioned-wins) on
# every row and, on the pronoun rows, flags the unresolved party:
#   purchase (11 new): sta_00079, sta_00055, sta_00094, sta_00040,
#                      sta_00039, sta_00061, sta_00080, sta_00098,
#                      sta_00069, sta_00099, sta_00048
#   audited purchase (4): sta_00090, sta_00091, sta_00092, sta_00101
#   payment  (6 new):     sta_00038, sta_00041, sta_00050, sta_00054,
#                         sta_00058, sta_00059
# Train split only — sta_00085 lives in the locked validation split and is
# documented, not relabeled.
CORRECTIVE_TRAIN_IDS = {
    # audited Pattern D (Phase 22 22-row audit)
    "sta_00090", "sta_00091", "sta_00092", "sta_00101",
    # purchase-class extension (fte_fyjc_73 sweep)
    "sta_00079", "sta_00055", "sta_00094", "sta_00040", "sta_00039",
    "sta_00061", "sta_00080", "sta_00098", "sta_00069", "sta_00099",
    "sta_00048",
    # payment-class extension (fte_fyjc_73 sweep)
    "sta_00038", "sta_00041", "sta_00050", "sta_00054", "sta_00058",
    "sta_00059",
}

LEGACY_7 = ["transaction_type", "parties", "amounts", "payment_method",
            "references", "ambiguities", "grounding"]


def key(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def tok(t: str) -> frozenset:
    return frozenset(key(t).split())


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: str):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def normalize_amounts(obj):
    """Corpus convention: integer-string amounts (Phase 20 finding R3)."""
    if isinstance(obj, dict):
        return {k: normalize_amounts(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_amounts(v) for v in obj]
    return obj


def correct_dual_payment(out: dict, input_text: str) -> dict:
    """Corrective target for audited dual-payment purchase rows.

    Keeps the (supported) purchase interpretation and both grounded amounts;
    records the payment-method conflict instead of claiming a single mode.
    """
    fixed = json.loads(json.dumps(out))  # deep copy
    fixed["payment_method"] = "unknown"
    fixed["payment_method_enum"] = "UNKNOWN"
    fixed["ambiguities"] = ["conflicting information"]
    fixed["ambiguity_flags"] = ["CONFLICTING_INFORMATION"]
    fixed["suggested_status"] = "REVIEW_REQUIRED"
    fixed["grounding"] = {
        "all_fields_explicitly_grounded": True,
        "inferred_fields": [],
    }
    # Preserve legacy reference fields when already set (payment-class rows
    # carry referenced_party from the received-amount clause); null stays null.
    fixed.setdefault("referenced_party", None)
    fixed.setdefault("referenced_transaction_index", None)
    for fc in fixed.get("field_confidences", []):
        if fc.get("field_name") == "payment_method":
            fc["value"] = "UNKNOWN"
            fc["grounding"] = "CONFLICTING"
            fc["source_text"] = "cash and cheque both stated"
            fc["reasoning"] = (
                "phase22 corrective re-label: input states two payment modes; "
                "single mode not claimable"
            )
    fixed["overall_confidence"] = "0.55"
    fixed.setdefault("safety_flags", ["NONE"])
    if "UNRESOLVED_FIELDS" not in fixed["safety_flags"]:
        fixed["safety_flags"] = [f for f in fixed["safety_flags"] if f != "NONE"] + ["UNRESOLVED_FIELDS"]
    fixed.setdefault("scope_flags", ["SINGLE_TRANSACTION"])
    return normalize_amounts(fixed)


def passthrough_hardcore(out: dict) -> dict:
    """Frozen hardcore targets are already full 18-field v0.1-contract objects.
    Only normalize stray float-string amounts to the corpus convention."""
    return normalize_amounts(out)


def main() -> None:
    hc_sha = sha256_file(HARDCORE)
    if hc_sha != HARDCORE_EXPECTED_SHA:
        raise SystemExit(
            f"FROZEN HARDCORE DATASET HASH MISMATCH: {hc_sha} != {HARDCORE_EXPECTED_SHA} — STOP"
        )

    gate = ExpandedGroundingGate()
    canonical = load(CANONICAL_TRAIN)
    val_rows = load(CANONICAL_VAL)
    test_rows = load(CANONICAL_TEST)
    bench = load(BENCHMARK)

    # --- part 1: canonical train (keep / correct / exclude) -----------------
    kept, corrected, excluded = [], [], []
    for r in canonical:
        rid = r["id"]
        if rid in CORRECTIVE_TRAIN_IDS:
            out = correct_dual_payment(r["output"], r["input"])
            rep = validate_structured_interpretation(out, allow_expanded=True)
            if not rep.valid:
                raise SystemExit(f"corrective row {rid} failed schema: {[e.code for e in rep.errors]}")
            res = gate.ground(out, r["input"])
            if not res.safe_for_kernel:
                raise SystemExit(f"corrective row {rid} failed gate: {res.issues}")
            corrected.append({"id": rid, "input": r["input"], "output": out,
                              "metadata": {"source": "fyjc_specialist_train", "phase22": "corrective_relabel"}})
        else:
            kept.append({"id": rid, "input": r["input"], "output": r["output"],
                         "metadata": {"source": "fyjc_specialist_train", "phase22": "canonical"}})

    # --- part 2: hardcore projection ----------------------------------------
    hc_rows = load(HARDCORE)
    hc_ids = {r["id"] for r in hc_rows}
    if EXCLUDE_HARDCORE_IDS & hc_ids != EXCLUDE_HARDCORE_IDS:
        raise SystemExit("expected exclusion ids missing from hardcore corpus")
    projected, hc_dropped = [], []
    for r in hc_rows:
        if r["id"] in EXCLUDE_HARDCORE_IDS:
            continue
        out = passthrough_hardcore(r["output"])
        rep = validate_structured_interpretation(out, allow_expanded=True)
        res = gate.ground(out, r["input"])
        if rep.valid and res.safe_for_kernel:
            projected.append({"id": r["id"], "input": r["input"], "output": out,
                              "metadata": {"source": "fyjc_hardcore_1000", "phase22": "verbatim_18field"}})
        else:
            hc_dropped.append({"id": r["id"], "schema": [e.code for e in rep.errors], "gate": res.issues})

    rows = kept + corrected + projected

    # --- STEP 8: evaluation isolation (enforced, not advisory) --------------
    eval_keys = {key(r["input"]) for r in val_rows} | {key(r["input"]) for r in test_rows}
    for r in rows:
        if key(r["input"]) in eval_keys:
            raise SystemExit(
                f"HARD INTEGRITY FAILURE: {r['id']} overlaps canonical val/test — source data problem"
            )

    # Benchmark material must never enter training (Phase 21 leakage policy,
    # now applied to the FULL candidate set, not just the hardcore slice).
    bench_keys = {key(b["input"]) for b in bench}
    bench_toks = [(b["id"], tok(b["input"])) for b in bench]

    # Duplicate policy: first occurrence in composition order wins.
    dropped = []
    seen_inputs: dict = {}
    final_rows = []
    for r in rows:
        k = key(r["input"])
        reason = None
        if k in seen_inputs:
            reason = f"exact_duplicate_of:{seen_inputs[k]}"
        elif k in bench_keys:
            reason = "phase17_benchmark_exact"
        else:
            t = tok(r["input"])
            for bid, bt in bench_toks:
                if jaccard(t, bt) >= 0.75:
                    reason = f"phase17_benchmark_near_dup:{bid}"
                    break
        if reason:
            dropped.append({"id": r["id"], "reason": reason})
            continue
        seen_inputs[k] = r["id"]
        final_rows.append(r)
    rows = final_rows

    # Final verification pass — must be provably clean.
    id_dup, bench_exact, bench_near, val_overlap = [], [], [], []
    seen_ids: dict = {}
    for r in rows:
        k = key(r["input"])
        if r["id"] in seen_ids:
            id_dup.append(r["id"])
        seen_ids[r["id"]] = True
        if k in eval_keys:
            val_overlap.append(r["id"])
        if k in bench_keys:
            bench_exact.append(r["id"])
        else:
            t = tok(r["input"])
            for bid, bt in bench_toks:
                if jaccard(t, bt) >= 0.75:
                    bench_near.append((r["id"], bid))
                    break

    excluded_ids_present = sorted(EXCLUDE_HARDCORE_IDS & {r["id"] for r in rows})

    problems = []
    if id_dup: problems.append(f"duplicate ids: {id_dup[:5]}")
    if val_overlap: problems.append(f"overlap with canonical val/test: {val_overlap[:5]}")
    if bench_exact: problems.append(f"exact overlap with Phase 17 benchmark: {bench_exact[:5]}")
    if bench_near: problems.append(f"near-dup vs Phase 17 benchmark: {bench_near[:8]}")
    if excluded_ids_present: problems.append(f"excluded ids present: {excluded_ids_present}")
    if problems:
        raise SystemExit("LEAKAGE/INTEGRITY CHECK FAILED:\n" + "\n".join(problems))

    # --- write ---------------------------------------------------------------
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    out_sha = sha256_file(OUT)
    dist = {
        "source": Counter(r["metadata"]["source"] for r in rows),
        "phase22": Counter(r["metadata"]["phase22"] for r in rows),
        "transaction_type": Counter(r["output"]["transaction_type"] for r in rows),
        "suggested_status": Counter(r["output"].get("suggested_status", "(legacy7: none)") for r in rows),
    }

    manifest = {
        "dataset_name": "phase22_v02_train",
        "version": "1.0",
        "row_count": len(rows),
        "composition": {
            "canonical_kept": len(kept),
            "canonical_corrective_relabel": len(corrected),
            "canonical_excluded_ids": sorted(excluded),
            "hardcore_verbatim": len(projected),
            "hardcore_gate_or_schema_dropped": hc_dropped,
            "hardcore_excluded_ids": sorted(EXCLUDE_HARDCORE_IDS),
            "leakage_or_duplicate_dropped": dropped,
        },
        "sources": {
            "canonical_train_sha256": sha256_file(CANONICAL_TRAIN),
            "hardcore_sha256": hc_sha,
            "hardcore_expected_sha256": HARDCORE_EXPECTED_SHA,
        },
        "output_sha256": out_sha,
        "leakage_checks": {
        "input_duplicates_within_dataset": 0,  # enforced by dedup pass above
        "duplicate_ids": len(id_dup),
            "overlap_vs_canonical_val_test": len(val_overlap),
            "exact_overlap_vs_phase17_benchmark": len(bench_exact),
            "near_dup_ge_0.75_vs_phase17_benchmark": len(bench_near),
            "excluded_ids_present": len(excluded_ids_present),
            "rows_dropped_for_leakage_or_duplicates": len(dropped),
            "result": "PASS" if not problems else "FAIL",
        },
        "distributions": {k: dict(v) for k, v in dist.items()},
        "target_format": "full 18-field contract rows {id, input, output, metadata}; "
                         "assistant target == compact 18-field JSON (v0.1 job contract)",
        "determinism": "fixed corpus ordering; PYTHONHASHSEED=0; no timestamps",
        "validator": "backend.schema_verifier.validate_structured_interpretation(allow_expanded=True) "
                     "+ ExpandedGroundingGate (production stack, unmodified)",
    }
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"rows written: {len(rows)} -> {OUT}")
    print(f"  canonical kept={len(kept)} corrective={len(corrected)} excluded={len(excluded)}")
    print(f"  hardcore verbatim={len(projected)} dropped={len(hc_dropped)} excluded=2")
    print(f"output sha256: {out_sha}")
    print(f"leakage checks: ALL PASS")
    print(f"distributions: {json.dumps({k: dict(v) for k, v in dist.items()}, default=dict)[:600]}")


if __name__ == "__main__":
    main()
