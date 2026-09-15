"""Platrixa metered developer authentication gate (Phase 16).

Boundary package — admission control only. See gate.py for the atomic
reservation semantics and tokens.py for the honest key-hashing security
boundary. This package performs no financial reasoning of any kind.

Import discipline: importing ``backend.auth`` must stay CHEAP and free of
side effects — the API route imports this package at module scope, and
the Phase 7F clean-import contract forbids loading the database stack
(engine creation, psycopg2) at API import time. ``gate`` and ``tokens``
are light and imported eagerly; ``models`` (which imports
backend.database.db via Base) is exposed lazily via module __getattr__.
"""

from backend.auth.gate import (  # noqa: F401
    METERING_ENV_VAR,
    REASON_INACTIVE,
    REASON_MISSING_KEY,
    REASON_METERING_UNAVAILABLE,
    REASON_OK,
    REASON_QUOTA_EXHAUSTED,
    REASON_UNKNOWN_KEY,
    MeteredGateError,
    TenantContext,
    authorize_request,
    resolve_tenant,
    reserve_unit,
)
from backend.auth.tokens import hash_token  # noqa: F401

__all__ = [
    "METERING_ENV_VAR",
    "MeteredGateError",
    "TenantContext",
    "TenantQuota",
    "REASON_INACTIVE",
    "REASON_MISSING_KEY",
    "REASON_METERING_UNAVAILABLE",
    "REASON_OK",
    "REASON_QUOTA_EXHAUSTED",
    "REASON_UNKNOWN_KEY",
    "authorize_request",
    "current_usage_month",
    "hash_token",
    "resolve_tenant",
    "reserve_unit",
]


def __getattr__(name):
    # Lazy exports that would drag in backend.database.db (engine creation
    # + psycopg2) if imported eagerly. Everything else resolved normally.
    if name in ("TenantQuota", "current_usage_month"):
        from backend.auth import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
