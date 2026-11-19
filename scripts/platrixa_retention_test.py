#!/usr/bin/env python3
"""M12 — retention pruning keeps promises and drops only expired evidence.

Checks:

  * the default is a DRY RUN (nothing deleted, nothing counted as deleted);
  * old TERMINAL jobs are deleted;
  * recent terminal jobs are kept;
  * old NON-TERMINAL jobs (QUEUED / PROCESSING) are kept — an old promise is an
    incident, not garbage;
  * a terminal row that somehow still holds capacity is kept (deleting it would
    leak a queue slot);
  * expired rate-limit windows are pruned;
  * a non-loopback database is refused unless --allow-remote;
  * the batch limit is honoured (bounded work per run).

Run:
    python3 scripts/platrixa_retention_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def main() -> int:
    import pgserver
    from sqlalchemy import create_engine, text

    from backend.auth import async_jobs

    data = Path("/tmp/platrixa_retention_pgdata")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data)
    uri = server.get_uri()
    if "/tmp/" not in uri:
        raise SystemExit(f"refusing non-disposable database: {uri!r}")

    from backend.auth import gate as metered_gate

    os.environ[metered_gate.METERING_ENV_VAR] = uri
    async_jobs._schema_ensured.discard(uri)
    async_jobs._session_factory()

    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS platrixa_tenant_rate_limits ("
            " tenant_id VARCHAR(64) NOT NULL, window_kind VARCHAR(16) NOT NULL,"
            " window_start TIMESTAMPTZ NOT NULL, count INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (tenant_id, window_kind, window_start))"
        ))
        rows = [
            # job_id, status, age_days, capacity_held
            ("old_completed", "COMPLETED", 120, False),
            ("old_failed", "FAILED", 120, False),
            ("new_completed", "COMPLETED", 1, False),
            ("old_queued", "QUEUED", 200, True),
            ("old_processing", "PROCESSING", 200, True),
            ("old_terminal_but_held", "COMPLETED", 200, True),
        ]
        for job_id, status, age, held in rows:
            conn.execute(text(
                "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash,"
                " status, kind, request_json, capacity_held, created_at, updated_at)"
                " VALUES (:j, 'ret-tenant', :r, 'h', :s, 'inference', '{}', :held,"
                " now() - (:age || ' days')::interval, now() - (:age || ' days')::interval)"
            ), {"j": job_id, "r": job_id, "s": status, "held": held, "age": age})
        conn.execute(text(
            "INSERT INTO platrixa_tenant_rate_limits (tenant_id, window_kind, window_start, count)"
            " VALUES ('ret-tenant', 'minute', now() - interval '120 days', 3),"
            "        ('ret-tenant', 'minute', now(), 1)"
        ))

    def remaining() -> set[str]:
        with engine.begin() as conn:
            return {str(r[0]) for r in conn.execute(text("SELECT job_id FROM platrixa_async_jobs"))}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/platrixa_retention.py"),
             "--database-url", uri, *args],
            capture_output=True, text=True,
        )

    dry = run()
    check("dry run exits 0", dry.returncode == 0, dry.stderr[:300])
    dry_json = json.loads(dry.stdout)
    check("dry run deletes nothing", dry_json["deleted"] == {} and len(remaining()) == 6)
    check("dry run reports 2 eligible terminal jobs", dry_json["eligible"]["terminal_jobs"] == 2,
          str(dry_json["eligible"]))
    check(
        "dry run counts the non-terminal rows it would keep",
        dry_json["eligible"]["non_terminal_jobs_kept"] == 2,
        str(dry_json["eligible"]),
    )
    check(
        "dry run counts the capacity-holding rows it would keep",
        dry_json["eligible"]["jobs_holding_capacity_kept"] == 3,
        str(dry_json["eligible"]),
    )
    check("dry run counts the expired rate-limit window", dry_json["eligible"]["rate_limit_windows"] == 1)

    remote = subprocess.run(
        [sys.executable, str(ROOT / "scripts/platrixa_retention.py"),
         "--database-url", "postgresql://user@db.example.com:5432/prod", "--apply"],
        capture_output=True, text=True,
    )
    check("a non-loopback database is refused without --allow-remote", remote.returncode != 0)

    applied = run("--apply", "--days", "90")
    check("apply exits 0", applied.returncode == 0, applied.stderr[:400])
    applied_json = json.loads(applied.stdout)
    left = remaining()
    check("apply deletes both expired terminal jobs", applied_json["deleted"]["terminal_jobs"] == 2,
          str(applied_json["deleted"]))
    check("old COMPLETED is gone", "old_completed" not in left)
    check("old FAILED is gone", "old_failed" not in left)
    check("recent terminal job is kept", "new_completed" in left)
    check("old QUEUED job is kept (a promise is not garbage)", "old_queued" in left)
    check("old PROCESSING job is kept", "old_processing" in left)
    check("a terminal row still holding capacity is kept", "old_terminal_but_held" in left)

    with engine.begin() as conn:
        windows = conn.execute(text("SELECT count(*) FROM platrixa_tenant_rate_limits")).scalar_one()
    check("the expired rate-limit window is pruned and the current one kept", int(windows) == 1, str(windows))

    # bounded batches: with batch=1 and max_batches=1 at most one row goes
    with engine.begin() as conn:
        for job_id in ("batch_a", "batch_b"):
            conn.execute(text(
                "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash, status,"
                " kind, request_json, capacity_held, created_at, updated_at)"
                " VALUES (:j, 'ret-tenant', :j, 'h', 'COMPLETED', 'inference', '{}', FALSE,"
                " now() - interval '200 days', now() - interval '200 days')"
            ), {"j": job_id})
    bounded = run("--apply", "--days", "90", "--batch", "1", "--max-batches", "1")
    bounded_json = json.loads(bounded.stdout)
    check(
        "the batch limit bounds one run's work",
        bounded_json["deleted"]["terminal_jobs"] == 1,
        str(bounded_json["deleted"]),
    )

    # The fixture deliberately contains an IMPOSSIBLE row: a COMPLETED job that
    # still holds capacity. Retention must keep it (deleting it would hide the
    # leak), and the reconciler must REPORT it. Both are asserted, because a
    # reconciler that stays quiet about a leak is worse than no reconciler.
    from backend.auth import queue_capacity

    rec = queue_capacity.reconcile()
    check(
        "the surviving held-but-terminal row is still visible to reconcile",
        rec["terminal_still_held"] == 1,
        json.dumps(rec),
    )
    check(
        "reconcile flags the inconsistency instead of hiding it",
        rec["consistent"] is False,
        json.dumps(rec),
    )
    check(
        "reconcile counts the pending rows exactly",
        rec["pending_rows"] == 3 and rec["pending_already_released"] == 0,
        json.dumps(rec),
    )

    engine.dispose()
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
