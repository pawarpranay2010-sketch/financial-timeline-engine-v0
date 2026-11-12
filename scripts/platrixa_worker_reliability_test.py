#!/usr/bin/env python3
"""Worker reliability repair suite — failure reporting, 429, provenance.

Run:
    python scripts/platrixa_worker_reliability_test.py
    pytest scripts/platrixa_worker_reliability_test.py

Three defects are covered, each with its own section:

  A  FAILURE REPORTING. A model failure used to leave the job in PROCESSING
     until its lease lapsed — and once the attempt budget was spent, nothing
     could ever claim it again, so the job was lost silently. Every failure now
     goes through the fenced ``POST /v1/worker/jobs/{id}/failure`` route, which
     moves the job to RETRY_WAIT (budget left) or FAILED (terminal) using the
     SERVER's classification. Nothing in this section may ever submit a result.

  B  HTTP 429. The worker distinguishes a rate limit from a bad candidate: it
     parses ``Retry-After`` in both legal forms, applies a bounded fallback when
     the header is absent or invalid, clamps the wait to a configurable ceiling,
     and refuses to wait past the lease's safe window. A 401/403/400/422 is NOT
     retried, because the same request would be refused again.

  C  PROVENANCE. Provider/model identity comes from CONFIGURATION. The model
     server cannot name itself, a missing identity stays explicitly unknown, and
     the supported loopback llama.cpp configuration keeps its real label.

Scope: OFFLINE only. Every request is served by the in-process fake transports
imported from ``platrixa_outbound_inference_worker_test``; no socket is opened,
no Railway call is made, no lease exists on a real server, and no model is
started. Nothing here can reach production.
"""
from __future__ import annotations

import email.utils
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platrixa_outbound_inference_worker as worker_mod  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    DEFAULT_MODEL_PROVIDER,
    FAILURE_CODE_MODEL_UNAVAILABLE,
    FAILURE_CODE_REQUEST_MALFORMED,
    FAILURE_CODE_UNAUTHORIZED,
    FAILURE_CODE_VALIDATION_FAILED,
    PROVENANCE_UNKNOWN,
    BridgeUnavailableError,
    ModelRateLimitError,
    ModelRequestRejected,
    OutboundInferenceWorker,
    WorkerConfig,
    WorkerConfigError,
    classify_processing_failure,
    format_run_report,
    parse_retry_after,
)
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    BRIDGE_BASE,
    BRIDGE_BODY,
    BRIDGE_KEY,
    RAW_INPUT,
    RESULT_ENVELOPE,
    WORKER_ID,
    FakeClock,
    FakeResponse,
    FakeSession,
    chat_wire,
    claim_body,
    no_work_body,
    renewal_body,
)

MODEL_URL = "http://127.0.0.1:11434"
ANSWER = '{"transaction_type": "EXPENSE", "amount": 25000}'

# The failure-report acknowledgement the server returns for a retryable failure.
FAILURE_ACK_RETRY = {"api_version": "v1", "status": "RETRY_WAIT", "retryable": True}
FAILURE_ACK_TERMINAL = {"api_version": "v1", "status": "FAILED", "retryable": False}


def expect_raises(exc_type, fn) -> BaseException:
    try:
        fn()
    except exc_type as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001 - report the wrong type loudly
        raise AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {exc_type.__name__}, but nothing was raised")


class Recorder:
    """Everything an offline test needs to observe from one worker run."""

    def __init__(self) -> None:
        self.claims = 0
        self.results: list = []
        self.failures: list = []
        self.leases: list = []
        self.sleeps: list = []

    def api_handler(self, call, *, claim=None, lease_seconds=120.0, renew_every=40.0,
                    failure_response=None):
        url = call["url"]
        if url.endswith("/claim"):
            self.claims += 1
            if claim is not None and self.claims <= claim:
                return FakeResponse(200, claim_body(lease_seconds=lease_seconds,
                                                    renew_every_seconds=renew_every))
            return FakeResponse(200, no_work_body(1.0))
        if url.endswith("/lease"):
            self.leases.append(call)
            return FakeResponse(200, renewal_body(lease_seconds=lease_seconds))
        if url.endswith("/result"):
            self.results.append(call)
            return FakeResponse(200, RESULT_ENVELOPE)
        if url.endswith("/failure"):
            self.failures.append(call)
            if failure_response is not None:
                return failure_response()
            return FakeResponse(200, dict(FAILURE_ACK_RETRY))
        raise AssertionError(f"unexpected API call: {url}")


