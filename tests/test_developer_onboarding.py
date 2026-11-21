"""Security, isolation and end-to-end tests for developer onboarding.

Real code paths, real HTTP boundary, real SQL store. The store is SQLite
because this environment has no PostgreSQL server; the SAME portable schema
file backs production and the test store (see
backend/database/platrixa_developer_onboarding_schema.sql). The two credential
tables are created here with their production shape.

What is NOT exercised here (stated, not hidden):
  * PostgreSQL-only behaviour: ``pg_advisory_xact_lock`` and the PostgreSQL
    nonce store (``management_assertion_nonces`` uses ``to_timestamp`` +
    ``ON CONFLICT ... RETURNING``). Owner assertions are therefore verified
    for real EXCEPT the durable nonce consumption, which is monkeypatched to
    "accepted" in the owner-auth tests. One test proves that with the real
    nonce store on SQLite the management plane fails CLOSED (503) rather than
    admitting the request.
  * Actual SMTP delivery (no provider is configured in this repository).

Run:
    python3 -m pytest tests/test_developer_onboarding.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

PUBLIC_ORIGIN = "https://platrixa.test"
OWNER_TENANT = "owner-tenant"
ASSERTION_SECRET = "m" * 40
ASSERTION_ISSUER = "platrixa-edge"


# ---------------------------------------------------------------------------
# Store fixtures
# ---------------------------------------------------------------------------

_CREDENTIAL_DDL = (
    """
    CREATE TABLE IF NOT EXISTS platrixa_tenant_quotas (
        api_key_hash        VARCHAR(64)  PRIMARY KEY,
        tenant_id           VARCHAR(64)  NOT NULL,
        monthly_limit       INTEGER      NOT NULL DEFAULT 100,
        current_month_usage INTEGER      NOT NULL DEFAULT 0,
        usage_month         VARCHAR(7)   NOT NULL,
        is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
        last_request_at     TIMESTAMP    NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platrixa_api_keys (
        id            VARCHAR(36)  PRIMARY KEY,
        tenant_id     VARCHAR(64)  NOT NULL,
        name          VARCHAR(120) NOT NULL,
        key_prefix    VARCHAR(20)  NOT NULL,
        key_hash      VARCHAR(64)  NOT NULL,
        environment   VARCHAR(20)  NOT NULL DEFAULT 'test',
        status        VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
        created_at    TIMESTAMP    NOT NULL,
        last_used_at  TIMESTAMP    NULL,
        revoked_at    TIMESTAMP    NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_test_key_hash ON platrixa_api_keys (key_hash)",
    """
    CREATE TABLE IF NOT EXISTS platrixa_async_jobs (
        job_id     VARCHAR(64) PRIMARY KEY,
        tenant_id  VARCHAR(64) NOT NULL,
        result_id  VARCHAR(64),
        status     VARCHAR(16),
        kind       VARCHAR(16),
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )
    """,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    url = f"sqlite:///{tmp_path}/store.db"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    # The repository TRACKS a .env that points at a real database. Existing
    # modules (backend/database/db.py) read DATABASE_URL lazily and would
    # otherwise open a production connection from a test process. Point it at
    # a throwaway SQLite file for the whole test.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/unused-app.db")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "d" * 40)
    monkeypatch.setenv("PLATRIXA_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("PLATRIXA_OWNER_NOTIFICATION_EMAIL", "owner@platrixa.test")
    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", "v" * 40)
    monkeypatch.setenv("PLATRIXA_MANAGEMENT_ASSERTION_SECRET", ASSERTION_SECRET)
    monkeypatch.setenv("PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS", ASSERTION_ISSUER)
    monkeypatch.setenv("PLATRIXA_KEY_MANAGEMENT_TENANT_ID", OWNER_TENANT)
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_DAY", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP", "1000")

    from backend.auth import gate
    from backend.onboarding import store

    store.reset_cache()
    gate._session_factory_cache.clear()
    store.ensure_schema(url)
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "store.db"), future=True)
    with engine.begin() as conn:
        for statement in _CREDENTIAL_DDL:
            conn.exec_driver_sql(statement)
    engine.dispose()

    yield SimpleNamespace(url=url, path=str(tmp_path / "store.db"))

    store.reset_cache()
    gate._session_factory_cache.clear()


class _StubClient:
    """Injected at app level: exercises the real HTTP contract, no model load."""

    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return None

    def process(self, raw_input: str, request_id=None):
        from platrixa.errors import InputError

        if not (raw_input or "").strip():
            raise InputError("raw_input must not be empty")
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


@pytest.fixture()
def client(env):
    from api.main import create_app

    app = create_app()
    app.state.platrixa_client = _StubClient()
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_APPLICATION = {
    "name": "Asha Verma",
    "email": "asha@acme-labs.example",
    "company": "Acme Labs",
    "building": "A reconciliation service that validates ledger entries before posting.",
    "expected_usage": "small",
    "profile_url": None,
}


def _apply(client, **overrides):
    payload = dict(_APPLICATION)
    payload.update(overrides)
    return client.post("/v1/developer/access-requests", json=payload)


