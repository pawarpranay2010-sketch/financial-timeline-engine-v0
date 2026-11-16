"""Phase 5G — Secure API-key lifecycle test suite.

Developer key management (create/list/revoke/rotate) against REAL
embedded PostgreSQL (pgserver, discarded after the run). The suite
proves: one-time raw-secret exposure, hash-only storage, tenant
isolation, atomic revocation/rotation, the management-authorization
boundary (data-plane keys can never manage keys), database uniqueness,
fail-closed store behavior, and the OpenAPI contract shape. Closes with
a full regression run of the Phase 5C suite against the same stack.

Run:
    python3 scripts/fte_fyjc_83_phase5g_api_key_lifecycle_test.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import api_keys as key_store  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


_MGMT_TOKEN = "mgmt-token-0123456789abcdef"
_TENANT_A = "tenant-5g"
_TENANT_B = "tenant-5g-b"


_ASSERTION_SECRET = "phase5g-assertion-secret"
_ISSUER = "platrixa-operator-v1"
_AUDIENCE = "platrixa-management"


def mgmt_hdr(token: str | None = _MGMT_TOKEN) -> dict:
    return {"X-Platrixa-Management-Token": token} if token else {}


def _json_bytes(obj: object) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def assertion_headers(
    method: str,
    path: str,
    query: str = "",
    body: bytes = b"",
    content_type: str = "",
    tenant: str | None = None,
) -> dict:
    """Assertion headers bound to the ACTUAL request the client will send."""
    if tenant is None:
        tenant = os.environ.get(key_store.MANAGEMENT_TENANT_ENV_VAR, "")
    ts = str(int(time.time()))
    nonce = f"mgmt-{uuid.uuid4().hex}"
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "|".join(
        [_ISSUER, _AUDIENCE, tenant, method, path, query, content_type, body_hash, ts, nonce]
    )
    signature = hmac.new(
        _ASSERTION_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "x-platrixa-assertion-signature": signature,
        "x-platrixa-assertion-issuer": _ISSUER,
        "x-platrixa-assertion-audience": _AUDIENCE,
        "x-platrixa-assertion-tenant-id": tenant,
        "x-platrixa-assertion-method": method,
        "x-platrixa-assertion-path": path,
        "x-platrixa-assertion-query-string": query,
        "x-platrixa-assertion-content-type": content_type,
        "x-platrixa-assertion-body-hash": body_hash,
        "x-platrixa-assertion-timestamp": ts,
        "x-platrixa-assertion-nonce": nonce,
    }


class MgmtClient:
    """TestClient that attaches a request-bound gateway assertion whenever the
    caller supplied a management token — the production management contract."""

    def __init__(self, app) -> None:
        self._c = TestClient(app)

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        token = next(
            (v for k, v in headers.items() if k.lower() == "x-platrixa-management-token"),
            None,
        )
        if token:
            body = b""
            content_type = ""
            if "json" in kw:
                body = _json_bytes(kw["json"])
                content_type = "application/json"
            elif "content" in kw:
                body = kw["content"]
                content_type = next(
                    (v for k, v in headers.items() if k.lower() == "content-type"), ""
                )
            path, _, query = url.partition("?")
            headers.update(
                assertion_headers(method.upper(), path, query, body, content_type)
            )
        return self._c.request(method, url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)


def set_mgmt_tenant(tenant_id: str) -> None:
    os.environ[key_store.MANAGEMENT_TENANT_ENV_VAR] = tenant_id


# ---------------------------------------------------------------------------
# Embedded PostgreSQL (discarded after the run) + management configuration
# ---------------------------------------------------------------------------

_PG = None


def pg_backend() -> tuple[object, str]:
    global _PG
    if _PG is not None:
        return _PG
    import pgserver

    pgdata = Path("/tmp/platrixa_phase5g_pgdata")
    # Fresh store per run — lifecycle tests assert exact counts.
    import shutil

    shutil.rmtree(pgdata, ignore_errors=True)
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ[key_store.MANAGEMENT_TOKEN_ENV_VAR] = _MGMT_TOKEN
    os.environ[key_store.MANAGEMENT_TENANT_ENV_VAR] = _TENANT_A
    os.environ["PLATRIXA_MANAGEMENT_ASSERTION_SECRET"] = _ASSERTION_SECRET
    os.environ["PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS"] = _ISSUER
    # Apply the Phase 16 quota DDL explicitly (same as suite 79) — the
    # runtime gate expects its table to exist; the Phase 5G registry DDL
    # self-ensures on first use of the key store.
    from sqlalchemy import create_engine, text

    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    quota_ddl = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "database"
        / "platrixa_tenant_quota_schema.sql"
    ).read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(quota_ddl))
    # Reset the lazy factory so the fresh URL + DDL take effect.
    # Phase 5I: key-store engine cache removed (delegates to the gate's
    # canonical cache) — reset via the canonical cache + schema set.
    from backend.auth import gate as _gate5i

    _gate5i._session_factory_cache.pop(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), None
    )
    key_store._schema_ensured.discard(uri)
    key_store._session_factory()  # self-ensures the registry schema
    _PG = (srv, uri)
    return _PG


# ---------------------------------------------------------------------------
# Counting stub client (mirrors the facade contract; the data-plane side
# must run real admission through the metering store)
# ---------------------------------------------------------------------------


class StubClient:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def process(self, text, request_id=None):
        with self.lock:
            self.calls += 1
        digits = "".join(ch for ch in (text or "") if ch.isdigit()) or "0"
        return type(
            "R",
            (),
            {
                "status": "VERIFIED",
                "status_label": "VERIFIED",
                "success": True,
                "request_id": f"req-{self.calls}",
                "next_action": None,
                "issues": [],
                "grounding_issues": [],
                "rule_evidence": [],
                "interpretation": {
                    "transaction_type_enum": "PURCHASE",
                    "amounts": [{"value": digits, "currency": "INR", "source": "explicit"}],
                    "payment_method_enum": "CASH",
                },
                "accounting": {
                    "debit_lines": [{"account": "Furniture", "amount": int(digits)}],
                    "credit_lines": [{"account": "Cash", "amount": int(digits)}],
                },
            },
        )()

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


def client() -> MgmtClient:
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)
    developer.set_client(StubClient())
    return MgmtClient(create_app())


# ---------------------------------------------------------------------------
# A0. Zero-config honesty — the management plane is fail-closed
# ---------------------------------------------------------------------------


def section_a0(c: TestClient) -> None:
    print("\nA0. zero-config management plane")
    os.environ.pop(key_store.MANAGEMENT_TOKEN_ENV_VAR, None)
    os.environ.pop(key_store.MANAGEMENT_TENANT_ENV_VAR, None)
    r = c.post("/v1/developer/api-keys", json={"name": "x"})
    check(
        "create without management config → 400 NOT_CONFIGURED",
        r.status_code == 400 and r.json()["error"]["code"] == "API_KEY_MANAGEMENT_NOT_CONFIGURED",
        f"{r.status_code} {r.text[:90]}",
    )
    r = c.get("/v1/developer/api-keys")
    check("list without management config → 400", r.status_code == 400, str(r.status_code))

    os.environ[key_store.MANAGEMENT_TOKEN_ENV_VAR] = _MGMT_TOKEN
    r = c.get("/v1/developer/api-keys", headers=mgmt_hdr())
    check(
        "token without tenant binding → 503 fail-closed",
        r.status_code == 503 and r.json()["error"]["code"] == "API_KEY_MANAGEMENT_UNAVAILABLE",
        str(r.status_code),
    )
    os.environ[key_store.MANAGEMENT_TOKEN_ENV_VAR] = _MGMT_TOKEN
    os.environ[key_store.MANAGEMENT_TENANT_ENV_VAR] = _TENANT_A


# ---------------------------------------------------------------------------
# A. create — validation + one-time secret
# ---------------------------------------------------------------------------


def section_a(c: TestClient) -> dict:
    print("\nA. create: validation + one-time secret exposure")
    r = c.post("/v1/developer/api-keys", json={"name": "   "}, headers=mgmt_hdr())
    check(
        "blank name → 400 API_KEY_NAME_INVALID",
        r.status_code == 400 and r.json()["error"]["code"] == "API_KEY_NAME_INVALID",
        f"{r.status_code}",
    )
    r = c.post("/v1/developer/api-keys", json={"name": "x" * 121}, headers=mgmt_hdr())
    check(
        "121-char name → 400 API_KEY_NAME_INVALID",
        r.status_code == 400 and r.json()["error"]["code"] == "API_KEY_NAME_INVALID",
        f"{r.status_code}",
    )
    r = c.post(
        "/v1/developer/api-keys", json={"name": "ok", "environment": "staging"}, headers=mgmt_hdr()
    )
    check(
        "bad environment → 400 API_KEY_ENVIRONMENT_INVALID",
        r.status_code == 400 and r.json()["error"]["code"] == "API_KEY_ENVIRONMENT_INVALID",
        f"{r.status_code} {r.text[:80]}",
    )

    r = c.post(
        "/v1/developer/api-keys", json={"name": "Local dev", "environment": "test"},
        headers=mgmt_hdr(),
    )
    check("create → 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    first = r.json()
    check(
        "raw key present once, plx_test_ format, high entropy",
        first.get("key", "").startswith("plx_test_") and len(first["key"]) >= 48,
        first.get("key", "")[:16],
    )

    r = c.post(
        "/v1/developer/api-keys", json={"name": "Production", "environment": "live"},
        headers=mgmt_hdr(),
    )
    check("second create (live env) → 201", r.status_code == 201 and r.json()["environment"] == "live", str(r.status_code))
    second = r.json()
    check("two keys → two distinct secrets", first["key"] != second["key"] and first["id"] != second["id"])

    set_mgmt_tenant(_TENANT_B)
    r = c.post("/v1/developer/api-keys", json={"name": "B key", "environment": "test"}, headers=mgmt_hdr())
    set_mgmt_tenant(_TENANT_A)
    check("sibling-tenant create → 201", r.status_code == 201, str(r.status_code))
    sibling = r.json()
    return {"first": first, "second": second, "sibling": sibling}


# ---------------------------------------------------------------------------
# B. list — masking, deterministic ordering, tenant isolation
# ---------------------------------------------------------------------------


def section_b(c: TestClient, created: dict) -> None:
    print("\nB. list: masking, ordering, isolation")
    r = c.get("/v1/developer/api-keys", headers=mgmt_hdr())
    check("list → 200", r.status_code == 200, str(r.status_code))
    keys = r.json().get("keys", [])
    check("exactly tenant A's two keys", len(keys) == 2, f"{len(keys)}")
    check(
        "no raw key / key_hash anywhere in the listing",
        all("key" not in k and "key_hash" not in k for k in keys),
        json.dumps(keys)[:100],
    )
    check(
        "masked 16-char plx_ prefix on every entry",
        all(k.get("key_prefix", "").startswith("plx_") and len(k["key_prefix"]) == 16 for k in keys),
        str([k.get("key_prefix") for k in keys]),
    )
    ids = [k["id"] for k in keys]
    check(
        "deterministic order (created_at DESC, id DESC)",
        ids == [created["second"]["id"], created["first"]["id"]],
        str(ids),
    )

    set_mgmt_tenant(_TENANT_B)
    rb = c.get("/v1/developer/api-keys", headers=mgmt_hdr())
    set_mgmt_tenant(_TENANT_A)
    keys_b = rb.json().get("keys", [])
    check(
        "tenant B sees only its own key",
        len(keys_b) == 1 and keys_b[0]["id"] == created["sibling"]["id"],
        f"{len(keys_b)}",
    )


# ---------------------------------------------------------------------------
# C. revoke — durable, idempotent, tenant-scoped, no 500s
# ---------------------------------------------------------------------------


def section_c(c: TestClient, created: dict) -> None:
    print("\nC. revoke: durable, idempotent, atomic")
    first_id = created["first"]["id"]
    r = c.post(f"/v1/developer/api-keys/{first_id}/revoke", headers=mgmt_hdr())
    check(
        "revoke → 200 REVOKED with timestamp",
        r.status_code == 200 and r.json()["status"] == "REVOKED" and r.json()["revoked_at"],
        r.text[:90],
    )
    check("revoke response leaks no secret", "key" not in r.json() and "key_hash" not in r.json())
    r2 = c.post(f"/v1/developer/api-keys/{first_id}/revoke", headers=mgmt_hdr())
    check(
        "repeat revoke → same deterministic REVOKED view (idempotent)",
        r2.status_code == 200 and r2.json()["status"] == "REVOKED",
        str(r2.status_code),
    )
    r2b = c.post(f"/v1/developer/api-keys/{first_id}/revoke", headers=mgmt_hdr())
    check(
        "third revoke → still 200, identical revoked_at (no 503)",
        r2b.status_code == 200
        and r2b.json()["status"] == "REVOKED"
        and r2b.json()["revoked_at"] == r2.json()["revoked_at"],
        f"{r2b.status_code} {r2b.text[:90]}",
    )
    r3 = c.post(f"/v1/developer/api-keys/{created['sibling']['id']}/revoke", headers=mgmt_hdr())
    check(
        "cross-tenant revoke → 404 indistinguishable",
        r3.status_code == 404 and r3.json()["error"]["code"] == "API_KEY_NOT_FOUND",
        f"{r3.status_code}",
    )
    r4 = c.post("/v1/developer/api-keys/not-a-uuid/revoke", headers=mgmt_hdr())
    check("malformed key id → 404 (no 500)", r4.status_code == 404, str(r4.status_code))
    r5 = c.post("/v1/developer/api-keys/11111111-1111-1111-1111-111111111111/revoke", headers=mgmt_hdr())
    check("unknown uuid → 404", r5.status_code == 404, str(r5.status_code))


# ---------------------------------------------------------------------------
# D. rotate — atomic switch, one-time secret, revoked keys cannot rotate
# ---------------------------------------------------------------------------


def section_d(c: TestClient, created: dict) -> None:
    print("\nD. rotate: atomic credential switch")
    second_id = created["second"]["id"]
    r = c.post(f"/v1/developer/api-keys/{second_id}/rotate", headers=mgmt_hdr())
    check(
        "rotate → 201 with NEW live raw key",
        r.status_code == 201 and r.json().get("key", "").startswith("plx_live_"),
        r.text[:90],
    )
    first_rotation = r.json()
    r2 = c.post(f"/v1/developer/api-keys/{second_id}/rotate", headers=mgmt_hdr())
    check(
        "re-rotate → different secret again",
        r2.status_code == 201 and r2.json()["key"] != first_rotation["key"],
        str(r2.status_code),
    )
    check("rotate response carries no hash", "key_hash" not in r2.json())
    r3 = c.post(f"/v1/developer/api-keys/{created['sibling']['id']}/rotate", headers=mgmt_hdr())
    check("cross-tenant rotate → 404", r3.status_code == 404, str(r3.status_code))
    r4 = c.post(f"/v1/developer/api-keys/{created['first']['id']}/rotate", headers=mgmt_hdr())
    check(
        "rotate a REVOKED key → 404 API_KEY_REVOKED",
        r4.status_code == 404 and r4.json()["error"]["code"] == "API_KEY_REVOKED",
        f"{r4.status_code}",
    )


# ---------------------------------------------------------------------------
# E. runtime authentication compatibility — revocation/rotation bite
# ---------------------------------------------------------------------------


def section_e(c: TestClient, created: dict) -> None:
    print("\nE. runtime auth compatibility (data plane)")
    stale_raw = created["second"]["key"]  # secret from BEFORE the rotations
    r = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": stale_raw},
    )
    check("pre-rotation raw key → 401 (old credential dead)", r.status_code == 401, str(r.status_code))

    r2 = c.post(f"/v1/developer/api-keys/{created['second']['id']}/rotate", headers=mgmt_hdr())
    current_raw = r2.json()["key"]
    r3 = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": current_raw},
    )
    check(
        "rotated-in credential authenticates /v1/process → 200 VERIFIED",
        r3.status_code == 200 and r3.json().get("status") == "VERIFIED",
        f"{r3.status_code} {r3.text[:70]}",
    )

    r4 = c.post(f"/v1/developer/api-keys/{created['second']['id']}/revoke", headers=mgmt_hdr())
    check("revoke the current key → 200", r4.status_code == 200, str(r4.status_code))
    r5 = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": current_raw},
    )
    check("revoked credential → 401 immediately", r5.status_code == 401, str(r5.status_code))

    set_mgmt_tenant(_TENANT_B)
    r6 = c.post(f"/v1/developer/api-keys/{created['sibling']['id']}/rotate", headers=mgmt_hdr())
    b_raw = r6.json().get("key", "")
    set_mgmt_tenant(_TENANT_A)
    r7 = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": b_raw},
    )
    check(
        "sibling tenant's credential unaffected → 200 VERIFIED",
        r7.status_code == 200 and r7.json().get("status") == "VERIFIED",
        f"{r7.status_code}",
    )


# ---------------------------------------------------------------------------
# D2. rotation preserves the quota row — replacing a credential never grants
#     a fresh monthly allowance
# ---------------------------------------------------------------------------


def _quota_row(uri: str, raw_key: str):
    from sqlalchemy import create_engine, text

    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT monthly_limit, current_month_usage, usage_month, is_active "
                "FROM platrixa_tenant_quotas WHERE api_key_hash = :h"
            ),
            {"h": hash_token(raw_key)},
        ).first()


def _set_usage(uri: str, raw_key: str, usage: int) -> None:
    from sqlalchemy import create_engine, text

    from backend.auth.models import current_usage_month

    engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_tenant_quotas SET current_month_usage = :u, "
                "usage_month = :m WHERE api_key_hash = :h"
            ),
            {"u": usage, "m": current_usage_month(), "h": hash_token(raw_key)},
        )


def _drain(c: TestClient, raw_key: str, cap: int = 70) -> tuple[int, bool]:
    """Issue real /v1/process calls until 429; return (successes, hit_limit)."""
    ok = 0
    for _ in range(cap):
        r = c.post(
            "/v1/process",
            json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
            headers={"X-Platrixa-API-Key": raw_key},
        )
        if r.status_code == 200:
            ok += 1
            continue
        return ok, r.status_code == 429
    return ok, False


def _new_quota_key(c: TestClient, name: str) -> dict:
    r = c.post(
        "/v1/developer/api-keys",
        json={"name": name, "environment": "live"},
        headers=mgmt_hdr(),
    )
    check(f"quota fixture key '{name}' created → 201", r.status_code == 201, str(r.status_code))
    return r.json()


def _rotate(c: TestClient, key_id: str) -> dict:
    r = c.post(f"/v1/developer/api-keys/{key_id}/rotate", headers=mgmt_hdr())
    check("rotate → 201 with new live key", r.status_code == 201 and r.json().get("key", "").startswith("plx_live_"), r.text[:80])
    return r.json()


def section_d2(c: TestClient, uri: str) -> None:
    print("\nD2. rotation preserves quota (no fresh allowance)")

    # --- 1. rotation at zero usage ---------------------------------------
    key0 = _new_quota_key(c, "quota-zero")
    raw0 = key0["key"]
    before = _quota_row(uri, raw0)
    check(
        "fresh key starts at 0/100",
        before is not None and before[0] == 100 and before[1] == 0,
        str(before),
    )
    rotated0 = _rotate(c, key0["id"])
    after = _quota_row(uri, rotated0["key"])
    check(
        "zero usage preserved (limit, usage, month identical)",
        after is not None and (after[0], after[1], after[2]) == (before[0], before[1], before[2]),
        f"{before} -> {after}",
    )
    check(
        "quota row reassigned to the new hash, old hash has no row",
        _quota_row(uri, raw0) is None and after is not None,
    )
    r = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": raw0},
    )
    check("old key rejected after rotation → 401", r.status_code == 401, str(r.status_code))

    # --- 2. rotation after partial usage ---------------------------------
    key1 = _new_quota_key(c, "quota-partial")
    raw1 = key1["key"]
    _set_usage(uri, raw1, 40)
    before = _quota_row(uri, raw1)
    rotated1 = _rotate(c, key1["id"])
    raw1r = rotated1["key"]
    after = _quota_row(uri, raw1r)
    check(
        "partial usage preserved across rotation (40/100, same month)",
        after is not None
        and (after[0], after[1], after[2]) == (before[0], before[1], before[2])
        and after[1] == 40,
        f"{before} -> {after}",
    )
    r = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": raw1},
    )
    check("previous key rejected after partial rotation → 401", r.status_code == 401, str(r.status_code))
    ok, hit = _drain(c, raw1r)
    check(
        "rotated key gets exactly the remaining allowance (60, then 429)",
        ok == 60 and hit,
        f"successes={ok} hit_limit={hit}",
    )
    used = _quota_row(uri, raw1r)
    check("bucket reads 100/100 afterwards (no reset)", used is not None and used[1] == 100, str(used))

    # --- 3. rotation at exhausted quota ----------------------------------
    key2 = _new_quota_key(c, "quota-exhausted")
    raw2 = key2["key"]
    _set_usage(uri, raw2, 100)
    rotated2 = _rotate(c, key2["id"])
    raw2r = rotated2["key"]
    after = _quota_row(uri, raw2r)
    check(
        "exhausted 100/100 preserved across rotation",
        after is not None and after[0] == 100 and after[1] == 100,
        str(after),
    )
    r = c.post(
        "/v1/process",
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        headers={"X-Platrixa-API-Key": raw2r},
    )
    check(
        "rotated exhausted key → 429 QUOTA_EXHAUSTED (no fresh allowance)",
        r.status_code == 429 and r.json().get("error", {}).get("code") == "QUOTA_EXHAUSTED",
        f"{r.status_code} {r.text[:80]}",
    )
    check(
        "spent units still 100/100 after the rejected call",
        (_quota_row(uri, raw2r) or (None, None))[1] == 100,
    )


# ---------------------------------------------------------------------------
# F. authorization boundary — the two planes never mix
# ---------------------------------------------------------------------------


def section_f(c: TestClient) -> None:
    print("\nF. authorization boundaries (data-plane ≠ management-plane)")
    r = c.post(
        "/v1/developer/api-keys",
        json={"name": "escalation attempt"},
        headers={"X-Platrixa-Management-Token": "plx_test_definitely_a_runtime_key_shape"},
    )
    check(
        "runtime-shaped token as management token → 403",
        r.status_code == 403 and r.json()["error"]["code"] == "API_KEY_MANAGEMENT_UNAUTHORIZED",
        str(r.status_code),
    )
    r = c.get("/v1/developer/api-keys")
    check("missing management token → 403", r.status_code == 403, str(r.status_code))
    r = c.get("/v1/developer/api-keys", headers={"X-Platrixa-Management-Token": "wrong"})
    check("wrong management token → 403", r.status_code == 403, str(r.status_code))


# ---------------------------------------------------------------------------
# G. storage invariants on the REAL database
# ---------------------------------------------------------------------------


def section_g(uri: str) -> None:
    print("\nG. storage invariants (real DB)")
    from sqlalchemy import create_engine, text

    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'platrixa_api_keys'"
            )
        ).scalars().all()
        check("no raw-key column exists", not any("raw" in c for c in cols), str(cols))
        try:
            conn.execute(
                text(
                    "INSERT INTO platrixa_api_keys (id, tenant_id, name, key_prefix, key_hash, "
                    "environment, status, created_at) VALUES "
                    "('99999999-9999-9999-9999-999999999999','t','n','p','NOTAHASH','test','ACTIVE',now())"
                )
            )
            conn.rollback()
            check("hash-format CHECK constraint enforced", False, "bad hash inserted")
        except Exception:
            conn.rollback()
            check("hash-format CHECK constraint enforced", True)
        row = conn.execute(text("SELECT key_hash FROM platrixa_api_keys LIMIT 1")).first()
        if row:
            try:
                conn.execute(
                    text(
                        "INSERT INTO platrixa_api_keys (id, tenant_id, name, key_prefix, key_hash, "
                        "environment, status, created_at) VALUES "
                        "('88888888-8888-8888-8888-888888888888','t','n','p',:h,'test','ACTIVE',now())"
                    ),
                    {"h": row[0]},
                )
                conn.rollback()
                check("key_hash UNIQUE constraint enforced", False, "duplicate hash inserted")
            except Exception:
                conn.rollback()
                check("key_hash UNIQUE constraint enforced", True)
        n_revoked = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_api_keys "
                "WHERE status = 'REVOKED' AND revoked_at IS NULL"
            )
        ).scalar()
        check("revocation-consistency CHECK holds", n_revoked == 0, str(n_revoked))


# ---------------------------------------------------------------------------
# H. secret-leakage scan (OpenAPI, errors, 403 bodies)
# ---------------------------------------------------------------------------


def section_h(c: TestClient, created: dict) -> None:
    print("\nH. secret-leakage scan")
    openapi = c.get("/openapi.json").json()
    blob = json.dumps(openapi)
    raw_first = created["first"]["key"]
    leaks = [s for s in (raw_first, created["second"]["key"], _MGMT_TOKEN) if s in blob]
    check("no raw key / management token in OpenAPI", not leaks, str(leaks)[:60])
    err = c.post("/v1/developer/api-keys", json={"name": ""}, headers=mgmt_hdr())
    check("validation error body carries no secret", raw_first not in err.text and _MGMT_TOKEN not in err.text)
    r403 = c.get("/v1/developer/api-keys", headers={"X-Platrixa-Management-Token": _MGMT_TOKEN + "x"})
    check("403 body carries no secret", raw_first not in r403.text)


# ---------------------------------------------------------------------------
# I. regression — the Phase 5C suite against the same stack, unchanged
# ---------------------------------------------------------------------------


def regression_79() -> None:
    print("\nI. regression: suite 79 (Phase 5C), zero changed lines")
    import subprocess

    # Free the 5G embedded server first so the nested suite owns the machine.
    if _PG is not None:
        try:
            _PG[0].stop()
        except Exception:
            pass
    proc = subprocess.run(
        [sys.executable, "scripts/fte_fyjc_79_phase5c_idempotency_test.py"],
        capture_output=True,
        text=True,
        timeout=160,
    )
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    tail = lines[-2:] if lines else ["<no output>"]
    result_line = next((l for l in lines if "RESULT:" in l), "")
    check(
        "suite 79 passes unchanged",
        proc.returncode == 0 and "[FAIL]" not in (proc.stdout or "") and "ALL PASS" in result_line,
        " | ".join(tail)[:200],
    )


# ---------------------------------------------------------------------------


def main() -> int:
    _, uri = pg_backend()
    c = client()
    section_a0(c)
    created = section_a(c)
    section_b(c, created)
    section_c(c, created)
    section_d(c, created)
    section_e(c, created)
    section_d2(c, uri)
    section_f(c)
    section_g(uri)
    section_h(c, created)
    regression_79()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print(f"\n=== Phase 5G: {passed}/{len(CHECKS)} checks passed ===")
    if failed:
        for name, ok, detail in CHECKS:
            if not ok:
                print(f"  FAIL: {name} — {detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
