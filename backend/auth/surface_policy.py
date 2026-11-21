"""Server-side authority boundary for the PUBLIC WEBSITE surface.

WHY THIS EXISTS
---------------
The website reaches the API through a same-origin proxy that authenticates the
browser as ONE shared website tenant (it injects the server-side
``PLATRIXA_API_KEY``). Every browser request therefore arrives with
``x-platrixa-surface: website`` and a verified visitor assertion, but with the
shared tenant's data-plane key.

That is fine for the public processing contract and for the *public*
onboarding endpoints, which authenticate on their own claims (a visitor
identity, or a single-use token). It is NOT fine for tenant-scoped management
operations: a same-origin script could otherwise rotate or enumerate the
shared tenant's credentials.

This module makes the boundary explicit and DEFAULT-DENY on the backend, so
the protection does not depend on the edge proxy's configuration being right:

    website surface  -> only WEBSITE_ROUTE_SPECS (method + exact path)
    developer surface (no surface marker) -> unchanged, authenticated by the
                        caller's own data-plane key
    owner surface    -> unchanged, authenticated by the signed management
                        assertion (never by a data-plane key)

The allowlist is a mirror of ``frontend/web/lib/website-route-policy.js`` and
``frontend/functions/v1/[[path]].js``; ``tests/test_onboarding_remediation.py``
asserts all three tables agree, so they cannot drift silently.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

# (methods, exact-path regex) — MUST match the JS tables element for element.
WEBSITE_ROUTE_SPECS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("GET", "HEAD"), r"^/v1/health$"),
    (("GET", "HEAD"), r"^/v1/ready$"),
    (("GET", "HEAD"), r"^/v1/capabilities$"),
    (("GET", "HEAD"), r"^/v1/usage$"),
    (("POST",), r"^/v1/process$"),
    (("POST",), r"^/v1/process/document$"),
    (("POST",), r"^/v1/documents$"),
    (("POST",), r"^/v1/inference-jobs$"),
    (("GET", "HEAD"), r"^/v1/jobs/[A-Za-z0-9._:-]{1,128}$"),
    (("GET", "HEAD"), r"^/v1/results/[A-Za-z0-9._:-]{1,128}$"),
    (("POST",), r"^/v1/webhook-endpoints$"),
    (("GET", "HEAD"), r"^/v1/webhook-endpoints$"),
    # Public developer onboarding (no tenant authority).
    (("POST",), r"^/v1/developer/access-requests$"),
    (("POST",), r"^/v1/developer/verify-email$"),
    (("POST",), r"^/v1/developer/resend-verification$"),
    (("GET", "HEAD"), r"^/v1/developer/access-status$"),
    (("POST",), r"^/v1/developer/keys/reveal$"),
)

# Compiled once. Patterns are anchored and matched against the canonical path.
_WEBSITE_ROUTES: Tuple[Tuple[frozenset, "re.Pattern[str]"], ...] = tuple(
    (frozenset(methods), re.compile(pattern)) for methods, pattern in WEBSITE_ROUTE_SPECS
)

# Explicitly unreachable from the website. Kept as documentation AND as a
# defence-in-depth check in tests; the allowlist above is already default-deny.
WEBSITE_FORBIDDEN_PATHS: Tuple[str, ...] = (
    "/v1/developer/keys/rotate",
    "/v1/developer/jobs",
    "/v1/developer/api-keys",
    "/v1/developer/usage",
    "/v1/developer/requests",
    "/v1/owner/onboarding/overview",
    "/v1/operator/manage",
)

MAX_WEBSITE_PATH_LENGTH = 256
_CANONICAL_RE = re.compile(r"^[A-Za-z0-9\-._~/]+$")


def normalize_website_path(raw_pathname: Optional[str]) -> Optional[str]:
    """The canonical pathname, or None when the request is not canonical.

    Rejects percent-encoding, backslashes, duplicate slashes, dot segments and
    trailing slashes so a path-confusion variant can never reach a pattern.
    """
    if not isinstance(raw_pathname, str) or not raw_pathname:
        return None
    if len(raw_pathname) > MAX_WEBSITE_PATH_LENGTH:
        return None
    if not raw_pathname.startswith("/"):
        return None
    if "%" in raw_pathname or "\\" in raw_pathname or "//" in raw_pathname:
        return None
    if re.search(r"(^|/)\.{1,2}(/|$)", raw_pathname):
        return None
    if len(raw_pathname) > 1 and raw_pathname.endswith("/"):
        return None
    if not _CANONICAL_RE.match(raw_pathname):
        return None
    return raw_pathname


def website_route_allowed(method: str, pathname: Optional[str]) -> bool:
    """True only for an allowlisted method + exact canonical path."""
    canonical = normalize_website_path(pathname)
    if canonical is None:
        return False
    verb = str(method or "").upper()
    return any(verb in methods and pattern.match(canonical) for methods, pattern in _WEBSITE_ROUTES)


def is_management_path(pathname: Optional[str]) -> bool:
    """True for a tenant/owner/operator authority path (documentation + tests)."""
    canonical = normalize_website_path(pathname or "")
    if not canonical:
        return False
    if canonical.startswith(("/v1/owner/", "/v1/operator/", "/v1/internal/")):
        return True
    if canonical.startswith("/v1/developer/api-keys"):
        return True
    if canonical in ("/v1/developer/keys/rotate", "/v1/developer/jobs"):
        return True
    if canonical.startswith(("/v1/developer/usage", "/v1/developer/requests")):
        return True
    return False


def allowlisted_paths() -> Iterable[str]:
    """Human-readable allowlist (used by tests and documentation)."""
    for methods, pattern in WEBSITE_ROUTE_SPECS:
        yield f"{'/'.join(sorted(methods))} {pattern.strip('^$')}"