def _rows(env, sql, params=()):
    conn = sqlite3.connect(env.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _scalar(env, sql, params=()):
    rows = _rows(env, sql, params)
    return rows[0][0] if rows else None


def _all_text(env) -> str:
    conn = sqlite3.connect(env.path)
    chunks = []
    try:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            for row in conn.execute(f"SELECT * FROM {table}"):
                chunks.extend(str(value) for value in row if isinstance(value, str))
    finally:
        conn.close()
    return "\n".join(chunks)


def _token_from_body(env, application_id: str, kind: str) -> str:
    rows = _rows(
        env,
        "SELECT body_text FROM platrixa_developer_notifications "
        "WHERE application_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
        (application_id, kind),
    )
    assert rows, f"no {kind} notification queued"
    match = re.search(r"#token=([A-Za-z0-9_\-]+)", rows[0]["body_text"])
    assert match, f"no token in {kind} body"
    return match.group(1)


def _app_id_for(env, email: str) -> str:
    value = _scalar(
        env,
        "SELECT id FROM platrixa_developer_access_requests WHERE email = ?",
        (email,),
    )
    assert value, f"no application for {email}"
    return value


def _advance_to_reveal(client, env, email: str = _APPLICATION["email"]) -> dict:
    """submit → verify → provisioned; returns ids and the reveal token."""
    assert _apply(client, email=email).status_code == 202
    application_id = _app_id_for(env, email)
    verify_token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    verified = client.post("/v1/developer/verify-email", json={"token": verify_token})
    assert verified.status_code == 200, verified.text
    row = _rows(
        env,
        "SELECT status, tenant_id FROM platrixa_developer_access_requests WHERE id = ?",
        (application_id,),
    )[0]
    assert row["status"] == "ACTIVE", row["status"]
    reveal_token = _token_from_body(env, application_id, "ONBOARDING")
    return {
        "application_id": application_id,
        "tenant_id": row["tenant_id"],
        "reveal_token": reveal_token,
    }


def _activate(client, env, email: str = _APPLICATION["email"]) -> dict:
    info = _advance_to_reveal(client, env, email)
    revealed = client.post("/v1/developer/keys/reveal", json={"token": info["reveal_token"]})
    assert revealed.status_code == 200, revealed.text
    info["api_key"] = revealed.json()["api_key"]
    info["key_id"] = revealed.json()["key_id"]
    return info


def _owner_headers(method: str, path: str, body: bytes = b"", query: str = "") -> dict:
    """A real management gateway assertion (canonical message per api_keys.py)."""
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    content_type = "application/json"
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "|".join(
        [ASSERTION_ISSUER, "platrixa-management", OWNER_TENANT, method, path,
         query, content_type, body_hash, timestamp, nonce]
    )
    signature = hmac.new(
        ASSERTION_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "x-platrixa-assertion-signature": signature,
        "x-platrixa-assertion-issuer": ASSERTION_ISSUER,
        "x-platrixa-assertion-audience": "platrixa-management",
        "x-platrixa-assertion-tenant-id": OWNER_TENANT,
        "x-platrixa-assertion-method": method,
        "x-platrixa-assertion-path": path,
        "x-platrixa-assertion-query-string": query,
        "x-platrixa-assertion-content-type": content_type,
        "x-platrixa-assertion-body-hash": body_hash,
        "x-platrixa-assertion-timestamp": timestamp,
        "x-platrixa-assertion-nonce": nonce,
        "content-type": content_type,
    }


@pytest.fixture()
def owner(monkeypatch):
    """Accept the (PostgreSQL-only) nonce consumption; HMAC stays real."""
    from backend.auth import api_keys as key_store

    monkeypatch.setattr(
        key_store, "consume_management_nonce", lambda *a, **k: key_store.MGMT_OK
    )


def _owner_get(client, path: str, query: str = ""):
    # The signed query string must be the query actually sent.
    headers = _owner_headers("GET", path, query=query)
    url = path + (f"?{query}" if query else "")
    return client.get(url, headers=headers)


def _owner_post(client, path: str, payload: dict):
    body = json.dumps(payload).encode()
    headers = _owner_headers("POST", path, body=body)
    return client.post(path, content=body, headers=headers)


# ===========================================================================
# 1. Application submission
# ===========================================================================


def test_valid_submission_creates_pending_application(client, env):
    response = _apply(client)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "PENDING_VERIFICATION"
    assert body["verification_required"] is True
    assert "application_id" not in body  # never handed to the browser
    rows = _rows(env, "SELECT * FROM platrixa_developer_access_requests")
    assert len(rows) == 1
    assert rows[0]["status"] == "PENDING_VERIFICATION"
    assert rows[0]["tenant_id"] is None
    assert rows[0]["key_id"] is None


def test_missing_required_field_is_rejected(client, env):
    response = _apply(client, name="   ")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "NAME_REQUIRED"
    assert error["field"] == "name"
    assert _rows(env, "SELECT id FROM platrixa_developer_access_requests") == []


def test_invalid_email_is_rejected_and_normalised(client, env):
    assert _apply(client, email="not-an-email").json()["error"]["code"] == "EMAIL_INVALID"
    assert client.post(
        "/v1/developer/access-requests",
        json={**_APPLICATION, "email": "asha@acme-labs.example\nBcc: x@y.z"},
    ).status_code == 400
    ok = _apply(client, email="  Asha@Acme-Labs.Example  ")
    assert ok.status_code == 202
    stored = _scalar(env, "SELECT email FROM platrixa_developer_access_requests")
    assert stored == "asha@acme-labs.example"


def test_malformed_optional_url_is_rejected(client):
    for bad in ("javascript:alert(1)", "ftp://acme.example", "acme labs", "https://"):
        response = _apply(client, profile_url=bad)
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "PROFILE_URL_INVALID"
    assert _apply(client, profile_url="https://github.com/acme").status_code == 202


def test_excessively_long_input_is_rejected(client):
    assert _apply(client, company="c" * 200).json()["error"]["code"] == "FIELD_TOO_LONG"
    assert _apply(client, building="b" * 1500).json()["error"]["code"] == "FIELD_TOO_LONG"
    assert _apply(
        client, profile_url="https://example.com/" + "x" * 400
    ).json()["error"]["code"] == "PROFILE_URL_TOO_LONG"


def test_duplicate_application_is_neutral_and_creates_no_second_row(client, env):
    first = _apply(client)
    second = _apply(client)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(_rows(env, "SELECT id FROM platrixa_developer_access_requests")) == 1
    assert len(
        _rows(env, "SELECT id FROM platrixa_developer_notifications WHERE kind='EMAIL_VERIFICATION'")
    ) == 1


def test_concurrent_duplicate_submissions_yield_one_application(client, env):
    results = []

    def _hit():
        results.append(_apply(client).status_code)

    threads = [threading.Thread(target=_hit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(code == 202 for code in results), results
    assert len(_rows(env, "SELECT id FROM platrixa_developer_access_requests")) == 1


def test_rate_limit_enforced_per_client(client, env, monkeypatch):
    from backend.onboarding import config

    monkeypatch.setenv(config.MAX_PER_IP_HOUR_ENV_VAR, "2")
    assert _apply(client, email="one@acme.example").status_code == 202
    assert _apply(client, email="two@acme.example").status_code == 202
    blocked = _apply(client, email="three@acme.example")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers.get("Retry-After") == "60"
    # The refusal is auditable: an owner notification survived the rollback.
    kinds = [
        row["kind"]
        for row in _rows(env, "SELECT kind FROM platrixa_developer_notifications")
    ]
    assert "SUSPICIOUS_ACTIVITY" in kinds


def test_global_daily_cap_enforced(client, monkeypatch):
    from backend.onboarding import config

    monkeypatch.setenv(config.GLOBAL_DAILY_CAP_ENV_VAR, "1")
    assert _apply(client, email="one@acme.example").status_code == 202
    second = _apply(client, email="two@acme.example")
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "ONBOARDING_CAPACITY_REACHED"


def test_website_surface_requires_valid_visitor_assertion(client):
    response = client.post(
        "/v1/developer/access-requests",
        json=_APPLICATION,
        headers={"x-platrixa-surface": "website"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VISITOR_AUTH_REQUIRED"


def test_forged_visitor_identity_header_is_not_trusted(client):
    response = client.post(
        "/v1/developer/access-requests",
        json=_APPLICATION,
        headers={
            "x-platrixa-surface": "website",
            "x-platrixa-visitor-id": str(uuid.uuid4()),
            "x-platrixa-visitor-assertion": "V1|forged",
            "x-platrixa-request-timestamp": str(int(time.time())),
            "x-platrixa-visitor-nonce": "n" * 20,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VISITOR_AUTH_REQUIRED"


def test_csrf_rejects_missing_origin_on_website_surface(client, monkeypatch):
    from backend.auth import visitor_request

    monkeypatch.setattr(
        visitor_request,
        "verify_visitor_request",
        lambda **kwargs: ("VALID", str(uuid.uuid4())),
    )
    response = client.post(
        "/v1/developer/access-requests",
        json=_APPLICATION,
        headers={"x-platrixa-surface": "website"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid_origin"


def test_csrf_rejects_foreign_origin_on_website_surface(client, monkeypatch):
    from backend.auth import visitor_request

    monkeypatch.setattr(
        visitor_request,
        "verify_visitor_request",
        lambda **kwargs: ("VALID", str(uuid.uuid4())),
    )
    response = client.post(
        "/v1/developer/access-requests",
        json=_APPLICATION,
        headers={"x-platrixa-surface": "website", "origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid_origin"


# ===========================================================================
# 2. Email verification
# ===========================================================================


def test_verification_token_is_stored_only_as_a_hash(client, env):
    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    # The verification table holds ONLY the digest.
    rows = _rows(env, "SELECT * FROM platrixa_developer_email_verifications")
    assert rows and all(token not in str(value) for row in rows for value in tuple(row))
    stored = _scalar(
        env,
        "SELECT token_hash FROM platrixa_developer_email_verifications",
    )
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    # The raw token exists only inside the pending outbound message payload,
    # which is cleared on delivery (asserted separately).
    assert _scalar(
        env,
        "SELECT count(*) FROM platrixa_developer_notifications "
        "WHERE body_text LIKE ? AND status = 'PENDING'",
        (f"%{token}%",),
    ) == 1


def test_valid_token_verifies_and_provisions(client, env):
    info = _advance_to_reveal(client, env)
    row = _rows(
        env,
        "SELECT email_verified_at, status FROM platrixa_developer_access_requests WHERE id = ?",
        (info["application_id"],),
    )[0]
    assert row["email_verified_at"]
    assert row["status"] == "ACTIVE"


def test_replayed_token_is_rejected(client, env):
    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    assert client.post("/v1/developer/verify-email", json={"token": token}).status_code == 200
    replay = client.post("/v1/developer/verify-email", json={"token": token})
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "VERIFICATION_TOKEN_INVALID"


def test_expired_token_is_rejected(client, env):
    from sqlalchemy import text

    from backend.onboarding import store

    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    with store.get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_developer_email_verifications "
                "SET expires_at = :t WHERE application_id = :a"
            ),
            {"t": datetime.utcnow() - timedelta(minutes=1), "a": application_id},
        )
    response = client.post("/v1/developer/verify-email", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VERIFICATION_TOKEN_EXPIRED"


def test_invalid_token_is_rejected_without_enumeration(client):
    response = client.post("/v1/developer/verify-email", json={"token": "x" * 43})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VERIFICATION_TOKEN_INVALID"
    assert response.json()["error"]["message"] == "verification link is invalid or expired"


def test_resend_is_throttled_then_allowed(client, env, monkeypatch):
    _apply(client)
    first = client.post(
        "/v1/developer/resend-verification", json={"email": _APPLICATION["email"]}
    )
    assert first.status_code == 429
    assert first.json()["error"]["code"] == "RESEND_THROTTLED"
    unknown = client.post(
        "/v1/developer/resend-verification", json={"email": "nobody@acme.example"}
    )
    assert unknown.status_code == 202  # same shape: no enumeration

    from backend.onboarding import config

    monkeypatch.setenv(config.RESEND_COOLDOWN_ENV_VAR, "1")
    from sqlalchemy import text
    from backend.onboarding import store

    with store.get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_developer_email_verifications "
                "SET created_at = :t"
            ),
            {"t": datetime.utcnow() - timedelta(minutes=5)},
        )
    resumed = client.post(
        "/v1/developer/resend-verification", json={"email": _APPLICATION["email"]}
    )
    assert resumed.status_code == 202
    assert len(
        _rows(env, "SELECT id FROM platrixa_developer_email_verifications")
    ) == 2


def test_concurrent_verification_attempts_have_exactly_one_winner(client, env):
    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    outcomes = []

    def _hit():
        outcomes.append(
            client.post("/v1/developer/verify-email", json={"token": token}).status_code
        )

    threads = [threading.Thread(target=_hit) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(200) == 1, outcomes
    assert outcomes.count(400) == 4
    assert len(_rows(env, "SELECT id FROM platrixa_api_keys")) == 1


# ===========================================================================
# 3. Provisioning
# ===========================================================================


def test_exactly_one_tenant_one_key_one_quota(client, env):
    info = _activate(client, env)
    keys = _rows(env, "SELECT * FROM platrixa_api_keys WHERE tenant_id = ?", (info["tenant_id"],))
    quotas = _rows(
        env, "SELECT * FROM platrixa_tenant_quotas WHERE tenant_id = ?", (info["tenant_id"],)
    )
    applications = _rows(
        env,
        "SELECT status, tenant_id, monthly_limit FROM platrixa_developer_access_requests",
    )
    assert len(keys) == 1 and keys[0]["status"] == "ACTIVE"
    assert len(quotas) == 1 and int(quotas[0]["monthly_limit"]) == 50
    assert quotas[0]["current_month_usage"] == 0
    assert applications[0]["status"] == "ACTIVE"
    assert applications[0]["tenant_id"] == info["tenant_id"]
    assert applications[0]["monthly_limit"] == 50


def test_provisioning_is_idempotent_on_retry(client, env):
    from backend.onboarding import service

    info = _advance_to_reveal(client, env)
    again = service.provision_application(info["application_id"], actor="test-retry")
    assert again["status"] == "ACTIVE"
    assert again["tenant_id"] == info["tenant_id"]
    assert len(_rows(env, "SELECT id FROM platrixa_api_keys")) == 1
    assert len(_rows(env, "SELECT api_key_hash FROM platrixa_tenant_quotas")) == 1


def test_concurrent_provisioning_creates_one_tenant(client, env):
    from backend.onboarding import service

    info = _advance_to_reveal(client, env)
    outcomes = []

    def _hit():
        try:
            service.provision_application(info["application_id"], actor="thread")
            outcomes.append("ok")
        except Exception as exc:  # noqa: BLE001 - outcome is what is asserted
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=_hit) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(_rows(env, "SELECT id FROM platrixa_api_keys")) == 1
    assert len(_rows(env, "SELECT api_key_hash FROM platrixa_tenant_quotas")) == 1


def test_transaction_rolls_back_when_a_step_fails(client, env, monkeypatch):
    from backend.onboarding import notifications, service, store

    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")

    real_enqueue = notifications.enqueue
    calls = {"n": 0}

    def _explode(*args, **kwargs):
        # Let the verification notification succeed; fail the FIRST
        # notification created inside the provisioning transaction.
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("simulated notification failure")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(notifications, "enqueue", _explode)
    failed = client.post("/v1/developer/verify-email", json={"token": token})
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "PROVISIONING_FAILED"
    assert _rows(env, "SELECT id FROM platrixa_api_keys") == []
    assert _rows(env, "SELECT api_key_hash FROM platrixa_tenant_quotas") == []
    assert _rows(env, "SELECT key_id FROM platrixa_developer_key_deliveries") == []
    status = _scalar(
        env, "SELECT status FROM platrixa_developer_access_requests WHERE id = ?", (application_id,)
    )
    assert status == "VERIFIED"

    # Recovery: the same application provisions cleanly once the step works.
    monkeypatch.setattr(notifications, "enqueue", real_enqueue)
    with store.session() as db:
        pass
    result = service.provision_application(application_id, actor="retry-after-rollback")
    assert result["status"] == "ACTIVE"
    assert len(_rows(env, "SELECT id FROM platrixa_api_keys")) == 1


def test_suspicious_application_requires_review_and_gets_no_key(client, env):
    response = _apply(client, email="someone@gmail.com", company="Personal")
    assert response.status_code == 202
    application_id = _app_id_for(env, "someone@gmail.com")
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    verified = client.post("/v1/developer/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "REVIEW_REQUIRED"
    assert _rows(env, "SELECT id FROM platrixa_api_keys") == []
    assert _rows(env, "SELECT key_id FROM platrixa_developer_key_deliveries") == []
    owner_kinds = [
        row["kind"]
        for row in _rows(env, "SELECT kind FROM platrixa_developer_notifications WHERE audience='OWNER'")
    ]
    assert "REVIEW_REQUIRED" in owner_kinds


def test_role_account_and_disposable_domain_require_review(client, env):
    assert _apply(client, email="admin@acme-labs.example").status_code == 202
    app_one = _app_id_for(env, "admin@acme-labs.example")
    token = _token_from_body(env, app_one, "EMAIL_VERIFICATION")
    client.post("/v1/developer/verify-email", json={"token": token})
    assert _scalar(
        env, "SELECT status FROM platrixa_developer_access_requests WHERE id = ?", (app_one,)
    ) == "REVIEW_REQUIRED"

    assert _apply(client, email="dev@mailinator.com").status_code == 202
    app_two = _app_id_for(env, "dev@mailinator.com")
    token = _token_from_body(env, app_two, "EMAIL_VERIFICATION")
    client.post("/v1/developer/verify-email", json={"token": token})
    assert _scalar(
        env, "SELECT status FROM platrixa_developer_access_requests WHERE id = ?", (app_two,)
    ) == "REVIEW_REQUIRED"


def test_missing_delivery_secret_holds_provisioning(client, env, monkeypatch):
    monkeypatch.delenv("PLATRIXA_KEY_DELIVERY_SECRET", raising=False)
    _apply(client)
    application_id = _app_id_for(env, _APPLICATION["email"])
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    response = client.post("/v1/developer/verify-email", json={"token": token})
    assert response.status_code == 200
    assert response.json()["status"] == "VERIFIED"
    assert _rows(env, "SELECT id FROM platrixa_api_keys") == []
    reason = _scalar(
        env, "SELECT last_error FROM platrixa_developer_access_requests WHERE id = ?", (application_id,)
    )
    assert reason == "KEY_DELIVERY_NOT_CONFIGURED"
    owner_kinds = [
        row["kind"]
        for row in _rows(env, "SELECT kind FROM platrixa_developer_notifications WHERE audience='OWNER'")
    ]
    assert "PROVISIONING_FAILED" in owner_kinds


# ===========================================================================
# 4. API key security and one-time delivery
# ===========================================================================


def test_no_plaintext_key_is_stored_in_any_table(client, env):
    info = _activate(client, env)
    assert info["api_key"].startswith("plx_live_")
    text = _all_text(env)
    assert info["api_key"] not in text
    stored_hash = _scalar(
        env, "SELECT key_hash FROM platrixa_api_keys WHERE id = ?", (info["key_id"],)
    )
    assert stored_hash == hashlib.sha256(info["api_key"].encode()).hexdigest()
    assert stored_hash != info["api_key"]


def test_no_plaintext_key_or_token_in_logs(client, env, caplog):
    with caplog.at_level(logging.DEBUG):
        info = _activate(client, env)
        client.post(
            "/v1/process",
            headers={"X-Platrixa-API-Key": info["api_key"]},
            json={"raw_input": "Sold goods for cash Rs 100"},
        )
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert info["api_key"] not in logged
    assert info["reveal_token"] not in logged


def test_reveal_is_single_use(client, env):
    info = _advance_to_reveal(client, env)
    first = client.post("/v1/developer/keys/reveal", json={"token": info["reveal_token"]})
    assert first.status_code == 200
    assert first.json()["api_key"].startswith("plx_live_")
    second = client.post("/v1/developer/keys/reveal", json={"token": info["reveal_token"]})
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "REVEAL_ALREADY_USED"
    # The nonce is gone, so the key can no longer be derived at all.
    assert _scalar(
        env, "SELECT key_nonce FROM platrixa_developer_key_deliveries"
    ) is None


def test_expired_reveal_link_is_refused_and_voided(client, env):
    from sqlalchemy import text
    from backend.onboarding import store

    info = _advance_to_reveal(client, env)
    with store.get_engine().begin() as conn:
        conn.execute(
            text("UPDATE platrixa_developer_key_deliveries SET expires_at = :t"),
            {"t": datetime.utcnow() - timedelta(minutes=1)},
        )
    response = client.post("/v1/developer/keys/reveal", json={"token": info["reveal_token"]})
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "REVEAL_LINK_EXPIRED"
    assert _scalar(env, "SELECT key_nonce FROM platrixa_developer_key_deliveries") is None


def test_revealed_key_authenticates_against_the_real_gate(client, env):
    info = _activate(client, env)
    response = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VERIFIED"
    assert info["api_key"] not in response.text


def test_revoked_key_stops_authenticating(client, env, owner):
    info = _activate(client, env)
    path = f"/v1/owner/onboarding/tenants/{info['tenant_id']}/keys/{info['key_id']}/revoke"
    revoked = _owner_post(client, path, {})
    assert revoked.status_code == 200, revoked.text
    denied = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert denied.status_code == 401


def test_rotation_replaces_the_key_and_invalidates_the_old_one(client, env, owner):
    info = _activate(client, env)
    rotated = _owner_post(
        client, f"/v1/owner/onboarding/tenants/{info['tenant_id']}/keys/rotate", {}
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["revoked_key_id"] == info["key_id"]
    assert "api_key" not in body  # never returned to the owner

    old = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert old.status_code == 401

    application_id = info["application_id"]
    rows = _rows(
        env,
        "SELECT body_text FROM platrixa_developer_notifications "
        "WHERE application_id = ? AND kind = 'KEY_ROTATION'",
        (application_id,),
    )
    assert rows
    token = re.search(r"#token=([A-Za-z0-9_\-]+)", rows[0]["body_text"]).group(1)
    revealed = client.post("/v1/developer/keys/reveal", json={"token": token})
    assert revealed.status_code == 200
    new_key = revealed.json()["api_key"]
    assert new_key != info["api_key"]
    ok = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": new_key},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert ok.status_code == 200


def test_key_list_and_detail_never_return_key_material(client, env, owner):
    info = _activate(client, env)
    detail = _owner_get(
        client, f"/v1/owner/onboarding/applications/{info['application_id']}"
    )
    assert detail.status_code == 200
    payload = detail.json()
    text = json.dumps(payload)
    assert info["api_key"] not in text
    assert "key_nonce" not in text
    assert payload["key_delivery"][0]["revealed"] is True
    assert payload["key_delivery"][0]["derivable"] is False


# ===========================================================================
# 5. Tenant isolation
# ===========================================================================


def _two_developers(client, env):
    first = _activate(client, env, "asha@acme-labs.example")
    second = _activate(client, env, "ravi@globex.example")
    assert first["tenant_id"] != second["tenant_id"]
    return first, second


def test_developer_cannot_read_another_tenants_jobs(client, env, monkeypatch):
    from backend.onboarding import service

    monkeypatch.setattr(service, "_suspicion_reason", lambda *a, **k: None)
    first, second = _two_developers(client, env)
    now = datetime.utcnow()
    with sqlite3.connect(env.path) as conn:
        conn.execute(
            "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, status, kind, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("job-a", first["tenant_id"], "res-a", "COMPLETED", "document", now, now),
        )
        conn.execute(
            "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, status, kind, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("job-b", second["tenant_id"], "res-b", "QUEUED", "document", now, now),
        )
    listing = client.get(
        "/v1/developer/jobs?tenant_id=" + quote(second["tenant_id"], safe=""),
        headers={"X-Platrixa-API-Key": first["api_key"]},
    )
    assert listing.status_code == 200
    jobs = listing.json()["jobs"]
    assert [job["job_id"] for job in jobs] == ["job-a"]
    assert listing.json()["tenant_id"] == first["tenant_id"]


def test_developer_usage_is_tenant_scoped(client, env, monkeypatch):
    from backend.onboarding import service

    monkeypatch.setattr(service, "_suspicion_reason", lambda *a, **k: None)
    first, second = _two_developers(client, env)
    client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": second["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    usage = client.get("/v1/usage", headers={"X-Platrixa-API-Key": first["api_key"]})
    assert usage.status_code == 200
    assert usage.json()["tenant_id"] == first["tenant_id"]
    assert usage.json()["used"] == 0


def test_browser_supplied_tenant_id_cannot_override_identity(client, env, monkeypatch):
    from backend.onboarding import service

    monkeypatch.setattr(service, "_suspicion_reason", lambda *a, **k: None)
    first, second = _two_developers(client, env)
    response = client.post(
        "/v1/developer/keys/rotate",
        headers={"X-Platrixa-API-Key": first["api_key"]},
        json={"tenant_id": second["tenant_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == first["tenant_id"]
    still_active = _scalar(
        env,
        "SELECT count(*) FROM platrixa_api_keys WHERE tenant_id = ? AND status = 'ACTIVE'",
        (second["tenant_id"],),
    )
    assert int(still_active) == 1


def test_developer_key_cannot_reach_owner_endpoints(client, env):
    info = _activate(client, env)
    response = client.get(
        "/v1/owner/onboarding/overview",
        headers={"X-Platrixa-API-Key": info["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "API_KEY_MANAGEMENT_UNAUTHORIZED"


# ===========================================================================
# 6. Notifications
# ===========================================================================


def test_owner_and_developer_notifications_are_queued(client, env):
    _apply(client)
    rows = _rows(
        env,
        "SELECT audience, kind, recipient, status, body_text FROM platrixa_developer_notifications",
    )
    by_audience = {(row["audience"], row["kind"]): row for row in rows}
    assert ("OWNER", "NEW_ACCESS_REQUEST") in by_audience
    assert ("DEVELOPER", "EMAIL_VERIFICATION") in by_audience
    owner_row = by_audience[("OWNER", "NEW_ACCESS_REQUEST")]
    assert owner_row["recipient"] == "owner@platrixa.test"
    assert owner_row["status"] == "PENDING"
    # Owner body must never contain the verification token.
    developer_row = by_audience[("DEVELOPER", "EMAIL_VERIFICATION")]
    token = re.search(r"#token=([A-Za-z0-9_\-]+)", developer_row["body_text"]).group(1)
    assert token not in owner_row["body_text"]


def test_owner_body_never_contains_key_material(client, env):
    info = _activate(client, env)
    for row in _rows(
        env, "SELECT body_text FROM platrixa_developer_notifications WHERE audience='OWNER'"
    ):
        assert info["api_key"] not in row["body_text"]
        assert info["reveal_token"] not in row["body_text"]
        assert "key_nonce" not in row["body_text"]


def test_provisioning_retry_does_not_duplicate_notifications(client, env):
    from backend.onboarding import service

    info = _advance_to_reveal(client, env)
    service.provision_application(info["application_id"], actor="retry")
    service.provision_application(info["application_id"], actor="retry-again")
    provisioned = _rows(
        env,
        "SELECT id FROM platrixa_developer_notifications "
        "WHERE kind = 'PROVISIONED' AND application_id = ?",
        (info["application_id"],),
    )
    welcome = _rows(
        env,
        "SELECT id FROM platrixa_developer_notifications "
        "WHERE kind = 'ONBOARDING' AND application_id = ?",
        (info["application_id"],),
    )
    assert len(provisioned) == 1
    assert len(welcome) == 1


def test_unconfigured_provider_keeps_notifications_pending(client, env):
    from backend.onboarding import notifications

    _apply(client)
    result = notifications.dispatch_due(10)
    assert result["configured"] is False
    row = _rows(
        env,
        "SELECT status, last_error FROM platrixa_developer_notifications "
        "WHERE kind = 'EMAIL_VERIFICATION'",
    )[0]
    assert row["status"] == "PENDING"
    assert row["last_error"] == "EMAIL_NOT_CONFIGURED"


def test_retryable_failure_schedules_retry(client, env, monkeypatch):
    from backend.onboarding import email, notifications

    _apply(client)

    class _Flaky:
        name = "smtp"
        configured = True

        def send(self, message):
            return email.DeliveryOutcome(ok=False, error="SMTP_UNAVAILABLE", retryable=True)

    monkeypatch.setattr(email, "transport", lambda: _Flaky())
    result = notifications.dispatch_due(10)
    assert result["retried"] >= 1
    row = _rows(
        env,
        "SELECT status, attempts, next_attempt_at FROM platrixa_developer_notifications "
        "WHERE audience = 'DEVELOPER'",
    )[0]
    assert row["status"] == "PENDING"
    assert row["attempts"] == 1
    assert row["next_attempt_at"] is not None


def test_non_retryable_failure_marks_failed_and_clears_body(client, env, monkeypatch):
    from backend.onboarding import email, notifications

    _apply(client)

    class _Rejecting:
        name = "smtp"
        configured = True

        def send(self, message):
            return email.DeliveryOutcome(ok=False, error="SMTP_AUTH_FAILED", retryable=False)

    monkeypatch.setattr(email, "transport", lambda: _Rejecting())
    notifications.dispatch_due(10)
    row = _rows(
        env,
        "SELECT status, last_error, body_text FROM platrixa_developer_notifications "
        "WHERE audience = 'DEVELOPER'",
    )[0]
    assert row["status"] == "FAILED"
    assert row["last_error"] == "SMTP_AUTH_FAILED"
    assert row["body_text"] == ""


def test_successful_delivery_clears_the_token_from_storage(client, env, monkeypatch):
    from backend.onboarding import email, notifications

    _apply(client)
    sent = []

    class _Recording:
        name = "smtp"
        configured = True

        def send(self, message):
            sent.append(message)
            return email.DeliveryOutcome(ok=True)

    monkeypatch.setattr(email, "transport", lambda: _Recording())
    result = notifications.dispatch_due(20)
    assert result["sent"] >= 2
    verification = [
        row for row in _rows(
            env,
            "SELECT status, body_text FROM platrixa_developer_notifications "
            "WHERE kind = 'EMAIL_VERIFICATION'",
        )
    ][0]
    assert verification["status"] == "SENT"
    assert verification["body_text"] == ""
    token = re.search(r"#token=([A-Za-z0-9_\-]+)", sent[0].body).group(1)
    assert token not in _all_text(env)


# ===========================================================================
# 7. Owner dashboard
# ===========================================================================


def test_owner_endpoint_denied_without_assertion(client):
    response = client.get("/v1/owner/onboarding/overview")
    assert response.status_code == 403


def test_owner_endpoint_fails_closed_when_nonce_store_unavailable(client, env):
    # On SQLite the durable nonce store cannot run; the management plane must
    # reject (503) rather than admit the owner request.
    headers = _owner_headers("GET", "/v1/owner/onboarding/overview")
    response = client.get("/v1/owner/onboarding/overview", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "API_KEY_MANAGEMENT_UNAVAILABLE"


def test_owner_assertion_with_wrong_signature_is_rejected(client, env, owner):
    headers = _owner_headers("GET", "/v1/owner/onboarding/overview")
    headers["x-platrixa-assertion-signature"] = "0" * 64
    response = client.get("/v1/owner/onboarding/overview", headers=headers)
    assert response.status_code == 403


def test_owner_assertion_with_wrong_tenant_is_rejected(client, env, owner):
    headers = _owner_headers("GET", "/v1/owner/onboarding/overview")
    headers["x-platrixa-assertion-tenant-id"] = "another-owner"
    response = client.get("/v1/owner/onboarding/overview", headers=headers)
    assert response.status_code == 403


def test_authorized_owner_reads_overview(client, env, owner):
    _apply(client)
    response = _owner_get(client, "/v1/owner/onboarding/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applications_total"] == 1
    assert body["pending_verification"] == 1
    assert body["by_status"]["PENDING_VERIFICATION"] == 1
    assert body["email_provider"]["configured"] is False
    assert body["key_delivery_configured"] is True


def test_owner_suspend_and_restore_is_audited(client, env, owner):
    info = _activate(client, env)
    suspended = _owner_post(
        client, f"/v1/owner/onboarding/applications/{info['application_id']}/suspend",
        {"reason": "abuse investigation"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "SUSPENDED"
    denied = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert denied.status_code == 401

    restored = _owner_post(
        client, f"/v1/owner/onboarding/applications/{info['application_id']}/restore", {}
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"
    allowed = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Sold goods for cash Rs 100"},
    )
    assert allowed.status_code == 200

    actions = [
        row["action"]
        for row in _rows(env, "SELECT action FROM platrixa_developer_onboarding_audit")
    ]
    assert "TENANT_SUSPENDED" in actions
    assert "TENANT_RESTORED" in actions
    actors = {
        row["actor"]
        for row in _rows(env, "SELECT actor FROM platrixa_developer_onboarding_audit")
    }
    assert f"owner:{OWNER_TENANT}" in actors


def test_owner_quota_change_applies_to_tenant(client, env, owner):
    info = _activate(client, env)
    response = _owner_post(
        client, f"/v1/owner/onboarding/tenants/{info['tenant_id']}/quota",
        {"monthly_limit": 250},
    )
    assert response.status_code == 200
    assert response.json()["monthly_limit"] == 250
    assert int(_scalar(
        env, "SELECT monthly_limit FROM platrixa_tenant_quotas WHERE tenant_id = ?",
        (info["tenant_id"],),
    )) == 250

    bad = _owner_post(
        client, f"/v1/owner/onboarding/tenants/{info['tenant_id']}/quota",
        {"monthly_limit": -1},
    )
    assert bad.status_code == 400


def test_owner_review_and_manual_provision(client, env, owner):
    _apply(client, email="someone@gmail.com")
    application_id = _app_id_for(env, "someone@gmail.com")
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    client.post("/v1/developer/verify-email", json={"token": token})
    assert _scalar(
        env, "SELECT status FROM platrixa_developer_access_requests WHERE id = ?", (application_id,)
    ) == "REVIEW_REQUIRED"

    response = _owner_post(
        client, f"/v1/owner/onboarding/applications/{application_id}/provision", {}
    )
    assert response.status_code == 409

    advanced = _owner_post(
        client, f"/v1/owner/onboarding/applications/{application_id}/advance", {}
    )
    assert advanced.status_code == 200
    assert advanced.json()["status"] == "REVIEW_REQUIRED"


def test_owner_application_list_paginates_and_filters(client, env, owner, monkeypatch):
    from backend.onboarding import service

    monkeypatch.setattr(service, "_suspicion_reason", lambda *a, **k: None)
    for email in ("a@acme.example", "b@acme.example", "c@acme.example"):
        _apply(client, email=email)
    application_id = _app_id_for(env, "a@acme.example")
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    client.post("/v1/developer/verify-email", json={"token": token})

    page = _owner_get(client, "/v1/owner/onboarding/applications", "limit=2&offset=0")
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert len(body["applications"]) == 2

    page_two = _owner_get(client, "/v1/owner/onboarding/applications", "limit=2&offset=2")
    assert len(page_two.json()["applications"]) == 1

    active = _owner_get(
        client, "/v1/owner/onboarding/applications", "status=ACTIVE"
    ).json()
    assert active["total"] == 1
    assert active["applications"][0]["email"] == "a@acme.example"


def test_owner_notification_list_and_dispatch(client, env, owner):
    _apply(client)
    listing = _owner_get(client, "/v1/owner/onboarding/notifications")
    assert listing.status_code == 200
    assert len(listing.json()["notifications"]) >= 2
    dispatch = _owner_post(client, "/v1/owner/onboarding/notifications/dispatch", {})
    assert dispatch.status_code == 200
    assert dispatch.json()["configured"] is False


# ===========================================================================
# 8. End-to-end acceptance (Part 12)
# ===========================================================================


def test_end_to_end_new_developer_journey(client, env, owner):
    """Submit → verify → provision → reveal → process → jobs → usage → owner."""
    submitted = _apply(client)
    assert submitted.status_code == 202
    application_id = _app_id_for(env, _APPLICATION["email"])

    verify_token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    verified = client.post("/v1/developer/verify-email", json={"token": verify_token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "ACTIVE"

    status_check = client.get(
        "/v1/developer/access-status",
        params={"application_id": application_id, "email": _APPLICATION["email"]},
    )
    assert status_check.status_code == 200
    assert status_check.json()["status"] == "ACTIVE"

    reveal_token = _token_from_body(env, application_id, "ONBOARDING")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200
    api_key = revealed.json()["api_key"]
    assert revealed.json()["tenant_id"].startswith("dev-")
    assert revealed.json()["api_base_url"] == f"{PUBLIC_ORIGIN}/v1"

    processed = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": api_key},
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
    )
    assert processed.status_code == 200, processed.text
    result = processed.json()
    assert result["status"] == "VERIFIED"
    assert result["api_status"] == "VERIFIED"
    assert result["accounting"] is not None

    usage = client.get("/v1/usage", headers={"X-Platrixa-API-Key": api_key})
    assert usage.status_code == 200
    assert usage.json()["used"] == 1
    assert usage.json()["monthly_limit"] == 50

    jobs = client.get("/v1/developer/jobs", headers={"X-Platrixa-API-Key": api_key})
    assert jobs.status_code == 200

    overview = _owner_get(client, "/v1/owner/onboarding/overview")
    assert overview.status_code == 200
    assert overview.json()["active_developers"] == 1
    assert overview.json()["active_api_keys"] == 1

    # Owner received BOTH channels: dashboard rows and queued email.
    owner_rows = _rows(
        env,
        "SELECT kind, status, recipient FROM platrixa_developer_notifications "
        "WHERE audience = 'OWNER' ORDER BY created_at",
    )
    kinds = [row["kind"] for row in owner_rows]
    assert "NEW_ACCESS_REQUEST" in kinds
    assert "EMAIL_VERIFIED" in kinds
    assert "PROVISIONED" in kinds
    assert all(row["status"] == "PENDING" for row in owner_rows)
    assert all(row["recipient"] == "owner@platrixa.test" for row in owner_rows)

    # The model/engine contract is untouched: VERIFIED came from the engine.
    assert result.get("engine_status", result["status"]) == "VERIFIED"
