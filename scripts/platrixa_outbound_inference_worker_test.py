#!/usr/bin/env python3
"""Platrixa — outbound inference worker suite (Stage 2, fte worker contract).

Deterministic tests for ``scripts/platrixa_outbound_inference_worker.py``.
Nothing here contacts Railway, any external model API, or the production API
base URL: every request is served by an in-process fake transport, and every
thread wait is bounded by an event or a short timeout.

Coverage (the ten behaviours reproduced from the Colab implementation, then the
eight hardening cases this suite adds):

     1  no-work response + the exact request headers (and no bridge call)
     2  a claimed job exposes its lease proof, timings and both URLs
     3  unsafe endpoints are refused (remote plaintext HTTP, absolute lease
        URLs, non-loopback bridge, embedded credentials)
     4  the bridge call carries the FULL body, the raw input, and ONLY the
        bridge credential
     5  submission carries the COMPLETE bridge body plus the lease proof
     6  a 409 on the result endpoint fences the worker and is never retried
     7  a malformed/oversized/non-object bridge response is never submitted
     8  a real heartbeat that receives 409 marks the lease lost
     9  a real heartbeat accepts a renewal whose proof matches
    10  a real heartbeat rejects a renewal with the wrong generation
    11  an expired, unrenewed lease prevents submission
    12  repeated renewal transport failures lose the lease once the confirmed
        deadline passes (and not before)
    13  a mismatched owner/generation proof is not evidence of ownership
    14  a lease already known lost is never bridged or submitted
    15  an end-to-end 409 surfaces as ``LeaseLost`` with exactly one submission
    16  a blocked renewal cannot make shutdown hang
    17  oversized and non-object bodies fail closed at the decode/submit guard
    18  lease endpoints are validated (absolute, query/fragment, traversal,
        mismatched job id, wrong suffix)
    19  the poll loop is bounded: NO_WORK pacing hints are honoured and clamped,
        transport failures back off exponentially under a cap and reset after a
        success, and a stop request ends an unbounded loop (no real waiting)

Run:
    python scripts/platrixa_outbound_inference_worker_test.py
    pytest scripts/platrixa_outbound_inference_worker_test.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from platrixa_outbound_inference_worker import (  # noqa: E402
    API_KEY_HEADER,
    BRIDGE_KEY_HEADER,
    WORKER_ID_HEADER,
    BridgeResponseError,
    LeaseLost,
    MalformedResponseError,
    OutboundInferenceWorker,
    UnsafeEndpointError,
    WorkerConfig,
    resolve_lease_endpoint,
)

# ---------------------------------------------------------------------------
# Fixtures — a fake HTTP transport. No socket is ever opened here.
# ---------------------------------------------------------------------------

API_BASE = "https://api.example.test"
BRIDGE_BASE = "http://127.0.0.1:8081"
API_KEY = "plx_test_tenant_key_not_a_secret"
BRIDGE_KEY = "device-bridge-key-not-a-secret"
WORKER_ID = "device-a"

RAW_INPUT = "purchased furniture from raj for rs.25000"
JOB_ID = "job-1"
REQUEST_ID = "req-1"

# The COMPLETE bridge body, including the model block and the completion
# status. The worker must transport this verbatim — never an extracted field.
BRIDGE_BODY = {
    "interpretation_text": '{"transaction_type": "EXPENSE", "amount": 25000}',
    "completion_status": "COMPLETE",
    "finish_reason": "stop",
    "model": {"provider": "llama.cpp local bridge", "artifact_identity": "PROVENANCE_UNKNOWN"},
}

RESULT_ENVELOPE = {
    "api_version": "v1",
    "request_id": REQUEST_ID,
    "api_status": "REVIEW_REQUIRED",
    "engine_status": "REVIEW_REQUIRED",
}


class FakeResponse:
    """The subset of ``requests.Response`` the worker reads."""

    def __init__(self, status_code: int, body=None, text=None, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        if text is None:
            text = json.dumps(body) if body is not None else ""
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Records every call and delegates the response to a handler."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        call = {"url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        return self._handler(call)


class FakeClock:
    """An injectable monotonic clock so lease timing needs no real waiting."""

    def __init__(self, start: float = 0.0):
        self._value = float(start)

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += float(seconds)


def error_body(code: str, message: str = "") -> dict:
    return {
        "api_version": "v1",
        "error": {
            "code": code,
            "message": message or code,
            "request_id": REQUEST_ID,
            "api_status": "FAILED",
            "retryable": False,
        },
    }


def claim_body(
    *,
    job_id: str = JOB_ID,
    owner: str = WORKER_ID,
    generation: int = 1,
    lease_seconds: float = 120.0,
    renew_every_seconds: float = 40.0,
    raw_input: str = RAW_INPUT,
    renew_url=None,
    result_url=None,
) -> dict:
    """The documented CLAIMED claim response, verbatim in shape."""
    return {
        "api_version": "v1",
        "status": "CLAIMED",
        "job": {
            "job_id": job_id,
            "request_id": REQUEST_ID,
            "raw_input": raw_input,
            "lease": {
                "owner": owner,
                "generation": generation,
                "lease_seconds": lease_seconds,
                "renew_every_seconds": renew_every_seconds,
                "renew_url": renew_url or f"/v1/worker/jobs/{job_id}/lease",
                "result_url": result_url or f"/v1/worker/jobs/{job_id}/result",
            },
        },
    }


def renewal_body(owner: str = WORKER_ID, generation: int = 1, lease_seconds: float = 120.0) -> dict:
    return {
        "api_version": "v1",
        "job_id": JOB_ID,
        "renewed": True,
        "lease": {
            "owner": owner,
            "generation": generation,
            "lease_seconds": lease_seconds,
            "renew_every_seconds": 40.0,
        },
    }


def no_work_body(retry_after_seconds: float = 3.0) -> dict:
    return {
        "api_version": "v1",
        "status": "NO_WORK",
        "job": None,
        "retry_after_seconds": retry_after_seconds,
    }


def make_worker(handler, *, bridge_handler=None, now=time.monotonic, tick=0.01, config=None, sleep=None):
    """Worker wired to fake transports only — never a real ``requests.Session``."""
    if config is None:
        config = WorkerConfig(
            api_base_url=API_BASE,
            api_key=API_KEY,
            worker_id=WORKER_ID,
            bridge_url=BRIDGE_BASE,
            bridge_key=BRIDGE_KEY,
            heartbeat_tick_s=tick,
            shutdown_timeout_s=5.0,
        )
    if bridge_handler is None:
        bridge_handler = lambda call: FakeResponse(200, BRIDGE_BODY)  # noqa: E731
    api_session = FakeSession(handler)
    bridge_session = FakeSession(bridge_handler)
    worker = OutboundInferenceWorker(
        config,
        session=api_session,
        bridge_session=bridge_session,
        now=now,
        sleep=sleep,  # injected so the poll loop never really waits
    )
    return worker, api_session, bridge_session


def only(calls, suffix: str) -> list:
    return [c for c in calls if c["url"].endswith(suffix)]


def expect_raises(exc_type, fn) -> BaseException:
    """Assert ``fn`` raises ``exc_type``; returns the exception."""
    try:
        fn()
    except exc_type as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001 - deliberate: report the wrong type
        raise AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {exc_type.__name__}, but nothing was raised")


def wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """Bounded spin on a predicate for cross-thread transitions."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# 1-2  Claiming
# ---------------------------------------------------------------------------


