#!/usr/bin/env python3
"""Tests for the device-wide concurrency limiter (M8), no phone required.

Proves the properties the Termux service depends on:

  * the cap is never exceeded, even with many racing processes;
  * a bounded wait returns "busy" instead of blocking forever;
  * release frees exactly one slot, and only for the process that holds it;
  * a holder killed with SIGKILL leaves a stale slot that is reclaimed;
  * a live holder is never stolen, however old its record is;
  * `status` reports held/free slots without mutating anything.

Run:
    python3 scripts/platrixa_slot_limiter_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "platrixa_slot_limiter.py"
sys.path.insert(0, str(ROOT / "scripts"))

from platrixa_slot_limiter import SlotLimiter  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _holding_script(slots_dir: str, hold_s: float) -> str:
    """A child that acquires, holds, and reports what it got."""
    return (
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "from platrixa_slot_limiter import SlotLimiter\n"
        f"lim = SlotLimiter({slots_dir!r}, 2)\n"
        "tok = lim.acquire(5)\n"
        "print(tok, flush=True)\n"
        "time.sleep(" + str(hold_s) + ")\n"
    )


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="platrixa-slots-")
    slots = os.path.join(tmp, "slots")

    # 1. basic acquire / release ------------------------------------------
    lim = SlotLimiter(slots, 2)
    t1 = lim.acquire(1)
    t2 = lim.acquire(1)
    t3 = lim.acquire(0.3)
    check("two slots can be acquired at max=2", t1 is not None and t2 is not None)
    check("a third acquisition waits then reports busy (no third concurrent slot)", t3 is None)
    check("release frees the slot it owns", lim.release(t1) is True)
    t3b = lim.acquire(1)
    check("the freed slot can be re-acquired", t3b is not None)
    lim.release(t2)
    lim.release(t3b)

    # 2. status is read-only and accurate ---------------------------------
    token = lim.acquire(1)
    status = lim.status()
    check(
        "status reports one slot in use with the holder pid",
        status["in_use"] == 1 and status["available"] == 1,
        json.dumps(status),
    )
    check("status does not release anything", SlotLimiter(slots, 2).status()["in_use"] == 1)
    lim.release(token)
    check("status reports zero in use after release", lim.status()["in_use"] == 0)

    # 3. cross-process cap: 6 processes, max 2 ----------------------------
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _holding_script(slots, 1.5)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    time.sleep(0.6)
    live = SlotLimiter(slots, 2).status()["in_use"]
    check(
        "six racing processes never exceed the device cap of 2",
        live <= 2,
        f"in_use={live}",
    )
    outcomes = []
    for p in procs:
        out, err = p.communicate(timeout=30)
        outcomes.append(out.strip())
    granted = [o for o in outcomes if o and o != "None"]
    check(
        "every racing process either got a slot or reported busy",
        len(granted) + outcomes.count("None") == 6,
        f"outcomes={outcomes}",
    )

    # 4. killed holder is reclaimed ---------------------------------------
    lim4 = SlotLimiter(slots, 1)
    holder = subprocess.Popen(
        [sys.executable, "-c", _holding_script(slots, 60)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_line = holder.stdout.readline().strip()
    check("a child acquired the only slot", first_line not in ("", "None"), first_line)
    holder.kill()
    holder.wait(timeout=10)
    reclaimed = lim4.acquire(3)
    check(
        "a SIGKILLed holder's slot is reclaimed (no stuck device)",
        reclaimed is not None,
    )
    if reclaimed:
        lim4.release(reclaimed)

    # 5. a live holder is never stolen on age alone -----------------------
    lim5 = SlotLimiter(slots, 1, stale_ttl_s=1)
    tok5 = lim5.acquire(1)
    slot_dir = Path(slots) / "slot-0"
    old = time.time() - 3600
    os.utime(slot_dir, (old, old))
    check(
        "a LIVE holder older than the TTL is not stolen",
        lim5.acquire(0.5) is None,
    )
    lim5.release(tok5)

    # 6. a foreign process cannot release our slot ------------------------
    lim6 = SlotLimiter(slots, 1)
    tok6 = lim6.acquire(1)
    foreign = f"0:{os.getpid() + 1}"
    check("a token from another pid cannot free the slot", lim6.release(foreign) is False)
    check("the slot is still held after the foreign release attempt", lim6.status()["in_use"] == 1)
    lim6.release(tok6)

    # 7. CLI exit codes ---------------------------------------------------
    # The CLI process exits immediately after acquiring, so its own slot is
    # legitimately stale. A LIVE holder is needed to test the busy exit code.
    live_holder = subprocess.Popen(
        [sys.executable, "-c", _holding_script(slots, 60)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    held = live_holder.stdout.readline().strip()
    check("a live CLI-path holder acquired the only slot", held not in ("", "None"), held)
    cli_busy = subprocess.run(
        [sys.executable, str(SCRIPT), "acquire", "--slots-dir", slots, "--max", "1", "--wait", "0.3"],
        capture_output=True, text=True,
    )
    check(
        "CLI acquire exits 75 (EX_TEMPFAIL) when a live holder has all slots",
        cli_busy.returncode == 75,
        str(cli_busy.returncode),
    )
    check(
        "CLI release of another process's token is refused",
        subprocess.run(
            [sys.executable, str(SCRIPT), "release", "--slots-dir", slots, "--token", held],
            capture_output=True, text=True,
        ).returncode == 1,
    )
    live_holder.kill()
    live_holder.wait(timeout=10)

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
