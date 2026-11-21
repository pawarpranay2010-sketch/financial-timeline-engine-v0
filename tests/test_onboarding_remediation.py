"""Regression tests for the S-1 … S-9 remediation of Developer Onboarding v1.

Each test maps to one audit finding; the mapping is repeated in the test name
and in the remediation report. Nothing here weakens an existing test: the
original suites still run unchanged.

Covered:
  S-1/S-2  website route policy — one table in three places, default deny,
           path-confusion rejection, no management route reachable through the
           public surface, visitor-scoped job history.
  S-3      trusted-proxy client identity (rightmost-trusted XFF, allowlisted
           header only, spoof-resistant, malformed fallback) and honest 429s.
  S-4/S-5/S-6  durable dispatcher: no SMTP in the request path, lease-based
           claiming, claim-token ownership on acknowledgement, stale recovery.
  S-7      rotation preserves the monthly usage bucket.
  S-8      status lookup by opaque submission reference (and never a tenant id).
  S-9      versioned key-derivation secret: rotating it does not strand a
           pending delivery.
  §D.11    runtime never runs DDL; the migration is a deliberate step.

Run:
    python -m pytest tests/test_onboarding_remediation.py -v
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_ORIGIN = "https://platrixa.test"
OWNER_EMAIL = "owner@platrixa.test"

APPLICATION = {
    "name": "Asha Verma",
    "email": "asha@acme-labs.example",
    "company": "Acme Labs",
    "building": "A reconciliation service that validates ledger entries before posting.",
    "expected_usage": "small",
    "profile_url": None,
}

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
        visitor_id VARCHAR(64) NULL,
        result_id  VARCHAR(64),
        status     VARCHAR(16),
        kind       VARCHAR(16),
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )
    """,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    url = f"sqlite:///{tmp_path}/store.db"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/unused-app.db")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "d" * 40)
    monkeypatch.delenv("PLATRIXA_KEY_DELIVERY_SECRET_PREVIOUS", raising=False)
    monkeypatch.delenv("PLATRIXA_KEY_DELIVERY_SECRET_VERSION", raising=False)
    monkeypatch.setenv("PLATRIXA_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("PLATRIXA_OWNER_NOTIFICATION_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", "v" * 40)
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_DAY", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP", "1000")
    monkeypatch.delenv("PLATRIXA_ONBOARDING_AUTO_MIGRATE", raising=False)
    monkeypatch.delenv("PLATRIXA_ONBOARDING_DISPATCH_TOKEN", raising=False)

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


