#!/usr/bin/env python3
"""Platrixa S4 — multi-tenant worker authorization (disposable PostgreSQL).

The S4 decision is to RETAIN one worker identity per tenant for the private
beta: the claim's tenant is derived from the authenticated API key, so a worker
holding tenant A's key can only ever claim A's jobs. A multi-tenant worker
credential would require a new server-controlled assignment model; that is
documented as a design proposal, not implemented (see
``PLATRIXA_S4_MULTITENANT_WORKER_DECISION.md``).

This suite proves the invariants that decision rests on, through the REAL
routes and the REAL database:

  1. a client-supplied tenant header never widens or changes the claim scope;
  2. concurrent claims on one tenant never hand the same job to two workers;
  3. a foreign worker cannot submit a result for another tenant's job;
  4. a stale lease generation is refused (fencing);
  5. the same Idempotency-Key under two tenants is two independent requests;
  6. no route reads a tenant from a header (source-level contract check).

Run (needs the test venv with pgserver/psycopg2):
    python scripts/platrixa_s4_multitenant_authorization_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from platrixa_phase1b_tenant_isolation_test import (  # noqa: E402
    TENANT_A,
    TENANT_B,
    key_for,
    pg,
    reset_jobs,
    set_limits,
)

PASSED = 0
FAILED: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def new_client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def main() -> int:
    print("=" * 78)
    print("Platrixa S4 — multi-tenant worker authorization")
    print("=" * 78)

    pg()
    from api.main import create_app

    reset_jobs()
    set_limits(50, 10)
    app = create_app()
    client = new_client(app)

    stamp = int(time.time() * 1000)
    key_a = key_for(TENANT_A, f"s4-a-{stamp}")
    key_b = key_for(TENANT_B, f"s4-b-{stamp}")
    A = {"x-platrixa-api-key": key_a}
    B = {"x-platrixa-api-key": key_b}

    print("\nA. Tenant scope is derived from the key, never a header")
    ra = client.post(
        "/v1/inference-jobs",
        headers={**A, "Idempotency-Key": f"s4-a-create-{stamp}"},
        json={"raw_input": "received cash 100"},
    )
    job_a = (ra.json() or {}).get("job_id")
    check("A1 tenant A creates a job", ra.status_code == 202 and bool(job_a), ra.text[:200])

    # B spoofs A's tenant via every plausible header spelling.
    spoof = {
        **B,
        "X-Platrixa-Tenant": TENANT_A,
        "X-Platrixa-Tenant-Id": TENANT_A,
        "X-Tenant-Id": TENANT_A,
    }
    claim_b_spoof = client.post("/v1/worker/jobs/claim", headers=spoof)
    body_b = claim_b_spoof.json() or {}
    check(
        "A2 a spoofed tenant header does not surface tenant A's job to B",
        claim_b_spoof.status_code == 200
        and body_b.get("status") == "NO_WORK"
        and body_b.get("job") is None,
        f"{claim_b_spoof.status_code} {body_b}",
    )

    claim_a_spoof = client.post(
        "/v1/worker/jobs/claim",
        headers={**A, "X-Platrixa-Tenant-Id": TENANT_B},
    )
    body_a = claim_a_spoof.json() or {}
    claimed_a = (body_a.get("job") or {})
    check(
        "A3 A's own key still claims A's job despite a spoofed tenant header",
        claim_a_spoof.status_code == 200
        and claimed_a.get("job_id") == job_a,
        f"{claim_a_spoof.status_code} {body_a}",
    )

    source = (REPO_ROOT / "api" / "routes" / "inference_api.py").read_text(encoding="utf-8")
    check(
        "A4 no worker route reads a client-supplied tenant header",
        "x-platrixa-tenant" not in source.lower(),
        "a tenant header string appears in inference_api.py",
    )
    check(
        "A5 the claim tenant comes from _resolve_idempotency_tenant",
        "tenant = _resolve_idempotency_tenant(request)" in source,
        "claim does not derive its tenant from the authenticated key",
    )

    print("\nB. One job is never claimed by two concurrent workers")
    rb = client.post(
        "/v1/inference-jobs",
        headers={**A, "Idempotency-Key": f"s4-a-race-{stamp}"},
        json={"raw_input": "paid rent 500"},
    )
    job_race = (rb.json() or {}).get("job_id")
    check("B1 a second job exists for the race", rb.status_code == 202 and bool(job_race), rb.text[:200])

    import threading

    attempts: list = []
    barrier = threading.Barrier(2)

    def claim_once(idx: int) -> None:
        thread_client = new_client(app)
        barrier.wait(timeout=10)
        response = thread_client.post(
            "/v1/worker/jobs/claim", headers={**A, "X-Platrixa-Worker-Id": f"race-{idx}"}
        )
        attempts.append((response.status_code, (response.json() or {}).get("job") or {}))

    threads = [threading.Thread(target=claim_once, args=(i,)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    claimed_ids = [job.get("job_id") for _, job in attempts if job]
    check(
        "B2 both concurrent claims completed",
        len(attempts) == 2 and all(status == 200 for status, _ in attempts),
        attempts,
    )
    check(
        "B3 the raced job was handed to exactly one worker",
        claimed_ids.count(job_race) == 1,
        claimed_ids,
    )
    check(
        "B4 no job id is claimed twice",
        len(claimed_ids) == len(set(claimed_ids)),
        claimed_ids,
    )

    print("\nC. Cross-tenant submission and stale-generation fencing")
    # B submits a candidate for A's job: must be a non-disclosing 404.
    cross = client.post(
        f"/v1/worker/jobs/{job_race}/result",
        headers={**B, "content-type": "application/json"},
        json={"lease": {"owner": "race-1", "generation": 1}, "bridge_body": {}},
    )
    check(
        "C1 a foreign worker cannot submit a result for another tenant's job",
        cross.status_code == 404,
        f"{cross.status_code} {cross.text[:160]}",
    )

    # A submits with a stale generation: fencing refuses it, no result written.
    lease = (dict(attempts[0][1]).get("lease") if attempts and attempts[0][1] else {}) or {}
    real_generation = int(lease.get("generation", 1) or 1)
    stale = client.post(
        f"/v1/worker/jobs/{job_race}/result",
        headers={**A, "content-type": "application/json"},
        json={
            "lease": {"owner": lease.get("owner", "race-1"), "generation": max(0, real_generation - 1)},
            "bridge_body": {},
        },
    )
    check(
        "C2 a stale lease generation is refused (nothing committed)",
        stale.status_code == 409,
        f"{stale.status_code} {stale.text[:160]}",
    )
    job_view = client.get(f"/v1/jobs/{job_race}", headers=A)
    check(
        "C3 the refused submission did not complete the job",
        (job_view.json() or {}).get("status") in ("PROCESSING", "RETRY_WAIT"),
        job_view.text[:200],
    )

    print("\nD. Idempotency is tenant-scoped")
    shared_key = f"s4-shared-{stamp}"
    idem_a = client.post(
        "/v1/inference-jobs",
        headers={**A, "Idempotency-Key": shared_key},
        json={"raw_input": "bought stationery 200"},
    )
    idem_b = client.post(
        "/v1/inference-jobs",
        headers={**B, "Idempotency-Key": shared_key},
        json={"raw_input": "bought stationery 200"},
    )
    check(
        "D1 the same Idempotency-Key under two tenants is two independent jobs",
        idem_a.status_code == 202
        and idem_b.status_code == 202
        and (idem_a.json() or {}).get("job_id") != (idem_b.json() or {}).get("job_id"),
        f"{idem_a.status_code}/{idem_b.status_code}",
    )

    total = PASSED + len(FAILED)
    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{total} multi-tenant authorization checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
