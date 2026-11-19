#!/usr/bin/env python3
"""Private-beta developer workflow — job history, usage accuracy, isolation.

Disposable embedded PostgreSQL, real HTTP routes (FastAPI TestClient), real
provisioned tenant credentials and the real job store:

  * GET /v1/jobs: tenant scoping, isolation (including attempted bypass via
    query parameters), keyset pagination across pages, stable ordering,
    page-size bounds, status filter, empty results, malformed cursors,
    authentication failures, and that no job payload is returned;
  * the existing job-status and result endpoints keep working;
  * DEVELOPER EXPERIENCE (workstream 5C, offline part): submit a batch with a
    tenant key, receive a job id, poll it, find it via GET /v1/jobs, attempt
    the result before completion, inspect usage, retry with the same
    idempotency key and prove there is no duplicate job or duplicate charge;
  * usage accounting: a 5-transaction batch is ONE job unit and FIVE
    transaction units — never five jobs, never one transaction; idempotent
    replays charge nothing; developer and owner reports label unit and window.

Run:
    python3 scripts/platrixa_private_beta_jobs_usage_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    queue_capacity,
    tenant_rate_limit,
    transaction_quota,
)
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}

OWNER_TENANT = "jobs-owner-tenant"
TENANT_A = "jobs-tenant-a"
TENANT_B = "jobs-tenant-b"
TENANT_EMPTY = "jobs-tenant-empty"
MGMT_TOKEN = "test-management-token-not-a-real-credential"

RAW_INPUT = "purchased furniture from raj for rs.25000"
_KEYS: dict[str, str] = {}
_COUNTER = {"n": 0}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def pg() -> str:
    if _PG.get("uri"):
        return _PG["uri"]
    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_jobs_usage_pgdata")
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

    root = Path(__file__).resolve().parent.parent
    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        conn.execute(
            text((root / "backend/database/platrixa_tenant_quota_schema.sql").read_text("utf-8"))
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


def idem() -> str:
    _COUNTER["n"] += 1
    return f"jobs-test-{_COUNTER['n']}-{int(time.time() * 1000)}"


def main() -> int:
    pg()

    os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"] = MGMT_TOKEN
    os.environ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID"] = OWNER_TENANT
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = "500"
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = "200"
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

    app = create_app()
    client = TestClient(app)
    mgmt = {"x-platrixa-management-token": MGMT_TOKEN}

    # ---- schema: the cursor index must exist on a fresh store --------------
    idx = _scalar(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_async_jobs_tenant_cursor'"
    )
    check("GET /v1/jobs keyset index is created by the startup path", int(idx) == 1, f"idx={idx}")

    # ---- provisioning through the real owner route ------------------------
    for tenant in (TENANT_A, TENANT_B, TENANT_EMPTY):
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
        check(f"provision {tenant}", r.status_code == 201, f"http={r.status_code} {r.text[:160]}")
        if r.status_code == 201:
            _KEYS[tenant] = r.json()["key"]
    if len(_KEYS) != 3:
        return 1

    def headers(tenant: str, key: str | None = None) -> dict:
        return {"x-platrixa-api-key": key if key is not None else _KEYS[tenant]}

    def submit(tenant: str, body: dict, key_header: str | None = None):
        h = headers(tenant)
        if key_header is not None:
            h = {"x-platrixa-api-key": key_header}
        h["Idempotency-Key"] = idem()
        return client.post("/v1/inference-jobs", headers=h, json=body)

    def batch_body(count: int) -> dict:
        return {
            "mode": "independent",
            "transactions": [
                {"transaction_id": f"tx-{i:03d}", "input": RAW_INPUT} for i in range(count)
            ],
        }

    a_jobs: list[str] = []
    b_jobs: list[str] = []

    # =====================================================================
    # 1. AUTHENTICATION
    # =====================================================================
    anon = client.get("/v1/jobs")
    check(
        "GET /v1/jobs without a key is 401",
        anon.status_code == 401,
        f"http={anon.status_code} {anon.text[:160]}",
    )
    bad = client.get("/v1/jobs", headers={"x-platrixa-api-key": "not-a-real-key"})
    check(
        "GET /v1/jobs with an unknown key is 401",
        bad.status_code == 401,
        f"http={bad.status_code} {bad.text[:160]}",
    )

    # =====================================================================
    # 2. EMPTY RESULT IS A VALID RESPONSE
    # =====================================================================
    empty = client.get("/v1/jobs", headers=headers(TENANT_EMPTY))
    empty_body = empty.json() if empty.status_code == 200 else {}
    check(
        "a tenant with no jobs gets an empty, valid page",
        empty.status_code == 200
        and empty_body.get("jobs") == []
        and empty_body.get("has_more") is False
        and empty_body.get("next_cursor") is None,
        f"http={empty.status_code} {empty.text[:200]}",
    )

    # =====================================================================
    # 3. SUBMIT REAL WORK (singles + one 5-transaction batch) --------------
    # =====================================================================
    for _ in range(3):
        r = submit(TENANT_A, {"raw_input": RAW_INPUT})
        if r.status_code == 202:
            a_jobs.append(r.json()["job_id"])

    batch = submit(TENANT_A, batch_body(5))
    batch_body_json = batch.json() if batch.status_code == 202 else {}
    batch_job = batch_body_json.get("job_id", "")
    check(
        "a 5-transaction batch is accepted as ONE job (202)",
        batch.status_code == 202 and bool(batch_job),
        f"http={batch.status_code} {batch.text[:200]}",
    )
    if batch_job:
        a_jobs.append(batch_job)

    # More jobs, so pagination has several pages to traverse.
    for _ in range(22):
        r = submit(TENANT_A, {"raw_input": RAW_INPUT})
        if r.status_code == 202:
            a_jobs.append(r.json()["job_id"])

    for _ in range(2):
        r = submit(TENANT_B, {"raw_input": RAW_INPUT})
        if r.status_code == 202:
            b_jobs.append(r.json()["job_id"])

    check("all tenant A submissions were accepted", len(a_jobs) == 26, f"jobs={len(a_jobs)}")
    check("all tenant B submissions were accepted", len(b_jobs) == 2, f"jobs={len(b_jobs)}")

    # =====================================================================
    # 4. LIST OWN JOBS / TENANT ISOLATION / BYPASS ATTEMPTS
    # =====================================================================
    listing = client.get("/v1/jobs?limit=100", headers=headers(TENANT_A))
    listing_body = listing.json() if listing.status_code == 200 else {}
    listed_ids = [j["job_id"] for j in listing_body.get("jobs", [])]
    check(
        "tenant A lists its own jobs",
        listing.status_code == 200 and set(listed_ids) == set(a_jobs),
        f"http={listing.status_code} got={len(listed_ids)} want={len(a_jobs)}",
    )
    check(
        "tenant A's page never contains another tenant's job",
        not (set(listed_ids) & set(b_jobs)),
        "leaked a tenant B job",
    )
    check(
        "no job payload or result envelope is returned by the list",
        all(
            key not in listing_body.get("jobs", [{}])[0]
            for key in ("request_json", "result_json", "raw_input", "accounting")
        )
        if listing_body.get("jobs")
        else False,
        json.dumps(listing_body.get("jobs", [{}])[0])[:200],
    )

    b_listing = client.get("/v1/jobs?limit=100", headers=headers(TENANT_B))
    b_listed = [j["job_id"] for j in (b_listing.json().get("jobs") if b_listing.status_code == 200 else [])]
    check(
        "tenant B lists only its own jobs",
        b_listing.status_code == 200 and set(b_listed) == set(b_jobs),
        f"http={b_listing.status_code} got={b_listed}",
    )

    bypass = client.get(
        f"/v1/jobs?limit=100&tenant_id={TENANT_B}&tenant={TENANT_B}&visitor_id=00000000-0000-0000-0000-000000000000",
        headers=headers(TENANT_A),
    )
    bypass_body = bypass.json() if bypass.status_code == 200 else {}
    bypass_ids = [j["job_id"] for j in bypass_body.get("jobs", [])]
    check(
        "query parameters cannot override the authenticated tenant",
        bypass.status_code == 200
        and set(bypass_ids) == set(a_jobs)
        and not (set(bypass_ids) & set(b_jobs)),
        f"http={bypass.status_code} got={len(bypass_ids)}",
    )

    # =====================================================================
    # 5. CURSOR PAGINATION: EVERY JOB EXACTLY ONCE, IN A STABLE ORDER
    # =====================================================================
    seen: list[str] = []
    cursor = None
    pages = 0
    page_sizes: list[int] = []
    while True:
        query = "/v1/jobs?limit=7" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(query, headers=headers(TENANT_A))
        if page.status_code != 200:
            check("pagination page request succeeds", False, page.text[:200])
            break
        body = page.json()
        ids = [j["job_id"] for j in body["jobs"]]
        seen.extend(ids)
        page_sizes.append(len(ids))
        pages += 1
        cursor = body.get("next_cursor")
        if not cursor:
            break
        if pages > 20:  # runaway guard
            break

    check(
        "pagination traverses every job exactly once",
        sorted(seen) == sorted(a_jobs) and len(seen) == len(set(seen)),
        f"pages={pages} seen={len(seen)} unique={len(set(seen))} expected={len(a_jobs)}",
    )
    check(
        "page sizes respect the requested bound and the final page is short",
        all(size <= 7 for size in page_sizes) and page_sizes[-1] <= 7 and sum(page_sizes) == len(a_jobs),
        f"sizes={page_sizes}",
    )

    db_order = [
        str(row["job_id"])
        for row in _rows(
            "SELECT job_id FROM platrixa_async_jobs WHERE tenant_id = :t "
            "ORDER BY created_at DESC, job_id DESC",
            {"t": TENANT_A},
        )
    ]
    check(
        "the listed order is the documented stable order (created_at DESC, job_id DESC)",
        seen == db_order,
        "pagination order differs from the deterministic DB order",
    )

    # =====================================================================
    # 6. PAGE-SIZE AND FILTER VALIDATION
    # =====================================================================
    too_big = client.get("/v1/jobs?limit=101", headers=headers(TENANT_A))
    check(
        "limit above the hard maximum is rejected",
        too_big.status_code == 400 and too_big.json()["error"]["code"] == "INPUT_INVALID",
        f"http={too_big.status_code} {too_big.text[:160]}",
    )
    zero = client.get("/v1/jobs?limit=0", headers=headers(TENANT_A))
    check(
        "limit below 1 is rejected",
        zero.status_code == 400 and zero.json()["error"]["code"] == "INPUT_INVALID",
        f"http={zero.status_code} {zero.text[:160]}",
    )
    default_page = client.get("/v1/jobs", headers=headers(TENANT_A))
    check(
        "the default page size is bounded (20)",
        default_page.status_code == 200
        and default_page.json()["limit"] == 20
        and len(default_page.json()["jobs"]) == 20
        and default_page.json()["has_more"] is True,
        f"http={default_page.status_code} {default_page.text[:120]}",
    )

    queued = client.get("/v1/jobs?limit=100&status=QUEUED", headers=headers(TENANT_A))
    check(
        "status filtering returns only matching jobs",
        queued.status_code == 200
        and len(queued.json()["jobs"]) == len(a_jobs)
        and all(j["status"] == "QUEUED" for j in queued.json()["jobs"]),
        f"http={queued.status_code} {queued.text[:160]}",
    )
    completed = client.get("/v1/jobs?limit=100&status=COMPLETED", headers=headers(TENANT_A))
    check(
        "status filtering with no matches returns an empty page",
        completed.status_code == 200 and completed.json()["jobs"] == [],
        f"http={completed.status_code} {completed.text[:160]}",
    )
    bad_status = client.get("/v1/jobs?status=DONE", headers=headers(TENANT_A))
    check(
        "an unknown status filter is rejected explicitly",
        bad_status.status_code == 400
        and bad_status.json()["error"]["code"] == "INVALID_STATUS_FILTER",
        f"http={bad_status.status_code} {bad_status.text[:160]}",
    )

    # =====================================================================
    # 7. MALFORMED CURSORS FAIL LOUDLY (never silently restart at page 1)
    # =====================================================================
    for bad_cursor in ("not-a-cursor", "e30", "eyJ2IjoyfQ"):
        r = client.get(f"/v1/jobs?cursor={bad_cursor}", headers=headers(TENANT_A))
        check(
            f"malformed cursor {bad_cursor!r} is rejected with INVALID_CURSOR",
            r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CURSOR",
            f"http={r.status_code} {r.text[:160]}",
        )

    # =====================================================================
    # 8. BATCH JOB METADATA AND EXISTING ENDPOINTS UNCHANGED
    # =====================================================================
    if batch_job:
        entry = next((j for j in listing_body.get("jobs", []) if j["job_id"] == batch_job), None)
        check(
            "a batch appears as ONE job carrying its transaction count",
            entry is not None
            and entry["transaction_count"] == 5
            and entry["batch"] == {"mode": "independent", "transaction_count": 5},
            json.dumps(entry)[:240] if entry else "batch job missing from listing",
        )
        status = client.get(f"/v1/jobs/{batch_job}", headers=headers(TENANT_A))
        check(
            "GET /v1/jobs/{job_id} still returns the batch status block",
            status.status_code == 200
            and status.json().get("batch", {}).get("total_transactions") == 5,
            f"http={status.status_code} {status.text[:200]}",
        )
        result_id = _scalar(
            "SELECT result_id FROM platrixa_async_jobs WHERE job_id = :j", {"j": batch_job}
        )
        not_ready = client.get(f"/v1/results/{result_id}", headers=headers(TENANT_A))
        check(
            "GET /v1/results/{id} still reports RESULT_NOT_READY for a queued job",
            not_ready.status_code == 404
            and not_ready.json()["error"]["code"] == "RESULT_NOT_READY",
            f"http={not_ready.status_code} {not_ready.text[:200]}",
        )

    if b_jobs:
        foreign = client.get(f"/v1/jobs/{b_jobs[0]}", headers=headers(TENANT_A))
        check(
            "a foreign job id is indistinguishable from a missing one (404)",
            foreign.status_code == 404,
            f"http={foreign.status_code} {foreign.text[:160]}",
        )

    # =====================================================================
    # 9. USAGE ACCURACY — JOBS AND TRANSACTIONS ARE DIFFERENT UNITS --------
    # =====================================================================
    usage = client.get("/v1/usage", headers=headers(TENANT_A))
    usage_body = usage.json() if usage.status_code == 200 else {}
    db_quota_used = int(
        _scalar(
            "SELECT current_month_usage FROM platrixa_tenant_quotas WHERE tenant_id = :t",
            {"t": TENANT_A},
        )
        or 0
    )
    tenant_usage = usage_body.get("tenant_usage") or {}
    jobs_block = tenant_usage.get("jobs") or {}
    tx_block = tenant_usage.get("transactions") or {}

    check(
        "GET /v1/usage reports the per-credential quota units unchanged",
        usage.status_code == 200
        and usage_body.get("used") == db_quota_used
        and usage_body.get("key_quota", {}).get("used") == db_quota_used,
        f"http={usage.status_code} body_used={usage_body.get('used')} db={db_quota_used}",
    )
    check(
        "the quota block states its unit and scope (no bare 'used' claim)",
        "job" in str(usage_body.get("key_quota", {}).get("unit", "")).lower()
        and usage_body.get("key_quota", {}).get("scope") == "this credential",
        json.dumps(usage_body.get("key_quota"))[:200],
    )
    check(
        "jobs submitted counts JOBS, not transactions",
        jobs_block.get("submitted") == len(a_jobs),
        f"submitted={jobs_block.get('submitted')} expected={len(a_jobs)}",
    )
    db_tx_used = int(
        _scalar(
            "SELECT used FROM platrixa_tenant_transaction_quotas WHERE tenant_id = :t",
            {"t": TENANT_A},
        )
        or 0
    )
    check(
        "a 5-transaction batch is 5 transaction units, not 5 jobs / 1 transaction",
        tx_block.get("admitted") == 30 and jobs_block.get("submitted") == 26,
        f"admitted={tx_block.get('admitted')} jobs={jobs_block.get('submitted')}",
    )
    check(
        "the reported transaction count equals the transaction quota meter",
        tx_block.get("admitted") == db_tx_used,
        f"reported={tx_block.get('admitted')} meter={db_tx_used}",
    )
    check(
        "per-transaction state rows are reported as batch-only, never extrapolated",
        tx_block.get("tracked_rows") == 5
        and tx_block.get("coverage", {}).get("admitted_transactions") == 30
        and tx_block.get("coverage", {}).get("admitted_without_a_per_transaction_row") == 25
        and tx_block.get("reconciliation", {}).get("consistent") is True,
        json.dumps(tx_block.get("coverage"))[:260],
    )
    check(
        "the two meters are demonstrably different numbers for the same tenant",
        db_quota_used == 26 and tx_block.get("admitted") == 30,
        f"job_units={db_quota_used} tx_units={tx_block.get('admitted')}",
    )
    check(
        "usage states its window and the per-key limitation",
        tenant_usage.get("window", {}).get("basis", "").startswith("UTC calendar month")
        and any("per credential" in note for note in usage_body.get("limitations", [])),
        json.dumps(usage_body.get("limitations"))[:200],
    )

    # /v1/developer/usage is bound to the MANAGEMENT tenant (the operator's
    # own tenant id), never to a caller-supplied one. The management tenant
    # has no credentials or jobs here, so this asserts the scope and the shape
    # (units + both meters present), not tenant A's numbers.
    tenant_usage_r = client.get("/v1/developer/usage", headers=mgmt)
    tu_body = tenant_usage_r.json() if tenant_usage_r.status_code == 200 else {}
    check(
        "tenant usage is bound to the management tenant, not a caller value",
        tenant_usage_r.status_code == 200
        and tu_body.get("tenant_id") == OWNER_TENANT
        and tu_body.get("quota", {}).get("jobs", {}).get("used") == 0
        and tu_body.get("tenant_usage", {}).get("jobs", {}).get("submitted") == 0,
        f"http={tenant_usage_r.status_code} {tenant_usage_r.text[:300]}",
    )
    check(
        "tenant usage exposes both quota meters with explicit units",
        "job" in str(tu_body.get("quota", {}).get("jobs", {}).get("unit", "")).lower()
        and "transaction"
        in str(tu_body.get("quota", {}).get("transactions", {}).get("unit", "")).lower()
        and tu_body.get("unit") == "admitted job units (one per admitted API job)",
        json.dumps(tu_body.get("quota"))[:300],
    )
    dev_on_mgmt = client.get("/v1/developer/usage", headers=headers(TENANT_A))
    check(
        "a developer key cannot read the management usage report",
        dev_on_mgmt.status_code == 403,
        f"http={dev_on_mgmt.status_code}",
    )

    dev_owner = client.get("/v1/owner/usage", headers=headers(TENANT_A))
    check(
        "a developer key cannot reach owner usage",
        dev_owner.status_code in (401, 403),
        f"http={dev_owner.status_code}",
    )
    owner = client.get("/v1/owner/usage", headers=mgmt)
    owner_body = owner.json() if owner.status_code == 200 else {}
    a_slot = next(
        (t for t in owner_body.get("tenants", []) if t.get("tenant_id") == TENANT_A), {}
    )
    check(
        "owner usage labels the window of every figure",
        owner.status_code == 200
        and a_slot.get("jobs", {}).get("window") == "all_time"
        and "month" in str(owner_body.get("reporting_windows", {}).get("quota", "")).lower(),
        f"http={owner.status_code} {owner.text[:300]}",
    )
    check(
        "owner usage keeps per-tenant job and transaction totals separate",
        a_slot.get("jobs", {}).get("total") == 26
        and a_slot.get("transactions", {}).get("admitted_by_jobs") == 30
        and a_slot.get("quota", {}).get("jobs", {}).get("used") == 26
        and a_slot.get("quota", {}).get("transactions", {}).get("used") == 30,
        json.dumps(a_slot)[:320],
    )
    # Before the fix, processing/retry/pending counters ignored `kind`, so a
    # document job could be counted as inference work. A document job submitted
    # into the SAME tenant must not change any inference counter.
    async_jobs.create_job(TENANT_A, {"raw_input": RAW_INPUT}, kind="document")
    owner_after = client.get("/v1/owner/usage", headers=mgmt)
    owner_after_body = owner_after.json() if owner_after.status_code == 200 else {}
    a_after = next(
        (t for t in owner_after_body.get("tenants", []) if t.get("tenant_id") == TENANT_A), {}
    )
    check(
        "owner job counters are inference-only (a document job is not merged in)",
        owner_after.status_code == 200
        and a_after.get("jobs", {}).get("total") == 26
        and a_after.get("jobs", {}).get("pending") == 26
        and a_after.get("transactions", {}).get("admitted_by_jobs") == 30,
        json.dumps(a_after.get("jobs"))[:260],
    )

    # =====================================================================
    # 10. DEVELOPER EXPERIENCE: IDEMPOTENT RETRY CHARGES NOTHING -----------
    # =====================================================================
    before_jobs = int(_scalar("SELECT count(*) FROM platrixa_async_jobs") or 0)
    before_used = int(
        _scalar(
            "SELECT current_month_usage FROM platrixa_tenant_quotas WHERE tenant_id = :t",
            {"t": TENANT_B},
        )
        or 0
    )
    key = idem()
    body = {"raw_input": RAW_INPUT}
    first = client.post(
        "/v1/inference-jobs", headers={**headers(TENANT_B), "Idempotency-Key": key}, json=body
    )
    replay = client.post(
        "/v1/inference-jobs", headers={**headers(TENANT_B), "Idempotency-Key": key}, json=body
    )
    after_jobs = int(_scalar("SELECT count(*) FROM platrixa_async_jobs") or 0)
    after_used = int(
        _scalar(
            "SELECT current_month_usage FROM platrixa_tenant_quotas WHERE tenant_id = :t",
            {"t": TENANT_B},
        )
        or 0
    )
    check(
        "an idempotent replay returns the original job",
        first.status_code == 202
        and replay.status_code == 202
        and replay.json().get("job_id") == first.json().get("job_id"),
        f"first={first.status_code} replay={replay.status_code}",
    )
    check(
        "an idempotent replay creates no second job",
        after_jobs == before_jobs + 1,
        f"before={before_jobs} after={after_jobs}",
    )
    check(
        "an idempotent replay charges no second job unit",
        after_used == before_used + 1,
        f"before={before_used} after={after_used}",
    )
    replay_job = first.json().get("job_id") if first.status_code == 202 else None
    if replay_job:
        found = client.get("/v1/jobs?limit=100", headers=headers(TENANT_B))
        check(
            "the replayed job is discoverable exactly once through GET /v1/jobs",
            found.status_code == 200
            and [j["job_id"] for j in found.json()["jobs"]].count(replay_job) == 1,
            f"http={found.status_code}",
        )

    # =====================================================================
    # SUMMARY
    # =====================================================================
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print(f"\n{passed}/{len(CHECKS)} checks passed" + (f", {failed} FAILED" if failed else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
