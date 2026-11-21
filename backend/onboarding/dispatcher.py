"""Durable dispatcher for self-service developer onboarding notifications.

Run as a long-lived worker or one-shot job:

    python -m backend.onboarding.dispatcher --once
    python -m backend.onboarding.dispatcher --interval 30

The dispatcher claims due notification rows with a lease token, delivers them
through the configured email provider, and acknowledges each row with the same
token. Concurrent instances coordinate through the database (claim_token +
lease_expires_at), so at most one worker owns a row at a time; stale claims
are recovered after the lease expires.

No SMTP work happens in the request path: the API only enqueues rows.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from backend.onboarding import config, notifications


_STOP = {"flag": False}


def _handle_signal(signum, frame):
    _STOP["flag"] = True


def run_once(limit: int = 50, worker_id: Optional[str] = None) -> dict:
    """One pass: deliver due notifications, then expire abandoned applications.

    Expiry belongs here rather than in the request path so an idle intake
    still converges (audit S-4). A failure to expire never blocks delivery.
    """
    from backend.onboarding import service

    result = notifications.dispatch_due(limit=limit, worker_id=worker_id)
    try:
        expired = service.expire_abandoned(actor="dispatcher", limit=limit)
        result["expired"] = expired.get("expired", 0)
    except Exception as exc:  # schema not ready / store unreachable
        result["expired"] = 0
        result["expire_error"] = type(exc).__name__
    return result


def run_loop(interval: int, limit: int, worker_id: Optional[str]) -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    while not _STOP["flag"]:
        try:
            result = run_once(limit=limit, worker_id=worker_id)
            print(f"dispatched={result.get('claimed', 0)} sent={result.get('sent', 0)} failed={result.get('failed', 0)}", flush=True)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"dispatcher error: {type(exc).__name__}: {exc}", flush=True)
        if _STOP["flag"]:
            break
        time.sleep(max(1, int(interval)))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa onboarding notification dispatcher")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between passes (loop mode)")
    parser.add_argument("--limit", type=int, default=50, help="Max rows per pass")
    parser.add_argument("--worker-id", default=None, help="Optional worker identity for observability")
    args = parser.parse_args(argv)

    if args.once:
        result = run_once(limit=args.limit, worker_id=args.worker_id)
        print(result)
        return 0
    return run_loop(args.interval, args.limit, args.worker_id)


if __name__ == "__main__":
    sys.exit(main())