class _StubKernel:
    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return None

    def process(self, raw_input: str, request_id=None):
        return SimpleNamespace(
            status="VERIFIED",
            status_label="VERIFIED",
            success=True,
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
    app.state.platrixa_client = _StubKernel()
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _apply(client, **overrides):
    payload = dict(APPLICATION)
    payload.update(overrides)
    return client.post("/v1/developer/access-requests", json=payload)


def _token_from_body(env, application_id, kind):
    rows = _rows(
        env,
        "SELECT body_text FROM platrixa_developer_notifications "
        "WHERE application_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
        (application_id, kind),
    )
    assert rows, f"no {kind} notification queued"
    match = re.search(r"#token=([A-Za-z0-9_\-]+)", rows[0]["body_text"])
    assert match
    return match.group(1)


def _app_id_for(env, email):
    return _scalar(
        env, "SELECT id FROM platrixa_developer_access_requests WHERE email = ?", (email,)
    )


def _activate(client, env, email=None):
    email = email or APPLICATION["email"]
    assert _apply(client, email=email).status_code == 202
    application_id = _app_id_for(env, email)
    token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    verified = client.post("/v1/developer/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "ACTIVE"
    reveal_token = _token_from_body(env, application_id, "ONBOARDING")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200, revealed.text
    body = revealed.json()
    return {
        "application_id": application_id,
        "tenant_id": body["tenant_id"],
        "api_key": body["api_key"],
        "key_id": body["key_id"],
    }


class _Headers(dict):
    def get(self, name, default=None):
        return dict.get(self, name.lower(), default)


class _Request:
    """The two attributes ``api.rate_limit`` reads, and nothing else."""

    def __init__(self, headers=None, peer="10.0.0.9"):
        self.headers = _Headers({k.lower(): v for k, v in (headers or {}).items()})
        self.client = SimpleNamespace(host=peer)


# ---------------------------------------------------------------------------
# S-1 / S-2 — website route policy
# ---------------------------------------------------------------------------


def _js_route_specs(path: Path):
    text = path.read_text(encoding="utf-8")
    return re.findall(
        r'\{\s*methods:\s*\[([^\]]*)\]\s*,\s*pattern:\s*"([^"]+)"\s*\}', text
    )


def test_route_policy_table_is_identical_in_all_three_places():
    from backend.auth import surface_policy

    python_specs = [
        (tuple(sorted(methods)), pattern) for methods, pattern in surface_policy.WEBSITE_ROUTE_SPECS
    ]

    for path in (
        REPO_ROOT / "frontend" / "functions" / "v1" / "[[path]].js",
        REPO_ROOT / "frontend" / "web" / "lib" / "website-route-policy.js",
    ):
        js_specs = []
        for raw_methods, pattern in _js_route_specs(path):
            methods = tuple(
                sorted(m.strip().strip('"') for m in raw_methods.split(",") if m.strip())
            )
            js_specs.append((methods, pattern))
        assert js_specs == python_specs, f"route table drift in {path}"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/developer/keys/rotate",
        "/v1/developer/jobs",
        "/v1/developer/api-keys",
        "/v1/developer/api-keys/PLX123",
        "/v1/developer/usage",
        "/v1/developer/requests",
        "/v1/owner/onboarding/overview",
        "/v1/owner/onboarding/applications",
        "/v1/operator/manage",
        "/v1/internal/onboarding/dispatch",
        "/v1/api-keys",
        "/v1/admin",
        "/v1",
        "/",
        "/v1/developer/keys/reveal/../rotate",
        "/v1/developer/%2e%2e/keys/rotate",
        "/v1//developer/keys/rotate",
        "/v1/developer/keys/rotate/",
        "/v1/developer/keys/rotate?x=1",
        "/v1/developer/keys\\rotate",
    ],
)
def test_management_and_malformed_paths_are_not_forwarded(path):
    from backend.auth import surface_policy

    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        assert not surface_policy.website_route_allowed(method, path), (method, path)


def test_public_developer_routes_are_forwarded_for_their_exact_method_only():
    from backend.auth import surface_policy

    allowed = [
        ("POST", "/v1/developer/access-requests"),
        ("POST", "/v1/developer/verify-email"),
        ("POST", "/v1/developer/resend-verification"),
        ("GET", "/v1/developer/access-status"),
        ("POST", "/v1/developer/keys/reveal"),
        ("POST", "/v1/process"),
        ("GET", "/v1/jobs/abc-123"),
    ]
    for method, path in allowed:
        assert surface_policy.website_route_allowed(method, path), (method, path)
    # Wrong method on a known path is still refused (default deny, not prefix).
    assert not surface_policy.website_route_allowed("GET", "/v1/developer/keys/reveal")
    assert not surface_policy.website_route_allowed("POST", "/v1/developer/access-status")
    assert not surface_policy.website_route_allowed("DELETE", "/v1/process")


