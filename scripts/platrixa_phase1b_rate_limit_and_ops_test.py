#!/usr/bin/env python3
"""Phase 1B — durable per-tenant rate limiting + owner operational visibility.

Disposable embedded PostgreSQL, real HTTP routes (FastAPI TestClient).

Proves:

  * the tenant request-rate counter is DURABLE — it survives a fresh app
    instance and a cleared process cache, which the per-IP in-memory limiter
    in ``api/rate_limit.py`` cannot;
  * one tenant being throttled does not throttle another tenant;
  * a throttled request is refused with 429 + Retry-After and creates no job;
  * the owner status route is reachable ONLY with the management credential,
    never with a developer API key;
  * the owner route reports queue depth, reconciliation, lease-derived worker
    activity and admission rejections, and leaks no tenant id or key material.

Run:
    python3 scripts/platrixa_phase1b_rate_limit_and_ops_test.py
"""

from __future__ import annotations

import os
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
    "DATABASE_URL",
):
    os.environ.pop(_v, None)

from backend.auth import async_jobs, queue_capacity, tenant_rate_limit  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}
OWNER_TENANT = "owner-ops-tenant"
TENANT_A = "rate-tenant-a"
TENANT_B = "rate-tenant-b"
MGMT_TOKEN = "test-management-token-not-a-real-credential"


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def pg() -> str:
    if _PG.get("uri"):
        return _PG["uri"]
    import shutil

    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_phase1b_rate_ops_pgdata")
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
    # Deliberately does NOT apply platrixa_visitor_schema.sql: the store's own
    # startup path must create those columns (the fresh-database defect).
    async_jobs._schema_ensured.discard(uri)
    async_jobs._session_factory()
    return uri


def _engine():
    from sqlalchemy import create_engine

    return create_engine(
        pg().replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )


def _set_limits(global_limit: int, per_tenant: int) -> None:
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = str(global_limit)
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = str(per_tenant)
    queue_capacity.reset_cache()


def _set_rate(limit: int, window: int = 60) -> None:
    os.environ[tenant_rate_limit.TENANT_RATE_LIMIT_ENV_VAR] = str(limit)
    os.environ[tenant_rate_limit.TENANT_RATE_WINDOW_ENV_VAR] = str(window)
    tenant_rate_limit.reset_cache()


def _job_count() -> int:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        n = conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
    engine.dispose()
    return int(n)


def _reset_jobs() -> None:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_async_jobs"))
    engine.dispose()


