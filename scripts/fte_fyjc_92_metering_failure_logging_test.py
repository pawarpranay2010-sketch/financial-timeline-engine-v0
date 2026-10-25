#!/usr/bin/env python3
"""
Metering-failure logging evidence (P1, fte_fyjc_92).

A  Claim authentication path — POST /v1/worker/jobs/claim authenticates
   through admission.authenticate_only. A store failure must return the
   SAME 503 envelope it always did, and must log EXACTLY ONE line naming
   the allow-listed CAUSE class (this used to log nothing at all).
B  Reserve path — POST /v1/process reserves through admission.admit ->
   gate.authorize_units. Same public contract, exactly one line, and the
   logged cause is the underlying exception, not the MeteredGateError
   wrapper (which is always the same string and hides the real cause).
C  Cause-name allow-list — a class that is not on the list degrades to a
   fixed token; exception messages are never used.

Both failing exceptions carry a FAKE DSN in their message, so every
section also asserts that the secret never reaches the log.

Offline: no PostgreSQL, no network, no model. The gate is patched at its
two seams (resolve_tenant / reserve_units); the real admission, route and
error-envelope code runs unmodified, so the response under test is the
one production serves.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Neutralize workspace gate configuration BEFORE the API is imported, then
# set the metering variable explicitly per section (same convention as
# fte_fyjc_62: gate state is re-read at request time, so this is effective).
for _var in ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY"):
    os.environ.pop(_var, None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

FAKE_DSN = "postgresql://meter_user:fake-secret-9x7@meter-db.internal:5432/platrixa"
SECRET_MARKERS = ("fake-secret-9x7", "meter-db.internal", FAKE_DSN)

# The 503 envelope as served BEFORE this change (captured from the live
# handler): the fix must not move a single byte of it.
EXPECTED_503_BODY = {
    "api_version": "v1",
    "error": {
        "api_status": "PROCESSING",
        "api_status_label": "Processing",
        "code": "METERING_UNAVAILABLE",
        "message": "metering service unavailable; request not admitted",
        "retryable": True,
    },
}

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


class OperationalError(Exception):
    """DBAPI-style class name (allow-listed); its message carries the fake DSN."""


class NotAllowlistedError(Exception):
    """A class name that must never be written to the log."""


class _Recorder(logging.Handler):
    """Collects rendered log lines from the one logger both layers use."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _post_and_capture(logger: logging.Logger, fn):
    """Run ``fn`` (one request) with the logger recorded; return (response, lines)."""
    rec = _Recorder()
    old_level = logger.level
    logger.addHandler(rec)
    logger.setLevel(logging.INFO)
    try:
        response = fn()
    finally:
        logger.removeHandler(rec)
        logger.setLevel(old_level)
    return response, rec.lines


def _response_checks(prefix: str, response, lines: list[str]) -> None:
    body = response.json() if response.text else None
    check(f"{prefix}1 status is still 503", response.status_code == 503, str(response.status_code))
    check(f"{prefix}2 response body unchanged", body == EXPECTED_503_BODY,
          json.dumps(body, sort_keys=True))
    warn_lines = [ln for ln in lines if "METERING_UNAVAILABLE" in ln or "metering" in ln]
    check(f"{prefix}3 exactly one metering-failure log line",
          len(warn_lines) == 1, f"count={len(warn_lines)} lines={lines}")
    check(f"{prefix}4 logs allow-listed cause class + fixed reason code",
          bool(warn_lines)
          and "cause=OperationalError" in warn_lines[0]
          and "reason=METERING_UNAVAILABLE" in warn_lines[0],
          warn_lines[0] if warn_lines else "")
    leaked = [ln for ln in lines if any(marker in ln for marker in SECRET_MARKERS)]
    check(f"{prefix}5 fake DSN/secret absent from every log line",
          not leaked, str(leaked))


class _StubClient:
    """Model stand-in: reaching it means admission wrongly admitted."""

    def process(self, text, request_id=None):
        raise AssertionError("model must never be reached on a refused request")


# ---------------------------------------------------------------------------
# A — claim authentication path (authenticate_only)
# ---------------------------------------------------------------------------