def test_backend_denies_website_surface_management_routes_before_auth(client):
    """Defence in depth: the edge is not the only enforcement point."""

    headers = {"x-platrixa-surface": "website"}
    for method, path in (
        ("POST", "/v1/developer/keys/rotate"),
        ("GET", "/v1/developer/jobs"),
        ("GET", "/v1/developer/api-keys"),
        ("GET", "/v1/developer/usage"),
        ("GET", "/v1/owner/onboarding/overview"),
        ("POST", "/v1/internal/onboarding/dispatch"),
    ):
        response = client.request(method, path, headers=headers, json={} if method == "POST" else None)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_backend_requires_a_verified_visitor_for_every_website_request(client):
    # Allowlisted path, no assertion: fail closed, never a tenant-only fallback.
    response = client.post(
        "/v1/developer/access-requests",
        headers={"x-platrixa-surface": "website"},
        json=dict(APPLICATION),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VISITOR_AUTH_REQUIRED"


def test_rotation_refuses_the_website_surface_even_if_it_reached_the_route(env, monkeypatch):
    """Surface marker without the middleware scope must still be refused."""
    from api.routes import developer_onboarding

    request = SimpleNamespace(headers={"x-platrixa-api-key": "plx_live_" + "a" * 43})
    monkeypatch.setattr(
        developer_onboarding._async_jobs, "current_visitor_scope", lambda: "visitor-0123456789ab"
    )
    with pytest.raises(Exception) as excinfo:
        developer_onboarding.rotate_key(request, None)
    assert getattr(excinfo.value, "code", "") == "FORBIDDEN"


def test_visitor_jobs_are_scoped_to_the_verified_visitor(env):
    from backend.onboarding import service

    tenant = "dev-aaaaaaaaaaaa"
    other_tenant = "dev-bbbbbbbbbbbb"
    mine = "visitor-0123456789ab"
    theirs = "visitor-fedcba987654"
    now = "2026-01-01 00:00:00"
    conn = sqlite3.connect(env.path)
    try:
        for job_id, job_tenant, visitor in (
            ("job-mine", tenant, mine),
            ("job-theirs", tenant, theirs),
            ("job-other-tenant", other_tenant, mine),
            ("job-legacy", tenant, None),
        ):
            conn.execute(
                "INSERT INTO platrixa_async_jobs "
                "(job_id, tenant_id, visitor_id, result_id, status, kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'COMPLETED', 'process', ?, ?)",
                (job_id, job_tenant, visitor, f"res-{job_id}", now, now),
            )
        conn.commit()
    finally:
        conn.close()

    scoped = service.visitor_jobs(tenant, mine)
    ids = {job["job_id"] for job in scoped["jobs"]}
    assert ids == {"job-mine"}, ids
    assert scoped["scope"] == "visitor"

    with pytest.raises(Exception) as excinfo:
        service.visitor_jobs(tenant, "")
    assert getattr(excinfo.value, "code", "") == "VISITOR_AUTH_REQUIRED"


def test_jobs_route_uses_visitor_scope_when_the_surface_is_website(client, env):
    from api.routes import developer_onboarding
    from backend.auth import async_jobs
    from starlette.requests import Request

    # A tenant-only read is what the audit objected to; the route must switch
    # to the visitor scope whenever the middleware bound one. The tenant and
    # key come from the real provisioning path, so authentication is real.
    info = _activate(client, env)
    tenant = info["tenant_id"]
    visitor = "visitor-0123456789ab"

    conn = sqlite3.connect(env.path)
    try:
        for job_id, job_visitor in (
            ("job-in-scope", visitor),
            ("job-out-of-scope", "visitor-999999999999"),
        ):
            conn.execute(
                "INSERT INTO platrixa_async_jobs "
                "(job_id, tenant_id, visitor_id, result_id, status, kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'COMPLETED', 'process', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                (job_id, tenant, job_visitor, f"res-{job_id}"),
            )
        conn.commit()
    finally:
        conn.close()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/developer/jobs",
        "query_string": b"",
        "headers": [(b"x-platrixa-api-key", info["api_key"].encode())],
        "server": ("testserver", 80),
        "client": ("10.0.0.9", 1234),
        "scheme": "http",
    }
    request = Request(scope)

    token = async_jobs.set_visitor_scope(visitor)
    try:
        body = developer_onboarding.jobs(request, limit=20, offset=0)
    finally:
        async_jobs.reset_visitor_scope(token)
    assert body["scope"] == "visitor"
    assert {job["job_id"] for job in body["jobs"]} == {"job-in-scope"}

    # Developer (non-website) surface keeps the tenant-wide history.
    body = developer_onboarding.jobs(request, limit=20, offset=0)
    assert "scope" not in body
    assert {job["job_id"] for job in body["jobs"]} == {"job-in-scope", "job-out-of-scope"}


# ---------------------------------------------------------------------------
# S-3 — client identity behind the proxy
# ---------------------------------------------------------------------------


def test_client_identity_prefers_the_allowlisted_edge_header(monkeypatch):
    from api.rate_limit import _client_key

    monkeypatch.setenv("PLATRIXA_CLIENT_IP_HEADER", "cf-connecting-ip")
    monkeypatch.setenv("PLATRIXA_TRUSTED_PROXY_COUNT", "2")
    request = _Request(
        headers={
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "1.1.1.1, 198.51.100.4, 203.0.113.7",
        },
        peer="10.0.0.9",
    )
    assert _client_key(request) == "203.0.113.7"