def test_no_work_and_headers() -> None:
    worker, api, bridge = make_worker(lambda call: FakeResponse(200, no_work_body(3.0)))

    result = worker.claim()

    assert result.no_work is True, "an explicit NO_WORK must be reported as no work"
    assert result.job is None, "NO_WORK must carry no job"
    assert result.retry_after_seconds == 3.0, "the server's pacing hint must be honoured"

    assert len(api.calls) == 1, f"expected exactly one claim request, got {len(api.calls)}"
    call = api.calls[0]
    assert call["url"] == f"{API_BASE}/v1/worker/jobs/claim", call["url"]
    assert call["headers"][API_KEY_HEADER] == API_KEY, "the tenant key must authenticate the claim"
    assert call["headers"][WORKER_ID_HEADER] == WORKER_ID, "the worker id labels the lease owner"
    assert bridge.calls == [], "a no-work claim must never touch the model bridge"


def test_claimed_job() -> None:
    worker, _api, _bridge = make_worker(lambda call: FakeResponse(200, claim_body()))

    result = worker.claim()

    assert result.no_work is False and result.job is not None
    job = result.job
    assert job.job_id == JOB_ID, job.job_id
    assert job.request_id == REQUEST_ID, job.request_id
    assert job.raw_input == RAW_INPUT, job.raw_input
    assert job.state.owner == WORKER_ID and job.state.generation == 1
    assert job.state.lease_seconds == 120.0 and job.state.renew_every_seconds == 40.0
    assert job.state.renew_url == f"{API_BASE}/v1/worker/jobs/{JOB_ID}/lease", job.state.renew_url
    assert job.state.result_url == f"{API_BASE}/v1/worker/jobs/{JOB_ID}/result", job.state.result_url
    assert job.state.is_lost is False, "a fresh claim starts held"


