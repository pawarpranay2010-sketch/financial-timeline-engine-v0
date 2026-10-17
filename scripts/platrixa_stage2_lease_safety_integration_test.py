#!/usr/bin/env python3
"""Stage 2 lease-safety audit — ISOLATED INTEGRATION suite (not committed).

What this is
============
An integration harness that drives the REAL Stage 2 worker over REAL localhost
HTTP against the REAL FastAPI application, the REAL PostgreSQL-backed job store
and the REAL server-side lease/fence SQL, with a controllable mock model bridge
standing in for llama-server.

Every layer under test is the production implementation:
  * ``scripts/platrixa_outbound_inference_worker.py``  — the worker under audit
  * ``api/routes/inference_api.py``                    — the claim/renew/result routes
  * ``backend/auth/async_jobs.py``                     — the claim/renew/commit SQL
  * PostgreSQL (embedded, disposable)                  — the durable store
The ONLY substitutions are the device-side pieces a CI host cannot have: the
model bridge and the kernel's client stub (both come from the Stage 1 harness).

Isolation guardrails (checked before anything runs)
--------------------------------------------------
  * every URL the suite uses must resolve to a loopback address, and the suite
    ABORTS if any PLATRIXA_* origin environment variable points off-host;
  * a fresh embedded PostgreSQL cluster in a temp dir, dropped at the end;
  * disposable tenant keys/worker ids, no production credential or record;
  * the worker's API base URL is ``http://127.0.0.1:<ephemeral>`` (loopback http
    is the one plaintext form the worker accepts);
  * no Railway host, no external model API, no real inference.

Evidence classification used in the output
------------------------------------------
Each check is tagged so unit evidence, integration evidence and source
inspection are never conflated. Everything not tagged UNIT/IPC here was
observed end to end over a socket.

Run:
    .venv/bin/python scripts/platrixa_stage2_lease_safety_integration_test.py
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

# The disposable embedded cluster the shared Stage 1 harness provisions.
_PG_DATA = Path("/tmp/platrixa_fte89_pgdata")

import requests  # noqa: E402


# ---------------------------------------------------------------------------
# Guardrail 1 — nothing may resolve off-host. Checked BEFORE importing the app.
# ---------------------------------------------------------------------------


def _is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_isolation() -> None:
    for var in (
        "PLATRIXA_API_BASE_URL",
        "PLATRIXA_MODEL_ENDPOINT_URL",
        "PLATRIXA_LOCAL_BRIDGE_URL",
    ):
        value = (os.environ.get(var) or "").strip()
        if value and not _is_loopback(value):
            raise SystemExit(
                f"ABORT: {var}={value!r} is not loopback — this suite must never "
                "route a request to a production host."
            )
    for var in ("PLATRIXA_API_KEY", "PLATRIXA_MODEL_API_KEY"):
        if (os.environ.get(var) or "").strip():
            raise SystemExit(
                f"ABORT: {var} is set — the suite must use disposable tenant keys, "
                "never a real credential."
            )


assert_isolation()

import fte_fyjc_89_outbound_inference_bridge_test as S1  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import async_jobs  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    BridgeResponseError,
    BridgeUnavailableError,
    LeaseLost,
    OutboundInferenceWorker,
    WorkerConfig,
)

import uvicorn  # noqa: E402

KEY_A = "audit-tenant-a-key-00000000001"
KEY_B = "audit-tenant-b-key-00000000002"
TENANT_A = "tenant-audit-a"
TENANT_B = "tenant-audit-b"
BRIDGE_KEY = "audit-device-bridge-key"
RAW_INPUT = S1.RAW_INPUT
BRIDGE_BODY = S1.bridge_body(S1.VALID_CANDIDATE)

CHECKS: list = []
_SCENARIO = {"name": ""}


def scenario(name: str) -> None:
    _SCENARIO["name"] = name
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")


def check(name: str, ok: bool, detail: str = "", *, kind: str = "INTEGRATION") -> None:
    CHECKS.append((f"{_SCENARIO['name']} :: {name}", bool(ok), detail, kind))
    print(f"  [{'PASS' if ok else 'FAIL'}] ({kind}) {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Mock model bridge — the real request shape, controllable responses
# ---------------------------------------------------------------------------

_OVERSIZED = json.dumps({"pad": "x" * 70000})


class BridgeState:
    def __init__(self) -> None:
        self.mode = "happy"
        self.paths: list = []
        self.headers: list = []
        self.bodies: list = []
        self.responses: list = []  # scripted (status, body) sequence, consumed in order
        self.served_bodies: list = []  # the bodies the bridge actually served
        self.gate: threading.Event | None = None
        # Runs BEFORE the response is written, so a server-side state change
        # (expiry, superseding claim) is committed while the worker is still
        # blocked waiting for its body. That makes the interruption ordering
        # deterministic instead of racy.
        self.before_respond = None
        self.served = threading.Event()

    def reset(self, mode: str = "happy") -> None:
        self.mode = mode
        self.paths.clear()
        self.headers.clear()
        self.bodies.clear()
        self.gate = None
        self.before_respond = None
        self.responses.clear()
        self.served_bodies.clear()
        self.served.clear()


class _BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silence the default stderr logging
        pass

    def do_POST(self) -> None:  # noqa: N802 - http.server contract
        state: BridgeState = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        state.paths.append(self.path)
        state.headers.append({k.lower(): v for k, v in self.headers.items()})
        try:
            state.bodies.append(json.loads(raw or b"{}"))
        except Exception:
            state.bodies.append(raw.decode("utf-8", "replace"))

        if state.mode == "close":
            # A connection error (no HTTP response at all) — used to prove the
            # worker's connection-error SHORT-CIRCUIT: if it fell through to the
            # "/" fallback we would see a second connection here.
            self.close_connection = True
            try:
                self.connection.close()
            except Exception:
                pass
            state.served.set()
            return

        if state.gate is not None:
            state.gate.wait(15)
        time.sleep(0.0 if state.mode != "slow" else 1.5)
        if state.before_respond is not None:
            state.before_respond(self.path)

        if state.responses:
            status, body = state.responses.pop(0)
        else:
            status, body = self._respond(state.mode)
        state.served_bodies.append(body)
        payload = body if isinstance(body, str) else json.dumps(body)
        data = payload.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            pass
        state.served.set()

    def _respond(self, mode: str):
        if mode in ("happy", "slow"):
            return 200, BRIDGE_BODY
        if mode == "fallback":
            # 404 on /interpret, 200 on / — the documented fallback rule.
            return (404, {"detail": "not mounted"}) if self.path == "/interpret" else (200, BRIDGE_BODY)
        if mode == "405":
            return (405, {"detail": "method"}) if self.path == "/interpret" else (200, BRIDGE_BODY)
        if mode == "500":
            return 500, {"detail": "bridge exploded"}
        if mode == "notjson":
            return 200, "<html>not json</html>"
        if mode == "nonobject":
            return 200, [1, 2, 3]
        if mode == "oversized":
            return 200, _OVERSIZED
        return 200, BRIDGE_BODY


def start_bridge() -> tuple:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeHandler)
    server.state = BridgeState()  # type: ignore[attr-defined]
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="audit-mock-bridge", daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


# ---------------------------------------------------------------------------
# Local API over a real socket (the worker uses real HTTP, not an ASGI shim)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_api(app) -> tuple:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="audit-api-server", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(300):
        try:
            if requests.get(f"{base}/health", timeout=1.0).status_code in (200, 404):
                return server, thread, base
        except requests.RequestException:
            time.sleep(0.05)
    raise SystemExit("ABORT: the local audit API did not become reachable")


# ---------------------------------------------------------------------------
# Worker + API helpers
# ---------------------------------------------------------------------------


def make_worker(bridge_url: str, *, key: str = KEY_A, worker_id: str = "w-audit-a", api_base: str,
                **overrides) -> OutboundInferenceWorker:
    config = WorkerConfig(
        api_base_url=api_base,
        api_key=key,
        worker_id=worker_id,
        bridge_url=bridge_url,
        bridge_key=BRIDGE_KEY,
        bridge_timeout_s=overrides.pop("bridge_timeout_s", 10.0),
        heartbeat_tick_s=overrides.pop("heartbeat_tick_s", 0.02),
        shutdown_timeout_s=overrides.pop("shutdown_timeout_s", 5.0),
        **overrides,
    )
    return OutboundInferenceWorker(config)


def create_job(c, *, key: str = KEY_A, raw_input: str = RAW_INPUT, idem: str | None = None):
    headers = {"X-Platrixa-API-Key": key}
    if idem:
        headers["Idempotency-Key"] = idem
    return c.post("/v1/inference-jobs", json={"raw_input": raw_input}, headers=headers)


def claim_via_http(c, *, key: str = KEY_A, worker_id: str = "w-audit-a"):
    return c.post("/v1/worker/jobs/claim", headers={"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id})


def submit_via_http(c, job_id: str, lease: dict, body: dict, *, key: str = KEY_A, worker_id: str = "w-audit-a"):
    return c.post(
        f"/v1/worker/jobs/{job_id}/result",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]}, "bridge_body": body},
        headers={"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id},
    )


def renew_via_http(c, job_id: str, lease: dict, *, key: str = KEY_A, worker_id: str = "w-audit-a"):
    return c.post(
        f"/v1/worker/jobs/{job_id}/lease",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]}},
        headers={"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id},
    )


def job_row(engine, job_id: str):
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, lease_owner, lease_generation, result_json, http_status, "
                "attempt_count, lease_expires_at FROM platrixa_async_jobs WHERE job_id = :j"
            ),
            {"j": job_id},
        ).mappings().first()
    return dict(row) if row else None


def reset(engine) -> None:
    S1.reset_runtime_state(engine)
    S1.make_tenant(engine, KEY_A, TENANT_A, 1000)
    S1.make_tenant(engine, KEY_B, TENANT_B, 1000)


_INFRA_THREADS = {"audit-api-server", "audit-mock-bridge", "MainThread"}


def stray_threads() -> list:
    """Worker-owned threads still alive.

    The only thread the worker creates is the lease heartbeat, so anything left
    alive under that name is a leak. The server-side request threads of the local
    TestClient/uvicorn/mock-bridge (AnyIO, ``Thread-N (process_request_thread)``,
    ``platrixa-request-log``) are infrastructure owned by those servers, not by
    the worker, and are reported separately in the cleanup section.
    """
    return heartbeat_threads()


def heartbeat_threads() -> list:
    return [t.name for t in threading.enumerate() if t.name == "platrixa-lease-heartbeat" and t.is_alive()]


# ---------------------------------------------------------------------------
# Scenario 1 — happy path, end to end through the real worker
# ---------------------------------------------------------------------------


def s1_happy_path(c, engine, api_base, bridge) -> None:
    scenario("S1 happy path — real worker over real HTTP → fenced commit → stored result")
    reset(engine)
    bridge.state.reset("happy")

    created = create_job(c)
    check("job creation returns 202 with the polling handles", created.status_code == 202, created.text[:160])
    job_id = created.json()["job_id"]
    result_id = created.json()["result_id"]

    worker = make_worker(bridge.base, api_base=api_base)
    claim = worker.claim()
    check("the worker claimed the job over real HTTP", (not claim.no_work) and claim.job is not None)
    job = claim.job
    check("the claimed job id matches the created job", job.job_id == job_id, f"{job.job_id} != {job_id}")
    check("the claim carried the server's raw_input verbatim", job.raw_input == RAW_INPUT)
    check("the lease names this worker and a positive generation",
          job.state.owner == "w-audit-a" and job.state.generation >= 1,
          f"{job.state.owner}/{job.state.generation}")

    envelope = worker.process_job(job)

    check("the mock bridge received exactly one request on /interpret",
          bridge.state.paths == ["/interpret"], str(bridge.state.paths))
    check("the bridge received {\"text\": raw_input} and ONLY the bridge credential",
          bridge.state.bodies == [{"text": RAW_INPUT}]
          and bridge.state.headers[0].get("x-platrixa-android-bridge-key") == BRIDGE_KEY
          and all("x-platrixa-api-key" not in h for h in bridge.state.headers),
          json.dumps({"bodies": bridge.state.bodies, "hdrs": bridge.state.headers})[:200])

    row = job_row(engine, job_id)
    check("the server committed the job (COMPLETED, lease released)",
          row["status"] == "COMPLETED" and row["lease_owner"] is None and row["lease_expires_at"] is None,
          json.dumps(row, default=str)[:200])
    check("the stored envelope is the server's, not the worker's construction",
          json.loads(row["result_json"]) == envelope, "stored != returned")
    check("the authoritative status came from the deterministic kernel (VERIFIED for the canonical input)",
          envelope.get("api_status") == "VERIFIED" and envelope.get("status") == "VERIFIED",
          json.dumps({k: envelope.get(k) for k in ("status", "api_status")}))
    check("the kernel produced an accounting result — the worker computed nothing",
          envelope.get("accounting_result") is not None and envelope.get("interpretation") is not None)

    rr = c.get(f"/v1/results/{result_id}", headers={"X-Platrixa-API-Key": KEY_A})
    check("the stored result is readable through the existing result route and matches",
          rr.status_code == 200 and rr.json() == envelope, f"{rr.status_code}")
    check("the reused job-status route reports the same authoritative state",
          c.get(f"/v1/jobs/{job_id}", headers={"X-Platrixa-API-Key": KEY_A}).json()["status"] == "VERIFIED")
    check("no worker/heartbeat thread leaked after a completed job", stray_threads() == [], str(stray_threads()))


# ---------------------------------------------------------------------------
# Scenario 2 — lease expiry, with and without a superseding claim
# ---------------------------------------------------------------------------


def s2_lease_expiry(c, engine, api_base, bridge) -> None:
    scenario("S2 lease expiry — what expiry does and does not revoke (server behaviour)")
    reset(engine)

    # --- 2a: expiry WITHOUT a superseding claim (no other worker took the job).
    created = create_job(c)
    job_id = created.json()["job_id"]
    lease = claim_via_http(c, worker_id="w-expiry-a").json()["job"]["lease"]
    S1.expire_lease(engine, job_id)
    row_after_expiry = job_row(engine, job_id)
    check("the lease is expired in the store while the job is still PROCESSING",
          row_after_expiry["status"] == "PROCESSING" and row_after_expiry["lease_expires_at"] is not None)

    r_claim_self = claim_via_http(c, worker_id="w-expiry-b")
    check("an expired lease makes the job CLAIMABLE again (crash recovery) — even by another worker",
          r_claim_self.status_code == 200 and r_claim_self.json()["status"] == "CLAIMED",
          r_claim_self.text[:160])
    lease2 = r_claim_self.json()["job"]["lease"]
    check("the reclaim bumped the fencing generation",
          lease2["generation"] == lease["generation"] + 1, f"{lease} -> {lease2}")

    # The stale (superseded) worker is fenced on BOTH paths.
    r_stale_renew = renew_via_http(c, job_id, lease, worker_id="w-expiry-a")
    check("the superseded worker's renewal is refused (409 LEASE_LOST)",
          r_stale_renew.status_code == 409 and r_stale_renew.json()["error"]["code"] == "LEASE_LOST",
          f"{r_stale_renew.status_code} {r_stale_renew.text[:140]}")
    r_stale_submit = submit_via_http(c, job_id, lease, BRIDGE_BODY, worker_id="w-expiry-a")
    check("the superseded worker's result is refused (409 LEASE_LOST) — stale output cannot commit",
          r_stale_submit.status_code == 409 and r_stale_submit.json()["error"]["code"] == "LEASE_LOST",
          f"{r_stale_submit.status_code} {r_stale_submit.text[:140]}")
    check("the failed stale submission left the job PROCESSING with no stored result",
          job_row(engine, job_id)["result_json"] is None)

    r_current = submit_via_http(c, job_id, lease2, BRIDGE_BODY, worker_id="w-expiry-b")
    check("the generation that currently owns the job commits successfully",
          r_current.status_code == 200 and r_current.json()["api_status"] == "VERIFIED",
          f"{r_current.status_code} {r_current.text[:160]}")

    # --- 2b: expiry with NO superseding claim — the O1 boundary case.
    #
    # This block USED to assert the defect (O1): an expired-but-unsuperseded
    # lease could still renew and still commit. Stage 2 O1 changed the server
    # contract, so the assertions now encode the intended rule: expiry alone
    # revokes commit authority, and renewal cannot resurrect it.
    created2 = create_job(c)
    job2 = created2.json()["job_id"]
    lease3 = claim_via_http(c, worker_id="w-boundary-a").json()["job"]["lease"]
    S1.expire_lease(engine, job2)
    r_expired_renew = renew_via_http(c, job2, lease3, worker_id="w-boundary-a")
    r_expired_submit = submit_via_http(c, job2, lease3, BRIDGE_BODY, worker_id="w-boundary-a")
    check("O1: an expired-but-unsuperseded lease CANNOT be renewed (no silent resurrection)",
          r_expired_renew.status_code == 409
          and r_expired_renew.json().get("error", {}).get("code") == "LEASE_EXPIRED",
          f"{r_expired_renew.status_code} {r_expired_renew.text[:140]}")
    check("O1: an expired-but-unsuperseded lease CANNOT commit",
          r_expired_submit.status_code == 409
          and r_expired_submit.json().get("error", {}).get("code") == "LEASE_EXPIRED",
          f"{r_expired_submit.status_code} {r_expired_submit.text[:140]}")
    row_o1 = job_row(engine, job2)
    check("O1: the refused commit stored nothing and left the job PROCESSING at the same generation",
          row_o1["status"] == "PROCESSING" and row_o1["result_json"] is None
          and row_o1["attempt_count"] == 1, json.dumps({k: str(v) for k, v in row_o1.items()}))
    reclaimed = claim_via_http(c, worker_id="w-boundary-b")
    check("O1: after the expiry the job is claimable at a NEW generation (the only path forward)",
          reclaimed.status_code == 200 and reclaimed.json()["status"] == "CLAIMED"
          and reclaimed.json()["job"]["lease"]["generation"] == lease3["generation"] + 1,
          reclaimed.text[:160])
    lease4 = reclaimed.json()["job"]["lease"]
    r_fresh = submit_via_http(c, job2, lease4, BRIDGE_BODY, worker_id="w-boundary-b")
    check("O1: the new generation commits normally (expiry cost a reclaim, not the job)",
          r_fresh.status_code == 200 and r_fresh.json()["api_status"] == "VERIFIED",
          f"{r_fresh.status_code} {r_fresh.text[:140]}")


# ---------------------------------------------------------------------------
# Scenario 3 — competing workers, with the REAL worker on the losing side
# ---------------------------------------------------------------------------


def s3_competing_workers(c, engine, api_base, bridge) -> None:
    scenario("S3 competing workers — a late worker cannot overwrite the authoritative result")
    reset(engine)
    bridge.state.reset("happy")

    created = create_job(c)
    job_id = created.json()["job_id"]
    result_id = created.json()["result_id"]

    worker_a = make_worker(bridge.base, api_base=api_base, worker_id="w-compete-a")
    job_a = worker_a.claim().job
    # A's inference completes, but we withhold the submission to force the race.
    body_a = worker_a.call_bridge(job_a.raw_input)
    check("worker A produced a complete bridge body locally", body_a == BRIDGE_BODY)

    # A's lease expires, B claims the SAME job at a newer generation and commits.
    S1.expire_lease(engine, job_id)
    claim_b = claim_via_http(c, worker_id="w-compete-b").json()
    check("worker B reclaimed the same job at a newer generation",
          claim_b["job"]["job_id"] == job_id
          and claim_b["job"]["lease"]["generation"] == job_a.state.generation + 1,
          json.dumps(claim_b["job"]["lease"]))
    lease_b = claim_b["job"]["lease"]
    r_b = submit_via_http(c, job_id, lease_b, BRIDGE_BODY, worker_id="w-compete-b")
    check("worker B's result became authoritative", r_b.status_code == 200, r_b.text[:160])
    stored_b = job_row(engine, job_id)["result_json"]

    # A submits late.
    r_a = submit_via_http(c, job_id, {"owner": job_a.state.owner, "generation": job_a.state.generation},
                          body_a, worker_id="w-compete-a")
    check("OBSERVED: worker A's late submission returned 200 (the STORED authoritative result, replayed)",
          r_a.status_code == 200, f"{r_a.status_code} {r_a.text[:140]}")
    check("A's late submission did NOT overwrite the stored result",
          job_row(engine, job_id)["result_json"] == stored_b)
    check("A's replay carried Idempotent-Replayed: true (a replay, not a new commit)",
          r_a.headers.get("Idempotent-Replayed") == "true",
          str(r_a.headers.get("Idempotent-Replayed")))
    check("the stored result is the same envelope the result route serves",
          c.get(f"/v1/results/{result_id}", headers={"X-Platrixa-API-Key": KEY_A}).json()
          == json.loads(stored_b))

    # Reversed order: A submits BEFORE B's claim exists (A's generation was never
    # superseded) — the commit succeeds, and B then finds nothing to claim.
    created2 = create_job(c)
    job2 = created2.json()["job_id"]
    job_a2 = make_worker(bridge.base, api_base=api_base, worker_id="w-compete-c").claim().job
    r_a2 = worker_a.submit_result(job_a2, BRIDGE_BODY)  # A commits first, in order
    check("in the reversed order the first (still-current) generation commits", r_a2.get("api_status") == "VERIFIED")
    r_b2 = submit_via_http(c, job2, job_a2.state.proof.to_wire(), BRIDGE_BODY, worker_id="w-compete-c")
    check("a duplicate submission at the same generation replays instead of recomputing",
          r_b2.status_code == 200 and r_b2.headers.get("Idempotent-Replayed") == "true",
          f"{r_b2.status_code} {r_b2.headers.get('Idempotent-Replayed')}")


# ---------------------------------------------------------------------------
# Scenario 4 — interruption points (deterministic, event-driven)
# ---------------------------------------------------------------------------


def s4_interruption(c, engine, api_base, bridge) -> None:
    scenario("S4 interruption — loss observed at each worker stage, no commit")
    reset(engine)

    # (a) Lease lost BEFORE inference: the worker must not even call the bridge.
    bridge.state.reset("happy")
    created = create_job(c)
    job_id = created.json()["job_id"]
    worker = make_worker(bridge.base, api_base=api_base, worker_id="w-int-a")
    job = worker.claim().job
    job.state.mark_lost("simulated fence loss before inference")
    try:
        worker.process_job(job)
        raised = None
    except LeaseLost as exc:
        raised = exc
    check("interruption before inference: the worker raises LeaseLost", isinstance(raised, LeaseLost))
    check("interruption before inference: the bridge was never called", bridge.state.paths == [])
    check("interruption before inference: nothing was submitted and the job is unchanged",
          job_row(engine, job_id)["status"] == "PROCESSING"
          and job_row(engine, job_id)["result_json"] is None)

    # (b) Interruption DURING inference: the bridge blocks, the bounded bridge
    #     timeout fires, and the worker abandons the candidate without submitting.
    bridge.state.reset("happy")
    gate = threading.Event()
    bridge.state.gate = gate
    created2 = create_job(c)
    job2 = created2.json()["job_id"]
    worker2 = make_worker(bridge.base, api_base=api_base, worker_id="w-int-b", bridge_timeout_s=0.4)
    job_b = worker2.claim().job
    started = time.monotonic()
    try:
        worker2.process_job(job_b)
        raised_b = None
    except BridgeUnavailableError as exc:
        raised_b = exc
    elapsed = time.monotonic() - started
    check("interruption during a blocked inference: the worker times out and abandons (BridgeUnavailableError)",
          isinstance(raised_b, BridgeUnavailableError), type(raised_b).__name__)
    check("the abandoned candidate was never submitted",
          job_row(engine, job2)["result_json"] is None and job_row(engine, job2)["status"] == "PROCESSING")
    check("the bounded bridge timeout was honoured (no unbounded hang)", elapsed < 5.0, f"{elapsed:.2f}s")
    check("the heartbeat thread was joined after the interruption",
          not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate()))
    gate.set()
    bridge.state.gate = None

    # (c) Interruption AFTER inference, BEFORE submission: the lease is expired
    #     server-side inside the bridge's post-response hook, so the local check
    #     still passes and the SERVER decides. Then the same interleaving with a
    #     superseding claim, where the server must refuse.
    bridge.state.reset("happy")
    created3 = create_job(c)
    job3 = created3.json()["job_id"]
    worker3 = make_worker(bridge.base, api_base=api_base, worker_id="w-int-c")
    job_c = worker3.claim().job
    bridge.state.before_respond = lambda path, jid=job3: S1.expire_lease(engine, jid)
    try:
        envelope = worker3.process_job(job_c)
        raised_c = None
    except LeaseLost as exc:
        envelope, raised_c = None, exc
    finally:
        bridge.state.before_respond = None
    # Stage 2 O1: this block USED to assert that the expiry-straddling
    # submission still committed "because no generation superseded it" — the
    # defect itself. The server now refuses at the commit point, so the worker
    # maps the refusal to LeaseLost and stores nothing.
    check("O1: expiry committed before the submission is REFUSED (no unsuperseded commit)",
          isinstance(raised_c, LeaseLost) and "LEASE_EXPIRED" in str(raised_c),
          type(raised_c).__name__ if raised_c else json.dumps(envelope or {})[:120])
    check("O1: the refused late candidate stored nothing and left the job PROCESSING at the same generation",
          job_row(engine, job3)["status"] == "PROCESSING"
          and job_row(engine, job3)["result_json"] is None
          and job_row(engine, job3)["lease_generation"] == job_c.state.generation,
          json.dumps(job_row(engine, job3), default=str)[:200])
    # Expiry costs a RECLAIM, not the job: the next generation may commit it.
    # (This also drains job3 so the later scenarios are not handed leftover work.)
    reclaimed_c = claim_via_http(c, key=KEY_A, worker_id="w-int-c2")
    check("O1: the expired job is reclaimable at the NEXT generation after the refusal",
          reclaimed_c.status_code == 200
          and reclaimed_c.json().get("job", {}).get("job_id") == job3
          and reclaimed_c.json()["job"]["lease"]["generation"] == job_c.state.generation + 1,
          reclaimed_c.text[:200])
    lease_c2 = reclaimed_c.json()["job"]["lease"]
    fresh_c = submit_via_http(c, job3, lease_c2, BRIDGE_BODY, key=KEY_A, worker_id="w-int-c2")
    check("O1: the reclaimed generation then commits normally",
          fresh_c.status_code == 200 and fresh_c.json().get("api_status") == "VERIFIED",
          f"{fresh_c.status_code} {fresh_c.text[:160]}")

    bridge.state.reset("happy")
    created4 = create_job(c)
    job4 = created4.json()["job_id"]
    worker4 = make_worker(bridge.base, api_base=api_base, worker_id="w-int-d")
    job_d = worker4.claim().job

    superseded: dict = {}

    def supersede_before_submit(path, jid=job4):
        # While the worker is still waiting for its inference body, another
        # worker claims the job: the generation moves under its feet.
        S1.expire_lease(engine, jid)
        reclaimed = claim_via_http(c, key=KEY_A, worker_id="w-int-e")
        superseded["job_id"] = reclaimed.json().get("job", {}).get("job_id")
        superseded["generation"] = reclaimed.json().get("job", {}).get("lease", {}).get("generation")

    bridge.state.before_respond = supersede_before_submit
    try:
        worker4.process_job(job_d)
        raised_d = None
    except LeaseLost as exc:
        raised_d = exc
    check("the superseding claim in the race targeted the SAME job at a newer generation",
          superseded.get("job_id") == job4 and superseded.get("generation") == job_d.state.generation + 1,
          json.dumps(superseded))
    check("interruption after inference with a superseding claim: the worker maps the server's 409 to LeaseLost",
          isinstance(raised_d, LeaseLost), type(raised_d).__name__)
    check("the superseded worker's candidate was NOT committed",
          job_row(engine, job4)["result_json"] is None, json.dumps(job_row(engine, job4), default=str)[:160])
    bridge.state.before_respond = None

    # (d) Interruption WHILE the result request is in flight: proven at the unit
    #     level (an injected session that answers 409 mid-request), because the
    #     real server cannot be made to pause mid-transaction from here.
    from platrixa_outbound_inference_worker_test import (  # noqa: PLC0415
        FakeResponse,
        FakeSession,
        claim_body,
        error_body,
    )

    def inflight(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        return FakeResponse(409, error_body("LEASE_LOST", "generation moved during the request"))

    cfg = WorkerConfig(api_base_url=api_base, api_key=KEY_A, worker_id="w-int-f",
                       bridge_url=bridge.base, bridge_key=BRIDGE_KEY)
    unit_worker = OutboundInferenceWorker(cfg, session=FakeSession(inflight),
                                          bridge_session=FakeSession(lambda call: FakeResponse(200, BRIDGE_BODY)))
    unit_job = unit_worker.claim().job
    try:
        unit_worker.submit_result(unit_job, BRIDGE_BODY)
        raised_e = None
    except LeaseLost as exc:
        raised_e = exc
    check("UNIT: an in-flight 409 becomes LeaseLost and marks the lease lost",
          isinstance(raised_e, LeaseLost) and unit_job.state.is_lost, type(raised_e).__name__)

    check("no worker/heartbeat thread leaked across all interruption points",
          stray_threads() == [] and heartbeat_threads() == [],
          str({"stray": stray_threads(), "heartbeat": heartbeat_threads()}))


# ---------------------------------------------------------------------------
# Scenario 5 — renewal failure matrix
# ---------------------------------------------------------------------------


def s5_renewal_failures(c, engine, api_base, bridge) -> None:
    scenario("S5 renewal failures — worker distrust vs. server refusal")
    reset(engine)
    from platrixa_outbound_inference_worker_test import FakeResponse, FakeSession, claim_body, renewal_body

    # (a) transient failure then a real successful renewal against the real server.
    # A short lease for this block only: the server derives renew_every_seconds
    # from the lease (lease/3), so a 120s default would never renew inside a
    # test run. This is a local configuration value, not a production default.
    previous_lease = os.environ.get(async_jobs.INFERENCE_LEASE_ENV_VAR)
    os.environ[async_jobs.INFERENCE_LEASE_ENV_VAR] = "6"
    created = create_job(c)
    real_session = requests.Session()
    flaky = {"renewals": 0}

    class FlakySession:
        """The REAL transport, except the FIRST lease renewal fails."""

        def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
            if url.endswith("/lease"):
                flaky["renewals"] += 1
                if flaky["renewals"] == 1:
                    raise requests.ConnectionError("transient network failure")
            return real_session.post(url, json=json, headers=headers, timeout=timeout)

    cfg = WorkerConfig(api_base_url=api_base, api_key=KEY_A, worker_id="w-renew-a",
                       bridge_url=bridge.base, bridge_key=BRIDGE_KEY, heartbeat_tick_s=0.02)
    transient_worker = OutboundInferenceWorker(cfg, session=FlakySession(),
                                               bridge_session=FakeSession(lambda call: FakeResponse(200, BRIDGE_BODY)))
    job_t = transient_worker.claim().job
    check("the short-lease configuration produced a fast renew cadence",
          job_t.state.renew_every_seconds <= 2.0, str(job_t.state.renew_every_seconds))
    deadline_before = job_t.state.deadline
    expiry_before = job_row(engine, job_t.job_id)["lease_expires_at"]
    heartbeat = transient_worker.start_heartbeat(job_t.state)
    try:
        for _ in range(500):
            if job_t.state.renewals >= 1:
                break
            time.sleep(0.02)
        check("a transient renewal failure did NOT lose the lease",
              job_t.state.is_lost is False and job_t.state.renewal_failures >= 1,
              f"failures={job_t.state.renewal_failures} lost={job_t.state.lost_reason}")
        check("the successful retry extended the confirmed deadline",
              job_t.state.renewals >= 1 and job_t.state.deadline >= deadline_before,
              f"renewals={job_t.state.renewals}")
        expiry_after = job_row(engine, job_t.job_id)["lease_expires_at"]
        check("the real server accepted the retried renewal (stored lease pushed forward)",
              expiry_after is not None and expiry_before is not None and expiry_after > expiry_before,
              f"{expiry_before} -> {expiry_after}")
        envelope = transient_worker.submit_result(job_t, BRIDGE_BODY)
        check("after recovery the candidate commits normally", envelope.get("api_status") == "VERIFIED")
    finally:
        transient_worker.stop_heartbeat(heartbeat)
        if previous_lease is None:
            os.environ.pop(async_jobs.INFERENCE_LEASE_ENV_VAR, None)
        else:
            os.environ[async_jobs.INFERENCE_LEASE_ENV_VAR] = previous_lease

    # (b) repeated failures past the allowed window → lease lost locally.
    from platrixa_outbound_inference_worker_test import FakeClock

    clock = FakeClock(0.0)

    def always_fail(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body(lease_seconds=10.0, renew_every_seconds=1.0))
        raise requests.ConnectionError("api unreachable")

    cfg2 = WorkerConfig(api_base_url=api_base, api_key=KEY_A, worker_id="w-renew-b",
                        bridge_url=bridge.base, bridge_key=BRIDGE_KEY, heartbeat_tick_s=0.005)
    failing = OutboundInferenceWorker(cfg2, session=FakeSession(always_fail),
                                      bridge_session=FakeSession(lambda call: FakeResponse(200, BRIDGE_BODY)),
                                      now=clock)
    job_f = failing.claim().job
    hb = failing.start_heartbeat(job_f.state)
    try:
        clock.advance(1.5)
        time.sleep(0.15)
        check("UNIT/CLOCK: failures before the confirmed deadline do not lose the lease",
              job_f.state.is_lost is False and job_f.state.renewal_failures >= 1)
        clock.advance(20.0)
        check("UNIT/CLOCK: repeated failures past the deadline mark the lease lost",
              job_f.state.lost_event.wait(3.0) and "deadline" in (job_f.state.lost_reason or ""),
              job_f.state.lost_reason)
        try:
            failing.submit_result(job_f, BRIDGE_BODY)
            raised = None
        except LeaseLost as exc:
            raised = exc
        check("UNIT: a lease lost by deadline never submits", isinstance(raised, LeaseLost))
    finally:
        failing.stop_heartbeat(hb)

    # (c) 409 from the real server's renewal path (superseded generation).
    created3 = create_job(c)
    job3 = created3.json()["job_id"]
    lease3 = claim_via_http(c, worker_id="w-renew-c").json()["job"]["lease"]
    S1.expire_lease(engine, job3)
    reclaimed = claim_via_http(c, worker_id="w-renew-d").json()
    check("the superseding claim took the SAME job", reclaimed["job"]["job_id"] == job3, reclaimed["job"]["job_id"])
    claim_new = reclaimed["job"]["lease"]
    r409 = renew_via_http(c, job3, lease3, worker_id="w-renew-c")
    check("INTEGRATION: the real server answers 409 LEASE_LOST for a superseded renewal",
          r409.status_code == 409 and r409.json()["error"]["code"] == "LEASE_LOST",
          f"{r409.status_code} {r409.text[:140]}")
    worker409 = make_worker(bridge.base, api_base=api_base, worker_id="w-renew-c")
    state = worker409._build_claimed_job({
        "job_id": job3,
        "request_id": "req",
        "raw_input": RAW_INPUT,
        "lease": {"owner": lease3["owner"], "generation": lease3["generation"],
                  "lease_seconds": 120.0, "renew_every_seconds": 40.0,
                  "renew_url": f"/v1/worker/jobs/{job3}/lease",
                  "result_url": f"/v1/worker/jobs/{job3}/result"},
    })
    try:
        worker409.renew_lease(state.state)
        raised409 = None
    except LeaseLost as exc:
        raised409 = exc
    check("the worker maps the real 409 into LeaseLost", isinstance(raised409, LeaseLost), str(raised409))
    check("the new generation still owns the job", claim_new["generation"] == lease3["generation"] + 1)

    # (d) malformed / wrong-proof renewal bodies (unit, deterministic).
    for label, body in (
        ("no lease object", {"api_version": "v1", "renewed": True}),
        ("wrong owner", renewal_body(owner="someone-else", generation=1)),
        ("wrong generation", renewal_body(owner="w-audit-a", generation=99)),
    ):
        cfg3 = WorkerConfig(api_base_url=api_base, api_key=KEY_A, worker_id="w-audit-a",
                            bridge_url=bridge.base, bridge_key=BRIDGE_KEY)
        w = OutboundInferenceWorker(cfg3, session=FakeSession(lambda call, b=body: FakeResponse(200, b)),
                                    bridge_session=FakeSession(lambda call: FakeResponse(200, BRIDGE_BODY)))
        st = w._build_claimed_job(claim_body()["job"]).state
        try:
            w.renew_lease(st)
            bad = None
        except Exception as exc:  # noqa: BLE001 - the type IS the assertion
            bad = exc
        check(f"UNIT: a {label} renewal body is refused as unverifiable", type(bad).__name__ == "MalformedResponseError",
              type(bad).__name__)


# ---------------------------------------------------------------------------
# Scenario 6 — bridge failure matrix against the real worker
# ---------------------------------------------------------------------------


def s6_bridge_failures(c, engine, api_base, bridge) -> None:
    scenario("S6 bridge failures — never submitted as a valid candidate")
    reset(engine)

    cases = [
        ("malformed JSON", "notjson", BridgeResponseError),
        ("non-object JSON", "nonobject", BridgeResponseError),
        ("oversized body", "oversized", BridgeResponseError),
        ("HTTP 500", "500", BridgeUnavailableError),
        ("connection closed mid-request", "close", BridgeUnavailableError),
        ("bridge timeout", "slow", BridgeUnavailableError),
    ]
    for label, mode, expected in cases:
        bridge.state.reset(mode)
        created = create_job(c)
        job_id = created.json()["job_id"]
        worker = make_worker(bridge.base, api_base=api_base, worker_id="w-bridge-x",
                             bridge_timeout_s=0.4 if mode == "slow" else 10.0)
        job = worker.claim().job
        try:
            worker.process_job(job)
            raised = None
        except Exception as exc:  # noqa: BLE001 - the type IS the assertion
            raised = exc
        row = job_row(engine, job_id)
        check(f"{label}: the worker fails closed with {expected.__name__}",
              isinstance(raised, expected), type(raised).__name__)
        check(f"{label}: nothing was submitted (job PROCESSING, no result)",
              row["status"] == "PROCESSING" and row["result_json"] is None,
              json.dumps(row, default=str)[:140])
        check(f"{label}: the bridge never saw the tenant API key",
              all("x-platrixa-api-key" not in h for h in bridge.state.headers))

    # connection-error short-circuit: exactly ONE connection attempt.
    bridge.state.reset("close")
    created = create_job(c)
    worker = make_worker(bridge.base, api_base=api_base, worker_id="w-bridge-y")
    job = worker.claim().job
    try:
        worker.process_job(job)
    except BridgeUnavailableError:
        pass
    check("connection error short-circuits: /interpret is the ONLY path attempted (no / fallback)",
          bridge.state.paths == ["/interpret"], str(bridge.state.paths))

    # 404 on /interpret → the documented fallback to "/" succeeds.
    bridge.state.reset("fallback")
    created = create_job(c)
    job_id = created.json()["job_id"]
    worker = make_worker(bridge.base, api_base=api_base, worker_id="w-bridge-z")
    job = worker.claim().job
    envelope = worker.process_job(job)
    check("404 on /interpret falls back to / and commits",
          bridge.state.paths == ["/interpret", "/"] and envelope.get("api_status") == "VERIFIED",
          str(bridge.state.paths))

    # 405 on /interpret → same fallback rule.
    bridge.state.reset("405")
    created = create_job(c)
    worker = make_worker(bridge.base, api_base=api_base, worker_id="w-bridge-w")
    job = worker.claim().job
    worker.process_job(job)
    check("405 on /interpret also falls back to /", bridge.state.paths == ["/interpret", "/"], str(bridge.state.paths))

    # connection refused (nothing listening).
    dead = f"http://127.0.0.1:{_free_port()}"
    created = create_job(c)
    job_id = created.json()["job_id"]
    worker = make_worker(dead, api_base=api_base, worker_id="w-bridge-v", bridge_timeout_s=2.0)
    job = worker.claim().job
    try:
        worker.process_job(job)
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("connection refused: BridgeUnavailableError, nothing submitted",
          isinstance(raised, BridgeUnavailableError) and job_row(engine, job_id)["result_json"] is None,
          type(raised).__name__)

    # A truncated/incomplete candidate is submitted VERBATIM and the SERVER
    # rejects it — the worker does not repair or reinterpret it.
    bridge.state.reset("happy")
    truncated = S1.bridge_body(S1.VALID_CANDIDATE, completion_status="TRUNCATED", finish_reason="length")
    bridge.state.mode = "truncated"
    _orig_respond = _BridgeHandler._respond

    def _respond(self, mode):
        if mode == "truncated":
            return 200, truncated
        return _orig_respond(self, mode)

    _BridgeHandler._respond = _respond
    try:
        created = create_job(c)
        job_id = created.json()["job_id"]
        worker = make_worker(bridge.base, api_base=api_base, worker_id="w-bridge-u")
        job = worker.claim().job
        try:
            envelope = worker.process_job(job)
            raised_t = None
        except Exception as exc:  # noqa: BLE001 - recording the observed type is the point
            envelope, raised_t = None, exc
        row_t = job_row(engine, job_id)
        stored_t = json.loads(row_t["result_json"]) if row_t["result_json"] else None
        check("a TRUNCATED candidate is transported VERBATIM (the worker does not repair or reinterpret it)",
              bridge.state.served_bodies[-1] == truncated,
              json.dumps(bridge.state.served_bodies[-1])[:160])
        check("the server's stored envelope keeps the engine's own terminal state (never VERIFIED)",
              stored_t is not None and stored_t.get("api_status") != "VERIFIED"
              and stored_t.get("status") != "VERIFIED",
              json.dumps({k: (stored_t or {}).get(k) for k in ("status", "api_status")}))
        print(f"    observed: worker outcome={type(raised_t).__name__ or 'returned envelope'} "
              f"| job={row_t['status']} | http_status={row_t['http_status']} "
              f"| api_status={ (stored_t or {}).get('api_status') }")
    finally:
        _BridgeHandler._respond = _orig_respond


# ---------------------------------------------------------------------------
# Scenario 7 — idempotency and stored-result consistency
# ---------------------------------------------------------------------------


def s7_idempotency(c, engine, api_base, bridge) -> None:
    scenario("S7 idempotency — one job, one reservation, one authoritative commit")
    reset(engine)
    bridge.state.reset("happy")
    usage_before = S1.tenant_usage(engine, KEY_A)

    idem = "audit-idem-key-0001"
    first = create_job(c, idem=idem)
    second = create_job(c, idem=idem)
    check("a duplicate creation with the same Idempotency-Key returns the SAME job",
          first.status_code == 202 and second.status_code == 202
          and first.json()["job_id"] == second.json()["job_id"],
          f"{first.status_code}/{second.status_code}")
    check("the duplicate did not reserve a second quota unit",
          S1.tenant_usage(engine, KEY_A) == usage_before + 1,
          f"{S1.tenant_usage(engine, KEY_A)} != {usage_before + 1}")
    check("the replay is marked Idempotent-Replayed: true",
          second.headers.get("Idempotent-Replayed") == "true",
          str(second.headers.get("Idempotent-Replayed")))
    check("only ONE inference job row exists for that idempotency key",
          S1.inference_job_count(engine) == 1, str(S1.inference_job_count(engine)))

    job_id = first.json()["job_id"]
    result_id = first.json()["result_id"]
    worker = make_worker(bridge.base, api_base=api_base, worker_id="w-idem-a")
    job = worker.claim().job
    envelope = worker.process_job(job)

    bridge_calls_after = len(bridge.state.paths)
    replay = submit_via_http(c, job_id, job.state.proof.to_wire(), BRIDGE_BODY, worker_id="w-idem-a")
    check("a duplicate result submission at the SAME generation replays (200, no new commit)",
          replay.status_code == 200 and replay.headers.get("Idempotent-Replayed") == "true",
          f"{replay.status_code} {replay.headers.get('Idempotent-Replayed')}")
    check("the replay returned the identical stored envelope",
          replay.json() == envelope and replay.json() == json.loads(job_row(engine, job_id)["result_json"]))
    check("the duplicate submission triggered NO second inference locally",
          len(bridge.state.paths) == bridge_calls_after, str(bridge.state.paths))

    stale = submit_via_http(c, job_id, {"owner": job.state.owner, "generation": job.state.generation + 5},
                            BRIDGE_BODY, worker_id="w-idem-a")
    check("a bogus (future) generation cannot overwrite: it replays the stored result",
          stale.status_code == 200 and stale.json() == envelope
          and job_row(engine, job_id)["result_json"] == json.dumps(envelope),
          f"{stale.status_code}")

    final = c.get(f"/v1/results/{result_id}", headers={"X-Platrixa-API-Key": KEY_A})
    check("the final stored result read back through the API is consistent",
          final.status_code == 200 and final.json() == envelope
          and final.json()["api_status"] == "VERIFIED"
          and final.json().get("request_id") == envelope.get("request_id"),
          f"{final.status_code}")
    check("one job, one attempt, one commit",
          job_row(engine, job_id)["attempt_count"] == 1 and job_row(engine, job_id)["status"] == "COMPLETED")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def s8_poll_loop_liveness(c, engine, api_base, bridge) -> None:
    """A deterministic rejection of ONE candidate must not stop the worker.

    The server answers 422 for a candidate the deterministic pipeline refuses
    (here: a TRUNCATED generation). The job is TERMINAL from the server's point
    of view, so the device worker must keep serving the next job instead of
    tearing itself down.
    """
    scenario("S8 poll-loop liveness — a terminal 422 must not kill the worker")
    reset(engine)
    bridge.state.reset("happy")
    truncated = S1.bridge_body(S1.VALID_CANDIDATE, completion_status="TRUNCATED", finish_reason="length")
    # First inference: the pipeline rejects it. Second: the canonical input.
    bridge.state.responses = [(200, truncated), (200, BRIDGE_BODY)]

    rejected = create_job(c).json()
    follow = create_job(c).json()

    config = WorkerConfig(
        api_base_url=api_base, api_key=KEY_A, worker_id="w-live-a",
        bridge_url=bridge.base, bridge_key=BRIDGE_KEY, heartbeat_tick_s=0.02,
    )
    worker = OutboundInferenceWorker(config, sleep=lambda seconds: None)  # no real waiting
    try:
        completed = worker.run_forever(max_polls=3)
        raised = None
    except BaseException as exc:  # noqa: BLE001 - the escaping exception is the observation
        completed, raised = None, exc
    check("the poll loop survives a deterministic 422 and still serves the NEXT job",
          raised is None and completed == 1,
          f"raised={type(raised).__name__}: {raised} | completed={completed}")
    check("the rejected job is terminal in the store with a non-VERIFIED engine state",
          job_row(engine, rejected["job_id"])["status"] == "COMPLETED"
          and json.loads(job_row(engine, rejected["job_id"])["result_json"])["api_status"] != "VERIFIED",
          json.dumps(job_row(engine, rejected["job_id"]), default=str)[:180])
    check("the following job committed normally after the rejection",
          job_row(engine, follow["job_id"])["status"] == "COMPLETED"
          and json.loads(job_row(engine, follow["job_id"])["result_json"])["api_status"] == "VERIFIED",
          json.dumps(job_row(engine, follow["job_id"]), default=str)[:180])
    check("no heartbeat thread survived the rejection", heartbeat_threads() == [], str(heartbeat_threads()))


def main() -> int:
    print("=" * 78)
    print("Stage 2 lease-safety audit — isolated integration suite")
    print("=" * 78)
    print("isolation guardrails: OK (loopback only, no PLATRIXA_* origin, no real credential)")

    srv, engine = S1.pg_backend()
    print(f"embedded PostgreSQL ready (disposable cluster under {S1.Path('/tmp/platrixa_fte89_pgdata')})")
    stub = S1.HarnessClient()
    developer.set_client(stub)
    app = create_app()
    c = TestClient(app)
    bridge_server, bridge_url = start_bridge()
    api_server, api_thread, api_base = start_api(app)
    print(f"real API on {api_base} (uvicorn, loopback only) | mock bridge on {bridge_url}")
    check("the audit API origin is loopback", _is_loopback(api_base), api_base)
    check("the mock bridge origin is loopback", _is_loopback(bridge_url), bridge_url)

    class _Bridge:
        pass

    bridge = _Bridge()
    bridge.base = bridge_url
    bridge.state = bridge_server.state  # type: ignore[attr-defined]

    try:
        s1_happy_path(c, engine, api_base, bridge)
        s2_lease_expiry(c, engine, api_base, bridge)
        s3_competing_workers(c, engine, api_base, bridge)
        s4_interruption(c, engine, api_base, bridge)
        s5_renewal_failures(c, engine, api_base, bridge)
        s6_bridge_failures(c, engine, api_base, bridge)
        s7_idempotency(c, engine, api_base, bridge)
        s8_poll_loop_liveness(c, engine, api_base, bridge)
    finally:
        developer.reset_client()
        bridge_server.shutdown()
        api_server.should_exit = True
        api_thread.join(timeout=5)
        try:
            srv.cleanup()
        except Exception as exc:  # pragma: no cover
            print(f"note: pgserver cleanup raised {type(exc).__name__}: {exc}")
        # The embedded cluster's data directory is disposable: remove it so this
        # suite leaves no temporary database behind.
        shutil.rmtree(str(_PG_DATA), ignore_errors=True)

    passed = sum(1 for _, ok, _, _ in CHECKS if ok)
    total = len(CHECKS)
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{total} integration checks passed")
    for name, ok, detail, kind in CHECKS:
        if not ok:
            print(f"  FAIL ({kind}) {name} — {detail}")
    print("=" * 78)

    leftovers = stray_threads() + heartbeat_threads()
    print(f"threads still alive after teardown: {leftovers}")
    return 0 if passed == total and not leftovers else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
