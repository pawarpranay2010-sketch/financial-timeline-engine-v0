"""Visitor assertion nonce store — bounded expiry cleanup.

Why this module exists
======================
``backend/database/platrixa_visitor_nonce_schema.sql`` created
``visitor_assertion_nonces`` with an ``expires_at`` column and an index for
cleanup, but nothing ever invoked the deletion predicate, so expired rows
accumulated forever. This module owns that maintenance operation.

Guarantees
----------
* Deletes ONLY rows whose ``expires_at < NOW()``.
* Deletes AT MOST ``batch_size`` rows per call (bounded work per invocation).
* Uses ``FOR UPDATE SKIP LOCKED`` so concurrent invocations never block each
  other and never delete the same row twice.
* Returns the number of rows actually deleted.
* Never touches uniqueness, assertion verification or replay semantics — it
  only removes rows that are already expired and therefore unusable.

One-shot command (schedule it with an existing scheduler or cron):

    PLATRIXA_METERING_DATABASE_URL=postgresql://... \
        python -m backend.auth.visitor_nonces --batch-size 1000

Exit status: 0 = cleanup ran (deleted count printed), 1 = cleanup failed.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("platrixa.visitor_nonces")

DEFAULT_BATCH_SIZE = 1000
MAX_BATCH_SIZE = 10_000

_CLEANUP_SQL = """
WITH expired AS (
    SELECT nonce
      FROM visitor_assertion_nonces
     WHERE expires_at < NOW()
     ORDER BY expires_at
     LIMIT :batch_size
     FOR UPDATE SKIP LOCKED
)
DELETE FROM visitor_assertion_nonces AS target
 USING expired
 WHERE target.nonce = expired.nonce
RETURNING target.nonce
"""


class VisitorNonceCleanupError(RuntimeError):
    """Cleanup could not run (no store configured, or the store failed)."""


def _database_url() -> str:
    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL", "") or "").strip()
    if not url:
        raise VisitorNonceCleanupError("PLATRIXA_METERING_DATABASE_URL is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _validate_batch_size(batch_size: int) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("batch_size must be an integer")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be <= {MAX_BATCH_SIZE}")
    return batch_size


def cleanup_expired_visitor_nonces(batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Delete at most ``batch_size`` expired visitor nonces; return the count.

    Raises ``ValueError`` on an invalid batch size and
    ``VisitorNonceCleanupError`` when the store is unconfigured or fails.
    Authentication is unaffected: verification already fails closed on a
    nonce-store error, and this function never deletes unexpired rows.
    """
    batch_size = _validate_batch_size(batch_size)
    url = _database_url()

    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(url, future=True, pool_pre_ping=True)
        try:
            with engine.begin() as conn:
                rows = conn.execute(text(_CLEANUP_SQL), {"batch_size": batch_size}).fetchall()
        finally:
            engine.dispose()
    except Exception as exc:  # driver/network/schema failures
        logger.warning("visitor nonce cleanup failed: %s", type(exc).__name__)
        raise VisitorNonceCleanupError(f"visitor nonce cleanup failed: {type(exc).__name__}") from exc

    deleted = len(rows)
    logger.info("visitor nonce cleanup deleted %s expired row(s)", deleted)
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.auth.visitor_nonces",
        description="Delete expired visitor assertion nonces (one-shot, bounded).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"maximum rows to delete this run (1..{MAX_BATCH_SIZE}, default {DEFAULT_BATCH_SIZE})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        deleted = cleanup_expired_visitor_nonces(args.batch_size)
    except (ValueError, VisitorNonceCleanupError) as exc:
        print(f"❌ visitor nonce cleanup failed: {exc}", file=sys.stderr)
        return 1
    print(f"✅ visitor nonce cleanup deleted {deleted} expired row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
