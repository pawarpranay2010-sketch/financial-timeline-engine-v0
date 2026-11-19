#!/usr/bin/env python3
"""Multi-transaction batch API — end-to-end acceptance suite.

Disposable embedded PostgreSQL, real HTTP routes (FastAPI TestClient), real
admiral credentials and the real deterministic kernel:

  * single-transaction backward compatibility (unchanged body, claim, result);
  * batch request validation: empty, duplicate ids, count limit, byte limit,
    summed-input limit, ambiguous shape, bad mode, bad transaction id;
  * INDEPENDENT batches: per-transaction outcomes, partial failure, result
    listing and per-transaction envelope retrieval;
  * ORDERED batches: order enforcement, approved-state hand-off, state-hash
    chain, chain halt with deterministic BLOCKED dependents;
  * batch idempotency (same key/body replays; a changed body is a 409);
  * the SEPARATE accounting-transaction quota (and that a rejected batch
    creates no job);
  * tenant isolation for batch results and per-transaction results;
  * the release endpoint (a busy device does not burn a job's attempt budget);
  * owner-only usage reporting, and that a developer key cannot reach it;
  * fresh-database migration (the startup path creates the batch schema).

Run:
    python3 scripts/platrixa_batch_api_test.py
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

OWNER_TENANT = "batch-owner-tenant"
TENANT_A = "batch-tenant-a"
TENANT_B = "batch-tenant-b"
MGMT_TOKEN = "test-management-token-not-a-real-credential"

RAW_INPUT = "purchased furniture from raj for rs.25000"
_KEYS: dict[str, str] = {}
_COUNTER = {"n": 0}

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
    import pgserver
    from sqlalchemy import create_engine, text

    data = Path("/tmp/platrixa_batch_api_pgdata")
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
    # Deliberately no manual batch/visitor DDL: the store's own startup path must
    # create the batch schema on a fresh database (the defect this guards).
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


def _job_count() -> int:
    return int(_scalar("SELECT count(*) FROM platrixa_async_jobs") or 0)


def _tx_used(tenant: str) -> int:
    row = _scalar(
        "SELECT used FROM platrixa_tenant_transaction_quotas WHERE tenant_id = :t",
        {"t": tenant},
    )
    return int(row or 0)


def key() -> str:
    _COUNTER["n"] += 1
    return f"batch-test-{_COUNTER['n']}-{int(time.time() * 1000)}"


def main() -> int:
    pg()

    os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"] = MGMT_TOKEN
    os.environ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID"] = OWNER_TENANT
    os.environ[queue_capacity.GLOBAL_LIMIT_ENV_VAR] = "200"
    os.environ[queue_capacity.PER_TENANT_LIMIT_ENV_VAR] = "50"
    queue_capacity.reset_cache()
    os.environ[tenant_rate_limit.TENANT_RATE_LIMIT_ENV_VAR] = "10000"
    os.environ[tenant_rate_limit.TENANT_RATE_WINDOW_ENV_VAR] = "60"
    tenant_rate_limit.reset_cache()
    os.environ[batch_jobs.MAX_TRANSACTIONS_ENV_VAR] = "5"
    os.environ[batch_jobs.MAX_INPUT_CHARS_ENV_VAR] = "20000"
    batch_jobs.reset_cache()
    os.environ[transaction_quota.TX_MONTHLY_LIMIT_ENV_VAR] = "50"
    transaction_quota.reset_cache()

    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app()
    client = TestClient(app)
    mgmt = {"x-platrixa-management-token": MGMT_TOKEN}

    # ---- fresh-database schema (created by the STARTUP path) --------------
    cols = _scalar(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'platrixa_async_jobs' "
        "AND column_name IN ('batch_mode','transaction_count')"
    )
    table = _scalar(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'platrixa_batch_transactions'"
    )
    check("startup path creates the batch job columns on a fresh database", int(cols) == 2, f"cols={cols}")
    check("startup path creates the batch transaction table on a fresh database", int(table) == 1, f"tables={table}")

    # ---- provisioning through the real owner route ------------------------
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
        check(f"provision {tenant}", r.status_code == 201, f"http={r.status_code} {r.text[:160]}")
        if r.status_code == 201:
            _KEYS[tenant] = r.json()["key"]
    if len(_KEYS) != 2:
        return 1

    def headers(tenant: str, idem: str | None = None) -> dict:
        h = {"x-platrixa-api-key": _KEYS[tenant]}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    def submit(body: dict, tenant: str = TENANT_A, idem: str | None = None):
        return client.post("/v1/inference-jobs", headers=headers(tenant, idem), json=body)

    def claim(tenant: str = TENANT_A):
        return client.post("/v1/worker/jobs/claim", headers=headers(tenant))

    def batch_body(mode: str, items: list[tuple[str, str]]) -> dict:
        return {
            "mode": mode,
            "transactions": [{"transaction_id": i, "input": t} for i, t in items],
        }

    def finish(result, label: str):
        try:
            return result.json()
        except Exception:
            return {"__unparsable__": label}

    # =====================================================================
    # 1. SINGLE-TRANSACTION BACKWARD COMPATIBILITY
    # =====================================================================
    single = submit({"raw_input": RAW_INPUT}, idem=key())
    single_body = finish(single, "single")
    check("single-transaction job is still accepted (202)", single.status_code == 202, f"http={single.status_code} {single.text[:200]}")
    check(
        "single-transaction response carries no batch fields",
        "mode" not in single_body and "transaction_count" not in single_body,
        json.dumps(single_body)[:200],
    )
    single_job = single_body.get("job_id", "")

    claimed = claim()
    claimed_body = finish(claimed, "claim")
    job = claimed_body.get("job") or {}
    check(
        "single-transaction claim returns raw_input and no transaction list",
        claimed.status_code == 200
        and job.get("raw_input") == RAW_INPUT
        and "transactions" not in job,
        json.dumps(job)[:200],
    )
    lease = job.get("lease") or {}
    result = client.post(
        f"/v1/worker/jobs/{single_job}/result",
        headers=headers(TENANT_A),
        json={"lease": {"owner": lease.get("owner"), "generation": lease.get("generation")},
              "bridge_body": bridge_body(VALID_CANDIDATE)},
    )
    check(
        "single-transaction result still returns the canonical VERIFIED envelope",
        result.status_code == 200
        and result.json().get("api_status") == "VERIFIED"
        and result.json().get("status") == "VERIFIED",
        f"http={result.status_code} {result.text[:240]}",
    )
    status = client.get(f"/v1/jobs/{single_job}", headers=headers(TENANT_A))
    check(
        "single-transaction job status carries no batch block",
        status.status_code == 200 and "batch" not in status.json(),
        status.text[:200],
    )

    # =====================================================================
    # 2. BATCH VALIDATION (rejected BEFORE admission)
    # =====================================================================
    jobs_before = _job_count()
    used_before = _tx_used(TENANT_A)

    empty = submit({"mode": "independent", "transactions": []}, idem=key())
    check("empty batch is rejected with BATCH_EMPTY",
          empty.status_code == 400 and empty.json()["error"]["code"] == "BATCH_EMPTY",
          empty.text[:200])

    duplicate = submit(
        batch_body("independent", [("tx-a", "bought goods for cash 100"), ("tx-a", "sold goods for cash 200")]),
        idem=key(),
    )
    dup_body = finish(duplicate, "dup")
    check(
        "duplicate transaction ids are rejected with the ids named",
        duplicate.status_code == 400
        and dup_body["error"]["code"] == "BATCH_DUPLICATE_TRANSACTION_ID"
        and dup_body["error"]["details"]["duplicate_transaction_ids"] == ["tx-a"],
        duplicate.text[:240],
    )

    too_many = submit(
        batch_body("independent", [(f"tx-{i}", RAW_INPUT) for i in range(6)]), idem=key()
    )
    check(
        "a batch over the configured transaction limit is rejected",
        too_many.status_code == 400
        and too_many.json()["error"]["code"] == "BATCH_TOO_MANY_TRANSACTIONS"
        and too_many.json()["error"]["details"]["max_transactions"] == 5,
        too_many.text[:240],
    )

    ambiguous = submit(
        {"raw_input": RAW_INPUT, "transactions": [{"transaction_id": "tx-1", "input": RAW_INPUT}]},
        idem=key(),
    )
    check(
        "sending raw_input AND transactions is rejected as ambiguous",
        ambiguous.status_code == 400 and ambiguous.json()["error"]["code"] == "BATCH_AMBIGUOUS",
        ambiguous.text[:200],
    )

    bad_mode = submit(batch_body("sequential", [("tx-1", RAW_INPUT)]), idem=key())
    check(
        "an unknown execution mode is rejected",
        bad_mode.status_code == 400 and bad_mode.json()["error"]["code"] == "BATCH_MODE_INVALID",
        bad_mode.text[:200],
    )

    bad_id = submit(
        {"mode": "independent", "transactions": [{"transaction_id": "bad id!", "input": RAW_INPUT}]},
        idem=key(),
    )
    check(
        "an unusable transaction id is rejected with its index",
        bad_id.status_code == 400
        and bad_id.json()["error"]["code"] == "BATCH_TRANSACTION_INVALID"
        and bad_id.json()["error"]["details"]["index"] == 0,
        bad_id.text[:220],
    )

    os.environ[batch_jobs.MAX_BYTES_ENV_VAR] = "1024"
    batch_jobs.reset_cache()
    big = submit(
        batch_body("independent", [(f"tx-{i}", "x" * 600) for i in range(3)]), idem=key()
    )
    check(
        "a request over the byte limit is rejected",
        big.status_code == 400 and big.json()["error"]["code"] == "BATCH_TOO_LARGE",
        big.text[:200],
    )
    os.environ.pop(batch_jobs.MAX_BYTES_ENV_VAR, None)
    batch_jobs.reset_cache()

    check("rejected batches created no job", _job_count() == jobs_before, f"{jobs_before} → {_job_count()}")
    check(
        "rejected batches charged no transaction units",
        _tx_used(TENANT_A) == used_before,
        f"{used_before} → {_tx_used(TENANT_A)}",
    )

    # =====================================================================
    # 3. INDEPENDENT BATCH: PARTIAL OUTCOMES
    # =====================================================================
    indep_key = key()
    indep_body = batch_body(
        "independent",
        [("tx-ok", RAW_INPUT), ("tx-bad", "sold goods for cash 3000")],
    )
    indep = submit(indep_body, idem=indep_key)
    indep_json = finish(indep, "indep")
    check("independent batch is accepted (202)", indep.status_code == 202, f"http={indep.status_code} {indep.text[:200]}")
    check(
        "batch acceptance names the mode and the transaction count",
        indep_json.get("mode") == "independent" and indep_json.get("transaction_count") == 2,
        json.dumps(indep_json)[:240],
    )
    check(
        "batch quota charges one unit per transaction",
        _tx_used(TENANT_A) == used_before + 2,
        f"{used_before} → {_tx_used(TENANT_A)}",
    )
    indep_job = indep_json.get("job_id", "")
    indep_result = indep_json.get("result_id", "")

    # replay: same key + same body
    replay = submit(indep_body, idem=indep_key)
    check(
        "same Idempotency-Key and body replay the original batch job",
        replay.status_code == 202
        and replay.json().get("job_id") == indep_job
        and replay.headers.get("Idempotent-Replayed") == "true",
        f"http={replay.status_code} {replay.text[:200]}",
    )
    check(
        "a batch replay charges no additional transaction units",
        _tx_used(TENANT_A) == used_before + 2,
        f"used={_tx_used(TENANT_A)}",
    )
    conflict = submit(
        batch_body("independent", [("tx-ok", RAW_INPUT), ("tx-other", "bought goods 100")]),
        idem=indep_key,
    )
    check(
        "same Idempotency-Key with a different batch is a 409",
        conflict.status_code == 409
        and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
        conflict.text[:200],
    )

    indep_claim = claim()
    indep_claim_json = finish(indep_claim, "indep-claim")
    indep_job_body = indep_claim_json.get("job") or {}
    check(
        "batch claim returns every pending transaction with its batch index",
        indep_claim.status_code == 200
        and indep_job_body.get("mode") == "independent"
        and [t["transaction_id"] for t in indep_job_body.get("transactions", [])] == ["tx-ok", "tx-bad"]
        and indep_job_body["transactions"][1]["batch_index"] == 1,
        json.dumps(indep_job_body)[:300],
    )
    check(
        "batch claim carries a bounded device budget",
        float(indep_job_body.get("batch_budget_seconds") or 0) > 0,
        json.dumps(indep_job_body)[:200],
    )

    lease = indep_job_body.get("lease") or {}
    proof = {"owner": lease.get("owner"), "generation": lease.get("generation")}
    submitted = client.post(
        f"/v1/worker/jobs/{indep_job}/result",
        headers=headers(TENANT_A),
        json={
            "lease": proof,
            "transactions": [
                {"transaction_id": "tx-ok", "bridge_body": bridge_body(VALID_CANDIDATE)},
                {
                    "transaction_id": "tx-bad",
                    "error": {"code": "VALIDATION_FAILED", "message": "model produced unusable JSON"},
                },
            ],
        },
    )
    env = finish(submitted, "indep-submit")
    check(
        "independent batch commits with per-transaction outcomes",
        submitted.status_code == 200, f"http={submitted.status_code} {submitted.text[:300]}",
    )
    check(
        "batch envelope counts a verified and a failed transaction",
        env.get("batch_status") == "COMPLETE"
        and env.get("verified_count") == 1
        and env.get("failed_count") == 1
        and env.get("total_transactions") == 2,
        json.dumps({k: env.get(k) for k in ("batch_status", "verified_count", "failed_count")}),
    )
    check(
        "a partial batch is not reported as success",
        env.get("success") is False and env.get("api_status") == "FAILED",
        json.dumps({k: env.get(k) for k in ("success", "api_status")}),
    )
    check(
        "independent mode records no accounting state chain",
        env.get("state_chain") == [] and env.get("final_state_hash") is None,
        json.dumps(env.get("state_chain")),
    )
    check(
        "independent mode records no prior-state hash on any transaction",
        all(t.get("prior_state_hash") is None for t in env.get("transactions", [])),
        json.dumps(env.get("transactions"))[:240],
    )

    stored = client.get(f"/v1/results/{indep_result}", headers=headers(TENANT_A))
    check(
        "the batch envelope is served from the canonical result URL",
        stored.status_code == 200 and stored.json().get("batch_id") == indep_job,
        stored.text[:200],
    )
    listing = client.get(f"/v1/results/{indep_result}/transactions", headers=headers(TENANT_A))
    listing_json = finish(listing, "listing")
    check(
        "the transaction listing returns both summaries in batch order",
        listing.status_code == 200
        and listing_json.get("total") == 2
        and [t["transaction_id"] for t in listing_json.get("transactions", [])] == ["tx-ok", "tx-bad"],
        listing.text[:300],
    )
    one = client.get(f"/v1/results/{indep_result}/transactions/tx-ok", headers=headers(TENANT_A))
    check(
        "a verified transaction's own envelope is retrievable",
        one.status_code == 200 and one.json().get("api_status") == "VERIFIED",
        one.text[:200],
    )
    failed_one = client.get(f"/v1/results/{indep_result}/transactions/tx-bad", headers=headers(TENANT_A))
    check(
        "a failed transaction's own envelope names the error code",
        failed_one.status_code == 400
        and failed_one.json().get("error", {}).get("code") == "VALIDATION_FAILED",
        failed_one.text[:240],
    )

    cross = client.get(f"/v1/results/{indep_result}/transactions/tx-ok", headers=headers(TENANT_B))
    check(
        "another tenant cannot read a batch transaction result",
        cross.status_code == 404 and cross.json()["error"]["code"] == "RESULT_NOT_FOUND",
        cross.text[:200],
    )
    cross_list = client.get(f"/v1/results/{indep_result}/transactions", headers=headers(TENANT_B))
    check(
        "another tenant cannot list a batch's transactions",
        cross_list.status_code == 404,
        cross_list.text[:200],
    )

    indep_status = client.get(f"/v1/jobs/{indep_job}", headers=headers(TENANT_A))
    check(
        "batch job status reports the per-outcome counts",
        indep_status.status_code == 200
        and indep_status.json().get("batch", {}).get("counts", {}).get("verified") == 1
        and indep_status.json().get("batch", {}).get("counts", {}).get("failed") == 1,
        indep_status.text[:300],
    )

    # =====================================================================
    # 4. ORDERED BATCH: STATE CHAIN AND DETERMINISTIC HALT
    # =====================================================================
    ordered = submit(
        batch_body(
            "ordered",
            [
                ("seq-1", RAW_INPUT),
                ("seq-2", "purchased stationery for cash 500"),
                ("seq-3", "sold goods for cash 3000"),
            ],
        ),
        idem=key(),
    )
    ordered_json = finish(ordered, "ordered")
    check("ordered batch is accepted", ordered.status_code == 202, ordered.text[:200])
    ordered_job = ordered_json.get("job_id", "")
    ordered_result = ordered_json.get("result_id", "")

    ordered_claim = claim()
    ordered_job_body = finish(ordered_claim, "ordered-claim").get("job") or {}
    check(
        "ordered claim returns the sequence in batch order",
        [t["transaction_id"] for t in ordered_job_body.get("transactions", [])]
        == ["seq-1", "seq-2", "seq-3"],
        json.dumps(ordered_job_body)[:240],
    )
    o_lease = ordered_job_body.get("lease") or {}
    o_proof = {"owner": o_lease.get("owner"), "generation": o_lease.get("generation")}
    url = f"/v1/worker/jobs/{ordered_job}/transactions"

    violation = client.post(
        f"{url}/seq-2/result",
        headers=headers(TENANT_A),
        json={"lease": o_proof, "bridge_body": bridge_body(VALID_CANDIDATE)},
    )
    check(
        "an ordered batch refuses a transaction submitted out of order",
        violation.status_code == 409
        and violation.json()["error"]["code"] == "BATCH_ORDER_VIOLATION"
        and violation.json()["error"]["details"]["expected_transaction_id"] == "seq-1",
        violation.text[:240],
    )

    first = client.post(
        f"{url}/seq-1/result",
        headers=headers(TENANT_A),
        json={"lease": o_proof, "bridge_body": bridge_body(VALID_CANDIDATE)},
    )
    first_json = finish(first, "ordered-1")
    check(
        "the first verified link returns the next transaction with approved state",
        first.status_code == 200
        and first_json.get("transaction", {}).get("status") == "VERIFIED"
        and first_json.get("next", {}).get("transaction_id") == "seq-2"
        and isinstance(first_json.get("next", {}).get("prior_state"), dict)
        and first_json["next"]["prior_state_hash"],
        first.text[:400],
    )
    prior_hash = (first_json.get("next") or {}).get("prior_state_hash")

    bad_candidate = bridge_body({"not": "a candidate"})
    second = client.post(
        f"{url}/seq-2/result",
        headers=headers(TENANT_A),
        json={"lease": o_proof, "bridge_body": bad_candidate},
    )
    second_json = finish(second, "ordered-2")
    check(
        "a non-verified link halts the chain in one response",
        second.status_code == 200 and second_json.get("batch_status") == "HALTED",
        f"http={second.status_code} {second.text[:300]}",
    )
    by_id = {t["transaction_id"]: t for t in second_json.get("transactions", [])}
    check(
        "the halted link keeps its real kernel outcome",
        by_id.get("seq-2", {}).get("status") in ("INVALID_INPUT", "REVIEW_REQUIRED", "FAILED", "UNSUPPORTED"),
        json.dumps(by_id.get("seq-2")),
    )
    check(
        "the dependent transaction is BLOCKED deterministically",
        by_id.get("seq-3", {}).get("status") == "BLOCKED"
        and by_id.get("seq-3", {}).get("error_code") == "BLOCKED_BY_PRIOR_UNVERIFIED_STATE",
        json.dumps(by_id.get("seq-3")),
    )
    check(
        "the halted link records the approved prior state hash it built on",
        by_id.get("seq-2", {}).get("prior_state_hash") == prior_hash,
        json.dumps(by_id.get("seq-2")),
    )
    check(
        "the state chain contains exactly the verified link",
        len(second_json.get("state_chain") or []) == 1
        and second_json.get("final_state_hash") == prior_hash,
        json.dumps(second_json.get("state_chain")),
    )
    check(
        "the batch envelope reports one verified, one halted and one blocked",
        second_json.get("verified_count") == 1
        and second_json.get("blocked_count") == 1
        and second_json.get("success") is False,
        json.dumps({k: second_json.get(k) for k in ("verified_count", "blocked_count", "success")}),
    )
    blocked_result = client.get(
        f"/v1/results/{ordered_result}/transactions/seq-3", headers=headers(TENANT_A)
    )
    check(
        "a blocked transaction has no fabricated result envelope",
        blocked_result.status_code == 404
        and blocked_result.json()["error"]["code"] == "RESULT_NOT_READY",
        blocked_result.text[:200],
    )

    # =====================================================================
    # 5. TRANSACTION QUOTA (separate from the job quota)
    # =====================================================================
    quota_limit = _tx_used(TENANT_B) + 2
    transaction_quota.set_limit(TENANT_B, quota_limit)
    first_b = submit(batch_body("independent", [("b-1", RAW_INPUT), ("b-2", RAW_INPUT)]), tenant=TENANT_B, idem=key())
    check("a batch inside the transaction allowance is admitted", first_b.status_code == 202, first_b.text[:200])
    over = submit(batch_body("independent", [("b-3", RAW_INPUT)]), tenant=TENANT_B, idem=key())
    check(
        "a batch over the transaction allowance is refused with 429",
        over.status_code == 429
        and over.json()["error"]["code"] == "TRANSACTION_QUOTA_EXCEEDED"
        and over.json()["error"]["details"]["limit"] == quota_limit,
        over.text[:240],
    )
    check(
        "a refused batch creates no job",
        _scalar(
            "SELECT count(*) FROM platrixa_async_jobs WHERE tenant_id = :t AND request_json LIKE '%b-3%'",
            {"t": TENANT_B},
        ) == 0,
    )

    # =====================================================================
    # 6. RELEASE (busy device must not burn the attempt budget)
    # =====================================================================
    rel = claim(tenant=TENANT_B)
    rel_job = (finish(rel, "rel").get("job") or {})
    rel_id = rel_job.get("job_id", "")
    rel_lease = rel_job.get("lease") or {}
    rel_proof = {"owner": rel_lease.get("owner"), "generation": rel_lease.get("generation")}
    released = client.post(
        f"/v1/worker/jobs/{rel_id}/release",
        headers=headers(TENANT_B),
        json={"lease": rel_proof, "reason": "SLOT_BUSY"},
    )
    check(
        "a busy device releases the claim back to QUEUED",
        released.status_code == 200
        and released.json().get("released") is True
        and released.json().get("status") == "QUEUED",
        released.text[:240],
    )
    row = _scalar(
        "SELECT attempt_count FROM platrixa_async_jobs WHERE job_id = :j", {"j": rel_id}
    )
    check("a released claim refunds the attempt it consumed", int(row or 0) == 0, f"attempt_count={row}")
    reclaim = claim(tenant=TENANT_B)
    check(
        "the released job is claimable again",
        reclaim.status_code == 200
        and (finish(reclaim, "reclaim").get("job") or {}).get("job_id") == rel_id,
        reclaim.text[:240],
    )

    # =====================================================================
    # 7. OWNER-ONLY USAGE
    # =====================================================================
    usage = client.get("/v1/owner/usage", headers=mgmt)
    usage_json = finish(usage, "usage")
    tenants = {t["tenant_id"]: t for t in usage_json.get("tenants", [])}
    check("owner usage report is available to the management credential", usage.status_code == 200, usage.text[:200])
    check(
        "owner usage reports jobs, transactions and both quotas per tenant",
        TENANT_A in tenants
        and tenants[TENANT_A]["jobs"]["total"] >= 3
        and tenants[TENANT_A]["transactions"]["verified"] >= 2
        and tenants[TENANT_A]["quota"]["jobs"] is not None
        and tenants[TENANT_A]["quota"]["transactions"] is not None,
        json.dumps(tenants.get(TENANT_A, {}))[:400],
    )
    check(
        "owner usage reports batch bounds and blocked/deferred counts",
        usage_json.get("batch", {}).get("max_transactions") == 5
        and tenants[TENANT_A]["transactions"]["blocked"] == 1,
        json.dumps(usage_json.get("batch")),
    )
    denied = client.get("/v1/owner/usage", headers=headers(TENANT_A))
    check("a developer key cannot reach the owner usage report", denied.status_code == 403, denied.text[:200])
    check(
        "the owner usage report exposes no transaction text or key material",
        _KEYS[TENANT_A] not in usage.text and RAW_INPUT not in usage.text,
        "leak detected",
    )

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} batch API checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
