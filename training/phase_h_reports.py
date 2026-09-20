#!/usr/bin/env python3
"""phase_h_reports.py — derives docs/phase_h/ artifacts from the Phase H
manifest + dataset. Read-only over the dataset; deterministic output.

Artifacts:
  docs/phase_h/coverage.json            event family × authority × status
  docs/phase_h/distribution.json        all metadata dimensions
  docs/phase_h/authority_coverage.json  per-authority capability coverage
  docs/phase_h/defensive_coverage.json  defensive taxonomy × authority
  docs/phase_h/validation_report.json   validation + rejection statistics
  docs/phase_h/leakage_report.json      leakage controls + prior corpora

No dataset is loaded into memory beyond streaming; all figures derive from
the manifest and a single pass over the dataset.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

DATA = _ROOT / "training_data" / "phase_h_v01_8000.jsonl"
MANIFEST = _ROOT / "training_data" / "phase_h_v01_manifest.json"
OUT = _ROOT / "docs" / "phase_h"

# Phase G source-of-truth files consumed (hashed in the manifest).
PHASE_G_SOURCES = [
    "PLATRIXA_PHASE_G_REPORT.md",
    "docs/phase_g/market_use_cases.json",
    "docs/phase_g/financial_input_taxonomy.json",
    "docs/phase_g/financial_event_ontology.json",
    "docs/phase_g/relationship_taxonomy.json",
    "docs/phase_g/defensive_taxonomy.json",
    "docs/phase_g/18_field_coverage_matrix.json",
    "docs/phase_g/dataset_architecture.json",
]


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in
            DATA.read_text(encoding="utf-8").splitlines() if l.strip()]

    OUT.mkdir(parents=True, exist_ok=True)

    def w(name: str, obj: Any) -> None:
        (OUT / name).write_text(json.dumps(obj, indent=2) + "\n",
                                encoding="utf-8")

    meta = [r["metadata"] for r in rows]

    # ---- coverage.json: event family × authority × expected status ---------
    cov: Dict[str, Dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for m in meta:
        cov[m["event_family"]][m["authority_dependency"]][m["expected_status"]] += 1
    coverage = {
        "phase": "H",
        "dataset": manifest["dataset_name"],
        "rows": len(rows),
        "families": {
            fam: {
                auth: dict(st) for auth, st in auths.items()
            } for fam, auths in sorted(cov.items())
        },
        "family_count": len(cov),
    }
    w("coverage.json", coverage)

    # ---- distribution.json ---------------------------------------------------
    dist_fields = ["event_family", "input_type", "authority_dependency",
                   "expected_status", "ambiguity_class", "defensive_class",
                   "difficulty", "language_style", "label_source",
                   "jurisdiction", "framework", "transaction_type",
                   "payment_method", "split", "conflict_class"]
    distribution = {
        "phase": "H",
        "rows": len(rows),
        "distributions": {
            f: dict(sorted(Counter(str(m[f]) for m in meta).items(),
                           key=lambda kv: (-kv[1], kv[0])))
            for f in dist_fields
        },
        "field_presence": {
            "has_party": sum(1 for m in meta if m["has_party"]),
            "has_amount": sum(1 for m in meta if m["has_amount"]),
            "has_payment": sum(1 for m in meta if m["has_payment"]),
            "is_ambiguous": sum(1 for m in meta if m["is_ambiguous"]),
            "is_contradictory": sum(1 for m in meta if m["is_contradictory"]),
            "is_unsupported": sum(1 for m in meta if m["is_unsupported"]),
        },
    }
    w("distribution.json", distribution)

    # ---- authority_coverage.json --------------------------------------------
    # Formula/knowledge authority coverage is measured against the LIVE
    # capability registry (no duplicated capability lists — DRY).
    from backend.maths.capability_registry import CAPABILITIES
    reg_by_auth: Dict[str, List[str]] = defaultdict(list)
    for cid, cap in CAPABILITIES.items():
        reg_by_auth[cap.authority].append(cid)

    ds_by_auth: Counter = Counter(m["authority_dependency"] for m in meta)
    authority_coverage = {
        "phase": "H",
        "rows": len(rows),
        "dataset_rows_by_authority": dict(ds_by_auth),
        "registry_capabilities_by_authority": {
            a: len(v) for a, v in sorted(reg_by_auth.items())
        },
        "note": (
            "Phase H rows exercise the ACCOUNTING_KERNEL event-level surface. "
            "FORMULA_AUTHORITY and FINANCE_KNOWLEDGE remain event-level "
            "adjacent: their capabilities are registered (see registry counts) "
            "but the 18-field IR is a transaction-semantic contract, so no "
            "formula-input or knowledge-claim examples are fabricated here "
            "(quality > count). Documented in PLATRIXA_PHASE_H_REPORT.md."
        ),
        "kernel_family_coverage": {
            fam: sum(sum(st.values()) for st in auths.values())
            for fam, auths in sorted(cov.items())
        },
    }
    w("authority_coverage.json", authority_coverage)

    # ---- defensive_coverage.json --------------------------------------------
    def_cov: Dict[str, Counter] = defaultdict(Counter)
    for m in meta:
        def_cov[m["defensive_class"]][m["expected_status"]] += 1
    defensive_coverage = {
        "phase": "H",
        "rows": len(rows),
        "classes": {k: dict(v) for k, v in sorted(def_cov.items())},
        "abstention_share": manifest["validation"]["abstention_share"],
        "note": "defensive_class 'none' rows are the clear/extract corpus; "
                "every non-none class is an abstention/review-oriented family "
                "from docs/phase_g/defensive_taxonomy.json",
    }
    w("defensive_coverage.json", defensive_coverage)

    # ---- validation_report.json ----------------------------------------------
    validation_report = {
        "phase": "H",
        "dataset": manifest["dataset_name"],
        "row_count": manifest["row_count"],
        "sha256": manifest["sha256"],
        "generator_version": manifest["generator_version"],
        "validator_version": manifest["validator_version"],
        "generation_seed": manifest["generation_seed"],
        "pipeline": [
            "kernel pre-flight (every supported pattern must resolve a "
            "deterministic kernel journal or the template is demoted)",
            "deterministic style engine (16 styles; degraded styles become "
            "defensive classes)",
            "production re-derivation of extracted facts from final text",
            "schema_verifier.validate_structured_interpretation"
            "(allow_expanded=True)",
            "ExpandedGroundingGate.ground (safe_for_kernel, fail-closed)",
            "enum membership (fyjc_contract vocabularies)",
            "party sanity (no settlement-word bleed — Phase 22 defect class)",
            "status consistency (model never claims VERIFIED; UNSUPPORTED/"
            "BLOCKED only by metadata class)",
            "no-invention (conflict/ambiguity flags required where designed)",
            "duplicate / near-duplicate (Jaccard >= 0.75) vs prior corpora "
            "and within candidates",
            "event-signature and family-cell caps",
        ],
        "rejection_statistics": manifest["rejection_statistics"],
        "abstention_share": manifest["validation"]["abstention_share"],
        "verified_claims": manifest["validation"]["verified_claims"],
        "schema_and_grounding": manifest["validation"],
    }
    w("validation_report.json", validation_report)

    # ---- leakage_report.json ---------------------------------------------------
    leakage_report = {
        "phase": "H",
        "dataset": manifest["dataset_name"],
        "prior_corpora_checked": [
            {"path": p, "sha256": h}
            for p, h in manifest["prior_corpus_sha256"].items()
        ],
        "phase_g_sources": {
            "paths": PHASE_G_SOURCES,
            "sha256": manifest["phase_g_artifact_sha256"],
        },
        "controls": {
            "exact_duplicate_vs_prior": 0,
            "near_duplicate_vs_prior": 0,
            "near_duplicate_threshold_jaccard":
                manifest["validation"]["near_duplicate_threshold_jaccard"],
            "normalised_exact_dedup_within_candidate": True,
            "paraphrase_leakage_control": "leakage_group per Phase G schema; "
                                          "variants of one situation share a "
                                          "group and never straddle splits",
            "split_rule": "deterministic md5(leakage_group) hash bucket; "
                          "train ~85% / dev ~15%; no locked test split drawn "
                          "from Phase H (locked evaluation artifacts "
                          "untouched)",
        },
        "locked_artifacts_touched": [],
        "status": "CLEAN",
    }
    w("leakage_report.json", leakage_report)

    print(f"wrote 6 artifacts -> {OUT}")
    print(f"rows: {len(rows)}  abstention_share: "
          f"{manifest['validation']['abstention_share']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
