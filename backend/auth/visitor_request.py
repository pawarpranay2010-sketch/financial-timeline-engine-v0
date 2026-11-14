"""Visitor request authentication for the website surface (Phase 1B V2).

A request is a WEBSITE request only when the trusted edge proxy marks it with
``x-platrixa-surface: website``. The proxy strips any browser-supplied value
and signs a fresh assertion for every request.

The website surface is FAIL-CLOSED: without a valid, correctly signed
assertion there is no visitor identity, and no scope is ever bound. Developer
and worker requests carry no surface marker, so they keep the pre-existing
tenant-scoped authorization untouched.

This module is deliberately free of FastAPI/Starlette imports so the decision
logic can be unit-tested without a running server or database.
"""

from __future__ import annotations

from typing import Optional, Tuple

from backend.auth.api_keys import validate_visitor_assertion

WEBSITE_SURFACE = "website"

HEADER_SURFACE = "x-platrixa-surface"
HEADER_ASSERTION = "x-platrixa-visitor-assertion"
HEADER_VISITOR_ID = "x-platrixa-visitor-id"
HEADER_TIMESTAMP = "x-platrixa-request-timestamp"
HEADER_NONCE = "x-platrixa-visitor-nonce"

SAFE_METHODS = ("GET", "HEAD")


def is_website_surface(value: Optional[str]) -> bool:
    """True only for the exact marker the edge proxy sets."""
    return (value or "").strip().lower() == WEBSITE_SURFACE


def verify_visitor_request(
    *,
    assertion: Optional[str],
    visitor_id: Optional[str],
    timestamp: Optional[str],
    nonce: Optional[str],
    method: str,
    path: str,
    query_string: str,
    body_hash: str,
) -> Tuple[str, Optional[str]]:
    """Authenticate a website visitor request.

    Returns ``(reason, visitor_id)``. ``reason`` is ``VALID`` only when the
    HMAC covers the exact method/path/query/body, the timestamp is fresh, the
    nonce is unused, and the visitor id is well formed. In every other case
    the returned visitor id is ``None`` — there is no fallback.
    """
    if not (assertion or "").strip():
        return "MISSING_ASSERTION", None
    if not (visitor_id or "").strip():
        return "MISSING_VISITOR_ID", None
    if not (timestamp or "").strip():
        return "MISSING_TIMESTAMP", None
    if not (nonce or "").strip():
        return "MISSING_NONCE", None

    reason, verified, _tenant = validate_visitor_assertion(
        provided_assertion=assertion,
        provided_visitor_id=visitor_id,
        provided_timestamp=timestamp,
        provided_nonce=nonce,
        method=method,
        path=path,
        query_string=query_string,
        body_hash=body_hash,
    )
    if reason != "VALID" or not verified:
        return reason or "INVALID_ASSERTION", None
    return "VALID", verified


def csrf_origin_allowed(
    *,
    method: str,
    origin: Optional[str],
    public_origin: Optional[str],
) -> bool:
    """Exact-Origin CSRF gate for browser mutations.

    Reads are always allowed (they are strictly read-only). A mutation needs
    a configured public origin and an exact match; a missing configuration
    fails closed.
    """
    if (method or "").upper() in SAFE_METHODS:
        return True
    configured = (public_origin or "").strip()
    if not configured:
        return False
    return (origin or "").strip() == configured
