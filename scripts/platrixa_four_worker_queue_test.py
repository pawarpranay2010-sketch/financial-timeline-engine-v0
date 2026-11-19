#!/usr/bin/env python3
"""Four-worker queue coordination — offline acceptance (Test A, simulated).

WHAT THIS PROVES (and what it does NOT)
---------------------------------------
This suite runs FOUR concurrent worker clients against the real HTTP claim
endpoint and the real durable queue, using a disposable embedded PostgreSQL.
It proves the SERVER-side properties four Android Termux workers depend on:

  * four independent worker identities authenticate separately;
  * every job is claimed by exactly ONE worker (no duplicate claims);
  * a busy worker never causes a job to be lost — the queue drains;
  * tenant isolation holds under concurrent claims;
  * a lease can be recovered after a worker interruption, with the fencing
    generation advanced;
  * a stale worker cannot commit (its old owner/generation is refused);
  * one accepted result per job (no duplicate accepted result);
  * an in-flight job held by a failed worker does not block other workers;
  * a released claim returns to QUEUED and refunds the attempt;
  * queue fairness is MEASURED (per-worker distribution is reported).

It does NOT run the model, llama.cpp, Termux or a physical phone, and it makes
no claim about four-way inference concurrency. In the deployed architecture
the four worker PROCESSES share ONE local model server; if that server
serialises generation, four workers provide pipelining, not four-way
parallelism. Real-device measurements are a separate, pending acceptance step
(see docs/ANDROID_PRIVATE_BETA_DEVICE_TEST.md).

Run:
    python3 scripts/platrixa_four_worker_queue_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
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
    "DATABASE_URL",
):
    os.environ.pop(_v, None)

from backend.auth import (  # noqa: E402
    async_jobs,
    queue_capacity,
    tenant_rate_limit,
    transaction_quota,
)
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}

OWNER_TENANT = "four-worker-owner"
TENANT_W = "four-worker-tenant"
TENANT_OTHER = "four-worker-other"
MGMT_TOKEN = "test-management-token-not-a-real-credential"

RAW_INPUT = "purchased furniture from raj for rs.25000"
_KEYS: dict[str, str] = {}
_COUNTER = {"n": 0}
WORKERS = ("android-worker-1", "android-worker-2", "android-worker-3", "android-worker-4")


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def pg() -> str:
    if _PG.get("uri"):
        return _PG["uri"]
    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_four_worker_pgdata")
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


def _scalar(sql: str, params: dict | None = None):
    from sqlalchemy import create_engine, text

    engine = create_engine(
        pg().replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            # UPDATE statements return no rows; report the affected count.
            return result.scalar() if result.returns_rows else result.rowcount
    finally:
        engine.dispose()


def idem() -> str:
    _COUNTER["n"] += 1
    return f"four-worker-{_COUNTER['n']}-{int(time.time() * 1000)}"


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
    os.environ[transaction_quota.TX_MONTHLY_LIMIT_ENV_VAR] = "500"
    transaction_quota.reset_cache()

    from fastapi.testclient import TestClient

    from api.main import create_app

    client = TestClient(create_app())
    mgmt = {"x-platrixa-management-token": MGMT_TOKEN}

    for tenant in (TENANT_W, TENANT_OTHER):
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
    if len(_KEYS) != 2:
        return 1

    def headers(tenant: str) -> dict:
        return {"x-platrixa-api-key": _KEYS[tenant]}

    def submit(tenant: str, count: int) -> list[str]:
        ids: list[str] = []
        for _ in range(count):
            r = client.post(
                "/v1/inference-jobs",
                headers={**headers(tenant), "Idempotency-Key": idem()},
                json={"raw_input": RAW_INPUT},
            )
            if r.status_code == 202:
                ids.append(r.json()["job_id"])
        return ids

    def claim(tenant: str, worker_id: str | None = None) -> dict:
        h = headers(tenant)
        if worker_id:
            h["x-platrixa-worker-id"] = worker_id
        r = client.post("/v1/worker/jobs/claim", headers=h)
        if r.status_code != 200:
            return {"__http__": r.status_code}
        return r.json()

    # =====================================================================
    # 1. FOUR INDEPENDENT WORKERS DRAINING ONE TENANT'S QUEUE
    # =====================================================================
    submitted = submit(TENANT_W, 12)
    other_jobs = submit(TENANT_OTHER, 2)
    check("12 inference jobs are queued for the worker tenant", len(submitted) == 12, f"n={len(submitted)}")
    check("a second tenant has its own queued jobs", len(other_jobs) == 2, f"n={len(other_jobs)}")

    barrier = threading.Barrier(len(WORKERS))
    claims: list[dict] = []
    claims_lock = threading.Lock()
    errors: list[str] = []

    def worker_loop(worker_id: str) -> None:
        local: list[dict] = []
        try:
            barrier.wait(timeout=20)
            for _ in range(len(submitted) + 5):
                body = claim(TENANT_W, worker_id)
                job = body.get("job")
                if not job:
                    break
                local.append(
                    {
                        "worker": worker_id,
                        "job_id": job.get("job_id"),
                        "owner": (job.get("lease") or {}).get("owner"),
                        "generation": (job.get("lease") or {}).get("generation"),
                    }
                )
                time.sleep(0.01)  # simulate a device doing work between claims
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(f"{worker_id}: {type(exc).__name__}: {exc}")
        with claims_lock:
            claims.extend(local)

    threads = [threading.Thread(target=worker_loop, args=(w,)) for w in WORKERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    claimed_ids = [c["job_id"] for c in claims]
    distribution = {w: sum(1 for c in claims if c["worker"] == w) for w in WORKERS}
    print(f"  [INFO] claim distribution: {distribution}")

    check("all four worker clients authenticated and ran", not errors, "; ".join(errors[:2]))
    check(
        "every queued job was claimed exactly once across four workers",
        sorted(claimed_ids) == sorted(submitted) and len(claimed_ids) == len(set(claimed_ids)),
        f"claimed={len(claimed_ids)} unique={len(set(claimed_ids))} expected={len(submitted)}",
    )
    check(
        "each claim carries the identity of the worker that made it",
        all(c["owner"] == c["worker"] for c in claims),
        "a claim reported a foreign lease owner",
    )
    check(
        "no worker was starved (fairness measured, not assumed)",
        all(distribution[w] > 0 for w in WORKERS),
        json.dumps(distribution),
    )
    check(
        "tenant isolation holds under concurrent claims",
        not (set(claimed_ids) & set(other_jobs)),
        "a different tenant's job was claimed",
    )
    drained = claim(TENANT_W)
    check(
        "a drained queue answers an explicit no-work response (nothing lost)",
        drained.get("job") is None,
        json.dumps(drained)[:200],
    )

    # =====================================================================
    # 2. INTERRUPTION RECOVERY AND FENCING
    # =====================================================================
    first = claim(TENANT_OTHER, WORKERS[0])
    job = first.get("job") or {}
    other_job = job.get("job_id")
    lease = job.get("lease") or {}
    check(
        "a worker can authenticate and claim the second tenant's job",
        bool(other_job) and lease.get("owner") == WORKERS[0],
        json.dumps(first)[:200],
    )

    # Simulate the worker's process dying: its lease expires.
    _scalar(
        "UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 second' "
        "WHERE job_id = :j",
        {"j": other_job},
    )
    reclaimed = claim(TENANT_OTHER, WORKERS[1]) if other_job else {}
    reclaimed_job = reclaimed.get("job") or {}
    new_lease = reclaimed_job.get("lease") or {}
    check(
        "an expired lease is reclaimed by another worker with a higher generation",
        reclaimed_job.get("job_id") == other_job
        and int(new_lease.get("generation") or 0) > int(lease.get("generation") or 0),
        f"old_gen={lease.get('generation')} new_gen={new_lease.get('generation')}",
    )

    stale_commit = async_jobs.complete_job(
        other_job,
        envelope={"status": "VERIFIED", "api_status": "VERIFIED"},
        http_status=200,
        request_id=None,
        lease_owner=str(lease.get("owner")),
        lease_generation=int(lease.get("generation") or 0),
    ) if other_job else True
    check(
        "a stale worker cannot commit a result after losing its lease",
        stale_commit is False,
        f"accepted={stale_commit}",
    )

    valid_commit = async_jobs.complete_job(
        other_job,
        envelope={"status": "VERIFIED", "api_status": "VERIFIED"},
        http_status=200,
        request_id=None,
        lease_owner=str(new_lease.get("owner")),
        lease_generation=int(new_lease.get("generation") or 0),
    ) if other_job else False
    duplicate_commit = async_jobs.complete_job(
        other_job,
        envelope={"status": "VERIFIED", "api_status": "VERIFIED"},
        http_status=200,
        request_id=None,
        lease_owner=str(new_lease.get("owner")),
        lease_generation=int(new_lease.get("generation") or 0),
    ) if other_job else False
    check(
        "exactly one result commit is accepted per job (no duplicate result)",
        valid_commit is True and duplicate_commit is False,
        f"first={valid_commit} second={duplicate_commit}",
    )

    # =====================================================================
    # 3. A FAILED WORKER DOES NOT BLOCK THE OTHERS
    # =====================================================================
    # Drain anything still claimable from the recovery section, so the next
    # assertion starts from a known queue rather than leftover jobs.
    while True:
        leftover = claim(TENANT_OTHER, WORKERS[3])
        leftover_job = leftover.get("job") or {}
        if not leftover_job.get("job_id"):
            break
        leftover_lease = leftover_job.get("lease") or {}
        async_jobs.complete_job(
            leftover_job["job_id"],
            envelope={"status": "VERIFIED", "api_status": "VERIFIED"},
            http_status=200,
            request_id=None,
            lease_owner=str(leftover_lease.get("owner")),
            lease_generation=int(leftover_lease.get("generation") or 0),
        )

    more = submit(TENANT_OTHER, 4)
    held = claim(TENANT_OTHER, WORKERS[2])
    held_job = (held.get("job") or {}).get("job_id")
    claimed_by_others: list[dict] = []
    for worker_id in (WORKERS[0], WORKERS[1], WORKERS[3]):
        body = claim(TENANT_OTHER, worker_id)
        j = body.get("job") or {}
        if j.get("job_id"):
            claimed_by_others.append(
                {"job_id": j["job_id"], "lease": j.get("lease") or {}}
            )
    other_claimed_ids = [c["job_id"] for c in claimed_by_others]
    check(
        "a job held by a hung worker does not block the remaining queue",
        bool(held_job)
        and set(other_claimed_ids) == set(more) - {held_job}
        and len(other_claimed_ids) == len(set(other_claimed_ids)),
        f"held={held_job} claimed={other_claimed_ids} submitted={more}",
    )

    # =====================================================================
    # 4. RELEASE RETURNS THE CLAIM (BUSY DEVICE SLOT, NO LOST WORK)
    # =====================================================================
    if claimed_by_others:
        target = claimed_by_others[0]
        release_target = target["job_id"]
        release_lease = target["lease"]
        released = client.post(
            f"/v1/worker/jobs/{release_target}/release",
            headers=headers(TENANT_OTHER),
            json={
                "lease": {
                    "owner": release_lease.get("owner"),
                    "generation": release_lease.get("generation"),
                },
                "reason": "SLOT_BUSY",
            },
        )
        released_body = released.json() if released.status_code == 200 else {}
        attempts = int(
            _scalar(
                "SELECT attempt_count FROM platrixa_async_jobs WHERE job_id = :j",
                {"j": release_target},
            )
            or 0
        )
        check(
            "a busy device releases its claim back to QUEUED and refunds the attempt",
            released.status_code == 200
            and released_body.get("released") is True
            and released_body.get("status") == "QUEUED"
            and attempts == 0,
            f"http={released.status_code} attempts={attempts} {released.text[:200]}",
        )
        reclaimed_after_release = claim(TENANT_OTHER, WORKERS[3])
        check(
            "a released job is claimable again by another worker",
            (reclaimed_after_release.get("job") or {}).get("job_id") == release_target,
            json.dumps(reclaimed_after_release)[:200],
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