# ---------------------------------------------------------------------------
# 3  Endpoint safety
# ---------------------------------------------------------------------------


def test_reject_unsafe_endpoint() -> None:
    def config(**overrides):
        base = dict(api_base_url=API_BASE, api_key=API_KEY, worker_id=WORKER_ID, bridge_url=BRIDGE_BASE)
        base.update(overrides)
        return WorkerConfig(**base)

    def build(**overrides):
        return OutboundInferenceWorker(config(**overrides))

    # Remote plaintext HTTP would put the tenant key on the wire unprotected.
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url="http://api.example.test"))
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url="http://10.0.0.5:8000"))
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url="ftp://api.example.test"))
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url="https://key:secret@api.example.test"))
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url="https://api.example.test/?x=1"))
    expect_raises(UnsafeEndpointError, lambda: build(api_base_url=""))

    # A loopback http API is legitimate local development and must be accepted
    # (the rule is "no REMOTE plaintext", not "no plaintext").
    local = build(api_base_url="http://127.0.0.1:8000")
    assert local.config.api_base_url == "http://127.0.0.1:8000"

    # The model bridge is device-local only: no public bridge, ever.
    expect_raises(UnsafeEndpointError, lambda: build(bridge_url="http://192.168.1.20:8081"))
    expect_raises(UnsafeEndpointError, lambda: build(bridge_url="http://bridge.example.test:8081"))
    expect_raises(UnsafeEndpointError, lambda: build(bridge_url="http://0.0.0.0:8081"))

    # A claim response must not be able to redirect the worker's credential or
    # its raw input somewhere else.
    def handler(call):
        return FakeResponse(200, claim_body(renew_url="https://attacker.example/v1/worker/jobs/job-1/lease"))

    worker, _api, _bridge = make_worker(handler)
    expect_raises(UnsafeEndpointError, worker.claim)


# ---------------------------------------------------------------------------
# 4-5  The bridge body, transported whole
# ---------------------------------------------------------------------------


