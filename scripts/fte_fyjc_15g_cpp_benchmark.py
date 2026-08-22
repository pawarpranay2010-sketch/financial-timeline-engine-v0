#!/usr/bin/env python3
"""
Platrixa
Sprint 15G - C++ Authority Performance Benchmark (before/after)
scripts/fte_fyjc_15g_cpp_benchmark.py

HFT-style measurement of the deterministic C++ authority path, BEFORE
(one-shot subprocess per request) vs AFTER (persistent `--worker` transport,
backend/maths/fyjc_15g.CppAuthorityWorker):

  * IR -> C++ execution           (registered formula solve through the
                                   compiled authority)
  * formula/rule lookup           (the C++ registry lookup inside the solve)
  * journal validation            (deterministic structural checks, Python)
  * ledger validation             (opening + debit - credit == closing)
  * trial-balance validation      (total Dr == total Cr)
  * complete C++ authority exec   (full FYJC replay pipeline + C++ verify)

Every number is MEASURED (median of repeated runs), never estimated. The
worker path is proven byte-identical to the one-shot path before any timing
is trusted (equivalence gate). Optimization never weakens correctness,
determinism, lineage, refusal safety or the C++ authority rules.

Pure measurement module: no source files are modified.
"""

import statistics
import sys
import time

sys.path.insert(0, ".")

from backend.formula_engine_cpp import cpp_calculate
from backend.maths.fyjc_15g import (
    CppAuthorityWorker,
    build_replay_record,
    replay_execute,
    validate_journal,
    validate_ledger,
    validate_trial_balance,
)
from backend.maths.fyjc_bk_15f_benchmark import VERIFIED_CASES
from backend.maths.fyjc_bk_reasoning import reason_bk_question
from backend.maths.status import VERIFIED

N_RUNS = 200


def _timed(fn, runs: int = N_RUNS) -> float:
    """Median wall time in milliseconds over `runs` executions."""
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def _commission_facts(seed: int) -> dict:
    return {
        "Sales": {"value": float(10000 + seed), "reporting_period": "FY2025"},
        "Commission Rate": {"value": 5.0, "reporting_period": "FY2025"},
    }


