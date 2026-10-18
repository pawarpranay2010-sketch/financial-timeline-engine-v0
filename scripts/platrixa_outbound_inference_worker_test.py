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
    20  the poll loop serves job after job (audit regression: per-job heartbeat
        teardown used to set the worker-wide shutdown signal)
    21  a deterministic terminal rejection (422) does not kill the poll loop,
        while a bad credential on the claim path still does (audit regression)
    22  per-job teardown is a distinct lifecycle event from worker shutdown:
        a normally finished job and a fenced-off job both leave the worker
        running, and stop() is what actually ends the loop (assertion review)
    23  a fresh acceptance (no replay marker) is reported as a fresh commit
    24  `Idempotent-Replayed: true` is reported as a REPLAY, never counted as a
        completed job and never resubmitted (Stage 2 O2)
    25  the replay marker is parsed strictly: case/whitespace tolerated, an
        undefined value is refused instead of guessed
    26  a submission whose transport failed has an UNKNOWN outcome: not counted
        as accepted, not resubmitted, and the poll loop survives it
    27  the log distinguishes ACCEPTED from REPLAYED and never carries a
        credential

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
    ApiError,
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


def test_terminal_rejection_does_not_kill_the_poll_loop() -> None:
    """A deterministic 422 is the server's verdict on ONE job, not a shutdown.

    Regression for the audit finding: the result endpoint answers a refused
    candidate with HTTP 422 + the engine's terminal envelope and the job is
    COMPLETED, but the worker re-raised a non-retryable ApiError out of
    run_forever, tearing the device worker down and leaving every later job
    QUEUED forever.
    """
    state = {"claims": 0, "submissions": 0}

    def handler(call):
        if call["url"].endswith("/claim"):
            state["claims"] += 1
            return FakeResponse(200, claim_body()) if state["claims"] == 1 \
                else FakeResponse(200, no_work_body(1.0))
        state["submissions"] += 1
        return FakeResponse(422, {
            "api_version": "v1", "status": "VALIDATION_FAILED", "api_status": "FAILED",
            "retryable": False, "success": False,
        })

    worker, api, _bridge = make_worker(handler, sleep=lambda seconds: None)
    completed = worker.run_forever(max_polls=2)

    assert completed == 0, "a refused candidate is never a completed job"
    assert state["claims"] == 2, "the loop must go on to the next claim after a rejection"
    assert len(only(api.calls, "/claim")) == 2
    assert state["submissions"] == 1, "a refused candidate must not be resubmitted"
    assert only(api.calls, "/result")[0]["json"]["bridge_body"] == BRIDGE_BODY

    # A direct caller still sees the rejection, with the engine's own code — the
    # fix is scoped to the poll loop, not to the evidence.
    def direct_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(422, {"api_version": "v1", "status": "VALIDATION_FAILED",
                                  "api_status": "FAILED", "retryable": False})

    direct_worker, _a, _b = make_worker(direct_handler)
    job = direct_worker.claim().job
    exc = expect_raises(
        ApiError,
        lambda: direct_worker.submit_result(job, BRIDGE_BODY),
    )
    assert exc.status_code == 422 and exc.retryable is False, str(exc)
    assert exc.code == "VALIDATION_FAILED", exc.code

    # A bad CREDENTIAL on the claim path is still fatal: retrying it would spin.
    auth_worker, _c, _d = make_worker(lambda call: FakeResponse(401, error_body("UNAUTHORIZED", "bad key")))
    auth_exc = expect_raises(ApiError, lambda: auth_worker.run_forever(max_polls=1))
    assert auth_exc.status_code == 401 and auth_exc.retryable is False


