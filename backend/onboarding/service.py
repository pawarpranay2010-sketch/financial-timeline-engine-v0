"""Developer onboarding: application → verification → provisioning → delivery.

Every state transition is decided HERE, from server-side facts only. No request
field can name a status, tenant, quota or approval: the submitted body carries
the six application answers and nothing else.

State machine (the closed vocabulary in config.STATUS):

    submit ──────────────► PENDING_VERIFICATION
    verify (single use) ─► VERIFIED
    eligibility pass ────► PROVISIONING ──commit──► ACTIVE
    eligibility doubt ───► REVIEW_REQUIRED
    abandoned ───────────► EXPIRED
    owner rejects ───────► REJECTED
    owner suspends ──────► SUSPENDED  (keys stop authenticating)

Provisioning is ONE transaction: tenant key row + quota row + delivery row +
application status + notifications all commit together, guarded by database
constraints (unique tenant per application, unique key hash, unique
application per delivery) and, on PostgreSQL, a transaction-scoped advisory
lock on the tenant id. A retry therefore either finds the committed ACTIVE
state and reports it, or leaves no trace.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import timedelta
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.auth.tokens import hash_token
from backend.onboarding import config, keys, notifications, store
from backend.onboarding import email as email_transport
from backend.onboarding.store import (
    access_requests,
    api_keys,
    audit,
    key_deliveries,
    submission_handles,
    tenant_quotas,
    verifications,
)

logger = logging.getLogger("platrixa.onboarding")

# ---------------------------------------------------------------------------
# Errors — deterministic, machine-readable, no internals
# ---------------------------------------------------------------------------


class OnboardingError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, field: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.field = field


class ValidationError(OnboardingError):
    def __init__(self, field: str, code: str, message: str):
        super().__init__(code, message, 400, field=field)


class NotFound(OnboardingError):
    def __init__(self, code: str = "NOT_FOUND", message: str = "not found"):
        super().__init__(code, message, 404)


class Conflict(OnboardingError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, 409)


class Unavailable(OnboardingError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, 503)


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

MAX_NAME = 120
MAX_EMAIL = 320
MAX_COMPANY = 160
MAX_BUILDING = 1000
MAX_PROFILE_URL = 300
MAX_TOPIC = 40

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

USAGE_CHOICES = ("hobby", "small", "medium", "large")
_USAGE_ALIASES = {
    "hobby": "hobby",
    "personal": "hobby",
    "prototype": "hobby",
    "testing": "hobby",
    "small": "small",
    "startup": "small",
    "low": "small",
    "medium": "medium",
    "growing": "medium",
    "moderate": "medium",
    "large": "large",
    "enterprise": "large",
    "scale": "large",
    "high": "large",
}

# Domains that are not a work email. Free-mail is REVIEW, never automatic.
FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "ymail.com",
        "outlook.com", "hotmail.com", "live.com", "msn.com", "aol.com",
        "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me",
        "gmx.com", "mail.com", "zoho.com", "yandex.com", "rediffmail.com",
    }
)

# Throwaway inboxes: a verification there proves nothing about a work email.
DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
        "temp-mail.org", "throwawaymail.com", "yopmail.com", "sharklasers.com",
        "getnada.com", "dispostable.com", "trashmail.com", "maildrop.cc",
        "fakeinbox.com", "mintemail.com", "spamgourmet.com", "mytemp.email",
        "emailondeck.com", "moakt.com", "tempr.email", "discard.email",
    }
)

ROLE_LOCAL_PARTS = frozenset(
    {
        "admin", "administrator", "postmaster", "hostmaster", "webmaster",
        "abuse", "noreply", "no-reply", "donotreply", "test", "testing",
        "root", "info", "sales", "support", "billing", "security", "hello",
    }
)


def _text(value: Any, field: str, *, minimum: int, maximum: int, code: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(field, code, f"{field.replace('_', ' ')} is required")
    cleaned = value.strip()
    if _CONTROL_RE.search(cleaned):
        raise ValidationError(field, code, f"{field.replace('_', ' ')} contains invalid characters")
    if len(cleaned) < minimum:
        raise ValidationError(field, code, f"{field.replace('_', ' ')} is required")
    if len(cleaned) > maximum:
        raise ValidationError(field, "FIELD_TOO_LONG", f"{field.replace('_', ' ')} must be at most {maximum} characters")
    return cleaned


def normalize_email(raw: Any) -> str:
    """Trim, lowercase, and validate. Normalisation makes duplicates visible."""
    if not isinstance(raw, str):
        raise ValidationError("email", "EMAIL_REQUIRED", "work email is required")
    email = raw.strip().lower()
    if not email:
        raise ValidationError("email", "EMAIL_REQUIRED", "work email is required")
    if len(email) > MAX_EMAIL:
        raise ValidationError("email", "EMAIL_TOO_LONG", "work email is too long")
    if _CONTROL_RE.search(email) or not _EMAIL_RE.match(email):
        raise ValidationError("email", "EMAIL_INVALID", "enter a valid email address")
    local, _, domain = email.partition("@")
    if len(local) > 64 or ".." in email or domain.startswith(".") or domain.endswith("."):
        raise ValidationError("email", "EMAIL_INVALID", "enter a valid email address")
    return email


def email_domain(email: str) -> str:
    return email.partition("@")[2].lower()


def email_local_part(email: str) -> str:
    return email.partition("@")[0].lower()


def normalize_usage(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValidationError("expected_usage", "EXPECTED_USAGE_REQUIRED", "expected API usage is required")
    cleaned = raw.strip().lower().replace(" ", "_")
    for choice in USAGE_CHOICES:
        if cleaned == choice:
            return choice
    for token, choice in _USAGE_ALIASES.items():
        if token in cleaned:
            return choice
    raise ValidationError(
        "expected_usage",
        "EXPECTED_USAGE_INVALID",
        "expected usage must be one of: hobby, small, medium, large",
    )


def normalize_profile_url(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "website or GitHub profile must be a URL")
    cleaned = raw.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_PROFILE_URL:
        raise ValidationError("profile_url", "PROFILE_URL_TOO_LONG", f"website or GitHub profile must be at most {MAX_PROFILE_URL} characters")
    if _CONTROL_RE.search(cleaned) or " " in cleaned:
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "enter a valid http(s) URL")
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "enter a valid http(s) URL") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "enter a valid http(s) URL")
    if "." not in parsed.hostname and parsed.hostname != "localhost":
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "enter a valid http(s) URL")
    if "@" in (parsed.netloc or ""):
        raise ValidationError("profile_url", "PROFILE_URL_INVALID", "enter a valid http(s) URL")
    return cleaned


def validate_application_payload(payload: Mapping[str, Any]) -> dict:
    """The six fields, validated server-side. Unknown keys are ignored."""
    name = _text(payload.get("name"), "name", minimum=1, maximum=MAX_NAME, code="NAME_REQUIRED")
    email = normalize_email(payload.get("email"))
    company = _text(payload.get("company"), "company", minimum=1, maximum=MAX_COMPANY, code="COMPANY_REQUIRED")
    building = _text(payload.get("building"), "building", minimum=1, maximum=MAX_BUILDING, code="BUILDING_REQUIRED")
    usage = normalize_usage(payload.get("expected_usage"))
    profile_url = normalize_profile_url(payload.get("profile_url"))
    return {
        "name": name,
        "email": email,
        "company": company,
        "building": building,
        "expected_usage": usage,
        "profile_url": profile_url,
    }


def ip_hash(client_ip: Optional[str]) -> str:
    """Stable, non-reversible client key. Raw addresses are never stored."""
    return hashlib.sha256(f"platrixa-onboarding-ip|{client_ip or 'unknown'}".encode("utf-8")).hexdigest()


def _require_store() -> None:
    if not config.configured():
        raise Unavailable(
            "ONBOARDING_NOT_CONFIGURED",
            "self-service onboarding is not configured on this deployment",
        )
    try:
        # RUNTIME NEVER RUNS DDL. The service account only needs DML rights;
        # the schema is applied by `python -m backend.auth.init_onboarding`.
        # `PLATRIXA_ONBOARDING_AUTO_MIGRATE=1` exists for local development and
        # tests only and is off by default.
        if config.auto_migrate():
            store.ensure_schema()
        else:
            store.require_schema()
    except store.SchemaNotReady as exc:
        logger.error("onboarding schema is not migrated: %s", ", ".join(exc.missing))
        raise Unavailable(
            "ONBOARDING_SCHEMA_NOT_READY",
            "onboarding storage has not been migrated on this deployment; "
            "run the onboarding migration before enabling self-service access",
        ) from exc
    except SQLAlchemyError as exc:
        # The store is unreachable. This is a deployment problem, not a request
        # problem: fail closed with 503 rather than a driver 500.
        logger.warning("onboarding store unavailable: %s", type(exc).__name__)
        raise Unavailable(
            "ONBOARDING_UNAVAILABLE", "onboarding service unavailable"
        ) from exc


def _base_url() -> Optional[str]:
    """API base used in developer-facing text.

    An explicit ``PLATRIXA_API_BASE_URL`` wins (the documented ingress host),
    otherwise the public origin plus ``/v1`` — the route prefix the developer
    actually calls.
    """
    explicit = config.api_base_url()
    if explicit:
        return explicit
    origin = config.public_origin()
    return f"{origin}/v1" if origin else None


def _docs_url() -> Optional[str]:
    explicit = config.docs_url()
    if explicit:
        return explicit
    origin = config.public_origin()
    return f"{origin}/developer" if origin else None


def _tenant_id_for(application_id: str) -> str:
    """Deterministic, bounded, lowercase slug — retry-safe by construction."""
    return "dev-" + application_id.replace("-", "")[:12]


def _view(row: Mapping[str, Any]) -> dict:
    return {
        "application_id": row["id"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "email": row["email"],
        "name": row["name"],
        "company": row["company"],
        "expected_usage": row["expected_usage"],
        "tenant_id": row["tenant_id"],
        "monthly_limit": row["monthly_limit"],
        "email_verified": row["email_verified_at"] is not None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _new_submission_handle(db, application_id: str, email: str, now) -> str:
    """Mint one opaque submission reference inside the caller's transaction.

    The reference is random (128 bits), stored with the application it points
    at, and is the ONLY thing a submitter receives. It is deliberately not the
    application id, not a tenant id and not an authentication token; knowing it
    (together with the applied address) is what makes a status lookup
    legitimate. New and duplicate submissions both receive one, so the response
    shape cannot reveal whether an address already applied.
    """
    handle_id = str(uuid.uuid4())
    db.execute(
        submission_handles.insert().values(
            id=handle_id,
            application_id=application_id,
            email=email,
            created_at=now,
            last_used_at=None,
            revoked_at=None,
        )
    )
    return handle_id


def _existing_or_new_submission_handle(db, application_id: str, email: str, now) -> str:
    """Reuse an existing active handle for this application, else mint one.

    Duplicate and race-lost submissions therefore return the SAME reference as
    the original submission, keeping the acknowledgement byte-identical and
    preventing the handle itself from becoming an oracle.
    """
    existing = db.execute(
        select(submission_handles.c.id)
        .where(
            submission_handles.c.application_id == application_id,
            submission_handles.c.email == email,
            submission_handles.c.revoked_at.is_(None),
        )
        .order_by(submission_handles.c.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing)
    return _new_submission_handle(db, application_id, email, now)


def _handle_for_open_application(email: str) -> Optional[str]:
    """Race-lost submissions: reference the application that won the index."""
    try:
        with store.session() as db:
            row = db.execute(
                select(access_requests.c.id, access_requests.c.email).where(
                    access_requests.c.email == email,
                    access_requests.c.status.in_(config.OPEN_STATUSES),
                )
            ).mappings().first()
            if row is None:
                return None
            with store.session() as db2:
                return _existing_or_new_submission_handle(
                    db2, row["id"], row["email"], store.utcnow()
                )
    except Exception:
        logger.warning("submission reference lookup failed")
        return None


def _audit(db, actor: str, action: str, *, application_id=None, tenant_id=None, detail=None) -> None:
    db.execute(
        audit.insert().values(
            id=str(uuid.uuid4()),
            actor=actor[:128],
            action=action[:64],
            application_id=application_id,
            tenant_id=tenant_id,
            detail=(detail or "")[:500] or None,
            created_at=store.utcnow(),
        )
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def submit_access_request(
    payload: Mapping[str, Any],
    *,
    client_ip: Optional[str] = None,
    visitor_id: Optional[str] = None,
) -> dict:
    """Validate, rate-limit, record the application, send verification."""
    fields = validate_application_payload(payload)
    _require_store()

    now = store.utcnow()
    hashed_ip = ip_hash(client_ip)
    ttl = timedelta(seconds=config.verification_ttl_seconds())

    try:
        with store.session() as db:
            hour_ago = now - timedelta(hours=1)
            day_ago = now - timedelta(days=1)
            per_ip_hour = db.execute(
                select(func.count())
                .select_from(access_requests)
                .where(
                    access_requests.c.client_ip_hash == hashed_ip,
                    access_requests.c.created_at >= hour_ago,
                )
            ).scalar_one()
            per_ip_day = db.execute(
                select(func.count())
                .select_from(access_requests)
                .where(
                    access_requests.c.client_ip_hash == hashed_ip,
                    access_requests.c.created_at >= day_ago,
                )
            ).scalar_one()
            global_day = db.execute(
                select(func.count())
                .select_from(access_requests)
                .where(access_requests.c.created_at >= day_ago)
            ).scalar_one()

            if per_ip_hour >= config.max_per_ip_hour() or per_ip_day >= config.max_per_ip_day():
                notifications.enqueue(
                    db,
                    application_id=None,
                    audience=notifications.AUDIENCE_OWNER,
                    kind="SUSPICIOUS_ACTIVITY",
                    recipient=config.owner_email() or None,
                    subject="Platrixa: onboarding rate limit reached",
                    body=(
                        "An access-request submission was refused by the onboarding rate limit.\n"
                        f"client_key={hashed_ip[:16]}…\n"
                        f"per_ip_hour={per_ip_hour} per_ip_day={per_ip_day}\n"
                        "No application was created. Review if this persists."
                    ),
                    dedupe_key=f"suspicious-rate:{hashed_ip}:{now.strftime('%Y%m%d%H')}",
                )
                # Commit the abuse record BEFORE refusing, so the refusal
                # cannot roll its own evidence back.
                db.commit()
                raise OnboardingError(
                    "RATE_LIMITED",
                    "too many access requests from this client; retry later",
                    429,
                )
            if global_day >= config.global_daily_cap():
                notifications.enqueue(
                    db,
                    application_id=None,
                    audience=notifications.AUDIENCE_OWNER,
                    kind="SUSPICIOUS_ACTIVITY",
                    recipient=config.owner_email() or None,
                    subject="Platrixa: onboarding daily cap reached",
                    body=(
                        "The daily self-service onboarding cap was reached; further requests "
                        f"are refused. cap={config.global_daily_cap()}.\n"
                        "No application was created for this request."
                    ),
                    dedupe_key=f"suspicious-cap:{now.strftime('%Y%m%d')}",
                )
                db.commit()
                raise OnboardingError(
                    "ONBOARDING_CAPACITY_REACHED",
                    "self-service onboarding is temporarily unavailable; retry later",
                    429,
                )

            existing = db.execute(
                select(access_requests.c.id, access_requests.c.status).where(
                    access_requests.c.email == fields["email"],
                    access_requests.c.status.in_(config.OPEN_STATUSES),
                )
            ).mappings().first()

            if existing is not None:
                # Duplicate active application: no second row, no second
                # email, and a BYTE-IDENTICAL answer to a fresh submission so
                # the endpoint cannot be used to confirm which addresses
                # already applied.
                _audit(
                    db,
                    "system",
                    "APPLICATION_DUPLICATE_SUPPRESSED",
                    application_id=existing["id"],
                    detail="open application already exists for this email",
                )
                # Same response SHAPE and same capability as a fresh
                # submission; the reference points at the existing application
                # so the submitter can check its status. No new row, no email.
                duplicate_handle = _existing_or_new_submission_handle(
                    db, existing["id"], fields["email"], now
                )
                return _accepted("duplicate-suppressed", reference=duplicate_handle)

            application_id = str(uuid.uuid4())
            reason = _suspicion_reason(fields, hashed_ip=hashed_ip, db=db, now=now)
            db.execute(
                access_requests.insert().values(
                    id=application_id,
                    email=fields["email"],
                    name=fields["name"],
                    company=fields["company"],
                    building=fields["building"],
                    expected_usage=fields["expected_usage"],
                    profile_url=fields["profile_url"],
                    status=config.STATUS.PENDING_VERIFICATION,
                    status_reason=reason,
                    email_verified_at=None,
                    tenant_id=None,
                    key_id=None,
                    monthly_limit=None,
                    client_ip_hash=hashed_ip,
                    visitor_id=(visitor_id or None) and str(visitor_id)[:64],
                    provisioning_attempts=0,
                    last_error=None,
                    created_at=now,
                    updated_at=now,
                    expires_at=now + timedelta(days=config.application_expiry_days()),
                )
            )

            verification_id, _token = _issue_verification(db, application_id, now)

            _enqueue_verification_email(db, application_id, fields, _token)
            notifications.enqueue(
                db,
                application_id=application_id,
                audience=notifications.AUDIENCE_OWNER,
                kind="NEW_ACCESS_REQUEST",
                recipient=config.owner_email() or None,
                subject=f"Platrixa: new API access request — {fields['company'][:60]}",
                body=(
                    "A new Developer API access request was submitted.\n\n"
                    f"application={application_id}\n"
                    f"name={fields['name']}\n"
                    f"email={fields['email']}\n"
                    f"company={fields['company']}\n"
                    f"expected_usage={fields['expected_usage']}\n"
                    f"profile={fields['profile_url'] or '-'}\n"
                    f"building={fields['building'][:400]}\n"
                    f"suspicion={reason or 'none'}\n\n"
                    "Status: PENDING_VERIFICATION. No key is issued until the work "
                    "email is verified and eligibility passes."
                ),
                dedupe_key=f"owner-new-request:{application_id}",
            )
            _audit(db, "system", "APPLICATION_SUBMITTED", application_id=application_id,
                   detail=f"usage={fields['expected_usage']} suspicion={reason or 'none'}")
            handle_id = _new_submission_handle(db, application_id, fields["email"], now)
    except IntegrityError:
        # A concurrent submission for the same address won the unique index.
        # Same neutral acknowledgement (and same capability) as the duplicate
        # path, resolved in a fresh transaction after the rollback.
        return _accepted(
            "concurrent-duplicate",
            reference=_handle_for_open_application(fields["email"]),
        )
    except SQLAlchemyError as exc:
        logger.warning("onboarding submit store failure: %s", type(exc).__name__)
        raise Unavailable("ONBOARDING_UNAVAILABLE", "onboarding service unavailable") from exc

    # NO SMTP in the request path. The verification and owner notifications are
    # durable rows; the dispatcher (cron/CLI/operator) delivers them. The 202
    # says "application received", never "email sent".
    return _accepted("created", reference=handle_id)


def _accepted(_reason: str, *, reference: Optional[str] = None) -> dict:
    """The ONE public acknowledgement shape for every accept path.