def test_client_identity_takes_the_rightmost_trusted_hop_not_the_spoofed_left(monkeypatch):
    from api.rate_limit import _client_key

    monkeypatch.delenv("PLATRIXA_CLIENT_IP_HEADER", raising=False)
    monkeypatch.setenv("PLATRIXA_TRUSTED_PROXY_COUNT", "2")
    # The caller set "1.2.3.4" (leftmost noise); the two trusted hops appended
    # 198.51.100.4 and 203.0.113.7, so the identity is the entry two hops from
    # the right — never the attacker-chosen leftmost value.
    request = _Request(headers={"x-forwarded-for": "1.2.3.4, 198.51.100.4, 203.0.113.7"})
    assert _client_key(request) == "198.51.100.4"

    monkeypatch.setenv("PLATRIXA_TRUSTED_PROXY_COUNT", "1")
    one_hop = _Request(headers={"x-forwarded-for": "1.2.3.4, 198.51.100.4"})
    assert _client_key(one_hop) == "198.51.100.4"


def test_client_identity_never_uses_xff_without_a_trusted_proxy(monkeypatch):
    from api.rate_limit import _client_key

    monkeypatch.delenv("PLATRIXA_TRUSTED_PROXY_COUNT", raising=False)
    monkeypatch.delenv("PLATRIXA_CLIENT_IP_HEADER", raising=False)
    request = _Request(headers={"x-forwarded-for": "1.2.3.4"})
    assert _client_key(request) == "10.0.0.9"


@pytest.mark.parametrize(
    "header_value",
    ["not-an-ip", "1.2.3.4, 5.6.7.8", "", "1.2.3.4:99999", "[fe80::1", "1.2.3.4\r\n5.6.7.8"],
)
def test_client_identity_falls_back_to_the_peer_on_a_malformed_header(monkeypatch, header_value):
    from api.rate_limit import _client_key

    monkeypatch.setenv("PLATRIXA_CLIENT_IP_HEADER", "cf-connecting-ip")
    request = _Request(headers={"cf-connecting-ip": header_value})
    assert _client_key(request) == "10.0.0.9"


def test_client_identity_ignores_an_unlisted_header_name(monkeypatch):
    from api.rate_limit import _client_key

    # Only the fixed allowlist may be configured, so a browser header such as
    # x-platrixa-visitor-id can never be trusted by accident.
    monkeypatch.setenv("PLATRIXA_CLIENT_IP_HEADER", "x-forwarded-host")
    request = _Request(headers={"x-forwarded-host": "203.0.113.7"})
    assert _client_key(request) == "10.0.0.9"


def test_ipv6_forms_are_canonicalised(monkeypatch):
    from api.rate_limit import _client_key

    monkeypatch.setenv("PLATRIXA_CLIENT_IP_HEADER", "cf-connecting-ip")
    assert _client_key(_Request(headers={"cf-connecting-ip": "[2001:db8::1]:443"})) == "2001:db8::1"
    assert _client_key(_Request(headers={"cf-connecting-ip": "::ffff:203.0.113.7"})) == "203.0.113.7"


def test_per_client_onboarding_cap_is_honest_not_silent(env, monkeypatch):
    """Two distinct clients get independent buckets; the third hit is a 429."""
    from api.main import create_app

    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "2")
    app = create_app()
    app.state.platrixa_client = _StubKernel()
    with TestClient(app, client=("198.51.100.4", 1234)) as scoped_client:
        assert _apply(scoped_client, email="a@acme-a.example").status_code == 202
        assert _apply(scoped_client, email="b@acme-b.example").status_code == 202
        third = _apply(scoped_client, email="c@acme-c.example")
        assert third.status_code == 429, third.text
        assert third.json()["error"]["code"] == "RATE_LIMITED"

    with TestClient(app, client=("203.0.113.9", 4321)) as other_client:
        assert _apply(other_client, email="d@acme-d.example").status_code == 202


