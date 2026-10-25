#!/usr/bin/env python3
"""Platrixa worker — does a FATAL claim rejection produce a usable run report?

Run:
    python scripts/platrixa_worker_fatal_report_test.py
    pytest scripts/platrixa_worker_fatal_report_test.py

The defect this covers (P4)
--------------------------
``main()`` only caught ``KeyboardInterrupt`` around ``run_forever``, so a
non-retryable claim error — HTTP 401 for a refused tenant key, or a 4xx whose
ApiError message is the server's raw response body — escaped the CLI:

  * Python's traceback went to stderr (including whatever the response body
    contained, through the exception's ``__str__``),
  * the final run report was never printed, and
  * the exit code was Python's generic 1, indistinguishable from a crash.

Fail-fast itself is the contract and must NOT change: a bad credential still
stops the run immediately and still exits non-zero. What changes is that the
operator now gets the run report plus one bounded, secret-free reason line,
under an exit code (3) distinct from 0 (finished), 2 (configuration error)
and 130 (interrupt).

Scope: OFFLINE only. Every POST is served by the in-process ``FakeSession``
imported from ``platrixa_outbound_inference_worker_test``; there is no Railway
call, no real session, no lease, no model. Nothing here can reach a network.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platrixa_outbound_inference_worker as worker_mod  # noqa: E402
from platrixa_outbound_inference_worker import ApiError  # noqa: E402
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    WORKER_ID,
    FakeResponse,
    FakeSession,
    error_body,
    no_work_body,
)

METERING_CODE = "METERING_UNAVAILABLE"
# A response body that would leak if anyone ever printed str(exc) or the raw
# response: it stands in for a driver/DSN-bearing error payload.
LEAKY_BODY = {
    "api_version": "v1",
    "error": {
        "code": "CONFLICT",
        "message": "detail postgres://leaky_user:fake-secret-9x7@meter-db.internal/db",
        "request_id": "req-1",
        "api_status": "FAILED",
        "retryable": False,
    },
}
LEAKY_MARKER = "fake-secret-9x7"
CONFIG_ENV = "PLATRIXA_API_KEY"


def _quiet_logging() -> object:
    """Make main()'s basicConfig a no-op so log lines never reach real stderr."""
    root = logging.getLogger()
    handler = logging.NullHandler()
    root.addHandler(handler)
    return handler


def _unquiet(handler: object) -> None:
    logging.getLogger().removeHandler(handler)  # type: ignore[arg-type]


def _run_cli(handler, argv=None):
    """Run the REAL ``main()`` with the API transport faked in-process.

    Returns ``(exit_code, stdout, stderr, session)`` — the session is the one
    every fake POST was recorded on, so "no extra request" is observable.
    """
    session = FakeSession(handler)
    out, err = io.StringIO(), io.StringIO()
    quiet = _quiet_logging()
    saved_key = __import__("os").environ.pop(CONFIG_ENV, None)
    try:
        with mock.patch.object(worker_mod.requests, "Session", lambda *a, **k: session):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = worker_mod.main(argv or ["--help-placeholder"])
    finally:
        if saved_key is not None:
            __import__("os").environ[CONFIG_ENV] = saved_key
        _unquiet(quiet)
    return code, out.getvalue(), err.getvalue(), session


def _default_argv(*extra: str) -> list:
    return [
        "--api-base-url", API_BASE,
        "--api-key", API_KEY,
        "--worker-id", WORKER_ID,
        *extra,
    ]


def _claim_calls(session: FakeSession) -> list:
    return [c for c in session.calls if c["url"].endswith("/claim")]


def _all_output(stdout: str, stderr: str) -> str:
    return stdout + "\n" + stderr


# ---------------------------------------------------------------------------
# 1  Fatal 401: report + reason + non-zero exit, no traceback, one request
# ---------------------------------------------------------------------------

def test_fatal_401_reports_and_exits_non_zero() -> None:
    code, stdout, stderr, session = _run_cli(
        lambda call: FakeResponse(401, error_body("UNAUTHORIZED", "the API rejected this tenant key")),
        _default_argv("--once"),
    )

    assert code != 0, "a fatal claim rejection must never exit 0"
    assert code == worker_mod.EXIT_FATAL_CLAIM == 3, f"exit code {code}"
    assert "run report:" in stdout, stdout
    assert "claims=1" in stdout, stdout
    # The report must say the claim produced NO outcome — before this change
    # a zero-claimed run was described as "NO_WORK only", which is a lie when
    # the server refused the credential.
    assert "every claim attempt failed" in stdout, stdout
    assert "NO_WORK only" not in stdout, stdout

    assert "fatal claim error: HTTP 401 UNAUTHORIZED" in stderr, stderr
    assert "Traceback" not in _all_output(stdout, stderr), stderr
    assert len(_claim_calls(session)) == 1, f"exactly one claim request, got {len(_claim_calls(session))}"
    assert API_KEY not in _all_output(stdout, stderr), "a credential was printed"


# ---------------------------------------------------------------------------
# 2  A raw response body must never be printed, even when it is the message
# ---------------------------------------------------------------------------

