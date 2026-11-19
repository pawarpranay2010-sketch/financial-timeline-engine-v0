"""Platrixa — owner-only tenant provisioning + isolation test.

Proves, against a throwaway local PostgreSQL, that a NEW developer tenant can
be provisioned with its own credential and quota, and that it is isolated from
an existing tenant on every read and management path.

Local only: no production connection, no production credential, no deploy.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    import pgserver

    dsn = pgserver.get_server(tempfile.mkdtemp(prefix="plx_prov_")).get_uri()
    os.environ.update(
        DATABASE_URL=dsn,
        PLATRIXA_METERING_DATABASE_URL=dsn,
        PLATRIXA_KEY_MANAGEMENT_TENANT_ID="termux-device",
        PLATRIXA_KEY_MANAGEMENT_TOKEN="prov-test-token-" + "y" * 32,
    )

    import psycopg2
    from fastapi.testclient import TestClient

    from api.main import app
    from api.routes import developer_keys as dk_routes
    from backend.auth import api_keys as store

    # Production-compatible management credential (static token + tenant
    # binding). Sent on every management request so this suite exercises the
    # REAL authorization on the deployed line, not a test bypass.
    MGMT = {"x-platrixa-management-token": os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"]}

    client = TestClient(app, raise_server_exceptions=False)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    def scalar(sql: str, **params):
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params.values()) or None)
            row = cur.fetchone()
        return row[0] if row else None

    def rows(sql: str, **params):
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params.values()) or None)
            return cur.fetchall()

    print("\nSETUP — pre-existing tenant (the production tenant shape)")
    existing = store.provision_tenant("termux-device", "Existing Termux device", "test", 100)
    existing_key = existing["key"]
    check("setup: existing tenant provisioned", existing["tenant_id"] == "termux-device")

    # Management authorization. The token header (MGMT) is the real credential
    # on the deployed line; the dependency override only exists on the
    # assertion-only line, where there is no token path. P30 below asserts the
    # unauthenticated case regardless.
    if hasattr(dk_routes, "require_management"):
        app.dependency_overrides[dk_routes.require_management] = lambda: "termux-device"

    print("\nA. TENANT PROVISIONING")
    r = client.post(
        "/v1/developer/tenants",
        json={
            "tenant_id": "dev-alice",
            "developer_name": "Alice (invited 2026-11-17)",
            "environment": "test",
            "monthly_limit": 7,
        },
        headers=MGMT,
    )
    # NOTE: never interpolate the response body here — it contains the raw key.
    check("P1a provisioning returns 201", r.status_code == 201, f"http={r.status_code}")
    body = r.json() if r.status_code == 201 else {}
    alice_key = body.get("key", "")
    alice_key_id = body.get("key_id", "")

    check(
        "P1b new tenant is distinct from termux-device",
        body.get("tenant_id") == "dev-alice" and body.get("tenant_id") != "termux-device",
        f"tenant={body.get('tenant_id')}",
    )
    check("P1c raw key returned exactly once at creation", alice_key.startswith("plx_test_"))
    check(
        "P1d quota row created with the requested limit",
        scalar(
            "SELECT monthly_limit FROM platrixa_tenant_quotas WHERE tenant_id=%s",
            t="dev-alice",
        ) == 7,
    )
    check(
        "P1e existing tenant rows unchanged",
        scalar("SELECT count(*) FROM platrixa_api_keys WHERE tenant_id=%s", t="termux-device") == 1,
    )

    print("\nB. AUTHENTICATION AND QUOTA")
    r = client.get("/v1/usage", headers={"x-platrixa-api-key": alice_key})
    usage = r.json() if r.status_code == 200 else {}
    check("P2 new key authenticates (/v1/usage 200)", r.status_code == 200, f"http={r.status_code}")
    check("P3 usage reports the new tenant", usage.get("tenant_id") == "dev-alice", f"tenant={usage.get('tenant_id')}")
    check("P4 usage reports the provisioned quota", usage.get("monthly_limit") == 7, f"limit={usage.get('monthly_limit')}")

    r = client.get("/v1/usage", headers={"x-platrixa-api-key": existing_key})
    check(
        "P5 existing tenant key still works",
        r.status_code == 200 and r.json().get("tenant_id") == "termux-device",
        f"http={r.status_code}",
    )

    print("\nC. JOB / RESULT ISOLATION")
    r_existing_job = client.post(
        "/v1/inference-jobs",
        json={"raw_input": "Paid rent 100 rupees in cash."},
        headers={"x-platrixa-api-key": existing_key, "Idempotency-Key": "iso-existing-20261117-1"},
    )
    existing_job_id = (r_existing_job.json() or {}).get("job_id", "") if r_existing_job.status_code == 202 else ""
    check(
        "P6 existing tenant can create an inference job",
        r_existing_job.status_code == 202 and bool(existing_job_id),
        f"http={r_existing_job.status_code} job={existing_job_id}",
    )

    if existing_job_id:
        r = client.get(f"/v1/jobs/{existing_job_id}", headers={"x-platrixa-api-key": alice_key})
        check(
            "P7 new key cannot read the existing tenant's job (404)",
            r.status_code == 404,
            f"http={r.status_code}",
        )
        r = client.get(f"/v1/results/{existing_job_id}", headers={"x-platrixa-api-key": alice_key})
        check(
            "P8 new key cannot retrieve the existing tenant's result (404)",
            r.status_code == 404,
            f"http={r.status_code}",
        )

    r_alice_job = client.post(
        "/v1/inference-jobs",
        json={"raw_input": "Paid rent 100 rupees in cash."},
        headers={"x-platrixa-api-key": alice_key, "Idempotency-Key": "iso-alice-20261117-1"},
    )
    alice_job_id = (r_alice_job.json() or {}).get("job_id", "") if r_alice_job.status_code == 202 else ""
    check("P9 new tenant can create its own job", r_alice_job.status_code == 202, f"http={r_alice_job.status_code}")
    if alice_job_id:
        r = client.get(f"/v1/jobs/{alice_job_id}", headers={"x-platrixa-api-key": existing_key})
        check(
            "P10 existing tenant cannot read the new tenant's job (404)",
            r.status_code == 404,
            f"http={r.status_code}",
        )

    print("\nD. USAGE / KEY-LIST ISOLATION")
    r = client.get("/v1/usage", headers={"x-platrixa-api-key": alice_key})
    check("P11 new key never sees another tenant's usage", r.json().get("tenant_id") == "dev-alice", f"tenant={r.json().get('tenant_id')}")
    alice_list = store.list_api_keys("dev-alice")
    check(
        "P12 cross-tenant key listing is impossible (store scope)",
        len(alice_list) == 1 and alice_list[0]["id"] == alice_key_id,
        f"keys={len(alice_list)}",
    )
    r = client.get("/v1/developer/api-keys", headers={**MGMT, "x-request-id": "prov-1"})
    listed_ids = [k["id"] for k in (r.json() or {}).get("keys", [])] if r.status_code == 200 else []
    check(
        "P13 management listing is bound to the owner tenant only",
        r.status_code == 200 and alice_key_id not in listed_ids,
        f"http={r.status_code} listed={len(listed_ids)}",
    )

    print("\nE. CROSS-TENANT REVOCATION")
    view, code = store.revoke_api_key("termux-device", alice_key_id)
    check(
        "P14 cross-tenant revocation is impossible",
        view is None and code == store.KEY_NOT_FOUND,
        f"code={code}",
    )
    r = client.get("/v1/usage", headers={"x-platrixa-api-key": alice_key})
    check("P15 rejected revocation leaves the key working", r.status_code == 200, f"http={r.status_code}")

    print("\nF. RAW KEY HANDLING")
    r = client.get("/v1/developer/api-keys", headers={**MGMT, "x-request-id": "prov-2"})
    payload = r.text
    check("P16 listing never contains raw or hashed key material", alice_key not in payload and "key_hash" not in payload)
    stored = scalar("SELECT key_hash FROM platrixa_api_keys WHERE id=%s", i=alice_key_id)
    check(
        "P17 database stores only the SHA-256 hash",
        stored == hashlib.sha256(alice_key.encode()).hexdigest() and stored != alice_key,
    )
    leaked = 0
    for table, col in (("platrixa_api_keys", "name"), ("platrixa_tenant_quotas", "tenant_id")):
        leaked += scalar(f"SELECT count(*) FROM {table} WHERE {col} LIKE %s", p=f"%{alice_key}%")
    check("P18 raw key appears in no text column", leaked == 0, f"matches={leaked}")

    print("\nG. REVOCATION")
    r = client.post(
        f"/v1/developer/tenants/dev-alice/keys/{alice_key_id}/revoke",
        headers={**MGMT, "x-request-id": "prov-3"},
    )
    check("P19 revoke returns 200", r.status_code == 200, f"http={r.status_code}")
    r = client.get("/v1/usage", headers={"x-platrixa-api-key": alice_key})
    check("P20 revoked key stops authenticating (401)", r.status_code == 401, f"http={r.status_code}")
    check(
        "P21 revocation deactivated the quota row atomically",
        scalar("SELECT is_active FROM platrixa_tenant_quotas WHERE tenant_id=%s", t="dev-alice") is False,
    )
    r = client.get("/v1/usage", headers={"x-platrixa-api-key": existing_key})
    check("P22 existing tenant unaffected by the revocation", r.status_code == 200, f"http={r.status_code}")

    print("\nH. DUPLICATES, QUOTA, ATOMICITY")
    r = client.post(
        "/v1/developer/tenants",
        json={"tenant_id": "dev-alice", "developer_name": "Alice again", "monthly_limit": 7},
        headers=MGMT,
    )
    check("P23 duplicate provisioning is rejected (409)", r.status_code == 409, f"http={r.status_code}")
    check(
        "P24 duplicate provisioning created no rows",
        scalar("SELECT count(*) FROM platrixa_api_keys WHERE tenant_id=%s", t="dev-alice") == 1,
    )

    r = client.post(
        "/v1/developer/tenants",
        json={"tenant_id": "dev-bob", "developer_name": "Bob", "monthly_limit": 1},
        headers=MGMT,
    )
    bob_key = (r.json() or {}).get("key", "")
    check("P25 second tenant provisioned", r.status_code == 201, f"http={r.status_code}")
    first = client.post("/v1/inference-jobs", json={"raw_input": "a"}, headers={"x-platrixa-api-key": bob_key})
    second = client.post("/v1/inference-jobs", json={"raw_input": "b"}, headers={"x-platrixa-api-key": bob_key})
    check(
        "P26 quota exhaustion returns 429",
        first.status_code == 202 and second.status_code == 429,
        f"first={first.status_code} second={second.status_code}",
    )
    check(
        "P27 refused admission created no orphan job",
        scalar("SELECT count(*) FROM platrixa_async_jobs WHERE tenant_id=%s", t="dev-bob") == 1,
    )

    original_gen = store.generate_raw_key
    existing_hash = scalar("SELECT key_hash FROM platrixa_api_keys WHERE tenant_id=%s", t="termux-device")
    try:
        # Force a REAL unique-constraint violation: reuse an existing key hash.
        store.generate_raw_key = lambda env: (
            "plx_test_collision",
            "plx_test_collisi",
            existing_hash,
        )
        failed = False
        try:
            store.provision_tenant("dev-carol", "Carol", "test", 10)
        except store.ApiKeyStoreError:
            failed = True
        finally:
            store.generate_raw_key = original_gen
        check("P28 failed provisioning fails closed", failed)
        check(
            "P29 rollback left no tenant, key or quota row",
            scalar("SELECT count(*) FROM platrixa_api_keys WHERE tenant_id=%s", t="dev-carol") == 0
            and scalar("SELECT count(*) FROM platrixa_tenant_quotas WHERE tenant_id=%s", t="dev-carol") == 0,
        )
    finally:
        store.generate_raw_key = original_gen

    print("\nI. AUTHORIZATION AND CONFIGURATION FAILURES")
    app.dependency_overrides.clear()
    r = client.post(
        "/v1/developer/tenants",
        json={"tenant_id": "dev-mallory", "developer_name": "Mallory"},
    )
    check("P30 provisioning without management authorization is 403", r.status_code == 403, f"http={r.status_code}")
    check(
        "P31 unauthorized request created no tenant",
        scalar("SELECT count(*) FROM platrixa_api_keys WHERE tenant_id=%s", t="dev-mallory") == 0,
    )

    if hasattr(dk_routes, "require_management"):
        app.dependency_overrides[dk_routes.require_management] = lambda: "termux-device"
    for bad in ("", "a", "Alice", "a b", "x" * 65, "dev_alice"):
        r = client.post(
            "/v1/developer/tenants",
            json={"tenant_id": bad, "developer_name": "Bad"},
            headers={**MGMT, "x-request-id": "prov-invalid"},
        )
        RESULTS.append((f"P32 invalid tenant id {bad[:12]!r} rejected", r.status_code in (400, 403, 422)))
        print(f"  [{'PASS' if r.status_code in (400, 403, 422) else 'FAIL'}] P32 invalid tenant id {bad[:12]!r} — http={r.status_code}")

    saved = os.environ.pop("PLATRIXA_METERING_DATABASE_URL")
    try:
        missing_config = False
        try:
            store.provision_tenant("dev-nodb", "No DB", "test", 10)
        except store.ApiKeyStoreError:
            missing_config = True
        check("P33 provisioning with no store configured fails closed", missing_config)
    finally:
        os.environ["PLATRIXA_METERING_DATABASE_URL"] = saved

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 72)
    print(f"RESULT: {passed}/{len(RESULTS)} checks passed")
    print("=" * 72)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