# ---------------------------------------------------------------------------
# S-4 / S-5 / S-6 — durable dispatcher
# ---------------------------------------------------------------------------


def test_no_smtp_runs_inside_the_request_path(client, env, monkeypatch):
    from backend.onboarding import email, notifications

    sent = []

    class _Transport:
        name = "smtp"
        configured = True

        def send(self, message):
            sent.append(message)

            class _Ok:
                ok = True
                error = None
                retryable = False

            return _Ok()

    monkeypatch.setattr(email, "transport", lambda: _Transport())
    assert _apply(client).status_code == 202
    assert sent == [], "submission must only enqueue, never send"
    assert _rows(env, "SELECT status FROM platrixa_developer_notifications")
    assert notifications.dispatch_due(20)["sent"] >= 1
    assert sent, "the dispatcher is what sends"


def test_dispatch_claims_are_leased_and_only_the_owner_may_acknowledge(client, env, monkeypatch):
    from backend.onboarding import email, notifications

    _apply(client)

    stolen = {}

    class _Thief:
        name = "smtp"
        configured = True

        def send(self, message):
            # A concurrent dispatcher recovers the expired lease mid-send.
            conn = sqlite3.connect(env.path)
            try:
                stolen["token"] = conn.execute(
                    "SELECT claim_token FROM platrixa_developer_notifications LIMIT 1"
                ).fetchone()[0]
                conn.execute(
                    "UPDATE platrixa_developer_notifications "
                    "SET claim_token = 'someone-else' WHERE 1 = 1"
                )
                conn.commit()
            finally:
                conn.close()

            class _Ok:
                ok = True
                error = None
                retryable = False

            return _Ok()

    monkeypatch.setattr(email, "transport", lambda: _Thief())
    result = notifications.dispatch_due(20)
    assert result["lost"] >= 1, result
    assert result["sent"] == 0, result
    # A stale send must never write a terminal state.
    statuses = {row["status"] for row in _rows(env, "SELECT status FROM platrixa_developer_notifications")}
    assert "SENT" not in statuses


def test_expired_lease_is_recovered_and_delivered_once(env, monkeypatch):
    from backend.onboarding import email, notifications

    monkeypatch.setenv("PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS", "1")

    class _Down:
        name = "smtp"
        configured = True

        def send(self, message):
            class _Fail:
                ok = False
                error = "SMTP_UNAVAILABLE"
                retryable = True

            return _Fail()

    monkeypatch.setattr(email, "transport", lambda: _Down())

    # Build the row through the real path so the schema is exercised.
    from api.main import create_app

    app = create_app()
    app.state.platrixa_client = _StubKernel()
    with TestClient(app) as scoped:
        assert _apply(scoped).status_code == 202

    first = notifications.dispatch_due(20)
    assert first["retried"] >= 1

    # Simulate a crashed worker holding the row: PENDING with a stale lease.
    conn = sqlite3.connect(env.path)
    try:
        conn.execute(
            "UPDATE platrixa_developer_notifications "
            "SET status = 'SENDING', claim_token = 'dead-worker', "
            "    lease_expires_at = '2000-01-01 00:00:00', "
            "    next_attempt_at = NULL"
        )
        conn.commit()
    finally:
        conn.close()

    delivered = []

    class _Up:
        name = "smtp"
        configured = True

        def send(self, message):
            delivered.append(message)

            class _Ok:
                ok = True
                error = None
                retryable = False

            return _Ok()

    monkeypatch.setattr(email, "transport", lambda: _Up())
    second = notifications.dispatch_due(20)
    assert second["recovered"] >= 1, second
    assert second["sent"] >= 1, second
    assert len(delivered) == second["sent"]
    assert all(
        row["status"] == "SENT"
        for row in _rows(env, "SELECT status FROM platrixa_developer_notifications")
    )


def test_dispatcher_cli_runs_one_pass_and_exits(env):
    from backend.onboarding import dispatcher

    assert dispatcher.main(["--once"]) == 0


# ---------------------------------------------------------------------------
# S-7 — quota survives rotation
# ---------------------------------------------------------------------------


