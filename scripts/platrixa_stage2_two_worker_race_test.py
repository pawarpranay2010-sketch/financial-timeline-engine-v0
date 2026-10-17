#!/usr/bin/env python3
"""Stage 2 — DETERMINISTIC live two-worker concurrency test (isolated).

Why this file exists
====================
The Stage 2 lease-safety audit (`platrixa_stage2_lease_safety_integration_test.py`)
proved the server-side generation fence with sequential actors. The remaining
question was whether the fence still holds when TWO live workers actually
contend for one job over real sockets — a stale worker that finishes inference
late, after a newer generation already owns the job.

This suite drives that race for real:

  * the REAL FastAPI application (`api.main.create_app`) served by ``uvicorn`` on
    a loopback socket — every request below is a real HTTP request;
  * the REAL PostgreSQL-backed job store (`backend/auth/async_jobs.py`) in a
    disposable embedded cluster;
  * the REAL claim / lease-renewal / result routes and their fenced SQL;
  * TWO real ``OutboundInferenceWorker`` instances with DIFFERENT worker
    identities, sharing ONE authenticated tenant (a claim is tenant-scoped, so
    only workers inside the same tenant can ever contend for the same job —
    asserted in S0, not assumed).

The ONLY substitution is the device-side model bridge (a controllable
``http.server`` mock): the expensive inference, never the claim or the commit.

Determinism
-----------
Ordering is carried by ``threading.Event`` barriers, each with a bounded
timeout — no arbitrary sleep decides an outcome:
  ``hold_arrived``  the bridge holds A's inference request open;
  ``release_a``     the test lets A's inference finish, so A then submits;
  ``b_claimed``     worker B holds the newer generation.
``RecordingSession`` re-checks ``b_claimed`` *inside* A's own result request, so
"A submitted after B had the newer generation" is a hard assertion rather than a
timing observation.

Lease expiry has two variants and both are exercised:
  * ``store`` — the server-side expiry EVENT is set in the disposable store
    (``expire_lease``), i.e. how a crashed/partitioned worker looks to the
    server. B's claim, A's submission and B's submission still go through the
    real routes.
  * ``real``  — NO store mutation: a 2-second server lease plus a non-renewing
    worker, and B poll-claims (bounded) until the SERVER's own lease clock lets
    it in. The server is the oracle for expiry.

Isolation
---------
Importing the Stage 2 suite runs its guardrails (every PLATRIXA_* origin must be
loopback; no real credential may be set) before the app is imported. Every
origin used here is loopback, the store is a throwaway embedded cluster, the keys
are disposable, and nothing is pushed, deployed or committed.

Run:
    .venv/bin/python scripts/platrixa_stage2_two_worker_race_test.py [iterations]
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import requests  # noqa: E402

# Importing this module executes its isolation guardrails and imports the app.
import platrixa_stage2_lease_safety_integration_test as S2  # noqa: E402
import fte_fyjc_89_outbound_inference_bridge_test as S1  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import async_jobs  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    LeaseLost,
    OutboundInferenceWorker,
    WorkerConfig,
)

# ONE tenant, TWO worker identities. A claim is tenant-scoped, so two workers can
# only ever contend for the same job inside one authenticated tenant; S0 asserts
# that constraint instead of assuming it.
KEY = S2.KEY_A
WORKER_A = "race-worker-a"
WORKER_B = "race-worker-b"
OTHER_TENANT_WORKER = "race-worker-other-tenant"
CREATOR = "race-job-creator"
BRIDGE_KEY_A = "race-bridge-key-a"
BRIDGE_KEY_B = "race-bridge-key-b"
RAW_INPUT = S1.RAW_INPUT

# Worker A's candidate carries a marker. If a stale write ever landed, the marker
# would appear in the stored envelope — the positive "A did not overwrite B"
# proof on top of byte-equality with B's committed envelope.
STALE_MARKER = "marker-stale-worker-a"
_STALE_CANDIDATE = copy.deepcopy(S1.VALID_CANDIDATE)
_STALE_CANDIDATE["ambiguities"] = [STALE_MARKER]
BODY_A = S1.bridge_body(_STALE_CANDIDATE)
BODY_B = S1.bridge_body(S1.VALID_CANDIDATE)

# The disposable embedded cluster the shared Stage 1 harness provisions.
_PG_DATA = Path("/tmp/platrixa_fte89_pgdata")

LEASE_ENV = async_jobs.INFERENCE_LEASE_ENV_VAR
LEASE_STORE_MODE = "60"   # long enough that A's local guard cannot fire first
LEASE_REAL_MODE = "2"     # the shortest server lease that lets B reclaim quickly

check = S2.check
scenario = S2.scenario


# ---------------------------------------------------------------------------
# Real HTTP helpers (loopback uvicorn — not an in-process ASGI shim)
# ---------------------------------------------------------------------------


def _headers(worker_id: str, key: str = KEY) -> dict:
    return {"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id}


def create_job(api_base: str, *, key: str = KEY, raw_input: str = RAW_INPUT):
    return requests.post(
        f"{api_base}/v1/inference-jobs",
        json={"raw_input": raw_input},
        headers=_headers(CREATOR, key),
        timeout=15,
    )


def http_claim(api_base: str, worker_id: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/worker/jobs/claim", headers=_headers(worker_id, key), timeout=15
    )


def http_submit(api_base: str, job_id: str, lease: dict, body: dict, worker_id: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/worker/jobs/{job_id}/result",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]},
              "bridge_body": body},
        headers=_headers(worker_id, key),
        timeout=15,
    )


def read_result(api_base: str, result_id: str, *, key: str = KEY):
    return requests.get(
        f"{api_base}/v1/results/{result_id}", headers=_headers("race-result-reader", key), timeout=15
    )


class RecordingSession:
    """The REAL transport, recording every ``/result`` response verbatim.

    ``gate`` carries the ordering barrier: ``b_claimed`` must already be set when
    A builds its own result request, which makes "A submitted after B held the
    newer generation" an in-request assertion.
    """

    def __init__(self, gate: dict | None = None) -> None:
        self.inner = requests.Session()
        self.results: list = []
        self._gate = gate or {}

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        if url.endswith("/result"):
            event = self._gate.get("b_claimed")
            if event is not None:
                assert event.is_set(), "worker A submitted before B held the newer generation"
        response = self.inner.post(url, json=json, headers=headers, timeout=timeout)
        if url.endswith("/result"):
            self.results.append({
                "status": response.status_code,
                "body": response.text,
                "replayed": response.headers.get("Idempotent-Replayed"),
                "at": time.monotonic(),
            })
        return response


def build_worker(api_base, bridge_url, *, worker_id, bridge_key, session=None, tick=30.0, sleep=None):
    config = WorkerConfig(
        api_base_url=api_base,
        api_key=KEY,
        worker_id=worker_id,
        bridge_url=bridge_url,
        bridge_key=bridge_key,
        bridge_timeout_s=10.0,
        heartbeat_tick_s=tick,
        shutdown_timeout_s=5.0,
    )
    return OutboundInferenceWorker(
        config,
        session=session if session is not None else requests.Session(),
        bridge_session=requests.Session(),
        sleep=sleep,
    )


class Assertions:
    """Collects invariant failures so a race reports ALL of them, not the first."""

    def __init__(self) -> None:
        self.failures: list = []

    def require(self, condition, message) -> bool:
        if not condition:
            self.failures.append(message)
        return bool(condition)


def heartbeat_alive() -> bool:
    return any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())


def _row_status(row) -> dict:
    if row is None:
        return {}
    return {
        "status": row["status"],
        "lease_owner": row["lease_owner"],
        "lease_generation": row["lease_generation"],
        "attempt_count": row["attempt_count"],
        "has_result": row["result_json"] is not None,
    }


# ---------------------------------------------------------------------------
# S0 — tenant scoping (why two contending workers must share one tenant)
# ---------------------------------------------------------------------------


def s0_tenant_scoping(api_base, engine, bridge) -> None:
    scenario("S0 tenant scoping — separate worker identities, ONE tenant (required to contend)")
    S2.reset(engine)
    bridge.state.reset("happy")

    job = create_job(api_base).json()
    lease_a = http_claim(api_base, WORKER_A).json()["job"]["lease"]
    check("worker A claimed the job and the claim names A as the lease owner",
          lease_a["owner"] == WORKER_A and lease_a["generation"] >= 1, json.dumps(lease_a))

    other = http_claim(api_base, OTHER_TENANT_WORKER, key=S2.KEY_B)
    check("a worker authenticated as a DIFFERENT tenant cannot see or claim this job (NO_WORK)",
          other.status_code == 200 and other.json()["status"] == "NO_WORK",
          f"{other.status_code} {other.text[:140]}")
    check("the job is untouched by the foreign-tenant claim (still owned by A)",
          S2.job_row(engine, job["job_id"])["lease_owner"] == WORKER_A)
    check("a bare claim starts no heartbeat thread (the heartbeat is per processed job)",
          heartbeat_alive() is False)


# ---------------------------------------------------------------------------
# S1/S2 — the deterministic two-worker race
# ---------------------------------------------------------------------------


def race_once(api_base, engine, bridge, *, mode: str, b_commits_first: bool) -> dict:
    """One deterministic race. Returns evidence; failures are collected, not raised."""
    S2.reset(engine)
    bridge.state.reset("happy")
    os.environ[LEASE_ENV] = LEASE_STORE_MODE if mode == "store" else LEASE_REAL_MODE

    hold_arrived = threading.Event()
    release_a = threading.Event()
    b_claimed = threading.Event()

    def hold_a(_path: str) -> None:
        """Hold ONLY worker A's inference request open (keyed on its bridge key)."""
        headers = bridge.state.headers[-1] if bridge.state.headers else {}
        if headers.get("x-platrixa-android-bridge-key") != BRIDGE_KEY_A:
            return  # B never calls the bridge here; never hold anyone else
        hold_arrived.set()
        release_a.wait(30)

    bridge.state.before_respond = hold_a
    bridge.state.responses = [(200, BODY_A)]  # A's candidate, carrying the marker

    a = Assertions()
    evidence: dict = {"mode": mode, "order": "b_first" if b_commits_first else "a_first"}

    created = create_job(api_base)
    a.require(created.status_code == 202, f"job creation: {created.status_code} {created.text[:120]}")
    job_id = created.json()["job_id"]
    result_id = created.json()["result_id"]

    recording_a = RecordingSession({"b_claimed": b_claimed})
    worker_a = build_worker(api_base, bridge.base, worker_id=WORKER_A,
                            bridge_key=BRIDGE_KEY_A, session=recording_a)
    worker_b = build_worker(api_base, bridge.base, worker_id=WORKER_B, bridge_key=BRIDGE_KEY_B)

    # --- worker A claims generation G, then its inference is held open --------
    claim_a = worker_a.claim()
    if claim_a.job is None:
        a.require(False, "worker A could not claim the job")
        evidence["failures"] = a.failures
        bridge.state.before_respond = None
        return evidence
    job_a = claim_a.job
    evidence["owner_a"] = job_a.state.owner
    evidence["gen_a"] = job_a.state.generation

    outcome: dict = {}

    def run_a():
        try:
            outcome["envelope"] = worker_a.process_job(job_a)
        except BaseException as exc:  # noqa: BLE001 - recorded, then asserted on
            outcome["error"] = exc

    thread_a = threading.Thread(target=run_a, name="race-worker-a", daemon=True)
    thread_a.start()
    a.require(hold_arrived.wait(15), "worker A never reached the bridge within the bound")
    a.require(job_a.state.renewals == 0, "a renewal slipped into the race window (heartbeat tick)")
    a.require(job_a.state.is_lost is False, "A's lease was already lost before the race began")
    a.require(bridge.state.paths == ["/interpret"], f"A's bridge calls: {bridge.state.paths}")

    # --- the server-side lease goes away and worker B takes over --------------
    if mode == "store":
        # The server-side expiry EVENT, in the disposable store: how a crashed or
        # partitioned worker looks to the server. The claim/commit paths below are
        # real routes, untouched.
        S1.expire_lease(engine, job_id)
        claim_b = worker_b.claim()
        a.require(claim_b.job is not None, "B's claim after the lease expiry found no work")
        job_b = claim_b.job
        t_b_claim = time.monotonic()
    else:
        # NO store mutation: B polls until the SERVER's own lease clock expires.
        job_b, t_b_claim, deadline = None, None, time.monotonic() + 25
        while time.monotonic() < deadline:
            attempt = worker_b.claim()
            if attempt.job is not None:
                job_b, t_b_claim = attempt.job, time.monotonic()
                break
            time.sleep(0.1)
        a.require(job_b is not None, "B could not claim the job inside the bounded window")

    if job_b is None:
        release_a.set()
        thread_a.join(timeout=15)
        bridge.state.before_respond = None
        evidence["failures"] = a.failures
        return evidence

    lease_b = job_b.state.proof.to_wire()
    a.require(job_b.job_id == job_id, f"B claimed a different job: {job_b.job_id}")
    a.require(lease_b["owner"] == WORKER_B, f"B's lease owner: {lease_b['owner']}")
    a.require(lease_b["generation"] == job_a.state.generation + 1,
              f"generations must be strictly newer: A={job_a.state.generation} B={lease_b['generation']}")
    evidence.update({"owner_b": lease_b["owner"], "gen_b": lease_b["generation"],
                     "t_b_claim": t_b_claim, "holding_b": _row_status(S2.job_row(engine, job_id))})
    b_claimed.set()

    # --- order B: B commits first; then A's stale candidate arrives -----------
    if b_commits_first:
        envelope_b = worker_b.submit_result(job_b, BODY_B)
        a.require(envelope_b.get("api_status") == "VERIFIED",
                  f"B's commit status: {envelope_b.get('api_status')}")
        stored_b = S2.job_row(engine, job_id)["result_json"]
        evidence["b_envelope"] = envelope_b
        evidence["stored_after_b"] = stored_b
        a.require(stored_b is not None, "B's commit stored no result")

    # --- release A's held inference: A now submits its stale candidate --------
    release_a.set()
    thread_a.join(timeout=20)
    a.require(not thread_a.is_alive(), "worker A never finished its stale submission")

    a_result = recording_a.results[-1] if recording_a.results else None
    evidence["a_result"] = a_result
    evidence["a_error"] = type(outcome.get("error")).__name__ if outcome.get("error") else None

    if mode == "real":
        # A's local guard may legitimately expire its own deadline before it can
        # submit. Whatever the worker decided, the SERVER's verdict on A's stale
        # proof is captured directly here.
        raw = http_submit(api_base, job_id, job_a.state.proof.to_wire(), BODY_A, WORKER_A)
        evidence["a_raw_stale"] = {"status": raw.status_code, "body": raw.text[:400],
                                   "at": time.monotonic()}
        a.require(raw.status_code == 409,
                  f"the server must refuse A's stale proof, got {raw.status_code} {raw.text[:160]}")
        a.require(raw.json().get("error", {}).get("code") == "LEASE_LOST",
                  f"unexpected stale-refusal body: {raw.text[:160]}")
    else:
        a.require(a_result is not None,
                  "worker A never sent its stale submission (the fence was not exercised)")
        if a_result is not None:
            a.require(a_result["at"] > t_b_claim, "A's result request must be after B's claim")

    row_after_a = S2.job_row(engine, job_id)
    evidence["row_after_a"] = _row_status(row_after_a)

    # --- the fence must have refused A, and nothing of A's may be stored ------
    if not b_commits_first:
        if mode == "store" and a_result is not None:
            a.require(a_result["status"] == 409,
                      f"expected 409 LEASE_LOST for the stale generation, got {a_result['status']}")
            a.require(
                json.loads(a_result["body"] or "{}").get("error", {}).get("code") == "LEASE_LOST",
                f"unexpected stale-rejection body: {a_result['body'][:160]}",
            )
        if mode == "store":
            a.require(isinstance(outcome.get("error"), LeaseLost),
                      f"A's worker must map the refusal to LeaseLost, got {evidence['a_error']}")
        a.require(row_after_a["status"] == "PROCESSING" and row_after_a["result_json"] is None,
                  f"A's stale candidate must not have been committed: {evidence['row_after_a']}")
        a.require(row_after_a["lease_owner"] == WORKER_B,
                  f"the lease must still belong to B, got {row_after_a['lease_owner']}")

        envelope_b = worker_b.submit_result(job_b, BODY_B)
        a.require(envelope_b.get("api_status") == "VERIFIED",
                  f"B's commit status: {envelope_b.get('api_status')}")
        evidence["b_envelope"] = envelope_b
    else:
        # Order B: B already committed, so A's late arrival must be a REPLAY of
        # the stored authoritative result — recorded separately from a 409.
        stored_b = evidence.get("stored_after_b")
        if a_result is not None:
            a.require(a_result["status"] == 200,
                      f"a post-commit stale arrival replays (200), got {a_result['status']}")
            a.require(a_result["replayed"] == "true",
                      f"the replay must be labelled Idempotent-Replayed: true, got {a_result['replayed']}")
            # The route re-serializes the stored envelope (JSONResponse uses
            # compact separators), so compare the parsed bodies, not the bytes.
            a.require(json.loads(a_result["body"]) == json.loads(stored_b),
                      "the replayed body must be the STORED authoritative result")
            a.require(json.loads(a_result["body"]) == evidence.get("b_envelope"),
                      "the replay must carry B's committed envelope")
        a.require(row_after_a["result_json"] == stored_b,
                  "A's stale candidate must not have changed the stored result")

    # --- final authoritative state -------------------------------------------
    final = S2.job_row(engine, job_id)
    evidence["final"] = _row_status(final)
    envelope_b = evidence.get("b_envelope") or {}
    a.require(final["status"] == "COMPLETED", f"final status: {final['status']}")
    a.require(final["lease_owner"] is None and final["lease_expires_at"] is None,
              "the committed job must have released its lease")
    a.require(final["lease_generation"] == lease_b["generation"],
              f"final generation: {final['lease_generation']} != B's {lease_b['generation']}")
    a.require(final["result_json"] is not None, "no authoritative result was stored")
    if final["result_json"] is not None:
        a.require(json.loads(final["result_json"]) == envelope_b,
                  "the stored result must be exactly the envelope B committed")
        a.require(STALE_MARKER not in final["result_json"],
                  "A's stale candidate marker leaked into the authoritative result")
    served = read_result(api_base, result_id)
    a.require(served.status_code == 200 and served.json() == envelope_b,
              f"the result route must serve the same authoritative envelope ({served.status_code})")
    a.require(heartbeat_alive() is False, "a heartbeat thread leaked out of the race")

    bridge.state.before_respond = None
    evidence["failures"] = a.failures
    return evidence