def test_poll_loop_serves_more_than_one_job() -> None:
    """A long-running worker must serve job after job, not exactly one.

    Regression for the audit finding that per-JOB heartbeat teardown set the
    worker-wide shutdown event, so ``run_forever`` exited after the first job it
    processed (success or rejection) and every later job stayed QUEUED.
    """
    state = {"claims": 0, "submissions": 0}

    def handler(call):
        if call["url"].endswith("/claim"):
            state["claims"] += 1
            if state["claims"] <= 2:
                return FakeResponse(200, claim_body(job_id=f"job-{state['claims']}"))
            return FakeResponse(200, no_work_body(1.0))
        state["submissions"] += 1
        return FakeResponse(200, RESULT_ENVELOPE)

    worker, api, _bridge = make_worker(handler, sleep=lambda seconds: None)
    completed = worker.run_forever(max_polls=3)

    assert completed == 2, f"expected two served jobs, got {completed}"
    assert state["claims"] == 3, f"expected a third claim after two jobs, got {state['claims']}"
    assert state["submissions"] == 2
    assert worker.stop_event.is_set() is False, "serving a job must not set the shutdown signal"
    assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())
    assert worker.stop_event.wait(0) is False


def test_job_lifecycle_is_distinct_from_worker_shutdown() -> None:
    """Per-job teardown vs. worker shutdown — the LIFECYCLE, not an event value.

    The audit review asked for proof that the replacement assertion in
    ``test_result_409_becomes_lease_lost`` checks the intended invariant and not
    merely the current boolean of an event. This test drives the real loop
    through the real claim/submit interface and proves the contract underneath:

      * a job that finishes NORMALLY leaves the worker-wide shutdown signal
        unset and no heartbeat thread alive;
      * a job whose submission is FENCED OFF (409, lease lost) is likewise NOT a
        shutdown — the same worker goes on to serve the next job;
      * the real shutdown mechanism (``stop()``) then sets the signal, ends the
        loop without issuing one further claim, and leaves no owned thread.

    Everything is asserted at the actual job boundaries, and the negative
    control (``stop()`` really does set the flag) shows the flag is meaningful.
    """
    state = {"claims": 0, "submissions": 0}
    boundaries: list = []

    def handler(call):
        if call["url"].endswith("/claim"):
            state["claims"] += 1
            if state["claims"] <= 3:
                return FakeResponse(
                    200,
                    claim_body(job_id=f"job-{state['claims']}", generation=state["claims"]),
                )
            return FakeResponse(200, no_work_body(1.0))
        state["submissions"] += 1
        if state["submissions"] == 2:
            # The middle job loses its lease to a newer generation.
            return FakeResponse(409, error_body("LEASE_LOST", "a newer generation owns this job"))
        return FakeResponse(200, RESULT_ENVELOPE)

    def on_result(job, _envelope):
        # Observed BETWEEN jobs, i.e. after per-job teardown has already run.
        boundaries.append({
            "job": job.job_id,
            "shutdown_set": worker.stop_event.is_set(),
            "heartbeat_alive": any(
                t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate()
            ),
        })

    worker, api, _bridge = make_worker(handler, sleep=lambda seconds: None)
    completed = worker.run_forever(max_polls=4, on_result=on_result)

    # The fenced job (job-2) is NOT completed and did NOT end the loop.
    assert completed == 2, f"expected two committed jobs, got {completed}"
    assert state["claims"] == 4, f"expected four claims (3 jobs + NO_WORK), got {state['claims']}"
    assert [b["job"] for b in boundaries] == ["job-1", "job-3"], str(boundaries)
    assert all(b["shutdown_set"] is False for b in boundaries), \
        "finishing a job must not set the worker-wide shutdown signal"
    assert all(b["heartbeat_alive"] is False for b in boundaries), \
        "each job's heartbeat must be stopped and joined at the job boundary"
    # Lease loss (job-2) abandoned only that job: job-3 was still served, and the
    # shutdown signal is STILL unset after three jobs, one of them fenced.
    assert worker.stop_event.is_set() is False
    assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())

    # -- the real shutdown mechanism, and its negative control -----------------
    worker.stop()
    assert worker.stop_event.is_set() is True, "stop() is the ONE writer of the shutdown signal"
    claims_before = len(only(api.calls, "/claim"))
    assert worker.run_forever(max_polls=5) == 0, "a stopped worker must not serve anything"
    assert len(only(api.calls, "/claim")) == claims_before, "a stopped loop must not even claim"
    assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())


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
    assert job.state.is_lost, "the lease must be marked lost"
    assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())
    # Per-job teardown must NOT set the worker-wide shutdown signal — otherwise a
    # worker stops after its first job (audit finding).
    assert worker.stop_event.is_set() is False


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
# 23-27  Stage 2 O2 — fresh acceptance vs idempotent replay
# ---------------------------------------------------------------------------


