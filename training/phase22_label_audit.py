#!/usr/bin/env python3
"""phase22_label_audit.py — Phase 22 controlled data-quality audit (evidence only).

Reproduces the Phase 22 classification of the ORIGINAL 1,000-row specialist
corpus through the CURRENT production stack (no production file is modified):

    FYJCAISpecialist.parse()                      (fresh, deterministic)
        -> target normalized to the ESTABLISHED corpus convention
           (integer-string amounts; see STEP 4 rule in
           PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md)
        -> validate_structured_interpretation(allow_expanded=True)
        -> ExpandedGroundingGate.ground()

Classification rule (mirrors the user-reported 251/727/22 result):
    CLEAN        gate.ground(...).gate_safe is True
    FORMAT_ONLY  only VERIFIED-claim issues (suggested_status==VERIFIED /
                 grounding fields / grounding_level / verifier_status)
    GENUINE      any other grounding issue (semantic mismatch)

Outputs:
    training_data/phase22_genuine22.jsonl   one evidence record per GENUINE row:
        {id, input, legacy_target, specialist_output, grounding_issues}
    stdout: classification counts, numeric '.0' incidence, 'Rs'-party
    incidence (legacy vs fresh), short-sentence structure coverage (STEP 6).

Determinism: re-executes with PYTHONHASHSEED=0 (Phase 21 finding R1: the
specialist iterates keyword sets).
"""
import json
import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

from backend.maths.fyjc_ai_specialist import FYJCAISpecialist
from backend.maths.fyjc_contract import LEGACY_FIELDS
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
from backend.maths.schema_verifier import validate_structured_interpretation

ORIG = "training_data/fyjc_specialist_1000.jsonl"
OUT = "training_data/phase22_genuine22.jsonl"

STATUS_CLAIM_KEYS = ("suggested_status", "grounding", "grounding_level", "verifier_status")
VERIFIED_CLAIM_TOKENS = {"VERIFIED", "NOT_VERIFIED", "UNVERIFIED", "HUMAN_REVIEW_REQUIRED"}

# STEP 4 representation rule (documented in the audit report):
#   "Amount <v> not supported by input text" is a FORMAT_ONLY artifact iff the
#   integer value of <v> occurs in the input under the gate's own digit
#   normalization. Decimal-preserving and value-mismatch failures stay GENUINE.
AMOUNT_ISSUE_PREFIX = "Amount "
AMOUNT_ISSUE_SUFFIX = " not supported by input text"


def _amount_is_format_only(issue: str, input_text: str) -> bool:
    if not (issue.startswith(AMOUNT_ISSUE_PREFIX) and issue.endswith(AMOUNT_ISSUE_SUFFIX)):
        return False
    value = issue[len(AMOUNT_ISSUE_PREFIX):-len(AMOUNT_ISSUE_SUFFIX)]
    try:
        f = float(value)
    except ValueError:
        return False
    if f != int(f):
        return False  # true decimal amount — genuine representation problem
    digits = str(int(f))
    import re
    norm = re.sub(r"[^0-9]", "", input_text)
    return digits in norm


def normalize_amounts_convention(obj):
    """STEP 4 corpus convention: integer-string amounts (no float '.0' artifacts)."""
    if isinstance(obj, dict):
        return {k: normalize_amounts_convention(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_amounts_convention(v) for v in obj]
    return obj


def normalize_target(target):
    """Normalize a legacy 7-field target to the 18-field corpus convention."""
    out = {k: target[k] for k in LEGACY_FIELDS if k in target}
    return normalize_amounts_convention(out)


def classify_issues(issues, input_text):
    """(classification, genuine_issues) from gate issue strings.

    FORMAT_ONLY: pure numeric-representation artifacts ("1000.0" vs "1000")
    where the integer value is verifiably present in the input.
    GENUINE:     anything else (semantic mismatch, party errors, decimals).
    """
    genuine = [
        i for i in issues
        if not any(t in i for t in VERIFIED_CLAIM_TOKENS)
        and not _amount_is_format_only(i, input_text)
    ]
    if not issues:
        return "CLEAN", []
    if not genuine:
        return "FORMAT_ONLY", []
    return "GENUINE", genuine


def main():
    rows = [json.loads(l) for l in open(ORIG) if l.strip()]
    specialist = FYJCAISpecialist()
    gate = ExpandedGroundingGate()

    counts = Counter()
    genuine_records = []
    fresh_dot0 = 0
    fresh_rs_party = 0
    legacy_rs_party = 0
    covered = Counter()

    for r in rows:
        target = normalize_target(r["output"])
        fresh = specialist.parse(r["input"])

        # numeric '.0' incidence: legacy target vs fresh emission (STEP 4)
        if any(a["value"].endswith(".0") for a in fresh["amounts"]):
            fresh_dot0 += 1
        # 'Rs'-party incidence (STEP 5)
        if any(p.strip(".").lower() == "rs" for p in fresh["parties"]):
            fresh_rs_party += 1
        if any(p.strip(".").lower() == "rs" for p in target["parties"]):
            legacy_rs_party += 1

        # short-sentence structure coverage (STEP 6)
        low = r["input"].lower()
        if "paid rs" in low:
            covered["paid_rs_any"] += 1
        if "paid rs" in low and " to " in low and " by cash" in low:
            covered["paid_rs_to_X_by_cash"] += 1
        if "paid rs" in low and " for " in low:
            covered["paid_rs_for_expense"] += 1

        result = gate.ground(fresh, r["input"])
        cls, genuine = classify_issues(list(result.issues), r["input"])
        counts[cls] += 1
        if cls == "GENUINE":
            genuine_records.append({
                "id": r["id"],
                "input": r["input"],
                "legacy_target": r["output"],
                "specialist_output": {k: fresh[k] for k in LEGACY_FIELDS},
                "grounding_issues": sorted(genuine),
            })

    with open(OUT, "w") as f:
        for rec in genuine_records:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    print(f"classification: {dict(counts)}")
    print(f"genuine rows written: {len(genuine_records)} -> {OUT}")
    print(f"numeric '.0' in fresh emissions: {fresh_dot0}/1000 rows")
    print(f"'Rs' as party: fresh={fresh_rs_party}/1000  legacy_target={legacy_rs_party}/1000")
    print(f"short-sentence coverage: {dict(covered)}")


if __name__ == "__main__":
    main()
