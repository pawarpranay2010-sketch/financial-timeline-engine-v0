#!/usr/bin/env python3
"""Platrixa — device-wide inference concurrency limiter.

WHY THIS EXISTS
---------------
llama.cpp *queues* a request when every slot is busy; it does not refuse it
(upstream server docs). That is the right behaviour for a chat client and the
wrong behaviour for a leased job: a queued request holds the worker's HTTP
connection, and the worker's lease can expire while it waits for a generation
that has not started. The result is wasted phone time and a confusing
LEASE_EXPIRED, instead of a clean, retryable "the device is busy".

This limiter is the external refusal. It is shared by every worker instance on
the phone, so `N` worker processes cannot put more than `N` generations in
flight no matter how they are scheduled.

PRIMITIVE
---------
`mkdir` is atomic on every POSIX filesystem (and on Android's FUSE-backed
storage), needs no extra package on Termux, and is safe across processes that
share nothing but the directory. Each slot is a directory containing the
holder's PID and acquisition time:

    <slots_dir>/slot-0/{pid,acquired_at}
    <slots_dir>/slot-1/{pid,acquired_at}

* **Acquire**: `mkdir slot-i`; on success write the pid and return the token
  `"i:pid"`. On failure, read the holder's pid: a dead pid (or an unreadable
  record older than the TTL) is stale and is reclaimed.
* **Release**: remove the directory only when the recorded pid matches the
  releasing process — a stale holder can never free the slot of a live one.
* **Never unbounded**: acquisition uses a bounded wait and returns ``None``
  rather than blocking forever.

A live PID is NEVER reclaimed on age alone: a generation may legitimately run
longer than the TTL, and stealing its slot would break the concurrency
guarantee exactly when the device is most loaded. The TTL is a backstop for
records with no usable PID only.

CLI (used by the Termux service and by operators)
-------------------------------------------------
    python3 scripts/platrixa_slot_limiter.py status   --slots-dir DIR
    python3 scripts/platrixa_slot_limiter.py acquire  --slots-dir DIR --max 1 --wait 5
    python3 scripts/platrixa_slot_limiter.py release  --slots-dir DIR --token 0:1234

Exit codes: 0 success, 75 (EX_TEMPFAIL) when no slot is available in time.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_MAX_SLOTS = 1
DEFAULT_WAIT_SECONDS = 5.0
DEFAULT_STALE_TTL_SECONDS = 900.0
POLL_INTERVAL_SECONDS = 0.2

EX_TEMPFAIL = 75


def _read_pid(slot: Path) -> Optional[int]:
    try:
        raw = (slot / "pid").read_text(encoding="utf-8").strip()
        pid = int(raw)
        return pid if pid > 0 else None
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but belongs to another user: treat as alive (never steal).
        return True
    except Exception:
        return False


class SlotLimiter:
    """Cross-process counting semaphore with PID-aware stale recovery."""

    def __init__(
        self,
        slots_dir: str,
        max_slots: int = DEFAULT_MAX_SLOTS,
        stale_ttl_s: float = DEFAULT_STALE_TTL_SECONDS,
    ) -> None:
        self.slots_dir = Path(slots_dir)
        self.max_slots = max(1, int(max_slots))
        self.stale_ttl_s = max(1.0, float(stale_ttl_s))

    # -- internals --------------------------------------------------------

    def _slot_path(self, index: int) -> Path:
        return self.slots_dir / f"slot-{index}"

    def _is_stale(self, slot: Path, pid: Optional[int]) -> bool:
        if pid is None:
            # No usable PID: age is the only evidence available.
            try:
                age = time.time() - slot.stat().st_mtime
            except Exception:
                return True
            return age > self.stale_ttl_s
        return not _pid_alive(pid)

    def _try_acquire_slot(self, index: int) -> Optional[str]:
        slot = self._slot_path(index)
        try:
            slot.mkdir()
        except FileExistsError:
            pid = _read_pid(slot)
            if not self._is_stale(slot, pid):
                return None
            # Stale: reclaim and race fairly for it (mkdir is atomic, so at
            # most one reclaimer wins; the loser simply sees FileExistsError).
            shutil.rmtree(slot, ignore_errors=True)
            try:
                slot.mkdir()
            except Exception:
                return None
        except Exception:
            return None
        token = f"{index}:{os.getpid()}"
        try:
            (slot / "pid").write_text(str(os.getpid()), encoding="utf-8")
            (slot / "acquired_at").write_text(
                f"{time.time():.3f}", encoding="utf-8"
            )
        except Exception:
            shutil.rmtree(slot, ignore_errors=True)
            return None
        return token

    # -- public API -------------------------------------------------------

    def acquire(self, wait_s: float = DEFAULT_WAIT_SECONDS) -> Optional[str]:
        """Acquire one slot, waiting at most ``wait_s`` seconds.

        Returns the token, or ``None`` when every slot stayed busy for the
        whole wait budget. Never blocks indefinitely.
        """
        self.slots_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(wait_s))
        while True:
            for index in range(self.max_slots):
                token = self._try_acquire_slot(index)
                if token is not None:
                    return token
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_INTERVAL_SECONDS)

    def release(self, token: Optional[str]) -> bool:
        """Release a slot held by THIS process. Mismatched tokens change nothing."""
        if not token:
            return False
        try:
            index_text, pid_text = str(token).split(":", 1)
            index = int(index_text)
            holder = int(pid_text)
        except Exception:
            return False
        if holder != os.getpid():
            # A different process must never free our slot; and we must never
            # free a slot a newer generation now owns.
            return False
        slot = self._slot_path(index)
        recorded = _read_pid(slot)
        if recorded is not None and recorded != holder:
            return False
        shutil.rmtree(slot, ignore_errors=True)
        return True

    def status(self) -> Dict[str, object]:
        """Read-only view: which slots are held, by which pid, for how long."""
        slots: List[Dict[str, object]] = []
        for index in range(self.max_slots):
            slot = self._slot_path(index)
            if not slot.is_dir():
                slots.append({"index": index, "held": False})
                continue
            pid = _read_pid(slot)
            age = None
            try:
                age = round(time.time() - float((slot / "acquired_at").read_text().strip()), 3)
            except Exception:
                try:
                    age = round(time.time() - slot.stat().st_mtime, 3)
                except Exception:
                    age = None
            slots.append(
                {
                    "index": index,
                    "held": pid is not None and _pid_alive(pid),
                    "pid": pid,
                    "age_seconds": age,
                    "stale": self._is_stale(slot, pid),
                }
            )
        in_use = sum(1 for s in slots if s.get("held"))
        return {
            "slots_dir": str(self.slots_dir),
            "max_slots": self.max_slots,
            "in_use": in_use,
            "available": max(0, self.max_slots - in_use),
            "slots": slots,
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa device-wide inference slot limiter")
    parser.add_argument("action", choices=("status", "acquire", "release"))
    parser.add_argument("--slots-dir", required=True)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_SLOTS, dest="max_slots")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--token", default=None)
    parser.add_argument("--ttl", type=float, default=DEFAULT_STALE_TTL_SECONDS)
    args = parser.parse_args(argv)

    limiter = SlotLimiter(args.slots_dir, args.max_slots, args.ttl)

    if args.action == "status":
        print(json.dumps(limiter.status(), indent=2))
        return 0
    if args.action == "acquire":
        token = limiter.acquire(args.wait)
        if token is None:
            print(json.dumps({"acquired": False, "reason": "all slots busy"}))
            return EX_TEMPFAIL
        print(json.dumps({"acquired": True, "token": token}))
        return 0
    released = limiter.release(args.token)
    print(json.dumps({"released": released, "token": args.token}))
    return 0 if released else 1


if __name__ == "__main__":
    sys.exit(main())
