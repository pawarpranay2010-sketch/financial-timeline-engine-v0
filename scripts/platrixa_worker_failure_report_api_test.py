#!/usr/bin/env python3
"""Fenced failure-report route — acceptance suite against real PostgreSQL.

Run (needs the repo's API dependencies + embedded postgres):
    .venv/bin/python scripts/platrixa_worker_failure_report_api_test.py

Covers the SERVER half of the reliability repair: the additive
``POST /v1/worker/jobs/{job_id}/failure`` route and the opportunistic
exhausted-lease sweep on the claim path.

  A  a retryable failure moves the job to RETRY_WAIT (backoff gate), and the
     lease is released so the job is reclaimable
  B  a permanent failure is terminal FAILED, and stays non-retryable
  C  an unsupported failure code is refused (400) and writes nothing
  D  a superseded generation cannot report (409), and the newer worker's lease
     is untouched
  E  a job that already has an authoritative result cannot be failed (409);
     the stored result is unchanged
  F  a foreign tenant gets a non-disclosing 404
  G  the retry budget is enforced through the report path (third report → FAILED)
  H  the SWEEP terminalises a job that exhausted its budget and whose lease
     lapsed, so it can no longer sit in PROCESSING forever

Nothing here points at production: an embedded PostgreSQL instance is created
under /tmp and discarded. No Railway variable is read or written, and no job is
created outside this process's own throwaway database.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.auth import async_jobs  # noqa: E402
from fte_fyjc_89_outbound_inference_bridge_test import (  # noqa: E402
    KEY_A,
    KEY_B,
    VALID_CANDIDATE,
    HarnessClient,
    bridge_body,
    create_and_claim,
    expire_lease,
    make_client,
    make_tenant,
    pg_backend,
    reset_runtime_state,
    worker_headers,
)

PASSED = 0
FAILED: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
        return
    FAILED.append((name, detail))
    print(f"  [FAIL] {name} — {detail}")


def job_row(engine, job_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT status, result_json, error_json, retryable, attempt_count, "
                "max_attempts, lease_owner, lease_generation, lease_expires_at "
                "FROM platrixa_async_jobs WHERE job_id = :j"
            ),
            {"j": job_id},
        ).mappings().first()


def report(c: TestClient, job_id: str, lease: dict, code: str, *, key: str = KEY_A,
           message: str = "worker could not produce a candidate", worker: str = "w-device-1"):
    return c.post(
        f"/v1/worker/jobs/{job_id}/failure",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]},
              "error": {"code": code, "message": message}},
        headers=worker_headers(key, worker),
    )


def fresh(c: TestClient, engine, raw_input: str = "purchased furniture from raj for rs.25000"):
    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)
    return create_and_claim(c, engine, raw_input, "w-device-1")


def section_a_and_b(c: TestClient, engine) -> None:
    print("\nA — retryable failure → RETRY_WAIT (lease released, result untouched)")
    job_id, lease, _rid, _created = fresh(c, engine)
    r = report(c, job_id, lease, "MODEL_UNAVAILABLE")
    row = job_row(engine, job_id)
    check("A1 200 and the body names RETRY_WAIT",
          r.status_code == 200 and r.json().get("status") == "RETRY_WAIT",
          f"{r.status_code} {r.text[:200]}")
    check("A2 retryable=true is the SERVER's classification",
          r.json().get("retryable") is True, r.text[:200])
    check("A3 the row is RETRY_WAIT, not PROCESSING",
          row["status"] == "RETRY_WAIT", str(dict(row)))
    check("A4 no result was written", row["result_json"] is None, str(row["result_json"])[:120])
    check("A5 the lease was released (reclaimable after backoff)",
          row["lease_owner"] is None and row["lease_expires_at"] is None, str(dict(row)))
    check("A6 the stored error carries the reported code",
          json.loads(row["error_json"])["error"]["code"] == "MODEL_UNAVAILABLE",
          str(row["error_json"])[:200])

    print("\nB — permanent failure → terminal FAILED")
    job_id, lease, _rid, _created = fresh(c, engine)
    r = report(c, job_id, lease, "VALIDATION_FAILED")
    row = job_row(engine, job_id)
    check("B1 200 and the body names FAILED",
          r.status_code == 200 and r.json().get("status") == "FAILED",
          f"{r.status_code} {r.text[:200]}")
    check("B2 retryable=false", r.json().get("retryable") is False, r.text[:200])
    check("B3 the row is terminal FAILED", row["status"] == "FAILED", str(row["status"]))
    check("B4 still no result", row["result_json"] is None, str(row["result_json"])[:120])


def section_c_to_f(c: TestClient, engine) -> None:
    print("\nC — an unsupported failure code is a contract error, not a guess")
    job_id, lease, _rid, _created = fresh(c, engine)
    r = report(c, job_id, lease, "BECAUSE_I_SAID_SO")
    row = job_row(engine, job_id)
    check("C1 400 REQUEST_MALFORMED", r.status_code == 400, f"{r.status_code} {r.text[:200]}")
    check("C2 the job is untouched (still PROCESSING)",
          row["status"] == "PROCESSING" and row["lease_owner"] == lease["owner"],
          str(dict(row)))

    print("\nD — a superseded generation cannot report")
    job_id, lease_a, _rid, _created = fresh(c, engine)
    expire_lease(engine, job_id)
    claim_b = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-device-2"))
    check("D0 the second worker claimed the same job",
          claim_b.status_code == 200 and claim_b.json()["job"]["job_id"] == job_id,
          claim_b.text[:200])
    lease_b = claim_b.json()["job"]["lease"]
    r = report(c, job_id, lease_a, "MODEL_UNAVAILABLE", worker="w-device-1")
    row = job_row(engine, job_id)
    check("D1 409 LEASE_LOST for the stale generation", r.status_code == 409,
          f"{r.status_code} {r.text[:200]}")
    check("D2 the newer worker's lease is untouched",
          row["lease_owner"] == lease_b["owner"]
          and row["lease_generation"] == lease_b["generation"]
          and row["status"] == "PROCESSING",
          str(dict(row)))

    print("\nE — a completed job cannot be failed, and its result survives")
    job_id, lease, _rid, _created = fresh(c, engine)
    submitted = c.post(
        f"/v1/worker/jobs/{job_id}/result",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]},
              "bridge_body": bridge_body(VALID_CANDIDATE)},
        headers=worker_headers(KEY_A, "w-device-1"),
    )
    check("E0 the candidate committed authoritatively",
          submitted.status_code == 200 and submitted.json().get("status") == "VERIFIED",
          f"{submitted.status_code} {submitted.text[:200]}")
    before = job_row(engine, job_id)["result_json"]
    r = report(c, job_id, lease, "MODEL_UNAVAILABLE")
    after = job_row(engine, job_id)
    check("E1 409 — an authoritative result is never failed", r.status_code == 409,
          f"{r.status_code} {r.text[:200]}")
    check("E2 the job is still COMPLETED with the SAME stored result",
          after["status"] == "COMPLETED" and after["result_json"] == before,
          str(after["status"]))

    print("\nF — a foreign tenant gets a non-disclosing 404")
    job_id, lease, _rid, _created = fresh(c, engine)
    r = report(c, job_id, lease, "MODEL_UNAVAILABLE", key=KEY_B)
    row = job_row(engine, job_id)
    check("F1 404 for another tenant's job", r.status_code == 404,
          f"{r.status_code} {r.text[:200]}")
    check("F2 and nothing was written", row["status"] == "PROCESSING", str(row["status"]))


def section_g_and_h(c: TestClient, engine) -> None:
    print("\nG — the retry budget is enforced through the report path")
    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)
    job_id, lease, _rid, _created = create_and_claim(c, engine, "paid rent 12000", "w-device-1")
    statuses = []
    current = lease
    for _ in range(3):
        r = report(c, job_id, current, "MODEL_UNAVAILABLE")
        statuses.append(r.json().get("status") if r.status_code == 200 else r.status_code)
        if r.status_code != 200:
            break
        if r.json()["status"] == "RETRY_WAIT":
            # Backoff gate: make it claimable again immediately.
            with engine.begin() as conn:
                conn.execute(text("UPDATE platrixa_async_jobs SET next_attempt_at = now() - interval '1 second' WHERE job_id = :j"), {"j": job_id})
            claim = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-device-1"))
            if claim.status_code == 200 and claim.json().get("job"):
                current = claim.json()["job"]["lease"]
    row = job_row(engine, job_id)
    check("G1 report outcomes walk RETRY_WAIT → RETRY_WAIT → FAILED",
          statuses == ["RETRY_WAIT", "RETRY_WAIT", "FAILED"], str(statuses))
    check("G2 the row is terminal FAILED once the budget is spent",
          row["status"] == "FAILED", str(dict(row)))
    check("G3 still no result was ever written", row["result_json"] is None,
          str(row["result_json"])[:120])

    print("\nH — the sweep terminalises a stuck, budget-exhausted job")
    job_id, lease, _rid, _created = create_and_claim(c, engine, "bought a laptop 60000", "w-device-1")
    # Claim it the maximum number of times, expiring the lease each time, and
    # never reporting a failure — the exact crash/kill scenario.
    for _ in range(2):
        expire_lease(engine, job_id)
        again = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-device-1"))
        check("H0 an abandoned job is still claimable while budget remains",
              again.status_code == 200 and again.json().get("status") == "CLAIMED",
              again.text[:160])
    expire_lease(engine, job_id)
    row_before = job_row(engine, job_id)
    check("H1 the fixture reached the stuck state (budget spent, lease lapsed)",
          row_before["attempt_count"] >= row_before["max_attempts"]
          and row_before["status"] == "PROCESSING",
          str(dict(row_before)))
    # The next claim runs the sweep; before this repair the job stayed in
    # PROCESSING forever because claim_next_job requires attempt_count < max.
    swept = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-device-1"))
    row_after = job_row(engine, job_id)
    check("H2 the claim answers NO_WORK", swept.status_code == 200
          and swept.json().get("status") == "NO_WORK", swept.text[:160])
    check("H3 the stuck job is now terminal FAILED, not PROCESSING",
          row_after["status"] == "FAILED", str(row_after["status"]))
    check("H4 the sweep wrote no result",
          row_after["result_json"] is None, str(row_after["result_json"])[:120])
    check("H5 the sweep names why, in the job's error envelope",
          json.loads(row_after["error_json"])["error"]["code"] == async_jobs.LEASE_EXHAUSTED_CODE,
          str(row_after["error_json"])[:200])


def main() -> int:
    print("=" * 78)
    print("Worker failure-report route — acceptance against real PostgreSQL")
    print("=" * 78)
    _srv, engine = pg_backend()
    c = make_client(HarnessClient())
    reset_runtime_state(engine)
    try:
        section_a_and_b(c, engine)
        section_c_to_f(c, engine)
        section_g_and_h(c, engine)
    finally:
        reset_runtime_state(engine)

    total = PASSED + len(FAILED)
    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{total} failure-report checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
