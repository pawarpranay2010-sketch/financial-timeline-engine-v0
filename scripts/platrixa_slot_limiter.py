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

FAIRNESS (one phone, several tenants)
-------------------------------------
A plain counting semaphore is not fair: the worker that polls fastest wins the
slot every time, so one tenant with a large backlog can starve the others. Each
waiter therefore registers a FIFO ticket (``<slots_dir>/waiting/<ns>-<pid>``)
BEFORE trying a slot, and a waiter may only take a free slot when NO LIVE
WAITER IS OLDER. A ticket whose owner is dead (or whose record is older than
the TTL with no usable PID) is pruned, so a crashed waiter cannot block the
queue. The result is arrival-order service across tenant workers without any
shared process, daemon or lock server — and without a waiting worker ever
holding a job lease (workers acquire the slot before generating, and release
the claim when the slot stays busy).

CLI (used by the Termux service and by operators)
-------------------------------------------------
    python3 scripts/platrixa_slot_limiter.py status   --slots-dir DIR
    python3 scripts/platrixa_slot_limiter.py acquire  --slots-dir DIR --max 1 --wait 5 --tenant dev-alice
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

    def _waiting_dir(self) -> Path:
        return self.slots_dir / "waiting"

    # -- fair queue -------------------------------------------------------

    def _register_waiter(self, tenant: Optional[str]) -> Path:
        """Append this process to the FIFO queue and return its ticket path."""
        waiting = self._waiting_dir()
        waiting.mkdir(parents=True, exist_ok=True)
        # Nanosecond timestamp primary key + pid tiebreak: monotonic enough for
        # arrival order, unique per process, and sortable as a plain string.
        name = f"{time.time_ns():020d}-{os.getpid():08d}"
        ticket = waiting / name
        try:
            ticket.write_text((tenant or "").strip(), encoding="utf-8")
        except Exception:
            return ticket
        return ticket

    def _waiter_pid(self, ticket: Path) -> Optional[int]:
        try:
            return int(ticket.name.rsplit("-", 1)[1])
        except Exception:
            return None

    def _prune_waiters(self) -> List[Path]:
        """Return the LIVE tickets in arrival order, deleting dead ones."""
        waiting = self._waiting_dir()
        try:
            tickets = sorted(p for p in waiting.iterdir() if p.is_file())
        except Exception:
            return []
        live: List[Path] = []
        for ticket in tickets:
            pid = self._waiter_pid(ticket)
            if pid is not None:
                if _pid_alive(pid):
                    live.append(ticket)
                    continue
                try:
                    ticket.unlink()
                except Exception:
                    pass
                continue
            # No usable PID in the name: age is the only evidence available.
            try:
                stale = (time.time() - ticket.stat().st_mtime) > self.stale_ttl_s
            except Exception:
                stale = True
            if stale:
                try:
                    ticket.unlink()
                except Exception:
                    pass
            else:
                live.append(ticket)
        return live

    def _is_stale(self, slot: Path, pid: Optional[int]) -> bool:
        if pid is None:
            # No usable PID: age is the only evidence available.
            try:
                age = time.time() - slot.stat().st_mtime
            except Exception:
                return True
            return age > self.stale_ttl_s
        return not _pid_alive(pid)

    def _try_acquire_slot(self, index: int, *, tenant: Optional[str] = None) -> Optional[str]:
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
            # Label only (operator visibility); never used for authorization.
            (slot / "tenant").write_text((tenant or "").strip(), encoding="utf-8")
        except Exception:
            shutil.rmtree(slot, ignore_errors=True)
            return None
        return token

    # -- public API -------------------------------------------------------

    def acquire(
        self,
        wait_s: float = DEFAULT_WAIT_SECONDS,
        *,
        tenant: Optional[str] = None,
        fair: bool = True,
    ) -> Optional[str]:
        """Acquire one slot, waiting at most ``wait_s`` seconds.

        With ``fair=True`` (the default, and what the Termux service uses) this
        process may take a free slot only when it is the OLDEST live waiter, so
        four tenant workers are served in arrival order instead of by poll
        frequency. ``fair=False`` keeps the original best-effort behaviour for
        single-tenant tools and tests.

        Returns the token, or ``None`` when no slot could be taken for the whole
        wait budget. Never blocks indefinitely and never steals a live slot.
        """
        self.slots_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(wait_s))
        ticket: Optional[Path] = None
        if fair:
            ticket = self._register_waiter(tenant)
        try:
            while True:
                if ticket is not None:
                    live = self._prune_waiters()
                    if live and live[0] != ticket:
                        # Someone who arrived earlier is still waiting: yielding
                        # here is what prevents tenant starvation.
                        if time.monotonic() >= deadline:
                            return None
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue
                for index in range(self.max_slots):
                    token = self._try_acquire_slot(index, tenant=tenant)
                    if token is not None:
                        return token
                if time.monotonic() >= deadline:
                    return None
                time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            if ticket is not None:
                try:
                    ticket.unlink()
                except Exception:
                    pass

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
            tenant = ""
            try:
                tenant = (slot / "tenant").read_text(encoding="utf-8").strip()
            except Exception:
                tenant = ""
            slots.append(
                {
                    "index": index,
                    "held": pid is not None and _pid_alive(pid),
                    "pid": pid,
                    "tenant": tenant or None,
                    "age_seconds": age,
                    "stale": self._is_stale(slot, pid),
                }
            )
        waiters: List[Dict[str, object]] = []
        for ticket in self._prune_waiters():
            try:
                label = ticket.read_text(encoding="utf-8").strip()
            except Exception:
                label = ""
            try:
                waited = round(time.time() - ticket.stat().st_mtime, 3)
            except Exception:
                waited = None
            waiters.append(
                {
                    "tenant": label or None,
                    "pid": self._waiter_pid(ticket),
                    "waited_seconds": waited,
                }
            )
        in_use = sum(1 for s in slots if s.get("held"))
        return {
            "slots_dir": str(self.slots_dir),
            "max_slots": self.max_slots,
            "in_use": in_use,
            "available": max(0, self.max_slots - in_use),
            "slots": slots,
            "waiters": waiters,
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa device-wide inference slot limiter")
    parser.add_argument("action", choices=("status", "acquire", "release"))
    parser.add_argument("--slots-dir", required=True)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_SLOTS, dest="max_slots")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--token", default=None)
    parser.add_argument("--tenant", default=None, help="Label for operator visibility (never authorization)")
    parser.add_argument("--no-fair", action="store_true", help="Best-effort acquisition without FIFO fairness")
    parser.add_argument("--ttl", type=float, default=DEFAULT_STALE_TTL_SECONDS)
    args = parser.parse_args(argv)

    limiter = SlotLimiter(args.slots_dir, args.max_slots, args.ttl)

    if args.action == "status":
        print(json.dumps(limiter.status(), indent=2))
        return 0
    if args.action == "acquire":
        token = limiter.acquire(args.wait, tenant=args.tenant, fair=not args.no_fair)
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
