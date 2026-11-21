"""Configuration for self-service developer onboarding (v1).

Every value is an explicit environment variable with a bounded, conservative
default. Nothing here enables an operation that needs a secret the deployment
does not have: the store URL and the key-delivery secret are REQUIRED, and a
missing one is a fail-closed state the API reports honestly rather than
guessing.

Units (documented once, here):

  * A quota unit is ONE ADMITTED request on the metered /v1 data plane —
    exactly the unit ``platrixa_tenant_quotas.monthly_limit`` already counts
    (see backend/auth/gate.py). The bucket is a UTC ``YYYY-MM`` calendar
    month and it resets by rollover inside the atomic reservation, not by a
    scheduler.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The onboarding tables live beside the metering tables (same PostgreSQL
# store, same credentials). Reusing that URL is deliberate: provisioning must
# be able to write the key/quota rows in the SAME transaction as the
# application state.
ONBOARDING_DB_ENV_VAR = "PLATRIXA_ONBOARDING_DATABASE_URL"
METERING_DB_ENV_VAR = "PLATRIXA_METERING_DATABASE_URL"

# Key material for one-time delivery. There is NO default and no derived
# fallback: without it, provisioning refuses to issue a key.
DELIVERY_SECRET_ENV_VAR = "PLATRIXA_KEY_DELIVERY_SECRET"
DELIVERY_SECRET_MIN_LEN = 32

# Versioned delivery secret rotation. Keys are derived from (key_id, nonce)
# with a VERSIONED secret; the version is stored with the delivery row, so
# rotating the secret never strands an un-revealed key. Transition sequence:
#   1. set PLATRIXA_KEY_DELIVERY_SECRET_PREVIOUS = <current secret>
#   2. set PLATRIXA_KEY_DELIVERY_SECRET = <new secret> and bump
#      PLATRIXA_KEY_DELIVERY_SECRET_VERSION by one
#   3. keep the previous value until every outstanding delivery window has
#      expired (>= PLATRIXA_ONBOARDING_REVEAL_TTL_SECONDS), then remove it.
# Rows written before versioning are treated as version 1.
DELIVERY_SECRET_PREVIOUS_ENV_VAR = "PLATRIXA_KEY_DELIVERY_SECRET_PREVIOUS"
DELIVERY_SECRET_VERSION_ENV_VAR = "PLATRIXA_KEY_DELIVERY_SECRET_VERSION"
DEFAULT_DELIVERY_SECRET_VERSION = 2

# Notification dispatcher lease: how long one worker owns a claimed row.
DISPATCH_LEASE_ENV_VAR = "PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS"
DEFAULT_DISPATCH_LEASE_SECONDS = 300

# Optional shared token for the operational dispatch endpoint
# (POST /v1/internal/onboarding/dispatch). Unset -> the route answers 404.
DISPATCH_TOKEN_ENV_VAR = "PLATRIXA_ONBOARDING_DISPATCH_TOKEN"
DISPATCH_TOKEN_MIN_LEN = 16

# Dev/test convenience: apply the migration from runtime code. OFF by default
# so a production service account never needs schema-owner privileges.
AUTO_MIGRATE_ENV_VAR = "PLATRIXA_ONBOARDING_AUTO_MIGRATE"

ENABLED_ENV_VAR = "PLATRIXA_ONBOARDING_ENABLED"

# Conservative beta allowance: 50 admitted requests per calendar month, half
# the existing per-key default of 100. Each tenant's quota stays independently
# configurable afterwards (owner dashboard / management plane).
DEFAULT_INITIAL_QUOTA = 50
INITIAL_QUOTA_ENV_VAR = "PLATRIXA_ONBOARDING_INITIAL_QUOTA"

DEFAULT_VERIFICATION_TTL_SECONDS = 24 * 60 * 60
VERIFICATION_TTL_ENV_VAR = "PLATRIXA_ONBOARDING_VERIFICATION_TTL_SECONDS"

DEFAULT_RESEND_COOLDOWN_SECONDS = 60
RESEND_COOLDOWN_ENV_VAR = "PLATRIXA_ONBOARDING_RESEND_COOLDOWN_SECONDS"

DEFAULT_REVEAL_TTL_SECONDS = 15 * 60
REVEAL_TTL_ENV_VAR = "PLATRIXA_ONBOARDING_REVEAL_TTL_SECONDS"

DEFAULT_APPLICATION_EXPIRY_DAYS = 14
APPLICATION_EXPIRY_ENV_VAR = "PLATRIXA_ONBOARDING_APPLICATION_EXPIRY_DAYS"

DEFAULT_MAX_PER_IP_HOUR = 3
MAX_PER_IP_HOUR_ENV_VAR = "PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR"

DEFAULT_MAX_PER_IP_DAY = 8
MAX_PER_IP_DAY_ENV_VAR = "PLATRIXA_ONBOARDING_MAX_PER_IP_DAY"

DEFAULT_GLOBAL_DAILY_CAP = 100
GLOBAL_DAILY_CAP_ENV_VAR = "PLATRIXA_ONBOARDING_GLOBAL_DAILY_CAP"

# Verification-link origin (the public website). Optional: when absent the
# notification text says the link is unavailable instead of inventing one.
PUBLIC_ORIGIN_ENV_VAR = "PLATRIXA_PUBLIC_ORIGIN"

# Owner notification destination. Absent -> owner EMAIL notifications are
# recorded as SUPPRESSED with an explicit reason (the dashboard still shows
# them); the system never invents an address.
OWNER_EMAIL_ENV_VAR = "PLATRIXA_OWNER_NOTIFICATION_EMAIL"

# Free-mail domains are not "work" email. Default: review, never auto-issue.
ALLOW_FREEMAIL_ENV_VAR = "PLATRIXA_ONBOARDING_ALLOW_FREEMAIL"

# Outbound mail identity. The sender is required for SMTP; the reply-to and
# display name are optional and only affect the message headers.
EMAIL_FROM_ENV_VAR = "PLATRIXA_EMAIL_FROM"
EMAIL_REPLY_TO_ENV_VAR = "PLATRIXA_EMAIL_REPLY_TO"

# Where developers send their first authenticated request (defaults to the
# public origin). Configurable so an ingress host can differ from the site.
API_BASE_URL_ENV_VAR = "PLATRIXA_API_BASE_URL"
DOCS_URL_ENV_VAR = "PLATRIXA_DOCS_URL"


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def database_url() -> str:
    """The onboarding/metering store URL, or "" when not configured."""
    return (
        (os.getenv(ONBOARDING_DB_ENV_VAR, "") or "").strip()
        or (os.getenv(METERING_DB_ENV_VAR, "") or "").strip()
    )


def configured() -> bool:
    return bool(database_url()) and _env_bool(ENABLED_ENV_VAR, True)


def delivery_secret() -> str:
    """Key-delivery secret, or "" when absent/too short (fail closed)."""
    value = (os.getenv(DELIVERY_SECRET_ENV_VAR, "") or "").strip()
    return value if len(value) >= DELIVERY_SECRET_MIN_LEN else ""


def delivery_configured() -> bool:
    return bool(delivery_secret())


def delivery_secret_version() -> int:
    """Version stamped on NEW deliveries (default 2, the versioned scheme)."""
    return _env_int(DELIVERY_SECRET_VERSION_ENV_VAR, DEFAULT_DELIVERY_SECRET_VERSION)


def delivery_secret_for(version: int) -> str:
    """The secret for one version, or "" when that version is not configured."""
    current_version = delivery_secret_version()
    if int(version) == current_version:
        return delivery_secret()
    previous = (os.getenv(DELIVERY_SECRET_PREVIOUS_ENV_VAR, "") or "").strip()
    if len(previous) >= DELIVERY_SECRET_MIN_LEN:
        return previous
    return ""


def delivery_secret_candidates(version: int) -> list[tuple[int, str]]:
    """(version, secret) pairs to try when revealing one delivery row.

    The row's own version wins; the current version and the previous one are
    fallbacks for rows written before a rotation (version columns default to 1,
    while the current version starts at 2).
    """
    wanted = [int(version), delivery_secret_version(), max(1, delivery_secret_version() - 1)]
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for candidate in wanted:
        if candidate in seen:
            continue
        seen.add(candidate)
        secret = delivery_secret_for(candidate)
        if secret:
            out.append((candidate, secret))
    return out


def dispatch_lease_seconds() -> int:
    return _env_int(DISPATCH_LEASE_ENV_VAR, DEFAULT_DISPATCH_LEASE_SECONDS)


def dispatch_token() -> str:
    """Operational dispatch token, or "" when the route is disabled."""
    value = (os.getenv(DISPATCH_TOKEN_ENV_VAR, "") or "").strip()
    return value if len(value) >= DISPATCH_TOKEN_MIN_LEN else ""


def auto_migrate() -> bool:
    return _env_bool(AUTO_MIGRATE_ENV_VAR, False)


def public_origin() -> str:
    return (os.getenv(PUBLIC_ORIGIN_ENV_VAR, "") or "").strip().rstrip("/")


def owner_email() -> str:
    return (os.getenv(OWNER_EMAIL_ENV_VAR, "") or "").strip()


def initial_quota() -> int:
    value = _env_int(INITIAL_QUOTA_ENV_VAR, DEFAULT_INITIAL_QUOTA)
    # Mirrors backend.auth.api_keys.validate_monthly_limit bounds.
    return min(value, 1_000_000)


def verification_ttl_seconds() -> int:
    return _env_int(VERIFICATION_TTL_ENV_VAR, DEFAULT_VERIFICATION_TTL_SECONDS)


def resend_cooldown_seconds() -> int:
    return _env_int(RESEND_COOLDOWN_ENV_VAR, DEFAULT_RESEND_COOLDOWN_SECONDS)


def reveal_ttl_seconds() -> int:
    return _env_int(REVEAL_TTL_ENV_VAR, DEFAULT_REVEAL_TTL_SECONDS)


def application_expiry_days() -> int:
    return _env_int(APPLICATION_EXPIRY_ENV_VAR, DEFAULT_APPLICATION_EXPIRY_DAYS)


def max_per_ip_hour() -> int:
    return _env_int(MAX_PER_IP_HOUR_ENV_VAR, DEFAULT_MAX_PER_IP_HOUR)


def max_per_ip_day() -> int:
    return _env_int(MAX_PER_IP_DAY_ENV_VAR, DEFAULT_MAX_PER_IP_DAY)


def global_daily_cap() -> int:
    return _env_int(GLOBAL_DAILY_CAP_ENV_VAR, DEFAULT_GLOBAL_DAILY_CAP)


def allow_freemail() -> bool:
    return _env_bool(ALLOW_FREEMAIL_ENV_VAR, False)


def email_from() -> str:
    return (os.getenv(EMAIL_FROM_ENV_VAR, "") or "").strip()


def email_reply_to() -> str:
    return (os.getenv(EMAIL_REPLY_TO_ENV_VAR, "") or "").strip()


def api_base_url() -> str:
    """Explicit API base for developer-facing text (falls back to origin)."""
    return (os.getenv(API_BASE_URL_ENV_VAR, "") or "").strip().rstrip("/")


def docs_url() -> str:
    return (os.getenv(DOCS_URL_ENV_VAR, "") or "").strip().rstrip("/")


@dataclass(frozen=True)
class Status:
    """The closed state vocabulary (Part 4). Never set from request input."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


STATUS = Status()

ALL_STATUSES = frozenset(
    {
        STATUS.PENDING_VERIFICATION,
        STATUS.VERIFIED,
        STATUS.PROVISIONING,
        STATUS.ACTIVE,
        STATUS.REVIEW_REQUIRED,
        STATUS.REJECTED,
        STATUS.EXPIRED,
        STATUS.SUSPENDED,
    }
)

# States in which one email may hold at most one application (the partial
# unique index in the schema enforces this in the database, not in code).
OPEN_STATUSES = (
    STATUS.PENDING_VERIFICATION,
    STATUS.VERIFIED,
    STATUS.PROVISIONING,
    STATUS.ACTIVE,
    STATUS.REVIEW_REQUIRED,
)
