#!/usr/bin/env python3
"""Full inference lifecycle over real HTTP — Android worker ↔ Platrixa API.

Run (needs the repo's API dependencies + embedded postgres):
    .venv/bin/python scripts/platrixa_android_lifecycle_e2e_test.py

WHAT THIS PROVES, and why it is different from the existing suites:

  * ``platrixa_android_model_contract_test.py`` proves the worker's model
    contract against in-process fakes.
  * ``fte_fyjc_89_outbound_inference_bridge_test.py`` proves the API/bridge
    contract through an in-process TestClient.
  * Neither one runs the actual worker binary against the actual API over a
    TCP socket. This suite does: a REAL uvicorn server, REAL PostgreSQL, the
    REAL deterministic kernel, a REAL ``requests`` transport, and the worker
    started as a SUBPROCESS with a clean environment, talking to a local HTTP
    server that imitates the phone's OpenAI-compatible endpoint.

The lifecycle exercised end to end:

    create job → claim → lease → prompt sent to the model server → candidate
    → submit → shared parser → evidence/grounding → kernel → authoritative
    result persisted → terminal state → polled by the client

plus the failure paths that decide whether a job can be lost: a model outage
(reported, RETRY_WAIT), a truncated candidate (refused), a rate limit (waited
out, not burned), an abandoned lease with a spent budget (terminalised), a
stale worker (fenced), and a cross-tenant worker (isolated).

Nothing here points at production: the database is an embedded PostgreSQL
instance under /tmp, the model server is a local stub, and every host is
127.0.0.1. No Railway variable is read, no real job is created, and the phone
is never involved.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

for _v in (
    "PLATRIXA_DEV_API_KEY",
    "PLATRIXA_KEY_MANAGEMENT_TOKEN",
    "PLATRIXA_KEY_MANAGEMENT_TENANT_ID",
):
    os.environ.pop(_v, None)

import requests  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import text  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from fte_fyjc_89_outbound_inference_bridge_test import (  # noqa: E402
    KEY_A,
    KEY_B,
    VALID_CANDIDATE,
    HarnessClient,
    expire_lease,
    make_tenant,
    pg_backend,
    reset_runtime_state,
)
from platrixa_outbound_inference_worker import build_model_prompt  # noqa: E402

PASSED = 0
FAILED: list = []
MODEL_ID = "e2e-phone-model"
PROVIDER = "phone-llamacpp"
RAW_INPUT = "purchased furniture from raj for rs.25000"

CANDIDATE_TEXT = json.dumps(VALID_CANDIDATE)


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def summary_line(title: str) -> None:
    print(f"\n{title}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ---------------------------------------------------------------------------
# A local server shaped like the Android LLM app's endpoint
# ---------------------------------------------------------------------------


class ModelStub:
    """OpenAI-compatible loopback server with switchable behaviour."""

    def __init__(self) -> None:
        self.mode = "json"
        self.seen: list = []
        self.port = free_port()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:  # keep the suite's output clean
                return

            def _send(self, status: int, body: bytes, ctype: str, extra=None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, payload: dict, extra=None) -> None:
                self._send(status, json.dumps(payload).encode(), "application/json", extra)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": MODEL_ID}]})
                else:
                    self._json(404, {"error": {"message": "no such route"}})

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                stub.seen.append(
                    {"path": self.path, "body": body, "headers": dict(self.headers)}
                )
                mode = stub.mode
                if mode == "unavailable":
                    self._json(503, {"error": {"message": "model not loaded"}})
                    return
                if mode == "ratelimit_once" and len(stub.seen) == 1:
                    self._json(429, {"error": {"message": "slow down"}},
                               {"Retry-After": "0"})
                    return
                if mode == "truncated":
                    self._json(200, {"choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"transaction_type": "PURCH'},
                        "finish_reason": "length",
                    }]})
                    return
                if mode == "sse":
                    pieces = [CANDIDATE_TEXT[i:i + 40] for i in range(0, len(CANDIDATE_TEXT), 40)]
                    lines = [": ping"]  # a real stream may open with a comment
                    for piece in pieces:
                        lines.append("data: " + json.dumps(
                            {"choices": [{"index": 0, "delta": {"content": piece},
                                          "finish_reason": None}]}))
                    lines.append("data: " + json.dumps(
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
                    lines.append("data: [DONE]")
                    payload = ("\n\n".join(lines) + "\n\n").encode()
                    self._send(200, payload, "text/event-stream; charset=utf-8")
                    return
                self._json(200, {"id": "cmpl-local", "object": "chat.completion",
                                 "choices": [{"index": 0,
                                              "message": {"role": "assistant",
                                                          "content": CANDIDATE_TEXT},
                                              "finish_reason": "stop"}]})

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def reset(self, mode: str = "json") -> None:
        self.mode = mode
        self.seen = []


# ---------------------------------------------------------------------------
# The real API, on a real socket
# ---------------------------------------------------------------------------


class ApiServer:
    def __init__(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        developer.set_client(HarnessClient())  # REAL kernel, stub client
        self._server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=self.port,
                           log_level="warning", access_log=False)
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if requests.get(self.base + "/openapi.json", timeout=1).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        raise RuntimeError("the test API did not become ready")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def headers(key: str = KEY_A, worker_id: str = "w-e2e-1") -> dict:
    return {"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id}


def create_job(api: ApiServer, raw_input: str = RAW_INPUT) -> dict:
    r = requests.post(api.base + "/v1/inference-jobs", json={"raw_input": raw_input},
                      headers={"X-Platrixa-API-Key": KEY_A}, timeout=30)
    assert r.status_code == 202, f"{r.status_code} {r.text[:400]}"
    return r.json()


def claim(api: ApiServer, key: str = KEY_A, worker_id: str = "w-e2e-1") -> dict:
    r = requests.post(api.base + "/v1/worker/jobs/claim", headers=headers(key, worker_id),
                      timeout=30)
    assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
    return r.json()


def poll(api: ApiServer, job_id: str, key: str = KEY_A) -> dict:
    r = requests.get(f"{api.base}/v1/jobs/{job_id}",
                     headers={"X-Platrixa-API-Key": key}, timeout=30)
    assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
    return r.json()


def fetch_result(api: ApiServer, result_id: str, key: str = KEY_A) -> dict:
    """Fetch the envelope. A refused candidate is served with its engine status
    (422 for VALIDATION_FAILED), so any 2xx/4xx JSON envelope is accepted here;
    the assertions read the ENVELOPE, never the transport status."""
    r = requests.get(f"{api.base}/v1/results/{result_id}",
                     headers={"X-Platrixa-API-Key": key}, timeout=30)
    body = r.json()
    assert "status" in body and "api_version" in body, f"{r.status_code} {r.text[:400]}"
    return body


def row(engine, job_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT status, result_json, error_json, retryable, attempt_count, "
                "max_attempts, lease_owner, lease_generation FROM platrixa_async_jobs "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        ).mappings().first()


def run_worker(api: ApiServer, model: ModelStub, *extra: str,
               worker_id: str = "w-e2e-1", key: str = KEY_A) -> subprocess.CompletedProcess:
    """Start the REAL worker binary with a clean environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATRIXA_")}
    env["PYTHONPATH"] = str(_REPO_ROOT)
    cmd = [
        sys.executable, str(_REPO_ROOT / "scripts" / "platrixa_outbound_inference_worker.py"),
        "--api-base-url", api.base,
        "--api-key", key,
        "--worker-id", worker_id,
        "--model-url", f"http://127.0.0.1:{model.port}",
        "--model-id", MODEL_ID,
        "--model-provider", PROVIDER,
        "--model-timeout", "30",
        "--once", "--verbose",
        *extra,
    ]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)


