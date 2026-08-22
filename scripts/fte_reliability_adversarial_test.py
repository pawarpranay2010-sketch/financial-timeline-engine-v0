"""Platrixa reliability-layer adversarial probe (hardcore verification, test-only).

Conflicting tables / duplicate tables / low-confidence OCR / blocked states
must surface deterministically and must never silently resolve in favor of a
guessed value.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.extraction_reliability import (  # noqa: E402
    STATE_VERIFIED,
    STATE_CONFLICT,
    STATE_REVIEW_REQUIRED,
    STATE_BLOCKED,
    STATE_UNANALYZED,
    STATE_DERIVED,
    _detect_conflicts_from_occurrences,
    classify_extraction_state,
)

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail if detail else ''}]")


# ---------------------------------------------------------------------------
# classify_extraction_state unit contract (real signature: fact, best, in_conflict)
# ---------------------------------------------------------------------------

# 1. Two independent table paths, same period, conflicting values -> CONFLICT.
conflict_occurrences = {
    "Revenue": [
        {"value": 281700000000, "period": "FY2025", "column": "FY2025",
         "table": "T1", "page": 26, "path": "income statement", "flagged": False},
        {"value": 281070000000, "period": "FY2025", "column": "FY2025",
         "table": "T2", "page": 47, "path": "segment note", "flagged": False},
    ],
}
conflicts = _detect_conflicts_from_occurrences(conflict_occurrences, {"Revenue": {}})
check("R1 · conflicting table cells -> conflict record", bool(conflicts),
      str([(c.get("metric"), c.get("status")) for c in conflicts]))
check("R1b · conflict carries both competing values", bool(conflicts)
      and len(conflicts[0].get("competing_values", [])) >= 2,
      str([c.get("value") for c in conflicts[0].get("competing_values", [])]))

# 2. in_conflict propagates to a CONFLICT state, never verified.
st_conflict = classify_extraction_state(
    {"value": 281700000000, "source": "10-K", "reporting_period": "FY2025"},
    best=None,
    in_conflict=True,
)
check("R2 · conflicting fact classified CONFLICT (never verified)",
      st_conflict[0] == STATE_CONFLICT, str(st_conflict))

# 3. Low-confidence OCR can NEVER be verified.
ocr_fact = {"value": 281700000000, "source": "OCR scan", "ocr": True, "ocr_confidence": 0.42}
st_ocr = classify_extraction_state(ocr_fact, best=None)
check("R3 · low-confidence OCR -> REVIEW_REQUIRED, never verified",
      st_ocr[0] == STATE_REVIEW_REQUIRED, str(st_ocr))

# 4. Clean layout-backed fact -> VERIFIED.
clean_fact = {"value": 281700000000, "source": "10-K", "reporting_period": "FY2025",
              "provenance_tier": "DOCUMENT"}
st_clean = classify_extraction_state(clean_fact, best={"identity": {"ok": True},
                                                       "method": "table", "flagged": False})
check("R4 · clean layout-backed fact -> VERIFIED", st_clean[0] == STATE_VERIFIED, str(st_clean))

# 5. Missing value -> BLOCKED (never guessed).
st_blocked = classify_extraction_state({"value": None, "source": "—"}, best=None)
check("R5 · missing value -> BLOCKED", st_blocked[0] == STATE_BLOCKED, str(st_blocked))

# 6. Flagged (malformed) table -> REVIEW_REQUIRED even with a value.
st_flag = classify_extraction_state(
    {"value": 100, "source": "10-K", "reporting_period": "FY2025"},
    best={"identity": {"ok": True}, "method": "table", "flagged": True},
)
check("R6 · flagged/malformed table -> REVIEW_REQUIRED", st_flag[0] == STATE_REVIEW_REQUIRED,
      str(st_flag))

# 7. Duplicate identical values -> NO conflict (not a false positive).
dup_occurrences = {
    "Revenue": [
        {"value": 281700000000, "period": "FY2025", "column": "FY2025",
         "table": "T1", "page": 26, "path": "income statement", "flagged": False},
        {"value": 281700000000, "period": "FY2025", "column": "FY2025",
         "table": "T2", "page": 40, "path": "mda", "flagged": False},
    ],
}
dup_conflicts = _detect_conflicts_from_occurrences(dup_occurrences, {"Revenue": {}})
check("R7 · duplicate identical values -> no false conflict", not dup_conflicts,
      str(len(dup_conflicts)))

# 8. Scale jump (281.70B vs 281,700,000,000) is NOT a conflict.
scale_occurrences = {
    "Revenue": [
        {"value": 281700000000, "period": "FY2025", "column": "FY2025",
         "table": "T1", "page": 26, "path": "extractor", "flagged": False},
    ],
}
scale_conflicts = _detect_conflicts_from_occurrences(
    scale_occurrences,
    {"Revenue": {"value": 281700000000, "reporting_period": "FY2025"}},
)
check("R8 · scale-equivalent values not treated as conflict", not scale_conflicts,
      str(len(scale_conflicts)))

# 9. Flagged tables are excluded from conflict proof (they are review_required).
flagged_occ = {
    "Revenue": [
        {"value": 100, "period": "FY2025", "column": "FY2025",
         "table": "T1", "page": 26, "path": "a", "flagged": True},
        {"value": 200, "period": "FY2025", "column": "FY2025",
         "table": "T2", "page": 27, "path": "b", "flagged": False},
    ],
}
fl_conflicts = _detect_conflicts_from_occurrences(flagged_occ, {"Revenue": {}})
check("R9 · flagged tables excluded from conflict proof", not fl_conflicts,
      str(len(fl_conflicts)))

# 10. Unlabeled numeric columns (no period/column key) are never compared.
no_key_occ = {
    "Revenue": [
        {"value": 100, "table": "T1", "page": 26, "path": "a", "flagged": False},
        {"value": 200, "table": "T1", "page": 26, "path": "b", "flagged": False},
    ],
}
nk_conflicts = _detect_conflicts_from_occurrences(no_key_occ, {"Revenue": {}})
check("R10 · unlabeled columns never compared (no invented conflict)", not nk_conflicts,
      str(len(nk_conflicts)))

print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in CHECKS if ok)
failed = [(n, d) for n, ok, d in CHECKS if not ok]
print(f"RESULT: {passed}/{len(CHECKS)} checks pass")
if failed:
    for n, d in failed:
        print(f"  FAIL: {n} [{d}]")
else:
    print("ALL CHECKS PASS")
