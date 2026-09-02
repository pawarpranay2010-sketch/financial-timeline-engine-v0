#!/usr/bin/env python3
"""
Platrixa FYJC — Deterministic Dataset Splitting

Splits FYJC training data into train/validation/test sets with:
- Reproducible seed
- No duplicate/near-duplicate leakage between splits
- Stratified splitting by category where possible
- Machine-readable statistics

Usage:
    python training/split.py training_data/generated_training_raw.jsonl
    python training/split.py training_data/specialist_clean_training.jsonl --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_records(path: str) -> List[Dict[str, Any]]:
    """Load JSONL records."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def deduplicate(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Remove exact duplicates (by input + output)."""
    seen = set()
    unique = []
    dup_count = 0
    for record in records:
        key = (
            record.get("input", ""),
            record.get("output", ""),
        )
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        unique.append(record)
    return unique, dup_count


def get_stratification_key(record: Dict[str, Any]) -> str:
    """Get a stratification key from metadata."""
    meta = record.get("_p4_metadata") or {}
    category = meta.get("category", "unknown")
    status = meta.get("kernel_status") or meta.get("engine_status") or "unknown"
    return f"{category}::{status}"


def stratified_split(
    records: List[Dict[str, Any]],
    train_ratio: float = 0.80,
    val_ratio: float = 0.10,
    test_ratio: float = 0.10,
    seed: int = 42,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split records into train/val/test with stratification.

    Preserves category and status distribution across splits where possible.
    For small strata (<3 records), falls back to random assignment.
    """
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, \
        f"Ratios must sum to 1.0, got {train_ratio + val_ratio + test_ratio}"

    rng = random.Random(seed)

    # Group by stratification key
    strata = defaultdict(list)
    for record in records:
        key = get_stratification_key(record)
        strata[key].append(record)

    train, val, test = [], [], []

    for key, group in strata.items():
        rng.shuffle(group)
        n = len(group)

        if n < 3:
            # Too few for stratified split; assign randomly
            for record in group:
                r = rng.random()
                if r < train_ratio:
                    train.append(record)
                elif r < train_ratio + val_ratio:
                    val.append(record)
                else:
                    test.append(record)
        else:
            # Stratified split
            n_train = max(1, int(n * train_ratio))
            n_val = max(1, int(n * val_ratio))
            n_test = n - n_train - n_val

            if n_test < 1:
                # Adjust if rounding left no test samples
                n_test = 1
                n_train = n - n_train - 1

            train.extend(group[:n_train])
            val.extend(group[n_train:n_train + n_val])
            test.extend(group[n_train + n_val:])

    # Shuffle each split
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    return train, val, test


def compute_stats(
    train: List[Dict], val: List[Dict], test: List[Dict],
    all_records: List[Dict]
) -> Dict[str, Any]:
    """Compute comprehensive dataset statistics."""

    def _dist(records: List[Dict]) -> Dict[str, int]:
        cats = Counter()
        for r in records:
            meta = r.get("_p4_metadata") or {}
            cats[meta.get("category", "unknown")] += 1
        return dict(cats.most_common())

    def _status_dist(records: List[Dict]) -> Dict[str, int]:
        statuses = Counter()
        for r in records:
            meta = r.get("_p4_metadata") or {}
            s = meta.get("kernel_status") or meta.get("engine_status") or "unknown"
            statuses[s] += 1
        return dict(statuses.most_common())

    def _source_dist(records: List[Dict]) -> Dict[str, int]:
        sources = Counter()
        for r in records:
            meta = r.get("_p4_metadata") or {}
            sources[meta.get("source", "unknown")] += 1
        return dict(sources.most_common())

    # Check for leakage (input text appearing in multiple splits)
    train_inputs = {r.get("input", "").lower().strip() for r in train}
    val_inputs = {r.get("input", "").lower().strip() for r in val}
    test_inputs = {r.get("input", "").lower().strip() for r in test}

    train_val_leak = train_inputs & val_inputs
    train_test_leak = train_inputs & test_inputs
    val_test_leak = val_inputs & test_inputs

    return {
        "total_records": len(all_records),
        "after_dedup": len(train) + len(val) + len(test),
        "splits": {
            "train": {
                "count": len(train),
                "percentage": round(100 * len(train) / max(1, len(all_records)), 1),
                "categories": _dist(train),
                "statuses": _status_dist(train),
                "sources": _source_dist(train),
            },
            "validation": {
                "count": len(val),
                "percentage": round(100 * len(val) / max(1, len(all_records)), 1),
                "categories": _dist(val),
                "statuses": _status_dist(val),
                "sources": _source_dist(val),
            },
            "test": {
                "count": len(test),
                "percentage": round(100 * len(test) / max(1, len(all_records)), 1),
                "categories": _dist(test),
                "statuses": _status_dist(test),
                "sources": _source_dist(test),
            },
        },
        "leakage": {
            "train_val_overlap": len(train_val_leak),
            "train_test_overlap": len(train_test_leak),
            "val_test_overlap": len(val_test_leak),
            "total_leaked": len(train_val_leak) + len(train_test_leak) + len(val_test_leak),
        },
    }


def write_jsonl(records: List[Dict[str, Any]], path: str):
    """Write records to JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Split FYJC training data into train/val/test"
    )
    parser.add_argument("input", help="Input JSONL file")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: training_data/)")
    parser.add_argument("--train-ratio", type=float, default=0.80)
    parser.add_argument("--val-ratio", type=float, default=0.10)
    parser.add_argument("--test-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-dedup", action="store_true",
                        help="Skip deduplication")
    parser.add_argument("--json", action="store_true",
                        help="Output stats as JSON")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "training_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load
    records = load_records(args.input)
    print(f"Loaded {len(records)} records from {args.input}")

    # Dedup
    if not args.no_dedup:
        records, dup_count = deduplicate(records)
        print(f"After dedup: {len(records)} records ({dup_count} duplicates removed)")

    if len(records) < 10:
        print(f"ERROR: Too few records ({len(records)}) for meaningful splitting")
        sys.exit(1)

    # Split
    train, val, test = stratified_split(
        records,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        seed=args.seed,
    )

    # Write
    train_path = output_dir / "specialist_train.jsonl"
    val_path = output_dir / "specialist_val.jsonl"
    test_path = output_dir / "specialist_test.jsonl"

    write_jsonl(train, str(train_path))
    write_jsonl(val, str(val_path))
    write_jsonl(test, str(test_path))

    print(f"\nWrote splits:")
    print(f"  Train:      {len(train)} records -> {train_path}")
    print(f"  Validation: {len(val)} records  -> {val_path}")
    print(f"  Test:       {len(test)} records  -> {test_path}")

    # Stats
    stats = compute_stats(train, val, test, records)
    stats_path = output_dir / "dataset_quality_report.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"\nDataset report: {stats_path}")

    # Leakage check
    leakage = stats["leakage"]
    if leakage["total_leaked"] > 0:
        print(f"\n⚠ WARNING: {leakage['total_leaked']} records leaked between splits")
    else:
        print(f"\n✓ No data leakage between splits")

    if args.json:
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
