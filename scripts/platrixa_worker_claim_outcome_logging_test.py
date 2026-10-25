#!/usr/bin/env python3
"""Platrixa — can a zero-acceptance worker run be diagnosed from its output?

Run:
    python scripts/platrixa_worker_claim_outcome_logging_test.py
    pytest scripts/platrixa_worker_claim_outcome_logging_test.py

The defect this covers
----------------------
``worker stopped; 0 job(s) submitted and accepted`` was printed for THREE
different situations, and ``main()`` exited 0 for all of them:

  a. the queue was empty — the server answered ``200 {"status": "NO_WORK"}``;
  b. no claim ever produced an outcome — every attempt was refused with
     ``503 METERING_UNAVAILABLE`` (or failed at the transport level), and the
     poll budget ran out;
  c. a job WAS claimed, but this worker could not commit it (server rejection,
     bridge unavailable, lease lost, ambiguous transport).

Before this change (b) left a single WARNING and (a)/(c) left NOTHING AT ALL —
NO_WORK had no log statement at any level, so even ``--verbose`` could not
prove the worker had reached the claim endpoint and been told there was no
work. The run report (``claims``, ``claim_failures``, ``no_work``, ``claimed``,
``accepted``, ``replayed``, ``rejected``, ``lease_lost``, ``bridge_failures``,
``unknown``) is what makes the three cases distinguishable without re-running
anything.

Scope of this suite: OFFLINE only. Every POST is served by the existing
in-process ``FakeSession``; there is no Railway call, no job claim, no lease
renewal, no submission, and no model inference. It proves the worker's
REPORTING, not that production returned any particular answer.

It also pins the two things this change must NOT alter: ``last_run_stats``
(the Stage 2 O2 ``{accepted, replayed}`` contract read by other tests) and the
legacy summary line.
"""
from __future__ import annotations

import contextlib
import io
import logging
import sys
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platrixa_outbound_inference_worker as worker_mod  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    ApiError,
    format_run_report,
)
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    RESULT_ENVELOPE,
    WORKER_ID,
    FakeResponse,
    FakeSession,
    claim_body,
    error_body,
    expect_raises,
    make_worker,
    no_work_body,
    renewal_body,
)

METERING_CODE = "METERING_UNAVAILABLE"
STORE_CODE = "ASYNC_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture():
    """Collect this module's log records at INFO (the worker's NO_WORK level)."""
    records: list = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("platrixa.worker")
    handler = _Collect()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, handler, records, previous_level


def _release(logger, handler, previous_level) -> None:
    logger.removeHandler(handler)
    logger.setLevel(previous_level)


def _worker(api_handler, bridge_handler=None):
    """A worker with fake transports and no real waiting."""
    worker, _api, _bridge = make_worker(
        api_handler, bridge_handler=bridge_handler, sleep=lambda seconds: None
    )
    return worker


def _messages(records) -> list:
    return [r.getMessage() for r in records]


def _run_cli(handler):
    """Run the REAL ``main()`` with the model/API transport replaced in-process."""
    out = io.StringIO()
    root = logging.getLogger()
    # A handler on the root makes main()'s ``basicConfig`` a no-op, so the
    # test does not write the worker's warnings to the real stderr.
    quiet = logging.NullHandler()
    root.addHandler(quiet)
    try:
        with mock.patch.object(worker_mod.requests, "Session", lambda *a, **k: FakeSession(handler)):
            with contextlib.redirect_stdout(out):
                code = worker_mod.main(
                    [
                        "--api-base-url", API_BASE,
                        "--api-key", API_KEY,
                        "--worker-id", WORKER_ID,
                        "--once",
                    ]
                )
    finally:
        root.removeHandler(quiet)
    return code, out.getvalue()


# ---------------------------------------------------------------------------
# 1  NO_WORK is observable (it used to be completely silent)
# ---------------------------------------------------------------------------