def make_reliability_worker(
    model_handler=None,
    *,
    bridge_handler=None,
    claim=1,
    lease_seconds=120.0,
    renew_every=40.0,
    model_url=MODEL_URL,
    model_provider=None,
    rate_limit_max_retries=2,
    rate_limit_max_wait_s=60.0,
    rate_limit_fallback_s=5.0,
    failure_response=None,
    clock=None,
):
    """A worker wired to fakes only, with the failure route served."""
    rec = Recorder()
    api_handler = lambda call: rec.api_handler(  # noqa: E731
        call, claim=claim, lease_seconds=lease_seconds, renew_every=renew_every,
        failure_response=failure_response,
    )
    config = WorkerConfig(
        api_base_url=API_BASE,
        api_key=API_KEY,
        worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE,
        bridge_key=BRIDGE_KEY,
        model_url=model_url,
        model_provider=model_provider,
        heartbeat_tick_s=0.01,
        shutdown_timeout_s=5.0,
        rate_limit_max_retries=rate_limit_max_retries,
        rate_limit_max_wait_s=rate_limit_max_wait_s,
        rate_limit_fallback_s=rate_limit_fallback_s,
    )
    api_session = FakeSession(api_handler)
    bridge_session = FakeSession(bridge_handler or (lambda call: FakeResponse(200, BRIDGE_BODY)))
    model_session = FakeSession(model_handler) if model_handler is not None else None
    worker = OutboundInferenceWorker(
        config,
        session=api_session,
        bridge_session=bridge_session,
        model_session=model_session,
        now=clock if clock is not None else time.monotonic,
        sleep=lambda seconds: rec.sleeps.append(float(seconds)),
    )
    return worker, rec


# ---------------------------------------------------------------------------
# A. Retry-After parsing (both legal forms)
# ---------------------------------------------------------------------------


def test_retry_after_delay_seconds_form() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(" 0 ") == 0.0
    assert parse_retry_after("1.5") == 1.5, "a float emitted by a proxy is tolerated"


def test_retry_after_http_date_form() -> None:
    now = 1_700_000_000.0
    header = email.utils.formatdate(now + 30, usegmt=True)
    wait = parse_retry_after(header, now=now)
    assert wait is not None, header
    assert 29.0 <= wait <= 30.0, wait


def test_retry_after_absent_or_invalid_is_none() -> None:
    for bad in (None, "", "   ", "soon", "next tuesday", "-5", "nan", "inf", "Sun, 99 Xxx 2026"):
        assert parse_retry_after(bad) is None, bad


def test_retry_after_past_date_is_zero_not_negative() -> None:
    now = 1_700_000_000.0
    header = email.utils.formatdate(now - 60, usegmt=True)
    assert parse_retry_after(header, now=now) == 0.0


def test_retry_after_absurd_value_has_a_credibility_ceiling() -> None:
    parsed = parse_retry_after("999999999")
    assert parsed is not None and parsed <= 86_400.0, parsed


# ---------------------------------------------------------------------------
# B. 429 handling
# ---------------------------------------------------------------------------


def test_429_delay_seconds_then_provider_recovers() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(429, {"error": "rate limited"}, headers={"Retry-After": "2"})
        return chat_wire(ANSWER)

    worker, rec = make_reliability_worker(model_handler)
    outcome = worker.process_job(worker.claim().job)

    assert outcome.accepted is True, dict(outcome)
    assert len(calls) == 2, f"one 429 then one success, got {len(calls)}"
    assert sum(rec.sleeps) >= 2.0, f"the Retry-After delay must be honoured: {sum(rec.sleeps)}"
    assert len(rec.results) == 1, "the recovered candidate must be submitted"
    assert rec.failures == [], "a recovered rate limit is NOT a failure to report"


def test_429_http_date_then_provider_recovers() -> None:
    """The HTTP-date form is understood end-to-end by the model path.

    Built against the real wall clock, because that is what the header means;
    the lease clock stays injected so the lease math is deterministic.
    """
    header = email.utils.formatdate(time.time() + 3, usegmt=True)
    calls = []

    def model_handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(429, {"error": "slow down"}, headers={"Retry-After": header})
        return chat_wire(ANSWER)

    worker, rec = make_reliability_worker(model_handler, rate_limit_max_wait_s=10.0)
    worker.process_job(worker.claim().job)

    assert len(calls) == 2, calls
    assert 2.0 <= sum(rec.sleeps) <= 3.1, f"expected the ~3s date delay, got {sum(rec.sleeps)}"