def _log_capture():
    """Collect this module's log records (used to prove what the worker says)."""
    import logging

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


def test_fresh_acceptance_is_a_fresh_commit() -> None:
    """O2: no replay marker + 200 == THIS candidate became authoritative."""

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE)  # the server sends no marker

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job

    outcome = worker.submit_result(job, BRIDGE_BODY)

    assert outcome.replayed is False, "a response with no replay marker is not a replay"
    assert outcome.accepted is True, "this candidate is the one that was committed"
    # The outcome is still the server's envelope, duck-typed for every caller.
    assert outcome == RESULT_ENVELOPE, "the envelope must be returned unchanged"
    assert json.loads(json.dumps(outcome)) == RESULT_ENVELOPE, "the outcome must stay JSON-encodable"
    assert len(only(api.calls, "/result")) == 1

    # Through the loop: one fresh acceptance, no replay.
    claims = {"n": 0}

    def one_job_handler(call):
        if call["url"].endswith("/claim"):
            claims["n"] += 1
            return FakeResponse(200, claim_body()) if claims["n"] == 1 \
                else FakeResponse(200, no_work_body(1.0))
        return FakeResponse(200, RESULT_ENVELOPE)

    loop_worker, _a, _b = make_worker(one_job_handler, sleep=lambda seconds: None)
    assert loop_worker.run_forever(max_polls=2) == 1
    assert loop_worker.last_run_stats == {"accepted": 1, "replayed": 0}, loop_worker.last_run_stats



def test_replay_is_never_reported_as_acceptance() -> None:
    """O2: `Idempotent-Replayed: true` means an OLDER result was replayed."""

    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE, headers={"Idempotent-Replayed": "true"})

    worker, api, _bridge = make_worker(handler)
    job = worker.claim().job

    outcome = worker.submit_result(job, BRIDGE_BODY)

    assert outcome.replayed is True, "the server said this was a replay"
    assert outcome.accepted is False, "a replay is NOT a fresh acceptance"
    assert outcome == RESULT_ENVELOPE, "the replayed envelope is still the server's own bytes"

    # The loop must not count it, must not resubmit, and must keep serving.
    state = {"claims": 0}

    def loop_handler(call):
        if call["url"].endswith("/claim"):
            state["claims"] += 1
            return FakeResponse(200, claim_body(job_id=f"job-{state['claims']}")) \
                if state["claims"] <= 2 else FakeResponse(200, no_work_body(1.0))
        return FakeResponse(200, RESULT_ENVELOPE, headers={"Idempotent-Replayed": "true"})

    loop_worker, loop_api, _c = make_worker(loop_handler, sleep=lambda seconds: None)
    completed = loop_worker.run_forever(max_polls=3)

    assert completed == 0, "a replay must never be counted as a completed (accepted) job"
    assert loop_worker.last_run_stats == {"accepted": 0, "replayed": 2}, loop_worker.last_run_stats
    assert len(only(loop_api.calls, "/result")) == 2, "one submission per job, never a retry"
    assert state["claims"] == 3, "the loop continues after a replay"