def section_a(client: TestClient) -> None:
    print("\nA — claim authentication path (POST /v1/worker/jobs/claim)")

    def boom_resolve(provided):
        raise metered_gate.MeteredGateError("metering lookup failed") from OperationalError(
            "connection failed for " + FAKE_DSN
        )

    original = metered_gate.resolve_tenant
    metered_gate.resolve_tenant = boom_resolve
    try:
        response, lines = _post_and_capture(
            logging.getLogger("platrixa.api"),
            lambda: client.post(
                "/v1/worker/jobs/claim",
                headers={"x-platrixa-api-key": "plx_test_key"},
            ),
        )
    finally:
        metered_gate.resolve_tenant = original
    _response_checks("A", response, lines)


# ---------------------------------------------------------------------------
# B — reserve path (admit -> authorize_units -> reserve_units)
# ---------------------------------------------------------------------------

def section_b(client: TestClient) -> None:
    print("\nB — reserve path (POST /v1/process)")

    ok_ctx = metered_gate.TenantContext(
        tenant_id="tenant-p1",
        monthly_limit=10,
        current_month_usage=0,
        usage_month="2026-10",
        units_reserved=0,
    )

    def ok_resolve(provided):
        return metered_gate.REASON_OK, ok_ctx

    def boom_reserve(provided_key, units=1):
        raise metered_gate.MeteredGateError("metering reservation failed") from OperationalError(
            "connection failed for " + FAKE_DSN
        )

    # resolve_tenant answers OK so the guard admits the request and the
    # handler reaches its single reservation; reserve_units then fails.
    orig_resolve, orig_reserve = metered_gate.resolve_tenant, metered_gate.reserve_units
    metered_gate.resolve_tenant, metered_gate.reserve_units = ok_resolve, boom_reserve
    try:
        response, lines = _post_and_capture(
            logging.getLogger("platrixa.api"),
            lambda: client.post(
                "/v1/process",
                json={"raw_input": "T"},
                headers={"x-platrixa-api-key": "plx_test_key"},
            ),
        )
    finally:
        metered_gate.resolve_tenant, metered_gate.reserve_units = orig_resolve, orig_reserve
    _response_checks("B", response, lines)


# ---------------------------------------------------------------------------
# C — cause-name allow-list (unit level)
# ---------------------------------------------------------------------------

def section_c() -> None:
    print("\nC — cause-name allow-list")
    try:
        from backend.auth.gate import _safe_cause_name
    except ImportError:
        check("C0 _safe_cause_name exists in backend.auth.gate", False, "not importable")
        return

    try:
        raise metered_gate.MeteredGateError("metering lookup failed") from OperationalError(FAKE_DSN)
    except metered_gate.MeteredGateError as exc:
        allowed = _safe_cause_name(exc)
    check("C1 allow-listed cause class name is returned", allowed == "OperationalError", allowed)
    check("C2 exception message never used (fake DSN absent)",
          FAKE_DSN not in allowed and "connection failed" not in allowed, allowed)

    try:
        raise metered_gate.MeteredGateError("metering lookup failed") from NotAllowlistedError(FAKE_DSN)
    except metered_gate.MeteredGateError as exc:
        blocked = _safe_cause_name(exc)
    check("C3 unlisted class degrades to a fixed token", blocked == "unclassified", blocked)
    check("C4 unlisted class name and message absent from the token",
          "NotAllowlisted" not in blocked and FAKE_DSN not in blocked, blocked)


def main() -> int:
    print("=" * 72)
    print("P1 — METERING-FAILURE LOGGING EVIDENCE (fte_fyjc_92)")
    print("=" * 72)

    saved = {var: os.environ.get(var) for var in
             ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY")}
    for var in ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY"):
        os.environ.pop(var, None)
    os.environ["PLATRIXA_METERING_DATABASE_URL"] = FAKE_DSN

    app = create_app()
    app.state.platrixa_client = _StubClient()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        section_a(client)
        section_b(client)
        section_c()
    finally:
        developer.reset_client()
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 72)
    print(f"TOTAL: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS"
          + (f"  —  {len(failed)} FAILED" if failed else ""))
    for n, d in failed:
        print(f"  FAIL: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
