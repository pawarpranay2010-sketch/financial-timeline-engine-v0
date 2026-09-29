#!/usr/bin/env python3
"""
PLATRIXA — SECURITY BASELINE ORCHESTRATOR (security_baseline_test)
====================================================================

Executable form of ``reports/SECURITY_BASELINE.md``. Freezes the security
properties that were empirically verified by the security hardening pass into
a durable, machine-checkable contract.

WHAT THIS IS
------------
An ORCHESTRATOR, not a new test suite. It does not duplicate the individual
assertions of the suites it runs — those already exist and are the source of
truth. This file establishes only:

  * every required suite EXISTS,
  * every required suite EXECUTES,
  * every required suite PASSES,
  * ``false_VERIFIED`` remains 0.

ANTI-VACUOUSNESS (Phase 12)
----------------------------
A silently-disappearing test must never read as a pass. The runner therefore
fails when:

  1. a required suite file is missing;
  2. a required suite exits non-zero (which includes import/collection errors);
  3. a suite reports ZERO executed checks — a suite that collects nothing fails;
  4. a suite produces no recognizable pass count in its output;
  5. the false-VERIFIED gate is not 0.

Merely searching for filenames is explicitly NOT treated as passing.

EXIT CODES
----------
  0  baseline holds
  1  baseline violated (a required suite failed, vanished, or was vacuous)
  2  runner error (e.g. interpreter missing)

Run:  python3 scripts/security_baseline_test.py
      python3 scripts/security_baseline_test.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

# Per-suite timeout. The larger suites (boundary closure) take minutes; the
# security suites are fast. A timeout is a FAILURE, never a skip.
_DEFAULT_TIMEOUT = 900

# ---------------------------------------------------------------------------
# Required suites
# ---------------------------------------------------------------------------
# `group` maps to the SECURITY_BASELINE.md section it underwrites.
# `assert_pass` is a regex that must appear in stdout — it is how the runner
# distinguishes "ran and passed N checks" from "ran and printed nothing".

SUITES: List[Dict[str, Any]] = [
    # -- Security regression -------------------------------------------------
    {"name": "fte_sec_01_grounding_fail_closed_test", "group": "security",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    {"name": "fte_sec_02_api_authz_admission_test", "group": "security",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    {"name": "fte_sec_03_webhook_ssrf_seal_test", "group": "security",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    {"name": "fte_sec_04_error_disclosure_test", "group": "security",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    {"name": "fte_sec_05_dependency_policy_test", "group": "security",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    # -- Invariant probe -----------------------------------------------------
    {"name": "_sec_invariant_probe", "group": "invariant",
     "assert_pass": r"INVARIANTS: \d+ passed, 0 failed"},
    # -- Financial truth -----------------------------------------------------
    {"name": "fte_fyjc_grounding_verifier_test", "group": "financial_truth",
     "assert_pass": r"\d+/\d+ checks passed"},
    {"name": "fte_fyjc_53_grounding_verification_wiring_test", "group": "financial_truth",
     "assert_pass": r"\d+/\d+ passed"},
    {"name": "fte_fyjc_52_kernel_boundary_test", "group": "financial_truth",
     "assert_pass": r"\d+/\d+ passed"},
    {"name": "fte_invoice_false_verified_gate_test", "group": "financial_truth",
     "assert_pass": r"false_VERIFIED count:\s*0"},
    # -- API contract --------------------------------------------------------
    {"name": "fte_fyjc_59_status_contract_test", "group": "api_contract",
     "assert_pass": r"PASS — \d+/\d+ checks passed"},
    {"name": "fte_fyjc_80_phase5d_result_contract_test", "group": "api_contract",
     "assert_pass": r"\d+/\d+ (?:PASS|passed)"},
    {"name": "fte_fyjc_81_phase5e_async_documents_test", "group": "api_contract",
     "assert_pass": r"\d+/\d+ PASS"},
    {"name": "fte_fyjc_66_metered_gate_test", "group": "api_contract",
     "assert_pass": r"TOTAL: \d+/\d+ PASS",
     # HISTORY (2026-09-29): this suite previously failed H4 intermittently.
     # Root cause was NOT pool sizing (the earlier note below was wrong) but a
     # cache-key bug in backend/auth/gate.py::_session_factory: the engine
     # cache was LOOKED UP with the raw env URL and STORED under the
     # normalized "postgresql+psycopg2://" URL, so it never hit. Every gate
     # call built a fresh Engine + ConnectionPool; the 40-thread section H
     # created 132 engines, exhausted the server's 100-connection cap
     # ("too many clients already"), and produced spurious fail-closed 503s
     # where the quota contract requires 429.
     #
     # FIXED: the URL is now normalized before the lookup, with a lock so
     # concurrent cold-starts build exactly one engine. Verified 52/52 on
     # three consecutive runs.
     #
     # The annotation is RETAINED so that, if H4 ever fails again, it is
     # immediately identifiable as a real regression rather than the old
     # cache bug resurfacing. The suite STILL FAILS the baseline when it
     # fails — annotated for fast diagnosis, never silently excused.
     "known_condition": "H4 503-instead-of-429 under 40-thread concurrency. "
                        "The engine-cache-key bug that caused this is FIXED "
                        "(gate.py normalizes the URL before the cache lookup, "
                        "+ lock). A recurrence here is a genuine regression, "
                        "not the historical cache bug."},
    {"name": "fte_fyjc_83_phase5g_api_key_lifecycle_test", "group": "api_contract",
     "assert_pass": r"\d+/\d+ checks passed"},
    {"name": "fte_fyjc_84_phase5h_observability_test", "group": "api_contract",
     "assert_pass": r"\d+/\d+ checks passed"},
]

# Patterns that indicate a suite actually executed checks. Used for the
# zero-executed-tests anti-vacuousness rule.
_CHECK_MARKERS = re.compile(
    r"\[PASS\]|OK \[|^\s*OK\b|PASS:|checks passed|\d+/\d+", re.MULTILINE
)

# A suite whose output contains a failing marker is a failure even if the exit
# code is 0 (some suites print a failure banner and still exit 0).
_FAIL_MARKERS = re.compile(r"\[FAIL\]|^\s*FAILED:|✗|❌", re.MULTILINE)


def _run_suite(spec: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Execute one required suite and classify the outcome."""
    name = spec["name"]
    path = _SCRIPTS / f"{name}.py"
    try:
        shown_path = str(path.relative_to(_REPO_ROOT))
    except ValueError:
        shown_path = str(path)
    result: Dict[str, Any] = {
        "name": name,
        "group": spec["group"],
        "file": shown_path,
        "known_condition": spec.get("known_condition"),
        "status": "FAIL",
        "reason": "",
        "exit_code": None,
        "checks_executed": 0,
        "duration_s": 0.0,
    }

    # (1) existence
    if not path.exists():
        result["reason"] = "MISSING: required suite file does not exist"
        return result

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        result["reason"] = f"TIMEOUT after {timeout}s"
        result["duration_s"] = time.monotonic() - started
        return result
    except Exception as exc:  # pragma: no cover
        result["reason"] = f"RUNNER ERROR: {type(exc).__name__}: {exc}"
        result["duration_s"] = time.monotonic() - started
        return result
    result["duration_s"] = round(time.monotonic() - started, 2)
    result["exit_code"] = code
    blob = f"{stdout}\n{stderr}"

    # (2) collection / import errors surface as a traceback on stderr
    if "Traceback (most recent call last)" in blob and code != 0:
        tail = [ln for ln in blob.strip().splitlines() if ln.strip()][-1:]
        result["reason"] = f"COLLECTION/IMPORT ERROR: {tail[0][:160] if tail else 'traceback'}"
        return result

    # (3) zero executed checks is a failure, never a pass
    result["checks_executed"] = len(_CHECK_MARKERS.findall(blob))
    if result["checks_executed"] == 0:
        result["reason"] = "VACUOUS: suite executed zero checks (silent disappearance)"
        return result

    # (4) non-zero exit (import errors, uncaught exceptions)
    if code != 0:
        detail = [ln for ln in (stdout + stderr).strip().splitlines() if ln.strip()]
        result["reason"] = f"EXIT {code}: {detail[-1][:160] if detail else 'no output'}"
        return result

    # (5) explicit failure markers, independent of exit code
    fail_hits = _FAIL_MARKERS.findall(blob)
    if fail_hits:
        result["reason"] = f"FAILURE MARKERS in output ({len(fail_hits)} occurrence(s))"
        return result

    # (6) the suite must print a recognizable pass summary
    if not re.search(spec["assert_pass"], blob):
        result["reason"] = (
            "NO PASS SUMMARY: suite ran but did not print an expected pass line; "
            "it may have been restructured or gutted"
        )
        return result

    result["status"] = "PASS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Platrixa security baseline")
    parser.add_argument("--json", action="store_true",
                        help="emit only the machine-readable summary")
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT,
                        help=f"per-suite timeout seconds (default {_DEFAULT_TIMEOUT})")
    args = parser.parse_args()

    results = [_run_suite(spec, args.timeout) for spec in SUITES]

    groups: Dict[str, Dict[str, int]] = {}
    for r in results:
        g = groups.setdefault(r["group"], {"pass": 0, "fail": 0})
        g["pass" if r["status"] == "PASS" else "fail"] += 1

    failed = [r for r in results if r["status"] != "PASS"]
    summary = {
        "baseline": "reports/SECURITY_BASELINE.md",
        "commit": "87ff8e12a1865d082afb2211d2c4ab8ce74ab30a",
        "suites_total": len(results),
        "suites_passed": len(results) - len(failed),
        "suites_failed": len(failed),
        "groups": groups,
        "results": results,
        "verdict": "BASELINE HOLDS" if not failed else "BASELINE VIOLATED",
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if not failed else 1

    print("=" * 78)
    print("PLATRIXA SECURITY BASELINE")
    print(f"contract: reports/SECURITY_BASELINE.md")
    print("=" * 78)
    for group in ("security", "invariant", "financial_truth", "api_contract"):
        rows = [r for r in results if r["group"] == group]
        if not rows:
            continue
        label = {
            "security": "SECURITY REGRESSION",
            "invariant": "INVARIANT PROBE",
            "financial_truth": "FINANCIAL TRUTH",
            "api_contract": "API CONTRACT",
        }[group]
        print(f"\n--- {label} ---")
        for r in rows:
            mark = "PASS" if r["status"] == "PASS" else "FAIL"
            extra = (f" — {r['reason']}" if r["status"] != "PASS"
                     else f" — {r['checks_executed']} check markers, {r['duration_s']}s")
            line = f"  [{mark}] {r['name']}{extra}"
            if r["status"] != "PASS" and r.get("known_condition"):
                line += f"\n         KNOWN PRE-EXISTING CONDITION: {r['known_condition']}"
            print(line)

    print()
    print("=" * 78)
    print(f"suites: {summary['suites_passed']}/{summary['suites_total']} passed")
    for group, counts in sorted(groups.items()):
        print(f"  {group:<16} {counts['pass']} pass / {counts['fail']} fail")
    if failed:
        print(f"\nBASELINE VIOLATED — {len(failed)} suite(s):")
        for r in failed:
            print(f"  - {r['name']}: {r['reason']}")
            if r.get("known_condition"):
                print(f"      KNOWN PRE-EXISTING CONDITION: {r['known_condition']}")
        print("\nThis is BASELINE FREEZING: do not modify production code here.")
        print("Report the failing property, the responsible test, and whether it is")
        print("a genuine regression or a baseline/test problem.")
    else:
        print("\nBASELINE HOLDS — every required security property still passes.")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _exc:  # pragma: no cover
        print(f"RUNNER ERROR: {type(_exc).__name__}: {_exc}", file=sys.stderr)
        sys.exit(2)