def test_bridge_full_body() -> None:
    worker, _api, bridge = make_worker(
        lambda call: FakeResponse(200, claim_body()),
        bridge_handler=lambda call: FakeResponse(200, BRIDGE_BODY),
    )

    body = worker.call_bridge(RAW_INPUT)

    assert body == BRIDGE_BODY, "the bridge body must be transported verbatim, key for key"
    assert set(body) == set(BRIDGE_BODY)
    assert body["model"] == BRIDGE_BODY["model"], "device-reported model identity is not rewritten"

    assert len(bridge.calls) == 1, f"expected one bridge request, got {len(bridge.calls)}"
    call = bridge.calls[0]
    assert call["url"] == f"{BRIDGE_BASE}/interpret", "the documented /interpret path is tried first"
    assert call["json"] == {"text": RAW_INPUT}, "the bridge receives the server's raw input verbatim"
    assert call["headers"][BRIDGE_KEY_HEADER] == BRIDGE_KEY
    assert API_KEY_HEADER not in call["headers"], "the tenant key must never reach the model bridge"


def test_submits_full_body() -> None:
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE)

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job

    envelope = worker.submit_result(job, BRIDGE_BODY)

    assert envelope == RESULT_ENVELOPE, "the server's envelope is returned unchanged"

    submits = only(api.calls, "/result")
    assert len(submits) == 1, f"expected exactly one submission, got {len(submits)}"
    payload = submits[0]["json"]
    assert payload["bridge_body"] == BRIDGE_BODY, "the COMPLETE bridge body must be submitted"
    assert payload["lease"] == {"owner": WORKER_ID, "generation": 1}, "the lease proof is required"
    assert payload["request_id"] == REQUEST_ID, "correlation id is carried, never used as input"
    assert BRIDGE_KEY_HEADER not in submits[0]["headers"], "the bridge key must never reach the API"


# ---------------------------------------------------------------------------
# 6, 15  Fencing on submission
# ---------------------------------------------------------------------------


def test_result_409_fences() -> None:
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(409, error_body("LEASE_LOST", "the lease was lost before commit"))

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job

    exc = expect_raises(LeaseLost, lambda: worker.submit_result(job, BRIDGE_BODY))
    assert "LEASE_LOST" in str(exc), str(exc)
    assert job.state.is_lost, "a 409 must mark the lease lost locally too"
    assert "409" in (job.state.lost_reason or "") or "LEASE_LOST" in (job.state.lost_reason or "")
    assert len(only(api.calls, "/result")) == 1, "a fenced submission must never be retried"


def test_run_forever_is_bounded_and_backs_off() -> None:
    """The poll loop never spins: it paces itself, backs off, and can be stopped.

    ``sleep`` is injected, so this asserts the pacing POLICY (values and order)
    with no real waiting at all.
    """

    def config(**overrides):
        base = dict(
            api_base_url=API_BASE,
            api_key=API_KEY,
            worker_id=WORKER_ID,
            bridge_url=BRIDGE_BASE,
            heartbeat_tick_s=0.01,
            poll_interval_s=5.0,
            max_backoff_s=12.0,
            max_poll_interval_s=60.0,
        )
        base.update(overrides)
        return WorkerConfig(**base)

    # -- NO_WORK honours the server's hint, clamped to a bounded window -------
    for hint, expected in [(3.0, 3.0), (10_000.0, 60.0), (None, 5.0), (0, 5.0)]:
        sleeps = []
        body = no_work_body(hint) if hint is not None else {
            "api_version": "v1", "status": "NO_WORK", "job": None
        }
        worker, api, _bridge = make_worker(
            lambda call, body=body: FakeResponse(200, body),
            config=config(),
            sleep=sleeps.append,
        )
        completed = worker.run_forever(max_polls=2)
        assert completed == 0
        assert len(api.calls) == 2, f"max_polls must bound the loop (got {len(api.calls)})"
        assert sleeps == [expected, expected], f"hint {hint}: slept {sleeps}, expected {expected}"
        assert all(0 < s <= 60.0 for s in sleeps), "every wait must be positive and bounded"

    # -- transport failures back off exponentially, up to the cap, then reset --
    sleeps = []
    calls = {"n": 0}

    def flaky(call):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise requests.ConnectionError("api down")
        return FakeResponse(200, no_work_body(3.0))

    worker, api, _bridge = make_worker(flaky, config=config(), sleep=sleeps.append)
    worker.run_forever(max_polls=6)
    # Three failures: 5 → 10 → 12 (the cap). The successful fourth claim resets
    # the backoff, and the server's NO_WORK hint (3s) paces the rest.
    assert sleeps == [5.0, 10.0, 12.0, 3.0, 3.0, 3.0], f"unexpected backoff sequence: {sleeps}"
    assert sleeps[0] < sleeps[1] and sleeps[1] < sleeps[2], "backoff must grow, never shrink"
    assert max(sleeps) <= 12.0, "backoff must stay under max_backoff_s"
    assert len(api.calls) == 6, "max_polls must bound the loop"

    # -- a stop request ends the loop even with no jobs and no max_polls ------
    holder = {}
    stopped = []

    def stop_on_sleep(seconds):
        stopped.append(seconds)
        holder["worker"].stop()

    worker, api, _bridge = make_worker(
        lambda call: FakeResponse(200, no_work_body(1.0)),
        config=config(),
        sleep=stop_on_sleep,
    )
    holder["worker"] = worker
    assert worker.run_forever() == 0, "stop() must end an unbounded poll loop"
    assert len(stopped) == 1 and len(api.calls) == 1, "the loop must exit on the first stop signal"