def test_fatal_4xx_never_prints_the_response_body() -> None:
    leaked_text = json.dumps(LEAKY_BODY)
    code, stdout, stderr, session = _run_cli(
        lambda call: FakeResponse(409, LEAKY_BODY),
        _default_argv("--once"),
    )

    assert code == worker_mod.EXIT_FATAL_CLAIM, f"exit code {code}"
    combined = _all_output(stdout, stderr)
    assert "fatal claim error: HTTP 409 CONFLICT" in stderr, stderr
    assert "run report:" in stdout, stdout
    assert LEAKY_MARKER not in combined, "the raw response body reached the operator"
    assert "leaky_user" not in combined and "meter-db.internal" not in combined, combined
    assert leaked_text not in combined, "the response body was printed verbatim"
    assert "Traceback" not in combined, combined
    assert len(_claim_calls(session)) == 1, len(_claim_calls(session))
    assert API_KEY not in combined, "a credential was printed"


# ---------------------------------------------------------------------------
# 3  Retryable failures keep their existing retry/backoff behaviour
# ---------------------------------------------------------------------------

def test_retryable_503_still_retries_and_exits_zero() -> None:
    code, stdout, stderr, session = _run_cli(
        lambda call: FakeResponse(503, error_body(METERING_CODE, "metering service unavailable")),
        _default_argv("--max-polls", "2"),
    )

    assert code == 0, f"a retryable outage must keep exiting 0, got {code}"
    assert len(_claim_calls(session)) == 2, f"both polls must be attempted, got {len(_claim_calls(session))}"
    assert "every claim attempt failed (2)" in stdout, stdout
    assert "worker stopped; 0 job(s) submitted and accepted" in stdout, stdout
    assert "fatal claim error" not in _all_output(stdout, stderr), stderr
    assert "Traceback" not in _all_output(stdout, stderr), stderr
    assert API_KEY not in _all_output(stdout, stderr), "a credential was printed"


# ---------------------------------------------------------------------------
# 4  The healthy and configuration-error exit codes are unchanged
# ---------------------------------------------------------------------------

def test_no_work_run_still_exits_zero_and_reports() -> None:
    code, stdout, stderr, session = _run_cli(
        lambda call: FakeResponse(200, no_work_body(1.0)),
        _default_argv("--once"),
    )

    assert code == 0, f"exit code {code}"
    assert "worker stopped; 0 job(s) submitted and accepted" in stdout, stdout
    assert "NO_WORK only" in stdout, stdout
    assert "run report:" in stdout, stdout
    assert len(_claim_calls(session)) == 1, len(_claim_calls(session))


def test_configuration_error_exit_code_is_unchanged() -> None:
    code, stdout, stderr, session = _run_cli(
        lambda call: FakeResponse(200, no_work_body(1.0)),
        ["--api-base-url", API_BASE, "--worker-id", WORKER_ID, "--once"],
    )

    assert code == 2, f"configuration errors still exit 2, got {code}"
    assert "worker configuration error" in stderr, stderr
    assert session.calls == [], "a configuration error must not touch the network"
    assert "run report:" not in stdout, stdout


def test_interrupt_exit_code_is_unchanged() -> None:
    def _interrupt(call):
        raise KeyboardInterrupt

    code, stdout, stderr, _session = _run_cli(_interrupt, _default_argv("--once"))

    assert code == 130, f"interrupt still exits 130, got {code}"
    assert "interrupted; stopped cleanly" in stderr, stderr
    assert "Traceback" not in _all_output(stdout, stderr), stderr


# ---------------------------------------------------------------------------
# 5  The fatal line itself is bounded
# ---------------------------------------------------------------------------

def test_fatal_message_bounds_the_error_code() -> None:
    ugly = ApiError(401, "BAD\ncode: leak", "raw body with fake-secret-9x7", retryable=False)
    message = worker_mod.fatal_claim_message(ugly)

    assert "\n" not in message, message
    assert "UNSPECIFIED" in message, message
    assert "leak" not in message and "fake-secret-9x7" not in message, message
    assert "HTTP 401" in message, message

    clean = ApiError(401, "UNAUTHORIZED", "the API rejected this tenant key", retryable=False)
    assert "HTTP 401 UNAUTHORIZED" in worker_mod.fatal_claim_message(clean)


TESTS = (
    test_fatal_401_reports_and_exits_non_zero,
    test_fatal_4xx_never_prints_the_response_body,
    test_retryable_503_still_retries_and_exits_zero,
    test_no_work_run_still_exits_zero_and_reports,
    test_configuration_error_exit_code_is_unchanged,
    test_interrupt_exit_code_is_unchanged,
    test_fatal_message_bounds_the_error_code,
)


def main() -> int:
    print("=" * 78)
    print("Worker fatal-run reporting — focused suite (platrixa_worker_fatal_report)")
    print("=" * 78)
    passed = 0
    failures = []
    for fn in TESTS:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - a failing test is data, not a crash
            import traceback

            failures.append((fn.__name__, exc))
            print(f"  [FAIL] {fn.__name__} — {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  [PASS] {fn.__name__}")

    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{len(TESTS)} fatal-run checks passed")
    for name, exc in failures:
        print(f"  FAIL {name} — {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