def test_rotation_preserves_the_monthly_usage_bucket(client, env):
    info = _activate(client, env)
    processed = client.post(
        "/v1/process",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
    )
    assert processed.status_code == 200, processed.text
    usage_before = _rows(
        env,
        "SELECT current_month_usage, usage_month FROM platrixa_tenant_quotas WHERE is_active = 1",
    )[0]
    assert usage_before["current_month_usage"] == 1

    rotated = client.post(
        "/v1/developer/keys/rotate",
        headers={"X-Platrixa-API-Key": info["api_key"]},
        json={},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["current_month_usage"] == 1, "rotation must not reset the billing bucket"
    assert body["usage_month"] == usage_before["usage_month"]

    active = _rows(
        env,
        "SELECT api_key_hash, current_month_usage, usage_month FROM platrixa_tenant_quotas "
        "WHERE is_active = 1",
    )
    assert len(active) == 1
    assert active[0]["current_month_usage"] == 1

    # The revoked key stops authenticating; the replacement's one-time link
    # still delivers a key bound to the SAME carried-over quota.
    old = client.get("/v1/usage", headers={"X-Platrixa-API-Key": info["api_key"]})
    assert old.status_code in (401, 403), old.text

    reveal_token = _token_from_body(env, info["application_id"], "KEY_ROTATION")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200, revealed.text
    new_key = revealed.json()["api_key"]
    usage = client.get("/v1/usage", headers={"X-Platrixa-API-Key": new_key})
    assert usage.status_code == 200
    assert usage.json()["used"] == 1, usage.json()


# ---------------------------------------------------------------------------
# S-8 — opaque submission references
# ---------------------------------------------------------------------------


def test_status_lookup_uses_the_opaque_reference_and_never_a_tenant_id(client, env):
    submitted = _apply(client)
    assert submitted.status_code == 202
    reference = submitted.json()["application_reference"]
    assert reference and reference != _app_id_for(env, APPLICATION["email"])

    ok = client.get(
        "/v1/developer/access-status",
        params={"reference": reference, "email": APPLICATION["email"]},
    )
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["application_reference"] == reference
    assert "tenant_id" not in payload

    # The reference alone is not a capability: the applied address must match.
    wrong_email = client.get(
        "/v1/developer/access-status",
        params={"reference": reference, "email": "someone-else@acme-labs.example"},
    )
    assert wrong_email.status_code == 404

    unknown = client.get(
        "/v1/developer/access-status",
        params={"reference": str(uuid.uuid4()), "email": APPLICATION["email"]},
    )
    assert unknown.status_code == 404


def test_duplicate_submission_returns_the_same_reference(client, env):
    first = _apply(client)
    second = _apply(client)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["application_reference"]


# ---------------------------------------------------------------------------
# S-9 — versioned derivation secret
# ---------------------------------------------------------------------------


def test_rotating_the_delivery_secret_does_not_strand_a_pending_key(client, env, monkeypatch):
    assert _apply(client).status_code == 202
    application_id = _app_id_for(env, APPLICATION["email"])
    verify_token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    assert client.post("/v1/developer/verify-email", json={"token": verify_token}).json()["status"] == "ACTIVE"
    reveal_token = _token_from_body(env, application_id, "ONBOARDING")

    # Operator rotates the secret: version 3 becomes current, the old value is
    # retained as PREVIOUS for outstanding deliveries.
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "n" * 40)
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET_PREVIOUS", "d" * 40)
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET_VERSION", "3")

    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200, revealed.text
    api_key = revealed.json()["api_key"]
    usage = client.get("/v1/usage", headers={"X-Platrixa-API-Key": api_key})
    assert usage.status_code == 200


def test_unrecoverable_secret_fails_honestly(client, env, monkeypatch):
    assert _apply(client).status_code == 202
    application_id = _app_id_for(env, APPLICATION["email"])
    verify_token = _token_from_body(env, application_id, "EMAIL_VERIFICATION")
    client.post("/v1/developer/verify-email", json={"token": verify_token})
    reveal_token = _token_from_body(env, application_id, "ONBOARDING")

    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "z" * 40)
    monkeypatch.delenv("PLATRIXA_KEY_DELIVERY_SECRET_PREVIOUS", raising=False)
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET_VERSION", "4")

    response = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KEY_DELIVERY_INTEGRITY"