def main() -> int:
    pg()

    os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"] = MGMT_TOKEN
    os.environ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID"] = OWNER_TENANT

    from fastapi.testclient import TestClient

    from api.main import create_app
    from backend.auth import api_keys as store

    app = create_app()

    # ---- provisioning through the REAL owner route ------------------------
    client = TestClient(app)
    mgmt = {"x-platrixa-management-token": MGMT_TOKEN}
    keys: dict[str, str] = {}
    for tenant in (TENANT_A, TENANT_B):
        r = client.post(
            "/v1/developer/tenants",
            headers=mgmt,
            json={
                "tenant_id": tenant,
                "developer_name": f"{tenant} developer",
                "environment": "test",
                "monthly_limit": 100,
            },
        )
        check(f"provision {tenant} via owner route", r.status_code == 201, f"http={r.status_code} {r.text[:160]}")
        if r.status_code == 201:
            keys[tenant] = r.json()["key"]
    if len(keys) != 2:
        return 1

    # ---- fresh-database job plane: no manual visitor DDL ------------------
    _set_limits(100, 10)
    _set_rate(3)

    def submit(tenant: str, suffix: str):
        return client.post(
            "/v1/inference-jobs",
            headers={
                "x-platrixa-api-key": keys[tenant],
                "Idempotency-Key": f"rl-{tenant}-{suffix}-{int(time.time() * 1000)}",
            },
            json={"raw_input": "revenue was 100"},
        )

    first = submit(TENANT_A, "1")
    check(
        "job creation works on a store where only the startup path created visitor columns",
        first.status_code == 202,
        f"http={first.status_code} {first.text[:200]}",
    )

    # ---- per-tenant rate limiting ----------------------------------------
    second = submit(TENANT_A, "2")
    third = submit(TENANT_A, "3")
    fourth = submit(TENANT_A, "4")
    check(
        "requests within the tenant limit are admitted",
        second.status_code == 202 and third.status_code == 202,
        f"http={second.status_code},{third.status_code}",
    )
    check(
        "the request over the tenant limit is refused with 429",
        fourth.status_code == 429,
        f"http={fourth.status_code} {fourth.text[:160]}",
    )
    check(
        "the refusal carries Retry-After and a tenant-safe code",
        fourth.headers.get("Retry-After") is not None
        and fourth.json().get("error", {}).get("code") == tenant_rate_limit.TENANT_RATE_LIMITED,
        f"retry={fourth.headers.get('Retry-After')} body={fourth.text[:160]}",
    )
    check(
        "a throttled request created no job",
        _job_count() == 3,
        f"jobs={_job_count()}",
    )

    other = submit(TENANT_B, "1")
    check(
        "another tenant is unaffected by A's throttling",
        other.status_code == 202,
        f"http={other.status_code} {other.text[:160]}",
    )

    # ---- durability: a completely fresh app + cleared cache --------------
    tenant_rate_limit.reset_cache()
    os.environ[tenant_rate_limit.TENANT_RATE_LIMIT_ENV_VAR] = "3"
    tenant_rate_limit.limits()
    app2 = create_app()
    client2 = TestClient(app2)
    after_restart = client2.post(
        "/v1/inference-jobs",
        headers={
            "x-platrixa-api-key": keys[TENANT_A],
            "Idempotency-Key": f"rl-restart-{int(time.time() * 1000)}",
        },
        json={"raw_input": "revenue was 100"},
    )
    check(
        "the tenant counter survives a fresh app instance (durable, not in-memory)",
        after_restart.status_code == 429,
        f"http={after_restart.status_code} {after_restart.text[:160]}",
    )

    # ---- owner operational visibility ------------------------------------
    ops = client.get("/v1/owner/queue/status", headers=mgmt)
    check("owner status is reachable with the management credential", ops.status_code == 200, f"http={ops.status_code}")
    body = ops.json() if ops.status_code == 200 else {}
    check(
        "owner status reports queue bounds and pending workload",
        body.get("queue", {}).get("global_limit") == 100
        and body.get("queue", {}).get("per_tenant_limit") == 10
        and body.get("queue", {}).get("pending_total") == 4,
        f"queue={body.get('queue')}",
    )
    check(
        "owner status reports reconciliation state",
        body.get("reconciliation", {}).get("consistent") is True,
        f"reconciliation={body.get('reconciliation')}",
    )
    check(
        "owner status reports lease-derived worker activity (none yet)",
        body.get("workers") == [],
        f"workers={body.get('workers')}",
    )
    check(
        "owner status reports the effective rate limit",
        body.get("rate_limit", {}).get("tenant_limit") == 3
        and body.get("rate_limit", {}).get("enabled") is True,
        f"rate_limit={body.get('rate_limit')}",
    )

    api_key_only = client.get("/v1/owner/queue/status", headers={"x-platrixa-api-key": keys[TENANT_A]})
    check(
        "a developer API key cannot reach owner operations (403)",
        api_key_only.status_code == 403,
        f"http={api_key_only.status_code}",
    )
    anonymous = client.get("/v1/owner/queue/status")
    check(
        "owner operations are closed without a management credential (403)",
        anonymous.status_code == 403,
        f"http={anonymous.status_code}",
    )

    text_body = ops.text if ops.status_code == 200 else ""
    check(
        "owner response leaks no tenant id, key or key prefix",
        TENANT_A not in text_body and TENANT_B not in text_body and keys[TENANT_A] not in text_body,
        "tenant id or raw key present in the owner response",
    )

    # ---- admission rejections are counted and visible --------------------
    _set_limits(4, 4)
    queue_capacity.reset_rejections()
    refusals = [submit(TENANT_B, f"sat-{i}") for i in range(2)]
    check(
        "queue saturation still refuses with 503",
        any(r.status_code == 503 for r in refusals),
        f"codes={[r.status_code for r in refusals]}",
    )
    ops2 = client.get("/v1/owner/queue/status", headers=mgmt)
    rejections = ops2.json().get("admission", {}).get("rejections", {}) if ops2.status_code == 200 else {}
    check(
        "the owner status surfaces admission rejections by code",
        sum(int(v) for v in rejections.values()) >= 1,
        f"rejections={rejections}",
    )

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
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
