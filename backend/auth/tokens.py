"""API-key hashing for the metered developer gate (Phase 16).

The hosted API stores ONLY the deterministic SHA-256 hash of a developer
API key — never the raw key. Lookup is performed by hashing the supplied
key and using the digest as the primary key, so no plaintext comparison
against stored values is ever needed.

Security boundary (stated honestly):

* SHA-256 is a fast hash. Storing ``hash_token(key)`` protects the raw
  key from casual exposure in the database, but a database attacker who
  also knows the key FORMAT could brute-force low-entropy keys. The
  raw keys issued to developers must therefore be high-entropy random
  tokens (the dev seed tool generates 32-byte URL-safe tokens).
* Hashing in the database makes raw-key recovery from a dump
  non-trivial; it does NOT make database compromise harmless.
"""

from __future__ import annotations

import hashlib

__all__ = ["hash_token"]


def hash_token(token: str) -> str:
    """Deterministic SHA-256 hex digest of an API key.

    Deterministic by design: the same key always maps to the same hash,
    which is what makes hash-keyed tenant lookup possible without
    storing (or comparing) raw keys anywhere.

    The token is stripped before hashing so that incidental whitespace
    around a header value cannot mint a second, mismatched identity.
    """
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
