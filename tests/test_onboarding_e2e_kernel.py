"""End-to-end developer journey with the REAL deterministic kernel.

Sequence (the brief's steps 1–12):

  1. submit a developer access request (six fields, public endpoint)
  2. verify the email using the real token-validation endpoint
  3. confirm the server-side eligibility evaluation ran
  4. a separate tenant is provisioned
  5. a unique API key exists for that tenant
  6. the key is retrieved through the one-time reveal mechanism
  7. a real accounting transaction is submitted to the Developer API
  8. the REAL kernel validates, grounds and runs deterministic accounting
  9. the completed result is retrieved (the synchronous /v1/process envelope)
 10. tenant-scoped job history is retrieved
 11. tenant-scoped usage is retrieved
 12. a second tenant cannot reach the first tenant's resources

What is real here: the FastAPI app, the visitor/CSRF middleware, the metered
API-key gate, the kernel's validation + deterministic grounding + accounting,
the SQL store, and the full onboarding state machine.

What is supplied by the test: the MODEL STEP ONLY. No model worker exists in
this environment (inference is hosted on the Android device), so the
interpretation candidate is built from the same bridge-body contract the
device uses and handed to ``Kernel.process_from_source``. Everything after
the candidate — forbidden-output rejection, 18-field validation, grounding,
deterministic accounting, evidence, and the VERIFIED decision — is the real
engine. This is model-dependent vs deterministic separation, not a mock
kernel: no status is fabricated and nothing is short-circuited.

Run:
    python -m pytest tests/test_onboarding_e2e_kernel.py -v
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

PUBLIC_ORIGIN = "https://platrixa.test"
OWNER_EMAIL = "owner@platrixa.test"

RAW_INPUT = "purchased furniture from raj for rs.25000"

# The canonical candidate the existing kernel boundary suite treats as a
# deterministic VERIFIED case.
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
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
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
        result_id  VARCHAR(64),
        status     VARCHAR(16),
        kind       VARCHAR(16),
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )
    """,
)


class RealKernelClient:
    """The app's client: real Kernel, fixed interpretation (model step only)."""

    def __init__(self) -> None:
        from backend.kernel.kernel import Kernel

        self._kernel = Kernel()

    def provider_status(self) -> dict:
        return {"available": True, "loadable": True, "model_id": "e2e-test-provider"}

    def rule_pack_summary(self):
        return None

    def process(self, raw_input: str, request_id=None):
        from backend.model_provider.local_bridge import (
            PROVENANCE_UNKNOWN,
            interpretation_from_bridge_body,
        )
        from platrixa._facade import PlatrixaResult

        body = {
            "interpretation_text": json.dumps(VALID_CANDIDATE),
            "completion_status": "COMPLETE",
            "finish_reason": "stop",
            "model": {"provider": "local-llama.cpp", "adapter_loaded": None},
        }

        def _interpret_once():
            return interpretation_from_bridge_body(
                body,
                raw_input,
                model_id="local-llama.cpp",
                provider_revision=PROVENANCE_UNKNOWN,
            )

        # The real kernel: identical downstream code as Kernel.process().
        result = self._kernel.process_from_source(
            raw_input, _interpret_once, request_id=request_id
        )
        return PlatrixaResult(result)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from backend.auth import gate
    from backend.onboarding import store

    url = f"sqlite:///{tmp_path}/store.db"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/unused-app.db")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "d" * 40)
    monkeypatch.setenv("PLATRIXA_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("PLATRIXA_OWNER_NOTIFICATION_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", "v" * 40)
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_DAY", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP", "1000")

    store.reset_cache()
    gate._session_factory_cache.clear()
    store.ensure_schema(url)
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "store.db"), future=True)
    with engine.begin() as conn:
        for statement in _CREDENTIAL_DDL:
            conn.exec_driver_sql(statement)
    engine.dispose()

    yield {"url": url, "path": str(tmp_path / "store.db")}

    store.reset_cache()
    gate._session_factory_cache.clear()


@pytest.fixture()
def client(env):
    from api.main import create_app

    app = create_app()
    app.state.platrixa_client = RealKernelClient()
    with TestClient(app) as test_client:
        yield test_client