def test_429_without_retry_after_uses_bounded_fallback() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(429, {"error": "rate limited"})
        return chat_wire(ANSWER)

    worker, rec = make_reliability_worker(model_handler, rate_limit_fallback_s=4.0)
    worker.process_job(worker.claim().job)

    assert len(calls) == 2
    assert 3.9 <= sum(rec.sleeps) <= 4.1, f"fallback wait must be about 4s, got {sum(rec.sleeps)}"


def test_429_invalid_retry_after_uses_fallback_not_zero() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(429, {"error": "rate limited"}, headers={"Retry-After": "whenever"})
        return chat_wire(ANSWER)

    worker, rec = make_reliability_worker(model_handler, rate_limit_fallback_s=2.0)
    worker.process_job(worker.claim().job)

    assert sum(rec.sleeps) >= 1.9, f"an invalid hint must not collapse to no wait: {rec.sleeps}"


def test_429_excessive_wait_is_clamped_to_the_configurable_ceiling() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(429, {"error": "come back tomorrow"}, headers={"Retry-After": "3600"})
        return chat_wire(ANSWER)

    worker, rec = make_reliability_worker(model_handler, rate_limit_max_wait_s=8.0)
    worker.process_job(worker.claim().job)

    assert sum(rec.sleeps) <= 8.05, f"a huge hint must be clamped, got {sum(rec.sleeps)}"
    assert len(calls) == 2, "the request is still re-issued after the clamped wait"


def test_429_wait_longer_than_the_lease_is_refused() -> None:
    """Never sleep past the confirmed lease: refuse and report instead."""
    calls = []

    def model_handler(call):
        calls.append(call)
        return FakeResponse(429, {"error": "slow down"}, headers={"Retry-After": "30"})

    # lease 20s → safe window is 20 - max(1, 5) = 15s < 30s wait.
    worker, rec = make_reliability_worker(
        model_handler, lease_seconds=20.0, renew_every=5.0, rate_limit_max_wait_s=60.0
    )
    job = worker.claim().job

    exc = expect_raises(BridgeUnavailableError, lambda: worker.process_job(job))
    assert isinstance(exc, ModelRateLimitError), type(exc).__name__
    assert rec.sleeps == [], f"an unsafe wait must not sleep at all: {rec.sleeps}"
    assert len(calls) == 1, "only the initial request is made"
    assert rec.results == [], "nothing may be submitted"


def test_429_budget_exhausted_reports_a_retryable_failure() -> None:
    def model_handler(call):
        return FakeResponse(429, {"error": "rate limited"}, headers={"Retry-After": "1"})

    worker, rec = make_reliability_worker(model_handler, rate_limit_max_retries=2)
    completed = worker.run_forever(max_polls=1)

    assert completed == 0, "a rate-limited generation is never an accepted result"
    assert rec.results == [], "no result may be submitted"
    assert len(rec.failures) == 1, f"expected one failure report, got {len(rec.failures)}"
    assert rec.failures[0]["json"]["error"]["code"] == FAILURE_CODE_MODEL_UNAVAILABLE
    report = worker.last_run_report
    assert report["failures_reported"] == 1, report
    assert report["bridge_failures"] == 1, report


def test_auth_rejection_is_terminal_and_not_retried() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        return FakeResponse(401, {"error": "bad key"})

    worker, rec = make_reliability_worker(model_handler)
    completed = worker.run_forever(max_polls=1)

    assert completed == 0
    assert len(calls) == 1, f"a refused credential must not be retried: {len(calls)}"
    assert rec.sleeps == [], "no rate-limit wait for an auth failure"
    assert rec.failures[0]["json"]["error"]["code"] == FAILURE_CODE_UNAUTHORIZED


def test_request_refusal_is_terminal_and_not_retried() -> None:
    calls = []

    def model_handler(call):
        calls.append(call)
        return FakeResponse(422, {"error": "prompt too long"})

    worker, rec = make_reliability_worker(model_handler)
    worker.run_forever(max_polls=1)

    assert len(calls) == 1, calls
    assert rec.failures[0]["json"]["error"]["code"] == FAILURE_CODE_REQUEST_MALFORMED


def test_model_unavailable_reports_a_retryable_failure() -> None:
    def model_handler(call):
        return FakeResponse(503, {"error": "warming up"})

    worker, rec = make_reliability_worker(model_handler)
    worker.run_forever(max_polls=1)

    assert rec.results == []
    assert rec.failures[0]["json"]["error"]["code"] == FAILURE_CODE_MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# A (continued). Failure reporting contract, fencing and hygiene
