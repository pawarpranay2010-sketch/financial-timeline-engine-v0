#!/usr/bin/env python3
"""Phase 1B — durable pending-queue capacity enforcement (PostgreSQL).

Evidence for the CORRECTED capacity design: pending capacity is derived from
``platrixa_async_jobs.capacity_held`` inside the same transaction that inserts
the job, serialised by ``pg_advisory_xact_lock``. There are NO process-local
counters anywhere on this path.

Every check below runs against a REAL disposable PostgreSQL cluster started
by the repository's supported embedded-PostgreSQL tooling (pgserver — the same
harness the Phase 5G/5K suites use). Nothing is mocked, because atomic
admission, lease recovery and durable accounting are precisely what a mock
cannot prove.

Run:
    python3 scripts/platrixa_phase1b_queue_capacity_test.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Deterministic admission mode: tenant keys only (no Phase 15 break-glass key).
# DATABASE_URL is cleared too: this suite must never inherit an ambient
# (possibly production) database credential. It is set explicitly, below, to
# the disposable embedded cluster's unix-socket URI.
for _v in (
    "PLATRIXA_DEV_API_KEY",
    "PLATRIXA_QUEUE_GLOBAL_LIMIT",
    "PLATRIXA_QUEUE_PER_TENANT_LIMIT",
    "DATABASE_URL",
):
    os.environ.pop(_v, None)

from backend.auth import async_jobs, queue_capacity  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def pg() -> str:
    """Start embedded PostgreSQL once, point the store at it, ensure schema."""
    if _PG.get("uri"):
        return _PG["uri"]
    import shutil

    import pgserver

    data = Path("/tmp/platrixa_phase1b_queue_pgdata")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(data)
    uri = srv.get_uri()
    # Hard guard: only the disposable local socket is ever used.
    if "/tmp/" not in uri:
        raise RuntimeError(f"refusing non-disposable test database: {uri!r}")
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    # backend.auth.models (via api_keys) imports backend.database.db, which
    # requires DATABASE_URL at import time. Point it at the SAME disposable
    # cluster — never at an ambient/production value.
    os.environ["DATABASE_URL"] = uri
    _PG["srv"] = srv
    _PG["uri"] = uri
    # Ensure the model tables + quotas DDL used by the API-level checks.
    from sqlalchemy import create_engine, text

    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    quota_ddl = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "database"
        / "platrixa_tenant_quota_schema.sql"
    ).read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(quota_ddl))
    engine.dispose()
    # Force the async store to create/upgrade its schema (DDL + migrations).
    async_jobs._schema_ensured.discard(uri)
    async_jobs._session_factory()
    # Visitor-ownership columns (applied by backend.auth.init_visitor_schema in
    # a deployment; create_job always writes them).
    visitor_ddl = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "database"
        / "platrixa_visitor_schema.sql"
    ).read_text(encoding="utf-8")
    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        conn.execute(text(visitor_ddl))
    engine.dispose()
    return uri


def _engine():
    from sqlalchemy import create_engine

    return create_engine(
        pg().replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )


def reset_jobs() -> None:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE platrixa_async_jobs"))
    engine.dispose()


def set_limits(global_limit: int, per_tenant: int) -> None:
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = str(global_limit)
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = str(per_tenant)
    queue_capacity.reset_cache()


def pending(tenant_id: str) -> tuple[int, int]:
    """(global, tenant) durable pending counts straight from the table."""
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        g = conn.execute(
            text("SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE")
        ).scalar_one()
        t = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t"
            ),
            {"t": tenant_id},
        ).scalar_one()
    engine.dispose()
    return int(g), int(t)


def total_jobs() -> int:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        n = conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
    engine.dispose()
    return int(n)


def status_of(job_id: str) -> str:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        s = conn.execute(
            text("SELECT status FROM platrixa_async_jobs WHERE job_id = :j"),
            {"j": job_id},
        ).scalar_one()
    engine.dispose()
    return str(s)


def force_claimable(job_id: str) -> None:
    """Test-only: clear the retry backoff gate so the job is immediately claimable."""
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET next_attempt_at = now() "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        )
    engine.dispose()


def submit(tenant: str):
    return async_jobs.create_job(tenant, {"raw_input": "hello"}, kind=async_jobs.JOB_KIND_INFERENCE)


# ---------------------------------------------------------------------------
# A. Global and per-tenant limits
# ---------------------------------------------------------------------------


def section_a() -> None:
    print("\nA. Basic limits")
    reset_jobs()

    # 1. global limit enforced
    set_limits(3, 3)
    created = [submit("t-a") for _ in range(3)]
    rejected = None
    try:
        submit("t-a")
    except queue_capacity.QueueCapacityExceeded as exc:
        rejected = exc
    check(
        "A1 global pending limit enforced atomically",
        rejected is not None
        and rejected.code == queue_capacity.GLOBAL_QUEUE_FULL
        and pending("t-a")[0] == 3
        and total_jobs() == 3,
        f"rejected={rejected!r} jobs={total_jobs()}",
    )

    # 2. per-tenant limit enforced (global headroom remains)
    reset_jobs()
    set_limits(10, 2)
    submit("t-b")
    submit("t-b")
    rejected_b = None
    try:
        submit("t-b")
    except queue_capacity.QueueCapacityExceeded as exc:
        rejected_b = exc
    check(
        "A2 per-tenant pending limit enforced",
        rejected_b is not None
        and rejected_b.code == queue_capacity.TENANT_QUEUE_FULL
        and pending("t-b") == (2, 2),
        f"rejected={rejected_b!r} pending={pending('t-b')}",
    )

    # 3. one tenant cannot consume another tenant's reserved capacity
    submit("t-c")
    submit("t-c")
    check(
        "A3 tenant capacity is isolated (t-b full, t-c admitted)",
        total_jobs() == 4 and pending("t-b") == (4, 2) and pending("t-c")[1] == 2,
        f"global={pending('t-b')} t_c={pending('t-c')} jobs={total_jobs()}",
    )
    rejected_c = None
    try:
        submit("t-c")
    except queue_capacity.QueueCapacityExceeded as exc:
        rejected_c = exc
    check(
        "A3b t-c hits its OWN per-tenant limit, not t-b's",
        rejected_c is not None and rejected_c.code == queue_capacity.TENANT_QUEUE_FULL,
        f"rejected={rejected_c!r}",
    )


# ---------------------------------------------------------------------------
# B. Release on terminal states, no double counting
# ---------------------------------------------------------------------------


def _claim(tenant: str, owner: str, lease_seconds: int = 60):
    return async_jobs.claim_next_job(
        lease_seconds=lease_seconds,
        owner=owner,
        kind=async_jobs.JOB_KIND_INFERENCE,
        tenant_id=tenant,
    )


def section_b() -> None:
    print("\nB. Release and no double counting")
    reset_jobs()
    set_limits(10, 10)

    # 4. completion releases
    rec = submit("t-r1")
    claimed = _claim("t-r1", "worker-r1")
    held_after_claim = pending("t-r1")[0]
    ok = async_jobs.complete_job(
        claimed.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-r1",
        lease_generation=claimed.lease_generation,
    )
    check(
        "B4 claim keeps the slot; completion releases it exactly once",
        ok and held_after_claim == 1 and pending("t-r1")[0] == 0,
        f"ok={ok} held_after_claim={held_after_claim} pending={pending('t-r1')}",
    )

    # 7. duplicate completion releases nothing twice
    again = async_jobs.complete_job(
        claimed.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-r1",
        lease_generation=claimed.lease_generation,
    )
    check(
        "B7 duplicate completion is refused and does not double-release",
        again is False and pending("t-r1")[0] == 0,
        f"again={again} pending={pending('t-r1')}",
    )

    # 5. permanent failure releases
    rec2 = submit("t-r2")
    c2 = _claim("t-r2", "worker-r2")
    failed = async_jobs.fail_job(
        c2.job_id,
        envelope={"error": {"code": "DETERMINISTIC_FAILURE"}},
        http_status=422,
        retryable=False,
        lease_owner="worker-r2",
        lease_generation=c2.lease_generation,
    )
    check(
        "B5 permanently failed job releases pending capacity",
        failed and status_of(c2.job_id) == "FAILED" and pending("t-r2")[0] == 0,
        f"failed={failed} status={status_of(c2.job_id)} pending={pending('t-r2')}",
    )

    # 6. a retry does not double count
    rec3 = submit("t-r3")
    c3 = _claim("t-r3", "worker-r3")
    retried = async_jobs.fail_job(
        c3.job_id,
        envelope={"error": {"code": "PROVIDER_TIMEOUT"}},
        http_status=503,
        retryable=True,
        lease_owner="worker-r3",
        lease_generation=c3.lease_generation,
    )
    check(
        "B6 retryable failure keeps exactly one slot (no double count)",
        retried
        and status_of(c3.job_id) == "RETRY_WAIT"
        and pending("t-r3")[0] == 1,
        f"retried={retried} status={status_of(c3.job_id)} pending={pending('t-r3')}",
    )

    # retry -> reclaim -> complete still releases exactly once
    force_claimable(c3.job_id)
    c3b = _claim("t-r3", "worker-r3b")
    after_reclaim = pending("t-r3")[0]
    done = async_jobs.complete_job(
        c3b.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-r3b",
        lease_generation=c3b.lease_generation,
    )
    check(
        "B6b retry then reclaim then complete: one slot held, released once",
        done and after_reclaim == 1 and pending("t-r3")[0] == 0,
        f"done={done} after_reclaim={after_reclaim} pending={pending('t-r3')}",
    )

    # 5b. retry-budget exhaustion releases
    rec4 = submit("t-r4")
    last = None
    for attempt in range(3):
        c = _claim("t-r4", f"worker-r4-{attempt}")
        if c is None:
            break
        last = c
        async_jobs.fail_job(
            c.job_id,
            envelope={"error": {"code": "PROVIDER_TIMEOUT"}},
            http_status=503,
            retryable=True,
            lease_owner=f"worker-r4-{attempt}",
            lease_generation=c.lease_generation,
        )
        force_claimable(c.job_id)
    check(
        "B5b retry-budget exhaustion terminalises FAILED and releases capacity",
        status_of(rec4.job_id) == "FAILED" and pending("t-r4")[0] == 0,
        f"status={status_of(rec4.job_id)} pending={pending('t-r4')} last={last is not None}",
    )

    # Worker id is NOT a tenant id (regression for the record_release(lease_owner) bug)
    rec5 = submit("tenant-real")
    c5 = _claim("tenant-real", "worker-identity-xyz")
    async_jobs.complete_job(
        c5.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-identity-xyz",
        lease_generation=c5.lease_generation,
    )
    engine_pending = pending("tenant-real")[0]
    check(
        "B8 capacity release uses the row's tenant, never the worker id",
        engine_pending == 0
        and pending("worker-identity-xyz")[1] == 0
        and queue_capacity.reconcile()["consistent"] is True,
        f"tenant_pending={engine_pending} worker_as_tenant={pending('worker-identity-xyz')}",
    )


# ---------------------------------------------------------------------------
# C. Concurrency
# ---------------------------------------------------------------------------


def section_c() -> None:
    print("\nC. Concurrency")
    reset_jobs()
    set_limits(5, 5)

    # 8. concurrent submissions cannot exceed the GLOBAL limit
    barrier = threading.Barrier(12)

    def worker(i: int):
        barrier.wait()
        try:
            submit(f"c-global-{i}")  # distinct tenants: only the global bound applies
            return True
        except queue_capacity.QueueCapacityExceeded:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(worker, range(12)))
    admitted = sum(1 for r in results if r)
    check(
        "C8 concurrent admission never exceeds the global limit",
        admitted == 5 and total_jobs() == 5 and pending("c-global-0")[0] == 5,
        f"admitted={admitted} jobs={total_jobs()}",
    )

    # 9. concurrent submissions cannot exceed the PER-TENANT limit
    reset_jobs()
    set_limits(50, 3)
    barrier2 = threading.Barrier(10)

    def worker2(i: int):
        barrier2.wait()
        try:
            submit("c-one-tenant")
            return True
        except queue_capacity.QueueCapacityExceeded:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results2 = list(pool.map(worker2, range(10)))
    admitted2 = sum(1 for r in results2 if r)
    check(
        "C9 concurrent admission never exceeds the per-tenant limit",
        admitted2 == 3 and total_jobs() == 3 and pending("c-one-tenant") == (3, 3),
        f"admitted={admitted2} jobs={total_jobs()}",
    )

    # 12. queue-full rejection leaves no orphaned job / reservation, and the
    # idempotency claim is released so a later retry with the same key works.
    reset_jobs()
    set_limits(1, 1)
    submit("c-full")
    before_jobs = total_jobs()
    before_pending = pending("c-full")[0]
    rejected = None
    try:
        submit("c-full")
    except queue_capacity.QueueCapacityExceeded as exc:
        rejected = exc
    check(
        "C12 queue-full rejection creates no orphaned job or reservation",
        rejected is not None
        and total_jobs() == before_jobs
        and pending("c-full")[0] == before_pending
        and queue_capacity.reconcile()["consistent"],
        f"rejected={rejected!r} jobs={total_jobs()} pending={pending('c-full')}",
    )
    # Free the slot, then admit again — accounting must be exactly correct.
    rec = _claim("c-full", "worker-c12")
    async_jobs.complete_job(
        rec.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-c12",
        lease_generation=rec.lease_generation,
    )
    after = submit("c-full")
    check(
        "C12b released capacity is reusable immediately",
        after is not None and pending("c-full")[0] == 1,
        f"pending={pending('c-full')}",
    )


# ---------------------------------------------------------------------------
# D. Recovery
# ---------------------------------------------------------------------------


def section_d() -> None:
    print("\nD. Recovery")
    reset_jobs()
    set_limits(5, 5)

    # 13. a simulated process restart loses no accounting
    rec = submit("d-restart")
    before = pending("d-restart")[0]
    # Drop every in-process cache — equivalent to a fresh worker process.
    metered_gate._session_factory_cache.clear()
    async_jobs._schema_ensured.clear()
    queue_capacity.reset_cache()
    after = pending("d-restart")[0]
    try:
        # 1 slot is already held; limit is 5, so exactly 4 more may be admitted.
        for _ in range(4):
            submit("d-restart")
        submit("d-restart")  # the 5th must be refused
        fifth_ok = True
    except queue_capacity.QueueCapacityExceeded:
        fifth_ok = False
    check(
        "D13 restart does not lose capacity accounting (limit still applies)",
        before == 1 and after == 1 and fifth_ok is False and pending("d-restart")[0] == 5,
        f"before={before} after={after} pending={pending('d-restart')} fifth_ok={fifth_ok}",
    )
    check(
        "D13b no process-local counters exist on the capacity path",
        not hasattr(queue_capacity, "_global_counter")
        and not hasattr(queue_capacity, "_tenant_counters"),
        "module exposes counters",
    )

    # 14/15/16. worker interruption + lease expiry + re-claim
    reset_jobs()
    set_limits(5, 5)
    rec2 = submit("d-lease")
    c1 = _claim("d-lease", "worker-d1", lease_seconds=1)
    held_during = pending("d-lease")[0]
    time.sleep(1.3)  # let the lease expire (worker "died")
    c2 = _claim("d-lease", "worker-d2", lease_seconds=60)
    held_after_reclaim = pending("d-lease")[0]
    check(
        "D14/15/16 interruption holds one slot; expired lease is recoverable; "
        "re-claim does not double count",
        c1 is not None
        and c2 is not None
        and held_during == 1
        and held_after_reclaim == 1
        and c2.lease_generation > c1.lease_generation,
        f"held={held_during} after={held_after_reclaim} gens={getattr(c1,'lease_generation',None)}/"
        f"{getattr(c2,'lease_generation',None)}",
    )

    # 15b. stale worker cannot commit after a reclaim
    stale = async_jobs.complete_job(
        c1.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-d1",
        lease_generation=c1.lease_generation,
    )
    check(
        "D15b stale (fenced-out) worker cannot complete; capacity still held by the new owner",
        stale is False and pending("d-lease")[0] == 1,
        f"stale={stale} pending={pending('d-lease')}",
    )
    done = async_jobs.complete_job(
        c2.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-d2",
        lease_generation=c2.lease_generation,
    )
    check(
        "D16b new owner completes and releases exactly once",
        done and pending("d-lease")[0] == 0,
        f"done={done} pending={pending('d-lease')}",
    )

    # 17b. exhausted-lease sweep terminalises and releases
    reset_jobs()
    set_limits(5, 5)
    rec3 = submit("d-sweep")
    for attempt in range(3):
        c = _claim("d-sweep", f"worker-sweep-{attempt}", lease_seconds=1)
        if c is None:
            break
        # Simulate a crashed worker: never report, just let the lease lapse.
        time.sleep(1.15)
        from sqlalchemy import text

        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE platrixa_async_jobs SET attempt_count = max_attempts WHERE job_id = :j"),
                {"j": c.job_id},
            )
        engine.dispose()
    swept = async_jobs.fail_exhausted_leases(tenant_id="d-sweep", kind=async_jobs.JOB_KIND_INFERENCE)
    check(
        "D17 exhausted-lease sweep releases pending capacity",
        swept >= 1 and status_of(rec3.job_id) == "FAILED" and pending("d-sweep")[0] == 0,
        f"swept={swept} status={status_of(rec3.job_id)} pending={pending('d-sweep')}",
    )

    # 18. reconciliation detects deliberately introduced inconsistency
    reset_jobs()
    set_limits(5, 5)
    rec4 = submit("d-recon")
    c4 = _claim("d-recon", "worker-recon")
    async_jobs.complete_job(
        c4.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-recon",
        lease_generation=c4.lease_generation,
    )
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platrixa_async_jobs SET capacity_held = TRUE WHERE job_id = :j"),
            {"j": rec4.job_id},
        )
    engine.dispose()
    diverged = queue_capacity.reconcile()
    repaired = queue_capacity.repair()
    healed = queue_capacity.reconcile()
    check(
        "D18 reconciliation detects and repairs a deliberately inconsistent row",
        diverged["consistent"] is False
        and diverged["terminal_still_held"] == 1
        and repaired["terminal_released"] == 1
        and healed["consistent"] is True,
        f"diverged={diverged} repaired={repaired} healed={healed}",
    )


# ---------------------------------------------------------------------------
# E. Configuration validation
# ---------------------------------------------------------------------------


def section_e() -> None:
    print("\nE. Configuration validation")
    cases = [("0", "10"), ("-5", "10"), ("not-a-number", "10"), ("10", "20")]
    failures = []
    for g, t in cases:
        os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = g
        os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = t
        queue_capacity.reset_cache()
        try:
            queue_capacity.validate_config()
            failures.append((g, t, "accepted"))
        except queue_capacity.QueueLimitConfigurationError:
            pass
    check(
        "E1 invalid/zero/negative/inverted limits fail fast at startup",
        failures == [],
        f"accepted-invalid={failures}",
    )
    defaults = {}
    for var in (queue_capacity.GLOBAL_LIMIT_ENV_VAR, queue_capacity.PER_TENANT_LIMIT_ENV_VAR):
        os.environ.pop(var, None)
    queue_capacity.reset_cache()
    g, t = queue_capacity.validate_config()
    check(
        "E2 unset limits use conservative finite defaults (never 'no limit')",
        g == queue_capacity.DEFAULT_GLOBAL_PENDING_LIMIT
        and t == queue_capacity.DEFAULT_PER_TENANT_PENDING_LIMIT
        and g > 0
        and t > 0
        and t <= g,
        f"defaults=({g},{t})",
    )


# ---------------------------------------------------------------------------
# F. API-level concurrency: idempotency + quota
# ---------------------------------------------------------------------------


def _api_key(tenant_id: str, name: str) -> str:
    from backend.auth import api_keys as key_store

    uri = pg()
    key_store._schema_ensured.discard(uri)
    key_store._session_factory()
    created = key_store.create_api_key(tenant_id, name, "test")
    return created["key"]


def _set_monthly_limit(raw_key: str, limit: int) -> None:
    from sqlalchemy import text

    from backend.auth.tokens import hash_token

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_tenant_quotas SET monthly_limit = :l, "
                "current_month_usage = 0 WHERE api_key_hash = :h"
            ),
            {"l": limit, "h": hash_token(raw_key)},
        )
    engine.dispose()


def _usage(raw_key: str) -> int:
    from sqlalchemy import text

    from backend.auth.tokens import hash_token

    engine = _engine()
    with engine.begin() as conn:
        v = conn.execute(
            text(
                "SELECT current_month_usage FROM platrixa_tenant_quotas "
                "WHERE api_key_hash = :h"
            ),
            {"h": hash_token(raw_key)},
        ).scalar_one()
    engine.dispose()
    return int(v)


def section_f() -> None:
    print("\nF. API-level concurrency (idempotency + quota)")
    from fastapi.testclient import TestClient

    from api.main import create_app

    reset_jobs()
    set_limits(100, 50)
    app = create_app()
    key = _api_key("f-tenant", "phase1b-idem")
    _set_monthly_limit(key, 100)
    hdr = {"x-platrixa-api-key": key}

    # 10. concurrent identical idempotent submissions create exactly ONE job
    idem = f"phase1b-{int(time.time())}-idem"
    barrier = threading.Barrier(6)

    def submit_once(_i: int):
        # Each thread gets its OWN client (TestClient is not thread-safe).
        client = TestClient(app)
        barrier.wait()
        r = client.post(
            "/v1/inference-jobs",
            headers={**hdr, "Idempotency-Key": idem},
            json={"raw_input": "revenue was 100"},
        )
        return r.status_code, r.headers.get("Idempotent-Replayed"), r.json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(submit_once, range(6)))
    created_count = sum(1 for _s, rep, _b in responses if rep == "false")
    check(
        "F10 concurrent idempotent requests create exactly one job",
        total_jobs() == 1 and created_count == 1 and _usage(key) == 1,
        f"jobs={total_jobs()} created={created_count} usage={_usage(key)} "
        f"statuses={[s for s, _r, _b in responses]}",
    )

    # 11. quota reservations stay correct under concurrent admission
    reset_jobs()
    _set_monthly_limit(key, 5)
    barrier2 = threading.Barrier(10)

    def submit_unique(i: int):
        client = TestClient(app)
        barrier2.wait()
        r = client.post(
            "/v1/inference-jobs",
            headers={**hdr, "Idempotency-Key": f"phase1b-quota-{int(time.time())}-{i}-" + str(id(threading.current_thread()))},
            json={"raw_input": "revenue was 100"},
        )
        return r.status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(submit_unique, range(10)))
    admitted = sum(1 for c in codes if c == 202)
    refused = sum(1 for c in codes if c == 429)
    check(
        "F11 monthly quota is exact under concurrent admission",
        admitted == 5 and refused == 5 and _usage(key) == 5 and total_jobs() == 5,
        f"admitted={admitted} refused={refused} usage={_usage(key)} jobs={total_jobs()} codes={codes}",
    )

    # 12b. queue-full at the API returns 503 with the precise code and no job
    reset_jobs()
    set_limits(1, 1)
    _set_monthly_limit(key, 100)
    client = TestClient(app)
    first = client.post(
        "/v1/inference-jobs",
        headers={**hdr, "Idempotency-Key": f"phase1b-full-1-{int(time.time())}"},
        json={"raw_input": "revenue was 100"},
    )
    full = client.post(
        "/v1/inference-jobs",
        headers={**hdr, "Idempotency-Key": f"phase1b-full-2-{int(time.time())}"},
        json={"raw_input": "revenue was 100"},
    )
    body = full.json()
    code = (body.get("error") or {}).get("code") or body.get("code")
    check(
        "F12b queue-full maps to HTTP 503 with an explicit capacity code",
        first.status_code == 202
        and full.status_code == 503
        and code in (queue_capacity.GLOBAL_QUEUE_FULL, queue_capacity.TENANT_QUEUE_FULL)
        and total_jobs() == 1
        and queue_capacity.reconcile()["consistent"],
        f"first={first.status_code} full={full.status_code} code={code} jobs={total_jobs()} body={str(body)[:200]}",
    )

    # 12c. the idempotency key of a queue-full rejection is reusable afterwards
    idem2 = f"phase1b-retry-{int(time.time())}"
    client.post(
        "/v1/inference-jobs",
        headers={**hdr, "Idempotency-Key": idem2},
        json={"raw_input": "revenue was 100"},
    )
    rec = _claim("f-tenant", "worker-f12c")
    async_jobs.complete_job(
        rec.job_id,
        envelope={"api_version": "v1", "status": "VERIFIED"},
        http_status=200,
        lease_owner="worker-f12c",
        lease_generation=rec.lease_generation,
    )
    retry = client.post(
        "/v1/inference-jobs",
        headers={**hdr, "Idempotency-Key": idem2},
        json={"raw_input": "revenue was 100"},
    )
    check(
        "F12c a queue-full rejection does not strand the Idempotency-Key",
        retry.status_code == 202,
        f"retry={retry.status_code} body={str(retry.json())[:200]}",
    )


def main() -> int:
    try:
        pg()
        section_a()
        section_b()
        section_c()
        section_d()
        section_e()
        section_f()
    except Exception:
        traceback.print_exc()
        check("suite completed without unexpected error", False, "exception (see traceback)")
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print(f"\n=== Phase 1B queue capacity: {passed}/{len(CHECKS)} checks passed ===")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    print("RESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
