#!/usr/bin/env python3
"""Visitor Ownership V2 — isolated PostgreSQL migration + replay evidence.

Runs ONLY against the database in PLATRIXA_METERING_DATABASE_URL, which must
be a disposable test database. It never reads or writes any other store.

Covers:
  Phase 2  schema, backfill, indexes, nonce uniqueness, migration idempotency
  Phase 3  first-use success, replay rejection, concurrent single use,
           fail-closed on database loss, cleanup cannot drop in-window nonces

Run:
  PLATRIXA_METERING_DATABASE_URL=postgresql://... \
  PLATRIXA_VISITOR_HMAC_SECRET=... \
      python scripts/v2_pg_replay_test.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if not ok else ""))


def _url() -> str:
    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("PLATRIXA_METERING_DATABASE_URL is not set")
    if "test" not in url.lower():
        raise SystemExit("refusing to run: database URL does not look like a test database")
    return url


def _engine(sqlalchemy_url: str):
    from sqlalchemy import create_engine

    if sqlalchemy_url.startswith("postgresql://"):
        sqlalchemy_url = sqlalchemy_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(sqlalchemy_url, future=True)


def _sign(secret: str, visitor: str, method: str, path: str, query: str,
          body_hash: str, ts: str, nonce: str) -> str:
    canonical = f"V1|platrixa|{visitor}|{method}|{path}|{query}|{body_hash}|{ts}|{nonce}"
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return f"{canonical}|{sig}"


def _validate(assertion: str, method: str, path: str, query: str, body_hash: str,
              ts: str, nonce: str):
    from backend.auth.api_keys import validate_visitor_assertion

    return validate_visitor_assertion(
        provided_assertion=assertion,
        provided_visitor_id=None,
        provided_timestamp=ts,
        provided_nonce=nonce,
        method=method,
        path=path,
        query_string=query,
        body_hash=body_hash,
    )


def phase2() -> None:
    from sqlalchemy import inspect, text

    from backend.auth import async_jobs
    from backend.auth.init_visitor_nonce import initialise_visitor_nonce_schema
    from backend.auth.init_visitor_schema import initialise_visitor_schema

    url = _url()
    engine = _engine(url)
    with engine.begin() as conn:
        conn.execute(text(async_jobs._DDL))
        for stmt in async_jobs._MIGRATION_5K:
            conn.execute(text(stmt))
        for stmt in async_jobs._MIGRATION_INFERENCE:
            conn.execute(text(stmt))
        # Rerun-safety: return this test database to its pre-migration shape
        # and clear only the harness' own rows.
        for stmt in (
            "DROP INDEX IF EXISTS idx_paj_visitor_tenant",
            "DROP INDEX IF EXISTS idx_paj_tenant_visitor_status",
            "ALTER TABLE platrixa_async_jobs DROP COLUMN IF EXISTS visitor_id",
            "ALTER TABLE platrixa_async_jobs DROP COLUMN IF EXISTS owner_type",
            "DELETE FROM platrixa_async_jobs WHERE job_id IN ('job_legacy_1','job_backfill_1')",
        ):
            conn.execute(text(stmt))
        # pre-migration rows: one developer, one completed result
        conn.execute(text(
            "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash, "
            "status, kind, request_json, created_at, updated_at) VALUES "
            "('job_legacy_1','tenant-1','res_legacy_1','h1','COMPLETED','document','{}',now(),now()) "
            "ON CONFLICT (job_id) DO NOTHING"
        ))

    cols_before = {c["name"] for c in inspect(engine).get_columns("platrixa_async_jobs")}
    check("pre-migration table has no owner_type", "owner_type" not in cols_before,
          str(sorted(cols_before))[:200])

    ok1 = initialise_visitor_schema()
    ok2 = initialise_visitor_schema()  # idempotency
    check("visitor schema migration applied", ok1 is True)
    check("visitor schema migration is idempotent (second run)", ok2 is True)

    insp = inspect(engine)
    cols = {c["name"]: str(c["type"]) for c in insp.get_columns("platrixa_async_jobs")}
    check("column visitor_id UUID NULL", cols.get("visitor_id", "").upper().startswith("UUID"),
          str(cols.get("visitor_id")))
    check("column owner_type VARCHAR(32)", "VARCHAR(32)" in cols.get("owner_type", "").upper(),
          str(cols.get("owner_type")))

    idx = {i["name"] for i in insp.get_indexes("platrixa_async_jobs")}
    check("index idx_paj_visitor_tenant", "idx_paj_visitor_tenant" in idx, str(sorted(idx)))
    check("index idx_paj_tenant_visitor_status", "idx_paj_tenant_visitor_status" in idx)

    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT visitor_id, owner_type FROM platrixa_async_jobs WHERE job_id='job_legacy_1'"
        )).mappings().one()
    check("existing row backfilled to legacy", row["visitor_id"] is None
          and row["owner_type"] == "legacy", str(dict(row)))

    # backfill of a website-owned row (visitor_id present, owner_type unset)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash, "
            "status, kind, request_json, visitor_id, owner_type, created_at, updated_at) VALUES "
            "('job_backfill_1','tenant-1','res_backfill_1','h2','QUEUED','document','{}',"
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', NULL, now(), now()) "
            "ON CONFLICT (job_id) DO NOTHING"
        ))
    initialise_visitor_schema()
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT owner_type FROM platrixa_async_jobs WHERE job_id='job_backfill_1'"
        )).scalar_one()
    check("visitor row backfilled to website_visitor", row == "website_visitor", str(row))

    check("nonce schema applied", initialise_visitor_nonce_schema() is True)
    check("nonce schema idempotent", initialise_visitor_nonce_schema() is True)
    pk = insp.get_pk_constraint("visitor_assertion_nonces")
    check("nonce PRIMARY KEY on (nonce)", pk.get("constrained_columns") == ["nonce"], str(pk))

    # uniqueness enforced by the database itself
    from sqlalchemy.exc import IntegrityError

    dup_rejected = False
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO visitor_assertion_nonces (nonce, consumed) VALUES (:n, TRUE)"
            ), {"n": f"v2-{os.getpid()}-dup"})
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO visitor_assertion_nonces (nonce, consumed) VALUES (:n, TRUE)"
                ), {"n": f"v2-{os.getpid()}-dup"})
        except IntegrityError:
            dup_rejected = True
    except Exception as exc:  # pragma: no cover - diagnostic only
        print("  duplicate insert error:", type(exc).__name__)
    check("database rejects duplicate nonce", dup_rejected)


def phase3() -> None:
    from sqlalchemy import text

    secret = (os.getenv("PLATRIXA_VISITOR_HMAC_SECRET") or "").strip()
    if not secret:
        raise SystemExit("PLATRIXA_VISITOR_HMAC_SECRET is not set")
    url = _url()
    engine = _engine(url)
    visitor = "c" * 32
    body_hash = hashlib.sha256(b"").hexdigest()
    path = "/api/v1/jobs/job_1"

    import uuid

    run = uuid.uuid4().hex[:12]

    def n(name: str) -> str:
        return f"v2-{run}-{name}"

    # keep reruns independent without touching any non-test row
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM visitor_assertion_nonces WHERE nonce LIKE 'v2-%'"))

    def fresh(nonce: str):
        ts = str(int(time.time()))
        return _sign(secret, visitor, "GET", path, "", body_hash, ts, nonce), ts

    # 1. first use succeeds
    a1, ts1 = fresh(n("single"))
    reason1, vid1, _ = _validate(a1, "GET", path, "", body_hash, ts1, n("single"))
    check("first nonce consumption succeeds", reason1 == "VALID" and vid1 == visitor, reason1)

    # 2. reuse fails
    a2, ts2 = fresh(n("single"))
    reason2, vid2, _ = _validate(a2, "GET", path, "", body_hash, ts2, n("single"))
    check("reusing the same nonce is rejected", reason2 == "REPLAYDETECTED" and vid2 is None, reason2)

    # 3. concurrency: exactly one success
    for round_no in range(3):
        nonce = n(f"race-{round_no}-{int(time.time()*1000)}")
        assertion, ts = fresh(nonce)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker():
            r, _v, _t = _validate(assertion, "GET", path, "", body_hash, ts, nonce)
            with lock:
                outcomes.append(r)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        successes = outcomes.count("VALID")
        check(f"concurrent same nonce: exactly one success (round {round_no + 1})",
              successes == 1, f"valid={successes} outcomes={outcomes}")

    # 4. database unavailable → fail closed
    saved = os.environ.get("PLATRIXA_METERING_DATABASE_URL")
    os.environ["PLATRIXA_METERING_DATABASE_URL"] = "postgresql://postgres:@/nope?host=/tmp/nonexistent-v2"
    try:
        a, ts = fresh(n("dbdown"))
        reason, vid, _ = _validate(a, "GET", path, "", body_hash, ts, n("dbdown"))
    finally:
        os.environ["PLATRIXA_METERING_DATABASE_URL"] = saved
    check("database unavailable fails closed", reason in ("NONCE_STORE_UNAVAILABLE", "NONCE_STORE_ERROR")
          and vid is None, reason)

    # 5. cleanup: the REAL application routine (bounded, SKIP LOCKED).
    from backend.auth.visitor_nonces import (
        MAX_BATCH_SIZE,
        cleanup_expired_visitor_nonces,
    )

    expired_nonce, valid_nonce = n("expired-row"), n("window")

    def seed(nonce: str, expired: bool) -> None:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO visitor_assertion_nonces (nonce, asserted_at, consumed, expires_at) "
                "VALUES (:nonce, now() - INTERVAL '2 hours', TRUE, "
                + ("now() - INTERVAL '1 hour'" if expired else "now() + INTERVAL '1 hour'")
                + ") ON CONFLICT (nonce) DO UPDATE SET expires_at = EXCLUDED.expires_at"
            ), {"nonce": nonce})

    def remaining(nonce: str) -> int:
        with engine.begin() as conn:
            return conn.execute(
                text("SELECT count(*) FROM visitor_assertion_nonces WHERE nonce=:n"), {"n": nonce}
            ).scalar_one()

    seed(expired_nonce, True)
    seed(valid_nonce, False)
    check("cleanup precondition: one expired + one in-window nonce present",
          remaining(expired_nonce) == 1 and remaining(valid_nonce) == 1)

    deleted = cleanup_expired_visitor_nonces(batch_size=1000)
    check("application cleanup reports deleted > 0", deleted > 0, f"deleted={deleted}")
    check("application cleanup removed the expired nonce", remaining(expired_nonce) == 0)
    check("application cleanup retained the unexpired nonce", remaining(valid_nonce) == 1)

    again = cleanup_expired_visitor_nonces(batch_size=1000)
    check("repeated cleanup is safe and returns 0 when nothing has expired", again == 0,
          f"second_run={again}")

    # Bounded work: three more expired rows, batch_size=2 deletes exactly two.
    bounded = [n("bounded-a"), n("bounded-b"), n("bounded-c")]
    for nonce in bounded:
        seed(nonce, True)
    first_batch = cleanup_expired_visitor_nonces(batch_size=2)
    leftover = sum(remaining(n) for n in bounded)
    second_batch = cleanup_expired_visitor_nonces(batch_size=2)
    check("cleanup is bounded by batch_size", first_batch == 2 and leftover == 1,
          f"first={first_batch} leftover={leftover}")
    check("the next bounded run clears the remainder", second_batch == 1,
          f"second={second_batch}")

    for bad in (0, -1, MAX_BATCH_SIZE + 1):
        try:
            cleanup_expired_visitor_nonces(batch_size=bad)
            check(f"invalid batch_size {bad} is rejected", False, "no exception")
        except ValueError:
            check(f"invalid batch_size {bad} is rejected", True)

    a, ts = fresh(valid_nonce)
    reason, _vid, _ = _validate(a, "GET", path, "", body_hash, ts, valid_nonce)
    check("retained in-window nonce still rejects a replay after cleanup",
          reason == "REPLAYDETECTED", reason)

    fresh_after = n("post-cleanup")
    a, ts = fresh(fresh_after)
    reason, vid, _ = _validate(a, "GET", path, "", body_hash, ts, fresh_after)
    check("a fresh valid assertion still works after cleanup",
          reason == "VALID" and vid == visitor, reason)

    # an assertion outside the 60s window is refused before the nonce store
    old_ts = str(int(time.time()) - 120)
    a_old = _sign(secret, visitor, "GET", path, "", body_hash, old_ts, n("old"))
    reason, _vid, _ = _validate(a_old, "GET", path, "", body_hash, old_ts, n("old"))
    check("expired assertion rejected before nonce consumption", reason == "TIMESTAMP_EXPIRED", reason)


def main() -> int:
    _url()
    print("=== Phase 2 — migrations on isolated PostgreSQL ===")
    phase2()
    print("\n=== Phase 3 — replay protection on isolated PostgreSQL ===")
    phase3()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