def test_result_409_becomes_lease_lost() -> None:
    """End to end through process_job: one submission, then the candidate dies."""

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=30.0, renew_every_seconds=10.0))
        return FakeResponse(409, error_body("LEASE_LOST", "stale worker"))

    worker, api, bridge = make_worker(handler)
    job = worker.claim().job

    expect_raises(LeaseLost, lambda: worker.process_job(job))

    submits = only(api.calls, "/result")
    assert len(submits) == 1, f"the fence must stop the second attempt (got {len(submits)})"
    assert submits[0]["json"]["bridge_body"] == BRIDGE_BODY
    assert len(bridge.calls) == 1, "the bridge is called once per claimed job"
    assert job.state.is_lost and worker.stop_event.is_set(), "the heartbeat was stopped and the lease lost"
    assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())


# ---------------------------------------------------------------------------
# 7, 17  Fail-closed decoding
# ---------------------------------------------------------------------------


def test_bad_bridge_not_submitted() -> None:
    bad_bodies = [
        ("non-object json", FakeResponse(200, text="[1, 2, 3]")),
        ("not json at all", FakeResponse(200, text="<html>gateway</html>")),
        ("oversized", FakeResponse(200, text=json.dumps({"pad": "x" * 70000}))),
        ("empty body", FakeResponse(200, text="")),
    ]
    for label, response in bad_bodies:
        # The BAD response is served by the bridge; the API keeps its normal
        # answer, so any submission would be visible if one happened.
        def handler(call):
            if call["url"].endswith("/claim"):
                return FakeResponse(200, claim_body())
            return FakeResponse(200, RESULT_ENVELOPE)

        worker, api, _bridge = make_worker(
            handler,
            bridge_handler=lambda call, response=response: response,
        )
        job = worker.claim().job
        expect_raises(BridgeResponseError, lambda: worker.process_job(job))
        assert only(api.calls, "/result") == [], f"{label}: nothing may be submitted"
        # An unusable CANDIDATE is not a lost LEASE: the worker still holds the
        # lease (it simply has nothing to submit), so the job is reclaimed only
        # when the server-side lease expires.
        assert job.state.is_lost is False, f"{label}: the lease must not be declared lost"


