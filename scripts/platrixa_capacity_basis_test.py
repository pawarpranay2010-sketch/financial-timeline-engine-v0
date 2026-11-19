#!/usr/bin/env python3
"""M9 — the capacity NUMBER must have a basis (no simulated-device claims).

The failure this prevents: a local benchmark with a stub model reporting
"0.7s/job → N developers", which is arithmetic about a simulation and must
never reach a beta invite or a README.

Checks:

  * with no measured device input, the estimate is explicitly NOT quotable and
    carries the warning that says so;
  * scenario rows are labelled ``is_a_capacity_claim: false``;
  * a measured input is reduced by the safety margin (30% by default) and only
    then converted to developers at 100 jobs/month;
  * the arithmetic is exact and reproducible;
  * nonsense inputs are refused instead of producing a plausible number.

Run:
    python3 scripts/platrixa_capacity_basis_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from platrixa_worker_benchmark import (  # noqa: E402
    DEFAULT_SAFETY_MARGIN,
    build_capacity_estimate,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def main() -> int:
    assumed = build_capacity_estimate(non_model_overhead_s=0.25, simulated_end_to_end_s=0.45)
    check("without a device measurement the estimate is NOT quotable", assumed["quotable"] is False)
    check(
        "the unmeasured basis says it is not a device measurement",
        "NOT a device measurement" in assumed["basis"],
        assumed["basis"],
    )
    check(
        "the unmeasured warning tells the operator to measure",
        "on-device benchmark" in assumed["warning"],
        assumed["warning"],
    )
    check(
        "every scenario row is labelled as not a capacity claim",
        all(row["is_a_capacity_claim"] is False for row in assumed["scenarios"]),
    )
    check(
        "assumptions name the one-slot-at-a-time device default",
        any("SLOT_MAX=1" in a for a in assumed["assumptions"]),
        str(assumed["assumptions"]),
    )

    measured = build_capacity_estimate(
        non_model_overhead_s=0.25,
        simulated_end_to_end_s=0.45,
        device_jobs_per_hour=20.0,
        device_label="Pixel 7 / Qwen2.5-3B-Q4 / 512 tokens",
    )
    check("a measured device input makes the estimate quotable", measured["quotable"] is True)
    dm = measured["device_measurement"]
    check("the default margin is 30%", abs(dm["margin"] - DEFAULT_SAFETY_MARGIN) < 1e-9)
    check("20 measured jobs/hour becomes 14 safe jobs/hour", abs(dm["safe_jobs_per_hour"] - 14.0) < 1e-6, str(dm))
    check(
        "14 safe jobs/hour is 100.8 developers at 100 jobs/month",
        abs(dm["developers_at_100_jobs_per_month"] - 100.8) < 0.05,
        str(dm["developers_at_100_jobs_per_month"]),
    )
    check(
        "the safe monthly figure is 14 x 24 x 30",
        dm["safe_jobs_per_month"] == round(14.0 * 24 * 30, 0),
        str(dm["safe_jobs_per_month"]),
    )
    check("the measured basis states the haircut", "safety margin" in measured["basis"], measured["basis"])
    check("the device label is recorded for the report", dm["device_label"].startswith("Pixel 7"))

    generous = build_capacity_estimate(
        non_model_overhead_s=0.25,
        simulated_end_to_end_s=0.45,
        device_jobs_per_hour=20.0,
        margin=0.5,
    )
    check(
        "a 50% margin halves the safe throughput",
        abs(generous["device_measurement"]["safe_jobs_per_hour"] - 10.0) < 1e-6,
        str(generous["device_measurement"]["safe_jobs_per_hour"]),
    )

    refusals = 0
    for kwargs in (
        {"device_jobs_per_hour": 0},
        {"device_jobs_per_hour": -5},
        {"device_jobs_per_hour": 20.0, "margin": 1.0},
        {"device_jobs_per_hour": 20.0, "margin": -0.1},
    ):
        try:
            build_capacity_estimate(
                non_model_overhead_s=0.25, simulated_end_to_end_s=0.45, **kwargs
            )
        except ValueError:
            refusals += 1
    check("impossible inputs are refused with ValueError", refusals == 4, f"{refusals}/4")

    print()
    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    print("=" * 78)
    print(f"RESULT: {passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        for name, ok, detail in CHECKS:
            if not ok:
                print(f"  FAIL: {name} — {detail}")
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