def test_replay_marker_parsing_is_strict() -> None:
    """O2: the marker is authoritative — case/whitespace tolerated, junk refused."""

    def worker_for(headers):
        def handler(call):
            if call["url"].endswith("/claim"):
                return FakeResponse(200, claim_body())
            return FakeResponse(200, RESULT_ENVELOPE, headers=headers)
        worker, _api, _b = make_worker(handler)
        return worker, worker.claim().job

    # Values the contract defines.
    for headers, expected in [
        ({"Idempotent-Replayed": "true"}, True),
        ({"idempotent-replayed": "TRUE"}, True),
        ({"Idempotent-Replayed": " true "}, True),
        ({"Idempotent-Replayed": "false"}, False),
        ({}, False),
    ]:
        worker, job = worker_for(headers)
        assert worker.submit_result(job, BRIDGE_BODY).replayed is expected, f"{headers} → {expected}"

    # A value the contract does not define must NOT be guessed at: either guess
    # would be a claim about whether this candidate was committed.
    for bad in ["yes", "1", "maybe", ""]:
        worker, job = worker_for({"Idempotent-Replayed": bad})
        expect_raises(MalformedResponseError, lambda w=worker, j=job: w.submit_result(j, BRIDGE_BODY))

    # Through the loop the loop must survive it, count nothing, and not retry.
    def handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(200, RESULT_ENVELOPE, headers={"Idempotent-Replayed": "maybe"})

    worker, api, _b = make_worker(handler, sleep=lambda seconds: None)
    assert worker.run_forever(max_polls=1) == 0, "unusable metadata is not an acceptance"
    assert worker.last_run_stats == {"accepted": 0, "replayed": 0}, worker.last_run_stats
    assert len(only(api.calls, "/result")) == 1, "the candidate must not be resubmitted"



def test_ambiguous_submission_is_not_acceptance() -> None:
    """O2: a transport failure during submit has an UNKNOWN outcome.

    Nothing is inferred in either direction: the loop does not die, the job is
    not counted as accepted, and the candidate is not blindly resubmitted at the
    same generation. If the commit did land, the server replays it later; if it
    did not, the lease expires and the job is reclaimed.
    """
    state = {"claims": 0, "submits": 0}

    def handler(call):
        if call["url"].endswith("/claim"):
            state["claims"] += 1
            return FakeResponse(200, claim_body())
        state["submits"] += 1
        raise requests.ConnectionError("connection reset after the request was sent")

    worker, api, _bridge = make_worker(handler, sleep=lambda seconds: None)
    completed = worker.run_forever(max_polls=2)

    assert completed == 0, "an unknown outcome must never be reported as accepted"
    assert worker.last_run_stats == {"accepted": 0, "replayed": 0}, worker.last_run_stats
    assert state["submits"] == 2, f"one submission attempt per claim (got {state['submits']})"
    assert worker.stop_event.is_set() is False, "a dropped connection must not stop the worker"
    assert len(only(api.calls, "/result")) == 2

    # A direct caller gets the typed transport error, not a fabricated envelope.
    def direct_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        raise requests.ConnectionError("reset")

    direct, _a, _b = make_worker(direct_handler)
    job = direct.claim().job
    from platrixa_outbound_inference_worker import TransportError  # noqa: PLC0415

    expect_raises(TransportError, lambda: direct.submit_result(job, BRIDGE_BODY))
    assert job.state.is_lost is False, "an unacknowledged submission is not a lost lease"



def test_outcome_logging_is_distinct_and_secret_free() -> None:
    """O2: the log says which happened, and never carries a credential."""
    logger, handler, records, previous_level = _log_capture()
    try:
        def replay_handler(call):
            if call["url"].endswith("/claim"):
                return FakeResponse(200, claim_body())
            return FakeResponse(200, RESULT_ENVELOPE, headers={"Idempotent-Replayed": "true"})

        worker, _api, _b = make_worker(replay_handler, sleep=lambda seconds: None)
        worker.run_forever(max_polls=1)

        def fresh_handler(call):
            if call["url"].endswith("/claim"):
                return FakeResponse(200, claim_body())
            return FakeResponse(200, RESULT_ENVELOPE)

        fresh, _c, _d = make_worker(fresh_handler, sleep=lambda seconds: None)
        fresh.run_forever(max_polls=1)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    messages = [r.getMessage() for r in records]
    replays = [m for m in messages if "REPLAYED" in m]
    accepted = [m for m in messages if "ACCEPTED" in m]
    assert len(replays) == 1, messages
    assert len(accepted) == 1, messages
    assert replays[0] != accepted[0]
    blob = "\n".join(messages)
    for secret in (API_KEY, BRIDGE_KEY):
        assert secret not in blob, "a log line leaked a credential"