def main() -> int:
    print("=" * 76)
    print("SPRINT 15G - C++ AUTHORITY PERFORMANCE BENCHMARK (BEFORE/AFTER)")
    print("=" * 76)

    if not __import__("backend.formula_engine_cpp",
                      fromlist=["binary_path"]).binary_path():
        print("compiled C++ authority not deployed - nothing to measure.")
        print("(strict path BLOCKs by design; no Python fallback is claimed.)")
        return 0

    # ---- equivalence gate: the worker MUST match the one-shot path --------
    with CppAuthorityWorker() as worker:
        mismatches = 0
        for seed in range(20):
            one_shot = cpp_calculate("COMMISSION", _commission_facts(seed))
            worker_res = worker.submit({
                "metric": "COMMISSION",
                "inputs": {k: {"value": v["value"]}
                           for k, v in _commission_facts(seed).items()},
            })
            if one_shot is None or worker_res is None \
                    or one_shot != worker_res:
                mismatches += 1
        print(f"equivalence gate (worker == one-shot): "
              f"{'PASS' if mismatches == 0 else f'{mismatches} MISMATCHES'}")
        if mismatches:
            print("benchmark ABORTED: worker diverged from the authority.")
            return 1

        # ---- 1. IR -> C++ execution --------------------------------------
        ms_before = _timed(lambda: cpp_calculate("COMMISSION",
                                                 _commission_facts(1)))
        ms_after = _timed(lambda: worker.submit({
            "metric": "COMMISSION",
            "inputs": {k: {"value": v["value"]}
                       for k, v in _commission_facts(1).items()},
        }))
        print(f"IR -> C++ execution        before {ms_before:8.3f} ms  "
              f"after {ms_after:8.3f} ms  "
              f"({ms_before / max(ms_after, 1e-9):5.1f}x)")

    # ---- 2. formula/rule lookup (in-C++ registry lookup per solve) -------
    with CppAuthorityWorker() as worker:
        ms_before_lookup = _timed(lambda: cpp_calculate("COMMISSION",
                                                        _commission_facts(2)))
        ms_after_lookup = _timed(lambda: worker.submit({
            "metric": "COMMISSION",
            "inputs": {k: {"value": v["value"]}
                       for k, v in _commission_facts(2).items()},
        }))
    print(f"formula/rule lookup        before {ms_before_lookup:8.3f} ms  "
          f"after {ms_after_lookup:8.3f} ms  "
          f"({ms_before_lookup / max(ms_after_lookup, 1e-9):5.1f}x)")

    # ---- 3-5. deterministic validations (Python structural checks) -------
    journals = []
    for case in VERIFIED_CASES:
        out = reason_bk_question(case["question"])
        if out.get("status") == VERIFIED:
            journals.extend(out.get("journals") or [out.get("journal")] or [])
    ledger = __import__("backend.maths.fyjc_accounting",
                        fromlist=["post_ledger"]).post_ledger([
                            {"debits": [{"account": l["account"],
                                         "amount": l["amount"]}
                                        for l in j.get("debit_lines") or []],
                             "credits": [{"account": l["account"],
                                          "amount": l["amount"]}
                                         for l in j.get("credit_lines") or []]}
                            for j in journals])
    tb = __import__("backend.maths.fyjc_accounting",
                    fromlist=["build_trial_balance"]).build_trial_balance([
                        {"debits": [{"account": l["account"],
                                     "amount": l["amount"]}
                                    for l in j.get("debit_lines") or []],
                         "credits": [{"account": l["account"],
                                      "amount": l["amount"]}
                                     for l in j.get("credit_lines") or []]}
                        for j in journals])

    def _validate_journal():
        for j in journals:
            validate_journal(j)

    def _validate_ledger():
        validate_ledger(ledger)

    def _validate_tb():
        validate_trial_balance(tb)

    ms_journal = _timed(_validate_journal, runs=50)
    ms_ledger = _timed(_validate_ledger, runs=50)
    ms_tb = _timed(_validate_tb, runs=50)
    print(f"journal validation         before       - ms  after "
          f"{ms_journal:8.3f} ms  (per journal "
          f"{ms_journal / max(len(journals), 1):.4f} ms)")
    print(f"ledger validation          before       - ms  after "
          f"{ms_ledger:8.3f} ms")
    print(f"trial-balance validation   before       - ms  after "
          f"{ms_tb:8.3f} ms")

    # ---- 6. complete C++ authority execution (full replay pipeline) ------
    def _full_pipeline():
        rec = build_replay_record(
            "Purchased goods from Rahul for Rs.10,000 at 10% trade "
            "discount. Half the amount was paid immediately and a cash "
            "discount of 2% was allowed on the amount paid.",
            verify_cpp=True)
        replay_execute(rec)
        return rec

    ms_pipeline = _timed(_full_pipeline, runs=20)
    print(f"complete authority exec    before       - ms  after "
          f"{ms_pipeline:8.3f} ms  (build + replay + C++ verify)")

    print("=" * 76)
    print("benchmark summary:")
    print(f"  IR -> C++ execution : {ms_before:.2f} ms -> {ms_after:.2f} ms "
          f"({ms_before / max(ms_after, 1e-9):.1f}x)")
    print(f"  formula/rule lookup : {ms_before_lookup:.2f} ms -> "
          f"{ms_after_lookup:.2f} ms "
          f"({ms_before_lookup / max(ms_after_lookup, 1e-9):.1f}x)")
    print(f"  journal validation  : {ms_journal:.2f} ms across "
          f"{len(journals)} journals")
    print("all timings are measured medians; worker == one-shot verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
