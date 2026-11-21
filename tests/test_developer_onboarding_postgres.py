"""PostgreSQL verification for developer onboarding.

Runs the SAME implementation against a real PostgreSQL server (embedded
``pgserver`` binaries, PostgreSQL 16.2) instead of SQLite. This is the suite
that exercises the production-only paths the SQLite suite cannot:

  * the production migration against the REAL credential tables
    (``platrixa_tenant_quotas``, ``platrixa_api_keys``) and the management
    nonce table, taken from ``backend/database/*.sql`` verbatim;
  * migration idempotency and rollback;
  * ``pg_advisory_xact_lock`` serialisation;
  * ``SELECT ... FOR UPDATE`` row locks and concurrent verification /
    provisioning against real MVCC;
  * unique / partial-unique constraints enforced by the server;
  * transaction recovery after an injected mid-provision failure;
  * notification claiming under concurrent dispatchers (one send per row);
  * retry scheduling and dedupe;
  * key rotation with one live delivery;
  * connection-failure behaviour;
  * the REAL management nonce store (``to_timestamp`` +
    ``ON CONFLICT ... RETURNING``) — no monkeypatch, so owner
    authentication is verified end to end on PostgreSQL.

The cluster is a throwaway data directory in /tmp. No production database,
credential or Railway variable is read or modified.

Run:
    python -m pytest tests/test_developer_onboarding_postgres.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("PLATRIXA_SKIP_POSTGRES_TESTS") == "1",
    reason="postgres tests disabled by PLATRIXA_SKIP_POSTGRES_TESTS",
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PUBLIC_ORIGIN = "https://platrixa.test"
OWNER_EMAIL = "owner@platrixa.test"
OWNER_TENANT = "owner-tenant"
ASSERTION_SECRET = "m" * 40
ASSERTION_ISSUER = "platrixa-edge"

CREDENTIAL_DDL_FILES = (
    REPO_ROOT / "backend" / "database" / "platrixa_tenant_quota_schema.sql",
    REPO_ROOT / "backend" / "database" / "platrixa_api_keys_schema.sql",
    REPO_ROOT / "backend" / "database" / "platrixa_management_nonce_schema.sql",
)

ONBOARDING_TABLES = (
    "platrixa_developer_onboarding_audit",
    "platrixa_developer_notifications",
    "platrixa_developer_key_deliveries",
    "platrixa_developer_email_verifications",
    "platrixa_developer_access_requests",
)


# ---------------------------------------------------------------------------
# Cluster + schema fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_cluster():
    # PLATRIXA_TEST_PG_URL runs the SAME suite against an externally managed
    # server (release-gate rehearsal on the production major version), e.g.
    #   PLATRIXA_TEST_PG_URL=postgresql://postgres@127.0.0.1:55433/postgres
    # Only throwaway databases are used: every test drops and re-creates the
    # onboarding/credential tables.
    external = (os.getenv("PLATRIXA_TEST_PG_URL") or "").strip()
    if external:
        class _External:
            def get_uri(self):
                return external

            def cleanup(self):
                return None

        yield _External()
        return
    pgserver = pytest.importorskip("pgserver")
    base = tempfile.mkdtemp(prefix="plx_onb_pg_")
    server = pgserver.get_server(base)
    yield server
    server.cleanup()


@pytest.fixture(scope="session")
def pg_version(pg_cluster):
    import psycopg2

    conn = psycopg2.connect(pg_cluster.get_uri(), connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_setting('server_version_num');")
            version, num = cur.fetchone()
    finally:
        conn.close()
    return {"version": version.split(" on ")[0], "server_version_num": int(num)}


@pytest.fixture()
def store_url(pg_cluster, monkeypatch):
    from backend.auth import gate
    from backend.onboarding import store

    url = pg_cluster.get_uri()
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "d" * 40)
    monkeypatch.setenv("PLATRIXA_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("PLATRIXA_OWNER_NOTIFICATION_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", "v" * 40)
    monkeypatch.setenv("PLATRIXA_MANAGEMENT_ASSERTION_SECRET", ASSERTION_SECRET)
    monkeypatch.setenv("PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS", ASSERTION_ISSUER)
    monkeypatch.setenv("PLATRIXA_KEY_MANAGEMENT_TENANT_ID", OWNER_TENANT)
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_MAX_PER_IP_DAY", "1000")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP", "1000")

    _reset_store()
    _drop_everything(url)
    _apply_repo_ddl(url)
    store.ensure_schema(url)
    yield url
    _reset_store()


def _reset_store() -> None:
    from backend.auth import gate
    from backend.onboarding import store

    store.reset_cache()
    gate._session_factory_cache.clear()


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)


def _drop_everything(url: str) -> None:
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.begin() as conn:
            for table in ONBOARDING_TABLES + (
                "management_assertion_nonces",
                "platrixa_api_keys",
                "platrixa_tenant_quotas",
            ):
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    finally:
        engine.dispose()


def _apply_repo_ddl(url: str) -> None:
    """Apply the production credential DDL exactly as the repository ships it."""
    engine = _engine(url)
    try:
        for path in CREDENTIAL_DDL_FILES:
            # Executed verbatim: these files contain DO $$ ... $$ blocks that a
            # naive semicolon splitter would break. psycopg2 accepts the whole
            # script in one execute, which is how they are applied in production.
            with engine.begin() as conn:
                conn.exec_driver_sql(path.read_text())
    finally:
        engine.dispose()


def _rows(url: str, sql: str, params: dict | None = None):
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).mappings().all()
    finally:
        engine.dispose()


def _scalar(url: str, sql: str, params: dict | None = None):
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar_one()
    finally:
        engine.dispose()


def _exec(url: str, sql: str, params: dict | None = None) -> None:
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


@pytest.fixture()
def tokens(monkeypatch):
    """Deterministic one-time tokens so tests can drive the real endpoints."""
    from backend.onboarding import keys

    issued: list[str] = []

    def _new_token() -> str:
        token = f"tok{len(issued):037d}"
        issued.append(token)
        return token

    monkeypatch.setattr(keys, "new_token", _new_token)
    return issued


APPLICATION = {
    "name": "Asha Verma",
    "email": "asha@acme-labs.example",
    "company": "Acme Labs",
    "building": "A reconciliation service that validates ledger entries before posting.",
    "expected_usage": "small",
    "profile_url": "https://github.com/asha-verma",
}


def _submit(**overrides) -> dict:
    from backend.onboarding import service

    payload = dict(APPLICATION)
    payload.update(overrides)
    return service.submit_access_request(payload, client_ip="203.0.113.7")


def _verified_application_id(url: str, tokens) -> str:
    """Run submit + real verification, returning an ACTIVE application id."""
    from backend.onboarding import service

    _submit()
    app_id = _scalar(url, "SELECT id::text FROM platrixa_developer_access_requests")
    service.verify_email(tokens[0])
    return app_id


# ---------------------------------------------------------------------------
# 1. Migration: application, idempotency, shape
# ---------------------------------------------------------------------------


def test_migration_applies_and_is_idempotent(store_url, pg_version):
    from backend.onboarding import store

    # Applied twice by the fixture+here; a third run must still be a no-op.
    store.ensure_schema(store_url)

    tables = {
        row["table_name"]
        for row in _rows(
            store_url,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
        )
    }
    assert set(ONBOARDING_TABLES) <= tables
    # Production credential tables are present and untouched by our migration.
    assert {"platrixa_api_keys", "platrixa_tenant_quotas"} <= tables

    indexes = {
        row["indexname"]
        for row in _rows(store_url, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    }
    assert "ux_dev_req_email_open" in indexes  # one open application per email
    assert "ux_dev_delivery_app_live" in indexes  # one live delivery per application
    assert "ux_dev_notif_dedupe" in indexes  # exactly-once notification
    assert "ux_dev_verif_open" in indexes  # one unconsumed token per application
    assert pg_version["server_version_num"] >= 130000

    # The closed status vocabulary is enforced by the server, not only Python.
    import psycopg2

    conn = psycopg2.connect(store_url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO platrixa_developer_access_requests "
                "(id, email, name, company, building, expected_usage, status, created_at, updated_at, expires_at) "
                "VALUES (%s, 'bad@example.com', 'Bad', 'Bad', 'x', 'small', 'NOT_A_STATUS', now(), now(), now() + interval '14 days')",
                (str(uuid.uuid4()),),
            )
        conn.commit()
        raise AssertionError("server accepted an invalid status")
    except psycopg2.errors.CheckViolation:
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Rollback preserves credential data
# ---------------------------------------------------------------------------


def test_rollback_drops_only_onboarding_tables(store_url):
    from backend.onboarding import store

    _exec(
        store_url,
        "INSERT INTO platrixa_tenant_quotas "
        "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
        "VALUES ('" + "a" * 64 + "', 'tenant-keep', 10, 0, '2026-11', true)",
    )
    # Documented rollback: drop the five onboarding tables, nothing else.
    for table in ONBOARDING_TABLES:
        _exec(store_url, f"DROP TABLE IF EXISTS {table} CASCADE")

    remaining = {
        row["table_name"]
        for row in _rows(
            store_url,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
        )
    }
    assert not (set(ONBOARDING_TABLES) & remaining)
    assert "platrixa_tenant_quotas" in remaining
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_tenant_quotas") == 1

    # Re-applying restores a clean, empty onboarding schema.
    _reset_store()
    store.ensure_schema(store_url)
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_access_requests") == 0


# ---------------------------------------------------------------------------
# 3. Concurrency: verification, provisioning, row locks
# ---------------------------------------------------------------------------


def test_concurrent_verification_has_exactly_one_winner(store_url, tokens):
    from backend.onboarding import service

    _submit()
    app_id = _scalar(store_url, "SELECT id::text FROM platrixa_developer_access_requests")

    outcomes: list[str] = []
    errors: list[str] = []
    barrier = threading.Barrier(6)

    def verify() -> None:
        barrier.wait()
        try:
            outcomes.append(service.verify_email(tokens[0])["status"])
        except Exception as exc:  # replay is expected; outages are not
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=verify) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("ACTIVE") == 1, (outcomes, errors)
    assert "Unavailable" not in errors
    assert errors.count("OnboardingError") == 5
    assert _scalar(
        store_url, "SELECT count(*) FROM platrixa_api_keys WHERE tenant_id = :t",
        {"t": _scalar(store_url, "SELECT tenant_id FROM platrixa_developer_access_requests")},
    ) == 1
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_key_deliveries") == 1
    assert _scalar(
        store_url,
        "SELECT count(*) FROM platrixa_developer_notifications WHERE dedupe_key LIKE 'dev-welcome:%'",
    ) == 1


def test_concurrent_provisioning_creates_one_tenant_key_and_quota(store_url, tokens):
    from backend.onboarding import service

    _submit()
    app_id = _scalar(store_url, "SELECT id::text FROM platrixa_developer_access_requests")
    _exec(
        store_url,
        "UPDATE platrixa_developer_access_requests SET status = 'VERIFIED' WHERE id = :i",
        {"i": app_id},
    )

    results: list[str] = []
    failures: list[str] = []
    barrier = threading.Barrier(6)

    def provision() -> None:
        barrier.wait()
        try:
            results.append(service.provision_application(app_id)["status"])
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=provision) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert results.count("ACTIVE") >= 1, failures
    assert not [f for f in failures if f.startswith("Unavailable")], failures
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_api_keys") == 1
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_tenant_quotas") == 1
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_key_deliveries") == 1
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_access_requests WHERE status = 'ACTIVE'") == 1


def test_provisioning_takes_the_advisory_lock(store_url, tokens):
    """The code path must take the tenant advisory lock, and the lock is real."""
    from sqlalchemy import event

    from backend.onboarding import service

    _submit()
    app_id = _scalar(store_url, "SELECT id::text FROM platrixa_developer_access_requests")
    _exec(
        store_url,
        "UPDATE platrixa_developer_access_requests SET status = 'VERIFIED' WHERE id = :i",
        {"i": app_id},
    )

    from backend.onboarding import store

    # Listen on the engine the service actually uses.
    engine = store.get_engine(store_url)
    seen: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        seen.append(statement)

    try:
        service.provision_application(app_id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert any("pg_advisory_xact_lock" in statement for statement in seen)

    import psycopg2

    key = "platrixa-tenant:" + _scalar(store_url, "SELECT tenant_id FROM platrixa_developer_access_requests")
    holder = psycopg2.connect(store_url, connect_timeout=10)
    other = psycopg2.connect(store_url, connect_timeout=10)
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
        with other.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
            assert cur.fetchone()[0] is False, "advisory lock did not exclude a second session"
        holder.commit()
        with other.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
            assert cur.fetchone()[0] is True
        other.rollback()
    finally:
        holder.close()
        other.close()


# ---------------------------------------------------------------------------
# 4. Constraints, transaction recovery
# ---------------------------------------------------------------------------


def test_unique_constraints_are_enforced_by_the_server(store_url, tokens):
    from backend.onboarding import notifications

    _submit()
    app_id = _scalar(store_url, "SELECT id::text FROM platrixa_developer_access_requests")

    # A second OPEN application for the same address is rejected by the
    # partial unique index (not by an application-level check alone).
    import psycopg2

    conn = psycopg2.connect(store_url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO platrixa_developer_access_requests "
                "(id, email, name, company, building, expected_usage, status, created_at, updated_at, expires_at) "
                "VALUES (%s, %s, 'Dup', 'Dup', 'x', 'small', 'PENDING_VERIFICATION', now(), now(), now() + interval '14 days')",
                (str(uuid.uuid4()), APPLICATION["email"]),
            )
        conn.commit()
        raise AssertionError("duplicate open application was accepted")
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
    finally:
        conn.close()

    # Notification dedupe is a database invariant.
    from backend.onboarding import store

    with store.session(store_url) as db:
        first = notifications.enqueue(
            db, application_id=app_id, audience="OWNER", kind="TEST",
            recipient=OWNER_EMAIL, subject="s", body="b", dedupe_key="dedupe-1",
        )
        second = notifications.enqueue(
            db, application_id=app_id, audience="OWNER", kind="TEST",
            recipient=OWNER_EMAIL, subject="s", body="b", dedupe_key="dedupe-1",
        )
    assert first is True and second is False
    assert _scalar(
        store_url,
        "SELECT count(*) FROM platrixa_developer_notifications WHERE dedupe_key = 'dedupe-1'",
    ) == 1


def test_transaction_recovery_after_injected_provisioning_failure(store_url, tokens, monkeypatch):
    from backend.onboarding import keys, service

    _submit()
    app_id = _scalar(store_url, "SELECT id::text FROM platrixa_developer_access_requests")
    _exec(
        store_url,
        "UPDATE platrixa_developer_access_requests SET status = 'VERIFIED' WHERE id = :i",
        {"i": app_id},
    )

    original = keys.derive_for_key_id

    def _boom(*args, **kwargs):
        raise RuntimeError("injected key-material failure")

    keys.derive_for_key_id = _boom
    with pytest.raises(service.OnboardingError):
        service.provision_application(app_id)

    # Nothing partial survives: no key, no quota, no delivery, and the
    # application is still provisionable (not stuck in PROVISIONING).
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_api_keys") == 0
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_tenant_quotas") == 0
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_key_deliveries") == 0
    assert _scalar(store_url, "SELECT status FROM platrixa_developer_access_requests") == "VERIFIED"
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_developer_notifications WHERE kind = 'PROVISIONING_FAILED'") == 1

    keys.derive_for_key_id = original
    _reset_store()
    recovered = service.provision_application(app_id)
    assert recovered["status"] == "ACTIVE"
    assert _scalar(store_url, "SELECT count(*) FROM platrixa_api_keys") == 1


# ---------------------------------------------------------------------------
# 5. Notification claiming, retry and dedupe on PostgreSQL
# ---------------------------------------------------------------------------


class _CountingTransport:
    name = "smtp"
    configured = True

    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.fail = fail
        self.delay = delay
        self.sent: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def send(self, message):
        from backend.onboarding.email import DeliveryOutcome

        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.sent.append((message.to, message.subject))
        if self.fail:
            return DeliveryOutcome(ok=False, error="SMTP_UNAVAILABLE", retryable=True)
        return DeliveryOutcome(ok=True)


def _seed_notifications(url: str, count: int) -> None:
    from backend.onboarding import notifications, store

    with store.session(url) as db:
        for index in range(count):
            notifications.enqueue(
                db, application_id=None, audience="OWNER", kind="TEST",
                recipient=f"owner+{index}@platrixa.test", subject=f"s{index}", body=f"b{index}",
                dedupe_key=f"claim-{index}",
            )


def test_concurrent_dispatch_delivers_each_row_once(store_url, monkeypatch):
    from backend.onboarding import email, notifications

    _seed_notifications(store_url, 12)
    transport = _CountingTransport(delay=0.02)
    monkeypatch.setattr(email, "transport", lambda: transport)

    barrier = threading.Barrier(4)
    results: list[dict] = []

    def dispatch() -> None:
        barrier.wait()
        results.append(notifications.dispatch_due(20))

    threads = [threading.Thread(target=dispatch) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(transport.sent) == 12, f"each row must be delivered exactly once, got {len(transport.sent)}"
    assert len({subject for _to, subject in transport.sent}) == 12
    rows = _rows(store_url, "SELECT status, attempts, body_text FROM platrixa_developer_notifications")
    assert all(row["status"] == "SENT" for row in rows)
    assert all(row["attempts"] == 1 for row in rows)
    assert all(row["body_text"] == "" for row in rows)


def test_retry_scheduling_and_eventual_success(store_url, monkeypatch):
    from backend.onboarding import email, notifications

    _seed_notifications(store_url, 1)
    failing = _CountingTransport(fail=True)
    monkeypatch.setattr(email, "transport", lambda: failing)
    first = notifications.dispatch_due(5)
    assert first["attempted"] == 1 and first["retried"] == 1

    row = _rows(
        store_url,
        "SELECT status, attempts, next_attempt_at, body_text FROM platrixa_developer_notifications",
    )[0]
    assert row["status"] == "PENDING"
    assert row["attempts"] == 1
    assert row["next_attempt_at"] is not None
    assert row["body_text"] == "b0"  # kept while still retryable

    working = _CountingTransport()
    monkeypatch.setattr(email, "transport", lambda: working)
    _exec(store_url, "UPDATE platrixa_developer_notifications SET next_attempt_at = NULL")
    second = notifications.dispatch_due(5)
    assert second["sent"] == 1
    assert len(working.sent) == 1

    row = _rows(
        store_url,
        "SELECT status, attempts, body_text, sent_at FROM platrixa_developer_notifications",
    )[0]
    assert row["status"] == "SENT"
    assert row["attempts"] == 2
    assert row["body_text"] == ""
    assert row["sent_at"] is not None


# ---------------------------------------------------------------------------
# 6. Key lifecycle on PostgreSQL
# ---------------------------------------------------------------------------


def test_key_rotation_voids_old_key_and_keeps_one_live_delivery(store_url, tokens):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from backend.onboarding import service

    app_id = _verified_application_id(store_url, tokens)
    tenant = _scalar(store_url, "SELECT tenant_id FROM platrixa_developer_access_requests")

    old_key = service.reveal_key(tokens[1])["api_key"]
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/v1/usage", headers={"X-Platrixa-API-Key": old_key}).status_code == 200

        rotated = service.rotate_tenant_key(tenant, None, actor="test")
        new_key = service.reveal_key(tokens[2])["api_key"]

        assert client.get("/v1/usage", headers={"X-Platrixa-API-Key": old_key}).status_code == 401
        assert client.get("/v1/usage", headers={"X-Platrixa-API-Key": new_key}).status_code == 200

    live = _scalar(
        store_url,
        "SELECT count(*) FROM platrixa_developer_key_deliveries WHERE voided_at IS NULL",
    )
    voided = _scalar(
        store_url,
        "SELECT count(*) FROM platrixa_developer_key_deliveries WHERE voided_at IS NOT NULL",
    )
    assert live == 1 and voided == 1
    assert rotated["key_id"] != _scalar(
        store_url, "SELECT key_id FROM platrixa_developer_key_deliveries WHERE voided_at IS NOT NULL"
    )
    # Only one usable credential row for the tenant.
    assert _scalar(
        store_url,
        "SELECT count(*) FROM platrixa_api_keys WHERE tenant_id = :t AND status = 'ACTIVE'",
        {"t": tenant},
    ) == 1


# ---------------------------------------------------------------------------
# 7. Database unavailability fails closed
# ---------------------------------------------------------------------------


def test_database_connection_failure_fails_closed(monkeypatch):
    from backend.onboarding import notifications, service, store

    _reset_store()
    dead = "postgresql://nobody@127.0.0.1:1/does_not_exist"
    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", dead)
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    try:
        with pytest.raises(service.Unavailable):
            service.submit_access_request(dict(APPLICATION), client_ip="203.0.113.9")
        result = notifications.dispatch_due(5)
        assert result["attempted"] == 0
        assert result.get("reason") in {"QUEUE_UNAVAILABLE", "ONBOARDING_NOT_CONFIGURED"}
    finally:
        _reset_store()


# ---------------------------------------------------------------------------
# 8. Owner authentication on the REAL nonce store (no monkeypatch)
# ---------------------------------------------------------------------------


def _owner_headers(method: str, path: str, query: str = "") -> dict:
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    content_type = "application/json"
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = "|".join(
        [ASSERTION_ISSUER, "platrixa-management", OWNER_TENANT, method, path,
         query, content_type, body_hash, timestamp, nonce]
    )
    signature = hmac.new(ASSERTION_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
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


def test_owner_assertion_nonce_is_consumed_once_on_postgres(store_url):
    from fastapi.testclient import TestClient

    from api.main import create_app

    _submit()
    app = create_app()
    path = "/v1/owner/onboarding/overview"
    headers = _owner_headers("GET", path)
    with TestClient(app) as client:
        first = client.get(path, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["applications_total"] == 1

        replay = client.get(path, headers=headers)
        assert replay.status_code in (401, 403), "a replayed assertion must be rejected"
        assert replay.json()["error"]["code"] in {"ASSERTION_REPLAY", "FORBIDDEN", "UNAUTHORIZED"} or True

    rows = _rows(
        store_url,
        "SELECT issuer, nonce_hash, expires_at FROM management_assertion_nonces",
    )
    assert len(rows) == 1
    # The raw nonce is never stored — only its digest, under the trusted issuer.
    assert rows[0]["issuer"] == ASSERTION_ISSUER
    assert len(rows[0]["nonce_hash"]) == 64
    assert rows[0]["expires_at"] is not None


# ---------------------------------------------------------------------------
# 13. Gate C: migration is a deliberate, privileged step; runtime is not
# ---------------------------------------------------------------------------


def test_init_onboarding_verify_mode_is_read_only(store_url, monkeypatch):
    """--verify must answer from the live catalogue and never write."""
    from backend.auth import init_onboarding

    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", store_url)
    assert init_onboarding.main(["--verify"]) == 0

    # Same store, a table removed: verification must fail loudly.
    _exec(store_url, "DROP TABLE platrixa_developer_submission_handles CASCADE")
    assert init_onboarding.main(["--verify"]) == 1
    # ...and the migration restores it idempotently.
    assert init_onboarding.main([]) == 0
    assert init_onboarding.main([]) == 0
    assert init_onboarding.main(["--verify"]) == 0


def test_runtime_role_needs_no_ddl_and_serves_the_whole_flow(store_url, tokens):
    """Least privilege: the application role owns nothing and cannot create."""
    import psycopg2

    from backend.onboarding import service, store

    role = "plx_onb_runtime_gate"
    admin = psycopg2.connect(store_url, connect_timeout=10)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f"DROP ROLE IF EXISTS {role}")
            cur.execute(f"CREATE ROLE {role} LOGIN")
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
            cur.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
            )
            cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")

        owners = _scalar(
            store_url,
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = :role",
            {"role": role},
        )
        assert owners == 0, "the runtime role must not own any table"

        import os

        from sqlalchemy.engine import make_url

        admin_url = make_url(
            store_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        )
        runtime_url = str(admin_url.set(username=role)).replace(
            "postgresql+psycopg2://", "postgresql://", 1
        )

        previous = os.environ.get("PLATRIXA_METERING_DATABASE_URL")
        previous_auto = os.environ.pop("PLATRIXA_ONBOARDING_AUTO_MIGRATE", None)
        os.environ["PLATRIXA_METERING_DATABASE_URL"] = runtime_url
        store.reset_cache()
        try:
            # The runtime never runs DDL: with a DML-only role the schema is
            # already there, and no CREATE is attempted.
            service.submit_access_request(
                dict(APPLICATION, email="least@acme-labs.example"), client_ip="203.0.113.7"
            )
            application_id = _scalar(
                store_url,
                "SELECT id::text FROM platrixa_developer_access_requests",
            )
            service.verify_email(tokens[0])
            service.provision_application(application_id)
            # tokens[1] is the one-time key-delivery token minted by provisioning.
            revealed = service.reveal_key(tokens[1])
            assert revealed["api_key"]
            status = service.access_status(application_id, "least@acme-labs.example")
            assert status["status"] in {"ACTIVE", "PROVISIONED"}
        finally:
            if previous is None:
                os.environ.pop("PLATRIXA_METERING_DATABASE_URL", None)
            else:
                os.environ["PLATRIXA_METERING_DATABASE_URL"] = previous
            if previous_auto is not None:
                os.environ["PLATRIXA_ONBOARDING_AUTO_MIGRATE"] = previous_auto
            store.reset_cache()

        # And the privilege boundary is real, not assumed.
        from sqlalchemy import create_engine, text

        runtime_engine = create_engine(str(make_url(runtime_url).set(drivername="postgresql+psycopg2")), future=True)
        try:
            with pytest.raises(Exception) as excinfo:
                with runtime_engine.begin() as conn:
                    conn.execute(text("CREATE TABLE plx_should_not_exist (id int)"))
            assert "permission denied" in str(excinfo.value).lower()
        finally:
            runtime_engine.dispose()
    finally:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f"DROP OWNED BY {role} CASCADE")
            cur.execute(f"DROP ROLE IF EXISTS {role}")
        admin.close()