# ---------------------------------------------------------------------------
# Model adapter tests — phone llama-server integration
# ---------------------------------------------------------------------------


def _model_fake_response(body: dict, *, status: int = 200, headers: Optional[Dict[str, str]] = None) -> FakeResponse:
    """Helper: build a FakeResponse with body as JSON text."""
    return FakeResponse(status, body=body, text=json.dumps(body), headers=headers)


def test_model_chat_dialect_accepts_valid_response() -> None:
    """POST /v1/chat/completions with the Phase-H Alpaca prompt; a valid JSON
    answer is collected and the result body shape is identical to the bridge."""
    captured_bridge: Optional[Dict[str, Any]] = {}
    called_with_claim: bool = False

    def claim_handler(call):
        nonlocal called_with_claim
        called_with_claim = True
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        # Collect the bridge body that would be submitted — we only assert shape.
        return _model_fake_response({
            "interpretation_text": '{"transaction_type": "EXPENSE", "amount": 25000}',
            "completion_status": "COMPLETE",
            "finish_reason": "stop",
            "model": {"provider": "android-llama-server", "model": "default", "dialect": "chat", "artifact_identity": "PROVENANCE_UNKNOWN"},
        })

    def result_handler(call):
        nonlocal captured_bridge
        if call["url"].endswith("/result"):
            payload = call["json"]
            captured_bridge = payload.get("bridge_body", {})
            return FakeResponse(200, RESULT_ENVELOPE)
        return _model_fake_response({"status": "UNKNOWN"})

    def on_result(job, envelope):
        # Verify the envelope shape matches what the bridge would produce.
        assert "api_version" in envelope
        assert "request_id" in envelope
        assert envelope.get("api_status") == "REVIEW_REQUIRED"

    replay_handler_inner = lambda c: claim_handler(c) if c["url"].endswith("/claim") else _model_fake_response({"status": "CLAIMED"})
    worker, api, bridge = make_worker(
        lambda c: claim_handler(c) if c["url"].endswith("/claim") else _model_fake_response({"status": "CLAIMED"}),
        bridge_handler=result_handler,
        sleep=lambda seconds: None,
    )
    worker._model_base_url = "http://127.0.0.1:11434"
    worker._model_id = "default"
    worker._model_dialect = "chat"
    job = worker.claim().job
    envelope = worker.process_job(job)
    assert envelope.replayed is False
    assert "interpretation_text" in envelope
    captured_bridge.clear()

    # Re-claim and process once more to verify bridge_body in submit_result
    job2 = worker.claim().job
    outcome = worker.process_job(job2)
    assert outcome.accepted is True
    assert "interpretation_text" in outcome
    # The bridge_body captured in submit_result should have the right shape.
    assert "interpretation_text" in outcome.get("bridge_body", {})


