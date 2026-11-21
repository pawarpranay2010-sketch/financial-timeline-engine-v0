"""Email transport for onboarding notifications.

The application had no email provider, so this module is the provider-neutral
integration boundary plus the first real implementation:

  * ``SmtpTransport``  — ``smtplib`` over STARTTLS (default) or implicit TLS,
    configured entirely by environment variables. No new dependency.
  * ``NullTransport``  — the honest "not configured" state. It never pretends a
    message was delivered; the durable queue keeps the notification PENDING
    with the explicit error ``EMAIL_NOT_CONFIGURED`` so it flushes the moment
    a provider exists.

Adding SendGrid/Resend/SES later means one class implementing ``send()`` and
``probe()`` and returning it from :func:`transport`; the queue, retry and
dedupe logic does not change.

Guarantees:

  * Credentials are read from the environment only, never logged, never
    returned by :func:`provider_status` or :meth:`SmtpTransport.probe`.
  * ``send`` never raises: every failure becomes a :class:`DeliveryOutcome`
    with ``retryable`` set correctly (auth/refusal are terminal, network and
    server errors are retryable).
  * Message bodies (which can carry verification/reveal links) are never
    written to logs. A successful SMTP hand-off is the ONLY evidence of
    delivery that this module reports.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger("platrixa.onboarding.email")

PROVIDER_ENV_VAR = "PLATRIXA_EMAIL_PROVIDER"
SMTP_HOST_ENV_VAR = "PLATRIXA_SMTP_HOST"
SMTP_PORT_ENV_VAR = "PLATRIXA_SMTP_PORT"
SMTP_USERNAME_ENV_VAR = "PLATRIXA_SMTP_USERNAME"
SMTP_PASSWORD_ENV_VAR = "PLATRIXA_SMTP_PASSWORD"
SMTP_STARTTLS_ENV_VAR = "PLATRIXA_SMTP_STARTTLS"
SMTP_SSL_ENV_VAR = "PLATRIXA_SMTP_SSL"
# DEV/TEST ONLY: allow SMTP AUTH over a plaintext socket (e.g. a local relay).
ALLOW_PLAINTEXT_AUTH_ENV_VAR = "PLATRIXA_SMTP_ALLOW_PLAINTEXT_AUTH"
EMAIL_FROM_ENV_VAR = "PLATRIXA_EMAIL_FROM"
EMAIL_FROM_NAME_ENV_VAR = "PLATRIXA_EMAIL_FROM_NAME"
EMAIL_REPLY_TO_ENV_VAR = "PLATRIXA_EMAIL_REPLY_TO"

DEFAULT_SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class DeliveryOutcome:
    """Result of ONE delivery attempt. Never raises out of ``send``."""

    ok: bool
    error: Optional[str] = None
    retryable: bool = True


def _env(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    reply_to: Optional[str] = None
    # Stable identity of the QUEUED notification (its row id). Used to derive a
    # deterministic Message-ID so a duplicate produced by the unavoidable
    # "provider accepted, sender died before acknowledging" window carries the
    # same id and can be recognised by the receiver.
    message_id: Optional[str] = None


class NullTransport:
    """No provider configured — delivery is impossible, and says so."""

    name = "none"
    configured = False

    def send(self, message: Message) -> DeliveryOutcome:
        return DeliveryOutcome(ok=False, error="EMAIL_NOT_CONFIGURED", retryable=True)

    def probe(self) -> dict:
        return {
            "ok": False,
            "reason": "EMAIL_NOT_CONFIGURED",
            "detail": "Set PLATRIXA_EMAIL_PROVIDER=smtp and the SMTP_* variables.",
        }


class InsecureSmtpAuth(RuntimeError):
    """Credentials are configured but neither TLS mode is enabled.

    Audit S-10: sending AUTH over a plaintext socket would expose the SMTP
    password. Refusing is safer than silently downgrading.
    """


class SmtpTransport:
    """SMTP delivery with STARTTLS (default) or implicit TLS."""

    name = "smtp"
    configured = True

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str,
        password: str,
        starttls: bool,
        implicit_tls: bool,
        sender_name: str = "",
        reply_to: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._implicit_tls = implicit_tls
        self._sender_name = sender_name
        self._reply_to = reply_to

    # -- connection -----------------------------------------------------

    def _connect(self) -> smtplib.SMTP:
        if self._implicit_tls:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=SMTP_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(
                self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS
            )
        client.ehlo()
        if self._starttls and not self._implicit_tls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if self._username:
            if self._insecure_auth():
                # Never put the password on an unencrypted connection.
                client.close()
                raise InsecureSmtpAuth()
            client.login(self._username, self._password)
        return client

    # -- delivery -------------------------------------------------------

    def _insecure_auth(self) -> bool:
        """Credentials configured with no TLS at all (audit S-10).

        Deliberately allowed only with the explicit development opt-in: a
        local test relay is one thing, a production password on a plaintext
        socket is another.
        """
        if not self._username:
            return False
        if self._starttls or self._implicit_tls:
            return False
        return not _env_bool(ALLOW_PLAINTEXT_AUTH_ENV_VAR, False)

    def send(self, message: Message) -> DeliveryOutcome:
        if self._insecure_auth():
            logger.error("onboarding email refused: credentials configured without TLS")
            return DeliveryOutcome(ok=False, error="SMTP_INSECURE_AUTH", retryable=False)
        email = EmailMessage()
        from_header = (
            formataddr((self._sender_name, self._sender))
            if self._sender_name
            else self._sender
        )
        email["From"] = from_header
        email["To"] = message.to
        email["Subject"] = message.subject
        reply_to = message.reply_to or self._reply_to
        if reply_to:
            email["Reply-To"] = reply_to
        email.set_content(message.body)
        if message.message_id and "@" in self._sender:
            domain = self._sender.rsplit("@", 1)[-1]
            email["Message-ID"] = f"<platrixa-{message.message_id}@{domain}>"
        try:
            with self._connect() as client:
                client.send_message(email)
            # smtplib returning without an exception means the server accepted
            # responsibility for the message. That is the only delivery claim
            # made here; no "delivered to inbox" is ever asserted.
            return DeliveryOutcome(ok=True)
        except InsecureSmtpAuth:
            # Misconfiguration, not a transient fault: retrying cannot help.
            logger.error("onboarding email refused: credentials configured without TLS")
            return DeliveryOutcome(ok=False, error="SMTP_INSECURE_AUTH", retryable=False)
        except smtplib.SMTPAuthenticationError:
            # Credentials are wrong: retrying the same delivery cannot help,
            # and the queue must not hammer the provider.
            logger.warning("onboarding email auth failure (not retryable)")
            return DeliveryOutcome(ok=False, error="SMTP_AUTH_FAILED", retryable=False)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPNotSupportedError):
            logger.warning("onboarding email refused (not retryable)")
            return DeliveryOutcome(ok=False, error="SMTP_REFUSED", retryable=False)
        except smtplib.SMTPSenderRefused:
            logger.warning("onboarding email sender refused (not retryable)")
            return DeliveryOutcome(ok=False, error="SMTP_SENDER_REFUSED", retryable=False)
        except ssl.SSLError:
            logger.warning("onboarding email TLS failure (not retryable)")
            return DeliveryOutcome(ok=False, error="SMTP_TLS_FAILED", retryable=False)
        except (OSError, smtplib.SMTPException):
            # Connection/timeout/transient server errors: retryable.
            logger.warning("onboarding email transport failure (retryable)")
            return DeliveryOutcome(ok=False, error="SMTP_UNAVAILABLE", retryable=True)

    # -- diagnostics ----------------------------------------------------

    def probe(self) -> dict:
        """Open a real connection and authenticate — send nothing.

        Returns a redacted report: host/port/TLS stage and a bounded failure
        code. Credentials and the raw exception text are never included.
        """
        if self._insecure_auth():
            return {"ok": False, "stage": "tls", "reason": "SMTP_INSECURE_AUTH"}
        stage = "connect"
        try:
            with self._connect() as client:
                stage = "close"
                client.noop()
            return {
                "ok": True,
                "stage": "authenticated" if self._username else "connected",
                "host": self._host,
                "port": self._port,
                "tls": "implicit" if self._implicit_tls else ("starttls" if self._starttls else "none"),
            }
        except InsecureSmtpAuth:
            return {"ok": False, "stage": "tls", "reason": "SMTP_INSECURE_AUTH"}
        except smtplib.SMTPAuthenticationError:
            return {"ok": False, "stage": "auth", "reason": "SMTP_AUTH_FAILED"}
        except ssl.SSLError:
            return {"ok": False, "stage": "tls", "reason": "SMTP_TLS_FAILED"}
        except (OSError, smtplib.SMTPException):
            return {"ok": False, "stage": stage, "reason": "SMTP_UNAVAILABLE"}


def transport() -> NullTransport | SmtpTransport:
    """Resolve the configured transport. Unconfigured is a first-class state."""
    provider = _env(PROVIDER_ENV_VAR).lower() or "none"
    if provider != "smtp":
        return NullTransport()
    host = _env(SMTP_HOST_ENV_VAR)
    sender = _env(EMAIL_FROM_ENV_VAR)
    if not host or not sender:
        return NullTransport()
    try:
        port = int(_env(SMTP_PORT_ENV_VAR) or DEFAULT_SMTP_PORT)
    except ValueError:
        port = DEFAULT_SMTP_PORT
    return SmtpTransport(
        host=host,
        port=port,
        sender=sender,
        username=_env(SMTP_USERNAME_ENV_VAR),
        password=_env(SMTP_PASSWORD_ENV_VAR),
        starttls=_env_bool(SMTP_STARTTLS_ENV_VAR, True),
        implicit_tls=_env_bool(SMTP_SSL_ENV_VAR, False),
        sender_name=_env(EMAIL_FROM_NAME_ENV_VAR),
        reply_to=_env(EMAIL_REPLY_TO_ENV_VAR),
    )


def provider_status() -> dict:
    """Non-secret provider status for readiness/diagnostics.

    Reports which REQUIRED variables are missing (names only — values are never
    read into the response) so an operator can fix configuration without
    guessing.
    """
    active = transport()
    provider = _env(PROVIDER_ENV_VAR).lower() or "none"
    missing: list[str] = []
    if provider != "smtp":
        missing.append(PROVIDER_ENV_VAR)
    else:
        if not _env(SMTP_HOST_ENV_VAR):
            missing.append(SMTP_HOST_ENV_VAR)
        if not _env(EMAIL_FROM_ENV_VAR):
            missing.append(EMAIL_FROM_ENV_VAR)
    from_address = _env(EMAIL_FROM_ENV_VAR)
    return {
        "provider": active.name,
        "configured": bool(active.configured),
        "from_configured": bool(from_address),
        "from_domain": from_address.rsplit("@", 1)[-1] if "@" in from_address else "",
        "from_name_configured": bool(_env(EMAIL_FROM_NAME_ENV_VAR)),
        "reply_to_configured": bool(_env(EMAIL_REPLY_TO_ENV_VAR)),
        "host_configured": bool(_env(SMTP_HOST_ENV_VAR)),
        "credentials_configured": bool(_env(SMTP_USERNAME_ENV_VAR) and _env(SMTP_PASSWORD_ENV_VAR)),
        "starttls": _env_bool(SMTP_STARTTLS_ENV_VAR, True),
        "implicit_tls": _env_bool(SMTP_SSL_ENV_VAR, False),
        "missing": missing,
        "next_step": (
            "Provider configured. Use the diagnostics CLI with --probe to verify "
            "connectivity without sending a message."
            if active.configured
            else "Set the missing variables, then run: python -m backend.auth.email_diagnostics --probe"
        ),
    }
