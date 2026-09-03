#!/usr/bin/env python3
"""
Platrixa FYJC — Provider-Independent Training Format Formatter

Converts validated FYJC training records into the conversational format
required by Qwen2.5-1.5B-Instruct for supervised fine-tuning.

The formatter produces the exact Alpaca-style prompt format that the
existing Colab notebook uses, but is provider-independent.

Model learns:
    natural-language transaction → StructuredInterpretation JSON

Model does NOT learn:
    - Deterministic accounting (the kernel handles that)
    - Journal entry generation
    - Ledger management

Usage:
    python training/format.py training_data/specialist_train.jsonl
    python training/format.py training_data/specialist_val.jsonl --output-dir formatted/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Prompt template (matches existing Colab notebook exactly)
# ---------------------------------------------------------------------------

ALPACA_PROMPT = """Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.

### Instruction:
{}

### Input:
{}

### Response:
{}"""


# ---------------------------------------------------------------------------
# System instruction (matches fyjc_export.py)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = (
    "You are the FYJC accounting language-understanding specialist. "
    "Parse the student's accounting language into a grounded structured "
    "interpretation with exactly these 18 fields: transaction_type, "
    "parties, amounts, payment_method, references, ambiguities, grounding, "
    "transaction_type_enum, payment_method_enum, ambiguity_flags, "
    "referenced_transaction_index, referenced_party, referenced_amount, "
    "field_confidences, overall_confidence, suggested_status, safety_flags, "
    "scope_flags. Do not invent missing information. Never produce journal "
    "entries, debit/credit decisions, or accounting conclusions. Output only "
    "machine-readable JSON with exactly these fields."
)


def format_record(record: Dict[str, Any], eos_token: str = "") -> Dict[str, Any]:
    """Format a single training record into the Qwen training format.

    Input: {instruction, input, output, _p4_metadata}
    Output: {text} where text is the full Alpaca prompt with response.

    The response target is the output dict serialized as compact,
    machine-readable JSON (valid JSON with double quotes), so the model
    learns to emit the exact Phase 1 18-field contract.
    """
    instruction = record.get("instruction") or SYSTEM_INSTRUCTION
    input_text = record.get("input", "")
    output_value = record.get("output", "")

    # Serialize dict outputs as compact valid JSON (never Python repr).
    if isinstance(output_value, (dict, list)):
        output_str = json.dumps(
            output_value, ensure_ascii=False, separators=(",", ":")
        )
    else:
        output_str = str(output_value)

    # Format as Alpaca prompt
    text = ALPACA_PROMPT.format(instruction, input_text, output_str)
    if eos_token:
        text += eos_token

    return {"text": text}


def format_dataset(
    records: List[Dict[str, Any]],
    eos_token: str = "",
) -> List[Dict[str, Any]]:
    """Format a list of records into the Qwen training format."""
    formatted = []
    for record in records:
        formatted.append(format_record(record, eos_token))
    return formatted


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load records from JSONL."""
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


def write_jsonl(records: List[Dict[str, Any]], path: str):
    """Write records to JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Format FYJC training data for Qwen2.5"
    )
    parser.add_argument("files", nargs="+",
                        help="Input JSONL files to format")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory")
    parser.add_argument("--eos-token", default="",
                        help="EOS token to append (auto-detected in training)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate output format")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "training_data"

    for input_path in args.files:
        records = load_jsonl(input_path)
        if not records:
            print(f"Skipping empty file: {input_path}")
            continue

        formatted = format_dataset(records, args.eos_token)

        # Output filename
        input_name = Path(input_path).stem
        output_path = output_dir / f"{input_name}_formatted.jsonl"
        output_dir.mkdir(parents=True, exist_ok=True)

        write_jsonl(formatted, str(output_path))
        print(f"Formatted {len(formatted)} records: {input_path} -> {output_path}")

        # Validate if requested
        if args.validate:
            print("  Validation:")
            valid = 0
            invalid = 0
            for i, rec in enumerate(formatted):
                text = rec.get("text", "")
                if "### Instruction:" in text and "### Response:" in text:
                    valid += 1
                else:
                    invalid += 1
                    if invalid <= 3:
                        print(f"  ✗ Record {i}: missing expected prompt markers")
            print(f"  Valid: {valid}, Invalid: {invalid}")


if __name__ == "__main__":
    main()
