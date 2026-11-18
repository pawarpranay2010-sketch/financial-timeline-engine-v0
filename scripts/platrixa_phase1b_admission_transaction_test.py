#!/usr/bin/env python3
"""Phase 1B B1/B2/B5 — admission transaction boundary and lock protocol.

The broader capacity suite (platrixa_phase1b_queue_capacity_test.py) already
proves the *behaviour* of the limits under threads. This suite proves the
*mechanism* the correctness depends on, which behaviour alone cannot show:

  T1  The lock is a transaction-scoped advisory lock on the same PostgreSQL
      session (connection) that performs the insert.
  T2  A second SESSION (different connection, different engine) blocks on the
      lock until the first transaction ends — i.e. serialisation is at the
      cluster, not in process memory.
  T3  ROLLBACK releases the lock and reserves nothing: no row, no capacity,
      and the next admission proceeds immediately.
  T4  A database exception AFTER the reservation but BEFORE the insert
      (duplicate primary key) rolls the whole admission back: no orphan row,
      no leaked slot, lock released.
  T5  COMMIT releases the lock (no advisory lock survives the transaction).
  T6  Two SEPARATE PROCESSES racing for the final global slot admit exactly
      one job between them.
  T7  Rollback is not required for correctness of the lock key: every
      admission path resolves to one fixed key (source-level assertion that
      no second lock domain exists).

Run:  python scripts/platrixa_phase1b_admission_transaction_test.py
Disposable PostgreSQL under /tmp only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def pg_uri(uri: str) -> str:
    return uri.replace("postgresql://", "postgresql+psycopg2://", 1)


def main() -> int:
    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_admission_pg")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(data)
    uri = srv.get_uri()
    if "/tmp/" not in uri:
        print(f"refusing to run against a non-disposable store: {uri}")
        return 2

    os.environ["PLATRIXA_METERING_DATABASE_URL"] = uri
    os.environ["DATABASE_URL"] = uri
    os.environ["PLATRIXA_QUEUE_GLOBAL_LIMIT"] = "5"
    os.environ["PLATRIXA_QUEUE_PER_TENANT_LIMIT"] = "5"

    from backend.auth import async_jobs, queue_capacity

    async_jobs._session_factory()  # ensure job schema + capacity migration
    # Job ownership columns are applied by the visitor schema at startup; the
    # create_job INSERT references them, so mirror that startup step here.
    visitor_sql = (ROOT / "backend/database/platrixa_visitor_schema.sql").read_text(
        encoding="utf-8"
    )
    boot = create_engine(pg_uri(uri), future=True)
    with boot.begin() as c:
        c.execute(text(visitor_sql))
    boot.dispose()
    queue_capacity.reset_cache()

    def advisory_locks() -> int:
        engine = create_engine(pg_uri(uri), future=True)
        try:
            with engine.connect() as conn:
                return int(
                    conn.execute(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                            "AND ((classid::bigint << 32) | objid::bigint) = :k"
                        ),
                        {"k": queue_capacity.ADMISSION_LOCK_KEY},
                    ).scalar_one()
                )
        finally:
            engine.dispose()

    def job_count(tenant: str | None = None) -> int:
        engine = create_engine(pg_uri(uri), future=True)
        try:
            with engine.connect() as conn:
                if tenant is None:
                    return int(
                        conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
                    )
                return int(
                    conn.execute(
                        text("SELECT count(*) FROM platrixa_async_jobs WHERE tenant_id = :t"),
                        {"t": tenant},
                    ).scalar_one()
                )
        finally:
            engine.dispose()

    print("\nT1. the lock is a transaction-scoped advisory lock on the insert session")
    engine = create_engine(pg_uri(uri), future=True)
    conn = engine.connect()
    backend_pid = int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
    conn.commit()  # close the implicit transaction opened by that SELECT
    with conn.begin():
        lock_before = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid()"
                )
            ).scalar_one()
        )
        queue_capacity.acquire_admission_lock(conn)
        lock_row = conn.execute(
            text(
                "SELECT classid::bigint, objid::bigint, granted FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
        ).all()
        same_pid = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = :p"
                ),
                {"p": backend_pid},
            ).scalar_one()
        )
    check("no advisory lock held on the session before admission", lock_before == 0, str(lock_before))
    check(
        "advisory lock is held by the SAME backend session as the transaction",
        same_pid == 1,
        f"locks={same_pid} pid={backend_pid}",
    )
    check(
        "single lock row, granted, on the fixed admission key",
        len(lock_row) == 1
        and bool(lock_row[0][2])
        and ((int(lock_row[0][0]) << 32) | int(lock_row[0][1]))
        == queue_capacity.ADMISSION_LOCK_KEY,
        str(lock_row),
    )
    check("lock released when that transaction ended", advisory_locks() == 0, str(advisory_locks()))

    print("\nT2. a second SESSION blocks until the first transaction ends")
    blocker = engine.connect()
    blocker_tx = blocker.begin()
    queue_capacity.acquire_admission_lock(blocker)

    waited = {}

    def second_session():
        other = create_engine(pg_uri(uri), future=True)
        try:
            started = time.monotonic()
            with other.connect() as c2:
                with c2.begin():
                    queue_capacity.acquire_admission_lock(c2)
            waited["seconds"] = time.monotonic() - started
        finally:
            other.dispose()

    import threading

    t = threading.Thread(target=second_session)
    t.start()
    t.join(timeout=1.0)
    blocked = t.is_alive() and "seconds" not in waited
    blocker_tx.commit()
    blocker.close()
    t.join(timeout=15)
    check(
        "second connection cannot acquire the lock while the first holds it",
        blocked,
        f"waited={waited}",
    )
    check(
        "second connection proceeds immediately after the first transaction commits",
        "seconds" in waited and waited["seconds"] < 10,
        str(waited),
    )
    check("no lock survives both transactions", advisory_locks() == 0, str(advisory_locks()))

    print("\nT3. ROLLBACK after acquiring the lock reserves nothing")
    before = job_count()
    pending_before = queue_capacity.pending_summary()["pending_total"]
    raiser = engine.connect()
    try:
        tx = raiser.begin()
        queue_capacity.acquire_admission_lock(raiser)
        queue_capacity.check_admission(raiser, "rollback-tenant")
        raise RuntimeError("simulated route failure after reservation")
    except RuntimeError:
        tx.rollback()
    finally:
        raiser.close()
    check("rollback inserted no job", job_count() == before, f"{before} -> {job_count()}")
    check(
        "rollback returned no pending capacity",
        queue_capacity.pending_summary()["pending_total"] == pending_before,
        str(queue_capacity.pending_summary()["pending_total"]),
    )
    check("rollback released the lock", advisory_locks() == 0, str(advisory_locks()))
    admitted = async_jobs.create_job("rollback-tenant", {"doc": "after-rollback"})
    check(
        "the next admission is admitted immediately after the rollback",
        admitted is not None and job_count("rollback-tenant") == 1,
        str(job_count("rollback-tenant")),
    )

    print("\nT4. database exception AFTER reservation, BEFORE insert")
    # Fill the queue to its limit so any leaked reservation would be visible.
    with engine.begin() as c:
        c.execute(text("DELETE FROM platrixa_async_jobs"))
    os.environ["PLATRIXA_QUEUE_GLOBAL_LIMIT"] = "3"
    os.environ["PLATRIXA_QUEUE_PER_TENANT_LIMIT"] = "3"
    queue_capacity.reset_cache()
    for i in range(2):
        async_jobs.create_job("exception-tenant", {"doc": f"seed-{i}"})
    pending_seeded = queue_capacity.pending_summary()["pending_total"]
    jobs_seeded = job_count()

    raiser = engine.connect()
    tx = raiser.begin()
    queue_capacity.acquire_admission_lock(raiser)
    queue_capacity.check_admission(raiser, "exception-tenant")
    duplicate_id = str(
        raiser.execute(text("SELECT job_id FROM platrixa_async_jobs LIMIT 1")).scalar_one()
    )
    insert_error = None
    try:
        raiser.execute(
            text(
                "INSERT INTO platrixa_async_jobs "
                "(job_id, tenant_id, result_id, request_hash, status, kind, request_json, "
                " created_at, updated_at, capacity_held) "
                "VALUES (:j, 'exception-tenant', :r, :h, 'QUEUED', 'document', :q, now(), now(), TRUE)"
            ),
            {
                "j": duplicate_id,  # deliberately violates the primary key
                "r": "res-" + uuid.uuid4().hex,
                "h": uuid.uuid4().hex,
                "q": json.dumps({"doc": "explode"}),
            },
        )
    except Exception as exc:  # IntegrityError
        insert_error = type(exc).__name__
    finally:
        tx.rollback()
        raiser.close()

    check(
        "the insert failed with a database error (test premise)",
        insert_error is not None and "Integrity" in insert_error,
        str(insert_error),
    )
    check("failed insert left no extra job", job_count() == jobs_seeded, f"{jobs_seeded} -> {job_count()}")
    check(
        "failed insert leaked no capacity",
        queue_capacity.pending_summary()["pending_total"] == pending_seeded,
        str(queue_capacity.pending_summary()["pending_total"]),
    )
    check("failed insert released the lock", advisory_locks() == 0, str(advisory_locks()))
    # One slot is still free (2 of 3 held): the next admission must fit.
    fits = async_jobs.create_job("exception-tenant", {"doc": "after-error"})
    check("a later admission still sees the correct remaining slot", fits is not None)
    try:
        async_jobs.create_job("exception-tenant", {"doc": "over-limit"})
        rejected = False
    except queue_capacity.QueueCapacityExceeded:
        rejected = True
    check("the queue is then exactly full (3/3) and refuses the next job", rejected)

    print("\nT5. COMMIT releases the lock")
    conn2 = engine.connect()
    with conn2.begin():
        queue_capacity.acquire_admission_lock(conn2)
        queue_capacity.pending_counts(conn2, "commit-tenant")
    conn2.close()
    check("no advisory lock survives COMMIT", advisory_locks() == 0, str(advisory_locks()))

    print("\nT6. two separate PROCESSES race for the final global slot")
    # Fresh store so only the racers can consume capacity.
    with engine.begin() as c:
        c.execute(text("DELETE FROM platrixa_async_jobs"))
    os.environ["PLATRIXA_QUEUE_GLOBAL_LIMIT"] = "1"
    os.environ["PLATRIXA_QUEUE_PER_TENANT_LIMIT"] = "1"
    queue_capacity.reset_cache()

    go_file = Path("/tmp/platrixa_admission_go")
    go_file.unlink(missing_ok=True)
    racer = (
        "import os,sys,time;"
        f"sys.path.insert(0,{str(ROOT)!r});"
        "from pathlib import Path;"
        "from backend.auth import async_jobs, queue_capacity;"
        "go = Path('/tmp/platrixa_admission_go');"
        "\nwhile not go.exists():\n    time.sleep(0.005)\n"
        "out = Path(sys.argv[1]);"
        "\ntry:\n"
        "    async_jobs.create_job(sys.argv[2], {'doc': 'race'})\n"
        "    out.write_text('ADMITTED')\n"
        "except queue_capacity.QueueCapacityExceeded:\n"
        "    out.write_text('REJECTED')\n"
    )
    results = [Path(f"/tmp/platrixa_race_{i}.txt") for i in range(2)]
    for r in results:
        r.unlink(missing_ok=True)
    env = dict(os.environ)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", racer, str(results[i]), f"race-tenant-{i}"],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(2)
    ]
    time.sleep(0.4)  # let both processes import and block on the go file
    go_file.write_text("go")
    outs = []
    for p in procs:
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
        outs.append((p.returncode, (p.stderr or "").read() if p.stderr else ""))
    go_file.unlink(missing_ok=True)
    outcomes = [r.read_text().strip() if r.exists() else "MISSING" for r in results]
    admitted_count = outcomes.count("ADMITTED")
    check(
        "exactly one of two racing processes is admitted for the final slot",
        admitted_count == 1,
        f"outcomes={outcomes} rc={[o[0] for o in outs]}",
    )
    check(
        "the losing process was rejected on capacity, not by an error",
        outcomes.count("REJECTED") == 1,
        f"outcomes={outcomes}",
    )
    check("the store holds exactly one job after the cross-process race", job_count() == 1, str(job_count()))
    check(
        "capacity accounting is consistent after the cross-process race",
        queue_capacity.reconcile()["consistent"] is True,
        str(queue_capacity.reconcile()),
    )
    check("no advisory lock is left behind by the racers", advisory_locks() == 0, str(advisory_locks()))

    print("\nT7. one lock key, one protocol, no second lock domain")
    pattern_ok = True
    detail = ""
    for rel in (
        "backend/auth/async_jobs.py",
        "api/routes/async_api.py",
        "api/routes/inference_api.py",
    ):
        source = (ROOT / rel).read_text(encoding="utf-8")
        advisory_calls = source.count("pg_advisory")
        if advisory_calls and rel != "backend/auth/async_jobs.py":
            pattern_ok = False
            detail = f"{rel} issues its own advisory-lock SQL"
    capacity_source = (ROOT / "backend/auth/queue_capacity.py").read_text(encoding="utf-8")
    # The one real call site: the SQL literal, so a docstring mention does not
    # mask a second acquisition.
    advisory_calls = capacity_source.count("SELECT pg_advisory_xact_lock(:k)")
    other_modules = [
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.py")
        if "scripts" not in p.parts
        and "queue_capacity" not in p.name
        and "pg_advisory_xact_lock(" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    check(
        "only queue_capacity.py issues advisory-lock SQL",
        pattern_ok and advisory_calls == 1 and not other_modules,
        detail or f"call sites={advisory_calls} other={other_modules}",
    )
    check(
        "the admission lock key is a single fixed constant",
        capacity_source.count("ADMISSION_LOCK_KEY = ") == 1
        and capacity_source.count("SELECT pg_advisory_xact_lock(:k)") == 1
        and "ADMISSION_LOCK_KEY" in capacity_source.split("def acquire_admission_lock")[1][:1200],
        "constant or acquisition appears more than once",
    )
    check(
        "the lock key is the transaction-scoped variant (never session-scoped)",
        "pg_advisory_lock(" not in capacity_source and "pg_advisory_unlock" not in capacity_source,
        "a session-scoped advisory lock call exists",
    )
    admissibility = async_jobs.create_job.__doc__ or ""
    source = (ROOT / "backend/auth/async_jobs.py").read_text(encoding="utf-8")
    check(
        "create_job takes the lock before counting and inserting (single transaction)",
        "with session.begin():\n                queue_capacity.acquire_admission_lock(session)\n"
        "                queue_capacity.check_admission(session, tenant_id)"
        in source,
        "admission sequence not found in the expected order",
    )
    check(
        "there is exactly one production INSERT INTO platrixa_async_jobs",
        sum(
            (ROOT / p).read_text(encoding="utf-8").count("INSERT INTO platrixa_async_jobs")
            for p in ("backend/auth/async_jobs.py",)
        )
        == 1
        and not any(
            "INSERT INTO platrixa_async_jobs" in (ROOT / p).read_text(encoding="utf-8")
            for p in ("api/routes/async_api.py", "api/routes/inference_api.py")
        ),
        "an alternate insert path exists",
    )

    engine.dispose()

    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    print(f"\n=== Phase 1B admission transaction: {passed}/{len(CHECKS)} checks passed ===")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
