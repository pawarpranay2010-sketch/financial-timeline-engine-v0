"""Key material for onboarding: tokens, hashing, and one-time delivery.

Two different problems, solved differently on purpose:

VERIFICATION / REVEAL TOKENS
    A 32-byte CSPRNG token is handed to the developer (email link). Only its
    SHA-256 digest is stored, exactly like API keys; lookup is by digest, so
    the database never holds a usable token.

THE API KEY ITSELF
    Requirement: the raw key must not be stored, yet it must be deliverable
    once, later, to the verified owner of the email — and must stop being
    recoverable after that window.

    Storing the raw key (or a reversible ciphertext) would violate the first
    requirement. Instead the key is DERIVED:

        raw = "plx_live_" + base64url(HMAC-SHA256(secret, key_id|nonce))

    Only ``SHA-256(raw)`` goes into the credential tables, and the delivery
    row holds the public ``key_id`` + random ``nonce``. On reveal the server
    recomputes the key with the deployment secret and the stored nonce, checks
    the digest, returns it once, and ZEROES the nonce. From that moment the
    key cannot be re-derived by anyone — not even the server — while the
    digest in the credential tables still authenticates it.

    The derived key is a PRF output from a 32+ byte secret, so it is as
    unpredictable as a random token; the secret never touches the database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Optional, Tuple

from backend.auth.tokens import hash_token
from backend.onboarding import config

KEY_PREFIX = "plx_live_"
_NONCE_BYTES = 24
_DERIVATION_DOMAIN = "platrixa-developer-key-v1"

# A generated key is "plx_live_" + 43 base64url chars (32 bytes of HMAC).
_KEY_RE = re.compile(r"^plx_live_[A-Za-z0-9_-]{43}$")


def new_token() -> str:
    """URL-safe CSPRNG token for verification/reveal links."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hash_token(token)


def new_nonce() -> str:
    """Public random nonce stored beside the key digest (never the key)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(_NONCE_BYTES)).rstrip(b"=").decode()


def derive_api_key(
    key_id: str,
    nonce: str,
    secret: Optional[str] = None,
    *,
    version: Optional[int] = None,
) -> str:
    """Derive the one-time-deliverable raw key. Raises when unconfigured.

    ``version`` selects the VERSIONED secret, so rotating
    PLATRIXA_KEY_DELIVERY_SECRET does not invalidate keys whose delivery row
    was written under an earlier version.
    """
    if secret is None:
        material = (
            config.delivery_secret_for(version)
            if version is not None
            else config.delivery_secret()
        )
    else:
        material = secret
    if not material:
        raise RuntimeError("key delivery secret is not configured")
    message = f"{_DERIVATION_DOMAIN}|{key_id}|{nonce}".encode("utf-8")
    digest = hmac.new(material.encode("utf-8"), message, hashlib.sha256).digest()
    return KEY_PREFIX + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def key_prefix_for(raw_key: str) -> str:
    """Masked identification prefix (fits VARCHAR(20), never the secret)."""
    return raw_key[:16]


def is_well_formed_api_key(value: str) -> bool:
    return bool(_KEY_RE.match(value or ""))


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode("utf-8")).digest(),
        hashlib.sha256(b.encode("utf-8")).digest(),
    )


def derive_for_key_id(key_id: str) -> Tuple[str, str, str, str, int]:
    """(raw, digest, prefix, nonce, secret_version) for a NEW delivery."""
    version = config.delivery_secret_version()
    nonce = new_nonce()
    raw = derive_api_key(key_id, nonce, version=version)
    return raw, hash_token(raw), key_prefix_for(raw), nonce, version
