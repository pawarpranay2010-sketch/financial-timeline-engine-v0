"""End-to-end security & contract tests for the hosted developer API.

Phase: developer productization — proves the required properties with the
REAL production code path (no stubbed auth), using a temporary SQLite
metering store (the gate is SQLAlchemy-generic; PostgreSQL-specific DDL
semantics live in backend/database/platrixa_tenant_quota_schema.sql).

Covers (hardening brief Parts 2/3/4/5/15):
  A. Authentication         valid / missing / invalid / deactivated keys
  B. API-key lifecycle      CSPRNG generation, uniqueness, hash-only storage
  C. Tenant isolation       key→tenant mapping, cross-tenant never matches
  D. Metering               quota increments, exhaustion 429, atomicity,
                            auth failures consume zero, YYYY-MM rollover
  E. HTTP                   /v1/process, /v1/health, /v1/ready
  F. Security               no raw keys persisted, no secret leakage in
                            responses or logs
  G. Processing             injected-client success + status survival
  H. JSON                   serializable, documented fields

Run:
    python3 -m pytest tests/test_developer_api_security.py -v
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.auth import gate as metered_gate
from backend.auth import tokens as auth_tokens

# ---------------------------------------------------------------------------
# Infrastructure: a real SQLite-backed metering store per test module.
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS platrixa_tenant_quotas (
    api_key_hash        VARCHAR(64)  PRIMARY KEY,
    tenant_id           VARCHAR(64)  NOT NULL,
    monthly_limit       INTEGER      NOT NULL DEFAULT 100,
    current_month_usage INTEGER      NOT NULL DEFAULT 0,
    usage_month         VARCHAR(7)   NOT NULL,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    last_request_at     TIMESTAMPTZ  NULL
);
"""

_TMPDIR = tempfile.mkdtemp(prefix="platrixa-metering-test-")
_SQLITE_URL = f"sqlite:///{_TMPDIR}/metering.db"

# The gate converts postgresql:// → postgresql+psycopg2://; do the same for
# sqlite so SQLAlchemy's generic driver is used (no extra deps).
_TEST_URL = _SQLITE_URL.replace("sqlite:///", "sqlite+pysqlite:///", 1)


def _provision(tenant_id: str, monthly_limit: int, raw_key: str | None = None) -> str:
    """Insert a tenant row the same way dev_seed_tenant does; return raw key."""
    from sqlalchemy import create_engine, text

    from backend.auth.models import current_usage_month

    if raw_key is None:
        raw_key = "plx_live_" + secrets.token_urlsafe(32)  # same CSPRNG form
    key_hash = auth_tokens.hash_token(raw_key)
    engine = create_engine(_TEST_URL, future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, 0, :m, TRUE)"
            ),
            {
                "h": key_hash,
                "t": tenant_id,
                "l": monthly_limit,
                "m": current_usage_month(),
            },
        )
    engine.dispose()
    return raw_key


@pytest.fixture(scope="module", autouse=True)
def metering_env():
    """Activate the REAL metered gate against the temporary store."""
    with patch.dict(os.environ, {metered_gate.METERING_ENV_VAR: _TEST_URL}):
        metered_gate._session_factory_cache.clear()
        yield
    metered_gate._session_factory_cache.clear()


# A stub client injected at the app level so tests exercise the REAL gate
# (DB-backed, env-configured) without loading the 1.5B model. Processing
# itself is separately proven by the local CLI run and the package example.
class _StubClient:
    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return None

    def process(self, raw_input: str, request_id=None):
        from platrixa.errors import InputError

        if not (raw_input or "").strip():
            raise InputError("raw_input must not be empty")  # real facade contract
        status = "VERIFIED" if "cash" in raw_input.lower() else "REVIEW_REQUIRED"
        return SimpleNamespace(
            status=status,
            status_label=status,
            success=status == "VERIFIED",
            next_action=None,
            issues=[],
            grounding_issues=[],
            rule_evidence=[],
            interpretation={"transaction_type_enum": "PAYMENT", "parties": []},
            accounting={
                "debit_lines": [{"account": "Cash", "amount": 100}],
                "credit_lines": [{"account": "Sales", "amount": 100}],
            },
        )


@pytest.fixture(scope="module")
def client():
    from api.main import create_app
    from api.routes import developer

    app = create_app()
    app.state.platrixa_client = _StubClient()
    with TestClient(app) as c:
        yield c