# ---------------------------------------------------------------------------


def test_unusable_model_output_reports_validation_failed_without_a_result() -> None:
    """Empty/malformed output: fail closed, report, never submit."""
    worker, rec = make_reliability_worker(lambda call: FakeResponse(200, text="not json at all"))
    completed = worker.run_forever(max_polls=1)

    assert completed == 0
    assert rec.results == [], "an unusable candidate must never be submitted"
    assert len(rec.failures) == 1
    payload = rec.failures[0]["json"]
    assert payload["error"]["code"] == FAILURE_CODE_VALIDATION_FAILED
    assert payload["lease"] == {"owner": WORKER_ID, "generation": 1}, payload["lease"]


def test_failure_report_carries_no_payload_and_no_secret() -> None:
    worker, rec = make_reliability_worker(lambda call: FakeResponse(200, text=""))
    worker.run_forever(max_polls=1)

    encoded = json.dumps(rec.failures[0]["json"])
    assert RAW_INPUT not in encoded, "the financial payload must never leave in a failure report"
    assert API_KEY not in encoded and BRIDGE_KEY not in encoded, "a credential leaked"
    assert set(rec.failures[0]["json"]) == {"lease", "error"}, rec.failures[0]["json"]
    assert len(rec.failures[0]["json"]["error"]["message"]) <= 200


def test_failure_report_to_a_superseded_generation_does_not_raise() -> None:
    """A 409 means the server already moved on: count it, never retry."""
    worker, rec = make_reliability_worker(
        lambda call: FakeResponse(503, {"error": "down"}),
        failure_response=lambda: FakeResponse(409, {"error": {"code": "LEASE_LOST"}}),
    )
    completed = worker.run_forever(max_polls=1)

    assert completed == 0, "no exception may escape the poll loop"
    report = worker.last_run_report
    assert report["lease_lost"] == 1, report
    assert report["failures_reported"] == 0, report
    assert rec.results == []


def test_failure_report_store_error_is_counted_not_raised() -> None:
    worker, rec = make_reliability_worker(
        lambda call: FakeResponse(503, {"error": "down"}),
        failure_response=lambda: FakeResponse(503, {"error": {"code": "ASYNC_UNAVAILABLE"}}),
    )
    completed = worker.run_forever(max_polls=1)

    assert completed == 0
    report = worker.last_run_report
    assert report["failure_report_failures"] == 1, report
    assert report["failures_reported"] == 0, report


def test_malformed_failure_report_ack_is_not_reported_as_success() -> None:
    worker, rec = make_reliability_worker(
        lambda call: FakeResponse(503, {"error": "down"}),
        failure_response=lambda: FakeResponse(200, {"status": "PROBABLY_FINE"}),
    )
    worker.run_forever(max_polls=1)

    report = worker.last_run_report
    assert report["failures_reported"] == 0, report
    assert report["failure_report_failures"] == 1, report


def test_classification_maps_every_failure_class() -> None:
    cases = [
        (ModelRequestRejected("x", code=FAILURE_CODE_UNAUTHORIZED, status_code=401),
         FAILURE_CODE_UNAUTHORIZED),
        (ModelRequestRejected("x", code=FAILURE_CODE_REQUEST_MALFORMED, status_code=422),
         FAILURE_CODE_REQUEST_MALFORMED),
        (ModelRateLimitError("x", retry_after_s=1.0, attempts=3), FAILURE_CODE_MODEL_UNAVAILABLE),
        (BridgeUnavailableError("x"), FAILURE_CODE_MODEL_UNAVAILABLE),
        (worker_mod.BridgeResponseError("x"), FAILURE_CODE_VALIDATION_FAILED),
        (RuntimeError("boom"), "INTERNAL_ERROR"),
    ]
    for exc, expected in cases:
        code, reason = classify_processing_failure(exc)
        assert code == expected, (type(exc).__name__, code)
        assert reason and "\n" not in reason, reason


def test_run_report_distinguishes_reported_from_unreported_failures() -> None:
    text = format_run_report({
        "claims": 1, "claim_failures": 0, "no_work": 0, "claimed": 1,
        "accepted": 0, "replayed": 0, "rejected": 0, "lease_lost": 0,
        "bridge_failures": 1, "unknown": 0,
        "failures_reported": 1, "failure_report_failures": 0,
    })
    assert "1 failure(s) reported" in text, text
    assert "failure-report-failures=0" in text, text


