"""Email + onboarding configuration diagnostics.

Read-only. Prints a JSON report an operator can paste into a ticket: which
environment variables are present (names and booleans only — never values),
which are missing, and, with ``--probe``, whether the SMTP endpoint actually
accepts a connection and the configured credentials. ``--probe`` sends NO
message.

Usage::

    python -m backend.auth.email_diagnostics
    python -m backend.auth.email_diagnostics --probe

Exit codes: 0 = configured (and probe OK when requested), 2 = not configured,
3 = probe failed, 4 = onboarding store unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys


def _report(*, probe: bool) -> tuple[dict, int]:
    from backend.onboarding import config, email, notifications

    status = email.provider_status()
    report: dict = {
        "onboarding_enabled": config.configured(),
        "store_configured": bool(config.database_url()),
        "key_delivery_secret_configured": config.delivery_configured(),
        "owner_notification_email_configured": bool(config.owner_email()),
        "public_origin_configured": bool(config.public_origin()),
        "email": status,
    }
    exit_code = 0 if (status["configured"] and report["store_configured"]) else 2
    if not config.delivery_configured():
        # Without this, verified applications are held instead of activated.
        exit_code = 2

    if probe:
        transport = email.transport()
        result = transport.probe()
        report["email_probe"] = result
        if not result.get("ok"):
            exit_code = 3

    try:
        report["notifications"] = notifications.counts()
    except Exception:  # pragma: no cover - defensive, diagnostics must not crash
        report["notifications"] = {"available": False}
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="open a real SMTP connection and authenticate (sends no message)",
    )
    args = parser.parse_args(argv)
    report, exit_code = _report(probe=args.probe)
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
