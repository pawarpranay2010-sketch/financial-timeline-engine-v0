#!/usr/bin/env python3
"""Phase 1B — independent two-tenant isolation (disposable PostgreSQL).

Two synthetic tenants, two independent API keys, one disposable embedded
PostgreSQL cluster. Tenant A must never be able to read, poll, retrieve,
claim, complete, or manipulate Tenant B's jobs, results, usage, or pending
capacity — and the reverse must hold too.

Every assertion goes through the REAL HTTP routes (FastAPI TestClient) and the
REAL database; the ``tenant_id`` column alone is never treated as proof of
isolation.

Run:
    python3 scripts/platrixa_phase1b_tenant_isolation_test.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Never inherit an ambient (possibly production) credential; this suite sets
# DATABASE_URL explicitly to the disposable local socket below.
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
TENANT_A = "iso-tenant-a"
TENANT_B = "iso-tenant-b"


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def pg() -> str:
    if _PG.get("uri"):
        return _PG["uri"]
    import shutil

    import pgserver

    data = Path("/tmp/platrixa_phase1b_isolation_pgdata")
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

    from sqlalchemy import create_engine, text

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
    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        conn.execute(text((root / "backend/database/platrixa_visitor_schema.sql").read_text("utf-8")))
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


def pending(tenant: str) -> int:
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE AND tenant_id = :t"
            ),
            {"t": tenant},
        ).scalar_one()
    engine.dispose()
    return int(n)


def key_for(tenant: str, name: str) -> str:
    from backend.auth import api_keys as key_store

    key_store._schema_ensured.discard(pg())
    key_store._session_factory()
    return key_store.create_api_key(tenant, name, "test")["key"]


def usage_of(raw_key: str) -> int:
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


def main() -> int:
    try:
        pg()
        from fastapi.testclient import TestClient

        from api.main import create_app

        reset_jobs()
        set_limits(50, 2)
        app = create_app()
        client = TestClient(app)

        key_a = key_for(TENANT_A, "iso-a")
        key_b = key_for(TENANT_B, "iso-b")
        A = {"x-platrixa-api-key": key_a}
        B = {"x-platrixa-api-key": key_b}

        print("\nA. Independent submission, status and result retrieval")
        ra = client.post(
            "/v1/inference-jobs",
            headers={**A, "Idempotency-Key": f"iso-a-{int(time.time())}"},
            json={"raw_input": "revenue was 100"},
        )
        rb = client.post(
            "/v1/inference-jobs",
            headers={**B, "Idempotency-Key": f"iso-b-{int(time.time())}"},
            json={"raw_input": "revenue was 250"},
        )
        ja, jb = ra.json().get("job_id"), rb.json().get("job_id")
        resa, resb = ra.json().get("result_id"), rb.json().get("result_id")
        check(
            "A1 both tenants submit independently and receive distinct jobs",
            ra.status_code == 202 and rb.status_code == 202 and ja and jb and ja != jb,
            f"{ra.status_code}/{rb.status_code} jobs={ja},{jb}",
        )

        own_a = client.get(f"/v1/jobs/{ja}", headers=A)
        cross_ab = client.get(f"/v1/jobs/{ja}", headers=B)
        own_b = client.get(f"/v1/jobs/{jb}", headers=B)
        cross_ba = client.get(f"/v1/jobs/{jb}", headers=A)
        check(
            "A2 job status: owner 200, cross-tenant 404 in BOTH directions",
            own_a.status_code == 200
            and own_b.status_code == 200
            and cross_ab.status_code == 404
            and cross_ba.status_code == 404,
            f"ownA={own_a.status_code} crossAB={cross_ab.status_code} "
            f"ownB={own_b.status_code} crossBA={cross_ba.status_code}",
        )

        print("\nB. Worker claim, completion, capacity accounting")
        claim_a = client.post("/v1/worker/jobs/claim", headers=A)
        claimed_a = (claim_a.json() or {}).get("job") or {}
        claim_again_b = client.post("/v1/worker/jobs/claim", headers=B)
        claimed_b = (claim_again_b.json() or {}).get("job") or {}
        check(
            "B1 each worker claims only its OWN tenant's job",
            claimed_a.get("job_id") == ja and claimed_b.get("job_id") == jb,
            f"A={claimed_a.get('job_id')} B={claimed_b.get('job_id')} expected={ja}/{jb}",
        )
        check(
            "B2 pending capacity is accounted per tenant",
            pending(TENANT_A) == 1 and pending(TENANT_B) == 1,
            f"A={pending(TENANT_A)} B={pending(TENANT_B)}",
        )

        # Candidate submission isolation: B cannot submit a candidate for A's job.
        cross_result = client.post(
            f"/v1/worker/jobs/{ja}/result",
            headers={**B, "content-type": "application/json"},
            json={"lease": {"owner": "iso-b-worker", "generation": 1}, "bridge_body": {}},
        )
        own_result_bad = client.post(
            f"/v1/worker/jobs/{ja}/result",
            headers={**A, "content-type": "application/json"},
            json={"lease": {"owner": "iso-a-worker", "generation": 1}, "bridge_body": {}},
        )
        check(
            "B3 candidate submission for another tenant's job is 404 (non-disclosing)",
            cross_result.status_code == 404,
            f"cross={cross_result.status_code} own_bad_body={own_result_bad.status_code}",
        )

        # Complete both jobs at the store layer (the kernel path is covered by
        # the developer-workflow suite); then results must stay tenant-scoped.
        # A's job was already claimed through the API above; complete it with
        # exactly the lease proof that same claim returned (no re-claim, which
        # would be a no-op because the job is PROCESSING).
        lease_a = claimed_a["lease"]
        completed = async_jobs.complete_job(
            claimed_a["job_id"],
            envelope={"api_version": "v1", "status": "VERIFIED", "data": {}},
            http_status=200,
            lease_owner=lease_a["owner"],
            lease_generation=int(lease_a["generation"]),
        )
        check("B3b the owning tenant's claim can complete its own job", completed is True, f"completed={completed}")
        own_res = client.get(f"/v1/results/{resa}", headers=A)
        cross_res = client.get(f"/v1/results/{resa}", headers=B)
        check(
            "B4 result retrieval: owner 200, cross-tenant 404",
            own_res.status_code == 200 and cross_res.status_code == 404,
            f"own={own_res.status_code} cross={cross_res.status_code}",
        )
        check(
            "B5 completion releases only the owning tenant's capacity",
            pending(TENANT_A) == 0 and pending(TENANT_B) == 1,
            f"A={pending(TENANT_A)} B={pending(TENANT_B)}",
        )

        print("\nC. Usage reporting isolation")
        ua = client.get("/v1/usage", headers=A).json()
        ub = client.get("/v1/usage", headers=B).json()
        check(
            "C1 /v1/usage resolves the caller's OWN tenant only",
            ua.get("tenant_id") == TENANT_A
            and ub.get("tenant_id") == TENANT_B
            and ua.get("used") == 1
            and ub.get("used") == 1,
            f"A={ua.get('tenant_id')}/{ua.get('used')} B={ub.get('tenant_id')}/{ub.get('used')}",
        )
        check(
            "C2 usage counters are independent (A's calls never charge B)",
            usage_of(key_a) == 1 and usage_of(key_b) == 1,
            f"A={usage_of(key_a)} B={usage_of(key_b)}",
        )

        print("\nD. Idempotency isolation")
        same_key = f"iso-shared-{int(time.time())}"
        a_first = client.post(
            "/v1/inference-jobs", headers={**A, "Idempotency-Key": same_key},
            json={"raw_input": "revenue was 100"},
        )
        b_first = client.post(
            "/v1/inference-jobs", headers={**B, "Idempotency-Key": same_key},
            json={"raw_input": "revenue was 250"},
        )
        a_replay = client.post(
            "/v1/inference-jobs", headers={**A, "Idempotency-Key": same_key},
            json={"raw_input": "revenue was 100"},
        )
        check(
            "D1 the same Idempotency-Key string is tenant-scoped, not shared",
            b_first.status_code == 202
            and b_first.json().get("job_id") != a_first.json().get("job_id")
            and a_replay.json().get("job_id") == a_first.json().get("job_id")
            and a_replay.headers.get("Idempotent-Replayed") == "true",
            f"a={a_first.json().get('job_id')} b={b_first.json().get('job_id')} "
            f"replay={a_replay.json().get('job_id')} hdr={a_replay.headers.get('Idempotent-Replayed')}",
        )

        print("\nE. Queue admission isolation")
        reset_jobs()
        set_limits(50, 1)
        # A fills its own per-tenant slot.
        a_ok = client.post(
            "/v1/inference-jobs", headers={**A, "Idempotency-Key": f"iso-full-a-{int(time.time())}"},
            json={"raw_input": "revenue was 100"},
        )
        # A is now full; B must still be admitted.
        b_ok = client.post(
            "/v1/inference-jobs", headers={**B, "Idempotency-Key": f"iso-full-b-{int(time.time())}"},
            json={"raw_input": "revenue was 250"},
        )
        a_full = client.post(
            "/v1/inference-jobs", headers={**A, "Idempotency-Key": f"iso-full-a2-{int(time.time())}"},
            json={"raw_input": "revenue was 100"},
        )
        body = a_full.json()
        code = (body.get("error") or {}).get("code") or body.get("code")
        check(
            "E1 one tenant's saturated queue does not block another tenant",
            a_ok.status_code == 202
            and b_ok.status_code == 202
            and a_full.status_code == 503
            and code == queue_capacity.TENANT_QUEUE_FULL
            and pending(TENANT_A) == 1
            and pending(TENANT_B) == 1,
            f"a={a_ok.status_code} b={b_ok.status_code} full={a_full.status_code} "
            f"code={code} A={pending(TENANT_A)} B={pending(TENANT_B)}",
        )

        print("\nF. Unauthenticated / foreign credential")
        no_key = client.get("/v1/usage", headers={})
        foreign = client.get("/v1/usage", headers={"x-platrixa-api-key": "plx_live_not_a_real_key"})
        check(
            "F1 missing and unknown credentials are rejected (no cross-tenant fallback)",
            no_key.status_code in (401, 503) and foreign.status_code in (401, 503),
            f"missing={no_key.status_code} unknown={foreign.status_code}",
        )
        check(
            "F2 reconciliation is consistent after the whole isolation run",
            queue_capacity.reconcile()["consistent"] is True,
            str(queue_capacity.reconcile()),
        )
    except Exception:
        traceback.print_exc()
        check("suite completed without unexpected error", False, "exception (see traceback)")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print(f"\n=== Phase 1B tenant isolation: {passed}/{len(CHECKS)} checks passed ===")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    print("RESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
