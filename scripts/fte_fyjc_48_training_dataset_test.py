#!/usr/bin/env python3
"""
Platrixa — Phase 5: Training Dataset Tests
=============================================

Tests for the 1,000-example FYJC specialist training dataset.

Categories:
  A. Dataset structure and count
  B. 18-field contract compliance
  C. Forbidden field rejection
  D. Enum validation
  E. Confidence ranges
  F. Duplicate/leakage detection
  G. Category coverage
  H. Split correctness
  I. REVIEW_REQUIRED policy
  J. Grounding compatibility
  K. Regression: Phase 1-4 + legacy
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_contract import (
    ALL_VALID_FIELDS,
    VALID_TRANSACTION_TYPES,
    VALID_PAYMENT_METHODS,
    VALID_AMBIGUITY_TYPES,
    VALID_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS,
    VALID_GROUNDING_LEVELS,
)
from backend.maths.schema_verifier import validate_structured_interpretation

passed = 0
failed = 0
skipped = 0
results = []


def _check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        results.append(("✅", name, detail))
    else:
        failed += 1
        results.append(("❌", name, detail))


def _skip(name: str, reason: str):
    global skipped
    skipped += 1
    results.append(("⏭️", name, reason))


# =========================================================================
# Load dataset
# =========================================================================

project_root = Path(__file__).resolve().parent.parent
dataset_path = project_root / "training_data" / "fyjc_specialist_1000.jsonl"
train_path = project_root / "training_data" / "fyjc_specialist_train.jsonl"
val_path = project_root / "training_data" / "fyjc_specialist_validation.jsonl"
test_path = project_root / "training_data" / "fyjc_specialist_test.jsonl"

if not dataset_path.exists():
    print(f"❌ Dataset not found: {dataset_path}")
    print("Run: python -m training.build_1000")
    sys.exit(1)

records = []
with open(dataset_path, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

print(f"\nLoaded {len(records)} records from {dataset_path}")


# =========================================================================
# A. DATASET STRUCTURE AND COUNT
# =========================================================================

print("\n" + "=" * 70)
print("A. DATASET STRUCTURE AND COUNT")
print("=" * 70)

_check("A1: Exactly 1,000 records", len(records) == 1000, f"count={len(records)}")

# Top-level structure
all_have_id = all("id" in r for r in records)
all_have_input = all("input" in r for r in records)
all_have_output = all("output" in r for r in records)
all_have_metadata = all("metadata" in r for r in records)

_check("A2: All records have 'id'", all_have_id)
_check("A3: All records have 'input'", all_have_input)
_check("A4: All records have 'output'", all_have_output)
_check("A5: All records have 'metadata'", all_have_metadata)

# Input is non-empty string
all_inputs_str = all(isinstance(r.get("input"), str) and r["input"].strip() for r in records)
_check("A6: All inputs are non-empty strings", all_inputs_str)

# Output is dict
all_outputs_dict = all(isinstance(r.get("output"), dict) for r in records)
_check("A7: All outputs are dicts", all_outputs_dict)


# =========================================================================
# B. 18-FIELD CONTRACT COMPLIANCE
# =========================================================================

print("\n" + "=" * 70)
print("B. 18-FIELD CONTRACT COMPLIANCE")
print("=" * 70)

missing_field_records = 0
for r in records:
    out = r.get("output", {})
    missing = ALL_VALID_FIELDS - set(out.keys())
    if missing:
        missing_field_records += 1

_check("B1: All 18 fields present in every output", missing_field_records == 0,
       f"records_with_missing={missing_field_records}")

# Validate each output against Phase 1 schema
schema_failures = 0
for r in records:
    report = validate_structured_interpretation(r.get("output", {}))
    if not report.valid:
        schema_failures += 1

_check("B2: All outputs pass Phase 1 schema validation", schema_failures == 0,
       f"failures={schema_failures}")


# =========================================================================
# C. FORBIDDEN FIELD REJECTION
# =========================================================================

print("\n" + "=" * 70)
print("C. FORBIDDEN FIELD REJECTION")
print("=" * 70)

FORBIDDEN = {"journal", "debit_lines", "credit_lines", "ledger",
             "balances", "debit_account", "credit_account", "journal_entry"}

forbidden_found = 0
for r in records:
    out = r.get("output", {})
    found = FORBIDDEN & set(out.keys())
    if found:
        forbidden_found += 1

_check("C1: No forbidden accounting fields in any output", forbidden_found == 0,
       f"records_with_forbidden={forbidden_found}")


# =========================================================================
# D. ENUM VALIDATION
# =========================================================================

print("\n" + "=" * 70)
print("D. ENUM VALIDATION")
print("=" * 70)

bad_tx_enum = 0
bad_pm_enum = 0
bad_ambig = 0
bad_safety = 0
bad_scope = 0

for r in records:
    out = r.get("output", {})
    tx = out.get("transaction_type_enum", "")
    if tx and tx not in VALID_TRANSACTION_TYPES:
        bad_tx_enum += 1
    pm = out.get("payment_method_enum", "")
    if pm and pm not in VALID_PAYMENT_METHODS:
        bad_pm_enum += 1
    for flag in out.get("ambiguity_flags", []):
        if flag not in VALID_AMBIGUITY_TYPES:
            bad_ambig += 1
    for flag in out.get("safety_flags", []):
        if flag not in VALID_SAFETY_FLAGS:
            bad_safety += 1
    for flag in out.get("scope_flags", []):
        if flag not in VALID_SCOPE_FLAGS:
            bad_scope += 1

_check("D1: All transaction_type_enum values valid", bad_tx_enum == 0, f"invalid={bad_tx_enum}")
_check("D2: All payment_method_enum values valid", bad_pm_enum == 0, f"invalid={bad_pm_enum}")
_check("D3: All ambiguity_flags valid", bad_ambig == 0, f"invalid={bad_ambig}")
_check("D4: All safety_flags valid", bad_safety == 0, f"invalid={bad_safety}")
_check("D5: All scope_flags valid", bad_scope == 0, f"invalid={bad_scope}")


# =========================================================================
# E. CONFIDENCE RANGES
# =========================================================================

print("\n" + "=" * 70)
print("E. CONFIDENCE RANGES")
print("=" * 70)

bad_overall_conf = 0
bad_field_conf = 0
bad_grounding_level = 0

for r in records:
    out = r.get("output", {})
    oc = out.get("overall_confidence", "0.0")
    try:
        c = float(oc)
        if c < 0.0 or c > 1.0:
            bad_overall_conf += 1
    except (ValueError, TypeError):
        bad_overall_conf += 1

    for fc in out.get("field_confidences", []):
        if not isinstance(fc, dict):
            bad_field_conf += 1
            continue
        try:
            c = float(fc.get("confidence", "-1"))
            if c < 0.0 or c > 1.0:
                bad_field_conf += 1
        except (ValueError, TypeError):
            bad_field_conf += 1
        gl = fc.get("grounding", "")
        if gl and gl not in VALID_GROUNDING_LEVELS:
            bad_grounding_level += 1

_check("E1: All overall_confidence in [0.0, 1.0]", bad_overall_conf == 0,
       f"invalid={bad_overall_conf}")
_check("E2: All field_confidences in [0.0, 1.0]", bad_field_conf == 0,
       f"invalid={bad_field_conf}")
_check("E3: All grounding levels valid", bad_grounding_level == 0,
       f"invalid={bad_grounding_level}")


# =========================================================================
# F. DUPLICATE / LEAKAGE DETECTION
# =========================================================================

print("\n" + "=" * 70)
print("F. DUPLICATE / LEAKAGE DETECTION")
print("=" * 70)

# Duplicate IDs
ids = [r.get("id", "") for r in records]
unique_ids = set(ids)
_check("F1: No duplicate IDs", len(ids) == len(unique_ids),
       f"total={len(ids)}, unique={len(unique_ids)}")

# Duplicate inputs (normalized)
norm_inputs = [r.get("input", "").lower().strip() for r in records]
unique_inputs = set(norm_inputs)
_check("F2: No duplicate inputs", len(norm_inputs) == len(unique_inputs),
       f"total={len(norm_inputs)}, unique={len(unique_inputs)}")

# Split leakage
if train_path.exists() and val_path.exists() and test_path.exists():
    def load_ids(path):
        ids = set()
        inputs = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        r = json.loads(line)
                        ids.add(r.get("id", ""))
                        inputs.add(r.get("input", "").lower().strip())
                    except json.JSONDecodeError:
                        pass
        return ids, inputs

    train_ids, train_inputs = load_ids(train_path)
    val_ids, val_inputs = load_ids(val_path)
    test_ids, test_inputs = load_ids(test_path)

    id_overlap = (train_ids & val_ids) | (train_ids & test_ids) | (val_ids & test_ids)
    input_overlap = (train_inputs & val_inputs) | (train_inputs & test_inputs) | (val_inputs & test_inputs)

    _check("F3: No ID overlap between splits", len(id_overlap) == 0,
           f"overlap={len(id_overlap)}")
    _check("F4: No input overlap between splits", len(input_overlap) == 0,
           f"overlap={len(input_overlap)}")
else:
    _skip("F3: Split ID overlap", "Split files not found")
    _skip("F4: Split input overlap", "Split files not found")


# =========================================================================
# G. CATEGORY COVERAGE
# =========================================================================

print("\n" + "=" * 70)
print("G. CATEGORY COVERAGE")
print("=" * 70)

# Transaction type coverage
tx_types = Counter(r.get("output", {}).get("transaction_type_enum", "") for r in records)
_check("G1: At least 5 transaction types represented", len(tx_types) >= 5,
       f"types={len(tx_types)}: {dict(tx_types)}")

# Difficulty coverage
difficulties = Counter(r.get("metadata", {}).get("difficulty", "") for r in records)
_check("G2: Adversarial examples present", difficulties.get("adversarial", 0) > 0,
       f"count={difficulties.get('adversarial', 0)}")
_check("G3: Ambiguous examples present", difficulties.get("ambiguous", 0) > 0,
       f"count={difficulties.get('ambiguous', 0)}")
_check("G4: Incomplete examples present", difficulties.get("incomplete", 0) > 0,
       f"count={difficulties.get('incomplete', 0)}")
_check("G5: Clear examples present", difficulties.get("clear", 0) > 0,
       f"count={difficulties.get('clear', 0)}")

# Language style coverage
styles = Counter(r.get("metadata", {}).get("language_style", "") for r in records)
_check("G6: Standard language present", styles.get("standard", 0) > 0,
       f"count={styles.get('standard', 0)}")
_check("G7: Conversational language present", styles.get("conversational", 0) > 0,
       f"count={styles.get('conversational', 0)}")
_check("G8: Noisy language present", styles.get("noisy", 0) > 0,
       f"count={styles.get('noisy', 0)}")

# Multi-transaction coverage
multi_count = sum(1 for r in records if "MULTI_TRANSACTION" in
                  (r.get("output", {}).get("scope_flags", []) or []))
_check("G9: Multi-transaction examples present", multi_count > 0,
       f"count={multi_count}")

# Reference coverage
ref_count = sum(1 for r in records if r.get("metadata", {}).get("has_reference", False))
_check("G10: Reference examples present", ref_count > 0, f"count={ref_count}")

# Ambiguity coverage
ambig_count = sum(1 for r in records if r.get("metadata", {}).get("is_ambiguous", False))
_check("G11: Ambiguous examples present", ambig_count > 0, f"count={ambig_count}")

# Missing info coverage
missing_party = sum(1 for r in records if "MISSING_PARTY" in
                    (r.get("output", {}).get("ambiguity_flags", []) or []))
missing_amount = sum(1 for r in records if "MISSING_AMOUNT" in
                     (r.get("output", {}).get("ambiguity_flags", []) or []))
missing_pm = sum(1 for r in records if "MISSING_PAYMENT_MODE" in
                 (r.get("output", {}).get("ambiguity_flags", []) or []))
_check("G12: Missing party examples present", missing_party > 0, f"count={missing_party}")
_check("G13: Missing amount examples present", missing_amount > 0, f"count={missing_amount}")
_check("G14: Missing payment mode examples present", missing_pm > 0, f"count={missing_pm}")


# =========================================================================
# H. SPLIT CORRECTNESS
# =========================================================================

print("\n" + "=" * 70)
print("H. SPLIT CORRECTNESS")
print("=" * 70)

if train_path.exists() and val_path.exists() and test_path.exists():
    def count_lines(path):
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    train_n = count_lines(train_path)
    val_n = count_lines(val_path)
    test_n = count_lines(test_path)

    _check("H1: Train = 800", train_n == 800, f"count={train_n}")
    _check("H2: Validation = 100", val_n == 100, f"count={val_n}")
    _check("H3: Test = 100", test_n == 100, f"count={test_n}")
    _check("H4: Total = 1,000", train_n + val_n + test_n == 1000,
           f"total={train_n + val_n + test_n}")
else:
    _skip("H1-H4", "Split files not found")


# =========================================================================
# I. REVIEW_REQUIRED POLICY
# =========================================================================

print("\n" + "=" * 70)
print("I. REVIEW_REQUIRED POLICY")
print("=" * 70)

verified_count = 0
hard_verified = []
clear_review = []
for r in records:
    out = r.get("output", {})
    ss = out.get("suggested_status", "")
    diff = r.get("metadata", {}).get("difficulty", "")
    if ss == "VERIFIED":
        verified_count += 1
        if diff != "clear":
            hard_verified.append(r.get("id"))
    elif diff == "clear":
        clear_review.append(r.get("id"))

# Policy: only fully-grounded clear inputs may be VERIFIED; every
# ambiguous/adversarial/contradictory/unsupported/incomplete input must be
# REVIEW_REQUIRED (an interpretation with insufficient certainty/grounding
# must never be VERIFIED).
_check("I1: Every VERIFIED record is a clear grounded input",
       len(hard_verified) == 0, f"non-clear VERIFIED={hard_verified[:5]}")
_check("I2: Every non-clear record is REVIEW_REQUIRED",
       len(clear_review) == 0, f"clear-but-REVIEW_REQUIRED={clear_review[:5]}")
_check("I3: Both statuses are represented (VERIFIED + REVIEW_REQUIRED)",
       0 < verified_count < len(records), f"verified_count={verified_count}")

# All records should have suggested_status
missing_status = sum(1 for r in records if "suggested_status" not in r.get("output", {}))
_check("I4: All records have suggested_status", missing_status == 0,
       f"missing={missing_status}")


# =========================================================================
# J. GROUNDING COMPATIBILITY
# =========================================================================

print("\n" + "=" * 70)
print("J. GROUNDING COMPATIBILITY")
print("=" * 70)

# All records should have grounding dict
missing_grounding = sum(1 for r in records if "grounding" not in r.get("output", {}))
_check("J1: All records have grounding dict", missing_grounding == 0,
       f"missing={missing_grounding}")

# grounding should be a dict
bad_grounding_type = sum(1 for r in records
                         if not isinstance(r.get("output", {}).get("grounding"), dict))
_check("J2: All grounding values are dicts", bad_grounding_type == 0,
       f"bad_type={bad_grounding_type}")

# field_confidences should be a list
bad_fc_type = sum(1 for r in records
                  if not isinstance(r.get("output", {}).get("field_confidences"), list))
_check("J3: All field_confidences are lists", bad_fc_type == 0,
       f"bad_type={bad_fc_type}")


# =========================================================================
# K. REGRESSION: PHASE 1-4 + LEGACY
# =========================================================================

print("\n" + "=" * 70)
print("K. REGRESSION: PHASE 1-4 + LEGACY")
print("=" * 70)

r_p4 = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_47_grounding_migration_test.py > /dev/null 2>&1")
_check("K1: Phase 4 tests pass", r_p4 == 0, f"exit={r_p4}")

r_p3 = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_46_real_ai_specialist_test.py > /dev/null 2>&1")
_check("K2: Phase 3 tests pass", r_p3 == 0, f"exit={r_p3}")

r_p2 = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_45_ai_specialist_test.py > /dev/null 2>&1")
_check("K3: Phase 2 tests pass", r_p2 == 0, f"exit={r_p2}")

r_p1 = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_44_contract_expansion_test.py > /dev/null 2>&1")
_check("K4: Phase 1 tests pass", r_p1 == 0, f"exit={r_p1}")

r_lu = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_41_contract_unit_tests.py > /dev/null 2>&1")
_check("K5: Legacy unit tests pass", r_lu == 0, f"exit={r_lu}")

r_li = os.system(f"cd {project_root} && python3 scripts/fte_fyjc_41_contract_integration_test.py > /dev/null 2>&1")
_check("K6: Legacy integration tests pass", r_li == 0, f"exit={r_li}")


# =========================================================================
# RESULTS
# =========================================================================

print("\n" + "=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)

for icon, name, detail in results:
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))

print(f"\n{'=' * 70}")
print(f"  PASSED:  {passed}")
print(f"  FAILED:  {failed}")
print(f"  SKIPPED: {skipped}")
print(f"  TOTAL:   {passed + failed + skipped}")
print(f"{'=' * 70}")

if failed > 0:
    print("\n⚠️  FAILURES DETECTED")
    sys.exit(1)
else:
    print("\n✅ ALL TESTS PASSED")
    sys.exit(0)
