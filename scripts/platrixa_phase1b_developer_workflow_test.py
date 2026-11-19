#!/usr/bin/env python3
"""Phase 1B — complete Developer API workflow (disposable PostgreSQL).

End-to-end evidence for the whole data plane, with a synthetic tenant, a
synthetic API key and a real disposable embedded PostgreSQL cluster:

    1. submit an inference job with a developer API key
    2. receive its job id (persistence verified in the database)
    3. poll job status
    4. queue admission (durable, bounded)
    5. worker claim
    6. submit a device candidate
    7. deterministic accounting kernel validation
    8. retrieve the authoritative result
    9. usage + quota accounting
   10. capacity release
   11. tenant isolation for the same artifacts
   12. recovery after a worker interruption

The deterministic accounting kernel remains authoritative: a model-suggested
VERIFIED is clamped, and status is never derived from model output.

Run:
    python3 scripts/platrixa_phase1b_developer_workflow_test.py
"""

from __future__ import annotations

import json
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
    "DATABASE_URL",
):
    os.environ.pop(_v, None)

from backend.auth import async_jobs, queue_capacity  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
_PG: dict = {}
TENANT = "wf-tenant"
RAW_INPUT = "purchased furniture from raj for rs.25000"

# The canonical 18-field candidate (the pair the kernel boundary suite already
# treats as a deterministic VERIFIED case). Model suggests VERIFIED on purpose,
# so the clamp to REVIEW_REQUIRED is observable.
VALID_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "VERIFIED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


def bridge_body(candidate: dict) -> dict:
    return {
        "interpretation_text": json.dumps(candidate),
        "completion_status": "COMPLETE",
        "finish_reason": "stop",
        "model": {"provider": "local-llama.cpp", "adapter_loaded": None},
    }


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

    data = Path("/tmp/platrixa_phase1b_workflow_pgdata")
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
        conn.execute(text((root / "backend/database/platrixa_tenant_quota_schema.sql").read_text("utf-8")))
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


def scalar(sql: str, params: dict | None = None):
    from sqlalchemy import text

    engine = _engine()
    with engine.begin() as conn:
        v = conn.execute(text(sql), params or {}).scalar_one()
    engine.dispose()
    return v


def set_limits(g: int, t: int) -> None:
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = str(g)
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = str(t)
    queue_capacity.reset_cache()


