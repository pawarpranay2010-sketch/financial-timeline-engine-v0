"""Signed edge -> backend client-IP claim.

WHY THIS EXISTS
---------------
The public website reaches the API through the Cloudflare Pages proxy, which
builds a FRESH header set and forwards only an allowlist. Railway then
overwrites ``x-real-ip`` / ``x-forwarded-for`` with the immediate peer (the
Cloudflare edge). The backend's socket peer is therefore one shared address for
every visitor, and ``api.rate_limit._client_key`` collapses all of them into a
single per-IP bucket — including the onboarding caps, where the fourth
applicant in an hour is refused.

The ONE place that knows the real client address is the edge function
(``cf-connecting-ip`` is set by Cloudflare and cannot be set by the browser).
So the edge *asserts* it, and the backend trusts the assertion only when it is
cryptographically bound to the request it arrived with:

    canonical = "V1|platrixa-client-ip|<client_ip>|<visitor_assertion_signature>"
    x-platrixa-client-ip-signature = HMAC-SHA256(PLATRIXA_VISITOR_HMAC_SECRET, canonical)

Binding to the visitor assertion's signature (the last field of
``x-platrixa-visitor-assertion``) ties the claim to one exact request: the
assertion is verified and its nonce consumed by the website-surface middleware,
so a claim captured from another request cannot be replayed into a new one, and
a claim without a matching assertion verifies nothing.

SECURITY RULES
--------------
* No secret configured -> no claim is trusted (the caller falls back to the
  transport peer, which no header can forge).
* The surface marker must be exactly ``website``. A direct caller to the Railway
  backend can send any headers it likes, but it cannot produce the HMAC.
* The HMAC is computed over the EXACT string the edge sent; the value is
  canonicalised into an address only AFTER the signature matches, so the signed
  bytes and the bucketed identity cannot diverge.
* ``X-Forwarded-For`` and ``X-Real-IP`` are never consulted here: on this
  topology they are infrastructure-written and carry the edge, not the client.

The matching edge implementation lives in
``frontend/functions/v1/[[path]].js`` and ``frontend/functions/api/[[path]].js``.
The canonical string and the golden vector in
``tests/fixtures/client_ip_claim_vector.json`` are asserted by both
``tests/test_client_ip_claim.py`` and ``scripts/client_ip_claim_test.mjs`` so
the two implementations cannot drift silently.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

CLAIM_CONTEXT = "V1|platrixa-client-ip"

CLAIM_HEADER = "x-platrixa-client-ip"
CLAIM_SIGNATURE_HEADER = "x-platrixa-client-ip-signature"
SURFACE_HEADER = "x-platrixa-surface"
ASSERTION_HEADER = "x-platrixa-visitor-assertion"

WEBSITE_SURFACE = "website"

VISITOR_SECRET_ENV_VAR = "PLATRIXA_VISITOR_HMAC_SECRET"


def visitor_secret() -> str:
    return (os.getenv(VISITOR_SECRET_ENV_VAR, "") or "").strip()


def canonical_message(client_ip: str, assertion_signature: str) -> str:
    """The exact bytes both sides sign. Field order is the contract."""
    return "|".join((CLAIM_CONTEXT, client_ip, assertion_signature))


def sign(secret: str, client_ip: str, assertion_signature: str) -> str:
    """HMAC-SHA256 hex of :func:`canonical_message` (edge-side equivalent)."""
    return hmac.new(
        secret.encode("utf-8"),
        canonical_message(client_ip, assertion_signature).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def assertion_signature(assertion: Optional[str]) -> Optional[str]:
    """The signature field of a ``V1|platrixa|...|<hmac>`` assertion."""
    if not isinstance(assertion, str):
        return None
    parts = assertion.split("|")
    if len(parts) != 10 or parts[0] != "V1" or parts[1] != "platrixa":
        return None
    signature = parts[9].strip()
    if len(signature) != 64:
        return None
    return signature


def is_website_surface(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == WEBSITE_SURFACE


def verified_client_ip(
    *,
    surface: Optional[str],
    assertion: Optional[str],
    client_ip: Optional[str],
    signature: Optional[str],
    secret: Optional[str] = None,
) -> Optional[str]:
    """The asserted client address, or ``None`` when it is not trustworthy.

    Never raises: any missing, malformed or unsigned input is simply "no
    claim", which leaves the caller on its non-forgeable fallback.
    """
    if not is_website_surface(surface):
        return None
    raw_ip = (client_ip or "").strip()
    if not raw_ip or len(raw_ip) > 64:
        return None
    provided = (signature or "").strip().lower()
    if len(provided) != 64:
        return None
    bound_to = assertion_signature(assertion)
    if not bound_to:
        return None
    key = (secret if secret is not None else visitor_secret()).strip()
    if not key:
        return None
    expected = sign(key, raw_ip, bound_to)
    if not hmac.compare_digest(expected, provided):
        return None
    return raw_ip


__all__ = [
    "CLAIM_CONTEXT",
    "CLAIM_HEADER",
    "CLAIM_SIGNATURE_HEADER",
    "SURFACE_HEADER",
    "ASSERTION_HEADER",
    "VISITOR_SECRET_ENV_VAR",
    "assertion_signature",
    "canonical_message",
    "is_website_surface",
    "sign",
    "verified_client_ip",
    "visitor_secret",
]