# ---------------------------------------------------------------------------
# §D.11 — migration is deliberate, runtime does no DDL
# ---------------------------------------------------------------------------


def test_runtime_refuses_to_create_schema(tmp_path, monkeypatch):
    from backend.auth import gate
    from backend.onboarding import service, store

    url = f"sqlite:///{tmp_path}/empty.db"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/unused.db")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.delenv("PLATRIXA_ONBOARDING_AUTO_MIGRATE", raising=False)
    store.reset_cache()
    gate._session_factory_cache.clear()

    with pytest.raises(Exception) as excinfo:
        service.submit_access_request(dict(APPLICATION), client_ip="198.51.100.4")
    assert getattr(excinfo.value, "code", "") == "ONBOARDING_SCHEMA_NOT_READY"

    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "platrixa_developer_access_requests" not in tables, "runtime must not create tables"

    store.reset_cache()
    gate._session_factory_cache.clear()


def test_migration_init_is_idempotent_and_verify_detects_gaps(tmp_path, monkeypatch):
    from backend.auth import init_onboarding
    from backend.onboarding import store

    url = f"sqlite:///{tmp_path}/init.db"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    store.reset_cache()

    assert init_onboarding.main(["--verify"]) == 1, "an empty store must fail verification"
    assert init_onboarding.main([]) == 0
    assert init_onboarding.main([]) == 0, "the migration must be re-runnable"
    assert init_onboarding.main(["--verify"]) == 0

    store.reset_cache()


# ---------------------------------------------------------------------------
# Operational dispatch endpoint
# ---------------------------------------------------------------------------


def test_ops_dispatch_endpoint_is_disabled_without_a_token(client):
    response = client.post("/v1/internal/onboarding/dispatch")
    assert response.status_code == 404
    response = client.post(
        "/v1/internal/onboarding/dispatch", headers={"x-platrixa-dispatch-token": "x" * 20}
    )
    assert response.status_code == 404


def test_ops_dispatch_endpoint_requires_the_exact_token(client, monkeypatch):
    monkeypatch.setenv("PLATRIXA_ONBOARDING_DISPATCH_TOKEN", "t" * 24)
    assert client.post("/v1/internal/onboarding/dispatch").status_code == 403
    assert (
        client.post(
            "/v1/internal/onboarding/dispatch", headers={"x-platrixa-dispatch-token": "wrong"}
        ).status_code
        == 403
    )
    accepted = client.post(
        "/v1/internal/onboarding/dispatch", headers={"x-platrixa-dispatch-token": "t" * 24}
    )
    assert accepted.status_code == 200, accepted.text
    assert "attempted" in accepted.json()


# ---------------------------------------------------------------------------
# S-10 — cleartext SMTP AUTH is refused, not silently sent
# ---------------------------------------------------------------------------


def test_smtp_refuses_to_authenticate_without_tls(monkeypatch):
    from backend.onboarding.email import Message, SmtpTransport

    monkeypatch.delenv("PLATRIXA_SMTP_ALLOW_PLAINTEXT_AUTH", raising=False)

    transport = SmtpTransport(
        host="smtp.example.test",
        port=587,
        sender="no-reply@platrixa.test",
        username="user",
        password="hunter2",
        starttls=False,
        implicit_tls=False,
    )
    outcome = transport.send(Message(to="dev@acme-labs.example", subject="s", body="b"))
    assert outcome.ok is False
    assert outcome.error == "SMTP_INSECURE_AUTH"
    assert outcome.retryable is False, "a misconfiguration must not be retried forever"
    assert transport.probe()["reason"] == "SMTP_INSECURE_AUTH"


def test_smtp_without_credentials_may_use_a_plain_relay():
    """No username means no secret to leak: submission-only relays stay usable."""
    from backend.onboarding.email import Message, SmtpTransport

    transport = SmtpTransport(
        host="127.0.0.1",
        port=1,
        sender="no-reply@platrixa.test",
        username="",
        password="",
        starttls=False,
        implicit_tls=False,
    )
    # Connection refused (port 1) is a transport fault, never SMTP_INSECURE_AUTH.
    outcome = transport.send(Message(to="dev@acme-labs.example", subject="s", body="b"))
    assert outcome.ok is False
    assert outcome.error == "SMTP_UNAVAILABLE"
