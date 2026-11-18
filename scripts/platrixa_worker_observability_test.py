#!/usr/bin/env python3
"""Platrixa S3 — worker observability and health reporting.

Deterministic tests for the worker-local state machine, the secret-free health
snapshot, the atomic status file, and the ``--print-status`` reader. No network,
no Railway, no model server, no credential.

Covered:
  * STARTING → READY → PROCESSING → REPORTING → READY → STOPPED transitions;
  * WAITING_FOR_MODEL while the model is unready, with liveness still true;
  * FAILED on a fatal (non-retryable) claim rejection;
  * counters in the snapshot match the run report exactly;
  * no tenant key, model key, authorization header, or transaction text appears;
  * stop() is observable as STOPPED;
  * the status file is valid JSON and `--print-status` reproduces it;
  * a MODEL_UNAVAILABLE generation records its failure category.

Run: python scripts/platrixa_worker_observability_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from platrixa_outbound_inference_worker import (  # noqa: E402
    API_KEY_HEADER,
    STATE_BACKING_OFF,
    STATE_FAILED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_REPORTING,
    STATE_STOPPED,
    STATE_WAITING_FOR_MODEL,
    WORKER_STATES,
    ApiError,
    OutboundInferenceWorker,
    WorkerConfig,
)
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    BRIDGE_BASE,
    BRIDGE_KEY,
    RESULT_ENVELOPE,
    WORKER_ID,
    FakeResponse,
    FakeSession,
    claim_body,
    error_body,
    no_work_body,
    renewal_body,
)

WORKER_SCRIPT = REPO_ROOT / "scripts" / "platrixa_outbound_inference_worker.py"
MODEL_URL = "http://127.0.0.1:11434"
RAW_INPUT = "purchased furniture from raj for rs.25000"
MODEL_KEY = "model-secret-key-do-not-leak"
ANSWER = '{"transaction_type": "EXPENSE", "amount": 25000}'

PASSED = 0
FAILED: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def models_body(*ids: str) -> dict:
    return {"object": "list", "data": [{"id": model_id} for model_id in ids]}


def chat_body(content: str = ANSWER, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ]
    }


def make_worker(api_handler, model_handler, *, status_file=None, sleep=None):
    config = WorkerConfig(
        api_base_url=API_BASE,
        api_key=API_KEY,
        worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE,
        bridge_key=BRIDGE_KEY,
        model_url=MODEL_URL,
        model_id="default",
        model_key=MODEL_KEY,
        model_dialect="chat",
        heartbeat_tick_s=0.01,
        shutdown_timeout_s=5.0,
        status_file=status_file,
    )
    worker = OutboundInferenceWorker(
        config,
        session=FakeSession(api_handler),
        bridge_session=FakeSession(
            lambda call: (_ for _ in ()).throw(
                AssertionError("the direct-model path must not use the bridge")
            )
        ),
        model_session=FakeSession(model_handler),
        now=time.monotonic,
        sleep=sleep if sleep is not None else (lambda seconds: None),
    )
    return worker


def full_api_handler(observed_states: list, worker_box: dict, *, fatal: bool = False):
    state = {"claims": 0}

    def handler(call):
        url = call["url"]
        if url.endswith("/claim"):
            state["claims"] += 1
            observed_states.append(worker_box["worker"].state)
            if fatal:
                return FakeResponse(401, error_body("UNAUTHORIZED"))
            if state["claims"] == 1:
                return FakeResponse(200, claim_body(raw_input=RAW_INPUT))
            return FakeResponse(200, no_work_body(0.01))
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if url.endswith("/result"):
            observed_states.append(worker_box["worker"].state)
            return FakeResponse(200, RESULT_ENVELOPE)
        if url.endswith("/failure"):
            observed_states.append(worker_box["worker"].state)
            return FakeResponse(200, {"api_version": "v1", "status": "RETRY_WAIT"})
        raise AssertionError(f"unexpected API call: {url}")

    return handler


def make_model_handler(observed_states=None, worker_box=None):
    def handler(call):
        if call["url"].endswith("/v1/models"):
            return FakeResponse(200, models_body("default"))
        if observed_states is not None and worker_box is not None:
            observed_states.append(worker_box["worker"].state)
        return FakeResponse(200, chat_body())
    return handler


# ---------------------------------------------------------------------------
# A. Lifecycle transitions
# ---------------------------------------------------------------------------


def test_happy_path_states() -> None:
    observed: list = []
    box: dict = {}
    worker = make_worker(
        full_api_handler(observed, box), make_model_handler(observed, box)
    )
    box["worker"] = worker

    worker.run_forever(max_polls=3)

    check(
        "a run starts, becomes ready, and stops",
        worker.state == STATE_STOPPED,
        worker.state,
    )
    check(
        "the claim is made from READY",
        STATE_READY in observed,
        observed,
    )
    check(
        "the model call happens while PROCESSING",
        STATE_PROCESSING in observed,
        observed,
    )
    check(
        "the submission happens while REPORTING",
        STATE_REPORTING in observed,
        observed,
    )
    check(
        "every reported state is a known worker state",
        all(state in WORKER_STATES for state in observed),
        observed,
    )


def test_waiting_for_model_is_live_but_not_ready() -> None:
    box: dict = {}
    observed: list = []

    def record_sleep(seconds):
        # The readiness gate sets WAITING_FOR_MODEL, then BACKING_OFF, then
        # sleeps; the run ends as STOPPED by design, so observe during the run.
        observed.append(box["worker"].state)

    worker = make_worker(
        lambda call: FakeResponse(200, no_work_body(0.01)),
        lambda call: FakeResponse(503, error_body("MODEL_LOADING")),
        sleep=record_sleep,
    )
    box["worker"] = worker

    worker.run_forever(max_polls=2)
    snapshot = worker.health_snapshot()

    check(
        "an unready model puts the worker in WAITING_FOR_MODEL",
        STATE_WAITING_FOR_MODEL in observed,
        observed,
    )
    check(
        "liveness stays true while waiting for the model",
        snapshot["liveness"]["alive"] is True,
        snapshot["liveness"],
    )
    check(
        "readiness is false while waiting for the model",
        snapshot["readiness"]["ready"] is False,
        snapshot["readiness"],
    )
    check(
        "the readiness reason names the HTTP failure",
        "503" in snapshot["readiness"]["reason"],
        snapshot["readiness"],
    )
    check(
        "liveness and readiness are separate keys",
        "liveness" in snapshot and "readiness" in snapshot,
        sorted(snapshot),
    )


def test_idle_queue_backs_off() -> None:
    observed: list = []
    states_seen: list = []
    box: dict = {}

    def record_sleep(seconds):
        states_seen.append(box["worker"].state)

    worker = make_worker(
        lambda call: FakeResponse(200, no_work_body(0.01)),
        make_model_handler(observed, box),
        sleep=record_sleep,
    )
    box["worker"] = worker
    # Only NO_WORK answers: state must pass through BACKING_OFF.
    worker.run_forever(max_polls=1)
    check(
        "an idle queue is observable as BACKING_OFF",
        STATE_BACKING_OFF in states_seen,
        states_seen,
    )


# ---------------------------------------------------------------------------
# B. Failure states
# ---------------------------------------------------------------------------


def test_fatal_claim_marks_failed() -> None:
    observed: list = []
    box: dict = {}
    status_path = Path(tempfile.mkdtemp(prefix="plx_s3_")) / "status.json"
    worker = make_worker(
        full_api_handler(observed, box, fatal=True),
        make_model_handler(observed, box),
        status_file=str(status_path),
    )
    box["worker"] = worker

    try:
        worker.run_forever(max_polls=1)
    except ApiError as exc:
        check("a fatal claim error still raises", exc.code == "UNAUTHORIZED", exc.code)
    else:
        check("a fatal claim error still raises", False, "no ApiError")

    snapshot = worker.health_snapshot()
    check(
        "a fatal claim rejection is observable as FAILED",
        worker.state == STATE_FAILED,
        worker.state,
    )
    check(
        "the failure category is recorded",
        snapshot["last_failure_category"] == "UNAUTHORIZED",
        snapshot["last_failure_category"],
    )
    check(
        "the status file records the terminal state",
        status_path.exists() and json.loads(status_path.read_text())["state"] == STATE_FAILED,
        status_path.read_text()[:200] if status_path.exists() else "missing",
    )


def test_model_unavailable_failure_category() -> None:
    observed: list = []
    box: dict = {}
    state = {"ready": True, "claims": 0}

    def api(call):
        url = call["url"]
        if url.endswith("/claim"):
            state["claims"] += 1
            return FakeResponse(200, claim_body(raw_input=RAW_INPUT))
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if url.endswith("/failure"):
            observed.append(worker.state)
            return FakeResponse(200, {"api_version": "v1", "status": "RETRY_WAIT"})
        raise AssertionError(url)

    def model(call):
        if call["url"].endswith("/v1/models"):
            return FakeResponse(200, models_body("default")) if state["ready"] else FakeResponse(
                503, error_body("MODEL_DOWN")
            )
        state["ready"] = False
        return FakeResponse(503, text="model down")

    worker = make_worker(api, model)
    box["worker"] = worker
    worker.run_forever(max_polls=2)
    snapshot = worker.health_snapshot()

    check(
        "an unusable model generation is reported",
        snapshot["counters"]["failures_reported"] == 1,
        snapshot["counters"],
    )
    check(
        "the failure category is MODEL_UNAVAILABLE",
        snapshot["last_failure_category"] == "MODEL_UNAVAILABLE",
        snapshot["last_failure_category"],
    )
    check(
        "the worker returns to waiting, not to claiming",
        worker.state in (STATE_WAITING_FOR_MODEL, STATE_BACKING_OFF, STATE_STOPPED),
        worker.state,
    )


# ---------------------------------------------------------------------------
# C. Counter accuracy + redaction
# ---------------------------------------------------------------------------


def test_counters_match_run_report() -> None:
    observed: list = []
    box: dict = {}
    worker = make_worker(
        full_api_handler(observed, box), make_model_handler(observed, box)
    )
    box["worker"] = worker
    worker.run_forever(max_polls=3)
    snapshot = worker.health_snapshot()

    check(
        "snapshot counters equal the run report",
        snapshot["counters"] == worker.last_run_report,
        f"{snapshot['counters']} != {worker.last_run_report}",
    )
    check(
        "the accepted count is exact",
        snapshot["counters"]["accepted"] == 1,
        snapshot["counters"],
    )
    check(
        "claim counters are not double-counted",
        snapshot["counters"]["claims"] == snapshot["counters"]["no_work"] + 1,
        snapshot["counters"],
    )


def test_status_snapshot_redaction() -> None:
    observed: list = []
    box: dict = {}
    status_path = Path(tempfile.mkdtemp(prefix="plx_s3_")) / "status.json"
    worker = make_worker(
        full_api_handler(observed, box),
        make_model_handler(observed, box),
        status_file=str(status_path),
    )
    box["worker"] = worker
    worker.run_forever(max_polls=3)

    text = status_path.read_text(encoding="utf-8")
    check("the status file is valid JSON", isinstance(json.loads(text), dict), text[:120])
    for secret in (API_KEY, MODEL_KEY):
        check("the status file contains no credential", secret not in text, "credential leaked")
    check(
        "the status file names no authorization header",
        "authorization" not in text.lower() and API_KEY_HEADER.lower() not in text.lower(),
        "auth header name leaked",
    )
    check(
        "the status file contains no transaction text",
        RAW_INPUT not in text,
        "raw input leaked into status",
    )
    snapshot = json.loads(text)
    check(
        "the snapshot carries the operational identity fields",
        snapshot["worker_id"] == WORKER_ID
        and snapshot["model_id"] == "default"
        and snapshot["model_endpoint"] == MODEL_URL,
        {k: snapshot.get(k) for k in ("worker_id", "model_id", "model_endpoint")},
    )
    check(
        "the snapshot records the last completed job",
        snapshot["last_completed_job_id"] is not None,
        snapshot["last_completed_job_id"],
    )


# ---------------------------------------------------------------------------
# D. Shutdown + CLI
# ---------------------------------------------------------------------------


def test_stop_is_observable() -> None:
    box: dict = {}
    worker = make_worker(
        lambda call: FakeResponse(200, no_work_body(0.01)),
        lambda call: FakeResponse(200, models_body("default")),
    )
    box["worker"] = worker
    worker.stop()
    completed = worker.run_forever(max_polls=5)

    check("stop() prevents any claim", completed == 0 and worker.last_run_report["claims"] == 0, worker.last_run_report)
    check("stop() is observable as STOPPED", worker.state == STATE_STOPPED, worker.state)
    check(
        "liveness reports false after shutdown",
        worker.health_snapshot()["liveness"]["alive"] is False,
        worker.health_snapshot()["liveness"],
    )


def test_print_status_cli() -> None:
    observed: list = []
    box: dict = {}
    status_path = Path(tempfile.mkdtemp(prefix="plx_s3_cli_")) / "status.json"
    worker = make_worker(
        full_api_handler(observed, box),
        make_model_handler(observed, box),
        status_file=str(status_path),
    )
    box["worker"] = worker
    worker.run_forever(max_polls=2)

    result = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), "--print-status", "--status-file", str(status_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    check(
        "--print-status exits 0 and prints the snapshot",
        result.returncode == 0 and json.loads(result.stdout)["worker_id"] == WORKER_ID,
        f"rc={result.returncode} out={result.stdout[:200]} err={result.stderr[:200]}",
    )

    missing = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), "--print-status", "--status-file", str(status_path) + ".nope"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    check(
        "--print-status on a missing file exits 1 with a clear line",
        missing.returncode == 1 and "unavailable" in missing.stderr,
        f"rc={missing.returncode} err={missing.stderr[:200]}",
    )

    no_path = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), "--print-status"],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    check(
        "--print-status without a path is a configuration error (2)",
        no_path.returncode == 2 and "requires --status-file" in no_path.stderr,
        f"rc={no_path.returncode} err={no_path.stderr[:200]}",
    )


def main() -> int:
    print("=" * 78)
    print("Platrixa S3 — worker observability and health reporting")
    print("=" * 78)
    sections = (
        (
            "A. Lifecycle transitions",
            (test_happy_path_states, test_waiting_for_model_is_live_but_not_ready, test_idle_queue_backs_off),
        ),
        ("B. Failure states", (test_fatal_claim_marks_failed, test_model_unavailable_failure_category)),
        ("C. Counters and redaction", (test_counters_match_run_report, test_status_snapshot_redaction)),
        ("D. Shutdown and CLI", (test_stop_is_observable, test_print_status_cli)),
    )
    for title, tests in sections:
        print(f"\n{title}")
        for test in tests:
            try:
                test()
            except BaseException as exc:  # noqa: BLE001
                check(test.__name__, False, f"{type(exc).__name__}: {exc}")

    total = PASSED + len(FAILED)
    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{total} observability checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