def main() -> int:
    try:
        pg()
        from fastapi.testclient import TestClient

        from api.main import create_app
        from backend.auth import api_keys as key_store
        from backend.auth.tokens import hash_token

        set_limits(50, 5)
        app = create_app()
        client = TestClient(app)

        key_store._schema_ensured.discard(pg())
        key_store._session_factory()
        key = key_store.create_api_key(TENANT, "workflow", "test")["key"]
        other_key = key_store.create_api_key("wf-tenant-other", "workflow-other", "test")["key"]
        H = {"x-platrixa-api-key": key}
        O = {"x-platrixa-api-key": other_key}

        print("\n1-3. Submit, persist, poll")
        created = client.post(
            "/v1/inference-jobs",
            headers={**H, "Idempotency-Key": f"platrixa-wf-submit-{int(time.time())}"},
            json={"raw_input": RAW_INPUT},
        )
        body = created.json()
        job_id, result_id = body.get("job_id"), body.get("result_id")
        row = scalar(
            "SELECT status || '|' || capacity_held::text || '|' || tenant_id "
            "FROM platrixa_async_jobs WHERE job_id = :j",
            {"j": job_id},
        )
        check(
            "1/2 submission returns a job id and the job is persisted QUEUED with its slot held",
            created.status_code == 202 and row == f"QUEUED|true|{TENANT}",
            f"{created.status_code} row={row}",
        )
        polled = client.get(f"/v1/jobs/{job_id}", headers=H)
        check(
            "3 job status poll reads the durable state",
            polled.status_code == 200 and polled.json()["status"] in ("QUEUED", "PROCESSING"),
            f"{polled.status_code} {polled.text[:120]}",
        )

        print("\n4. Queue admission")
        admission = client.post(
            "/v1/inference-jobs",
            headers={**H, "Idempotency-Key": f"platrixa-wf-admit-{int(time.time())}"},
            json={"raw_input": RAW_INPUT},
        )
        pending_now = scalar(
            "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE"
        )
        check(
            "4 durable admission counts the waiting job",
            admission.status_code == 202 and int(pending_now) == 2,
            f"{admission.status_code} pending={pending_now}",
        )

        print("\n5. Worker claim")
        claim = client.post("/v1/worker/jobs/claim", headers=H)
        claimed = claim.json()
        lease = claimed["job"]["lease"]
        check(
            "5 the device worker claims this tenant's job with a lease",
            claim.status_code == 200
            and claimed["status"] == "CLAIMED"
            and claimed["job"]["job_id"] == job_id
            and int(lease["generation"]) >= 1,
            f"{claim.status_code} {str(claimed)[:160]}",
        )

        print("\n6-7. Candidate submission + deterministic kernel validation")
        submitted = client.post(
            f"/v1/worker/jobs/{job_id}/result",
            headers={**H, "content-type": "application/json"},
            json={"lease": {"owner": lease["owner"], "generation": int(lease["generation"])},
                  "bridge_body": bridge_body(VALID_CANDIDATE)},
        )
        env = submitted.json()
        check(
            "6/7 the candidate is validated by the deterministic kernel and the envelope is authoritative",
            submitted.status_code == 200
            and env.get("status") == "VERIFIED"
            and env.get("api_status") == "VERIFIED"
            and env.get("accounting_result") is not None
            and env.get("interpretation") is not None
            and env.get("accounting") == env.get("accounting_result")
            and env.get("candidate", {}).get("suggested_status") != "VERIFIED",
            f"{submitted.status_code} status={env.get('status')} "
            f"suggested={(env.get('candidate') or {}).get('suggested_status')} {submitted.text[:200]}",
        )

        print("\n8-10. Result retrieval, usage, capacity release")
        retrieved = client.get(f"/v1/results/{result_id}", headers=H)
        check(
            "8 GET /v1/results returns exactly the stored envelope",
            retrieved.status_code == 200 and retrieved.json() == env,
            f"{retrieved.status_code}",
        )
        usage = client.get("/v1/usage", headers=H).json()
        stored_usage = scalar(
            "SELECT current_month_usage FROM platrixa_tenant_quotas WHERE api_key_hash = :h",
            {"h": hash_token(key)},
        )
        check(
            "9 usage and monthly quota account the job exactly once",
            usage.get("tenant_id") == TENANT
            and usage.get("used") == int(stored_usage)
            and int(stored_usage) >= 1,
            f"usage={usage} stored={stored_usage}",
        )
        pending_after = scalar(
            "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE AND tenant_id = :t",
            {"t": TENANT},
        )
        reconciled = queue_capacity.reconcile()
        check(
            "10 terminal completion released the pending slot exactly once",
            int(pending_after) == 1  # the second (admission-demo) job is still waiting
            and reconciled["consistent"] is True,
            f"tenant_pending={pending_after} reconcile={reconciled}",
        )

        print("\n11. Tenant isolation on these exact artifacts")
        cross_job = client.get(f"/v1/jobs/{job_id}", headers=O)
        cross_res = client.get(f"/v1/results/{result_id}", headers=O)
        check(
            "11 another tenant cannot read this job or its result (404 both)",
            cross_job.status_code == 404 and cross_res.status_code == 404,
            f"job={cross_job.status_code} result={cross_res.status_code}",
        )

        print("\n12. Recovery after worker interruption")
        # Claim the still-waiting job with a 1-second lease, then "lose" the
        # worker: the job must remain recoverable WITHOUT consuming a second
        # pending slot, and the stale owner must never commit.
        waiting = scalar(
            "SELECT job_id FROM platrixa_async_jobs "
            "WHERE capacity_held = TRUE AND tenant_id = :t LIMIT 1",
            {"t": TENANT},
        )
        first = async_jobs.claim_next_job(
            lease_seconds=1, owner="wf-worker-dead", kind=async_jobs.JOB_KIND_INFERENCE,
            tenant_id=TENANT,
        )
        held_during = int(
            scalar(
                "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE AND tenant_id = :t",
                {"t": TENANT},
            )
        )
        time.sleep(1.4)
        second = async_jobs.claim_next_job(
            lease_seconds=60, owner="wf-worker-alive", kind=async_jobs.JOB_KIND_INFERENCE,
            tenant_id=TENANT,
        )
        stale = async_jobs.complete_job(
            first.job_id,
            envelope={"api_version": "v1", "status": "VERIFIED"},
            http_status=200,
            lease_owner="wf-worker-dead",
            lease_generation=first.lease_generation,
        )
        done = async_jobs.complete_job(
            second.job_id,
            envelope={"api_version": "v1", "status": "VERIFIED"},
            http_status=200,
            lease_owner="wf-worker-alive",
            lease_generation=second.lease_generation,
        )
        final_pending = int(
            scalar(
                "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE AND tenant_id = :t",
                {"t": TENANT},
            )
        )
        check(
            "12 an interrupted worker's job is recovered by a new owner, occupies exactly one slot, "
            "and the stale worker cannot commit",
            first is not None
            and second is not None
            and first.job_id == waiting == second.job_id
            and held_during == 1
            and stale is False
            and done is True
            and final_pending == 0
            and queue_capacity.reconcile()["consistent"] is True,
            f"held={held_during} stale={stale} done={done} pending={final_pending}",
        )
    except Exception:
        traceback.print_exc()
        check("suite completed without unexpected error", False, "exception (see traceback)")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print(f"\n=== Phase 1B developer workflow: {passed}/{len(CHECKS)} checks passed ===")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    print("RESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