def _report_races(label: str, runs: list) -> None:
    failures = [(i, ev["failures"]) for i, ev in enumerate(runs) if ev.get("failures")]
    check(f"{label}: all {len(runs)} races satisfied every invariant", not failures,
          f"{len(failures)} failed iteration(s): {failures[:2]}")


def s1_race_orders(api_base, engine, bridge, iterations: int) -> None:
    scenario(f"S1 deterministic two-worker race — {iterations} iterations per arrival order")
    order1 = [race_once(api_base, engine, bridge, mode="store", b_commits_first=False)
              for _ in range(iterations)]
    _report_races("order A-stale-first (A refused 409 while B holds the newer generation)", order1)
    order2 = [race_once(api_base, engine, bridge, mode="store", b_commits_first=True)
              for _ in range(iterations)]
    _report_races("order B-commits-first (A's late arrival replayed, never overwrote)", order2)

    sample1 = order1[-1]
    check("every race used two separate worker identities with the same tenant",
          all(ev.get("owner_a") == WORKER_A and ev.get("owner_b") == WORKER_B for ev in order1 + order2))
    check("every race gave B a strictly newer generation than A",
          all(ev.get("gen_b") == ev.get("gen_a", 0) + 1 for ev in order1 + order2),
          str([(ev.get("gen_a"), ev.get("gen_b")) for ev in order1 + order2][:3]))
    check("the stale-generation refusal was a real 409 LEASE_LOST from the real API",
          sample1.get("a_result", {}).get("status") == 409
          and json.loads(sample1["a_result"]["body"]).get("error", {}).get("code") == "LEASE_LOST",
          json.dumps(sample1.get("a_result"))[:200])
    check("the post-commit stale arrival was a labelled replay, distinct from the 409 rejection",
          order2[-1].get("a_result", {}).get("replayed") == "true",
          json.dumps(order2[-1].get("a_result"))[:200])


