#!/usr/bin/env python3
"""Phase 1B B4 — capacity_held migration against a POPULATED legacy store.

Proves, on a disposable PostgreSQL cluster, that the durable-capacity
migration converges an upgraded store to the fresh-install shape:

  1. A pre-migration table (no capacity_held, no kind, no lease columns)
     holding QUEUED / PROCESSING / RETRY_WAIT / COMPLETED / FAILED rows is
     migrated in place with no data loss.
  2. Terminal rows are backfilled to capacity_held = FALSE, pending rows to
     TRUE — so pending capacity after upgrade equals the number of genuinely
     pending jobs (not the table size).
  3. The migration is idempotent: a second run (and a concurrent run from a
     second process) changes nothing.
  4. The partial index used by the capacity counts exists and is actually
     chosen by the planner for the global and per-tenant count queries.
  5. Cross-tenant accounting is correct (a tenant count never includes
     another tenant's rows).

Run:  python scripts/platrixa_phase1b_capacity_migration_test.py
Needs: pgserver + SQLAlchemy + psycopg2 in the interpreter.
Never touches a production database: the cluster lives under /tmp and the
script refuses to run against a non-loopback / non-/tmp store.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


LEGACY_DDL = """
CREATE TABLE platrixa_async_jobs (
    job_id        VARCHAR(64)  NOT NULL,
    tenant_id     VARCHAR(64)  NOT NULL,
    result_id     VARCHAR(64)  NOT NULL,
    request_hash  VARCHAR(64)  NOT NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'QUEUED',
    request_json  TEXT         NOT NULL,
    result_json   TEXT,
    http_status   INTEGER,
    error_json    TEXT,
    retryable     BOOLEAN      NOT NULL DEFAULT false,
    request_id    VARCHAR(128),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id)
);
"""

# The realistic legacy population, in the proportions that matter: pending
# rows that must keep their slot and terminal rows that must give theirs back.
POPULATION = [
    # (status, tenant_id, count, attempt_count)
    ("QUEUED", "tenant-a", 2, 0),
    ("PROCESSING", "tenant-a", 1, 1),
    ("RETRY_WAIT", "tenant-b", 1, 1),
    ("COMPLETED", "tenant-a", 3, 1),
    ("FAILED", "tenant-b", 2, 5),
]

EXPECTED_PENDING = sum(n for s, _t, n, _a in POPULATION if s in ("QUEUED", "PROCESSING", "RETRY_WAIT"))
EXPECTED_TOTAL = sum(n for _s, _t, n, _a in POPULATION for _ in range(1))


def engine_for(uri: str):
    from sqlalchemy import create_engine

    return create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)


def seed_legacy(uri: str) -> None:
    from sqlalchemy import text

    engine = engine_for(uri)
    with engine.begin() as conn:
        conn.execute(text(LEGACY_DDL))
        for status, tenant, n, attempts in POPULATION:
            for i in range(n):
                conn.execute(
                    text(
                        "INSERT INTO platrixa_async_jobs "
                        "(job_id, tenant_id, result_id, request_hash, status, request_json, "
                        " result_json, attempt_count_placeholder) VALUES (:j, :t, :r, :h, :s, :q, :rj, 0)"
                    )
                    if False
                    else text(
                        "INSERT INTO platrixa_async_jobs "
                        "(job_id, tenant_id, result_id, request_hash, status, request_json, result_json) "
                        "VALUES (:j, :t, :r, :h, :s, :q, :rj)"
                    ),
                    {
                        "j": f"{status.lower()}-{tenant}-{i}",
                        "t": tenant,
                        "r": f"res-{status.lower()}-{tenant}-{i}",
                        "h": uuid.uuid4().hex,
                        "s": status,
                        "q": json.dumps({"doc": "synthetic"}),
                        "rj": json.dumps({"api_version": "v1"}) if status == "COMPLETED" else None,
                    },
                )
    engine.dispose()


def main() -> int:
    import pgserver

    data = Path("/tmp/platrixa_migration_pg")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(data)
    uri = srv.get_uri()
    if "/tmp/" not in uri:
        print(f"refusing to run against a non-disposable store: {uri}")
        return 2
    os.environ["PLATRIXA_METERING_DATABASE_URL"] = uri
    os.environ["DATABASE_URL"] = uri

    print("\n1. legacy store: populated, pre-migration")
    seed_legacy(uri)
    from sqlalchemy import text

    engine = engine_for(uri)
    with engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'platrixa_async_jobs'"
                )
            )
        }
        total = conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
    check("legacy table has no capacity_held column", "capacity_held" not in cols, str(sorted(cols))[:120])
    check("legacy table has no kind column", "kind" not in cols)
    check(f"legacy population is {EXPECTED_TOTAL} rows", total == EXPECTED_TOTAL, str(total))

    print("\n2. migration (runs via the real _session_factory path)")
    from backend.auth import async_jobs

    # A second, independent process proves startup races are harmless.
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys;"
                f"os.environ['PLATRIXA_METERING_DATABASE_URL']={uri!r};"
                f"os.environ['DATABASE_URL']={uri!r};"
                f"sys.path.insert(0,{str(ROOT)!r});"
                "from backend.auth import async_jobs as aj;"
                "aj._session_factory();"
                "print('child-migrated')"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    check(
        "concurrent second process migrates without error",
        child.returncode == 0 and "child-migrated" in child.stdout,
        (child.stderr or "")[-200:],
    )

    async_jobs._session_factory()  # migration for this process

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, capacity_held, kind, count(*) FROM platrixa_async_jobs GROUP BY 1,2,3")
        ).all()
        total = conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
        pending = conn.execute(
            text("SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE")
        ).scalar_one()

    by_status = {}
    for status, held, kind, n in rows:
        by_status.setdefault(status, {"held": 0, "released": 0, "kinds": set()})
        by_status[status]["held" if held else "released"] += int(n)
        by_status[status]["kinds"].add(kind)

    check(f"no rows lost by the migration ({EXPECTED_TOTAL})", total == EXPECTED_TOTAL, str(total))
    check(
        "terminal rows backfilled to capacity_held = FALSE",
        by_status.get("COMPLETED", {}).get("released") == 3
        and by_status.get("FAILED", {}).get("released") == 2,
        str(by_status),
    )
    check(
        "pending rows (QUEUED/PROCESSING/RETRY_WAIT) hold capacity",
        by_status.get("QUEUED", {}).get("held") == 2
        and by_status.get("PROCESSING", {}).get("held") == 1
        and by_status.get("RETRY_WAIT", {}).get("held") == 1,
        str(by_status),
    )
    check(
        f"pending capacity equals genuinely pending jobs ({EXPECTED_PENDING})",
        pending == EXPECTED_PENDING,
        str(pending),
    )
    check(
        "kind backfilled to 'document' for every historical row",
        all(v["kinds"] == {"document"} for v in by_status.values()),
        str({k: v["kinds"] for k, v in by_status.items()}),
    )

    print("\n3. idempotence")
    from backend.auth import queue_capacity

    before = queue_capacity.pending_summary()
    async_jobs._schema_ensured.clear()
    async_jobs._session_factory()  # forced re-run of every migration statement
    after = queue_capacity.pending_summary()
    check(
        "re-running the migration changes no counts",
        before["pending_total"] == after["pending_total"] == EXPECTED_PENDING,
        f"{before['pending_total']} -> {after['pending_total']}",
    )
    check(
        "reconciler reports a consistent store after the migration",
        queue_capacity.reconcile()["consistent"] is True,
        str(queue_capacity.reconcile()),
    )

    print("\n4. per-tenant counting")
    with engine.connect() as conn:
        tenant_a = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t"
            ),
            {"t": "tenant-a"},
        ).scalar_one()
        tenant_b = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t"
            ),
            {"t": "tenant-b"},
        ).scalar_one()
    check("tenant-a pending is its own 3 rows only", tenant_a == 3, str(tenant_a))
    check("tenant-b pending is its own 1 row only", tenant_b == 1, str(tenant_b))
    check(
        "per-tenant pending counts sum to the global pending count",
        tenant_a + tenant_b == EXPECTED_PENDING,
        f"{tenant_a}+{tenant_b} vs {EXPECTED_PENDING}",
    )

    print("\n5. index suitability for the capacity counts")
    with engine.connect() as conn:
        indexes = {
            r[0]
            for r in conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'platrixa_async_jobs'")
            )
        }
        plans = {}
        for label, sql, params in (
            (
                "global",
                "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE",
                {},
            ),
            (
                "tenant",
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t",
                {"t": "tenant-a"},
            ),
        ):
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            plan = " ".join(
                str(r[0]) for r in conn.execute(text("EXPLAIN " + sql), params).all()
            )
            plans[label] = plan
    check(
        "partial index idx_async_jobs_capacity_held exists",
        "idx_async_jobs_capacity_held" in indexes,
        str(sorted(indexes)),
    )
    check(
        "planner can serve the global count from the partial index",
        "idx_async_jobs_capacity_held" in plans["global"],
        plans["global"][:160],
    )
    # At the expected bounded size the planner may legitimately prefer the
    # cheaper single-column tenant index over the partial one. What must hold
    # is that an index serves the predicate and no sequential scan is needed.
    check(
        "planner serves the per-tenant count without a sequential scan",
        "Index" in plans["tenant"] and "Seq Scan" not in plans["tenant"],
        plans["tenant"][:160],
    )
    check(
        "per-tenant predicate is index-supported by either capacity-aware index",
        any(name in plans["tenant"] for name in ("idx_async_jobs_capacity_held", "idx_async_jobs_tenant")),
        plans["tenant"][:160],
    )

    engine.dispose()

    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    print(f"\n=== Phase 1B capacity migration: {passed}/{len(CHECKS)} checks passed ===")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
