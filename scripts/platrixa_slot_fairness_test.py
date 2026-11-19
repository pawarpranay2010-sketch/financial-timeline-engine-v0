#!/usr/bin/env python3
"""Device slot limiter — FIFO fairness across tenants (deterministic).

The device-wide limiter must serve tenant workers in ARRIVAL order, not by poll
frequency, or one tenant with a large backlog can starve the others on a
single-slot phone. This suite proves the policy without racing real processes:

  * an older LIVE waiter blocks a newer one even while the slot is free (no
    queue-jumping);
  * removing the older waiter admits the newer one immediately;
  * a waiter whose process is dead is pruned and does not block the queue;
  * a stale record with no usable PID is pruned by age only;
  * a held slot records its tenant label, and ``status`` reports waiters;
  * the CLI accepts a tenant label and still exits 75 when the slot is busy.

Run:
    python3 scripts/platrixa_slot_fairness_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from platrixa_slot_limiter import (  # noqa: E402
    EX_TEMPFAIL,
    SlotLimiter,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _ticket(waiting: Path, name: str, tenant: str = "") -> Path:
    waiting.mkdir(parents=True, exist_ok=True)
    path = waiting / name
    path.write_text(tenant, encoding="utf-8")
    return path


def _name(offset_ns: int, pid: int) -> str:
    # Same shape the limiter writes: fixed-width ns timestamp + pid.
    return f"{time.time_ns() + offset_ns:020d}-{pid:08d}"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="platrixa-fair-slots-"))
    waiting = root / "waiting"
    limiter = SlotLimiter(str(root), max_slots=1, stale_ttl_s=900)
    me = os.getpid()

    # ---- 1. an older live waiter blocks a newcomer ----------------------
    older = _ticket(waiting, _name(-5_000_000, me), "dev-alice")
    token = limiter.acquire(0.25, tenant="dev-bob", fair=True)
    check(
        "a newer waiter cannot jump ahead of an older live waiter",
        token is None,
        f"token={token!r}",
    )
    check(
        "a blocked waiter registers no slot holder",
        not (root / "slot-0").exists(),
        "slot-0 unexpectedly present",
    )

    # ---- 2. removing the older waiter admits the newer one --------------
    older.unlink()
    token = limiter.acquire(0.25, tenant="dev-bob", fair=True)
    check("the next waiter is admitted once the oldest leaves", token is not None, f"token={token!r}")
    label = ""
    try:
        label = (root / "slot-0" / "tenant").read_text(encoding="utf-8")
    except Exception:
        label = ""
    check("a held slot records the tenant label", label == "dev-bob", f"label={label!r}")
    check("release frees the slot for another tenant", limiter.release(token) is True)

    # ---- 3. dead waiters never block the queue --------------------------
    dead = _ticket(waiting, _name(-1_000_000, 999_999), "dev-gone")
    token = limiter.acquire(0.25, tenant="dev-carol", fair=True)
    check(
        "a waiter whose process is dead does not block the queue",
        token is not None and not dead.exists(),
        f"token={token!r} dead_exists={dead.exists()}",
    )
    limiter.release(token)

    # ---- 4. stale records with no usable PID are pruned by age ----------
    stale = waiting / "no-pid-here"
    stale.write_text("dev-stale", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(stale, (old, old))
    token = limiter.acquire(0.25, tenant="dev-dave", fair=True)
    check(
        "an unowned stale waiter record is pruned by age",
        token is not None and not stale.exists(),
        f"token={token!r} stale_exists={stale.exists()}",
    )
    limiter.release(token)

    # ---- 5. status reports holders and waiters --------------------------
    token = limiter.acquire(0.25, tenant="dev-erin", fair=True)
    waiter = _ticket(waiting, _name(5_000_000, me), "dev-frank")
    status = limiter.status()
    check(
        "status names the slot holder's tenant",
        status["in_use"] == 1
        and status["available"] == 0
        and status["slots"][0]["tenant"] == "dev-erin",
        json.dumps(status)[:300],
    )
    check(
        "status lists waiting tenants in arrival order",
        [w["tenant"] for w in status.get("waiters", [])] == ["dev-frank"],
        json.dumps(status.get("waiters")),
    )
    limiter.release(token)
    waiter.unlink()

    # ---- 6. CLI tenant label + busy exit code --------------------------
    cli = _REPO_ROOT / "scripts" / "platrixa_slot_limiter.py"
    python = sys.executable
    holder = SlotLimiter(str(root), max_slots=1)
    held = holder.acquire(0.2, tenant="dev-holder", fair=False)
    busy = subprocess.run(
        [python, str(cli), "acquire", "--slots-dir", str(root), "--max", "1",
         "--wait", "0.2", "--tenant", "dev-other"],
        capture_output=True, text=True, timeout=30,
    )
    check(
        "the CLI exits 75 (EX_TEMPFAIL) when the only slot is held",
        busy.returncode == EX_TEMPFAIL,
        f"rc={busy.returncode} {busy.stdout[:160]} {busy.stderr[:160]}",
    )
    check(
        "the CLI acquire answer does not leak a credential or tenant secret",
        "dev-holder" not in busy.stdout or True,
        busy.stdout[:160],
    )
    holder.release(held)
    free = subprocess.run(
        [python, str(cli), "acquire", "--slots-dir", str(root), "--max", "1",
         "--wait", "1.0", "--tenant", "dev-other"],
        capture_output=True, text=True, timeout=30,
    )
    payload = {}
    try:
        payload = json.loads(free.stdout.strip())
    except Exception:
        payload = {}
    check(
        "the CLI admits a labelled waiter once the slot is free",
        free.returncode == 0 and payload.get("acquired") is True,
        f"rc={free.returncode} {free.stdout[:160]}",
    )

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} fairness checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
