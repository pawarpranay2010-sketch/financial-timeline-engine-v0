"""Real SMTP delivery tests for developer onboarding notifications.

These tests do NOT stub the transport. A local SMTP server (aiosmtpd) is
started on loopback, the application sends through ``smtplib`` exactly as a
deployment would, and the messages are parsed back out of the server to prove:

  * the verification email is actually delivered, and the token in it works
    against the real verification endpoint;
  * a resend delivers a new link and invalidates the previous one;
  * the activation email carries a one-time key link and never the raw key;
  * owner notifications for a new application, a review case and a
    provisioning/configuration failure are delivered;
  * a transport outage is retryable and a retry delivers EXACTLY ONE copy
    (no duplicate email), after which the body is cleared;
  * a retried verification does not send a second activation email;
  * tokens, keys and SMTP credentials never appear in logs or diagnostics.

The transport is real; the provider is local. No external account exists in
this environment, so a production provider still needs credentials — that is
reported separately, not simulated as "sent".

Run:
    python -m pytest tests/test_onboarding_email_smtp.py -v
"""

from __future__ import annotations

import email
import email.policy
import logging
import re
import socket
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

PUBLIC_ORIGIN = "https://platrixa.test"
OWNER_EMAIL = "owner@platrixa.test"

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


# ---------------------------------------------------------------------------
# Local SMTP server
# ---------------------------------------------------------------------------


class _Collector:
    """aiosmtpd handler that records every accepted message."""

    def __init__(self) -> None:
        self.envelopes: list = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 (aiosmtpd API)
        self.envelopes.append(envelope)
        return "250 Message accepted for delivery"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SMTP_USERNAME = "onboarding-mailer"
SMTP_PASSWORD = "smtp-secret-value"


def _authenticator(server, session, envelope, mechanism, auth_data):
    """Accept exactly the configured mailbox credentials; reject others."""
    from aiosmtpd.smtp import AuthResult

    login = getattr(auth_data, "login", b"")
    password = getattr(auth_data, "password", b"")
    if isinstance(login, str):
        login = login.encode()
    if isinstance(password, str):
        password = password.encode()
    ok = login == SMTP_USERNAME.encode() and password == SMTP_PASSWORD.encode()
    return AuthResult(success=bool(ok))


class _MailServer:
    def __init__(self, port: int | None = None) -> None:
        from aiosmtpd.controller import Controller

        self.port = port or _free_port()
        self.collector = _Collector()
        # AUTH is exercised for real (login+password), over plaintext loopback.
        self._controller = Controller(
            self.collector,
            hostname="127.0.0.1",
            port=self.port,
            ready_timeout=10,
            authenticator=_authenticator,
            auth_require_tls=False,
        )

    def start(self) -> None:
        self._controller.start()

    def stop(self) -> None:
        self._controller.stop()

    # -- message helpers -------------------------------------------------
    def messages(self) -> list:
        parsed = []
        for envelope in self.collector.envelopes:
            parsed.append(
                email.message_from_bytes(envelope.content, policy=email.policy.default)
            )
        return parsed

    def for_recipient(self, address: str) -> list:
        return [m for m in self.messages() if address in (m.get("To") or "")]

    def clear(self) -> None:
        self.collector.envelopes.clear()

    def body(self, message) -> str:
        try:
            return message.get_body(preferencelist=("plain",)).get_content()
        except Exception:  # pragma: no cover - defensive
            return message.get_payload(decode=True).decode("utf-8", "replace")


@pytest.fixture(scope="module")
def mail_server():
    server = _MailServer()
    server.start()
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# Application fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch, mail_server):
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
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_DAY", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP", "1000")
    # Real SMTP against the loopback server. No TLS on loopback.
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("PLATRIXA_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("PLATRIXA_SMTP_PORT", str(mail_server.port))
    monkeypatch.setenv("PLATRIXA_SMTP_STARTTLS", "0")
    monkeypatch.setenv("PLATRIXA_SMTP_SSL", "0")
    monkeypatch.setenv("PLATRIXA_SMTP_USERNAME", SMTP_USERNAME)
    monkeypatch.setenv("PLATRIXA_SMTP_PASSWORD", SMTP_PASSWORD)
    # The in-process aiosmtpd relay speaks no TLS; production refuses plaintext
    # AUTH (audit S-10), so the harness opts in explicitly.
    monkeypatch.setenv("PLATRIXA_SMTP_ALLOW_PLAINTEXT_AUTH", "1")
    monkeypatch.setenv("PLATRIXA_EMAIL_FROM", "no-reply@platrixa.test")
    monkeypatch.setenv("PLATRIXA_EMAIL_FROM_NAME", "Platrixa")
    monkeypatch.setenv("PLATRIXA_EMAIL_REPLY_TO", "support@platrixa.test")

    store.reset_cache()
    gate._session_factory_cache.clear()
    store.ensure_schema(url)
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "store.db"), future=True)
    with engine.begin() as conn:
        for statement in _CREDENTIAL_DDL:
            conn.exec_driver_sql(statement)
    engine.dispose()
    mail_server.clear()

    yield SimpleNamespace(url=url, path=str(tmp_path / "store.db"), mail=mail_server)
    mail_server.clear()
    store.reset_cache()
    gate._session_factory_cache.clear()


