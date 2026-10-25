"""Platrixa metered developer gate (Phase 16).

Admission control for the hosted developer API:

    HTTP request
        ↓
    X-Platrixa-API-Key header
        ↓
    hash (SHA-256) → tenant row lookup        (401 on unknown/inactive)
        ↓
    ATOMIC quota reservation                  (429 when exhausted)
        ↓
    TenantContext → the existing public interface → Kernel

Hard boundary rules:

  * This module NEVER calls the Kernel, the model provider, accounting,
    grounding, or the RuleEngine. It is an admission-control boundary
    and contains no financial logic of any kind.
  * The raw API key exists ONLY inside the request scope that supplied
    it. It is hashed immediately, never persisted, never logged, never
    serialized into a TenantContext, a response, or an error message.
  * Authentication and quota enforcement happen BEFORE the public
    interface is resolved and before Kernel.process is invoked — a
    rejected request must never trigger a model load or inference.
  * The reservation is ONE database UPDATE whose WHERE clause carries
    the full admission predicate (active key, current-bucket usage
    below limit, or a stale-bucket row eligible for rollover).
    PostgreSQL row-level locking on the primary key serializes
    concurrent UPDATEs for the same key, so the count of accepted
    reservations can never exceed ``monthly_limit``. There is no
    read-increment-write race and no compensation logic.
  * Month semantics: ``usage_month`` is a deterministic UTC "YYYY-MM"
    bucket. A stale bucket transitions into the new bucket INSIDE the
    reservation UPDATE itself (usage resets to 1 in the same atomic
    statement) — no scheduler, and August usage can never be counted
    as September usage.
  * Activation is EXPLICIT: metering runs only when
    PLATRIXA_METERING_DATABASE_URL is set. There is deliberately NO
    DATABASE_URL fallback — an implicit fallback would silently convert
    the documented zero-config local contract into fail-closed 401s on
    any host with an ambient DATABASE_URL, and could brick an
    unconfigured deployment.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import and_, case, or_, update

logger = logging.getLogger("platrixa.api")

# Metering is activated explicitly via PLATRIXA_METERING_DATABASE_URL.
# Rationale (audit-backed): backend.database.db raises at import when
# DATABASE_URL is absent, so importing it at module scope would break
# the Phase 7F clean-import contract for /health. Reading the variable
# at request time keeps the API importable without any database. A
# dedicated variable also lets the metering store differ from the
# application store, and keeps zero-config local development open (the
# gate simply does not activate — documented behavior).
METERING_ENV_VAR = "PLATRIXA_METERING_DATABASE_URL"

# Bounded connection attempt (readiness fix, 2026-10-20). Readiness previously
# inferred "store available" from the mere construction of an Engine — which
# opens no socket — so a configured-but-unreachable store was reported ready.
# Every metering connection now carries a connect timeout, and readiness does a
# real `SELECT 1`.
CONNECT_TIMEOUT_ENV_VAR = "PLATRIXA_DB_CONNECT_TIMEOUT_SECONDS"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
# libpq itself coerces values below 2 to 2, so the floor matches that rather
# than accepting a number the server would silently ignore.
MIN_CONNECT_TIMEOUT_SECONDS = 2
MAX_CONNECT_TIMEOUT_SECONDS = 30

# Readiness deadline (follow-up, 2026-10-20). A connect timeout alone does NOT
# bound the probe, because a connection is not the only phase that can block:
#   * a saturated pool blocks BEFORE any socket exists;
#   * a server that accepts the connection and then stalls the QUERY blocks
#     inside execution, where connect_timeout no longer applies.
# Each of those has a supported bound owned by the layer that enforces it
# (SQLAlchemy pool, libpq connect, PostgreSQL statement timer), and
# ``readiness_deadline_seconds()`` is their sum plus cleanup slack.
#
# Deliberately NOT a wall-clock timer that "expires" a probe: interrupting a
# synchronous libpq call from another thread is neither safe nor portable, and
# abandoning the worker would leak a checked-out connection.
#
# KNOWN RESIDUAL (documented, not fixed here): if the network path goes mute
# AFTER the statement has been sent — the server executes it, but the reply
# never arrives — no bound below applies. connect_timeout has expired, the
# statement is already complete server-side, and psycopg exposes no portable
# client-side read deadline. Measured: still no return after 25s. This is a
# response-path failure, distinct from a stalled query (bounded) and from a
# dropped SYN at connect (bounded by connect_timeout).
STATEMENT_TIMEOUT_MS_ENV_VAR = "PLATRIXA_DB_STATEMENT_TIMEOUT_MS"
DEFAULT_STATEMENT_TIMEOUT_MS = 1500
MIN_STATEMENT_TIMEOUT_MS = 100
MAX_STATEMENT_TIMEOUT_MS = 30_000

POOL_TIMEOUT_ENV_VAR = "PLATRIXA_DB_POOL_TIMEOUT_SECONDS"
DEFAULT_POOL_TIMEOUT_SECONDS = 1.0
MIN_POOL_TIMEOUT_SECONDS = 0.1
MAX_POOL_TIMEOUT_SECONDS = 30.0

# Session close + verdict bookkeeping. Closing a psycopg connection sends
# Terminate and closes the socket — no round trip — so this is headroom, not a
# hidden wait.
CLEANUP_SLACK_SECONDS = 0.5

# Both supported PostgreSQL URL schemes. psycopg3 accepts the same libpq
# keywords as psycopg2 (connect_timeout, options), so one code path bounds
# both; leaving a scheme out is how a timeout silently stops protecting a
# deployment after a driver swap.
BOUNDED_POSTGRES_SCHEMES = ("postgresql+psycopg2://", "postgresql+psycopg://")

# HTTP semantics are the route's concern; the gate reports outcomes as
# reason codes so this module stays HTTP-agnostic and directly testable.
REASON_OK = "OK"
REASON_MISSING_KEY = "MISSING_KEY"  # → 401
REASON_UNKNOWN_KEY = "UNKNOWN_KEY"  # → 401 (externally indistinguishable)
REASON_INACTIVE = "INACTIVE"  # → 401
REASON_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"  # → 429
REASON_METERING_UNAVAILABLE = "METERING_UNAVAILABLE"  # → 503 (fail closed)


class MeteredGateError(Exception):
    """Metering store unavailable while enforcing admission (fail closed)."""


# Cause class names that may be written to the log when a request is
# refused (P1 metering-failure logging, 2026-10-25). The log carries an
# allow-listed CLASS NAME plus a fixed reason code — and nothing else. An
# exception MESSAGE is the one place a DSN, password, host, credential or
# SQL parameter naturally appears, so messages, args and tracebacks are
# never logged on these paths, whatever the class happens to say.
SAFE_CAUSE_NAMES = frozenset({
    # DBAPI / SQLAlchemy driver layer
    "OperationalError", "InterfaceError", "InternalError", "ProgrammingError",
    "IntegrityError", "DataError", "NotSupportedError", "DBAPIError",
    "StatementError", "DisconnectionError", "SQLAlchemyError",
    # connection pool
    "PoolTimeout", "PoolClosed", "ResourceClosedError",
    # sockets / TLS
    "TimeoutError", "ConnectionError", "ConnectionResetError",
    "ConnectionRefusedError", "ConnectionAbortedError", "BrokenPipeError",
    "OSError", "SSLError", "CertificateError", "URLError", "gaierror",
    # driver missing / not importable
    "ImportError", "ModuleNotFoundError",
})
_UNCLASSIFIED_CAUSE = "unclassified"


def _safe_cause_name(exc: BaseException) -> str:
    """Allow-listed class name of the exception that CAUSED ``exc``.

    Walks the ``__cause__`` / ``__context__`` chain and returns the first
    class name on ``SAFE_CAUSE_NAMES``; everything else degrades to the
    fixed token ``unclassified``. Only static class identifiers are read:
    ``str(exc)``, ``args`` and the traceback are deliberately ignored, so
    a driver message carrying a DSN or a credential cannot reach the log.
    """
    current: Optional[BaseException] = exc
    for _ in range(8):
        if current is None:
            break
        name = type(current).__name__
        if name in SAFE_CAUSE_NAMES:
            return name
        following = current.__cause__ or current.__context__
        if following is current:
            break
        current = following
    return _UNCLASSIFIED_CAUSE


@dataclass(frozen=True)
class TenantContext:
    """Minimal read-only context handed to downstream code.

    Carries NO key material — raw key or hash. ``units_reserved`` is 1
    for every admitted request (the reservation happened before this
    object was constructed) and 0 whenever admission failed, so the
    auditability questions ("was this request authenticated?", "was one
    quota unit reserved?") are answerable from this object alone.
    """

    tenant_id: str
    monthly_limit: int
    current_month_usage: int
    usage_month: str
    units_reserved: int = 0

    def to_safe_dict(self) -> dict:
        """Serialisable audit view — never contains key material."""
        return {
            "tenant_id": self.tenant_id,
            "monthly_limit": self.monthly_limit,
            "current_month_usage": self.current_month_usage,
            "usage_month": self.usage_month,
            "units_reserved": self.units_reserved,
        }


# ---------------------------------------------------------------------------
# Store wiring (lazy, request-time; never at API import)
# ---------------------------------------------------------------------------


def _metering_database_url() -> Optional[str]:
    """The metering store URL, if metering is configured (else None).

    Metering activates ONLY on its dedicated variable,
    PLATRIXA_METERING_DATABASE_URL. There is deliberately NO fallback to
    DATABASE_URL: an implicit fallback would silently flip the documented
    Phase 15 contract ("zero-config local development stays open") into
    fail-closed 401s on any host whose .env happens to define DATABASE_URL,
    and could brick an unconfigured deployment (every request 401s against
    a tenant table that was never seeded). Explicit over implicit: the
    operator who wants metering sets the variable, runs init_metering, and
    seeds tenants — one deliberate act, fully documented.

    Reading ``os.environ`` directly (instead of importing
    backend.database.db, which raises when DATABASE_URL is unset) preserves
    the Phase 7F clean-import contract.
    """
    return (os.getenv(METERING_ENV_VAR, "") or "").strip() or None


def connect_timeout_seconds() -> int:
    """Effective bounded connect timeout for the metering store (seconds).

    Operator-tunable via PLATRIXA_DB_CONNECT_TIMEOUT_SECONDS, clamped to
    [2, 30]. An unparseable value falls back to the default instead of
    failing the deployment: a bad timeout must not remove the store.
    """
    raw = (os.getenv(CONNECT_TIMEOUT_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_CONNECT_TIMEOUT_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "invalid %s=%r; using default %ds",
            CONNECT_TIMEOUT_ENV_VAR,
            raw[:20],
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        return DEFAULT_CONNECT_TIMEOUT_SECONDS
    return max(MIN_CONNECT_TIMEOUT_SECONDS, min(MAX_CONNECT_TIMEOUT_SECONDS, value))


def statement_timeout_ms() -> int:
    """Server-side bound on the readiness query, in milliseconds.

    Applied as the libpq ``options`` keyword (``-c statement_timeout=…``), so
    PostgreSQL itself interrupts a stalled query. ``connect_timeout`` cannot do
    this: it stops applying once the connection is established.
    """
    raw = (os.getenv(STATEMENT_TIMEOUT_MS_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_STATEMENT_TIMEOUT_MS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "invalid %s=%r; using default %dms",
            STATEMENT_TIMEOUT_MS_ENV_VAR,
            raw[:20],
            DEFAULT_STATEMENT_TIMEOUT_MS,
        )
        return DEFAULT_STATEMENT_TIMEOUT_MS
    return max(MIN_STATEMENT_TIMEOUT_MS, min(MAX_STATEMENT_TIMEOUT_MS, value))


def pool_timeout_seconds() -> float:
    """How long the engine pool may block waiting for a free connection.

    SQLAlchemy's default is 30s — ten times the connect bound — which is why a
    readiness probe could wait far longer than its documented ceiling while a
    dead connection was still checked out.
    """
    raw = (os.getenv(POOL_TIMEOUT_ENV_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_POOL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "invalid %s=%r; using default %.1fs",
            POOL_TIMEOUT_ENV_VAR,
            raw[:20],
            DEFAULT_POOL_TIMEOUT_SECONDS,
        )
        return DEFAULT_POOL_TIMEOUT_SECONDS
    return max(MIN_POOL_TIMEOUT_SECONDS, min(MAX_POOL_TIMEOUT_SECONDS, value))


def readiness_deadline_seconds() -> float:
    """PROBE-PHASE budget for one readiness check (pool + connect + query).

    This is the ceiling the probe measures itself against: the sum of the
    bounds each layer enforces (SQLAlchemy pool wait, libpq connect,
    PostgreSQL statement), plus cleanup slack. The verdict is taken as soon as
    the probe query returns, so the budget covers the probe phases only.

    It is explicitly NOT a wall-clock response deadline:

      * cleanup (session close / a rollback round trip) runs after the verdict
        and can therefore extend the HTTP response past this number —
        ``CLEANUP_SLACK_SECONDS`` is an allowance, not an enforced bound;
      * a network path that goes mute AFTER a statement is sent is not bounded
        at all (see the KNOWN RESIDUAL note above);
      * ``pool_pre_ping`` executes its liveness check on checkout, before the
        probe can apply its transaction-local statement timeout, so a server
        that accepts and then stalls is bounded only by the residual above.

    An orchestrator timeout should therefore be at least this value plus a
    cleanup allowance, and callers must treat an overrun as not-ready (which
    :func:`probe_store` enforces) rather than as success.
    """
    return (
        pool_timeout_seconds()
        + connect_timeout_seconds()
        + statement_timeout_ms() / 1000.0
        + CLEANUP_SLACK_SECONDS
    )


def _engine_connect_args(url: str) -> dict:
    """Driver ``connect_args`` bounding CONNECTION ESTABLISHMENT only.

    ``connect_timeout`` bounds connection establishment (and any reconnect),
    and is a libpq keyword understood by psycopg2 and psycopg3 alike, so both
    supported PostgreSQL schemes are bounded by the same path.

    The statement bound is deliberately NOT applied here (scope fix,
    2026-10-25). This engine is shared by the admission path, so a
    connect-level ``options=-c statement_timeout=…`` also aborted tenant
    lookups and the quota reservation UPDATE: a row-lock wait on a healthy but
    busy store surfaced as a fail-closed 503 instead of the documented 429/200
    outcomes. The readiness probe applies its own bound transaction-locally in
    :func:`_apply_probe_statement_timeout`, so only the probe is shortened.

    Applied to PostgreSQL dialects only: other dialects (the suites use sqlite)
    reject ``connect_timeout`` outright, so passing it blindly would break every
    non-PostgreSQL store rather than bound it.
    """
    if url.startswith(BOUNDED_POSTGRES_SCHEMES):
        return {"connect_args": {"connect_timeout": connect_timeout_seconds()}}
    return {}


def _apply_probe_statement_timeout(session) -> None:
    """Bound THIS probe transaction's statements to ``statement_timeout_ms()``.

    ``set_config('statement_timeout', …, is_local => true)`` is the
    parameterized form of ``SET LOCAL``: the bound lives exactly as long as the
    surrounding transaction, which the Session opens implicitly on the first
    execute. Closing the session ends that transaction, so the pooled
    connection is returned with the server's own default — the bound never
    leaks into a later admission statement or into another request's checkout.

    PostgreSQL-only by nature. On other dialects (the suites use sqlite) this
    is a no-op, matching :func:`_engine_connect_args`.
    """
    from sqlalchemy import text

    # Duck-typed deliberately: suites substitute lightweight session doubles
    # (execute/close only). Anything that cannot name its dialect is treated as
    # non-PostgreSQL and skipped, exactly like the sqlite store.
    get_bind = getattr(session, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        return
    session.execute(
        text("SELECT set_config('statement_timeout', :bound, true)"),
        {"bound": f"{int(statement_timeout_ms())}ms"},
    )


def _session_factory():
    """A sessionmaker bound to the METERING store (not the app store).

    A dedicated engine is built lazily per unique URL and cached, so the
    metering gate works even when backend.database.db is not importable
    (no DATABASE_URL) and never touches the application pool.

    BUG FIX (2026-09-29) — the engine cache never hit. The lookup below used
    the RAW env URL while the entry was STORED under the NORMALIZED URL
    ("postgresql://…" rewritten to "postgresql+psycopg2://…"), so the two
    keys could never match. Every call therefore built a brand-new Engine
    and ConnectionPool that was never reused and never disposed.

    Under concurrent load that is unbounded connection growth: the Phase 16
    concurrency suite (fte_fyjc_66_metered_gate_test, section H — 40 threads)
    built 132 engines in one process, exhausted the server's 100-connection
    cap ("FATAL: sorry, too many clients already"), and the resulting
    psycopg2.OperationalError surfaced as MeteredGateError — i.e. spurious
    fail-closed HTTP 503s where the quota contract requires 429.

    It failed SAFE (503 = never admitted, no auth or quota bypass), but it is
    also an availability amplification: one API key could drive the metering
    store to sustained 503s. The fix is to normalize the URL BEFORE the cache
    lookup so the key used for lookup and the key used for storage are the
    same string. A lock makes the cold-start race safe, so N simultaneous
    first-calls still build exactly one engine.
    """
    global _session_factory_cache
    url = _metering_database_url()
    if url is None:
        raise MeteredGateError("metering store not configured")

    # Normalize FIRST: the cache key and the engine URL must be the same
    # string, otherwise the lookup below can never match what we store.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    cached = _session_factory_cache.get(url)
    if cached is not None:
        return cached

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    with _session_factory_lock:
        # Re-check under the lock: concurrent cold-start callers must not
        # each construct their own engine.
        cached = _session_factory_cache.get(url)
        if cached is not None:
            return cached
        engine = create_engine(
            url,
            pool_pre_ping=True,
            future=True,
            pool_timeout=pool_timeout_seconds(),
            **_engine_connect_args(url),
        )
        factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _session_factory_cache[url] = factory
        return factory


_session_factory_cache: dict = {}
_session_factory_lock = threading.Lock()


def _metering_configured() -> bool:
    return _metering_database_url() is not None


def probe_store() -> None:
    """One REAL, bounded connectivity check against the metering store.

    Readiness must never be inferred from the fact that an Engine object could
    be constructed: ``create_engine`` is lazy and opens no socket, so a
    configured-but-unreachable store is indistinguishable from a healthy one
    until a connection is actually attempted. This executes ``SELECT 1`` on the
    SAME cached engine the admission path uses — no second pool, one round trip
    — so success means the store behind the credential boundary is genuinely
    usable right now.

    Raises :class:`MeteredGateError` on any failure. The message is fixed text:
    driver messages can carry the DSN (host, user, sometimes the password) and
    this outcome is surfaced by ``/v1/ready``.

    Bounds applied here (2026-10-25): pool wait and connect come from the
    shared engine; the statement bound is set for THIS transaction only by
    :func:`_apply_probe_statement_timeout`, so the reservation path is never
    shortened. The deadline verdict is taken immediately after the probe query
    returns, before cleanup, so an overrun always fails closed; a slow close
    can extend the response but cannot change the verdict.
    """
    from sqlalchemy import text

    if not _metering_configured():
        raise MeteredGateError("metering store not configured")
    started = time.monotonic()
    session = None
    try:
        session = _session_factory()()
        # Probe-only statement bound (scope fix, 2026-10-25): the shared engine
        # carries no connect-level statement_timeout, so apply it for THIS
        # transaction and let the pool hand the connection back at the server
        # default. Readiness is the path that must also be short; a metered
        # reservation must not be aborted mid-lock-wait.
        _apply_probe_statement_timeout(session)
        session.execute(text("SELECT 1"))
        # Deadline verdict BEFORE cleanup (2026-10-25). Measured over the probe
        # phases — pool wait, connect, statement — so a slow session close can
        # delay the HTTP response but can never turn an overrunning probe into
        # a late "ready". Overrun fails closed, exactly like a probe error.
        elapsed = time.monotonic() - started
        deadline = readiness_deadline_seconds()
        if elapsed > deadline:
            logger.warning(
                "readiness probe exceeded its %.1fs budget (%.2fs); reporting unavailable",
                deadline,
                elapsed,
            )
            raise MeteredGateError("metering store unavailable")
    except MeteredGateError:
        # The overrun verdict above is already logged and fully formed: never
        # re-wrap it (that would log a second, less specific line) and never
        # change the exception type the caller sees.
        raise
    except Exception as exc:
        # P2 (2026-10-25): a failed probe used to be SILENT — the caller only
        # ever saw the fixed text "metering store unavailable". This is the
        # ONE log site for a probe failure: admission.store_reachable() and
        # the route layer stay silent, and the negative cache bounds repeats
        # to at most one probe (hence one line) per TTL window. Only the
        # allow-listed CAUSE class name is logged — driver messages routinely
        # embed the DSN, user name and password, so str(exc), args and the
        # traceback are never used.
        logger.warning(
            "readiness probe failed: cause=%s",
            _safe_cause_name(exc),
        )
        raise MeteredGateError("metering store unavailable") from exc
    finally:
        # A close failure must not mask the verdict or change the exception
        # type the caller sees.
        if session is not None:
            try:
                session.close()
            except Exception:  # pragma: no cover — defensive
                logger.debug("metering probe session close failed")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def resolve_tenant(
    provided_key: Optional[str],
) -> Tuple[str, Optional[TenantContext]]:
    """Authenticate an API key WITHOUT consuming quota.

    Returns ``(reason, context_or_none)``. Pure lookup — used to answer
    "is this key known and active?" and by :func:`reserve_unit` to
    distinguish authentication failure from quota exhaustion.
    Raises :class:`MeteredGateError` when the metering store is
    unavailable (callers must fail closed).
    """
    from backend.auth.models import TenantQuota, current_usage_month
    from backend.auth.tokens import hash_token

    if not provided_key or not provided_key.strip():
        return REASON_MISSING_KEY, None
    if not _metering_configured():
        # No metering store configured → no credentials exist to match.
        return REASON_UNKNOWN_KEY, None

    key_hash = hash_token(provided_key)
    try:
        SessionLocal = _session_factory()
    except Exception as exc:
        raise MeteredGateError("metering store unavailable") from exc

    try:
        with SessionLocal() as session:
            row = session.get(TenantQuota, key_hash)
            if row is None:
                return REASON_UNKNOWN_KEY, None
            if not row.is_active:
                return REASON_INACTIVE, None
            month = current_usage_month()
            usage = row.current_month_usage if row.usage_month == month else 0
            return REASON_OK, TenantContext(
                tenant_id=row.tenant_id,
                monthly_limit=row.monthly_limit,
                current_month_usage=usage,
                usage_month=month,
                units_reserved=0,
            )
    except MeteredGateError:
        raise
    except Exception as exc:
        raise MeteredGateError("metering lookup failed") from exc


def reserve_unit(provided_key: Optional[str]) -> Tuple[str, Optional[TenantContext]]:
    """Authenticate AND atomically reserve ONE unit of monthly quota.

    Thin wrapper over :func:`reserve_units` with ``units=1`` — the exact
    Phase 16 semantics, kept as a named function because the single-item
    routes and their suites call it. ``usage + 1 <= monthly_limit`` is
    equivalent to the historical ``usage < monthly_limit``.

    See :func:`reserve_units` for the full invariant.
    """
    return reserve_units(provided_key, 1)


def reserve_units(
    provided_key: Optional[str],
    units: int = 1,
) -> Tuple[str, Optional[TenantContext]]:
    """Authenticate AND atomically reserve ``units`` units of monthly quota.

    Phase 5J: bulk admission needs to charge for N independent items in ONE
    decision, so this extends the single-statement predicate rather than
    looping :func:`reserve_unit`. The reservation remains ONE UPDATE whose
    WHERE clause carries the complete admission predicate:

        hash matches  AND  is_active  AND
        ( same-month bucket AND usage + units <= limit
          OR  stale month bucket (rollover — resets to units, and only
              when units itself fits the limit) )

    PostgreSQL serializes concurrent UPDATEs on the same primary-key row,
    therefore:

        concurrent accepted reservations <= monthly_limit
        (and a batch is never partially reserved)

    under arbitrary concurrency — no read-modify-write race, no retries,
    no post-hoc compensation, and no loop that could reserve part of a
    batch and then fail. A quota-exhausted, unknown or inactive key
    matches zero rows and NOTHING is written (authentication failures and
    quota failures consume zero quota — the all-or-nothing property the
    bulk route documents).

    ``units`` must be >= 1; a non-positive value is treated as a caller
    error and rejected without touching the store.

    Returns ``(reason, context_or_none)``; raises
    :class:`MeteredGateError` when the store is unavailable.
    """
    if not isinstance(units, int) or units < 1:
        return REASON_MISSING_KEY, None
    from backend.auth.models import TenantQuota, current_usage_month
    from backend.auth.tokens import hash_token

    if not provided_key or not provided_key.strip():
        return REASON_MISSING_KEY, None
    if not _metering_configured():
        return REASON_UNKNOWN_KEY, None

    key_hash = hash_token(provided_key)
    month = current_usage_month()
    now = datetime.now(timezone.utc)

    try:
        SessionLocal = _session_factory()
    except Exception as exc:
        raise MeteredGateError("metering store unavailable") from exc

    try:
        with SessionLocal() as session:
            result = session.execute(
                update(TenantQuota)
                .where(
                    TenantQuota.api_key_hash == key_hash,
                    TenantQuota.is_active.is_(True),
                    or_(
                        and_(
                            TenantQuota.usage_month == month,
                            TenantQuota.current_month_usage + units
                            <= TenantQuota.monthly_limit,
                        ),
                        # Stale bucket: rollover into the new month —
                        # admissible only when the whole batch fits the
                        # limit, so the limit applies to the NEW month
                        # and a batch is never partially admitted.
                        and_(
                            TenantQuota.usage_month != month,
                            units <= TenantQuota.monthly_limit,
                        ),
                    ),
                )
                .values(
                    current_month_usage=case(
                        (TenantQuota.usage_month != month, units),
                        else_=TenantQuota.current_month_usage + units,
                    ),
                    usage_month=month,
                    last_request_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            reserved = result.rowcount or 0

        if reserved == 1:
            # Re-read the committed row for the exact audit counters.
            reason, ctx = resolve_tenant(provided_key)
            if reason == REASON_OK and ctx is not None:
                return REASON_OK, TenantContext(
                    tenant_id=ctx.tenant_id,
                    monthly_limit=ctx.monthly_limit,
                    current_month_usage=ctx.current_month_usage,
                    usage_month=ctx.usage_month,
                    units_reserved=units,
                )
            # Practically unreachable (row existed a moment ago); treat
            # defensively as a store problem — fail closed.
            raise MeteredGateError("post-reservation readback failed")

        # Zero rows updated: distinguish why, via a pure lookup that
        # consumes nothing. (This lookup is safe, not a retry — the
        # reservation attempt already happened exactly once.)
        reason, _ = resolve_tenant(provided_key)
        if reason in (REASON_UNKNOWN_KEY, REASON_INACTIVE, REASON_MISSING_KEY):
            return reason, None
        return REASON_QUOTA_EXHAUSTED, None
    except MeteredGateError:
        raise
    except Exception as exc:
        raise MeteredGateError("metering reservation failed") from exc


def authorize_request(
    provided_key: Optional[str],
) -> Tuple[str, Optional[TenantContext]]:
    """Full admission decision for ONE unit, mapping failure to fail-closed."""
    return authorize_units(provided_key, 1)


def authorize_units(
    provided_key: Optional[str],
    units: int = 1,
) -> Tuple[str, Optional[TenantContext]]:
    """Full admission decision for ``units`` units, mapping failure closed.

    Never raises for store unavailability: an unavailable metering
    dependency returns ``REASON_METERING_UNAVAILABLE`` so the HTTP layer
    can refuse the request (503) instead of admitting it. The critical
    property: metering failure ⇒ request NOT admitted (and, for a batch,
    NOT partially admitted).
    """
    try:
        return reserve_units(provided_key, units)
    except MeteredGateError as exc:
        # ONE line per refused request, written at the layer that maps the
        # failure to a reason code: the route layer only maps that reason
        # to HTTP, and admission.admit does not log on this path, so a
        # refused request is never logged twice. Logged: the fixed reason
        # code and the allow-listed CAUSE class name. The previous
        # ``type(exc).__name__`` was always the wrapper
        # ("MeteredGateError"), which named nothing about the real
        # failure. Never the message, traceback, DSN or SQL — P1, 2026-10-25.
        logger.warning(
            "metering gate unavailable: reason=%s cause=%s",
            REASON_METERING_UNAVAILABLE,
            _safe_cause_name(exc),
        )
        return REASON_METERING_UNAVAILABLE, None