def test_response_size_and_type_validation() -> None:
    # Decode guards.
    worker, _api, _bridge = make_worker(
        lambda call: FakeResponse(200, no_work_body()),
        bridge_handler=lambda call: FakeResponse(200, BRIDGE_BODY),
    )
    assert worker.call_bridge(RAW_INPUT) == BRIDGE_BODY

    for label, response in [
        ("json array", FakeResponse(200, text="[]")),
        ("json scalar", FakeResponse(200, text="7")),
        ("oversized", FakeResponse(200, text=json.dumps({"pad": "y" * 66000}))),
        ("unparseable", FakeResponse(200, text="{not json")),
    ]:
        bad_worker, _a, _b = make_worker(
            lambda call: FakeResponse(200, no_work_body()),
            bridge_handler=lambda call, response=response: response,
        )
        expect_raises(BridgeResponseError, lambda w=bad_worker: w.call_bridge(RAW_INPUT))

    # Submit guards: a non-object body, and a body over the server's 64 KiB cap.
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE)

    guard_worker, api, _bridge2 = make_worker(handler)
    job = guard_worker.claim().job
    expect_raises(BridgeResponseError, lambda: guard_worker.submit_result(job, ["not", "an", "object"]))
    expect_raises(
        BridgeResponseError,
        lambda: guard_worker.submit_result(job, {"interpretation_text": "z" * 70000}),
    )
    assert only(api.calls, "/result") == [], "an invalid candidate must not reach the network"


# ---------------------------------------------------------------------------
# 8-10, 12-13  Heartbeat and renewal proof
# ---------------------------------------------------------------------------


def test_real_heartbeat_409_marks_lease_lost() -> None:
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=0.4, renew_every_seconds=0.05))
        return FakeResponse(409, error_body("LEASE_LOST", "the lease is no longer held"))

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job
    heartbeat = worker.start_heartbeat(job.state)
    try:
        assert job.state.lost_event.wait(3.0), "the heartbeat never reported the lost lease"
        assert job.state.is_lost
        assert "LEASE_LOST" in (job.state.lost_reason or ""), job.state.lost_reason
        assert len(only(api.calls, "/lease")) >= 1, "the heartbeat must actually renew"
    finally:
        worker.stop_heartbeat(heartbeat)
    assert not heartbeat.is_alive()


def test_real_heartbeat_accepts_matching_renewal() -> None:
    renewed = threading.Event()

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=5.0, renew_every_seconds=0.05))
        renewed.set()
        return FakeResponse(200, renewal_body(owner=WORKER_ID, generation=1, lease_seconds=5.0))

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job
    deadline_before = job.state.deadline
    heartbeat = worker.start_heartbeat(job.state)
    try:
        assert renewed.wait(3.0), "the heartbeat never attempted a renewal"
        assert wait_until(lambda: job.state.renewals >= 1), "the confirmed renewal was not recorded"
        assert job.state.is_lost is False, "a matching renewal must NOT lose the lease"
        renews = only(api.calls, "/lease")
        assert renews, "expected a renewal request"
        assert renews[0]["json"] == {"lease": {"owner": WORKER_ID, "generation": 1}}
        assert job.state.deadline >= deadline_before, "a confirmed renewal extends the deadline"
    finally:
        worker.stop_heartbeat(heartbeat)
    assert not heartbeat.is_alive()


def test_real_heartbeat_rejects_wrong_generation() -> None:
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=0.4, renew_every_seconds=0.05))
        # 200 OK, but the proof is not ours: generation 99 belongs to someone else.
        return FakeResponse(200, renewal_body(owner=WORKER_ID, generation=99))

    worker, _api, _bridge = make_worker(handler)
    job = worker.claim().job
    heartbeat = worker.start_heartbeat(job.state)
    try:
        assert job.state.lost_event.wait(3.0), "a foreign renewal proof must lose the lease"
        assert "proof does not match" in (job.state.lost_reason or ""), job.state.lost_reason
    finally:
        worker.stop_heartbeat(heartbeat)
    assert not heartbeat.is_alive()


