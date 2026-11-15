#!/usr/bin/env python3
"""Operator-management authentication contract — regression evidence.

Exercises the REAL verifier (``validate_management_gateway_assertion``) and the
REAL operator route (``GET /v1/developer/api-keys``) against the REAL isolated
PostgreSQL nonce store. The visitor path is never involved: no visitor header
is ever sent, and the management token stays the only credential.

Covers:
  * issuer allowlist (exact match, missing/empty/malformed config fails closed)
  * signature / audience / timestamp / tenant binding regressions
  * durable nonce replay protection: sequential, concurrent, cross-issuer
  * fail-closed behaviour when the nonce store is unavailable
  * raw nonce is never persisted (only its SHA-256 digest)

Run (isolated store only):
  PLATRIXA_METERING_DATABASE_URL=postgresql://...test... \
  PLATRIXA_MANAGEMENT_ASSERTION_SECRET=... \
  PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS=... \
      python scripts/v2_management_assertion_test.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


MGMT_TOKEN = "v2-isolated-management-token"
MGMT_TENANT = "v2-mgmt-tenant"
SECRET = "v2-isolated-management-assertion-secret"
# The issuer established by the operator gateway (PLATRIXA_FINAL_SECURITY_AUDIT_REPORT.md:
# "Issuer and version: platrixa-operator-v1").
ISSUER = "platrixa-operator-v1"
ALT_ISSUER = "platrixa-operator-v1-alt"  # test-only, to prove namespace separation
AUDIENCE = "platrixa-management"
ROUTE = "/v1/developer/api-keys"


def canonical(issuer: str, audience: str, tenant: str, method: str, path: str,
              query: str, content_type: str, body_hash: str, ts: str, nonce: str) -> str:
    return "|".join([issuer, audience, tenant, method, path, query,
                     content_type, body_hash, ts, nonce])


def signed(*, issuer: str = ISSUER, audience: str = AUDIENCE, tenant: str = MGMT_TENANT,
           method: str = "GET", path: str = ROUTE, query: str = "",
           content_type: str = "", body_hash: str = hashlib.sha256(b"").hexdigest(),
           ts: str | None = None,
           nonce: str | None = None, secret: str = SECRET) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    nonce = nonce or f"mgmt-{uuid.uuid4().hex}"
    canon = canonical(issuer, audience, tenant, method, path, query, content_type, body_hash, ts, nonce)
    sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    return {
        "x-platrixa-management-token": MGMT_TOKEN,
        "x-platrixa-assertion-signature": sig,
        "x-platrixa-assertion-issuer": issuer,
        "x-platrixa-assertion-audience": audience,
        "x-platrixa-assertion-tenant-id": tenant,
        "x-platrixa-assertion-method": method,
        "x-platrixa-assertion-path": path,
        "x-platrixa-assertion-query-string": query,
        "x-platrixa-assertion-content-type": content_type,
        "x-platrixa-assertion-body-hash": body_hash,
        "x-platrixa-assertion-timestamp": ts,
        "x-platrixa-assertion-nonce": nonce,
    }


def verify(headers: dict[str, str]):
    from backend.auth.api_keys import validate_management_gateway_assertion

    return validate_management_gateway_assertion(
        signature=headers["x-platrixa-assertion-signature"],
        issuer=headers["x-platrixa-assertion-issuer"],
        audience=headers["x-platrixa-assertion-audience"],
        tenant_id=headers["x-platrixa-assertion-tenant-id"],
        method=headers["x-platrixa-assertion-method"],
        path=headers["x-platrixa-assertion-path"],
        query_string=headers["x-platrixa-assertion-query-string"],
        content_type=headers["x-platrixa-assertion-content-type"],
        body_hash=headers["x-platrixa-assertion-body-hash"],
        timestamp=headers["x-platrixa-assertion-timestamp"],
        nonce=headers["x-platrixa-assertion-nonce"],
        secret=SECRET,
    )


def _bound_headers(method: str, path: str, *, body: bytes = b"", content_type: str = "",
                   query: str = "", tenant: str = MGMT_TENANT, ts: str | None = None,
                   nonce: str | None = None,
                   sig: str | None = None) -> dict[str, str]:
    """Assertion headers bound to the ACTUAL request that will be sent."""
    ts = ts or str(int(time.time()))
    nonce = nonce or f"mgmt-{uuid.uuid4().hex}"
    body_hash = hashlib.sha256(body).hexdigest()
    canon = canonical(ISSUER, AUDIENCE, tenant, method, path, query, content_type,
                      body_hash, ts, nonce)
    return {
        "x-platrixa-management-token": MGMT_TOKEN,
        "x-platrixa-assertion-signature": sig or hmac.new(
            SECRET.encode(), canon.encode(), hashlib.sha256
        ).hexdigest(),
        "x-platrixa-assertion-issuer": ISSUER,
        "x-platrixa-assertion-audience": AUDIENCE,
        "x-platrixa-assertion-tenant-id": tenant,
        "x-platrixa-assertion-method": method,
        "x-platrixa-assertion-path": path,
        "x-platrixa-assertion-query-string": query,
        "x-platrixa-assertion-content-type": content_type,
        "x-platrixa-assertion-body-hash": body_hash,
        "x-platrixa-assertion-timestamp": ts,
        "x-platrixa-assertion-nonce": nonce,
    }


def endpoint_matrix(client) -> None:
    """Actual HTTP status/body checks for every management endpoint."""
    print("\n--- endpoint authorization matrix (actual HTTP requests) ---")
    body = b'{"name":"matrix-key","environment":"test"}'
    json_ct = "application/json"

    _create_headers = _bound_headers("POST", ROUTE, body=body, content_type=json_ct)
    _create_headers["content-type"] = json_ct
    r = client.post(ROUTE, headers=_create_headers, content=body)
    check("matrix: bound assertion create succeeds", r.status_code == 201, f"{r.status_code} {r.text[:80]}")
    key_id = r.json().get("id", "")

    endpoints = [
        ("POST", ROUTE, body, json_ct),
        ("GET", ROUTE, b"", ""),
        ("POST", f"{ROUTE}/{key_id}/revoke", b"", ""),
        ("POST", f"{ROUTE}/{key_id}/rotate", b"", ""),
        ("GET", f"{ROUTE}/{key_id}/usage", b"", ""),
        ("GET", "/v1/developer/usage", b"", ""),
        ("GET", "/v1/developer/requests", b"", ""),
        ("GET", "/v1/developer/requests/11111111-1111-1111-1111-111111111111", b"", ""),
    ]

    # 1. static token alone is never sufficient on any management endpoint
    for method, path, payload, ct in endpoints:
        r = client.request(method, path, headers={"x-platrixa-management-token": MGMT_TOKEN},
                           content=payload or None)
        check(f"token-only rejected: {method} {path}", r.status_code == 403, str(r.status_code))

    # 2. partial assertion headers are rejected, never downgraded
    for drop in ("x-platrixa-assertion-audience", "x-platrixa-assertion-signature",
                 "x-platrixa-assertion-nonce", "x-platrixa-assertion-body-hash"):
        h = _bound_headers("GET", ROUTE)
        h.pop(drop)
        r = client.get(ROUTE, headers=h)
        check(f"partial headers rejected (missing {drop.split('-')[-1]})",
              r.status_code == 403, str(r.status_code))

    # 3. binding: signed for another method / path / query / content-type / body
    r = client.get(ROUTE, headers=_bound_headers("DELETE", ROUTE))
    check("wrong METHOD rejected", r.status_code == 403, str(r.status_code))
    r = client.get(ROUTE, headers=_bound_headers("GET", "/v1/developer/other"))
    check("wrong PATH rejected", r.status_code == 403, str(r.status_code))
    r = client.get("/v1/developer/requests", headers=_bound_headers(
        "GET", "/v1/developer/requests", query="limit=2"))
    check("wrong QUERY rejected", r.status_code == 403, str(r.status_code))
    _wrong_ct = _bound_headers("POST", ROUTE, body=body, content_type="text/plain")
    _wrong_ct["content-type"] = json_ct
    r = client.post(ROUTE, headers=_wrong_ct, content=body)
    check("wrong CONTENT-TYPE rejected", r.status_code == 403, str(r.status_code))
    _wrong_body = _bound_headers("POST", ROUTE, body=b'{"name":"other"}', content_type=json_ct)
    _wrong_body["content-type"] = json_ct
    r = client.post(ROUTE, headers=_wrong_body, content=body)
    check("wrong BODY rejected", r.status_code == 403, str(r.status_code))

    # 4. tenant, replay
    r = client.get(ROUTE, headers=_bound_headers("GET", ROUTE, tenant="other-tenant"))
    check("wrong TENANT rejected", r.status_code == 403, str(r.status_code))

    h = _bound_headers("GET", ROUTE)
    r1 = client.get(ROUTE, headers=h)
    r2 = client.get(ROUTE, headers=h)
    check("replayed nonce: first accepted", r1.status_code == 200, str(r1.status_code))
    check("replayed nonce: second rejected", r2.status_code == 403, str(r2.status_code))

    # 5. observability endpoints accept the SAME bound assertion contract
    for path in ("/v1/developer/usage", "/v1/developer/requests"):
        r = client.get(path, headers=_bound_headers("GET", path))
        check(f"bound assertion accepted: GET {path}", r.status_code == 200, str(r.status_code))
    rid = "11111111-1111-1111-1111-111111111111"
    path = f"/v1/developer/requests/{rid}"
    r = client.get(path, headers=_bound_headers("GET", path))
    check("bound assertion accepted: GET request detail (404 for unknown id)",
          r.status_code == 404, str(r.status_code))

    # 6. nonce store outage fails closed with 503, not a degraded accept
    saved = os.environ["PLATRIXA_METERING_DATABASE_URL"]
    os.environ["PLATRIXA_METERING_DATABASE_URL"] = "postgresql://postgres:@127.0.0.1:1/x"
    try:
        r = client.get(ROUTE, headers=_bound_headers("GET", ROUTE))
    finally:
        os.environ["PLATRIXA_METERING_DATABASE_URL"] = saved
    check("nonce store outage fails closed (503)", r.status_code == 503, str(r.status_code))



def main() -> int:
    db_url = (os.getenv("PLATRIXA_METERING_DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("PLATRIXA_METERING_DATABASE_URL is not set")

    os.environ["PLATRIXA_KEY_MANAGEMENT_TOKEN"] = MGMT_TOKEN
    os.environ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID"] = MGMT_TENANT
    os.environ["PLATRIXA_MANAGEMENT_ASSERTION_SECRET"] = SECRET
    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = ISSUER

    from fastapi.testclient import TestClient

    from api.main import create_app
    from backend.auth.api_keys import (
        MGMT_INVALID,
        MGMT_NONCE_UNAVAILABLE,
        MGMT_OK,
        MGMT_REPLAY,
    )
    from backend.auth.init_management_nonce import initialise_management_nonce_schema

    if not initialise_management_nonce_schema():
        raise SystemExit("management nonce schema could not be initialised")

    # The usage endpoints read the Phase 16 quota table; apply its DDL to the
    # same disposable store (the lifecycle suite does the same explicitly).
    from sqlalchemy import create_engine, text

    _engine = create_engine(
        db_url.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    _quota_ddl = (
        ROOT / "backend" / "database" / "platrixa_tenant_quota_schema.sql"
    ).read_text(encoding="utf-8")
    with _engine.begin() as _conn:
        _conn.execute(text(_quota_ddl))

    client = TestClient(create_app())

    # ---------------------------------------------------------------- route
    h = signed()
    r = client.get(ROUTE, headers=h)
    check("valid signed management assertion is accepted by the caller", r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")

    # ------------------------------------------------- allowlist / issuer
    reason, tenant = verify(signed())
    check("configured legitimate issuer succeeds",
          reason == MGMT_OK and tenant == MGMT_TENANT, f"{reason}/{tenant}")

    reason, _ = verify(signed(issuer="unknown-gateway"))
    check("unknown issuer fails", reason == MGMT_INVALID, reason)

    no_issuer = signed()
    no_issuer["x-platrixa-assertion-issuer"] = ""
    reason, _ = verify(no_issuer)
    check("missing issuer fails", reason == MGMT_INVALID, reason)

    empty_issuer = signed()
    empty_issuer["x-platrixa-assertion-issuer"] = "   "
    reason, _ = verify(empty_issuer)
    check("empty issuer fails", reason == MGMT_INVALID, reason)

    reason, _ = verify(signed(issuer="platrixa-operator-v1-extra"))
    check("issuer mismatch (prefix of trusted value) fails", reason == MGMT_INVALID, reason)

    os.environ.pop("PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS", None)
    reason, _ = verify(signed())
    check("missing allowlist fails closed", reason == MGMT_INVALID, reason)

    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = "   "
    reason, _ = verify(signed())
    check("empty allowlist fails closed", reason == MGMT_INVALID, reason)

    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = "platrixa-operator-v1, bad issuer!"
    reason, _ = verify(signed())
    check("malformed allowlist fails closed", reason == MGMT_INVALID, reason)
    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = ISSUER

    # ------------------------------------- unchanged enforcement (regression)
    bad_sig = signed()
    bad_sig["x-platrixa-assertion-signature"] = "0" * 64
    check("invalid signature still rejected", verify(bad_sig)[0] == MGMT_INVALID)

    bad_aud = signed(audience="some-other-audience")
    check("wrong audience still rejected", verify(bad_aud)[0] == MGMT_INVALID)

    expired = signed(ts=str(int(time.time()) - 301))
    check("expired timestamp still rejected", verify(expired)[0] == MGMT_INVALID)

    future = signed(ts=str(int(time.time()) + 301))
    check("future timestamp still rejected", verify(future)[0] == MGMT_INVALID)

    r = client.get(ROUTE, headers=signed(tenant="some-other-tenant"))
    check("assertion tenant must match the operator binding (route)", r.status_code == 403,
          f"status={r.status_code}")

    # --------------------------------------------------------- nonce replay
    nonce = f"mgmt-replay-{uuid.uuid4().hex}"
    first = verify(signed(nonce=nonce))
    second = verify(signed(nonce=nonce))
    check("first valid request succeeds", first[0] == MGMT_OK, first[0])
    check("sequential replay is rejected", second[0] == MGMT_REPLAY, second[0])

    race_nonce = f"mgmt-race-{uuid.uuid4().hex}"
    race = signed(nonce=race_nonce)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        outcome = verify(race)[0]
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent replay allows exactly one success",
          outcomes.count(MGMT_OK) == 1 and outcomes.count(MGMT_REPLAY) == 7,
          f"ok={outcomes.count(MGMT_OK)} replay={outcomes.count(MGMT_REPLAY)}")

    missing_nonce = signed()
    missing_nonce["x-platrixa-assertion-nonce"] = ""
    check("missing nonce is rejected", verify(missing_nonce)[0] == MGMT_INVALID)

    short_nonce = f"short-{uuid.uuid4().hex[:4]}"
    check("malformed (too short) nonce is rejected",
          verify(signed(nonce=short_nonce))[0] == MGMT_INVALID, short_nonce)

    # invalid assertions must not consume the nonce
    burns = f"mgmt-burn-{uuid.uuid4().hex}"
    invalid = signed(nonce=burns)
    invalid["x-platrixa-assertion-signature"] = "f" * 64
    invalid_reason = verify(invalid)[0]
    valid_after = verify(signed(nonce=burns))
    check("an invalid assertion does not consume the nonce",
          invalid_reason == MGMT_INVALID and valid_after[0] == MGMT_OK,
          f"invalid={invalid_reason} valid={valid_after[0]}")

    # --------------------------------------------------- issuer namespaces
    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = f"{ISSUER},{ALT_ISSUER}"
    shared_nonce = f"mgmt-ns-{uuid.uuid4().hex}"
    a = verify(signed(issuer=ISSUER, nonce=shared_nonce))
    b = verify(signed(issuer=ALT_ISSUER, nonce=shared_nonce))
    a_again = verify(signed(issuer=ISSUER, nonce=shared_nonce))
    check("different issuers have separate nonce namespaces",
          a[0] == MGMT_OK and b[0] == MGMT_OK and a_again[0] == MGMT_REPLAY,
          f"a={a[0]} b={b[0]} a_again={a_again[0]}")
    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = ISSUER

    # ---------------------------------------------------- store fail-closed
    saved = os.environ["PLATRIXA_METERING_DATABASE_URL"]
    os.environ["PLATRIXA_METERING_DATABASE_URL"] = (
        "postgresql://postgres:@/nope?host=/tmp/nonexistent-mgmt-nonce"
    )
    try:
        down = verify(signed(nonce=f"mgmt-down-{uuid.uuid4().hex}"))
    finally:
        os.environ["PLATRIXA_METERING_DATABASE_URL"] = saved
    check("unavailable nonce storage fails closed",
          down[0] == MGMT_NONCE_UNAVAILABLE and down[1] is None, down[0])

    # ------------------------------------------- storage shape (no raw nonce)
    from sqlalchemy import create_engine, text

    raw_nonce = f"mgmt-rawcheck-{uuid.uuid4().hex}"
    verify(signed(nonce=raw_nonce))
    engine = create_engine(
        saved.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        raw_rows = conn.execute(
            text("SELECT count(*) FROM management_assertion_nonces WHERE nonce_hash = :n"),
            {"n": raw_nonce},
        ).scalar_one()
        digest_rows = conn.execute(
            text("SELECT count(*) FROM management_assertion_nonces WHERE nonce_hash = :n"),
            {"n": hashlib.sha256(raw_nonce.encode()).hexdigest()},
        ).scalar_one()
    check("raw nonce is never stored; only its SHA-256 digest",
          raw_rows == 0 and digest_rows == 1, f"raw={raw_rows} digest={digest_rows}")

    endpoint_matrix(client)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} management-assertion checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
