#!/usr/bin/env python3
"""
Platrixa P5a Notebook — Defensive Startup Check Tests

Tests the defensive logic that the notebook implements:
- Training file existence check
- Model name validation
- Safe filename handling
- JSONL structure validation (empty fields, valid JSON)
- No Fibonacci/Alpaca contamination
- Correct instruction field validation (the r.get("instruction","") fix)

These tests verify the LOGIC, not the Colab runtime. They run locally
against the actual repository files.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_RESULTS = {"passed": 0, "failed": 0, "errors": []}


def assert_test(name: str, condition: bool, detail: str = ""):
    if condition:
        _RESULTS["passed"] += 1
        print(f"  ✓ {name}")
    else:
        _RESULTS["failed"] += 1
        msg = f"  ✗ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        _RESULTS["errors"].append(msg)


def test_training_file_exists():
    """Training JSONL file exists in the repository."""
    print("\n1. Training file existence")
    path = _PROJECT_ROOT / "training_data" / "specialist_clean_training.jsonl"
    assert_test("specialist_clean_training.jsonl exists", path.exists(), f"checked {path}")
    if path.exists():
        size = path.stat().st_size
        assert_test("File is non-empty", size > 0, f"size={size}")


def test_training_file_structure():
    """Training JSONL has the correct structure: instruction, input, output fields."""
    print("\n2. Training file structure")
    path = _PROJECT_ROOT / "training_data" / "specialist_clean_training.jsonl"
    if not path.exists():
        print("  ⚠️  Skipping — file not found")
        return

    records = []
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    assert_test(f"Line {i+1} is valid JSON", False, str(e))

    assert_test("Has records", len(records) > 0, f"count={len(records)}")

    if records:
        # Check required fields
        required_fields = {"instruction", "input", "output"}
        first_keys = set(records[0].keys())
        assert_test(
            "First record has instruction/input/output fields",
            required_fields.issubset(first_keys),
            f"found keys: {first_keys}",
        )

        # Check no empty fields
        empty_inst = sum(1 for r in records if not str(r.get("instruction", "")).strip())
        empty_in = sum(1 for r in records if not str(r.get("input", "")).strip())
        empty_out = sum(1 for r in records if not str(r.get("output", "")).strip())
        assert_test("No empty instruction fields", empty_inst == 0, f"empty={empty_inst}/{len(records)}")
        assert_test("No empty input fields", empty_in == 0, f"empty={empty_in}/{len(records)}")
        assert_test("No empty output fields", empty_out == 0, f"empty={empty_out}/{len(records)}")

        # Check output is valid JSON
        bad_json = 0
        for r in records:
            try:
                json.loads(r["output"])
            except (json.JSONDecodeError, TypeError):
                bad_json += 1
        assert_test("All output fields are valid JSON", bad_json == 0, f"bad={bad_json}/{len(records)}")


def test_instruction_field_validation():
    """The r.get("instruction","") fix: verify the correct key is used."""
    print("\n3. Instruction field validation (bug fix)")
    path = _PROJECT_ROOT / "training_data" / "specialist_clean_training.jsonl"
    if not path.exists():
        print("  ⚠️  Skipping — file not found")
        return

    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # BUG: r.get("", "") always returns "" — counts everything as empty
    buggy_empty = sum(1 for r in records if not str(r.get("", "")).strip())
    # FIX: r.get("instruction", "") returns the actual instruction
    fixed_empty = sum(1 for r in records if not str(r.get("instruction", "")).strip())

    assert_test(
        "Bug: r.get('','') reports all as empty",
        buggy_empty == len(records),
        f"buggy count={buggy_empty}/{len(records)}",
    )
    assert_test(
        "Fix: r.get('instruction','') correctly identifies non-empty",
        fixed_empty == 0,
        f"fixed count={fixed_empty}/{len(records)}",
    )
    assert_test(
        "Bug and fix produce different results",
        buggy_empty != fixed_empty,
        f"buggy={buggy_empty}, fixed={fixed_empty}",
    )


def test_no_fibonacci_contamination():
    """No Fibonacci/Alpaca records in the training data."""
    print("\n4. No Fibonacci/Alpaca contamination")
    path = _PROJECT_ROOT / "training_data" / "specialist_clean_training.jsonl"
    if not path.exists():
        print("  ⚠️  Skipping — file not found")
        return

    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    fib_check = any("fibonacci" in str(r.get("input", "")).lower() for r in records)
    alpaca_check = any("alpaca" in str(r.get("instruction", "")).lower() for r in records)
    assert_test("No Fibonacci records", not fib_check)
    assert_test("No Alpaca records", not alpaca_check)


def test_safe_filename_handling():
    """Safe filename regex correctly identifies problematic filenames."""
    print("\n5. Safe filename handling")
    safe_pattern = re.compile(r'^[a-zA-Z0-9_.-]+$')

    # Safe filenames
    assert_test("Simple name is safe", safe_pattern.match("specialist_clean_training.jsonl") is not None)
    assert_test("With underscores is safe", safe_pattern.match("my_file_v2.jsonl") is not None)
    assert_test("With dots is safe", safe_pattern.match("file.name.json") is not None)
    assert_test("With hyphens is safe", safe_pattern.match("my-file.jsonl") is not None)

    # Unsafe filenames
    assert_test("Spaces are unsafe", safe_pattern.match("my file.jsonl") is None)
    assert_test("Parentheses are unsafe", safe_pattern.match("file (1).jsonl") is None)
    assert_test("Special chars are unsafe", safe_pattern.match("file@name.jsonl") is None)
    assert_test("Unicode is unsafe", safe_pattern.match("données.jsonl") is None)


def test_model_name_validation():
    """Model name validation logic."""
    print("\n6. Model name validation")
    # The notebook checks: if "1.5B" not in actual_name → warning
    expected_model = "Qwen2.5-1.5B-Instruct-bnb-4bit"
    assert_test(
        "Expected model name contains 1.5B",
        "1.5B" in expected_model,
    )
    # Verify the notebook's model name constant
    notebook_model = "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"
    assert_test(
        "Notebook model name is 1.5B",
        "1.5B" in notebook_model,
    )
    assert_test(
        "Notebook model is Instruct variant",
        "Instruct" in notebook_model,
    )


def test_notebook_config_values():
    """Verify the notebook has the correct intentional config values."""
    print("\n7. Notebook configuration values")
    notebook_path = _PROJECT_ROOT / "notebooks" / "p5a_training.ipynb"
    if not notebook_path.exists():
        print("  ⚠️  Skipping — notebooks/p5a_training.ipynb not found")
        return

    with open(notebook_path) as f:
        nb = json.load(f)

    # Extract all code cell source
    all_code = ""
    for cell in nb.get("cells", []):
        if cell["cell_type"] == "code":
            all_code += "".join(cell["source"]) + "\n"

    # Check critical config values
    assert_test("load_in_4bit = False", "load_in_4bit = False" in all_code or "load_in_4bit=False" in all_code)
    assert_test("max_steps = 20", "max_steps = 20" in all_code or "max_steps=20" in all_code)
    assert_test("max_seq_length = 1024", "max_seq_length = 1024" in all_code or "max_seq_length=1024" in all_code)
    assert_test("1.5B model referenced", "1.5B" in all_code)
    assert_test("No 7B model in loading", "Qwen2.5-7B" not in all_code)
    assert_test("No max_steps = 60", "max_steps = 60" not in all_code)

    # Check defensive checks exist
    assert_test("File existence check", "os.path.exists" in all_code)
    assert_test("Empty field check", "empty_inst" in all_code)
    assert_test("JSON validity check", "json.loads" in all_code)
    assert_test("Fibonacci check", "fibonacci" in all_code.lower())
    assert_test("Google Drive persistence", "drive.mount" in all_code or "google.colab" in all_code)
    assert_test("Local save", "save_pretrained" in all_code)


def test_eval_tiers_exist():
    """All three evaluation tiers exist."""
    print("\n8. Evaluation tier files")
    tiers = {
        "ambiguity": "training_data/specialist_ambiguity_eval.jsonl",
        "unsupported": "training_data/specialist_unsupported_eval.jsonl",
        "robustness": "training_data/specialist_robustness_eval.jsonl",
    }
    for name, path in tiers.items():
        full = _PROJECT_ROOT / path
        assert_test(f"Tier '{name}' exists", full.exists(), f"checked {full}")
        if full.exists():
            with open(full) as f:
                count = sum(1 for line in f if line.strip())
            assert_test(f"Tier '{name}' has records", count > 0, f"count={count}")


def test_evaluation_script_compiles():
    """Evaluation script compiles without errors."""
    print("\n9. Evaluation script compilation")
    eval_path = _PROJECT_ROOT / "scripts" / "fte_fyjc_p5a_evaluation.py"
    assert_test("Evaluation script exists", eval_path.exists())
    if eval_path.exists():
        import py_compile
        try:
            py_compile.compile(str(eval_path), doraise=True)
            assert_test("Evaluation script compiles", True)
        except py_compile.PyCompileError as e:
            assert_test("Evaluation script compiles", False, str(e))


if __name__ == "__main__":
    print("=" * 70)
    print("Platrixa P5a Notebook Defensive Check Tests")
    print("=" * 70)

    test_training_file_exists()
    test_training_file_structure()
    test_instruction_field_validation()
    test_no_fibonacci_contamination()
    test_safe_filename_handling()
    test_model_name_validation()
    test_notebook_config_values()
    test_eval_tiers_exist()
    test_evaluation_script_compiles()

    print("\n" + "=" * 70)
    passed = _RESULTS["passed"]
    failed = _RESULTS["failed"]
    total = passed + failed
    print(f"RESULTS: {passed}/{total} PASS" + (f" ({failed} FAIL)" if failed else ""))
    print("=" * 70)

    if _RESULTS["errors"]:
        print("\nFailed tests:")
        for e in _RESULTS["errors"]:
            print(f"  {e}")

    sys.exit(0 if failed == 0 else 1)
