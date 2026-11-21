"""Delivery-guarantee regression tests for the onboarding dispatcher (Phase 1).

The operator-readiness report reproduced a duplicate send: with a 1s lease and
a slower sender, the dispatcher claimed a whole batch up front, the tail rows'
leases expired while the head was delivered, a second worker recovered them and
every affected message went out twice (7 sends for 5 rows).

These tests pin the two properties that close it:

  * a row is claimed immediately before ITS OWN send, never as part of a batch
    that can sit ``SENDING`` while other rows are delivered;
  * while a send is in flight the lease is RENEWED, so a slow SMTP server can
    never make a live send look abandoned.

They also pin what is deliberately NOT prevented: a crash between the provider
accepting a message and the row being acknowledged is retried, so the transport
must carry a stable Message-ID for the receiver to recognise the duplicate.

Run:
    python -m pytest tests/test_onboarding_dispatch_guarantees.py -v
"""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from backend.onboarding import store

    monkeypatch.setenv("PLATRIXA_METERING_DATABASE_URL", f"sqlite:///{tmp_path}/dispatch.db")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("PLATRIXA_KEY_DELIVERY_SECRET", "d" * 40)
    monkeypatch.setenv("PLATRIXA_EMAIL_PROVIDER", "none")
    monkeypatch.setenv("PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS", "300")
    store.reset_cache()
    store.ensure_schema()
    yield SimpleNamespace(url=f"sqlite:///{tmp_path}/dispatch.db", path=str(tmp_path / "dispatch.db"))
    store.reset_cache()


def _seed(count: int, prefix: str = "k") -> None:
    from backend.onboarding import notifications, store

    with store.session() as db:
        for index in range(count):
            notifications.enqueue(
                db,
                application_id=None,
                audience="OWNER",
                kind="TEST",
                recipient=f"owner+{index}@platrixa.test",
                subject=f"s{index}",
                body=f"b{index}",
                dedupe_key=f"{prefix}-{index}",
            )


