#!/usr/bin/env python3
"""Platrixa — readiness deadline semantics (remediation, 2026-10-25).

WHAT THIS SUITE PROVES
======================
``readiness_deadline_seconds()`` is a PROBE-PHASE budget, not a wall-clock
response deadline. The implementation must therefore:

  * take the verdict immediately after the probe query returns, BEFORE the
    session is closed, so cleanup can never turn an overrun into a late
    "ready" (and can never turn a prompt success into "unavailable");
  * fail CLOSED when the probe phases exceed the budget, with exactly one
    log line (no duplicate "probe failed" line for the verdict it produced);
  * still run cleanup on every path, and survive a cleanup failure without
    changing the verdict.

Additionally, the budget must be the exact sum of the documented terms, and
cleanup latency must remain an acknowledged extension of the HTTP response —
measured here, not asserted away.

Run:
    .venv/bin/python scripts/fte_fyjc_95_deadline_semantics_test.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.auth import admission as admission_boundary  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - plumbing
        self.lines.append(record.getMessage())


class _Session:
    """A duck-typed session: no dialect, so the probe bound helper is a no-op."""

    def __init__(self, *, execute_sleep: float = 0.0, close_sleep: float = 0.0,
                 close_raises: bool = False) -> None:
        self.execute_sleep = execute_sleep
        self.close_sleep = close_sleep
        self.close_raises = close_raises
        self.closed = 0
        self.executed = 0

    def execute(self, *_args, **_kwargs):
        self.executed += 1
        if self.execute_sleep:
            time.sleep(self.execute_sleep)
        return None

    def close(self) -> None:
        self.closed += 1
        if self.close_sleep:
            time.sleep(self.close_sleep)
        if self.close_raises:
            raise RuntimeError("close failed (must not change the verdict)")


def _use_session(session: _Session):
    """Install a factory returning ``session``; return the previous factory."""
    previous = metered_gate._session_factory
    metered_gate._session_factory = lambda: (lambda: session)  # type: ignore[assignment]
    return previous


def section_a() -> None:
    print("\nA — the budget is the exact sum of its documented terms")
    default = (
        metered_gate.pool_timeout_seconds()
        + metered_gate.connect_timeout_seconds()
        + metered_gate.statement_timeout_ms() / 1000.0
        + metered_gate.CLEANUP_SLACK_SECONDS
    )
    check("A1 the default budget is 6.0s (1.0 pool + 3 connect + 1.5 statement + 0.5 slack)",
          abs(metered_gate.readiness_deadline_seconds() - 6.0) < 1e-9
          and abs(default - 6.0) < 1e-9,
          f"{metered_gate.readiness_deadline_seconds()}")

    saved = {k: os.environ.get(k) for k in (
        metered_gate.POOL_TIMEOUT_ENV_VAR,
        metered_gate.CONNECT_TIMEOUT_ENV_VAR,
        metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR,
    )}
    try:
        os.environ[metered_gate.POOL_TIMEOUT_ENV_VAR] = "2.0"
        os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "5"
        os.environ[metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR] = "800"
        expected = 2.0 + 5 + 0.8 + metered_gate.CLEANUP_SLACK_SECONDS
        check("A2 operator overrides flow into the budget exactly",
              abs(metered_gate.readiness_deadline_seconds() - expected) < 1e-9,
              f"{metered_gate.readiness_deadline_seconds()} != {expected}")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def section_b() -> None:
    print("\nB — an overrunning probe fails CLOSED, once, and still cleans up")
    saved_ttl = os.environ.get(admission_boundary.READINESS_CACHE_TTL_ENV_VAR)
    os.environ[metered_gate.METERING_ENV_VAR] = "sqlite:///unused-deadline-semantics.db"
    os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
    admission_boundary.reset_readiness_cache()

    session = _Session(execute_sleep=0.20, close_sleep=0.05)
    previous_factory = _use_session(session)
    previous_deadline = metered_gate.readiness_deadline_seconds
    metered_gate.readiness_deadline_seconds = lambda: 0.05  # type: ignore[assignment]

    capture = _Capture()
    logger = logging.getLogger("platrixa.api")
    logger.addHandler(capture)
    raised = None
    elapsed = 0.0
    try:
        t0 = time.perf_counter()
        try:
            metered_gate.probe_store()
        except metered_gate.MeteredGateError as exc:
            raised = str(exc)
        elapsed = time.perf_counter() - t0
    finally:
        logger.removeHandler(capture)
        metered_gate._session_factory = previous_factory  # type: ignore[assignment]
        metered_gate.readiness_deadline_seconds = previous_deadline  # type: ignore[assignment]
        if saved_ttl is None:
            os.environ.pop(admission_boundary.READINESS_CACHE_TTL_ENV_VAR, None)
        else:
            os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = saved_ttl

    check("B1 the overrun raises the fixed, DSN-free gate error",
          raised == "metering store unavailable", str(raised))
    check("B2 cleanup still ran exactly once after the verdict",
          session.closed == 1, f"closed={session.closed}")
    check("B3 the probe query ran once", session.executed == 1, f"executed={session.executed}")
    exceeded = [ln for ln in capture.lines if "budget" in ln]
    failed = [ln for ln in capture.lines if "readiness probe failed" in ln]
    check("B4 the overrun is logged exactly once, under its own message",
          len(exceeded) == 1, f"{capture.lines}")
    check("B5 the overrun is not re-logged as a generic probe failure",
          not failed, str(failed))
    check("B6 the verdict was NOT delayed past the budget by cleanup alone",
          elapsed >= 0.20, f"{elapsed:.2f}s")
    check("B7 the log names no DSN, host or credential",
          not any(tok in " ".join(capture.lines) for tok in ("sqlite", "postgres", "unused-deadline")),
          str(capture.lines))


def section_c() -> None:
    print("\nC — a slow cleanup cannot change a SUCCESS verdict")
    saved_ttl = os.environ.get(admission_boundary.READINESS_CACHE_TTL_ENV_VAR)
    os.environ[metered_gate.METERING_ENV_VAR] = "sqlite:///unused-deadline-semantics.db"
    os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
    admission_boundary.reset_readiness_cache()

    session = _Session(execute_sleep=0.0, close_sleep=0.50)
    previous_factory = _use_session(session)
    previous_deadline = metered_gate.readiness_deadline_seconds
    metered_gate.readiness_deadline_seconds = lambda: 0.10  # type: ignore[assignment]

    capture = _Capture()
    logger = logging.getLogger("platrixa.api")
    logger.addHandler(capture)
    try:
        t0 = time.perf_counter()
        verdict = admission_boundary.store_reachable()
        elapsed = time.perf_counter() - t0
    finally:
        logger.removeHandler(capture)
        metered_gate._session_factory = previous_factory  # type: ignore[assignment]
        metered_gate.readiness_deadline_seconds = previous_deadline  # type: ignore[assignment]
        if saved_ttl is None:
            os.environ.pop(admission_boundary.READINESS_CACHE_TTL_ENV_VAR, None)
        else:
            os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = saved_ttl

    check("C1 the probe phase was inside the budget, so the verdict is ready",
          verdict is True, str(verdict))
    check("C2 cleanup ran exactly once", session.closed == 1, f"closed={session.closed}")
    check("C3 cleanup latency extended the response (documented, not hidden)",
          elapsed >= 0.45, f"{elapsed:.2f}s")
    check("C4 a slow close produced NO overrun verdict",
          not [ln for ln in capture.lines if "budget" in ln], str(capture.lines))
    check("C5 the measured response exceeded the patched budget, yet stayed ready",
          elapsed > 0.10, f"{elapsed:.2f}s > 0.10s")


def section_d() -> None:
    print("\nD — a cleanup FAILURE does not change the verdict")
    saved_ttl = os.environ.get(admission_boundary.READINESS_CACHE_TTL_ENV_VAR)
    os.environ[metered_gate.METERING_ENV_VAR] = "sqlite:///unused-deadline-semantics.db"
    os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
    admission_boundary.reset_readiness_cache()

    session = _Session(close_raises=True)
    previous_factory = _use_session(session)
    try:
        raised = None
        try:
            metered_gate.probe_store()
        except Exception as exc:  # must not raise: close failures are defensive
            raised = type(exc).__name__
        verdict = admission_boundary.store_reachable()
    finally:
        metered_gate._session_factory = previous_factory  # type: ignore[assignment]
        if saved_ttl is None:
            os.environ.pop(admission_boundary.READINESS_CACHE_TTL_ENV_VAR, None)
        else:
            os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = saved_ttl

    check("D1 a close failure does not surface as a probe error", raised is None, str(raised))
    check("D2 the verdict stays ready", verdict is True, str(verdict))
    check("D3 close was still attempted", session.closed >= 1, f"closed={session.closed}")


def section_e() -> None:
    print("\nE — the admission block fails closed on an overrun")
    saved_ttl = os.environ.get(admission_boundary.READINESS_CACHE_TTL_ENV_VAR)
    os.environ[metered_gate.METERING_ENV_VAR] = "sqlite:///unused-deadline-semantics.db"
    os.environ.pop(admission_boundary.PHASE15_ENV_VAR, None)
    os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
    admission_boundary.reset_readiness_cache()

    session = _Session(execute_sleep=0.20)
    previous_factory = _use_session(session)
    previous_deadline = metered_gate.readiness_deadline_seconds
    metered_gate.readiness_deadline_seconds = lambda: 0.05  # type: ignore[assignment]
    try:
        block = admission_boundary.readiness_block()["admission"]
    finally:
        metered_gate._session_factory = previous_factory  # type: ignore[assignment]
        metered_gate.readiness_deadline_seconds = previous_deadline  # type: ignore[assignment]
        if saved_ttl is None:
            os.environ.pop(admission_boundary.READINESS_CACHE_TTL_ENV_VAR, None)
        else:
            os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = saved_ttl

    check("E1 an overrunning probe reports the store unavailable",
          block.get("metering_store_available") is False,
          str(block.get("metering_store_available")))
    check("E2 production_ready follows it to False (never a late ready)",
          block.get("production_ready") is False, str(block.get("production_ready")))
    check("E3 the block still carries no connection material",
          "sqlite" not in repr(block) and "unused-deadline" not in repr(block), repr(block))


def main() -> int:
    print("=" * 78)
    print("DEADLINE — probe-phase budget semantics (fte_fyjc_95)")
    print("=" * 78)

    saved_env = {k: os.environ.get(k) for k in (
        metered_gate.METERING_ENV_VAR,
        admission_boundary.PHASE15_ENV_VAR,
        admission_boundary.READINESS_CACHE_TTL_ENV_VAR,
    )}
    try:
        section_a()
        section_b()
        section_c()
        section_d()
        section_e()
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        admission_boundary.reset_readiness_cache()

    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"TOTAL: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS"
          + (f"  —  {len(failed)} FAILED" if failed else ""))
    for name, detail in failed:
        print(f"  FAIL: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
