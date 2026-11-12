#!/usr/bin/env python3
"""Regression: committed-result stats are recorded before invoking on_result.

Fix C (review finding): if on_result raises after the server has accepted the
result, last_run_stats / the run report must already reflect the server's
confirmed decision. The callback's exception still propagates. A replay is
never counted as a fresh acceptance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from platrixa_outbound_inference_worker import (  # noqa: E402
    MODEL_DIALECT_CHAT,
    OutboundInferenceWorker,
    WorkerConfig,
)

API_BASE = "https://api.example.test"
BRIDGE_BASE = "http://127.0.0.1:8081"
API_KEY = "plx_test_tenant_key_not_a_secret"
BRIDGE_KEY = "device-bridge-key-not-a-secret"
WORKER_ID = "device-a"
JOB_ID = "job-cb"
REQUEST_ID = "req-cb"

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
    def __init__(self, status_code: int, body=None, text=None, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        return json.loads(self.text)


class FakeSession:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        call = {"url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        return self._handler(call)


def claim_body(job_id=JOB_ID, owner=WORKER_ID, generation=1):
    return {
        "api_version": "v1",
        "status": "CLAIMED",
        "job": {
            "job_id": job_id,
            "request_id": REQUEST_ID,
            "raw_input": "purchased furniture from raj for rs.25000",
            "lease": {
                "owner": owner,
                "generation": generation,
                "lease_seconds": 120.0,
                "renew_every_seconds": 40.0,
                "renew_url": f"/v1/worker/jobs/{job_id}/lease",
                "result_url": f"/v1/worker/jobs/{job_id}/result",
            },
        },
    }


def no_work_body(retry_after=0.01):
    return {"api_version": "v1", "status": "NO_WORK", "job": None, "retry_after_seconds": retry_after}


def error_body(code: str, message: str = ""):
    return {"api_version": "v1", "error": {"code": code, "message": message or code, "request_id": REQUEST_ID, "api_status": "FAILED", "retryable": False}}


def make_worker(api_handler, bridge_handler=None):
    config = WorkerConfig(
        api_base_url=API_BASE,
        api_key=API_KEY,
        worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE,
        bridge_key=BRIDGE_KEY,
        model_dialect=MODEL_DIALECT_CHAT,
        heartbeat_tick_s=0.01,
        shutdown_timeout_s=5.0,
    )
    if bridge_handler is None:
        bridge_handler = lambda call: FakeResponse(200, BRIDGE_BODY)  # noqa: E731
    worker = OutboundInferenceWorker(
        config,
        session=FakeSession(api_handler),
        bridge_session=FakeSession(bridge_handler),
        now=lambda: 0.0,
        sleep=lambda _: None,
    )
    return worker


class CallbackBoom(RuntimeError):
    pass


def test_accepted_result_stats_survive_callback_failure():
    """A fresh acceptance is recorded in stats even if on_result raises."""
    def api(call):
        if call["url"].endswith("/claim"):
            if not getattr(api, "claimed", False):
                api.claimed = True
                return FakeResponse(200, claim_body())
            return FakeResponse(200, no_work_body())
        if call["url"].endswith("/result"):
            return FakeResponse(200, RESULT_ENVELOPE)  # fresh commit (no replay header)
        if call["url"].endswith("/lease"):
            return FakeResponse(200, {"api_version": "v1", "renewed": True, "job_id": JOB_ID, "lease": {"owner": WORKER_ID, "generation": 1, "lease_seconds": 120.0, "renew_every_seconds": 40.0}})
        raise AssertionError(f"unexpected call: {call['url']}")

    worker = make_worker(api)

    def boom(job, envelope):
        raise CallbackBoom("observer failed")

    raised = False
    try:
        worker.run_forever(max_polls=2, on_result=boom)
    except CallbackBoom:
        raised = True
    assert raised, "callback exception must propagate (not be swallowed)"

    stats = worker.last_run_stats
    report = worker.last_run_report
    assert stats["accepted"] == 1, f"accepted result must be reflected in stats despite callback failure; stats={stats}"
    assert stats["replayed"] == 0, f"fresh acceptance must not be counted as replay; stats={stats}"
    assert report["accepted"] == 1, f"report must reflect accepted commit; report={report}"
    assert report["replayed"] == 0


def test_replayed_result_stats_survive_callback_failure():
    """A replay is recorded as replay (never fresh acceptance) even if on_result raises."""
    def api(call):
        if call["url"].endswith("/claim"):
            if not getattr(api, "claimed", False):
                api.claimed = True
                return FakeResponse(200, claim_body(job_id="job-replay"))
            return FakeResponse(200, no_work_body())
        if call["url"].endswith("/result"):
            return FakeResponse(200, RESULT_ENVELOPE, headers={"Idempotent-Replayed": "true"})
        if call["url"].endswith("/lease"):
            return FakeResponse(200, {"api_version": "v1", "renewed": True, "job_id": "job-replay", "lease": {"owner": WORKER_ID, "generation": 1, "lease_seconds": 120.0, "renew_every_seconds": 40.0}})
        raise AssertionError(f"unexpected call: {call['url']}")

    worker = make_worker(api)

    raised = False
    try:
        worker.run_forever(max_polls=2, on_result=lambda j, e: (_ for _ in ()).throw(CallbackBoom("boom")))
    except CallbackBoom:
        raised = True
    assert raised, "callback exception must propagate"

    stats = worker.last_run_stats
    report = worker.last_run_report
    assert stats["replayed"] == 1, f"replay must be reflected in stats; stats={stats}"
    assert stats["accepted"] == 0, f"replay must NEVER count as fresh acceptance; stats={stats}"
    assert report["replayed"] == 1
    assert report["accepted"] == 0


TESTS = (
    test_accepted_result_stats_survive_callback_failure,
    test_replayed_result_stats_survive_callback_failure,
)


def main() -> int:
    print("=" * 78)
    print("Callback-failure regression — Fix C")
    print("=" * 78)
    passed = 0
    failures = []
    for fn in TESTS:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"  [FAIL] {fn.__name__} — {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
        else:
            passed += 1
            print(f"  [PASS] {fn.__name__}")
    print(f"\nRESULT: {passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