def test_renewal_transport_failures_lose_the_lease_at_the_deadline() -> None:
    """Failures before the confirmed deadline are tolerated; past it, not."""
    clock = FakeClock(0.0)
    attempts = [0]

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=10.0, renew_every_seconds=1.0))
        attempts[0] += 1
        raise requests.ConnectionError("network down")

    worker, _api, _bridge = make_worker(handler, now=clock, tick=0.005)
    job = worker.claim().job
    heartbeat = worker.start_heartbeat(job.state)
    try:
        clock.advance(1.5)
        assert wait_until(lambda: attempts[0] >= 1), "the heartbeat never attempted a renewal"
        assert job.state.is_lost is False, "a failure BEFORE the deadline must not be treated as loss"

        clock.advance(20.0)
        assert job.state.lost_event.wait(3.0), "past the deadline the lease must be declared lost"
        assert "deadline" in (job.state.lost_reason or ""), job.state.lost_reason
        assert job.state.renewal_failures >= 2, job.state.renewal_failures
    finally:
        worker.stop_heartbeat(heartbeat)
    assert not heartbeat.is_alive()


def test_wrong_renewal_proof_marks_lease_lost() -> None:
    def handler_factory(body):
        def handler(call):
            if call["url"].endswith("/claim"):
                return FakeResponse(200, claim_body(lease_seconds=0.4, renew_every_seconds=0.05))
            return FakeResponse(200, body)
        return handler

    # Direct: owner mismatch, generation mismatch, and a lease-less body.
    for label, body in [
        ("owner mismatch", renewal_body(owner="someone-else", generation=1)),
        ("generation mismatch", renewal_body(owner=WORKER_ID, generation=2)),
        ("no lease object", {"api_version": "v1", "renewed": True}),
    ]:
        worker, _api, _bridge = make_worker(handler_factory(body))
        job = worker.claim().job
        expect_raises(MalformedResponseError, lambda w=worker, j=job: w.renew_lease(j.state))

    # Through a real heartbeat: the worker must fail closed, not assume the lease.
    worker, _api, _bridge = make_worker(handler_factory(renewal_body(owner="someone-else", generation=1)))
    job = worker.claim().job
    heartbeat = worker.start_heartbeat(job.state)
    try:
        assert job.state.lost_event.wait(3.0), "an unverifiable renewal proof must lose the lease"
        assert "proof unusable" in (job.state.lost_reason or ""), job.state.lost_reason
    finally:
        worker.stop_heartbeat(heartbeat)
    assert not heartbeat.is_alive()


# ---------------------------------------------------------------------------
# 11, 14  Expiry and pre-submission loss
# ---------------------------------------------------------------------------


def test_expired_unrenewed_lease_prevents_submission() -> None:
    clock = FakeClock(0.0)

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=10.0, renew_every_seconds=5.0))
        return FakeResponse(200, RESULT_ENVELOPE)

    worker, api, _bridge = make_worker(handler, now=clock)
    job = worker.claim().job

    clock.advance(11.0)  # nobody renewed: the confirmed deadline has passed
    expect_raises(LeaseLost, lambda: worker.submit_result(job, BRIDGE_BODY))

    assert only(api.calls, "/result") == [], "an expired lease must not submit"
    assert job.state.is_lost, "expiry must be recorded as lease loss"
    assert "deadline" in (job.state.lost_reason or ""), job.state.lost_reason


def test_lease_lost_before_submission_discards_the_candidate() -> None:
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE)

    worker, api, bridge = make_worker(handler)
    job = worker.claim().job
    job.state.mark_lost("simulated fence loss")

    expect_raises(LeaseLost, lambda: worker.process_job(job))

    assert bridge.calls == [], "a lost lease must not spend model time"
    assert only(api.calls, "/result") == [], "a lost lease must not submit a candidate"


# ---------------------------------------------------------------------------
# 16  Shutdown
# ---------------------------------------------------------------------------


