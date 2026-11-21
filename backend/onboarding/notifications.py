"""Durable, retryable notification queue.

Design rules:

* A notification is a DATABASE ROW, created in the same transaction as the
  state change that caused it. If provisioning commits, the owner and
  developer notifications exist; if it rolls back, they do not. That is how a
  failed send can never corrupt application or provisioning state.
* Each logical event has a unique ``dedupe_key``. Retried provisioning (or a
  retried HTTP request) hits the unique index and adds nothing, so the owner is
  never emailed twice for one event.
* Delivery is attempted by the DISPATCHER (``backend.onboarding.dispatcher``),
  never inline in a latency-sensitive public request: the request only
  persists the row and returns.
* CLAIM SAFETY: a worker claims a row with a conditional UPDATE that sets
  ``claim_token`` + ``claimed_at`` + ``lease_expires_at``. Only the holder of
  that token may acknowledge, reschedule or release the row, so two concurrent
  dispatchers can never both mark one message sent. A crashed worker's row is
  recovered when its LEASE expires — not by comparing ``created_at``, which
  would let a long-running send of an old row be stolen mid-flight.
* CLAIM ONE ROW AT A TIME: a pass selects the due ids and then claims each row
  immediately before its own send. Claiming a whole batch up front meant the
  tail of a large batch sat ``SENDING`` while the head was delivered, so its
  lease expired and a second worker re-sent it.
* LEASE RENEWAL: while a send is in flight a background keeper renews the lease
  (``_LeaseKeeper``). A slow SMTP server can therefore never make a live send
  look abandoned. Renewal interval is ``lease/3``, so clock skew up to about a
  third of the lease between workers is tolerated; beyond that, workers must be
  kept in sync by NTP like any other distributed lease system.
* A non-retryable provider error (auth failure, refused recipient) marks the
  row FAILED instead of hammering the provider; retryable ones get bounded
  exponential backoff and stop at MAX_ATTEMPTS.
* Message bodies are cleared once the row reaches a terminal state: a
  verification link's token must not linger in the database after delivery.
* Secrets: no API key, no password and no management credential ever appears
  in a notification body. Verification/reveal links are the only secrets, and
  only in DEVELOPER messages.

DELIVERY GUARANTEE (stated, not implied)
----------------------------------------
* AT MOST ONE OWNER at a time: a live lease plus its renewal means only one
  worker can be inside a row's send, and only that worker can acknowledge it.
* AT LEAST ONCE delivery: a worker that dies after the provider accepted the
  message but before the acknowledgement commits leaves the row ``PENDING``,
  and it is retried. SMTP cannot distinguish "accepted, then the sender lost
  the acknowledgement" from "never accepted", so this window is unavoidable.
  It is minimised (lease renewal for the whole send, acknowledgement immediately
  after) and made detectable: every message carries a STABLE Message-ID derived
  from the notification id, so a receiver that honours it recognises the
  duplicate instead of surfacing two emails.
* NEVER duplicate under the supported operating model: two (or more) dispatchers
  on a synchronised clock, no lease shorter than a second, and the SMTP
  hand-off contained in the process that holds the lease.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.onboarding import config, email, store
from backend.onboarding.store import notifications as notifications_table

logger = logging.getLogger("platrixa.onboarding.notifications")

AUDIENCE_DEVELOPER = "DEVELOPER"
AUDIENCE_OWNER = "OWNER"

STATUS_PENDING = "PENDING"
STATUS_SENDING = "SENDING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_SUPPRESSED = "SUPPRESSED"

MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600

# How long a lease keeper waits after a failed renewal before giving up on it.
LEASE_RENEWAL_GRACE_SECONDS = 5.0


def backoff_seconds(attempt: int) -> int:
    exponent = max(0, attempt - 1)
    return min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** min(exponent, 8)))


def enqueue(
    db,
    *,
    application_id: Optional[str],
    audience: str,
    kind: str,
    recipient: Optional[str],
    subject: str,
    body: str,
    dedupe_key: str,
) -> bool:
    """Add one notification inside the CALLER's transaction.

    Returns False when this event was already queued. A duplicate is detected
    by the unique constraint on ``dedupe_key`` inside a savepoint, so a
    duplicate never aborts (and never rolls back) the surrounding provisioning
    transaction.
    """
    now = store.utcnow()
    if not recipient:
        row_status = STATUS_SUPPRESSED
        last_error = "RECIPIENT_NOT_CONFIGURED"
    else:
        row_status = STATUS_PENDING
        last_error = None

    savepoint = db.begin_nested()
    try:
        db.execute(
            notifications_table.insert().values(
                id=str(uuid.uuid4()),
                application_id=application_id,
                audience=audience,
                kind=kind,
                recipient=recipient,
                subject=subject[:200],
                body_text=body,
                status=row_status,
                attempts=0,
                next_attempt_at=now if row_status == STATUS_PENDING else None,
                last_error=last_error,
                dedupe_key=dedupe_key[:200],
                created_at=now,
                sent_at=None,
                claimed_at=None,
                claim_token=None,
                lease_expires_at=None,
            )
        )
        savepoint.commit()
        return True
    except IntegrityError:
        savepoint.rollback()
        return False
    except SQLAlchemyError:
        savepoint.rollback()
        raise


def _claim(db, notification_id: str, now, lease_seconds: int) -> Optional[str]:
    """Atomically claim one PENDING row. Returns the claim token, or None."""
    token = str(uuid.uuid4())
    result = db.execute(
        update(notifications_table)
        .where(
            notifications_table.c.id == notification_id,
            notifications_table.c.status == STATUS_PENDING,
            notifications_table.c.recipient.is_not(None),
        )
        .values(
            status=STATUS_SENDING,
            claim_token=token,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))),
        )
    )
    return token if (result.rowcount or 0) == 1 else None


def _owned(claim_token: str):
    """WHERE clause: this row is still owned by ``claim_token``."""
    return (
        notifications_table.c.claim_token == claim_token,
        notifications_table.c.status == STATUS_SENDING,
    )


def renew_lease(
    db, notification_id: str, claim_token: str, lease_seconds: int, now=None
) -> bool:
    """Extend a live claim. False means the row is no longer ours.

    Fenced by the claim token: a recovered (or re-claimed) row belongs to a
    different token, so a late renewal can never resurrect a dead claim.
    """
    now = now or store.utcnow()
    result = db.execute(
        update(notifications_table)
        .where(
            notifications_table.c.id == notification_id,
            *_owned(claim_token),
        )
        .values(
            lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds)))
        )
    )
    return (result.rowcount or 0) == 1


class _LeaseKeeper:
    """Renew one row's lease for as long as its send is in flight.

    Without this, a send that outlives the lease looks abandoned: another
    dispatcher recovers the row and delivers the same message again. That is
    exactly the duplicate this class exists to prevent, and it is observable:
    ``lost`` becomes True only when a renewal is refused because another worker
    already owns the row.
    """

    def __init__(self, notification_id: str, claim_token: str, lease_seconds: int) -> None:
        self._id = notification_id
        self._token = claim_token
        self._lease = max(1, int(lease_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False
        self.renewals = 0

    def _tick(self) -> None:
        interval = max(1.0, self._lease / 3.0)
        while not self._stop.wait(interval):
            try:
                with store.session() as db:
                    ok = renew_lease(db, self._id, self._token, self._lease)
            except SQLAlchemyError:
                # Transient store trouble: keep trying. The row is still ours
                # until a renewal is actually REFUSED.
                continue
            except Exception:  # pragma: no cover - defensive
                return
            if not ok:
                self.lost = True
                return
            self.renewals += 1

    def start(self) -> bool:
        """Renew once now, then keep renewing in the background.

        Returns False when the row is already owned by someone else — the
        caller must then NOT send, because that would be the duplicate.
        """
        try:
            with store.session() as db:
                ok = renew_lease(db, self._id, self._token, self._lease)
        except SQLAlchemyError:
            # Cannot prove loss here; the acknowledgement is still fenced by
            # the claim token, so a false positive would only skip a retry.
            ok = True
        if not ok:
            self.lost = True
            return False
        self._thread = threading.Thread(
            target=self._tick, name=f"onboarding-lease-{self._id[:8]}", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=LEASE_RENEWAL_GRACE_SECONDS)


def recover_stale(db, now=None) -> int:
    """Return LEASE-EXPIRED in-flight rows to PENDING. Never touches a live lease."""
    now = now or store.utcnow()
    result = db.execute(
        update(notifications_table)
        .where(
            notifications_table.c.status == STATUS_SENDING,
            or_(
                notifications_table.c.lease_expires_at.is_(None),
                notifications_table.c.lease_expires_at < now,
            ),
        )
        .values(
            status=STATUS_PENDING,
            claim_token=None,
            claimed_at=None,
            lease_expires_at=None,
            next_attempt_at=now,
        )
    )
    return result.rowcount or 0


# Backwards-compatible private name (existing tests import it).
def _recover_stale(db) -> None:
    recover_stale(db)


def _acknowledge(db, notification_id: str, owned_token: str, **values) -> bool:
    """Write a terminal/retry state, but ONLY while this claim is still ours.

    False means another worker recovered and re-claimed the row first; the
    caller counts it as ``lost`` and never reports a delivery it does not own.
    ``owned_token`` is the fencing token; ``values`` may also set
    ``claim_token=None`` to release the row.
    """
    result = db.execute(
        update(notifications_table)
        .where(
            notifications_table.c.id == notification_id,
            *_owned(owned_token),
        )
        .values(**values)
    )
    return (result.rowcount or 0) == 1


def dispatch_due(limit: int = 20, *, worker_id: Optional[str] = None) -> dict:
    """Attempt delivery for due rows. Never raises; returns counts.

    The queue row, not the SMTP call, is the source of truth: a provider
    outage leaves rows PENDING with a retry time, and the dashboard shows the
    delivery state honestly.
    """
    if not config.configured():
        return {"attempted": 0, "sent": 0, "failed": 0, "pending": 0,
                "reason": "ONBOARDING_NOT_CONFIGURED"}

    active = email.transport()
    if not active.configured:
        # No provider: record the honest reason and leave every row DUE (not
        # pushed into a backoff window), so the queue flushes immediately once
        # a provider is configured. Attempts are not burned.
        try:
            store.require_schema()
            with store.session() as db:
                pending = db.execute(
                    update(notifications_table)
                    .where(
                        notifications_table.c.status == STATUS_PENDING,
                        notifications_table.c.recipient.is_not(None),
                    )
                    .values(last_error="EMAIL_NOT_CONFIGURED", next_attempt_at=None)
                )
                count = pending.rowcount or 0
        except SQLAlchemyError:
            return {"attempted": 0, "sent": 0, "failed": 0, "pending": 0,
                    "reason": "QUEUE_UNAVAILABLE", "configured": False}
        except store.SchemaNotReady:
            return {"attempted": 0, "sent": 0, "failed": 0, "pending": 0,
                    "reason": "SCHEMA_NOT_READY", "configured": False}
        return {"attempted": 0, "sent": 0, "failed": 0, "pending": count,
                "provider": active.name, "configured": False,
                "reason": "EMAIL_NOT_CONFIGURED"}

    lease_seconds = config.dispatch_lease_seconds()
    try:
        store.require_schema()
        with store.session() as db:
            recovered = recover_stale(db)
            # Only ids are selected here. Each row is claimed immediately
            # before ITS OWN send, so a batch can never accumulate expired
            # leases while the head of the batch is being delivered.
            now = store.utcnow()
            due = db.execute(
                select(notifications_table.c.id)
                .where(
                    notifications_table.c.status == STATUS_PENDING,
                    notifications_table.c.recipient.is_not(None),
                    or_(
                        notifications_table.c.next_attempt_at.is_(None),
                        notifications_table.c.next_attempt_at <= now,
                    ),
                )
                .order_by(notifications_table.c.created_at.asc())
                .limit(max(1, min(limit, 100)))
            ).all()
    except SQLAlchemyError:
        logger.warning("notification queue unavailable")
        return {"attempted": 0, "sent": 0, "failed": 0, "pending": 0,
                "reason": "QUEUE_UNAVAILABLE"}
    except store.SchemaNotReady:
        logger.warning("notification queue schema is not migrated")
        return {"attempted": 0, "sent": 0, "failed": 0, "pending": 0,
                "reason": "SCHEMA_NOT_READY"}

    sent = 0
    failed = 0
    lost = 0
    retried = 0
    claimed = 0

    for candidate in due:
        notification_id = candidate[0]
        try:
            with store.session() as db:
                token = _claim(db, notification_id, store.utcnow(), lease_seconds)
                if token is None:
                    continue
                row = db.execute(
                    select(
                        notifications_table.c.recipient,
                        notifications_table.c.subject,
                        notifications_table.c.body_text,
                        notifications_table.c.attempts,
                    ).where(notifications_table.c.id == notification_id)
                ).mappings().first()
        except SQLAlchemyError:
            logger.warning("notification claim failed")
            continue
        if row is None:
            continue

        claimed += 1
        keeper = _LeaseKeeper(notification_id, token, lease_seconds)
        if not keeper.start():
            # Lost the lease between claim and send: sending now WOULD be the
            # duplicate. Leave the row to its new owner.
            lost += 1
            continue
        try:
            outcome = active.send(
                email.Message(
                    to=row["recipient"],
                    subject=row["subject"],
                    body=row["body_text"],
                    message_id=notification_id,
                )
            )
        except Exception:  # a transport must not take the dispatcher down
            logger.warning("notification transport raised; treating as retryable")
            outcome = email.DeliveryOutcome(
                ok=False, error="TRANSPORT_ERROR", retryable=True
            )
        finally:
            keeper.stop()

        attempts = int(row["attempts"] or 0) + 1
        now = store.utcnow()
        owned = False
        try:
            with store.session() as db:
                if outcome.ok:
                    owned = _acknowledge(
                        db,
                        notification_id,
                        token,
                        status=STATUS_SENT,
                        attempts=attempts,
                        sent_at=now,
                        next_attempt_at=None,
                        last_error=None,
                        body_text="",  # token must not linger
                        claim_token=None,
                        lease_expires_at=None,
                    )
                elif outcome.error == "EMAIL_NOT_CONFIGURED":
                    # Keep it queued without burning attempts: the deployment
                    # is misconfigured, not the message.
                    owned = _acknowledge(
                        db,
                        notification_id,
                        token,
                        status=STATUS_PENDING,
                        last_error="EMAIL_NOT_CONFIGURED",
                        next_attempt_at=now + timedelta(seconds=MAX_BACKOFF_SECONDS),
                        claim_token=None,
                        lease_expires_at=None,
                    )
                elif not outcome.retryable or attempts >= MAX_ATTEMPTS:
                    owned = _acknowledge(
                        db,
                        notification_id,
                        token,
                        status=STATUS_FAILED,
                        attempts=attempts,
                        last_error=(outcome.error or "DELIVERY_FAILED")[:80],
                        next_attempt_at=None,
                        body_text="",
                        claim_token=None,
                        lease_expires_at=None,
                    )
                else:
                    owned = _acknowledge(
                        db,
                        notification_id,
                        token,
                        status=STATUS_PENDING,
                        attempts=attempts,
                        last_error=(outcome.error or "DELIVERY_FAILED")[:80],
                        next_attempt_at=now + timedelta(seconds=backoff_seconds(attempts)),
                        claim_token=None,
                        lease_expires_at=None,
                    )
        except SQLAlchemyError:
            logger.warning("notification state update failed")
            continue

        if not owned:
            # A concurrent worker recovered and re-claimed the row; a stale
            # send must never write a terminal state.
            lost += 1
        elif outcome.ok:
            sent += 1
        elif outcome.error == "EMAIL_NOT_CONFIGURED":
            retried += 1
        elif not outcome.retryable or attempts >= MAX_ATTEMPTS:
            failed += 1
        else:
            retried += 1

        if keeper.lost:
            logger.warning(
                "onboarding lease lost during send id=%s (possible duplicate)", notification_id
            )

    logger.info(
        "onboarding dispatch worker=%s claimed=%d sent=%d failed=%d retried=%d lost=%d recovered=%d",
        worker_id or "-",
        claimed,
        sent,
        failed,
        retried,
        lost,
        recovered,
    )
    return {
        "attempted": claimed,
        "sent": sent,
        "failed": failed,
        "retried": retried,
        "lost": lost,
        "recovered": recovered,
        "provider": active.name,
        "configured": bool(active.configured),
    }


def counts() -> dict:
    """Delivery-state counts for the owner dashboard."""
    empty = {STATUS_PENDING: 0, STATUS_SENDING: 0, STATUS_SENT: 0,
             STATUS_FAILED: 0, STATUS_SUPPRESSED: 0}
    try:
        store.require_schema()
        with store.session() as db:
            rows = db.execute(
                select(notifications_table.c.status, notifications_table.c.id)
            ).all()
    except SQLAlchemyError:
        return {"available": False, "counts": empty}
    except store.SchemaNotReady:
        return {"available": False, "counts": empty}
    for status, _id in rows:
        empty[status] = empty.get(status, 0) + 1
    return {"available": True, "counts": empty}