class _StubKernelClient:
    """Only /v1/process needs this; onboarding never calls it."""

    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return None

    def process(self, raw_input: str, request_id=None):  # pragma: no cover
        raise AssertionError("kernel must not be called by onboarding tests")


@pytest.fixture()
def client(env):
    from api.main import create_app

    app = create_app()
    app.state.platrixa_client = _StubKernelClient()
    with TestClient(app) as test_client:
        yield test_client


_APPLICATION = {
    "name": "Asha Verma",
    "email": "asha@acme-labs.example",
    "company": "Acme Labs",
    "building": "A reconciliation service that validates ledger entries before posting.",
    "expected_usage": "small",
    "profile_url": "https://github.com/asha-verma",
}


def _apply(client, **overrides):
    payload = dict(_APPLICATION)
    payload.update(overrides)
    response = client.post("/v1/developer/access-requests", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def _token_from(body: str, path: str) -> str:
    match = re.search(re.escape(path) + r"#token=([A-Za-z0-9_\-]+)", body)
    assert match, f"no {path} token found in message body"
    return match.group(1)


def _rows(env, sql: str, params=()):
    engine = create_engine("sqlite+pysqlite:///" + env.path, future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).mappings().all()
    finally:
        engine.dispose()


def _exec(env, sql: str, params=()):
    engine = create_engine("sqlite+pysqlite:///" + env.path, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Verification email
# ---------------------------------------------------------------------------


def test_verification_email_delivered_and_token_verifies(client, env):
    _apply(client)
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    mails = env.mail.for_recipient(_APPLICATION["email"])
    assert len(mails) == 1, "exactly one verification email must be delivered"
    message = mails[0]
    assert "verify" in (message.get("Subject") or "").lower()
    assert message.get("From") == "Platrixa <no-reply@platrixa.test>"
    assert message.get("Reply-To") == "support@platrixa.test"

    token = _token_from(env.mail.body(message), "/developer/verify")
    # Hash-only storage: the token itself is nowhere in the database.
    stored = _rows(env, "SELECT token_hash FROM platrixa_developer_email_verifications")
    assert stored and all(token not in row["token_hash"] for row in stored)

    verified = client.post("/v1/developer/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "ACTIVE"

    notifications.dispatch_due(20)

    # Activation email: one-time key link, never the raw key.
    activation = [m for m in env.mail.for_recipient(_APPLICATION["email"])
                  if "active" in (m.get("Subject") or "").lower()]
    assert len(activation) == 1
    body = env.mail.body(activation[0])
    assert "/developer/keys/reveal" not in body
    assert "/developer/key#token=" in body
    assert "X-Platrixa-API-Key" in body  # usage instructions
    reveal_token = _token_from(body, "/developer/key")

    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert revealed.status_code == 200, revealed.text
    api_key = revealed.json()["api_key"]
    assert api_key.startswith("plx_live_")
    # The key appears in NO delivered message.
    for message in env.mail.messages():
        assert api_key not in env.mail.body(message)

    usage = client.get("/v1/usage", headers={"X-Platrixa-API-Key": api_key})
    assert usage.status_code == 200, usage.text

    # One-time: a second reveal with the same link fails.
    again = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    assert again.status_code in (404, 410), again.text


# ---------------------------------------------------------------------------
# 2. Resend
# ---------------------------------------------------------------------------


def test_resend_delivers_new_link_and_invalidates_previous(client, env):
    _apply(client)
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    first = _token_from(env.mail.body(env.mail.for_recipient(_APPLICATION["email"])[0]), "/developer/verify")

    # Inside the cooldown window the request is throttled (anti-abuse), and
    # the generic body never confirms whether an application exists.
    throttled = client.post(
        "/v1/developer/resend-verification", json={"email": _APPLICATION["email"]}
    )
    assert throttled.status_code == 429

    # Past the window, a new single-use link is delivered.
    _exec(
        env,
        "UPDATE platrixa_developer_email_verifications "
        "SET created_at = '2000-01-01 00:00:00'",
    )
    resend = client.post(
        "/v1/developer/resend-verification", json={"email": _APPLICATION["email"]}
    )
    assert resend.status_code == 202, resend.text
    notifications.dispatch_due(20)
    mails = env.mail.for_recipient(_APPLICATION["email"])
    assert len(mails) == 2
    second = _token_from(env.mail.body(mails[1]), "/developer/verify")
    assert second != first

    stale = client.post("/v1/developer/verify-email", json={"token": first})
    assert stale.status_code in (400, 410), "the replaced link must stop working"

    fresh = client.post("/v1/developer/verify-email", json={"token": second})
    assert fresh.status_code == 200 and fresh.json()["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# 3. Retry after an outage: exactly one delivery
# ---------------------------------------------------------------------------


def test_transport_outage_is_retryable_and_retry_sends_exactly_once(env, monkeypatch):
    from api.main import create_app
    from backend.onboarding import notifications

    # This test owns its SMTP endpoint so stopping it cannot disturb others.
    local = _MailServer()
    monkeypatch.setenv("PLATRIXA_SMTP_PORT", str(local.port))
    # Deliberately do not start it yet: the first delivery attempt must fail.
    try:
        app = create_app()
        app.state.platrixa_client = _StubKernelClient()
        with TestClient(app) as client:
            response = _apply(client)
            assert response["status"] == "PENDING_VERIFICATION"

        pending = _rows(
            env,
            "SELECT id, status, attempts, last_error, body_text FROM platrixa_developer_notifications",
        )
        assert pending, "notifications must be enqueued"
        assert all(row["status"] == "PENDING" for row in pending)
        assert all(row["attempts"] == 0 for row in pending)
        # Bodies are retained while delivery is still pending (needed to retry).
        assert any(row["body_text"] for row in pending)

        # First dispatch attempt fails (server down).
        result = notifications.dispatch_due(20)
        assert result["retried"] >= 1 or result["failed"] >= 1
        pending = _rows(
            env,
            "SELECT id, status, attempts, last_error, body_text FROM platrixa_developer_notifications",
        )
        assert all(row["status"] == "PENDING" for row in pending)
        assert all(row["attempts"] == 1 for row in pending)
        assert all(row["last_error"] == "SMTP_UNAVAILABLE" for row in pending)

        # Bring the service back on the SAME port.
        local = _MailServer(port=local.port)
        local.start()
        time.sleep(0.3)
        _exec(env, "UPDATE platrixa_developer_notifications SET next_attempt_at = NULL")

        result = notifications.dispatch_due(20)
        assert result["sent"] == len(pending), result
        assert result["failed"] == 0

        # Exactly one copy of each message reached the server.
        developer_mails = local.for_recipient(_APPLICATION["email"])
        owner_mails = local.for_recipient(OWNER_EMAIL)
        assert len(developer_mails) == 1
        assert len(owner_mails) == 1

        sent_rows = _rows(
            env,
            "SELECT status, attempts, body_text, sent_at FROM platrixa_developer_notifications",
        )
        assert all(row["status"] == "SENT" for row in sent_rows)
        assert all(row["attempts"] == 2 for row in sent_rows)
        assert all(row["body_text"] == "" for row in sent_rows), "bodies must be cleared"
        assert all(row["sent_at"] is not None for row in sent_rows)

        # Nothing is left to send: a second dispatch delivers nothing new.
        before = len(local.messages())
        again = notifications.dispatch_due(20)
        assert again["attempted"] == 0
        assert len(local.messages()) == before
    finally:
        local.stop()


# ---------------------------------------------------------------------------
# 4. Retried verification must not duplicate the activation email
# ---------------------------------------------------------------------------


def test_repeated_verification_sends_one_activation_email(client, env):
    _apply(client)
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    token = _token_from(
        env.mail.body(env.mail.for_recipient(_APPLICATION["email"])[0]), "/developer/verify"
    )
    first = client.post("/v1/developer/verify-email", json={"token": token})
    assert first.status_code == 200
    notifications.dispatch_due(20)
    second = client.post("/v1/developer/verify-email", json={"token": token})
    assert second.status_code in (200, 400, 410)
    notifications.dispatch_due(20)

    activation = [m for m in env.mail.for_recipient(_APPLICATION["email"])
                  if "active" in (m.get("Subject") or "").lower()]
    assert len(activation) == 1, "activation email must be sent exactly once"


# ---------------------------------------------------------------------------
# 5. Owner notifications
# ---------------------------------------------------------------------------


def test_owner_is_notified_of_new_application_and_review_case(client, env):
    _apply(client, email="asha@gmail.com")
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    owner_mails = env.mail.for_recipient(OWNER_EMAIL)
    assert len(owner_mails) == 1
    assert "access request" in (owner_mails[0].get("Subject") or "").lower()

    token = _token_from(
        env.mail.body(env.mail.for_recipient("asha@gmail.com")[0]), "/developer/verify"
    )
    verified = client.post("/v1/developer/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "REVIEW_REQUIRED"
    notifications.dispatch_due(20)

    owner_mails = env.mail.for_recipient(OWNER_EMAIL)
    subjects = " | ".join((m.get("Subject") or "").lower() for m in owner_mails)
    assert "review" in subjects


def test_owner_is_notified_when_provisioning_is_blocked_by_configuration(
    client, env, monkeypatch
):
    _apply(client)
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    token = _token_from(
        env.mail.body(env.mail.for_recipient(_APPLICATION["email"])[0]), "/developer/verify"
    )
    monkeypatch.delenv("PLATRIXA_KEY_DELIVERY_SECRET", raising=False)

    verified = client.post("/v1/developer/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    payload = verified.json()
    # Verification succeeded, activation did NOT: no misleading success.
    assert payload["status"] == "VERIFIED"
    notifications.dispatch_due(20)

    subjects = " | ".join(
        (m.get("Subject") or "").lower() for m in env.mail.for_recipient(OWNER_EMAIL)
    )
    assert "blocked by configuration" in subjects
    # And the developer received no activation/key link.
    assert not [
        m
        for m in env.mail.for_recipient(_APPLICATION["email"])
        if "active" in (m.get("Subject") or "").lower()
    ]


# ---------------------------------------------------------------------------
# 6. Secrets never reach logs or diagnostics
# ---------------------------------------------------------------------------


def test_tokens_keys_and_credentials_never_appear_in_logs(client, env, caplog):
    caplog.set_level(logging.DEBUG)
    _apply(client)
    from backend.onboarding import notifications

    notifications.dispatch_due(20)
    body = env.mail.body(env.mail.for_recipient(_APPLICATION["email"])[0])
    verify_token = _token_from(body, "/developer/verify")
    client.post("/v1/developer/verify-email", json={"token": verify_token})
    notifications.dispatch_due(20)

    activation = [m for m in env.mail.for_recipient(_APPLICATION["email"])
                  if "active" in (m.get("Subject") or "").lower()][0]
    reveal_token = _token_from(env.mail.body(activation), "/developer/key")
    revealed = client.post("/v1/developer/keys/reveal", json={"token": reveal_token})
    api_key = revealed.json()["api_key"]

    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert verify_token not in blob
    assert reveal_token not in blob
    assert api_key not in blob
    assert "smtp-secret-value" not in blob


# ---------------------------------------------------------------------------
# 7. Provider diagnostics
# ---------------------------------------------------------------------------


def test_provider_status_and_probe(env, monkeypatch):
    from backend.onboarding import email

    status = email.provider_status()
    assert status["provider"] == "smtp"
    assert status["configured"] is True
    assert status["missing"] == []
    assert status["from_domain"] == "platrixa.test"
    assert status["reply_to_configured"] is True
    assert status["credentials_configured"] is True

    probe = email.transport().probe()
    assert probe["ok"] is True, probe
    assert probe["port"] == env.mail.port
    assert "smtp-secret-value" not in repr(probe)

    monkeypatch.setenv("PLATRIXA_SMTP_PORT", str(_free_port()))
    bad = email.transport().probe()
    assert bad["ok"] is False
    assert bad["reason"] == "SMTP_UNAVAILABLE"
    assert "smtp-secret-value" not in repr(bad)

    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    missing = email.provider_status()
    assert missing["configured"] is False
    assert "PLATRIXA_EMAIL_PROVIDER" in missing["missing"]
    assert email.transport().probe()["reason"] == "EMAIL_NOT_CONFIGURED"