# ---------------------------------------------------------------------------
# C. Provider and model provenance
# ---------------------------------------------------------------------------


def _provenance_block(worker, rec) -> dict:
    worker.process_job(worker.claim().job)
    assert len(rec.results) == 1, "the valid candidate must be submitted"
    return rec.results[0]["json"]["bridge_body"]["model"]


def test_default_provider_is_the_real_android_backend() -> None:
    worker, rec = make_reliability_worker(lambda call: chat_wire(ANSWER))
    block = _provenance_block(worker, rec)
    assert block["provider"] == DEFAULT_MODEL_PROVIDER == "android-llama-server", block
    assert block["model"] == "default", block
    assert block["artifact_identity"] == PROVENANCE_UNKNOWN, "no verified hash exists"


def test_explicit_provider_is_recorded_verbatim() -> None:
    worker, rec = make_reliability_worker(lambda call: chat_wire(ANSWER), model_provider="llamacpp-phone-a")
    assert _provenance_block(worker, rec)["provider"] == "llamacpp-phone-a"


def test_unknown_provider_stays_explicitly_unknown() -> None:
    worker, rec = make_reliability_worker(lambda call: chat_wire(ANSWER), model_provider="unknown")
    assert _provenance_block(worker, rec)["provider"] == PROVENANCE_UNKNOWN


def test_invalid_provider_configuration_is_refused() -> None:
    for bad in ("not a provider!", "provider/with/slash", "x" * 80):
        expect_raises(
            WorkerConfigError,
            lambda bad=bad: make_reliability_worker(
                lambda call: chat_wire(ANSWER), model_provider=bad
            ),
        )


def test_model_reported_identity_cannot_override_provenance() -> None:
    """A model server cannot name itself: the response body is not provenance."""
    lying = chat_wire(ANSWER)
    body = json.loads(lying.text)
    body["model"] = "gpt-9-omniscient"
    body["provider"] = "openai"
    lying.text = json.dumps(body)

    worker, rec = make_reliability_worker(lambda call: lying, model_provider="llamacpp-phone-a")
    block = _provenance_block(worker, rec)
    assert block["provider"] == "llamacpp-phone-a", block
    assert block["model"] == "default", block
    assert "gpt-9-omniscient" not in json.dumps(block)


def test_bridge_mode_body_is_transported_verbatim() -> None:
    """Device-bridge mode: the worker never invents a model block."""
    worker, rec = make_reliability_worker(None, model_url=None)
    worker.process_job(worker.claim().job)
    submitted = rec.results[0]["json"]["bridge_body"]
    assert submitted == BRIDGE_BODY, submitted


TESTS = (
    test_retry_after_delay_seconds_form,
    test_retry_after_http_date_form,
    test_retry_after_absent_or_invalid_is_none,
    test_retry_after_past_date_is_zero_not_negative,
    test_retry_after_absurd_value_has_a_credibility_ceiling,
    test_429_delay_seconds_then_provider_recovers,
    test_429_http_date_then_provider_recovers,
    test_429_without_retry_after_uses_bounded_fallback,
    test_429_invalid_retry_after_uses_fallback_not_zero,
    test_429_excessive_wait_is_clamped_to_the_configurable_ceiling,
    test_429_wait_longer_than_the_lease_is_refused,
    test_429_budget_exhausted_reports_a_retryable_failure,
    test_auth_rejection_is_terminal_and_not_retried,
    test_request_refusal_is_terminal_and_not_retried,
    test_model_unavailable_reports_a_retryable_failure,
    test_unusable_model_output_reports_validation_failed_without_a_result,
    test_failure_report_carries_no_payload_and_no_secret,
    test_failure_report_to_a_superseded_generation_does_not_raise,
    test_failure_report_store_error_is_counted_not_raised,
    test_malformed_failure_report_ack_is_not_reported_as_success,
    test_classification_maps_every_failure_class,
    test_run_report_distinguishes_reported_from_unreported_failures,
    test_default_provider_is_the_real_android_backend,
    test_explicit_provider_is_recorded_verbatim,
    test_unknown_provider_stays_explicitly_unknown,
    test_invalid_provider_configuration_is_refused,
    test_model_reported_identity_cannot_override_provenance,
    test_bridge_mode_body_is_transported_verbatim,
)


def main() -> int:
    print("=" * 78)
    print("Worker reliability repair — focused suite (failure report / 429 / provenance)")
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
    print(f"RESULT: {passed}/{len(TESTS)} reliability checks passed")
    for name, exc in failures:
        print(f"  FAIL {name} — {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