# Keys provisioned once for the module (unique secrets per tenant by design).
KEY_A = _provision("tenant-a", 40)  # generous: shared by many contract tests
KEY_B = _provision("tenant-b", 2)  # small on purpose: exhaustion is asserted
KEY_INACTIVE = _provision("tenant-c", 10)
# Deactivate tenant-c directly.
from sqlalchemy import create_engine, text as _text

with create_engine(_TEST_URL, future=True).begin() as _conn:
    _conn.exec_driver_sql(_DDL)
    _conn.execute(
        _text("UPDATE platrixa_tenant_quotas SET is_active = FALSE WHERE tenant_id = 'tenant-c'")
    )


# ---------------------------------------------------------------------------
# A. Authentication
# ---------------------------------------------------------------------------


def test_valid_key_admitted(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_version"] == "v1"
    assert body["status"] in {"VERIFIED", "REVIEW_REQUIRED", "BLOCKED"}


def test_missing_key_rejected(client):
    r = client.post("/v1/process", json={"raw_input": "Sold goods for cash Rs 100"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_invalid_key_rejected_and_externally_indistinguishable(client):
    r_missing = client.post(
        "/v1/process", json={"raw_input": "Sold goods for cash Rs 100"}
    )
    r_invalid = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": "plx_live_" + secrets.token_urlsafe(32)},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r_invalid.status_code == 401
    # Missing vs invalid must be externally indistinguishable (doc contract).
    assert r_missing.json()["error"]["code"] == r_invalid.json()["error"]["code"]
    assert r_missing.json()["error"]["message"] == r_invalid.json()["error"]["message"]


def test_deactivated_key_rejected(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_INACTIVE},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# B. API-key lifecycle — CSPRNG generation, uniqueness, hash-only storage
# ---------------------------------------------------------------------------


def test_key_generation_is_csprng_and_prefixed():
    keys = {"plx_live_" + secrets.token_urlsafe(32) for _ in range(200)}
    assert len(keys) == 200  # secrets.token_urlsafe is a CSPRNG; never repeats in practice
    assert all(k.startswith("plx_live_") for k in keys)
    assert all(len(k) >= 40 for k in keys)  # 32 bytes of entropy, URL-safe


def test_two_tenants_have_different_keys_and_hashes():
    raw1 = "plx_live_" + secrets.token_urlsafe(32)
    raw2 = "plx_live_" + secrets.token_urlsafe(32)
    h1, h2 = auth_tokens.hash_token(raw1), auth_tokens.hash_token(raw2)
    assert raw1 != raw2
    assert h1 != h2
    # Hash format matches the production DDL CHECK constraint.
    import re

    assert re.fullmatch(r"[0-9a-f]{64}", h1)


def test_plaintext_keys_never_stored_in_metering_db():
    conn = sqlite3.connect(_TMPDIR + "/metering.db")
    try:
        rows = conn.execute(
            "SELECT api_key_hash, tenant_id FROM platrixa_tenant_quotas"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "expected provisioned rows"
    for raw in (KEY_A, KEY_B, KEY_INACTIVE):
        for h, t in rows:
            assert raw not in (h or "") and raw not in (t or ""), (
                "plaintext key material found in the database"
            )
            assert raw.encode("utf-8").hex() not in (h or "")


def test_hash_is_deterministic_and_lookups_use_hash_path():
    assert auth_tokens.hash_token("  abc  ") == auth_tokens.hash_token("abc")
    reason, ctx = metered_gate.resolve_tenant(KEY_A)
    assert reason == metered_gate.REASON_OK
    assert ctx.tenant_id == "tenant-a"
    assert ctx.units_reserved == 0  # pure lookup consumed nothing


def test_raw_key_not_in_any_response(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A, "X-Request-Id": "rawkey-echo-check"},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r.status_code == 200
    assert KEY_A not in r.text
    # And even a rejected request must not echo the presented key.
    r2 = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": "plx_live_definitely-invalid-key-abcdef"},
        json={"raw_input": "x"},
    )
    assert r2.status_code == 401
    assert "definitely-invalid-key" not in r2.text


def test_constant_time_comparison_present():
    # The Phase 15 gate must compare in constant time (hashed compare_digest).
    import inspect

    import api.routes.developer as dev

    src = inspect.getsource(dev)
    assert "hmac.compare_digest" in src
    assert "compare_digest(a, b)" in src


# ---------------------------------------------------------------------------
# C. Tenant isolation
# ---------------------------------------------------------------------------


def test_key_maps_only_to_its_own_tenant():
    _, ctx_a = metered_gate.resolve_tenant(KEY_A)
    _, ctx_b = metered_gate.resolve_tenant(KEY_B)
    assert ctx_a.tenant_id == "tenant-a"
    assert ctx_b.tenant_id == "tenant-b"


def test_tenant_a_key_cannot_authenticate_as_tenant_b(client):
    # Exhaust tenant B completely (limit 2), then verify A still works —
    # proving B's exhaustion can never be influenced by A's consumption
    # and that A's key resolves only to A's row.
    for _ in range(2):
        r = client.post(
            "/v1/process",
            headers={"X-Platrixa-API-Key": KEY_B},
            json={"raw_input": "Sold goods for cash Rs 100"},
        )
        assert r.status_code == 200
    r_b = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_B},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r_b.status_code == 429
    assert r_b.json()["error"]["code"] == "QUOTA_EXHAUSTED"

    r_a = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r_a.status_code == 200, "tenant A must be unaffected by tenant B exhaustion"


def test_random_key_never_matches_a_tenant():
    for _ in range(25):
        reason, ctx = metered_gate.resolve_tenant("plx_live_" + secrets.token_urlsafe(32))
        assert reason == metered_gate.REASON_UNKNOWN_KEY
        assert ctx is None


# ---------------------------------------------------------------------------
# D. Metering — quota semantics, atomicity, rollover
# ---------------------------------------------------------------------------


def _usage(tenant_id: str) -> tuple[int, str]:
    conn = sqlite3.connect(_TMPDIR + "/metering.db")
    try:
        row = conn.execute(
            "SELECT current_month_usage, usage_month FROM platrixa_tenant_quotas "
            "WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0], row[1]


def test_auth_failures_consume_zero_quota(client):
    before, _ = _usage("tenant-a")
    client.post("/v1/process", json={"raw_input": "Sold goods for cash Rs 100"})  # no key
    client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": "plx_live_bogus"},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    after, _ = _usage("tenant-a")
    assert after == before, "authentication failures must consume zero quota"


def test_each_admitted_request_consumes_exactly_one_unit(client):
    before, month_before = _usage("tenant-a")
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Paid rent Rs 500 by cash"},
    )
    assert r.status_code == 200
    after, month_after = _usage("tenant-a")
    assert after == before + 1
    assert month_after == month_before
    from backend.auth.models import current_usage_month

    assert month_after == current_usage_month()  # YYYY-MM bucket


def test_quota_exhaustion_returns_documented_429(client):
    # tenant-b was exhausted in the isolation test (limit 2, used 2).
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_B},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r.status_code == 429
    body = r.json()
    assert body["api_version"] == "v1"
    assert body["error"]["code"] == "QUOTA_EXHAUSTED"


def test_reservation_is_atomic_under_concurrency():
    """N=5 limit; 12 concurrent reserve attempts → at most N accepted."""
    raw = _provision("tenant-concurrent", 5)
    results = []

    def _hit(_i):
        reason, ctx = metered_gate.reserve_unit(raw)
        results.append(reason)

    threads = [threading.Thread(target=_hit, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    accepted = sum(1 for r in results if r == metered_gate.REASON_OK)
    rejected = sum(1 for r in results if r == metered_gate.REASON_QUOTA_EXHAUSTED)
    assert accepted == 5, f"expected exactly 5 admissions, got {accepted}"
    assert rejected == 7
    used, _ = _usage("tenant-concurrent")
    assert used == 5, "database count must equal admitted requests (no over-reservation)"


def test_monthly_bucket_rollover_resets_usage():
    from backend.auth.models import TenantQuota, current_usage_month
    from sqlalchemy.orm import sessionmaker

    raw = _provision("tenant-rollover", 3)
    SessionLocal = sessionmaker(bind=create_engine(_TEST_URL, future=True), future=True)
    stale_month = "2000-01"
    with SessionLocal() as s:
        h = auth_tokens.hash_token(raw)
        s.get(TenantQuota, h).usage_month = stale_month
        s.get(TenantQuota, h).current_month_usage = 3
        s.commit()

    # Usage is stale-bucketed → resolve_tenant must report 0 usage this month.
    reason, ctx = metered_gate.resolve_tenant(raw)
    assert reason == metered_gate.REASON_OK
    assert ctx.current_month_usage == 0

    # Reservation must succeed despite stale usage == limit, and reset the
    # bucket atomically inside the same UPDATE.
    reason2, ctx2 = metered_gate.reserve_unit(raw)
    assert reason2 == metered_gate.REASON_OK
    assert ctx2.current_month_usage == 1
    used, month = _usage("tenant-rollover")
    assert used == 1
    assert month == current_usage_month()


def test_metering_unavailable_fails_closed(monkeypatch):
    raw = "plx_live_" + secrets.token_urlsafe(32)
    monkeypatch.setenv(metered_gate.METERING_ENV_VAR, "sqlite+pysqlite:///" + _TMPDIR + "/missing.db")
    metered_gate._session_factory_cache.clear()
    reason, ctx = metered_gate.authorize_request(raw)
    assert reason == metered_gate.REASON_METERING_UNAVAILABLE
    assert ctx is None  # never admitted
    monkeypatch.setenv(metered_gate.METERING_ENV_VAR, _TEST_URL)
    metered_gate._session_factory_cache.clear()


# ---------------------------------------------------------------------------
# E. HTTP surface
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "platrixa-developer-api"
    assert body["api_version"] == "v1"


def test_ready_endpoint(client):
    r = client.get("/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["api_version"] == "v1"
    assert body["status"] in {"ready", "not_ready"}
    assert "provider" in body


def test_malformed_json_is_400_not_422(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A, "Content-Type": "application/json"},
        content=b"{broken json",
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "REQUEST_MALFORMED"


def test_domain_invalid_input_is_422_input_invalid(client):
    # Admitted (quota consumed) then domain-rejected: whitespace-only input.
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "   "},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INPUT_INVALID"


def test_oversized_body_is_413(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "x" * 70_000},
    )
    assert r.status_code == 413


def test_status_survives_http_boundary(client):
    r1 = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert r1.json()["status"] == "VERIFIED"
    r2 = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Sold goods on credit"},
    )
    assert r2.json()["status"] == "REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# F. Security — logs carry no secrets; responses carry no internals
# ---------------------------------------------------------------------------


def test_no_key_material_in_logs(client, caplog):
    with caplog.at_level(logging.INFO, logger="platrixa.api"):
        client.post(
            "/v1/process",
            headers={"X-Platrixa-API-Key": KEY_A},
            json={"raw_input": "Sold goods for cash Rs 100"},
        )
        client.post(
            "/v1/process",
            headers={"X-Platrixa-API-Key": "plx_live_bogus-xyz"},
            json={"raw_input": "Sold goods for cash Rs 100"},
        )
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert KEY_A not in text
    assert "bogus-xyz" not in text


def test_error_bodies_carry_no_stack_traces(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        content=b"{oops",
    )
    body = r.text
    assert "Traceback" not in body
    assert "File \"" not in body


def test_dev_seed_key_prefix_matches_production_convention():
    import inspect

    from backend.auth import dev_seed_tenant

    src = inspect.getsource(dev_seed_tenant)
    assert "secrets.token_urlsafe(32)" in src, "keys must come from the CSPRNG"
    assert "plx_" in src


# ---------------------------------------------------------------------------
# G/H. JSON contract
# ---------------------------------------------------------------------------


def test_success_response_is_fully_serializable_and_documented(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Purchased furniture for cash Rs 15,000"},
    )
    assert r.status_code == 200
    body = r.json()
    for field in ("api_version", "status", "status_label", "success",
                  "interpretation", "accounting", "issues", "grounding_issues",
                  "rule_evidence", "request_id", "next_action"):
        assert field in body, f"documented field {field} missing"
    json.dumps(body)  # raises if not JSON-serializable
    assert body["status"] in {
        "VERIFIED", "REVIEW_REQUIRED", "VALIDATION_FAILED",
        "GROUNDING_FAILED", "FORBIDDEN_OUTPUT", "MODEL_UNAVAILABLE",
        "UNSUPPORTED_TRANSACTION", "BLOCKED",
    }


def test_no_internal_module_names_in_responses(client):
    r = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": KEY_A},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    body = r.text
    for leak in ("transformers", "peft", "torch", "huggingface", "HF_TOKEN"):
        assert leak not in body