def s2_real_expiry(api_base, engine, bridge, iterations: int) -> None:
    scenario(f"S2 real server-clock expiry — NO store mutation, {iterations} iterations")
    runs = [race_once(api_base, engine, bridge, mode="real", b_commits_first=False)
            for _ in range(iterations)]
    _report_races("real-clock expiry (B reclaimed only once the server's lease expired)", runs)
    check("in every real-clock race the server refused A's stale proof with 409 LEASE_LOST",
          all(ev.get("a_raw_stale", {}).get("status") == 409 for ev in runs),
          str([ev.get("a_raw_stale") for ev in runs][:2]))


# ---------------------------------------------------------------------------
# S3 — one real worker serves job after job; only stop() ends it
# ---------------------------------------------------------------------------


def s3_worker_lifecycle(api_base, engine, bridge) -> None:
    scenario("S3 worker lifecycle on the real server — two jobs in one run, then stop()")
    S2.reset(engine)
    bridge.state.reset("happy")
    os.environ[LEASE_ENV] = LEASE_STORE_MODE

    first = create_job(api_base).json()
    second = create_job(api_base).json()

    boundaries: list = []
    worker = build_worker(api_base, bridge.base, worker_id="lifecycle-worker",
                          bridge_key=BRIDGE_KEY_B, sleep=lambda seconds: None)

    def on_result(job, envelope):
        boundaries.append({"job": job.job_id, "shutdown_set": worker.stop_event.is_set(),
                           "heartbeat_alive": heartbeat_alive(),
                           "status": envelope.get("api_status")})

    completed = worker.run_forever(max_polls=2, on_result=on_result)
    check("ONE real worker served BOTH queued jobs in a single run_forever call", completed == 2,
          f"completed={completed}")
    check("both jobs are COMPLETED with a VERIFIED authoritative result in the real store",
          all(S2.job_row(engine, j["job_id"])["status"] == "COMPLETED" for j in (first, second))
          and all(json.loads(S2.job_row(engine, j["job_id"])["result_json"])["api_status"] == "VERIFIED"
                  for j in (first, second)))
    check("the worker-wide shutdown signal was unset at every job boundary",
          bool(boundaries) and all(b["shutdown_set"] is False for b in boundaries), str(boundaries))
    check("no heartbeat thread was alive at any job boundary",
          all(b["heartbeat_alive"] is False for b in boundaries), str(boundaries))
    check("the worker-wide shutdown signal is still unset after serving two jobs",
          worker.stop_event.is_set() is False)

    # The real shutdown mechanism, plus the negative control.
    third = create_job(api_base).json()
    worker.stop()
    check("stop() sets the worker-wide shutdown signal (the flag is not trivially false)",
          worker.stop_event.is_set() is True)
    again = worker.run_forever(max_polls=5)
    check("a stopped worker serves nothing, even with a job waiting", again == 0, f"served={again}")
    check("the job left behind stays QUEUED and untouched by the stopped worker",
          S2.job_row(engine, third["job_id"])["status"] == "QUEUED"
          and S2.job_row(engine, third["job_id"])["attempt_count"] == 0,
          json.dumps(_row_status(S2.job_row(engine, third["job_id"]))))
    check("no heartbeat thread survived shutdown", heartbeat_alive() is False)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    iterations = int(argv[0]) if argv else 20
    real_iterations = max(5, iterations // 2)

    print("=" * 78)
    print("Stage 2 — deterministic LIVE two-worker concurrency test (isolated)")
    print("=" * 78)
    print("isolation guardrails: OK (loopback only, no PLATRIXA_* origin, no real credential)")

    previous_lease = os.environ.get(LEASE_ENV)
    srv, engine = S1.pg_backend()
    developer.set_client(S1.HarnessClient())
    app = create_app()
    bridge_server, bridge_url = S2.start_bridge()
    api_server, api_thread, api_base = S2.start_api(app)
    print(f"real API on {api_base} (uvicorn, loopback) | mock bridge on {bridge_url}")
    print(f"real PostgreSQL store (disposable embedded cluster) | iterations={iterations}")

    class _Bridge:
        pass

    bridge = _Bridge()
    bridge.base = bridge_url
    bridge.state = bridge_server.state  # type: ignore[attr-defined]
    check("the race API origin is loopback", S2._is_loopback(api_base), api_base)
    check("the mock bridge origin is loopback", S2._is_loopback(bridge_url), bridge_url)

    try:
        s0_tenant_scoping(api_base, engine, bridge)
        s1_race_orders(api_base, engine, bridge, iterations)
        s2_real_expiry(api_base, engine, bridge, real_iterations)
        s3_worker_lifecycle(api_base, engine, bridge)
    finally:
        developer.reset_client()
        bridge_server.shutdown()
        api_server.should_exit = True
        api_thread.join(timeout=5)
        if previous_lease is None:
            os.environ.pop(LEASE_ENV, None)
        else:
            os.environ[LEASE_ENV] = previous_lease
        try:
            srv.cleanup()
        except Exception as exc:  # pragma: no cover
            print(f"note: pgserver cleanup raised {type(exc).__name__}: {exc}")
        # The embedded cluster's data directory is disposable: remove it so this
        # suite leaves no temporary database behind.
        shutil.rmtree(str(_PG_DATA), ignore_errors=True)

    passed = sum(1 for _, ok, _, _ in S2.CHECKS if ok)
    total = len(S2.CHECKS)
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{total} checks passed")
    for name, ok, detail, kind in S2.CHECKS:
        if not ok:
            print(f"  FAIL ({kind}) {name} — {detail}")
    print("=" * 78)
    leftovers = S2.heartbeat_threads()
    print(f"worker heartbeat threads still alive after teardown: {leftovers}")
    return 0 if passed == total and not leftovers else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
