#!/usr/bin/env python3
"""Platrixa — metered-admission timeout scope (remediation, 2026-10-25).

WHAT THIS SUITE PROVES
======================
The readiness work added three bounds to the metering engine. Two are
intentionally shared with normal metered traffic:

  * ``connect_timeout``  (libpq)        — bounded connect / reconnect;
  * ``pool_timeout``     (SQLAlchemy)   — bounded pool wait;

and one is PROBE-ONLY:

  * ``statement_timeout`` (PostgreSQL)  — applied with ``set_config(...,
    is_local => true)`` for the probe transaction, so it must never shorten a
    tenant lookup or the quota reservation UPDATE.

Sections:

  A  Scope          — the shared connection carries 0; the probe transaction
                      carries the configured bound; the pooled connection is
                      back at 0 after the probe (no leak).
  B  Locked row     — a reservation whose row is locked for 2.2s WAITS and
                      then succeeds. Before the scope fix the connect-level
                      1500ms statement_timeout aborted it and the gate answered
                      a spurious 503.
  C  Pool saturation— holding every pooled connection makes the next admission
                      fail CLOSED at ~the 1.0s pool timeout (never SQLAlchemy's
                      30s default), with no leaked connection afterwards.
  D  Baseline       — an ordinary reservation still succeeds inside 1s.

Run:
    .venv/bin/python scripts/fte_fyjc_94_admission_timeout_scope_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
SKIPS: list[tuple[str, str]] = []

DDL_PATH = REPO_ROOT / "backend" / "database" / "platrixa_tenant_quota_schema.sql"


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def skip(name: str, reason: str) -> None:
    SKIPS.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _normalize(uri: str) -> str:
    if uri.startswith("postgresql://"):
        return uri.replace("postgresql://", "postgresql+psycopg2://", 1)
    return uri


def make_tenant(engine, key: str, tenant_id: str, limit: int, *, usage: int = 0) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, :u, :m, true)"
            ),
            {"h": hash_token(key), "t": tenant_id, "l": limit, "u": usage, "m": _month()},
        )


def tenant_usage(engine, key: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT current_month_usage FROM platrixa_tenant_quotas WHERE api_key_hash = :h"),
            {"h": hash_token(key)},
        ).scalar()


def section_a(engine) -> None:
    print("\nA — the statement bound is probe-only (scope)")
    with engine.connect() as conn:
        shared = conn.exec_driver_sql("SHOW statement_timeout").scalar()
    check("A1 the shared metering connection carries NO statement_timeout",
          shared in ("0", "0ms"), f"statement_timeout={shared!r}")

    probe_s = metered_gate._session_factory()()
    try:
        metered_gate._apply_probe_statement_timeout(probe_s)
        in_probe = probe_s.execute(text("SHOW statement_timeout")).scalar()
    finally:
        probe_s.close()
    check("A2 the probe transaction carries the configured 1500ms bound",
          in_probe in ("1500ms", "1500"), f"statement_timeout={in_probe!r}")

    try:
        metered_gate.probe_store()
        probed, probe_err = True, ""
    except Exception as exc:
        probed, probe_err = False, type(exc).__name__
    check("A3 the probe completes against the real store", probed, probe_err)

    with engine.connect() as conn:
        after = conn.exec_driver_sql("SHOW statement_timeout").scalar()
    check("A4 the transaction-local bound does not leak to the next checkout",
          after in ("0", "0ms"), f"statement_timeout={after!r}")

    first = id(metered_gate._session_factory())
    second = id(metered_gate._session_factory())
    check("A5 the probe reuses the cached engine (no second pool)", first == second,
          f"{first} != {second}")


def section_b(engine, admin_engine) -> None:
    print("\nB — a locked reservation row WAITS (no 1.5s server abort)")
    key = "scope-lock-key"
    make_tenant(admin_engine, key, "tenant-lock", limit=5)

    lock_conn = admin_engine.connect()
    holder = lock_conn.begin()
    lock_conn.execute(
        text("SELECT api_key_hash FROM platrixa_tenant_quotas WHERE api_key_hash = :h FOR UPDATE"),
        {"h": hash_token(key)},
    )

    outcome: dict = {}

    def reserve() -> None:
        t0 = time.perf_counter()
        try:
            outcome["reason"], outcome["ctx"] = metered_gate.reserve_unit(key)
        except Exception as exc:  # the gate should map, never raise here
            outcome["error"] = type(exc).__name__
        outcome["elapsed"] = time.perf_counter() - t0

    worker = threading.Thread(target=reserve, daemon=True)
    worker.start()
    time.sleep(2.2)  # > the 1500ms statement bound; < the 3s+pool budget
    holder.commit()
    lock_conn.close()
    worker.join(timeout=10)

    check("B1 the reservation returned", "elapsed" in outcome, str(outcome))
    check("B2 it was NOT aborted at the 1500ms probe bound",
          outcome.get("elapsed", 0) >= 2.0, f"elapsed={outcome.get('elapsed')}")
    check("B3 it was admitted once the lock cleared",
          outcome.get("reason") == metered_gate.REASON_OK and outcome.get("ctx") is not None,
          str(outcome.get("reason")))
    check("B4 exactly one unit was reserved",
          getattr(outcome.get("ctx"), "units_reserved", None) == 1,
          str(getattr(outcome.get("ctx"), "units_reserved", None)))
    check("B5 the stored usage advanced by one", tenant_usage(admin_engine, key) == 1,
          str(tenant_usage(admin_engine, key)))
    print(f"    measured: lock held 2.2s → reservation resolved in {outcome.get('elapsed', 0):.2f}s "
          f"({outcome.get('reason')})")


def section_c(engine, admin_engine) -> None:
    print("\nC — pool saturation fails closed at the 1.0s pool timeout")
    key = "scope-pool-key"
    make_tenant(admin_engine, key, "tenant-pool", limit=5)

    pool = engine.pool
    capacity = pool.size() + getattr(pool, "_max_overflow", 0)
    held = []
    try:
        for _ in range(capacity):
            held.append(engine.connect())
        check("C1 the pool is fully checked out", pool.checkedout() == capacity,
              f"checkedout={pool.checkedout()} capacity={capacity}")

        t0 = time.perf_counter()
        reason, ctx = metered_gate.authorize_units(key)
        elapsed = time.perf_counter() - t0
        check("C2 saturation fails CLOSED (never admits)",
              reason == metered_gate.REASON_METERING_UNAVAILABLE and ctx is None,
              f"reason={reason}")
        check("C3 it does not wait SQLAlchemy's 30s default",
              0.9 <= elapsed <= 4.0, f"elapsed={elapsed:.2f}s")
        check("C4 nothing was charged while saturated",
              tenant_usage(admin_engine, key) == 0, str(tenant_usage(admin_engine, key)))
        print(f"    measured: saturated admission failed closed in {elapsed:.2f}s "
              f"(pool timeout {metered_gate.pool_timeout_seconds():.1f}s, {capacity} connections held)")
    finally:
        for conn in held:
            conn.close()

    check("C5 every connection returned to the pool", pool.checkedout() == 0,
          f"checkedout={pool.checkedout()}")
    reason2, ctx2 = metered_gate.authorize_units(key)
    check("C6 admission recovers immediately after the pressure clears",
          reason2 == metered_gate.REASON_OK and ctx2 is not None, str(reason2))
    check("C7 the recovered admission charged exactly one unit",
          tenant_usage(admin_engine, key) == 1, str(tenant_usage(admin_engine, key)))


def section_d(admin_engine) -> None:
    print("\nD — an ordinary reservation is unaffected")
    key = "scope-normal-key"
    make_tenant(admin_engine, key, "tenant-normal", limit=5)

    t0 = time.perf_counter()
    reason, ctx = metered_gate.authorize_units(key)
    elapsed = time.perf_counter() - t0
    check("D1 a normal reservation is admitted", reason == metered_gate.REASON_OK, str(reason))
    check("D2 it completes well inside the probe budget", elapsed < 1.0, f"{elapsed:.2f}s")
    check("D3 it reserved exactly one unit",
          getattr(ctx, "units_reserved", None) == 1, str(getattr(ctx, "units_reserved", None)))
    check("D4 stored usage is one", tenant_usage(admin_engine, key) == 1,
          str(tenant_usage(admin_engine, key)))


def main() -> int:
    print("=" * 78)
    print("SCOPE — metered-admission timeout scope (fte_fyjc_94)")
    print("=" * 78)

    try:
        import pgserver
    except Exception as exc:
        skip("A–D all checks", f"pgserver unavailable ({type(exc).__name__}); "
                              "the scope of a PostgreSQL statement bound cannot be faked")
        print(f"\nTOTAL: 0/0 PASS — {len(SKIPS)} SKIP (not a pass)")
        return 1

    if not DDL_PATH.exists():
        skip("A–D all checks", f"quota DDL not found ({DDL_PATH})")
        print(f"\nTOTAL: 0/0 PASS — {len(SKIPS)} SKIP (not a pass)")
        return 1

    data_dir = (Path(tempfile.mkdtemp(prefix="fte94_")) / "pgdata")
    srv = pgserver.get_server(str(data_dir))
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ.pop(metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR, None)
    metered_gate._session_factory_cache.clear()

    admin_engine = create_engine(_normalize(uri), future=True)
    ddl = DDL_PATH.read_text(encoding="utf-8")
    with admin_engine.begin() as conn:
        conn.execute(text(ddl))

    engine = metered_gate._session_factory().kw["bind"]
    try:
        section_a(engine)
        section_b(engine, admin_engine)
        section_c(engine, admin_engine)
        section_d(admin_engine)
    finally:
        metered_gate._session_factory_cache.clear()
        engine.dispose()
        admin_engine.dispose()
        srv.cleanup()

    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"TOTAL: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS"
          + (f"  —  {len(failed)} FAILED" if failed else ""))
    for name, detail in failed:
        print(f"  FAIL: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
