#!/usr/bin/env python3
"""RC audit — job-history cursors, visitor scoping, UTC windows, accounting.

Disposable embedded PostgreSQL, the real FastAPI app over HTTP, the real job
store, the real quota meters and the real usage-reporting code paths.

A. Cursor correctness (GET /v1/jobs)
   identical timestamps, multi-page traversal, inserts between pages, empty and
   final pages, malformed/tampered/foreign cursors, canonical encoding, limit
   bounds, status-filter + cursor interaction, index usability.

B. Visitor scoping
   signed visitor assertions over HTTP for GET /v1/jobs and GET /v1/jobs/{id}:
   cross-tenant, cross-visitor, caller-supplied id expansion, foreign job
   indistinguishable from a missing one, fail-closed assertions.

C. UTC usage windows
   first/last instant of a UTC month, exact month boundary, December→January,
   admission-time attribution when completion lands in another month, naive or
   local-time shift traps, invalid month labels.

D. Transaction outcome accounting
   real admissions reconciled against BOTH meters, rejected-before-admission,
   idempotent replay, worker retry, per-state exclusivity, blocked/deferred
   policy, coverage limitation, developer/owner report agreement.

Run:
    python3 scripts/platrixa_rc_audit_test.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _v in (
    "PLATRIXA_DEV_API_KEY",
    "PLATRIXA_QUEUE_GLOBAL_LIMIT",
    "PLATRIXA_QUEUE_PER_TENANT_LIMIT",
    "PLATRIXA_TENANT_RATE_LIMIT",
    "PLATRIXA_TENANT_RATE_WINDOW_SECONDS",
    "PLATRIXA_TENANT_TX_MONTHLY_LIMIT",
    "PLATRIXA_BATCH_MAX_TRANSACTIONS",
    "PLATRIXA_BATCH_MAX_BYTES",
    "PLATRIXA_BATCH_MAX_INPUT_CHARS",
    "PLATRIXA_BATCH_MAX_SECONDS",
    "DATABASE_URL",
):
    os.environ.pop(_v, None)

from backend.auth import (  # noqa: E402
    async_jobs,
    batch_jobs,
    job_history,
    queue_capacity,
    tenant_rate_limit,
    transaction_quota,
    usage_reporting,
)
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}

MGMT_TOKEN = "rc-audit-management-token-not-a-real-credential"
VISITOR_SECRET = "rc-audit-visitor-secret-not-a-real-credential"

OWNER_TENANT = "rc-owner"
TENANT_TS = "rc-ts-tenant"          # identical timestamps + pagination
TENANT_FINAL = "rc-final-page"      # exactly-full final page
TENANT_WWW = "rc-www"               # visitor scoping
TENANT_W = "rc-window"              # UTC window boundaries
TENANT_C = "rc-accounting"          # real admissions vs both meters
TENANT_D = "rc-states"              # per-state fixtures

RAW_INPUT = "purchased furniture from raj for rs.25000"
_KEYS: dict[str, str] = {}
_COUNTER = {"n": 0}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


# ---------------------------------------------------------------------------
# Disposable store
# ---------------------------------------------------------------------------

def pg() -> str:
    if _PG.get("uri"):
        return _PG["uri"]
    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_rc_audit_pgdata")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(data)
    uri = srv.get_uri()
    if "/tmp/" not in uri:
        raise RuntimeError(f"refusing non-disposable test database: {uri!r}")
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ["DATABASE_URL"] = uri
    _PG["srv"] = srv
    _PG["uri"] = uri

    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        conn.execute(
            text((ROOT / "backend/database/platrixa_tenant_quota_schema.sql").read_text("utf-8"))
        )
    engine.dispose()
    async_jobs._schema_ensured.discard(uri)
    async_jobs._session_factory()
    return uri


def _engine():
    from sqlalchemy import create_engine

    return create_engine(
        pg().replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )


def _scalar(sql: str, params: dict | None = None):
    from sqlalchemy import text

    engine = _engine()
    try:
        with engine.begin() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    finally:
        engine.dispose()


def _rows(sql: str, params: dict | None = None):
    from sqlalchemy import text

    engine = _engine()
    try:
        with engine.begin() as conn:
            return conn.execute(text(sql), params or {}).mappings().all()
    finally:
        engine.dispose()


def _exec(sql: str, params: dict | None = None) -> None:
    from sqlalchemy import text

    engine = _engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def insert_job(
    job_id: str,
    tenant: str,
    *,
    created_at: datetime,
    updated_at: datetime | None = None,
    status: str = "COMPLETED",
    kind: str = "inference",
    visitor: str | None = None,
    owner: str | None = None,
    transaction_count: int = 1,
    batch_mode: str = "single",
    result_json: str | None = None,
) -> None:
    """Seed one job row with an EXACT created_at (read-model fixture)."""
    _exec(
        "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash, status,"
        " kind, request_json, result_json, http_status, retryable, request_id, created_at,"
        " updated_at, visitor_id, owner_type, capacity_held, batch_mode, transaction_count)"
        " VALUES (:j, :t, :r, :h, :st, :k, '{}', :rj, 200, false, :rid, :c, :u, :v, :o, TRUE,"
        " :bm, :tc)",
        {
            "j": job_id,
            "t": tenant,
            "r": "res-" + job_id.replace("job_", ""),
            "h": hashlib.sha256(job_id.encode()).hexdigest(),
            "st": status,
            "k": kind,
            "rj": result_json,
            "rid": "req-" + uuid.uuid4().hex[:8],
            "c": created_at,
            "u": updated_at or created_at,
            "v": visitor,
            "o": owner,
            "bm": batch_mode,
            "tc": transaction_count,
        },
    )


def insert_tx(job_id: str, tenant: str, index: int, status: str, created_at: datetime) -> None:
    _exec(
        "INSERT INTO platrixa_batch_transactions (job_id, tenant_id, batch_index,"
        " transaction_id, status, created_at, updated_at)"
        " VALUES (:j, :t, :i, :tx, :st, :c, :c)",
        {
            "j": job_id,
            "t": tenant,
            "i": index,
            "tx": f"tx-{index}",
            "st": status,
            "c": created_at,
        },
    )


def visitor_headers(visitor: str, path: str, query: str = "") -> dict:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = (
        f"V1|platrixa|{visitor}|GET|{path}|{query}|{body_hash}|{ts}|{nonce}"
    )
    sig = hmac.new(VISITOR_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "x-platrixa-surface": "website",
        "x-platrixa-visitor-id": visitor,
        "x-platrixa-visitor-assertion": f"{canonical}|{sig}",
        "x-platrixa-request-timestamp": ts,
        "x-platrixa-visitor-nonce": nonce,
    }


def raw_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main() -> int:  # noqa: C901 - a linear audit
    pg()
    os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"] = MGMT_TOKEN
    os.environ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID"] = OWNER_TENANT
    os.environ["PLATRIXA_VISITOR_HMAC_SECRET"] = VISITOR_SECRET
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = "1000"
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = "500"
    queue_capacity.reset_cache()
    os.environ[tenant_rate_limit.TENANT_RATE_LIMIT_ENV_VAR] = "100000"
    os.environ[tenant_rate_limit.TENANT_RATE_WINDOW_ENV_VAR] = "60"
    tenant_rate_limit.reset_cache()
    os.environ[batch_jobs.MAX_TRANSACTIONS_ENV_VAR] = "10"
    os.environ[batch_jobs.MAX_INPUT_CHARS_ENV_VAR] = "20000"
    batch_jobs.reset_cache()
    os.environ[transaction_quota.TX_MONTHLY_LIMIT_ENV_VAR] = "500"
    transaction_quota.reset_cache()

    from fastapi.testclient import TestClient

    from api.main import create_app
    from backend.auth import models

    app = create_app()
    client = TestClient(app)
    mgmt = {"x-platrixa-management-token": MGMT_TOKEN}

    for tenant in (TENANT_TS, TENANT_FINAL, TENANT_WWW, TENANT_W, TENANT_C, TENANT_D):
        r = client.post(
            "/v1/developer/tenants",
            headers=mgmt,
            json={
                "tenant_id": tenant,
                "developer_name": f"{tenant} developer",
                "environment": "test",
                "monthly_limit": 500,
            },
        )
        if r.status_code != 201:
            check(f"provision {tenant}", False, f"http={r.status_code} {r.text[:200]}")
            return 1
        _KEYS[tenant] = r.json()["key"]

    def headers(tenant: str, extra: dict | None = None) -> dict:
        h = {"x-platrixa-api-key": _KEYS[tenant]}
        h.update(extra or {})
        return h

    def submit(tenant: str, body: dict, idem: str | None = None):
        _COUNTER["n"] += 1
        h = headers(tenant)
        h["Idempotency-Key"] = idem or f"rc-audit-{_COUNTER['n']}-{uuid.uuid4().hex[:12]}"
        return client.post("/v1/inference-jobs", headers=h, json=body)

    def page(tenant: str, query: str, extra: dict | None = None):
        return client.get(f"/v1/jobs?{query}", headers=headers(tenant, extra))

    # =====================================================================
    # A. CURSOR CORRECTNESS
    # =====================================================================
    print("\nA. Cursor correctness — GET /v1/jobs")

    tie = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    tie_ids = [f"job_{i:024x}" for i in range(1, 13)]
    for jid in tie_ids:
        insert_job(jid, TENANT_TS, created_at=tie)
    older = [
        (f"job_{100 + i:024x}", tie - timedelta(minutes=5 + i)) for i in range(3)
    ]
    for jid, ts in older:
        insert_job(jid, TENANT_TS, created_at=ts)

    db_order = [
        str(r["job_id"])
        for r in _rows(
            "SELECT job_id FROM platrixa_async_jobs WHERE tenant_id = :t"
            " ORDER BY created_at DESC, job_id DESC",
            {"t": TENANT_TS},
        )
    ]

    # -- identical timestamps: total order by job_id, every row exactly once --
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        q = "limit=5" + (f"&cursor={cursor}" if cursor else "")
        r = page(TENANT_TS, q)
        if r.status_code != 200:
            check("pagination page succeeds", False, f"http={r.status_code} {r.text[:200]}")
            break
        body = r.json()
        seen.extend(j["job_id"] for j in body["jobs"])
        pages += 1
        cursor = body.get("next_cursor")
        if not cursor or pages > 20:
            break
    check(
        "12 jobs sharing one created_at page with no duplicate and no skip",
        sorted(seen) == sorted(db_order) and len(seen) == len(set(seen)) == 15,
        f"pages={pages} seen={len(seen)} unique={len(set(seen))} expected=15",
    )
    mismatch = next(((a, b) for a, b in zip(seen, db_order) if a != b), None)
    check(
        "tied timestamps are ordered by job_id DESC (stable total order)",
        seen == db_order,
        f"first difference: {mismatch}",
    )

    # -- has_more / next_cursor existence ------------------------------------
    first = page(TENANT_TS, "limit=5").json()
    check(
        "has_more is true and next_cursor is present when another page exists",
        first["has_more"] is True and bool(first["next_cursor"]),
        json.dumps({k: first[k] for k in ("count", "has_more", "next_cursor")}),
    )
    exact = page(TENANT_TS, "limit=15").json()
    check(
        "a page that exactly consumes the result set reports has_more=false and no cursor",
        exact["has_more"] is False and exact["next_cursor"] is None and exact["count"] == 15,
        json.dumps({k: exact[k] for k in ("count", "has_more", "next_cursor")}),
    )
    beyond = page(TENANT_TS, f"limit=5&cursor={exact_cursor}" if (exact_cursor := job_history.next_cursor_for(exact["jobs"])) else "limit=5")
    beyond_body = beyond.json() if beyond.status_code == 200 else {}
    check(
        "a cursor at the oldest row yields an empty, terminal page",
        beyond.status_code == 200
        and beyond_body.get("jobs") == []
        and beyond_body.get("has_more") is False
        and beyond_body.get("next_cursor") is None,
        f"http={beyond.status_code} {beyond.text[:160]}",
    )

    final_ids = [f"job_{200 + i:024x}" for i in range(4)]
    for i, jid in enumerate(final_ids):
        insert_job(jid, TENANT_FINAL, created_at=tie + timedelta(seconds=i))
    full = page(TENANT_FINAL, "limit=4").json()
    check(
        "an exactly-full final page does not advertise a further page",
        full["count"] == 4 and full["has_more"] is False and full["next_cursor"] is None,
        json.dumps({k: full[k] for k in ("count", "has_more", "next_cursor")}),
    )

    # -- concurrent insertions between page requests -------------------------
    seen2: list[str] = []
    r1 = page(TENANT_TS, "limit=3").json()
    seen2.extend(j["job_id"] for j in r1["jobs"])
    cursor = r1["next_cursor"]
    late_ids = [f"job_{300 + i:024x}" for i in range(3)]
    for i, jid in enumerate(late_ids):
        insert_job(jid, TENANT_TS, created_at=tie + timedelta(minutes=1, seconds=i))
    while cursor:
        r = page(TENANT_TS, f"limit=3&cursor={cursor}")
        if r.status_code != 200:
            break
        b = r.json()
        seen2.extend(j["job_id"] for j in b["jobs"])
        cursor = b.get("next_cursor")
    check(
        "jobs inserted between pages are neither duplicated nor do they cause skips",
        len(seen2) == len(set(seen2)) and set(seen2) == set(db_order),
        f"seen={len(seen2)} unique={len(set(seen2))} expected={len(db_order)}",
    )
    check(
        "insertions newer than the first page are absent from that traversal (keyset snapshot)",
        not (set(seen2) & set(late_ids)),
        f"appeared: {sorted(set(seen2) & set(late_ids))}",
    )
    fresh = page(TENANT_TS, "limit=100").json()
    check(
        "a fresh traversal sees the inserted jobs with no duplicates",
        len(fresh["jobs"]) == len({j["job_id"] for j in fresh["jobs"]}) == 18,
        f"count={fresh['count']}",
    )

    # -- limit bounds --------------------------------------------------------
    big = page(TENANT_TS, "limit=101")
    zero = page(TENANT_TS, "limit=0")
    max_ok = page(TENANT_TS, "limit=100")
    one_ok = page(TENANT_TS, "limit=1")
    check(
        "limit above the hard maximum is rejected with INPUT_INVALID",
        big.status_code == 400 and big.json()["error"]["code"] == "INPUT_INVALID",
        f"http={big.status_code} {big.text[:140]}",
    )
    check(
        "limit below 1 is rejected with INPUT_INVALID",
        zero.status_code == 400 and zero.json()["error"]["code"] == "INPUT_INVALID",
        f"http={zero.status_code} {zero.text[:140]}",
    )
    check(
        "limit=100 and limit=1 are accepted and honoured",
        max_ok.status_code == 200
        and max_ok.json()["limit"] == 100
        and one_ok.status_code == 200
        and one_ok.json()["count"] == 1,
        f"max={max_ok.status_code} one={one_ok.status_code}",
    )
    default_page = page(TENANT_TS, "")
    check(
        "the default page size is 20 and is reported back to the client",
        default_page.json()["limit"] == 20 and default_page.json()["count"] == 18,
        f"{default_page.status_code} {default_page.text[:140]}",
    )

    # -- status filter x cursor ---------------------------------------------
    for jid in tie_ids[:4]:
        _exec("UPDATE platrixa_async_jobs SET status = 'QUEUED' WHERE job_id = :j", {"j": jid})
    filt_seen: list[str] = []
    cursor = None
    for _ in range(10):
        q = "limit=2&status=QUEUED" + (f"&cursor={cursor}" if cursor else "")
        b = page(TENANT_TS, q).json()
        filt_seen.extend(j["job_id"] for j in b["jobs"])
        cursor = b.get("next_cursor")
        if not cursor:
            break
    check(
        "status filtering and keyset pagination compose correctly",
        sorted(filt_seen) == sorted(tie_ids[:4]) and len(filt_seen) == len(set(filt_seen)),
        f"seen={filt_seen}",
    )
    lower = page(TENANT_TS, "status=queued&limit=100").json()
    check(
        "the status filter is case-insensitive and normalised to the stored vocabulary",
        lower["count"] == 4 and all(j["status"] == "QUEUED" for j in lower["jobs"]),
        f"count={lower['count']}",
    )

    # -- cursor codec: canonical, validated ---------------------------------
    stamp = datetime(2026, 11, 10, 12, 0, 0, 123456, tzinfo=timezone.utc)
    enc = job_history.encode_cursor(stamp, tie_ids[0])
    check(
        "cursor encoding is canonical (URL-safe, unpadded, deterministic)",
        enc == job_history.encode_cursor(stamp, tie_ids[0])
        and "=" not in enc
        and all(c.isalnum() or c in "-_" for c in enc),
        enc,
    )
    rt = job_history.decode_cursor(enc)
    check(
        "cursor round-trips the exact ordering key (timestamp + job id)",
        rt[0] == stamp and rt[1] == tie_ids[0],
        f"{rt}",
    )
    check(
        "the cursor carries exactly the ordering key and a version, nothing else",
        sorted(json.loads(base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)))) == ["c", "j", "v"],
        enc,
    )

    malformed = {
        "empty string": "",
        "not base64url": "not-a-cursor!!",
        "base64 but not JSON": base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        "JSON but not an object": base64.urlsafe_b64encode(b"[1,2,3]").decode(),
        "unsupported version": raw_cursor({"v": 2, "c": tie.isoformat(), "j": tie_ids[0]}),
        "missing position": raw_cursor({"v": 1}),
        "malformed job id": raw_cursor({"v": 1, "c": tie.isoformat(), "j": "job_ZZZ"}),
        "naive timestamp": raw_cursor({"v": 1, "c": "2026-11-10T12:00:00", "j": tie_ids[0]}),
        "unparseable timestamp": raw_cursor({"v": 1, "c": "yesterday", "j": tie_ids[0]}),
        "oversized": "A" * 600,
    }
    for label, bad in malformed.items():
        r = page(TENANT_TS, f"limit=5&cursor={bad}")
        check(
            f"cursor rejected ({label}) with 400 INVALID_CURSOR",
            r.status_code == 400 and r.json().get("error", {}).get("code") == "INVALID_CURSOR",
            f"http={r.status_code} {r.text[:160]}",
        )

    # -- cross-tenant and cross-visitor cursor reuse -------------------------
    foreign_cursor = job_history.next_cursor_for(full["jobs"])
    reused = page(TENANT_TS, f"limit=5&cursor={foreign_cursor}")
    check(
        "a cursor minted for another tenant selects only the caller's rows",
        reused.status_code == 200
        and not (set(j["job_id"] for j in reused.json()["jobs"]) & set(final_ids)),
        f"http={reused.status_code} {reused.text[:160]}",
    )
    tampered = raw_cursor({"v": 1, "c": tie.isoformat(), "j": f"job_{999:024x}"})
    tr = page(TENANT_TS, f"limit=5&cursor={tampered}")
    check(
        "a hand-crafted cursor cannot select another tenant's or a nonexistent job",
        tr.status_code == 200
        and not (set(j["job_id"] for j in tr.json()["jobs"]) & set(final_ids)),
        f"http={tr.status_code} {tr.text[:160]}",
    )

    # -- index / no OFFSET ---------------------------------------------------
    from sqlalchemy import event as _event
    from sqlalchemy import text as _sql_text
    from sqlalchemy.engine import Engine as _Engine

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    _event.listen(_Engine, "before_cursor_execute", _capture)
    try:
        job_history.list_jobs(TENANT_TS, limit=5)
        job_history.list_jobs(TENANT_TS, limit=5, cursor=enc)
    finally:
        _event.remove(_Engine, "before_cursor_execute", _capture)
    listing_sql = [s for s in captured if "platrixa_async_jobs" in s]
    first_sql = listing_sql[0] if listing_sql else ""
    cursor_sql = listing_sql[1] if len(listing_sql) > 1 else ""
    check(
        "the executed listing statement is bounded with LIMIT and has no OFFSET",
        bool(first_sql)
        and "LIMIT" in first_sql.upper()
        and "OFFSET" not in first_sql.upper(),
        first_sql[:320],
    )
    check(
        "the executed listing statement scopes to the tenant and applies the keyset bound",
        "tenant_id =" in first_sql
        and "(created_at, job_id) <" in cursor_sql
        and "ORDER BY created_at DESC, job_id DESC" in first_sql,
        (first_sql + "\n" + cursor_sql)[:600],
    )
    engine = _engine()
    try:
        with engine.begin() as conn:
            conn.execute(_sql_text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                str(row[0])
                for row in conn.execute(
                    _sql_text(
                        "EXPLAIN SELECT job_id FROM platrixa_async_jobs"
                        " WHERE tenant_id = :t ORDER BY created_at DESC, job_id DESC LIMIT 6"
                    ),
                    {"t": TENANT_TS},
                )
            )
    finally:
        engine.dispose()
    check(
        "the keyset query is served by idx_async_jobs_tenant_cursor",
        "idx_async_jobs_tenant_cursor" in plan,
        plan[:400],
    )
    idx = _scalar(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_async_jobs_tenant_cursor'"
    )
    check("the cursor index exists on the store", int(idx) == 1, f"idx={idx}")

    # =====================================================================
    # B. VISITOR SCOPING
    # =====================================================================
    print("\nB. Visitor scoping — GET /v1/jobs and GET /v1/jobs/{job_id}")

    A = "a" * 32
    B = "b" * 32
    dev_job = f"job_{400:024x}"
    a_job = f"job_{401:024x}"
    b_job = f"job_{402:024x}"
    insert_job(dev_job, TENANT_WWW, created_at=tie, owner="developer_client")
    insert_job(a_job, TENANT_WWW, created_at=tie, owner="website_visitor", visitor=A)
    insert_job(b_job, TENANT_WWW, created_at=tie, owner="website_visitor", visitor=B)

    dev_list = page(TENANT_WWW, "limit=100").json()
    check(
        "the developer data plane (no visitor scope) lists the whole tenant",
        {j["job_id"] for j in dev_list["jobs"]} == {dev_job, a_job, b_job},
        json.dumps([j["job_id"] for j in dev_list["jobs"]]),
    )
    dev_cursor = job_history.next_cursor_for(dev_list["jobs"])

    ra = client.get(
        "/v1/jobs?limit=100",
        headers=headers(TENANT_WWW, visitor_headers(A, "/v1/jobs", "limit=100")),
    )
    a_ids = {j["job_id"] for j in ra.json()["jobs"]} if ra.status_code == 200 else set()
    check(
        "a visitor-scoped listing contains only that visitor's jobs",
        ra.status_code == 200 and a_ids == {a_job},
        f"http={ra.status_code} ids={sorted(a_ids)} {ra.text[:140]}",
    )
    rb = client.get(
        "/v1/jobs?limit=100",
        headers=headers(TENANT_WWW, visitor_headers(B, "/v1/jobs", "limit=100")),
    )
    check(
        "a second visitor sees only its own job",
        rb.status_code == 200 and {j["job_id"] for j in rb.json()["jobs"]} == {b_job},
        f"http={rb.status_code} {rb.text[:160]}",
    )

    a_own = client.get(
        f"/v1/jobs/{a_job}",
        headers=headers(TENANT_WWW, visitor_headers(A, f"/v1/jobs/{a_job}")),
    )
    a_foreign = client.get(
        f"/v1/jobs/{b_job}",
        headers=headers(TENANT_WWW, visitor_headers(A, f"/v1/jobs/{b_job}")),
    )
    a_missing = client.get(
        f"/v1/jobs/job_{999:024x}",
        headers=headers(TENANT_WWW, visitor_headers(A, f"/v1/jobs/job_{999:024x}")),
    )
    check(
        "a visitor can poll its own job",
        a_own.status_code == 200,
        f"http={a_own.status_code} {a_own.text[:160]}",
    )
    check(
        "a foreign visitor's job is indistinguishable from a nonexistent one",
        a_foreign.status_code == a_missing.status_code == 404
        and a_foreign.json().get("error", {}).get("code")
        == a_missing.json().get("error", {}).get("code")
        and set(a_foreign.json()) == set(a_missing.json()),
        f"foreign={a_foreign.status_code}:{a_foreign.text[:120]} "
        f"missing={a_missing.status_code}:{a_missing.text[:120]}",
    )
    a_dev = client.get(
        f"/v1/jobs/{dev_job}",
        headers=headers(TENANT_WWW, visitor_headers(A, f"/v1/jobs/{dev_job}")),
    )
    check(
        "a visitor cannot reach a developer-owned job in the same tenant",
        a_dev.status_code == 404,
        f"http={a_dev.status_code} {a_dev.text[:140]}",
    )

    expand_query = f"limit=100&visitor_id={B}&tenant_id={TENANT_TS}&owner_type=developer_client"
    rx = client.get(
        f"/v1/jobs?{expand_query}",
        headers=headers(TENANT_WWW, visitor_headers(A, "/v1/jobs", expand_query)),
    )
    check(
        "caller-supplied visitor_id/tenant_id cannot widen a visitor listing",
        rx.status_code == 200 and {j["job_id"] for j in rx.json()["jobs"]} == {a_job},
        f"http={rx.status_code} {rx.text[:160]}",
    )

    cross_query = f"limit=100&cursor={dev_cursor}"
    rc = client.get(
        f"/v1/jobs?{cross_query}",
        headers=headers(TENANT_WWW, visitor_headers(A, "/v1/jobs", cross_query)),
    )
    check(
        "a developer listing's cursor cannot lift visitor restrictions",
        rc.status_code == 200 and not (set(j["job_id"] for j in rc.json()["jobs"]) - {a_job}),
        f"http={rc.status_code} {rc.text[:160]}",
    )

    other_tenant_query = "limit=100"
    ro = client.get(
        f"/v1/jobs?{other_tenant_query}",
        headers=headers(TENANT_TS, visitor_headers(A, "/v1/jobs", other_tenant_query)),
    )
    check(
        "visitor scope composes with the authenticated tenant (no cross-tenant leak)",
        ro.status_code == 200 and ro.json()["jobs"] == [],
        f"http={ro.status_code} {ro.text[:160]}",
    )

    missing_assert = client.get(
        "/v1/jobs", headers=headers(TENANT_WWW, {"x-platrixa-surface": "website"})
    )
    check(
        "a website request without a visitor assertion fails closed",
        missing_assert.status_code == 403,
        f"http={missing_assert.status_code} {missing_assert.text[:140]}",
    )
    forged = visitor_headers(A, "/v1/jobs", "limit=100")
    forged["x-platrixa-visitor-assertion"] = forged["x-platrixa-visitor-assertion"].replace(
        A, "c" * 32
    )
    rf = client.get("/v1/jobs?limit=100", headers=headers(TENANT_WWW, forged))
    check(
        "a forged visitor assertion fails closed",
        rf.status_code == 403,
        f"http={rf.status_code} {rf.text[:140]}",
    )

    # =====================================================================
    # C. UTC USAGE WINDOWS
    # =====================================================================
    print("\nC. UTC usage windows")

    w_nov = usage_reporting.month_window("2026-11")
    w_dec = usage_reporting.month_window("2026-12")
    check(
        "the monthly window is a UTC half-open interval",
        w_nov["start"] == "2026-11-01T00:00:00+00:00"
        and w_nov["end"] == "2026-12-01T00:00:00+00:00",
        json.dumps(w_nov),
    )
    check(
        "December rolls over into the next year",
        w_dec["end"] == "2027-01-01T00:00:00+00:00",
        json.dumps(w_dec),
    )
    check(
        "window bounds are timezone-aware UTC, never naive or server-local",
        datetime.fromisoformat(w_nov["start"]).utcoffset() == timedelta(0)
        and datetime.fromisoformat(w_nov["end"]).utcoffset() == timedelta(0),
        json.dumps(w_nov),
    )
    bad_months = ["2026-13", "2026-00", "2026", "2026-1-1", "", "not-a-month"]
    check(
        "an invalid month label is rejected rather than silently reinterpreted",
        all(
            _raises(lambda m=m: usage_reporting.month_window(m)) for m in bad_months
        ),
        json.dumps(bad_months),
    )

    nov_first = datetime(2026, 11, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    nov_last = datetime(2026, 11, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
    dec_first = datetime(2026, 12, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    dec_last = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    jan_first = datetime(2027, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    spanning = datetime(2026, 11, 30, 23, 59, 0, tzinfo=timezone.utc)
    local_trap = datetime(2026, 10, 31, 20, 0, 0, tzinfo=timezone.utc)

    insert_job(f"job_{500:024x}", TENANT_W, created_at=nov_first)
    insert_job(f"job_{501:024x}", TENANT_W, created_at=nov_last)
    insert_job(f"job_{502:024x}", TENANT_W, created_at=dec_first)
    insert_job(f"job_{503:024x}", TENANT_W, created_at=dec_last)
    insert_job(f"job_{504:024x}", TENANT_W, created_at=jan_first)
    insert_job(
        f"job_{505:024x}",
        TENANT_W,
        created_at=spanning,
        updated_at=dec_first + timedelta(minutes=10),
    )
    insert_job(f"job_{506:024x}", TENANT_W, created_at=local_trap)
    insert_job(
        f"job_{507:024x}", TENANT_W, created_at=datetime(2026, 11, 15, 12, tzinfo=timezone.utc),
        kind="document",
    )
    for i, state in enumerate(("VERIFIED", "BLOCKED", "DEFERRED")):
        insert_tx(f"job_{505:024x}", TENANT_W, i, state, datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc))

    nov = usage_reporting.tenant_usage_counts(TENANT_W, month="2026-11")
    dec = usage_reporting.tenant_usage_counts(TENANT_W, month="2026-12")
    jan = usage_reporting.tenant_usage_counts(TENANT_W, month="2027-01")
    octo = usage_reporting.tenant_usage_counts(TENANT_W, month="2026-10")

    check(
        "the first and last instants of a UTC month are inside that month",
        nov["jobs"]["submitted"] == 3,
        json.dumps(nov["jobs"]),
    )
    check(
        "the exact month boundary belongs to the next month, not this one",
        dec_first.isoformat().startswith("2026-12")
        and dec["jobs"]["submitted"] == 2
        and nov["jobs"]["submitted"] == 3,
        f"nov={nov['jobs']['submitted']} dec={dec['jobs']['submitted']}",
    )
    check(
        "December-to-January attribution is correct at the year boundary",
        jan["jobs"]["submitted"] == 1 and dec["jobs"]["submitted"] == 2,
        f"dec={dec['jobs']['submitted']} jan={jan['jobs']['submitted']}",
    )
    check(
        "a job admitted in one month and completed in another counts by admission",
        octo["jobs"]["submitted"] == 1 and nov["jobs"]["submitted"] == 3,
        f"oct={octo['jobs']['submitted']} nov={nov['jobs']['submitted']}",
    )
    check(
        "no local-time shift: a late-October instant is never counted in November",
        octo["jobs"]["submitted"] == 1 and nov["jobs"]["submitted"] == 3,
        f"oct={octo['jobs']['submitted']} nov={nov['jobs']['submitted']}",
    )
    check(
        "document jobs are excluded from inference job counters but reported separately",
        nov["jobs"]["document_jobs_submitted"] == 1
        and nov["jobs"]["submitted"] == 3,
        json.dumps(nov["jobs"]),
    )
    check(
        "per-transaction states are windowed by their own admission instant",
        nov["transactions"]["tracked_rows"] == 3
        and nov["transactions"]["verified"] == 1
        and nov["transactions"]["blocked"] == 1
        and nov["transactions"]["deferred"] == 1
        and dec["transactions"]["tracked_rows"] == 0,
        json.dumps(nov["transactions"]),
    )
    check(
        "no month argument uses the current UTC month bucket",
        nov["window"]["month"] == "2026-11"
        and usage_reporting.month_window(None)["month"] == models.current_usage_month()
        and usage_reporting.tenant_usage_counts(TENANT_W)["window"]["month"]
        == usage_reporting.month_window(None)["month"],
        usage_reporting.month_window(None)["month"],
    )
    check(
        "the window states its basis (UTC calendar month, by admission time)",
        "UTC" in nov["window"]["basis"] and "created_at" in nov["window"]["basis"],
        nov["window"]["basis"],
    )

    # =====================================================================
    # D. TRANSACTION OUTCOME ACCOUNTING
    # =====================================================================
    print("\nD. Transaction outcome accounting")

    def job_units(tenant: str) -> int:
        r = client.get("/v1/usage", headers=headers(tenant))
        return int(r.json().get("used", -1)) if r.status_code == 200 else -1

    def tx_units(tenant: str) -> int:
        return int(transaction_quota.usage(tenant)["used"])

    j0, t0 = job_units(TENANT_C), tx_units(TENANT_C)

    single = submit(TENANT_C, {"raw_input": RAW_INPUT})
    check(
        "a single-transaction job is admitted",
        single.status_code == 202 and single.json().get("job_id"),
        f"http={single.status_code} {single.text[:160]}",
    )
    single_id = single.json().get("job_id", "")

    batch_body = {
        "mode": "independent",
        "transactions": [
            {"transaction_id": f"tx-{i:03d}", "input": RAW_INPUT} for i in range(3)
        ],
    }
    batch_idem = f"rc-batch-{uuid.uuid4().hex[:10]}"
    batch = submit(TENANT_C, batch_body, idem=batch_idem)
    batch_id = batch.json().get("job_id", "")
    check(
        "a 3-transaction batch is admitted as one job",
        batch.status_code == 202 and bool(batch_id),
        f"http={batch.status_code} {batch.text[:160]}",
    )

    ordered_body = {
        "mode": "ordered",
        "transactions": [
            {"transaction_id": f"ord-{i}", "input": RAW_INPUT} for i in range(2)
        ],
    }
    ordered = submit(TENANT_C, ordered_body)
    check(
        "a 2-transaction ordered batch is admitted as one job",
        ordered.status_code == 202 and ordered.json().get("job_id"),
        f"http={ordered.status_code} {ordered.text[:160]}",
    )

    replay = submit(TENANT_C, batch_body, idem=batch_idem)
    check(
        "an idempotent replay returns the original job and creates no second job",
        replay.status_code in (200, 202) and replay.json().get("job_id") == batch_id,
        f"http={replay.status_code} {replay.text[:200]}",
    )

    dup = submit(
        TENANT_C,
        {
            "mode": "independent",
            "transactions": [
                {"transaction_id": "dup-1", "input": RAW_INPUT},
                {"transaction_id": "dup-1", "input": RAW_INPUT},
            ],
        },
    )
    check(
        "a batch with duplicate transaction ids is rejected before admission",
        dup.status_code == 400
        and dup.json()["error"]["code"] == "BATCH_DUPLICATE_TRANSACTION_ID",
        f"http={dup.status_code} {dup.text[:180]}",
    )

    too_many = submit(
        TENANT_C,
        {
            "mode": "independent",
            "transactions": [
                {"transaction_id": f"big-{i}", "input": RAW_INPUT} for i in range(11)
            ],
        },
    )
    check(
        "an over-limit batch is rejected before admission",
        too_many.status_code == 400,
        f"http={too_many.status_code} {too_many.text[:180]}",
    )

    j1, t1 = job_units(TENANT_C), tx_units(TENANT_C)
    check(
        "one job unit is charged per admitted job (3), never per transaction",
        j1 - j0 == 3,
        f"job units {j0} -> {j1}",
    )
    check(
        "transaction units follow the batch size (1 + 3 + 2 = 6)",
        t1 - t0 == 6,
        f"transaction units {t0} -> {t1}",
    )
    check(
        "rejected-before-admission requests consume neither meter",
        dup.status_code == 400 and too_many.status_code == 400 and j1 - j0 == 3 and t1 - t0 == 6,
        f"jobs+={j1 - j0} tx+={t1 - t0}",
    )

    db_admitted = int(
        _scalar(
            "SELECT coalesce(sum(transaction_count), 0) FROM platrixa_async_jobs"
            " WHERE tenant_id = :t",
            {"t": TENANT_C},
        )
    )
    db_jobs = int(
        _scalar(
            "SELECT count(*) FROM platrixa_async_jobs WHERE tenant_id = :t AND kind = 'inference'",
            {"t": TENANT_C},
        )
    )
    check(
        "the transaction meter equals sum(transaction_count) over admitted jobs",
        db_admitted == t1,
        f"db={db_admitted} meter={t1}",
    )
    check(
        "the job meter equals the number of admitted inference jobs for this tenant",
        db_jobs == j1 and db_jobs == 3,
        f"db={db_jobs} meter={j1}",
    )
    check(
        "job rows are not double-charged by the replay or the retry-free admission path",
        db_jobs == 3,
        f"rows={db_jobs}",
    )

    count = usage_reporting.tenant_usage_counts(TENANT_C)
    check(
        "the usage report agrees with the transaction meter on admitted transactions",
        count["transactions"]["admitted"] == t1 == 6,
        f"report={count['transactions']['admitted']} meter={t1}",
    )
    check(
        "the usage report agrees with the job meter on submitted jobs",
        count["jobs"]["submitted"] == j1 == 3,
        f"report={count['jobs']['submitted']} meter={j1}",
    )
    check(
        "the per-state buckets are mutually exclusive and sum to the tracked rows",
        sum(
            count["transactions"][s]
            for s in (
                "verified",
                "review_required",
                "unsupported",
                "invalid_input",
                "failed",
                "blocked",
                "deferred",
                "pending",
            )
        )
        == count["transactions"]["tracked_rows"]
        == 5,
        json.dumps(count["transactions"]),
    )
    check(
        "the coverage limitation is explicit: single jobs admit transactions without a row",
        count["transactions"]["coverage"]["admitted_transactions"] == 6
        and count["transactions"]["coverage"]["per_transaction_rows"] == 5
        and count["transactions"]["coverage"]["admitted_without_a_per_transaction_row"] == 1
        and "single-transaction jobs" in count["transactions"]["coverage"]["note"],
        json.dumps(count["transactions"]["coverage"]),
    )
    check(
        "no total is fabricated for states the stored rows cannot describe",
        count["transactions"]["evaluated_tracked"] == 0
        and count["transactions"]["pending"] == 5,
        json.dumps(count["transactions"]),
    )
    check(
        "the report states that admitted-but-unevaluated transactions are still charged",
        "admitted (and charged)" in count["transactions"]["states_note"],
        count["transactions"]["states_note"],
    )

    # -- developer + owner reports reconcile --------------------------------
    dev_usage = client.get("/v1/usage", headers=headers(TENANT_C))
    dev_body = dev_usage.json() if dev_usage.status_code == 200 else {}
    check(
        "GET /v1/usage labels the per-credential quota unit and window",
        dev_usage.status_code == 200
        and dev_body["key_quota"]["unit"].startswith("admitted job units")
        and dev_body["key_quota"]["resets_at"]
        and dev_body["used"] == j1,
        f"http={dev_usage.status_code} {dev_usage.text[:200]}",
    )
    check(
        "GET /v1/usage reports the same tenant counters as the usage module",
        dev_body.get("tenant_usage", {}).get("transactions", {}).get("admitted") == 6
        and dev_body.get("tenant_usage", {}).get("jobs", {}).get("submitted") == 3,
        json.dumps(dev_body.get("tenant_usage", {}))[:240],
    )
    check(
        "GET /v1/usage states the per-key attribution limitation",
        any("per TENANT" in item for item in dev_body.get("limitations", [])),
        json.dumps(dev_body.get("limitations", [])),
    )

    tenant_usage = client.get(f"/v1/developer/usage?tenant_id={TENANT_C}", headers=mgmt)
    tu = tenant_usage.json() if tenant_usage.status_code == 200 else {}
    check(
        "GET /v1/developer/usage is scoped to the authenticated management tenant",
        tenant_usage.status_code == 200
        and tu.get("tenant_id") == OWNER_TENANT
        and tu.get("used") == 0
        and tu.get("tenant_usage", {}).get("jobs", {}).get("submitted") == 0,
        f"http={tenant_usage.status_code} {tenant_usage.text[:260]}",
    )
    check(
        "a caller-supplied tenant_id cannot redirect the management usage report",
        TENANT_C not in tenant_usage.text,
        tenant_usage.text[:220],
    )
    check(
        "GET /v1/developer/usage labels the job and transaction quotas separately",
        tu.get("quota", {}).get("jobs", {}).get("unit") == "admitted job units"
        and tu.get("quota", {}).get("transactions", {}).get("unit")
        == "admitted accounting transactions"
        and tu.get("quota", {}).get("jobs", {}).get("month") == models.current_usage_month()
        and bool(tu.get("quota", {}).get("jobs", {}).get("resets_at"))
        and len(tu.get("limitations", [])) >= 2,
        json.dumps(
            {
                "quota": tu.get("quota"),
                "limitations": tu.get("limitations"),
            }
        )[:900],
    )

    owner_usage = client.get("/v1/owner/usage", headers=mgmt)
    ob = owner_usage.json() if owner_usage.status_code == 200 else {}
    slot = next((t for t in ob.get("tenants", []) if t["tenant_id"] == TENANT_C), {})
    check(
        "GET /v1/owner/usage reconciles jobs and transactions for the tenant",
        owner_usage.status_code == 200
        and slot.get("jobs", {}).get("total") == 3
        and slot.get("transactions", {}).get("admitted_by_jobs") == 6,
        f"http={owner_usage.status_code} {json.dumps(slot)[:260]}",
    )
    check(
        "GET /v1/owner/usage distinguishes all-time counters from the monthly quota",
        slot.get("jobs", {}).get("window") == "all_time"
        and slot.get("quota", {}).get("jobs", {}).get("used") == 3
        and slot.get("quota", {}).get("transactions", {}).get("used") == 6,
        json.dumps(slot.get("jobs", {}))[:200],
    )

    # -- worker retry charges nothing ---------------------------------------
    claimed = async_jobs.claim_next_job(owner="rc-audit-worker", kind="inference", tenant_id=TENANT_C)
    retry_ok = False
    if claimed is not None:
        retry_ok = async_jobs.fail_job(
            claimed.job_id,
            envelope={"error": {"code": "MODEL_UNAVAILABLE", "message": "audit retry"}},
            http_status=503,
            retryable=True,
            lease_owner=claimed.lease_owner,
            lease_generation=claimed.lease_generation,
        )
    j2, t2 = job_units(TENANT_C), tx_units(TENANT_C)
    retried = _rows(
        "SELECT status, attempt_count FROM platrixa_async_jobs WHERE job_id = :j",
        {"j": claimed.job_id if claimed else ""},
    )
    check(
        "a worker retry moves the job to RETRY_WAIT without charging again",
        claimed is not None
        and retry_ok
        and retried
        and retried[0]["status"] == "RETRY_WAIT"
        and int(retried[0]["attempt_count"]) == 1
        and (j2, t2) == (j1, t1),
        f"claimed={bool(claimed)} retry={retry_ok} jobs {j1}->{j2} tx {t1}->{t2} "
        f"{[dict(r) for r in retried]}",
    )
    check(
        "the retried job still appears exactly once in job history",
        sum(
            1
            for j in page(TENANT_C, "limit=100").json()["jobs"]
            if j["job_id"] == (claimed.job_id if claimed else "")
        )
        == 1,
        "retried job duplicated or missing",
    )

    # -- blocked / deferred / pending categorisation -------------------------
    d_job = f"job_{600:024x}"
    insert_job(
        d_job,
        TENANT_D,
        created_at=datetime(2026, 11, 10, 9, tzinfo=timezone.utc),
        status="COMPLETED",
        transaction_count=4,
        batch_mode="independent",
    )
    for i, state in enumerate(("VERIFIED", "BLOCKED", "DEFERRED", "PENDING")):
        insert_tx(d_job, TENANT_D, i, state, datetime(2026, 11, 10, 9, tzinfo=timezone.utc))
    dc = usage_reporting.tenant_usage_counts(TENANT_D)
    check(
        "blocked, deferred and pending rows are counted as admitted, not as failures",
        dc["transactions"]["blocked"] == 1
        and dc["transactions"]["deferred"] == 1
        and dc["transactions"]["pending"] == 1
        and dc["transactions"]["failed"] == 0
        and dc["transactions"]["evaluated_tracked"] == 1,
        json.dumps(dc["transactions"]),
    )
    check(
        "the blocked/deferred rows reconcile with the admission count (no anomaly)",
        dc["transactions"]["admitted"] == 4
        and dc["transactions"]["tracked_rows"] == 4
        and dc["transactions"]["reconciliation"]["consistent"] is True,
        json.dumps(dc["transactions"]["reconciliation"]),
    )

    # =====================================================================
    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} RC audit checks passed")
    if failed:
        for name, _ok, detail in failed:
            print(f"  FAILED: {name} — {detail}")
    return 0 if not failed else 1


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