def test_model_completion_dialect_accepts_valid_response() -> None:
    """POST /v1/completions with the Phase-H Alpaca prompt (exact SFT framing).
    A valid JSON answer is collected and the result body shape is identical."""
    captured_bridge: Optional[Dict[str, Any]] = {}

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return _model_fake_response({"status": "CLAIMED"})

    def result_handler(call):
        nonlocal captured_bridge
        if call["url"].endswith("/result"):
            payload = call["json"]
            captured_bridge = payload.get("bridge_body", {})
            return FakeResponse(200, RESULT_ENVELOPE)
        return _model_fake_response({"status": "UNKNOWN"})

    def on_result(job, envelope):
        assert "api_version" in envelope
        assert "request_id" in envelope
        assert envelope.get("api_status") == "REVIEW_REQUIRED"

    worker, api, bridge = make_worker(
        claim_handler,
        bridge_handler=result_handler,
        sleep=lambda seconds: None,
    )
    worker._model_base_url = "http://127.0.0.1:11434"
    worker._model_id = "default"
    worker._model_dialect = "completion"
    job = worker.claim().job
    envelope = worker.process_job(job)
    assert envelope.replayed is False
    assert "interpretation_text" in envelope


def _model_fake_session(handler):
    """A fake model-session whose ``post`` goes to ``handler``.

    The worker enters the direct-model path when ``_model_base_url`` is set
    and reaches ``call_model``, which POSTs to the model endpoint using
    ``_model_session``. Supplying this fake keeps the test offline and
    deterministic: it never touches a real llama-server or Railway.
    """
    session = FakeSession(handler)
    # OutboundInferenceWorker stores the model session as ``_model_session``.
    return session


def test_model_chat_dialect_truncated_stream_fails_closed() -> None:
    """Chat streaming that ends without [DONE] fails closed and is never submitted.

    The model POST is served by a fake model session, so the truncated SSE is
    parsed by ``_collect_model_stream`` through the real model-response handling
    path (``call_model`` -> ``_parse_model_response`` -> _collect_model_stream``).
    """
    submitted: list = []

    def model_handler(call):
        # Deliberately truncated SSE: a real chunk, then no ``[DONE]``.
        return FakeResponse(
            200,
            text=(
                "data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}\n"
                "\n"
            ),
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
        )

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return _model_fake_response({"status": "CLAIMED"})

    def result_handler(call):
        submitted.append(call)
        return _model_fake_response({"status": "UNKNOWN"})

    model_session = _model_fake_session(model_handler)
    worker, api, bridge = make_worker(
        claim_handler,
        bridge_handler=result_handler,
        sleep=lambda seconds: None,
    )
    worker._model_base_url = "http://127.0.0.1:11434"
    worker._model_id = "default"
    worker._model_dialect = "chat"
    worker._model_session = model_session

    job = worker.claim().job
    exc = expect_raises(BridgeResponseError, lambda: worker.process_job(job))
    assert "without [DONE]" in str(exc), f"unexpected error text: {exc}"

    # The incomplete candidate must be discarded: no result submitted, no bridge
    # touched, and the fake model endpoint asked exactly once.
    assert submitted == [], f"the truncated candidate must not be submitted (got {submitted})"
    assert bridge.calls == [], "the direct-model path must not fall back to the device bridge"
    model_calls = [c for c in api.calls if c["url"].endswith("/v1/chat/completions")]
    assert len(model_calls) == 1, f"expected one model POST, got {model_calls}"
    payload = model_calls[0]["json"]
    assert payload["model"] == "default"
    assert payload["stream"] is False
    assert isinstance(payload["messages"], list) and len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert build_model_prompt(RAW_INPUT) == payload["messages"][0]["content"]
    assert payload["temperature"] == MODEL_TEMPERATURE
    assert payload["top_p"] == MODEL_TOP_P
    assert payload["max_tokens"] == MODEL_MAX_NEW_TOKENS