The caller-visible text must not differ between "created", "already
    pending" and "lost a race", or the endpoint becomes an account oracle.
    """
    return {
        "status": config.STATUS.PENDING_VERIFICATION,
        "verification_required": True,
        # Opaque submission capability; null only if a race-lost lookup fails.
        "application_reference": reference,
        "message": (
            "Application received. If this address has an open application, a "
            "verification email is on its way — verify your work email to "
            "continue. Access is issued only after verification and eligibility."
        ),
    }


def _suspicion_reason(fields: Mapping[str, Any], *, hashed_ip: str, db, now) -> Optional[str]:
    """Server-side signals that route an application to REVIEW_REQUIRED.

    Stored on the application (never returned to the applicant) and re-checked
    after verification, so a signal can never be cleared by the browser.
    """
    domain = email_domain(fields["email"])
    if domain in DISPOSABLE_DOMAINS:
        return "DISPOSABLE_DOMAIN"
    if email_local_part(fields["email"]) in ROLE_LOCAL_PARTS:
        return "ROLE_ACCOUNT"
    if domain in FREEMAIL_DOMAINS and not config.allow_freemail():
        return "FREEMAIL_DOMAIN"
    recent_same_ip = db.execute(
        select(func.count())
        .select_from(access_requests)
        .where(
            access_requests.c.client_ip_hash == hashed_ip,
            access_requests.c.created_at >= now - timedelta(days=1),
        )
    ).scalar_one()
    # Third-or-later request from one client in 24h is a multi-account
    # signal; a developer legitimately retrying once is not punished.
    if recent_same_ip >= 2:
        return "MULTIPLE_REQUESTS_SAME_CLIENT"
    return None


def _issue_verification(db, application_id: str, now) -> tuple[str, str]:
    """Create one single-use token; store only its digest."""
    db.execute(
        update(verifications)
        .where(
            verifications.c.application_id == application_id,
            verifications.c.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    verification_id = str(uuid.uuid4())
    token = keys.new_token()
    db.execute(
        verifications.insert().values(
            id=verification_id,
            application_id=application_id,
            token_hash=keys.token_digest(token),
            created_at=now,
            expires_at=now + timedelta(seconds=config.verification_ttl_seconds()),
            consumed_at=None,
            attempts=0,
        )
    )
    return verification_id, token


def _enqueue_verification_email(db, application_id: str, fields: Mapping[str, Any], token: str) -> None:
    origin = config.public_origin()
    if origin:
        link = f"{origin}/developer/verify#token={token}"
        body = (
            f"Hello {fields['name']},\n\n"
            "Thanks for requesting Platrixa Developer API access.\n\n"
            f"Verify your work email to continue:\n{link}\n\n"
            "The link is single-use and expires in "
            f"{max(1, config.verification_ttl_seconds() // 3600)} hour(s). "
            "If you did not request access, ignore this message — no application "
            "is activated without verification.\n\n"
            "— Platrixa"
        )
    else:
        body = (
            f"Hello {fields['name']},\n\n"
            "Thanks for requesting Platrixa Developer API access.\n\n"
            "Email verification is required, but this deployment has no public "
            "website origin configured, so no verification link can be generated. "
            "Contact the Platrixa operator to continue.\n\n"
            "— Platrixa"
        )
    notifications.enqueue(
        db,
        application_id=application_id,
        audience=notifications.AUDIENCE_DEVELOPER,
        kind="EMAIL_VERIFICATION",
        recipient=fields["email"],
        subject="Verify your work email — Platrixa Developer API",
        body=body,
        dedupe_key=f"dev-verify:{uuid.uuid4()}",
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_email(token: str) -> dict:
    """Consume one verification token, then advance the application."""
    if not isinstance(token, str) or not token.strip():
        raise OnboardingError("VERIFICATION_TOKEN_INVALID", "verification link is invalid or expired")
    _require_store()
    digest = keys.token_digest(token.strip())
    now = store.utcnow()
    application_id: Optional[str] = None

    try:
        with store.session() as db:
            row = db.execute(
                select(verifications).where(verifications.c.token_hash == digest)
            ).mappings().first()
            if row is None:
                raise OnboardingError(
                    "VERIFICATION_TOKEN_INVALID",
                    "verification link is invalid or expired",
                )
            if row["consumed_at"] is not None:
                raise OnboardingError(
                    "VERIFICATION_TOKEN_INVALID",
                    "verification link is invalid or expired",
                )
            if row["expires_at"] <= now:
                db.execute(
                    update(verifications)
                    .where(verifications.c.id == row["id"])
                    .values(consumed_at=now)
                )
                raise OnboardingError(
                    "VERIFICATION_TOKEN_EXPIRED",
                    "verification link has expired; request a new one",
                )

            consumed = db.execute(
                update(verifications)
                .where(
                    verifications.c.id == row["id"],
                    verifications.c.consumed_at.is_(None),
                )
                .values(consumed_at=now, attempts=verifications.c.attempts + 1)
            )
            if (consumed.rowcount or 0) != 1:
                # Concurrent verification: exactly one caller wins.
                raise OnboardingError(
                    "VERIFICATION_TOKEN_INVALID",
                    "verification link is invalid or expired",
                )

            application = db.execute(
                select(access_requests).where(access_requests.c.id == row["application_id"])
            ).mappings().first()
            if application is None:
                raise OnboardingError("VERIFICATION_TOKEN_INVALID", "verification link is invalid or expired")
            application_id = application["id"]

            if application["expires_at"] <= now:
                db.execute(
                    update(access_requests)
                    .where(access_requests.c.id == application_id)
                    .values(status=config.STATUS.EXPIRED, updated_at=now)
                )
                raise OnboardingError("APPLICATION_EXPIRED", "this application has expired")

            if application["status"] in (
                config.STATUS.REJECTED,
                config.STATUS.SUSPENDED,
                config.STATUS.REVIEW_REQUIRED,
                config.STATUS.ACTIVE,
            ):
                return _view(application)

            db.execute(
                update(access_requests)
                .where(access_requests.c.id == application_id)
                .values(
                    status=config.STATUS.VERIFIED,
                    email_verified_at=application["email_verified_at"] or now,
                    updated_at=now,
                )
            )
            notifications.enqueue(
                db,
                application_id=application_id,
                audience=notifications.AUDIENCE_OWNER,
                kind="EMAIL_VERIFIED",
                recipient=config.owner_email() or None,
                subject=f"Platrixa: work email verified — {application['company'][:60]}",
                body=(
                    "A developer verified their work email.\n\n"
                    f"application={application_id}\n"
                    f"email={application['email']}\n"
                    f"company={application['company']}\n"
                    "State: VERIFIED. Eligibility and provisioning run automatically."
                ),
                dedupe_key=f"owner-verified:{application_id}",
            )
            _audit(db, "developer", "EMAIL_VERIFIED", application_id=application_id)
    except SQLAlchemyError as exc:
        logger.warning("onboarding verify store failure: %s", type(exc).__name__)
        raise Unavailable("ONBOARDING_UNAVAILABLE", "onboarding service unavailable") from exc

    # Delivery is the dispatcher's job; this request never waits on SMTP.
    return advance_application(application_id)


def resend_verification(email: str) -> dict:
    """Re-issue a verification link with throttle + neutral responses."""
    normalized = normalize_email(email)
    _require_store()
    now = store.utcnow()
    generic = {
        "status": config.STATUS.PENDING_VERIFICATION,
        "message": "If an application is awaiting verification, a new link has been sent.",
    }
    try:
        with store.session() as db:
            application = db.execute(
                select(access_requests).where(
                    access_requests.c.email == normalized,
                    access_requests.c.status == config.STATUS.PENDING_VERIFICATION,
                )
            ).mappings().first()
            if application is None:
                return generic

            last = db.execute(
                select(verifications.c.created_at)
                .where(verifications.c.application_id == application["id"])
                .order_by(verifications.c.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if last is not None and (now - last) < timedelta(seconds=config.resend_cooldown_seconds()):
                raise OnboardingError(
                    "RESEND_THROTTLED",
                    "a verification email was sent recently; wait before requesting another",
                    429,
                )

            _verification_id, token = _issue_verification(db, application["id"], now)
            fields = {
                "name": application["name"],
                "email": application["email"],
            }
            _enqueue_verification_email(db, application["id"], fields, token)
            _audit(db, "developer", "VERIFICATION_RESENT", application_id=application["id"])
    except SQLAlchemyError as exc:
        logger.warning("onboarding resend store failure: %s", type(exc).__name__)
        raise Unavailable("ONBOARDING_UNAVAILABLE", "onboarding service unavailable") from exc

    return generic


def access_status(
    application_id: Optional[str] = None,
    email: str = "",
    *,
    reference: Optional[str] = None,
) -> dict:
    """Status for one application.

    Authorized by the opaque ``application_reference`` returned by submission
    (preferred) plus the applied address, or by the legacy application id plus
    the address. The reference is a 128-bit capability, so guessing one is not
    feasible; a wrong or unknown reference is indistinguishable from a wrong
    address, and neither reveals whether an account exists.
    """
    normalized = normalize_email(email)
    _require_store()
    now = store.utcnow()
    with store.session() as db:
        app_id: Optional[str] = None
        handle = None
        if reference is not None:
            if not isinstance(reference, str) or not reference.strip():
                raise OnboardingError("APPLICATION_NOT_FOUND", "application not found", 404)
            handle = db.execute(
                select(submission_handles).where(
                    submission_handles.c.id == reference.strip(),
                    submission_handles.c.revoked_at.is_(None),
                )
            ).mappings().first()
            # The reference alone is never enough: the bound address must match.
            if handle is not None and handle["email"] == normalized:
                app_id = handle["application_id"]
        elif isinstance(application_id, str) and application_id.strip():
            app_id = application_id.strip()

        if app_id is None:
            raise OnboardingError("APPLICATION_NOT_FOUND", "application not found", 404)

        row = db.execute(
            select(access_requests).where(
                access_requests.c.id == app_id,
                access_requests.c.email == normalized,
            )
        ).mappings().first()
        if row is None:
            # Indistinguishable from a wrong address: no enumeration.
            raise OnboardingError("APPLICATION_NOT_FOUND", "application not found", 404)
        if handle is not None:
            db.execute(
                update(submission_handles)
                .where(submission_handles.c.id == handle["id"])
                .values(last_used_at=now)
            )

    view = _view(row)
    # Internal tenant ids are not exposed by a status lookup.
    view.pop("tenant_id", None)
    if reference is not None:
        view["application_reference"] = reference
    return view


# ---------------------------------------------------------------------------
# Eligibility + provisioning
# ---------------------------------------------------------------------------


def advance_application(application_id: str, *, actor: str = "system") -> dict:
    """Decide the next state from server-side facts and act on it."""
    _require_store()
    now = store.utcnow()
    try:
        with store.session() as db:
            row = store.lock_row(db, access_requests, application_id)
            if row is None:
                raise NotFound("APPLICATION_NOT_FOUND", "application not found")
            if row["status"] == config.STATUS.ACTIVE:
                return _view(row)
            if row["status"] in (
                config.STATUS.PENDING_VERIFICATION,
                config.STATUS.REJECTED,
                config.STATUS.EXPIRED,
                config.STATUS.SUSPENDED,
            ):
                return _view(row)
            if row["expires_at"] <= now:
                db.execute(
                    update(access_requests)
                    .where(access_requests.c.id == application_id)
                    .values(status=config.STATUS.EXPIRED, updated_at=now)
                )
                _audit(db, actor, "APPLICATION_EXPIRED", application_id=application_id)
                refreshed = db.execute(
                    select(access_requests).where(access_requests.c.id == application_id)
                ).mappings().first()
                return _view(refreshed)

            decision, reason = _eligibility(row, db=db, now=now)
            if decision == "REVIEW":
                db.execute(
                    update(access_requests)
                    .where(access_requests.c.id == application_id)
                    .values(
                        status=config.STATUS.REVIEW_REQUIRED,
                        status_reason=reason,
                        updated_at=now,
                    )
                )
                notifications.enqueue(
                    db,
                    application_id=application_id,
                    audience=notifications.AUDIENCE_OWNER,
                    kind="REVIEW_REQUIRED",
                    recipient=config.owner_email() or None,
                    subject="Platrixa: access request needs review",
                    body=(
                        "An application was moved to REVIEW_REQUIRED; no key was issued.\n\n"
                        f"application={application_id}\n"
                        f"email={row['email']}\n"
                        f"company={row['company']}\n"
                        f"reason={reason}\n\n"
                        "Review it in the owner dashboard and provision or reject it."
                    ),
                    dedupe_key=f"owner-review:{application_id}",
                )
                _audit(db, actor, "REVIEW_REQUIRED", application_id=application_id, detail=str(reason))
                refreshed = db.execute(
                    select(access_requests).where(access_requests.c.id == application_id)
                ).mappings().first()
                return _view(refreshed)

            if decision == "HOLD":
                db.execute(
                    update(access_requests)
                    .where(access_requests.c.id == application_id)
                    .values(status_reason=reason, last_error=reason, updated_at=now)
                )
                notifications.enqueue(
                    db,
                    application_id=application_id,
                    audience=notifications.AUDIENCE_OWNER,
                    kind="PROVISIONING_FAILED",
                    recipient=config.owner_email() or None,
                    subject="Platrixa: provisioning blocked by configuration",
                    body=(
                        "A verified application cannot be provisioned automatically.\n\n"
                        f"application={application_id}\n"
                        f"email={row['email']}\n"
                        f"reason={reason}\n\n"
                        "Set the missing server configuration and retry from the dashboard. "
                        "No key was issued and no tenant was created."
                    ),
                    dedupe_key=f"owner-provisioning-blocked:{application_id}:{reason}",
                )
                _audit(db, actor, "PROVISIONING_BLOCKED", application_id=application_id, detail=str(reason))
                refreshed = db.execute(
                    select(access_requests).where(access_requests.c.id == application_id)
                ).mappings().first()
                return _view(refreshed)
    except NotFound:
        raise
    except SQLAlchemyError as exc:
        logger.warning("onboarding advance store failure: %s", type(exc).__name__)
        raise Unavailable("ONBOARDING_UNAVAILABLE", "onboarding service unavailable") from exc

    return provision_application(application_id, actor=actor)


def _eligibility(row: Mapping[str, Any], *, db, now) -> tuple[str, Optional[str]]:
    """Pure server-side decision. Returns ("PROVISION"|"REVIEW"|"HOLD", reason)."""
    email = row["email"]
    domain = email_domain(email)
    if domain in DISPOSABLE_DOMAINS:
        return "REVIEW", "DISPOSABLE_DOMAIN"
    if email_local_part(email) in ROLE_LOCAL_PARTS:
        return "REVIEW", "ROLE_ACCOUNT"
    if domain in FREEMAIL_DOMAINS and not config.allow_freemail():
        return "REVIEW", "FREEMAIL_DOMAIN"
    if row["status_reason"] in ("DISPOSABLE_DOMAIN", "ROLE_ACCOUNT", "FREEMAIL_DOMAIN"):
        return "REVIEW", row["status_reason"]

    tenant = row["tenant_id"] or _tenant_id_for(row["id"])
    existing_key = db.execute(
        select(api_keys.c.id).where(api_keys.c.tenant_id == tenant).limit(1)
    ).first()
    other_application = db.execute(
        select(access_requests.c.id).where(
            access_requests.c.tenant_id == tenant,
            access_requests.c.id != row["id"],
        ).limit(1)
    ).first()
    if existing_key is not None or other_application is not None:
        return "REVIEW", "TENANT_ALREADY_PROVISIONED"

    verified_today = db.execute(
        select(func.count())
        .select_from(access_requests)
        .where(
            access_requests.c.status.in_((config.STATUS.ACTIVE, config.STATUS.PROVISIONING)),
            access_requests.c.updated_at >= now - timedelta(hours=24),
        )
    ).scalar_one()
    if verified_today >= config.global_daily_cap():
        return "REVIEW", "ONBOARDING_CAPACITY"

    if not config.delivery_configured():
        return "HOLD", "KEY_DELIVERY_NOT_CONFIGURED"
    return "PROVISION", None


def provision_application(application_id: str, *, actor: str = "system") -> dict:
    """Transactional, idempotent provisioning. One tenant, one key, one quota."""
    _require_store()
    now = store.utcnow()
    try:
        with store.session() as db:
            row = store.lock_row(db, access_requests, application_id)
            if row is None:
                raise NotFound("APPLICATION_NOT_FOUND", "application not found")
            if row["status"] == config.STATUS.ACTIVE and row["tenant_id"]:
                return _provisioning_view(db, row)
            if row["status"] not in (config.STATUS.VERIFIED, config.STATUS.PROVISIONING):
                raise Conflict(
                    "APPLICATION_NOT_PROVISIONABLE",
                    f"application in state {row['status']} cannot be provisioned",
                )

            tenant = row["tenant_id"] or _tenant_id_for(application_id)
            if db.get_bind().dialect.name == "postgresql":
                # Serialise concurrent provisioning of this tenant.
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:t)::bigint)"),
                    {"t": f"platrixa-tenant:{tenant}"},
                )

            existing = db.execute(
                select(api_keys.c.id).where(api_keys.c.tenant_id == tenant).limit(1)
            ).first()
            if existing is not None:
                raise Conflict(
                    "TENANT_ALREADY_PROVISIONED",
                    "this tenant already owns a credential; rotate or revoke it instead",
                )

            db.execute(
                update(access_requests)
                .where(access_requests.c.id == application_id)
                .values(status=config.STATUS.PROVISIONING, updated_at=now)
            )

            monthly_limit = config.initial_quota()
            key_id = str(uuid.uuid4())
            # Derived key material: the raw key is discarded immediately and
            # re-derived only during the one-time reveal window.
            raw_key, key_hash, key_prefix, nonce, secret_version = keys.derive_for_key_id(key_id)
            del raw_key  # never stored, never logged

            db.execute(
                api_keys.insert().values(
                    id=key_id,
                    tenant_id=tenant,
                    name="Self-service onboarding",
                    key_prefix=key_prefix,
                    key_hash=key_hash,
                    environment="live",
                    status="ACTIVE",
                    created_at=now,
                    last_used_at=None,
                    revoked_at=None,
                )
            )
            db.execute(
                tenant_quotas.insert().values(
                    api_key_hash=key_hash,
                    tenant_id=tenant,
                    monthly_limit=monthly_limit,
                    current_month_usage=0,
                    usage_month=_usage_month(now),
                    is_active=True,
                    last_request_at=None,
                )
            )
            reveal_token = keys.new_token()
            db.execute(
                key_deliveries.insert().values(
                    key_id=key_id,
                    application_id=application_id,
                    tenant_id=tenant,
                    key_prefix=key_prefix,
                    key_hash=key_hash,
                    key_nonce=nonce,
                    reveal_token_hash=keys.token_digest(reveal_token),
                    secret_version=secret_version,
                    created_at=now,
                    expires_at=now + timedelta(seconds=config.reveal_ttl_seconds()),
                    revealed_at=None,
                    voided_at=None,
                )
            )
            db.execute(
                update(access_requests)
                .where(access_requests.c.id == application_id)
                .values(
                    status=config.STATUS.ACTIVE,
                    status_reason=None,
                    tenant_id=tenant,
                    key_id=key_id,
                    monthly_limit=monthly_limit,
                    provisioning_attempts=access_requests.c.provisioning_attempts + 1,
                    last_error=None,
                    updated_at=now,
                )
            )

            _enqueue_welcome_email(
                db,
                row=row,
                tenant=tenant,
                monthly_limit=monthly_limit,
                reveal_token=reveal_token,
                key_prefix=key_prefix,
            )
            notifications.enqueue(
                db,
                application_id=application_id,
                audience=notifications.AUDIENCE_OWNER,
                kind="PROVISIONED",
                recipient=config.owner_email() or None,
                subject=f"Platrixa: developer provisioned — {tenant}",
                body=(
                    "Automatic provisioning completed.\n\n"
                    f"application={application_id}\n"
                    f"email={row['email']}\n"
                    f"company={row['company']}\n"
                    f"tenant={tenant}\n"
                    f"key_prefix={key_prefix}…\n"
                    f"monthly_limit={monthly_limit} (admitted requests per UTC month)\n\n"
                    "The raw key is delivered once to the verified developer; it is "
                    "not included here and is not stored in plaintext."
                ),
                dedupe_key=f"owner-provisioned:{application_id}",
            )
            _audit(db, actor, "TENANT_PROVISIONED", application_id=application_id,
                   tenant_id=tenant, detail=f"quota={monthly_limit} key_prefix={key_prefix}")

            refreshed = db.execute(
                select(access_requests).where(access_requests.c.id == application_id)
            ).mappings().first()
            return _provisioning_view(db, refreshed)
    except (Conflict, NotFound, Unavailable):
        raise
    except Exception as exc:  # any failure mid-transaction must roll back
        logger.warning("provisioning failed: %s", type(exc).__name__)
        _record_provisioning_failure(application_id, type(exc).__name__, actor=actor)
        raise Unavailable(
            "PROVISIONING_FAILED",
            "provisioning failed; no tenant or key was created and the application can be retried",
        ) from exc
def _provisioning_view(db, row: Mapping[str, Any]) -> dict:
    quota = None
    if row["tenant_id"]:
        quota = db.execute(
            select(
                tenant_quotas.c.monthly_limit,
                tenant_quotas.c.current_month_usage,
                tenant_quotas.c.usage_month,
            ).where(tenant_quotas.c.tenant_id == row["tenant_id"]).limit(1)
        ).mappings().first()
    view = _view(row)
    view.update(
        {
            "api_base_url": _base_url(),
            "docs_url": _docs_url(),
            "quota": dict(quota) if quota else None,
            "key_delivery": "one-time link emailed to the verified address",
        }
    )
    return view


def _record_provisioning_failure(application_id: str, reason: str, *, actor: str) -> None:
    """Best-effort failure bookkeeping. Never changes the state to ACTIVE."""
    now = store.utcnow()
    try:
        with store.session() as db:
            row = db.execute(
                select(access_requests).where(access_requests.c.id == application_id)
            ).mappings().first()
            if row is None or row["status"] == config.STATUS.ACTIVE:
                # Never walk a committed ACTIVE application backwards.
                return
            attempts = int(row["provisioning_attempts"] or 0) + 1
            db.execute(
                update(access_requests)
                .where(access_requests.c.id == application_id)
                .values(
                    # Rolled-back provisioning leaves VERIFIED, so a retry is
                    # always possible without operator state surgery.
                    status=config.STATUS.VERIFIED,
                    provisioning_attempts=attempts,
                    last_error=reason[:120],
                    updated_at=now,
                )
            )
            notifications.enqueue(
                db,
                application_id=application_id,
                audience=notifications.AUDIENCE_OWNER,
                kind="PROVISIONING_FAILED",
                recipient=config.owner_email() or None,
                subject="Platrixa: provisioning failed",
                body=(
                    "Automatic provisioning failed and was rolled back.\n\n"
                    f"application={application_id}\n"
                    f"email={row['email']}\n"
                    f"failure={reason}\n"
                    f"attempts={attempts}\n\n"
                    "No tenant, key or quota row was committed. Retry from the "
                    "owner dashboard once the cause is resolved."
                ),
                dedupe_key=f"owner-provisioning-failed:{application_id}:{attempts}",
            )
            _audit(db, actor, "PROVISIONING_FAILED", application_id=application_id,
                   detail=f"cause={reason} attempts={attempts}")
    except Exception:  # bookkeeping must never mask the original failure
        logger.warning("could not record provisioning failure")


def _usage_month(now) -> str:
    # Same UTC YYYY-MM bucket the metered gate uses.
    return now.strftime("%Y-%m")


def _enqueue_welcome_email(db, *, row, tenant, monthly_limit, reveal_token, key_prefix) -> None:
    origin = config.public_origin()
    base = _base_url() or "<API_BASE_URL>"
    docs = _docs_url() or "<DOCS_URL>"
    if origin:
        reveal_link = f"{origin}/developer/key#token={reveal_token}"
        delivery = (
            f"Retrieve your key ONCE (link expires in "
            f"{max(1, config.reveal_ttl_seconds() // 60)} minutes):\n{reveal_link}\n\n"
            "After it is shown once, it cannot be retrieved again — copy it into "
            "your own backend environment variable or secret manager. Never embed "
            "an API key in browser JavaScript or a public mobile app."
        )
    else:
        delivery = (
            "A one-time key link could not be generated because this deployment has "
            "no public website origin configured. Contact the operator."
        )
    body = (
        f"Hello {row['name']},\n\n"
        "Your Platrixa Developer API access is active.\n\n"
        f"Developer / tenant id : {tenant}\n"
        f"Key id (public)       : {row['key_id'] or 'issued'}\n"
        f"API base URL          : {base}\n"
        f"Documentation         : {docs}\n"
        f"Initial quota         : {monthly_limit} admitted requests per UTC calendar month\n"
        f"Key prefix            : {key_prefix}…\n\n"
        f"{delivery}\n\n"
        "Authentication (send the key from your server, never from a browser):\n"
        f"  curl -X POST \"{base}/process\" \\\n"
        "    -H \"Content-Type: application/json\" \\\n"
        "    -H \"X-Platrixa-API-Key: $PLATRIXA_API_KEY\" \\\n"
        "    -d '{\"raw_input\": \"Purchased furniture for cash Rs. 15,000\"}'\n\n"
        "A VERIFIED result comes from the deterministic engine; model output alone "
        "never establishes it. Quota counts admitted requests; authentication "
        "failures cost nothing.\n\n"
        "Support: reply to this message or contact the Platrixa operator.\n\n"
        "— Platrixa"
    )
    notifications.enqueue(
        db,
        application_id=row["id"],
        audience=notifications.AUDIENCE_DEVELOPER,
        kind="ONBOARDING",
        recipient=row["email"],
        subject="Your Platrixa Developer API access is active",
        body=body,
        dedupe_key=f"dev-welcome:{row['id']}",
    )


# ---------------------------------------------------------------------------
# One-time key delivery
# ---------------------------------------------------------------------------


def reveal_key(reveal_token: str) -> dict:
    """Return the raw key exactly once, then make it un-derivable."""
    if not isinstance(reveal_token, str) or not reveal_token.strip():
        raise OnboardingError("REVEAL_TOKEN_INVALID", "key link is invalid or has expired", 404)
    _require_store()
    digest = keys.token_digest(reveal_token.strip())
    now = store.utcnow()

    try:
        with store.session() as db:
            delivery = db.execute(
                select(key_deliveries).where(key_deliveries.c.reveal_token_hash == digest)
            ).mappings().first()
            if delivery is None:
                raise OnboardingError("REVEAL_TOKEN_INVALID", "key link is invalid or has expired", 404)
            if delivery["revealed_at"] is not None or delivery["voided_at"] is not None:
                raise OnboardingError(
                    "REVEAL_ALREADY_USED",
                    "this key link has already been used; rotate the key to receive a new one",
                    410,
                )
            if delivery["expires_at"] <= now or not delivery["key_nonce"]:
                db.execute(
                    update(key_deliveries)
                    .where(key_deliveries.c.key_id == delivery["key_id"])
                    .values(key_nonce=None, voided_at=delivery["voided_at"] or now)
                )
                # Persist the void BEFORE refusing: a refusal must not roll
                # back the very mitigation it just applied.
                db.commit()
                raise OnboardingError(
                    "REVEAL_LINK_EXPIRED",
                    "this key link has expired; rotate the key to receive a new one",
                    410,
                )

            # Versioned derivation: try the row's own secret version first,
            # then the current and previous deployment secrets, so rotating
            # PLATRIXA_KEY_DELIVERY_SECRET never strands an un-revealed key.
            stored_version = int(delivery["secret_version"] or 1)
            candidates = config.delivery_secret_candidates(stored_version)
            if not candidates:
                raise Unavailable(
                    "KEY_DELIVERY_NOT_CONFIGURED",
                    "key delivery is not configured on this deployment",
                )
            raw = None
            for _version, secret in candidates:
                candidate = keys.derive_api_key(
                    delivery["key_id"], delivery["key_nonce"], secret
                )
                if keys.constant_time_equal(hash_token(candidate), delivery["key_hash"]):
                    raw = candidate
                    break
            if raw is None:
                # No configured secret reproduces the stored digest: refuse
                # rather than hand out a key that will not authenticate.
                raise Unavailable("KEY_DELIVERY_INTEGRITY", "key delivery integrity check failed")

            consumed = db.execute(
                update(key_deliveries)
                .where(
                    key_deliveries.c.key_id == delivery["key_id"],
                    key_deliveries.c.revealed_at.is_(None),
                )
                .values(revealed_at=now, key_nonce=None)
            )
            if (consumed.rowcount or 0) != 1:
                raise OnboardingError(
                    "REVEAL_ALREADY_USED",
                    "this key link has already been used; rotate the key to receive a new one",
                    410,
                )

            application = db.execute(
                select(access_requests).where(access_requests.c.id == delivery["application_id"])
            ).mappings().first() if delivery["application_id"] else None
            quota = db.execute(
                select(tenant_quotas.c.monthly_limit, tenant_quotas.c.usage_month)
                .where(tenant_quotas.c.api_key_hash == delivery["key_hash"])
            ).mappings().first()
            _audit(db, "developer", "KEY_REVEALED", application_id=delivery["application_id"],
                   tenant_id=delivery["tenant_id"], detail=f"key_prefix={delivery['key_prefix']}")
            return {
                "api_key": raw,
                "key_id": delivery["key_id"],
                "key_prefix": delivery["key_prefix"],
                "tenant_id": delivery["tenant_id"],
                "api_base_url": _base_url(),
                "docs_url": _docs_url(),
                "monthly_limit": quota["monthly_limit"] if quota else None,
                "quota_month": quota["usage_month"] if quota else None,
                "reveal_expires_at": delivery["expires_at"].isoformat(),
                "warning": (
                    "Store this key in a server-side environment variable or secret "
                    "manager now. It cannot be retrieved again. Never ship it in "
                    "browser JavaScript or a public mobile app."
                ),
            }
    except (OnboardingError, Unavailable):
        raise
    except SQLAlchemyError as exc:
        logger.warning("key reveal store failure: %s", type(exc).__name__)
        raise Unavailable("ONBOARDING_UNAVAILABLE", "onboarding service unavailable") from exc


# ---------------------------------------------------------------------------
# Developer-plane reads and rotation (authenticated by the tenant's own key)
# ---------------------------------------------------------------------------


def tenant_usage(tenant_id: str) -> dict:
    _require_store()
    with store.session() as db:
        keys_rows = db.execute(
            select(api_keys.c.id, api_keys.c.name, api_keys.c.key_prefix,
                   api_keys.c.status, api_keys.c.environment, api_keys.c.created_at)
            .where(api_keys.c.tenant_id == tenant_id)
            .order_by(api_keys.c.created_at.desc())
        ).mappings().all()
        quota_rows = db.execute(
            select(
                tenant_quotas.c.api_key_hash,
                tenant_quotas.c.monthly_limit,
                tenant_quotas.c.current_month_usage,
                tenant_quotas.c.usage_month,
                tenant_quotas.c.is_active,
                tenant_quotas.c.last_request_at,
            ).where(tenant_quotas.c.tenant_id == tenant_id)
        ).mappings().all()

    total_limit = sum(int(q["monthly_limit"]) for q in quota_rows if q["is_active"])
    total_used = sum(int(q["current_month_usage"]) for q in quota_rows if q["is_active"])
    return {
        "tenant_id": tenant_id,
        "monthly_limit": total_limit,
        "current_month_usage": total_used,
        "remaining": max(0, total_limit - total_used),
        "usage_month": quota_rows[0]["usage_month"] if quota_rows else None,
        "keys": [
            {
                "key_id": k["id"],
                "name": k["name"],
                "key_prefix": k["key_prefix"],
                "status": k["status"],
                "environment": k["environment"],
                "created_at": k["created_at"].isoformat() if k["created_at"] else None,
            }
            for k in keys_rows
        ],
    }


def _iso(value: Any) -> Optional[str]:
    """ISO timestamp for a datetime OR a driver-returned string (raw SQL)."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def tenant_jobs(tenant_id: str, *, limit: int = 20, offset: int = 0) -> dict:
    """Tenant-scoped job history. Returns empty+available=false if no store."""
    _require_store()
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    try:
        with store.session() as db:
            rows = db.execute(
                text(
                    "SELECT job_id, status, kind, result_id, created_at, updated_at "
                    "FROM platrixa_async_jobs WHERE tenant_id = :t "
                    "ORDER BY created_at DESC LIMIT :l OFFSET :o"
                ),
                {"t": tenant_id, "l": limit, "o": offset},
            ).mappings().all()
    except SQLAlchemyError:
        return {"tenant_id": tenant_id, "available": False, "jobs": []}
    return {
        "tenant_id": tenant_id,
        "available": True,
        "jobs": [
            {
                "job_id": r["job_id"],
                "status": r["status"],
                "kind": r["kind"],
                "result_id": r["result_id"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ],
    }


def visitor_jobs(
    tenant_id: str, visitor_id: str, *, limit: int = 20, offset: int = 0
) -> dict:
    """WEBSITE-surface job history: only the verified visitor's own jobs.

    The website reaches the API with the SHARED website tenant key, so a
    tenant-only query would expose every visitor's jobs to every other
    visitor. Ownership is the edge-verified visitor id bound by the middleware
    (never a client-supplied tenant/visitor value), and the tenant filter is
    still applied as defence in depth.
    """
    _require_store()
    if not isinstance(visitor_id, str) or not visitor_id.strip():
        raise OnboardingError(
            "VISITOR_AUTH_REQUIRED", "website request has no verified visitor identity", 403
        )
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    try:
        with store.session() as db:
            rows = db.execute(
                text(
                    "SELECT job_id, status, kind, result_id, created_at, updated_at "
                    "FROM platrixa_async_jobs WHERE tenant_id = :t AND visitor_id = :v "
                    "ORDER BY created_at DESC LIMIT :l OFFSET :o"
                ),
                {"t": tenant_id, "v": visitor_id.strip(), "l": limit, "o": offset},
            ).mappings().all()
    except SQLAlchemyError:
        return {"tenant_id": tenant_id, "scope": "visitor", "available": False, "jobs": []}
    return {
        "tenant_id": tenant_id,
        "scope": "visitor",
        "available": True,
        "jobs": [
            {
                "job_id": r["job_id"],
                "status": r["status"],
                "kind": r["kind"],
                "result_id": r["result_id"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ],
    }


def rotation_target(db, tenant_id: str, key_id: Optional[str]) -> Optional[Mapping[str, Any]]:
    query = select(api_keys).where(
        api_keys.c.tenant_id == tenant_id,
        api_keys.c.status == "ACTIVE",
    )
    if key_id:
        query = query.where(api_keys.c.id == key_id)
    return db.execute(query.order_by(api_keys.c.created_at.desc()).limit(1)).mappings().first()


def rotate_tenant_key(tenant_id: str, key_id: Optional[str], *, actor: str) -> dict:
    """Revoke one active key and issue its replacement in ONE transaction.

    The replacement's raw key is never returned here: it is delivered through
    the same one-time reveal mechanism as initial onboarding.
    """
    _require_store()
    if not config.delivery_configured():
        raise Unavailable(
            "KEY_DELIVERY_NOT_CONFIGURED",
            "key delivery is not configured on this deployment; rotation refused",
        )
    now = store.utcnow()
    try:
        with store.session() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:t)::bigint)"),
                    {"t": f"platrixa-tenant:{tenant_id}"},
                )
            old = rotation_target(db, tenant_id, key_id)
            if old is None:
                raise NotFound("API_KEY_NOT_FOUND", "no active key found for this tenant")

            db.execute(
                update(api_keys)
                .where(api_keys.c.id == old["id"])
                .values(status="REVOKED", revoked_at=now)
            )
            # Usage is CARRIED OVER, not reset: rotation replaces the
            # credential, not the billing period. The old quota row stays as
            # inactive history; the new one starts with the same bucket.
            old_quota = db.execute(
                select(
                    tenant_quotas.c.monthly_limit,
                    tenant_quotas.c.current_month_usage,
                    tenant_quotas.c.usage_month,
                    tenant_quotas.c.last_request_at,
                ).where(tenant_quotas.c.api_key_hash == old["key_hash"])
            ).mappings().first()
            db.execute(
                update(tenant_quotas)
                .where(tenant_quotas.c.api_key_hash == old["key_hash"])
                .values(is_active=False)
            )

            new_key_id = str(uuid.uuid4())
            raw_key, new_hash, new_prefix, nonce, secret_version = keys.derive_for_key_id(
                new_key_id
            )
            del raw_key
            application = db.execute(
                select(access_requests).where(access_requests.c.tenant_id == tenant_id).limit(1)
            ).mappings().first()
            monthly_limit = (
                int(old_quota["monthly_limit"]) if old_quota else config.initial_quota()
            )
            carried_usage = int(old_quota["current_month_usage"]) if old_quota else 0
            carried_month = old_quota["usage_month"] if old_quota else _usage_month(now)
            carried_last_request = old_quota["last_request_at"] if old_quota else None

            db.execute(
                api_keys.insert().values(
                    id=new_key_id,
                    tenant_id=tenant_id,
                    name=old["name"],
                    key_prefix=new_prefix,
                    key_hash=new_hash,
                    environment=old["environment"],
                    status="ACTIVE",
                    created_at=now,
                    last_used_at=None,
                    revoked_at=None,
                )
            )
            db.execute(
                tenant_quotas.insert().values(
                    api_key_hash=new_hash,
                    tenant_id=tenant_id,
                    monthly_limit=int(monthly_limit),
                    current_month_usage=carried_usage,
                    usage_month=carried_month,
                    is_active=True,
                    last_request_at=carried_last_request,
                )
            )
            reveal_token = keys.new_token()
            if application is not None:
                db.execute(
                    update(key_deliveries).where(
                        key_deliveries.c.application_id == application["id"]
                    ).values(voided_at=now, key_nonce=None)
                )
            db.execute(
                key_deliveries.insert().values(
                    key_id=new_key_id,
                    application_id=application["id"] if application else None,
                    tenant_id=tenant_id,
                    key_prefix=new_prefix,
                    key_hash=new_hash,
                    key_nonce=nonce,
                    reveal_token_hash=keys.token_digest(reveal_token),
                    secret_version=secret_version,
                    created_at=now,
                    expires_at=now + timedelta(seconds=config.reveal_ttl_seconds()),
                    revealed_at=None,
                    voided_at=None,
                )
            )

            origin = config.public_origin()
            if application is not None and origin:
                notifications.enqueue(
                    db,
                    application_id=application["id"],
                    audience=notifications.AUDIENCE_DEVELOPER,
                    kind="KEY_ROTATION",
                    recipient=application["email"],
                    subject="Your Platrixa API key was rotated",
                    body=(
                        "A new API key was issued for your tenant and the previous key "
                        "was revoked immediately.\n\n"
                        f"tenant={tenant_id}\n"
                        f"key_prefix={new_prefix}…\n"
                        f"Retrieve it once: {origin}/developer/key#token={reveal_token}\n\n"
                        "Store it in your server-side secret manager. The previous key "
                        "no longer authenticates."
                    ),
                    dedupe_key=f"dev-rotation:{new_key_id}",
                )
            _audit(db, actor, "API_KEY_ROTATED", application_id=application["id"] if application else None,
                   tenant_id=tenant_id, detail=f"old_prefix={old['key_prefix']} new_prefix={new_prefix}")
            return {
                "tenant_id": tenant_id,
                "revoked_key_id": old["id"],
                "key_id": new_key_id,
                "key_prefix": new_prefix,
                "status": "ACTIVE",
                "monthly_limit": monthly_limit,
                "current_month_usage": carried_usage,
                "usage_month": carried_month,
                "delivery": "one-time link emailed to the verified address"
                if application is not None
                else "no application record; the reveal link is not deliverable",
            }
    except (NotFound, Unavailable):
        raise
    except (IntegrityError, SQLAlchemyError) as exc:
        logger.warning("key rotation failed: %s", type(exc).__name__)
        raise Unavailable("ROTATION_FAILED", "rotation failed; the previous key is unchanged") from exc


# ---------------------------------------------------------------------------
# Owner dashboard operations
# ---------------------------------------------------------------------------


def owner_overview() -> dict:
    _require_store()
    with store.session() as db:
        status_rows = db.execute(
            select(access_requests.c.status, func.count())
            .group_by(access_requests.c.status)
        ).all()
        status_counts = {status: count for status, count in status_rows}
        active_keys = db.execute(
            select(func.count()).select_from(api_keys).where(api_keys.c.status == "ACTIVE")
        ).scalar_one()
        active_tenants = db.execute(
            select(func.count(func.distinct(api_keys.c.tenant_id))).where(
                api_keys.c.status == "ACTIVE"
            )
        ).scalar_one()
        usage = db.execute(
            select(
                func.coalesce(func.sum(tenant_quotas.c.current_month_usage), 0),
                func.coalesce(func.sum(tenant_quotas.c.monthly_limit), 0),
            ).where(tenant_quotas.c.is_active.is_(True))
        ).one()
        provisioning_failures = db.execute(
            select(func.count()).select_from(access_requests).where(
                access_requests.c.last_error.is_not(None),
                access_requests.c.status.in_((config.STATUS.VERIFIED, config.STATUS.PROVISIONING)),
            )
        ).scalar_one()
        recent = db.execute(
            select(access_requests.c.id, access_requests.c.email, access_requests.c.company,
                   access_requests.c.status, access_requests.c.tenant_id,
                   access_requests.c.created_at)
            .order_by(access_requests.c.created_at.desc())
            .limit(5)
        ).mappings().all()
    return {
        "applications_total": sum(status_counts.values()),
        "by_status": {s: status_counts.get(s, 0) for s in sorted(config.ALL_STATUSES)},
        "pending_verification": status_counts.get(config.STATUS.PENDING_VERIFICATION, 0),
        "review_required": status_counts.get(config.STATUS.REVIEW_REQUIRED, 0),
        "suspended": status_counts.get(config.STATUS.SUSPENDED, 0),
        "active_developers": int(active_tenants),
        "active_api_keys": int(active_keys),
        "provisioning_failures": int(provisioning_failures),
        "usage": {
            "current_month_usage": int(usage[0] or 0),
            "monthly_limit_total": int(usage[1] or 0),
        },
        "notifications": notifications.counts(),
        "email_provider": email_transport.provider_status(),
        "key_delivery_configured": config.delivery_configured(),
        "recent": [
            {
                "application_id": r["id"],
                "email": r["email"],
                "company": r["company"],
                "status": r["status"],
                "tenant_id": r["tenant_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in recent
        ],
    }


def owner_list_applications(
    *, status: Optional[str] = None, tenant_id: Optional[str] = None,
    limit: int = 20, offset: int = 0,
) -> dict:
    _require_store()
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with store.session() as db:
        conditions = []
        if status:
            if status not in config.ALL_STATUSES:
                raise OnboardingError("STATUS_INVALID", "unknown application status", 400)
            conditions.append(access_requests.c.status == status)
        if tenant_id:
            conditions.append(access_requests.c.tenant_id == tenant_id)
        base = select(access_requests)
        if conditions:
            base = base.where(*conditions)
        total = db.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = db.execute(
            base.order_by(access_requests.c.created_at.desc(), access_requests.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings().all()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "applications": [
            {
                **_view(r),
                "profile_url": r["profile_url"],
                "last_error": r["last_error"],
                "provisioning_attempts": r["provisioning_attempts"],
            }
            for r in rows
        ],
    }


def owner_application_detail(application_id: str) -> dict:
    _require_store()
    with store.session() as db:
        app = db.execute(
            select(access_requests).where(access_requests.c.id == application_id)
        ).mappings().first()
        if app is None:
            raise NotFound("APPLICATION_NOT_FOUND", "application not found")
        verification_rows = db.execute(
            select(verifications.c.id, verifications.c.created_at,
                   verifications.c.expires_at, verifications.c.consumed_at)
            .where(verifications.c.application_id == application_id)
            .order_by(verifications.c.created_at.desc())
        ).mappings().all()
        delivery_rows = db.execute(
            select(key_deliveries.c.key_id, key_deliveries.c.key_prefix,
                   key_deliveries.c.created_at, key_deliveries.c.expires_at,
                   key_deliveries.c.revealed_at, key_deliveries.c.voided_at,
                   key_deliveries.c.key_nonce)
            .where(key_deliveries.c.application_id == application_id)
            .order_by(key_deliveries.c.created_at.desc())
        ).mappings().all()
        notification_rows = db.execute(
            select(notifications.notifications_table.c.id,
                   notifications.notifications_table.c.audience,
                   notifications.notifications_table.c.kind,
                   notifications.notifications_table.c.status,
                   notifications.notifications_table.c.created_at,
                   notifications.notifications_table.c.sent_at,
                   notifications.notifications_table.c.last_error)
            .where(notifications.notifications_table.c.application_id == application_id)
            .order_by(notifications.notifications_table.c.created_at.desc())
            .limit(50)
        ).mappings().all()
        audit_rows = db.execute(
            select(audit.c.action, audit.c.actor, audit.c.detail, audit.c.created_at)
            .where(audit.c.application_id == application_id)
            .order_by(audit.c.created_at.desc())
            .limit(50)
        ).mappings().all()
        quota = None
        if app["tenant_id"]:
            quota = db.execute(
                select(tenant_quotas.c.api_key_hash, tenant_quotas.c.monthly_limit,
                       tenant_quotas.c.current_month_usage, tenant_quotas.c.usage_month,
                       tenant_quotas.c.is_active)
                .where(tenant_quotas.c.tenant_id == app["tenant_id"])
            ).mappings().all()
    detail = {
        **_view(app),
        "profile_url": app["profile_url"],
        "building": app["building"],
        "last_error": app["last_error"],
        "provisioning_attempts": app["provisioning_attempts"],
        "verifications": [
            {
                "verification_id": v["id"],
                "created_at": v["created_at"].isoformat() if v["created_at"] else None,
                "expires_at": v["expires_at"].isoformat() if v["expires_at"] else None,
                "consumed_at": v["consumed_at"].isoformat() if v["consumed_at"] else None,
            }
            for v in verification_rows
        ],
        # Never the nonce, never the hash, never a raw key.
        "key_delivery": [
            {
                "key_id": d["key_id"],
                "key_prefix": d["key_prefix"],
                "created_at": d["created_at"].isoformat() if d["created_at"] else None,
                "expires_at": d["expires_at"].isoformat() if d["expires_at"] else None,
                "revealed": d["revealed_at"] is not None,
                "voided": d["voided_at"] is not None,
                "derivable": d["key_nonce"] is not None,
            }
            for d in delivery_rows
        ],
        "notifications": [
            {
                "notification_id": n["id"],
                "audience": n["audience"],
                "kind": n["kind"],
                "status": n["status"],
                "created_at": n["created_at"].isoformat() if n["created_at"] else None,
                "sent_at": n["sent_at"].isoformat() if n["sent_at"] else None,
                "last_error": n["last_error"],
            }
            for n in notification_rows
        ],
        "audit": [
            {
                "action": a["action"],
                "actor": a["actor"],
                "detail": a["detail"],
                "created_at": a["created_at"].isoformat() if a["created_at"] else None,
            }
            for a in audit_rows
        ],
        "quota": [
            {
                "monthly_limit": q["monthly_limit"],
                "current_month_usage": q["current_month_usage"],
                "usage_month": q["usage_month"],
                "is_active": bool(q["is_active"]),
            }
            for q in (quota or [])
        ],
    }
    return detail


def owner_set_status(application_id: str, action: str, *, actor: str, reason: Optional[str] = None) -> dict:
    """Owner decisions: suspend, restore, reject. Tenant-scoped, audited."""
    _require_store()
    now = store.utcnow()
    with store.session() as db:
        app = store.lock_row(db, access_requests, application_id)
        if app is None:
            raise NotFound("APPLICATION_NOT_FOUND", "application not found")

        if action == "suspend":
            if not app["tenant_id"]:
                raise Conflict("APPLICATION_NOT_ACTIVE", "application has no tenant to suspend")
            db.execute(
                update(access_requests).where(access_requests.c.id == application_id)
                .values(status=config.STATUS.SUSPENDED, status_reason=(reason or "OWNER_SUSPENDED")[:64],
                        updated_at=now)
            )
            db.execute(
                update(tenant_quotas).where(tenant_quotas.c.tenant_id == app["tenant_id"])
                .values(is_active=False)
            )
            _audit(db, actor, "TENANT_SUSPENDED", application_id=application_id,
                   tenant_id=app["tenant_id"], detail=reason)
        elif action == "restore":
            if app["status"] != config.STATUS.SUSPENDED:
                raise Conflict("APPLICATION_NOT_SUSPENDED", "application is not suspended")
            db.execute(
                update(access_requests).where(access_requests.c.id == application_id)
                .values(status=config.STATUS.ACTIVE, status_reason=None, updated_at=now)
            )
            db.execute(
                update(tenant_quotas).where(tenant_quotas.c.tenant_id == app["tenant_id"])
                .values(is_active=True)
            )
            _audit(db, actor, "TENANT_RESTORED", application_id=application_id,
                   tenant_id=app["tenant_id"])
        elif action == "reject":
            if app["status"] == config.STATUS.ACTIVE:
                raise Conflict(
                    "APPLICATION_ACTIVE",
                    "an active developer must be suspended (not rejected) to stop access",
                )
            db.execute(
                update(access_requests).where(access_requests.c.id == application_id)
                .values(status=config.STATUS.REJECTED, status_reason=(reason or "OWNER_REJECTED")[:64],
                        updated_at=now)
            )
            db.execute(
                update(verifications).where(
                    verifications.c.application_id == application_id,
                    verifications.c.consumed_at.is_(None),
                ).values(consumed_at=now)
            )
            _audit(db, actor, "APPLICATION_REJECTED", application_id=application_id, detail=reason)
        else:
            raise OnboardingError("OWNER_ACTION_INVALID", "unknown owner action", 400)

        refreshed = db.execute(
            select(access_requests).where(access_requests.c.id == application_id)
        ).mappings().first()
        return _view(refreshed)


def owner_set_quota(tenant_id: str, monthly_limit: int, *, actor: str) -> dict:
    """Per-tenant quota change. Applies to every active key of that tenant."""
    _require_store()
    if isinstance(monthly_limit, bool) or not isinstance(monthly_limit, int):
        raise OnboardingError("QUOTA_INVALID", "monthly_limit must be an integer", 400)
    if monthly_limit < 0 or monthly_limit > 1_000_000:
        raise OnboardingError("QUOTA_INVALID", "monthly_limit must be between 0 and 1000000", 400)
    now = store.utcnow()
    with store.session() as db:
        updated = db.execute(
            update(tenant_quotas)
            .where(tenant_quotas.c.tenant_id == tenant_id)
            .values(monthly_limit=monthly_limit)
        )
        if (updated.rowcount or 0) == 0:
            raise NotFound("TENANT_NOT_FOUND", "no quota rows for this tenant")
        db.execute(
            update(access_requests)
            .where(access_requests.c.tenant_id == tenant_id)
            .values(monthly_limit=monthly_limit, updated_at=now)
        )
        _audit(db, actor, "QUOTA_UPDATED", tenant_id=tenant_id, detail=f"monthly_limit={monthly_limit}")
    return {"tenant_id": tenant_id, "monthly_limit": monthly_limit}


def owner_revoke_key(tenant_id: str, key_id: str, *, actor: str) -> dict:
    _require_store()
    now = store.utcnow()
    with store.session() as db:
        row = db.execute(
            select(api_keys).where(api_keys.c.id == key_id, api_keys.c.tenant_id == tenant_id)
        ).mappings().first()
        if row is None:
            raise NotFound("API_KEY_NOT_FOUND", "no such key for this tenant")
        db.execute(
            update(api_keys).where(api_keys.c.id == key_id)
            .values(status="REVOKED", revoked_at=now)
        )
        db.execute(
            update(tenant_quotas).where(tenant_quotas.c.api_key_hash == row["key_hash"])
            .values(is_active=False)
        )
        _audit(db, actor, "API_KEY_REVOKED", tenant_id=tenant_id, detail=f"key_prefix={row['key_prefix']}")
    return {"tenant_id": tenant_id, "key_id": key_id, "status": "REVOKED"}


def owner_notifications_list(*, limit: int = 50, offset: int = 0) -> dict:
    _require_store()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    table = notifications.notifications_table
    with store.session() as db:
        rows = db.execute(
            select(table.c.id, table.c.application_id, table.c.audience, table.c.kind,
                   table.c.status, table.c.created_at, table.c.sent_at,
                   table.c.attempts, table.c.last_error)
            .order_by(table.c.created_at.desc(), table.c.id.desc())
            .limit(limit).offset(offset)
        ).mappings().all()
    return {
        "limit": limit,
        "offset": offset,
        "notifications": [
            {
                "notification_id": r["id"],
                "application_id": r["application_id"],
                "audience": r["audience"],
                "kind": r["kind"],
                "status": r["status"],
                "attempts": r["attempts"],
                "last_error": r["last_error"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
            }
            for r in rows
        ],
    }


def expire_abandoned(*, actor: str = "system", limit: int = 200) -> dict:
    """Expire applications past their window. Explicit operator action."""
    _require_store()
    now = store.utcnow()
    with store.session() as db:
        rows = db.execute(
            select(access_requests.c.id)
            .where(
                access_requests.c.expires_at <= now,
                access_requests.c.status.in_(
                    (config.STATUS.PENDING_VERIFICATION, config.STATUS.VERIFIED)
                ),
            )
            .limit(limit)
        ).scalars().all()
        for application_id in rows:
            db.execute(
                update(access_requests).where(access_requests.c.id == application_id)
                .values(status=config.STATUS.EXPIRED, updated_at=now)
            )
            _audit(db, actor, "APPLICATION_EXPIRED", application_id=application_id)
    return {"expired": len(rows)}