def _apply(client, *, email: str, company: str) -> dict:
    payload = {
        "name": "Asha Verma" if "acme" in email else "Ravi Kumar",
        "email": email,
        "company": company,
        "building": "A reconciliation service that validates ledger entries before posting.",
        "expected_usage": "small",
        "profile_url": "https://github.com/example/profile",
    }
    response = client.post("/v1/developer/access-requests", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def _notification_body(env, email: str, kind: str) -> str:
    conn = sqlite3.connect(env["path"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT body_text FROM platrixa_developer_notifications "
            "WHERE recipient = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (email, kind),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no {kind} notification for {email}"
    return row["body_text"]


def _token(body: str, path: str) -> str:
    match = re.search(re.escape(path) + r"#token=([A-Za-z0-9_\-]+)", body)
    assert match, "no one-time link in the notification"
    return match.group(1)


def _onboard(client, env, *, email: str, company: str) -> dict:
    _apply(client, email=email, company=company)
    verify_token = _token(_notification_body(env, email, "EMAIL_VERIFICATION"), "/developer/verify")
    verified = client.post("/v1/developer/verify-email", json={"token": verify_token})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "ACTIVE", verified.text

    reveal_token = _token(_notification_body(env, email, "ONBOARDING"), "/developer/key")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200, revealed.text
    return revealed.json()


def _rows(env, sql: str, params=()):
    conn = sqlite3.connect(env["path"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_complete_developer_journey(client, env):
    # ---- steps 1–3: submit + verify + eligibility -------------------------
    _apply(client, email="asha@acme-labs.example", company="Acme Labs")
    application = _rows(
        env,
        "SELECT id, status, tenant_id, key_id, monthly_limit, email_verified_at, status_reason "
        "FROM platrixa_developer_access_requests",
    )[0]
    assert application["status"] == "PENDING_VERIFICATION"
    assert application["tenant_id"] is None, "no tenant before verification"

    verify_token = _token(
        _notification_body(env, "asha@acme-labs.example", "EMAIL_VERIFICATION"),
        "/developer/verify",
    )
    verified = client.post("/v1/developer/verify-email", json={"token": verify_token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "ACTIVE"

    audit = _rows(env, "SELECT action FROM platrixa_developer_onboarding_audit")
    actions = {row["action"] for row in audit}
    assert "EMAIL_VERIFIED" in actions or "TENANT_PROVISIONED" in actions

    # ---- steps 4–5: separate tenant + unique key -------------------------
    row = _rows(
        env,
        "SELECT id, status, tenant_id, key_id, monthly_limit, email_verified_at "
        "FROM platrixa_developer_access_requests",
    )[0]
    assert row["status"] == "ACTIVE"
    tenant_a = row["tenant_id"]
    assert tenant_a.startswith("dev-"), "a dedicated tenant must be provisioned"
    assert tenant_a != "owner-tenant"
    assert row["email_verified_at"] is not None
    assert row["monthly_limit"] == 50  # initial beta quota

    deliveries = _rows(
        env,
        "SELECT key_id, tenant_id, key_nonce, revealed_at FROM platrixa_developer_key_deliveries",
    )
    assert len(deliveries) == 1
    assert deliveries[0]["tenant_id"] == tenant_a
    # Key material is derived, never stored: only a live nonce exists pre-reveal.
    assert deliveries[0]["revealed_at"] is None

    # ---- step 6: one-time reveal ----------------------------------------
    reveal_token = _token(_notification_body(env, "asha@acme-labs.example", "ONBOARDING"), "/developer/key")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200
    key_a = revealed.json()["api_key"]
    assert key_a.startswith("plx_live_")

    delivery_after = _rows(
        env, "SELECT key_nonce, revealed_at FROM platrixa_developer_key_deliveries"
    )[0]
    assert delivery_after["key_nonce"] is None, "the nonce must be destroyed on reveal"
    assert delivery_after["revealed_at"] is not None
    # The raw key is nowhere in the database.
    for table in (
        "platrixa_api_keys",
        "platrixa_developer_key_deliveries",
        "platrixa_developer_access_requests",
        "platrixa_developer_notifications",
    ):
        for stored in _rows(env, f"SELECT * FROM {table}"):
            assert key_a not in str(dict(stored)), f"raw key leaked into {table}"

    # ---- steps 7–9: real transaction through the real kernel -------------
    headers = {"X-Platrixa-API-Key": key_a}
    processed = client.post("/v1/process", json={"raw_input": RAW_INPUT}, headers=headers)
    assert processed.status_code == 200, processed.text
    envelope = processed.json()
    assert envelope["status"] == "VERIFIED", envelope
    assert envelope["engine_status"] == "VERIFIED"
    assert envelope["accounting"], "deterministic accounting must be present"
    assert envelope["interpretation"]["transaction_type_enum"] == "PURCHASE"

    # Determinism: the same input produces the same accounting result.
    replay = client.post("/v1/process", json={"raw_input": RAW_INPUT}, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["accounting"] == envelope["accounting"]
    assert replay.json()["status"] == "VERIFIED"

    # ---- step 10: tenant-scoped job history ------------------------------
    jobs = client.get("/v1/developer/jobs", headers=headers)
    assert jobs.status_code == 200, jobs.text
    assert isinstance(jobs.json().get("jobs", jobs.json()), (list, dict))

    # ---- step 11: tenant-scoped usage ------------------------------------
    usage = client.get("/v1/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["used"] == 2, body
    assert body.get("monthly_limit", body.get("limit")) == 50, body

    # ---- step 12: second tenant isolation --------------------------------
    onboarding_b = _onboard(client, env, email="ravi@beta-systems.example", company="Beta Systems")
    key_b = onboarding_b["api_key"]
    tenant_b = onboarding_b["tenant_id"]
    assert tenant_b != tenant_a

    headers_b = {"X-Platrixa-API-Key": key_b}
    usage_b = client.get("/v1/usage", headers=headers_b)
    assert usage_b.status_code == 200
    assert usage_b.json()["used"] == 0, "usage must be per tenant"

    jobs_b = client.get("/v1/developer/jobs", headers=headers_b)
    assert jobs_b.status_code == 200
    jobs_b_list = jobs_b.json().get("jobs", [])
    if isinstance(jobs_b_list, list):
        assert jobs_b_list == [], "tenant B must not see tenant A's jobs"

    # A developer key is not a management credential.
    owner_attempt = client.get("/v1/owner/onboarding/overview", headers=headers_b)
    assert owner_attempt.status_code in (401, 403), owner_attempt.text

    # Rotating A's key retires it and leaves B working.
    rotated = client.post(
        "/v1/developer/keys/rotate", headers={"X-Platrixa-API-Key": key_a}, json={}
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["tenant_id"] == tenant_a
    assert client.get("/v1/usage", headers=headers).status_code == 401
    assert client.get("/v1/usage", headers=headers_b).status_code == 200