def test_no_work_is_logged_and_counted() -> None:
    logger, handler, records, previous_level = _capture()
    try:
        worker = _worker(lambda call: FakeResponse(200, no_work_body(1.0)))
        completed = worker.run_forever(max_polls=2)
    finally:
        _release(logger, handler, previous_level)

    assert completed == 0, "NO_WORK must never be counted as an accepted job"

    report = worker.last_run_report
    assert report["claims"] == 2, report
    assert report["no_work"] == 2, report
    assert report["claim_failures"] == 0, report
    assert report["claimed"] == 0, report
    assert report["accepted"] == 0 and report["replayed"] == 0, report

    no_work_lines = [m for m in _messages(records) if "NO_WORK" in m]
    assert len(no_work_lines) == 2, _messages(records)
    assert no_work_lines[0].startswith("claim 1: NO_WORK"), no_work_lines
    assert no_work_lines[1].startswith("claim 2: NO_WORK"), no_work_lines
    assert all(r.levelno == logging.INFO for r in records if "NO_WORK" in r.getMessage()), (
        "NO_WORK is normal pacing, so it is logged at INFO (visible with --verbose)"
    )

    text = format_run_report(report)
    assert "NO_WORK only" in text, text
    assert "claim attempt" not in text, text


# ---------------------------------------------------------------------------
# 2  A failed claim is NOT reported as NO_WORK
# ---------------------------------------------------------------------------


def test_failed_claim_is_not_reported_as_no_work() -> None:
    logger, handler, records, previous_level = _capture()
    try:
        worker = _worker(
            lambda call: FakeResponse(503, error_body(METERING_CODE, "metering service unavailable"))
        )
        completed = worker.run_forever(max_polls=2)
    finally:
        _release(logger, handler, previous_level)

    assert completed == 0, "a refused claim must never be counted as an accepted job"

    report = worker.last_run_report
    assert report["claims"] == 2, report
    assert report["claim_failures"] == 2, report
    assert report["no_work"] == 0, "a 503 is not a NO_WORK answer"
    assert report["claimed"] == 0, report

    warnings = [m for m in _messages(records) if "failed" in m]
    assert len(warnings) == 2, _messages(records)
    assert warnings[0].startswith("claim attempt 1 failed"), warnings
    assert METERING_CODE in warnings[0], warnings[0]

    text = format_run_report(report)
    assert "every claim attempt failed" in text, text
    assert "NO_WORK only" not in text, text


def test_zero_poll_budget_is_reported_as_no_attempt() -> None:
    worker = _worker(lambda call: FakeResponse(200, no_work_body(1.0)))

    completed = worker.run_forever(max_polls=0)

    assert completed == 0
    report = worker.last_run_report
    assert report["claims"] == 0, report
    text = format_run_report(report)
    assert "no claim was attempted" in text, text
    assert "NO_WORK only" not in text, text


# ---------------------------------------------------------------------------
# 3  A 503 is attributed to the dependency that actually failed
# ---------------------------------------------------------------------------


def test_503_attribution_names_the_right_dependency() -> None:
    metering = _worker(
        lambda call: FakeResponse(503, error_body(METERING_CODE, "metering service unavailable"))
    )
    exc = expect_raises(ApiError, metering.claim)
    assert exc.status_code == 503, exc
    assert exc.code == METERING_CODE, "the server's error code must be preserved verbatim"
    assert exc.retryable is True, "a 503 stays retryable — no claim outcome was received"
    message = str(exc)
    assert METERING_CODE in message, message
    assert "durable store" not in message, (
        "the admission guard refused the claim before the queue store was consulted; "
        "blaming the durable store misdirects the operator"
    )

    store = _worker(
        lambda call: FakeResponse(503, error_body(STORE_CODE, "async store unavailable"))
    )
    store_exc = expect_raises(ApiError, store.claim)
    assert store_exc.code == STORE_CODE, store_exc
    assert "durable store" in str(store_exc), str(store_exc)

    # A 503 with no readable envelope keeps the previous default code and text.
    bare = _worker(lambda call: FakeResponse(503, {}))
    bare_exc = expect_raises(ApiError, bare.claim)
    assert bare_exc.code == STORE_CODE, bare_exc
    assert "durable store" in str(bare_exc), str(bare_exc)


# ---------------------------------------------------------------------------
# 4-6  Claimed jobs are counted by what actually happened to them
# ---------------------------------------------------------------------------


def _job_handler(result_response):
    def handler(call):
        url = call["url"]
        if url.endswith("/claim"):
            return FakeResponse(200, claim_body())
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if url.endswith("/result"):
            return result_response()
        raise AssertionError(f"unexpected API call: {url}")

    return handler


