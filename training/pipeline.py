#!/usr/bin/env python3
"""
Platrixa FYJC — Unified Dataset Preparation Pipeline

Runs the complete pipeline:
    1. Load existing data (candidate cases + specialist_clean_training)
    2. Generate new cases from templates
    3. Validate all records
    4. Split into train/val/test
    5. Format for Qwen2.5 training
    6. Generate quality report

All CPU-safe. No GPU required.

Usage:
    python training/pipeline.py
    python training/pipeline.py --max-new 200 --seed 42
    python training/pipeline.py --with-kernel
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def run_pipeline(
    max_new: int = 500,
    seed: int = 42,
    with_kernel: bool = False,
    from_existing_only: bool = False,
) -> dict:
    """Run the complete dataset preparation pipeline.

    Returns a summary dict.
    """
    from training.generate import TrainingDataGenerator
    from training.validate import DatasetValidator
    from training.split import (
        load_records, deduplicate, stratified_split,
        compute_stats, write_jsonl,
    )
    from training.format import format_dataset, write_jsonl as write_formatted

    project_root = _PROJECT_ROOT
    output_dir = project_root / "training_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "steps": [],
        "start_time": time.time(),
    }

    # ── Step 1: Load existing data ──────────────────────────────────────
    print("=" * 60)
    print("STEP 1: Loading existing data")
    print("=" * 60)

    gen = TrainingDataGenerator(seed=seed)
    all_records = []

    existing_training = output_dir / "specialist_clean_training.jsonl"
    if existing_training.exists():
        records = gen.load_existing_training(str(existing_training))
        all_records.extend(records)
        print(f"  Loaded {len(records)} records from specialist_clean_training.jsonl")

    candidate_jsonl = project_root / "platrixa_ai_candidate_cases.jsonl"
    if candidate_jsonl.exists():
        cases = gen.load_existing_cases(str(candidate_jsonl))
        # Convert to training format
        from training.generate import (
            _infer_tx_type, _extract_parties_from_kernel,
            _extract_amounts, _infer_payment_method,
            _detect_ambiguities,
        )

        for case in cases:
            text = case.get("input_text", "")
            if not text:
                continue
            understanding = case.get("understanding") or {}
            output_dict = {
                "transaction_type": (understanding.get("transaction_type")
                                     or _infer_tx_type(text, case)),
                "parties": (understanding.get("parties")
                            or _extract_parties_from_kernel(case, text)),
                "amounts": _extract_amounts(case),
                "payment_method": _infer_payment_method(text, case),
                "references": [],
                "ambiguities": _detect_ambiguities(
                    text, case, case.get("status", "")
                ),
                "grounding": {
                    "all_fields_explicitly_grounded": True,
                    "inferred_fields": [],
                },
            }
            record = {
                "instruction": (
                    "Parse the student's accounting language into a grounded "
                    "structured interpretation. Do not invent missing information. "
                    "Identify: transaction_type, parties, amounts, payment_method, "
                    "references, ambiguities, and grounding status."
                ),
                "input": text,
                "output": json.dumps(output_dict, ensure_ascii=False),
                "_p4_metadata": {
                    "problem_id": case.get("case_id"),
                    "category": case.get("category"),
                    "subcategory": case.get("subcategory"),
                    "kernel_status": case.get("status"),
                    "content_hash": gen._hash_text(text),
                    "source": "existing_candidate_cases",
                },
            }
            all_records.append(record)

        print(f"  Loaded {len(cases)} candidate cases")

    print(f"  Total loaded: {len(all_records)}")
    summary["steps"].append({
        "step": "load", "count": len(all_records),
    })

    # ── Step 2: Generate new cases ──────────────────────────────────────
    if not from_existing_only:
        print("\n" + "=" * 60)
        print("STEP 2: Generating new cases from templates")
        print("=" * 60)

        new_cases = gen.generate_from_templates(max_cases=max_new, min_length=15)
        print(f"  Generated {len(new_cases)} new template cases")

        for i, case in enumerate(new_cases):
            output_dict = {
                "transaction_type": case.get("tx_hint", "unknown").lower(),
                "parties": [],
                "amounts": [],
                "payment_method": "UNKNOWN",
                "references": [],
                "ambiguities": _detect_ambiguities(
                    case["input_text"], {}, case.get("expected_status", "")
                ),
                "grounding": {
                    "all_fields_explicitly_grounded": False,
                    "inferred_fields": ["all"],
                },
            }
            record = {
                "instruction": (
                    "Parse the student's accounting language into a grounded "
                    "structured interpretation. Do not invent missing information. "
                    "Identify: transaction_type, parties, amounts, payment_method, "
                    "references, ambiguities, and grounding status."
                ),
                "input": case["input_text"],
                "output": json.dumps(output_dict, ensure_ascii=False),
                "_p4_metadata": {
                    "problem_id": f"GEN{i:04d}",
                    "category": case["category"],
                    "subcategory": case["subcategory"],
                    "content_hash": gen._hash_text(case["input_text"]),
                    "source": "generated_template",
                },
            }
            all_records.append(record)

        print(f"  Total after generation: {len(all_records)}")
        summary["steps"].append({
            "step": "generate", "count": len(new_cases),
        })

    # ── Step 3: Validate ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 3: Validating records")
    print("=" * 60)

    raw_path = str(output_dir / "pipeline_raw.jsonl")
    with open(raw_path, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    validator = DatasetValidator()
    validation = validator.validate_file(raw_path)
    print(f"  Total: {validation['total_records']}")
    print(f"  Passed: {validation['passed']}")
    print(f"  Failed: {validation['failed']}")
    if validation["errors"]:
        print(f"  Errors: {len(validation['errors'])}")
        for err in validation["errors"][:5]:
            print(f"    ✗ {err}")

    # Remove failed records
    valid_records = [
        all_records[i] for i, result in enumerate(validator.results)
        if result.passed
    ]
    print(f"  Valid records: {len(valid_records)}")
    summary["steps"].append({
        "step": "validate",
        "total": validation["total_records"],
        "passed": validation["passed"],
        "failed": validation["failed"],
    })

    # ── Step 4: Deduplicate ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 4: Deduplication")
    print("=" * 60)

    deduped, dup_count = deduplicate(valid_records)
    print(f"  Before dedup: {len(valid_records)}")
    print(f"  After dedup:  {len(deduped)}")
    print(f"  Removed:      {dup_count}")
    summary["steps"].append({
        "step": "dedup",
        "before": len(valid_records),
        "after": len(deduped),
        "removed": dup_count,
    })

    # ── Step 5: Split ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 5: Splitting into train/val/test")
    print("=" * 60)

    train, val, test = stratified_split(deduped, seed=seed)
    print(f"  Train:      {len(train)}")
    print(f"  Validation: {len(val)}")
    print(f"  Test:       {len(test)}")

    write_jsonl(train, str(output_dir / "specialist_train.jsonl"))
    write_jsonl(val, str(output_dir / "specialist_val.jsonl"))
    write_jsonl(test, str(output_dir / "specialist_test.jsonl"))

    stats = compute_stats(train, val, test, deduped)
    with open(output_dir / "dataset_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"  Leakage: {stats['leakage']['total_leaked']} records")
    summary["steps"].append({
        "step": "split",
        "train": len(train),
        "validation": len(val),
        "test": len(test),
        "leakage": stats["leakage"]["total_leaked"],
    })

    # ── Step 6: Format ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 6: Formatting for Qwen2.5 training")
    print("=" * 60)

    for split_name, split_records in [("train", train), ("val", val), ("test", test)]:
        formatted = format_dataset(split_records)
        formatted_path = output_dir / f"specialist_{split_name}_formatted.jsonl"
        write_formatted(formatted, str(formatted_path))
        print(f"  {split_name}: {len(formatted)} formatted -> {formatted_path}")

    summary["steps"].append({"step": "format", "splits_formatted": 3})

    # ── Summary ─────────────────────────────────────────────────────────
    summary["end_time"] = time.time()
    summary["duration_seconds"] = round(summary["end_time"] - summary["start_time"], 1)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Duration: {summary['duration_seconds']}s")
    print(f"Output:   {output_dir}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Run the complete FYJC dataset preparation pipeline"
    )
    parser.add_argument("--max-new", type=int, default=500,
                        help="Maximum new cases to generate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--with-kernel", action="store_true",
                        help="Run kernel on new cases (slow)")
    parser.add_argument("--from-existing-only", action="store_true",
                        help="Only use existing data")
    args = parser.parse_args()

    run_pipeline(
        max_new=args.max_new,
        seed=args.seed,
        with_kernel=args.with_kernel,
        from_existing_only=args.from_existing_only,
    )


if __name__ == "__main__":
    main()