def _rows(path: str, sql: str) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _exec(path: str, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


class _Transport:
    """Records every delivery. Optionally slow, failing, or explosive."""

    name = "smtp"
    configured = True

    def __init__(self, *, delay: float = 0.0, fail: bool = False, on_send=None, raises: bool = False):
        self.delay = delay
        self.fail = fail
        self.on_send = on_send
        self.raises = raises
        self.sent = []
        self.lock = threading.Lock()

    def send(self, message):
        from backend.onboarding.email import DeliveryOutcome

        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.sent.append(message)
        if self.on_send is not None:
            self.on_send(message)
        if self.raises:
            raise RuntimeError("transport exploded")
        if self.fail:
            return DeliveryOutcome(ok=False, error="SMTP_UNAVAILABLE", retryable=True)
        return DeliveryOutcome(ok=True)


# ---------------------------------------------------------------------------
# The reproduction: slow send, short lease, concurrent workers
# ---------------------------------------------------------------------------


def test_a_slow_send_renews_its_lease_and_is_never_recovered(env, monkeypatch):
    from backend.onboarding import email, notifications, store

    monkeypatch.setenv("PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS", "1")
    _seed(1)

    observed = {}

    def during_send(_message):
        # Longer than the whole 1s lease: without renewal the row would be
        # recoverable here, which is exactly how the duplicate was produced.
        time.sleep(2.4)
        with store.session() as db:
            observed["recovered"] = notifications.recover_stale(db)
        row = _rows(env.path, "SELECT status, lease_expires_at FROM platrixa_developer_notifications")[0]
        observed["status"] = row["status"]

    transport = _Transport(on_send=during_send)
    monkeypatch.setattr(email, "transport", lambda: transport)

    result = notifications.dispatch_due(5)
    assert result["sent"] == 1, result
    assert result["lost"] == 0, result
    # Still SENDING (its lease was extended) and nothing was recovered.
    assert observed["status"] == "SENDING"
    assert observed["recovered"] == 0
    rows = _rows(env.path, "SELECT status, attempts FROM platrixa_developer_notifications")
    assert [row["status"] for row in rows] == ["SENT"]
    assert rows[0]["attempts"] == 1


def test_a_live_lease_is_never_recovered_or_delivered_again(env, monkeypatch):
    from backend.onboarding import email, notifications

    _seed(1)
    _exec(
        env.path,
        "UPDATE platrixa_developer_notifications SET status = 'SENDING', "
        "claim_token = 'other-worker', lease_expires_at = '2999-01-01 00:00:00'",
    )

    transport = _Transport()
    monkeypatch.setattr(email, "transport", lambda: transport)
    result = notifications.dispatch_due(5)

    assert result["recovered"] == 0, result
    assert result["attempted"] == 0, result
    assert transport.sent == []


def test_concurrent_workers_never_deliver_a_row_twice_across_a_short_lease(env, monkeypatch):
    """The reported condition: batch > lease, several workers, slow sender."""
    from backend.onboarding import email, notifications

    monkeypatch.setenv("PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS", "1")
    _seed(8)

    # 8 rows x 0.25s = ~2s of work against a 1s lease. Claiming the batch up
    # front (the old behaviour) guaranteed the tail expired mid-run.
    transport = _Transport(delay=0.25)
    monkeypatch.setattr(email, "transport", lambda: transport)

    barrier = threading.Barrier(3)
    results: list[dict] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        result = notifications.dispatch_due(20)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    subjects = [message.subject for message in transport.sent]
    assert len(subjects) == 8, f"each row must be delivered exactly once, got {subjects}"
    assert len(set(subjects)) == 8, f"duplicate delivery: {subjects}"
    assert sum(result["sent"] for result in results) == 8
    assert sum(result["lost"] for result in results) == 0

    rows = _rows(env.path, "SELECT status, attempts, body_text FROM platrixa_developer_notifications")
    assert all(row["status"] == "SENT" for row in rows)
    assert all(row["attempts"] == 1 for row in rows)
    assert all(row["body_text"] == "" for row in rows)


def test_a_claim_stolen_mid_send_is_counted_lost_and_never_written_sent(env, monkeypatch):
    from backend.onboarding import email, notifications

    _seed(1)

    def steal(_message):
        # Simulate the only remaining way to lose a claim: another actor
        # rewrites the token while the send is in flight.
        _exec(
            env.path,
            "UPDATE platrixa_developer_notifications SET claim_token = 'someone-else'",
        )

    transport = _Transport(on_send=steal)
    monkeypatch.setattr(email, "transport", lambda: transport)
    result = notifications.dispatch_due(5)

    assert result["lost"] == 1, result
    assert result["sent"] == 0, result
    assert transport.sent, "the message did go out; the point is the ack is refused"
    rows = _rows(env.path, "SELECT status FROM platrixa_developer_notifications")
    assert rows[0]["status"] != "SENT"


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


def test_a_raising_transport_leaves_the_row_retryable(env, monkeypatch):
    from backend.onboarding import email, notifications

    _seed(1)
    transport = _Transport(raises=True)
    monkeypatch.setattr(email, "transport", lambda: transport)

    result = notifications.dispatch_due(5)
    assert result["retried"] == 1, result
    assert result["sent"] == 0, result

    row = _rows(
        env.path,
        "SELECT status, attempts, next_attempt_at, body_text, claim_token, lease_expires_at "
        "FROM platrixa_developer_notifications",
    )[0]
    assert row["status"] == "PENDING"
    assert row["attempts"] == 1
    assert row["next_attempt_at"] is not None
    assert row["body_text"] == "b0", "a retryable row keeps its body"
    assert row["claim_token"] is None
    assert row["lease_expires_at"] is None


def test_a_crashed_worker_row_is_recovered_after_its_lease_expires(env, monkeypatch):
    from backend.onboarding import email, notifications

    _seed(1)
    _exec(
        env.path,
        "UPDATE platrixa_developer_notifications SET status = 'SENDING', "
        "claim_token = 'dead-worker', lease_expires_at = '2000-01-01 00:00:00', "
        "next_attempt_at = NULL",
    )
    transport = _Transport()
    monkeypatch.setattr(email, "transport", lambda: transport)

    result = notifications.dispatch_due(5)
    assert result["recovered"] == 1, result
    assert result["sent"] == 1, result
    assert len(transport.sent) == 1
    rows = _rows(env.path, "SELECT status FROM platrixa_developer_notifications")
    assert rows[0]["status"] == "SENT"


def test_retries_reuse_one_stable_message_id(env, monkeypatch):
    """The unavoidable ambiguity window: same id every attempt, so a receiver
    can recognise the duplicate instead of surfacing two emails."""
    from backend.onboarding import email, notifications

    _seed(1)
    failing = _Transport(fail=True)
    monkeypatch.setattr(email, "transport", lambda: failing)
    assert notifications.dispatch_due(5)["retried"] == 1

    working = _Transport()
    monkeypatch.setattr(email, "transport", lambda: working)
    _exec(env.path, "UPDATE platrixa_developer_notifications SET next_attempt_at = NULL")
    assert notifications.dispatch_due(5)["sent"] == 1

    first, second = failing.sent[0], working.sent[0]
    assert first.message_id == second.message_id
    row_id = _rows(env.path, "SELECT id FROM platrixa_developer_notifications")[0]["id"]
    assert first.message_id == row_id


def test_smtp_sets_the_stable_message_id_header(monkeypatch):
    from backend.onboarding import email

    captured = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def send_message(self, message):
            captured["message_id"] = message["Message-ID"]
            captured["subject"] = message["Subject"]

    transport = email.SmtpTransport(
        host="smtp.platrixa.test",
        port=587,
        sender="no-reply@platrixa.test",
        username="",
        password="",
        starttls=True,
        implicit_tls=False,
    )
    monkeypatch.setattr(transport, "_connect", lambda: _Client())

    outcome = transport.send(
        email.Message(to="dev@acme.test", subject="Welcome", body="body", message_id="abc-123")
    )
    assert outcome.ok
    assert captured["message_id"] == "<platrixa-abc-123@platrixa.test>"
    assert captured["subject"] == "Welcome"