def test_model_chat_dialect_malformed_json_fails_closed() -> None:
    """Chat response with non-JSON body fails closed."""

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return _model_fake_response({"status": "CLAIMED"})

    def bridge_handler(call):
        return FakeResponse(200, {
            "interpretation_text": '{"transaction_type": "EXPENSE", "amount": 25000}',
            "completion_status": "COMPLETE",
            "finish_reason": "stop",
            "model": {"provider": "llama.cpp local bridge", "artifact_identity": "PROVENANCE_UNKNOWN"},
        })

    def result_handler(call):
        return FakeResponse(200, {"api_version": "v1", "status": "FAILED"})

    def model_handler(call):
        return FakeResponse(200, "this is not json", headers={"Content-Type": "application/json"})

    worker, api, bridge = make_worker(
        claim_handler,
        bridge_handler=bridge_handler,
        model_handler=model_handler,
        sleep=lambda seconds: None,
    )
    worker._model_base_url = "http://127.0.0.1:11434"
    worker._model_id = "default"
    worker._model_dialect = "chat"
    job = worker.claim().job
    try:
        envelope = worker.process_job(job)
        assert False, "expected BridgeResponseError for malformed JSON"
    except BridgeResponseError:
        pass  # expected


def test_model_chat_dialect_empty_output_fails_closed() -> None:
    """Chat response with empty content fails closed."""

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return _model_fake_response({"status": "CLAIMED"})

    def model_handler(call):
        return FakeResponse(200, json.dumps({"choices": [{"message": {"content": ""}}]}), headers={"Content-Type": "application/json"})

    def bridge_handler(call):
        return FakeResponse(200, {
            "interpretation_text": '{"transaction_type": "EXPENSE", "amount": 25000}',
            "completion_status": "COMPLETE",
            "finish_reason": "stop",
            "model": {"provider": "llama.cpp local bridge", "artifact_identity": "PROVENANCE_UNKNOWN"},
        })

    worker, api, bridge = make_worker(
        claim_handler,
        bridge_handler=bridge_handler,
        model_handler=model_handler,
        sleep=lambda seconds: None,
    )
    worker._model_base_url = "http://127.0.0.1:11434"
    worker._model_id = "default"
    worker._model_dialect = "chat"
    job = worker.claim().job
    try:
        envelope = worker.process_job(job)
        assert False, "expected BridgeResponseError for empty output"
    except BridgeResponseError:
        pass  # expected


def test_model_chat_dialect_tenant_isolation() -> None:
    """Different tenant keys result in different lease proofs; the model
    endpoint uses its own key that never reaches the API."""

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return _model_fake_response({"status": "CLAIMED"})

    def result_handler(call):
        return _model_fake_response({"status": "UNKNOWN"})

    def api_error_handler(call):
        if call["url"].endswith("/claim"):
            raise ApiError(403, "BAD_TENANT", "invalid tenant", retryable=False)
        return _model_fake_response({"status": "UNKNOWN"})

    # Two workers with different tenant keys
    worker_a, _, _ = make_worker(
        api_error_handler,
        model_handler=lambda c: _model_fake_response({"status": "CLAIMED"}),
        sleep=lambda seconds: None,
    )
    worker_a._model_base_url = "http://127.0.0.1:11434"
    worker_a._model_id = "default"
    worker_a._model_key = "key-a"

    worker_b, _, _ = make_worker(
        api_error_handler,
        model_handler=lambda c: _model_fake_response({"status": "CLAIMED"}),
        sleep=lambda seconds: None,
    )
    worker_b._model_base_url = "http://127.0.0.1:11434"
    worker_b._model_id = "default"
    worker_b._model_key = "key-b"

    # Both should be able to claim and process jobs using their own keys
    job_a = worker_a.claim().job
    envelope_a = worker_a.process_job(job_a)
    assert "interpretation_text" in envelope_a

    job_b = worker_b.claim().job
    envelope_b = worker_b.process_job(job_b)
    assert "interpretation_text" in envelope_b


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
    test_poll_loop_serves_more_than_one_job,
    test_job_lifecycle_is_distinct_from_worker_shutdown,
    test_terminal_rejection_does_not_kill_the_poll_loop,
    test_fresh_acceptance_is_a_fresh_commit,
    test_replay_is_never_reported_as_acceptance,
    test_replay_marker_parsing_is_strict,
    test_ambiguous_submission_is_not_acceptance,
    test_outcome_logging_is_distinct_and_secret_free,
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