def test_committed_candidate_is_counted_as_claimed_and_accepted() -> None:
    worker = _worker(_job_handler(lambda: FakeResponse(200, RESULT_ENVELOPE)))

    completed = worker.run_forever(max_polls=1)

    assert completed == 1, "a fresh fenced commit is the one thing counted as accepted"
    report = worker.last_run_report
    assert report["claimed"] == 1, report
    assert report["accepted"] == 1, report
    assert report["replayed"] == 0 and report["rejected"] == 0, report
    text = format_run_report(report)
    assert "claimed 1 job(s)" in text and "1 accepted" in text, text
    assert "NO_WORK only" not in text, text


def test_rejected_submission_is_counted_as_rejected() -> None:
    rejection = {
        "api_version": "v1",
        "status": "VALIDATION_FAILED",
        "engine_status": "VALIDATION_FAILED",
    }
    worker = _worker(_job_handler(lambda: FakeResponse(422, rejection)))

    completed = worker.run_forever(max_polls=1)

    assert completed == 0, "a refused candidate was never committed"
    report = worker.last_run_report
    assert report["claimed"] == 1, report
    assert report["rejected"] == 1, report
    assert report["accepted"] == 0 and report["replayed"] == 0, report
    text = format_run_report(report)
    assert "1 rejected" in text, text
    assert "NO_WORK only" not in text, text


def test_unavailable_bridge_is_counted_as_a_bridge_failure() -> None:
    worker = _worker(
        _job_handler(lambda: FakeResponse(200, RESULT_ENVELOPE)),
        bridge_handler=lambda call: FakeResponse(503, {"error": "model warming up"}),
    )

    completed = worker.run_forever(max_polls=1)

    assert completed == 0, "nothing may be submitted when the bridge is unavailable"
    report = worker.last_run_report
    assert report["claimed"] == 1, report
    assert report["bridge_failures"] == 1, report
    assert report["accepted"] == 0 and report["rejected"] == 0, report
    text = format_run_report(report)
    assert "1 bridge-failure(s)" in text, text
    assert "NO_WORK only" not in text, text


# ---------------------------------------------------------------------------
# 7-8  The CLI summary carries the distinction; the O2 contract is untouched
# ---------------------------------------------------------------------------


def test_cli_summary_distinguishes_the_zero_job_cases() -> None:
    code_no_work, text_no_work = _run_cli(lambda call: FakeResponse(200, no_work_body(1.0)))
    code_failed, text_failed = _run_cli(
        lambda call: FakeResponse(503, error_body(METERING_CODE, "metering service unavailable"))
    )

    for text in (text_no_work, text_failed):
        assert "worker stopped; 0 job(s) submitted and accepted" in text, text
        assert "run report:" in text, "the summary must carry the breakdown"
        assert API_KEY not in text, "the summary must never print a credential"

    assert "NO_WORK only" in text_no_work, text_no_work
    assert "every claim attempt failed" in text_failed, text_failed
    assert "NO_WORK only" not in text_failed, text_failed

    # Exit codes are deliberately UNCHANGED: both runs still exit 0, which is
    # precisely why the printed report has to distinguish them.
    assert code_no_work == 0 and code_failed == 0, (code_no_work, code_failed)


def test_run_stats_contract_is_unchanged() -> None:
    worker = _worker(lambda call: FakeResponse(200, no_work_body(1.0)))

    worker.run_forever(max_polls=1)

    assert worker.last_run_stats == {"accepted": 0, "replayed": 0}, worker.last_run_stats
    assert set(worker.last_run_report) >= set(worker_mod.RUN_REPORT_KEYS), worker.last_run_report


TESTS = (
    test_no_work_is_logged_and_counted,
    test_failed_claim_is_not_reported_as_no_work,
    test_zero_poll_budget_is_reported_as_no_attempt,
    test_503_attribution_names_the_right_dependency,
    test_committed_candidate_is_counted_as_claimed_and_accepted,
    test_rejected_submission_is_counted_as_rejected,
    test_unavailable_bridge_is_counted_as_a_bridge_failure,
    test_cli_summary_distinguishes_the_zero_job_cases,
    test_run_stats_contract_is_unchanged,
)


def main() -> int:
    print("=" * 78)
    print("Worker claim-outcome reporting — focused suite (platrixa_worker_claim_outcome_logging)")
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
    print(f"RESULT: {passed}/{len(TESTS)} claim-outcome checks passed")
    for name, exc in failures:
        print(f"  FAIL {name} — {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