def setup(engine) -> None:
    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-e2e-a", 1000)
    make_tenant(engine, KEY_B, "tenant-e2e-b", 1000)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def section_a_happy_path(api: ApiServer, model: ModelStub, engine) -> dict:
    summary_line("A — complete lifecycle: create → claim → lease → model → submit → kernel → result")
    setup(engine)
    model.reset("json")
    created = create_job(api)
    job_id = created["job_id"]

    proc = run_worker(api, model)
    check("A1 the worker exits 0", proc.returncode == 0,
          f"rc={proc.returncode}\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")

    check("A2 exactly one model call was made", len(model.seen) == 1, str(len(model.seen)))
    if model.seen:
        seen = model.seen[0]
        check("A3 the worker used /v1/chat/completions",
              seen["path"] == "/v1/chat/completions", seen["path"])
        payload = seen["body"]
        check("A4 the payload carries the configured model id and dialect flags",
              payload.get("model") == MODEL_ID and payload.get("stream") is False,
              json.dumps({k: payload.get(k) for k in ("model", "stream")}))
        expected_prompt = build_model_prompt(RAW_INPUT)
        check("A5 the prompt is the byte-exact Phase-H prompt",
              payload.get("messages") == [{"role": "user", "content": expected_prompt}],
              "prompt mismatch")
        check("A6 the tenant API key was NOT sent to the model server",
              KEY_A not in json.dumps(seen["headers"]), "tenant key leaked to the model")

    rec = row(engine, job_id)
    check("A7 the job is COMPLETED", rec["status"] == "COMPLETED", str(rec["status"]))
    check("A8 an authoritative result was persisted", bool(rec["result_json"]), "no result_json")

    envelope = fetch_result(api, created["result_id"])
    check("A9 the result endpoint serves the kernel's verdict",
          envelope.get("status") in ("VERIFIED", "REVIEW_REQUIRED", "UNSUPPORTED"),
          str(envelope.get("status")))
    evidence = (envelope.get("metadata") or {}).get("execution_evidence") or {}
    identity = evidence.get("model_identity") or {}
    check("A10 the evidence names the model the endpoint actually served",
          identity.get("model_id") == MODEL_ID, json.dumps(identity))
    check("A11 the identity is labelled device-reported, never server-verified",
          identity.get("identity_source") == "device-reported-unverified",
          json.dumps(identity))
    check("A12 unknown artifact provenance stays unknown",
          identity.get("provider_revision") == "PROVENANCE_UNKNOWN", json.dumps(identity))

    polled = poll(api, job_id)
    check("A13 polling agrees with the stored result", polled["status"] == envelope.get("status"),
          f"{polled['status']} vs {envelope.get('status')}")
    check("A14 a completed job is not retryable", polled["retryable"] is False,
          str(polled["retryable"]))
    return {"job_id": job_id, "lease": None, "result_id": created["result_id"],
            "result_json": rec["result_json"]}