def test_blocked_renewal_and_shutdown() -> None:
    entered = threading.Event()
    holder = {}

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=30.0, renew_every_seconds=0.05))
        entered.set()
        # A renewal in flight that will not return on its own: the worker must
        # still shut down without waiting for it (the deadline never passes here).
        holder["worker"].stop_event.wait(10.0)
        raise requests.ConnectionError("renewal aborted by shutdown")

    worker, _api, _bridge = make_worker(handler)
    holder["worker"] = worker
    job = worker.claim().job
    heartbeat = worker.start_heartbeat(job.state)
    assert entered.wait(3.0), "the heartbeat never entered the renewal"

    started = time.monotonic()
    worker.stop()
    joined = worker.stop_heartbeat(heartbeat)
    elapsed = time.monotonic() - started

    assert joined, "the heartbeat thread did not exit"
    assert not heartbeat.is_alive(), "the heartbeat thread must be joined on shutdown"
    assert elapsed < 2.0, f"shutdown blocked for {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# 18  Endpoint validation
# ---------------------------------------------------------------------------


def test_endpoint_validation() -> None:
    valid = f"{API_BASE}/v1/worker/jobs/{JOB_ID}/lease"
    assert resolve_lease_endpoint(API_BASE, f"/v1/worker/jobs/{JOB_ID}/lease", JOB_ID, "/lease") == valid

    cases = [
        ("absolute url", "https://attacker.example/v1/worker/jobs/job-1/lease"),
        ("protocol-relative", "//attacker.example/v1/worker/jobs/job-1/lease"),
        ("query component", "/v1/worker/jobs/job-1/lease?owner=x"),
        ("fragment component", "/v1/worker/jobs/job-1/lease#frag"),
        ("path traversal", "/v1/worker/jobs/../../v1/admin/lease"),
        ("encoded traversal", "/v1/worker/jobs/%2e%2e/lease"),
        ("mismatched job id", "/v1/worker/jobs/someone-elses-job/lease"),
        ("wrong suffix", "/v1/worker/jobs/job-1/result"),
        ("no leading slash", "v1/worker/jobs/job-1/lease"),
        ("empty", ""),
    ]
    for label, candidate in cases:
        expect_raises(
            (UnsafeEndpointError, MalformedResponseError),
            lambda c=candidate: resolve_lease_endpoint(API_BASE, c, JOB_ID, "/lease"),
        )

    # The same guard is applied to the lease the claim response actually sent.
    def handler(call):
        return FakeResponse(200, claim_body(result_url="/v1/worker/jobs/job-1/../job-1/result"))

    worker, _api, _bridge = make_worker(handler)
    expect_raises(UnsafeEndpointError, worker.claim)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


TESTS = (
    test_no_work_and_headers,
    test_claimed_job,
    test_reject_unsafe_endpoint,
    test_bridge_full_body,
    test_submits_full_body,
    test_result_409_fences,
    test_bad_bridge_not_submitted,
    test_real_heartbeat_409_marks_lease_lost,
    test_real_heartbeat_accepts_matching_renewal,
    test_real_heartbeat_rejects_wrong_generation,
    test_expired_unrenewed_lease_prevents_submission,
    test_renewal_transport_failures_lose_the_lease_at_the_deadline,
    test_wrong_renewal_proof_marks_lease_lost,
    test_lease_lost_before_submission_discards_the_candidate,
    test_result_409_becomes_lease_lost,
    test_blocked_renewal_and_shutdown,
    test_response_size_and_type_validation,
    test_endpoint_validation,
    test_run_forever_is_bounded_and_backs_off,
)


def main() -> int:
    print("=" * 78)
    print("Outbound inference worker — Stage 2 suite (platrixa_outbound_inference_worker)")
    print("=" * 78)
    passed = 0
    failures = []
    for fn in TESTS:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - a failing test is data, not a crash
            failures.append((fn.__name__, exc))
            print(f"  [FAIL] {fn.__name__} — {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  [PASS] {fn.__name__}")

    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{len(TESTS)} worker tests passed")
    for name, exc in failures:
        print(f"  FAIL {name} — {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