def section_b_sse(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("B — an SSE answer completes the same lifecycle")
    setup(engine)
    model.reset("sse")
    created = create_job(api, "paid electricity bill of rs.1800 to the board")
    proc = run_worker(api, model)

    check("B1 the worker exits 0", proc.returncode == 0,
          f"rc={proc.returncode}\n{proc.stderr[-600:]}")
    rec = row(engine, created["job_id"])
    check("B2 the streamed candidate became authoritative",
          rec["status"] == "COMPLETED" and bool(rec["result_json"]), str(rec["status"]))


def section_c_model_outage(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("C — a model outage is REPORTED, and the job becomes retryable")
    setup(engine)
    model.reset("unavailable")
    created = create_job(api, "bought a laptop from a vendor for rs.60000")
    job_id = created["job_id"]

    proc = run_worker(api, model)
    check("C1 the worker survives the outage and exits 0", proc.returncode == 0,
          f"rc={proc.returncode}\n{proc.stderr[-600:]}")

    rec = row(engine, job_id)
    check("C2 the job is RETRY_WAIT, not stranded in PROCESSING",
          rec["status"] == "RETRY_WAIT", str(rec["status"]))
    check("C3 no result was written", rec["result_json"] is None, "a result appeared")
    code = json.loads(rec["error_json"] or "{}").get("error", {}).get("code")
    check("C4 the recorded cause is MODEL_UNAVAILABLE", code == "MODEL_UNAVAILABLE", str(code))

    polled = poll(api, job_id)
    check("C5 polling reports RETRY_WAIT rather than a false terminal FAILED",
          polled["status"] == "RETRY_WAIT", str(polled["status"]))
    check("C6 polling marks it retryable", polled["retryable"] is True, str(polled["retryable"]))
    check("C7 polling names the reason code", "MODEL_UNAVAILABLE" in polled["reason_codes"],
          str(polled["reason_codes"]))


def section_d_truncated(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("D — a truncated candidate is refused by the pipeline, never committed")
    setup(engine)
    model.reset("truncated")
    created = create_job(api, "sold a bicycle to a friend for rs.7000")
    job_id = created["job_id"]

    proc = run_worker(api, model)
    check("D1 the worker exits 0", proc.returncode == 0, f"rc={proc.returncode}")

    rec = row(engine, job_id)
    # The refusal IS the authoritative outcome: the parser rejects the
    # candidate, the kernel records VALIDATION_FAILED, and that verdict is what
    # is stored. Nothing is committed as a financial interpretation.
    check("D2 the stored verdict is VALIDATION_FAILED",
          json.loads(rec["result_json"] or "{}").get("status") == "VALIDATION_FAILED",
          str(rec["status"]))
    envelope = fetch_result(api, created["result_id"])
    check("D3 no interpretation was produced", envelope.get("interpretation") is None,
          "an interpretation was accepted from a truncated answer")
    check("D4 no accounting was produced",
          envelope.get("accounting") is None and envelope.get("accounting_result") is None,
          "accounting ran on a truncated answer")
    check("D5 the refusal is not success", envelope.get("success") is False,
          str(envelope.get("success")))
    check("D6 the reason is recorded as a validation rejection",
          "VALIDATION_REJECTED" in (envelope.get("reason_codes") or []),
          str(envelope.get("reason_codes")))


def section_e_exhausted_lease(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("E — an abandoned lease with a spent budget is terminalised by the sweep")
    setup(engine)
    model.reset("json")
    created = create_job(api, "reimbursed travel expenses of rs.3200")
    job_id = created["job_id"]

    claim(api)  # a worker takes the job...
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platrixa_async_jobs SET attempt_count = max_attempts "
                 "WHERE job_id = :j"),
            {"j": job_id},
        )
    expire_lease(engine, job_id)  # ...and dies without reporting

    body = claim(api, worker_id="w-e2e-2")
    check("E1 the crashed job is not handed out again", body["status"] == "NO_WORK",
          str(body["status"]))
    rec = row(engine, job_id)
    check("E2 the job is terminal FAILED, not stuck in PROCESSING",
          rec["status"] == "FAILED", str(rec["status"]))
    code = json.loads(rec["error_json"] or "{}").get("error", {}).get("code")
    check("E3 the sweep explains itself with LEASE_EXHAUSTED", code == "LEASE_EXHAUSTED", str(code))
    check("E4 the sweep wrote no result", rec["result_json"] is None, "a result appeared")


def section_f_fencing(api: ApiServer, model: ModelStub, engine, completed: dict) -> None:
    summary_line("F — a stale worker cannot overwrite an authoritative result")
    job_id = completed["job_id"]
    before = completed["result_json"]

    # The generation that committed is over. A later submission must not
    # replace the result: the route answers 409 when fenced, or replays the
    # STORED envelope labelled as a replay. Either answer is safe; silently
    # committing the new candidate would not be.
    r = requests.post(
        f"{api.base}/v1/worker/jobs/{job_id}/result",
        json={"lease": {"owner": "w-e2e-1", "generation": 1},
              "bridge_body": {"interpretation_text": "{}",
                              "completion_status": "COMPLETE",
                              "finish_reason": "stop"}},
        headers=headers(KEY_A, "w-e2e-1"), timeout=30,
    )
    replay = (r.headers.get("Idempotent-Replayed") or "").strip().lower() == "true"
    check("F1 a second result submission is refused or replayed, never re-committed",
          r.status_code == 409 or (r.status_code == 200 and replay),
          f"{r.status_code} replay={replay} {r.text[:200]}")

    r2 = requests.post(
        f"{api.base}/v1/worker/jobs/{job_id}/failure",
        json={"lease": {"owner": "w-e2e-1", "generation": 1},
              "error": {"code": "MODEL_UNAVAILABLE", "message": "late report"}},
        headers=headers(KEY_A, "w-e2e-1"), timeout=30,
    )
    check("F2 a completed job cannot be failed (409)", r2.status_code == 409,
          f"{r2.status_code} {r2.text[:200]}")

    after = row(engine, job_id)["result_json"]
    check("F3 the stored result is byte-identical after both attempts", after == before,
          "the authoritative result changed")


def section_g_tenant_isolation(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("G — another tenant's worker can neither claim nor fail this job")
    setup(engine)
    model.reset("json")
    created = create_job(api, "bought stationery for rs.400")

    body = claim(api, key=KEY_B, worker_id="w-other-tenant")
    check("G1 a foreign worker sees NO_WORK", body["status"] == "NO_WORK", str(body["status"]))

    r = requests.post(
        f"{api.base}/v1/worker/jobs/{created['job_id']}/failure",
        json={"lease": {"owner": "w-other-tenant", "generation": 1},
              "error": {"code": "MODEL_UNAVAILABLE"}},
        headers=headers(KEY_B, "w-other-tenant"), timeout=30,
    )
    check("G2 a foreign failure report is a non-disclosing 404", r.status_code == 404,
          f"{r.status_code} {r.text[:200]}")
    check("G3 the victim job is untouched", row(engine, created["job_id"])["status"] == "QUEUED",
          str(row(engine, created["job_id"])["status"]))


def section_h_rate_limit(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("H — a 429 is waited out, not burned as a failed attempt")
    setup(engine)
    model.reset("ratelimit_once")
    created = create_job(api, "paid rent of rs.12000")
    proc = run_worker(api, model)

    check("H1 the worker exits 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("H2 the model was retried exactly once", len(model.seen) == 2, str(len(model.seen)))
    rec = row(engine, created["job_id"])
    check("H3 the retried generation completed normally", rec["status"] == "COMPLETED",
          str(rec["status"]))


def section_i_secret_hygiene(api: ApiServer, model: ModelStub, engine) -> None:
    summary_line("I — logs and model-side traffic never expose the tenant key")
    setup(engine)
    model.reset("json")
    create_job(api)
    proc = run_worker(api, model)
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("I1 the API key never appears in worker output", KEY_A not in combined,
          "the tenant key was printed")
    leaked = any(KEY_A in json.dumps(m["headers"]) for m in model.seen)
    check("I2 the API key never reaches the model server", leaked is False,
          "the tenant key was sent to the model")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("Platrixa — full inference lifecycle over real HTTP")
    print("=" * 78)

    _srv, engine = pg_backend()
    api = ApiServer()
    model = ModelStub()
    model.start()
    api.start()
    try:
        completed = section_a_happy_path(api, model, engine)
        # Fencing is checked immediately, while A's completed job is still the
        # current row: every later section resets the throwaway database.
        section_f_fencing(api, model, engine, completed)
        section_b_sse(api, model, engine)
        section_c_model_outage(api, model, engine)
        section_d_truncated(api, model, engine)
        section_e_exhausted_lease(api, model, engine)
        section_g_tenant_isolation(api, model, engine)
        section_h_rate_limit(api, model, engine)
        section_i_secret_hygiene(api, model, engine)
    finally:
        api.stop()
        model.stop()

    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{PASSED + len(FAILED)} lifecycle checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
